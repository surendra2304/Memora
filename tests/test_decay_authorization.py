"""
Regression tests for POST /v1/memories/decay.

Found by driving the running API rather than from the unit suite. Verified against
a live server on a fresh database: intelx - a non-admin holding no grant on any of
forge's namespaces - posted

    {"decay_rate_per_day": 1e9, "archive_threshold": 999.0,
     "unverified_threshold_days": 0}

and got HTTP 200 with archived_count=10. Every record in the corpus was archived,
9 of them owned by forge. Before: {'active': 10}. After: {'archived': 10}.

Three defects in one path:

1. No authorisation gate. Decay rewrites importance and archives records across
   the whole tenant, but the endpoint took only an actor header. Its siblings
   /v1/resilience/repair and /v1/reflection/run are memora-only.

2. No tenant scoping. MemoryDecayEngine.apply_time_decay accepts a tenant_id that
   MemoryService.apply_decay never passed, so a cycle swept every record in the
   database across all tenants.

3. Unbounded parameters. MemoryDecayRequest had no constraints at all. Since
   decay_factor is `rate * (age - threshold + 1)`, an unbounded rate drives every
   record to the 0.01 floor, and archive_threshold=999 then archives all of them.
"""
from fastapi.testclient import TestClient

from core.identity.service import IdentityService
from storage.relational.models import (
    LifecycleState,
    MemoryRecord,
    MemoryType,
)


ADMIN = {"X-Agent-Name": "memora"}
PEER = {"X-Agent-Name": "intelx"}
OWNER = {"X-Agent-Name": "forge"}


def _aged_record(db, agent_name, *, days=30, importance=0.25):
    from datetime import datetime, timedelta, timezone

    agent = IdentityService.register_agent(db, agent_name)
    ns = IdentityService.get_namespace_by_path(db, f"memora://{agent_name}/private")
    record = MemoryRecord(
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Ephemeral transient web page render cache log.",
        confidence=0.70,
        importance=importance,
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=datetime.now(timezone.utc) - timedelta(days=days),
    )
    db.add(record)
    db.commit()
    return record


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------

def test_a_non_admin_may_not_run_decay(client: TestClient, test_db):
    _aged_record(test_db, "forge")
    resp = client.post("/v1/memories/decay", headers=PEER, json={})
    assert resp.status_code == 403, f"got {resp.status_code}: {resp.text}"


def test_a_refused_decay_leaves_the_corpus_untouched(client: TestClient, test_db):
    record = _aged_record(test_db, "forge")
    before = record.importance

    resp = client.post(
        "/v1/memories/decay", headers=PEER,
        json={"decay_rate_per_day": 1.0, "archive_threshold": 1.0,
              "unverified_threshold_days": 0},
    )
    assert resp.status_code == 403, f"got {resp.status_code}: {resp.text}"

    test_db.expire_all()
    test_db.refresh(record)
    assert record.lifecycle_state == LifecycleState.ACTIVE, (
        "the record was archived by a call that should have been refused"
    )
    assert record.importance == before, "importance was rewritten by a refused call"


def test_the_admin_may_still_run_decay(client: TestClient, test_db):
    _aged_record(test_db, "forge")
    resp = client.post(
        "/v1/memories/decay", headers=ADMIN,
        json={"decay_rate_per_day": 0.05, "unverified_threshold_days": 7,
              "archive_threshold": 0.15},
    )
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"
    assert resp.json()["evaluated_total"] >= 1


# ---------------------------------------------------------------------------
# Parameter bounds
# ---------------------------------------------------------------------------

def test_absurd_decay_parameters_are_rejected(client: TestClient, test_db):
    for body in (
        {"decay_rate_per_day": 1e9},
        {"decay_rate_per_day": -5.0},
        {"archive_threshold": 999.0},
        {"archive_threshold": -1.0},
        {"unverified_threshold_days": -100},
        {"unverified_threshold_days": 99999999},
    ):
        resp = client.post("/v1/memories/decay", headers=ADMIN, json=body)
        assert resp.status_code == 422, (
            f"{body} was accepted with {resp.status_code}; it should be rejected"
        )


def test_bounded_parameters_at_their_extremes_are_accepted(client: TestClient, test_db):
    """The bounds must not be so tight that legitimate maintenance is impossible."""
    resp = client.post(
        "/v1/memories/decay", headers=ADMIN,
        json={"decay_rate_per_day": 1.0, "archive_threshold": 1.0,
              "unverified_threshold_days": 0},
    )
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"


def test_the_documented_defaults_still_work(client: TestClient, test_db):
    resp = client.post("/v1/memories/decay", headers=ADMIN, json={})
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["decay_rate_applied"] == 0.02
    assert body["archive_threshold"] == 0.15


# ---------------------------------------------------------------------------
# Tenant scoping
# ---------------------------------------------------------------------------

def test_a_decay_cycle_is_scoped_to_the_acting_tenant(client: TestClient, test_db):
    """apply_decay never forwarded the engine's tenant_id, so a cycle swept every
    tenant in the database."""
    from datetime import datetime, timedelta, timezone

    agent = IdentityService.register_agent(test_db, "forge")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://forge/private")
    other = MemoryRecord(
        tenant_id="acme",
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="A record belonging to a different tenant.",
        confidence=0.70,
        importance=0.25,
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    test_db.add(other)
    test_db.commit()

    resp = client.post(
        "/v1/memories/decay", headers=ADMIN,
        json={"decay_rate_per_day": 0.05, "unverified_threshold_days": 7,
              "archive_threshold": 0.15},
    )
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.text}"

    test_db.expire_all()
    test_db.refresh(other)
    assert other.lifecycle_state == LifecycleState.ACTIVE, (
        "a record in another tenant was archived by this tenant's decay cycle"
    )
    assert other.importance == 0.25, (
        f"another tenant's importance was rewritten to {other.importance}"
    )
