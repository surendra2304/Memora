"""
Canonical Relational Data Models for Memora
Defines Agent, Namespace, AccessGrant, MemoryRecord, MemoryRelationship, and AuditLog with SQLAlchemy 2.0.
"""
import enum
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from sqlalchemy import (
    CheckConstraint,
    String,
    Text,
    Float,
    Boolean,
    Integer,
    ForeignKey,
    DateTime,
    JSON,
    Enum as SQLEnum,
    Index,
    UniqueConstraint,
    event,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, Session
from storage.relational.base import Base, generate_uuid, get_utc_now


#: Sentinel stored in ``memory_shared_agents.agent_name`` meaning "every agent
#: in the tenant may read this SHARED memory".
SHARED_WITH_ALL_AGENTS = "*"


class MemoryScopeType(str, enum.Enum):
    """Memory visibility tier, independent of the namespace hierarchy.

    PRIVATE   - readable and writable only by ``owner_agent``.
    SHARED    - readable by the agents listed in ``memory_shared_agents``
                (or by everyone when the ``*`` sentinel is present); writable
                only by ``owner_agent``.
    UNIVERSAL - readable by every agent in the tenant; writable only by the
                Memora service identity / administrative paths.

    ``NamespaceType`` describes where a memory physically lives; this enum
    describes who may see it. Keeping them separate lets one schema host all
    three tiers without per-agent schemas.
    """

    PRIVATE = "PRIVATE"
    SHARED = "SHARED"
    UNIVERSAL = "UNIVERSAL"


#: Derivation used whenever a writer does not state a scope explicitly. It is
#: the historical access semantics of each namespace type, encoded once so the
#: ORM hook and the write pipeline cannot drift apart.
_NAMESPACE_SCOPE_DEFAULTS = {
    "universe-global": MemoryScopeType.UNIVERSAL,
    "public": MemoryScopeType.UNIVERSAL,
    "team-shared": MemoryScopeType.SHARED,
    "agent-private": MemoryScopeType.PRIVATE,
    "project-private": MemoryScopeType.PRIVATE,
}


def normalize_agent_name(value: Optional[str]) -> str:
    """Canonical agent handle used by the access predicate."""
    return (value or "").strip().lower()


def scope_for_namespace_type(namespace_type: Any) -> MemoryScopeType:
    raw = getattr(namespace_type, "value", namespace_type)
    return _NAMESPACE_SCOPE_DEFAULTS.get(str(raw or "").strip().lower(), MemoryScopeType.PRIVATE)


class NamespaceType(str, enum.Enum):
    AGENT_PRIVATE = "agent-private"
    PROJECT_PRIVATE = "project-private"
    TEAM_SHARED = "team-shared"
    UNIVERSE_GLOBAL = "universe-global"
    PUBLIC = "public"

class MemoryType(str, enum.Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    PREFERENCE = "preference"
    WORKING = "working"
    DECISION = "decision"
    RELATIONSHIP = "relationship"
    EXPERIENCE = "experience"
    PROJECT = "project"
    TOOL = "tool"
    SYSTEM = "system"

class LifecycleState(str, enum.Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    DELETED = "deleted"

class Agent(Base):
    """
    Represents an actor / agent in the ecosystem (e.g., FRIDAY, FORGE, NEXUS).
    Supports hierarchical parent-subagent delegation with bounded scopes.
    """
    __tablename__ = "agents"
    # Agent names are unique *per tenant*, not globally. A single-column unique
    # constraint here contradicted the (name, tenant_id) lookups in
    # IdentityService.register_agent and made it impossible for a second tenant
    # to register an agent name the first one already used (sqlite raised
    # "UNIQUE constraint failed: agents.name").
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_agents_tenant_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    name: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="worker")
    
    # Sub-agent hierarchy & bounded context
    parent_agent_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True)
    bounded_scope: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    # Relationships
    parent_agent: Mapped[Optional["Agent"]] = relationship("Agent", remote_side=[id], back_populates="sub_agents")
    sub_agents: Mapped[List["Agent"]] = relationship("Agent", back_populates="parent_agent")
    namespaces: Mapped[List["Namespace"]] = relationship("Namespace", back_populates="agent", cascade="all, delete-orphan")
    owned_memories: Mapped[List["MemoryRecord"]] = relationship("MemoryRecord", back_populates="owner", cascade="all, delete-orphan")
    access_grants: Mapped[List["AccessGrant"]] = relationship("AccessGrant", back_populates="agent", cascade="all, delete-orphan")
    audit_logs: Mapped[List["AuditLog"]] = relationship("AuditLog", back_populates="actor")

    def __repr__(self) -> str:
        return f"<Agent(id={self.id}, name={self.name}, role={self.role})>"

class Namespace(Base):
    """
    Represents logical memory boundaries (e.g. 'memora://friday/private').
    """
    __tablename__ = "namespaces"
    # Namespace paths are unique *per tenant*, not globally. IdentityService
    # resolve_namespace / get_namespace_by_path look namespaces up by
    # (path, tenant_id), so a global unique constraint made it impossible for a
    # second tenant to use a path the first one already had
    # ("UNIQUE constraint failed: namespaces.path").
    __table_args__ = (UniqueConstraint("tenant_id", "path", name="uq_namespaces_tenant_path"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    path: Mapped[str] = mapped_column(String(256), index=True, nullable=False)
    agent_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("agents.id", ondelete="CASCADE"), nullable=True)
    type: Mapped[NamespaceType] = mapped_column(
        SQLEnum(NamespaceType, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=NamespaceType.AGENT_PRIVATE,
        index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    # Relationships
    agent: Mapped[Optional["Agent"]] = relationship("Agent", back_populates="namespaces")
    memories: Mapped[List["MemoryRecord"]] = relationship("MemoryRecord", back_populates="namespace", cascade="all, delete-orphan")
    access_grants: Mapped[List["AccessGrant"]] = relationship("AccessGrant", back_populates="namespace", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Namespace(id={self.id}, path={self.path}, type={self.type})>"

class AccessGrant(Base):
    """
    Explicit access permission grant for an Agent on a Namespace.
    """
    __tablename__ = "access_grants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    agent_id: Mapped[str] = mapped_column(String(64), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    namespace_id: Mapped[str] = mapped_column(String(64), ForeignKey("namespaces.id", ondelete="CASCADE"), nullable=False, index=True)
    actions: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=lambda: ["read"])
    purpose: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    # Relationships
    agent: Mapped["Agent"] = relationship("Agent", back_populates="access_grants")
    namespace: Mapped["Namespace"] = relationship("Namespace", back_populates="access_grants")

    __table_args__ = (
        Index("ix_grant_agent_ns", "agent_id", "namespace_id"),
        Index("ix_grant_tenant_agent", "tenant_id", "agent_id"),
    )

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        now = datetime.now(timezone.utc)
        if self.expires_at.tzinfo is None:
            # Assume UTC if naive
            exp = self.expires_at.replace(tzinfo=timezone.utc)
        else:
            exp = self.expires_at
        return now > exp

    def __repr__(self) -> str:
        return f"<AccessGrant(id={self.id}, agent_id={self.agent_id}, namespace_id={self.namespace_id})>"

class MemoryRecord(Base):
    """
    The canonical memory entity storing persistent contextual information.
    """
    __tablename__ = "memory_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default_user", index=True)
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, default="friday", index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default_workspace", index=True)
    device_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default_device", index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # Scoped to the writing identity, not globally. A global unique constraint made
    # one agent's idempotency key shadow every other agent's inside the shared
    # tenant: a second agent reusing the same key silently lost its own write and
    # was handed back the first agent's record, content included.
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    namespace_id: Mapped[str] = mapped_column(String(64), ForeignKey("namespaces.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    # Scope tier + owner handle are the authorization pair evaluated by every
    # read path. ``owner_agent`` deliberately stores the lower-case agent name,
    # not an FK: authorization is name-based in the API, and the name survives
    # agent-row re-creation during bootstrap.
    scope: Mapped[MemoryScopeType] = mapped_column(
        SQLEnum(MemoryScopeType, values_callable=lambda obj: [e.value for e in obj], name="memory_scope_type"),
        nullable=False,
        default=MemoryScopeType.PRIVATE,
    )
    owner_agent: Mapped[str] = mapped_column(String(128), nullable=False, default="friday")
    memory_type: Mapped[MemoryType] = mapped_column(
        SQLEnum(MemoryType, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        index=True
    )
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
    provenance: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, default=dict)
    entities: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    lifecycle_state: Mapped[LifecycleState] = mapped_column(
        SQLEnum(LifecycleState, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=LifecycleState.CANDIDATE,
        index=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    valid_from: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, nullable=False, index=True)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    superseded_by_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("memory_records.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    namespace: Mapped["Namespace"] = relationship("Namespace", back_populates="memories")
    owner: Mapped["Agent"] = relationship("Agent", back_populates="owned_memories")
    superseded_by: Mapped[Optional["MemoryRecord"]] = relationship("MemoryRecord", remote_side=[id], foreign_keys=[superseded_by_id])
    audit_logs: Mapped[List["AuditLog"]] = relationship("AuditLog", back_populates="memory")
    outgoing_relationships: Mapped[List["MemoryRelationship"]] = relationship("MemoryRelationship", foreign_keys="MemoryRelationship.source_memory_id", back_populates="source_memory", cascade="all, delete-orphan")
    incoming_relationships: Mapped[List["MemoryRelationship"]] = relationship("MemoryRelationship", foreign_keys="MemoryRelationship.target_memory_id", back_populates="target_memory", cascade="all, delete-orphan")
    shared_agents: Mapped[List["MemorySharedAgent"]] = relationship("MemorySharedAgent", back_populates="memory", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "scope IN ('PRIVATE', 'SHARED', 'UNIVERSAL')",
            name="ck_memory_scope",
        ),
        # Idempotency is per (tenant, writing agent). NULL is distinct from NULL in
        # both SQLite and PostgreSQL unique indexes, so ordinary writes that carry no
        # idempotency key are never constrained against each other.
        Index("uq_memory_idempotency_scope", "tenant_id", "agent_id", "idempotency_key", unique=True),
        Index("ix_memory_owner_type", "owner_id", "memory_type"),
        Index("ix_memory_ns_state", "namespace_id", "lifecycle_state"),
        Index("ix_memory_tenant_owner", "tenant_id", "owner_id"),
        Index("ix_memory_tenant_ns", "tenant_id", "namespace_id"),
        Index("ix_memory_tenant_state", "tenant_id", "lifecycle_state"),
        Index("ix_memory_tenant_expires", "tenant_id", "expires_at"),
        Index("ix_memory_tenant_created", "tenant_id", "created_at"),
        Index("ix_memory_identity_user", "tenant_id", "user_id"),
        Index("ix_memory_identity_agent", "tenant_id", "agent_id"),
        Index("ix_memory_identity_task", "tenant_id", "task_id"),
        Index("ix_memory_identity_ws", "tenant_id", "workspace_id"),
        Index("ix_memory_scope_owner", "scope", "owner_agent"),
    )

    def __repr__(self) -> str:
        return f"<MemoryRecord(id={self.id}, type={self.memory_type}, state={self.lifecycle_state})>"


class MemorySharedAgent(Base):
    """Per-memory read allow-list for the SHARED scope.

    A row is ``(memory_id, agent_name)``. The sentinel ``*`` means the memory is
    shared with every agent in the tenant. A join table (instead of a JSON/CLOB
    column on ``memory_records``) keeps membership indexable on Oracle, avoids
    read-modify-write races on revocation, and cascades with the memory row.
    """

    __tablename__ = "memory_shared_agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    memory_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("memory_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    granted_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    memory: Mapped["MemoryRecord"] = relationship("MemoryRecord", back_populates="shared_agents")

    __table_args__ = (
        UniqueConstraint("memory_id", "agent_name", name="uq_memory_shared_agent"),
        Index("ix_memory_shared_lookup", "tenant_id", "agent_name"),
    )

    def __repr__(self) -> str:
        return f"<MemorySharedAgent(memory_id={self.memory_id}, agent={self.agent_name})>"


@event.listens_for(Session, "before_flush")
def _stamp_memory_scope(session: Session, _flush_context, _instances) -> None:
    """Derive ``scope``/``owner_agent`` for records created without them.

    Most of the codebase -- and the whole test suite -- constructs
    ``MemoryRecord`` rows directly. Requiring every call site to pass the new
    columns would have produced PRIVATE/``friday`` rows for records that really
    belong to another agent or to an open namespace, silently changing who can
    read them. Deriving here keeps legacy construction semantics identical to
    what the namespace/policy layer already implied, while the API write path
    still stamps an explicit scope it validated.
    """
    pending_agents = {obj.id: obj for obj in session.new if isinstance(obj, Agent)}
    pending_namespaces = {obj.id: obj for obj in session.new if isinstance(obj, Namespace)}
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, MemoryRecord):
            continue

        if not obj.owner_agent:
            candidate = normalize_agent_name(obj.agent_id)
            if not candidate and obj.owner is not None:
                candidate = normalize_agent_name(obj.owner.name)
            if not candidate and obj.owner_id:
                owner = pending_agents.get(obj.owner_id)
                if owner is None:
                    owner = session.get(Agent, obj.owner_id)
                if owner is not None:
                    candidate = normalize_agent_name(owner.name)
            obj.owner_agent = candidate or "friday"

        if obj.scope is None:
            namespace = obj.namespace
            if namespace is None and obj.namespace_id:
                namespace = pending_namespaces.get(obj.namespace_id)
                if namespace is None:
                    namespace = session.get(Namespace, obj.namespace_id)
            obj.scope = scope_for_namespace_type(getattr(namespace, "type", None))

class MemoryRelationship(Base):
    """
    Graph/Relationship layer simulating entity dependencies and knowledge links.
    """
    __tablename__ = "memory_relationships"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    source_memory_id: Mapped[str] = mapped_column(String(64), ForeignKey("memory_records.id", ondelete="CASCADE"), nullable=False, index=True)
    target_memory_id: Mapped[str] = mapped_column(String(64), ForeignKey("memory_records.id", ondelete="CASCADE"), nullable=False, index=True)
    relationship_type: Mapped[str] = mapped_column(String(64), nullable=False, default="relates_to", index=True)  # derived_from, depends_on, contradicts, supersedes, relates_to, causal_child
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, nullable=False)

    # Relationships
    source_memory: Mapped["MemoryRecord"] = relationship("MemoryRecord", foreign_keys=[source_memory_id], back_populates="outgoing_relationships")
    target_memory: Mapped["MemoryRecord"] = relationship("MemoryRecord", foreign_keys=[target_memory_id], back_populates="incoming_relationships")

    __table_args__ = (
        Index("ix_relationship_src_tgt", "source_memory_id", "target_memory_id"),
    )

    def __repr__(self) -> str:
        return f"<MemoryRelationship({self.source_memory_id} -[{self.relationship_type}]-> {self.target_memory_id})>"

class AuditLog(Base):
    """
    Tracks all reads, writes, queries, and policy decisions on memory entities.
    """
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    actor_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True)
    memory_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("memory_records.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, nullable=False, index=True)
    details: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, default=dict)

    # Relationships
    actor: Mapped[Optional["Agent"]] = relationship("Agent", back_populates="audit_logs")
    memory: Mapped[Optional["MemoryRecord"]] = relationship("MemoryRecord", back_populates="audit_logs")

    __table_args__ = (
        Index("ix_audit_tenant_action", "tenant_id", "action"),
    )

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, action={self.action}, timestamp={self.timestamp})>"

class DeletionTombstone(Base):
    """
    Durable multi-store deletion outbox tracking SQL, vector, cache, and graph convergence.
    """
    __tablename__ = "deletion_tombstones"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    memory_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    relational_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    vector_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cache_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    graph_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # A local hard delete is converged only after an enabled Turso write-through
    # replica has also recorded a durable deletion fence. Existing tombstones
    # default to complete because older versions did not track this replica.
    turso_deleted: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, index=True)  # PENDING, CONVERGED, FAILED
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)

    __table_args__ = (
        Index("ix_tombstone_tenant_status", "tenant_id", "status"),
    )

    def is_converged(self) -> bool:
        return (
            self.relational_deleted
            and self.vector_deleted
            and self.cache_deleted
            and self.graph_deleted
            and bool(self.turso_deleted)
        )

    def __repr__(self) -> str:
        return f"<DeletionTombstone(memory_id={self.memory_id}, status={self.status})>"


class EventLog(Base):
    """Durable Memora event feed. Consumers replay with the monotonic row id cursor."""
    __tablename__ = "event_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default", index=True)
    target_agent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    cloud_synced: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, nullable=False, index=True)

    __table_args__ = (Index("ix_event_log_tenant_cursor", "tenant_id", "id"), Index("ix_event_log_target_cursor", "target_agent", "id"))

    def __repr__(self) -> str:
        return f"<EventLog(id={self.id}, event_type={self.event_type})>"


class EventConsumerCursor(Base):
    """Per-agent durable acknowledgement point for the shared event feed."""
    __tablename__ = "event_consumer_cursors"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    agent: Mapped[str] = mapped_column(String(64), primary_key=True)
    consumer_id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    last_event_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, onupdate=get_utc_now, nullable=False)
