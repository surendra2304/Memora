"""
MEMORA v1 Collaboration Endpoints

Lets one agent ask another for help, offer material it owns, and hand a bounded
task to a sub-agent. Every path goes through PolicyEngine and is audited, so
collaboration widens access only where an owner explicitly allows it and only for
as long as the grant lasts.

The caller acts as itself: the actor identity comes from the authenticated
header, never from the request body, so an agent cannot ask on another's behalf.
"""
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.dependencies import get_actor_header, get_purpose_header
from core.collaboration.service import (
    CollaborationError,
    CollaborationPermissionError,
    CollaborationService,
)
from storage.relational.session import get_db

router = APIRouter(prefix="/v1/collaboration", tags=["v1 Collaboration"])


class AssistanceRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000,
                       description="What the caller needs help with.")
    purpose: Optional[str] = Field(default=None, description="Access intent justification.")
    limit: int = Field(default=5, ge=1, le=25, description="Max candidate peers to return.")


@router.post("/assist")
def request_assistance(
    req: AssistanceRequest,
    actor_name: str = Depends(get_actor_header),
    header_purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Ask the mesh for help.

    Returns material the caller may already read inline, plus candidate peers who
    hold relevant experience. Candidates are disclosed as identities and reasons
    only: a peer's private content is never read to build the list, and nothing
    new is granted by asking.
    """
    try:
        response = CollaborationService.request_assistance(
            db,
            requester_name=actor_name,
            query=req.query,
            purpose=req.purpose or header_purpose or "collaboration",
            limit=req.limit,
            tenant_id="default",
        )
    except CollaborationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return response.to_dict()


class ContributeRequest(BaseModel):
    memory_id: str = Field(..., min_length=1, max_length=64, description="The memory being offered.")
    recipient: str = Field(..., min_length=2, max_length=128, description="Agent name to grant access to.")
    purpose: Optional[str] = Field(default=None, max_length=512)
    actions: List[Literal["read", "query"]] = Field(
        default_factory=lambda: ["read"], min_length=1, max_length=2,
        description="Contribution is read-only; only read/query actions may be granted.",
    )
    ttl_hours: Optional[int] = Field(default=None, ge=1, le=24 * 30,
                                     description="Grant lifetime; defaults to 24h.")


@router.post("/contribute")
def contribute_memory(
    req: ContributeRequest,
    actor_name: str = Depends(get_actor_header),
    header_purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Offer one memory you own to a peer, as a scoped, expiring grant."""
    try:
        return CollaborationService.contribute(
            db,
            contributor_name=actor_name,
            memory_id=req.memory_id,
            recipient_name=req.recipient,
            purpose=req.purpose or header_purpose or "collaboration",
            actions=req.actions,
            ttl_hours=req.ttl_hours,
            tenant_id="default",
        )
    except CollaborationPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    except CollaborationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


class DelegateRequest(BaseModel):
    subagent_name: str = Field(..., min_length=2, max_length=64,
                               description="Name for the sub-agent to create.")
    task_description: str = Field(..., min_length=1, max_length=2000)
    bounded_scope: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="Namespace-path prefix the sub-agent may act within. "
                    "PolicyEngine enforces this, so it must be a real namespace path.",
    )


@router.post("/delegate")
def delegate_task(
    req: DelegateRequest,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Hand a task to a bounded sub-agent scoped under the caller."""
    try:
        return CollaborationService.delegate(
            db,
            delegator_name=actor_name,
            subagent_name=req.subagent_name,
            task_description=req.task_description,
            bounded_scope=req.bounded_scope,
            tenant_id="default",
        )
    except CollaborationPermissionError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except CollaborationError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
