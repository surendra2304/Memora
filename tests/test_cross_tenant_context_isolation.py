"""
Regression tests for cross-tenant leakage in search and context building.

Two defects, the second exposed by making agent names unique per tenant:

1. `ContextBuilderService.build_context_bundle` resolved the actor correctly by
   id but then passed only `actor.name` down to SearchService.hybrid_search,
   which re-resolved it with an unscoped get_agent_by_name. Once two tenants
   could both own an agent named "forge", that lookup bound the request to
   whichever forge came first and returned the other tenant's memories.

2. The predictive prefetch queried MemoryRecord with no tenant filter and an
   unbounded .all(), loading every tenant's EXPERIENCE/PROCEDURAL rows into
   Python and tokenising each one on every context request.

Verified before the fix: a globex request for "xenon compressor torque drift"
returned acme's memory (tenant_id == "acme" inside the bundle).
"""
import pytest
from sqlalchemy.orm import Query

from core.identity.service import IdentityService
from core.memory.context import builder as builder_module
from core.memory.context.builder import ContextBuilderService
from core.memory.pipeline.write_service import MemoryWriteService
from core.memory.search_service import SearchService
from storage.relational.models import MemoryType

SECRET_TEXT = "xenon compressor torque drift causes seal failure"


@pytest.fixture
def two_tenants(test_db):
    """acme owns a memory; globex owns a same-named agent and no memory."""
    IdentityService.register_agent(test_db, "forge", tenant_id="acme")
    MemoryWriteService.execute_pipeline(
        test_db,
        caller_name="forge",
        tenant_id="acme",
        content_text=SECRET_TEXT,
        memory_type=MemoryType.EXPERIENCE,
    )
    acme = IdentityService.get_agent_by_name(test_db, "forge", tenant_id="acme")
    globex = IdentityService.register_agent(test_db, "forge", tenant_id="globex")
    assert acme.id != globex.id
    return test_db, acme, globex


def test_a_same_named_agent_in_another_tenant_gets_nothing(two_tenants):
    """The headline leak: globex's forge must not read acme's memory."""
    db, _acme, globex = two_tenants

    bundle = ContextBuilderService.build_context_bundle(
        db, agent_id_or_name=globex.id, task_query=SECRET_TEXT
    )
    tenants = {m["tenant_id"] for m in bundle.memories}
    assert "acme" not in tenants, f"cross-tenant leak: {tenants}"


def test_search_scoped_to_a_tenant_excludes_other_tenants(two_tenants):
    db, _acme, globex = two_tenants

    results = SearchService.hybrid_search(
        db, query_text=SECRET_TEXT, actor_name=globex.name, tenant_id="globex"
    )
    assert all(r.record.tenant_id == "globex" for r in results), [
        r.record.tenant_id for r in results
    ]


def test_the_owner_tenant_can_still_read_its_own_memory(two_tenants):
    """Guard against over-correcting into denying legitimate access."""
    db, acme, _globex = two_tenants

    bundle = ContextBuilderService.build_context_bundle(
        db, agent_id_or_name=acme.id, task_query=SECRET_TEXT
    )
    tenants = {m["tenant_id"] for m in bundle.memories}
    assert tenants == {"acme"}, f"owner lost access to its own memory: {tenants}"


def test_neither_search_nor_prefetch_loads_foreign_tenant_rows(two_tenants):
    """Rows from another tenant must not be read at all, not merely filtered out."""
    db, _acme, globex = two_tenants

    loaded_tenants = set()
    real_all = Query.all

    def spy_all(self):
        rows = real_all(self)
        for row in rows:
            tenant = getattr(row, "tenant_id", None)
            if tenant is not None:
                loaded_tenants.add(tenant)
        return rows

    Query.all = spy_all
    try:
        ContextBuilderService.build_context_bundle(
            db, agent_id_or_name=globex.id, task_query=SECRET_TEXT
        )
    finally:
        Query.all = real_all

    assert "acme" not in loaded_tenants, (
        f"a foreign tenant's rows were read: {loaded_tenants}"
    )


def test_the_prefetch_scan_is_bounded():
    """The prefetch tokenises every row in Python, so the scan must be capped."""
    assert builder_module._PREFETCH_SCAN_LIMIT > 0


def test_policy_is_evaluated_against_the_right_tenant_actor(two_tenants):
    """A same-named agent in another tenant must not drive the policy decision.

    Without tenant-scoped actor resolution, hybrid_search resolved "forge"
    unscoped and evaluated the policy against acme's forge, so globex's forge —
    which holds the grant — was denied its own tenant's memory.
    """
    db, _acme, globex = two_tenants

    sentinel = IdentityService.register_agent(db, "sentinel", tenant_id="globex")
    result = MemoryWriteService.execute_pipeline(
        db,
        caller_name="sentinel",
        tenant_id="globex",
        target_namespace_path="memora://sentinel/shared",
        content_text="globex shared note about xenon compressor calibration",
        memory_type=MemoryType.EPISODIC,
    )
    namespace = result.record.namespace

    # Only globex's forge is granted access.
    IdentityService.grant_access(
        db,
        agent_id=globex.id,
        namespace_id=namespace.id,
        actions=["read", "query"],
        tenant_id="globex",
    )

    hits = SearchService.hybrid_search(
        db,
        query_text="globex shared note xenon compressor calibration",
        actor_name="forge",
        tenant_id="globex",
    )
    assert len(hits) == 1, (
        "globex.forge holds the grant but was denied — the policy decision was "
        f"evaluated against the wrong tenant's actor (got {len(hits)} hits)"
    )
    assert hits[0].record.tenant_id == "globex"
    assert sentinel.id != globex.id
