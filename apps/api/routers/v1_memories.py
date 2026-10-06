"""
MEMORA v1 Memory Endpoints
Provides POST /v1/memories, GET /v1/memories/search (Hybrid Search),
POST /v1/memories/{id}/verify, POST /v1/memories/{id}/share,
POST /v1/memories/{id}/supersede, DELETE /v1/memories/{id}, and relationships.
"""
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from storage.relational.session import get_db, storage_receipt, SessionLocal
from storage.relational.models import (
    MemoryRecord,
    MemoryType,
    LifecycleState,
    Namespace
)
from core.memory.schemas import MemoryRecordRead, MemoryQuery, MemoryPromoteRequest
from core.memory.pipeline.write_service import MemoryWriteService, MemoryPipelineError
from core.memory.pipeline.secret_scanner import SecretDetectedSecurityViolation
from core.memory.pipeline.poison_detector import PoisonMemoryViolation
from core.memory.service import MemoryService, MemoryNotFoundError, PermissionDeniedError
from core.identity.service import IdentityService
from core.memory.graph_service import GraphService, InvalidRelationshipError
from core.memory.search_service import SearchService
from core.policy.engine import PolicyEngine, PolicyDecision
from core.events.emitter import event_emitter
from core.memory.experience_service import ExperienceLearnerService, LearnExperienceRequest
from core.memory.pipeline.preference_extractor import PreferenceExtractor
from apps.api.dependencies import authenticate_agent, get_actor_header, get_purpose_header
from datetime import datetime
import logging

logger = logging.getLogger("memora.api.memories")


#: Exceptions whose message is authored by this codebase to describe a problem
#: with the request. The caller needs the text; it says nothing about internals.
_CLIENT_INPUT_ERRORS = (MemoryPipelineError, ValueError)


def _unexpected_write_error(e: Exception, where: str) -> HTTPException:
    """Map an unhandled exception onto the right status without leaking internals.

    These handlers used to answer 400 with detail=str(e) for every exception,
    which put internal exception text in the response body. When the failure was
    a database error that meant shipping the failing SQL statement, the bound
    parameter list and the sqlite3 exception class to the caller - observed live
    under concurrent load, where a UNIQUE-constraint violation on the
    idempotency index was returned verbatim.

    It was also the wrong status for a server fault: reporting one as 400 tells a
    caller to fix its payload when nothing about the payload was wrong.

    Input rejections are the exception to that. They are raised deliberately by
    this codebase with a message meant for the caller, and turning them into an
    opaque 500 would make the API impossible to use correctly - a caller
    submitting a malformed namespace path has to be told which part was bad.
    """
    if isinstance(e, _CLIENT_INPUT_ERRORS):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    logger.exception("Unhandled error in %s", where)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=f"Internal error while processing {where}. The detail has been "
               f"logged server-side.",
    )

router = APIRouter(
    prefix="/v1/memories",
    tags=["v1 Memories"],
    dependencies=[Depends(authenticate_agent)],
)


def _require_memory_read_access(db: Session, memory_id: str, actor_name: str, purpose: Optional[str]):
    """Resolve the caller and assert it may read `memory_id`; return the Agent row.

    Shared by the graph endpoints, which previously performed no authorization at
    all. Unknown ids are 404, an unregistered caller or a denied read is 403, so
    neither the existence nor the content of an inaccessible memory is revealed
    beyond the distinction the caller already has rights to make.
    """
    actor = IdentityService.get_agent_by_name(db, actor_name)
    if not actor:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Authenticated agent '{actor_name}' is not registered in Memora.",
        )

    record = db.query(MemoryRecord).filter(MemoryRecord.id == memory_id).first()
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Memory with ID '{memory_id}' not found.",
        )

    decision = PolicyEngine.evaluate_access(
        db,
        actor=actor,
        namespace=record.namespace,
        action="read",
        purpose=purpose,
        memory_id=record.id,
        log_audit=False,
    )
    if not decision.allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=decision.reason)
    return actor

