"""
MEMORA v1 Memory Endpoints
Provides POST /v1/memories, GET /v1/memories/search (Hybrid Search),
POST /v1/memories/{id}/verify, POST /v1/memories/{id}/share,
POST /v1/memories/{id}/supersede, DELETE /v1/memories/{id}, and relationships.
"""
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from storage.relational.session import get_db
from storage.relational.models import (
    MemoryRecord,
    MemoryType,
    LifecycleState,
    Namespace,
    Agent
)
from core.memory.schemas import MemoryRecordRead, MemoryQuery
from core.memory.pipeline.write_service import MemoryWriteService
from core.memory.pipeline.secret_scanner import SecretDetectedSecurityViolation
from core.memory.service import MemoryService, MemoryNotFoundError, PermissionDeniedError
from core.identity.service import IdentityService
from core.memory.graph_service import GraphService
from core.memory.search_service import SearchService, SearchResultItem
from core.policy.engine import PolicyEngine, PolicyDecision
from core.events.emitter import event_emitter
from core.memory.experience_service import ExperienceLearnerService, LearnExperienceRequest
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

class MemoryShareRequest(BaseModel):
    target_agent_name: str = Field(..., min_length=2, description="Handle of agent receiving access")
    actions: List[str] = Field(default_factory=lambda: ["read"], description="Permitted action list")
    purpose: Optional[str] = Field(default=None, description="Operational reason for sharing")
    ttl_hours: Optional[int] = Field(default=None, ge=1, le=8760, description="Grant TTL expiration in hours")

class MemorySupersedeRequest(BaseModel):
    new_memory_id: str = Field(..., description="ID of the new canonical memory record that supersedes this record")
    reason: Optional[str] = None

class MemoryDecayRequest(BaseModel):
    decay_rate_per_day: float = 0.02
    unverified_threshold_days: int = 14
    archive_threshold: float = 0.15

class MemoryRelationshipCreate(BaseModel):
    target_memory_id: str = Field(..., description="Destination memory ID")
    relationship_type: str = Field(default="relates_to", description="Graph edge type")
    weight: float = Field(default=1.0, ge=0.0, le=1.0)

class HybridSearchResultResponse(BaseModel):
    id: str
    namespace_id: str
    namespace_path: Optional[str] = None
    owner_name: Optional[str] = None
    memory_type: str
    content_text: str
    confidence: float
    importance: float
    lifecycle_state: str
    created_at: Optional[str] = None
    final_score: float
    semantic_score: float
    keyword_score: float
    graph_boost: float
    match_reasons: List[str]

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

