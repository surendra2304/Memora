"""
Memory Write Service for Memora
Executes the deterministic 10-Step Memory Write Pipeline before persisting memory records.
"""
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
import hashlib
import time
import logging

from storage.relational.models import MemoryRecord, MemoryType, LifecycleState, NamespaceType
from storage.relational.session import storage_receipt
from storage.vector.qdrant_adapter import vector_adapter
from storage.vector.embedding import EmbeddingGenerator
from core.identity.service import IdentityService
from core.policy.engine import PolicyEngine, PolicyDecision
from core.metrics.collector import metrics_collector
from core.events.emitter import event_emitter
from core.memory.pipeline.secret_scanner import SecretScanner
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
        storage = storage_receipt()
        return {
            "id": self.record.id,
            "tenant_id": getattr(self.record, "tenant_id", "default"),
            "user_id": getattr(self.record, "user_id", "default_user"),
            "agent_id": getattr(self.record, "agent_id", "friday"),
            "workspace_id": getattr(self.record, "workspace_id", "default_workspace"),
            "device_id": getattr(self.record, "device_id", "default_device"),
            "task_id": getattr(self.record, "task_id", None),
            "idempotency_key": getattr(self.record, "idempotency_key", None),
            "namespace_id": self.record.namespace_id,
            "owner_id": self.record.owner_id,
            "memory_type": self.record.memory_type.value,
            "content_text": self.record.content_text,
            "source": self.record.source,
            "provenance": self.record.provenance or {},
            "confidence": self.record.confidence,
            "importance": self.record.importance,
            "lifecycle_state": self.record.lifecycle_state.value,
            "created_at": self.record.created_at.isoformat() if self.record.created_at else None,
            "is_duplicate": self.is_duplicate,
            "duplicate_of_id": self.duplicate_of_id,
            "storage_backend": storage["backend"],
            "storage_durable": storage["durable"],
            "storage_durability": storage["durability"],
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
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        device_id: Optional[str] = None,
        task_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        target_namespace_path: Optional[str] = None,
        memory_type: Optional[MemoryType] = None,
        source: str = "api",
        source_type: Optional[str] = None,
        trust_level: Optional[str] = None,
        evidence_refs: Optional[List[str]] = None,
        provenance: Optional[Dict[str, Any]] = None,
        confidence: Optional[float] = None,
        importance: Optional[float] = None,
        purpose: Optional[str] = None,
        allow_duplicates: bool = False,
        valid_from: Optional[datetime] = None,
        valid_until: Optional[datetime] = None,
        expires_at: Optional[datetime] = None
    ) -> MemoryWriteResult:
        start_time = time.time()
        step_trace: Dict[str, Any] = {}
        vector_write_memory_id: Optional[str] = None
        vector_write_attempted = False
        write_transaction_committed = False
        resolved_actor_name = caller_name or actor_name or agent_id or "system"
        resolved_user_id = user_id or "default_user"
        resolved_workspace_id = workspace_id or "default_workspace"
        resolved_device_id = device_id or "default_device"

        try:
            # -------------------------------------------------------------
            # STEP 1: RECEIVE MEMORY EVENT
            # -------------------------------------------------------------
            if not isinstance(content_text, str) or not content_text.strip():
                raise MemoryPipelineError("Content text must be a non-empty string.")
            if len(content_text) > 100_000:
                raise MemoryPipelineError("Content text exceeds the 100000-character limit.")
            if idempotency_key is not None and (
                not isinstance(idempotency_key, str) or len(idempotency_key) > 128
            ):
                raise MemoryPipelineError("Idempotency key must be a string of at most 128 characters.")
            
            step_trace["step_1_receive_event"] = {
                "raw_length": len(content_text),
                "source": source,
                "target_namespace_path": target_namespace_path,
                "requested_type": memory_type.value if memory_type else None,
                "user_id": resolved_user_id,
                "agent_id": resolved_actor_name,
                "workspace_id": resolved_workspace_id,
                "task_id": task_id,
                "idempotency_key": idempotency_key
            }

            # -------------------------------------------------------------
            # STEP 2: AUTHENTICATE CALLER AND RESOLVE NAMESPACE (TENANT ISOLATED)
            # -------------------------------------------------------------
            actor = (
                IdentityService.get_agent_by_name(
                    db, resolved_actor_name, tenant_id=tenant_id
                )
                if tenant_id is not None
                else IdentityService.get_agent_by_name(db, resolved_actor_name)
            )
            resolved_tenant: str = str(tenant_id or (getattr(actor, "tenant_id", "default") if actor else "default"))
            if not actor:
                actor = IdentityService.register_agent(db, name=resolved_actor_name, role="worker", tenant_id=resolved_tenant)

            target_path = IdentityService.validate_namespace_path(
                target_namespace_path or f"memora://{actor.name}/private"
            )

            # ---------------------------------------------------------
            # CLAIM CHECK: never let a caller create a namespace that
            # belongs to somebody else.
            #
            # resolve_namespace creates a missing namespace owned by the
            # CALLER. Naming a foreign path therefore minted the row with the
            # attacker as owner, and the RULE_1 owner check at step 8 then
            # approved the attacker's own write. Verified live: forge stored
            # into memora://friday/private (201) and took ownership of it,
            # after which friday itself got 403 writing to its own private
            # space. Whoever wrote first won, and the real owner lost.
            #
            # The rule must not depend on the victim having registered yet.
            # The live capture shows why: forge wrote memora://friday/private
            # before friday had ever written anything, so the row was minted
            # owned by forge; when friday's agent row was created afterwards,
            # create_namespace adopted the row that already existed and friday
            # inherited a 403 on its own private space. A check based on
            # "is this root a registered agent" would have allowed it.
            #
            # memora://shared/projects/<id> is the documented shared-space idiom
            # (see SentinelAdapter.publish_approved_remediation) and is covered
            # by OPEN_NAMESPACE_ROOTS, so a publisher may still create it and
            # grant peers access.
            #
            # An existing namespace is left alone — step 8 evaluates the caller
            # against it properly, so a granted or shared space still works.
            # Only *creation* is gated here.
            # ---------------------------------------------------------
            if IdentityService.get_namespace_by_path(db, target_path, tenant_id=resolved_tenant) is None:
                owner_name = IdentityService.namespace_root(target_path)
                root_segment = target_path[len("memora://"):].split("/", 1)[0]
                in_own_scope = owner_name == actor.name
                bounded_scope = (actor.bounded_scope or "").rstrip("/")
                in_bounded_scope = bool(
                    bounded_scope
                    and (target_path == bounded_scope or target_path.startswith(f"{bounded_scope}/"))
                )
                # Universe and public namespaces are deployment-owned open
                # roots and may never be claimed by an ordinary writer. Preserve
                # the established first-write flow for the canonical
                # memora://team/shared collaboration namespace, while blocking
                # agents from staking arbitrary paths beneath the open team root.
                protected_open_root = (
                    root_segment in {"universe", "public"}
                    or (
                        root_segment == "team"
                        and target_path != "memora://team/shared"
                    )
                )
                if protected_open_root and actor.name != "memora":
                    raise PermissionDeniedError(
                        f"Only the Memora service identity may create a namespace under the protected open root '{root_segment}'."
                    )
                if owner_name is not None and not (in_own_scope or in_bounded_scope):
                    raise PermissionDeniedError(
                        f"Cannot create namespace '{target_path}': it belongs to "
                        f"agent '{owner_name}', and '{actor.name}' may only create "
                        f"namespaces under its own name. An existing namespace can "
                        f"still be written to if '{owner_name}' grants access."
                    )

            namespace = IdentityService.resolve_namespace(db, target_path, owner_agent_id=actor.id, tenant_id=resolved_tenant)
            step_trace["step_2_authenticate_and_resolve"] = {
                "tenant_id": resolved_tenant,
                "actor_id": actor.id,
                "actor_name": actor.name,
                "namespace_id": namespace.id,
                "namespace_path": namespace.path,
                "namespace_type": namespace.type.value
            }

            # -------------------------------------------------------------
            # STEP 3: CLASSIFY MEMORY TYPE AND SENSITIVITY (SECRET & POISON SCANNING)
            # -------------------------------------------------------------
            # 1. Secret Scanning
            SecretScanner.validate_content_safety(content_text)
            
            # 2. Poison / Prompt Injection Defenses
            from core.memory.pipeline.poison_detector import PoisonDetector
            PoisonDetector.validate_content_safety(content_text)

            resolved_type = memory_type or MemoryType.EPISODIC
            trusted_tier_types = {
                MemoryType.SEMANTIC,
                MemoryType.SYSTEM,
            }
            evidence_required_types = trusted_tier_types | {MemoryType.PROCEDURAL}
            trusted_writer = actor.name == "memora"
            canonical_source_type = source_type or (provenance or {}).get("source_type") or (
                "verified_fact" if trusted_writer and resolved_type in trusted_tier_types else "agent_generated"
            )
            requested_trust_level = trust_level or (provenance or {}).get("trust_level") or "candidate"
            # Trust labels are assertions, not proof. Only the separately keyed
            # service principal can attach a trusted label through this write path.
            canonical_trust_level = (
                str(requested_trust_level).lower()
                if trusted_writer else "candidate"
            )
            canonical_evidence_refs = evidence_refs or (provenance or {}).get("evidence_refs") or []

            # Trusted tiers have a separate authorization boundary. Agents must
            # first store observations as episodic/working/experience memories,
            # then an authorized actor promotes them with evidence. Merely sending
            # trust_level="verified" or a made-up evidence reference is not proof.
            norm_source = str(source).lower()
            norm_source_type = str(canonical_source_type).lower()
            norm_trust = str(canonical_trust_level).lower()

            untrusted_sources = {"ocr", "web_scrape", "web_text", "web", "tool_output", "tool", "model_output", "untrusted"}
            if resolved_type in evidence_required_types:
                if resolved_type in trusted_tier_types and not trusted_writer:
                    raise PermissionDeniedError(
                        f"Policy Violation: Direct {resolved_type.value.upper()} writes are restricted to the "
                        "authenticated memora service identity. Store observations as EPISODIC, WORKING, or "
                        "EXPERIENCE and use the evidence-checked promotion workflow."
                    )
                # Ordinary agents may submit PROCEDURAL candidates, but may not
                # elevate them to trusted status. The authenticated Memora service
                # may write a verified procedure only with explicit evidence.
                if resolved_type in trusted_tier_types or trusted_writer:
                    is_untrusted = (
                        norm_source in untrusted_sources
                        or norm_source_type in untrusted_sources
                        or norm_trust not in {"verified", "operator_confirmed"}
                        or not isinstance(canonical_evidence_refs, (list, tuple))
                        or not any(str(ref).strip() for ref in canonical_evidence_refs)
                    )
                    if is_untrusted:
                        raise PermissionDeniedError(
                            f"Policy Violation: Direct {resolved_type.value.upper()} writes require verified or "
                            "operator-confirmed trust and at least one evidence reference. Store unverified knowledge "
                            "as EPISODIC, WORKING, or EXPERIENCE, then use the evidence-checked promotion workflow."
                        )

            step_trace["step_3_classify_and_scan"] = {
                "memory_type": resolved_type.value,
                "sensitivity": "CONFIDENTIAL" if namespace.type == NamespaceType.AGENT_PRIVATE else "INTERNAL",
                "secrets_detected": False,
                "poison_detected": False
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
            extracted_entities = extracted_meta.get("entities", []) if isinstance(extracted_meta, dict) else []
            step_trace["step_5_extract_entities"] = extracted_meta

            # Authorize before either deduplication branch can return an existing
            # row. The old ordering let a caller with write-only access (or an
            # unauthorized target namespace) learn the contents and ID of a
            # record through an early idempotency/duplicate return.
            policy_decision = PolicyEngine.evaluate_access(
                db,
                actor=actor,
                namespace=namespace,
                action="write",
                purpose=purpose,
                log_audit=False,
            )
            if not policy_decision.allowed:
                PolicyEngine.log_audit_decision(
                    db,
                    policy_decision,
                    actor_id=actor.id,
                    tenant_id=resolved_tenant,
                )
                db.commit()
                raise PermissionDeniedError(policy_decision.reason)

            def _require_duplicate_read_access(existing_record: MemoryRecord) -> PolicyDecision:
                read_decision = PolicyEngine.evaluate_access(
                    db,
                    actor=actor,
                    namespace=existing_record.namespace,
                    action="read",
                    purpose=purpose,
                    memory_id=existing_record.id,
                    log_audit=False,
                )
                PolicyEngine.log_audit_decision(
                    db,
                    read_decision,
                    actor_id=actor.id,
                    memory_id=existing_record.id,
                    tenant_id=resolved_tenant,
                )
                if not read_decision.allowed:
                    db.commit()
                    raise PermissionDeniedError(read_decision.reason)
                return read_decision

            # -------------------------------------------------------------
            # STEP 6: DETECT DUPLICATES OR CONTRADICTIONS (WITH IDEMPOTENCY)
            # -------------------------------------------------------------
            # Check Idempotency Key first. Scoped to the writing agent: a key is
            # the caller's own retry token, not a fabric-wide identifier. Keying
            # only on tenant let one agent's key swallow another agent's write and
            # return the first agent's record, content included.
            if idempotency_key:
                existing_idemp = db.query(MemoryRecord).filter(
                    MemoryRecord.tenant_id == resolved_tenant,
                    MemoryRecord.agent_id == actor.name,
                    MemoryRecord.idempotency_key == idempotency_key
                ).first()
                if existing_idemp:
                    read_decision = _require_duplicate_read_access(existing_idemp)
                    PolicyEngine.log_audit_decision(
                        db,
                        policy_decision,
                        actor_id=actor.id,
                        tenant_id=resolved_tenant,
                    )
                    step_trace["step_6_deduplication"] = {
                        "is_duplicate": True,
                        "duplicate_of_id": existing_idemp.id,
                        "idempotent_hit": True
                    }
                    step_trace["step_8_apply_policy"] = policy_decision.to_dict()
                    step_trace["duplicate_read_policy"] = read_decision.to_dict()
                    db.commit()
                    db.refresh(existing_idemp)
                    metrics_collector.record_write(success=True, is_contradiction=False, latency_ms=(time.time() - start_time) * 1000)
                    return MemoryWriteResult(
                        record=existing_idemp,
                        step_outputs=step_trace,
                        is_duplicate=True,
                        duplicate_of_id=existing_idemp.id
                    )

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
                existing = db.query(MemoryRecord).filter(
                    MemoryRecord.id == dedup_result.duplicate_of_id,
                    MemoryRecord.tenant_id == resolved_tenant,
                ).first()
                if existing:
                    read_decision = _require_duplicate_read_access(existing)
                    PolicyEngine.log_audit_decision(
                        db,
                        policy_decision,
                        actor_id=actor.id,
                        tenant_id=resolved_tenant,
                    )
                    step_trace["step_8_apply_policy"] = policy_decision.to_dict()
                    step_trace["duplicate_read_policy"] = read_decision.to_dict()
                    db.commit()
                    db.refresh(existing)
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
            computed_importance = importance if importance is not None else (0.8 if extracted_meta.get("triples") else 0.5)
            retention_tier = "HOT" if computed_importance >= 0.75 else "STANDARD"

            step_trace["step_7_assign_metadata"] = {
                "confidence": computed_confidence,
                "importance": computed_importance,
                "retention_tier": retention_tier
            }

            # -------------------------------------------------------------
            # STEP 8: APPLY ACCESS AND SHARING POLICY
            # -------------------------------------------------------------
            # The decision was preflighted before deduplication to prevent early
            # returns from bypassing authorization. Persist the same policy
            # approval here, once all validation and deduplication have passed.
            PolicyEngine.log_audit_decision(
                db,
                policy_decision,
                actor_id=actor.id,
                tenant_id=resolved_tenant,
            )
            step_trace["step_8_apply_policy"] = policy_decision.to_dict()

            # -------------------------------------------------------------
            # STEP 9: PERSIST TO DATABASE, VECTOR INDEX & KNOWLEDGE GRAPH
            # -------------------------------------------------------------
            # Structured 8-field provenance (Requirement 4). Values are
            # canonicalized before deduplication so policy rejects unverified
            # semantic content before any early-return path.

            # Caller-supplied provenance goes in FIRST so the canonical fields
            # below always win. Spreading it last let any caller overwrite
            # trust_level, created_by, source and confidence, so a worker agent
            # could store a memory asserting trust_level="verified" and
            # created_by="<some other agent>" — verified with forge writing
            # trust_level=verified / created_by=friday / source=human_executive.
            # Extra caller keys that do not collide are still preserved.
            combined_provenance = {
                **(provenance or {}),
                "source": source,
                "source_type": canonical_source_type,
                "trust_level": canonical_trust_level,
                "evidence_refs": canonical_evidence_refs,
                "created_by": resolved_actor_name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": expires_at.isoformat() if expires_at else None,
                "confidence": computed_confidence,
                "content_sha256": content_hash,
                "extracted_entities": extracted_meta,
                "retention_tier": retention_tier,
                "pipeline_version": "2.0.0"
            }

            record = MemoryRecord(
                tenant_id=resolved_tenant,
                user_id=resolved_user_id,
                agent_id=actor.name if actor else resolved_actor_name,
                workspace_id=resolved_workspace_id,
                device_id=resolved_device_id,
                task_id=task_id,
                idempotency_key=idempotency_key,
                namespace_id=namespace.id,
                owner_id=actor.id,
                memory_type=resolved_type,
                content_text=normalized_content,
                content_hash=content_hash,
                entities=extracted_entities,
                source=source,
                provenance=combined_provenance,
                confidence=computed_confidence,
                importance=computed_importance,
                lifecycle_state=LifecycleState.ACTIVE,
                valid_from=valid_from,
                valid_until=valid_until,
                expires_at=expires_at
            )
            db.add(record)
            try:
                db.flush()
            except IntegrityError:
                # The unique idempotency index is the final arbiter when two
                # identical requests race past the pre-insert lookup. Roll back
                # the failed transaction, then adopt the winner if it is now
                # visible; non-idempotency integrity errors still propagate.
                if not idempotency_key:
                    raise
                db.rollback()
                winner = db.query(MemoryRecord).filter(
                    MemoryRecord.tenant_id == resolved_tenant,
                    MemoryRecord.agent_id == actor.name,
                    MemoryRecord.idempotency_key == idempotency_key,
                ).first()
                if winner is None:
                    raise
                read_decision = _require_duplicate_read_access(winner)
                PolicyEngine.log_audit_decision(
                    db,
                    policy_decision,
                    actor_id=actor.id,
                    tenant_id=resolved_tenant,
                )
                step_trace["step_6_deduplication"] = {
                    "is_duplicate": True,
                    "duplicate_of_id": winner.id,
                    "idempotent_hit": True,
                    "concurrent_conflict_recovered": True,
                }
                step_trace["step_8_apply_policy"] = policy_decision.to_dict()
                step_trace["duplicate_read_policy"] = read_decision.to_dict()
                db.commit()
                db.refresh(winner)
                metrics_collector.record_write(
                    success=True,
                    is_contradiction=False,
                    latency_ms=(time.time() - start_time) * 1000,
                )
                return MemoryWriteResult(
                    record=winner,
                    step_outputs=step_trace,
                    is_duplicate=True,
                    duplicate_of_id=winner.id,
                )

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

            # Graceful Vector Upsert with tenant isolation
            vector_indexed = False
            try:
                dense_embedding = EmbeddingGenerator.generate_embedding(normalized_content)
                vector_write_memory_id = record.id
                vector_write_attempted = True
                vector_indexed = vector_adapter.upsert_embedding(
                    memory_id=record.id,
                    vector=dense_embedding,
                    tenant_id=resolved_tenant,
                    payload={
                        "namespace_path": namespace.path,
                        "memory_type": resolved_type.value,
                        "owner": actor.name,
                        "user_id": resolved_user_id,
                        "agent_id": actor.name if actor else resolved_actor_name,
                        "workspace_id": resolved_workspace_id,
                        "task_id": task_id,
                        "trust_level": canonical_trust_level
                    }
                )
            except Exception:
                vector_indexed = False

            storage = storage_receipt()
            step_trace["step_9_persistence"] = {
                "memory_id": record.id,
                "tenant_id": resolved_tenant,
                "db_persisted": True,
                "storage_backend": storage["backend"],
                "storage_durable": storage["durable"],
                "storage_durability": storage["durability"],
                "vector_indexed": vector_indexed,
                "graph_links_created": len(graph_links),
                "graph_links": graph_links
            }

            # -------------------------------------------------------------
            # STEP 10: EMIT EVENT AND LOG AUDIT TRAIL (ONE ATOMIC COMMIT)
            # -------------------------------------------------------------
            event_payload = {
                "event": "memory.created",
                "memory_id": record.id,
                "tenant_id": resolved_tenant,
                "owner": actor.name,
                "namespace": namespace.path,
                "type": record.memory_type.value,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            event_emitter.publish("memory.created", event_payload, db=db)

            audit_entry = PolicyEngine.log_audit_decision(
                db,
                PolicyDecision(
                    allowed=True,
                    reason="Memory written via 10-step pipeline.",
                    rule_matched="MEMORY_PIPELINE_WRITE",
                    dimensions={"event": event_payload}
                ),
                actor_id=actor.id,
                memory_id=record.id,
                tenant_id=resolved_tenant
            )

            # Atomic commit of record, relationships, and audit trail
            db.commit()
            write_transaction_committed = True
            db.refresh(record)

            # Non-blocking Turso Cloud DB write-through sync
            try:
                from storage.relational.turso_sync import push_memory_to_turso_async
                push_memory_to_turso_async(record, agent=actor, namespace=namespace)
            except Exception as e:
                logger.debug(f"Turso cloud sync skipped: {e}")

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

        except Exception:
            # Do not leave partially flushed records or graph edges in a reused
            # caller session. Deliberately committed policy denials and identity
            # bootstrap work remain durable; this rolls back only the current open
            # transaction after any unexpected pipeline failure.
            try:
                db.rollback()
            except Exception:
                logger.exception("Failed to roll back memory write transaction")
            if (
                vector_write_attempted
                and vector_write_memory_id
                and not write_transaction_committed
            ):
                try:
                    deleted = vector_adapter.delete_embedding(
                        vector_write_memory_id,
                        tenant_id=resolved_tenant,
                    )
                    if not deleted:
                        logger.error(
                            "Could not compensate vector upsert for rolled-back memory '%s' (tenant '%s').",
                            vector_write_memory_id,
                            resolved_tenant,
                        )
                except Exception:
                    logger.exception(
                        "Failed to compensate vector upsert for rolled-back memory '%s'.",
                        vector_write_memory_id,
                    )
            metrics_collector.record_write(success=False, is_contradiction=False, latency_ms=(time.time() - start_time) * 1000)
            raise
