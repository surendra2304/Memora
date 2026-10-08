"""
Memory Ingestion, Query, Lifecycle, and Retrieval Endpoints
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from storage.relational.session import get_db
from storage.relational.models import MemoryType, LifecycleState, Namespace
from core.memory.service import (
    MemoryService,
    MemoryNotFoundError,
    PermissionDeniedError
)
from core.memory.pipeline.write_service import MemoryWriteService, MemoryPipelineError
from core.memory.pipeline.secret_scanner import SecretDetectedSecurityViolation
from core.memory.pipeline.poison_detector import PoisonMemoryViolation
from core.identity.service import IdentityService
from core.memory.schemas import (
    MemoryRecordCreate,
    MemoryRecordRead,
    MemoryQuery,
    MemoryTransitionRequest
)
from apps.api.dependencies import get_actor_header, get_purpose_header
import logging

logger = logging.getLogger("memora.api.legacy_memories")

router = APIRouter(prefix="/memories", tags=["Memories"])

@router.get("", response_model=List[MemoryRecordRead])
def list_memories(
    owner_name: Optional[str] = None,
    memory_type: Optional[MemoryType] = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000),
    actor_name: str = Depends(get_actor_header),
    purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db)
):
    # Keep the legacy list endpoint under the same identity and namespace
    # policy as /memories/query. A raw ORM listing bypassed private/shared
    # namespace checks and exposed every tenant memory to any caller.
    query = MemoryQuery(
        owner_name=owner_name.lower() if owner_name else None,
        memory_types=[memory_type] if memory_type else None,
        limit=limit,
        offset=offset,
    )
    try:
        return MemoryService.query_memories(
            db,
            query=query,
            actor_name=actor_name,
            purpose=purpose,
        )
    except PermissionDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

@router.post("", response_model=MemoryRecordRead, status_code=status.HTTP_201_CREATED)
def ingest_memory(
    memory_in: MemoryRecordCreate,
    actor_name: str = Depends(get_actor_header),
    purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db)
):
    try:
        authenticated_agent = IdentityService.get_agent_by_name(
            db, actor_name, tenant_id="default"
        )
        if not authenticated_agent:
            authenticated_agent = IdentityService.register_agent(
                db, actor_name, tenant_id="default"
            )

        if memory_in.owner_id and memory_in.owner_id != authenticated_agent.id:
            raise PermissionDeniedError("The request may not assign memory ownership to another agent.")
        if memory_in.owner_name and memory_in.owner_name.lower() != authenticated_agent.name:
            raise PermissionDeniedError("The request may not assign memory ownership to another agent.")
        if memory_in.agent_id and memory_in.agent_id not in {authenticated_agent.id, authenticated_agent.name}:
            raise PermissionDeniedError("The request agent_id must match the authenticated agent.")
        if memory_in.lifecycle_state not in {
            None,
            LifecycleState.CANDIDATE,
            LifecycleState.ACTIVE,
        }:
            raise PermissionDeniedError(
                "Initial ingestion cannot set trusted or terminal lifecycle states; use the controlled lifecycle workflow."
            )

        target_namespace_path = memory_in.namespace_path
        if memory_in.namespace_id:
            namespace = db.query(Namespace).filter(
                Namespace.id == memory_in.namespace_id,
                Namespace.tenant_id == authenticated_agent.tenant_id,
            ).first()
            if namespace is None:
                raise MemoryNotFoundError("Target namespace could not be resolved in the authenticated tenant.")
            target_namespace_path = namespace.path

        provenance = memory_in.provenance or {}
        result = MemoryWriteService.execute_pipeline(
            db=db,
            content_text=memory_in.content_text,
            caller_name=authenticated_agent.name,
            tenant_id=authenticated_agent.tenant_id,
            user_id=memory_in.user_id,
            agent_id=authenticated_agent.name,
            workspace_id=memory_in.workspace_id,
            device_id=memory_in.device_id,
            task_id=memory_in.task_id,
            idempotency_key=memory_in.idempotency_key,
            target_namespace_path=target_namespace_path,
            memory_type=memory_in.memory_type,
            source=memory_in.source,
            source_type=provenance.get("source_type"),
            trust_level=provenance.get("trust_level"),
            evidence_refs=provenance.get("evidence_refs"),
            provenance=provenance,
            confidence=memory_in.confidence,
            importance=memory_in.importance,
            purpose=purpose,
            valid_from=memory_in.valid_from,
            valid_until=memory_in.valid_until,
            expires_at=memory_in.expires_at,
        )
        return result.record
    except HTTPException:
        raise
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except (SecretDetectedSecurityViolation, PoisonMemoryViolation) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except (MemoryPipelineError, ValueError) as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception:
        logger.exception("Unhandled error in legacy memory ingestion")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal error while ingesting memory. The detail has been logged server-side.",
        )

@router.get("/{memory_id}", response_model=MemoryRecordRead)
def get_memory(
    memory_id: str,
    actor_name: str = Depends(get_actor_header),
    purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db)
):
    try:
        return MemoryService.get_memory_by_id(db, memory_id=memory_id, actor_name=actor_name, purpose=purpose)
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

@router.post("/query", response_model=List[MemoryRecordRead])
def query_memories(
    query: MemoryQuery,
    actor_name: str = Depends(get_actor_header),
    purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db)
):
    query.tenant_id = "default"  # API credentials currently carry no tenant claim.
    try:
        return MemoryService.query_memories(db, query=query, actor_name=actor_name, purpose=purpose)
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

@router.post("/{memory_id}/transition", response_model=MemoryRecordRead)
def transition_memory_lifecycle(
    memory_id: str,
    req: MemoryTransitionRequest,
    actor_name: str = Depends(get_actor_header),
    purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db)
):
    try:
        return MemoryService.transition_memory_state(
            db,
            memory_id=memory_id,
            target_state=req.target_state,
            actor_name=actor_name,
            superseded_by_id=req.superseded_by_id,
            purpose=req.purpose or purpose
        )
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
