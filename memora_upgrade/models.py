from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence
import hashlib
import json
import math
import time
import uuid


class MemoryKind(str, Enum):
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


class Lifecycle(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    DELETED = "deleted"


class ScopeKind(str, Enum):
    TENANT = "tenant"
    AGENT = "agent"
    SESSION = "session"
    PROJECT = "project"
    TEAM = "team"
    GLOBAL = "global"


@dataclass(frozen=True, slots=True)
class MemoryScope:
    tenant_id: str
    agent_id: str | None = None
    session_id: str | None = None
    project_id: str | None = None
    team_id: str | None = None
    visibility: ScopeKind = ScopeKind.AGENT

    def key(self) -> tuple[str, str | None, str | None, str | None, str | None, str]:
        return (
            self.tenant_id,
            self.agent_id,
            self.session_id,
            self.project_id,
            self.team_id,
            self.visibility.value,
        )


@dataclass(frozen=True, slots=True)
class Provenance:
    source: str
    source_id: str | None = None
    observed_at: datetime | None = None
    author: str | None = None
    authority: float = 0.5
    evidence_uri: str | None = None

    def normalized(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "source_id": self.source_id,
            "observed_at": (self.observed_at or datetime.now(timezone.utc)).isoformat(),
            "author": self.author,
            "authority": max(0.0, min(1.0, self.authority)),
            "evidence_uri": self.evidence_uri,
        }


@dataclass(frozen=True, slots=True)
class TemporalBounds:
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    observed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def is_valid_at(self, when: datetime) -> bool:
        when = when if when.tzinfo else when.replace(tzinfo=timezone.utc)
        start = self.valid_from.astimezone(timezone.utc) if self.valid_from else None
        end = self.valid_until.astimezone(timezone.utc) if self.valid_until else None
        if start and when < start:
            return False
        if end and when >= end:
            return False
        return True


@dataclass(frozen=True, slots=True)
class MemoryItem:
    id: str
    tenant_id: str
    owner_agent_id: str
    kind: MemoryKind
    text: str
    scope: MemoryScope
    confidence: float
    importance: float
    lifecycle: Lifecycle = Lifecycle.CANDIDATE
    provenance: Provenance = field(default_factory=lambda: Provenance("unknown"))
    temporal: TemporalBounds = field(default_factory=TemporalBounds)
    tags: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    source_event_id: str | None = None
    parent_memory_id: str | None = None
    version: int = 1
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_accessed_at: datetime | None = None
    last_verified_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.tenant_id or not self.owner_agent_id:
            raise ValueError("memory identity fields are required")
        if not self.text.strip():
            raise ValueError("memory text must not be blank")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence outside [0,1]")
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError("importance outside [0,1]")
        if self.version < 1:
            raise ValueError("version must be >= 1")

    @property
    def content_hash(self) -> str:
        normalized = " ".join(self.text.split()).strip().casefold()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def dedup_key(self) -> str:
        return hashlib.sha256(
            "|".join(
                [self.tenant_id, self.scope.agent_id or "", self.kind.value, self.content_hash]
            ).encode("utf-8")
        ).hexdigest()

    def authority_score(self) -> float:
        verification = 0.20 if self.lifecycle == Lifecycle.VERIFIED else 0.0
        provenance = 0.30 * max(0.0, min(1.0, self.provenance.authority))
        return min(1.0, 0.50 * self.confidence + provenance + verification)

    def freshness_score(self, now: datetime | None = None, half_life_days: float = 90.0) -> float:
        now = now or datetime.now(timezone.utc)
        observed = self.temporal.observed_at
        age = max(0.0, (now - (observed if observed.tzinfo else observed.replace(tzinfo=timezone.utc))).total_seconds())
        return math.exp(-age / max(1.0, half_life_days * 86400.0))


@dataclass(frozen=True, slots=True)
class QueryContext:
    tenant_id: str
    actor_agent_id: str
    session_id: str | None = None
    project_id: str | None = None
    team_id: str | None = None
    purpose: str | None = None
    now: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    max_results: int = 20
    max_context_tokens: int = 3000
    include_archived: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalCandidate:
    memory: MemoryItem
    lexical_rank: int | None = None
    semantic_rank: int | None = None
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    entity_score: float = 0.0
    temporal_score: float = 0.0
    policy_score: float = 1.0
    final_score: float = 0.0

    def with_score(self, score: float) -> "RetrievalCandidate":
        return RetrievalCandidate(
            memory=self.memory,
            lexical_rank=self.lexical_rank,
            semantic_rank=self.semantic_rank,
            lexical_score=self.lexical_score,
            semantic_score=self.semantic_score,
            entity_score=self.entity_score,
            temporal_score=self.temporal_score,
            policy_score=self.policy_score,
            final_score=score,
        )


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    memory_id: str
    text: str
    score: float
    scope: MemoryScope
    provenance: Provenance
    lifecycle: Lifecycle
    rationale: tuple[str, ...] = ()
    citation: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryEvent:
    event_id: str
    event_type: str
    tenant_id: str
    actor_agent_id: str
    memory_id: str | None
    timestamp_ns: int
    payload: Mapping[str, Any]
    schema_version: int = 1

    @classmethod
    def create(cls, event_type: str, tenant_id: str, actor_agent_id: str, memory_id: str | None, payload: Mapping[str, Any]) -> "MemoryEvent":
        return cls(str(uuid.uuid4()), event_type, tenant_id, actor_agent_id, memory_id, time.time_ns(), dict(payload))


def token_estimate(text: str) -> int:
    return max(1, len(text) // 4)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