class MemoryWriteRequest(BaseModel):
    user_id: Optional[str] = Field(default="default_user", description="Identity scope: User ID")
    agent_id: Optional[str] = Field(default=None, description="Identity scope: Calling agent ID")
    workspace_id: Optional[str] = Field(default="default_workspace", description="Identity scope: Workspace boundary")
    device_id: Optional[str] = Field(default="default_device", description="Identity scope: Device ID")
    task_id: Optional[str] = Field(default=None, description="Identity scope: Task context ID")
    idempotency_key: Optional[str] = Field(default=None, description="Idempotency key for deduplicated write")
    content_text: str = Field(..., min_length=1, description="Raw content of the memory event")
    target_namespace_path: Optional[str] = Field(default=None, description="Destination namespace URI")
    memory_type: Optional[MemoryType] = Field(default=MemoryType.EPISODIC, description="Classification type")
    source: str = Field(default="api", description="Ingestion source")
    source_type: Optional[str] = Field(default=None, description="Provenance source type")
    trust_level: Optional[str] = Field(default=None, description="Provenance trust level")
    evidence_refs: Optional[List[str]] = Field(default=None, description="Evidence reference URLs or IDs")
    provenance: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Provenance metadata")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    importance: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    expires_at: Optional[datetime] = Field(default=None, description="Temporal expiry timestamp")
    allow_duplicates: bool = Field(default=False)

