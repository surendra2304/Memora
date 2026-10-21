"""
Tests for Memora's Mandatory Retention Policy: Pre-Pruning Summarization (Zero Work Lost).
Guarantees the core invariant: NO WORK DONE BY ANY AGENT MAY EVER BE LOST.
Verifies that expiring working (30d) and episodic (90d) memories are condensed into
compact permanent semantic milestones in memora://<agent_name>/milestones before raw deletion.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from memora_upgrade.models import (
    MemoryItem,
    MemoryKind,
    MemoryScope,
    Lifecycle,
)
from memora_upgrade.store import MemoryStore
from memora_upgrade.consolidation import (
    Consolidator,
    ConsolidationConfig,
    MilestoneCondenser,
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


def test_milestone_condenser_field_extraction():
    text = (
        "Goal: Deploy high-availability Qdrant cluster on Kubernetes. "
        "Action: Configured StatefulSet with 3 replicas and EBS persistent volume claims. "
        "Outcome: Qdrant vector index is fully operational with 0ms downtime."
    )
    meta = {"source": "forge_task"}
    created_at = datetime(2026, 8, 15, 12, 0, 0, tzinfo=timezone.utc)
    summary = MilestoneCondenser.condense(
        MemoryItem(
            id="test_expiring_1",
            tenant_id="default",
            owner_agent_id="forge",
            kind=MemoryKind.WORKING,
            text=text,
            scope=MemoryScope("default", agent_id="forge"),
            confidence=0.95,
            importance=0.8,
            created_at=created_at,
            metadata=meta,
        )
    )

    assert summary.agent_id == "forge"
    assert "Deploy high-availability Qdrant cluster" in summary.goal
    assert "Configured StatefulSet" in summary.action
    assert "Qdrant vector index is fully operational" in summary.outcome
    assert "Milestone [forge @" in summary.summary_text
    assert "Goal was to" in summary.summary_text


def test_consolidator_decide_retention_horizons():
    consolidator = Consolidator(
        ConsolidationConfig(
            working_memory_horizon_days=30.0,
            episodic_memory_horizon_days=90.0,
        )
    )
    now = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Fresh active working memory (10 days old) -> KEEP
    fresh_working = MemoryItem(
        id="m_fresh_working",
        tenant_id="default",
        owner_agent_id="cortex",
        kind=MemoryKind.WORKING,
        text="Investigating API latency.",
        scope=MemoryScope("default", "cortex"),
        confidence=0.9,
        importance=0.5,
        lifecycle=Lifecycle.ACTIVE,
        created_at=now - timedelta(days=10),
    )
    d1 = consolidator.decide(fresh_working, now=now)
    assert d1.action == "KEEP"

    # 2. Expired working memory (35 days old) -> SUMMARIZE_AND_PROMOTE
    expired_working = MemoryItem(
        id="m_expired_working",
        tenant_id="default",
        owner_agent_id="cortex",
        kind=MemoryKind.WORKING,
        text="Investigated API latency and fixed connection pool exhaustion.",
        scope=MemoryScope("default", "cortex"),
        confidence=0.9,
        importance=0.5,
        lifecycle=Lifecycle.ACTIVE,
        created_at=now - timedelta(days=35),
    )
    d2 = consolidator.decide(expired_working, now=now)
    assert d2.action == "SUMMARIZE_AND_PROMOTE"
    assert "30-day retention horizon reached" in d2.reason

    # 3. Expired episodic memory (95 days old) -> SUMMARIZE_AND_PROMOTE
    expired_episodic = MemoryItem(
        id="m_expired_episodic",
        tenant_id="default",
        owner_agent_id="sentinel",
        kind=MemoryKind.EPISODIC,
        text="Goal: Patch CVE-2026-4412. Action: Updated dependencies. Outcome: Vulnerability mitigated.",
        scope=MemoryScope("default", "sentinel"),
        confidence=0.95,
        importance=0.8,
        lifecycle=Lifecycle.ACTIVE,
        created_at=now - timedelta(days=95),
    )
    d3 = consolidator.decide(expired_episodic, now=now)
    assert d3.action == "SUMMARIZE_AND_PROMOTE"
    assert "90-day retention horizon reached" in d3.reason


def test_promote_and_prune_memory_item_store():
    store = MemoryStore()
    consolidator = Consolidator()
    now = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)

    raw_item = MemoryItem(
        id="raw_scratchpad_1",
        tenant_id="default",
        owner_agent_id="stratex",
        kind=MemoryKind.WORKING,
        text="Goal: Execute risk balancing. Action: Re-hedged BTC/ETH delta positions. Outcome: Portfolio variance reduced by 14%.",
        scope=MemoryScope("default", "stratex"),
        confidence=0.95,
        importance=0.7,
        lifecycle=Lifecycle.ACTIVE,
        created_at=now - timedelta(days=40),
    )
    store.put(raw_item)

    # Perform zero-loss pre-pruning summarization
    milestone, purged_raw = consolidator.promote_and_prune_memory_item(raw_item, store, now=now)

    # Invariants verification:
    # 1. Milestone exists in store with SEMANTIC kind, PERMANENT retention tier, ACTIVE lifecycle
    assert milestone.kind == MemoryKind.SEMANTIC
    assert milestone.lifecycle == Lifecycle.ACTIVE
    assert milestone.metadata["retention_tier"] == "PERMANENT"
    assert milestone.metadata["namespace"] == "memora://stratex/milestones"
    assert store.get(milestone.id) is not None

    # 2. Raw verbose item is transitioned to DELETED only after milestone is confirmed
    assert purged_raw.lifecycle == Lifecycle.DELETED
    assert store.get("raw_scratchpad_1").lifecycle == Lifecycle.DELETED


def test_relational_zero_work_lost_consolidation():
    """Verify pre-pruning condensation on relational database authority."""
    init_db()
    db: Session = SessionLocal()
    consolidator = Consolidator()
    now = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)

    try:
        # Create agent & private namespace
        agent = db.query(Agent).filter(Agent.name == "intelx", Agent.tenant_id == "test_retention").first()
        if not agent:
            agent = Agent(id="agent_intelx_test", tenant_id="test_retention", name="intelx", role="worker")
            db.add(agent)
            db.flush()

        priv_ns = db.query(Namespace).filter(
            Namespace.path == "memora://intelx/private",
            Namespace.tenant_id == "test_retention",
        ).first()
        if not priv_ns:
            priv_ns = Namespace(
                id="ns_intelx_priv_test",
                tenant_id="test_retention",
                path="memora://intelx/private",
                agent_id=agent.id,
                type=NamespaceType.AGENT_PRIVATE,
            )
            db.add(priv_ns)
            db.flush()

        # Add 35-day-old working memory
        raw_rec = MemoryRecord(
            id="raw_intelx_work_1",
            tenant_id="test_retention",
            user_id="default_user",
            agent_id="intelx",
            workspace_id="ws_1",
            device_id="dev_1",
            namespace_id=priv_ns.id,
            owner_id=agent.id,
            memory_type=MemoryType.WORKING,
            content_text="Goal: Aggregate arXiv papers on sparse attention. Action: Crawled 45 preprints and extracted benchmarks. Outcome: Compiled comprehensive benchmark dataset.",
            content_hash="mock_hash_1",
            lifecycle_state=LifecycleState.ACTIVE,
            created_at=now - timedelta(days=35),
            observed_at=now - timedelta(days=35),
            confidence=0.9,
            importance=0.6,
        )
        db.add(raw_rec)
        db.commit()

        # Execute relational condensation pass
        milestone_rec, tombstone = consolidator.condense_and_prune_relational_record(db, raw_rec, now=now)
        db.commit()

        # Assertions:
        # 1. Milestone record written with MemoryType.SEMANTIC, PERMANENT retention tier
        assert milestone_rec.memory_type == MemoryType.SEMANTIC
        assert milestone_rec.provenance["retention_tier"] == "PERMANENT"
        assert milestone_rec.provenance["condensed_from_id"] == "raw_intelx_work_1"
        assert milestone_rec.lifecycle_state == LifecycleState.ACTIVE

        # 2. Cryptographic signature present and valid
        assert "hmac_signature" in milestone_rec.provenance
        assert verify_memory_signature(milestone_rec) is True

        # 3. Milestones namespace created: memora://intelx/milestones
        milestones_ns = db.query(Namespace).filter(Namespace.id == milestone_rec.namespace_id).first()
        assert milestones_ns is not None
        assert milestones_ns.path == "memora://intelx/milestones"

        # 4. Raw memory is deleted from memory_records
        raw_check = db.query(MemoryRecord).filter(MemoryRecord.id == "raw_intelx_work_1").first()
        assert raw_check is None

        # 5. Deletion tombstone is emitted for multi-store convergence
        tomb_check = db.query(DeletionTombstone).filter(DeletionTombstone.memory_id == "raw_intelx_work_1").first()
        assert tomb_check is not None
        assert tomb_check.relational_deleted is True

    finally:
        # Clean up test tenant artifacts
        db.query(DeletionTombstone).filter(DeletionTombstone.tenant_id == "test_retention").delete()
        db.query(MemoryRecord).filter(MemoryRecord.tenant_id == "test_retention").delete()
        db.query(Namespace).filter(Namespace.tenant_id == "test_retention").delete()
        db.query(Agent).filter(Agent.tenant_id == "test_retention").delete()
        db.commit()
        db.close()
