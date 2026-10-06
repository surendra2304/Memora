"""
Regression tests for the namespace claim vulnerability and path validation.

Found by driving the running API as an owner would, not from the unit suite:
forge stored a memory naming memora://friday/private, got HTTP 201, and ended up
owning that namespace row. friday then got 403 writing to its own private space.
Whoever wrote first won, and the legitimate owner lost.

These run fail-closed (MEMORA_ALLOW_ANONYMOUS_DEV removed) because the anonymous
dev mode authenticates every caller as the same identity and cannot express a
cross-agent claim at all.
"""
import pytest
from fastapi.testclient import TestClient

from core.identity.service import IdentityService
from storage.relational.models import NamespaceType


FRIDAY_KEY = "friday-claim-key"
FORGE_KEY = "forge-claim-key"


@pytest.fixture
def mesh(client: TestClient, monkeypatch):
    """Two provisioned mesh agents talking to a fail-closed Memora."""
    monkeypatch.delenv("MEMORA_ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.setenv("FRIDAY_API_KEY", FRIDAY_KEY)
    monkeypatch.setenv("FORGE_API_KEY", FORGE_KEY)
    return {
        "friday": {"X-Agent-Name": "friday", "X-API-Key": FRIDAY_KEY},
        "forge": {"X-Agent-Name": "forge", "X-API-Key": FORGE_KEY},
    }


def _store(client, headers, text, namespace=None):
    body = {"content_text": text}
    if namespace:
        body["target_namespace_path"] = namespace
    return client.post("/v1/memories", headers=headers, json=body)


# ---------------------------------------------------------------------------
# The claim itself
# ---------------------------------------------------------------------------

def test_agent_cannot_claim_another_agents_private_namespace(client: TestClient, mesh, test_db):
    """forge must not be able to take ownership of friday's private space.

    The namespace did not exist yet, so resolve_namespace created it with the
    CALLER as owner. The RULE_1 owner check at step 8 then approved forge's own
    write, because forge had just become the owner.
    """
    IdentityService.register_agent(test_db, name="forge", role="worker")

    # friday has not registered yet, which is exactly how the live capture
    # happened: forge wrote first and minted the row with itself as owner.
    resp = _store(client, mesh["forge"], "forge claiming friday's space",
                  namespace="memora://friday/private")
    assert resp.status_code == 403, (
        f"forge created memora://friday/private and got {resp.status_code}; "
        f"a caller must not be able to claim another agent's namespace"
    )

    ns = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")
    assert ns is None, "the foreign namespace must not have been created at all"


def test_legitimate_owner_can_still_write_its_own_private_namespace(client: TestClient, mesh, test_db):
    """The fix must not lock the real owner out of its own space."""
    IdentityService.register_agent(test_db, name="friday", role="supervisor")

    resp = _store(client, mesh["friday"], "friday's own note")
    assert resp.status_code == 201, f"friday writing to its own space got {resp.status_code}"

    ns = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")
    assert ns is not None
    friday = IdentityService.get_agent_by_name(test_db, "friday")
    assert ns.agent_id == friday.id, "the namespace must be owned by friday, not the first writer"


def test_owner_is_not_locked_out_by_a_prior_attacker(client: TestClient, mesh, test_db):
    """The live symptom: friday got 403 on its own namespace after forge claimed it.

    Reproduced by having forge attempt the claim first, then friday writing.
    """
    IdentityService.register_agent(test_db, name="friday", role="supervisor")
    IdentityService.register_agent(test_db, name="forge", role="worker")

    assert _store(client, mesh["forge"], "attacker", namespace="memora://friday/private").status_code == 403
    assert _store(client, mesh["friday"], "legitimate owner").status_code == 201


def test_granted_access_to_an_existing_namespace_still_works(client: TestClient, mesh, test_db):
    """Gating creation must not break a properly granted shared space."""
    friday = IdentityService.register_agent(test_db, name="friday", role="supervisor")
    IdentityService.register_agent(test_db, name="forge", role="worker")

    # friday owns the space and writes to it first.
    assert _store(client, mesh["friday"], "friday's record").status_code == 201
    ns = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")

    # Explicitly grant forge write access; now the write must succeed.
    IdentityService.grant_access(
        test_db,
        namespace_id=ns.id,
        agent_id=IdentityService.get_agent_by_name(test_db, "forge").id,
        actions=["read", "write"],
    )
    resp = _store(client, mesh["forge"], "forge writing under a grant",
                  namespace="memora://friday/private")
    assert resp.status_code == 201, f"a granted write was rejected: {resp.status_code} {resp.text}"
    assert friday is not None


def test_shared_project_namespace_remains_publishable(client: TestClient, mesh, test_db):
    """memora://shared/projects/<id> names no agent, so a publisher may create it.

    This is the documented promotion flow: SentinelAdapter publishes an approved
    remediation into the shared project namespace and then grants the target
    agent read access. The claim check must not break it.
    """
    IdentityService.register_agent(test_db, name="friday", role="supervisor")

    resp = _store(client, mesh["friday"], "approved remediation for auth-module",
                  namespace="memora://shared/projects/auth-module")
    assert resp.status_code == 201, (
        f"the shared-space promotion flow was blocked: {resp.status_code} {resp.text}"
    )

    ns = IdentityService.get_namespace_by_path(test_db, "memora://shared/projects/auth-module")
    assert ns is not None
    assert ns.type == NamespaceType.TEAM_SHARED


def test_unregistered_root_cannot_be_used_to_squat_a_registered_agents_name(client: TestClient, mesh, test_db):
    """A caller cannot invent a path under a name it does not control."""
    IdentityService.register_agent(test_db, name="friday", role="supervisor")
    IdentityService.register_agent(test_db, name="forge", role="worker")

    resp = _store(client, mesh["forge"], "squat", namespace="memora://friday/projects/x")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_path", [
    "memora://../../etc/passwd",
    "memora://friday/../../etc/shadow",
    "memora://friday/private/..",
    "memora://friday//private",
    "memora://friday/./private",
    "memora://",
    "memora:///",
    "",
    "   ",
    "memora://" + "a" * 200 + "/private",
    "memora://friday/" + "b" * 300,
])
def test_malformed_namespace_paths_are_rejected(bad_path, test_db):
    """Traversal and malformed segments must not become namespace rows.

    Paths are logical identifiers, but they are also the operand of prefix
    comparisons — a sub-agent's bounded_scope is matched with
    namespace.path.startswith(bounded_scope) — so a traversal segment can make a
    path that reads as belonging elsewhere satisfy a scope check.
    """
    with pytest.raises(ValueError):
        IdentityService.create_namespace(test_db, path=bad_path, ns_type=NamespaceType.TEAM_SHARED)

    assert IdentityService.get_namespace_by_path(test_db, bad_path) is None


