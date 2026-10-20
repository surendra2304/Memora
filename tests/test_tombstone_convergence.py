"""
Unit and Integration Tests for Multi-Store Deletion Convergence Worker.
Verifies that unconverged DeletionTombstones are polled and converged
across relational SQL, vector store, cache, graph relationships, and Turso replicas.
"""
from storage.relational.models import (
    DeletionTombstone,
    MemoryRecord,
    MemoryRelationship,
    MemoryType,
    LifecycleState,
)
from storage.convergence.tombstone_worker import TombstoneConvergenceWorker
from storage.vector.qdrant_adapter import vector_adapter
from core.cache.memory_cache import memory_cache
from core.identity.service import IdentityService


def test_tombstone_worker_single_convergence(test_db):
    """Test that a single unconverged tombstone is completely converged across all stores."""
    tenant = "tenant_conv_1"
    agent = IdentityService.register_agent(test_db, name="conv_agent_1", tenant_id=tenant)
    ns = IdentityService.resolve_namespace(test_db, path="memora://conv/ns", owner_agent_id=agent.id, tenant_id=tenant)

    # 1. Insert a memory record
    record = MemoryRecord(
        tenant_id=tenant,
        user_id="user_1",
        agent_id=agent.name,
        workspace_id="ws_1",
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Data destined for convergent hard delete",
        content_hash="hash_conv_1",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add(record)
    test_db.flush()
    mem_id = record.id

    # 2. Add vector embedding to mock store
    vector_adapter.upsert_embedding(
        memory_id=mem_id,
        vector=[0.1] * 1536,
        tenant_id=tenant,
        payload={"tenant_id": tenant},
    )
    assert mem_id in vector_adapter._mock_store

    # 3. Add to cache
    memory_cache.set(tenant_id=tenant, key=mem_id, value={"cached": True})
    assert memory_cache.get(tenant_id=tenant, key=mem_id) is not None

    # 4. Add graph relationship
    target_rec = MemoryRecord(
        tenant_id=tenant,
        user_id="user_1",
        agent_id=agent.name,
        workspace_id="ws_1",
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Target relation record",
        content_hash="hash_target_1",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add(target_rec)
    test_db.flush()

    rel = MemoryRelationship(
        source_memory_id=mem_id,
        target_memory_id=target_rec.id,
        relationship_type="relates_to",
        weight=1.0,
    )
    test_db.add(rel)

    # 5. Create an unconverged tombstone
    tombstone = DeletionTombstone(
        tenant_id=tenant,
        memory_id=mem_id,
        relational_deleted=False,
        vector_deleted=False,
        cache_deleted=False,
        graph_deleted=False,
        turso_deleted=False,
        status="PENDING",
    )
    test_db.add(tombstone)
    test_db.commit()

    worker = TombstoneConvergenceWorker(batch_size=10, max_retries=3)
    is_converged = worker.process_single_tombstone(test_db, tombstone)
    test_db.commit()

    assert is_converged is True
    assert tombstone.status == "CONVERGED"
    assert tombstone.relational_deleted is True
    assert tombstone.vector_deleted is True
    assert tombstone.cache_deleted is True
    assert tombstone.graph_deleted is True
    assert tombstone.turso_deleted is True

    # Verify stores are cleaned
    assert mem_id not in vector_adapter._mock_store
    assert memory_cache.get(tenant_id=tenant, key=mem_id) is None
    assert test_db.query(MemoryRecord).filter_by(id=mem_id).first() is None
    assert test_db.query(MemoryRelationship).filter_by(source_memory_id=mem_id).first() is None


def test_run_convergence_pass_batch(test_db):
    """Test running a convergence pass over a batch of multiple unconverged tombstones."""
    tenant = "tenant_conv_batch"
    agent = IdentityService.register_agent(test_db, name="batch_agent", tenant_id=tenant)
    IdentityService.resolve_namespace(test_db, path="memora://batch/ns", owner_agent_id=agent.id, tenant_id=tenant)

    tombstones = []
    for i in range(3):
        t = DeletionTombstone(
            tenant_id=tenant,
            memory_id=f"mem_batch_{i}",
            relational_deleted=True,
            vector_deleted=False,
            cache_deleted=False,
            graph_deleted=True,
            turso_deleted=True,
            status="PENDING",
        )
        test_db.add(t)
        tombstones.append(t)
    test_db.commit()

    worker = TombstoneConvergenceWorker(batch_size=50)
    receipt = worker.run_convergence_pass(test_db, tenant_id=tenant)

    assert receipt["processed"] == 3
    assert receipt["converged"] == 3
    assert receipt["failed"] == 0

    for t in tombstones:
        test_db.refresh(t)
        assert t.status == "CONVERGED"
        assert t.vector_deleted is True
        assert t.cache_deleted is True


def test_tombstone_exhausts_retries_and_fails(test_db, monkeypatch):
    """Test that a tombstone that continuously fails transitions to FAILED after max_retries."""
    tenant = "tenant_failing"
    t = DeletionTombstone(
        tenant_id=tenant,
        memory_id="mem_fail_99",
        relational_deleted=False,
        vector_deleted=False,
        cache_deleted=False,
        graph_deleted=False,
        turso_deleted=False,
        status="PENDING",
        retry_count=2,
    )
    test_db.add(t)
    test_db.commit()

    # Simulate vector failure
    def failing_vector_delete(*args, **kwargs):
        raise RuntimeError("Simulated Qdrant hard failure")

    monkeypatch.setattr(vector_adapter, "delete_embedding", failing_vector_delete)

    worker = TombstoneConvergenceWorker(batch_size=10, max_retries=3)
    worker.process_single_tombstone(test_db, t)
    test_db.commit()

    assert t.retry_count == 3
    assert t.status == "FAILED"
    assert t.is_converged() is False


def test_tombstone_background_worker_lifecycle():
    """Verify background worker thread starts and stops gracefully."""
    worker = TombstoneConvergenceWorker(batch_size=5)
    worker.start_background_worker(interval_seconds=0.05)
    assert worker._running is True
    # Idempotent start
    worker.start_background_worker(interval_seconds=0.05)
    assert worker._running is True

    worker.stop_background_worker()
    assert worker._running is False
    # Idempotent stop
    worker.stop_background_worker()
    assert worker._running is False
