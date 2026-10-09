"""Adversarial, tenant-bound API regressions added during iterative hardening.

Every fixture here is synthetic and uses the in-memory test database. The tests
exercise complete HTTP paths as well as the service-level delegation boundary;
no deployed service or real user data is involved.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from core.collaboration.service import CollaborationPermissionError, CollaborationService
from core.identity.service import IdentityService
from core.memory.graph_service import GraphService, InvalidRelationshipError
from core.memory.pipeline.write_service import MemoryWriteService
from core.policy.engine import PolicyEngine
from storage.relational.models import (
    AccessGrant,
    Agent,
    AuditLog,
    DeletionTombstone,
    EventLog,
    LifecycleState,
    MemoryRecord,
    MemoryRelationship,
    MemoryType,
    Namespace,
    NamespaceType,
)
from storage.vector.qdrant_adapter import vector_adapter


@pytest.fixture
def api_mesh(client, test_db, monkeypatch):
    """Fail-closed credentials paired with the matching default-tenant actors."""
    monkeypatch.delenv("MEMORA_ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    keys = {
        "friday": "phase17-friday-key",
        "forge": "phase17-forge-key",
        "memora": "phase17-memora-key",
    }
    for name, key in keys.items():
        monkeypatch.setenv(f"{name.upper()}_API_KEY", key)
    agents = {
        name: IdentityService.register_agent(
            test_db,
            name,
            role="supervisor" if name == "memora" else "worker",
            tenant_id="default",
        )
        for name in keys
    }
    headers = {
        name: {"X-Agent-Name": name, "X-API-Key": key}
        for name, key in keys.items()
    }
    return {"headers": headers, "agents": agents}


@pytest.fixture
def clean_vectors():
    previous = dict(vector_adapter._mock_store)
    vector_adapter._mock_store.clear()
    try:
        yield
    finally:
        vector_adapter._mock_store.clear()
        vector_adapter._mock_store.update(previous)


def _add_memory(
    db,
    agent,
    text,
    *,
    tenant_id="default",
    namespace=None,
    memory_type=MemoryType.EPISODIC,
):
    if namespace is None:
        namespace = IdentityService.get_namespace_by_path(
            db, f"memora://{agent.name}/private", tenant_id=tenant_id
        )
        if namespace is None:
            namespace = IdentityService.resolve_namespace(
                db,
                f"memora://{agent.name}/projects/phase17",
                owner_agent_id=agent.id,
                default_type=NamespaceType.PROJECT_PRIVATE,
                tenant_id=tenant_id,
            )
    record = MemoryRecord(
        tenant_id=tenant_id,
        namespace_id=namespace.id,
        owner_id=agent.id,
        memory_type=memory_type,
        content_text=text,
        source="synthetic-phase17",
        confidence=0.9,
        importance=0.7,
        lifecycle_state=LifecycleState.ACTIVE,
    )
    db.add(record)
    db.commit()
    return record


def test_authenticated_routes_do_not_adopt_a_foreign_same_named_identity(
    client, test_db, monkeypatch
):
    """A known Friday credential cannot bind to the only Friday in another tenant."""
    monkeypatch.delenv("MEMORA_ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("FRIDAY_API_KEY", "phase17-foreign-only-friday-key")
    headers = {
        "X-Agent-Name": "friday",
        "X-API-Key": "phase17-foreign-only-friday-key",
    }

    foreign_friday = IdentityService.register_agent(
        test_db, "friday", tenant_id="tenant-outsider"
    )
    foreign_record = _add_memory(
        test_db,
        foreign_friday,
        "foreign-private sentinel phase17-zircon-731",
        tenant_id="tenant-outsider",
    )
    default_forge = IdentityService.register_agent(test_db, "forge", tenant_id="default")
    default_record = _add_memory(
        test_db,
        default_forge,
        "default-private sentinel phase17-copper-842",
    )

    context = client.post(
        "/v1/context",
        headers=headers,
        json={"task_query": "phase17-zircon-731"},
    )
    assert context.status_code == 200
    assert foreign_record.id not in context.text
    assert foreign_record.content_text not in context.text
    local_friday = IdentityService.get_agent_by_name(
        test_db, "friday", tenant_id="default"
    )
    assert local_friday is not None
    assert local_friday.id != foreign_friday.id

    direct_write = client.post(
        "/v1/memories",
        headers=headers,
        json={"content_text": "phase17 default-tenant direct write"},
    )
    assert direct_write.status_code == 201, direct_write.text
    direct_record = test_db.query(MemoryRecord).filter(
        MemoryRecord.content_text == "phase17 default-tenant direct write"
    ).one()
    assert direct_record.tenant_id == "default"
    assert direct_record.owner_id == local_friday.id

    search = client.get(
        "/v1/memories/search",
        headers=headers,
        params={"q": "phase17-copper-842"},
    )
    assert search.status_code == 200
    assert default_record.content_text not in search.text
    assert foreign_record.content_text not in search.text

    task_query = client.post(
        "/v1/task/execute",
        headers=headers,
        json={"action": "query", "payload": {"query": "phase17-copper-842"}},
    )
    assert task_query.status_code == 200
    assert task_query.json()["result"]["count"] == 0
    assert default_record.content_text not in task_query.text
    assert foreign_record.content_text not in task_query.text

    # The task write path may bootstrap only the credential's default-tenant
    # identity; it must not reuse the foreign row merely because its name matches.
    task_write = client.post(
        "/v1/task/execute",
        headers=headers,
        json={"action": "store", "payload": {"content": "phase17 default-tenant task write"}},
    )
    assert task_write.status_code == 200
    assert task_write.json()["status"] == "SUCCESS"
    stored = test_db.query(MemoryRecord).filter(
        MemoryRecord.content_text == "phase17 default-tenant task write"
    ).one()
    assert stored.tenant_id == "default"
    assert stored.owner_id != foreign_friday.id


def test_namespace_policy_and_audit_metadata_are_tenant_bound(client, test_db, api_mesh):
    friday = api_mesh["agents"]["friday"]
    forge = api_mesh["agents"]["forge"]
    foreign_forge = IdentityService.register_agent(
        test_db, "forge", tenant_id="tenant-outsider"
    )

    private_path = "memora://forge/projects/phase17-policy"
    default_namespace = IdentityService.resolve_namespace(
        test_db,
        private_path,
        owner_agent_id=forge.id,
        default_type=NamespaceType.PROJECT_PRIVATE,
        tenant_id="default",
    )
    foreign_namespace = IdentityService.resolve_namespace(
        test_db,
        private_path,
        owner_agent_id=foreign_forge.id,
        default_type=NamespaceType.PROJECT_PRIVATE,
        tenant_id="tenant-outsider",
    )
    grant = IdentityService.grant_access(
        test_db,
        agent_id=friday.id,
        namespace_id=default_namespace.id,
        tenant_id="default",
        actions=["read"],
        purpose="synthetic policy visibility test",
    )

    peer_view = client.get(
        f"/v1/namespaces/{default_namespace.id}/policy",
        headers=api_mesh["headers"]["friday"],
    )
    assert peer_view.status_code == 403
    owner_view = client.get(
        f"/v1/namespaces/{default_namespace.id}/policy",
        headers=api_mesh["headers"]["forge"],
    )
    assert owner_view.status_code == 200
    assert owner_view.json()["access_grants"][0]["grant_id"] == grant.id
    foreign_view = client.get(
        f"/v1/namespaces/{foreign_namespace.id}/policy",
        headers=api_mesh["headers"]["friday"],
    )
    assert foreign_view.status_code == 404

    local_audit = AuditLog(
        tenant_id="default",
        actor_id=friday.id,
        action="phase17.local.audit.marker",
        details={"synthetic_marker": "tenant-default-only"},
    )
    foreign_audit = AuditLog(
        tenant_id="tenant-outsider",
        actor_id=foreign_forge.id,
        action="phase17.foreign.audit.marker",
        details={"synthetic_marker": "tenant-outsider-only"},
    )
    test_db.add_all([local_audit, foreign_audit])
    test_db.commit()

    own_audit = client.get("/audit", headers=api_mesh["headers"]["friday"])
    assert own_audit.status_code == 200
    assert "tenant-default-only" in own_audit.text
    assert "tenant-outsider-only" not in own_audit.text
    admin_audit = client.get("/audit", headers=api_mesh["headers"]["memora"])
    assert admin_audit.status_code == 200
    assert "tenant-outsider-only" not in admin_audit.text


def test_delegation_cannot_widen_parent_scope_or_actions(client, test_db, api_mesh):
    friday = api_mesh["agents"]["friday"]
    forge = api_mesh["agents"]["forge"]
    foreign_project = IdentityService.resolve_namespace(
        test_db,
        "memora://forge/projects/phase17-secret",
        owner_agent_id=forge.id,
        default_type=NamespaceType.PROJECT_PRIVATE,
    )

    denied = client.post(
        "/agents/subagents",
        headers=api_mesh["headers"]["friday"],
        json={
            "name": "scope-escalator",
            "bounded_scope": foreign_project.path,
        },
    )
    assert denied.status_code == 403
    assert test_db.query(Agent).filter(
        Agent.name == "friday:scope-escalator",
        Agent.tenant_id == "default",
    ).first() is None

    read_only_path = "memora://forge/projects/phase17-read-only"
    read_only_ns = IdentityService.resolve_namespace(
        test_db,
        read_only_path,
        owner_agent_id=forge.id,
        default_type=NamespaceType.PROJECT_PRIVATE,
    )
    IdentityService.grant_access(
        test_db,
        agent_id=friday.id,
        namespace_id=read_only_ns.id,
        tenant_id="default",
        actions=["read", "query"],
        purpose="synthetic delegation intersection test",
    )

    created = client.post(
        "/agents/subagents",
        headers=api_mesh["headers"]["friday"],
        json={"name": "limited-reader", "bounded_scope": read_only_path},
    )
    assert created.status_code == 201, created.text
    child = test_db.query(Agent).filter(Agent.id == created.json()["id"]).one()
    child_grant = test_db.query(AccessGrant).filter(
        AccessGrant.tenant_id == "default",
        AccessGrant.agent_id == child.id,
        AccessGrant.namespace_id == read_only_ns.id,
    ).one()
    assert set(child_grant.actions) == {"read", "query"}
    write_decision = PolicyEngine.evaluate_access(
        test_db, child, read_only_ns, action="write", log_audit=False
    )
    assert write_decision.allowed is False


def test_contribution_endpoint_rejects_mutation_grant_actions(client, test_db, api_mesh):
    friday = api_mesh["agents"]["friday"]
    forge = api_mesh["agents"]["forge"]
    record = _add_memory(test_db, friday, "synthetic phase17 contribution scope")

    response = client.post(
        "/v1/collaboration/contribute",
        headers=api_mesh["headers"]["friday"],
        json={
            "memory_id": record.id,
            "recipient": forge.name,
            "actions": ["delete"],
        },
    )
    assert response.status_code == 422
    assert test_db.query(AccessGrant).filter(
        AccessGrant.tenant_id == "default",
        AccessGrant.agent_id == forge.id,
        AccessGrant.namespace_id == record.namespace_id,
    ).count() == 0

    with pytest.raises(CollaborationPermissionError):
        CollaborationService.contribute(
            test_db,
            contributor_name=friday.name,
            memory_id=record.id,
            recipient_name=forge.name,
            actions=["*"],
            tenant_id="default",
        )


def test_grant_and_share_do_not_create_unregistered_identities(client, test_db, api_mesh):
    friday = api_mesh["agents"]["friday"]
    namespace = IdentityService.get_namespace_by_path(
        test_db, "memora://friday/private", tenant_id="default"
    )

    grant = client.post(
        "/namespaces/grants",
        headers=api_mesh["headers"]["friday"],
        json={
            "agent_name": "unknown-phase17-recipient",
            "namespace_id": namespace.id,
            "actions": ["read"],
        },
    )
    assert grant.status_code == 404
    assert IdentityService.get_agent_by_name(
        test_db, "unknown-phase17-recipient", tenant_id="default"
    ) is None

    record = _add_memory(test_db, friday, "synthetic unknown share recipient")
    share = client.post(
        f"/v1/memories/{record.id}/share",
        headers=api_mesh["headers"]["friday"],
        json={"target_agent_name": "unknown-phase17-share-recipient", "actions": ["read"]},
    )
    assert share.status_code == 404
    assert IdentityService.get_agent_by_name(
        test_db, "unknown-phase17-share-recipient", tenant_id="default"
    ) is None


def test_repair_preview_and_run_do_not_touch_foreign_tenant_state(
    client, test_db, api_mesh
):
    foreign_vector_id = "phase17-foreign-orphan-vector"
    default_vector_id = "phase17-default-orphan-vector"
    foreign_tombstone = DeletionTombstone(
        tenant_id="tenant-outsider",
        memory_id="phase17-foreign-tombstone-memory",
        relational_deleted=True,
        vector_deleted=False,
        cache_deleted=True,
        graph_deleted=True,
        status="PENDING_RETRY",
        retry_count=4,
    )
    test_db.add(foreign_tombstone)
    test_db.commit()
    vector_adapter.upsert_embedding(
        foreign_vector_id, [1.0] * 8, tenant_id="tenant-outsider"
    )
    vector_adapter.upsert_embedding(default_vector_id, [1.0] * 8, tenant_id="default")

    preview = client.get(
        "/v1/resilience/repair/preview",
        headers=api_mesh["headers"]["friday"],
    )
    assert preview.status_code == 200
    assert foreign_vector_id not in preview.text
    assert foreign_tombstone.memory_id not in preview.text

    run = client.post(
        "/v1/resilience/repair",
        headers=api_mesh["headers"]["memora"],
        json={"dry_run": False},
    )
    assert run.status_code == 200
    test_db.refresh(foreign_tombstone)
    assert foreign_tombstone.retry_count == 4
    assert foreign_tombstone.vector_deleted is False
    assert foreign_vector_id in vector_adapter._mock_store
    assert default_vector_id not in vector_adapter._mock_store

    vector_adapter._mock_store.pop(foreign_vector_id, None)
    vector_adapter._mock_store.pop(default_vector_id, None)


@pytest.mark.parametrize(
    ("namespace_type", "path"),
    [
        ("universe-global", "memora://universe/phase17-global"),
        ("public", "memora://public/phase17-public"),
        ("team-shared", "memora://team/phase17-open-team"),
        # The path root is authoritative too: a caller cannot squat an open
        # namespace by lying about it being a private project.
        ("project-private", "memora://universe/global"),
        ("project-private", "memora://public/phase17-private-public-root"),
        ("project-private", "memora://team/phase17-private-team-root"),
        ("project-private", "memora://shared/projects/phase17-private-shared-root"),
    ],
)
def test_regular_agents_cannot_claim_open_namespace_roots(
    client, test_db, api_mesh, namespace_type, path
):
    response = client.post(
        "/namespaces",
        headers=api_mesh["headers"]["friday"],
        json={"path": path, "type": namespace_type},
    )
    assert response.status_code == 403
    assert test_db.query(Namespace).filter(
        Namespace.path == path,
        Namespace.tenant_id == "default",
    ).first() is None


@pytest.mark.parametrize(
    ("actions", "expired"),
    [
        (["write"], False),
        (["query"], False),
        (["read"], True),
    ],
)
def test_assistance_does_not_disclose_ids_without_live_read_grant(
    client, test_db, api_mesh, actions, expired
):
    friday = api_mesh["agents"]["friday"]
    forge = api_mesh["agents"]["forge"]
    namespace = IdentityService.resolve_namespace(
        test_db,
        "memora://friday/projects/phase17-assistance-scope",
        owner_agent_id=friday.id,
        default_type=NamespaceType.PROJECT_PRIVATE,
    )
    record = _add_memory(
        test_db,
        friday,
        "phase17-hidden-collaboration-marker-934",
        namespace=namespace,
    )
    expiry = datetime.now(timezone.utc) - timedelta(hours=1) if expired else None
    IdentityService.grant_access(
        test_db,
        agent_id=forge.id,
        namespace_id=namespace.id,
        actions=actions,
        expires_at=expiry,
        purpose="synthetic assistance visibility boundary",
    )

    response = client.post(
        "/v1/collaboration/assist",
        headers=api_mesh["headers"]["forge"],
        json={"query": "phase17-hidden-collaboration-marker-934"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["immediately_usable"] == []
    assert body["candidates"] == []
    assert record.id not in response.text


def test_write_only_member_cannot_contribute_entire_shared_namespace(
    client, test_db, api_mesh
):
    friday = api_mesh["agents"]["friday"]
    forge = api_mesh["agents"]["forge"]
    memora = api_mesh["agents"]["memora"]
    path = "memora://friday/projects/phase17-write-only-contribution"
    namespace = IdentityService.resolve_namespace(
        test_db,
        path,
        owner_agent_id=friday.id,
        default_type=NamespaceType.PROJECT_PRIVATE,
    )
    IdentityService.grant_access(
        test_db,
        agent_id=forge.id,
        namespace_id=namespace.id,
        actions=["write"],
        purpose="synthetic write-only contributor test",
    )
    record = MemoryWriteService.execute_pipeline(
        db=test_db,
        caller_name=forge.name,
        tenant_id="default",
        target_namespace_path=path,
        content_text="synthetic Forge-owned row in Friday's private project",
    ).record

    response = client.post(
        "/v1/collaboration/contribute",
        headers=api_mesh["headers"]["forge"],
        json={"memory_id": record.id, "recipient": "memora", "actions": ["read"]},
    )
    assert response.status_code == 403
    assert test_db.query(AccessGrant).filter(
        AccessGrant.tenant_id == "default",
        AccessGrant.agent_id == memora.id,
        AccessGrant.namespace_id == namespace.id,
    ).first() is None


def test_legacy_wildcard_grant_is_not_authority_and_cannot_be_created(
    test_db, api_mesh
):
    forge = api_mesh["agents"]["forge"]
    namespace = IdentityService.get_namespace_by_path(
        test_db, "memora://friday/private", tenant_id="default"
    )
    test_db.add(AccessGrant(
        tenant_id="default",
        agent_id=forge.id,
        namespace_id=namespace.id,
        actions=["*"],
    ))
    test_db.commit()

    decision = PolicyEngine.evaluate_access(
        test_db, forge, namespace, action="delete", log_audit=False
    )
    assert decision.allowed is False
    with pytest.raises(ValueError, match="wildcard"):
        IdentityService.grant_access(
            test_db,
            agent_id=forge.id,
            namespace_id=namespace.id,
            actions=["*"],
        )


def test_vector_deletion_and_search_fail_closed_on_tenant_metadata(clean_vectors):
    foreign_id = "phase17-vector-tenant-guard"
    vector_adapter.upsert_embedding(
        foreign_id, [1.0, 0.0], tenant_id="tenant-outsider"
    )

    assert vector_adapter.delete_embedding(foreign_id, tenant_id="default") is False
    assert foreign_id in vector_adapter._mock_store
    assert vector_adapter.search_similarity(
        [1.0, 0.0], tenant_id="default", score_threshold=0.0
    ) == []
    assert vector_adapter.delete_embedding(
        foreign_id, tenant_id="tenant-outsider"
    ) is True
    assert foreign_id not in vector_adapter._mock_store

    legacy_id = "phase17-vector-missing-tenant"
    vector_adapter._mock_store[legacy_id] = {
        "vector": [1.0, 0.0],
        "payload": {},
    }
    assert vector_adapter.search_similarity(
        [1.0, 0.0], tenant_id="default", score_threshold=0.0
    ) == []
    assert vector_adapter.delete_embedding(legacy_id, tenant_id="default") is False
    assert legacy_id in vector_adapter._mock_store
    vector_adapter._mock_store.pop(legacy_id, None)


def test_regular_agent_cannot_self_create_global_namespace_through_write_api(
    client, test_db, api_mesh
):
    path = "memora://universe/phase17-unapproved-global"
    response = client.post(
        "/v1/memories",
        headers=api_mesh["headers"]["friday"],
        json={
            "target_namespace_path": path,
            "content_text": "synthetic attempt to claim a global namespace",
        },
    )
    assert response.status_code == 403
    assert IdentityService.get_namespace_by_path(
        test_db, path, tenant_id="default"
    ) is None


def test_canonical_team_shared_first_write_preserves_granted_collaboration(
    client, test_db, api_mesh
):
    path = "memora://team/shared"
    friday = api_mesh["agents"]["friday"]
    forge = api_mesh["agents"]["forge"]
    text = "phase17 team shared calibrated compressor torque is 42 Nm"

    first_write = client.post(
        "/v1/memories",
        headers=api_mesh["headers"]["friday"],
        json={"target_namespace_path": path, "content_text": text},
    )
    assert first_write.status_code == 201, first_write.text
    memory_id = first_write.json()["id"]
    namespace = IdentityService.get_namespace_by_path(
        test_db, path, tenant_id="default"
    )
    assert namespace is not None
    assert namespace.type == NamespaceType.TEAM_SHARED
    assert namespace.agent_id == friday.id

    # Merely living under the canonical shared path does not make content public.
    denied_read = client.get(
        f"/v1/memories/{memory_id}", headers=api_mesh["headers"]["forge"]
    )
    assert denied_read.status_code == 403
    assert text not in denied_read.text

    grant = client.post(
        "/namespaces/grants",
        headers=api_mesh["headers"]["friday"],
        json={
            "agent_name": forge.name,
            "namespace_path": path,
            "actions": ["read", "query"],
            "purpose": "synthetic team collaboration regression",
        },
    )
    assert grant.status_code == 201, grant.text
    assistance = client.post(
        "/v1/collaboration/assist",
        headers=api_mesh["headers"]["forge"],
        json={"query": "calibrated compressor torque"},
    )
    assert assistance.status_code == 200, assistance.text
    body = assistance.json()
    assert any(item["memory_id"] == memory_id for item in body["immediately_usable"])
    assert any(text in item["content_text"] for item in body["immediately_usable"])

    # A read/query grant does not silently widen into write access.
    denied_write = client.post(
        "/v1/memories",
        headers=api_mesh["headers"]["forge"],
        json={"target_namespace_path": path, "content_text": "unauthorized shared write"},
    )
    assert denied_write.status_code == 403


def test_global_http_body_limit_rejects_oversized_requests_before_json_parsing(
    client, api_mesh
):
    response = client.post(
        "/v1/task/execute",
        headers=api_mesh["headers"]["friday"],
        content=b"x" * (1_048_576 + 1),
    )
    assert response.status_code == 413
    assert "1048576-byte limit" in response.json()["detail"]


def test_global_http_body_limit_also_covers_chunked_requests(client, api_mesh):
    async def chunks():
        for _ in range(40):
            yield b"x" * 32_768

    async def send_chunked_request():
        transport = httpx.ASGITransport(app=client.app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as async_client:
            return await async_client.post(
                "/v1/task/execute",
                headers=api_mesh["headers"]["friday"],
                content=chunks(),
            )

    response = asyncio.run(send_chunked_request())
    assert response.status_code == 413
    assert "1048576-byte limit" in response.json()["detail"]


def test_task_envelopes_and_memory_idempotency_keys_are_bounded(client, api_mesh):
    headers = api_mesh["headers"]["friday"]
    too_many_fields = {f"field-{index}": index for index in range(33)}
    field_response = client.post(
        "/v1/task/execute",
        headers=headers,
        json={"action": "query", "payload": too_many_fields},
    )
    assert field_response.status_code == 422

    deep_payload = {"leaf": True}
    for _ in range(17):
        deep_payload = {"child": deep_payload}
    deep_response = client.post(
        "/v1/task/execute",
        headers=headers,
        json={"action": "query", "payload": {"nested": deep_payload}},
    )
    assert deep_response.status_code == 422

    node_response = client.post(
        "/v1/task/execute",
        headers=headers,
        json={"action": "query", "payload": {"items": list(range(4096))}},
    )
    assert node_response.status_code == 422

    too_large = client.post(
        "/v1/task/execute",
        headers=headers,
        json={"action": "query", "payload": {"query": "x" * (128 * 1024)}},
    )
    assert too_large.status_code == 422

    too_long_query = client.post(
        "/v1/task/execute",
        headers=headers,
        json={"action": "query", "payload": {"query": "x" * 4097}},
    )
    assert too_long_query.status_code == 422

    too_long_key = client.post(
        "/v1/memories",
        headers=headers,
        json={
            "content_text": "synthetic idempotency limit check",
            "idempotency_key": "k" * 129,
        },
    )
    assert too_long_key.status_code == 422


def test_context_event_keeps_raw_request_query_out_of_persisted_payload(
    client, test_db, api_mesh
):
    query = "phase17 synthetic query marker for event privacy"
    response = client.post(
        "/v1/context",
        headers=api_mesh["headers"]["friday"],
        json={"task_query": query, "token_budget": 500},
    )
    assert response.status_code == 200, response.text
    assert response.json()["query"] == query

    event = test_db.query(EventLog).filter(
        EventLog.event_type == "context.generated",
        EventLog.tenant_id == "default",
    ).order_by(EventLog.id.desc()).first()
    assert event is not None
    assert "query" not in event.payload
    assert query not in str(event.payload)


def test_hybrid_search_reapplies_namespace_and_type_scope_to_vector_candidates(
    test_db, clean_vectors
):

    from core.memory.search_service import SearchService
    from storage.vector.embedding import EmbeddingGenerator

    friday = IdentityService.register_agent(test_db, "friday")
    target = IdentityService.resolve_namespace(
        test_db,
        "memora://friday/projects/phase17-search-target",
        owner_agent_id=friday.id,
        default_type=NamespaceType.PROJECT_PRIVATE,
    )
    other = IdentityService.resolve_namespace(
        test_db,
        "memora://friday/projects/phase17-search-other",
        owner_agent_id=friday.id,
        default_type=NamespaceType.PROJECT_PRIVATE,
    )
    expected = _add_memory(
        test_db,
        friday,
        "phase17 vector scope marker allowed target record",
        namespace=target,
        memory_type=MemoryType.PROCEDURAL,
    )
    out_of_scope = _add_memory(
        test_db,
        friday,
        "phase17 vector scope marker outside requested namespace and type",
        namespace=other,
        memory_type=MemoryType.EPISODIC,
    )
    query_vector = EmbeddingGenerator.generate_embedding("phase17 vector scope marker")
    vector_adapter.upsert_embedding(expected.id, query_vector, tenant_id="default")
    vector_adapter.upsert_embedding(out_of_scope.id, query_vector, tenant_id="default")

    results = SearchService.hybrid_search(
        test_db,
        query_text="phase17 vector scope marker",
        actor_name=friday.name,
        tenant_id="default",
        namespace_path=target.path,
        memory_types=[MemoryType.PROCEDURAL],
        limit=10,
    )
    result_ids = {result.record.id for result in results}
    assert expected.id in result_ids
    assert out_of_scope.id not in result_ids


def test_hybrid_search_includes_expired_records_only_when_explicitly_requested(
    test_db, clean_vectors
):
    from core.memory.search_service import SearchService
    from storage.vector.embedding import EmbeddingGenerator

    friday = IdentityService.register_agent(test_db, "friday")
    namespace = IdentityService.get_namespace_by_path(
        test_db, "memora://friday/private", tenant_id="default"
    )
    expired = _add_memory(
        test_db,
        friday,
        "phase17 hybrid explicit expired search marker",
        namespace=namespace,
    )
    expired.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    test_db.commit()
    vector = EmbeddingGenerator.generate_embedding("phase17 hybrid explicit expired search marker")
    vector_adapter.upsert_embedding(expired.id, vector, tenant_id="default")

    def search(include_expired):
        return SearchService.hybrid_search(
            test_db,
            query_text="phase17 hybrid explicit expired search marker",
            actor_name=friday.name,
            tenant_id="default",
            include_expired=include_expired,
        )

    assert expired.id not in {item.record.id for item in search(False)}
    assert expired.id in {item.record.id for item in search(True)}


@pytest.mark.parametrize("mismatched_scope", ["namespace", "workspace", "task"])
def test_context_experience_prefetch_respects_all_requested_scopes(
    test_db, clean_vectors, mismatched_scope
):
    from core.memory.context.builder import ContextBuilderService

    friday = IdentityService.register_agent(test_db, "friday")
    target = IdentityService.resolve_namespace(
        test_db,
        "memora://friday/projects/phase17-prefetch-target",
        owner_agent_id=friday.id,
        default_type=NamespaceType.PROJECT_PRIVATE,
    )
    other = IdentityService.resolve_namespace(
        test_db,
        "memora://friday/projects/phase17-prefetch-other",
        owner_agent_id=friday.id,
        default_type=NamespaceType.PROJECT_PRIVATE,
    )
    namespace = other if mismatched_scope == "namespace" else target
    workspace_id = (
        "other-workspace" if mismatched_scope == "workspace" else "target-workspace"
    )
    task_id = "other-task" if mismatched_scope == "task" else "target-task"
    record = MemoryRecord(
        tenant_id="default",
        namespace_id=namespace.id,
        owner_id=friday.id,
        user_id="phase17-scope-user",
        agent_id=friday.name,
        workspace_id=workspace_id,
        task_id=task_id,
        memory_type=MemoryType.EXPERIENCE,
        content_text="high importance experience outside requested scope",
        confidence=0.9,
        importance=0.99,
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add(record)
    test_db.commit()

    bundle = ContextBuilderService.build_context_bundle(
        db=test_db,
        agent_id_or_name=friday.name,
        task_query="phase17 unrelated request marker",
        user_id="phase17-scope-user",
        workspace_id="target-workspace",
        task_id="target-task",
        namespace_path=target.path,
        token_budget=500,
        tenant_id="default",
    )
    assert record.id not in {item["id"] for item in bundle.memories}


def test_memory_read_policy_hides_deleted_and_out_of_window_records(
    client, test_db, api_mesh
):
    friday = api_mesh["agents"]["friday"]
    namespace = IdentityService.get_namespace_by_path(
        test_db, "memora://friday/private", tenant_id="default"
    )
    deleted = _add_memory(
        test_db, friday, "phase17 deleted memory sentinel", namespace=namespace
    )
    deleted.lifecycle_state = LifecycleState.DELETED
    expired = _add_memory(
        test_db, friday, "phase17 expired memory sentinel", namespace=namespace
    )
    expired.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    not_yet_valid = _add_memory(
        test_db, friday, "phase17 future memory sentinel", namespace=namespace
    )
    not_yet_valid.valid_from = datetime.now(timezone.utc) + timedelta(days=1)
    test_db.commit()

    for record in (deleted, expired, not_yet_valid):
        response = client.get(
            f"/v1/memories/{record.id}",
            headers=api_mesh["headers"]["friday"],
        )
        assert response.status_code == 403
        assert record.content_text not in response.text

    search = client.get(
        "/v1/memories/search",
        headers=api_mesh["headers"]["friday"],
        params={"q": "phase17 sentinel"},
    )
    assert search.status_code == 200
    assert "phase17 deleted memory sentinel" not in search.text
    assert "phase17 expired memory sentinel" not in search.text
    assert "phase17 future memory sentinel" not in search.text


def test_graph_relationships_are_tenant_local_and_write_transactional(
    test_db, api_mesh, monkeypatch, clean_vectors
):
    friday = api_mesh["agents"]["friday"]
    foreign_agent = IdentityService.register_agent(
        test_db, "friday", tenant_id="tenant-outsider"
    )
    local_record = _add_memory(
        test_db, friday, "synthetic local graph anchor phase17-rollback-anchor"
    )
    foreign_record = _add_memory(
        test_db,
        foreign_agent,
        "synthetic foreign graph anchor",
        tenant_id="tenant-outsider",
    )
    with pytest.raises(InvalidRelationshipError, match="tenant"):
        GraphService.create_relationship(
            test_db, local_record.id, foreign_record.id, relationship_type="relates_to"
        )

    from core.events.emitter import event_emitter
    from core.memory.pipeline.entity_extractor import EntityExtractor

    monkeypatch.setattr(
        EntityExtractor,
        "extract_entities_and_relationships",
        staticmethod(lambda _text: {
            "entities": ["phase17-rollback-anchor"],
            "resolved_canonical_entities": ["phase17-rollback-anchor"],
            "triples": [],
        }),
    )

    def fail_event(*_args, **_kwargs):
        raise RuntimeError("synthetic late pipeline failure")

    monkeypatch.setattr(event_emitter, "publish", fail_event)
    before_memory_ids = {
        row[0] for row in test_db.query(MemoryRecord.id).all()
    }
    before_vector_ids = set(vector_adapter._mock_store)
    with pytest.raises(RuntimeError, match="synthetic late pipeline failure"):
        MemoryWriteService.execute_pipeline(
            db=test_db,
            caller_name=friday.name,
            tenant_id="default",
            target_namespace_path="memora://friday/private",
            content_text="new distinct observation related to phase17-rollback-anchor",
        )

    test_db.expire_all()
    after_memory_ids = {
        row[0] for row in test_db.query(MemoryRecord.id).all()
    }
    assert after_memory_ids == before_memory_ids
    assert test_db.query(MemoryRecord).filter(
        MemoryRecord.content_text == "new distinct observation related to phase17-rollback-anchor"
    ).count() == 0
    assert test_db.query(MemoryRelationship).count() == 0
    assert set(vector_adapter._mock_store) == before_vector_ids
