"""
Regression tests for authorisation on POST /namespaces.

The write pipeline was gated against namespace land-grabbing, but this route was
a second door onto the same table and was left open. Found by driving the running
API rather than reading the code:

  * forge POSTed agent_name="friday" and got 201 for a namespace owned by
    friday, without friday's involvement. Ownership of a namespace decides who
    may administer its access grants, so attributing one to an unwilling owner is
    not harmless bookkeeping.
  * Naming an agent that did not exist fell through and silently attributed the
    namespace to the caller instead - the request asked for one owner and the row
    recorded another.
  * Because create_namespace returns the row that already exists rather than
    re-owning it, any caller could POST an arbitrary path and receive that
    namespace's full record, id and owner included, with a 201 Created for
    something it had not created.

These run fail-closed (MEMORA_ALLOW_ANONYMOUS_DEV removed) because anonymous dev
mode authenticates every caller as the same identity and cannot express a
cross-agent claim at all.
"""
import pytest
from fastapi.testclient import TestClient

from core.identity.service import IdentityService


FRIDAY_KEY = "friday-ns-key"
FORGE_KEY = "forge-ns-key"
STRATEX_KEY = "stratex-ns-key"
MEMORA_KEY = "memora-ns-key"


@pytest.fixture
def mesh(client: TestClient, monkeypatch):
    """Four provisioned mesh agents talking to a fail-closed Memora."""
    monkeypatch.delenv("MEMORA_ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.setenv("FRIDAY_API_KEY", FRIDAY_KEY)
    monkeypatch.setenv("FORGE_API_KEY", FORGE_KEY)
    monkeypatch.setenv("STRATEX_API_KEY", STRATEX_KEY)
    monkeypatch.setenv("MEMORA_API_KEY", MEMORA_KEY)
    return {
        "friday": {"X-Agent-Name": "friday", "X-API-Key": FRIDAY_KEY},
        "forge": {"X-Agent-Name": "forge", "X-API-Key": FORGE_KEY},
        "stratex": {"X-Agent-Name": "stratex", "X-API-Key": STRATEX_KEY},
        "memora": {"X-Agent-Name": "memora", "X-API-Key": MEMORA_KEY},
    }


def _bootstrap(client, headers, name):
    """Register an agent the way the mesh does - by having it act."""
    resp = client.post("/v1/memories", headers=headers,
                       json={"content_text": f"{name} bootstrap"})
    assert resp.status_code == 201, f"could not bootstrap {name}: {resp.text}"


def _create(client, headers, path, ns_type="agent-private", **extra):
    return client.post("/namespaces", headers=headers,
                       json={"path": path, "type": ns_type, **extra})


@pytest.fixture
def provisioned(client, mesh):
    _bootstrap(client, mesh["friday"], "friday")
    _bootstrap(client, mesh["forge"], "forge")
    _bootstrap(client, mesh["stratex"], "stratex")
    return mesh


# ---------------------------------------------------------------------------
# What must be refused
# ---------------------------------------------------------------------------

def test_caller_cannot_name_another_agent_as_owner(client, provisioned):
    """Ownership decides who administers a namespace's grants."""
    mesh = provisioned
    resp = _create(client, mesh["forge"], "memora://forge/owned-by-friday",
                   agent_name="friday")
    assert resp.status_code == 403, f"got {resp.status_code}: {resp.text}"


def test_caller_cannot_create_under_another_agents_root(client, provisioned):
    """The same land-grab the write pipeline blocks, attempted through this door."""
    mesh = provisioned
    resp = _create(client, mesh["forge"], "memora://stratex/staging")
    assert resp.status_code == 403, f"got {resp.status_code}: {resp.text}"
    assert "stratex" in resp.text


def test_existing_foreign_namespace_is_not_handed_back(client, provisioned):
    """A non-owner must not receive another agent's namespace record.

    create_namespace returns the existing row rather than re-owning it, so this
    was never an ownership theft - but it answered 201 Created for something the
    caller did not create, and disclosed the namespace id and owner for any path
    the caller cared to name.
    """
    mesh = provisioned
    resp = _create(client, mesh["forge"], "memora://friday/private")
    assert resp.status_code == 409, f"got {resp.status_code}: {resp.text}"
    body = resp.text
    assert "agent_id" not in body, f"namespace record disclosed: {body}"


def test_naming_an_unknown_agent_is_reported_not_silently_reassigned(client, provisioned):
    """The request asked for one owner; the row must not record another."""
    mesh = provisioned
    resp = _create(client, mesh["forge"], "memora://forge/ghost",
                   agent_name="ghostagent")
    assert resp.status_code == 404, f"got {resp.status_code}: {resp.text}"

    listed = client.get("/namespaces", headers=mesh["forge"]).json()
    assert not any(n["path"] == "memora://forge/ghost" for n in listed), (
        "the namespace was created anyway, under a different owner"
    )


def test_unknown_agent_id_is_reported(client, provisioned):
    mesh = provisioned
    resp = _create(client, mesh["forge"], "memora://forge/by-id",
                   agent_id="no-such-agent-id")
    assert resp.status_code == 404, f"got {resp.status_code}: {resp.text}"


def test_traversal_path_is_rejected(client, provisioned):
    mesh = provisioned
    resp = _create(client, mesh["forge"], "memora://../../etc/passwd")
    assert resp.status_code == 400, f"got {resp.status_code}: {resp.text}"


def test_no_traversal_row_is_persisted(client, provisioned, test_db):
    mesh = provisioned
    _create(client, mesh["forge"], "memora://../../etc/passwd")
    from storage.relational.models import Namespace
    rows = test_db.query(Namespace).filter(Namespace.path.like("%..%")).count()
    assert rows == 0, f"{rows} traversal path(s) were stored"


# ---------------------------------------------------------------------------
# What must keep working
# ---------------------------------------------------------------------------

def test_caller_may_create_its_own_project_space(client, provisioned):
    mesh = provisioned
    resp = _create(client, mesh["forge"], "memora://forge/projects/alpha",
                   ns_type="project-private")
    assert resp.status_code == 201, f"got {resp.status_code}: {resp.text}"
    assert resp.json()["path"] == "memora://forge/projects/alpha"


def test_documented_shared_space_idiom_still_works(client, provisioned):
    """memora://shared/projects/<id> is how agents publish for peers."""
    mesh = provisioned
    resp = _create(client, mesh["forge"], "memora://shared/projects/auth-module",
                   ns_type="team-shared")
    assert resp.status_code == 201, f"got {resp.status_code}: {resp.text}"


def test_admin_may_provision_a_space_for_another_agent(client, provisioned):
    """Blocking this would make the admin check pointless."""
    mesh = provisioned
    resp = _create(client, mesh["memora"], "memora://friday/delegated",
                   agent_name="friday")
    assert resp.status_code == 201, f"got {resp.status_code}: {resp.text}"


def test_admin_may_create_under_a_foreign_root(client, provisioned):
    mesh = provisioned
    resp = _create(client, mesh["memora"], "memora://stratex/staging",
                   agent_name="stratex")
    assert resp.status_code == 201, f"got {resp.status_code}: {resp.text}"


def test_owner_recreating_its_own_namespace_is_idempotent(client, provisioned):
    mesh = provisioned
    first = _create(client, mesh["friday"], "memora://friday/projects/x",
                    ns_type="project-private")
    assert first.status_code == 201
    again = _create(client, mesh["friday"], "memora://friday/projects/x",
                    ns_type="project-private")
    assert again.status_code == 201, f"got {again.status_code}: {again.text}"
    assert again.json()["id"] == first.json()["id"]


def test_admin_may_still_read_an_existing_foreign_namespace(client, provisioned):
    """The 409 is about non-owners; the administrator is exempt."""
    mesh = provisioned
    resp = _create(client, mesh["memora"], "memora://friday/private")
    assert resp.status_code == 201, f"got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Ownership integrity after the attempts above
# ---------------------------------------------------------------------------

def test_ownership_is_unchanged_by_a_refused_claim(client, provisioned, test_db):
    mesh = provisioned
    before = {
        n.path: n.agent_id
        for n in IdentityService.list_namespaces(test_db)
    }

    _create(client, mesh["forge"], "memora://friday/private")
    _create(client, mesh["forge"], "memora://stratex/staging")
    _create(client, mesh["forge"], "memora://forge/owned-by-friday",
            agent_name="friday")

    test_db.expire_all()
    after = {
        n.path: n.agent_id
        for n in IdentityService.list_namespaces(test_db)
    }
    for path, owner in before.items():
        assert after.get(path) == owner, (
            f"ownership of '{path}' changed from {owner} to {after.get(path)}"
        )
