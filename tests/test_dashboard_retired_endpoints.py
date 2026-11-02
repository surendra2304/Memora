"""The legacy Turso sync surface is retired; only the 410 contract remains."""


def test_dashboard_sync_requires_agent_identity(client, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MEMORA_API_KEY", "memora-test-key")
    monkeypatch.setenv("FRIDAY_API_KEY", "friday-test-key")

    assert client.post("/api/dashboard/sync").status_code == 401


def test_dashboard_sync_rejects_non_memora_identity(client, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MEMORA_API_KEY", "memora-test-key")
    monkeypatch.setenv("FRIDAY_API_KEY", "friday-test-key")

    response = client.post(
        "/api/dashboard/sync",
        headers={"X-Agent-Name": "friday", "X-API-Key": "friday-test-key"},
    )

    assert response.status_code == 403


def test_dashboard_sync_reports_retired_for_memora_identity(client, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MEMORA_API_KEY", "memora-test-key")

    response = client.post(
        "/api/dashboard/sync",
        headers={"X-Agent-Name": "memora", "X-API-Key": "memora-test-key"},
    )

    assert response.status_code == 410
    assert "retired" in response.json()["detail"].lower()


def test_global_dashboard_overview_no_longer_returns_private_memory_dump(client):
    response = client.get("/api/dashboard/overview")

    assert response.status_code == 410
    assert "policy-scoped" in response.json()["detail"]
