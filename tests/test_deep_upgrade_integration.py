"""
Integration tests for Memora Deep Upgrade core system changes:
- Strict tenant isolation
- Supervisor bypass elimination & default deny
- Authorization before pagination
- Durable deletion tombstone lifecycle
- Cursor-based batched decay
- Temporal validity & entity boost in hybrid search
"""
import pytest
from datetime import datetime, timezone, timedelta

from storage.relational.models import (
    Agent,
    Namespace,
    NamespaceType,
    MemoryRecord,
    MemoryType,
    LifecycleState,
    AccessGrant,
    AuditLog,
    DeletionTombstone,
)
from core.memory.schemas import MemoryRecordCreate, MemoryQuery
from core.identity.service import IdentityService
from core.policy.engine import PolicyEngine
from core.memory.service import MemoryService, PermissionDeniedError
from core.memory.search_service import SearchService
from core.lifecycle.decay import MemoryDecayEngine


def test_deep_upgrade_strict_tenant_isolation(test_db):
    """
    Directives 2 & 4: Operations in Tenant A must never be visible, searchable, or accessible in Tenant B.
    """
    # Create agents in two separate tenants
    agent_t1 = IdentityService.register_agent(test_db, "agent_t1", tenant_id="tenant_alpha")
    agent_t2 = IdentityService.register_agent(test_db, "agent_t2", tenant_id="tenant_beta")

    # Namespaces with tenant-scoped paths
    ns_t1 = IdentityService.resolve_namespace(test_db, "memora://tenant_alpha/global", tenant_id="tenant_alpha")
    ns_t2 = IdentityService.resolve_namespace(test_db, "memora://tenant_beta/global", tenant_id="tenant_beta")

    # Memories in Tenant Alpha
    mem_t1 = MemoryService.create_memory(
        test_db,
        MemoryRecordCreate(
            content_text="Confidential alpha tenant intelligence roadmap.",
            owner_id=agent_t1.id,
            namespace_id=ns_t1.id,
            tenant_id="tenant_alpha",
            memory_type=MemoryType.EPISODIC
        ),
        actor_name=agent_t1.name
    )

    # Memories in Tenant Beta
    mem_t2 = MemoryService.create_memory(
        test_db,
        MemoryRecordCreate(
            content_text="Public beta tenant announcements.",
            owner_id=agent_t2.id,
            namespace_id=ns_t2.id,
            tenant_id="tenant_beta",
            memory_type=MemoryType.EPISODIC
        ),
        actor_name=agent_t2.name
    )

    # Query from Tenant Alpha actor: must only see Tenant Alpha memory
    q_alpha = MemoryService.query_memories(
        test_db,
        query=MemoryQuery(tenant_id="tenant_alpha"),
        actor_name="agent_t1"
    )
    alpha_ids = [m.id for m in q_alpha]
    assert mem_t1.id in alpha_ids
    assert mem_t2.id not in alpha_ids

    # Query from Tenant Beta actor: must only see Tenant Beta memory
    q_beta = MemoryService.query_memories(
        test_db,
        query=MemoryQuery(tenant_id="tenant_beta"),
        actor_name="agent_t2"
    )
    beta_ids = [m.id for m in q_beta]
    assert mem_t2.id in beta_ids
    assert mem_t1.id not in beta_ids


def test_deep_upgrade_supervisor_bypass_eliminated(test_db):
    """
    Directive 3: Policy engine default deny. No hardcoded supervisor name/role bypass.
    """
    supervisor = IdentityService.register_agent(test_db, "friday", role="supervisor", tenant_id="default")
    worker = IdentityService.register_agent(test_db, "worker_x", role="worker", tenant_id="default")

    private_ns = IdentityService.resolve_namespace(
        test_db,
        "memora://worker_x/private",
        owner_agent_id=worker.id,
        tenant_id="default"
    )

    # Supervisor attempts to access worker private namespace without grant -> REJECTED
    decision = PolicyEngine.evaluate_access(test_db, supervisor, private_ns, "read")
    assert decision.allowed is False
    assert decision.rule_matched == "RULE_1_PRIVATE_BY_DEFAULT_PROMOTION_REQUIRED"

    # Grant access explicitly -> ALLOWED
    IdentityService.grant_access(
        test_db,
        agent_id=supervisor.id,
        namespace_id=private_ns.id,
        tenant_id="default",
        actions=["read"],
        purpose="Authorized supervisor security audit"
    )
    decision_after = PolicyEngine.evaluate_access(test_db, supervisor, private_ns, "read")
    assert decision_after.allowed is True
    assert decision_after.rule_matched == "RULE_1_EXPLICIT_GRANT_ACCESS"


