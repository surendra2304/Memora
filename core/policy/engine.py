"""
Multi-Dimensional Policy Enforcement Engine for Memora
Evaluates access across 5 dimensions:
- Who: Actor identity, role, parentage, subagent scope bounds
- What: Action requested (read, write, query, verify, supersede, delete), memory type
- Where: Namespace URI path, isolation level (private, project, team, global, public)
- Why: Declared task intent / authorization purpose
- How long: Time-bounded expiration & validity window

Enforces core invariant rules:
- Rule 1: Private by default, shared by explicit promotion.
- Rule 2: Project-shared namespaces require explicit project membership / grant.
- Rule 3: Sub-agents have strictly bounded context; blocked from parent private history.
- Rule 4: Every policy check logs an immutable entry to AuditLog.
"""
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from storage.relational.models import Agent, Namespace, NamespaceType, AccessGrant, AuditLog

class PolicyDecision:
    def __init__(
        self,
        allowed: bool,
        reason: str = "",
        rule_matched: str = "",
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
        action: str,  # 'read', 'write', 'query', 'verify', 'supersede', 'delete'
        purpose: Optional[str] = None,
        memory_id: Optional[str] = None,
        log_audit: bool = True
    ) -> PolicyDecision:
        action_normalized = action.lower().strip()
        dims = {
            "who": {"actor_id": actor.id, "actor_name": actor.name, "role": actor.role, "parent_id": actor.parent_agent_id},
            "what": {"action": action_normalized, "memory_id": memory_id},
            "where": {"namespace_id": namespace.id, "namespace_path": namespace.path, "namespace_type": namespace.type.value},
            "why": {"purpose": purpose or "unspecified"},
            "how_long": {"evaluated_at": datetime.now(timezone.utc).isoformat()}
        }

        # -------------------------------------------------------------
        # DIMENSION 1: SUB-AGENT BOUNDED CONTEXT ENFORCEMENT
        # -------------------------------------------------------------
        if actor.bounded_scope:
            # Subagent cannot access parent or other agent's private namespace
            if namespace.type == NamespaceType.AGENT_PRIVATE:
                decision = PolicyDecision(
                    allowed=False,
                    reason=f"Sub-agent '{actor.name}' is bounded to scope '{actor.bounded_scope}' and strictly forbidden from accessing private namespace '{namespace.path}'.",
                    rule_matched="RULE_3_SUBAGENT_BOUNDED_CONTEXT_ISOLATION",
                    dimensions=dims
                )
                if log_audit:
                    cls.log_audit_decision(db, decision, actor.id, memory_id)
                return decision

            # Subagent must be accessing within its bounded scope path
            if not (namespace.path == actor.bounded_scope or namespace.path.startswith(f"{actor.bounded_scope}/")):
                # Check if explicitly granted shared/global access
                grant = cls._get_active_grant(db, actor.id, namespace.id, action_normalized)
                if not grant and namespace.type not in [NamespaceType.UNIVERSE_GLOBAL, NamespaceType.PUBLIC]:
                    decision = PolicyDecision(
                        allowed=False,
                        reason=f"Sub-agent '{actor.name}' attempted access outside bounded scope '{actor.bounded_scope}' to '{namespace.path}'.",
                        rule_matched="RULE_3_SUBAGENT_SCOPE_EXCEEDED",
                        dimensions=dims
                    )
                    if log_audit:
                        cls.log_audit_decision(db, decision, actor.id, memory_id)
                    return decision

        # -------------------------------------------------------------
        # SUPERVISOR BYPASS (FRIDAY / root / surendra)
        # -------------------------------------------------------------
        if actor.name in ["friday", "root", "admin", "surendra"] and not actor.bounded_scope:
            decision = PolicyDecision(
                allowed=True,
                reason="Master supervisor authorization granted.",
                rule_matched="RULE_0_SUPERVISOR_FULL_ACCESS",
                dimensions=dims
            )
            if log_audit:
                cls.log_audit_decision(db, decision, actor.id, memory_id)
            return decision

        # -------------------------------------------------------------
        # DIMENSION 2: NAMESPACE ISOLATION RULES
        # -------------------------------------------------------------

        # RULE 1: AGENT-PRIVATE NAMESPACES ("Private by default, shared by explicit promotion")
        if namespace.type == NamespaceType.AGENT_PRIVATE:
            if namespace.agent_id == actor.id:
                decision = PolicyDecision(
                    allowed=True,
                    reason=f"Agent '{actor.name}' owns private namespace '{namespace.path}'.",
                    rule_matched="RULE_1_OWNER_PRIVATE_ACCESS",
                    dimensions=dims
                )
                if log_audit:
                    cls.log_audit_decision(db, decision, actor.id, memory_id)
                return decision
            else:
                # Private namespaces are strictly inaccessible to other agents
                decision = PolicyDecision(
                    allowed=False,
                    reason=f"Access denied: Namespace '{namespace.path}' is private to another agent. Private namespaces are isolated by default.",
                    rule_matched="RULE_1_PRIVATE_BY_DEFAULT_PROMOTION_REQUIRED",
                    dimensions=dims
                )
                if log_audit:
                    cls.log_audit_decision(db, decision, actor.id, memory_id)
                return decision

        # RULE 2: PROJECT-PRIVATE / SHARED NAMESPACES (Requires Explicit Grant / Membership)
        if namespace.type in [NamespaceType.PROJECT_PRIVATE, NamespaceType.TEAM_SHARED]:
            # Check owner
            if namespace.agent_id == actor.id:
                decision = PolicyDecision(
                    allowed=True,
                    reason=f"Agent '{actor.name}' created or owns project namespace '{namespace.path}'.",
                    rule_matched="RULE_2_PROJECT_OWNER_ACCESS",
                    dimensions=dims
                )
                if log_audit:
                    cls.log_audit_decision(db, decision, actor.id, memory_id)
                return decision

            # Check explicit AccessGrant (Who + What + How long + Why)
            grant = cls._get_active_grant(db, actor.id, namespace.id, action_normalized)
            if grant:
                decision = PolicyDecision(
                    allowed=True,
                    reason=f"Explicit access grant valid for action '{action_normalized}' on '{namespace.path}'. (Purpose: {grant.purpose or 'General'})",
                    rule_matched="RULE_2_PROJECT_MEMBERSHIP_GRANT_ACTIVE",
                    dimensions=dims
                )
                if log_audit:
                    cls.log_audit_decision(db, decision, actor.id, memory_id)
                return decision
            else:
                decision = PolicyDecision(
                    allowed=False,
                    reason=f"Access denied: Agent '{actor.name}' lacks active project membership or access grant for '{namespace.path}'.",
                    rule_matched="RULE_2_PROJECT_MEMBERSHIP_REQUIRED",
                    dimensions=dims
                )
                if log_audit:
                    cls.log_audit_decision(db, decision, actor.id, memory_id)
                return decision

        # UNIVERSE-GLOBAL NAMESPACES
        if namespace.type == NamespaceType.UNIVERSE_GLOBAL:
            if action_normalized in ["read", "query", "write"]:
                decision = PolicyDecision(
                    allowed=True,
                    reason="Universe-global namespace accessible to registered agents.",
                    rule_matched="RULE_GLOBAL_REGISTERED_ACCESS",
                    dimensions=dims
                )
            elif action_normalized in ["verify", "supersede", "delete"]:
                decision = PolicyDecision(
                    allowed=True,
                    reason="Global modification allowed.",
                    rule_matched="RULE_GLOBAL_MODIFICATION_PERMITTED",
                    dimensions=dims
                )
            else:
                decision = PolicyDecision(
                    allowed=False,
                    reason=f"Action '{action_normalized}' not permitted on universe-global namespace.",
                    rule_matched="RULE_GLOBAL_ACTION_REJECTED",
                    dimensions=dims
                )
            if log_audit:
                cls.log_audit_decision(db, decision, actor.id, memory_id)
            return decision

        # PUBLIC NAMESPACES
        if namespace.type == NamespaceType.PUBLIC:
            if action_normalized in ["read", "query"]:
                decision = PolicyDecision(
                    allowed=True,
                    reason="Public namespace read permitted.",
                    rule_matched="RULE_PUBLIC_READ_ALLOWED",
                    dimensions=dims
                )
            else:
                decision = PolicyDecision(
                    allowed=False,
                    reason="Public namespaces are strictly read-only.",
                    rule_matched="RULE_PUBLIC_WRITE_REJECTED",
                    dimensions=dims
                )
            if log_audit:
                cls.log_audit_decision(db, decision, actor.id, memory_id)
            return decision

        # Fallback denial
        decision = PolicyDecision(
            allowed=False,
            reason=f"Access denied: No matching policy rule for namespace type '{namespace.type.value}'.",
            rule_matched="RULE_DEFAULT_DENY",
            dimensions=dims
        )
        if log_audit:
            cls.log_audit_decision(db, decision, actor.id, memory_id)
        return decision

    @classmethod
    def _get_active_grant(cls, db: Session, agent_id: str, namespace_id: str, action: str) -> Optional[AccessGrant]:
        grants = db.query(AccessGrant).filter(
            AccessGrant.agent_id == agent_id,
            AccessGrant.namespace_id == namespace_id
        ).all()

        for g in grants:
            if not g.is_expired():
                # Check action list
                if action in g.actions or "*" in g.actions:
                    return g
        return None

    @classmethod
    def log_audit_decision(
        cls,
        db: Session,
        decision: PolicyDecision,
        actor_id: Optional[str] = None,
        memory_id: Optional[str] = None
    ) -> AuditLog:
        action_name = "policy_approved" if decision.allowed else "policy_denied"
        log = AuditLog(
            action=action_name,
            actor_id=actor_id,
            memory_id=memory_id,
            details=decision.to_dict()
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log