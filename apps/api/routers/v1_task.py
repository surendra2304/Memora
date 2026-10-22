"""Universal Task Execution Router for Memora (FRIDAY Universe Protocol)."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field, field_validator
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
    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:12]}", min_length=1, max_length=128)
    source_agent: str = Field(default="friday", min_length=1, max_length=128)
    target_agent: str = Field(default="memora", min_length=1, max_length=128)
    action: str = Field(default="store", min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: str = Field(default="normal", min_length=1, max_length=32)
    created_at: str | None = Field(default=None, max_length=64)
    trace_id: str | None = Field(default=None, max_length=128)

    @field_validator("payload")
    @classmethod
    def validate_payload_size(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 32:
            raise ValueError("Task payload may contain at most 32 top-level fields.")
        try:
            encoded_size = len(
                json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                .encode("utf-8")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Task payload must contain finite JSON-compatible values.") from exc
        if encoded_size > 128 * 1024:
            raise ValueError("Task payload exceeds the 131072-byte limit.")

        pending = [(value, 0)]
        node_count = 0
        while pending:
            item, depth = pending.pop()
            node_count += 1
            if node_count > 4096 or depth > 16:
                raise ValueError("Task payload nesting or item count exceeds its safety limit.")
            if isinstance(item, dict):
                pending.extend((child, depth + 1) for child in item.values())
            elif isinstance(item, list):
                pending.extend((child, depth + 1) for child in item)
        return value


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


#: HTTP status for each envelope outcome.
#:
#: The endpoint used to return 200 for every result, including failures, because
#: the route pinned status_code=200 and the outcome lived only in the body. A
#: caller that checks the HTTP status - which is the normal thing to do - saw a
#: successful response for a task that did nothing. This is the same class of
#: defect as the original dead write path, which answered 200 with
#: status="ERROR" having stored no memory.
#:
#: The envelope body is unchanged, so callers that parse it keep working; the
#: status code now tells the truth as well.
_HTTP_FOR_STATUS = {
    "SUCCESS": status.HTTP_200_OK,
    "DENIED": status.HTTP_403_FORBIDDEN,
    # 422 as a literal: the named constant was renamed in Starlette
    # (HTTP_422_UNPROCESSABLE_ENTITY -> _CONTENT) and using either name pins the
    # code to one side of that rename or emits a deprecation warning.
    "ERROR": 422,
    "REJECTED": 422,
}


def _http_status_for(envelope_status: str) -> int:
    """500 for anything unrecognised, so a new outcome cannot masquerade as 200."""
    return _HTTP_FOR_STATUS.get(envelope_status, status.HTTP_500_INTERNAL_SERVER_ERROR)


@task_router.post("/execute", response_model=TaskResultModel)
def execute_task(
    envelope: TaskEnvelopeModel,
    response: Response,
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
        result = _execute_store(db, envelope, actor, t0)
    elif action in _QUERY_ACTIONS:
        result = _execute_query(db, envelope, actor, t0)
    else:
        result = TaskResultModel(
            task_id=envelope.task_id,
            target_agent=envelope.target_agent,
            status="ERROR",
            error=f"Unknown action '{envelope.action}'. Expected one of "
                  f"{sorted(_STORE_ACTIONS | _QUERY_ACTIONS)}.",
            execution_time_ms=_elapsed_ms(t0),
        )

    response.status_code = _http_status_for(result.status)
    return result


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
    if len(content) > 100_000:
        return TaskResultModel(
            task_id=envelope.task_id,
            target_agent=envelope.target_agent,
            status="ERROR",
            error="Task memory content exceeds the 100000-character limit.",
            execution_time_ms=_elapsed_ms(t0),
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
            tenant_id="default",
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
            target_agent=envelope.target_agent,
            status="REJECTED",
            error=f"{type(exc).__name__}: {exc}",
            summary="Write rejected by Memora security policy.",
            execution_time_ms=_elapsed_ms(t0),
        )
    except PermissionDeniedError as exc:
        return TaskResultModel(
            task_id=envelope.task_id,
            target_agent=envelope.target_agent,
            status="DENIED",
            error=str(exc),
            summary="Write denied by the Memora policy engine.",
            execution_time_ms=_elapsed_ms(t0),
        )
    except MemoryPipelineError as exc:
        return TaskResultModel(
            task_id=envelope.task_id,
            target_agent=envelope.target_agent,
            status="ERROR",
            error=str(exc),
            summary="Memory write pipeline rejected the payload.",
            execution_time_ms=_elapsed_ms(t0),
        )

    lat = _elapsed_ms(t0)
    return TaskResultModel(
        task_id=envelope.task_id,
        target_agent=envelope.target_agent,
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
            target_agent=envelope.target_agent,
            status="ERROR",
            error="A query, task_query, prompt, or content field is required to recall memories.",
            execution_time_ms=_elapsed_ms(t0),
        )
    if len(query) > 4096:
        return TaskResultModel(
            task_id=envelope.task_id,
            target_agent=envelope.target_agent,
            status="ERROR",
            error="Task query exceeds the 4096-character limit.",
            execution_time_ms=_elapsed_ms(t0),
        )

    try:
        results = SearchService.hybrid_search(
            db=db,
            query_text=query,
            actor_name=actor,
            tenant_id="default",
            namespace_path=payload.get("target_namespace_path"),
            limit=limit,
        )
    except PermissionDeniedError as exc:
        return TaskResultModel(
            task_id=envelope.task_id,
            target_agent=envelope.target_agent,
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
        target_agent=envelope.target_agent,
        status="SUCCESS",
        result={"count": len(recalled), "memories": recalled, "query": query},
        summary=f"Retrieved {len(recalled)} memory item(s) in {lat}ms",
        execution_time_ms=lat,
    )
