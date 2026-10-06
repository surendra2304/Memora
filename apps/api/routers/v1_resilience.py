"""
MEMORA v1 Resilience Endpoints

Exposes the circuit breaker fleet and the self-healing supervisor over HTTP so a
human operator or a supervising agent can see when a dependency has been cut off
and can run a repair pass without shell access.

Read-only status is available to any authenticated agent. Repairs mutate state,
so they are restricted to the Memora service identity, and default to a dry run:
a caller has to ask for a real repair explicitly.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.dependencies import get_actor_header
from core.resilience.circuit_breaker import circuit_registry
from core.resilience.self_healing import self_healing_supervisor
from storage.relational.session import get_db

router = APIRouter(prefix="/v1/resilience", tags=["v1 Resilience"])

_ADMIN_AGENTS = {"memora"}


def _require_admin(actor_name: str) -> str:
    """Repairs change data, so only the Memora service identity may run them."""
    if actor_name not in _ADMIN_AGENTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the memora service identity may run resilience repairs.",
        )
    return actor_name


@router.get("/circuits")
def list_circuits(actor_name: str = Depends(get_actor_header)) -> Dict[str, Any]:
    """Snapshot of every circuit breaker and its current state."""
    snapshot = circuit_registry.snapshot()
    return {
        "circuits": snapshot,
        "circuit_count": len(snapshot),
        "open_count": sum(1 for c in snapshot.values() if c["state"] == "open"),
        "healthy": circuit_registry.healthy(),
    }


@router.get("/circuits/{name}")
def get_circuit(
    name: str, actor_name: str = Depends(get_actor_header)
) -> Dict[str, Any]:
    """State of one named breaker, e.g. `qdrant`."""
    breaker = circuit_registry.find(name)
    if breaker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No circuit breaker named '{name}'.",
        )
    return breaker.snapshot()


class CircuitResetRequest(BaseModel):
    reason: str = Field(..., min_length=1, max_length=200,
                        description="Why the breaker is being reset manually.")


@router.post("/circuits/{name}/reset")
def reset_circuit(
    name: str,
    req: CircuitResetRequest,
    actor_name: str = Depends(get_actor_header),
) -> Dict[str, Any]:
    """Force a breaker back to closed.

    Deliberately not exposed as a bulk operation: the supervisor's own repair
    pass refuses to force-reset open breakers, because a breaker that is open is
    usually right. This exists for an operator who knows the dependency is back.
    """
    _require_admin(actor_name)
    breaker = circuit_registry.find(name)
    if breaker is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No circuit breaker named '{name}'.",
        )
    breaker.reset()
    return {"circuit": name, "state": breaker.state.value, "reason": req.reason}


class RepairRequest(BaseModel):
    dry_run: bool = Field(
        default=True,
        description="Report what would change without changing it. Defaults to safe.",
    )


@router.post("/repair")
def run_repair(
    req: RepairRequest,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Run the self-healing supervisor over the corpus."""
    _require_admin(actor_name)
    report = self_healing_supervisor.run(db, dry_run=req.dry_run)
    return report.to_dict()


@router.get("/repair/preview")
def preview_repair(
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Dry-run repair, safe for any authenticated caller to inspect."""
    report = self_healing_supervisor.run(db, dry_run=True)
    return report.to_dict()
