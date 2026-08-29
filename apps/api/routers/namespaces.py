"""
Namespace Management Endpoints
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from storage.relational.session import get_db
from core.identity.service import IdentityService
from core.memory.schemas import NamespaceCreate, NamespaceRead

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