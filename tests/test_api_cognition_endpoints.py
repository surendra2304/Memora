"""
End-to-end tests for the resilience, collaboration and reflection endpoints.

These prove the wiring, not the services (each service has its own tests). What
matters here is what a service-level test cannot see: that the routes exist, that
the actor comes from the authenticated header rather than the request body, that
the mutating endpoints are admin-only, and that a failure in a dependency is
reported honestly rather than masked.
"""
import pytest

from core.identity.service import IdentityService
from core.memory.pipeline.write_service import MemoryWriteService
from core.resilience.circuit_breaker import circuit_registry
from storage.relational.models import MemoryRecord, NamespaceType


@pytest.fixture(autouse=True)
def _isolate_singletons():
    """circuit_registry and the mock vector store are process-wide."""
    from storage.vector.qdrant_adapter import vector_adapter

    circuit_registry.reset_all()
    vector_adapter._mock_store.clear()
    yield
    circuit_registry.reset_all()
    vector_adapter._mock_store.clear()


def _seed_corpus(db):
    """A small corpus with a recurring theme and a contradiction."""
    for agent in ("friday", "forge", "sentinel", "memora"):
        IdentityService.register_agent(db, agent)
    for agent, text in [
        ("friday", "xenon compressor seal torque must be 42 Nm"),
        ("forge", "xenon compressor seal torque must be 38 Nm"),
        ("sentinel", "xenon compressor seal inspection schedule"),
    ]:
        MemoryWriteService.execute_pipeline(db=db, caller_name=agent, content_text=text)


# ---------------------------------------------------------------------------
# resilience
# ---------------------------------------------------------------------------

def test_circuit_status_is_readable_by_any_authenticated_agent(client):
    response = client.get("/v1/resilience/circuits")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"circuits", "circuit_count", "open_count", "healthy"}
    assert body["healthy"] is True


def test_an_unknown_breaker_is_a_404_not_a_silent_creation(client):
    """Looking a breaker up must not register it as a side effect."""
    response = client.get("/v1/resilience/circuits/does-not-exist")
    assert response.status_code == 404
    listing = client.get("/v1/resilience/circuits").json()
    assert "does-not-exist" not in listing["circuits"]


def test_a_registered_breaker_is_visible_by_name(client, test_db):
    # Register one the way the Qdrant adapter does.
    from storage.vector.qdrant_adapter import vector_adapter

    vector_adapter.upsert_embedding("m-1", [0.1] * 4)

    listing = client.get("/v1/resilience/circuits").json()
    assert "qdrant" in listing["circuits"]

    single = client.get("/v1/resilience/circuits/qdrant")
    assert single.status_code == 200
    assert single.json()["name"] == "qdrant"


def test_repair_is_restricted_to_the_memora_identity(client):
    response = client.post("/v1/resilience/repair", json={"dry_run": True},
                           headers={"X-Agent-Name": "forge"})
    assert response.status_code == 403
    assert "memora" in response.json()["detail"]


def test_the_memora_identity_may_run_a_repair(client, test_db):
    _seed_corpus(test_db)
    response = client.post("/v1/resilience/repair", json={"dry_run": True},
                           headers={"X-Agent-Name": "memora"})
    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert "checks" in body


def test_a_dry_run_repair_changes_nothing(client, test_db):
    _seed_corpus(test_db)
    before = test_db.query(MemoryRecord).count()

    client.post("/v1/resilience/repair", json={"dry_run": True},
                headers={"X-Agent-Name": "memora"})

    assert test_db.query(MemoryRecord).count() == before


def test_repair_preview_is_open_to_any_agent_and_is_always_dry(client, test_db):
    _seed_corpus(test_db)
    response = client.get("/v1/resilience/repair/preview",
                          headers={"X-Agent-Name": "forge"})
    assert response.status_code == 200
    assert response.json()["dry_run"] is True


def test_resetting_an_unknown_breaker_is_a_404(client):
    response = client.post("/v1/resilience/circuits/ghost/reset",
                           json={"reason": "operator says it is back"},
                           headers={"X-Agent-Name": "memora"})
    assert response.status_code == 404
    assert "ghost" not in client.get("/v1/resilience/circuits").json()["circuits"]


def test_an_open_breaker_can_be_reset_by_an_admin(client):
    from storage.vector.qdrant_adapter import vector_adapter

    vector_adapter.upsert_embedding("m-1", [0.1] * 4)
    breaker = circuit_registry.find("qdrant")
    assert breaker is not None
    # Trip it deliberately.
    for _ in range(5):
        try:
            breaker.call(_raise)
        except Exception:
            pass
    assert breaker.state.value == "open"

    response = client.post("/v1/resilience/circuits/qdrant/reset",
                           json={"reason": "dependency restored"},
                           headers={"X-Agent-Name": "memora"})
    assert response.status_code == 200
    assert response.json()["state"] == "closed"


def _raise():
    raise ConnectionError("simulated dependency failure")


def test_reset_is_admin_only(client):
    from storage.vector.qdrant_adapter import vector_adapter

    vector_adapter.upsert_embedding("m-1", [0.1] * 4)
    response = client.post("/v1/resilience/circuits/qdrant/reset",
                           json={"reason": "let me in"},
                           headers={"X-Agent-Name": "intelx"})
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# collaboration
# ---------------------------------------------------------------------------

