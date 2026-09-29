import json

import pytest

from apps.api.routers import health
from storage.vector.qdrant_adapter import QdrantVectorAdapter


class DatabaseProbe:
    def execute(self, _statement):
        return 1


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ("production", {"status": "unavailable", "available": False, "backend": "qdrant"}),
        ("development", {"status": "in_memory", "available": True, "backend": "process_local"}),
    ],
)
def test_vector_readiness_distinguishes_production_from_local_fallback(
    monkeypatch, environment, expected
):
    adapter = QdrantVectorAdapter(url="http://localhost:6333")
    monkeypatch.setattr(adapter, "is_production", lambda: environment == "production")

    assert adapter.readiness() == expected


@pytest.mark.parametrize(
    ("vector_status", "expected_status", "expected_http"),
    [
        ({"status": "connected", "available": True, "backend": "qdrant"}, "healthy", 200),
        ({"status": "unavailable", "available": False, "backend": "qdrant"}, "degraded", 503),
    ],
)
def test_health_reflects_vector_store_availability(
    monkeypatch, vector_status, expected_status, expected_http
):
    monkeypatch.setattr(health, "turso_events_configured", lambda: False)
    monkeypatch.setattr(health, "production_mode", lambda: False)
    monkeypatch.setattr(health.vector_adapter, "readiness", lambda: vector_status)

    response = health.health_check(db=DatabaseProbe())
    body = json.loads(response.body)

    assert response.status_code == expected_http
    assert body["status"] == expected_status
    assert body["vector_store"] == vector_status

