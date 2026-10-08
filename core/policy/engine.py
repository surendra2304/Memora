"""
Policy Engine for Memora
Evaluates access control across 5 operational dimensions:
Who, What, Where, Why, How long.
"""
from typing import Optional, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from storage.relational.models import (
    Agent,
    Namespace,
    NamespaceType,
    AccessGrant,
    AuditLog,
    LifecycleState,
    MemoryRecord,
)
from core.metrics.collector import metrics_collector
from core.events.emitter import event_emitter

class PolicyDecision:
    def __init__(
        self,
        allowed: bool,
        reason: str,
        rule_matched: str,
        dimensions: Optional[Dict[str, Any]] = None
    ):
        self.allowed = allowed
        self.reason = reason
        self.rule_matched = rule_matched
        self.dimensions = dimensions or {}

    def __bool__(self) -> bool:
        return self.allowed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "rule_matched": self.rule_matched,
            "dimensions": self.dimensions
        }

class PolicyEngine:
    @classmethod
    def evaluate_access(
        cls,
        db: Session,
        actor: Agent,
        namespace: Namespace,
        action: str = "read",
        purpose: Optional[str] = None,
        memory_id: Optional[str] = None,
        log_audit: bool = True,
        allow_expired: bool = False,
    ) -> PolicyDecision:
        dims = {
            "who": {"id": actor.id, "name": actor.name, "role": actor.role, "bounded_scope": actor.bounded_scope},
            "what": {"action": action, "memory_id": memory_id},
            "where": {"namespace_id": namespace.id, "path": namespace.path, "type": namespace.type.value},
            "why": {"purpose": purpose or "unspecified"},
            "how_long": {"timestamp": datetime.now(timezone.utc).isoformat()}
        }

        # -------------------------------------------------------------
        # DIMENSION 0: STRICT TENANT ISOLATION
        # -------------------------------------------------------------
        actor_tenant = getattr(actor, "tenant_id", "default")
        namespace_tenant = getattr(namespace, "tenant_id", "default")
        if actor_tenant != namespace_tenant:
            decision = PolicyDecision(
                allowed=False,
                reason=f"Tenant mismatch: Actor tenant '{actor_tenant}' cannot access namespace tenant '{namespace_tenant}'.",
                rule_matched="RULE_TENANT_MISMATCH",
                dimensions=dims
            )
            cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
            return decision

        # The service administrator may administer every namespace inside its
        # own tenant. This check deliberately follows strict tenant isolation so
        # the memora credential never becomes a cross-tenant superuser.
        if actor.name == "memora":
            decision = PolicyDecision(
                allowed=True,
                reason=f"The memora service identity may administer namespace '{namespace.path}' within tenant '{actor_tenant}'.",
                rule_matched="MEMORA_TENANT_ADMIN",
                dimensions=dims,
            )
            cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
            return decision

        # A soft-deleted or out-of-window record is not readable merely because
        # its containing namespace is readable. Search already excludes these
        # states; enforce the same rule on direct GET and graph traversal paths.
        if action.lower() in {"read", "query"} and memory_id:
            record = db.query(MemoryRecord).filter(
                MemoryRecord.id == memory_id,
                MemoryRecord.tenant_id == actor_tenant,
                MemoryRecord.namespace_id == namespace.id,
            ).first()
            if record is not None:
                now = datetime.now(timezone.utc)

                def _as_utc(value):
                    if value is None:
                        return None
                    if value.tzinfo is None:
                        return value.replace(tzinfo=timezone.utc)
                    return value.astimezone(timezone.utc)

                expires_at = _as_utc(record.expires_at)
                valid_from = _as_utc(record.valid_from)
                valid_until = _as_utc(record.valid_until)
                unreadable = (
                    record.lifecycle_state == LifecycleState.DELETED
                    or (
                        expires_at is not None
                        and now > expires_at
                        and not allow_expired
                    )
                    or (valid_from is not None and now < valid_from)
                    or (valid_until is not None and now > valid_until)
                )
                if unreadable:
                    decision = PolicyDecision(
                        allowed=False,
                        reason="The memory is deleted or outside its active temporal validity window.",
                        rule_matched="MEMORY_NOT_CURRENTLY_READABLE",
                        dimensions=dims,
                    )
                    cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
                    return decision

        # -------------------------------------------------------------
        # DIMENSION 1: SUB-AGENT BOUNDED CONTEXT ISOLATION
        # -------------------------------------------------------------
        if actor.bounded_scope:
            if namespace.type == NamespaceType.AGENT_PRIVATE:
                decision = PolicyDecision(
                    allowed=False,
                    reason=f"Sub-agent '{actor.name}' is bounded to '{actor.bounded_scope}' and is strictly forbidden from accessing private namespace '{namespace.path}'.",
                    rule_matched="RULE_3_SUBAGENT_BOUNDED_CONTEXT_ISOLATION",
                    dimensions=dims
                )
                cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
                return decision

            bounded_scope = actor.bounded_scope.rstrip("/")
            in_bounded_scope = (
                namespace.path == bounded_scope
                or namespace.path.startswith(f"{bounded_scope}/")
            )
            if not in_bounded_scope:
                decision = PolicyDecision(
                    allowed=False,
                    reason=f"Sub-agent '{actor.name}' is restricted to scope '{actor.bounded_scope}'. Target '{namespace.path}' is outside boundary.",
                    rule_matched="RULE_3_SUBAGENT_SCOPE_EXCEEDED",
                    dimensions=dims
                )
                cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
                return decision

        # -------------------------------------------------------------
        # DIMENSION 2: RULE 1 - "PRIVATE BY DEFAULT"
        # -------------------------------------------------------------
        if namespace.type == NamespaceType.AGENT_PRIVATE:
            if namespace.agent_id == actor.id:
                decision = PolicyDecision(
                    allowed=True,
                    reason=f"Agent '{actor.name}' owns private namespace '{namespace.path}'.",
                    rule_matched="RULE_1_OWNER_PRIVATE_ACCESS",
                    dimensions=dims
                )
                cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
                return decision

            # Check for explicit access grant before denying
            grant = db.query(AccessGrant).filter(
                AccessGrant.tenant_id == actor_tenant,
                AccessGrant.agent_id == actor.id,
                AccessGrant.namespace_id == namespace.id
            ).first()

            if grant:
                if grant.is_expired():
                    decision = PolicyDecision(
                        allowed=False,
                        reason=f"Access grant for agent '{actor.name}' on namespace '{namespace.path}' expired at {grant.expires_at}.",
                        rule_matched="RULE_2_ACCESS_GRANT_EXPIRED",
                        dimensions=dims
                    )
                    cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
                    return decision


                if action not in grant.actions:
                    decision = PolicyDecision(
                        allowed=False,
                        reason=f"Access grant does not permit action '{action}'. Permitted: {grant.actions}",
                        rule_matched="RULE_2_ACTION_UNAUTHORIZED",
                        dimensions=dims
                    )
                    cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
                    return decision

                purpose_str = f" for purpose '{purpose}'" if purpose else ""
                decision = PolicyDecision(
                    allowed=True,
                    reason=f"Agent '{actor.name}' has explicit grant for private namespace '{namespace.path}'{purpose_str}.",
                    rule_matched="RULE_1_EXPLICIT_GRANT_ACCESS",
                    dimensions=dims
                )
                cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
                return decision

            decision = PolicyDecision(
                allowed=False,
                reason=f"Private namespace '{namespace.path}' is private to another agent and isolated. Agent '{actor.name}' cannot access it without promotion.",
                rule_matched="RULE_1_PRIVATE_BY_DEFAULT_PROMOTION_REQUIRED",
                dimensions=dims
            )
            cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
            return decision

        # -------------------------------------------------------------
        # DIMENSION 3: UNIVERSE GLOBAL & PUBLIC NAMESPACES
        # -------------------------------------------------------------
        if namespace.type in [NamespaceType.UNIVERSE_GLOBAL, NamespaceType.PUBLIC]:
            normalized_action = action.lower()
            record = None
            if memory_id:
                record = db.query(MemoryRecord).filter(
                    MemoryRecord.id == memory_id,
                    MemoryRecord.tenant_id == actor_tenant,
                    MemoryRecord.namespace_id == namespace.id,
                ).first()

            if normalized_action in {"read", "query"}:
                decision = PolicyDecision(
                    allowed=True,
                    reason=f"Namespace '{namespace.path}' is {namespace.type.value} and openly readable within tenant '{actor_tenant}'.",
                    rule_matched="PUBLIC_GLOBAL_READ",
                    dimensions=dims,
                )
            elif normalized_action == "write" and memory_id is None:
                # Global namespaces are append-only collaboration surfaces. Keep
                # the documented Universe adapter publish flow, but do not let a
                # write permission silently imply authority over existing rows.
                decision = PolicyDecision(
                    allowed=True,
                    reason=f"Agent '{actor.name}' may append to '{namespace.path}'.",
                    rule_matched="PUBLIC_GLOBAL_APPEND",
                    dimensions=dims,
                )
            elif normalized_action in {"write", "delete", "verify", "supersede", "share", "update", "promote", "transition"}:
                owns_record = bool(record and record.owner_id == actor.id)
                is_service_admin = actor.name == "memora"
                allowed = owns_record or is_service_admin
                decision = PolicyDecision(
                    allowed=allowed,
                    reason=(
                        f"Agent '{actor.name}' may mutate its own record in '{namespace.path}'."
                        if owns_record
                        else (
                            "The memora service identity may administer public/global records."
                            if is_service_admin
                            else f"Mutating an existing {namespace.type.value} record requires its owner or the memora service identity."
                        )
                    ),
                    rule_matched=(
                        "PUBLIC_GLOBAL_OWNER_MUTATION"
                        if owns_record
                        else ("PUBLIC_GLOBAL_ADMIN_MUTATION" if is_service_admin else "PUBLIC_GLOBAL_MUTATION_REQUIRES_OWNER")
                    ),
                    dimensions=dims,
                )
            else:
                decision = PolicyDecision(
                    allowed=False,
                    reason=f"Action '{action}' is not permitted in public/global namespace '{namespace.path}'.",
                    rule_matched="PUBLIC_GLOBAL_ACTION_DENIED",
                    dimensions=dims,
                )

            cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
            return decision

        # -------------------------------------------------------------
        # DIMENSION 4: RULE 2 - PROJECT / TEAM SHARED MEMBERSHIP & GRANTS
        # -------------------------------------------------------------
        if namespace.agent_id == actor.id:
            decision = PolicyDecision(
                allowed=True,
                reason=f"Agent '{actor.name}' is owner of namespace '{namespace.path}'.",
                rule_matched="RULE_2_OWNER_SHARED_ACCESS",
                dimensions=dims
            )
            cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
            return decision

        grant = db.query(AccessGrant).filter(
            AccessGrant.tenant_id == actor_tenant,
            AccessGrant.agent_id == actor.id,
            AccessGrant.namespace_id == namespace.id
        ).first()

        if grant:
            if grant.is_expired():
                decision = PolicyDecision(
                    allowed=False,
                    reason=f"Access grant for agent '{actor.name}' on namespace '{namespace.path}' expired at {grant.expires_at}.",
                    rule_matched="RULE_2_ACCESS_GRANT_EXPIRED",
                    dimensions=dims
                )
                cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
                return decision


            if action not in grant.actions:
                decision = PolicyDecision(
                    allowed=False,
                    reason=f"Access grant does not permit action '{action}'. Permitted: {grant.actions}",
                    rule_matched="RULE_2_ACTION_UNAUTHORIZED",
                    dimensions=dims
                )
                cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
                return decision

            purpose_str = f" for purpose '{purpose}'" if purpose else ""
            decision = PolicyDecision(
                allowed=True,
                reason=f"Agent '{actor.name}' has active membership grant for namespace '{namespace.path}'{purpose_str}.",
                rule_matched="RULE_2_PROJECT_MEMBERSHIP_GRANT_ACTIVE",
                dimensions=dims
            )
            cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
            return decision

        decision = PolicyDecision(
            allowed=False,
            reason=f"Shared namespace '{namespace.path}' requires explicit membership or access grant. None found for '{actor.name}'.",
            rule_matched="RULE_2_PROJECT_MEMBERSHIP_REQUIRED",
            dimensions=dims
        )
        cls._handle_decision(db, decision, actor.id, memory_id, log_audit)
        return decision

    @classmethod
    def _handle_decision(cls, db: Session, decision: PolicyDecision, actor_id: Optional[str], memory_id: Optional[str], log_audit: bool):
        metrics_collector.record_policy_check(decision.allowed)
        if not decision.allowed:
            event_emitter.publish("access.denied", {
                "actor_id": actor_id,
                "memory_id": memory_id,
                "reason": decision.reason,
                "rule": decision.rule_matched
            }, db=db)
        if log_audit:
            cls.log_audit_decision(db, decision, actor_id=actor_id, memory_id=memory_id)

    @classmethod
    def log_audit_decision(
        cls,
        db: Session,
        decision: PolicyDecision,
        actor_id: Optional[str] = None,
        memory_id: Optional[str] = None,
        tenant_id: Optional[str] = None
    ) -> AuditLog:
        action_name = "policy_approved" if decision.allowed else "policy_denied"
        resolved_tenant = tenant_id or "default"
        if actor_id and not tenant_id:
            try:
                actor_row = db.query(Agent.tenant_id).filter(Agent.id == actor_id).first()
                if actor_row and actor_row[0]:
                    resolved_tenant = actor_row[0]
            except Exception:
                pass

        audit_entry = AuditLog(
            tenant_id=resolved_tenant,
            actor_id=actor_id,
            memory_id=memory_id,
            action=action_name,
            details={
                "allowed": decision.allowed,
                "reason": decision.reason,
                "rule_matched": decision.rule_matched,
                "dimensions": decision.dimensions
            }
        )
        db.add(audit_entry)
        try:
            db.flush()
        except Exception:
            pass
        return audit_entry