class MemoryWriteResponse(BaseModel):
    id: str
    tenant_id: str = "default"
    user_id: str = "default_user"
    agent_id: str = "friday"
    workspace_id: str = "default_workspace"
    device_id: str = "default_device"
    task_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    namespace_id: str
    owner_id: str
    memory_type: MemoryType
    content_text: str
    source: str
    provenance: Dict[str, Any] = Field(default_factory=dict)
    confidence: float
    importance: float
    lifecycle_state: LifecycleState
    is_duplicate: bool
    duplicate_of_id: Optional[str] = None
    storage_backend: str
    storage_durable: bool
    storage_durability: str
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
    calling_agent = req.agent_id or actor_name

    def _run_write(session: Session):
        return MemoryWriteService.execute_pipeline(
            db=session,
            content_text=req.content_text,
            caller_name=calling_agent,
            user_id=req.user_id,
            agent_id=calling_agent,
            workspace_id=req.workspace_id,
            device_id=req.device_id,
            task_id=req.task_id,
            idempotency_key=req.idempotency_key,
            target_namespace_path=req.target_namespace_path,
            memory_type=req.memory_type,
            source=req.source,
            source_type=req.source_type,
            trust_level=req.trust_level,
            evidence_refs=req.evidence_refs,
            provenance=req.provenance,
            confidence=req.confidence,
            importance=req.importance,
            expires_at=req.expires_at,
            purpose=purpose,
            allow_duplicates=req.allow_duplicates
        )

    #: Session used for audit writes. Replaced when a retry moves the work onto a
    #: fresh session, so the handlers below never write through a poisoned one.
    audit_db = db
    retry_db: Optional[Session] = None

    try:
        try:
            result = _run_write(db)
        except IntegrityError:
            # Two concurrent writes carrying the same idempotency key can both
            # clear the pipeline's pre-insert check and then collide on the
            # unique index. The loser's session is left in a pending-rollback
            # state and cannot be reused, so the only sound recovery is to start
            # again on a fresh session: the retry's pre-check now finds the
            # winner and returns it as an idempotent hit, which is exactly what a
            # sequential replay would have returned.
            #
            # Without this the loser's IntegrityError reached the caller. Under
            # load - 9 agents x 40 writes - 437 of 1080 writes were rejected.
            #
            # Only a caller that supplied a retry token can be raced this way, so
            # anything else is a genuine integrity failure and must surface.
            if not req.idempotency_key:
                raise
            retry_db = SessionLocal()
            audit_db = retry_db
            result = _run_write(retry_db)

        storage = result.to_dict()
        return MemoryWriteResponse(
            id=result.record.id,
            tenant_id=getattr(result.record, "tenant_id", "default"),
            user_id=getattr(result.record, "user_id", "default_user"),
            agent_id=getattr(result.record, "agent_id", "friday"),
            workspace_id=getattr(result.record, "workspace_id", "default_workspace"),
            device_id=getattr(result.record, "device_id", "default_device"),
            task_id=getattr(result.record, "task_id", None),
            idempotency_key=getattr(result.record, "idempotency_key", None),
            namespace_id=result.record.namespace_id,
            owner_id=result.record.owner_id,
            memory_type=result.record.memory_type,
            content_text=result.record.content_text,
            source=result.record.source,
            provenance=result.record.provenance or {},
            confidence=result.record.confidence,
            importance=result.record.importance,
            lifecycle_state=result.record.lifecycle_state,
            is_duplicate=result.is_duplicate,
            duplicate_of_id=result.duplicate_of_id,
            storage_backend=storage["storage_backend"],
            storage_durable=storage["storage_durable"],
            storage_durability=storage["storage_durability"],
            step_trace=result.step_outputs
        )
    except SecretDetectedSecurityViolation as e:
        PolicyEngine.log_audit_decision(
            audit_db,
            PolicyDecision(
                allowed=False,
                reason=str(e),
                rule_matched="SECRET_SCANNER_SECURITY_REJECTION",
                dimensions={"secret_types": e.secret_types, "caller": actor_name}
            )
        )
        raise HTTPException(
            status_code=422,
            detail={"error": "SecurityPolicyViolation", "message": str(e), "flagged_secrets": e.secret_types}
        )
    except PoisonMemoryViolation as e:
        PolicyEngine.log_audit_decision(
            audit_db,
            PolicyDecision(
                allowed=False,
                reason=str(e),
                rule_matched="POISON_MEMORY_SECURITY_REJECTION",
                dimensions={"detected_patterns": e.detected_patterns, "caller": actor_name}
            )
        )
        raise HTTPException(
            status_code=422,
            detail={"error": "PoisonMemoryViolation", "message": str(e), "detected_patterns": e.detected_patterns}
        )
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        raise _unexpected_write_error(e, "this request")
    finally:
        if retry_db is not None:
            retry_db.close()

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
        raise _unexpected_write_error(e, "this request")

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
@router.get("/experience")
def get_experience_memories(
    domain: Optional[str] = Query(None, description="Optional domain filter"),
    limit: int = Query(5, ge=1, le=20),
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    try:
        records = ExperienceLearnerService.get_active_experiences(
            db=db,
            actor_name=actor_name,
            domain=domain,
            limit=limit
        )
        return [
            {
                "id": r.id,
                "content_text": r.content_text,
                "memory_type": r.memory_type.value,
                "importance": r.importance,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "provenance": r.provenance
            }
            for r in records
        ]
    except Exception as e:
        raise _unexpected_write_error(e, "this request")


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
        event_emitter.publish("memory.updated", {"memory_id": memory_id, "action": "verify", "actor": actor_name}, db=db)
        db.commit()
        return record
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

@router.post("/{memory_id}/promote", response_model=MemoryRecordRead)
def promote_memory_endpoint(
    memory_id: str,
    req: MemoryPromoteRequest,
    actor_name: str = Depends(get_actor_header),
    purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db)
):
    try:
        record = MemoryService.promote_to_semantic(
            db=db,
            memory_id=memory_id,
            promoted_by=req.promoted_by or actor_name,
            verification_evidence=req.verification_evidence,
            target_confidence=req.target_confidence,
            purpose=req.purpose or purpose
        )
        return record
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except (ValueError, PoisonMemoryViolation) as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
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
        }, db=db)
        db.commit()

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
        raise _unexpected_write_error(e, "this request")

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
        }, db=db)
        db.commit()
        return res
    except MemoryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except PermissionDeniedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except ValueError as e:
        # Self-supersession and supersession cycles. These conflict with the
        # state the records are already in rather than with the shape of the
        # request, so 409 rather than 422. Without this handler they escaped as
        # an unhandled 500.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

