from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
import copy
import threading
import time

from .models import Lifecycle, MemoryItem, MemoryEvent


@dataclass(frozen=True, slots=True)
class StoreStats:
    memories: int
    events: int
    tenants: int


class MemoryStore:
    """Thread-safe reference store representing the source-of-truth contract."""

    def __init__(self):
        self._memories: dict[str, MemoryItem] = {}
        self._dedup: dict[str, str] = {}
        self._events: list[MemoryEvent] = []
        self._lock = threading.RLock()

    def get(self, memory_id: str) -> MemoryItem | None:
        with self._lock:
            item = self._memories.get(memory_id)
            return copy.deepcopy(item) if item else None

    def put(self, memory: MemoryItem, *, allow_replace: bool = False) -> MemoryItem:
        with self._lock:
            existing_id = self._dedup.get(memory.dedup_key())
            if existing_id and existing_id != memory.id and not allow_replace:
                raise ValueError(f"duplicate memory content: {existing_id}")
            current = self._memories.get(memory.id)
            if current and not allow_replace:
                raise ValueError("memory already exists")
            self._memories[memory.id] = copy.deepcopy(memory)
            self._dedup[memory.dedup_key()] = memory.id
            return copy.deepcopy(memory)

    def transition(self, memory_id: str, lifecycle: Lifecycle, superseded_by_id: str | None = None) -> MemoryItem:
        with self._lock:
            current = self._memories[memory_id]
            allowed = {
                Lifecycle.CANDIDATE: {Lifecycle.ACTIVE, Lifecycle.DELETED, Lifecycle.SUPERSEDED},
                Lifecycle.ACTIVE: {Lifecycle.VERIFIED, Lifecycle.SUPERSEDED, Lifecycle.ARCHIVED, Lifecycle.DELETED},
                Lifecycle.VERIFIED: {Lifecycle.SUPERSEDED, Lifecycle.ARCHIVED, Lifecycle.DELETED},
                Lifecycle.SUPERSEDED: {Lifecycle.ARCHIVED, Lifecycle.DELETED},
                Lifecycle.ARCHIVED: {Lifecycle.ACTIVE, Lifecycle.DELETED},
                Lifecycle.DELETED: set(),
            }
            if lifecycle not in allowed[current.lifecycle]:
                raise ValueError(f"invalid lifecycle transition {current.lifecycle.value}->{lifecycle.value}")
            from dataclasses import replace
            metadata = (
                dict(current.metadata, superseded_by_id=superseded_by_id)
                if superseded_by_id else dict(current.metadata)
            )
            updated = replace(
                current,
                lifecycle=lifecycle,
                metadata=metadata,
            )
            self._memories[memory_id] = updated
            return copy.deepcopy(updated)

    def list_tenant(self, tenant_id: str) -> list[MemoryItem]:
        with self._lock:
            return [copy.deepcopy(x) for x in self._memories.values() if x.tenant_id == tenant_id]

    def append_event(self, event: MemoryEvent) -> None:
        with self._lock:
            if any(x.event_id == event.event_id for x in self._events):
                return
            self._events.append(event)

    def events(self) -> list[MemoryEvent]:
        with self._lock:
            return list(self._events)

    def stats(self) -> StoreStats:
        with self._lock:
            return StoreStats(
                memories=len(self._memories),
                events=len(self._events),
                tenants=len({m.tenant_id for m in self._memories.values()}),
            )


