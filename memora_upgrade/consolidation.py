from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable
import math

from .models import MemoryItem, Lifecycle


@dataclass(frozen=True, slots=True)
class ConsolidationDecision:
    memory_id: str
    action: str
    reason: str
    score: float


@dataclass(frozen=True, slots=True)
class ConsolidationConfig:
    min_confidence: float = 0.65
    min_importance: float = 0.40
    merge_similarity_threshold: float = 0.90
    archive_after_days: float = 90.0
    delete_after_days: float = 365.0


def consolidation_score(memory: MemoryItem, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    freshness = memory.freshness_score(now)
    authority = memory.authority_score()
    return min(1.0, 0.45 * memory.importance + 0.35 * authority + 0.20 * freshness)


class Consolidator:
    """Turns many low-value episodic fragments into fewer high-quality durable memories."""

    def __init__(self, config: ConsolidationConfig | None = None):
        self.config = config or ConsolidationConfig()

    def decide(self, memory: MemoryItem, now: datetime | None = None) -> ConsolidationDecision:
        now = now or datetime.now(timezone.utc)
        score = consolidation_score(memory, now)
        age_days = max(0.0, (now - memory.created_at.astimezone(timezone.utc)).total_seconds() / 86400)
        if memory.lifecycle == Lifecycle.DELETED:
            return ConsolidationDecision(memory.id, "DELETE", "already deleted", score)
        if memory.confidence < self.config.min_confidence and age_days >= 30:
            return ConsolidationDecision(memory.id, "ARCHIVE", "low confidence aging", score)
        if memory.importance < self.config.min_importance and age_days >= self.config.archive_after_days:
            return ConsolidationDecision(memory.id, "ARCHIVE", "low importance aging", score)
        if age_days >= self.config.delete_after_days and memory.lifecycle == Lifecycle.ARCHIVED:
            return ConsolidationDecision(memory.id, "DELETE", "retention expired", score)
        if memory.lifecycle == Lifecycle.CANDIDATE and memory.confidence >= self.config.min_confidence:
            return ConsolidationDecision(memory.id, "ACTIVATE", "confidence threshold", score)
        return ConsolidationDecision(memory.id, "KEEP", "within retention policy", score)


def conflict_score(memory: MemoryItem) -> float:
    return (
        0.55 * memory.confidence
        + 0.25 * memory.provenance.authority
        + 0.20 * memory.freshness_score()
    )


def pick_winner(items: Iterable[MemoryItem]) -> MemoryItem:
    candidates = list(items)
    if not candidates:
        raise ValueError("no memories")
    return max(candidates, key=lambda m: (conflict_score(m), m.id))


class TemporalResolver:
    """Separates observed recency from factual validity; never resolves conflicts by recency alone."""

    def resolve(self, existing: MemoryItem, incoming: MemoryItem) -> tuple[MemoryItem, str]:
        a = conflict_score(existing)
        b = conflict_score(incoming)
        if b > a:
            return incoming, f"incoming evidence {b:.4f} > existing {a:.4f}"
        if a > b:
            return existing, f"existing evidence {a:.4f} > incoming {b:.4f}"
        if incoming.provenance.authority > existing.provenance.authority:
            return incoming, "tie broken by provenance authority"
        return existing, "tie broken deterministically by existing memory id"


def cluster_key(memory: MemoryItem) -> tuple[str, str, tuple[str, ...]]:
    return memory.tenant_id, memory.kind.value, tuple(sorted(e.casefold() for e in memory.entities))
