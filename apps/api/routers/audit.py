"""
Audit Log Inspection Endpoints
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from storage.relational.session import get_db
from storage.relational.models import Agent, AuditLog
from core.memory.schemas import AuditLogRead
from apps.api.dependencies import (
    ADMIN_AGENTS,
    authenticate_agent,
    get_actor_header,
)

# The audit trail records every policy decision, memory id, and denial reason in
# the fabric. Authentication alone left it readable by any agent, which disclosed
# every other agent's activity — verified against a live server, where intelx read
# the full log including other agents' actor and memory ids. A caller may now see
# its own entries; the whole trail is admin-only.
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
    limit: int = Query(default=100, ge=1, le=500),
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    query = db.query(AuditLog).filter(AuditLog.tenant_id == "default")

    if actor_name not in ADMIN_AGENTS:
        me = db.query(Agent).filter(
            Agent.name == actor_name,
            Agent.tenant_id == "default",
        ).first()
        # Confine a non-admin to its own tenant-bound rows. Asking for someone
        # else's actor_id must yield nothing rather than that agent's trail.
        query = query.filter(AuditLog.actor_id == (me.id if me else "__none__"))
    elif actor_id:
        query = query.filter(AuditLog.actor_id == actor_id)
    if memory_id:
        query = query.filter(AuditLog.memory_id == memory_id)
    if action:
        query = query.filter(AuditLog.action == action)
    return query.order_by(AuditLog.timestamp.desc()).limit(limit).all()