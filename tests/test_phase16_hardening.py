"""Regression coverage for the post-audit hardening pass.

These tests exercise the real API and persistence paths with synthetic agents and
memories. They deliberately avoid live user data, third-party model APIs, and
production services.
"""
from datetime import datetime, timedelta, timezone
import json
import threading
from urllib.parse import urlsplit

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from apps.api.main import app
from apps.api.routers import v1_memories as v1_memories_router
from core.identity.service import IdentityService
from core.memory.context.budgeter import ContextBudgeter
from core.memory.context.reranker import RerankedMemoryItem
from core.memory.pipeline.write_service import MemoryWriteService
from storage.relational.base import Base
from storage.relational.session import get_db
from storage.relational.models import (
    AccessGrant,
    Agent,
    LifecycleState,
    MemoryRecord,
    MemoryType,
    Namespace,
    NamespaceType,
)


FRIDAY_KEY = "phase16-friday-test-key"
FORGE_KEY = "phase16-forge-test-key"
MEMORA_KEY = "phase16-memora-test-key"


@pytest.fixture
def mesh(client, test_db, monkeypatch):
    """Fail-closed API clients, each mapped to a separate synthetic identity."""
    monkeypatch.delenv("MEMORA_ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.setenv("FRIDAY_API_KEY", FRIDAY_KEY)
    monkeypatch.setenv("FORGE_API_KEY", FORGE_KEY)
    monkeypatch.setenv("MEMORA_API_KEY", MEMORA_KEY)
    friday = IdentityService.register_agent(test_db, "friday", role="supervisor")
    forge = IdentityService.register_agent(test_db, "forge")
    memora = IdentityService.register_agent(test_db, "memora", role="supervisor")
    return {
        "friday": {"X-Agent-Name": "friday", "X-API-Key": FRIDAY_KEY},
        "forge": {"X-Agent-Name": "forge", "X-API-Key": FORGE_KEY},
        "memora": {"X-Agent-Name": "memora", "X-API-Key": MEMORA_KEY},
        "agents": {"friday": friday, "forge": forge, "memora": memora},
    }


def test_namespace_endpoints_reject_owner_tenant_and_orphan_spoofing(
    client, mesh, test_db
):
    claimed_path = "memora://forge/projects/claimed-by-friday"
    claimed = client.post(
        "/namespaces",
        headers=mesh["friday"],
        json={
            "path": claimed_path,
            "type": "project-private",
            "agent_id": mesh["agents"]["forge"].id,
        },
    )
    assert claimed.status_code == 403
    assert test_db.query(Namespace).filter(Namespace.path == claimed_path).count() == 0

    cross_tenant_path = "memora://friday/projects/cross-tenant"
    cross_tenant = client.post(
        "/namespaces",
        headers=mesh["friday"],
        json={"path": cross_tenant_path, "tenant_id": "another-tenant"},
    )
    assert cross_tenant.status_code == 403
    assert test_db.query(Namespace).filter(Namespace.path == cross_tenant_path).count() == 0

    orphan_path = "memora://ghost/projects/orphan-grant"
    orphan_grant = client.post(
        "/namespaces/grants",
        headers=mesh["friday"],
        json={
            "agent_name": "forge",
            "namespace_path": orphan_path,
            "actions": ["read"],
        },
    )
    assert orphan_grant.status_code == 404
    assert test_db.query(Namespace).filter(Namespace.path == orphan_path).count() == 0


def test_namespace_grants_require_owner_and_forbid_wildcard(client, mesh, test_db):
    namespace = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")

    denied = client.post(
        "/namespaces/grants",
        headers=mesh["forge"],
        json={
            "agent_name": "memora",
            "namespace_id": namespace.id,
            "actions": ["read"],
        },
    )
    assert denied.status_code == 403
    assert test_db.query(AccessGrant).filter(
        AccessGrant.namespace_id == namespace.id,
        AccessGrant.agent_id == mesh["agents"]["memora"].id,
    ).count() == 0

    wildcard = client.post(
        "/namespaces/grants",
        headers=mesh["friday"],
        json={
            "agent_name": "forge",
            "namespace_id": namespace.id,
            "actions": ["*"],
        },
    )
    assert wildcard.status_code == 422


def test_v1_write_uses_authenticated_agent_not_body_claim(client, mesh, test_db):
    response = client.post(
        "/v1/memories",
        headers=mesh["friday"],
        json={"agent_id": "forge", "content_text": "synthetic identity substitution probe"},
    )

    assert response.status_code == 403
    assert test_db.query(MemoryRecord).filter(
        MemoryRecord.content_text == "synthetic identity substitution probe"
    ).count() == 0


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/v1/memories/learn-experience", {
            "agent_id": "forge",
            "outcomes": [{"task_name": "test", "status": "success"}],
        }),
        ("/v1/memories/record-interaction", {
            "agent_name": "forge", "user_text": "synthetic private preference",
        }),
        ("/v1/memories/learn-outcome", {
            "agent_name": "forge", "task_name": "test", "status": "failure",
        }),
    ],
)
def test_memory_workflows_reject_body_selected_agents(client, mesh, path, payload):
    response = client.post(path, headers=mesh["friday"], json=payload)
    assert response.status_code == 403


