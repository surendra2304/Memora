from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from .models import MemoryItem, Lifecycle


@dataclass(frozen=True, slots=True)
class ForgettingPolicy:
    min_importance_floor: float = 0.05
    decay_per_30_days: float = 0.05
    unverified_after_days: float = 30.0
    archive_after_days: float = 90.0


class ForgettingEngine:
    def __init__(self, policy: ForgettingPolicy | None = None):
        self.policy = policy or ForgettingPolicy()

    def importance_after_decay(self, memory: MemoryItem, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        reference = memory.last_verified_at or memory.created_at
        reference = reference.astimezone(timezone.utc)
        days = max(0.0, (now - reference).total_seconds() / 86400)
        periods = days / 30.0
        decay = periods * self.policy.decay_per_30_days
        return max(self.policy.min_importance_floor, memory.importance - decay)

    def suggested_lifecycle(self, memory: MemoryItem, now: datetime | None = None) -> Lifecycle:
        now = now or datetime.now(timezone.utc)
        importance = self.importance_after_decay(memory, now)
        age_days = max(0.0, (now - memory.created_at.astimezone(timezone.utc)).total_seconds() / 86400)
        if memory.lifecycle == Lifecycle.DELETED:
            return Lifecycle.DELETED
        if importance <= self.policy.min_importance_floor and age_days >= self.policy.archive_after_days:
            return Lifecycle.ARCHIVED
        return memory.lifecycle
