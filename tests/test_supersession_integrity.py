"""
Regression tests for supersession-graph integrity.

Found by driving the running API through stateful lifecycle sequences rather than
from the unit suite. Three defects, each accepted with HTTP 200 and each
confirmed in the database afterwards:

1. A memory could supersede itself. The row ended up lifecycle_state='superseded'
   with superseded_by_id pointing at its own id - dead, with no successor to
   redirect a reader to, and any consumer walking the chain loops on it forever.

2. A->B followed by B->A was accepted, leaving both records dead and pointing at
   each other. Neither is reachable as live and the chain has no end.

3. Promoting an already-superseded record set lifecycle_state back to 'verified'
   while leaving superseded_by_id intact. The record was then simultaneously live
   and dead: it answered recall as verified while still redirecting readers
   elsewhere. Observed as lifecycle_state='verified' alongside a non-null
   superseded_by_id.
"""
import pytest
from fastapi.testclient import TestClient

from core.memory.service import MemoryService
from storage.relational.models import LifecycleState, MemoryRecord


FORGE_KEY = "forge-lifecycle-key"


@pytest.fixture
def forge(client: TestClient, monkeypatch):
    monkeypatch.delenv("MEMORA_ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.setenv("FORGE_API_KEY", FORGE_KEY)
    return {"X-Agent-Name": "forge", "X-API-Key": FORGE_KEY}


def _store(client, headers, text):
    resp = client.post("/v1/memories", headers=headers, json={"content_text": text})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _supersede(client, headers, old_id, new_id):
    return client.post(f"/v1/memories/{old_id}/supersede", headers=headers,
                       json={"new_memory_id": new_id})


def _promote(client, headers, memory_id):
    return client.post(f"/v1/memories/{memory_id}/promote", headers=headers,
                       json={"verification_evidence": ["directly observed"]})


# ---------------------------------------------------------------------------
# Supersession
# ---------------------------------------------------------------------------

def test_a_memory_cannot_supersede_itself(client, forge):
    mid = _store(client, forge, "self supersede attempt")
    resp = _supersede(client, forge, mid, mid)
    assert resp.status_code == 409, f"got {resp.status_code}: {resp.text}"

    fetched = client.get(f"/v1/memories/{mid}", headers=forge).json()
    assert fetched["lifecycle_state"] != "superseded", (
        "the record was killed by an attempt that should have been refused"
    )


def test_a_direct_supersession_cycle_is_refused(client, forge):
    a = _store(client, forge, "cycle node A")
    b = _store(client, forge, "cycle node B")
    assert _supersede(client, forge, a, b).status_code == 200

    resp = _supersede(client, forge, b, a)
    assert resp.status_code == 409, f"got {resp.status_code}: {resp.text}"
    assert "cycle" in resp.text.lower()


def test_a_transitive_supersession_cycle_is_refused(client, forge):
    """A->B->C, then C->A would close the loop three records long."""
    a = _store(client, forge, "transitive A")
    b = _store(client, forge, "transitive B")
    c = _store(client, forge, "transitive C")
    assert _supersede(client, forge, a, b).status_code == 200
    assert _supersede(client, forge, b, c).status_code == 200

    resp = _supersede(client, forge, c, a)
    assert resp.status_code == 409, f"got {resp.status_code}: {resp.text}"


def test_a_legitimate_supersession_chain_still_works(client, forge):
    a = _store(client, forge, "chain A")
    b = _store(client, forge, "chain B")
    c = _store(client, forge, "chain C")

    assert _supersede(client, forge, a, b).status_code == 200
    assert _supersede(client, forge, b, c).status_code == 200

    states = {
        name: client.get(f"/v1/memories/{mid}", headers=forge).json()["lifecycle_state"]
        for name, mid in (("A", a), ("B", b), ("C", c))
    }
    # Supersession makes the winner active. Only explicit promotion makes a
    # record verified - conflating the two was the bug behind the zombie state.
    assert states == {"A": "superseded", "B": "superseded", "C": "active"}, states


def test_supersession_conflict_is_not_a_server_error(client, forge):
    """These escaped as an unhandled 500 before the route mapped ValueError."""
    mid = _store(client, forge, "status mapping check")
    resp = _supersede(client, forge, mid, mid)
    assert resp.status_code != 500, f"server error leaked: {resp.text}"


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------

def test_a_superseded_record_cannot_be_promoted(client, forge):
    a = _store(client, forge, "to be superseded")
    b = _store(client, forge, "its successor")
    assert _supersede(client, forge, a, b).status_code == 200

    resp = _promote(client, forge, a)
    assert resp.status_code == 422, f"got {resp.status_code}: {resp.text}"


def test_promotion_never_leaves_a_record_live_and_superseded(client, forge, test_db):
    """The exact inconsistency observed in the database."""
    a = _store(client, forge, "zombie candidate")
    b = _store(client, forge, "zombie successor")
    assert _supersede(client, forge, a, b).status_code == 200
    _promote(client, forge, a)

    test_db.expire_all()
    zombies = test_db.query(MemoryRecord).filter(
        MemoryRecord.superseded_by_id.isnot(None),
        MemoryRecord.lifecycle_state != LifecycleState.SUPERSEDED,
    ).count()
    assert zombies == 0, f"{zombies} record(s) are live yet superseded"


def test_promoting_the_live_successor_still_works(client, forge):
    a = _store(client, forge, "predecessor")
    b = _store(client, forge, "successor to promote")
    assert _supersede(client, forge, a, b).status_code == 200

    resp = _promote(client, forge, b)
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"
    assert resp.json()["lifecycle_state"] == "verified"


def test_promotion_of_an_ordinary_record_is_unaffected(client, forge):
    mid = _store(client, forge, "plain promotion")
    resp = _promote(client, forge, mid)
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Service-level invariants
# ---------------------------------------------------------------------------

def test_no_self_referential_supersession_is_persisted(client, forge, test_db):
    a = _store(client, forge, "invariant A")
    b = _store(client, forge, "invariant B")
    _supersede(client, forge, a, a)
    _supersede(client, forge, a, b)
    _supersede(client, forge, b, a)

    test_db.expire_all()
    rows = test_db.query(MemoryRecord).filter(
        MemoryRecord.id == MemoryRecord.superseded_by_id
    ).count()
    assert rows == 0, f"{rows} record(s) supersede themselves"


def test_service_rejects_self_supersession_with_a_clear_message(test_db):
    """The guard lives in the service, so it protects every caller, not just HTTP.

    Internal callers such as the task envelope drive MemoryService directly and
    never pass through the route, so a router-only check would leave them exposed.
    """
    from core.identity.service import IdentityService
    from core.memory.pipeline.write_service import MemoryWriteService

    IdentityService.register_agent(test_db, name="forge", role="worker")
    written = MemoryWriteService.execute_pipeline(
        db=test_db, content_text="service level A", actor_name="forge", source="api"
    )
    mid = written.record.id

    with pytest.raises(ValueError, match="cannot supersede itself"):
        MemoryService.supersede_memory(
            db=test_db, old_memory_id=mid, new_memory_id=mid, actor_name="forge"
        )
