"""
Tests for Memora Automated Horizon Sweeper Worker.
Guarantees autonomous pre-pruning summarization lifecycle across all 10 sovereign agents.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from apps.api.main import app
from storage.convergence.horizon_sweeper import (
    HorizonSweeperWorker,
)
from storage.relational.session import SessionLocal, init_db
from storage.relational.models import (
    Agent,
    Namespace,
    NamespaceType,
    MemoryRecord,
    MemoryType,
    LifecycleState,
    DeletionTombstone,
)
from core.security.provenance import verify_memory_signature


def setup_sweeper_test_data(db: Session, tenant_id: str, now: datetime):
    # Ensure test agent exists
    agent = db.query(Agent).filter(Agent.name == "futuris", Agent.tenant_id == tenant_id).first()
    if not agent:
        agent = Agent(id=f"agent_futuris_{tenant_id}", tenant_id=tenant_id, name="futuris", role="worker")
        db.add(agent)
        db.flush()

    priv_ns = db.query(Namespace).filter(
        Namespace.path == "memora://futuris/private",
        Namespace.tenant_id == tenant_id,
    ).first()
    if not priv_ns:
        priv_ns = Namespace(
            id=f"ns_futuris_priv_{tenant_id}",
            tenant_id=tenant_id,
            path="memora://futuris/private",
            agent_id=agent.id,
            type=NamespaceType.AGENT_PRIVATE,
        )
        db.add(priv_ns)
        db.flush()

    # 1. Expired working memory (35 days old)
    rec1 = MemoryRecord(
        id=f"raw_futuris_work_{tenant_id}",
        tenant_id=tenant_id,
        user_id="default_user",
        agent_id="futuris",
        workspace_id="ws_1",
        device_id="dev_1",
        namespace_id=priv_ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.WORKING,
        content_text=(
            "Goal: Rebalance autonomous crypto index portfolio. "
            "Action: Executed TWAP orders on Binance and Uniswap. "
            "Outcome: Portfolio rebalanced to target weights with <0.02% slippage."
        ),
        content_hash="hash_work_1",
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=now - timedelta(days=35),
        observed_at=now - timedelta(days=35),
        confidence=0.95,
        importance=0.8,
    )
    db.add(rec1)

    # 2. Expired episodic memory (95 days old)
    rec2 = MemoryRecord(
        id=f"raw_futuris_episodic_{tenant_id}",
        tenant_id=tenant_id,
        user_id="default_user",
        agent_id="futuris",
        workspace_id="ws_1",
        device_id="dev_1",
        namespace_id=priv_ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text=(
            "Goal: Handle sudden flash crash liquidity event. "
            "Action: Triggered stop-loss safeguards and halted high-frequency routing. "
            "Outcome: Preserved 99.8% capital allocation across market turbulence."
        ),
        content_hash="hash_epi_1",
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=now - timedelta(days=95),
        observed_at=now - timedelta(days=95),
        confidence=0.99,
        importance=0.9,
    )
    db.add(rec2)

    # 3. Active fresh working memory (5 days old) - should NOT be condensed
    rec3 = MemoryRecord(
        id=f"raw_futuris_fresh_{tenant_id}",
        tenant_id=tenant_id,
        user_id="default_user",
        agent_id="futuris",
        workspace_id="ws_1",
        device_id="dev_1",
        namespace_id=priv_ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.WORKING,
        content_text="Goal: Monitor live funding rates across exchanges. Action: Streaming ticker websocket. Outcome: Real-time telemetry healthy.",
        content_hash="hash_fresh_1",
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=now - timedelta(days=5),
        observed_at=now - timedelta(days=5),
        confidence=0.9,
        importance=0.5,
    )
    db.add(rec3)
    db.commit()


def cleanup_sweeper_test_data(db: Session, tenant_id: str):
    db.query(DeletionTombstone).filter(DeletionTombstone.tenant_id == tenant_id).delete()
    db.query(MemoryRecord).filter(MemoryRecord.tenant_id == tenant_id).delete()
    db.query(Namespace).filter(Namespace.tenant_id == tenant_id).delete()
    db.query(Agent).filter(Agent.tenant_id == tenant_id).delete()
    db.commit()


def test_horizon_sweeper_run_sweep_pass():
    init_db()
    db: Session = SessionLocal()
    tenant_id = "test_sweeper_tenant_1"
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)

    try:
        cleanup_sweeper_test_data(db, tenant_id)
        setup_sweeper_test_data(db, tenant_id, now)

        worker = HorizonSweeperWorker()
        result = worker.run_sweep_pass(db, tenant_id=tenant_id, now=now)

        assert result["status"] == "completed"
        assert result["total_condensed"] == 2
        assert len(result["details"]) == 1
        assert result["details"][0]["condensed_count"] == 2

        # Verify promoted milestones in DB
        milestones = (
            db.query(MemoryRecord)
            .filter(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.memory_type == MemoryType.SEMANTIC,
            )
            .all()
        )
        assert len(milestones) == 2
        for m in milestones:
            assert m.provenance["retention_tier"] == "PERMANENT"
            assert "Milestone [futuris @" in m.content_text
            assert verify_memory_signature(m) is True

        # Verify raw expiring records are deleted
        r1 = db.query(MemoryRecord).filter(MemoryRecord.id == f"raw_futuris_work_{tenant_id}").first()
        r2 = db.query(MemoryRecord).filter(MemoryRecord.id == f"raw_futuris_episodic_{tenant_id}").first()
        assert r1 is None
        assert r2 is None

        # Verify fresh record is preserved
        r3 = db.query(MemoryRecord).filter(MemoryRecord.id == f"raw_futuris_fresh_{tenant_id}").first()
        assert r3 is not None
        assert r3.lifecycle_state == LifecycleState.ACTIVE

        # Verify tombstones emitted for multi-store deletion convergence
        tombstones = db.query(DeletionTombstone).filter(DeletionTombstone.tenant_id == tenant_id).all()
        assert len(tombstones) == 2
        tomb_ids = {t.memory_id for t in tombstones}
        assert f"raw_futuris_work_{tenant_id}" in tomb_ids
        assert f"raw_futuris_episodic_{tenant_id}" in tomb_ids

    finally:
        cleanup_sweeper_test_data(db, tenant_id)
        db.close()


def test_horizon_sweeper_worker_lifecycle():
    worker = HorizonSweeperWorker()
    worker.start_background_worker(interval_seconds=0.1)
    assert worker._running is True
    assert worker._thread is not None
    assert worker._thread.is_alive()

    # Let thread run for 1 pass
    time.sleep(0.2)

    worker.stop_background_worker()
    assert worker._running is False
    assert not worker._thread.is_alive()


def test_horizon_sweeper_api_endpoint():
    init_db()
    db: Session = SessionLocal()
    tenant_id = "test_sweeper_tenant_api"
    now = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)

    os.environ["MEMORA_API_KEY"] = "memora_test_secret_key_123456789012345678"
    os.environ["SENTINEL_API_KEY"] = "sentinel_test_secret_key_12345678901234567"

    try:
        cleanup_sweeper_test_data(db, tenant_id)
        setup_sweeper_test_data(db, tenant_id, now)

        client = TestClient(app)

        # 1. Non-admin caller (sentinel) is rejected with 403 Forbidden
        resp_forbidden = client.post(
            "/v1/memories/sweep-horizon",
            headers={
                "X-Agent-Name": "sentinel",
                "X-API-Key": "sentinel_test_secret_key_12345678901234567",
            },
            json={"tenant_id": tenant_id},
        )
        assert resp_forbidden.status_code == 403

        # 2. Fabric sovereign (memora) succeeds with 200 OK
        resp_ok = client.post(
            "/v1/memories/sweep-horizon",
            headers={
                "X-Agent-Name": "memora",
                "X-API-Key": "memora_test_secret_key_123456789012345678",
            },
            json={"tenant_id": tenant_id},
        )
        assert resp_ok.status_code == 200
        data = resp_ok.json()
        assert data["status"] == "success"
        assert data["actor"] == "memora"
        assert data["sweep_result"]["total_condensed"] == 2

    finally:
        cleanup_sweeper_test_data(db, tenant_id)
        db.close()
