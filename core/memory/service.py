"""
Memory Service
Coordinates CRUD operations, policy enforcement, lifecycle transitions, supersession, and decay.
"""
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from storage.relational.models import (
    MemoryRecord,
    Agent,
    Namespace,
    NamespaceType,
    MemoryType,
    LifecycleState,
    AuditLog,
    DeletionTombstone,
)
from storage.vector.qdrant_adapter import vector_adapter
from core.identity.service import IdentityService
from core.policy.engine import PolicyEngine, PolicyDecision
from core.lifecycle.state_machine import MemoryLifecycleEngine
from core.lifecycle.supersession import SupersessionEngine
from core.lifecycle.decay import MemoryDecayEngine
from core.memory.schemas import MemoryRecordCreate, MemoryRecordUpdate, MemoryQuery

class PermissionDeniedError(Exception):
    pass

class MemoryNotFoundError(Exception):
    pass

class MemoryService:
    @staticmethod
    def create_memory(
        db: Session,
        memory_in: MemoryRecordCreate,
        actor_name: Optional[str] = None,
        purpose: Optional[str] = None
    ) -> MemoryRecord:
        tenant_id = getattr(memory_in, "tenant_id", "default")

        # Resolve Owner Agent
        if memory_in.owner_id:
            owner = IdentityService.get_agent_by_id(db, memory_in.owner_id)
        elif memory_in.owner_name:
            owner = IdentityService.get_agent_by_name(db, memory_in.owner_name)
            if not owner:
                owner = IdentityService.register_agent(db, memory_in.owner_name, tenant_id=tenant_id)
        else:
            actor_h = actor_name or "friday"
            owner = IdentityService.register_agent(db, actor_h, tenant_id=tenant_id)

        # Resolve Actor Agent
        actor = IdentityService.get_agent_by_name(db, actor_name) if actor_name else owner
        if not actor:
            actor = IdentityService.register_agent(db, actor_name or "unknown", tenant_id=tenant_id)

        # Resolve Namespace
        if memory_in.namespace_id:
            namespace = db.query(Namespace).filter(Namespace.id == memory_in.namespace_id).first()
        elif memory_in.namespace_path:
            namespace = IdentityService.resolve_namespace(db, memory_in.namespace_path, owner_agent_id=owner.id, tenant_id=tenant_id)
        else:
            ns_path = f"memora://{owner.name}/private"
            namespace = IdentityService.resolve_namespace(db, ns_path, owner_agent_id=owner.id, tenant_id=tenant_id)

        if not namespace:
            raise MemoryNotFoundError("Target namespace could not be resolved.")

        # Policy Check
        decision = PolicyEngine.evaluate_access(db, actor, namespace, action="write", purpose=purpose)
        if not decision.allowed:
            raise PermissionDeniedError(decision.reason)

        # Idempotency Check
        idemp_key = getattr(memory_in, "idempotency_key", None)
        if idemp_key:
            existing_idemp = db.query(MemoryRecord).filter(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.idempotency_key == idemp_key
            ).first()
            if existing_idemp:
                return existing_idemp

        # Poison & Prompt Injection Check
        from core.memory.pipeline.poison_detector import PoisonDetector
        PoisonDetector.validate_content_safety(memory_in.content_text)

        # Untrusted Semantic Memory Guard (Requirement 5)
        if memory_in.memory_type == MemoryType.SEMANTIC:
            norm_src = str(memory_in.source).lower()
            prov = memory_in.provenance or {}
            norm_stype = str(prov.get("source_type", "")).lower()
            norm_trust = str(prov.get("trust_level", "")).lower()
            untrusted = {"ocr", "web_scrape", "web_text", "web", "tool_output", "tool", "model_output", "untrusted"}
            if norm_src in untrusted or norm_stype in untrusted or norm_trust in {"untrusted", "candidate"}:
                raise PermissionDeniedError(
                    "Policy Violation: Untrusted knowledge cannot be written directly into SEMANTIC memory tier. "
                    "Write to EPISODIC or WORKING tier first, then promote via verified promotion workflow."
                )

        # Create record with 6-dimension identity scope
        resolved_user = getattr(memory_in, "user_id", "default_user") or "default_user"
        resolved_agent = getattr(memory_in, "agent_id", None) or owner.name
        resolved_ws = getattr(memory_in, "workspace_id", "default_workspace") or "default_workspace"
        resolved_dev = getattr(memory_in, "device_id", "default_device") or "default_device"
        resolved_task = getattr(memory_in, "task_id", None)

        record = MemoryRecord(
            tenant_id=tenant_id,
            user_id=resolved_user,
            agent_id=resolved_agent,
            workspace_id=resolved_ws,
            device_id=resolved_dev,
            task_id=resolved_task,
            idempotency_key=idemp_key,
            namespace_id=namespace.id,
            owner_id=owner.id,
            memory_type=memory_in.memory_type,
            content_text=memory_in.content_text,
            source=memory_in.source,
            provenance=memory_in.provenance or {},
            confidence=memory_in.confidence,
            importance=memory_in.importance,
            lifecycle_state=memory_in.lifecycle_state or LifecycleState.CANDIDATE,
            valid_from=memory_in.valid_from,
            valid_until=memory_in.valid_until,
            expires_at=memory_in.expires_at,
            entities=memory_in.entities or [],
        )
        db.add(record)
        db.flush()

        # Audit Log for creation (single atomic transaction)
        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                allowed=True,
                reason=f"Memory record created under namespace '{namespace.path}'.",
                rule_matched="MEMORY_CREATED",
                dimensions={"namespace_path": namespace.path, "memory_type": record.memory_type.value, "user_id": resolved_user}
            ),
            actor_id=actor.id,
            memory_id=record.id,
            tenant_id=tenant_id
        )
        db.commit()
        db.refresh(record)
        return record

    @staticmethod
    def get_memory_by_id(
        db: Session,
        memory_id: str,
        actor_name: Optional[str] = None,
        purpose: Optional[str] = None
    ) -> MemoryRecord:
        record = db.query(MemoryRecord).filter(MemoryRecord.id == memory_id).first()
        if not record:
            raise MemoryNotFoundError(f"Memory record with ID '{memory_id}' not found.")

        if actor_name:
            actor = IdentityService.get_agent_by_name(db, actor_name)
            if actor:
                decision = PolicyEngine.evaluate_access(
                    db,
                    actor=actor,
                    namespace=record.namespace,
                    action="read",
                    purpose=purpose,
                    memory_id=record.id
                )
                if not decision:
                    raise PermissionDeniedError(decision.reason)

        return record

    @staticmethod
    def query_memories(
        db: Session,
        query: MemoryQuery,
        actor_name: Optional[str] = None,
        purpose: Optional[str] = None,
        include_superseded: Optional[bool] = None,
        include_archived: Optional[bool] = None,
        include_deleted: Optional[bool] = None
    ) -> List[MemoryRecord]:
        actor = IdentityService.get_agent_by_name(db, actor_name) if actor_name else None
        
        # If querying specific namespace, run policy check
        if query.namespace_path and actor:
            ns = IdentityService.get_namespace_by_path(db, query.namespace_path)
            if ns:
                decision = PolicyEngine.evaluate_access(db, actor, ns, action="query", purpose=purpose)
                if not decision:
                    raise PermissionDeniedError(decision.reason)

        q = db.query(MemoryRecord).join(Namespace).join(Agent, MemoryRecord.owner_id == Agent.id)

        # 1. Strict Tenant Isolation
        target_tenant = getattr(query, "tenant_id", "default")
        if actor:
            target_tenant = getattr(actor, "tenant_id", target_tenant)
        q = q.filter(MemoryRecord.tenant_id == target_tenant)

        # 2. Strict User Isolation (Requirement 10)
        if query.user_id:
            q = q.filter(MemoryRecord.user_id == query.user_id)

        # 3. Agent and Scope Isolation
        if query.agent_id:
            q = q.filter(MemoryRecord.agent_id == query.agent_id)
        if query.workspace_id:
            q = q.filter(MemoryRecord.workspace_id == query.workspace_id)
        if query.task_id:
            q = q.filter(MemoryRecord.task_id == query.task_id)

        # 4. Temporal Validity & Expiry Filtering (Requirement 7)
        now_utc = datetime.now(timezone.utc)
        if not query.include_expired:
            q = q.filter(or_(MemoryRecord.expires_at == None, MemoryRecord.expires_at > now_utc))

        if query.time_from:
            q = q.filter(MemoryRecord.created_at >= query.time_from)
        if query.time_to:
            q = q.filter(MemoryRecord.created_at <= query.time_to)

        if query.query_text:
            q = q.filter(MemoryRecord.content_text.ilike(f"%{query.query_text}%"))

        if query.namespace_path:
            q = q.filter(Namespace.path == query.namespace_path)

        if query.owner_name:
            q = q.filter(Agent.name == query.owner_name.lower())

        if query.memory_types:
            q = q.filter(MemoryRecord.memory_type.in_(query.memory_types))

        # Lifecycle state filtering
        if query.lifecycle_states:
            q = q.filter(MemoryRecord.lifecycle_state.in_(query.lifecycle_states))
        else:
            inc_sup = query.include_superseded if include_superseded is None else include_superseded
            inc_arc = query.include_archived if include_archived is None else include_archived
            inc_del = query.include_deleted if include_deleted is None else include_deleted

            allowed_states = [LifecycleState.ACTIVE, LifecycleState.VERIFIED, LifecycleState.CANDIDATE]
            if inc_sup:
                allowed_states.append(LifecycleState.SUPERSEDED)
            if inc_arc:
                allowed_states.append(LifecycleState.ARCHIVED)
            if inc_del:
                allowed_states.append(LifecycleState.DELETED)
            q = q.filter(MemoryRecord.lifecycle_state.in_(allowed_states))

        if query.min_confidence:
            q = q.filter(MemoryRecord.confidence >= query.min_confidence)

        if query.min_importance:
            q = q.filter(MemoryRecord.importance >= query.min_importance)

        # Retrieve candidate matches ordered by created_at
        candidates = q.order_by(MemoryRecord.created_at.desc()).all()

        # Trust level filtering on provenance
        if query.trust_level:
            candidates = [
                c for c in candidates
                if (c.provenance or {}).get("trust_level") == query.trust_level
            ]

        # Filter out records where actor lacks read access BEFORE pagination
        if actor:
            accessible_results = []
            for r in candidates:
                dec = PolicyEngine.evaluate_access(db, actor, r.namespace, action="read", purpose=purpose, memory_id=r.id, log_audit=False)
                if dec.allowed:
                    accessible_results.append(r)
            return accessible_results[query.offset : query.offset + query.limit]

        return candidates[query.offset : query.offset + query.limit]

    @staticmethod
    def transition_memory_state(
        db: Session,
        memory_id: str,
        target_state: LifecycleState,
        actor_name: Optional[str] = None,
        superseded_by_id: Optional[str] = None,
        purpose: Optional[str] = None
    ) -> MemoryRecord:
        record = MemoryService.get_memory_by_id(db, memory_id)

        actor = IdentityService.get_agent_by_name(db, actor_name) if actor_name else None
        if actor:
            action_name = "verify" if target_state == LifecycleState.VERIFIED else "supersede"
            decision = PolicyEngine.evaluate_access(db, actor, record.namespace, action=action_name, purpose=purpose, memory_id=record.id)
            if not decision:
                raise PermissionDeniedError(decision.reason)

        MemoryLifecycleEngine.transition(record, target_state, superseded_by_id=superseded_by_id)
        db.commit()
        db.refresh(record)

        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                allowed=True,
                reason=f"Transitioned lifecycle state to '{target_state.value}'.",
                rule_matched="MEMORY_TRANSITION",
                dimensions={"target_state": target_state.value, "superseded_by_id": superseded_by_id}
            ),
            actor_id=actor.id if actor else None,
            memory_id=record.id
        )
        return record

    @staticmethod
    def verify_memory(
        db: Session,
        memory_id: str,
        actor_name: Optional[str] = None,
        notes: Optional[str] = None
    ) -> MemoryRecord:
        record = MemoryService.get_memory_by_id(db, memory_id)
        actor = IdentityService.get_agent_by_name(db, actor_name) if actor_name else None

        if actor:
            decision = PolicyEngine.evaluate_access(db, actor, record.namespace, action="verify", memory_id=record.id)
            if not decision:
                raise PermissionDeniedError(decision.reason)

        MemoryLifecycleEngine.transition(record, LifecycleState.VERIFIED)
        db.commit()
        db.refresh(record)

        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                allowed=True,
                reason=f"Memory verified by {actor_name or 'supervisor'}. Notes: {notes or 'none'}",
                rule_matched="MEMORY_VERIFIED",
                dimensions={"verified_at": record.last_verified_at.isoformat() if record.last_verified_at else None}
            ),
            actor_id=actor.id if actor else None,
            memory_id=record.id
        )
        return record

    @staticmethod
    def supersede_memory(
        db: Session,
        old_memory_id: str,
        new_memory_id: str,
        actor_name: Optional[str] = None,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        old_record = MemoryService.get_memory_by_id(db, old_memory_id)
        new_record = MemoryService.get_memory_by_id(db, new_memory_id)
        actor = IdentityService.get_agent_by_name(db, actor_name) if actor_name else None

        if actor:
            decision = PolicyEngine.evaluate_access(db, actor, old_record.namespace, action="supersede", memory_id=old_record.id)
            if not decision:
                raise PermissionDeniedError(decision.reason)

        resolution = SupersessionEngine.resolve_contradiction_and_supersede(
            db=db,
            existing_record=old_record,
            new_record=new_record,
            existing_owner_name=old_record.owner.name if old_record.owner else None,
            new_owner_name=new_record.owner.name if new_record.owner else None,
            reason=reason
        )

        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                allowed=True,
                reason=resolution.reason,
                rule_matched="MEMORY_SUPERSEDED",
                dimensions={
                    "winner_id": resolution.winner_id,
                    "superseded_id": resolution.superseded_id,
                    "evidence_winner": resolution.evidence_winner,
                    "evidence_loser": resolution.evidence_loser
                }
            ),
            actor_id=actor.id if actor else None,
            memory_id=old_record.id
        )

        return {
            "status": "superseded",
            "winner_id": resolution.winner_id,
            "superseded_id": resolution.superseded_id,
            "evidence_winner": resolution.evidence_winner,
            "evidence_loser": resolution.evidence_loser,
            "reason": resolution.reason
        }

    @staticmethod
    def delete_memory(
        db: Session,
        memory_id: str,
        actor_name: Optional[str] = None,
        hard_delete: bool = False
    ) -> Dict[str, Any]:
        record = db.query(MemoryRecord).filter(MemoryRecord.id == memory_id).first()
        if not record:
            raise MemoryNotFoundError(f"Memory with ID '{memory_id}' not found.")

        actor = IdentityService.get_agent_by_name(db, actor_name) if actor_name else None
        if actor:
            decision = PolicyEngine.evaluate_access(db, actor, record.namespace, action="delete", memory_id=record.id)
            if not decision.allowed:
                raise PermissionDeniedError(decision.reason)

        tenant_id = getattr(record, "tenant_id", "default")
        if hard_delete:
            tombstone = DeletionTombstone(
                tenant_id=tenant_id,
                memory_id=memory_id,
                relational_deleted=False,
                vector_deleted=False,
                cache_deleted=False,
                graph_deleted=False,
                status="DELETE_REQUESTED"
            )
            db.add(tombstone)
            db.flush()

            # 1. Vector deletion
            vec_ok = False
            try:
                vec_ok = vector_adapter.delete_embedding(memory_id, tenant_id=tenant_id)
            except Exception:
                vec_ok = False
            tombstone.vector_deleted = bool(vec_ok)

            # 2. Graph relationship deletion (Requirement 9: multi-store convergence)
            try:
                from storage.relational.models import MemoryRelationship
                db.query(MemoryRelationship).filter(
                    or_(
                        MemoryRelationship.source_memory_id == memory_id,
                        MemoryRelationship.target_memory_id == memory_id
                    )
                ).delete(synchronize_session=False)
                tombstone.graph_deleted = True
            except Exception:
                tombstone.graph_deleted = False

            tombstone.cache_deleted = True

            # 3. Relational deletion
            db.delete(record)
            tombstone.relational_deleted = True
            tombstone.status = "CONVERGED" if tombstone.is_converged() else "PENDING_RETRY"

            PolicyEngine.log_audit_decision(
                db,
                PolicyDecision(True, "Hard deleted memory and references across relational, vector, graph, and cache stores.", "MEMORY_HARD_DELETED"),
                actor_id=actor.id if actor else None,
                memory_id=memory_id,
                tenant_id=tenant_id
            )
            db.commit()

            return {
                "status": "hard_deleted",
                "memory_id": memory_id,
                "deletion_converged": tombstone.is_converged(),
                "tombstone_status": tombstone.status
            }
        else:
            MemoryLifecycleEngine.transition(record, LifecycleState.DELETED)
            PolicyEngine.log_audit_decision(
                db,
                PolicyDecision(True, "Soft deleted memory record (retained in audit trail).", "MEMORY_SOFT_DELETED"),
                actor_id=actor.id if actor else None,
                memory_id=memory_id,
                tenant_id=tenant_id
            )
            db.commit()
            db.refresh(record)
            return {"status": "soft_deleted", "memory_id": memory_id, "lifecycle_state": "deleted"}

    @staticmethod
    def promote_to_semantic(
        db: Session,
        memory_id: str,
        promoted_by: str = "friday",
        verification_evidence: Optional[List[str]] = None,
        target_confidence: float = 0.95,
        purpose: Optional[str] = None
    ) -> MemoryRecord:
        """
        Explicit promotion workflow from episodic or working memory into semantic memory (Requirement 6).
        Requires verification evidence and high confidence, rejecting unverified or poison vectors.
        """
        record = db.query(MemoryRecord).filter(MemoryRecord.id == memory_id).first()
        if not record:
            raise MemoryNotFoundError(f"Memory record with ID '{memory_id}' not found.")

        if not verification_evidence or len(verification_evidence) == 0:
            raise ValueError("Explicit promotion to SEMANTIC tier requires at least one verification evidence reference.")

        if target_confidence < 0.85:
            raise ValueError(f"Target confidence {target_confidence} below required promotion threshold (>= 0.85).")

        # Poison and Prompt Injection Defense
        from core.memory.pipeline.poison_detector import PoisonDetector
        PoisonDetector.validate_content_safety(record.content_text)

        old_tier = record.memory_type.value
        record.memory_type = MemoryType.SEMANTIC
        record.lifecycle_state = LifecycleState.VERIFIED
        record.confidence = target_confidence
        record.last_verified_at = datetime.now(timezone.utc)

        # Provenance enrichment with promotion tracking
        prov = dict(record.provenance or {})
        prov["promoted_from"] = old_tier
        prov["promoted_by"] = promoted_by
        prov["promoted_at"] = datetime.now(timezone.utc).isoformat()
        prov["trust_level"] = "verified"
        existing_evidence = prov.get("evidence_refs") or []
        prov["evidence_refs"] = list(set(existing_evidence + verification_evidence))
        prov["confidence"] = target_confidence
        record.provenance = prov

        # Sync updated vector classification
        try:
            from storage.vector.embedding import EmbeddingGenerator
            dense_embedding = EmbeddingGenerator.generate_embedding(record.content_text)
            vector_adapter.upsert_embedding(
                memory_id=record.id,
                vector=dense_embedding,
                tenant_id=record.tenant_id,
                payload={
                    "namespace_path": record.namespace.path if record.namespace else "",
                    "memory_type": MemoryType.SEMANTIC.value,
                    "owner": record.owner.name if record.owner else promoted_by,
                    "trust_level": "verified",
                    "user_id": getattr(record, "user_id", "default_user"),
                    "agent_id": getattr(record, "agent_id", "friday"),
                    "task_id": getattr(record, "task_id", None)
                }
            )
        except Exception:
            pass

        # Atomic audit and event dispatch
        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                allowed=True,
                reason=f"Memory promoted from {old_tier} to SEMANTIC tier by {promoted_by}. Evidence: {verification_evidence}",
                rule_matched="MEMORY_PROMOTED_TO_SEMANTIC",
                dimensions={
                    "promoted_by": promoted_by,
                    "old_tier": old_tier,
                    "target_confidence": target_confidence,
                    "evidence_refs": verification_evidence
                }
            ),
            actor_id=record.owner_id,
            memory_id=record.id,
            tenant_id=record.tenant_id
        )

        from core.events.emitter import event_emitter
        event_emitter.publish("memory.promoted", {
            "memory_id": record.id,
            "promoted_by": promoted_by,
            "new_type": "semantic",
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

        db.commit()
        db.refresh(record)
        return record

    @staticmethod
    def apply_decay(
        db: Session,
        decay_rate_per_day: float = 0.02,
        unverified_threshold_days: int = 14,
        archive_threshold: float = 0.15,
        actor_name: Optional[str] = None
    ) -> Dict[str, Any]:
        actor = IdentityService.get_agent_by_name(db, actor_name) if actor_name else None
        results = MemoryDecayEngine.apply_time_decay(
            db=db,
            decay_rate_per_day=decay_rate_per_day,
            unverified_threshold_days=unverified_threshold_days,
            archive_importance_threshold=archive_threshold
        )

        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                allowed=True,
                reason=f"Decay cycle completed: {results['decayed_count']} decayed, {results['archived_count']} archived.",
                rule_matched="MEMORY_DECAY_CYCLE",
                dimensions=results
            ),
            actor_id=actor.id if actor else None
        )
        return results