from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
from typing import Any


@dataclass(frozen=True, slots=True)
class RedisCachePolicy:
    ttl_seconds: int = 60
    include_policy_version: bool = True


def scoped_cache_key(tenant_id: str, actor_id: str, query: str, policy_version: str = "1") -> str:
    raw = f"{tenant_id}|{actor_id}|{policy_version}|{query}"
    return "memora:" + hashlib.sha256(raw.encode()).hexdigest()


class RedisMemoryCache:
    def __init__(self, redis_client: Any, policy: RedisCachePolicy | None = None):
        self.redis = redis_client
        self.policy = policy or RedisCachePolicy()

    def get(self, tenant_id: str, actor_id: str, query: str, policy_version: str = "1"):
        key = scoped_cache_key(tenant_id, actor_id, query, policy_version)
        raw = self.redis.get(key)
        return json.loads(raw) if raw else None

    def put(self, tenant_id: str, actor_id: str, query: str, value: Any, policy_version: str = "1"):
        key = scoped_cache_key(tenant_id, actor_id, query, policy_version)
        self.redis.setex(key, self.policy.ttl_seconds, json.dumps(value, ensure_ascii=False))
