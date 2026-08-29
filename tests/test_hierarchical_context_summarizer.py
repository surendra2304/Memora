"""
Integration Tests for Hierarchical LLM Context Summarization
Simulates 10,000+ token memory retrieval that is successfully compacted
to fit within a 4,000 token budget while preserving critical facts and provenance.
"""
import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timezone

from apps.api.main import app
from storage.relational.models import MemoryRecord, MemoryType, LifecycleState
from core.identity.service import IdentityService
from core.memory.context.builder import ContextBuilderService
from core.memory.context.budgeter import ContextBudgeter, LLMContextSummarizer
from core.memory.context.reranker import RerankedMemoryItem

@pytest.fixture
def api_client():
    return TestClient(app)

def test_10k_token_hierarchical_compaction_to_4k_budget(test_db):
    """
    Simulate a 10,000+ token candidate set compacted into a 4,000 token budget
    using hierarchical summarization while maintaining provenance source IDs.
    """
    agent = IdentityService.register_agent(test_db, "forge", role="worker")
    ns_forge = IdentityService.resolve_namespace(test_db, "memora://forge/projects/app-17", owner_agent_id=agent.id)
    ns_shared = IdentityService.resolve_namespace(test_db, "memora://shared/projects/app-17", owner_agent_id=agent.id)

    # Ingest 20 detailed memory records (~550 tokens / 2,200 chars each = ~11,000 total tokens)
    memory_ids = []
    for i in range(20):
        target_ns = ns_forge if i < 10 else ns_shared
        # Dense technical content with specific architecture facts, ports, metrics, and parameters
        content_block = (
            f"Module Component #{i:02d} Architectural Specification: "
            f"Port {8000 + i} configured for gRPC stream throughput with max concurrency {1000 * (i + 1)}. "
            f"PostgreSQL connection pool max_overflow={10 + i} and timeout=30.0s. "
            f"Redis cluster node-{i:02d} memory threshold set to 85% with LRU eviction policy. "
            f"Argon2id cryptographic salt parameters: m=65536, t=3, p=4 for secure password storage. "
            f"Detailed subsystem logging telemetry stream for cluster shard {i:02d} "
            f"retaining high-resolution execution traces for anomaly detection and load shedding. "
        ) * 5  # Repeats 5 times per item (~2,200 chars / ~550 tokens each)

        rec = MemoryRecord(
            id=f"mem-fact-block-{i:02d}",
            namespace_id=target_ns.id,
            owner_id=agent.id,
            memory_type=MemoryType.DECISION if i % 2 == 0 else MemoryType.PROCEDURAL,
            content_text=content_block,
            confidence=0.98,
            importance=0.92,
            lifecycle_state=LifecycleState.ACTIVE,
            created_at=datetime.now(timezone.utc)
        )
        test_db.add(rec)
        memory_ids.append(rec.id)

    test_db.commit()

    # Calculate raw token count before compaction
    raw_total_tokens = sum(ContextBudgeter.estimate_tokens(rec.content_text) for rec in test_db.query(MemoryRecord).filter(MemoryRecord.id.in_(memory_ids)).all())
    assert raw_total_tokens >= 8000, f"Raw tokens {raw_total_tokens} must exceed 8,000 tokens."

    # Build Context Bundle with 4000 token budget
    bundle = ContextBuilderService.build_context_bundle(
        db=test_db,
        agent_id_or_name="forge",
        task_query="gRPC port configuration connection pooling and Argon2id parameters",
        token_budget=4000,
        max_candidates=30
    )

    # Assertions
    assert bundle.total_tokens_estimated <= 4000, f"Context bundle tokens ({bundle.total_tokens_estimated}) must not exceed 4,000 budget!"
    assert bundle.compaction_strategy == "summarized", "Compaction strategy must be 'summarized' when tokens exceed budget!"
    
    # Check memories in bundle
    assert len(bundle.memories) > 0
    for mem in bundle.memories:
        assert mem["is_summarized"] is True
        assert len(mem["source_memory_ids"]) > 0
        assert mem["provenance"]["compaction"] == "hierarchical_llm_summary"

def test_api_v1_context_returns_compaction_strategy(api_client, test_db):
    """
    Test POST /v1/context API endpoint includes compaction_strategy in JSON response.
    """
    agent = IdentityService.register_agent(test_db, "friday", role="supervisor")
    ns = IdentityService.resolve_namespace(test_db, "memora://friday/private", owner_agent_id=agent.id)

    # Ingest a standard memory
    rec = MemoryRecord(
        id="mem-api-compaction-01",
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Single concise preference note.",
        confidence=1.0,
        importance=0.9,
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=datetime.now(timezone.utc)
    )
    test_db.add(rec)
    test_db.commit()

    resp = api_client.post(
        "/v1/context",
        json={"task_query": "concise preference note", "token_budget": 4000},
        headers={"X-Agent-Name": "friday"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "compaction_strategy" in data
    assert data["compaction_strategy"] in ["none", "summarized", "truncated"]