@router.post("/{memory_id}/relationships")
def create_memory_relationship(
    memory_id: str,
    req: MemoryRelationshipCreate,
    actor_name: str = Depends(get_actor_header),
    purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db)
):
    """Create a graph edge between two memories the caller may actually read.

    An edge is itself a disclosure: it proves both endpoints exist. Previously
    any authenticated agent could link any two memory ids in the fabric, and the
    resulting traversal handed back ids from other agents' private namespaces.
    """
    for candidate_id in (memory_id, req.target_memory_id):
        _require_memory_read_access(db, candidate_id, actor_name, purpose)

    try:
        rel = GraphService.create_relationship(
            db=db,
            source_memory_id=memory_id,
            target_memory_id=req.target_memory_id,
            relationship_type=req.relationship_type,
            weight=req.weight
        )
    except InvalidRelationshipError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        raise _unexpected_write_error(e, "this request")

    return {
        "status": "created",
        "id": rel.id,
        "source_memory_id": rel.source_memory_id,
        "target_memory_id": rel.target_memory_id,
        "relationship_type": rel.relationship_type,
        "weight": rel.weight
    }

@router.get("/{memory_id}/graph")
def get_memory_graph(
    memory_id: str,
    max_hops: int = Query(2, ge=1, le=5),
    actor_name: str = Depends(get_actor_header),
    purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db)
):
    actor = _require_memory_read_access(db, memory_id, actor_name, purpose)
    return GraphService.get_connected_memories(
        db=db,
        memory_id=memory_id,
        max_hops=max_hops,
        actor=actor,
        purpose=purpose,
    )

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
        event_emitter.publish("memory.updated", {"memory_id": memory_id, "action": "delete", "hard": hard}, db=db)
        db.commit()
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


class RecordInteractionRequest(BaseModel):
    agent_name: Optional[str] = Field(default=None, description="Subsystem agent name")
    user_text: str = Field(..., description="User input utterance or prompt")
    agent_text: Optional[str] = Field(default="", description="Agent output response or action")
    event_type: str = Field(default="dialogue", description="Event classification")
    tags: Optional[List[str]] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)


