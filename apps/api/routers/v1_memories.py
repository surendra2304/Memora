"""
MEMORA v1 Memory Endpoints
Provides POST /v1/memories routing through the 10-step Write Pipeline.
"""
from typing import Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from storage.relational.session import get_db
from storage.relational.models import MemoryType, LifecycleState
from core.memory.schemas import MemoryRecordRead
from core.memory.pipeline.write_service import MemoryWriteService
from core.memory.pipeline.secret_scanner import SecretDetectedSecurityViolation
from core.policy.engine import PolicyEngine, PolicyDecision
from core.memory.service import PermissionDeniedError
from apps.api.dependencies import get_actor_header, get_purpose_header

router = APIRouter(prefix="/v1/memories", tags=["v1 Memories"])

class MemoryWriteRequest(BaseModel):
    content_text: str = Field(..., min_length=1, description="Raw content of the memory event")
    target_namespace_path: Optional[str] = Field(default=None, description="Destination namespace URI (e.g. 'memora://forge/projects/app-17')")
    memory_type: Optional[MemoryType] = Field(default=MemoryType.EPISODIC, description="Classification type of the memory")
    source: str = Field(default="api", description="Ingestion source (e.g. 'cli', 'voice', 'workflow')")
    provenance: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Structured trace and contextual metadata")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    importance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    allow_duplicates: bool = Field(default=False, description="If True, skips duplicate short-circuiting")

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