"""
Namespace Management & Access Grants Endpoints
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from storage.relational.session import get_db
from storage.relational.models import Agent, Namespace
from core.identity.service import IdentityService
from core.memory.schemas import NamespaceCreate, NamespaceRead, AccessGrantCreate, AccessGrantRead
from apps.api.dependencies import (
    ADMIN_AGENTS,
    authenticate_agent,
    get_actor_header,
    require_admin,
)

router = APIRouter(
    prefix="/namespaces",
    tags=["Namespaces"],
    dependencies=[Depends(authenticate_agent)],
)

# Identities allowed to administer access on a namespace they do not own. Memora
# is the memory fabric itself; every other caller must own the namespace.
_ADMIN_AGENTS = {"memora"}


def _authorize_namespace_admin(db: Session, namespace_id: str, actor_name: str) -> Namespace:
    """Only the namespace owner (or the Memora service identity) may administer grants.

    Grant creation is the single highest-privilege operation in Memora: it is what
    turns a private namespace into a readable one. Previously any caller — including
    a completely unauthenticated one — could grant any agent any action on any
    namespace, which made the whole private-by-default model advisory.
    """
    namespace = db.query(Namespace).filter(Namespace.id == namespace_id).first()
    if not namespace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Namespace not found.")

    if actor_name in _ADMIN_AGENTS:
        return namespace

    actor = IdentityService.get_agent_by_name(db, actor_name)
    if not actor or namespace.agent_id != actor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Only the owner of namespace '{namespace.path}' or the Memora service "
                f"identity may administer its access grants."
            ),
        )
    return namespace


@router.post("", response_model=NamespaceRead, status_code=status.HTTP_201_CREATED)
def create_namespace(
    ns_in: NamespaceCreate,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
) -> Namespace:
    # A namespace with no owner has nobody who may administer its grants, which
    # leaves it either permanently unmanageable or, if the ownership check is
    # skipped, open to anyone. Attribute creation to the authenticated caller
    # unless an owner is named explicitly.
    try:
        path = IdentityService.validate_namespace_path(ns_in.path)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    caller = IdentityService.get_agent_by_name(db, actor_name) or IdentityService.register_agent(
        db, actor_name
    )

    # ---------------------------------------------------------------
    # The caller may not hand a namespace to somebody else.
    #
    # agent_id / agent_name were taken at face value, so any authenticated
    # agent could create rows owned by any other agent. Verified live: forge
    # POSTed agent_name="friday" and got 201 for a namespace owned by friday,
    # without friday's involvement. Ownership of a namespace is what decides
    # who may administer its grants, so attributing one to an unwilling owner
    # is not a harmless bookkeeping act.
    #
    # Naming an agent that does not exist used to fall through and silently
    # attribute the namespace to the caller instead, which is worse: the
    # request asks for one owner and the row records another. Report it.
    # ---------------------------------------------------------------
    owner = caller
    if ns_in.agent_id:
        owner = db.query(Agent).filter(Agent.id == ns_in.agent_id).first()
        if owner is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No agent with id '{ns_in.agent_id}'.",
            )
    elif ns_in.agent_name:
        owner = IdentityService.get_agent_by_name(db, ns_in.agent_name)
        if owner is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No agent named '{ns_in.agent_name}'.",
            )

    is_admin = actor_name in ADMIN_AGENTS
    if owner.id != caller.id:
        require_admin(actor_name)

    # ---------------------------------------------------------------
    # The same rule the write pipeline enforces: a caller may only bring a
    # namespace into existence under its own name, inside its bounded scope,
    # or under an open root such as memora://shared. Without it this route is
    # a second door onto the land-grab closed in the write pipeline - it lets
    # a caller mint memora://<somebody-else>/... rows directly.
    #
    # An existing namespace is left alone rather than re-owned here.
    # ---------------------------------------------------------------
    # The fabric administrator is exempt from both checks below: provisioning a
    # space on another agent's behalf is exactly its job, and blocking it here
    # would make the require_admin check above pointless.
    existing = IdentityService.get_namespace_by_path(db, path, tenant_id=ns_in.tenant_id)

    if existing is not None:
        # create_namespace hands back the row that already exists rather than
        # re-owning it, so this was never an ownership theft. It was still wrong
        # in two ways: the response said 201 Created for something that was not
        # created, and it gave any caller the full record - id and owner - of a
        # namespace belonging to somebody else, for any path they cared to name.
        if not is_admin and existing.agent_id != caller.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Namespace '{path}' already exists and is not owned by "
                    f"'{caller.name}'. Ask its owner to grant access."
                ),
            )
        return existing

    if not is_admin:
        root = IdentityService.namespace_root(path)
        in_own_scope = root == caller.name
        in_bounded_scope = bool(
            caller.bounded_scope and path.startswith(caller.bounded_scope)
        )
        if root is not None and not (in_own_scope or in_bounded_scope):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Cannot create namespace '{path}': it belongs to agent "
                    f"'{root}', and '{caller.name}' may only create namespaces "
                    f"under its own name. An existing namespace can still be "
                    f"used if '{root}' grants access."
                ),
            )

    namespace = IdentityService.create_namespace(
        db,
        path=path,
        ns_type=ns_in.type,
        agent_id=owner.id
    )
    return namespace

@router.get("", response_model=List[NamespaceRead])
def list_namespaces(agent_id: Optional[str] = None, db: Session = Depends(get_db)) -> List[Namespace]:
    return IdentityService.list_namespaces(db, agent_id=agent_id)

@router.post("/grants", response_model=AccessGrantRead, status_code=status.HTTP_201_CREATED)
def grant_namespace_access(
    grant_in: AccessGrantCreate,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
):
    target_ns_id = grant_in.namespace_id
    if not target_ns_id and grant_in.namespace_path:
        ns = IdentityService.get_namespace_by_path(db, grant_in.namespace_path)
        if not ns:
            ns = IdentityService.resolve_namespace(db, grant_in.namespace_path)
        target_ns_id = ns.id

    if not target_ns_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either namespace_id or namespace_path must be provided.",
        )

    # Fail before creating any side effects: the target agent must not be
    # auto-registered by grant_access for a grant we are about to refuse.
    _authorize_namespace_admin(db, target_ns_id, actor_name)

    grant = IdentityService.grant_access(
        db,
        agent_id=grant_in.agent_id,
        agent_name=grant_in.agent_name,
        namespace_id=target_ns_id,
        actions=grant_in.actions,
        purpose=grant_in.purpose,
        expires_at=grant_in.expires_at,
        ttl_hours=grant_in.ttl_hours
    )
    return grant

@router.delete("/grants", status_code=status.HTTP_200_OK)
def revoke_namespace_access(
    agent_id: str,
    namespace_id: str,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
):
    _authorize_namespace_admin(db, namespace_id, actor_name)
    success = IdentityService.revoke_access(db, agent_id=agent_id, namespace_id=namespace_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access grant not found.")
    return {"status": "revoked", "agent_id": agent_id, "namespace_id": namespace_id}
