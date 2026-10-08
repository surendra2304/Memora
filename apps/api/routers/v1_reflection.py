"""
MEMORA v1 Reflection Endpoints

Exposes the self-brain: run a reflection pass, read what it concluded, and list
the insights it has recorded over time.

Any authenticated agent may read insights and preview a pass, because knowing
what the system thinks is what makes the insights useful. Storing new insights
writes memories, so it is restricted to the Memora service identity and defaults
to a dry run.
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.dependencies import get_actor_header
from core.identity.service import IdentityService
from core.reflection.engine import reflection_engine
from storage.relational.models import MemoryRecord, MemoryType
from storage.relational.session import get_db

router = APIRouter(prefix="/v1/reflection", tags=["v1 Reflection"])

_ADMIN_AGENTS = {"memora"}


def _require_admin(actor_name: str) -> str:
    if actor_name not in _ADMIN_AGENTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the memora service identity may store reflections.",
        )
    return actor_name


def _resolve_reflection_actor(db: Session, actor_name: str):
    """Resolve authenticated service identity in the API's default tenant.

    Agent credentials currently identify a service name, not a tenant. Until
    credentials carry a tenant claim, selecting an arbitrary same-named agent
    from another tenant would be unsafe; this endpoint therefore binds to the
    default tenant and fails closed when that principal is not provisioned.
    """
    actor = IdentityService.get_agent_by_name(db, actor_name, tenant_id="default")
    if actor is None and actor_name == "memora":
        actor = IdentityService.register_agent(
            db, "memora", role="supervisor", tenant_id="default"
        )
    if actor is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The authenticated agent is not provisioned in the default tenant.",
        )
    return actor


class ReflectRequest(BaseModel):
    dry_run: bool = Field(
        default=True,
        description="Draw insights without storing them. Defaults to safe.",
    )


@router.post("/run")
def run_reflection(
    req: ReflectRequest,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Analyse visible memory and report grounded conclusions."""
    actor = _resolve_reflection_actor(db, actor_name)
    is_admin = actor.name in _ADMIN_AGENTS
    if not req.dry_run:
        _require_admin(actor.name)

    report = reflection_engine.reflect(
        db,
        tenant_id=actor.tenant_id,
        store_insights=not req.dry_run,
        actor=None if is_admin else actor,
    )
    return report.to_dict()


@router.get("/insights")
def list_insights(
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
    kind: Optional[str] = Query(default=None, description="Filter by insight kind."),
    limit: int = Query(default=50, ge=1, le=200),
) -> Dict[str, Any]:
    """Return stored admin insights or a policy-filtered live view for an agent."""
    actor = _resolve_reflection_actor(db, actor_name)

    if actor.name not in _ADMIN_AGENTS:
        report = reflection_engine.reflect(
            db,
            tenant_id=actor.tenant_id,
            store_insights=False,
            actor=actor,
        )
        insights = [
            insight.to_dict()
            for insight in report.insights
            if not kind or insight.kind == kind
        ][:limit]
        return {
            "count": len(insights),
            "tenant_id": actor.tenant_id,
            "source": "live_policy_filtered",
            "insights": insights,
        }

    query = (
        db.query(MemoryRecord)
        .filter(
            MemoryRecord.tenant_id == actor.tenant_id,
            MemoryRecord.memory_type == MemoryType.EXPERIENCE,
        )
        .order_by(MemoryRecord.created_at.desc())
    )
    rows = query.limit(limit * 3).all()

    insights: List[Dict[str, Any]] = []
    for record in rows:
        provenance = record.provenance
        if not isinstance(provenance, dict):
            continue
        if provenance.get("source") != "reflection_engine":
            continue
        if kind and provenance.get("reflection_kind") != kind:
            continue
        insights.append(
            {
                "memory_id": record.id,
                "kind": provenance.get("reflection_kind"),
                "subject": provenance.get("reflection_subject"),
                "summary": record.content_text,
                "confidence": record.confidence,
                "importance": record.importance,
                "evidence": provenance.get("reflection_evidence") or [],
                "suggested_action": provenance.get("suggested_action"),
                "created_at": record.created_at.isoformat() if record.created_at else None,
            }
        )
        if len(insights) >= limit:
            break

    return {
        "count": len(insights),
        "tenant_id": actor.tenant_id,
        "source": "stored",
        "insights": insights,
    }
