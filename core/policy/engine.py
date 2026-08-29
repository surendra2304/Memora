"""
Access Control and Policy Enforcement Engine
Enforces boundaries on namespaces and actions, and logs audit events.
"""
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from storage.relational.models import Agent, Namespace, NamespaceType, MemoryRecord, AuditLog

class PolicyDecision:
    def __init__(self, allowed: bool, reason: str = ""):
        self.allowed = allowed
        self.reason = reason

    def __bool__(self) -> bool:
        return self.allowed

class PolicyEngine:
    @staticmethod
    def evaluate_access(
        db: Session,
        actor: Agent,
        namespace: Namespace,
        action: str,  # 'read', 'write', 'verify', 'supersede', 'delete'
    ) -> PolicyDecision:
        # Master supervisor bypass (e.g. FRIDAY or root)
        if actor.name in ["friday", "root", "admin", "surendra"]:
            return PolicyDecision(True, "Master supervisor authorization.")

        # Namespace policy evaluation
        if namespace.type == NamespaceType.PUBLIC:
            if action in ["read", "query"]:
                return PolicyDecision(True, "Public namespace read allowed.")
            return PolicyDecision(False, "Public namespace is read-only for standard agents.")

        if namespace.type == NamespaceType.UNIVERSE_GLOBAL:
            if action in ["read", "query", "write"]:
                return PolicyDecision(True, "Universe global namespace accessible to registered agents.")
            if action in ["delete", "supersede"]:
                return PolicyDecision(True, "Global modification allowed.")

        if namespace.type == NamespaceType.TEAM_SHARED:
            return PolicyDecision(True, "Team-shared namespace access granted.")

        if namespace.type in [NamespaceType.AGENT_PRIVATE, NamespaceType.PROJECT_PRIVATE]:
            if namespace.agent_id == actor.id:
                return PolicyDecision(True, "Agent owns private namespace.")
            return PolicyDecision(False, f"Access denied: Namespace is private to agent_id {namespace.agent_id}")

        return PolicyDecision(False, f"Access denied: No policy matched for namespace type {namespace.type}")

    @staticmethod
    def log_audit(
        db: Session,
        action: str,
        actor_id: Optional[str] = None,
        memory_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> AuditLog:
        log = AuditLog(
            action=action,
            actor_id=actor_id,
            memory_id=memory_id,
            details=details or {}
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        return log