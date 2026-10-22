"""
Memory Service
Coordinates CRUD operations, policy enforcement, lifecycle transitions, supersession, and decay.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import logging
from sqlalchemy.orm import Session
from sqlalchemy import or_

from storage.relational.models import (
    MemoryRecord,
    Agent,
    Namespace,
    MemoryType,
    LifecycleState,
    DeletionTombstone,
)
from storage.vector.qdrant_adapter import vector_adapter
from core.identity.service import IdentityService
from core.policy.engine import PolicyEngine, PolicyDecision
from core.lifecycle.state_machine import MemoryLifecycleEngine
from core.lifecycle.supersession import SupersessionEngine
from core.lifecycle.decay import MemoryDecayEngine
from core.memory.schemas import MemoryRecordCreate, MemoryQuery

logger = logging.getLogger(__name__)


class PermissionDeniedError(Exception):
    pass

class MemoryNotFoundError(Exception):
    pass


#: Cap on how many terms one query may expand into, so a pathological query
#: cannot build an unbounded OR expression.
_MAX_QUERY_TERMS = 24


def _content_matches_any_term(query_text: str):
    """Match records containing ANY term of the query, not the whole phrase.

    This used to be ``content_text.ilike(f"%{query_text}%")``, which requires the
    entire query to appear as one contiguous run of characters. A natural question
    like "xenon compressor seal torque specification" therefore matched nothing at
    all, while "xenon" alone matched fine — the primary query endpoint silently
    returned zero rows for exactly the queries people actually ask. Verified
    against a live corpus: 0 rows for the full question, 4 for a single word.

    Terms are OR'd together, matching the semantics hybrid_search already uses in
    core/memory/search_service.py, so the two retrieval paths now agree.

    User input is also escaped here: ``%`` and ``_`` are LIKE wildcards, and they
    were previously interpolated straight into the pattern, so a query of "%"
    matched the entire corpus and "_" matched anything with one character.
    """
    terms = query_text.lower().split()[:_MAX_QUERY_TERMS]
    terms = [t for t in terms if t]
    if not terms:
        # Nothing to match on; the caller's other filters still apply.
        return or_(True)

    conditions = []
    for term in terms:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append(MemoryRecord.content_text.ilike(f"%{escaped}%", escape="\\"))
    return or_(*conditions)


class MemoryService:
    @staticmethod
    def _get_actor_scoped_memory(
        db: Session, memory_id: str, actor_name: Optional[str]
    ) -> tuple[Agent, MemoryRecord]:
        """Resolve an authenticated default-tenant actor and a memory it may mutate.

        API credentials have no tenant claim today, so every service mutation must
        resolve the principal only in the default tenant rather than falling back
        to an arbitrary same-named agent in another tenant.
        """
        if not actor_name:
            raise PermissionDeniedError("An authenticated actor is required to mutate a memory.")
        actor = IdentityService.get_agent_by_name(db, actor_name, tenant_id="default")
        if actor is None:
            raise PermissionDeniedError("The authenticated agent is not registered in Memora.")
        record = db.query(MemoryRecord).filter(
            MemoryRecord.id == memory_id,
            MemoryRecord.tenant_id == actor.tenant_id,
        ).first()
        if record is None:
            raise MemoryNotFoundError(f"Memory record with ID '{memory_id}' not found.")
        return actor, record

    @staticmethod
    def create_memory(
        db: Session,
        memory_in: MemoryRecordCreate,
        actor_name: Optional[str] = None,
        purpose: Optional[str] = None
    ) -> MemoryRecord:
        from core.memory.pipeline.secret_scanner import SecretScanner

        SecretScanner.validate_content_safety(memory_in.content_text)
        tenant_id = str(getattr(memory_in, "tenant_id", "default") or "default")

        # Resolve the caller and owner inside the requested tenant. A global
        # same-name lookup can bind a default-tenant operation to an unrelated
        # tenant's identity, while an unscoped owner_id can attach records across
        # tenants even if the namespace policy check later succeeds.
        actor = (
            IdentityService.get_agent_by_name(db, actor_name, tenant_id=tenant_id)
            if actor_name
            else None
        )
        if actor_name and actor is None:
            actor = IdentityService.register_agent(db, actor_name, tenant_id=tenant_id)

        if memory_in.owner_id:
            owner = IdentityService.get_agent_by_id(
                db, memory_in.owner_id, tenant_id=tenant_id
            )
            if owner is None:
                raise MemoryNotFoundError("Memory owner could not be resolved in the requested tenant.")
        elif memory_in.owner_name:
            requested_owner = memory_in.owner_name.strip().lower()
            if actor is not None and requested_owner != actor.name:
                raise PermissionDeniedError(
                    "The authenticated agent may not assign memory ownership to another agent."
                )
            owner = IdentityService.get_agent_by_name(
                db, requested_owner, tenant_id=tenant_id
            )
            if owner is None:
                owner = IdentityService.register_agent(
                    db, requested_owner, tenant_id=tenant_id
                )
        elif actor is not None:
            owner = actor
        else:
            owner = IdentityService.register_agent(db, "friday", tenant_id=tenant_id)

        if actor is None:
            actor = owner
        if actor.tenant_id != tenant_id or owner.tenant_id != tenant_id:
            raise PermissionDeniedError("Actor and owner must belong to the requested tenant.")

        # Resolve Namespace in the same tenant as actor, owner, and memory.
        if memory_in.namespace_id:
            namespace = db.query(Namespace).filter(
                Namespace.id == memory_in.namespace_id,
                Namespace.tenant_id == tenant_id,
            ).first()
        elif memory_in.namespace_path:
            namespace = IdentityService.resolve_namespace(
                db,
                memory_in.namespace_path,
                owner_agent_id=owner.id,
                tenant_id=tenant_id,
            )
        else:
            ns_path = f"memora://{owner.name}/private"
            namespace = IdentityService.resolve_namespace(
                db, ns_path, owner_agent_id=owner.id, tenant_id=tenant_id
            )

        if not namespace:
            raise MemoryNotFoundError("Target namespace could not be resolved.")

        # Policy Check
        decision = PolicyEngine.evaluate_access(db, actor, namespace, action="write", purpose=purpose)
        if not decision.allowed:
            raise PermissionDeniedError(decision.reason)

        # Resolve the 6-dimension identity scope up front. Idempotency is scoped
        # to the writing agent, so agent_id must be known before the check.
        resolved_user = getattr(memory_in, "user_id", "default_user") or "default_user"
        resolved_agent = getattr(memory_in, "agent_id", None) or owner.name
        resolved_ws = getattr(memory_in, "workspace_id", "default_workspace") or "default_workspace"
        resolved_dev = getattr(memory_in, "device_id", "default_device") or "default_device"
        resolved_task = getattr(memory_in, "task_id", None)

        # Idempotency Check. Scoped to the owner agent, matching the composite
        # unique index on (tenant_id, agent_id, idempotency_key).
        idemp_key = getattr(memory_in, "idempotency_key", None)
        if idemp_key:
            existing_idemp = db.query(MemoryRecord).filter(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.agent_id == resolved_agent,
                MemoryRecord.idempotency_key == idemp_key
            ).first()
            if existing_idemp:
                return existing_idemp

        # Poison & Prompt Injection Check
        from core.memory.pipeline.poison_detector import PoisonDetector
        PoisonDetector.validate_content_safety(memory_in.content_text)

        # Direct writes cannot use caller-supplied metadata to self-verify a
        # semantic assertion or install a system rule. Semantic records must use
        # the evidence-checked promotion path; SYSTEM records belong to Memora.
        supplied_provenance = memory_in.provenance if isinstance(memory_in.provenance, dict) else {}
        canonical_provenance = dict(supplied_provenance)
        trusted_writer = actor.name == "memora"
        if memory_in.memory_type in {MemoryType.SEMANTIC, MemoryType.SYSTEM} and not trusted_writer:
            raise PermissionDeniedError(
                f"Direct {memory_in.memory_type.value.upper()} writes are restricted to the authenticated "
                "memora service identity; use the appropriate review workflow."
            )
        if memory_in.memory_type == MemoryType.SEMANTIC:
            norm_src = str(memory_in.source).lower()
            norm_stype = str(canonical_provenance.get("source_type", "")).lower()
            norm_trust = str(canonical_provenance.get("trust_level", "candidate")).lower()
            evidence_refs = canonical_provenance.get("evidence_refs") or []
            untrusted = {"ocr", "web_scrape", "web_text", "web", "tool_output", "tool", "model_output", "untrusted"}
            if (
                norm_src in untrusted
                or norm_stype in untrusted
                or norm_trust not in {"verified", "operator_confirmed"}
                or not isinstance(evidence_refs, (list, tuple))
                or not any(str(ref).strip() for ref in evidence_refs)
            ):
                raise PermissionDeniedError(
                    "Policy Violation: Direct SEMANTIC writes require verified or operator-confirmed trust "
                    "and at least one evidence reference. Store unverified knowledge as EPISODIC or WORKING, "
                    "then promote it through the evidence-checked workflow."
                )

        requested_lifecycle = memory_in.lifecycle_state or LifecycleState.CANDIDATE
        if not trusted_writer:
            canonical_provenance["created_by"] = actor.name
            canonical_provenance["trust_level"] = "candidate"
            if requested_lifecycle == LifecycleState.VERIFIED:
                raise PermissionDeniedError(
                    "Only the authenticated memora service identity may create a verified lifecycle record."
                )
            if memory_in.memory_type == MemoryType.PROCEDURAL:
                requested_lifecycle = LifecycleState.CANDIDATE

        # Create record with the 6-dimension identity scope resolved above.
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
            provenance=canonical_provenance,
            confidence=memory_in.confidence,
            importance=memory_in.importance,
            lifecycle_state=requested_lifecycle,
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
            actor = IdentityService.get_agent_by_name(db, actor_name, tenant_id="default")
            if not actor:
                raise PermissionDeniedError(
                    f"Authenticated agent '{actor_name}' is not registered in the default tenant."
                )
            if record.tenant_id != actor.tenant_id:
                raise MemoryNotFoundError(f"Memory record with ID '{memory_id}' not found.")

        if actor_name:
            decision = PolicyEngine.evaluate_access(
                db,
                actor=actor,
                namespace=record.namespace,
                action="read",
                purpose=purpose,
                memory_id=record.id
            )
            if not decision.allowed:
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
        requested_tenant = getattr(query, "tenant_id", "default") or "default"
        actor = (
            IdentityService.get_agent_by_name(db, actor_name, tenant_id=requested_tenant)
            if actor_name else None
        )
        if actor_name and not actor:
            raise PermissionDeniedError(
                f"Authenticated agent '{actor_name}' is not registered in Memora."
            )
        
        # If querying specific namespace, run policy check
        if query.namespace_path and actor:
            ns = IdentityService.get_namespace_by_path(
                db, query.namespace_path, tenant_id=actor.tenant_id
            )
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
            q = q.filter(_content_matches_any_term(query.query_text))

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
                dec = PolicyEngine.evaluate_access(
                    db,
                    actor,
                    r.namespace,
                    action="read",
                    purpose=purpose,
                    memory_id=r.id,
                    log_audit=False,
                    allow_expired=query.include_expired,
                )
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
        actor, record = MemoryService._get_actor_scoped_memory(db, memory_id, actor_name)
        action_name = {
            LifecycleState.VERIFIED: "verify",
            LifecycleState.SUPERSEDED: "supersede",
            LifecycleState.DELETED: "delete",
        }.get(target_state, "transition")
        decision = PolicyEngine.evaluate_access(
            db,
            actor,
            record.namespace,
            action=action_name,
            purpose=purpose,
            memory_id=record.id,
        )
        if not decision.allowed:
            raise PermissionDeniedError(decision.reason)

        if superseded_by_id:
            replacement = db.query(MemoryRecord).filter(
                MemoryRecord.id == superseded_by_id,
                MemoryRecord.tenant_id == actor.tenant_id,
            ).first()
            if replacement is None:
                raise MemoryNotFoundError(f"Memory record with ID '{superseded_by_id}' not found.")
            replacement_read = PolicyEngine.evaluate_access(
                db, actor, replacement.namespace, action="read", purpose=purpose,
                memory_id=replacement.id, log_audit=False,
            )
            if not replacement_read.allowed:
                raise PermissionDeniedError(replacement_read.reason)

        MemoryLifecycleEngine.transition(record, target_state, superseded_by_id=superseded_by_id)
        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                allowed=True,
                reason=f"Transitioned lifecycle state to '{target_state.value}'.",
                rule_matched="MEMORY_TRANSITION",
                dimensions={"target_state": target_state.value, "superseded_by_id": superseded_by_id}
            ),
            actor_id=actor.id,
            memory_id=record.id,
            tenant_id=actor.tenant_id,
        )
        db.commit()
        db.refresh(record)
        return record

    @staticmethod
    def verify_memory(
        db: Session,
        memory_id: str,
        actor_name: Optional[str] = None,
        notes: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> MemoryRecord:
        actor, record = MemoryService._get_actor_scoped_memory(db, memory_id, actor_name)
        decision = PolicyEngine.evaluate_access(
            db,
            actor,
            record.namespace,
            action="verify",
            purpose=purpose,
            memory_id=record.id,
        )
        if not decision.allowed:
            raise PermissionDeniedError(decision.reason)

        MemoryLifecycleEngine.transition(record, LifecycleState.VERIFIED)
        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                allowed=True,
                reason=f"Memory verified by {actor.name}. Notes: {notes or 'none'}",
                rule_matched="MEMORY_VERIFIED",
                dimensions={
                    "verified_at": record.last_verified_at.isoformat() if record.last_verified_at else None,
                    "notes": notes,
                },
            ),
            actor_id=actor.id,
            memory_id=record.id,
            tenant_id=actor.tenant_id,
        )
        db.commit()
        db.refresh(record)
        return record

    @staticmethod
    def supersede_memory(
        db: Session,
        old_memory_id: str,
        new_memory_id: str,
        actor_name: Optional[str] = None,
        reason: Optional[str] = None,
        purpose: Optional[str] = None,
    ) -> Dict[str, Any]:
        actor, old_record = MemoryService._get_actor_scoped_memory(
            db, old_memory_id, actor_name
        )
        new_record = db.query(MemoryRecord).filter(
            MemoryRecord.id == new_memory_id,
            MemoryRecord.tenant_id == actor.tenant_id,
        ).first()
        if new_record is None:
            raise MemoryNotFoundError(f"Memory record with ID '{new_memory_id}' not found.")
        if old_record.id == new_record.id:
            raise ValueError("A memory cannot supersede itself.")

        # The resolution algorithm can mutate either record depending on their
        # evidence scores. Authorize mutation of both records before invoking it.
        for candidate in (old_record, new_record):
            decision = PolicyEngine.evaluate_access(
                db,
                actor,
                candidate.namespace,
                action="supersede",
                purpose=purpose,
                memory_id=candidate.id,
                log_audit=False,
            )
            if not decision.allowed:
                PolicyEngine.log_audit_decision(
                    db,
                    decision,
                    actor_id=actor.id,
                    memory_id=candidate.id,
                    tenant_id=actor.tenant_id,
                )
                db.commit()
                raise PermissionDeniedError(decision.reason)

        resolution = SupersessionEngine.resolve_contradiction_and_supersede(
            db=db,
            existing_record=old_record,
            new_record=new_record,
            existing_owner_name=old_record.owner.name if old_record.owner else None,
            new_owner_name=new_record.owner.name if new_record.owner else None,
            reason=reason,
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
                    "evidence_loser": resolution.evidence_loser,
                },
            ),
            actor_id=actor.id,
            memory_id=old_record.id,
            tenant_id=actor.tenant_id,
        )
        db.commit()

        return {
            "status": "superseded",
            "winner_id": resolution.winner_id,
            "superseded_id": resolution.superseded_id,
            "evidence_winner": resolution.evidence_winner,
            "evidence_loser": resolution.evidence_loser,
            "reason": resolution.reason,
        }

    @staticmethod
    def delete_memory(
        db: Session,
        memory_id: str,
        actor_name: Optional[str] = None,
        hard_delete: bool = False,
        tenant_id: str = "default",
    ) -> Dict[str, Any]:
        if not actor_name:
            raise PermissionDeniedError("An authenticated actor is required to delete a memory.")
        actor = IdentityService.get_agent_by_name(db, actor_name, tenant_id=tenant_id)
        if not actor:
            raise PermissionDeniedError("The authenticated agent is not registered in the requested tenant.")

        # Scope the lookup before revealing whether a cross-tenant ID exists.
        tenant_id = getattr(actor, "tenant_id", "default")
        record = db.query(MemoryRecord).filter(
            MemoryRecord.id == memory_id,
            MemoryRecord.tenant_id == tenant_id,
        ).first()
        if not record:
            raise MemoryNotFoundError(f"Memory with ID '{memory_id}' not found.")

        decision = PolicyEngine.evaluate_access(
            db,
            actor,
            record.namespace,
            action="delete",
            memory_id=record.id,
        )
        if not decision.allowed:
            raise PermissionDeniedError(decision.reason)

        tenant_id = getattr(record, "tenant_id", "default")
        if hard_delete:
            from storage.relational.turso_sync import (
                delete_memory_from_turso,
                turso_replica_enabled,
            )

            # Persist the deletion outbox before calling the remote replica. If
            # the request fails or the process exits after the local commit, the
            # self-healing worker can safely retry the tenant-scoped operation.
            turso_replica_required = turso_replica_enabled()
            tombstone = DeletionTombstone(
                tenant_id=tenant_id,
                memory_id=memory_id,
                relational_deleted=False,
                vector_deleted=False,
                cache_deleted=False,
                graph_deleted=False,
                turso_deleted=not turso_replica_required,
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

            if turso_replica_required:
                try:
                    tombstone.turso_deleted = bool(
                        delete_memory_from_turso(memory_id, tenant_id=tenant_id)
                    )
                except Exception:
                    logger.exception("Turso replica deletion failed; tombstone will remain retryable")
                    tombstone.turso_deleted = False
                tombstone.status = "CONVERGED" if tombstone.is_converged() else "PENDING_RETRY"
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
        actor_name: Optional[str] = None,
        verification_evidence: Optional[List[str]] = None,
        target_confidence: float = 0.95,
        purpose: Optional[str] = None,
    ) -> MemoryRecord:
        """Promote an authorized episodic/working record with explicit evidence.

        The authenticated principal, not a request-body ``promoted_by`` claim,
        is both the policy subject and the audit actor. Lookups are tenant-bound
        because current API credentials do not contain a tenant claim.
        """
        actor, record = MemoryService._get_actor_scoped_memory(
            db, memory_id, actor_name
        )
        decision = PolicyEngine.evaluate_access(
            db,
            actor,
            record.namespace,
            action="promote",
            purpose=purpose,
            memory_id=record.id,
            log_audit=False,
        )
        if not decision.allowed:
            PolicyEngine.log_audit_decision(
                db,
                decision,
                actor_id=actor.id,
                memory_id=record.id,
                tenant_id=actor.tenant_id,
            )
            db.commit()
            raise PermissionDeniedError(decision.reason)

        if record.memory_type not in {MemoryType.EPISODIC, MemoryType.WORKING, MemoryType.EXPERIENCE}:
            raise ValueError(
                f"Only episodic, working, or experience memories can be promoted; "
                f"this record is '{record.memory_type.value}'."
            )
        if record.lifecycle_state not in {
            LifecycleState.CANDIDATE,
            LifecycleState.ACTIVE,
            LifecycleState.VERIFIED,
        }:
            raise ValueError(
                f"Only candidate or active memories can be promoted; "
                f"this record is '{record.lifecycle_state.value}'."
            )

        evidence = verification_evidence
        if not isinstance(evidence, (list, tuple)) or not evidence:
            raise ValueError(
                "Explicit promotion to SEMANTIC tier requires at least one verification evidence reference."
            )
        normalized_evidence = []
        for ref in evidence:
            if not isinstance(ref, str) or not ref.strip() or len(ref.strip()) > 512:
                raise ValueError("Each evidence reference must be 1 to 512 non-whitespace characters.")
            value = ref.strip()
            if value not in normalized_evidence:
                normalized_evidence.append(value)
        if len(normalized_evidence) > 32:
            raise ValueError("At most 32 verification evidence references may be attached.")

        import math
        if not math.isfinite(target_confidence) or not 0.85 <= target_confidence <= 1.0:
            raise ValueError("Target confidence must be finite and between 0.85 and 1.0.")

        # Scan again at the trust-boundary transition: legacy/imported rows may
        # have entered before the current write-path scanners were installed.
        from core.memory.pipeline.poison_detector import PoisonDetector
        from core.memory.pipeline.secret_scanner import SecretScanner
        PoisonDetector.validate_content_safety(record.content_text)
        SecretScanner.validate_content_safety(record.content_text)

        old_tier = record.memory_type.value
        MemoryLifecycleEngine.transition(record, LifecycleState.VERIFIED)
        record.memory_type = MemoryType.SEMANTIC
        record.confidence = target_confidence
        record.last_verified_at = datetime.now(timezone.utc)

        provenance = record.provenance if isinstance(record.provenance, dict) else {}
        provenance = dict(provenance)
        provenance["promoted_from"] = old_tier
        provenance["promoted_by"] = actor.name
        provenance["promoted_at"] = datetime.now(timezone.utc).isoformat()
        provenance["trust_level"] = "verified"
        existing_evidence = provenance.get("evidence_refs")
        if not isinstance(existing_evidence, (list, tuple)):
            existing_evidence = []
        merged_evidence = []
        for ref in [*existing_evidence, *normalized_evidence]:
            if isinstance(ref, str) and ref.strip() and ref.strip() not in merged_evidence:
                merged_evidence.append(ref.strip())
        provenance["evidence_refs"] = merged_evidence
        provenance["confidence"] = target_confidence
        record.provenance = provenance

        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                allowed=True,
                reason=f"Memory promoted from {old_tier} to SEMANTIC tier by authenticated agent {actor.name}.",
                rule_matched="MEMORY_PROMOTED_TO_SEMANTIC",
                dimensions={
                    "promoted_by": actor.name,
                    "old_tier": old_tier,
                    "target_confidence": target_confidence,
                    "evidence_refs": normalized_evidence,
                },
            ),
            actor_id=actor.id,
            memory_id=record.id,
            tenant_id=actor.tenant_id,
        )
        from core.events.emitter import event_emitter
        event_emitter.publish("memory.promoted", {
            "memory_id": record.id,
            "promoted_by": actor.name,
            "new_type": "semantic",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, db=db)
        db.commit()
        db.refresh(record)

        # Keep the existing best-effort vector synchronization contract. The
        # relational row and audit trail are authoritative if the optional vector
        # store is offline; storage health remains visible via its normal status.
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
                    "owner": record.owner.name if record.owner else actor.name,
                    "trust_level": "verified",
                    "user_id": getattr(record, "user_id", "default_user"),
                    "agent_id": getattr(record, "agent_id", actor.name),
                    "task_id": getattr(record, "task_id", None),
                },
            )
        except Exception:
            pass
        return record

    @staticmethod
    def apply_decay(
        db: Session,
        decay_rate_per_day: float = 0.02,
        unverified_threshold_days: int = 14,
        archive_threshold: float = 0.15,
        actor_name: Optional[str] = None
    ) -> Dict[str, Any]:
        if (actor_name or "").lower() != "memora":
            raise PermissionDeniedError("Only the memora service identity may run memory decay.")
        if not 0.0 <= decay_rate_per_day <= 1.0:
            raise ValueError("decay_rate_per_day must be between 0 and 1.")
        if not 0 <= unverified_threshold_days <= 36500:
            raise ValueError("unverified_threshold_days must be between 0 and 36500.")
        if not 0.0 <= archive_threshold <= 1.0:
            raise ValueError("archive_threshold must be between 0 and 1.")

        actor = IdentityService.get_agent_by_name(db, "memora", tenant_id="default")
        if not actor:
            actor = IdentityService.register_agent(
                db, "memora", role="supervisor", tenant_id="default"
            )
        tenant_id = actor.tenant_id
        results = MemoryDecayEngine.apply_time_decay(
            db=db,
            decay_rate_per_day=decay_rate_per_day,
            unverified_threshold_days=unverified_threshold_days,
            archive_importance_threshold=archive_threshold,
            tenant_id=tenant_id,
        )
        results["tenant_id"] = tenant_id

        PolicyEngine.log_audit_decision(
            db,
            PolicyDecision(
                allowed=True,
                reason=f"Decay cycle completed: {results['decayed_count']} decayed, {results['archived_count']} archived.",
                rule_matched="MEMORY_DECAY_CYCLE",
                dimensions=results,
            ),
            actor_id=actor.id,
            tenant_id=tenant_id,
        )
        db.commit()
        return results
