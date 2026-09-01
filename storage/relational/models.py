"""
Canonical Relational Data Models for Memora
Defines Agent, Namespace, AccessGrant, MemoryRecord, MemoryRelationship, and AuditLog with SQLAlchemy 2.0.
"""
import enum
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from sqlalchemy import (
    String,
    Text,
    Float,
    ForeignKey,
    DateTime,
    JSON,
    Enum as SQLEnum,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from storage.relational.base import Base, generate_uuid, get_utc_now

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

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
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

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=generate_uuid)
    path: Mapped[str] = mapped_column(String(256), unique=True, index=True, nullable=False)
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
    namespace_id: Mapped[str] = mapped_column(String(64), ForeignKey("namespaces.id", ondelete="CASCADE"), nullable=False, index=True)
    owner_id: Mapped[str] = mapped_column(String(64), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    memory_type: Mapped[MemoryType] = mapped_column(
        SQLEnum(MemoryType, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        index=True
    )
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="unknown")
    provenance: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, default=dict)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    lifecycle_state: Mapped[LifecycleState] = mapped_column(
        SQLEnum(LifecycleState, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=LifecycleState.CANDIDATE,
        index=True
    )
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

    __table_args__ = (
        Index("ix_memory_owner_type", "owner_id", "memory_type"),
        Index("ix_memory_ns_state", "namespace_id", "lifecycle_state"),
    )

    def __repr__(self) -> str:
        return f"<MemoryRecord(id={self.id}, type={self.memory_type}, state={self.lifecycle_state})>"

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
    actor_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("agents.id", ondelete="SET NULL"), nullable=True, index=True)
    memory_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("memory_records.id", ondelete="SET NULL"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=get_utc_now, nullable=False, index=True)
    details: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, default=dict)

    # Relationships
    actor: Mapped[Optional["Agent"]] = relationship("Agent", back_populates="audit_logs")
    memory: Mapped[Optional["MemoryRecord"]] = relationship("MemoryRecord", back_populates="audit_logs")

    def __repr__(self) -> str:
        return f"<AuditLog(id={self.id}, action={self.action}, timestamp={self.timestamp})>"