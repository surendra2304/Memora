"""
Unit and Integration Tests for MEMORA Ecosystem Agent Adapters
Tests BaseAgentAdapter, AdapterRegistry, 403 Policy Denial Handling, and End-to-End Adapter Workflows.
"""
import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from adapters.base_adapter import (
    BaseAgentAdapter,
    MemoraAdapterError,
    MemoraAccessDeniedError,
    MemoraSecurityViolationError,
    MemoraNotFoundError
)
from adapters.adapter_registry import AdapterRegistry, adapter_registry
from core.identity.service import IdentityService
from storage.relational.models import NamespaceType

@pytest.fixture
def mock_api_client():
    return TestClient(app)

def test_adapter_registry_configuration_loading():
    """
    Test AdapterRegistry loads all ecosystem agents and default namespaces from YAML config.
    """
    configured_agents = adapter_registry.list_configured_agents()
    expected_agents = ["friday", "forge", "futuris", "intelx", "mt5", "nexus", "sentinel", "ai_universe"]
    for agent in expected_agents:
        assert agent in configured_agents

    # Check FRIDAY config
    friday_cfg = adapter_registry.get_agent_config("friday")
    assert friday_cfg["role"] == "supervisor"
    assert friday_cfg["default_namespace"] == "memora://friday/private"

    # Check AI Universe config
    universe_cfg = adapter_registry.get_agent_config("ai_universe")
    assert universe_cfg["default_namespace"] == "memora://universe/global"

def test_base_agent_adapter_request_formatting_and_write(mock_api_client, test_db):
    """
    Test BaseAgentAdapter properly formats headers (X-Agent-Name) and writes memories.
    """
    adapter = adapter_registry.get_adapter("forge", http_client=mock_api_client)
    assert adapter.agent_name == "forge"
    assert adapter.default_namespace == "memora://forge/private"

    # Write memory
    res = adapter.write_memory(
        content_text="Refactored the authentication token refresh pipeline.",
        memory_type="procedural",
        confidence=0.95,
        importance=0.85,
        purpose="Refactoring task"
    )
    assert res["id"] is not None
    assert res["content_text"] == "Refactored the authentication token refresh pipeline."
    assert res["memory_type"] == "procedural"
    assert res["step_trace"]["step_2_authenticate_and_resolve"]["actor_name"] == "forge"

def test_base_agent_adapter_search_and_context_retrieval(mock_api_client, test_db):
    """
    Test BaseAgentAdapter search_memories and get_context methods.
    """
    adapter = adapter_registry.get_adapter("nexus", http_client=mock_api_client)

    # Ingest record
    adapter.write_memory(
        content_text="Vite UI dashboard layout component hierarchy.",
        memory_type="project",
        confidence=1.0,
        importance=0.9
    )

    # Search
    search_results = adapter.search_memories(query="Vite UI dashboard", limit=5)
    assert isinstance(search_results, list)
    assert len(search_results) >= 1
    assert "Vite UI dashboard" in search_results[0]["content_text"]

    # Get Context Bundle
    bundle = adapter.get_context(task_query="Vite dashboard component", token_budget=2000)
    assert bundle["bundle_id"] is not None
    assert bundle["target_agent"] == "nexus"
    assert bundle["memories_count"] >= 1
    assert "summary" in bundle

def test_base_agent_adapter_graceful_403_access_denied_handling(mock_api_client, test_db):
    """
    Test that BaseAgentAdapter catches HTTP 403 policy rejections and raises MemoraAccessDeniedError.
    """
    # Create private namespace for FRIDAY with a secret decision memory
    friday = IdentityService.register_agent(test_db, "friday")
    ns_friday = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")

    friday_adapter = adapter_registry.get_adapter("friday", http_client=mock_api_client)
    res = friday_adapter.write_memory(
        content_text="Secret supervisor master architectural encryption tokens.",
        target_namespace_path="memora://friday/private"
    )
    secret_mem_id = res["id"]

    # FORGE attempts to write directly into FRIDAY's private namespace -> 403 Forbidden
    forge_adapter = adapter_registry.get_adapter("forge", http_client=mock_api_client)

    with pytest.raises(MemoraAccessDeniedError) as exc_info:
        forge_adapter.write_memory(
            content_text="Attempting unauthorized write to FRIDAY store.",
            target_namespace_path="memora://friday/private"
        )
    assert exc_info.value.status_code == 403
    assert "Access Denied" in str(exc_info.value)

def test_base_agent_adapter_security_violation_handling(mock_api_client):
    """
    Test that BaseAgentAdapter catches HTTP 422 secret leak rejections and raises MemoraSecurityViolationError.
    """
    adapter = adapter_registry.get_adapter("futuris", http_client=mock_api_client)

    with pytest.raises(MemoraSecurityViolationError) as exc_info:
        adapter.write_memory(
            content_text="Leaking raw key: sk-live-abcdef12345678901234567890abcdef"
        )
    assert exc_info.value.status_code == 422
    assert "Security/Validation Violation" in str(exc_info.value)

def test_base_agent_adapter_verify_and_share_workflows(mock_api_client, test_db):
    """
    Test BaseAgentAdapter verify_memory and share_memory lifecycle methods.
    """
    friday_adapter = adapter_registry.get_adapter("friday", http_client=mock_api_client)

    # Ingest memory
    write_res = friday_adapter.write_memory(
        content_text="Ecosystem unified protocol v2 release notes."
    )
    mem_id = write_res["id"]

    # Verify
    verify_res = friday_adapter.verify_memory(mem_id, notes="Verified by system supervisor")
    assert verify_res["lifecycle_state"] == "verified"

    # Share with FORGE
    share_res = friday_adapter.share_memory(
        memory_id=mem_id,
        target_agent_name="forge",
        actions=["read", "query"],
        purpose="Ecosystem synchronization",
        ttl_hours=48
    )
    assert share_res["status"] == "shared"
    assert share_res["shared_with"] == "forge"