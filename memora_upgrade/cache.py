from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import hashlib
import threading
import time


@dataclass(slots=True)
class CacheEntry:
    value: Any
    expires_at: float


class TenantScopedCache:
    def __init__(self, ttl_seconds: float = 60.0, max_entries: int = 10000):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._items: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()

    def key(self, tenant_id: str, actor_agent_id: str, query: str, policy_version: str = "1") -> str:
        raw = f"{tenant_id}|{actor_agent_id}|{policy_version}|{query}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._items.get(key)
            if not entry:
                return None
            if time.monotonic() >= entry.expires_at:
                self._items.pop(key, None)
                return None
            return entry.value

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._items) >= self.max_entries:
                oldest = min(self._items, key=lambda k: self._items[k].expires_at)
                self._items.pop(oldest, None)
            self._items[key] = CacheEntry(value, time.monotonic() + self.ttl)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
