"""
Regression guard for the search path loading whole entities it never reads.

Profiling a ~280ms search over a 3,000-record corpus put 83% of the time inside
Query.all(), and roughly half of that inside JSON deserialisation - 18,210 decode
calls across three searches. The keyword-scoring pass was loading complete
MemoryRecord entities, so every row dragged its `provenance` and `entities` JSON
columns through the decoder for values the loop never touched.

Asking for only the two columns the loop reads took the same search from 291ms to
22ms with byte-identical output across 8 queries and 75 rows.

The budget and the corpus size are both calibrated against measurements of the
two implementations in this exact fixture, not picked for comfort:

  corpus   with the fix   without it
    1500        6.2ms        25.9ms
    3000        9.4ms        49.8ms
    6000       19.6ms       147.1ms

A bound generous enough never to flake also never fails. Two earlier versions of
this test - a 3.0s budget, then 0.15s at 1,500 rows - passed with the
optimisation reverted, so they guarded nothing. If a future machine makes 0.07s
too tight, raise it, but keep it below the unfixed measurement or the test is
decorative.
"""
import time
import uuid

import pytest

from core.memory.search_service import SearchService
from storage.relational.models import (
    LifecycleState,
    MemoryRecord,
    MemoryType,
    Namespace,
    NamespaceType,
)

#: Sized so the two implementations actually separate. At 1,500 rows the gap is
#: only 6ms vs 26ms and any sane budget passes both; at 6,000 it is 20ms vs 147ms.
CORPUS = 6000
#: Measured in this fixture: 19.6ms with the fix, 147.1ms without it.
BUDGET_SECONDS = 0.07

WORDS = ("compressor seal torque valve pump bearing housing gasket filter inlet "
         "outlet motor flange impeller sensor reading pressure calibration xenon "
         "reactor coolant turbine manifold regulator coupling washer").split()


@pytest.fixture
def seeded(test_db):
    """A corpus large enough for a full-entity load to be visible."""
    from core.identity.service import IdentityService

    agent = IdentityService.register_agent(test_db, name="forge", role="worker")
    ns = IdentityService.create_namespace(
        test_db,
        path="memora://forge/perf",
        ns_type=NamespaceType.PROJECT_PRIVATE,
        agent_id=agent.id,
    )

    rows = []
    for i in range(CORPUS):
        # Every row carries JSON columns, which is the cost being guarded.
        text = " ".join(WORDS[(i * 7 + j) % len(WORDS)] for j in range(12)) + f" ref{i}"
        rows.append(MemoryRecord(
            id=str(uuid.uuid4()),
            tenant_id="default",
            user_id="default_user",
            agent_id=agent.name,
            workspace_id="default_workspace",
            namespace_id=ns.id,
            owner_id=agent.id,
            memory_type=MemoryType.EPISODIC,
            content_text=text,
            content_hash=uuid.uuid4().hex,
            source="api",
            provenance={"source": "api", "trust_level": "candidate", "pad": "x" * 200},
            entities=[WORDS[i % len(WORDS)], WORDS[(i + 3) % len(WORDS)]],
            confidence=0.8,
            importance=0.5,
            lifecycle_state=LifecycleState.ACTIVE,
        ))
    test_db.bulk_save_objects(rows)
    test_db.commit()
    return test_db


def test_keyword_pass_does_not_load_whole_entities(seeded):
    """The scoring loop needs an id and some text, not a hydrated entity."""
    db = seeded
    # Warm once so first-call import and mapper configuration are not timed.
    SearchService.hybrid_search(db, "xenon compressor seal torque",
                                actor_name="forge", tenant_id="default", limit=10)

    start = time.perf_counter()
    results = SearchService.hybrid_search(
        db, "xenon compressor seal torque",
        actor_name="forge", tenant_id="default", limit=10
    )
    elapsed = time.perf_counter() - start

    assert results, "the search returned nothing on a seeded corpus"
    assert elapsed < BUDGET_SECONDS, (
        f"search over {CORPUS} records took {elapsed:.2f}s, above the "
        f"{BUDGET_SECONDS}s budget. The keyword pass is probably loading whole "
        f"MemoryRecord entities again - it only needs id and content_text."
    )


def test_search_still_ranks_the_matching_record_first(seeded):
    """The optimisation must not change what comes back."""
    db = seeded
    from core.identity.service import IdentityService

    agent = IdentityService.get_agent_by_name(db, "forge")
    ns = db.query(Namespace).filter(Namespace.path == "memora://forge/perf").first()
    needle = MemoryRecord(
        id=str(uuid.uuid4()),
        tenant_id="default",
        user_id="default_user",
        agent_id=agent.name,
        workspace_id="default_workspace",
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="the zephyr quartz lantern requires a specific torque setting",
        content_hash=uuid.uuid4().hex,
        source="api",
        provenance={"source": "api", "trust_level": "candidate"},
        entities=["zephyr"],
        confidence=0.9,
        importance=0.5,
        lifecycle_state=LifecycleState.ACTIVE,
    )
    db.add(needle)
    db.commit()

    results = SearchService.hybrid_search(
        db, "zephyr quartz lantern torque",
        actor_name="forge", tenant_id="default", limit=10
    )
    assert results, "the distinctive record was not found at all"
    assert results[0].record.id == needle.id, (
        f"expected the matching record first, got {results[0].record.content_text[:60]!r}"
    )
