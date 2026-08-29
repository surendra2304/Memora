"""
Unit and Integration Tests for Neural Cross-Encoder Reranker
Compares Heuristic-only baseline vs Neural Cross-Encoder reranking
proving conceptual relevance is prioritized over superficial keyword overlap.
"""
import pytest
from datetime import datetime, timezone, timedelta
from storage.relational.models import MemoryRecord, MemoryType, LifecycleState
from core.memory.search_service import SearchResultItem
from core.memory.context.reranker import ContextReranker, NeuralCrossEncoderEngine

def test_heuristic_vs_cross_encoder_semantic_prioritization(test_db):
    """
    Evaluation Test: Prove Neural Cross-Encoder correctly prioritizes
    conceptually relevant security solutions over superficial keyword distracters.
    """
    user_query = "How do we prevent unauthorized database access in auth handler?"

    # Memory A: Lexical distracter (high keyword overlap with 'database', 'handler', 'access', 'auth', but no solution)
    mem_distracter = MemoryRecord(
        id="mem-distracter-001",
        namespace_id="ns-test-01",
        owner_id="agent-01",
        memory_type=MemoryType.EPISODIC,
        content_text="The database handler access counter is incremented when auth requests arrive at the gateway.",
        confidence=0.90,
        importance=0.60,
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=datetime.now(timezone.utc)
    )

    # Memory B: Conceptual solution (addresses SQL injection prevention and credential hashing with lower keyword repetition)
    mem_solution = MemoryRecord(
        id="mem-solution-002",
        namespace_id="ns-test-01",
        owner_id="agent-01",
        memory_type=MemoryType.DECISION,
        content_text="Enforce parameterized prepared statements and Argon2id cryptographic hashing on all user login endpoints to eliminate injection vulnerabilities.",
        confidence=0.95,
        importance=0.90,
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=datetime.now(timezone.utc)
    )

    # Mock initial search items where lexical search ranked the keyword distracter higher
    search_items = [
        SearchResultItem(record=mem_distracter, final_score=0.85, match_reasons=["keyword"]),
        SearchResultItem(record=mem_solution, final_score=0.45, match_reasons=["vector"])
    ]

    # 1. Baseline: Heuristic-only reranking (without Cross-Encoder)
    heuristic_results = ContextReranker.rerank(
        search_results=search_items,
        query=user_query,
        use_cross_encoder=False
    )
    # Under heuristic baseline, the keyword distracter stays #1 due to high initial lexical score
    assert heuristic_results[0].record.id == "mem-distracter-001"
    assert heuristic_results[1].record.id == "mem-solution-002"

    # 2. Upgraded: Neural Cross-Encoder reranking
    neural_results = ContextReranker.rerank(
        search_results=search_items,
        query=user_query,
        use_cross_encoder=True,
        cross_encoder_weight=0.70
    )

    # Under Neural Cross-Encoder, the conceptual solution is correctly promoted to Rank #1
    assert neural_results[0].record.id == "mem-solution-002", "Neural Cross-Encoder must prioritize deep conceptual relevance to Rank 1!"
    assert neural_results[0].rank == 1
    assert neural_results[0].cross_encoder_score > neural_results[1].cross_encoder_score
    assert neural_results[1].record.id == "mem-distracter-001"
    assert neural_results[1].rank == 2

def test_cross_encoder_candidate_reduction_and_metadata_fusion(test_db):
    """
    Test Step 1 Coarse Filtering (top 20) + Step 2 Cross-Encoding + Step 3 Metadata weighting.
    """
    query = "Optimize PostgreSQL query throughput and connection pooling"

    candidates = []
    for i in range(25):
        is_fresh = (i == 5 or i % 2 == 0)
        rec = MemoryRecord(
            id=f"mem-cand-{i:03d}",
            namespace_id="ns-test-01",
            owner_id="agent-01",
            memory_type=MemoryType.PROCEDURAL,
            content_text=f"Database tuning candidate #{i}: configure connection pooling and vacuum parameters." if i == 5 else f"Generic notes item #{i}.",
            confidence=0.95 if i == 5 else 0.70,
            importance=0.90 if i == 5 else 0.50,
            lifecycle_state=LifecycleState.ACTIVE,
            created_at=datetime.now(timezone.utc) if is_fresh else datetime.now(timezone.utc) - timedelta(days=90)
        )
        candidates.append(SearchResultItem(record=rec, final_score=0.40 + (0.02 * i), match_reasons=["vector"]))

    reranked = ContextReranker.rerank(
        search_results=candidates,
        query=query,
        top_k_cross_encoder=20,
        cross_encoder_weight=0.65
    )

    assert len(reranked) == 25
    # Memory #5 (the tuned database item with high confidence, importance & cross-encoder match) should rank near the top
    top_ids = [item.record.id for item in reranked[:5]]
    assert "mem-cand-005" in top_ids