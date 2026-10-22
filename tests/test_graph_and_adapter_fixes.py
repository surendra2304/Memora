"""
Regression tests for HIGH-1 (adapter auth header names) and HIGH-3 (graph
authorization). Both run against the fail-closed authentication path.
"""
import pytest
from fastapi.testclient import TestClient

from adapters.friday.adapter import FridayAdapter
from core.identity.service import IdentityService


FRIDAY_KEY = "friday-graph-key"
INTELX_KEY = "intelx-graph-key"


@pytest.fixture
def mesh(client: TestClient, test_db, monkeypatch):
    monkeypatch.delenv("MEMORA_ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.setenv("FRIDAY_API_KEY", FRIDAY_KEY)
    monkeypatch.setenv("INTELX_API_KEY", INTELX_KEY)
    IdentityService.register_agent(test_db, "friday")
    IdentityService.register_agent(test_db, "intelx")
    return {
        "friday": {"X-Agent-Name": "friday", "X-API-Key": FRIDAY_KEY},
        "intelx": {"X-Agent-Name": "intelx", "X-API-Key": INTELX_KEY},
    }


# ---------------------------------------------------------------------------
# HIGH-1: the adapter SDK sent header names the server does not read
# ---------------------------------------------------------------------------

def test_adapter_sends_the_header_names_the_server_reads():
    """`X-Agent-Key`/`X-Purpose` were invisible to apps/api/dependencies.py."""
    headers = FridayAdapter(api_key="k")._get_headers(purpose="incident triage")

    assert headers["X-Agent-Name"] == "friday"
    assert headers["X-API-Key"] == "k"
    assert headers["Authorization"] == "Bearer k"
    assert headers["X-Access-Purpose"] == "incident triage"

    assert "X-Agent-Key" not in headers
    assert "X-Purpose" not in headers


def test_adapter_authenticates_against_fail_closed_memora(client: TestClient, mesh):
    """The full adapter round trip must work without anonymous mode.

    Before the fix every adapter call raised
    `MemoraAdapterError: Memora API Error (401): Missing agent credentials`.
    """
    adapter = FridayAdapter(http_client=client, api_key=FRIDAY_KEY)

    written = adapter.write_memory(
        "the reactor coolant valve must be replaced every 30 days",
        purpose="incident triage",
    )
    assert written["id"]
    assert written["memory_type"] == "episodic"

    hits = adapter.search_memories("coolant valve")
    assert len(hits) >= 1
    assert "coolant valve" in hits[0]["content_text"]

    bundle = adapter.get_context("coolant valve maintenance", token_budget=800)
    assert bundle["memories_count"] >= 1


def test_adapter_rejects_a_wrong_credential(client: TestClient, mesh):
    """Fixing the header name must not turn into accepting any credential."""
    adapter = FridayAdapter(http_client=client, api_key="not-the-right-key")
    from adapters.base_adapter import MemoraAdapterError

    with pytest.raises(MemoraAdapterError):
        adapter.write_memory("should never be stored")


# ---------------------------------------------------------------------------
# HIGH-3: graph endpoints had no authorization
# ---------------------------------------------------------------------------

def _two_private_memories(client, mesh, test_db):
    IdentityService.register_agent(test_db, "friday")
    IdentityService.register_agent(test_db, "intelx")
    friday_id = client.post(
        "/v1/memories", headers=mesh["friday"],
        json={"content_text": "friday secret xenon compressor schematic"},
    ).json()["id"]
    intelx_id = client.post(
        "/v1/memories", headers=mesh["intelx"],
        json={"content_text": "intelx private lithium market intelligence"},
    ).json()["id"]
    return friday_id, intelx_id


def test_agent_cannot_link_memories_it_may_not_read(client: TestClient, mesh, test_db):
    friday_id, intelx_id = _two_private_memories(client, mesh, test_db)

    linked = client.post(
        f"/v1/memories/{friday_id}/relationships",
        headers=mesh["intelx"],
        json={"target_memory_id": intelx_id, "relationship_type": "relates_to"},
    )
    assert linked.status_code == 403


def test_graph_traversal_does_not_leak_other_agents_memory_ids(client: TestClient, mesh, test_db):
    """The owner links two of its own memories; a third party must see neither."""
    friday_id, _ = _two_private_memories(client, mesh, test_db)
    second_friday = client.post(
        "/v1/memories", headers=mesh["friday"],
        json={"content_text": "friday xenon compressor maintenance schedule"},
    ).json()["id"]

    link = client.post(
        f"/v1/memories/{friday_id}/relationships",
        headers=mesh["friday"],
        json={"target_memory_id": second_friday, "relationship_type": "relates_to"},
    )
    assert link.status_code == 200

    owner_view = client.get(f"/v1/memories/{friday_id}/graph?max_hops=2", headers=mesh["friday"])
    assert owner_view.status_code == 200
    assert second_friday in owner_view.json()["connected_memory_ids"]

    outsider = client.get(f"/v1/memories/{friday_id}/graph?max_hops=3", headers=mesh["intelx"])
    assert outsider.status_code == 403


def test_self_link_is_a_422_not_an_attribute_error(client: TestClient, mesh, test_db):
    """create_relationship returned None, which the endpoint dereferenced."""
    friday_id, _ = _two_private_memories(client, mesh, test_db)

    resp = client.post(
        f"/v1/memories/{friday_id}/relationships",
        headers=mesh["friday"],
        json={"target_memory_id": friday_id},
    )
    assert resp.status_code == 422
    assert "itself" in resp.json()["detail"]
    assert "NoneType" not in str(resp.json())


def test_dangling_edges_are_rejected(client: TestClient, mesh, test_db):
    """A link to a nonexistent memory must not be persisted.

    The read-access guard runs first and reports 404, which is the accurate
    status for an id that does not exist; GraphService's own guard (422) is the
    backstop for any caller that reaches it directly.
    """
    friday_id, _ = _two_private_memories(client, mesh, test_db)

    resp = client.post(
        f"/v1/memories/{friday_id}/relationships",
        headers=mesh["friday"],
        json={"target_memory_id": "does-not-exist-000"},
    )
    assert resp.status_code == 404
    assert "does-not-exist-000" in resp.json()["detail"]

    # Nothing was persisted for the dangling target.
    graph = client.get(f"/v1/memories/{friday_id}/graph", headers=mesh["friday"]).json()
    assert "does-not-exist-000" not in graph["connected_memory_ids"]


def test_graph_service_guard_rejects_dangling_edges_directly(test_db):
    """The service-level guard stands on its own for non-HTTP callers."""
    from core.memory.graph_service import GraphService, InvalidRelationshipError
    from core.memory.pipeline.write_service import MemoryWriteService

    IdentityService.register_agent(test_db, "friday")
    record = MemoryWriteService.execute_pipeline(
        db=test_db, caller_name="friday", content_text="graph guard probe memory"
    ).record

    with pytest.raises(InvalidRelationshipError):
        GraphService.create_relationship(
            db=test_db, source_memory_id=record.id, target_memory_id="phantom-id"
        )
    with pytest.raises(InvalidRelationshipError):
        GraphService.create_relationship(
            db=test_db, source_memory_id=record.id, target_memory_id=record.id
        )


def test_owner_can_still_link_its_own_memories(client: TestClient, mesh, test_db):
    friday_id, _ = _two_private_memories(client, mesh, test_db)
    other = client.post(
        "/v1/memories", headers=mesh["friday"],
        json={"content_text": "friday xenon compressor torque specification"},
    ).json()["id"]

    resp = client.post(
        f"/v1/memories/{friday_id}/relationships",
        headers=mesh["friday"],
        json={"target_memory_id": other, "relationship_type": "depends_on", "weight": 0.9},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "created"
    assert body["relationship_type"] == "depends_on"
    assert body["weight"] == 0.9


# ---------------------------------------------------------------------------
# MEDIUM-7: re-granting silently cleared the expiry
# ---------------------------------------------------------------------------

def test_regrant_without_a_ttl_preserves_the_existing_expiry(test_db):
    """Changing a grant's actions must not silently make it permanent."""
    from datetime import datetime, timedelta, timezone

    from core.identity.service import IdentityService

    IdentityService.register_agent(test_db, "friday")
    IdentityService.register_agent(test_db, "intelx")
    namespace = IdentityService.resolve_namespace(
        test_db, "memora://friday/private", owner_agent_id=None
    )

    original = IdentityService.grant_access(
        test_db,
        agent_name="intelx",
        namespace_id=namespace.id,
        actions=["read"],
        ttl_hours=1,
    )
    assert original.expires_at is not None
    original_expiry = original.expires_at

    # Re-grant to widen the action list, saying nothing about expiry.
    updated = IdentityService.grant_access(
        test_db,
        agent_name="intelx",
        namespace_id=namespace.id,
        actions=["read", "write"],
    )

    assert updated.expires_at == original_expiry, "expiry must survive an unrelated re-grant"
    assert updated.actions == ["read", "write"]
    assert updated.is_expired() is False

    # An explicit new expiry is still honoured. SQLite round-trips DateTime as
    # naive, so normalise before comparing.
    new_expiry = datetime.now(timezone.utc) + timedelta(hours=48)
    extended = IdentityService.grant_access(
        test_db,
        agent_name="intelx",
        namespace_id=namespace.id,
        actions=["read"],
        expires_at=new_expiry,
    )
    assert extended.expires_at is not None
    stored = extended.expires_at
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=timezone.utc)
    assert abs((stored - new_expiry).total_seconds()) < 5



# ---------------------------------------------------------------------------
# Tenant isolation in graph-assisted search
# ---------------------------------------------------------------------------

def test_graph_neighbor_boost_ignores_cross_tenant_relationships(test_db):
    """Foreign edges must not contribute graph boost to a tenant's search."""
    from storage.relational.models import MemoryRecord, MemoryRelationship, MemoryType, LifecycleState
    from core.memory.graph_service import GraphService

    friday = IdentityService.register_agent(test_db, "friday", tenant_id="tenant-a")
    intelx = IdentityService.register_agent(test_db, "intelx", tenant_id="tenant-b")
    friday_ns = IdentityService.resolve_namespace(
        test_db, "memora://tenant-a/friday/private", owner_agent_id=friday.id,
        tenant_id="tenant-a",
    )
    intelx_ns = IdentityService.resolve_namespace(
        test_db, "memora://tenant-b/intelx/private", owner_agent_id=intelx.id,
        tenant_id="tenant-b",
    )

    seed = MemoryRecord(
        tenant_id="tenant-a", user_id="u-a", agent_id=friday.name,
        workspace_id="w-a", namespace_id=friday_ns.id, owner_id=friday.id,
        memory_type=MemoryType.SEMANTIC, content_text="tenant A seed memory",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    foreign_neighbor = MemoryRecord(
        tenant_id="tenant-b", user_id="u-b", agent_id=intelx.name,
        workspace_id="w-b", namespace_id=intelx_ns.id, owner_id=intelx.id,
        memory_type=MemoryType.SEMANTIC, content_text="tenant B private neighbor",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    same_tenant_neighbor = MemoryRecord(
        tenant_id="tenant-a", user_id="u-a", agent_id=friday.name,
        workspace_id="w-a", namespace_id=friday_ns.id, owner_id=friday.id,
        memory_type=MemoryType.SEMANTIC, content_text="tenant A neighbor memory",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add_all([seed, foreign_neighbor, same_tenant_neighbor])
    test_db.flush()
    test_db.add_all([
        MemoryRelationship(
            source_memory_id=seed.id, target_memory_id=foreign_neighbor.id,
            relationship_type="relates_to", weight=1.0,
        ),
        MemoryRelationship(
            source_memory_id=seed.id, target_memory_id=same_tenant_neighbor.id,
            relationship_type="relates_to", weight=0.5,
        ),
    ])
    test_db.commit()

    tenant_scoped = GraphService.get_graph_neighbors(
        test_db, [seed.id], tenant_id="tenant-a"
    )
    assert same_tenant_neighbor.id in tenant_scoped
    assert foreign_neighbor.id not in tenant_scoped
    assert tenant_scoped[same_tenant_neighbor.id] == 0.5 * 0.15

    # Omitting tenant_id remains available only for compatibility with trusted
    # non-request callers that intentionally need the unscoped neighborhood.
    legacy = GraphService.get_graph_neighbors(test_db, [seed.id])
    assert foreign_neighbor.id in legacy


def test_graph_traversal_enforces_tenant_boundary_even_for_public_namespaces(test_db, monkeypatch):
    """Graph reads must not cross tenant boundaries even if policy allows a public node."""
    from types import SimpleNamespace

    from core.memory.graph_service import GraphService
    from storage.relational.models import (
        LifecycleState,
        MemoryRecord,
        MemoryRelationship,
        MemoryType,
        NamespaceType,
    )

    friday = IdentityService.register_agent(test_db, "friday", tenant_id="tenant-graph-a")
    intelx = IdentityService.register_agent(test_db, "intelx", tenant_id="tenant-graph-b")
    root_namespace = IdentityService.resolve_namespace(
        test_db,
        "memora://friday/private",
        owner_agent_id=friday.id,
        tenant_id="tenant-graph-a",
    )
    same_tenant_namespace = IdentityService.resolve_namespace(
        test_db,
        "memora://friday/projects/graph",
        owner_agent_id=friday.id,
        tenant_id="tenant-graph-a",
    )
    foreign_public_namespace = IdentityService.resolve_namespace(
        test_db,
        "memora://public",
        default_type=NamespaceType.PUBLIC,
        owner_agent_id=intelx.id,
        tenant_id="tenant-graph-b",
    )

    root = MemoryRecord(
        tenant_id="tenant-graph-a",
        user_id="friday-user",
        agent_id="friday",
        workspace_id="graph-test",
        namespace_id=root_namespace.id,
        owner_id=friday.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Tenant A graph root",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    same_tenant_neighbor = MemoryRecord(
        tenant_id="tenant-graph-a",
        user_id="friday-user",
        agent_id="friday",
        workspace_id="graph-test",
        namespace_id=same_tenant_namespace.id,
        owner_id=friday.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Tenant A graph neighbor",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    foreign_public_neighbor = MemoryRecord(
        tenant_id="tenant-graph-b",
        user_id="intelx-user",
        agent_id="intelx",
        workspace_id="graph-test",
        namespace_id=foreign_public_namespace.id,
        owner_id=intelx.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Tenant B public memory",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add_all([root, same_tenant_neighbor, foreign_public_neighbor])
    test_db.flush()
    test_db.add_all([
        MemoryRelationship(
            source_memory_id=root.id,
            target_memory_id=same_tenant_neighbor.id,
            relationship_type="relates_to",
            weight=0.5,
        ),
        # Simulate a legacy/corrupt edge. Read-time traversal must still fail closed.
        MemoryRelationship(
            source_memory_id=root.id,
            target_memory_id=foreign_public_neighbor.id,
            relationship_type="relates_to",
            weight=1.0,
        ),
    ])
    test_db.commit()

    # Defense in depth: enforce the service-level tenant invariant even if a
    # future policy bug incorrectly marks a foreign public namespace readable.
    monkeypatch.setattr(
        "core.memory.graph_service.PolicyEngine.evaluate_access",
        lambda *_args, **_kwargs: SimpleNamespace(allowed=True),
    )

    graph = GraphService.get_connected_memories(
        test_db,
        memory_id=root.id,
        max_hops=2,
        actor=friday,
        purpose="tenant isolation regression",
    )

    assert same_tenant_neighbor.id in graph["connected_memory_ids"]
    assert foreign_public_neighbor.id not in graph["connected_memory_ids"]
    assert all(
        foreign_public_neighbor.id not in {edge["source_id"], edge["target_id"]}
        for edge in graph["edges"]
    )
