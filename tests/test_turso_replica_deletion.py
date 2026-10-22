"""Local regressions for hard-delete convergence with the optional Turso mirror.

All network calls are mocked. These tests never contact a Turso account or use
non-synthetic memory data.
"""
import json
import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from core.identity.service import IdentityService
from core.memory.service import MemoryService
from core.resilience.self_healing import self_healing_supervisor
from storage.relational.models import (
    DeletionTombstone,
    LifecycleState,
    MemoryRecord,
    MemoryType,
)
from storage.relational import turso_sync


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _record_payload(memory_id: str, tenant_id: str = "tenant-a") -> dict:
    return {
        "id": memory_id,
        "namespace_id": "namespace-a",
        "owner_id": "owner-a",
        "memory_type": "episodic",
        "content_text": "synthetic record for local SQL verification",
        "source": "test",
        "confidence": 1.0,
        "importance": 0.5,
        "lifecycle_state": "active",
        "tenant_id": tenant_id,
        "created_at": "2026-10-08T00:00:00+00:00",
    }


def _args(statement: dict) -> list:
    return [item["value"] for item in statement["stmt"]["args"]]


def test_replica_upsert_sql_obeys_delete_fence_and_tenant_identity():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE memory_records (
            id TEXT PRIMARY KEY, namespace_id TEXT, owner_id TEXT,
            memory_type TEXT, content_text TEXT, source TEXT,
            confidence REAL, importance REAL, lifecycle_state TEXT,
            tenant_id TEXT, created_at TEXT
        );
        """
    )
    connection.execute(turso_sync._DELETE_FENCE_SCHEMA_SQL)
    connection.execute(turso_sync._DELETE_FENCE_SQL, ("tenant-a", "already-deleted"))

    blocked = turso_sync._memory_statement(_record_payload("already-deleted"))
    connection.execute(blocked["stmt"]["sql"], _args(blocked))
    assert connection.execute(
        "SELECT id FROM memory_records WHERE id = ?", ("already-deleted",)
    ).fetchone() is None, "an older async upsert must not resurrect a fenced delete"

    connection.execute(
        """INSERT INTO memory_records
           (id, namespace_id, owner_id, memory_type, content_text, source,
            confidence, importance, lifecycle_state, tenant_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("foreign-id", "foreign-ns", "foreign-owner", "episodic", "foreign marker",
         "test", 1.0, 0.5, "active", "tenant-b", ""),
    )
    cross_tenant_conflict = turso_sync._memory_statement(_record_payload("foreign-id", "tenant-a"))
    connection.execute(cross_tenant_conflict["stmt"]["sql"], _args(cross_tenant_conflict))
    assert connection.execute(
        "SELECT tenant_id, content_text FROM memory_records WHERE id = ?", ("foreign-id",)
    ).fetchone() == ("tenant-b", "foreign marker")

    normal = turso_sync._memory_statement(_record_payload("fresh-id"))
    connection.execute(normal["stmt"]["sql"], _args(normal))
    assert connection.execute(
        "SELECT tenant_id, content_text FROM memory_records WHERE id = ?", ("fresh-id",)
    ).fetchone() == ("tenant-a", "synthetic record for local SQL verification")
    connection.close()


