"""
Comprehensive Tests for Identity, Namespace Resolution, and 5D Policy Engine
"""
from datetime import datetime, timezone, timedelta
import pytest
from storage.relational.models import (
    Agent,
    Namespace,
    NamespaceType,
    AccessGrant,
    AuditLog,
    MemoryRecord,
    MemoryType,
    LifecycleState
)
from core.identity.service import IdentityService
from core.policy.engine import PolicyEngine, PolicyDecision
from core.memory.service import MemoryService, PermissionDeniedError
from core.memory.schemas import MemoryRecordCreate, MemoryQuery

def test_rule_1_private_by_default_isolation(test_db):
    """
    Test Rule 1: 'Private by default, shared by explicit promotion.'
    FORGE cannot read FRIDAY's private namespace, and vice versa.
    """
    # Create agents
    friday = IdentityService.register_agent(test_db, "friday", role="supervisor")
    forge = IdentityService.register_agent(test_db, "forge", role="worker")

    # FRIDAY's private namespace
    friday_private = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")
    assert friday_private is not None
    assert friday_private.type == NamespaceType.AGENT_PRIVATE

    # FORGE's private namespace
    forge_private = IdentityService.get_namespace_by_path(test_db, "memora://forge/private")
    assert forge_private is not None
    assert forge_private.type == NamespaceType.AGENT_PRIVATE

    # 1. FORGE attempts to read FRIDAY's private namespace -> MUST BE DENIED
    forge_eval = PolicyEngine.evaluate_access(
        test_db,
        actor=forge,
        namespace=friday_private,
        action="read",
        purpose="attempted_unauthorized_read"
    )
    assert forge_eval.allowed is False
    assert "private to another agent" in forge_eval.reason
    assert forge_eval.rule_matched == "RULE_1_PRIVATE_BY_DEFAULT_PROMOTION_REQUIRED"

    # 2. FORGE attempts to write to FRIDAY's private namespace -> MUST BE DENIED
    forge_write = PolicyEngine.evaluate_access(
        test_db,
        actor=forge,
        namespace=friday_private,
        action="write"
    )
    assert forge_write.allowed is False

    # 3. FORGE accessing its OWN private namespace -> MUST BE ALLOWED
    forge_own = PolicyEngine.evaluate_access(
        test_db,
        actor=forge,
        namespace=forge_private,
        action="read"
    )
    assert forge_own.allowed is True
    assert forge_own.rule_matched == "RULE_1_OWNER_PRIVATE_ACCESS"

def test_rule_2_explicit_access_grant_and_shared_namespace(test_db):
    """
    Test Rule 2: Project-shared namespaces require explicit project membership / grant.
    Both FORGE and FRIDAY can read a 'shared/approved' namespace when explicitly granted access.
    """
    friday = IdentityService.register_agent(test_db, "friday")
    forge = IdentityService.register_agent(test_db, "forge")
    nexus = IdentityService.register_agent(test_db, "nexus")

    # Shared project namespace
    shared_ns = IdentityService.resolve_namespace(
        test_db,
        path="memora://projects/shared-intelligence",
        default_type=NamespaceType.PROJECT_PRIVATE
    )

    # Initially, neither FORGE nor NEXUS has access
    assert PolicyEngine.evaluate_access(test_db, forge, shared_ns, "read").allowed is False
    assert PolicyEngine.evaluate_access(test_db, nexus, shared_ns, "read").allowed is False

    # Explicitly grant access to FORGE
    IdentityService.grant_access(
        test_db,
        agent_id=forge.id,
        namespace_id=shared_ns.id,
        actions=["read", "query"],
        purpose="Project intelligence sharing"
    )

    # Explicitly grant access to NEXUS
    IdentityService.grant_access(
        test_db,
        agent_id=nexus.id,
        namespace_id=shared_ns.id,
        actions=["read", "write"],
        purpose="Web interface collaboration"
    )

    # Now FORGE can read
    forge_eval = PolicyEngine.evaluate_access(test_db, forge, shared_ns, "read")
    assert forge_eval.allowed is True
    assert forge_eval.rule_matched == "RULE_2_PROJECT_MEMBERSHIP_GRANT_ACTIVE"

    # NEXUS can read and write
    assert PolicyEngine.evaluate_access(test_db, nexus, shared_ns, "read").allowed is True
    assert PolicyEngine.evaluate_access(test_db, nexus, shared_ns, "write").allowed is True

    # FORGE cannot write (only granted read/query)
    assert PolicyEngine.evaluate_access(test_db, forge, shared_ns, "write").allowed is False

    # FRIDAY (supervisor) has access
    assert PolicyEngine.evaluate_access(test_db, friday, shared_ns, "read").allowed is True

