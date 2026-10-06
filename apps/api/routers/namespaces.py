"""
Namespace Management & Access Grants Endpoints
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from storage.relational.session import get_db
from storage.relational.models import Namespace
from core.identity.service import IdentityService
from core.memory.schemas import NamespaceCreate, NamespaceRead, AccessGrantCreate, AccessGrantRead
from apps.api.dependencies import authenticate_agent, get_actor_header

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
    owner_agent_id = ns_in.agent_id
    if not owner_agent_id:
        owner = None
        if ns_in.agent_name:
            owner = IdentityService.get_agent_by_name(db, ns_in.agent_name)
        if not owner:
            owner = IdentityService.get_agent_by_name(db, actor_name) or IdentityService.register_agent(
                db, ns_in.agent_name or actor_name
            )
        owner_agent_id = owner.id

    namespace = IdentityService.create_namespace(
        db,
        path=ns_in.path,
        ns_type=ns_in.type,
        agent_id=owner_agent_id
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