def test_deep_upgrade_authorization_before_pagination(test_db):
    """
    Directive 5: Authorization evaluated before pagination slicing.
    Unauthorized items must not displace authorized items from a page.
    """
    alice = IdentityService.register_agent(test_db, "alice", tenant_id="tenant_page")
    bob = IdentityService.register_agent(test_db, "bob", tenant_id="tenant_page")

    bob_private = IdentityService.resolve_namespace(test_db, "memora://bob/private", owner_agent_id=bob.id, tenant_id="tenant_page")
    shared_ns = IdentityService.resolve_namespace(test_db, "memora://shared/team", owner_agent_id=bob.id, default_type=NamespaceType.TEAM_SHARED, tenant_id="tenant_page")

    # Explicitly grant Alice read access to shared_ns
    IdentityService.grant_access(
        test_db,
        agent_id=alice.id,
        namespace_id=shared_ns.id,
        tenant_id="tenant_page",
        actions=["read", "query"],
        purpose="Team knowledge sharing"
    )

    # Create 5 private memories belonging to Bob (Alice cannot read them)
    for i in range(5):
        MemoryService.create_memory(
            test_db,
            MemoryRecordCreate(
                content_text=f"Bob private memory {i}",
                owner_id=bob.id,
                namespace_id=bob_private.id,
                tenant_id="tenant_page"
            ),
            actor_name="bob"
        )

    # Create 3 shared memories (Alice can read them)
    shared_ids = []
    for i in range(3):
        m = MemoryService.create_memory(
            test_db,
            MemoryRecordCreate(
                content_text=f"Shared team guideline {i}",
                owner_id=bob.id,
                namespace_id=shared_ns.id,
                tenant_id="tenant_page"
            ),
            actor_name="bob"
        )
        shared_ids.append(m.id)

    # Alice queries with limit=3, offset=0
    # If authorization was after pagination, Bob's 5 private memories would fill the page and Alice would get 0 items!
    # Because authorization happens BEFORE pagination, Alice receives all 3 authorized items!
    alice_results = MemoryService.query_memories(
        test_db,
        query=MemoryQuery(tenant_id="tenant_page", limit=3, offset=0),
        actor_name="alice"
    )
    assert len(alice_results) == 3
    for res in alice_results:
        assert res.id in shared_ids


def test_deep_upgrade_durable_hard_delete(test_db):
    """
    Directive 17: Durable multi-store delete workflow with DeletionTombstone tracking.
    """
    agent = IdentityService.register_agent(test_db, "admin_agent", tenant_id="tenant_del")
    ns = IdentityService.resolve_namespace(test_db, "memora://admin_agent/private", owner_agent_id=agent.id, tenant_id="tenant_del")

    mem = MemoryService.create_memory(
        test_db,
        MemoryRecordCreate(
            content_text="Secret to be durably expunged across all stores.",
            owner_id=agent.id,
            namespace_id=ns.id,
            tenant_id="tenant_del"
        ),
        actor_name="admin_agent"
    )

    mem_id = mem.id

    # Execute durable deletion
    res = MemoryService.delete_memory(
        test_db,
        memory_id=mem_id,
        actor_name="admin_agent",
        hard_delete=True
    )

    assert res["status"] in ["deleted", "converged", "hard_deleted"]
    assert res["memory_id"] == mem_id

    # Relational record is expunged
    rec = test_db.query(MemoryRecord).filter(MemoryRecord.id == mem_id).first()
    assert rec is None

    # DeletionTombstone records the convergence
    tombstone = test_db.query(DeletionTombstone).filter(DeletionTombstone.memory_id == mem_id).first()
    assert tombstone is not None
    assert tombstone.tenant_id == "tenant_del"
    assert tombstone.relational_deleted is True
    assert tombstone.vector_deleted is True


