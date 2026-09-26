from storage.relational.models import LifecycleState, MemoryRecord, MemoryType
from core.identity.service import IdentityService
import apps.api.main as api_main
import storage.relational.session as db_session


def _value(value):
    return {"value": value}


def test_turso_import_merges_without_deleting_local_memories(test_db, monkeypatch):
    friday = IdentityService.register_agent(test_db, "friday")
    namespace = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")
    local = MemoryRecord(
        namespace_id=namespace.id,
        owner_id=friday.id,
        memory_type=MemoryType.PREFERENCE,
        content_text="Keep this local memory when Turso has no matching record.",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add(local)
    test_db.commit()
    local_id = local.id

    remote_agent_id = "remote-friday-id"
    remote_namespace_id = "remote-friday-ns"
    remote_agent = [
        _value(remote_agent_id), _value("forge"), _value(""), _value("2026-09-26"),
        _value("supervisor"), _value(None), _value(None), _value("default"),
    ]
    remote_namespace = [
        _value(remote_namespace_id), _value("memora://forge/private"),
        _value("agent-private"), _value(remote_agent_id), _value("2026-09-26"), _value("default"),
    ]

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"results": [
                {"response": {"result": {"rows": [remote_agent]}}},
                {"response": {"result": {"rows": [remote_namespace]}}},
                {"response": {"result": {"rows": []}}},
            ]}

    monkeypatch.setattr(db_session, "SessionLocal", lambda: test_db)
    monkeypatch.setattr(api_main.requests, "post", lambda *args, **kwargs: Response())
    monkeypatch.setenv("TURSO_DATABASE_URL", "https://memora.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-only")

    result = api_main.sync_from_turso()

    assert result["status"] == "success"
    assert result["deletions"] == 0
    assert test_db.query(MemoryRecord).filter(MemoryRecord.id == local_id).one().content_text.startswith("Keep this local")


def test_turso_sync_endpoint_does_not_claim_unconfigured_sync_succeeded(client, monkeypatch):
    monkeypatch.setattr(api_main, "sync_from_turso", lambda: {
        "status": "unconfigured", "agents_imported": 0, "namespaces_imported": 0, "memories_imported": 0
    })

    response = client.post("/api/dashboard/sync")

    assert response.status_code == 503
    assert response.json()["status"] == "unconfigured"


def test_turso_sync_non_200_is_reported_as_unavailable(monkeypatch):
    class Response:
        status_code = 503

    class Session:
        def close(self):
            pass

    monkeypatch.setattr(db_session, "SessionLocal", Session)
    monkeypatch.setattr(api_main.requests, "post", lambda *args, **kwargs: Response())
    monkeypatch.setenv("TURSO_DATABASE_URL", "https://memora.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "test-only")

    result = api_main.sync_from_turso()

    assert result["status"] == "unavailable"
    assert result["reason"] == "http_503"