def test_sdk_learn_outcome_round_trips_through_authenticated_api(
    client, mesh, test_db, monkeypatch
):
    """Exercise the SDK wire payload against the real API and local SQL test DB."""
    from sdk.memora_client import MemoraClient

    class BridgeResponse:
        def __init__(self, response):
            self.status = response.status_code
            self._body = response.content

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return self._body

    calls = []

    def api_urlopen(request, timeout):
        path = urlsplit(request.full_url).path
        calls.append({"path": path, "timeout": timeout, "body": json.loads(request.data)})
        response = client.post(
            path,
            headers=dict(request.header_items()),
            content=request.data,
        )
        return BridgeResponse(response)

    monkeypatch.setattr("urllib.request.urlopen", api_urlopen)
    sdk = MemoraClient(base_url="https://memora.synthetic.invalid")
    result = sdk.learn_from_outcome(
        "friday",
        task_name="synthetic local migration rehearsal",
        status="success",
        actions_taken="validated the dry-run plan",
        context="isolated test fixture",
        domain="synthetic-testing",
        namespace_path="memora://friday/private",
    )

    assert result["status"] == "learned"
    assert result["agent"] == "friday"
    assert result["memory_type"] == "experience"
    assert calls[0]["path"] == "/v1/memories/learn-outcome"
    assert calls[0]["body"]["agent_name"] == "friday"
    assert calls[0]["body"]["domain"] == "synthetic-testing"
    assert calls[0]["body"]["namespace_path"] == "memora://friday/private"

    record = test_db.query(MemoryRecord).filter_by(id=result["id"]).one()
    assert record.tenant_id == "default"
    assert record.owner_id == mesh["agents"]["friday"].id
    assert record.memory_type == MemoryType.EXPERIENCE
    assert record.namespace.path == "memora://friday/private"
    assert "synthetic-testing" in result["synthesized_rule"]


def test_agent_endpoints_do_not_select_or_list_foreign_tenant_identities(
    client, mesh, test_db
):
    IdentityService.register_agent(test_db, "foreign-only-agent", tenant_id="tenant-foreign")

    listed = client.get("/agents", headers=mesh["friday"])
    assert listed.status_code == 200
    assert "foreign-only-agent" not in {item["name"] for item in listed.json()}

    fetched = client.get("/agents/foreign-only-agent", headers=mesh["friday"])
    assert fetched.status_code == 404

    attempted_subagent = client.post(
        "/agents/subagents",
        headers=mesh["friday"],
        json={
            "name": "foreign-helper",
            "tenant_id": "tenant-foreign",
            "bounded_scope": "memora://friday/projects/foreign",
        },
    )
    assert attempted_subagent.status_code == 403
    assert test_db.query(Agent).filter(
        Agent.tenant_id == "tenant-foreign",
        Agent.name == "friday:foreign-helper",
    ).count() == 0