@router.post("/learn-experience", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
def learn_experience_endpoint(
    req: LearnExperienceRequest,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    calling_agent = req.agent_id or actor_name
    try:
        record = ExperienceLearnerService.learn_experience(
            db=db,
            actor_name=calling_agent,
            outcomes=req.outcomes,
            namespace_path=req.namespace_path
        )
        return {
            "id": record.id,
            "namespace_id": record.namespace_id,
            "owner_id": record.owner_id,
            "memory_type": record.memory_type.value,
            "content_text": record.content_text,
            "confidence": record.confidence,
            "importance": record.importance,
            "lifecycle_state": record.lifecycle_state.value,
            "provenance": record.provenance or {}
        }
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("/search", response_model=List[HybridSearchResultResponse])
def search_memories_get(
    q: str = Query(..., min_length=1, description="Query string for hybrid search"),
    namespace_path: Optional[str] = Query(None, description="Optional namespace filter"),
    limit: int = Query(10, ge=1, le=100),
    min_score: float = Query(0.0, ge=0.0, le=1.0),
    include_superseded: bool = Query(False),
    include_archived: bool = Query(False),
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    results = SearchService.hybrid_search(
        db=db,
        query_text=q,
        actor_name=actor_name,
        namespace_path=namespace_path,
        min_score=min_score,
        limit=limit,
        include_superseded=include_superseded,
        include_archived=include_archived
    )
    return [r.to_dict() for r in results]

@router.post("/query", response_model=List[MemoryRecordRead])
def query_memories(
    query_req: MemoryQuery,
    actor_name: str = Depends(get_actor_header),
    purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db)
):
    try:
        return MemoryService.query_memories(
            db,
            query=query_req,
            actor_name=actor_name,
            purpose=purpose
        )
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

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

@router.post("/{memory_id}/verify", response_model=MemoryRecordRead)
def verify_memory_endpoint(
    memory_id: str,
    req: Optional[MemoryVerifyRequest] = None,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    try:
        record = MemoryService.verify_memory(
            db=db,
            memory_id=memory_id,
            actor_name=actor_name,
            notes=req.notes if req else None
        )
        event_emitter.publish("memory.updated", {"memory_id": memory_id, "action": "verify", "actor": actor_name})
        return record
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

@router.post("/{memory_id}/share")
def share_memory_endpoint(
    memory_id: str,
    req: MemoryShareRequest,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    try:
        record = db.query(MemoryRecord).filter(MemoryRecord.id == memory_id).first()
        if not record:
            raise MemoryNotFoundError(f"Memory with ID '{memory_id}' not found.")

        caller = IdentityService.get_agent_by_name(db, actor_name)
        if not caller:
            caller = IdentityService.register_agent(db, actor_name)

        namespace = record.namespace or db.query(Namespace).filter(Namespace.id == record.namespace_id).first()

        grant = IdentityService.grant_access(
            db=db,
            agent_name=req.target_agent_name,
            namespace_id=record.namespace_id,
            actions=req.actions,
            purpose=req.purpose,
            ttl_hours=req.ttl_hours
        )

        event_emitter.publish("memory.shared", {
            "memory_id": memory_id,
            "shared_by": actor_name,
            "shared_with": req.target_agent_name,
            "namespace_path": namespace.path if namespace else None,
            "actions": req.actions
        })

        return {
            "status": "shared",
            "memory_id": memory_id,
            "grant_id": grant.id,
            "shared_with": req.target_agent_name,
            "actions": req.actions,
            "purpose": req.purpose,
            "expires_at": grant.expires_at.isoformat() if grant.expires_at else None
        }
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.post("/{memory_id}/supersede")
def supersede_memory_endpoint(
    memory_id: str,
    req: MemorySupersedeRequest,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    try:
        res = MemoryService.supersede_memory(
            db=db,
            old_memory_id=memory_id,
            new_memory_id=req.new_memory_id,
            actor_name=actor_name,
            reason=req.reason
        )
        event_emitter.publish("memory.superseded", {
            "superseded_id": res["superseded_id"],
            "winner_id": res["winner_id"],
            "actor": actor_name,
            "reason": res["reason"]
        })
        return res
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

@router.post("/{memory_id}/relationships")
def create_memory_relationship(
    memory_id: str,
    req: MemoryRelationshipCreate,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    try:
        rel = GraphService.create_relationship(
            db=db,
            source_memory_id=memory_id,
            target_memory_id=req.target_memory_id,
            relationship_type=req.relationship_type,
            weight=req.weight
        )
        return {
            "status": "created",
            "id": rel.id,
            "source_memory_id": rel.source_memory_id,
            "target_memory_id": rel.target_memory_id,
            "relationship_type": rel.relationship_type,
            "weight": rel.weight
        }
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

@router.get("/{memory_id}/graph")
def get_memory_graph(
    memory_id: str,
    max_hops: int = Query(2, ge=1, le=5),
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    return GraphService.get_connected_memories(db=db, memory_id=memory_id, max_hops=max_hops)

@router.delete("/{memory_id}")
def delete_memory_endpoint(
    memory_id: str,
    hard: bool = Query(default=False, description="If True, performs hard deletion purge"),
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    try:
        res = MemoryService.delete_memory(
            db=db,
            memory_id=memory_id,
            actor_name=actor_name,
            hard_delete=hard
        )
        event_emitter.publish("memory.updated", {"memory_id": memory_id, "action": "delete", "hard": hard})
        return res
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