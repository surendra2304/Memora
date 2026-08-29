"""
MEMORA v1 Memory Endpoints
Provides POST /v1/memories, lifecycle management (verify, supersede, archive, decay), and query.
"""
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from storage.relational.session import get_db
from storage.relational.models import MemoryType, LifecycleState
from core.memory.schemas import MemoryRecordRead, MemoryQuery
from core.memory.pipeline.write_service import MemoryWriteService
from core.memory.pipeline.secret_scanner import SecretDetectedSecurityViolation
from core.memory.service import MemoryService, MemoryNotFoundError, PermissionDeniedError
from core.policy.engine import PolicyEngine, PolicyDecision
from apps.api.dependencies import get_actor_header, get_purpose_header

router = APIRouter(prefix="/v1/memories", tags=["v1 Memories"])

class MemoryWriteRequest(BaseModel):
    content_text: str = Field(..., min_length=1, description="Raw content of the memory event")
    target_namespace_path: Optional[str] = Field(default=None, description="Destination namespace URI")
    memory_type: Optional[MemoryType] = Field(default=MemoryType.EPISODIC, description="Classification type")
    source: str = Field(default="api", description="Ingestion source")
    provenance: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Provenance metadata")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    importance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    allow_duplicates: bool = Field(default=False)

class MemoryWriteResponse(BaseModel):
    id: str
    namespace_id: str
    owner_id: str
    memory_type: MemoryType
    content_text: str
    source: str
    confidence: float
    importance: float
    lifecycle_state: LifecycleState
    is_duplicate: bool
    duplicate_of_id: Optional[str] = None
    step_trace: Dict[str, Any]

class MemoryVerifyRequest(BaseModel):
    notes: Optional[str] = None

class MemorySupersedeRequest(BaseModel):
    new_memory_id: str = Field(..., description="ID of the new canonical memory record that supersedes this record")
    reason: Optional[str] = None

class MemoryDecayRequest(BaseModel):
    decay_rate_per_day: float = 0.02
    unverified_threshold_days: int = 14
    archive_threshold: float = 0.15

class V1MemoryQueryRequest(BaseModel):
    query_text: Optional[str] = None
    namespace_path: Optional[str] = None
    owner_name: Optional[str] = None
    memory_types: Optional[List[MemoryType]] = None
    include_superseded: bool = False
    include_archived: bool = False
    include_deleted: bool = False
    min_confidence: Optional[float] = 0.0
    min_importance: Optional[float] = 0.0
    limit: int = 50
    offset: int = 0

@router.post("", response_model=MemoryWriteResponse, status_code=status.HTTP_201_CREATED)
def write_memory_event(
    req: MemoryWriteRequest,
    actor_name: str = Depends(get_actor_header),
    purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db)
):
    try:
        result = MemoryWriteService.execute_pipeline(
            db=db,
            content_text=req.content_text,
            caller_name=actor_name,
            target_namespace_path=req.target_namespace_path,
            memory_type=req.memory_type,
            source=req.source,
            provenance=req.provenance,
            confidence=req.confidence,
            importance=req.importance,
            purpose=purpose,
            allow_duplicates=req.allow_duplicates
        )

        return MemoryWriteResponse(
            id=result.record.id,
            namespace_id=result.record.namespace_id,
            owner_id=result.record.owner_id,
            memory_type=result.record.memory_type,
            content_text=result.record.content_text,
            source=result.record.source,
            confidence=result.record.confidence,
            importance=result.record.importance,
            lifecycle_state=result.record.lifecycle_state,
            is_duplicate=result.is_duplicate,
            duplicate_of_id=result.duplicate_of_id,
            step_trace=result.step_outputs
        )
    except SecretDetectedSecurityViolation as e:
        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                allowed=False,
                reason=str(e),
                rule_matched="SECRET_SCANNER_SECURITY_REJECTION",
                dimensions={"secret_types": e.secret_types, "caller": actor_name}
            )
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "SecurityPolicyViolation", "message": str(e), "flagged_secrets": e.secret_types}
        )
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("/{memory_id}", response_model=MemoryRecordRead)
def get_memory_record(
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
    query_req: V1MemoryQueryRequest,
    actor_name: str = Depends(get_actor_header),
    purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db)
):
    try:
        q = MemoryQuery(
            query_text=query_req.query_text,
            namespace_path=query_req.namespace_path,
            owner_name=query_req.owner_name,
            memory_types=query_req.memory_types,
            min_confidence=query_req.min_confidence,
            min_importance=query_req.min_importance,
            limit=query_req.limit,
            offset=query_req.offset
        )
        return MemoryService.query_memories(
            db,
            query=q,
            actor_name=actor_name,
            purpose=purpose,
            include_superseded=query_req.include_superseded,
            include_archived=query_req.include_archived,
            include_deleted=query_req.include_deleted
        )
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

@router.post("/{memory_id}/verify", response_model=MemoryRecordRead)
def verify_memory_endpoint(
    memory_id: str,
    req: Optional[MemoryVerifyRequest] = None,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    try:
        return MemoryService.verify_memory(
            db=db,
            memory_id=memory_id,
            actor_name=actor_name,
            notes=req.notes if req else None
        )
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

@router.post("/{memory_id}/supersede")
def supersede_memory_endpoint(
    memory_id: str,
    req: MemorySupersedeRequest,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    try:
        return MemoryService.supersede_memory(
            db=db,
            old_memory_id=memory_id,
            new_memory_id=req.new_memory_id,
            actor_name=actor_name,
            reason=req.reason
        )
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

@router.delete("/{memory_id}")
def delete_memory_endpoint(
    memory_id: str,
    hard: bool = Query(default=False, description="If True, performs hard deletion purge"),
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    try:
        return MemoryService.delete_memory(
            db=db,
            memory_id=memory_id,
            actor_name=actor_name,
            hard_delete=hard
        )
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

@router.post("/decay")
def trigger_memory_decay(
    req: Optional[MemoryDecayRequest] = None,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    rate = req.decay_rate_per_day if req else 0.02
    threshold_days = req.unverified_threshold_days if req else 14
    archive_thresh = req.archive_threshold if req else 0.15

    return MemoryService.apply_decay(
        db=db,
        decay_rate_per_day=rate,
        unverified_threshold_days=threshold_days,
        archive_threshold=archive_thresh,
        actor_name=actor_name
    )