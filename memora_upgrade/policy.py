from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable
import fnmatch


@dataclass(frozen=True, slots=True)
class AccessGrant:
    tenant_id: str
    actor_agent_id: str
    namespace_prefix: str
    actions: frozenset[str]
    purpose: str | None = None
    expires_at_ns: int | None = None

    def allows(self, *, tenant_id: str, actor_agent_id: str, namespace_key: str, action: str, purpose: str | None, now_ns: int) -> bool:
        if tenant_id != self.tenant_id or actor_agent_id != self.actor_agent_id:
            return False
        if self.expires_at_ns is not None and now_ns >= self.expires_at_ns:
            return False
        if action not in self.actions and "*" not in self.actions:
            return False
        if self.purpose is not None and self.purpose != purpose:
            return False
        return fnmatch.fnmatch(namespace_key, self.namespace_prefix)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: str
    rule: str


class MemoryPolicy:
    """Default-deny tenant/agent isolation with explicit share rules."""

    def __init__(self, grants: Iterable[AccessGrant] = ()):
        self._grants = tuple(grants)

    def can_read(self, tenant_id: str, actor_agent_id: str, memory_tenant_id: str, owner_agent_id: str, namespace_key: str, purpose: str | None, now_ns: int) -> PolicyDecision:
        if tenant_id != memory_tenant_id:
            return PolicyDecision(False, "tenant isolation", "TENANT_MISMATCH")
        if actor_agent_id == owner_agent_id:
            return PolicyDecision(True, "owner access", "OWNER_ACCESS")
        for grant in self._grants:
            if grant.allows(
                tenant_id=tenant_id,
                actor_agent_id=actor_agent_id,
                namespace_key=namespace_key,
                action="read",
                purpose=purpose,
                now_ns=now_ns,
            ):
                return PolicyDecision(True, "explicit grant", "EXPLICIT_GRANT")
        return PolicyDecision(False, "no read grant", "DEFAULT_DENY")

    def can_write(self, tenant_id: str, actor_agent_id: str, memory_tenant_id: str, owner_agent_id: str, namespace_key: str, purpose: str | None, now_ns: int) -> PolicyDecision:
        if tenant_id != memory_tenant_id:
            return PolicyDecision(False, "tenant isolation", "TENANT_MISMATCH")
        if actor_agent_id == owner_agent_id:
            return PolicyDecision(True, "owner write", "OWNER_WRITE")
        for grant in self._grants:
            if grant.allows(
                tenant_id=tenant_id,
                actor_agent_id=actor_agent_id,
                namespace_key=namespace_key,
                action="write",
                purpose=purpose,
                now_ns=now_ns,
            ):
                return PolicyDecision(True, "explicit write grant", "EXPLICIT_WRITE_GRANT")
        return PolicyDecision(False, "write denied", "DEFAULT_DENY")
