"""Universal Task Execution Router for Memora (FRIDAY Universe Protocol)."""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from storage.relational.session import get_db
from storage.relational.models import MemoryRecord, MemoryType, LifecycleState, Namespace, Agent
from core.memory.schemas import MemoryRecordRead
from core.memory.pipeline.write_service import MemoryWriteService
from apps.api.dependencies import get_actor_header

task_router = APIRouter(prefix="/v1/task", tags=["Universal Task Protocol"])


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


@task_router.post("/execute", response_model=TaskResultModel, status_code=status.HTTP_200_OK)
def execute_task(
    envelope: TaskEnvelopeModel,
    db: Session = Depends(get_db),
    actor: str = Depends(get_actor_header),
) -> TaskResultModel:
    """Execute universal task envelope for persistent cognitive memory operations."""
    t0 = time.time()
    action = envelope.action.lower().strip()
    caller = envelope.source_agent or actor or "friday"

    try:
        if action in ("store", "remember", "add", "record"):
            content = str(
                envelope.payload.get("content")
                or envelope.payload.get("text")
                or envelope.payload.get("content_text")
                or envelope.payload.get("query")
                or str(envelope.payload)
            )
            ns_path = envelope.payload.get("target_namespace_path") or f"memora://{caller}/general"
            mtype_str = (envelope.payload.get("memory_type") or envelope.payload.get("type") or "observation").lower()
            
            # Map memory type
            mtype = MemoryType.EPISODIC
            if "semantic" in mtype_str:
                mtype = MemoryType.SEMANTIC
            elif "procedure" in mtype_str:
                mtype = MemoryType.PROCEDURAL

            write_service = MemoryWriteService(db)
            write_res = write_service.process_write(
                actor_name=caller,
                content_text=content,
                target_namespace_path=ns_path,
                memory_type=mtype,
                source=f"agent:{caller}",
                provenance={"task_id": envelope.task_id, "trace_id": envelope.trace_id},
                allow_duplicates=True,
            )

            lat = int((time.time() - t0) * 1000)
            return TaskResultModel(
                task_id=envelope.task_id,
                target_agent="memora",
                status="SUCCESS",
                result={
                    "memory_id": write_res.get("id"),
                    "namespace_path": ns_path,
                    "duplicate": write_res.get("is_duplicate", False),
                },
                summary=f"Stored memory into {ns_path} ({lat}ms)",
                execution_time_ms=lat,
            )

        else:
            # Query / Search memory
            query = str(
                envelope.payload.get("query")
                or envelope.payload.get("task_query")
                or envelope.payload.get("prompt")
                or envelope.payload.get("content")
                or ""
            )

            # Query database for recent matching records
            q = db.query(MemoryRecord).filter(
                MemoryRecord.lifecycle_state == LifecycleState.ACTIVE
            )
            if query:
                q = q.filter(MemoryRecord.content_text.ilike(f"%{query[:50]}%"))

            records = q.order_by(MemoryRecord.created_at.desc()).limit(5).all()
            recalled = [
                {"id": r.id, "content_text": r.content_text, "memory_type": r.memory_type.value if hasattr(r.memory_type, "value") else str(r.memory_type)}
                for r in records
            ]

            lat = int((time.time() - t0) * 1000)
            return TaskResultModel(
                task_id=envelope.task_id,
                target_agent="memora",
                status="SUCCESS",
                result={
                    "count": len(recalled),
                    "memories": recalled,
                    "query": query,
                },
                summary=f"Retrieved {len(recalled)} memory item(s) in {lat}ms",
                execution_time_ms=lat,
            )

    except Exception as e:
        lat = int((time.time() - t0) * 1000)
        return TaskResultModel(
            task_id=envelope.task_id,
            target_agent="memora",
            status="ERROR",
            error=str(e),
            execution_time_ms=lat,
        )
