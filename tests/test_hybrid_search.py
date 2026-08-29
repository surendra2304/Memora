"""
Comprehensive Integration Tests for MEMORA Hybrid Storage and Search Architecture
Tests Vector Embedding, Keyword Full-Text Search, Graph Store, and Reciprocal Rank Fusion (RRF).
"""
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
from core.memory.search_service import SearchService

def test_embedding_generator_and_vector_adapter():
    """
    Test deterministic dense embedding generation and vector store search.
    """
    text_a = "PostgreSQL relational database schema design."
    text_b = "PostgreSQL database table indexing optimization."
    text_c = "Autonomous flight drone navigation in planetary atmosphere."

    vec_a = EmbeddingGenerator.generate_embedding(text_a)
    vec_b = EmbeddingGenerator.generate_embedding(text_b)
    vec_c = EmbeddingGenerator.generate_embedding(text_c)

    assert len(vec_a) == 384
    assert len(vec_b) == 384

    # Upsert into vector adapter
    vector_adapter.upsert_embedding("mem-a", vec_a, {"content": text_a})
    vector_adapter.upsert_embedding("mem-b", vec_b, {"content": text_b})
    vector_adapter.upsert_embedding("mem-c", vec_c, {"content": text_c})

    # Search for database query
    query_vec = EmbeddingGenerator.generate_embedding("PostgreSQL database")
    hits = vector_adapter.search_similarity(query_vec, limit=2)
    assert len(hits) >= 1
    hit_ids = [h.memory_id for h in hits]
    assert "mem-a" in hit_ids or "mem-b" in hit_ids

def test_graph_service_relationships_and_traversal(test_db):
    """
    Test GraphService creating relationships and 1-hop / 2-hop neighborhood expansion.
    """
    friday = IdentityService.register_agent(test_db, "friday")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")

    mem1 = MemoryRecord(namespace_id=ns.id, owner_id=friday.id, memory_type=MemoryType.PROJECT, content_text="Root Architecture Spec", lifecycle_state=LifecycleState.ACTIVE)
    mem2 = MemoryRecord(namespace_id=ns.id, owner_id=friday.id, memory_type=MemoryType.SEMANTIC, content_text="Database Layer Spec", lifecycle_state=LifecycleState.ACTIVE)
    mem3 = MemoryRecord(namespace_id=ns.id, owner_id=friday.id, memory_type=MemoryType.TOOL, content_text="Alembic Migration Tooling", lifecycle_state=LifecycleState.ACTIVE)

    test_db.add_all([mem1, mem2, mem3])
    test_db.commit()

    # Link mem1 -> mem2 (derived_from) and mem2 -> mem3 (depends_on)
    rel1 = GraphService.create_relationship(test_db, mem1.id, mem2.id, relationship_type="derived_from", weight=0.9)
    rel2 = GraphService.create_relationship(test_db, mem2.id, mem3.id, relationship_type="depends_on", weight=0.8)

    assert rel1.id is not None
    assert rel2.id is not None

    # Traverse graph from mem1 (2 hops)
    subgraph = GraphService.get_connected_memories(test_db, memory_id=mem1.id, max_hops=2)
    assert subgraph["root_memory_id"] == mem1.id
    assert mem2.id in subgraph["connected_memory_ids"]
    assert mem3.id in subgraph["connected_memory_ids"]
    assert len(subgraph["edges"]) == 2

def test_hybrid_search_with_rrf_and_graph_boost(test_db):
    """
    Test SearchService hybrid search combining Vector, Keyword FTS, and Graph Boost.
    """
    forge = IdentityService.register_agent(test_db, "forge")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://forge/private")

    # Ingest test records
    m1 = MemoryRecord(
        namespace_id=ns.id,
        owner_id=forge.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="Kubernetes cluster orchestration for distributed worker agents.",
        confidence=0.95,
        importance=0.85,
        lifecycle_state=LifecycleState.ACTIVE
    )
    m2 = MemoryRecord(
        namespace_id=ns.id,
        owner_id=forge.id,
        memory_type=MemoryType.EXPERIENCE,
        content_text="Docker container deployment manifest for worker agents.",
        confidence=0.90,
        importance=0.80,
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add_all([m1, m2])
    test_db.commit()

    # Index embeddings
    vector_adapter.upsert_embedding(m1.id, EmbeddingGenerator.generate_embedding(m1.content_text))
    vector_adapter.upsert_embedding(m2.id, EmbeddingGenerator.generate_embedding(m2.content_text))

    # Link graph edge
    GraphService.create_relationship(test_db, m1.id, m2.id, relationship_type="relates_to", weight=0.8)

    # Perform hybrid search
    results = SearchService.hybrid_search(
        db=test_db,
        query_text="Kubernetes worker orchestration",
        actor_name="forge",
        limit=5
    )

    assert len(results) >= 1
    top_result = results[0]
    assert top_result.record.id == m1.id
    assert top_result.final_score > 0.0
    assert len(top_result.match_reasons) >= 1

def test_api_v1_memories_search_endpoint(client: TestClient, test_db):
    """
    Test GET /v1/memories/search API endpoint.
    """
    nexus = IdentityService.register_agent(test_db, "nexus")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://nexus/private")

    mem = MemoryRecord(
        namespace_id=ns.id,
        owner_id=nexus.id,
        memory_type=MemoryType.PROCEDURAL,
        content_text="Edge cache invalidation pipeline for global CDN CDN networks.",
        confidence=0.99,
        importance=0.90,
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add(mem)
    test_db.commit()

    vector_adapter.upsert_embedding(mem.id, EmbeddingGenerator.generate_embedding(mem.content_text))

    # Search via HTTP GET
    resp = client.get("/v1/memories/search?q=CDN+cache+invalidation", headers={"X-Agent-Name": "nexus"})
    assert resp.status_code == 200
    hits = resp.json()
    assert len(hits) >= 1
    assert hits[0]["id"] == mem.id
    assert hits[0]["final_score"] > 0.0

def test_api_v1_memory_relationship_endpoints(client: TestClient, test_db):
    """
    Test POST /v1/memories/{id}/relationships and GET /v1/memories/{id}/graph.
    """
    friday = IdentityService.register_agent(test_db, "friday")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")

    m_src = MemoryRecord(namespace_id=ns.id, owner_id=friday.id, memory_type=MemoryType.DECISION, content_text="Source decision", lifecycle_state=LifecycleState.ACTIVE)
    m_tgt = MemoryRecord(namespace_id=ns.id, owner_id=friday.id, memory_type=MemoryType.EXPERIENCE, content_text="Target outcome", lifecycle_state=LifecycleState.ACTIVE)
    test_db.add_all([m_src, m_tgt])
    test_db.commit()

    # Create edge
    rel_resp = client.post(
        f"/v1/memories/{m_src.id}/relationships",
        json={"target_memory_id": m_tgt.id, "relationship_type": "causal_child", "weight": 0.95},
        headers={"X-Agent-Name": "friday"}
    )
    assert rel_resp.status_code == 200
    rel_data = rel_resp.json()
    assert rel_data["status"] == "created"
    assert rel_data["relationship_type"] == "causal_child"

    # Query graph
    graph_resp = client.get(f"/v1/memories/{m_src.id}/graph?max_hops=2", headers={"X-Agent-Name": "friday"})
    assert graph_resp.status_code == 200
    graph_data = graph_resp.json()
    assert m_tgt.id in graph_data["connected_memory_ids"]