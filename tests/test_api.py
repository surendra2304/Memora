"""
Integration Tests for FastAPI Endpoints
"""
from fastapi.testclient import TestClient

from core.identity.service import IdentityService

def test_health_check(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "memora-api"
    assert data["status"] in ["healthy", "degraded"]


def test_root_serves_memora_memory_observatory_not_legacy_dashboard(client: TestClient):
    response = client.get("/")

    assert response.status_code == 200
    assert "Memora — Memory Observatory" in response.text
    assert "Policy-scoped memory search" in response.text
    assert "sample" not in response.text.lower()

#: Identity creation is fabric administration, so it is performed as the Memora
#: service identity rather than as an arbitrary peer.
ADMIN = {"X-Agent-Name": "memora"}


def test_agent_registration_and_list(client: TestClient):
    response = client.post("/agents", headers=ADMIN,
                           json={"name": "futuris", "description": "Predictive Forecasting"})
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "futuris"

    list_resp = client.get("/agents")
    assert list_resp.status_code == 200
    names = [a["name"] for a in list_resp.json()]
    assert "futuris" in names


def test_a_peer_agent_cannot_mint_a_new_identity(client: TestClient):
    """A valid credential is not authority to create identities.

    Registering an agent is the root of every policy decision, so a peer being
    able to do it — including with role="supervisor" — is an escalation. Verified
    against a live server before the fix: intelx created 'rogue' as a supervisor.
    """
    resp = client.post("/agents", headers={"X-Agent-Name": "intelx"},
                       json={"name": "rogue", "role": "supervisor"})
    assert resp.status_code == 403

    names = [a["name"] for a in client.get("/agents").json()]
    assert "rogue" not in names, "the identity was created despite the 403"


def test_an_agent_can_create_a_subagent_under_itself(client: TestClient):
    """The endpoint used to read two fields absent from its own schema and 500."""
    client.post("/agents", headers=ADMIN, json={"name": "forge", "role": "worker"})

    resp = client.post("/agents/subagents", headers={"X-Agent-Name": "forge"}, json={
        "name": "helper", "role": "worker",
        "bounded_scope": "memora://forge/projects/app-1"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["name"].endswith("helper")
    assert resp.json()["bounded_scope"] == "memora://forge/projects/app-1"


def test_the_subagent_parent_is_the_caller_and_cannot_be_chosen(client: TestClient):
    """No request field may name a parent, so none can name someone else's."""
    client.post("/agents", headers=ADMIN, json={"name": "forge", "role": "worker"})
    client.post("/agents", headers=ADMIN, json={"name": "friday", "role": "supervisor"})

    resp = client.post("/agents/subagents", headers={"X-Agent-Name": "forge"}, json={
        "name": "mole", "role": "worker", "parent_agent_name": "friday",
        "bounded_scope": "memora://forge/projects/app-2"})
    assert resp.status_code == 201
    assert resp.json()["name"].startswith("forge:"), (
        f"sub-agent was parented under someone other than the caller: {resp.json()['name']}"
    )


def test_an_unregistered_caller_cannot_create_a_subagent(client: TestClient):
    """A caller with no identity must get 404, not have one minted for them."""
    resp = client.post("/agents/subagents", headers={"X-Agent-Name": "ghost"}, json={
        "name": "helper", "bounded_scope": "memora://ghost/projects/x"})
    assert resp.status_code == 404
    names = [a["name"] for a in client.get("/agents").json()]
    assert "ghost" not in names, "an identity was created as a side effect"

def test_memory_ingest_query_and_lifecycle(client: TestClient, test_db):
    # Only provisioned supervisors may verify a memory's lifecycle state.
    IdentityService.register_agent(test_db, "intelx", role="supervisor")

    # Ingest memory
    ingest_payload = {
        "owner_name": "intelx",
        "namespace_path": "memora://intelx/private",
        "memory_type": "episodic",
        "content_text": "Deep research findings regarding transformer attention latency.",
        "source": "intelx_crawler",
        "provenance": {
            "source_type": "verified_fact",
            "trust_level": "verified",
            "evidence_refs": ["synthetic-fixture:transformer-research"],
        },
        "confidence": 0.95,
        "importance": 0.90,
        "lifecycle_state": "active"
    }
    create_resp = client.post("/memories", json=ingest_payload, headers={"X-Agent-Name": "intelx"})
    assert create_resp.status_code == 201
    mem_data = create_resp.json()
    mem_id = mem_data["id"]
    assert mem_data["memory_type"] == "episodic"
    assert mem_data["lifecycle_state"] == "active"

    # Query memories
    query_resp = client.post("/memories/query", json={"query_text": "transformer", "owner_name": "intelx"}, headers={"X-Agent-Name": "intelx"})
    assert query_resp.status_code == 200
    results = query_resp.json()
    assert len(results) >= 1
    assert results[0]["id"] == mem_id

    # Transition lifecycle to verified
    trans_resp = client.post(f"/memories/{mem_id}/transition", json={"target_state": "verified"}, headers={"X-Agent-Name": "intelx"})
    assert trans_resp.status_code == 200
    assert trans_resp.json()["lifecycle_state"] == "verified"

    # Audit check. The trail spans actors, so reading another agent's entries
    # requires the admin identity; a peer sees only its own.
    audit_resp = client.get("/audit", headers=ADMIN, params={"memory_id": mem_id})
    assert audit_resp.status_code == 200
    audit_logs = audit_resp.json()
    assert len(audit_logs) >= 2


def test_the_audit_trail_is_scoped_for_non_admin_callers(client: TestClient):
    """A peer must not be able to read another agent's audit entries."""
    client.post("/memories", headers={"X-Agent-Name": "intelx"}, json={
        "owner_name": "intelx", "namespace_path": "memora://intelx/private",
        "content_text": "intelx audited activity", "source": "intelx"})

    # intelx can see its own trail.
    own = client.get("/audit", headers={"X-Agent-Name": "intelx"})
    assert own.status_code == 200

    # A different peer sees none of intelx's entries.
    other = client.get("/audit", headers={"X-Agent-Name": "forge"})
    assert other.status_code == 200
    intelx_ids = {e["actor_id"] for e in own.json()}
    leaked = [e for e in other.json() if e["actor_id"] in intelx_ids]
    assert leaked == [], f"forge read {len(leaked)} of intelx's audit entries"

    # Asking explicitly for someone else's actor_id must not widen the scope.
    if intelx_ids:
        probe = client.get("/audit", headers={"X-Agent-Name": "forge"},
                           params={"actor_id": next(iter(intelx_ids))})
        assert probe.json() == [], "actor_id filter let a peer read another's trail"


def test_legacy_memory_list_authenticates_and_enforces_private_namespace_policy(client, test_db, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("INTELX_API_KEY", "intelx-test-key")
    monkeypatch.setenv("FRIDAY_API_KEY", "friday-test-key")

    create_resp = client.post(
        "/memories",
        json={
            "owner_name": "intelx",
            "namespace_path": "memora://intelx/private",
            "memory_type": "episodic",
            "content_text": "Private IntelX investigation note.",
            "source": "test",
        },
        headers={"X-Agent-Name": "intelx", "Authorization": "Bearer intelx-test-key"},
    )
    assert create_resp.status_code == 201
    memory_id = create_resp.json()["id"]

    assert client.get("/memories").status_code == 401

    friday_resp = client.get(
        "/memories",
        headers={"X-Agent-Name": "friday", "Authorization": "Bearer friday-test-key"},
    )
    # A valid service key does not create a policy identity. Unknown agents
    # must fail closed instead of bypassing namespace checks.
    assert friday_resp.status_code == 403
    friday_record_resp = client.get(
        f"/memories/{memory_id}",
        headers={"X-Agent-Name": "friday", "Authorization": "Bearer friday-test-key"},
    )
    assert friday_record_resp.status_code == 403

    from core.identity.service import IdentityService
    IdentityService.register_agent(test_db, "friday")
    friday_resp = client.get(
        "/memories",
        headers={"X-Agent-Name": "friday", "Authorization": "Bearer friday-test-key"},
    )
    assert friday_resp.status_code == 200
    assert memory_id not in {record["id"] for record in friday_resp.json()}

    intelx_resp = client.get(
        "/memories",
        headers={"X-Agent-Name": "intelx", "Authorization": "Bearer intelx-test-key"},
    )
    assert intelx_resp.status_code == 200
    assert memory_id in {record["id"] for record in intelx_resp.json()}

def test_namespaces_api_and_grants(client: TestClient, test_db, monkeypatch):
    """
    Test /namespaces POST, GET, /namespaces/grants, and /namespaces/grants DELETE.
    """
    # Provision the recipient first; grant creation must not mint identities.
    IdentityService.register_agent(test_db, "forge")
    monkeypatch.setenv("MEMORA_API_KEY", "namespace-admin-test-key")
    admin_headers = {
        "X-Agent-Name": "memora",
        "X-API-Key": "namespace-admin-test-key",
    }

    # Open shared roots require the deployment administrator to provision them.
    create_resp = client.post(
        "/namespaces",
        headers=admin_headers,
        json={"path": "memora://shared/team-ops", "type": "team-shared"},
    )
    assert create_resp.status_code == 201
    ns_data = create_resp.json()
    assert ns_data["path"] == "memora://shared/team-ops"
    assert ns_data["type"] == "team-shared"

    # List namespaces
    list_resp = client.get("/namespaces", headers=admin_headers)
    assert list_resp.status_code == 200
    paths = [n["path"] for n in list_resp.json()]
    assert "memora://shared/team-ops" in paths

    # Grant access
    grant_resp = client.post("/namespaces/grants", headers=admin_headers, json={
        "agent_name": "forge",
        "namespace_path": "memora://shared/team-ops",
        "actions": ["read", "write"]
    })
    assert grant_resp.status_code == 201
    grant_data = grant_resp.json()
    assert "read" in grant_data["actions"]
    assert "write" in grant_data["actions"]

    # Revoke access
    revoke_resp = client.delete(
        f"/namespaces/grants?agent_id={grant_data['agent_id']}&namespace_id={grant_data['namespace_id']}",
        headers=admin_headers,
    )
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["status"] == "revoked"

def test_api_404_not_found_handling(client: TestClient):
    """
    Test 404 error responses on non-existent memory and agent lookups.
    """
    resp_mem = client.get("/v1/memories/non-existent-memory-id-9999", headers={"X-Agent-Name": "friday"})
    assert resp_mem.status_code == 404
    assert "not found" in resp_mem.json()["detail"].lower()

    resp_agent = client.get("/agents/non_existent_agent_9999")
    assert resp_agent.status_code == 404
