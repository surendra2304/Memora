from __future__ import annotations

import pytest

from storage.relational import session


def test_production_sqlite_configuration_is_rejected_before_engine_creation(tmp_path, monkeypatch):
    db_path = tmp_path / "production-local.db"
    monkeypatch.setattr(session, "_active_storage_backend", "uninitialized")
    monkeypatch.setattr(session, "_active_storage_durable", False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(session.settings, "DATABASE_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(session.settings, "SQLITE_FALLBACK_URL", f"sqlite:///{db_path.as_posix()}")
    monkeypatch.setattr(session.settings, "TURSO_DATABASE_URL", None)
    monkeypatch.setattr(session.settings, "TURSO_AUTH_TOKEN", None)

    with pytest.raises(RuntimeError, match="requires the self-hosted PostgreSQL DATABASE_URL"):
        session.create_db_engine()
    assert not db_path.exists()


def test_unsupported_turso_url_cannot_fall_back_to_sqlite_in_production(tmp_path, monkeypatch):
    fallback_path = tmp_path / "fallback.db"
    monkeypatch.setattr(session, "_active_storage_backend", "uninitialized")
    monkeypatch.setattr(session, "_active_storage_durable", False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(session.settings, "DATABASE_URL", "https://unit-test.turso.io")
    monkeypatch.setattr(session.settings, "TURSO_DATABASE_URL", None)
    monkeypatch.setattr(session.settings, "TURSO_AUTH_TOKEN", None)
    monkeypatch.setattr(session.settings, "SQLITE_FALLBACK_URL", f"sqlite:///{fallback_path.as_posix()}")

    with pytest.raises(RuntimeError, match="requires the self-hosted PostgreSQL DATABASE_URL"):
        session.create_db_engine()
    assert not fallback_path.exists()

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


def test_production_does_not_implicitly_select_configured_turso(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(session.settings, "DATABASE_URL", "sqlite:///./data/memora.db")
    monkeypatch.setattr(session.settings, "TURSO_DATABASE_URL", "https://memory.example.turso.io")
    monkeypatch.setattr(session.settings, "TURSO_AUTH_TOKEN", "isolated-test-token")

    with pytest.raises(RuntimeError, match="requires the self-hosted PostgreSQL DATABASE_URL"):
        session.create_db_engine()


def test_production_rejects_sqlite_even_when_turso_driver_is_unavailable(tmp_path, monkeypatch):
    fallback_path = tmp_path / "windows-fallback.db"
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(session.settings, "DATABASE_URL", "sqlite:///./data/memora.db")
    monkeypatch.setattr(session.settings, "TURSO_DATABASE_URL", "https://memory.example.turso.io")
    monkeypatch.setattr(session.settings, "TURSO_AUTH_TOKEN", "isolated-test-token")
    monkeypatch.setattr(session.settings, "SQLITE_FALLBACK_URL", f"sqlite:///{fallback_path.as_posix()}")

    with pytest.raises(RuntimeError, match="requires the self-hosted PostgreSQL DATABASE_URL"):
        session.create_db_engine()
    assert not fallback_path.exists()

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


def test_production_postgres_engine_is_authoritative(monkeypatch):
    """Exercise Memora's real PostgreSQL engine branch against the CI service."""
    import os

    from sqlalchemy import text

    dsn = os.getenv("MEMORA_TEST_POSTGRES_URL")
    if not dsn:
        pytest.skip("MEMORA_TEST_POSTGRES_URL is supplied by the PostgreSQL 18 CI job")

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(session.settings, "DATABASE_URL", dsn)
    monkeypatch.setattr(session.settings, "TURSO_DATABASE_URL", None)
    monkeypatch.setattr(session.settings, "TURSO_AUTH_TOKEN", None)
    monkeypatch.setattr(session.settings, "USE_SQLITE_FALLBACK", False)
    monkeypatch.setattr(session, "_active_storage_backend", "uninitialized")
    monkeypatch.setattr(session, "_active_storage_durable", False)

    pg_engine = session.create_db_engine()
    try:
        with pg_engine.connect() as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
        receipt = session.storage_receipt()
        assert receipt["backend"] == "postgresql"
        assert receipt["durable"] is True
        assert session.storage_ready() is True
    finally:
        pg_engine.dispose()
