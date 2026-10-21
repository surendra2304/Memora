"""Regression coverage for idempotent Universal Task Protocol writes."""

import pytest
from fastapi.testclient import TestClient

from core.identity.service import IdentityService
from storage.relational.models import MemoryRecord


@pytest.fixture
def task_mesh(client: TestClient, test_db, monkeypatch):
    monkeypatch.delenv("MEMORA_ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    key = "task-idempotency-regression-key"
    monkeypatch.setenv("FRIDAY_API_KEY", key)
    IdentityService.register_agent(test_db, "friday", role="worker", tenant_id="default")
    return {"headers": {"X-Agent-Name": "friday", "X-API-Key": key}}


def test_task_execute_replay_with_same_idempotency_key_returns_same_memory(
    client: TestClient, test_db, task_mesh
):
    """A retry reuses the original record even if its payload has changed."""
    headers = task_mesh["headers"]
    idempotency_key = "task-replay-key-001"
    envelope = {
        "task_id": "task-replay-001",
        "idempotency_key": idempotency_key,
        "action": "store",
        "payload": {"content": "first payload for idempotent replay"},
    }

    first = client.post("/v1/task/execute", headers=headers, json=envelope)
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["status"] == "SUCCESS", first_body
    first_memory_id = first_body["result"]["memory_id"]

    replay = client.post(
        "/v1/task/execute",
        headers=headers,
        json={**envelope, "payload": {"content": "changed retry payload"}},
    )
    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["status"] == "SUCCESS", replay_body
    assert replay_body["result"]["memory_id"] == first_memory_id
    assert replay_body["result"]["duplicate"] is True

    stored = (
        test_db.query(MemoryRecord)
        .filter_by(tenant_id="default", agent_id="friday", idempotency_key=idempotency_key)
        .all()
    )
    assert len(stored) == 1
    assert stored[0].id == first_memory_id
    assert stored[0].content_text == "first payload for idempotent replay"
