"""
Namespace Management & Access Grants Endpoints
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from storage.relational.session import get_db
from core.identity.service import IdentityService
from core.memory.schemas import NamespaceCreate, NamespaceRead, AccessGrantCreate, AccessGrantRead

router = APIRouter(prefix="/namespaces", tags=["Namespaces"])

@router.post("", response_model=NamespaceRead, status_code=status.HTTP_201_CREATED)
def create_namespace(ns_in: NamespaceCreate, db: Session = Depends(get_db)):
    namespace = IdentityService.create_namespace(
        db,
        path=ns_in.path,
        ns_type=ns_in.type,
        agent_id=ns_in.agent_id
    )
    return namespace

@router.get("", response_model=List[NamespaceRead])
def list_namespaces(agent_id: Optional[str] = None, db: Session = Depends(get_db)):
    return IdentityService.list_namespaces(db, agent_id=agent_id)

@router.post("/grants", response_model=AccessGrantRead, status_code=status.HTTP_201_CREATED)
def grant_namespace_access(grant_in: AccessGrantCreate, db: Session = Depends(get_db)):
    grant = IdentityService.grant_access(
        db,
        agent_id=grant_in.agent_id,
        namespace_id=grant_in.namespace_id,
        actions=grant_in.actions,
        purpose=grant_in.purpose,
        expires_at=grant_in.expires_at
    )
    return grant

@router.delete("/grants", status_code=status.HTTP_200_OK)
def revoke_namespace_access(agent_id: str, namespace_id: str, db: Session = Depends(get_db)):
    success = IdentityService.revoke_access(db, agent_id=agent_id, namespace_id=namespace_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Access grant not found.")
    return {"status": "revoked", "agent_id": agent_id, "namespace_id": namespace_id}