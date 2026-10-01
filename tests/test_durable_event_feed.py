import hashlib
import hmac
import json
import os
import time

import pytest
from fastapi import HTTPException

from apps.api.dependencies import authenticate_agent
from apps.api.routers.v1_events import (
    EventAcknowledgement,
    IncomingEnvelope,
    acknowledge_event,
    ingest_envelope,
    read_consumer_cursor,
    read_events,
)
from core.events.emitter import EventEmitter
from sdk.cloud_fallback import MemoraClient as CloudFallbackClient
from sdk.memora_client import MemoraClient
from storage.relational.models import EventConsumerCursor, EventLog


def test_event_uses_callers_transaction_and_replays_after_commit(test_db):
    emitter = EventEmitter()
    event = emitter.publish(
        "memory.created",
        {"memory_id": "m-1", "tenant_id": "default", "owner": "friday", "namespace": "memora://friday/private", "type": "episodic"},
        db=test_db,
    )
    assert event.cursor is not None
    test_db.commit()

    response = read_events(after_id=0, limit=10, event_type=None, agent="friday", db=test_db)
    assert response["events"][0]["event_id"] == event.event_id
    assert response["next_after_id"] == event.cursor


def test_rolled_back_memory_transaction_does_not_leave_event(test_db):
    emitter = EventEmitter()
    event = emitter.publish("memory.created", {"memory_id": "m-rollback"}, db=test_db)
    test_db.rollback()
    assert test_db.query(EventLog).filter_by(event_id=event.event_id).first() is None


def test_startup_cloud_import_requires_production_or_explicit_opt_in(monkeypatch):
    from apps.api.main import _startup_turso_sync_enabled

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("MEMORA_SYNC_TURSO_ON_STARTUP", raising=False)
    assert _startup_turso_sync_enabled() is False
    monkeypatch.setenv("MEMORA_SYNC_TURSO_ON_STARTUP", "true")
    assert _startup_turso_sync_enabled() is True
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("MEMORA_SYNC_TURSO_ON_STARTUP", raising=False)
    assert _startup_turso_sync_enabled() is True


def test_feed_sanitizes_private_context_payload_and_obeys_cursor_and_tenant(test_db):
    test_db.add_all([
        EventLog(event_id="one", event_type="context.generated", tenant_id="default", payload={"query": "private prompt", "bundle_id": "b-1", "agent": "friday", "memories_count": 2}),
        EventLog(event_id="two", event_type="memory.created", tenant_id="other", payload={"memory_id": "secret"}),
    ])
    test_db.commit()
    response = read_events(after_id=0, limit=10, event_type=None, agent="friday", db=test_db)
    assert len(response["events"]) == 1
    assert response["events"][0]["payload"] == {"bundle_id": "b-1", "agent": "friday", "memories_count": 2}
    next_page = read_events(after_id=response["next_after_id"], limit=10, event_type=None, agent="friday", db=test_db)
    assert next_page["events"] == []