def test_subagent_bounded_context_isolation(test_db):
    """
    Test Sub-Agent bounded context:
    A sub-agent can only access its designated bounded scope (e.g. memora://forge/projects/app-17),
    and is strictly blocked from accessing the parent's full private namespace.
    """
    forge = IdentityService.register_agent(test_db, "forge", role="worker")
    forge_private = IdentityService.get_namespace_by_path(test_db, "memora://forge/private")

    # Create sub-agent under FORGE bounded to a specific project
    subagent_scope = "memora://forge/projects/app-17"
    coder_subagent = IdentityService.register_subagent(
        test_db,
        parent_agent_name="forge",
        subagent_name="coder",
        bounded_scope=subagent_scope,
        description="Subagent for app-17 frontend coding"
    )

    assert coder_subagent.parent_agent_id == forge.id
    assert coder_subagent.bounded_scope == subagent_scope
    assert coder_subagent.role == "subagent"

    project_ns = IdentityService.get_namespace_by_path(test_db, subagent_scope)
    assert project_ns is not None

    # 1. Sub-agent can access its designated bounded scope
    sub_eval = PolicyEngine.evaluate_access(test_db, coder_subagent, project_ns, "read")
    assert sub_eval.allowed is True

    # 2. Sub-agent is STRICTLY FORBIDDEN from reading parent's private namespace
    parent_private_eval = PolicyEngine.evaluate_access(test_db, coder_subagent, forge_private, "read")
    assert parent_private_eval.allowed is False
    assert parent_private_eval.rule_matched == "RULE_3_SUBAGENT_BOUNDED_CONTEXT_ISOLATION"
    assert "strictly forbidden from accessing private namespace" in parent_private_eval.reason

    # 3. Sub-agent is forbidden from another project scope outside its boundary
    other_project_ns = IdentityService.resolve_namespace(test_db, "memora://forge/projects/app-99")
    outside_eval = PolicyEngine.evaluate_access(test_db, coder_subagent, other_project_ns, "read")
    assert outside_eval.allowed is False
    assert outside_eval.rule_matched == "RULE_3_SUBAGENT_SCOPE_EXCEEDED"

def test_time_bounded_and_purpose_grants(test_db):
    """
    Test 5D Policy Engine: How long (expiration) & Why (purpose)
    """
    intelx = IdentityService.register_agent(test_db, "intelx")
    target_ns = IdentityService.resolve_namespace(test_db, "memora://research/quantum")

    # Time-bounded grant in the past (expired)
    past_time = datetime.now(timezone.utc) - timedelta(hours=2)
    expired_grant = IdentityService.grant_access(
        test_db,
        agent_id=intelx.id,
        namespace_id=target_ns.id,
        actions=["read"],
        purpose="Time-limited research task",
        expires_at=past_time
    )

    # Should be rejected due to expiration
    expired_eval = PolicyEngine.evaluate_access(test_db, intelx, target_ns, "read")
    assert expired_eval.allowed is False

    # Update to valid future expiration
    future_time = datetime.now(timezone.utc) + timedelta(days=7)
    valid_grant = IdentityService.grant_access(
        test_db,
        agent_id=intelx.id,
        namespace_id=target_ns.id,
        actions=["read", "write"],
        purpose="Active deep research project",
        expires_at=future_time
    )

    # Should be allowed
    future_eval = PolicyEngine.evaluate_access(test_db, intelx, target_ns, "read", purpose="Active deep research project")
    assert future_eval.allowed is True
    assert "Active deep research project" in future_eval.reason

def test_audit_trail_logging_on_every_policy_evaluation(test_db):
    """
    Test Audit Trail: Every single policy evaluation (both approved and denied)
    must create a permanent record in the AuditLog table with 5D dimensions.
    """
    initial_audit_count = test_db.query(AuditLog).count()

    agent1 = IdentityService.register_agent(test_db, "agent_one")
    agent2 = IdentityService.register_agent(test_db, "agent_two")
    ns2 = IdentityService.get_namespace_by_path(test_db, "memora://agent_two/private")

    # 1. Denied attempt
    PolicyEngine.evaluate_access(
        test_db,
        actor=agent1,
        namespace=ns2,
        action="read",
        purpose="probe_attempt"
    )

    # 2. Approved attempt
    PolicyEngine.evaluate_access(
        test_db,
        actor=agent2,
        namespace=ns2,
        action="read",
        purpose="legitimate_read"
    )

    # Verify audit logs
    logs = test_db.query(AuditLog).order_by(AuditLog.timestamp.desc()).all()
    assert len(logs) >= initial_audit_count + 2

    denied_log = next(l for l in logs if l.action == "policy_denied" and l.actor_id == agent1.id)
    assert denied_log.details["allowed"] is False
    assert denied_log.details["dimensions"]["why"]["purpose"] == "probe_attempt"

    approved_log = next(l for l in logs if l.action == "policy_approved" and l.actor_id == agent2.id)
    assert approved_log.details["allowed"] is True
    assert approved_log.details["dimensions"]["why"]["purpose"] == "legitimate_read"

def test_policy_action_permission_gating(test_db):
    """
    Test granular action capability gating: read grant cannot delete or supersede.
    """
    agent = IdentityService.register_agent(test_db, "auditor")
    target_ns = IdentityService.resolve_namespace(test_db, "memora://shared/compliance", default_type=NamespaceType.PROJECT_PRIVATE)

    # Grant read-only access
    IdentityService.grant_access(
        test_db,
        agent_id=agent.id,
        namespace_id=target_ns.id,
        actions=["read", "query"],
        purpose="Compliance audit"
    )

    # Read/Query allowed
    assert PolicyEngine.evaluate_access(test_db, agent, target_ns, "read").allowed is True
    assert PolicyEngine.evaluate_access(test_db, agent, target_ns, "query").allowed is True

    # Write, Verify, Supersede, Delete rejected
    assert PolicyEngine.evaluate_access(test_db, agent, target_ns, "write").allowed is False
    assert PolicyEngine.evaluate_access(test_db, agent, target_ns, "verify").allowed is False
    assert PolicyEngine.evaluate_access(test_db, agent, target_ns, "delete").allowed is False