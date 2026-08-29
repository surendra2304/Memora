"""
Unit and Integration Tests for Phase 6 Advanced Entity Extraction & Entity Resolution
Tests Named Entity Recognition (NER), Canonical Entity Resolution, Semantic SPO Triples,
and Automatic Knowledge Graph Wiring.
"""
import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from core.memory.pipeline.entity_extractor import EntityExtractor
from core.memory.graph_service import GraphService
from core.memory.pipeline.write_service import MemoryWriteService
from storage.relational.models import MemoryType, LifecycleState

@pytest.fixture
def client():
    return TestClient(app)

def test_deep_entity_and_triple_extraction_from_architecture_doc():
    """
    Test extraction of complex entities (Technologies, People, Dates, Modules, Agents, Concepts)
    and Subject-Predicate-Object (SPO) relationship triples from a multi-sentence architecture document.
    """
    doc_text = """
    On 2026-08-29, Surendra designed the MEMORA persistent memory architecture for FRIDAY Universe.
    FORGE implemented the database layer using Postgres and SQLAlchemy, while Redis caches session events.
    Later, SENTINEL verified that pg_db connection pooling and auth.py parameterized queries are secure.
    """

    res = EntityExtractor.extract_entities_and_relationships(doc_text)

    # 1. Verify Typed Entities
    typed = res["typed_entities"]
    assert "2026-08-29" in typed["dates"]
    assert "Surendra" in typed["people"]
    assert "FORGE" in typed["agents"]
    assert "SENTINEL" in typed["agents"]
    assert any(tech in ["postgres", "pg_db", "redis", "sqlalchemy"] for tech in typed["technologies"])
    assert "auth.py" in typed["modules"]
    assert any("connection pooling" in c or "parameterized queries" in c for c in typed["concepts"])

    # 2. Verify Canonical Resolution
    resolved = res["resolved_canonical_entities"]
    assert "postgresql" in resolved
    assert "redis" in resolved
    assert "sqlalchemy" in resolved

    # 3. Verify Semantic SPO Triples
    triples = res["triples"]
    assert len(triples) >= 2
    
    # Check SPO predicates
    predicates = [t["predicate"] for t in triples]
    assert any(p in ["designed", "implemented", "verified", "caches"] for p in predicates)

def test_canonical_entity_resolution_aliases():
    """
    Test that synonyms and aliases resolve to a single canonical entity node.
    """
    assert EntityExtractor.resolve_canonical("Postgres") == "postgresql"
    assert EntityExtractor.resolve_canonical("PostgreSQL") == "postgresql"
    assert EntityExtractor.resolve_canonical("pg_db") == "postgresql"
    assert EntityExtractor.resolve_canonical("postgres_db") == "postgresql"
    assert EntityExtractor.resolve_canonical("k8s") == "kubernetes"
    assert EntityExtractor.resolve_canonical("kube") == "kubernetes"
    assert EntityExtractor.resolve_canonical("redis_cache") == "redis"
    assert EntityExtractor.resolve_canonical("fast_api") == "fastapi"
    assert EntityExtractor.resolve_canonical("auth.py") == "auth_module"

def test_write_pipeline_entity_resolution_and_graph_auto_wiring(test_db):
    """
    Test that the 10-step Write Pipeline automatically resolves entity aliases
    and wires relationship edges between related memory records in the Graph Store.
    """
    # 1. Write Memory 1 mentioning 'PostgreSQL'
    mem1_res = MemoryWriteService.execute_pipeline(
        db=test_db,
        content_text="Surendra designed the PostgreSQL canonical schema on 2026-08-29.",
        actor_name="friday",
        target_namespace_path="memora://friday/projects/architecture",
        memory_type=MemoryType.PROJECT,
        confidence=1.0,
        importance=0.9
    )
    mem1_id = mem1_res.record.id

    # 2. Write Memory 2 mentioning alias 'pg_db'
    mem2_res = MemoryWriteService.execute_pipeline(
        db=test_db,
        content_text="FORGE implemented high-throughput connection pooling for pg_db.",
        actor_name="forge",
        target_namespace_path="memora://forge/projects/architecture",
        memory_type=MemoryType.PROCEDURAL,
        confidence=0.95,
        importance=0.85
    )
    mem2_id = mem2_res.record.id

    # 3. Write Memory 3 mentioning alias 'Postgres'
    mem3_res = MemoryWriteService.execute_pipeline(
        db=test_db,
        content_text="SENTINEL verified that Postgres SSL database encryption is active.",
        actor_name="sentinel",
        target_namespace_path="memora://shared/projects/architecture",
        memory_type=MemoryType.EXPERIENCE,
        confidence=1.0,
        importance=0.9
    )
    mem3_id = mem3_res.record.id

    # 4. Write Memory 4 (Unrelated technology: Vite UI)
    mem4_res = MemoryWriteService.execute_pipeline(
        db=test_db,
        content_text="NEXUS configured Vite for frontend reactive bundling.",
        actor_name="nexus",
        target_namespace_path="memora://nexus/private",
        memory_type=MemoryType.TOOL,
        confidence=0.9,
        importance=0.7
    )
    mem4_id = mem4_res.record.id

    # 5. Query Graph Service 2-hop connected neighborhood from Memory 1
    connected = GraphService.get_connected_memories(test_db, memory_id=mem1_id, max_hops=2)

    connected_ids = connected["connected_memory_ids"]
    assert mem2_id in connected_ids, "Memory 2 (mentioning pg_db) must be connected to Memory 1 (mentioning PostgreSQL) via entity resolution!"
    assert mem3_id in connected_ids, "Memory 3 (mentioning Postgres) must be connected to Memory 1 via entity resolution!"
    assert mem4_id not in connected_ids, "Memory 4 (mentioning Vite) must NOT be connected to PostgreSQL cluster!"