"""
MEMORA v1 Namespace Policy Endpoints
Provides GET /v1/namespaces/{id}/policy to inspect effective access rules and grants.
"""
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from storage.relational.session import get_db
from storage.relational.models import Namespace, AccessGrant, Agent, NamespaceType
from core.identity.service import IdentityService
from apps.api.dependencies import get_actor_header

router = APIRouter(prefix="/v1/namespaces", tags=["v1 Namespaces"])

@router.get("/{namespace_id}/policy")
def get_namespace_policy(
    namespace_id: str,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    namespace = db.query(Namespace).filter(Namespace.id == namespace_id).first()
    if not namespace:
        namespace = db.query(Namespace).filter(Namespace.path == namespace_id).first()
    if not namespace:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Namespace '{namespace_id}' not found.")

    owner_agent = namespace.agent or (db.query(Agent).filter(Agent.id == namespace.agent_id).first() if namespace.agent_id else None)

    grants = db.query(AccessGrant).filter(AccessGrant.namespace_id == namespace.id).all()
    active_grants = []
    for g in grants:
        agent_obj = g.agent or db.query(Agent).filter(Agent.id == g.agent_id).first()
        active_grants.append({
            "grant_id": g.id,
            "agent_id": g.agent_id,
            "agent_name": agent_obj.name if agent_obj else "unknown",
            "actions": g.actions,
            "purpose": g.purpose,
            "expires_at": g.expires_at.isoformat() if g.expires_at else None,
            "is_expired": g.is_expired()
        })

    isolation_rules = {
        NamespaceType.AGENT_PRIVATE: "Rule 1: Private by default. Inaccessible to other agents unless explicitly promoted or granted.",
        NamespaceType.PROJECT_PRIVATE: "Rule 2: Project-shared. Requires explicit project membership or active AccessGrant.",
        NamespaceType.TEAM_SHARED: "Rule 2: Team-shared. Requires explicit team membership or active AccessGrant.",
        NamespaceType.UNIVERSE_GLOBAL: "Open Read: Accessible across the entire AI agent universe.",
        NamespaceType.PUBLIC: "Public Read: Openly accessible across all agents and public callers."
    }

    return {
        "namespace_id": namespace.id,
        "path": namespace.path,
        "type": namespace.type.value,
        "owner_agent_id": namespace.agent_id,
        "owner_agent_name": owner_agent.name if owner_agent else None,
        "governing_rule": isolation_rules.get(namespace.type, "Standard access control"),
        "total_active_grants": len(active_grants),
        "access_grants": active_grants,
        "created_at": namespace.created_at.isoformat()
    }