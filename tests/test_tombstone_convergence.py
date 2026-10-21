"""Regression tests for Memora multi-store deletion convergence."""
import importlib

from sqlalchemy import text
from sqlalchemy.orm import Query

from storage.relational.models import (
    DeletionTombstone, MemoryRecord, MemoryRelationship, MemoryType, LifecycleState,
)
from storage.convergence.tombstone_worker import TombstoneConvergenceWorker
from storage.vector.qdrant_adapter import vector_adapter
from core.cache.memory_cache import memory_cache
from core.identity.service import IdentityService


def _make_retry_case(db, tenant):
    agent = IdentityService.register_agent(db, name=f"{tenant}_agent", tenant_id=tenant)
    namespace = IdentityService.resolve_namespace(
        db, path=f"memora://{tenant}/retry", owner_agent_id=agent.id, tenant_id=tenant
    )
    records = []
    for index in range(3):
        record = MemoryRecord(
            tenant_id=tenant, user_id="user", agent_id=agent.name, workspace_id="workspace",
            namespace_id=namespace.id, owner_id=agent.id, memory_type=MemoryType.EPISODIC,
            content_text=f"Retry record {index}", content_hash=f"{tenant}_hash_{index}",
            lifecycle_state=LifecycleState.ACTIVE,
        )
        db.add(record)
        records.append(record)
    db.flush()
    target_id, unrelated_source_id, unrelated_target_id = (r.id for r in records)
    tombstone = DeletionTombstone(
        tenant_id=tenant, memory_id=target_id, relational_deleted=False, vector_deleted=False,
        cache_deleted=False, graph_deleted=False, turso_deleted=False, status="PENDING",
    )
    incident = MemoryRelationship(
        source_memory_id=target_id, target_memory_id=unrelated_source_id,
        relationship_type="relates_to", weight=1.0,
    )
    unrelated = MemoryRelationship(
        source_memory_id=unrelated_source_id, target_memory_id=unrelated_target_id,
        relationship_type="relates_to", weight=1.0,
    )
    db.add_all([tombstone, incident, unrelated])
    db.commit()
    return tombstone, target_id, incident.id, unrelated.id


def _set_local_deletion_stores(monkeypatch):
    monkeypatch.setattr(vector_adapter, "delete_embedding", lambda *args, **kwargs: True)
    worker_module = importlib.import_module("storage.convergence.tombstone_worker")
    monkeypatch.setattr(worker_module, "turso_replica_enabled", lambda: False)


def _install_sql_delete_failure_trigger(db, trigger_name, table_name, condition):
    db.execute(text(f"""
        CREATE TRIGGER {trigger_name}
        BEFORE DELETE ON {table_name}
        WHEN {condition}
        BEGIN SELECT RAISE(ABORT, 'injected SQL delete failure'); END
    """))
    db.commit()


