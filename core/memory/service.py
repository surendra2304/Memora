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
    AuditLog
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
        # Resolve Owner Agent
        if memory_in.owner_id:
            owner = IdentityService.get_agent_by_id(db, memory_in.owner_id)
        elif memory_in.owner_name:
            owner = IdentityService.get_agent_by_name(db, memory_in.owner_name)
            if not owner:
                owner = IdentityService.register_agent(db, memory_in.owner_name)
        else:
            actor = actor_name or "friday"
            owner = IdentityService.register_agent(db, actor)

        # Resolve Actor Agent
        actor = IdentityService.get_agent_by_name(db, actor_name) if actor_name else owner
        if not actor:
            actor = IdentityService.register_agent(db, actor_name or "unknown")

        # Resolve Namespace
        if memory_in.namespace_id:
            namespace = db.query(Namespace).filter(Namespace.id == memory_in.namespace_id).first()
        elif memory_in.namespace_path:
            namespace = IdentityService.resolve_namespace(db, memory_in.namespace_path, owner_agent_id=owner.id)
        else:
            ns_path = f"memora://{owner.name}/private"
            namespace = IdentityService.resolve_namespace(db, ns_path, owner_agent_id=owner.id)

        # Policy Check
        decision = PolicyEngine.evaluate_access(db, actor, namespace, action="write", purpose=purpose)
        if not decision:
            raise PermissionDeniedError(decision.reason)

        # Create record
        record = MemoryRecord(
            namespace_id=namespace.id,
            owner_id=owner.id,
            memory_type=memory_in.memory_type,
            content_text=memory_in.content_text,
            source=memory_in.source,
            provenance=memory_in.provenance or {},
            confidence=memory_in.confidence,
            importance=memory_in.importance,
            lifecycle_state=memory_in.lifecycle_state or LifecycleState.CANDIDATE
        )
        db.add(record)
        db.commit()
        db.refresh(record)

        # Audit Log for creation
        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                allowed=True,
                reason=f"Memory record created under namespace '{namespace.path}'.",
                rule_matched="MEMORY_CREATED",
                dimensions={"namespace_path": namespace.path, "memory_type": record.memory_type.value}
            ),
            actor_id=actor.id,
            memory_id=record.id
        )
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
        include_superseded: bool = False,
        include_archived: bool = False,
        include_deleted: bool = False
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
            allowed_states = [LifecycleState.ACTIVE, LifecycleState.VERIFIED, LifecycleState.CANDIDATE]
            if include_superseded:
                allowed_states.append(LifecycleState.SUPERSEDED)
            if include_archived:
                allowed_states.append(LifecycleState.ARCHIVED)
            if include_deleted:
                allowed_states.append(LifecycleState.DELETED)
            q = q.filter(MemoryRecord.lifecycle_state.in_(allowed_states))

        if query.min_confidence:
            q = q.filter(MemoryRecord.confidence >= query.min_confidence)

        if query.min_importance:
            q = q.filter(MemoryRecord.importance >= query.min_importance)

        results = q.order_by(MemoryRecord.created_at.desc()).offset(query.offset).limit(query.limit).all()

        # Filter out records where actor lacks read access (for cross-namespace queries)
        if actor:
            accessible_results = []
            for r in results:
                dec = PolicyEngine.evaluate_access(db, actor, r.namespace, action="read", purpose=purpose, memory_id=r.id, log_audit=False)
                if dec.allowed:
                    accessible_results.append(r)
            return accessible_results

        return results

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
        """Transitions a memory to VERIFIED, bumps confidence, and sets last_verified_at."""
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
        """Explicitly supersedes old memory with new canonical memory."""
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
        """Performs soft deletion (mark as deleted) or hard deletion (purge DB and vector index)."""
        record = db.query(MemoryRecord).filter(MemoryRecord.id == memory_id).first()
        if not record:
            raise MemoryNotFoundError(f"Memory with ID '{memory_id}' not found.")

        actor = IdentityService.get_agent_by_name(db, actor_name) if actor_name else None
        if actor:
            decision = PolicyEngine.evaluate_access(db, actor, record.namespace, action="delete", memory_id=record.id)
            if not decision:
                raise PermissionDeniedError(decision.reason)

        if hard_delete:
            vector_adapter.delete_embedding(memory_id)
            db.delete(record)
            db.commit()

            PolicyEngine.log_audit_decision(
                db,
                PolicyDecision(True, "Hard deleted memory and vector references.", "MEMORY_HARD_DELETED"),
                actor_id=actor.id if actor else None,
                memory_id=memory_id
            )
            return {"status": "hard_deleted", "memory_id": memory_id}
        else:
            MemoryLifecycleEngine.transition(record, LifecycleState.DELETED)
            db.commit()
            db.refresh(record)

            PolicyEngine.log_audit_decision(
                db,
                PolicyDecision(True, "Soft deleted memory record (retained in audit trail).", "MEMORY_SOFT_DELETED"),
                actor_id=actor.id if actor else None,
                memory_id=memory_id
            )
            return {"status": "soft_deleted", "memory_id": memory_id, "lifecycle_state": "deleted"}

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