def test_delegation_fails_closed_for_same_named_identity_in_foreign_tenant(
    client, monkeypatch, test_db
):
    """Without a tenant claim, a foreign same-name agent must not be selected."""
    api_key = "phase16-foreign-tenant-key"
    monkeypatch.delenv("MEMORA_ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("FRIDAY_API_KEY", api_key)

    IdentityService.register_agent(test_db, "friday", tenant_id="tenant-foreign")
    child = IdentityService.register_subagent(
        test_db,
        parent_agent_name="friday",
        subagent_name="foreign-worker",
        bounded_scope="memora://friday/projects/foreign",
        tenant_id="tenant-foreign",
    )
    response = client.post(
        "/v1/context",
        headers={"X-Agent-Name": "friday", "X-API-Key": api_key},
        json={"agent_id": child.name, "task_query": "foreign tenant context"},
    )
    assert response.status_code == 403


def test_context_endpoint_rejects_unrelated_agent_but_allows_bounded_child(
    client, mesh, test_db
):

    unrelated = client.post(
        "/v1/context",
        headers=mesh["friday"],
        json={"agent_id": "forge", "task_query": "private context probe"},
    )
    assert unrelated.status_code == 403

    scope = "memora://friday/projects/phase16"
    friday = mesh["agents"]["friday"]
    IdentityService.resolve_namespace(
        test_db,
        scope,
        owner_agent_id=friday.id,
        default_type=NamespaceType.PROJECT_PRIVATE,
    )
    child = IdentityService.register_subagent(
        test_db,
        parent_agent_name="friday",
        subagent_name="phase16-worker",
        bounded_scope=scope,
    )
    MemoryWriteService.execute_pipeline(
        db=test_db,
        caller_name="friday",
        target_namespace_path=scope,
        content_text="synthetic bounded context carries valve calibration 31 Nm",
    )

    delegated = client.post(
        "/v1/context",
        headers=mesh["friday"],
        json={
            "agent_id": child.name,
            "namespace_path": scope,
            "task_query": "valve calibration",
            "token_budget": 200,
        },
    )
    assert delegated.status_code == 200, delegated.text
    assert delegated.json()["target_agent"] == child.name
    assert any("valve calibration" in m["content_text"] for m in delegated.json()["memories"])


def test_delete_does_not_fall_back_to_same_named_agent_in_foreign_tenant(
    client, mesh, test_db
):
    foreign = IdentityService.register_agent(test_db, "friday", tenant_id="tenant-foreign")
    namespace = IdentityService.get_namespace_by_path(
        test_db, "memora://friday/private", tenant_id="tenant-foreign"
    )
    record = MemoryRecord(
        id="phase16-foreign-tenant-delete-probe",
        tenant_id="tenant-foreign",
        namespace_id=namespace.id,
        owner_id=foreign.id,
        memory_type=MemoryType.EPISODIC,
        content_text="synthetic foreign-tenant record that Friday cannot delete",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add(record)
    test_db.commit()

    response = client.delete(
        f"/v1/memories/{record.id}", headers=mesh["friday"]
    )
    assert response.status_code == 404
    assert test_db.query(MemoryRecord).filter(MemoryRecord.id == record.id).count() == 1


def test_context_rejects_oversized_task_query_before_retrieval(client, mesh):
    response = client.post(
        "/v1/context",
        headers=mesh["friday"],
        json={"task_query": "x" * 4097, "token_budget": 100},
    )
    assert response.status_code == 422


def test_non_owner_cannot_delete_another_agents_global_memory(client, mesh, test_db):
    # An administrator must provision the open global namespace first; ordinary
    # writers may append only after it exists, not create the open root themselves.
    IdentityService.resolve_namespace(
        test_db,
        "memora://universe/global",
        default_type=NamespaceType.UNIVERSE_GLOBAL,
    )
    result = MemoryWriteService.execute_pipeline(
        db=test_db,
        caller_name="friday",
        target_namespace_path="memora://universe/global",
        content_text="synthetic global memory owned by friday",
    )

    denied = client.delete(f"/v1/memories/{result.record.id}", headers=mesh["forge"])
    assert denied.status_code == 403
    assert test_db.query(MemoryRecord).filter(MemoryRecord.id == result.record.id).first() is not None

    owner_delete = client.delete(f"/v1/memories/{result.record.id}", headers=mesh["friday"])
    assert owner_delete.status_code == 200


def test_duplicate_response_requires_read_access_not_only_write_access(
    client, mesh, test_db
):
    friday = mesh["agents"]["friday"]
    forge = mesh["agents"]["forge"]
    path = "memora://friday/projects/write-only-dedup"
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
        purpose="synthetic write-only dedup regression",
    )
    MemoryWriteService.execute_pipeline(
        db=test_db,
        caller_name="friday",
        target_namespace_path=path,
        content_text="synthetic shared fact with confidential source detail",
    )

    response = client.post(
        "/v1/memories",
        headers=mesh["forge"],
        json={
            "target_namespace_path": path,
            "content_text": "synthetic shared fact with confidential source detail",
        },
    )
    assert response.status_code == 403


def test_semantic_tier_requires_explicit_verified_evidence(client, mesh):
    response = client.post(
        "/v1/memories",
        headers=mesh["friday"],
        json={
            "content_text": "synthetic unverified semantic assertion",
            "memory_type": "semantic",
            "source": "human_note",
        },
    )
    assert response.status_code == 403


def test_legacy_ingestion_runs_security_scans(client, mesh):
    response = client.post(
        "/memories",
        headers=mesh["friday"],
        json={
            "content_text": "api key sk-1234567890abcdef1234567890abcdef leaked",
            "memory_type": "episodic",
            "source": "legacy-test",
        },
    )
    assert response.status_code == 422


def test_legacy_ingestion_cannot_set_trusted_lifecycle_state(client, mesh):
    response = client.post(
        "/memories",
        headers=mesh["friday"],
        json={
            "content_text": "synthetic unreviewed record",
            "memory_type": "episodic",
            "lifecycle_state": "verified",
        },
    )
    assert response.status_code == 403


def test_reflection_is_tenant_and_namespace_scoped(client, mesh, test_db):
    friday = mesh["agents"]["friday"]
    friday_ns = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")
    secret = MemoryRecord(
        tenant_id="default",
        namespace_id=friday_ns.id,
        owner_id=friday.id,
        memory_type=MemoryType.EPISODIC,
        content_text="private reflection sentinel 42 Nm cobalt-finch",
        lifecycle_state=LifecycleState.ACTIVE,
        confidence=1.0,
        importance=0.9,
    )
    foreign_tenant_insight = MemoryRecord(
        tenant_id="tenant-outsider",
        namespace_id=IdentityService.resolve_namespace(
            test_db,
            "memora://memora/reflections",
            tenant_id="tenant-outsider",
        ).id,
        owner_id=IdentityService.register_agent(
            test_db, "memora", tenant_id="tenant-outsider", role="supervisor"
        ).id,
        memory_type=MemoryType.EXPERIENCE,
        content_text="[REFLECTION:contradiction] foreign-tenant-only-result",
        lifecycle_state=LifecycleState.ACTIVE,
        confidence=0.9,
        importance=0.9,
        provenance={
            "source": "reflection_engine",
            "reflection_kind": "contradiction",
            "reflection_subject": "foreign-tenant-only-result",
            "reflection_evidence": ["private evidence"],
        },
    )
    test_db.add_all([secret, foreign_tenant_insight])
    test_db.commit()

    dry_run = client.post(
        "/v1/reflection/run",
        headers=mesh["forge"],
        json={"dry_run": True},
    )
    assert dry_run.status_code == 200
    assert "cobalt-finch" not in dry_run.text
    assert "private reflection sentinel" not in dry_run.text

    listed = client.get("/v1/reflection/insights", headers=mesh["forge"])
    assert listed.status_code == 200
    assert "foreign-tenant-only-result" not in listed.text

    admin_listed = client.get("/v1/reflection/insights", headers=mesh["memora"])
    assert admin_listed.status_code == 200
    assert "foreign-tenant-only-result" not in admin_listed.text


def test_reflection_insights_from_private_corpus_are_not_disclosed_to_peer(
    client, mesh, test_db
):
    friday = mesh["agents"]["friday"]
    namespace = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")
    for text in (
        "quartz compressor seal torque is 42 Nm",
        "quartz compressor seal torque is 38 Nm",
    ):
        test_db.add(MemoryRecord(
            tenant_id="default",
            namespace_id=namespace.id,
            owner_id=friday.id,
            memory_type=MemoryType.EPISODIC,
            content_text=text,
            lifecycle_state=LifecycleState.ACTIVE,
            confidence=1.0,
            importance=0.8,
        ))
    test_db.commit()

    stored = client.post(
        "/v1/reflection/run",
        headers=mesh["memora"],
        json={"dry_run": False},
    )
    assert stored.status_code == 200
    assert stored.json()["stored"] > 0

    peer_view = client.get("/v1/reflection/insights", headers=mesh["forge"])
    assert peer_view.status_code == 200
    assert "quartz" not in peer_view.text
    assert "42" not in peer_view.text

    admin_view = client.get("/v1/reflection/insights", headers=mesh["memora"])
    assert "quartz" in admin_view.text


def test_decay_is_admin_only_and_scoped_to_admin_tenant(client, mesh, test_db):
    nexus = IdentityService.register_agent(test_db, "nexus")
    nexus_ns = IdentityService.get_namespace_by_path(test_db, "memora://nexus/private")
    foreign = IdentityService.register_agent(test_db, "nexus", tenant_id="tenant-outsider")
    foreign_ns = IdentityService.get_namespace_by_path(
        test_db, "memora://nexus/private", tenant_id="tenant-outsider"
    )
    old_time = datetime.now(timezone.utc) - timedelta(days=30)
    local_record = MemoryRecord(
        tenant_id="default",
        namespace_id=nexus_ns.id,
        owner_id=nexus.id,
        memory_type=MemoryType.EPISODIC,
        content_text="synthetic local aging record",
        confidence=0.7,
        importance=0.25,
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=old_time,
    )
    foreign_record = MemoryRecord(
        tenant_id="tenant-outsider",
        namespace_id=foreign_ns.id,
        owner_id=foreign.id,
        memory_type=MemoryType.EPISODIC,
        content_text="synthetic foreign aging record",
        confidence=0.7,
        importance=0.25,
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=old_time,
    )
    test_db.add_all([local_record, foreign_record])
    test_db.commit()

    user_run = client.post("/v1/memories/decay", headers=mesh["friday"], json={})
    assert user_run.status_code == 403

    response = client.post(
        "/v1/memories/decay",
        headers=mesh["memora"],
        json={"decay_rate_per_day": 0.05, "unverified_threshold_days": 7},
    )
    assert response.status_code == 200, response.text
    test_db.refresh(local_record)
    test_db.refresh(foreign_record)
    assert local_record.importance < 0.25
    assert foreign_record.importance == 0.25


def test_decay_rejects_unbounded_parameters(client, mesh):
    response = client.post(
        "/v1/memories/decay",
        headers=mesh["memora"],
        json={"decay_rate_per_day": -1.0},
    )
    assert response.status_code == 422


def _reranked(record, score=0.9):
    return RerankedMemoryItem(
        record=record,
        final_score=score,
        relevance_score=score,
        cross_encoder_score=score,
        confidence_weight=1.0,
        freshness_weight=1.0,
        importance_weight=1.0,
    )


def test_context_budgeter_truncates_oversized_singletons(test_db, mesh):
    agent = mesh["agents"]["friday"]
    namespace = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")
    record = MemoryRecord(
        namespace_id=namespace.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="x" * 10_000,
        lifecycle_state=LifecycleState.ACTIVE,
        confidence=1.0,
        importance=1.0,
    )
    test_db.add(record)
    test_db.commit()

    items, tokens, strategy = ContextBudgeter.fit_to_budget([_reranked(record)], max_tokens=7)
    assert tokens <= 7
    assert len(items) == 1
    assert items[0].is_truncated is True
    assert len(items[0].content_text) < len(record.content_text)
    assert strategy == "truncated"


def test_context_budgeter_never_exceeds_budget_with_many_clusters(test_db, mesh):
    agent = mesh["agents"]["friday"]
    items = []
    for index in range(5):
        namespace = IdentityService.resolve_namespace(
            test_db,
            f"memora://friday/projects/budget-{index}",
            owner_agent_id=agent.id,
            default_type=NamespaceType.PROJECT_PRIVATE,
        )
        record = MemoryRecord(
            namespace_id=namespace.id,
            owner_id=agent.id,
            memory_type=MemoryType.EPISODIC,
            content_text=(f"cluster {index} " + "long-content " * 100),
            lifecycle_state=LifecycleState.ACTIVE,
            confidence=1.0,
            importance=1.0,
        )
        test_db.add(record)
        items.append(_reranked(record, score=1.0 - index / 10))
    test_db.commit()

    packed, tokens, _strategy = ContextBudgeter.fit_to_budget(items, max_tokens=2)
    assert tokens <= 2
    assert len(packed) <= 2


def test_semantic_promotion_is_authorized_and_body_cannot_spoof_actor(
    client, mesh, test_db
):
    result = MemoryWriteService.execute_pipeline(
        db=test_db,
        caller_name="friday",
        content_text="synthetic field observation reports compressor pressure at 4.1 bar",
        confidence=0.7,
    )
    memory_id = result.record.id
    evidence = {"verification_evidence": ["synthetic:pressure-gauge-01"]}

    forged = client.post(
        f"/v1/memories/{memory_id}/promote",
        headers=mesh["forge"],
        json={**evidence, "promoted_by": "friday", "target_confidence": 0.96},
    )
    assert forged.status_code == 403
    test_db.refresh(result.record)
    assert result.record.memory_type == MemoryType.EPISODIC
    assert result.record.lifecycle_state == LifecycleState.ACTIVE

    owner = client.post(
        f"/v1/memories/{memory_id}/promote",
        headers=mesh["friday"],
        json={**evidence, "promoted_by": "memora", "target_confidence": 0.96},
    )
    assert owner.status_code == 200, owner.text
    promoted = owner.json()
    assert promoted["memory_type"] == "semantic"
    assert promoted["lifecycle_state"] == "verified"
    assert promoted["provenance"]["promoted_by"] == "friday"


def test_share_requires_namespace_authority_and_rejects_wildcard_grants(
    client, mesh, test_db
):
    result = MemoryWriteService.execute_pipeline(
        db=test_db,
        caller_name="friday",
        content_text="synthetic private maintenance note for share authorization test",
    )
    forge = mesh["agents"]["forge"]

    denied = client.post(
        f"/v1/memories/{result.record.id}/share",
        headers=mesh["forge"],
        json={"target_agent_name": "memora", "actions": ["read"]},
    )
    assert denied.status_code == 403
    assert test_db.query(AccessGrant).filter(
        AccessGrant.tenant_id == "default",
        AccessGrant.agent_id == mesh["agents"]["memora"].id,
        AccessGrant.namespace_id == result.record.namespace_id,
    ).count() == 0

    wildcard = client.post(
        f"/v1/memories/{result.record.id}/share",
        headers=mesh["friday"],
        json={"target_agent_name": "forge", "actions": ["*"]},
    )
    assert wildcard.status_code == 422

    shared = client.post(
        f"/v1/memories/{result.record.id}/share",
        headers=mesh["friday"],
        json={"target_agent_name": "forge", "actions": ["read"], "ttl_hours": 1},
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["scope"] == "namespace"
    assert shared.json()["shared_with"] == forge.name
    assert shared.json()["actions"] == ["read"]


def test_verify_is_tenant_scoped_authorized_and_idempotent(client, mesh, test_db):
    result = MemoryWriteService.execute_pipeline(
        db=test_db,
        caller_name="friday",
        content_text="synthetic candidate note for lifecycle verification",
        confidence=0.4,
    )
    memory_id = result.record.id

    denied = client.post(
        f"/v1/memories/{memory_id}/verify", headers=mesh["forge"], json={}
    )
    assert denied.status_code == 403
    test_db.refresh(result.record)
    assert result.record.lifecycle_state == LifecycleState.ACTIVE

    first = client.post(
        f"/v1/memories/{memory_id}/verify", headers=mesh["friday"], json={}
    )
    assert first.status_code == 200, first.text
    verified_confidence = first.json()["confidence"]
    assert verified_confidence == pytest.approx(0.5)

    second = client.post(
        f"/v1/memories/{memory_id}/verify", headers=mesh["friday"], json={}
    )
    assert second.status_code == 200, second.text
    assert second.json()["confidence"] == pytest.approx(verified_confidence)


def test_supersede_requires_authority_over_both_records(client, mesh, test_db):
    old = MemoryWriteService.execute_pipeline(
        db=test_db,
        caller_name="friday",
        content_text="synthetic established compressor maintenance procedure",
        confidence=0.9,
    ).record
    new = MemoryWriteService.execute_pipeline(
        db=test_db,
        caller_name="forge",
        content_text="synthetic replacement compressor procedure from Forge",
        confidence=0.9,
    ).record

    response = client.post(
        f"/v1/memories/{old.id}/supersede",
        headers=mesh["friday"],
        json={"new_memory_id": new.id},
    )
    assert response.status_code == 403
    test_db.refresh(old)
    test_db.refresh(new)
    assert old.lifecycle_state == LifecycleState.ACTIVE
    assert new.lifecycle_state == LifecycleState.ACTIVE


def test_api_idempotency_burst_returns_one_durable_record(
    client, monkeypatch, tmp_path
):
    """Stress the authenticated ASGI path with concurrent identical retries."""
    api_key = "phase16-concurrent-api-test-key"
    monkeypatch.delenv("MEMORA_ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("FRIDAY_API_KEY", api_key)

    engine = create_engine(
        f"sqlite:///{tmp_path / 'api-burst.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    setup = factory()
    try:
        IdentityService.register_agent(setup, "friday")
    finally:
        setup.close()

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    previous_override = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(v1_memories_router, "SessionLocal", factory)

    workers = 16
    barrier = threading.Barrier(workers)
    responses = []
    errors = []
    lock = threading.Lock()
    payload = {
        "content_text": "synthetic ASGI retry burst stores one logical service note",
        "idempotency_key": "phase16-api-burst-001",
    }
    headers = {"X-Agent-Name": "friday", "X-API-Key": api_key}

    def request_write():
        try:
            barrier.wait(timeout=30)
            response = client.post("/v1/memories", json=payload, headers=headers)
            with lock:
                responses.append(response)
        except Exception as exc:  # noqa: BLE001 - record thread failures verbatim
            with lock:
                errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=request_write) for _ in range(workers)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)

        assert not any(thread.is_alive() for thread in threads), "an ASGI writer thread hung"
        assert not errors, errors[:3]
        assert len(responses) == workers
        failures = [response for response in responses if response.status_code != 201]
        assert not failures, [(r.status_code, r.text) for r in failures[:3]]

        bodies = [response.json() for response in responses]
        assert len({body["id"] for body in bodies}) == 1
        assert sum(not body["is_duplicate"] for body in bodies) == 1
        assert sum(body["is_duplicate"] for body in bodies) == workers - 1

        check = factory()
        try:
            assert check.query(MemoryRecord).filter(
                MemoryRecord.idempotency_key == payload["idempotency_key"]
            ).count() == 1
        finally:
            check.close()
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_override
        engine.dispose()