def test_tombstone_worker_single_convergence(test_db, monkeypatch):
    tenant = "tenant_conv_1"
    agent = IdentityService.register_agent(test_db, name="conv_agent_1", tenant_id=tenant)
    ns = IdentityService.resolve_namespace(
        test_db, path="memora://conv/ns", owner_agent_id=agent.id, tenant_id=tenant
    )
    record = MemoryRecord(
        tenant_id=tenant, user_id="user_1", agent_id=agent.name, workspace_id="ws_1",
        namespace_id=ns.id, owner_id=agent.id, memory_type=MemoryType.EPISODIC,
        content_text="Data destined for convergent hard delete", content_hash="hash_conv_1",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add(record)
    test_db.flush()
    mem_id = record.id
    vector_adapter.upsert_embedding(
        memory_id=mem_id, vector=[0.1] * 1536, tenant_id=tenant, payload={"tenant_id": tenant},
    )
    memory_cache.set(tenant_id=tenant, key=mem_id, value={"cached": True})
    target_rec = MemoryRecord(
        tenant_id=tenant, user_id="user_1", agent_id=agent.name, workspace_id="ws_1",
        namespace_id=ns.id, owner_id=agent.id, memory_type=MemoryType.EPISODIC,
        content_text="Target relation record", content_hash="hash_target_1",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add(target_rec)
    test_db.flush()
    test_db.add_all([
        MemoryRelationship(
            source_memory_id=mem_id, target_memory_id=target_rec.id,
            relationship_type="relates_to", weight=1.0,
        ),
        DeletionTombstone(
            tenant_id=tenant, memory_id=mem_id, relational_deleted=False, vector_deleted=False,
            cache_deleted=False, graph_deleted=False, turso_deleted=False, status="PENDING",
        ),
    ])
    test_db.commit()

    # Don't replace the real adapter deletion path in this test: the mock store
    # honors tenant metadata and is part of the behavior under test.
    worker_module = importlib.import_module("storage.convergence.tombstone_worker")
    monkeypatch.setattr(worker_module, "turso_replica_enabled", lambda: False)
    tombstone = test_db.query(DeletionTombstone).filter_by(memory_id=mem_id).one()

    assert TombstoneConvergenceWorker(batch_size=10, max_retries=3).process_single_tombstone(
        test_db, tombstone
    )
    test_db.commit()
    assert tombstone.status == "CONVERGED"
    assert all((
        tombstone.relational_deleted,
        tombstone.vector_deleted,
        tombstone.cache_deleted,
        tombstone.graph_deleted,
        tombstone.turso_deleted,
    ))
    assert vector_adapter.delete_embedding(mem_id, tenant_id=tenant) is True
    assert mem_id not in vector_adapter._mock_store
    assert memory_cache.get(tenant_id=tenant, key=mem_id) is None
    assert test_db.query(MemoryRecord).filter_by(id=mem_id).first() is None

def test_tombstone_with_wrong_tenant_does_not_delete_foreign_memory_or_edges(test_db, monkeypatch):
    foreign_tenant, tombstone_tenant = "tenant_foreign_memory", "tenant_wrong_tombstone"
    agent = IdentityService.register_agent(
        test_db, name="foreign_memory_agent", tenant_id=foreign_tenant
    )
    namespace = IdentityService.resolve_namespace(
        test_db, path=f"memora://{foreign_tenant}/wrong-tenant-tombstone",
        owner_agent_id=agent.id, tenant_id=foreign_tenant,
    )
    foreign_record = MemoryRecord(
        tenant_id=foreign_tenant, user_id="foreign_user", agent_id=agent.name,
        workspace_id="foreign_workspace", namespace_id=namespace.id, owner_id=agent.id,
        memory_type=MemoryType.EPISODIC, content_text="Foreign tenant memory must survive",
        content_hash="foreign_memory_wrong_tenant", lifecycle_state=LifecycleState.ACTIVE,
    )
    other_record = MemoryRecord(
        tenant_id=foreign_tenant, user_id="foreign_user", agent_id=agent.name,
        workspace_id="foreign_workspace", namespace_id=namespace.id, owner_id=agent.id,
        memory_type=MemoryType.EPISODIC, content_text="Unrelated foreign tenant memory",
        content_hash="unrelated_foreign_memory_wrong_tenant", lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add_all([foreign_record, other_record])
    test_db.flush()
    incident = MemoryRelationship(
        source_memory_id=foreign_record.id, target_memory_id=other_record.id,
        relationship_type="relates_to", weight=1.0,
    )
    tombstone = DeletionTombstone(
        tenant_id=tombstone_tenant, memory_id=foreign_record.id, relational_deleted=False,
        vector_deleted=False, cache_deleted=False, graph_deleted=False, turso_deleted=False,
        status="PENDING",
    )
    test_db.add_all([incident, tombstone])
    test_db.commit()
    _set_local_deletion_stores(monkeypatch)

    worker = TombstoneConvergenceWorker(batch_size=10, max_retries=3)
    for expected_status in ("PENDING_RETRY", "PENDING_RETRY", "FAILED"):
        receipt = worker.run_convergence_pass(test_db, tenant_id=tombstone_tenant)
        assert receipt["processed"] == 1 and receipt["converged"] == 0
        assert tombstone.status == expected_status
    assert receipt["failed"] == 1 and tombstone.retry_count == 3
    assert not any((tombstone.vector_deleted, tombstone.cache_deleted, tombstone.turso_deleted,
                    tombstone.graph_deleted, tombstone.relational_deleted))
    assert test_db.query(MemoryRecord).filter_by(id=foreign_record.id).one() == foreign_record
    assert test_db.query(MemoryRelationship).filter_by(id=incident.id).one() == incident


def test_run_convergence_pass_batch(test_db, monkeypatch):
    tenant = "tenant_conv_batch"
    agent = IdentityService.register_agent(test_db, name="batch_agent", tenant_id=tenant)
    IdentityService.resolve_namespace(
        test_db, path="memora://batch/ns", owner_agent_id=agent.id, tenant_id=tenant
    )
    _set_local_deletion_stores(monkeypatch)
    tombstones = []
    for index in range(3):
        tombstone = DeletionTombstone(
            tenant_id=tenant, memory_id=f"mem_batch_{index}", relational_deleted=True,
            vector_deleted=False, cache_deleted=False, graph_deleted=True, turso_deleted=True,
            status="PENDING",
        )
        test_db.add(tombstone)
        tombstones.append(tombstone)
    test_db.commit()
    receipt = TombstoneConvergenceWorker(batch_size=50).run_convergence_pass(
        test_db, tenant_id=tenant
    )
    assert receipt["processed"] == 3 and receipt["converged"] == 3 and receipt["failed"] == 0
    for tombstone in tombstones:
        test_db.refresh(tombstone)
        assert tombstone.status == "CONVERGED" and tombstone.vector_deleted and tombstone.cache_deleted


def test_tombstone_exhausts_retries_and_fails(test_db, monkeypatch):
    tombstone = DeletionTombstone(
        tenant_id="tenant_failing", memory_id="mem_fail_99", relational_deleted=False,
        vector_deleted=False, cache_deleted=False, graph_deleted=False, turso_deleted=False,
        status="PENDING", retry_count=2,
    )
    test_db.add(tombstone)
    test_db.commit()
    monkeypatch.setattr(
        vector_adapter, "delete_embedding",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("Simulated Qdrant hard failure")),
    )
    worker = TombstoneConvergenceWorker(batch_size=10, max_retries=3)
    worker.process_single_tombstone(test_db, tombstone)
    test_db.commit()
    assert tombstone.retry_count == 3 and tombstone.status == "FAILED"
    assert not tombstone.is_converged()


def test_graph_cleanup_failure_retries_without_losing_unrelated_edges(test_db, monkeypatch):
    tenant = "tenant_graph_retry"
    tombstone, memory_id, incident_id, unrelated_id = _make_retry_case(test_db, tenant)
    _set_local_deletion_stores(monkeypatch)
    original_delete = Query.delete
    failures_remaining = 1

    def fail_graph_delete_once(query, *args, **kwargs):
        nonlocal failures_remaining
        if query.column_descriptions[0]["entity"] is MemoryRelationship and failures_remaining:
            failures_remaining -= 1
            raise RuntimeError("Injected graph cleanup failure")
        return original_delete(query, *args, **kwargs)

    monkeypatch.setattr(Query, "delete", fail_graph_delete_once)
    worker = TombstoneConvergenceWorker(batch_size=10, max_retries=3)
    assert worker.run_convergence_pass(test_db, tenant_id=tenant)["pending"] == 1
    assert tombstone.status == "PENDING_RETRY" and not tombstone.graph_deleted
    assert tombstone.relational_deleted and not tombstone.is_converged()
    assert test_db.query(MemoryRecord).filter_by(id=memory_id).first() is None
    assert worker.run_convergence_pass(test_db, tenant_id=tenant)["converged"] == 1
    assert tombstone.status == "CONVERGED" and tombstone.graph_deleted
    assert test_db.query(MemoryRelationship).filter_by(id=incident_id).first() is None
    assert test_db.query(MemoryRelationship).filter_by(id=unrelated_id).first() is not None


def test_relational_delete_failure_retries_without_marking_converged(test_db, monkeypatch):
    tenant = "tenant_relational_retry"
    tombstone, memory_id, incident_id, unrelated_id = _make_retry_case(test_db, tenant)
    _set_local_deletion_stores(monkeypatch)
    original_delete = test_db.delete
    failures_remaining = 1

    def fail_memory_delete_once(instance):
        nonlocal failures_remaining
        if isinstance(instance, MemoryRecord) and instance.id == memory_id and failures_remaining:
            failures_remaining -= 1
            raise RuntimeError("Injected relational deletion failure")
        return original_delete(instance)

    monkeypatch.setattr(test_db, "delete", fail_memory_delete_once)
    worker = TombstoneConvergenceWorker(batch_size=10, max_retries=3)
    assert worker.run_convergence_pass(test_db, tenant_id=tenant)["pending"] == 1
    assert tombstone.status == "PENDING_RETRY" and tombstone.graph_deleted
    assert not tombstone.relational_deleted and not tombstone.is_converged()
    assert test_db.query(MemoryRecord).filter_by(id=memory_id).first() is not None
    assert worker.run_convergence_pass(test_db, tenant_id=tenant)["converged"] == 1
    assert tombstone.status == "CONVERGED" and tombstone.relational_deleted
    assert test_db.query(MemoryRelationship).filter_by(id=incident_id).first() is None
    assert test_db.query(MemoryRelationship).filter_by(id=unrelated_id).first() is not None


def test_failed_tombstone_is_not_retried_after_retry_limit(test_db):
    tombstone = DeletionTombstone(
        tenant_id="tenant_terminal", memory_id="mem_terminal", relational_deleted=False,
        vector_deleted=False, cache_deleted=False, graph_deleted=False, turso_deleted=False,
        status="FAILED", retry_count=5,
    )
    test_db.add(tombstone)
    test_db.commit()
    receipt = TombstoneConvergenceWorker(max_retries=5).run_convergence_pass(test_db)
    assert receipt["processed"] == 0 and tombstone.retry_count == 5 and tombstone.status == "FAILED"


def test_graph_sql_failure_rolls_back_savepoint_and_retries(test_db, monkeypatch, caplog):
    tenant = "tenant_graph_sql_retry"
    tombstone, memory_id, incident_id, unrelated_id = _make_retry_case(test_db, tenant)
    _set_local_deletion_stores(monkeypatch)
    caplog.set_level("WARNING")
    original_delete = Query.delete
    failures_remaining = 1

    def fail_graph_delete_once(query, *args, **kwargs):
        nonlocal failures_remaining
        if query.column_descriptions[0]["entity"] is MemoryRelationship and failures_remaining:
            failures_remaining -= 1
            raise RuntimeError("Injected graph cleanup failure")
        return original_delete(query, *args, **kwargs)

    monkeypatch.setattr(Query, "delete", fail_graph_delete_once)
    worker = TombstoneConvergenceWorker(batch_size=10, max_retries=3)
    assert worker.run_convergence_pass(test_db, tenant_id=tenant)["pending"] == 1
    assert tombstone.status == "PENDING_RETRY" and not tombstone.graph_deleted
    assert test_db.query(MemoryRecord).filter_by(id=memory_id).first() is None
    assert worker.run_convergence_pass(test_db, tenant_id=tenant)["converged"] == 1
    assert test_db.query(MemoryRelationship).filter_by(id=incident_id).first() is None
    assert test_db.query(MemoryRelationship).filter_by(id=unrelated_id).first() is not None


def test_relational_sql_failure_rolls_back_savepoint_and_retries(test_db, monkeypatch, caplog):
    tenant = "tenant_record_sql_retry"
    tombstone, memory_id, incident_id, unrelated_id = _make_retry_case(test_db, tenant)
    _set_local_deletion_stores(monkeypatch)
    caplog.set_level("WARNING")
    _install_sql_delete_failure_trigger(
        test_db, "fail_tombstoned_memory_delete", "memory_records",
        "EXISTS (SELECT 1 FROM deletion_tombstones WHERE memory_id = OLD.id)",
    )
    worker = TombstoneConvergenceWorker(batch_size=10, max_retries=3)
    assert worker.run_convergence_pass(test_db, tenant_id=tenant)["pending"] == 1
    assert tombstone.status == "PENDING_RETRY" and not tombstone.relational_deleted
    assert tombstone.status == "PENDING_RETRY" and tombstone.graph_deleted
    assert not tombstone.relational_deleted and test_db.query(MemoryRecord).filter_by(id=memory_id).first()
    test_db.execute(text("DROP TRIGGER fail_tombstoned_memory_delete"))
    test_db.commit()
    assert worker.run_convergence_pass(test_db, tenant_id=tenant)["converged"] == 1
    assert tombstone.status == "CONVERGED" and tombstone.relational_deleted
    assert test_db.query(MemoryRelationship).filter_by(id=incident_id).first() is None
    assert test_db.query(MemoryRelationship).filter_by(id=unrelated_id).first() is not None


def test_sql_delete_failure_does_not_block_other_batch_tombstones(test_db, monkeypatch):
    tenant = "tenant_batch_sql_retry"
    tombstone, memory_id, incident_id, unrelated_id = _make_retry_case(test_db, tenant)
    _set_local_deletion_stores(monkeypatch)
    failed_delete_remaining = 1

    def fail_target_memory_delete(record_id):
        nonlocal failed_delete_remaining
        if record_id == memory_id and failed_delete_remaining:
            failed_delete_remaining -= 1
            return 1
        return 0

    test_db.connection().connection.driver_connection.create_function(
        "fail_target_memory_delete", 1, fail_target_memory_delete
    )
    _install_sql_delete_failure_trigger(
        test_db, "fail_one_batch_memory_delete", "memory_records",
        "fail_target_memory_delete(OLD.id)",
    )
    target_record = test_db.query(MemoryRecord).filter_by(id=memory_id).one()
    other_record = MemoryRecord(
        tenant_id=tenant, user_id=target_record.user_id, agent_id=target_record.agent_id,
        workspace_id=target_record.workspace_id, namespace_id=target_record.namespace_id,
        owner_id=target_record.owner_id, memory_type=target_record.memory_type,
        content_text="Independent batch retry record", content_hash=f"{tenant}_independent_hash",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add(other_record)
    test_db.flush()
    other_tombstone = DeletionTombstone(
        tenant_id=tenant, memory_id=other_record.id, relational_deleted=False,
        vector_deleted=False, cache_deleted=False, graph_deleted=False, turso_deleted=False,
        status="PENDING",
    )
    test_db.add(other_tombstone)
    test_db.commit()
    worker = TombstoneConvergenceWorker(batch_size=10, max_retries=3)
    first_pass = worker.run_convergence_pass(test_db, tenant_id=tenant)
    assert first_pass["processed"] == 2 and first_pass["converged"] == 1 and first_pass["pending"] == 1
    assert tombstone.status == "PENDING_RETRY" and not tombstone.relational_deleted
    assert other_tombstone.status == "CONVERGED"
    assert test_db.query(MemoryRecord).filter_by(id=memory_id).first() is not None
    assert test_db.query(MemoryRecord).filter_by(id=other_record.id).first() is None
    assert test_db.query(MemoryRelationship).filter_by(id=incident_id).first() is None
    assert test_db.query(MemoryRelationship).filter_by(id=unrelated_id).first() is not None
    test_db.execute(text("DROP TRIGGER fail_one_batch_memory_delete"))
    test_db.commit()
    assert worker.run_convergence_pass(test_db, tenant_id=tenant)["converged"] == 1
    assert tombstone.status == "CONVERGED"
    assert test_db.query(MemoryRelationship).filter_by(id=unrelated_id).first() is not None


def test_tombstone_background_worker_lifecycle():
    worker = TombstoneConvergenceWorker(batch_size=5)
    worker.start_background_worker(interval_seconds=0.05)
    assert worker._running
    worker.start_background_worker(interval_seconds=0.05)
    assert worker._running
    worker.stop_background_worker()
    assert not worker._running
    worker.stop_background_worker()
    assert not worker._running