@pytest.mark.parametrize("good_path,expected_root", [
    ("memora://friday/private", "friday"),
    ("memora://forge/delegated/forge:helper", "forge"),
    ("memora://team/shared", None),
    ("memora://universe/global", None),
    ("memora://public/announcements", None),
])
def test_valid_paths_normalise_and_report_their_root(good_path, expected_root, test_db):
    assert IdentityService.validate_namespace_path(good_path) == good_path
    assert IdentityService.namespace_root(good_path) == expected_root


def test_prefix_without_scheme_is_still_accepted(test_db):
    """Bare paths have always been normalised; that behaviour is unchanged."""
    assert IdentityService.validate_namespace_path("friday/private") == "memora://friday/private"


def test_non_string_path_is_rejected(test_db):
    with pytest.raises(ValueError):
        IdentityService.validate_namespace_path(None)


def test_traversal_path_rejected_over_http(client: TestClient, mesh, test_db):
    """End to end: the API must not persist a traversal namespace."""
    IdentityService.register_agent(test_db, name="friday", role="supervisor")

    resp = _store(client, mesh["friday"], "traversal attempt",
                  namespace="memora://../../etc/passwd")
    assert resp.status_code in (400, 422), f"got {resp.status_code}: {resp.text}"

    from storage.relational.models import Namespace
    rows = test_db.query(Namespace).filter(Namespace.path.like("%..%")).all()
    assert rows == [], f"traversal paths were persisted: {[r.path for r in rows]}"


def test_bounded_subagent_may_create_inside_its_own_scope(test_db):
    """A sub-agent's delegated scope must still be creatable by the sub-agent."""
    parent = IdentityService.register_agent(test_db, name="forge", role="worker")
    scope = "memora://forge/delegated/forge:helper"
    IdentityService.register_subagent(
        test_db,
        parent_agent_name="forge",
        subagent_name="helper",
        bounded_scope=scope,
    )
    sub = IdentityService.get_agent_by_name(test_db, "forge:helper")
    assert sub is not None
    assert sub.bounded_scope == scope
    assert IdentityService.namespace_root(scope) == "forge"
    assert parent is not None