@router.post("/record-interaction", status_code=status.HTTP_201_CREATED)
def record_interaction_endpoint(
    req: RecordInteractionRequest,
    actor_name: str = Depends(get_actor_header),
    purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db)
):
    calling_agent = (req.agent_name or actor_name).lower()
    created_records: List[str] = []
    extracted_facts: List[Dict[str, Any]] = []
    #: Per-item outcomes. This endpoint previously wrapped every write in a bare
    #: `except Exception: pass` and then reported `"status": "success"` with
    #: `recorded_count: 0`, so a rejected secret, a blocked prompt injection, and
    #: a genuinely successful no-op were all indistinguishable to the caller.
    skipped: List[Dict[str, Any]] = []
    security_rejections: List[Dict[str, Any]] = []

    def _classify_outcome(stage: str, label: str, exc: Exception) -> None:
        """Record why a write did not land, and keep security refusals visible."""
        if isinstance(exc, SecretDetectedSecurityViolation):
            security_rejections.append({
                "stage": stage,
                "item": label,
                "reason": "SecurityPolicyViolation",
                "flagged_secrets": exc.secret_types,
            })
        elif isinstance(exc, PoisonMemoryViolation):
            security_rejections.append({
                "stage": stage,
                "item": label,
                "reason": "PoisonMemoryViolation",
                "detected_patterns": exc.detected_patterns,
            })
        elif isinstance(exc, PermissionDeniedError):
            skipped.append({"stage": stage, "item": label, "reason": "policy_denied", "detail": str(exc)})
        else:
            skipped.append({"stage": stage, "item": label, "reason": type(exc).__name__, "detail": str(exc)})

    # 1. Automatic Fact & Preference Extraction. One bad fact must not abort the
    #    rest of the turn, but the reason has to reach the caller.
    facts = PreferenceExtractor.extract_facts(req.user_text)
    for fact in facts:
        try:
            write_res = MemoryWriteService.execute_pipeline(
                db=db,
                content_text=fact.normalized_fact,
                caller_name=calling_agent,
                memory_type=MemoryType.SEMANTIC,
                source=f"agent:{calling_agent}",
                provenance={
                    "category": fact.category,
                    "entities": fact.entities,
                    "raw_statement": fact.raw_statement,
                    "event_type": req.event_type
                },
                confidence=fact.confidence,
                importance=fact.importance,
                purpose=purpose or "Autonomous user preference extraction",
                allow_duplicates=False
            )
            created_records.append(write_res.record.id)
            extracted_facts.append(fact.to_dict())
        except Exception as e:
            _classify_outcome("fact_extraction", fact.normalized_fact[:80], e)

    # 2. Episodic Turn Recording
    episodic_recorded = False
    episodic_content = (
        f"User: {req.user_text} | Assistant: {req.agent_text}" if req.agent_text else f"User: {req.user_text}"
    )
    try:
        write_res = MemoryWriteService.execute_pipeline(
            db=db,
            content_text=episodic_content,
            caller_name=calling_agent,
            memory_type=MemoryType.EPISODIC,
            source=f"agent:{calling_agent}",
            provenance={
                "event_type": req.event_type,
                "tags": req.tags,
                "metadata": req.metadata
            },
            confidence=1.0,
            importance=0.7,
            purpose=purpose or "Autonomous interaction logging",
            allow_duplicates=False
        )
        created_records.append(write_res.record.id)
        episodic_recorded = True
    except Exception as e:
        _classify_outcome("episodic_turn", "episodic_turn", e)

    storage = storage_receipt()
    body: Dict[str, Any] = {
        "agent": calling_agent,
        "recorded_count": len(created_records),
        "memory_ids": created_records,
        "extracted_facts": extracted_facts,
        "episodic_recorded": episodic_recorded,
        "skipped": skipped,
        "security_rejections": security_rejections,
        "storage_backend": storage["backend"],
        "storage_durable": storage["durable"],
        "storage_durability": storage["durability"],
    }

    # A security scanner that fired is a refusal, not a quiet no-op.
    if security_rejections and not created_records:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "SecurityPolicyViolation", "security_rejections": security_rejections},
        )
    if security_rejections:
        body["status"] = "partial_security_rejection"
        return JSONResponse(status_code=status.HTTP_207_MULTI_STATUS, content=body)

    if not created_records:
        # Nothing was stored at all: the caller must not be told this succeeded.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "NothingRecorded", "skipped": skipped},
        )

    body["status"] = "success"
    return body


class LearnOutcomeRequest(BaseModel):
    agent_name: Optional[str] = Field(default=None, description="Calling agent name")
    task_name: str = Field(..., min_length=1, description="Executed task identifier")
    status: str = Field(..., description="'failure' or 'success'")
    error_log: Optional[str] = None
    actions_taken: Optional[str] = None
    context: Optional[str] = None
    domain: Optional[str] = None
    namespace_path: Optional[str] = None


@router.post("/learn-outcome", status_code=status.HTTP_201_CREATED)
def learn_outcome_endpoint(
    req: LearnOutcomeRequest,
    actor_name: str = Depends(get_actor_header),
    db: Session = Depends(get_db)
):
    calling_agent = (req.agent_name or actor_name).lower()
    try:
        record = ExperienceLearnerService.learn_single_outcome(
            db=db,
            actor_name=calling_agent,
            task_name=req.task_name,
            status=req.status,
            error_log=req.error_log,
            actions_taken=req.actions_taken,
            context=req.context,
            domain=req.domain,
            namespace_path=req.namespace_path
        )
        storage = storage_receipt()
        return {
            "status": "learned",
            "id": record.id,
            "agent": calling_agent,
            "memory_type": "experience",
            "synthesized_rule": record.content_text,
            "importance": record.importance,
            "storage_backend": storage["backend"],
            "storage_durable": storage["durable"],
            "storage_durability": storage["durability"],
        }
    except Exception as e:
        raise _unexpected_write_error(e, "this request")
