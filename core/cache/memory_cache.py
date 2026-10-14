"""
Thread-safe, tenant-isolated in-memory cache with TTL and LRU eviction.
Provides fast in-process caching for embeddings, search results, and agent contexts.
"""
from __future__ import annotations

import time
import threading
from typing import Any, Dict, Optional
from collections import OrderedDict


class CacheItem:
    __slots__ = ("value", "expires_at")

    def __init__(self, value: Any, expires_at: Optional[float] = None):
        self.value = value
        self.expires_at = expires_at

    def is_expired(self, now: float) -> bool:
        return self.expires_at is not None and now >= self.expires_at


class TenantScopedCache:
    """Thread-safe LRU cache with TTL expiration and tenant-level partitioning."""

    def __init__(self, max_capacity_per_tenant: int = 1000, default_ttl: float = 300.0):
        self.max_capacity = max_capacity_per_tenant
        self.default_ttl = default_ttl
        self._lock = threading.RLock()
        # Mapping: tenant_id -> OrderedDict[key, CacheItem]
        self._stores: Dict[str, OrderedDict[str, CacheItem]] = {}
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _get_tenant_store(self, tenant_id: str) -> OrderedDict[str, CacheItem]:
        if tenant_id not in self._stores:
            self._stores[tenant_id] = OrderedDict()
        return self._stores[tenant_id]

    def get(self, tenant_id: str, key: str) -> Optional[Any]:
        """Retrieve a value from the cache if present and not expired."""
        now = time.monotonic()
        with self._lock:
            store = self._stores.get(tenant_id)
            if store is None or key not in store:
                self._misses += 1
                return None

            item = store[key]
            if item.is_expired(now):
                del store[key]
                self._misses += 1
                return None

            # Move to end (most recently used)
            store.move_to_end(key)
            self._hits += 1
            return item.value

    def set(
        self,
        tenant_id: str,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
    ) -> None:
        """Store a value in the tenant's cache partition."""
        now = time.monotonic()
        effective_ttl = ttl if ttl is not None else self.default_ttl
        expires_at = (now + effective_ttl) if effective_ttl > 0 else None

        with self._lock:
            store = self._get_tenant_store(tenant_id)

            if key in store:
                store.move_to_end(key)
            elif len(store) >= self.max_capacity:
                # Evict least recently used (first item)
                store.popitem(last=False)
                self._evictions += 1

            store[key] = CacheItem(value, expires_at)

    def delete(self, tenant_id: str, key: str) -> bool:
        """Delete an item from a tenant's cache."""
        with self._lock:
            store = self._stores.get(tenant_id)
            if store is not None and key in store:
                del store[key]
                return True
            return False

    def clear(self, tenant_id: Optional[str] = None) -> None:
        """Clear cache for a specific tenant, or for all tenants if None."""
        with self._lock:
            if tenant_id is not None:
                if tenant_id in self._stores:
                    self._stores[tenant_id].clear()
            else:
                self._stores.clear()

    def stats(self) -> Dict[str, Any]:
        """Return cache telemetry and hit/miss statistics."""
        with self._lock:
            total_items = sum(len(s) for s in self._stores.values())
            tenant_count = len(self._stores)
            total_lookups = self._hits + self._misses
            hit_ratio = round((self._hits / total_lookups) if total_lookups > 0 else 0.0, 4)

            return {
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_ratio": hit_ratio,
                "total_items": total_items,
                "tenant_count": tenant_count,
            }


# Process-wide memory cache singleton
memory_cache = TenantScopedCache(max_capacity_per_tenant=2000, default_ttl=300.0)
