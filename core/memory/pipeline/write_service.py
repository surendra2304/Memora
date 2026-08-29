"""
Memory Write Service for Memora
Executes the deterministic 10-Step Memory Write Pipeline before persisting memory records.
"""
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import hashlib
import time
import logging

from storage.relational.models import MemoryRecord, MemoryType, LifecycleState, NamespaceType
from storage.vector.qdrant_adapter import vector_adapter
from storage.vector.embedding import EmbeddingGenerator
from core.identity.service import IdentityService
from core.policy.engine import PolicyEngine, PolicyDecision
from core.metrics.collector import metrics_collector
from core.events.emitter import event_emitter
from core.memory.pipeline.secret_scanner import SecretScanner, SecretLeakageError
from core.memory.pipeline.entity_extractor import EntityExtractor
from core.memory.pipeline.deduplication import DeduplicationEngine
from core.memory.service import PermissionDeniedError

logger = logging.getLogger(__name__)

class MemoryPipelineError(Exception):
    pass

class MemoryWriteResult:
    def __init__(
        self,
        record: MemoryRecord,
        step_outputs: Dict[str, Any],
        is_duplicate: bool = False,
        duplicate_of_id: Optional[str] = None
    ):
        self.record = record
        self.step_outputs = step_outputs
        self.is_duplicate = is_duplicate
        self.duplicate_of_id = duplicate_of_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.record.id,
            "namespace_id": self.record.namespace_id,
            "owner_id": self.record.owner_id,
            "memory_type": self.record.memory_type.value,
            "content_text": self.record.content_text,
            "confidence": self.record.confidence,
            "importance": self.record.importance,
            "lifecycle_state": self.record.lifecycle_state.value,
            "created_at": self.record.created_at.isoformat() if self.record.created_at else None,
            "is_duplicate": self.is_duplicate,
            "duplicate_of_id": self.duplicate_of_id,
            "step_trace": self.step_outputs
        }

