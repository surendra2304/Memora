"""
Comprehensive Integration Tests for MEMORA Retrieval & Context Pipeline
Tests ContextBuilderService, ContextReranker, Token Budgeting, Policy Filtering, and Context Bundles.
"""
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient

from storage.relational.models import (
    Agent,
    Namespace,
    NamespaceType,
    MemoryRecord,
    MemoryRelationship,
    MemoryType,
    LifecycleState
)
from storage.vector.embedding import EmbeddingGenerator
from storage.vector.qdrant_adapter import vector_adapter
from core.identity.service import IdentityService
from core.memory.graph_service import GraphService
from core.memory.search_service import SearchService, SearchResultItem
from core.memory.context.reranker import ContextReranker
from core.memory.context.budgeter import ContextBudgeter
from core.memory.context.builder import ContextBuilderService

def test_reranker_multi_factor_scoring(test_db):
    """
    Test ContextReranker weighting relevance, confidence, freshness, and importance.
    """
    friday = IdentityService.register_agent(test_db, "friday")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")

    # High confidence & fresh memory
    m_fresh = MemoryRecord(
        namespace_id=ns.id,
        owner_id=friday.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="Critical architecture directive for memory fabric.",
        confidence=1.0,
        importance=1.0,
        created_at=datetime.now(timezone.utc),
        lifecycle_state=LifecycleState.ACTIVE
    )
    # Low confidence & aged memory (90 days old)
    m_aged = MemoryRecord(
        namespace_id=ns.id,
        owner_id=friday.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="Old transient notes for testing.",
        confidence=0.3,
        importance=0.2,
        created_at=datetime.now(timezone.utc) - timedelta(days=90),
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add_all([m_fresh, m_aged])
    test_db.commit()

    search_items = [
        SearchResultItem(record=m_fresh, final_score=0.9),
        SearchResultItem(record=m_aged, final_score=0.9)
    ]

    reranked = ContextReranker.rerank(search_items)
    assert len(reranked) == 2
    assert reranked[0].record.id == m_fresh.id
    assert reranked[0].final_score > reranked[1].final_score

def test_token_budgeter_and_deduplication(test_db):
    """
    Test ContextBudgeter packing within token limits and deduplicating near-identical facts.
    """
    friday = IdentityService.register_agent(test_db, "friday")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")

    m1 = MemoryRecord(namespace_id=ns.id, owner_id=friday.id, memory_type=MemoryType.EPISODIC, content_text="A" * 200, confidence=1.0, importance=1.0, lifecycle_state=LifecycleState.ACTIVE)
    m2 = MemoryRecord(namespace_id=ns.id, owner_id=friday.id, memory_type=MemoryType.EPISODIC, content_text="B" * 200, confidence=0.9, importance=0.9, lifecycle_state=LifecycleState.ACTIVE)
    test_db.add_all([m1, m2])
    test_db.commit()

    search_items = [
        SearchResultItem(record=m1, final_score=0.9),
        SearchResultItem(record=m2, final_score=0.8)
    ]
    reranked = ContextReranker.rerank(search_items)

    # Budget of only 60 tokens (~240 chars)
    budgeted, tokens, compaction_strategy = ContextBudgeter.fit_to_budget(reranked, max_tokens=60)
    assert len(budgeted) >= 1
    assert tokens <= 60
    assert compaction_strategy in ["none", "summarized", "truncated"]

def test_fail_closed_policy_isolation_in_context_bundle(test_db):
    """
    Test that FORGE cannot receive FRIDAY's private memories in a Context Bundle.
    """
    friday = IdentityService.register_agent(test_db, "friday")
    forge = IdentityService.register_agent(test_db, "forge")

    ns_friday = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")
    ns_shared = IdentityService.resolve_namespace(test_db, "memora://universe/global", default_type=NamespaceType.UNIVERSE_GLOBAL)

    # FRIDAY Private Memory
    priv_mem = MemoryRecord(
        namespace_id=ns_friday.id,
        owner_id=friday.id,
        memory_type=MemoryType.DECISION,
        content_text="FRIDAY secret supervisor private master keys.",
        confidence=1.0,
        importance=1.0,
        lifecycle_state=LifecycleState.ACTIVE
    )
    # Universe Global Memory
    glob_mem = MemoryRecord(
        namespace_id=ns_shared.id,
        owner_id=friday.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="Universe global coordination protocols for agent ecosystem.",
        confidence=0.95,
        importance=0.90,
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add_all([priv_mem, glob_mem])
    test_db.commit()

    vector_adapter.upsert_embedding(priv_mem.id, EmbeddingGenerator.generate_embedding(priv_mem.content_text))
    vector_adapter.upsert_embedding(glob_mem.id, EmbeddingGenerator.generate_embedding(glob_mem.content_text))

    # FORGE builds context bundle
    bundle = ContextBuilderService.build_context_bundle(
        db=test_db,
        agent_id_or_name="forge",
        task_query="coordination keys protocols",
        token_budget=2000
    )

    included_ids = [m["id"] for m in bundle.memories]
    # FRIDAY private memory MUST NOT be present in FORGE's bundle
    assert priv_mem.id not in included_ids
    # Global memory should be present
    assert glob_mem.id in included_ids

def test_api_v1_context_post_endpoint(client: TestClient, test_db):
    """
    Test POST /v1/context endpoint returning curated Context Bundle with graph edges.
    """
    nexus = IdentityService.register_agent(test_db, "nexus")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://nexus/private")

    m1 = MemoryRecord(
        namespace_id=ns.id,
        owner_id=nexus.id,
        memory_type=MemoryType.PROJECT,
        content_text="Frontend high-speed reactive UI architecture design.",
        confidence=0.95,
        importance=0.90,
        lifecycle_state=LifecycleState.ACTIVE
    )
    m2 = MemoryRecord(
        namespace_id=ns.id,
        owner_id=nexus.id,
        memory_type=MemoryType.TOOL,
        content_text="Vite bundling toolchain for reactive UI components.",
        confidence=0.90,
        importance=0.85,
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add_all([m1, m2])
    test_db.commit()

    vector_adapter.upsert_embedding(m1.id, EmbeddingGenerator.generate_embedding(m1.content_text))
    vector_adapter.upsert_embedding(m2.id, EmbeddingGenerator.generate_embedding(m2.content_text))

    # Link graph edge
    GraphService.create_relationship(test_db, m1.id, m2.id, relationship_type="depends_on", weight=0.9)

    # Call POST /v1/context
    payload = {
        "task_query": "reactive UI frontend Vite toolchain",
        "token_budget": 3000
    }
    resp = client.post("/v1/context", json=payload, headers={"X-Agent-Name": "nexus"})
    assert resp.status_code == 200
    data = resp.json()

    assert data["target_agent"] == "nexus"
    assert data["memories_count"] >= 2
    assert "summary" in data
    assert data["graph_edges_count"] >= 1
    assert data["graph_edges"][0]["relationship_type"] == "depends_on"