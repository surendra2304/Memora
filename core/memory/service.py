"""
Memory Service
Coordinates CRUD operations, policy enforcement, lifecycle transitions, and audit logging.
"""
from typing import List, Optional, Tuple
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
from core.identity.service import IdentityService
from core.policy.engine import PolicyEngine, PolicyDecision
from core.lifecycle.state_machine import MemoryLifecycleEngine
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
        purpose: Optional[str] = None
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

        if query.lifecycle_states:
            q = q.filter(MemoryRecord.lifecycle_state.in_(query.lifecycle_states))
        else:
            q = q.filter(MemoryRecord.lifecycle_state != LifecycleState.DELETED)

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

        # Audit Log for state transition
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