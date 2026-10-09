"""
Tests for the multi-agent collaboration service.

Collaboration must never become a way around namespace isolation, so the
security properties are asserted alongside the happy paths: contributing someone
else's memory is refused, candidates are identified without disclosing private
content, and contribution grants expire.
"""

import pytest

from core.collaboration.service import CollaborationError, CollaborationService
from core.identity.service import IdentityService
from core.memory.pipeline.write_service import MemoryWriteService
from storage.relational.models import AccessGrant, MemoryRecord, Namespace


@pytest.fixture(autouse=True)
def _isolate_vector_store():
    from storage.vector.qdrant_adapter import vector_adapter

    vector_adapter._mock_store.clear()
    yield
    vector_adapter._mock_store.clear()


@pytest.fixture
def team(test_db):
    """friday holds knowledge in a shared namespace; forge and sentinel are peers."""
    friday = IdentityService.register_agent(test_db, "friday", role="supervisor")
    # bounded_scope is an enforced namespace-path prefix (PolicyEngine RULE_3),
    # not a description. A non-path value would lock forge out of every
    # namespace, so role/description carry the discoverable text instead.
    forge = IdentityService.register_agent(
        test_db, "forge", role="worker xenon compressor specialist"
    )
    sentinel = IdentityService.register_agent(test_db, "sentinel", role="security")
    # The canonical team-shared namespace may be initialized by its first
    # authenticated writer; the pipeline must not let that flow create arbitrary
    # universe/public or team-root namespaces.
    MemoryWriteService.execute_pipeline(
        test_db,
        caller_name="friday",
        target_namespace_path="memora://team/shared",
        content_text="xenon compressor seal torque must be 42 Nm after calibration",
    )
    return test_db, friday, forge, sentinel


# ---------------------------------------------------------------------------
# request_assistance
# ---------------------------------------------------------------------------

def test_requesting_assistance_returns_the_answer_already_visible(team):
    """A peer with a grant gets the answer inline, with no new access created."""
    db, _friday, forge, _sentinel = team

    # A "team-shared" namespace is not world-readable: PolicyEngine still
    # requires an explicit grant. Without one forge correctly sees nothing,
    # which test_assistance_requires_a_grant_to_shared_material asserts.
    shared = (
        db.query(Namespace).filter(Namespace.path == "memora://team/shared").first()
    )
    IdentityService.grant_access(
        db, agent_id=forge.id, namespace_id=shared.id, actions=["read", "query"]
    )

    result = CollaborationService.request_assistance(
        db, requester_name="forge", query="xenon compressor seal torque"
    )

    assert result.requester == "forge"
    assert result.immediately_usable, "granted knowledge should be usable immediately"
    assert any("42 Nm" in m["content_text"] for m in result.immediately_usable)


def test_assistance_requires_a_grant_to_shared_material(team):
    """Collaboration must not bypass PolicyEngine: no grant, no content."""
    db, _friday, forge, _sentinel = team

    result = CollaborationService.request_assistance(
        db, requester_name="forge", query="xenon compressor seal torque"
    )

    assert result.immediately_usable == [], "shared material leaked without a grant"


def test_candidates_are_identified_with_their_reason(team):
    db, _friday, _forge, _sentinel = team

    result = CollaborationService.request_assistance(
        db, requester_name="friday", query="xenon compressor repairs"
    )

    names = {c.agent_name for c in result.candidates}
    assert "forge" in names, "forge's role matches the query"
    forge_candidate = next(c for c in result.candidates if c.agent_name == "forge")
    assert "role/description matches" in forge_candidate.reason


def test_an_assistance_request_never_discloses_another_agents_private_content(team):
    """The core security property of collaboration."""
    db, _friday, forge, _sentinel = team

    # Sentinel writes something private.
    MemoryWriteService.execute_pipeline(
        db,
        caller_name="sentinel",
        target_namespace_path="memora://sentinel/private",
        content_text="SENTINEL PRIVATE: friday's access token is leaked-credential-value",
    )

    result = CollaborationService.request_assistance(
        db, requester_name="forge", query="sentinel private access token leaked credential"
    )

    blob = str(result.to_dict())
    assert "leaked-credential-value" not in blob, "private content leaked through collaboration"
    for entry in result.immediately_usable:
        assert entry["namespace_path"] != "memora://sentinel/private"


def test_an_unknown_requester_is_rejected_without_identity_provisioning(team):
    db, *_ = team
    with pytest.raises(CollaborationError, match="unknown requester"):
        CollaborationService.request_assistance(
            db, requester_name="newcomer", query="xenon compressor"
        )
    assert IdentityService.get_agent_by_name(
        db, "newcomer", tenant_id="default"
    ) is None


