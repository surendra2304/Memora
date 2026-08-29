"""
Policy Engine for Memora
Evaluates access control across 5 operational dimensions:
Who, What, Where, Why, How long.
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session

from storage.relational.models import (
    Agent,
    Namespace,
    NamespaceType,
    AccessGrant,
    AuditLog
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
        log_audit: bool = True
    ) -> PolicyDecision:
        dims = {
            "who": {"id": actor.id, "name": actor.name, "role": actor.role, "bounded_scope": actor.bounded_scope},
            "what": {"action": action, "memory_id": memory_id},
            "where": {"namespace_id": namespace.id, "path": namespace.path, "type": namespace.type.value},
            "why": {"purpose": purpose or "unspecified"},
            "how_long": {"timestamp": datetime.now(timezone.utc).isoformat()}
        }

        # -------------------------------------------------------------
        # SUPERVISOR GLOBAL BYPASS (FRIDAY / SUPERVISOR ROLE)
        # -------------------------------------------------------------
        if actor.name.lower() in ["friday", "supervisor", "admin"] or actor.role in ["supervisor", "admin"]:
            decision = PolicyDecision(
                allowed=True,
                reason=f"Agent '{actor.name}' is an ecosystem supervisor with global oversight permissions.",
                rule_matched="RULE_0_SUPERVISOR_GLOBAL_ACCESS",
                dimensions=dims
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

            if not namespace.path.startswith(actor.bounded_scope):
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
            else:
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
            decision = PolicyDecision(
                allowed=True,
                reason=f"Namespace '{namespace.path}' is {namespace.type.value} and openly readable.",
                rule_matched="PUBLIC_OR_GLOBAL_ACCESS",
                dimensions=dims
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

            if action not in grant.actions and "*" not in grant.actions:
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
            })
        if log_audit:
            cls.log_audit_decision(db, decision, actor_id=actor_id, memory_id=memory_id)

    @classmethod
    def log_audit_decision(
        cls,
        db: Session,
        decision: PolicyDecision,
        actor_id: Optional[str] = None,
        memory_id: Optional[str] = None
    ) -> AuditLog:
        action_name = "policy_approved" if decision.allowed else "policy_denied"
        audit_entry = AuditLog(
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
        db.commit()
        db.refresh(audit_entry)
        return audit_entry