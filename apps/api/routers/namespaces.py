"""
Namespace Management & Access Grants Endpoints
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from apps.api.dependencies import authenticate_agent, get_actor_header
from core.identity.service import IdentityService
from core.policy.engine import PolicyDecision, PolicyEngine
from core.memory.schemas import NamespaceCreate, NamespaceRead, AccessGrantCreate, AccessGrantRead
from storage.relational.models import Agent, Namespace, NamespaceType
from storage.relational.session import get_db

router = APIRouter(
    prefix="/namespaces",
    tags=["Namespaces"],
    dependencies=[Depends(authenticate_agent)],
)

_ADMIN_AGENTS = {"memora"}
_ALLOWED_GRANT_ACTIONS = {"read", "query", "write"}


def _authenticated_actor(db: Session, actor_name: str) -> Agent:
    """Resolve API principals only inside the tenant currently bound to credentials."""
    actor = IdentityService.get_agent_by_name(db, actor_name, tenant_id="default")
    if actor is None and actor_name == "memora":
        # The memora API credential is the root deployment identity. Provision
        # it lazily only after router-level authentication has accepted that key.
        actor = IdentityService.register_agent(
            db, "memora", role="supervisor", tenant_id="default"
        )
    if actor is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The authenticated agent is not registered in the default tenant.",
        )
    return actor


def _authorize_namespace_admin(
    db: Session, namespace_id: str, actor: Agent
) -> Namespace:
    """Only the namespace owner or the Memora service identity may manage grants."""
    namespace = db.query(Namespace).filter(
        Namespace.id == namespace_id,
        Namespace.tenant_id == actor.tenant_id,
    ).first()
    if not namespace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Namespace not found.")

    if actor.name not in _ADMIN_AGENTS and namespace.agent_id != actor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the namespace owner or the Memora service identity may administer its grants.",
        )
    return namespace


def _normalize_grant_actions(actions: List[str]) -> List[str]:
    normalized = list(dict.fromkeys(str(action).strip().lower() for action in actions))
    if not normalized or any(action not in _ALLOWED_GRANT_ACTIONS for action in normalized):
        raise HTTPException(
            status_code=422,
            detail="Namespace grants may contain only read, query, and write actions; wildcard/lifecycle actions are forbidden.",
        )
    return normalized


@router.post("", response_model=NamespaceRead, status_code=status.HTTP_201_CREATED)
def create_namespace(
    ns_in: NamespaceCreate,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
) -> Namespace:
    # Creating one's own default namespace may bootstrap the authenticated
    # identity, but request fields can never select another tenant or owner.
    actor = IdentityService.get_agent_by_name(db, actor_name, tenant_id="default")
    if actor is None:
        actor = IdentityService.register_agent(db, actor_name, tenant_id="default")
    if ns_in.tenant_id != actor.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The namespace tenant must match the authenticated agent's tenant.",
        )

    owner = actor
    if ns_in.agent_id or ns_in.agent_name:
        if actor.name not in _ADMIN_AGENTS:
            requested_owner = ns_in.agent_name or ns_in.agent_id
            if requested_owner not in {actor.name, actor.id}:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="An agent may create namespaces only for itself.",
                )
        if ns_in.agent_id:
            owner = IdentityService.get_agent_by_id(db, ns_in.agent_id)
        else:
            owner = IdentityService.get_agent_by_name(
                db, ns_in.agent_name, tenant_id=actor.tenant_id
            )
        if owner is None or owner.tenant_id != actor.tenant_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Namespace owner not found in this tenant.")

    try:
        path = IdentityService.validate_namespace_path(ns_in.path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    root = IdentityService.namespace_root(path)
    path_root = path[len("memora://"):].split("/", 1)[0]
    if actor.name not in _ADMIN_AGENTS and (
        ns_in.type in {NamespaceType.UNIVERSE_GLOBAL, NamespaceType.PUBLIC}
        or path_root in {"universe", "public"}
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Memora service identity may create public/universe namespaces or use those roots.",
        )
    if actor.name not in _ADMIN_AGENTS and (
        path_root in {"team", "shared"}
        or (ns_in.type == NamespaceType.TEAM_SHARED and root is None)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The Memora service identity must provision namespaces at an open shared root.",
        )
    if actor.name not in _ADMIN_AGENTS and root is not None and root != owner.name:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="An agent may create a namespace only under its own namespace root.",
        )

    existing = IdentityService.get_namespace_by_path(db, path, tenant_id=actor.tenant_id)
    if existing is not None and actor.name not in _ADMIN_AGENTS and existing.agent_id != actor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The existing namespace is owned by another agent.",
        )

    namespace = IdentityService.create_namespace(
        db,
        path=path,
        ns_type=ns_in.type,
        agent_id=owner.id,
        tenant_id=actor.tenant_id,
    )
    return namespace


@router.get("", response_model=List[NamespaceRead])
def list_namespaces(
    agent_id: Optional[str] = None,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
) -> List[Namespace]:
    actor = _authenticated_actor(db, actor_name)
    if actor.name in _ADMIN_AGENTS:
        return IdentityService.list_namespaces(db, agent_id=agent_id, tenant_id=actor.tenant_id)

    if agent_id and agent_id not in {actor.id, actor.name}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Agents may list only their own namespace metadata.",
        )
    return IdentityService.list_namespaces(db, agent_id=actor.id, tenant_id=actor.tenant_id)


@router.post("/grants", response_model=AccessGrantRead, status_code=status.HTTP_201_CREATED)
def grant_namespace_access(
    grant_in: AccessGrantCreate,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
):
    actor = _authenticated_actor(db, actor_name)
    if grant_in.tenant_id != actor.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The grant tenant must match the authenticated agent's tenant.",
        )

    if grant_in.namespace_id:
        target_ns_id = grant_in.namespace_id
    elif grant_in.namespace_path:
        existing = IdentityService.get_namespace_by_path(
            db, grant_in.namespace_path, tenant_id=actor.tenant_id
        )
        if existing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Namespace not found.")
        target_ns_id = existing.id
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either namespace_id or namespace_path must be provided.",
        )

    namespace = _authorize_namespace_admin(db, target_ns_id, actor)
    if grant_in.agent_id:
        target = IdentityService.get_agent_by_id(
            db, grant_in.agent_id, tenant_id=actor.tenant_id
        )
    elif grant_in.agent_name:
        target = IdentityService.get_agent_by_name(
            db, grant_in.agent_name, tenant_id=actor.tenant_id
        )
    else:
        target = None
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Target agent is not registered in this tenant; provision it through the admin identity endpoint first.",
        )

    actions = _normalize_grant_actions(grant_in.actions)
    try:
        grant = IdentityService.grant_access(
            db,
            agent_id=target.id,
            namespace_id=namespace.id,
            actions=actions,
            purpose=grant_in.purpose,
            expires_at=grant_in.expires_at,
            ttl_hours=grant_in.ttl_hours,
            tenant_id=actor.tenant_id,
            commit=False,
        )
        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                True,
                f"{actor.name} granted namespace access to agent '{grant.agent_id}'.",
                "NAMESPACE_ACCESS_GRANTED",
                {"namespace_id": namespace.id, "actions": actions},
            ),
            actor_id=actor.id,
            tenant_id=actor.tenant_id,
        )
        db.commit()
        db.refresh(grant)
        return grant
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/grants", status_code=status.HTTP_200_OK)
def revoke_namespace_access(
    agent_id: str,
    namespace_id: str,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
):
    actor = _authenticated_actor(db, actor_name)
    namespace = _authorize_namespace_admin(db, namespace_id, actor)
    target = IdentityService.get_agent_by_id(db, agent_id)
    if target is None or target.tenant_id != actor.tenant_id:
        target = IdentityService.get_agent_by_name(db, agent_id, tenant_id=actor.tenant_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target agent not found.")

    success = IdentityService.revoke_access(
        db,
        agent_id=target.id,
        namespace_id=namespace.id,
        tenant_id=actor.tenant_id,
        commit=False,
    )
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access grant not found.")

    PolicyEngine.log_audit_decision(
        db,
        PolicyDecision(
            True,
            f"{actor.name} revoked namespace access for agent '{target.name}'.",
            "NAMESPACE_ACCESS_REVOKED",
            {"namespace_id": namespace.id, "target_agent_id": target.id},
        ),
        actor_id=actor.id,
        tenant_id=actor.tenant_id,
    )
    db.commit()
    return {"status": "revoked", "agent_id": target.id, "namespace_id": namespace.id}