def test_deep_upgrade_cursor_based_decay(test_db):
    """
    Directive 14: Batched, primary-key cursor-based decay processing without full table scan.
    """
    agent = IdentityService.register_agent(test_db, "decay_agent", tenant_id="tenant_decay")
    ns = IdentityService.resolve_namespace(test_db, "memora://decay_agent/private", owner_agent_id=agent.id, tenant_id="tenant_decay")

    # Create old memory with low importance
    old_time = datetime.now(timezone.utc) - timedelta(days=60)
    old_mem = MemoryService.create_memory(
        test_db,
        MemoryRecordCreate(
            content_text="Old ephemeral operational note.",
            owner_id=agent.id,
            namespace_id=ns.id,
            tenant_id="tenant_decay",
            importance=0.20
        ),
        actor_name="decay_agent"
    )
    old_mem.created_at = old_time
    test_db.commit()

    # Create pinned memory (should be skipped)
    pinned_mem = MemoryService.create_memory(
        test_db,
        MemoryRecordCreate(
            content_text="Pinned critical system rule.",
            owner_id=agent.id,
            namespace_id=ns.id,
            tenant_id="tenant_decay",
            importance=0.99,
            provenance={"pinned": True}
        ),
        actor_name="decay_agent"
    )
    pinned_mem.created_at = old_time
    test_db.commit()

    # Run batched decay
    stats = MemoryDecayEngine.apply_time_decay(
        test_db,
        decay_rate_per_day=0.05,
        unverified_threshold_days=7,
        archive_importance_threshold=0.15,
        batch_size=10
    )

    test_db.refresh(old_mem)
    test_db.refresh(pinned_mem)

    assert stats["processed_count"] >= 2
    # Old memory decayed
    assert old_mem.importance < 0.20
    # Pinned memory preserved
    assert pinned_mem.importance == 0.99


def test_deep_upgrade_temporal_validity_and_entity_boost(test_db):
    """
    Directive 12: Hybrid retrieval temporal validity filtering and entity boost.
    """
    agent = IdentityService.register_agent(test_db, "search_agent", tenant_id="tenant_search")
    ns = IdentityService.resolve_namespace(test_db, "memora://search_agent/private", owner_agent_id=agent.id, tenant_id="tenant_search")

    now = datetime.now(timezone.utc)

    # Valid memory with entity match
    m_valid = MemoryService.create_memory(
        test_db,
        MemoryRecordCreate(
            content_text="Apollo spacecraft guidance computer source code architecture.",
            owner_id=agent.id,
            namespace_id=ns.id,
            tenant_id="tenant_search",
            valid_from=now - timedelta(days=1),
            valid_until=now + timedelta(days=1),
            entities=["Apollo", "spacecraft"]
        ),
        actor_name="search_agent"
    )

    # Expired memory
    m_expired = MemoryService.create_memory(
        test_db,
        MemoryRecordCreate(
            content_text="Apollo historical telemetry legacy format.",
            owner_id=agent.id,
            namespace_id=ns.id,
            tenant_id="tenant_search",
            expires_at=now - timedelta(hours=2),
            entities=["Apollo"]
        ),
        actor_name="search_agent"
    )

    results = SearchService.hybrid_search(
        test_db,
        query_text="Apollo spacecraft guidance",
        actor_name="search_agent",
        tenant_id="tenant_search",
        include_expired=False
    )

    retrieved_ids = [r.record.id for r in results]
    assert m_valid.id in retrieved_ids
    assert m_expired.id not in retrieved_ids
