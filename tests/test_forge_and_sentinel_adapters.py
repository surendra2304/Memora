"""
Integration Tests for FORGE & SENTINEL Specialized Adapters
Tests architectural decisions, sensitive security findings, explicit promotion to shared namespaces,
and strict cross-agent private namespace isolation.
"""
import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from adapters.adapter_registry import adapter_registry
from adapters.forge.adapter import ForgeAdapter
from adapters.sentinel.adapter import SentinelAdapter
from adapters.base_adapter import MemoraAccessDeniedError
from core.identity.service import IdentityService
from storage.relational.models import NamespaceType

@pytest.fixture
def mock_client():
    return TestClient(app)

def test_forge_and_sentinel_adapter_instantiations(mock_client):
    """
    Test AdapterRegistry instantiates specialized ForgeAdapter and SentinelAdapter classes.
    """
    forge_adapter = adapter_registry.get_adapter("forge", http_client=mock_client)
    assert isinstance(forge_adapter, ForgeAdapter)
    assert forge_adapter.default_namespace == "memora://forge/private"

    sentinel_adapter = adapter_registry.get_adapter("sentinel", http_client=mock_client)
    assert isinstance(sentinel_adapter, SentinelAdapter)
    assert sentinel_adapter.default_namespace == "memora://sentinel/private"

def test_forge_save_architecture_decision_and_retrieve_constraints(mock_client, test_db):
    """
    Test FORGE saving architecture decisions and querying coding constraints.
    """
    forge_adapter: ForgeAdapter = adapter_registry.get_adapter("forge", http_client=mock_client)

    # 1. Save architecture decision
    save_res = forge_adapter.save_architecture_decision(
        decision_text="Architectural decision: Use async PostgreSQL connection pool with maximum 20 workers for project-alpha.",
        project_id="alpha",
        confidence=1.0,
        importance=0.9
    )
    assert save_res["id"] is not None
    assert save_res["memory_type"] == "decision"
    assert save_res["step_trace"]["step_2_authenticate_and_resolve"]["namespace_path"] == "memora://forge/projects/alpha"

    # 2. Query constraints
    constraints = forge_adapter.get_coding_constraints("alpha", query="architectural decision postgresql")
    assert len(constraints) >= 1
    assert any("async PostgreSQL connection pool" in c["content_text"] for c in constraints)

def test_sentinel_private_finding_explicit_promotion_and_forge_access(mock_client, test_db):
    """
    Test full security lifecycle:
    1. SENTINEL records confidential finding in private namespace.
    2. FORGE attempts to write/read SENTINEL private namespace -> 403 Denied.
    3. SENTINEL promotes sanitized remediation to shared namespace and grants FORGE access.
    4. FORGE retrieves the shared remediation.
    5. FORGE remains blocked from SENTINEL private finding.
    """
    sentinel_adapter: SentinelAdapter = adapter_registry.get_adapter("sentinel", http_client=mock_client)
    forge_adapter: ForgeAdapter = adapter_registry.get_adapter("forge", http_client=mock_client)

    # 1. SENTINEL records confidential vulnerability finding
    private_res = sentinel_adapter.record_private_finding(
        finding_details="CRITICAL: Unsanitized SQL query parameter in user profile query endpoint on asset auth-core.",
        asset_id="auth-core",
        severity="CRITICAL"
    )
    private_mem_id = private_res["id"]
    assert private_res["step_trace"]["step_2_authenticate_and_resolve"]["namespace_path"] == "memora://sentinel/private"

    # 2. FORGE attempts unauthorized write into SENTINEL's private namespace
    with pytest.raises(MemoraAccessDeniedError) as exc_info:
        forge_adapter.write_memory(
            content_text="Attempting to tamper with SENTINEL private vulnerability logs.",
            target_namespace_path="memora://sentinel/private"
        )
    assert exc_info.value.status_code == 403
    assert "Access Denied" in str(exc_info.value)

    # 3. SENTINEL publishes approved, sanitized remediation to shared project namespace
    promotion_res = sentinel_adapter.publish_approved_remediation(
        remediation_text="SECURITY REMEDIATION: Always use parameterized SQLAlchemy ORM queries; raw string interpolation is prohibited in auth-core.",
        project_id="app-17",
        private_finding_id=private_mem_id,
        target_agent="forge"
    )
    assert promotion_res["status"] == "promoted"
    assert promotion_res["target_agent"] == "forge"
    assert promotion_res["shared_namespace"] == "memora://shared/projects/app-17"

    # 4. FORGE retrieves coding constraints for app-17 (including shared security guidance)
    shared_constraints = forge_adapter.get_coding_constraints("app-17", query="security remediation parameterized", include_shared=True)
    assert len(shared_constraints) >= 1
    found_remediation = any("Always use parameterized SQLAlchemy ORM queries" in c["content_text"] for c in shared_constraints)
    assert found_remediation is True

    # 5. FORGE remains blocked from SENTINEL private namespace
    with pytest.raises(MemoraAccessDeniedError):
        forge_adapter.write_memory(
            content_text="Tampering check",
            target_namespace_path="memora://sentinel/private"
        )