class MemoryWriteService:
    @classmethod
    def execute_pipeline(
        cls,
        db: Session,
        content_text: str,
        caller_name: Optional[str] = None,
        actor_name: Optional[str] = None,
        target_namespace_path: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        source: str = "api",
        provenance: Optional[Dict[str, Any]] = None,
        confidence: Optional[float] = None,
        importance: Optional[float] = None,
        purpose: Optional[str] = None,
        allow_duplicates: bool = False
    ) -> MemoryWriteResult:
        start_time = time.time()
        step_trace: Dict[str, Any] = {}
        resolved_actor_name = caller_name or actor_name or "system"

        try:
            # -------------------------------------------------------------
            # STEP 1: RECEIVE MEMORY EVENT
            # -------------------------------------------------------------
            if not content_text or not content_text.strip():
                raise MemoryPipelineError("Content text cannot be empty.")
            
            step_trace["step_1_receive_event"] = {
                "raw_length": len(content_text),
                "source": source,
                "target_namespace_path": target_namespace_path,
                "requested_type": memory_type.value if memory_type else None
            }

            # -------------------------------------------------------------
            # STEP 2: AUTHENTICATE CALLER AND RESOLVE NAMESPACE
            # -------------------------------------------------------------
            actor = IdentityService.get_agent_by_name(db, resolved_actor_name)
            if not actor:
                actor = IdentityService.register_agent(db, name=resolved_actor_name, role="worker")

            target_path = target_namespace_path or f"memora://{actor.name}/private"
            namespace = IdentityService.resolve_namespace(db, target_path, owner_agent_id=actor.id)
            step_trace["step_2_authenticate_and_resolve"] = {
                "actor_id": actor.id,
                "actor_name": actor.name,
                "namespace_id": namespace.id,
                "namespace_path": namespace.path,
                "namespace_type": namespace.type.value
            }

            # -------------------------------------------------------------
            # STEP 3: CLASSIFY MEMORY TYPE AND SENSITIVITY (SECRET SCANNING)
            # -------------------------------------------------------------
            SecretScanner.validate_content_safety(content_text)

            resolved_type = memory_type or MemoryType.EPISODIC
            step_trace["step_3_classify_and_scan"] = {
                "memory_type": resolved_type.value,
                "sensitivity": "CONFIDENTIAL" if namespace.type == NamespaceType.AGENT_PRIVATE else "INTERNAL",
                "secrets_detected": False
            }

            # -------------------------------------------------------------
            # STEP 4: NORMALIZE CONTENT INTO STRUCTURED FORMAT
            # -------------------------------------------------------------
            normalized_content = " ".join(content_text.strip().split())
            content_hash = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
            step_trace["step_4_normalize_content"] = {
                "char_count": len(normalized_content),
                "sha256": content_hash
            }

            # -------------------------------------------------------------
            # STEP 5: EXTRACT ENTITIES AND RELATIONSHIPS (DEEP EXTRACTION)
            # -------------------------------------------------------------
            extracted_meta = EntityExtractor.extract_entities_and_relationships(normalized_content)
            step_trace["step_5_extract_entities"] = extracted_meta

            # -------------------------------------------------------------
            # STEP 6: DETECT DUPLICATES OR CONTRADICTIONS
            # -------------------------------------------------------------
            dedup_result = DeduplicationEngine.check_duplicates_and_contradictions(
                db,
                namespace_id=namespace.id,
                content_text=normalized_content
            )
            step_trace["step_6_deduplication"] = {
                "is_duplicate": dedup_result.is_duplicate,
                "duplicate_of_id": dedup_result.duplicate_of_id,
                "similarity_score": dedup_result.similarity_score
            }

            if dedup_result.is_duplicate and not allow_duplicates and dedup_result.duplicate_of_id:
                existing = db.query(MemoryRecord).filter(MemoryRecord.id == dedup_result.duplicate_of_id).first()
                if existing:
                    metrics_collector.record_write(success=True, is_contradiction=False, latency_ms=(time.time() - start_time) * 1000)
                    return MemoryWriteResult(
                        record=existing,
                        step_outputs=step_trace,
                        is_duplicate=True,
                        duplicate_of_id=existing.id
                    )

            # -------------------------------------------------------------
            # STEP 7: ASSIGN CONFIDENCE, IMPORTANCE, AND RETENTION METADATA
            # -------------------------------------------------------------
            computed_confidence = confidence if confidence is not None else 1.0
            computed_importance = importance if importance is not None else (0.8 if extracted_meta["triples"] else 0.5)
            retention_tier = "HOT" if computed_importance >= 0.75 else "STANDARD"

            step_trace["step_7_assign_metadata"] = {
                "confidence": computed_confidence,
                "importance": computed_importance,
                "retention_tier": retention_tier
            }

            # -------------------------------------------------------------
            # STEP 8: APPLY ACCESS AND SHARING POLICY
            # -------------------------------------------------------------
            policy_decision = PolicyEngine.evaluate_access(
                db,
                actor=actor,
                namespace=namespace,
                action="write",
                purpose=purpose
            )
            if not policy_decision.allowed:
                raise PermissionDeniedError(policy_decision.reason)

            step_trace["step_8_apply_policy"] = policy_decision.to_dict()

            # -------------------------------------------------------------
            # STEP 9: PERSIST TO POSTGRESQL, VECTOR INDEX & KNOWLEDGE GRAPH
            # -------------------------------------------------------------
            combined_provenance = {
                **(provenance or {}),
                "content_sha256": content_hash,
                "extracted_entities": extracted_meta,
                "retention_tier": retention_tier,
                "pipeline_version": "1.0.0"
            }

            record = MemoryRecord(
                namespace_id=namespace.id,
                owner_id=actor.id,
                memory_type=resolved_type,
                content_text=normalized_content,
                source=source,
                provenance=combined_provenance,
                confidence=computed_confidence,
                importance=computed_importance,
                lifecycle_state=LifecycleState.ACTIVE
            )
            db.add(record)
            db.commit()
            db.refresh(record)

            # Knowledge Graph Entity Resolution & Auto-Linking
            graph_links = []
            try:
                from core.memory.graph_service import GraphService
                linked_edges = GraphService.auto_link_entity_memories(db, record, extracted_meta)
                graph_links = [
                    {"target_id": edge.target_memory_id, "type": edge.relationship_type, "weight": edge.weight}
                    for edge in linked_edges if edge
                ]
            except Exception as e:
                logger.debug(f"Graph auto-linking skipped: {e}")

            # Graceful Vector Upsert
            vector_indexed = False
            try:
                dense_embedding = EmbeddingGenerator.generate_embedding(normalized_content)
                vector_indexed = vector_adapter.upsert_embedding(
                    memory_id=record.id,
                    vector=dense_embedding,
                    payload={"namespace_path": namespace.path, "memory_type": resolved_type.value, "owner": actor.name}
                )
            except Exception as e:
                vector_indexed = False

            step_trace["step_9_persistence"] = {
                "memory_id": record.id,
                "db_persisted": True,
                "vector_indexed": vector_indexed,
                "graph_links_created": len(graph_links),
                "graph_links": graph_links
            }

            # -------------------------------------------------------------
            # STEP 10: EMIT EVENT AND LOG AUDIT TRAIL
            # -------------------------------------------------------------
            event_payload = {
                "event": "memory.created",
                "memory_id": record.id,
                "owner": actor.name,
                "namespace": namespace.path,
                "type": record.memory_type.value,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            event_emitter.publish("memory.created", event_payload)

            audit_entry = PolicyEngine.log_audit_decision(
                db,
                PolicyDecision(
                    allowed=True,
                    reason="Memory written via 10-step pipeline.",
                    rule_matched="MEMORY_PIPELINE_WRITE",
                    dimensions={"event": event_payload}
                ),
                actor_id=actor.id,
                memory_id=record.id
            )

            step_trace["step_10_emit_event_and_audit"] = {
                "event_emitted": "memory.created",
                "audit_logged": True,
                "audit_id": audit_entry.id
            }
            step_trace["step_10_emit_and_audit"] = step_trace["step_10_emit_event_and_audit"]

            metrics_collector.record_write(success=True, is_contradiction=False, latency_ms=(time.time() - start_time) * 1000)

            return MemoryWriteResult(
                record=record,
                step_outputs=step_trace,
                is_duplicate=False
            )

        except Exception as e:
            metrics_collector.record_write(success=False, is_contradiction=False, latency_ms=(time.time() - start_time) * 1000)
            raise