def test_assistance_response_serialises(team):
    db, *_ = team
    result = CollaborationService.request_assistance(db, "forge", "xenon compressor")
    payload = result.to_dict()
    assert set(payload) == {
        "requester", "query", "candidate_count", "candidates",
        "immediately_usable", "created_at",
    }
    import json
    json.dumps(payload)


# ---------------------------------------------------------------------------
# contribute
# ---------------------------------------------------------------------------

def test_contributing_creates_a_scoped_expiring_grant(team):
    db, friday, forge, _sentinel = team

    owned = (
        db.query(MemoryRecord).filter(MemoryRecord.owner_id == friday.id).first()
    )
    result = CollaborationService.contribute(
        db,
        contributor_name="friday",
        memory_id=owned.id,
        recipient_name="forge",
    )

    assert result["status"] == "contributed"
    assert result["to"] == "forge"
    assert result["expires_at"] is not None, "a contribution grant must expire"

    grant = db.query(AccessGrant).filter(AccessGrant.id == result["grant_id"]).first()
    assert grant is not None
    assert grant.agent_id == forge.id
    assert set(grant.actions) == {"read", "query"}
    assert grant.expires_at is not None


def test_an_agent_cannot_contribute_someone_elses_memory(team):
    """The critical guard: sharing must be the owner's decision."""
    db, friday, _forge, _sentinel = team

    fridays = db.query(MemoryRecord).filter(MemoryRecord.owner_id == friday.id).first()

    with pytest.raises(CollaborationError) as exc_info:
        CollaborationService.contribute(
            db,
            contributor_name="sentinel",   # does not own it
            memory_id=fridays.id,
            recipient_name="forge",
        )
    assert "does not own" in str(exc_info.value)


def test_contributing_to_yourself_is_refused(team):
    db, friday, *_ = team
    owned = db.query(MemoryRecord).filter(MemoryRecord.owner_id == friday.id).first()

    with pytest.raises(CollaborationError) as exc_info:
        CollaborationService.contribute(
            db, contributor_name="friday", memory_id=owned.id, recipient_name="friday"
        )
    assert "itself" in str(exc_info.value)


def test_contributing_a_nonexistent_memory_is_refused(team):
    db, *_ = team
    with pytest.raises(CollaborationError) as exc_info:
        CollaborationService.contribute(
            db, contributor_name="friday", memory_id="does-not-exist", recipient_name="forge"
        )
    assert "not found" in str(exc_info.value)


def test_a_contribution_is_audited(team):
    db, friday, forge, _sentinel = team
    from storage.relational.models import AuditLog

    owned = db.query(MemoryRecord).filter(MemoryRecord.owner_id == friday.id).first()
    before = db.query(AuditLog).count()

    CollaborationService.contribute(
        db, contributor_name="friday", memory_id=owned.id, recipient_name="forge"
    )

    after = db.query(AuditLog).all()
    assert len(after) > before
    assert any((a.details or {}).get("rule_matched") == "COLLABORATION_CONTRIBUTE" for a in after)


# ---------------------------------------------------------------------------
# delegate
# ---------------------------------------------------------------------------

def test_delegating_creates_a_bounded_subagent(team):
    db, friday, *_ = team

    result = CollaborationService.delegate(
        db,
        delegator_name="friday",
        subagent_name="forge_helper",
        task_description="recheck the xenon seal torque",
    )

    assert result["status"] == "delegated"
    # register_subagent namespaces children as "<parent>:<child>".
    assert result["subagent"] == "friday:forge_helper"
    assert result["bounded_scope"]

    subagent = IdentityService.get_agent_by_name(db, "friday:forge_helper")
    assert subagent is not None
    assert subagent.parent_agent_id == friday.id


def test_delegation_is_audited(team):
    db, *_ = team
    from storage.relational.models import AuditLog

    CollaborationService.delegate(
        db, delegator_name="friday", subagent_name="audit_probe",
        task_description="probe task",
    )
    assert any(
        (a.details or {}).get("rule_matched") == "COLLABORATION_DELEGATE"
        for a in db.query(AuditLog).all()
    )


def test_delegating_from_an_unknown_agent_is_refused(team):
    db, *_ = team
    with pytest.raises(CollaborationError) as exc_info:
        CollaborationService.delegate(
            db, delegator_name="ghost", subagent_name="x", task_description="y"
        )
    assert "unknown delegator" in str(exc_info.value)
