from __future__ import annotations

import pytest
from fastapi import HTTPException

from storage.relational import session


def test_production_sqlite_is_non_authoritative_and_readiness_fails_closed(tmp_path, monkeypatch):
    db_path = tmp_path / "production-local.db"
    monkeypatch.setattr(session, "_active_storage_backend", "uninitialized")
    monkeypatch.setattr(session, "_active_storage_durable", False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(session.settings, "DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(session.settings, "SQLITE_FALLBACK_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(session.settings, "TURSO_DATABASE_URL", None)
    monkeypatch.setattr(session.settings, "TURSO_AUTH_TOKEN", None)

    isolated_engine = session.create_db_engine()
    try:
        with isolated_engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        assert db_path.exists()
        assert session.storage_receipt() == {
            "backend": "sqlite",
            "durable": False,
            "durability": "process_local",
        }
        assert session.storage_ready() is False
        with pytest.raises(HTTPException) as error:
            next(session.get_db())
        assert error.value.status_code == 503
    finally:
        isolated_engine.dispose()


def test_unsupported_turso_orm_connection_cannot_use_sqlite_fallback_as_durable(tmp_path, monkeypatch):
    fallback_path = tmp_path / "fallback.db"
    monkeypatch.setattr(session, "_active_storage_backend", "uninitialized")
    monkeypatch.setattr(session, "_active_storage_durable", False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(session.settings, "DATABASE_URL", "https://unit-test.turso.io")
    monkeypatch.setattr(session.settings, "TURSO_DATABASE_URL", None)
    monkeypatch.setattr(session.settings, "TURSO_AUTH_TOKEN", None)
    monkeypatch.setattr(session.settings, "SQLITE_FALLBACK_URL", f"sqlite:///{fallback_path.as_posix()}")

    isolated_engine = session.create_db_engine()
    try:
        with isolated_engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        assert fallback_path.exists()
        assert session.storage_receipt() == {
            "backend": "sqlite_fallback",
            "durable": False,
            "durability": "process_local",
        }
        assert session.storage_ready() is False
    finally:
        isolated_engine.dispose()


def test_turso_url_uses_supported_sqlalchemy_libsql_format():
    assert session._turso_sqlalchemy_url(
        "https://memory.example.turso.io"
    ) == "sqlite+libsql://memory.example.turso.io?secure=true"
    assert session._turso_sqlalchemy_url(
        "libsql://memory.example.turso.io"
    ) == "sqlite+libsql://memory.example.turso.io?secure=true"
    assert session._turso_sqlalchemy_url(
        "https://memory.example.turso.io?region=aws"
    ) == "sqlite+libsql://memory.example.turso.io?region=aws&secure=true"


def test_production_prefers_configured_turso_over_ephemeral_sqlite(monkeypatch):
    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, _statement):
            return None

    class FakeEngine:
        def connect(self):
            return FakeConnection()

    captured = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return FakeEngine()

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(session.settings, "DATABASE_URL", "sqlite:///./data/memora.db")
    monkeypatch.setattr(session.settings, "TURSO_DATABASE_URL", "https://memory.example.turso.io")
    monkeypatch.setattr(session.settings, "TURSO_AUTH_TOKEN", "isolated-test-token")
    monkeypatch.setattr(session, "create_engine", fake_create_engine)

    engine = session.create_db_engine()

    assert isinstance(engine, FakeEngine)
    assert captured["url"] == "sqlite+libsql://memory.example.turso.io?secure=true"
    assert captured["kwargs"]["connect_args"] == {"auth_token": "isolated-test-token"}
    assert session.storage_receipt() == {
        "backend": "turso",
        "durable": True,
        "durability": "shared_durable",
    }


def test_unsupported_local_turso_driver_import_stays_non_authoritative(tmp_path, monkeypatch):
    fallback_path = tmp_path / "windows-fallback.db"
    original_create_engine = session.create_engine

    def create_engine_with_unavailable_libsql(url, **kwargs):
        if url.startswith("sqlite+libsql://"):
            raise ModuleNotFoundError("isolated simulation: driver unavailable on this platform")
        return original_create_engine(url, **kwargs)

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(session.settings, "DATABASE_URL", "sqlite:///./data/memora.db")
    monkeypatch.setattr(session.settings, "TURSO_DATABASE_URL", "https://memory.example.turso.io")
    monkeypatch.setattr(session.settings, "TURSO_AUTH_TOKEN", "isolated-test-token")
    monkeypatch.setattr(session.settings, "SQLITE_FALLBACK_URL", f"sqlite:///{fallback_path.as_posix()}")
    monkeypatch.setattr(session, "create_engine", create_engine_with_unavailable_libsql)

    fallback_engine = session.create_db_engine()
    try:
        with fallback_engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        assert fallback_path.exists()
        assert session.storage_receipt() == {
            "backend": "sqlite_fallback",
            "durable": False,
            "durability": "process_local",
        }
        assert session.storage_ready() is False
    finally:
        fallback_engine.dispose()


def test_write_receipt_names_actual_storage_backend(monkeypatch):
    from types import SimpleNamespace
    from core.memory.pipeline.write_service import MemoryWriteResult
    from storage.relational.models import LifecycleState, MemoryType

    monkeypatch.setattr(session, "_active_storage_backend", "sqlite")
    monkeypatch.setattr(session, "_active_storage_durable", False)
    record = SimpleNamespace(
        id="test-memory", tenant_id="tenant-a", user_id="user-a", agent_id="agent-a",
        workspace_id="workspace-a", device_id="device-a", task_id=None,
        idempotency_key=None, namespace_id="namespace-a", owner_id="agent-a",
        memory_type=MemoryType.EPISODIC, content_text="isolated receipt check", source="test",
        provenance={}, confidence=1.0, importance=0.5, lifecycle_state=LifecycleState.ACTIVE,
        created_at=None,
    )
    persistence_receipt = {
        "storage_backend": "sqlite",
        "storage_durable": False,
        "storage_durability": "process_local",
    }
    receipt = MemoryWriteResult(record, {"step_9_persistence": persistence_receipt}).to_dict()
    assert receipt["storage_backend"] == "sqlite"
    assert receipt["storage_durable"] is False
    assert receipt["storage_durability"] == "process_local"
    assert receipt["step_trace"]["step_9_persistence"] == persistence_receipt


def test_sdk_outage_never_reads_or_writes_a_local_memory_database(tmp_path, monkeypatch):
    import urllib.request
    from sdk.memora_client import MemoraClient

    local_db = tmp_path / "must-stay-unused.db"
    monkeypatch.setenv("CORTEX_API_KEY", "test-agent-key")

    def unavailable(*_args, **_kwargs):
        raise OSError("isolated test outage")

    monkeypatch.setattr(urllib.request, "urlopen", unavailable)
    client = MemoraClient(
        base_url="https://memora.invalid",
        local_db_path=str(local_db),
    )
    result = client.record_fact("cortex", "isolated fact")
    recalled = client.recall_memories("cortex", "isolated query")

    assert result["status"] == "error"
    assert result["cloud"] is False
    assert recalled == []
    assert not local_db.exists()