def test_live_event_route_requires_agent_authentication(client, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRIDAY_API_KEY", "friday-test-key")
    monkeypatch.setattr("apps.api.routers.v1_events.turso_events_configured", lambda: False)
    monkeypatch.setattr("apps.api.routers.v1_events.turso_required", lambda: False)

    response = client.get("/v1/events?after_id=0&limit=1")

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid agent credentials"


def test_live_event_route_returns_authenticated_allowlisted_envelope(client, test_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRIDAY_API_KEY", "friday-test-key")
    monkeypatch.setattr("apps.api.routers.v1_events.turso_events_configured", lambda: False)
    monkeypatch.setattr("apps.api.routers.v1_events.turso_required", lambda: False)
    test_db.add(EventLog(
        event_id="context-private-data",
        event_type="context.generated",
        tenant_id="default",
        payload={
            "bundle_id": "bundle-1",
            "agent": "friday",
            "memories_count": 2,
            "is_degraded": False,
            "query": "private user prompt",
            "memory_ids": ["secret-memory-id"],
            "internal_memory_metadata": {"tenant_id": "other", "owner": "private"},
        },
    ))
    test_db.commit()

    response = client.get(
        "/v1/events?after_id=0&limit=1",
        headers={"X-Agent-Name": "friday", "X-API-Key": "friday-test-key"},
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"agent", "events", "next_after_id", "has_more"}
    assert body["agent"] == "friday"
    assert len(body["events"]) == 1
    assert set(body["events"][0]) == {
        "id", "event_id", "event_type", "tenant_id", "created_at", "payload"
    }
    assert body["events"][0]["payload"] == {
        "bundle_id": "bundle-1",
        "agent": "friday",
        "memories_count": 2,
        "is_degraded": False,
    }
    assert "memory_ids" not in body["events"][0]["payload"]
    assert "internal_memory_metadata" not in body["events"][0]["payload"]


def test_broadcast_news_event_is_visible_to_independent_agent_reads(test_db):
    test_db.add(EventLog(
        event_id="intelx-broadcast-1",
        event_type="intelx.news",
        tenant_id="default",
        target_agent=None,
        payload={"headline": "Verified notice", "summary": "Relevant finding", "private_prompt": "do not expose"},
    ))
    test_db.commit()
    friday_view = read_events(after_id=0, limit=10, event_type="intelx.news", agent="friday", db=test_db)
    stratex_view = read_events(after_id=0, limit=10, event_type="intelx.news", agent="stratex", db=test_db)
    assert friday_view["events"][0]["event_id"] == "intelx-broadcast-1"
    assert stratex_view["events"][0]["event_id"] == "intelx-broadcast-1"
    assert friday_view["events"][0]["payload"] == {"headline": "Verified notice", "summary": "Relevant finding"}


def test_agent_cursors_advance_only_after_visible_event_ack(test_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MEMORA_TURSO_EVENTS_ENABLED", "false")
    event = EventLog(
        event_id="cursor-event-1", event_type="intelx.news", tenant_id="default",
        target_agent="futuris", payload={"headline": "Forecast input"},
    )
    hidden = EventLog(
        event_id="cursor-event-2", event_type="intelx.news", tenant_id="default",
        target_agent="stratex", payload={"headline": "Not for Futuris"},
    )
    later = EventLog(
        event_id="cursor-event-3", event_type="intelx.news", tenant_id="default",
        target_agent="futuris", payload={"headline": "Later forecast input"},
    )
    test_db.add_all([event, hidden, later])
    test_db.commit()

    assert read_consumer_cursor(agent="futuris", db=test_db)["after_id"] == 0
    assert read_events(after_id=0, limit=10, event_type=None, agent="futuris", db=test_db)["events"]
    assert read_consumer_cursor(agent="futuris", db=test_db)["after_id"] == 0
    with pytest.raises(HTTPException) as exc:
        acknowledge_event(EventAcknowledgement(event_id=hidden.id), agent="futuris", db=test_db)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException) as exc:
        acknowledge_event(EventAcknowledgement(event_id=later.id), agent="futuris", db=test_db)
    assert exc.value.status_code == 409

    result = acknowledge_event(EventAcknowledgement(event_id=event.id, consumer_id="cloud-supervisor"), agent="futuris", db=test_db)
    assert result["after_id"] == event.id
    assert read_consumer_cursor(agent="stratex", db=test_db)["after_id"] == 0
    assert read_consumer_cursor(agent="futuris", consumer_id="local-companion", db=test_db)["after_id"] == 0
    assert read_consumer_cursor(agent="futuris", consumer_id="cloud-supervisor", db=test_db)["after_id"] == event.id
    assert acknowledge_event(EventAcknowledgement(event_id=later.id, consumer_id="cloud-supervisor"), agent="futuris", db=test_db)["after_id"] == later.id
    cursor = test_db.query(EventConsumerCursor).filter_by(agent="futuris", consumer_id="cloud-supervisor").one()
    assert cursor.last_event_id == later.id


def test_event_ack_client_uses_named_agent_credential(monkeypatch):
    calls = []

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self):
            return b'{"status":"acknowledged","agent":"friday","after_id":17}'

    def fake_urlopen(request, timeout):
        calls.append(request)
        return Response()

    monkeypatch.setenv("FRIDAY_API_KEY", "friday-agent-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = MemoraClient(base_url="https://memora.invalid")
    assert client.read_event_cursor("friday", consumer_id="local-companion")["after_id"] == 17
    assert client.acknowledge_event("friday", 17, consumer_id="cloud-supervisor")["after_id"] == 17
    assert calls[0].full_url.endswith("/v1/events/cursor?consumer_id=local-companion")
    assert calls[1].full_url == "https://memora.invalid/v1/events/ack"
    assert calls[1].get_header("Authorization") == "Bearer friday-agent-key"
    assert json.loads(calls[1].data) == {"event_id": 17, "consumer_id": "cloud-supervisor"}


def test_agent_authentication_requires_matching_named_key(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRIDAY_API_KEY", "test-key")
    assert authenticate_agent("friday", "test-key", None) == "friday"
    with pytest.raises(HTTPException) as exc:
        authenticate_agent("inference", "test-key", None)
    assert exc.value.status_code == 401
    with pytest.raises(HTTPException):
        authenticate_agent("friday", None, None)


def test_auth_accepts_bearer_header(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("INTELX_API_KEY", "intelx-key")
    assert authenticate_agent("intelx", None, "Bearer intelx-key") == "intelx"


def test_actor_identity_cannot_be_spoofed_with_another_agents_header(monkeypatch):
    from apps.api.dependencies import get_actor_header

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRIDAY_API_KEY", "friday-key")
    monkeypatch.setenv("SENTINEL_API_KEY", "sentinel-key")
    assert get_actor_header("friday", None, "Bearer friday-key") == "friday"
    with pytest.raises(HTTPException) as exc:
        get_actor_header("sentinel", None, "Bearer friday-key")
    assert exc.value.status_code == 401


def test_sdk_polls_feed_with_agent_credential(monkeypatch):
    captured = {}

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self):
            return json.dumps({"events": [], "next_after_id": 4, "has_more": False}).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = request.headers
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setenv("FRIDAY_API_KEY", "friday-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = MemoraClient(base_url="https://memora.invalid", api_key="wrong-shared-key").poll_events("friday", after_id=3)
    assert result["status"] == "ok"
    assert "after_id=3" in captured["url"]
    assert captured["headers"]["Authorization"] == "Bearer friday-key"


def test_sdk_reports_missing_agent_credential(monkeypatch):
    monkeypatch.delenv("SENTINEL_API_KEY", raising=False)
    result = MemoraClient(base_url="https://memora.invalid", api_key="memora-key").poll_events("sentinel")
    assert result["status"] == "error"
    assert "SENTINEL_API_KEY" in result["error"]


def test_sdk_agent_headers_never_impersonate_with_memora_service_key(monkeypatch):
    monkeypatch.delenv("FRIDAY_API_KEY", raising=False)
    client = MemoraClient(base_url="https://memora.invalid", api_key="memora-service-key")
    assert "Authorization" not in client._headers("friday")


def test_cloud_fallback_client_uses_named_agent_key_without_sqlite(monkeypatch):
    captured = {}

    class Response:
        status = 201
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self):
            return b'{"status":"success","id":"cloud-memory-1"}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = request.headers
        return Response()

    monkeypatch.setenv("CORTEX_API_KEY", "cortex-test-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = CloudFallbackClient(base_url="https://memora.invalid")
    result = client.record_fact("cortex", "Cloud memory", category="decision")

    assert result["cloud"] is True
    assert captured["url"] == "https://memora.invalid/v1/memories"
    assert captured["headers"]["Authorization"] == "Bearer cortex-test-key"
    assert "local_db_path" not in client.__dict__


def test_sdk_ordinary_memory_routes_are_agent_authenticated():
    from apps.api.routers.v1_memories import router as memories_router

    assert any(dependency.dependency is authenticate_agent for dependency in memories_router.dependencies)


def test_sdk_publishes_signed_envelope(monkeypatch):
    captured = {}

    class Response:
        status = 202
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self):
            return b'{"status":"accepted","event_id":"msg-1","cursor":9}'

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = request.headers
        captured["envelope"] = json.loads(request.data.decode())
        return Response()

    monkeypatch.setenv("INTELX_API_KEY", "intelx-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    result = MemoraClient(base_url="https://memora.invalid").publish_event(
        "intelx", "futuris", "intelx.news", {"headline": "Market notice"}, message_id="msg-1"
    )
    assert result["status"] == "accepted"
    assert captured["url"].endswith("/mesh/envelope")
    assert captured["headers"]["Authorization"] == "Bearer intelx-key"
    envelope = IncomingEnvelope(**captured["envelope"])
    assert envelope.signature


def _signed_envelope(key, *, message_id="intelx-news-1", to_agent="futuris", payload=None):
    fields = {
        "message_id": message_id,
        "correlation_id": "corr-1",
        "from_agent": "intelx",
        "to_agent": to_agent,
        "intent": "intelx.news",
        "priority": "high",
        "ttl": 300,
        "auth_token": None,
        "payload": payload or {"headline": "Market notice", "source_url": "https://example.invalid/news"},
        "created_at": time.time(),
    }
    raw = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode()
    signature = hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
    return IncomingEnvelope(**fields, signature=signature)


def test_signed_intelx_event_is_durable_targeted_and_idempotent(test_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MEMORA_TURSO_EVENTS_ENABLED", "false")
    monkeypatch.setenv("INTELX_API_KEY", "intelx-test-key")
    env = _signed_envelope("intelx-test-key")
    first = ingest_envelope(env, "Bearer intelx-test-key", test_db)
    second = ingest_envelope(env, "Bearer intelx-test-key", test_db)
    assert first["status"] == "accepted"
    assert second["status"] == "duplicate"

    friday = read_events(after_id=0, limit=10, event_type=None, agent="friday", db=test_db)
    futuris = read_events(after_id=0, limit=10, event_type="intelx.news", agent="futuris", db=test_db)
    assert friday["events"] == []
    assert len(futuris["events"]) == 1
    assert futuris["events"][0]["payload"]["source_agent"] == "intelx"


def _signed_forecast_envelope(key, *, sender="futuris", payload=None):
    fields = {
        "message_id": "futuris-forecast-1",
        "correlation_id": "forecast-correlation-1",
        "from_agent": sender,
        "to_agent": "all",
        "intent": "futuris.forecast",
        "priority": "normal",
        "ttl": 300,
        "auth_token": None,
        "payload": payload or {
            "forecast_id": "forecast-1",
            "target": "BTC volatility over 24h",
            "status": "active",
            "as_of": "2026-09-29T10:00:00Z",
            "expires_at": "2026-09-30T10:00:00Z",
            "model_version": "futuris-test-model",
            "prediction": 0.5,
            "range_lower": 0.2,
            "range_upper": 0.8,
            "probability": 0.7,
            "confidence": 0.6,
            "prediction_is_not_authorization": True,
            "private_prompt": "must not be exposed",
        },
        "created_at": time.time(),
    }
    raw = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode()
    signature = hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
    return IncomingEnvelope(**fields, signature=signature)


def test_signed_futuris_forecast_is_visible_with_only_advisory_fields(test_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MEMORA_TURSO_EVENTS_ENABLED", "false")
    monkeypatch.setenv("FUTURIS_API_KEY", "futuris-test-key")
    monkeypatch.setattr("apps.api.routers.v1_events.turso_events_configured", lambda: False)
    monkeypatch.setattr("apps.api.routers.v1_events.turso_required", lambda: False)

    result = ingest_envelope(
        _signed_forecast_envelope("futuris-test-key"),
        "Bearer futuris-test-key",
        test_db,
    )
    assert result["status"] == "accepted"

    feed = read_events(after_id=0, limit=10, event_type="futuris.forecast", agent="sentinel", db=test_db)
    assert len(feed["events"]) == 1
    event = feed["events"][0]
    assert event["payload"] == {
        "source_agent": "futuris",
        "forecast_id": "forecast-1",
        "target": "BTC volatility over 24h",
        "status": "active",
        "as_of": "2026-09-29T10:00:00Z",
        "expires_at": "2026-09-30T10:00:00Z",
        "model_version": "futuris-test-model",
        "prediction": 0.5,
        "range_lower": 0.2,
        "range_upper": 0.8,
        "probability": 0.7,
        "confidence": 0.6,
        "prediction_is_not_authorization": True,
        "correlation_id": "forecast-correlation-1",
    }


def test_only_authenticated_futuris_can_publish_forecast_intent(test_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MEMORA_TURSO_EVENTS_ENABLED", "false")
    monkeypatch.setenv("INTELX_API_KEY", "intelx-test-key")

    with pytest.raises(HTTPException) as exc:
        ingest_envelope(
            _signed_forecast_envelope("intelx-test-key", sender="intelx"),
            "Bearer intelx-test-key",
            test_db,
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        {"prediction_is_not_authorization": False},
        {"confidence": 1.5},
        {"range_lower": 0.9},
        {"prediction": float("nan")},
        {"status": []},
    ],
)
def test_futuris_forecast_rejects_invalid_advisories_before_persistence(test_db, monkeypatch, change):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MEMORA_TURSO_EVENTS_ENABLED", "false")
    monkeypatch.setenv("FUTURIS_API_KEY", "futuris-test-key")
    monkeypatch.setattr("apps.api.routers.v1_events.turso_events_configured", lambda: False)
    monkeypatch.setattr("apps.api.routers.v1_events.turso_required", lambda: False)
    payload = {
        "forecast_id": "forecast-1", "target": "BTC volatility", "status": "active",
        "prediction": 0.5, "range_lower": 0.2, "range_upper": 0.8,
        "confidence": 0.6, "prediction_is_not_authorization": True,
    }
    payload.update(change)
    with pytest.raises(HTTPException) as exc:
        ingest_envelope(
            _signed_forecast_envelope("futuris-test-key", payload=payload),
            "Bearer futuris-test-key",
            test_db,
        )
    assert exc.value.status_code == 422
    assert test_db.query(EventLog).count() == 0


def test_mesh_ingest_rejects_tampering_and_bad_credentials(test_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MEMORA_TURSO_EVENTS_ENABLED", "false")
    monkeypatch.setenv("INTELX_API_KEY", "intelx-test-key")
    env = _signed_envelope("intelx-test-key")
    env.payload["headline"] = "tampered"
    with pytest.raises(HTTPException) as exc:
        ingest_envelope(env, "Bearer intelx-test-key", test_db)
    assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        ingest_envelope(_signed_envelope("intelx-test-key"), "Bearer wrong-key", test_db)
    assert exc.value.status_code == 401

    env = _signed_envelope("intelx-test-key")
    env.signature = None
    with pytest.raises(HTTPException) as exc:
        ingest_envelope(env, "Bearer intelx-test-key", test_db)
    assert exc.value.status_code == 401


def test_turso_event_adapter_uses_http_pipeline_without_printing_credentials(monkeypatch):
    from storage.relational import turso_events

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MEMORA_TURSO_EVENTS_ENABLED", "true")
    monkeypatch.setenv("TURSO_DATABASE_URL", "https://events-test.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-token")
    turso_events._schema_ready_for.clear()
    calls = []

    class Response:
        status = 200
        def __init__(self, body):
            self.body = body
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self):
            return json.dumps(self.body).encode()

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode())
        calls.append(body)
        sql = body["requests"][0]["stmt"]["sql"]
        if sql.startswith("SELECT id FROM event_log"):
            result = {"type": "ok", "response": {"result": {"rows": [[{"value": 42}]]}}}
        elif sql.startswith("SELECT id,event_id"):
            result = {"type": "ok", "response": {"result": {"rows": [[
                {"value": 42}, {"value": "event-42"}, {"value": "intelx.news"},
                {"value": "default"}, {"value": "futuris"}, {"value": '{"headline":"News"}'},
                {"value": "2026-09-26T00:00:00+00:00"},
            ]]}}}
        else:
            result = {"type": "ok", "response": {"result": {}}}
        return Response({"results": [result for _ in body["requests"]]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    cursor = turso_events.append({
        "event_id": "event-42", "event_type": "intelx.news", "tenant_id": "default",
        "target_agent": "futuris", "payload": {"headline": "News"}, "created_at": "2026-09-26T00:00:00+00:00",
    })
    rows = turso_events.read(after_id=0, limit=10, tenant_id="default", agent="futuris")
    assert cursor == 42
    assert rows[0]["target_agent"] == "futuris"
    assert rows[0]["payload"] == {"headline": "News"}
    assert len(calls) == 4


def test_turso_consumer_cursor_ack_requires_visible_event(monkeypatch):
    from storage.relational import turso_events

    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MEMORA_TURSO_EVENTS_ENABLED", "true")
    monkeypatch.setenv("TURSO_DATABASE_URL", "https://cursor-test.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "cursor-token")
    turso_events._schema_ready_for.clear()
    statements = []

    class Response:
        status = 200
        def __init__(self, body):
            self.body = body
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self):
            return json.dumps(self.body).encode()

    cursor_reads = 0
    def fake_urlopen(request, timeout):
        nonlocal cursor_reads
        batch = json.loads(request.data.decode())
        sql = batch["requests"][0]["stmt"]["sql"]
        statements.append(sql)
        if sql.startswith("SELECT last_event_id"):
            rows = [[{"value": 0 if cursor_reads == 0 else 42}]]
            cursor_reads += 1
        elif sql.startswith("SELECT id FROM event_log WHERE id>? "):
            rows = [[{"value": 42}]]
        else:
            rows = []
        return Response({"results": [{"type": "ok", "response": {"result": {"rows": rows}}}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert turso_events.acknowledge(tenant_id="default", agent="friday", event_id=42) == 42
    assert any("MAX(last_event_id,excluded.last_event_id)" in sql for sql in statements)


def _signed_decision_envelope(key, *, sender="stratex", payload=None):
    fields = {
        "message_id": "stratex-decision-1",
        "correlation_id": "journey-correlation-1",
        "from_agent": sender,
        "to_agent": "all",
        "intent": "stratex.decision",
        "priority": "normal",
        "ttl": 3600,
        "auth_token": None,
        "payload": payload
        or {
            "decision_id": "decision-1",
            "forecast_id": "forecast-1",
            "action": "paper_intent",
            "paper_only": True,
            "policy_reason": "PAPER_BLOCKED",
            "gates_summary": ["paper_mode_active", "live_forbidden_by_design"],
            "headline": "Paper-only evaluation recorded",
            "summary": "Advisory evaluated under paper policy; no live order.",
            "decided_at": "2026-10-01T00:00:00Z",
        },
        "created_at": time.time(),
    }
    raw = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode()
    signature = hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
    return IncomingEnvelope(**fields, signature=signature)


def test_signed_stratex_decision_round_trips_correlation_id(test_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MEMORA_TURSO_EVENTS_ENABLED", "false")
    monkeypatch.setenv("STRATEX_API_KEY", "stratex-test-key")
    monkeypatch.setattr("apps.api.routers.v1_events.turso_events_configured", lambda: False)
    monkeypatch.setattr("apps.api.routers.v1_events.turso_required", lambda: False)

    result = ingest_envelope(
        _signed_decision_envelope("stratex-test-key"),
        "Bearer stratex-test-key",
        test_db,
    )
    assert result["status"] == "accepted"

    feed = read_events(after_id=0, limit=10, event_type="stratex.decision", agent="friday", db=test_db)
    assert len(feed["events"]) == 1
    event = feed["events"][0]
    assert event["payload"]["correlation_id"] == "journey-correlation-1"
    assert event["payload"]["paper_only"] is True
    assert event["payload"]["action"] == "paper_intent"
    assert event["payload"]["source_agent"] == "stratex"


def test_only_authenticated_stratex_can_publish_decision_intent(test_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MEMORA_TURSO_EVENTS_ENABLED", "false")
    monkeypatch.setenv("INTELX_API_KEY", "intelx-test-key")

    with pytest.raises(HTTPException) as exc:
        ingest_envelope(
            _signed_decision_envelope("intelx-test-key", sender="intelx"),
            "Bearer intelx-test-key",
            test_db,
        )
    assert exc.value.status_code == 403
    assert test_db.query(EventLog).count() == 0


@pytest.mark.parametrize(
    "change",
    [
        {"paper_only": False},
        {"action": "live_order"},
        {"decision_id": ""},
        {"policy_reason": ""},
        {"gates_summary": "not-a-list"},
    ],
)
def test_stratex_decision_rejects_invalid_or_live_bearing_payloads(test_db, monkeypatch, change):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("MEMORA_TURSO_EVENTS_ENABLED", "false")
    monkeypatch.setenv("STRATEX_API_KEY", "stratex-test-key")
    monkeypatch.setattr("apps.api.routers.v1_events.turso_events_configured", lambda: False)
    monkeypatch.setattr("apps.api.routers.v1_events.turso_required", lambda: False)
    payload = {
        "decision_id": "decision-1",
        "forecast_id": "forecast-1",
        "action": "paper_intent",
        "paper_only": True,
        "policy_reason": "PAPER_BLOCKED",
    }
    payload.update(change)
    with pytest.raises(HTTPException) as exc:
        ingest_envelope(
            _signed_decision_envelope("stratex-test-key", payload=payload),
            "Bearer stratex-test-key",
            test_db,
        )
    assert exc.value.status_code == 422
    assert test_db.query(EventLog).count() == 0


def test_intelx_news_ingest_exposes_correlation_id_to_consumers(test_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MEMORA_TURSO_EVENTS_ENABLED", "false")
    monkeypatch.setenv("INTELX_API_KEY", "intelx-test-key")
    monkeypatch.setattr("apps.api.routers.v1_events.turso_events_configured", lambda: False)
    monkeypatch.setattr("apps.api.routers.v1_events.turso_required", lambda: False)

    fields = {
        "message_id": "intelx-news-corr-1",
        "correlation_id": "corr-journey-intelx-1",
        "from_agent": "intelx",
        "to_agent": "all",
        "intent": "intelx.news",
        "priority": "normal",
        "ttl": 600,
        "auth_token": None,
        "payload": {
            "signal_id": "sig-1",
            "headline": "Exchange announces maintenance window",
            "summary": "Routine announcement; unverified.",
            "published_at": "2026-10-01T00:00:00Z",
            "relevance": {"confidence": 0.5},
            "topics": ["stratex"],
        },
        "created_at": time.time(),
    }
    raw = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str).encode()
    envelope = IncomingEnvelope(
        **fields, signature=hmac.new(b"intelx-test-key", raw, hashlib.sha256).hexdigest()
    )
    result = ingest_envelope(envelope, "Bearer intelx-test-key", test_db)
    assert result["status"] == "accepted"

    feed = read_events(after_id=0, limit=10, event_type="intelx.news", agent="futuris", db=test_db)
    assert len(feed["events"]) == 1
    assert feed["events"][0]["payload"]["correlation_id"] == "corr-journey-intelx-1"
