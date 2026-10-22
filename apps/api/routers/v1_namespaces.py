"""MEMORA v1 namespace-policy inspection.

A policy report includes grant recipients and purposes, so it is administrative
metadata rather than a public namespace descriptor. Only the namespace owner and
the Memora service identity may inspect it, and API credentials are bound to the
default tenant until credentials carry an explicit tenant claim.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from apps.api.dependencies import get_actor_header
from core.identity.service import IdentityService
from storage.relational.models import AccessGrant, Agent, Namespace, NamespaceType
from storage.relational.session import get_db

router = APIRouter(prefix="/v1/namespaces", tags=["v1 Namespaces"])
_ADMIN_AGENTS = {"memora"}


@router.get("/{namespace_id}/policy")
def get_namespace_policy(
    namespace_id: str,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
):
    actor = IdentityService.get_agent_by_name(db, actor_name, tenant_id="default")
    if actor is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The authenticated agent is not provisioned in the default tenant.",
        )

    namespace = db.query(Namespace).filter(
        Namespace.id == namespace_id,
        Namespace.tenant_id == actor.tenant_id,
    ).first()
    if namespace is None:
        namespace = db.query(Namespace).filter(
            Namespace.path == namespace_id,
            Namespace.tenant_id == actor.tenant_id,
        ).first()
    if namespace is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Namespace '{namespace_id}' not found.",
        )

    if actor.name not in _ADMIN_AGENTS and namespace.agent_id != actor.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the namespace owner or Memora service identity may inspect its policy and grants.",
        )

    owner_agent = None
    if namespace.agent_id:
        owner_agent = db.query(Agent).filter(
            Agent.id == namespace.agent_id,
            Agent.tenant_id == actor.tenant_id,
        ).first()

    grants = db.query(AccessGrant).filter(
        AccessGrant.namespace_id == namespace.id,
        AccessGrant.tenant_id == actor.tenant_id,
    ).all()
    access_grants = []
    active_count = 0
    for grant in grants:
        agent_obj = db.query(Agent).filter(
            Agent.id == grant.agent_id,
            Agent.tenant_id == actor.tenant_id,
        ).first()
        expired = grant.is_expired()
        if not expired:
            active_count += 1
        access_grants.append({
            "grant_id": grant.id,
            "agent_id": grant.agent_id,
            "agent_name": agent_obj.name if agent_obj else "unknown",
            "actions": grant.actions,
            "purpose": grant.purpose,
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
            "is_expired": expired,
        })

    isolation_rules = {
        NamespaceType.AGENT_PRIVATE: "Rule 1: Private by default. Inaccessible to other agents unless explicitly promoted or granted.",
        NamespaceType.PROJECT_PRIVATE: "Rule 2: Project-shared. Requires explicit project membership or active AccessGrant.",
        NamespaceType.TEAM_SHARED: "Rule 2: Team-shared. Requires explicit team membership or active AccessGrant.",
        NamespaceType.UNIVERSE_GLOBAL: "Open Read: Accessible across the entire AI agent universe.",
        NamespaceType.PUBLIC: "Public Read: Openly accessible across all agents and public callers.",
    }

    return {
        "namespace_id": namespace.id,
        "path": namespace.path,
        "type": namespace.type.value,
        "owner_agent_id": namespace.agent_id,
        "owner_agent_name": owner_agent.name if owner_agent else None,
        "governing_rule": isolation_rules.get(namespace.type, "Standard access control"),
        "total_active_grants": active_count,
        "access_grants": access_grants,
        "created_at": namespace.created_at.isoformat(),
    }
