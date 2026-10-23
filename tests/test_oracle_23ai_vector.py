"""
Unit and integration tests for Oracle 23ai Native Vector Adapter.
Verifies table creation, vector upsert, cosine similarity search, and deletion.
"""
from __future__ import annotations

import pytest
from storage.relational.session import SessionLocal, init_db
from storage.vector.oracle_vector_adapter import OracleVectorAdapter


@pytest.fixture
def oracle_vector():
    init_db()
    adapter = OracleVectorAdapter(table_name="test_oracle_vector_store", dimension=384)
    session = SessionLocal()
    adapter.ensure_schema(db=session)
    session.close()
    yield adapter
    # Cleanup table after test
    s = SessionLocal()
    try:
        s.execute(__import__("sqlalchemy").text("DROP TABLE test_oracle_vector_store"))
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


def test_oracle_vector_adapter_lifecycle(oracle_vector):
    # 1. Readiness
    status = oracle_vector.readiness()
    assert status["available"] is True
    assert status["dimension"] == 384

    # 2. Upsert two orthogonal vectors
    vec_a = [1.0 if i < 192 else 0.0 for i in range(384)]
    vec_b = [1.0 if i >= 192 else 0.0 for i in range(384)]
    ok_a = oracle_vector.upsert_embedding(
        memory_id="mem_test_a",
        vector=vec_a,
        payload={"topic": "distributed_consensus"},
        tenant_id="tenant_v",
    )
    ok_b = oracle_vector.upsert_embedding(
        memory_id="mem_test_b",
        vector=vec_b,
        payload={"topic": "neural_embeddings"},
        tenant_id="tenant_v",
    )
    assert ok_a is True
    assert ok_b is True

    # 3. Similarity Search aligned with vec_b
    query = list(vec_b)
    hits = oracle_vector.search_similarity(
        query_vector=query,
        limit=5,
        tenant_id="tenant_v",
        score_threshold=0.5,
    )
    assert len(hits) == 1
    # mem_test_b must be the only hit clearing 0.5 threshold
    assert hits[0].memory_id == "mem_test_b"
    assert hits[0].score >= 0.99
    assert hits[0].payload["topic"] == "neural_embeddings"

    # 4. Deletion
    del_ok = oracle_vector.delete_embedding("mem_test_b", tenant_id="tenant_v")
    assert del_ok is True

    # Verify deleted
    hits_after = oracle_vector.search_similarity(
        query_vector=vec_b,
        limit=5,
        tenant_id="tenant_v",
        score_threshold=0.5,
    )
    assert len(hits_after) == 0
