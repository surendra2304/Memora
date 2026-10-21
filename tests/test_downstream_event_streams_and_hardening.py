from __future__ import annotations

import hashlib
import json
import time

from apps.api.routers.v1_events import IncomingEnvelope, ingest_envelope, poll_events
from core.events.emitter import EventEmitter
from core.identity.service import IdentityService
from core.memory.search_service import SearchService
from core.security.provenance import compute_memory_signature
from sdk.cloud_fallback import MemoraClient as CloudFallbackClient
from sdk.memora_client import MemoraClient
from storage.relational.models import MemoryRecord
from storage.vector.hybrid import hybrid_vector_relational_search


def test_poll_stream_sdk_client_constructs_correct_request(monkeypatch):
    """Verify that MemoraClient.poll_stream builds expected URL, headers, and params."""
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "agent": "cortex",
                "consumer_id": "test_consumer",
                "cursor": 42,
                "events": [{"id": 42, "event_type": "cortex.research", "payload": {"topic": "AI"}}],
                "has_more": False,
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("CORTEX_API_KEY", "cortex-mesh-key-12345678901234567890")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = MemoraClient(base_url="http://memora:8000")
    result = client.poll_stream(
        agent_name="cortex",
        consumer_id="test_consumer",
        auto_ack=True,
        event_type="cortex.research",
    )

    assert result["status"] == "ok"
    assert result["cursor"] == 42
    assert "consumer_id=test_consumer" in captured["url"]
    assert "auto_ack=true" in captured["url"]
    assert "event_type=cortex.research" in captured["url"]
    # When after_id is None, it should not be in the query string so server loads cursor
    assert "after_id=" not in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer cortex-mesh-key-12345678901234567890"


def test_cloud_fallback_client_poll_stream(monkeypatch):
    """Verify that CloudFallbackClient.poll_stream constructs expected request."""
    captured = {}

    def fake_request(agent_name, path, **kwargs):
        captured["agent"] = agent_name
        captured["path"] = path
        return {"status": "ok", "cloud": True, "events": []}

    client = CloudFallbackClient(base_url="http://memora:8000")
    monkeypatch.setattr(client, "_request", fake_request)

    res = client.poll_stream("vyralx", consumer_id="video-worker", auto_ack=True)
    assert res["status"] == "ok"
    assert captured["agent"] == "vyralx"
    assert "/v1/events/poll?" in captured["path"]
    assert "consumer_id=video-worker" in captured["path"]
    assert "auto_ack=true" in captured["path"]


