"""
Tier-1 Resilience, Standardized Contracts & Caching Test Suite for Memora.
Validates:
- Liveness (/health) and Readiness (/ready) probes
- Standardized Uniform Error Envelopes
- TenantScopedCache LRU/TTL lifecycle
- AsyncCircuitBreaker state transitions & jittered retries
- Telemetry & metrics exports
- Startup environment validation
"""
import asyncio
import pytest
from fastapi.testclient import TestClient

from core.cache.memory_cache import TenantScopedCache
from core.resilience.circuit_breaker import (
    AsyncCircuitBreaker,
    CircuitOpenError,
    CircuitState,
    retry_with_backoff_async,
)
from core.config import validate_startup_config, settings



# ==============================================================================
# 1. Probes & Health Contracts
# ==============================================================================

def test_health_probe_contract(client: TestClient):
    """Liveness probe must return 200 with service metadata and healthy status."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["service"] == "memora"
    assert data["status"] in ["healthy", "ok"]
    assert data["version"] == "2.0.0"
    assert data["evidence_class"] == "process_liveness"

    head_res = client.head("/health")
    assert head_res.status_code == 200


def test_ready_probe_contract(client: TestClient):
    """Readiness probe must return 200 with database, wal, and circuit health."""
    res = client.get("/ready")
    assert res.status_code in [200, 503]
    data = res.json()
    assert data["service"] == "memora"
    assert data["version"] == "2.0.0"
    assert "database" in data
    assert "wal" in data
    assert "circuits" in data
    assert data["evidence_class"] == "readiness_probe"

    head_res = client.head("/ready")
    assert head_res.status_code in [200, 503]


def test_ready_probe_fails_on_degraded_db():
    """Readiness probe returns 503 if database check fails."""
    from apps.api.routers import health

    class BrokenDb:
        def execute(self, *args, **kwargs):
            raise RuntimeError("Database unreachable")

    # Call the router function directly with broken DB
    res = health.readiness_check(db=BrokenDb())
    assert res.status_code == 503
    import json
    body = json.loads(res.body)
    assert body["status"] == "unready"
    assert "degraded" in body["database"]


# ==============================================================================
# 2. Standardized Uniform Error Envelopes
# ==============================================================================

def test_error_envelope_on_404(client: TestClient):
    """Non-existent route returns standardized error envelope with detail preserved."""
    res = client.get("/non_existent_route_404")
    assert res.status_code == 404
    data = res.json()
    assert data["success"] is False
    assert data["error"] == "not_found"
    assert "detail" in data
    assert "message" in data


def test_error_envelope_on_validation_422(client: TestClient):
    """Malformed request payload returns 422 with validation_error code and field details."""
    res = client.post(
        "/agents",
        headers={"X-Agent-Name": "memora"},
        json={"invalid_field": 12345},  # missing required 'name' field
    )
    assert res.status_code == 422
    data = res.json()
    assert data["success"] is False
    assert data["error"] == "validation_error"
    assert "detail" in data
    assert "message" in data


# ==============================================================================
# 3. Telemetry & Metrics
# ==============================================================================

def test_metrics_telemetry_json(client: TestClient):
    """GET /v1/metrics returns JSON summary with uptime, memory, and SLI signals."""
    res = client.get("/v1/metrics")
    assert res.status_code == 200
    data = res.json()
    assert "uptime_seconds" in data
    assert data["uptime_seconds"] >= 0.0
    assert "memory_rss_mb" in data
    assert "write_success_rate" in data
    assert "latencies_ms" in data


def test_metrics_prometheus_and_negotiation(client: TestClient):
    """GET /metrics defaults to Prometheus format, but supports JSON negotiation."""
    # Default: text/plain Prometheus
    res_prom = client.get("/metrics")
    assert res_prom.status_code == 200
    assert "text/plain" in res_prom.headers.get("content-type", "")
    assert "memora_write_success_rate" in res_prom.text

    # Content negotiation via Accept header
    res_json = client.get("/metrics", headers={"Accept": "application/json"})
    assert res_json.status_code == 200
    assert "uptime_seconds" in res_json.json()

    # Content negotiation via query parameter
    res_query = client.get("/metrics?format=json")
    assert res_query.status_code == 200
    assert "uptime_seconds" in res_query.json()


# ==============================================================================
# 4. TenantScopedCache (LRU & TTL)
# ==============================================================================

def test_tenant_scoped_cache_isolation_and_lru():
    """Cache enforces tenant isolation and LRU eviction when capacity is exceeded."""
    cache = TenantScopedCache(max_capacity_per_tenant=3, default_ttl=60.0)

    # Set items for tenant A
    cache.set("tenant_a", "k1", "v1")
    cache.set("tenant_a", "k2", "v2")
    cache.set("tenant_a", "k3", "v3")

    # Set same key for tenant B
    cache.set("tenant_b", "k1", "v_tenant_b")

    # Isolation check
    assert cache.get("tenant_a", "k1") == "v1"
    assert cache.get("tenant_b", "k1") == "v_tenant_b"

    # LRU eviction in tenant A: access k1 and k2 so k3 is least recently used
    cache.get("tenant_a", "k1")
    cache.get("tenant_a", "k2")
    # Insert 4th item, exceeding capacity 3 -> k3 is evicted
    cache.set("tenant_a", "k4", "v4")

    assert cache.get("tenant_a", "k3") is None
    assert cache.get("tenant_a", "k4") == "v4"
    assert cache.get("tenant_a", "k1") == "v1"

    stats = cache.stats()
    assert stats["hits"] >= 3
    assert stats["evictions"] == 1
    assert stats["tenant_count"] == 2


def test_tenant_scoped_cache_ttl_expiration():
    """Cache items expire when TTL has elapsed."""
    cache = TenantScopedCache(max_capacity_per_tenant=10, default_ttl=0.05)
    cache.set("tenant_x", "temp_key", "temp_val")

    assert cache.get("tenant_x", "temp_key") == "temp_val"

    import time
    time.sleep(0.06)

    assert cache.get("tenant_x", "temp_key") is None
    assert cache.stats()["misses"] >= 1


# ==============================================================================
# 5. AsyncCircuitBreaker & Retries with Jitter
# ==============================================================================

@pytest.mark.asyncio
async def test_async_circuit_breaker_lifecycle():
    """AsyncCircuitBreaker transitions to OPEN after threshold failures and rejects calls."""
    breaker = AsyncCircuitBreaker("test_dep", failure_threshold=2, recovery_timeout=0.1)

    fail_count = 0

    async def flaky_call():
        nonlocal fail_count
        fail_count += 1
        raise ConnectionError("connection refused")

    # Attempt 1: failure 1
    with pytest.raises(ConnectionError):
        await breaker.call(flaky_call)
    assert breaker.state == CircuitState.CLOSED

    # Attempt 2: failure 2 -> opens
    with pytest.raises(ConnectionError):
        await breaker.call(flaky_call)
    assert breaker.state == CircuitState.OPEN

    # Attempt 3: rejected immediately without calling flaky_call
    with pytest.raises(CircuitOpenError) as exc_info:
        await breaker.call(flaky_call)
    assert "open" in str(exc_info.value)
    assert fail_count == 2  # Not called

    # Wait for recovery timeout
    await asyncio.sleep(0.12)

    # Recovery probe: now succeeds
    async def healthy_call():
        return "success"

    result = await breaker.call(healthy_call)
    assert result == "success"
    assert breaker.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_retry_with_backoff_async_and_fallback():
    """retry_with_backoff_async retries with backoff and invokes fallback on terminal failure."""
    attempts = 0

    async def failing_operation():
        nonlocal attempts
        attempts += 1
        raise TimeoutError("upstream timeout")

    async def fallback_operation():
        return "fallback_recovered"

    result = await retry_with_backoff_async(
        failing_operation,
        max_retries=2,
        initial_delay=0.01,
        max_delay=0.05,
        jitter=True,
        retry_exceptions=(TimeoutError,),
        fallback=fallback_operation,
    )

    assert result == "fallback_recovered"
    assert attempts == 3  # Initial try + 2 retries


# ==============================================================================
# 6. Startup Environment Validation
# ==============================================================================

def test_startup_validation_in_dev_and_prod(monkeypatch):
    """validate_startup_config allows development mode but fails fast in production if keys missing."""
    # Development: no exception
    monkeypatch.setenv("ENVIRONMENT", "development")
    validate_startup_config()

    # Production without key: raises RuntimeError pointing to .env.example
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("MEMORA_API_KEY", raising=False)
    monkeypatch.delenv("MEMORA_MASTER_KEY", raising=False)
    monkeypatch.setattr(settings, "MEMORA_API_KEY", None)
    monkeypatch.setattr(settings, "MEMORA_MASTER_KEY", None)

    with pytest.raises(RuntimeError) as exc:
        validate_startup_config()
    assert ".env.example" in str(exc.value)