def test_assistance_returns_candidates_and_usable_material(client, test_db):
    _seed_corpus(test_db)
    IdentityService.create_namespace(
        test_db, "memora://team/shared", NamespaceType.TEAM_SHARED
    )

    response = client.post("/v1/collaboration/assist",
                           json={"query": "xenon compressor seal torque"},
                           headers={"X-Agent-Name": "forge"})
    assert response.status_code == 200
    body = response.json()
    assert body["requester"] == "forge", "actor must come from the header, not the body"
    assert "candidates" in body and "immediately_usable" in body


def test_the_actor_cannot_be_forged_in_the_request_body(client, test_db):
    """A caller acts as itself; there is no requester field to spoof."""
    _seed_corpus(test_db)
    response = client.post(
        "/v1/collaboration/assist",
        json={"query": "xenon compressor", "requester": "friday"},
        headers={"X-Agent-Name": "forge"},
    )
    assert response.status_code == 200
    assert response.json()["requester"] == "forge"


def test_contributing_someone_elses_memory_is_refused(client, test_db):
    _seed_corpus(test_db)
    owned_by_friday = (
        test_db.query(MemoryRecord)
        .filter(
            MemoryRecord.owner_id
            == IdentityService.get_agent_by_name(test_db, "friday").id
        )
        .first()
    )

    response = client.post(
        "/v1/collaboration/contribute",
        json={"memory_id": owned_by_friday.id, "recipient": "sentinel"},
        headers={"X-Agent-Name": "forge"},
    )
    assert response.status_code == 403, "a non-owner was allowed to share a memory"


def test_contributing_your_own_memory_creates_a_scoped_grant(client, test_db):
    _seed_corpus(test_db)
    mine = (
        test_db.query(MemoryRecord)
        .filter(
            MemoryRecord.owner_id
            == IdentityService.get_agent_by_name(test_db, "forge").id
        )
        .first()
    )

    response = client.post(
        "/v1/collaboration/contribute",
        json={"memory_id": mine.id, "recipient": "sentinel", "ttl_hours": 12},
        headers={"X-Agent-Name": "forge"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "contributed"
    assert body["grant_id"], "a grant must actually have been created"
    assert body["expires_at"] is not None, "the grant must be time-boxed"


def test_contributing_a_nonexistent_memory_is_a_404(client, test_db):
    _seed_corpus(test_db)
    response = client.post(
        "/v1/collaboration/contribute",
        json={"memory_id": "no-such-memory", "recipient": "sentinel"},
        headers={"X-Agent-Name": "forge"},
    )
    assert response.status_code == 404


def test_delegation_creates_a_bounded_subagent(client, test_db):
    _seed_corpus(test_db)
    response = client.post(
        "/v1/collaboration/delegate",
        json={"subagent_name": "torque-checker",
              "task_description": "verify seal torque"},
        headers={"X-Agent-Name": "forge"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "delegated"
    # register_subagent names children "<parent>:<child>"
    assert body["subagent"].endswith("torque-checker")

    sub = IdentityService.get_agent_by_name(test_db, body["subagent"])
    assert sub is not None
    assert sub.bounded_scope.startswith("memora://forge/")


# ---------------------------------------------------------------------------
# reflection
# ---------------------------------------------------------------------------

def test_reflection_defaults_to_a_dry_run(client, test_db):
    _seed_corpus(test_db)
    response = client.post("/v1/reflection/run", json={},
                           headers={"X-Agent-Name": "forge"})
    assert response.status_code == 200
    body = response.json()
    assert body["stored"] == 0, "a non-admin must not be able to store insights"
    assert body["insight_count"] > 0


def test_a_non_admin_cannot_store_reflections(client, test_db):
    _seed_corpus(test_db)
    response = client.post("/v1/reflection/run", json={"dry_run": False},
                           headers={"X-Agent-Name": "forge"})
    assert response.status_code == 403


def test_memora_may_store_reflections_and_read_them_back(client, test_db):
    _seed_corpus(test_db)
    run = client.post("/v1/reflection/run", json={"dry_run": False},
                      headers={"X-Agent-Name": "memora"})
    assert run.status_code == 200
    assert run.json()["stored"] > 0

    listing = client.get("/v1/reflection/insights",
                         headers={"X-Agent-Name": "forge"})
    assert listing.status_code == 200
    body = listing.json()
    assert body["count"] > 0
    kinds = {i["kind"] for i in body["insights"]}
    assert "contradiction" in kinds, (
        "the seeded 42 Nm vs 38 Nm conflict should have been recorded"
    )


def test_insights_can_be_filtered_by_kind(client, test_db):
    _seed_corpus(test_db)
    client.post("/v1/reflection/run", json={"dry_run": False},
                headers={"X-Agent-Name": "memora"})

    body = client.get("/v1/reflection/insights?kind=contradiction",
                      headers={"X-Agent-Name": "memora"}).json()
    assert body["count"] > 0
    assert all(i["kind"] == "contradiction" for i in body["insights"])


def test_insights_exclude_ordinary_experience_memories(client, test_db):
    """Only reflection output belongs here, not every EXPERIENCE record."""
    _seed_corpus(test_db)
    MemoryWriteService.execute_pipeline(
        db=test_db, caller_name="friday", content_text="an ordinary experience note"
    )

    client.post("/v1/reflection/run", json={"dry_run": False},
                headers={"X-Agent-Name": "memora"})

    body = client.get("/v1/reflection/insights",
                      headers={"X-Agent-Name": "memora"}).json()
    assert all("ordinary experience note" not in i["summary"] for i in body["insights"])