def test_remote_delete_pipeline_fences_then_deletes_only_requested_tenant(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            return json.dumps({"results": [{"type": "ok"}] * 3}).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data)
        return Response()

    monkeypatch.setenv("TURSO_DATABASE_URL", "https://synthetic.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "synthetic-test-token")
    monkeypatch.setattr(turso_sync.urllib.request, "urlopen", fake_urlopen)

    assert turso_sync.delete_memory_from_turso("memory-synthetic", tenant_id="tenant-synthetic") is True
    requests = captured["payload"]["requests"]
    assert len(requests) == 3
    assert "CREATE TABLE IF NOT EXISTS memora_deleted_memories" in requests[0]["stmt"]["sql"]
    assert "INSERT INTO memora_deleted_memories" in requests[1]["stmt"]["sql"]
    assert _args(requests[1]) == ["tenant-synthetic", "memory-synthetic"]
    assert requests[2]["stmt"]["sql"] == "DELETE FROM memory_records WHERE tenant_id = ? AND id = ?"
    assert _args(requests[2]) == ["tenant-synthetic", "memory-synthetic"]
    assert captured["timeout"] == 6.0


def test_hard_delete_records_failed_remote_replica_for_retry(test_db, monkeypatch):
    actor = IdentityService.register_agent(test_db, "friday", role="worker")
    namespace = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")
    record = MemoryRecord(
        id="turso-mirror-delete-regression",
        namespace_id=namespace.id,
        owner_id=actor.id,
        memory_type=MemoryType.EPISODIC,
        content_text="synthetic private marker for Turso deletion regression",
        lifecycle_state=LifecycleState.ACTIVE,
        tenant_id="default",
    )
    test_db.add(record)
    test_db.commit()

    remote_attempts = []
    monkeypatch.setattr(turso_sync, "turso_replica_enabled", lambda: True)
    monkeypatch.setattr(
        turso_sync,
        "delete_memory_from_turso",
        lambda memory_id, tenant_id="default": remote_attempts.append((memory_id, tenant_id)) or False,
    )

    result = MemoryService.delete_memory(
        test_db,
        memory_id=record.id,
        actor_name="friday",
        hard_delete=True,
    )
    tombstone = test_db.query(DeletionTombstone).filter_by(
        memory_id=record.id, tenant_id="default"
    ).one()
    assert test_db.query(MemoryRecord).filter_by(id=record.id).first() is None
    assert tombstone.turso_deleted is False
    assert tombstone.status == "PENDING_RETRY"
    assert result["deletion_converged"] is False
    assert remote_attempts == [(record.id, "default")]

    monkeypatch.setattr(
        turso_sync,
        "delete_memory_from_turso",
        lambda memory_id, tenant_id="default": remote_attempts.append((memory_id, tenant_id)) or True,
    )
    healed = self_healing_supervisor.check_unconverged_tombstones(test_db, dry_run=False)
    test_db.refresh(tombstone)
    assert healed.repaired == 1
    assert tombstone.turso_deleted is True
    assert tombstone.status == "CONVERGED"
    assert remote_attempts[-1] == (record.id, "default")


def test_turso_primary_does_not_schedule_a_duplicate_async_upsert(monkeypatch):
    from storage.relational import session

    monkeypatch.setenv("TURSO_DATABASE_URL", "https://synthetic.turso.io")
    monkeypatch.setenv("TURSO_AUTH_TOKEN", "synthetic-test-token")
    monkeypatch.setattr(session, "storage_receipt", lambda: {"backend": "turso", "durable": True})
    assert turso_sync.turso_replica_enabled() is False

    class UnexpectedThread:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Turso primary writes must not schedule a duplicate mirror upsert")

    monkeypatch.setattr(turso_sync.threading, "Thread", UnexpectedThread)
    turso_sync.push_memory_to_turso_async(object())


def test_migration_backfills_existing_tombstones_as_replica_complete(tmp_path):
    database = tmp_path / "turso-delete-migration.db"
    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database.as_posix()}")

    command.upgrade(config, "e4a1c7f90b23")
    connection = sqlite3.connect(database)
    connection.execute(
        """INSERT INTO deletion_tombstones
           (id, tenant_id, memory_id, relational_deleted, vector_deleted,
            cache_deleted, graph_deleted, status, retry_count, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("old-tombstone", "default", "old-memory", 1, 1, 1, 1,
         "CONVERGED", 0, "2026-10-07T00:00:00", "2026-10-07T00:00:00"),
    )
    connection.commit()
    connection.close()

    command.upgrade(config, "head")
    connection = sqlite3.connect(database)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(deletion_tombstones)")}
    assert "turso_deleted" in columns
    assert connection.execute(
        "SELECT turso_deleted FROM deletion_tombstones WHERE id = ?", ("old-tombstone",)
    ).fetchone() == (1,)
    connection.close()
