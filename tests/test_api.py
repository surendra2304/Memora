"""
Integration Tests for FastAPI Endpoints
"""
import pytest
from fastapi.testclient import TestClient
from storage.relational.models import MemoryType, LifecycleState

def test_health_check(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "memora-api"
    assert data["status"] in ["healthy", "degraded"]

def test_agent_registration_and_list(client: TestClient):
    response = client.post("/agents", json={"name": "futuris", "description": "Predictive Forecasting"})
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "futuris"

    list_resp = client.get("/agents")
    assert list_resp.status_code == 200
    names = [a["name"] for a in list_resp.json()]
    assert "futuris" in names

def test_memory_ingest_query_and_lifecycle(client: TestClient):
    # Ingest memory
    ingest_payload = {
        "owner_name": "intelx",
        "namespace_path": "memora://intelx/private",
        "memory_type": "semantic",
        "content_text": "Deep research findings regarding transformer attention latency.",
        "source": "intelx_crawler",
        "confidence": 0.95,
        "importance": 0.90,
        "lifecycle_state": "active"
    }
    create_resp = client.post("/memories", json=ingest_payload, headers={"X-Agent-Name": "intelx"})
    assert create_resp.status_code == 201
    mem_data = create_resp.json()
    mem_id = mem_data["id"]
    assert mem_data["memory_type"] == "semantic"
    assert mem_data["lifecycle_state"] == "active"

    # Query memories
    query_resp = client.post("/memories/query", json={"query_text": "transformer", "owner_name": "intelx"}, headers={"X-Agent-Name": "intelx"})
    assert query_resp.status_code == 200
    results = query_resp.json()
    assert len(results) >= 1
    assert results[0]["id"] == mem_id

    # Transition lifecycle to verified
    trans_resp = client.post(f"/memories/{mem_id}/transition", json={"target_state": "verified"}, headers={"X-Agent-Name": "intelx"})
    assert trans_resp.status_code == 200
    assert trans_resp.json()["lifecycle_state"] == "verified"

    # Audit check
    audit_resp = client.get("/audit", params={"memory_id": mem_id})
    assert audit_resp.status_code == 200
    audit_logs = audit_resp.json()
    assert len(audit_logs) >= 2

def test_namespaces_api_and_grants(client: TestClient):
    """
    Test /namespaces POST, GET, /namespaces/grants, and /namespaces/grants DELETE.
    """
    # Create namespace
    create_resp = client.post("/namespaces", json={"path": "memora://shared/team-ops", "type": "team-shared"})
    assert create_resp.status_code == 201
    ns_data = create_resp.json()
    assert ns_data["path"] == "memora://shared/team-ops"
    assert ns_data["type"] == "team-shared"

    # List namespaces
    list_resp = client.get("/namespaces")
    assert list_resp.status_code == 200
    paths = [n["path"] for n in list_resp.json()]
    assert "memora://shared/team-ops" in paths

    # Grant access
    grant_resp = client.post("/namespaces/grants", json={
        "agent_name": "forge",
        "namespace_path": "memora://shared/team-ops",
        "actions": ["read", "write"]
    })
    assert grant_resp.status_code == 201
    grant_data = grant_resp.json()
    assert "read" in grant_data["actions"]
    assert "write" in grant_data["actions"]

    # Revoke access
    revoke_resp = client.delete(f"/namespaces/grants?agent_id={grant_data['agent_id']}&namespace_id={grant_data['namespace_id']}")
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["status"] == "revoked"

def test_api_404_not_found_handling(client: TestClient):
    """
    Test 404 error responses on non-existent memory and agent lookups.
    """
    resp_mem = client.get("/v1/memories/non-existent-memory-id-9999", headers={"X-Agent-Name": "friday"})
    assert resp_mem.status_code == 404
    assert "not found" in resp_mem.json()["detail"].lower()

    resp_agent = client.get("/agents/non_existent_agent_9999")
    assert resp_agent.status_code == 404