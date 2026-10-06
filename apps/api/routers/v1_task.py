"""Universal Task Execution Router for Memora (FRIDAY Universe Protocol)."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from storage.relational.session import get_db
from storage.relational.models import MemoryType
from core.memory.pipeline.write_service import MemoryWriteService, MemoryPipelineError
from core.memory.pipeline.secret_scanner import SecretDetectedSecurityViolation
from core.memory.pipeline.poison_detector import PoisonMemoryViolation
from core.memory.search_service import SearchService
from core.memory.service import PermissionDeniedError
from apps.api.dependencies import get_actor_header

logger = logging.getLogger(__name__)

task_router = APIRouter(prefix="/v1/task", tags=["Universal Task Protocol"])

_STORE_ACTIONS = {"store", "remember", "add", "record"}
_QUERY_ACTIONS = {"query", "search", "recall", "retrieve", "find", "ask"}


class TaskEnvelopeModel(BaseModel):
    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}")
    source_agent: str = Field(default="friday")
    target_agent: str = Field(default="memora")
    action: str = Field(default="store")
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: str = Field(default="normal")
    created_at: str | None = None
    trace_id: str | None = None


class TaskResultModel(BaseModel):
    task_id: str
    target_agent: str = "memora"
    status: str = "SUCCESS"
    result: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    error: str | None = None
    execution_time_ms: int = 0


def _elapsed_ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)


@task_router.post("/execute", response_model=TaskResultModel, status_code=status.HTTP_200_OK)
def execute_task(
    envelope: TaskEnvelopeModel,
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor_header),
) -> TaskResultModel:
    """Execute universal task envelope for persistent cognitive memory operations.

    The authenticated caller is authoritative for identity: `source_agent` in the
    envelope is metadata, not a credential, and must never widen the caller's scope.
    """
    t0 = time.time()
    action = envelope.action.lower().strip()

    if action in _STORE_ACTIONS:
        return _execute_store(db, envelope, actor, t0)
    if action in _QUERY_ACTIONS:
        return _execute_query(db, envelope, actor, t0)

    return TaskResultModel(
        task_id=envelope.task_id,
        target_agent="memora",
        status="ERROR",
        error=f"Unknown action '{envelope.action}'. Expected one of "
              f"{sorted(_STORE_ACTIONS | _QUERY_ACTIONS)}.",
        execution_time_ms=_elapsed_ms(t0),
    )


def _execute_store(
    db: Session,
    envelope: TaskEnvelopeModel,
    actor: str,
    t0: float,
) -> TaskResultModel:
    """Persist a memory through the canonical 10-step write pipeline."""
    payload = envelope.payload
    content = str(
        payload.get("content")
        or payload.get("text")
        or payload.get("content_text")
        or payload.get("query")
        or str(payload)
    )
    ns_path = payload.get("target_namespace_path") or f"memora://{actor}/general"
    mtype_str = str(payload.get("memory_type") or payload.get("type") or "observation").lower()

    mtype = MemoryType.EPISODIC
    if "semantic" in mtype_str:
        mtype = MemoryType.SEMANTIC
    elif "procedure" in mtype_str:
        mtype = MemoryType.PROCEDURAL
    elif "experience" in mtype_str:
        mtype = MemoryType.EXPERIENCE
    elif "working" in mtype_str:
        mtype = MemoryType.WORKING

    try:
        write_res = MemoryWriteService.execute_pipeline(
            db=db,
            content_text=content,
            caller_name=actor,
            target_namespace_path=ns_path,
            memory_type=mtype,
            source=f"agent:{actor}",
            provenance={"task_id": envelope.task_id, "trace_id": envelope.trace_id},
            allow_duplicates=True,
        )
    except (SecretDetectedSecurityViolation, PoisonMemoryViolation) as exc:
        # A security rejection is a refusal, not a server fault. Report it in-band
        # so the calling agent can act on it, but never as SUCCESS.
        return TaskResultModel(
            task_id=envelope.task_id,
            target_agent="memora",
            status="REJECTED",
            error=f"{type(exc).__name__}: {exc}",
            summary="Write rejected by Memora security policy.",
            execution_time_ms=_elapsed_ms(t0),
        )
    except PermissionDeniedError as exc:
        return TaskResultModel(
            task_id=envelope.task_id,
            target_agent="memora",
            status="DENIED",
            error=str(exc),
            summary="Write denied by the Memora policy engine.",
            execution_time_ms=_elapsed_ms(t0),
        )
    except MemoryPipelineError as exc:
        return TaskResultModel(
            task_id=envelope.task_id,
            target_agent="memora",
            status="ERROR",
            error=str(exc),
            summary="Memory write pipeline rejected the payload.",
            execution_time_ms=_elapsed_ms(t0),
        )

    lat = _elapsed_ms(t0)
    return TaskResultModel(
        task_id=envelope.task_id,
        target_agent="memora",
        status="SUCCESS",
        result={
            "memory_id": write_res.record.id,
            "namespace_path": ns_path,
            "memory_type": mtype.value,
            "duplicate": write_res.is_duplicate,
        },
        summary=f"Stored memory into {ns_path} ({lat}ms)",
        execution_time_ms=lat,
    )


def _execute_query(
    db: Session,
    envelope: TaskEnvelopeModel,
    actor: str,
    t0: float,
) -> TaskResultModel:
    """Recall memories through hybrid search, which enforces the policy gate.

    This previously ran a raw ORM `ilike` over every ACTIVE record with no tenant,
    namespace, or policy filtering, which handed any caller the most recent matches
    from every private namespace in the fabric.
    """
    payload = envelope.payload
    query = str(
        payload.get("query")
        or payload.get("task_query")
        or payload.get("prompt")
        or payload.get("content")
        or ""
    ).strip()
    limit = payload.get("limit") or 5
    try:
        limit = max(1, min(int(limit), 100))
    except (TypeError, ValueError):
        limit = 5

    if not query:
        return TaskResultModel(
            task_id=envelope.task_id,
            target_agent="memora",
            status="ERROR",
            error="A query, task_query, prompt, or content field is required to recall memories.",
            execution_time_ms=_elapsed_ms(t0),
        )

    try:
        results = SearchService.hybrid_search(
            db=db,
            query_text=query,
            actor_name=actor,
            namespace_path=payload.get("target_namespace_path"),
            limit=limit,
        )
    except PermissionDeniedError as exc:
        return TaskResultModel(
            task_id=envelope.task_id,
            target_agent="memora",
            status="DENIED",
            error=str(exc),
            execution_time_ms=_elapsed_ms(t0),
        )

    recalled = [
        {
            "id": item.record.id,
            "content_text": item.record.content_text,
            "memory_type": item.record.memory_type.value,
            "namespace_path": item.record.namespace.path if item.record.namespace else None,
            "score": round(item.final_score, 4),
        }
        for item in results
    ]

    lat = _elapsed_ms(t0)
    return TaskResultModel(
        task_id=envelope.task_id,
        target_agent="memora",
        status="SUCCESS",
        result={"count": len(recalled), "memories": recalled, "query": query},
        summary=f"Retrieved {len(recalled)} memory item(s) in {lat}ms",
        execution_time_ms=lat,
    )
