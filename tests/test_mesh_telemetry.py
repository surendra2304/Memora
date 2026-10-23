"""
Tests for Memora's Mesh Circuit Breaker & Health Telemetry Reporter.
Verifies telemetry snapshot generation, HMAC signing, and resilience reporting.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from apps.api.main import app
from core.resilience.telemetry import mesh_telemetry_reporter


client = TestClient(app)


def test_telemetry_snapshot_structure():
    snapshot = mesh_telemetry_reporter.get_telemetry_snapshot()
    assert snapshot["agent"] == "memora"
    assert "timestamp" in snapshot
    assert snapshot["health_grade"] in {"HEALTHY", "DEGRADED"}
    assert "circuit_count" in snapshot
    assert "circuits" in snapshot
    assert "embedder_mode" in snapshot


def test_telemetry_signing():
    data = {"agent": "memora", "status": "healthy", "score": 1.0}
    sig = mesh_telemetry_reporter._sign_payload(data, "test_secret_key_1234567890")
    assert len(sig) == 64
    # Re-computing with same key gives exact match
    assert sig == mesh_telemetry_reporter._sign_payload(data, "test_secret_key_1234567890")


def test_telemetry_dispatch_handles_network_gracefully():
    # Simulate timeout when Sentinel or FRIDAY are offline
    with patch("requests.post", side_effect=Exception("Connection refused")):
        res = mesh_telemetry_reporter.report_mesh_telemetry()
        assert "unreachable" in res["sentinel"]["sentinel_status"]
        assert "unreachable" in res["friday"]["friday_status"]


def test_telemetry_api_endpoints():
    # 1. GET /v1/resilience/telemetry
    resp_get = client.get("/v1/resilience/telemetry", headers={"X-Agent-Name": "friday"})
    assert resp_get.status_code == 200
    body = resp_get.json()
    assert body["agent"] == "memora"
    assert "health_grade" in body

    # 2. POST /v1/resilience/telemetry/report
    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp

        resp_post = client.post("/v1/resilience/telemetry/report", headers={"X-Agent-Name": "memora"})
        assert resp_post.status_code == 200
        post_body = resp_post.json()
        assert post_body["sentinel"]["sentinel_status"] == "delivered"
        assert post_body["friday"]["friday_status"] == "delivered"
