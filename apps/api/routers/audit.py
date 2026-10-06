"""
Audit Log Inspection Endpoints
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from storage.relational.session import get_db
from storage.relational.models import AuditLog
from core.memory.schemas import AuditLogRead
from apps.api.dependencies import authenticate_agent

# The audit trail records every policy decision, memory id, and denial reason in
# the fabric, so it is authenticated like every other memory-bearing router.
router = APIRouter(
    prefix="/audit",
    tags=["Audit"],
    dependencies=[Depends(authenticate_agent)],
)

@router.get("", response_model=List[AuditLogRead])
def list_audit_logs(
    actor_id: Optional[str] = None,
    memory_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    db: Session = Depends(get_db)
):
    query = db.query(AuditLog)
    if actor_id:
        query = query.filter(AuditLog.actor_id == actor_id)
    if memory_id:
        query = query.filter(AuditLog.memory_id == memory_id)
    if action:
        query = query.filter(AuditLog.action == action)
    return query.order_by(AuditLog.timestamp.desc()).limit(limit).all()