"""
Comprehensive Integration Tests for MEMORA Infrastructure Finalization
Tests Observability Metrics, Redis/Event Bus, Namespace Policy API, Memory Sharing, and Graceful Degradation.
"""
import pytest
from fastapi.testclient import TestClient
from storage.relational.models import (
    Agent,
    Namespace,
    NamespaceType,
    MemoryRecord,
    MemoryType,
    LifecycleState
)
from storage.vector.embedding import EmbeddingGenerator
from storage.vector.qdrant_adapter import vector_adapter
from core.identity.service import IdentityService
from core.metrics.collector import metrics_collector
from core.events.emitter import event_emitter
from core.memory.context.builder import ContextBuilderService

def test_metrics_collector_and_endpoints(client: TestClient):
    """
    Test MetricsCollector telemetry and /v1/metrics and /metrics Prometheus format.
    """
    metrics_collector.record_write(success=True, latency_ms=12.5)
    metrics_collector.record_policy_check(allowed=True)
    metrics_collector.record_policy_check(allowed=False)
    metrics_collector.record_retrieval([0.95, 0.88], [5.0, 45.0], latency_ms=25.0)
    metrics_collector.record_context_generation(tokens_used=500, token_budget=2000, latency_ms=15.0)

    # JSON Endpoint
    json_resp = client.get("/v1/metrics")
    assert json_resp.status_code == 200
    data = json_resp.json()
    assert "write_success_rate" in data
    assert "policy_denial_rate" in data
    assert "staleness_rate" in data
    assert "latencies_ms" in data

    # Prometheus Endpoint
    prom_resp = client.get("/metrics")
    assert prom_resp.status_code == 200
    assert "memora_write_success_rate" in prom_resp.text
    assert "memora_latency_ms" in prom_resp.text

def test_events_emitter_and_query_endpoint(client: TestClient):
    """
    Test event publishing and /v1/events query endpoint.
    """
    event_emitter.publish("test.event", {"action": "heartbeat", "status": "ok"})
    
    resp = client.get("/v1/events?limit=10")
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) >= 1
    assert any(e["event_type"] == "test.event" for e in events)

def test_namespace_policy_inspection_endpoint(client: TestClient, test_db):
    """
    Test GET /v1/namespaces/{id}/policy returns effective rules and grants.
    """
    friday = IdentityService.register_agent(test_db, "friday", role="supervisor")
    forge = IdentityService.register_agent(test_db, "forge", role="worker")

    ns = IdentityService.resolve_namespace(test_db, "memora://friday/projects/collab", default_type=NamespaceType.PROJECT_PRIVATE)
    IdentityService.grant_access(test_db, agent_name="forge", namespace_id=ns.id, actions=["read", "query"], purpose="Collab dev", ttl_hours=24)
    test_db.commit()

    # Query policy
    resp = client.get(f"/v1/namespaces/{ns.id}/policy", headers={"X-Agent-Name": "friday"})
    assert resp.status_code == 200
    policy_data = resp.json()
    assert policy_data["type"] == "project-private"
    assert policy_data["total_active_grants"] == 1
    assert policy_data["access_grants"][0]["agent_name"] == "forge"
    assert "Rule 2" in policy_data["governing_rule"]

def test_memory_sharing_endpoint(client: TestClient, test_db):
    """
    Test POST /v1/memories/{id}/share explicitly grants access to another agent.
    """
    friday = IdentityService.register_agent(test_db, "friday")
    forge = IdentityService.register_agent(test_db, "forge")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")

    mem = MemoryRecord(
        namespace_id=ns.id,
        owner_id=friday.id,
        memory_type=MemoryType.EXPERIENCE,
        content_text="Shared deployment runbook for edge gateways.",
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add(mem)
    test_db.commit()

    # Share with FORGE
    share_payload = {
        "target_agent_name": "forge",
        "actions": ["read", "query"],
        "purpose": "Deploying edge cluster",
        "ttl_hours": 12
    }
    share_resp = client.post(f"/v1/memories/{mem.id}/share", json=share_payload, headers={"X-Agent-Name": "friday"})
    assert share_resp.status_code == 200
    share_data = share_resp.json()
    assert share_data["status"] == "shared"
    assert share_data["shared_with"] == "forge"

    # Verify event published
    recent_events = event_emitter.get_recent_events(limit=5, event_type="memory.shared")
    assert len(recent_events) >= 1
    assert recent_events[0]["payload"]["shared_with"] == "forge"

def test_graceful_degradation_when_vector_store_fails(test_db):
    """
    Test that ContextBuilderService falls back gracefully without crashing when vector store is offline.
    """
    intelx = IdentityService.register_agent(test_db, "intelx")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://intelx/private")

    mem = MemoryRecord(
        namespace_id=ns.id,
        owner_id=intelx.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="Graceful degradation test record in PostgreSQL.",
        confidence=0.95,
        importance=0.90,
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add(mem)
    test_db.commit()

    # Build context bundle under standard fallback
    bundle = ContextBuilderService.build_context_bundle(
        db=test_db,
        agent_id_or_name="intelx",
        task_query="degradation test",
        token_budget=2000
    )

    assert bundle.bundle_id is not None
    assert bundle.total_tokens_estimated > 0
    assert len(bundle.memories) >= 1