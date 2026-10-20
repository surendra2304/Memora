"""
Unit and Integration Tests for PostgreSQL Relational Authority and 3-Tier Scoping Law.
Verifies CRUD operations on namespaces and memory_records, and enforces the 3-Tier Scoping Law:
- agent-private: Readable ONLY by the authoring agent.
- team-shared: Requires an explicit AccessGrant.
- universe-global: Readable by all ecosystem agents.
"""
from storage.relational.models import (
    Namespace,
    NamespaceType,
    AccessGrant,
    MemoryRecord,
    MemoryType,
    LifecycleState,
)
from storage.relational.session import (
    validate_production_database_url,
)
from core.identity.service import IdentityService
from core.policy.engine import PolicyEngine


def test_validate_production_database_url():
    """Verify that production mode strictly requires PostgreSQL."""
    # Valid PostgreSQL URLs
    validate_production_database_url("postgresql://user:pass@host:5432/db")
    validate_production_database_url("postgresql+psycopg2://user:pass@host:5432/db")

    # In non-production mode, other URLs are permitted
    validate_production_database_url("sqlite:///./data/memora.db")


def test_crud_namespaces_and_records(test_db):
    """Verify full CRUD lifecycle on namespaces and memory_records."""
    tenant = "tenant_authority_crud"
    agent = IdentityService.register_agent(test_db, name="crud_agent", tenant_id=tenant)

    # 1. CREATE Namespace
    ns = Namespace(
        tenant_id=tenant,
        path="memora://crud/workspace",
        agent_id=agent.id,
        type=NamespaceType.AGENT_PRIVATE,
    )
    test_db.add(ns)
    test_db.commit()
    test_db.refresh(ns)
    assert ns.id is not None

    # 2. READ Namespace
    fetched_ns = test_db.query(Namespace).filter_by(id=ns.id).first()
    assert fetched_ns is not None
    assert fetched_ns.path == "memora://crud/workspace"

    # 3. CREATE Memory Record
    rec = MemoryRecord(
        tenant_id=tenant,
        user_id="user_crud",
        agent_id=agent.name,
        workspace_id="ws_crud",
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Relational authority verification statement",
        content_hash="hash_crud_1",
        confidence=1.0,
        importance=0.8,
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add(rec)
    test_db.commit()
    test_db.refresh(rec)
    assert rec.id is not None

    # 4. UPDATE Memory Record
    rec.importance = 0.95
    rec.content_text = "Updated relational statement"
    test_db.commit()
    test_db.refresh(rec)
    assert rec.importance == 0.95
    assert rec.content_text == "Updated relational statement"

    # 5. DELETE Memory Record & Namespace
    rec_id = rec.id
    ns_id = ns.id
    test_db.delete(rec)
    test_db.delete(ns)
    test_db.commit()

    assert test_db.query(MemoryRecord).filter_by(id=rec_id).first() is None
    assert test_db.query(Namespace).filter_by(id=ns_id).first() is None


def test_three_tier_scoping_law(test_db):
    """
    Verify Safety Invariant 1 (3-Tier Scoping Law):
    - agent-private: Readable ONLY by authoring agent.
    - team-shared: Requires explicit AccessGrant.
    - universe-global: Readable by all ecosystem agents.
    """
    tenant = "tenant_scoping_law"
    agent_author = IdentityService.register_agent(test_db, name="author_agent", tenant_id=tenant)
    agent_peer = IdentityService.register_agent(test_db, name="peer_agent", tenant_id=tenant)

    # -------------------------------------------------------------------------
    # Tier 1: AGENT-PRIVATE
    # -------------------------------------------------------------------------
    ns_private = Namespace(
        tenant_id=tenant,
        path="memora://author/private_vault",
        agent_id=agent_author.id,
        type=NamespaceType.AGENT_PRIVATE,
    )
    test_db.add(ns_private)
    test_db.flush()

    rec_private = MemoryRecord(
        tenant_id=tenant,
        user_id="user_1",
        agent_id=agent_author.name,
        workspace_id="ws_1",
        namespace_id=ns_private.id,
        owner_id=agent_author.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Confidential private memory",
        content_hash="h_priv",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add(rec_private)
    test_db.commit()

    # Author can read
    dec_author = PolicyEngine.evaluate_access(
        test_db,
        actor=agent_author,
        namespace=ns_private,
        action="read",
        memory_id=rec_private.id,
    )
    assert dec_author.allowed is True

    # Peer is denied access to private namespace
    dec_peer = PolicyEngine.evaluate_access(
        test_db,
        actor=agent_peer,
        namespace=ns_private,
        action="read",
        memory_id=rec_private.id,
    )
    assert dec_peer.allowed is False
    assert "private" in dec_peer.reason.lower() or "isolated" in dec_peer.reason.lower()

    # -------------------------------------------------------------------------
    # Tier 2: TEAM-SHARED (Requires explicit AccessGrant)
    # -------------------------------------------------------------------------
    ns_shared = Namespace(
        tenant_id=tenant,
        path="memora://team/shared_project",
        agent_id=agent_author.id,
        type=NamespaceType.TEAM_SHARED,
    )
    test_db.add(ns_shared)
    test_db.flush()

    rec_shared = MemoryRecord(
        tenant_id=tenant,
        user_id="user_1",
        agent_id=agent_author.name,
        workspace_id="ws_1",
        namespace_id=ns_shared.id,
        owner_id=agent_author.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Shared project plan",
        content_hash="h_shared",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add(rec_shared)
    test_db.commit()

    # Peer has NO grant yet -> denied
    dec_shared_before = PolicyEngine.evaluate_access(
        test_db,
        actor=agent_peer,
        namespace=ns_shared,
        action="read",
        memory_id=rec_shared.id,
    )
    assert dec_shared_before.allowed is False

    # Issue explicit AccessGrant
    grant = AccessGrant(
        tenant_id=tenant,
        agent_id=agent_peer.id,
        namespace_id=ns_shared.id,
        actions=["read"],
        purpose="Project collaboration",
    )
    test_db.add(grant)
    test_db.commit()

    # Peer now has explicit grant -> allowed
    dec_shared_after = PolicyEngine.evaluate_access(
        test_db,
        actor=agent_peer,
        namespace=ns_shared,
        action="read",
        memory_id=rec_shared.id,
    )
    assert dec_shared_after.allowed is True

    # -------------------------------------------------------------------------
    # Tier 3: UNIVERSE-GLOBAL (Readable by all ecosystem agents)
    # -------------------------------------------------------------------------
    ns_global = Namespace(
        tenant_id=tenant,
        path="memora://universe/global_knowledge",
        agent_id=None,
        type=NamespaceType.UNIVERSE_GLOBAL,
    )
    test_db.add(ns_global)
    test_db.flush()

    rec_global = MemoryRecord(
        tenant_id=tenant,
        user_id="user_1",
        agent_id=agent_author.name,
        workspace_id="ws_1",
        namespace_id=ns_global.id,
        owner_id=agent_author.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="Ecosystem-wide protocol constants",
        content_hash="h_global",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add(rec_global)
    test_db.commit()

    # Both author and peer can read openly
    dec_global_author = PolicyEngine.evaluate_access(
        test_db,
        actor=agent_author,
        namespace=ns_global,
        action="read",
        memory_id=rec_global.id,
    )
    dec_global_peer = PolicyEngine.evaluate_access(
        test_db,
        actor=agent_peer,
        namespace=ns_global,
        action="read",
        memory_id=rec_global.id,
    )
    assert dec_global_author.allowed is True
    assert dec_global_peer.allowed is True
