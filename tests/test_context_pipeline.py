"""
Comprehensive Integration Tests for MEMORA Retrieval & Context Pipeline
Tests ContextBuilderService, ContextReranker, Token Budgeting, Policy Filtering, and Context Bundles.
"""
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event

from storage.relational.models import (
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
from core.memory.search_service import SearchResultItem, SearchService
from core.memory.context.reranker import ContextReranker
from core.memory.context.budgeter import BudgetedMemoryItem, ContextBudgeter
from core.memory.context.builder import ContextBuilderService


def test_context_degrades_to_keyword_retrieval_when_vector_search_fails(
    test_db, monkeypatch
):
    agent = IdentityService.register_agent(test_db, "vector-fallback-agent")
    namespace = IdentityService.get_namespace_by_path(
        test_db, "memora://vector-fallback-agent/private"
    )
    record = MemoryRecord(
        namespace_id=namespace.id,
        owner_id=agent.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="distinctive relational fallback calibration",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add(record)
    test_db.commit()

    def unavailable(**_kwargs):
        raise RuntimeError("vector backend unavailable")

    monkeypatch.setattr(vector_adapter, "search_similarity", unavailable)

    bundle = ContextBuilderService.build_context_bundle(
        db=test_db,
        agent_id_or_name=agent.id,
        task_query="distinctive relational fallback calibration",
    )

    assert record.id in {memory["id"] for memory in bundle.memories}
    assert bundle.is_degraded
    assert "degraded vector store fallback" in bundle.summary


def test_context_propagates_non_vector_search_failures_without_retry(
    test_db, monkeypatch
):
    agent = IdentityService.register_agent(test_db, "search-error-agent")
    calls = []

    def fail_search(cls, **_kwargs):
        calls.append(True)
        raise RuntimeError("relational query failed")

    monkeypatch.setattr(SearchService, "hybrid_search", classmethod(fail_search))

    with pytest.raises(RuntimeError, match="relational query failed"):
        ContextBuilderService.build_context_bundle(
            db=test_db,
            agent_id_or_name=agent.id,
            task_query="query",
        )

    assert len(calls) == 1


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
    IdentityService.register_agent(test_db, "forge")

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


def test_context_graph_edges_exclude_cross_tenant_relationships(test_db, monkeypatch):
    actor = IdentityService.register_agent(test_db, "context-graph-agent", tenant_id="acme")
    foreign_agent = IdentityService.register_agent(
        test_db, "context-graph-agent", tenant_id="globex"
    )
    local_namespace = IdentityService.get_namespace_by_path(
        test_db, "memora://context-graph-agent/private", tenant_id="acme"
    )
    foreign_namespace = IdentityService.get_namespace_by_path(
        test_db, "memora://context-graph-agent/private", tenant_id="globex"
    )
    local_source = MemoryRecord(
        tenant_id="acme",
        namespace_id=local_namespace.id,
        owner_id=actor.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="local source memory",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    local_target = MemoryRecord(
        tenant_id="acme",
        namespace_id=local_namespace.id,
        owner_id=actor.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="local target memory",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    foreign_target = MemoryRecord(
        tenant_id="globex",
        namespace_id=foreign_namespace.id,
        owner_id=foreign_agent.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="foreign target memory",
        lifecycle_state=LifecycleState.ACTIVE,
    )
    test_db.add_all([local_source, local_target, foreign_target])
    test_db.flush()

    valid_edge = MemoryRelationship(
        source_memory_id=local_source.id,
        target_memory_id=local_target.id,
        relationship_type="depends_on",
        weight=0.9,
    )
    corrupt_edge = MemoryRelationship(
        source_memory_id=local_source.id,
        target_memory_id=foreign_target.id,
        relationship_type="leaks_to",
        weight=1.0,
    )
    test_db.add_all([valid_edge, corrupt_edge])
    test_db.commit()

    monkeypatch.setattr(
        SearchService,
        "hybrid_search",
        classmethod(
            lambda cls, **_kwargs: [
                SearchResultItem(local_source, final_score=0.9),
                SearchResultItem(local_target, final_score=0.8),
            ]
        ),
    )
    monkeypatch.setattr(
        ContextBudgeter,
        "fit_to_budget",
        classmethod(
            lambda cls, _memories, **_kwargs: (
                [
                    BudgetedMemoryItem(
                        record=local_source,
                        content_text=local_source.content_text,
                        token_count=1,
                        score=0.9,
                        source_memory_ids=[
                            local_source.id,
                            local_target.id,
                            foreign_target.id,
                        ],
                    )
                ],
                1,
                "none",
            )
        ),
    )
    relationship_queries = []

    def capture_relationship_query(_conn, _cursor, statement, _parameters, _context, _executemany):
        if "FROM memory_relationships" in statement:
            relationship_queries.append(statement)

    event.listen(test_db.get_bind(), "before_cursor_execute", capture_relationship_query)
    try:
        bundle = ContextBuilderService.build_context_bundle(
            db=test_db,
            agent_id_or_name=actor.id,
            task_query="local source and target",
            tenant_id="acme",
        )
    finally:
        event.remove(test_db.get_bind(), "before_cursor_execute", capture_relationship_query)

    assert bundle.graph_edges == [
        {
            "source_id": local_source.id,
            "target_id": local_target.id,
            "relationship_type": "depends_on",
            "weight": 0.9,
        }
    ]
    assert foreign_target.id not in bundle.memories[0]["source_memory_ids"]
    assert len(relationship_queries) == 1
    query = relationship_queries[0].lower()
    assert query.count("tenant_id") >= 2


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