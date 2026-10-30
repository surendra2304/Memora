"""Scope-based memory access control.

Memora used to express visibility only through namespace hierarchy and
``access_grants``. That made "private to an agent", "shared with a named set of
agents" and "readable by the whole universe" depend on namespace naming
conventions that every retrieval path had to re-derive -- and most paths did not
re-derive it at all. This module is the single place that decides whether a
memory row is visible to a caller, and expresses that decision both as a SQL
predicate (so the database never materialises invisible rows) and as a Python
check (for rows already loaded).

The authorization model is a property of the record, not of the caller-supplied
identity fields:

    PRIVATE   owner_agent = caller
    SHARED    caller is listed in memory_shared_agents for the record, or the
              record carries the ``*`` sentinel (shared with all agents)
    UNIVERSAL always readable inside the tenant

Writes are narrower than reads: only ``owner_agent`` may mutate a PRIVATE or
SHARED record. UNIVERSAL records are created and mutated exclusively by the
Memora service identity / administrative surfaces.

Every helper fails closed: a missing caller matches nothing.
"""
from typing import Optional

from sqlalchemy import and_, exists, false, or_
from sqlalchemy.orm import Query, Session

from storage.relational.models import (
    MemoryRecord,
    MemoryScopeType,
    MemorySharedAgent,
    SHARED_WITH_ALL_AGENTS,
    normalize_agent_name,
)

#: Agent identity allowed to author UNIVERSAL memories and to run the
#: administrative maintenance paths (decay, sweeps, repair).
SERVICE_AGENT = "memora"


def visible_memory_predicate(caller_agent: Optional[str]):
    """Return the SQL predicate granting ``caller_agent`` read access.

    The predicate is intentionally self-contained (one correlated EXISTS) so it
    can be appended to any query that already has ``memory_records`` in its
    FROM/joins, including the lexical and vector-candidate legs of hybrid
    search.

    ``owner_agent = caller`` is included in the SHARED arm because an owner may
    always read what it wrote, even when the allow-list is empty.
    """
    caller = normalize_agent_name(caller_agent)
    if not caller:
        # Fail closed: an unauthenticated/unknown caller sees nothing.
        return false()

    shared_with_caller = exists().where(
        and_(
            MemorySharedAgent.memory_id == MemoryRecord.id,
            MemorySharedAgent.agent_name.in_((caller, SHARED_WITH_ALL_AGENTS)),
        )
    )

    return or_(
        MemoryRecord.scope == MemoryScopeType.UNIVERSAL,
        and_(
            MemoryRecord.scope == MemoryScopeType.SHARED,
            or_(MemoryRecord.owner_agent == caller, shared_with_caller),
        ),
        and_(
            MemoryRecord.scope == MemoryScopeType.PRIVATE,
            MemoryRecord.owner_agent == caller,
        ),
    )


def scope_memory_query(query: Query, caller_agent: Optional[str]) -> Query:
    """Apply the visibility predicate to a ``MemoryRecord`` query."""
    return query.filter(visible_memory_predicate(caller_agent))


def record_is_visible(record: MemoryRecord, caller_agent: Optional[str]) -> bool:
    """Python mirror of :func:`visible_memory_predicate` for loaded rows.

    ``record.shared_agents`` is consulted when it has already been loaded;
    otherwise the predicate's membership check is not repeated here, so callers
    that post-filter loaded rows should prefer the SQL predicate. The services
    that use this helper load membership explicitly or filter in SQL first.
    """
    caller = normalize_agent_name(caller_agent)
    if not caller:
        return False

    scope = getattr(record, "scope", None)
    if scope is None:
        # Legacy row that pre-dates the scope column. It has not been backfilled
        # yet, so fall back to the namespace-derived tier rather than denying
        # every read during a rolling upgrade.
        from storage.relational.models import scope_for_namespace_type

        scope = scope_for_namespace_type(
            getattr(getattr(record, "namespace", None), "type", None)
        )

    owner = normalize_agent_name(getattr(record, "owner_agent", None))
    if scope == MemoryScopeType.UNIVERSAL:
        return True
    if scope == MemoryScopeType.PRIVATE:
        return bool(owner) and owner == caller
    if scope == MemoryScopeType.SHARED:
        if owner and owner == caller:
            return True
        members = getattr(record, "shared_agents", None)
        if members is None:
            return False
        allowed = {
            normalize_agent_name(member.agent_name) for member in members
        }
        return SHARED_WITH_ALL_AGENTS in allowed or caller in allowed
    return False


