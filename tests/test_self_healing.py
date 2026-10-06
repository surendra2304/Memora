"""
Tests for the self-healing supervisor.

Each check must both DETECT a real defect and REPAIR it when not in dry-run.
Tests build the broken state directly rather than mocking, so a check that
silently stops matching still fails here.
"""
from datetime import datetime, timedelta, timezone

import pytest

from core.resilience.circuit_breaker import CircuitState, circuit_registry
from core.resilience.self_healing import HealingReport, self_healing_supervisor
from core.identity.service import IdentityService
from storage.relational.models import (
    DeletionTombstone,
    LifecycleState,
    MemoryRecord,
    MemoryType,
)


@pytest.fixture(autouse=True)
def _isolate_vector_store_and_circuits():
    """vector_adapter and circuit_registry are process-wide singletons.

    Without this, a vector written by one test is visible to the next, which
    made these tests pass alone and fail in the full suite.
    """
    from core.resilience.circuit_breaker import circuit_registry
    from storage.vector.qdrant_adapter import vector_adapter

    vector_adapter._mock_store.clear()
    circuit_registry.reset_all()
    yield
    vector_adapter._mock_store.clear()
    circuit_registry.reset_all()


def _make_memory(db, agent_name="friday", state=LifecycleState.ACTIVE, importance=0.8,
                 content="a memory about the xenon compressor", age_days=0):
    agent = IdentityService.register_agent(db, agent_name)
    namespace = IdentityService.resolve_namespace(db, f"memora://{agent_name}/private")
    record = MemoryRecord(
        namespace_id=namespace.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text=content,
        confidence=0.9,
        importance=importance,
        lifecycle_state=state,
        created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


# ---------------------------------------------------------------------------
# Report contract
# ---------------------------------------------------------------------------

def test_a_clean_system_reports_healthy(test_db):
    report = self_healing_supervisor.run(test_db, dry_run=True)
    assert isinstance(report, HealingReport)
    names = {c.name for c in report.checks}
    assert names == {
        "unconverged_tombstones",
        "orphaned_vectors",
        "dangling_supersession",
        "open_circuits",
        "decay_backlog",
    }
    assert report.healthy is True
    assert report.total_findings == 0


def test_dry_run_never_mutates(test_db):
    """The read-only endpoint must not repair anything."""
    record = _make_memory(test_db, state=LifecycleState.DELETED)
    from storage.vector.qdrant_adapter import vector_adapter

    vector_adapter._mock_store[record.id] = {"vector": [0.1], "payload": {}, "tenant_id": "default"}

    report = self_healing_supervisor.run(test_db, dry_run=True)
    orphans = next(c for c in report.checks if c.name == "orphaned_vectors")
    assert orphans.findings == 1
    assert orphans.repaired == 0
    assert record.id in vector_adapter._mock_store, "dry run must not evict"

    vector_adapter._mock_store.clear()


# ---------------------------------------------------------------------------
# Orphaned vectors: a soft-deleted memory still retrievable by semantic search
# ---------------------------------------------------------------------------

def test_detects_and_evicts_a_vector_whose_memory_was_soft_deleted(test_db):
    from storage.vector.qdrant_adapter import vector_adapter

    record = _make_memory(test_db)
    vector_adapter._mock_store[record.id] = {"vector": [0.1], "payload": {}, "tenant_id": "default"}

    # Soft delete: the row survives but the embedding is never removed.
    record.lifecycle_state = LifecycleState.DELETED
    test_db.commit()

    dry = self_healing_supervisor.check_orphaned_vectors(test_db, dry_run=True)
    assert dry.findings == 1
    assert dry.healthy is False
    assert record.id in vector_adapter._mock_store

    wet = self_healing_supervisor.check_orphaned_vectors(test_db, dry_run=False)
    assert wet.repaired == 1
    assert record.id not in vector_adapter._mock_store, "orphan should have been evicted"


def test_a_live_memory_vector_is_not_an_orphan(test_db):
    from storage.vector.qdrant_adapter import vector_adapter

    record = _make_memory(test_db)
    vector_adapter._mock_store[record.id] = {"vector": [0.1], "payload": {}, "tenant_id": "default"}

    result = self_healing_supervisor.check_orphaned_vectors(test_db, dry_run=False)
    assert result.healthy is True
    assert result.findings == 0
    assert record.id in vector_adapter._mock_store, "a live memory must keep its embedding"

    vector_adapter._mock_store.clear()


# ---------------------------------------------------------------------------
# Unconverged tombstones
# ---------------------------------------------------------------------------

def test_detects_and_converges_a_stuck_tombstone(test_db):
    from storage.vector.qdrant_adapter import vector_adapter

    record = _make_memory(test_db)
    vector_adapter._mock_store[record.id] = {"vector": [0.1], "payload": {}, "tenant_id": "default"}

    tombstone = DeletionTombstone(
        tenant_id="default",
        memory_id=record.id,
        relational_deleted=True,
        vector_deleted=False,   # Qdrant was down when the delete ran
        cache_deleted=True,
        graph_deleted=True,
        status="PENDING_RETRY",
    )
    test_db.add(tombstone)
    test_db.commit()

    dry = self_healing_supervisor.check_unconverged_tombstones(test_db, dry_run=True)
    assert dry.findings == 1
    assert dry.repaired == 0
    assert any("vector" in d for d in dry.details)

    wet = self_healing_supervisor.check_unconverged_tombstones(test_db, dry_run=False)
    assert wet.repaired == 1
    test_db.refresh(tombstone)
    assert tombstone.status == "CONVERGED"
    assert tombstone.vector_deleted is True
    assert tombstone.retry_count == 1


def test_an_already_converged_tombstone_is_not_a_finding(test_db):
    record = _make_memory(test_db)
    test_db.add(DeletionTombstone(
        tenant_id="default", memory_id=record.id,
        relational_deleted=True, vector_deleted=True,
        cache_deleted=True, graph_deleted=True, status="CONVERGED",
    ))
    test_db.commit()

    result = self_healing_supervisor.check_unconverged_tombstones(test_db, dry_run=False)
    assert result.healthy is True
    assert result.findings == 0


# ---------------------------------------------------------------------------
# Dangling supersession
# ---------------------------------------------------------------------------

def test_detects_and_clears_a_dangling_superseded_by_pointer(test_db):
    record = _make_memory(test_db)
    record.superseded_by_id = "memory-that-was-hard-deleted"
    test_db.commit()

    dry = self_healing_supervisor.check_dangling_supersession(test_db, dry_run=True)
    assert dry.findings == 1
    assert dry.healthy is False

    wet = self_healing_supervisor.check_dangling_supersession(test_db, dry_run=False)
    assert wet.repaired == 1
    test_db.refresh(record)
    assert record.superseded_by_id is None


def test_a_valid_supersession_pointer_is_left_alone(test_db):
    older = _make_memory(test_db, content="the original compressor note")
    newer = _make_memory(test_db, content="the corrected compressor note")
    older.superseded_by_id = newer.id
    test_db.commit()

    result = self_healing_supervisor.check_dangling_supersession(test_db, dry_run=False)
    assert result.healthy is True
    test_db.refresh(older)
    assert older.superseded_by_id == newer.id, "a valid pointer must not be cleared"


# ---------------------------------------------------------------------------
# Open circuits
# ---------------------------------------------------------------------------

def test_reports_an_open_dependency_circuit(test_db):
    breaker = circuit_registry.get("test_dep", failure_threshold=1, recovery_timeout=9999)
    breaker.reset()

    def boom():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        breaker.call(boom)

    result = self_healing_supervisor.check_open_circuits(test_db, dry_run=True)
    assert result.healthy is False
    assert result.findings == 1
    assert any("test_dep" in d for d in result.details)

    # The supervisor must NOT force-reset: the breaker's own probe is the
    # correct recovery path, and resetting would stampede a dead backend.
    assert breaker.state is CircuitState.OPEN

    breaker.reset()


def test_reports_healthy_when_all_circuits_are_closed(test_db):
    circuit_registry.reset_all()
    result = self_healing_supervisor.check_open_circuits(test_db, dry_run=True)
    assert result.healthy is True
    assert result.findings == 0


# ---------------------------------------------------------------------------
# Decay backlog
# ---------------------------------------------------------------------------

def test_reports_a_decay_backlog(test_db):
    _make_memory(test_db, importance=0.9, age_days=30, content="old high-importance memory")

    result = self_healing_supervisor.check_decay_backlog(test_db, dry_run=True)
    assert result.findings == 1
    assert result.healthy is False


def test_a_recent_memory_is_not_backlog(test_db):
    _make_memory(test_db, importance=0.9, age_days=1, content="fresh memory")

    result = self_healing_supervisor.check_decay_backlog(test_db, dry_run=True)
    assert result.findings == 0
    assert result.healthy is True


# ---------------------------------------------------------------------------
# Robustness: a broken check must not take the run down
# ---------------------------------------------------------------------------

def test_a_failing_check_is_reported_without_aborting_the_run(test_db, monkeypatch):
    def explode(db, dry_run):
        raise RuntimeError("check blew up")

    monkeypatch.setattr(
        self_healing_supervisor, "check_decay_backlog", explode, raising=True
    )
    report = self_healing_supervisor.run(test_db, dry_run=True)

    failed = [c for c in report.checks if c.error]
    assert len(failed) == 1
    assert "RuntimeError" in failed[0].error
    assert report.healthy is False
    # The other four checks still ran.
    assert len(report.checks) == 5


def test_the_report_serialises_to_plain_json_types(test_db):
    report = self_healing_supervisor.run(test_db, dry_run=True)
    payload = report.to_dict()
    assert set(payload) == {"healthy", "dry_run", "total_findings", "total_repaired", "checks"}
    import json
    json.dumps(payload)  # must not raise


def test_repairs_are_capped_per_run(test_db):
    """A badly corrupted store must not become an unbounded write storm."""
    from storage.vector.qdrant_adapter import vector_adapter

    cap = self_healing_supervisor.MAX_REPAIRS_PER_CHECK
    for i in range(cap + 5):
        vector_adapter._mock_store[f"ghost-{i}"] = {"vector": [0.1], "payload": {}, "tenant_id": "default"}

    result = self_healing_supervisor.check_orphaned_vectors(test_db, dry_run=False)
    assert result.findings == cap + 5
    assert result.repaired <= cap

    vector_adapter._mock_store.clear()
