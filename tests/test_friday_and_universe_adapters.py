"""
Integration Tests for FRIDAY & AI Universe Specialized Ecosystem Adapters
Tests FRIDAY user preferences, context session queries, task delegation, and AI Universe model reasoning grounding.
"""
import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from adapters.adapter_registry import adapter_registry
from adapters.friday.adapter import FridayAdapter
from adapters.ai_universe.adapter import AIUniverseAdapter
from core.identity.service import IdentityService
from storage.relational.models import MemoryType, LifecycleState, NamespaceType

@pytest.fixture
def mock_client():
    return TestClient(app)

def test_friday_adapter_instantiation_from_registry(mock_client):
    """
    Test AdapterRegistry returns a specialized FridayAdapter instance.
    """
    adapter = adapter_registry.get_adapter("friday", http_client=mock_client)
    assert isinstance(adapter, FridayAdapter)
    assert adapter.agent_name == "friday"
    assert adapter.role == "supervisor"
    assert adapter.default_namespace == "memora://friday/private"

def test_friday_user_preference_save_and_session_context_retrieval(mock_client, test_db):
    """
    Test FRIDAY writing a user preference and retrieving it in a session Context Bundle.
    """
    friday_adapter: FridayAdapter = adapter_registry.get_adapter("friday", http_client=mock_client)

    # 1. Save user preference
    pref_res = friday_adapter.save_user_preference(
        preference_text="User prefers dark mode UI and concise bullet points in responses.",
        confidence=1.0,
        importance=0.95
    )
    assert pref_res["id"] is not None
    assert pref_res["memory_type"] == "preference"
    assert pref_res["confidence"] == 1.0

    # 2. Retrieve session context bundle
    bundle = friday_adapter.get_session_context(
        user_query="dark mode UI bullet points preference",
        token_budget=4000
    )
    assert bundle["bundle_id"] is not None
    assert bundle["target_agent"] == "friday"
    assert bundle["memories_count"] >= 1
    
    # Check that preference content is inside bundle memories
    found = any("dark mode UI" in m["content_text"] for m in bundle["memories"])
    assert found is True

def test_friday_delegate_task_with_bounded_context(mock_client, test_db):
    """
    Test FRIDAY delegating task to a sub-agent with scoped bounded namespace context.
    """
    friday_adapter: FridayAdapter = adapter_registry.get_adapter("friday", http_client=mock_client)

    target_scope = "memora://friday/projects/data-pipeline"
    proj_ns = IdentityService.resolve_namespace(test_db, target_scope, default_type=NamespaceType.PROJECT_PRIVATE)
    
    # Register sub-agent with bounded scope
    IdentityService.register_subagent(
        test_db,
        parent_agent_name="friday",
        subagent_name="worker-1",
        bounded_scope=target_scope
    )
    test_db.commit()

    # Ingest a project memory in project namespace
    friday_adapter.write_memory(
        content_text="ETL schema for real-time market data stream processing.",
        target_namespace_path=target_scope,
        memory_type="project"
    )

    # Delegate to sub-agent 'worker-1'
    bundle = friday_adapter.delegate_task_with_context(
        sub_agent_name="worker-1",
        task_description="real-time market data stream schema",
        bounded_scope=target_scope,
        token_budget=2000
    )

    assert bundle["bundle_id"] is not None
    assert bundle["target_agent"] == "friday:worker-1"
    assert bundle["memories_count"] >= 1

def test_ai_universe_ground_model_reasoning(mock_client, test_db):
    """
    Test AI Universe grounding model reasoning only against verified memories to prevent hallucination.
    """
    universe_adapter: AIUniverseAdapter = adapter_registry.get_adapter("ai_universe", http_client=mock_client)
    assert isinstance(universe_adapter, AIUniverseAdapter)
    assert universe_adapter.default_namespace == "memora://universe/global"

    # Ingest 1 active memory and 1 verified memory
    unverified_res = universe_adapter.write_memory(
        content_text="Quantum encryption prototype beta candidate notes.",
        target_namespace_path="memora://universe/global"
    )

    verified_res = universe_adapter.write_memory(
        content_text="Verified quantum key distribution standard protocol v1.0.",
        target_namespace_path="memora://universe/global"
    )
    # Promote to verified
    universe_adapter.verify_memory(verified_res["id"], notes="Formally audited and verified standard.")

    # Call ground_model_reasoning
    grounding = universe_adapter.ground_model_reasoning(
        prompt="quantum encryption distribution standard protocol",
        min_confidence=0.8
    )

    assert grounding["query"] == "quantum encryption distribution standard protocol"
    assert "VERIFIED GROUNDING KNOWLEDGE" in grounding["grounded_prompt"]
    assert grounding["verified_facts_count"] >= 1

    # Check verified facts list only contains verified memory
    fact_texts = [f["fact"] for f in grounding["verified_facts"]]
    assert any("Verified quantum key distribution" in text for text in fact_texts)
    assert not any("prototype beta candidate notes" in text for text in fact_texts)