def test_custom_mesh_envelope_payload_and_sanitization(test_db, monkeypatch):
    """Verify that agent mesh envelopes preserve custom payload fields while stripping forbidden keys."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MEMORA_TURSO_EVENTS_ENABLED", "false")
    monkeypatch.setenv("VYRALX_API_KEY", "vyralx-key-1234567890123456789012")

    envelope = IncomingEnvelope(
        message_id="env-vyralx-publish-1",
        correlation_id="corr-1",
        from_agent="vyralx",
        to_agent="cortex",
        intent="vyralx.custom_render",
        ttl=300,
        payload={
            "video_title": "Quantum Computing",
            "render_quality": "4k",
            "query": "DROP TABLE agents",  # forbidden internal key
            "auth_token": "secret_token",  # forbidden internal key
        },
        created_at=time.time(),
    )

    # Ingest envelope
    ingest_result = ingest_envelope(envelope, "Bearer vyralx-key-1234567890123456789012", test_db)
    assert ingest_result["status"] == "accepted"

    # Cortex polls for this event
    poll_result = poll_events(
        after_id=0,
        agent="cortex",
        consumer_id="default",
        db=test_db,
    )

    assert len(poll_result["events"]) == 1
    event = poll_result["events"][0]
    assert event["event_type"] == "vyralx.custom_render"
    # Allowed custom fields preserved
    assert event["payload"]["video_title"] == "Quantum Computing"
    assert event["payload"]["render_quality"] == "4k"
    assert event["payload"]["source_agent"] == "vyralx"
    # Forbidden internal keys stripped
    assert "query" not in event["payload"]
    assert "auth_token" not in event["payload"]


def test_independent_cursor_progression_across_downstream_agents(test_db):
    """Verify that multiple downstream agents (Cortex, Sentinel, Futuris) have independent monotonic cursors."""
    emitter = EventEmitter()
    # Publish 3 universal events
    _ = emitter.publish("memory.created", {"memory_id": "m1", "tenant_id": "default", "owner": "friday", "type": "episodic"}, db=test_db)
    e2 = emitter.publish("memory.created", {"memory_id": "m2", "tenant_id": "default", "owner": "friday", "type": "episodic"}, db=test_db)
    e3 = emitter.publish("memory.created", {"memory_id": "m3", "tenant_id": "default", "owner": "friday", "type": "episodic"}, db=test_db)
    test_db.commit()

    # 1. Cortex polls and auto-acknowledges first 2
    res_cortex = poll_events(agent="cortex", consumer_id="stream-1", limit=2, auto_ack=True, db=test_db)
    assert len(res_cortex["events"]) == 2
    assert res_cortex["cursor"] == e2.cursor

    # 2. Sentinel polls with auto_ack=False
    res_sentinel = poll_events(agent="sentinel", consumer_id="stream-1", limit=3, auto_ack=False, db=test_db)
    assert len(res_sentinel["events"]) == 3
    # Sentinel cursor should still be 0 since auto_ack was False
    assert res_sentinel["cursor"] == 0

    # 3. Cortex polls again with no after_id -> resumes from e2.cursor and gets e3
    res_cortex_next = poll_events(agent="cortex", consumer_id="stream-1", limit=10, auto_ack=True, db=test_db)
    assert len(res_cortex_next["events"]) == 1
    assert res_cortex_next["events"][0]["id"] == e3.cursor
    assert res_cortex_next["cursor"] == e3.cursor

    # 4. Verify Sentinel can still poll from 0
    res_sentinel_again = poll_events(agent="sentinel", consumer_id="stream-1", limit=10, auto_ack=True, db=test_db)
    assert len(res_sentinel_again["events"]) == 3
    assert res_sentinel_again["cursor"] == e3.cursor


def test_tampered_records_discarded_from_search_service(test_db):
    """Verify that direct DB tampering on a signed memory record is rejected by SearchService."""
    actor = IdentityService.register_agent(test_db, name="forge", tenant_id="default")
    ns = IdentityService.resolve_namespace(test_db, "memora://forge/private", owner_agent_id=actor.id, tenant_id="default")

    content = "Kubernetes deployment configurations"
    c_hash = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()

    record = MemoryRecord(
        id="mem_forge_sig_1",
        tenant_id="default",
        owner_id=actor.id,
        agent_id=actor.name,
        namespace_id=ns.id,
        memory_type="semantic",
        content_text=content,
        content_hash=c_hash,
        source="agent:forge",
        confidence=1.0,
        lifecycle_state="active",
        provenance={"category": "infra"},
    )
    sig = compute_memory_signature(
        {
            "id": record.id,
            "tenant_id": "default",
            "owner_id": actor.id,
            "content_hash": c_hash,
            "memory_type": "semantic",
        }
    )
    record.provenance = {
        "category": "infra",
        "hmac_signature": sig,
        "signature_version": "v1",
    }
    test_db.add(record)
    test_db.commit()

    # Search with forge actor should return the record
    results = SearchService.hybrid_search(
        test_db,
        query_text="Kubernetes deployment",
        actor_name="forge",
        tenant_id="default",
    )
    assert len(results) == 1
    assert results[0].record.id == "mem_forge_sig_1"

    # TAMPER with the record directly in the DB: change content_text without recalculating signature
    record.content_text = "Tampered malicious backdoor injection"
    test_db.commit()

    # Search again: tampered record MUST be discarded
    tampered_results = SearchService.hybrid_search(
        test_db,
        query_text="malicious backdoor",
        actor_name="forge",
        tenant_id="default",
    )
    assert len(tampered_results) == 0


def test_tampered_records_discarded_from_hybrid_search(test_db):
    """Verify that direct DB tampering is rejected in hybrid_vector_relational_search."""
    actor = IdentityService.register_agent(test_db, name="sentinel", tenant_id="default")
    ns = IdentityService.resolve_namespace(test_db, "memora://sentinel/private", owner_agent_id=actor.id, tenant_id="default")

    content = "Firewall security policies and egress filters"
    c_hash = hashlib.sha256(content.strip().encode("utf-8")).hexdigest()

    record = MemoryRecord(
        id="mem_sentinel_sig_1",
        tenant_id="default",
        owner_id=actor.id,
        agent_id=actor.name,
        namespace_id=ns.id,
        memory_type="semantic",
        content_text=content,
        content_hash=c_hash,
        source="agent:sentinel",
        confidence=1.0,
        lifecycle_state="active",
        provenance={"category": "security"},
    )
    sig = compute_memory_signature(
        {
            "id": record.id,
            "tenant_id": "default",
            "owner_id": actor.id,
            "content_hash": c_hash,
            "memory_type": "semantic",
        }
    )
    record.provenance = {
        "category": "security",
        "hmac_signature": sig,
        "signature_version": "v1",
    }
    test_db.add(record)
    test_db.commit()

    # Valid search
    res = hybrid_vector_relational_search(test_db, "Firewall security", tenant_id="default")
    assert len(res) == 1
    assert res[0]["id"] == "mem_sentinel_sig_1"

    # TAMPER directly: modify content_text
    record.content_text = "Corrupted policy payload"
    test_db.commit()

    # Search should now exclude the tampered record
    tampered_res = hybrid_vector_relational_search(test_db, "Corrupted policy", tenant_id="default")
    assert len(tampered_res) == 0