def may_mutate_record(record: MemoryRecord, caller_agent: Optional[str]) -> bool:
    """Only the owning agent may mutate a PRIVATE/SHARED memory.

    UNIVERSAL memories belong to the fabric, so authoring/mutating them is
    limited to the Memora service identity. Administrative maintenance paths
    (decay, horizon sweeps) do not call this helper; they run under an explicit
    service-identity check of their own.
    """
    caller = normalize_agent_name(caller_agent)
    if not caller:
        return False
    scope = getattr(record, "scope", None)
    if scope is None:
        from storage.relational.models import scope_for_namespace_type

        scope = scope_for_namespace_type(
            getattr(getattr(record, "namespace", None), "type", None)
        )
    if scope == MemoryScopeType.UNIVERSAL:
        return caller == SERVICE_AGENT
    return normalize_agent_name(getattr(record, "owner_agent", None)) == caller


def shared_membership(db: Session, memory_id: str) -> list[str]:
    """Return the normalised allow-list for a memory (empty when PRIVATE)."""
    rows = (
        db.query(MemorySharedAgent.agent_name)
        .filter(MemorySharedAgent.memory_id == memory_id)
        .all()
    )
    return [normalize_agent_name(row[0]) for row in rows if row[0]]


def grant_shared_access(
    db: Session,
    record: MemoryRecord,
    agent_name: str,
    granted_by: Optional[str] = None,
    commit: bool = False,
) -> MemorySharedAgent:
    """Add ``agent_name`` to a record's SHARED allow-list (idempotent)."""
    target = normalize_agent_name(agent_name)
    if not target:
        raise ValueError("A shared memory needs a non-empty target agent name.")

    existing = (
        db.query(MemorySharedAgent)
        .filter(
            MemorySharedAgent.memory_id == record.id,
            MemorySharedAgent.agent_name == target,
        )
        .first()
    )
    if existing is not None:
        return existing

    membership = MemorySharedAgent(
        tenant_id=record.tenant_id,
        memory_id=record.id,
        agent_name=target,
        granted_by=normalize_agent_name(granted_by) or None,
    )
    db.add(membership)
    db.flush()
    return membership


def share_record_with(
    db: Session,
    record: MemoryRecord,
    agent_name: str,
    granted_by: Optional[str] = None,
) -> MemorySharedAgent:
    """Promote a record to SHARED and add one recipient.

    PRIVATE memories are escalated to SHARED; UNIVERSAL memories stay
    UNIVERSAL (already readable by everyone). The caller is responsible for
    having verified ownership first.
    """
    if record.scope != MemoryScopeType.UNIVERSAL:
        record.scope = MemoryScopeType.SHARED
    return grant_shared_access(db, record, agent_name, granted_by=granted_by)


def share_record_with_all(
    db: Session,
    record: MemoryRecord,
    granted_by: Optional[str] = None,
) -> MemorySharedAgent:
    """Promote a record to SHARED and make it readable by every agent."""
    if record.scope != MemoryScopeType.UNIVERSAL:
        record.scope = MemoryScopeType.SHARED
    return grant_shared_access(
        db, record, SHARED_WITH_ALL_AGENTS, granted_by=granted_by
    )


__all__ = [
    "SERVICE_AGENT",
    "grant_shared_access",
    "may_mutate_record",
    "record_is_visible",
    "scope_memory_query",
    "share_record_with",
    "share_record_with_all",
    "shared_membership",
    "visible_memory_predicate",
]
