"""
Comprehensive Integration Tests for MEMORA Memory Lifecycle Manager
Tests State Machine, Contradiction Resolution, Supersession, Soft/Hard Deletion, and Decay.
"""
from datetime import datetime, timezone, timedelta
import pytest
from fastapi.testclient import TestClient

from storage.relational.models import (
    Agent,
    Namespace,
    NamespaceType,
    MemoryRecord,
    MemoryType,
    LifecycleState,
    AuditLog
)
from storage.vector.qdrant_adapter import vector_adapter
from core.identity.service import IdentityService
from core.memory.service import MemoryService
from core.lifecycle.supersession import SupersessionEngine
from core.lifecycle.decay import MemoryDecayEngine

def test_verify_memory_endpoint(client: TestClient, test_db):
    """
    Test POST /v1/memories/{id}/verify updates state, timestamp, and confidence.
    """
    friday = IdentityService.register_agent(test_db, "friday", role="supervisor")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")

    mem = MemoryRecord(
        namespace_id=ns.id,
        owner_id=friday.id,
        memory_type=MemoryType.DECISION,
        content_text="Ecosystem emergency shutdown threshold established at 0.95 confidence.",
        confidence=0.85,
        importance=0.90,
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add(mem)
    test_db.commit()

    resp = client.post(f"/v1/memories/{mem.id}/verify", json={"notes": "Audited by FRIDAY Supervisor"}, headers={"X-Agent-Name": "friday"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["lifecycle_state"] == "verified"
    assert data["last_verified_at"] is not None
    assert data["confidence"] > 0.85

def test_supersede_and_retrieval_exclusion(client: TestClient, test_db):
    """
    Test Supersession:
    - Superseded memory is linked via superseded_by_id.
    - Superseded memory is excluded from normal retrieval queries.
    - Superseded memory can still be queried historically.
    """
    forge = IdentityService.register_agent(test_db, "forge")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://forge/private")

    # Ingest old memory (e.g. initial architectural plan)
    old_mem = MemoryRecord(
        namespace_id=ns.id,
        owner_id=forge.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="API server will deploy on port 5000 using Flask framework.",
        confidence=0.80,
        importance=0.70,
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add(old_mem)
    test_db.commit()

    # Ingest new memory (superseding decision)
    new_mem = MemoryRecord(
        namespace_id=ns.id,
        owner_id=forge.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="API server architecture migrated to FastAPI on port 8000.",
        confidence=0.98,
        importance=0.95,
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add(new_mem)
    test_db.commit()

    # Call supersede endpoint
    sup_resp = client.post(
        f"/v1/memories/{old_mem.id}/supersede",
        json={"new_memory_id": new_mem.id, "reason": "Framework upgrade to FastAPI"},
        headers={"X-Agent-Name": "forge"}
    )
    assert sup_resp.status_code == 200
    sup_data = sup_resp.json()
    assert sup_data["status"] == "superseded"
    assert sup_data["winner_id"] == new_mem.id
    assert sup_data["superseded_id"] == old_mem.id

    # Verify old memory has superseded state in DB
    test_db.refresh(old_mem)
    assert old_mem.lifecycle_state == LifecycleState.SUPERSEDED
    assert old_mem.superseded_by_id == new_mem.id

    # 1. NORMAL QUERY: Should only return new_mem (old_mem is excluded)
    normal_query_resp = client.post(
        "/v1/memories/query",
        json={"query_text": "API server", "owner_name": "forge"},
        headers={"X-Agent-Name": "forge"}
    )
    assert normal_query_resp.status_code == 200
    normal_results = normal_query_resp.json()
    assert len(normal_results) == 1
    assert normal_results[0]["id"] == new_mem.id

    # 2. HISTORICAL QUERY: With include_superseded=True, returns both records
    hist_query_resp = client.post(
        "/v1/memories/query",
        json={"query_text": "API server", "owner_name": "forge", "include_superseded": True},
        headers={"X-Agent-Name": "forge"}
    )
    assert hist_query_resp.status_code == 200
    hist_results = hist_query_resp.json()
    assert len(hist_results) == 2
    returned_ids = {r["id"] for r in hist_results}
    assert old_mem.id in returned_ids
    assert new_mem.id in returned_ids

def test_contradiction_rule_provenance_and_confidence_over_recency(test_db):
    """
    Test Rule: 'Never resolve contradictions using recency alone; use provenance and confidence first.'
    High-confidence verified memory from FRIDAY beats newer unverified crawler memory.
    """
    friday = IdentityService.register_agent(test_db, "friday", role="supervisor")
    crawler = IdentityService.register_agent(test_db, "crawler", role="worker")

    ns = IdentityService.resolve_namespace(test_db, "memora://universe/global", default_type=NamespaceType.UNIVERSE_GLOBAL)

    # Established verified fact from supervisor
    established_record = MemoryRecord(
        namespace_id=ns.id,
        owner_id=friday.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="Production database connection pool limit is strictly set to 50 connections.",
        confidence=1.0,
        importance=0.95,
        source="friday_supervisor",
        provenance={"verified_by": "surendra", "doc": "runbook.md"},
        lifecycle_state=LifecycleState.VERIFIED
    )
    test_db.add(established_record)
    test_db.commit()

    # Newer contradictory candidate from external crawler
    newer_crawler_record = MemoryRecord(
        namespace_id=ns.id,
        owner_id=crawler.id,
        memory_type=MemoryType.SEMANTIC,
        content_text="Database connection pool limit is 10 connections.",
        confidence=0.40,
        importance=0.30,
        source="crawler",
        provenance={"url": "unverified_blog.com"},
        lifecycle_state=LifecycleState.CANDIDATE
    )
    test_db.add(newer_crawler_record)
    test_db.commit()

    # Resolve contradiction
    decision = SupersessionEngine.resolve_contradiction_and_supersede(
        db=test_db,
        existing_record=established_record,
        new_record=newer_crawler_record,
        existing_owner_name="friday",
        new_owner_name="crawler"
    )

    # Established verified supervisor record MUST WIN over newer unverified crawler record
    assert decision.winner_id == established_record.id
    assert decision.superseded_id == newer_crawler_record.id
    assert decision.evidence_winner > decision.evidence_loser

    test_db.refresh(established_record)
    test_db.refresh(newer_crawler_record)
    assert established_record.lifecycle_state == LifecycleState.VERIFIED
    assert newer_crawler_record.lifecycle_state == LifecycleState.SUPERSEDED
    assert newer_crawler_record.superseded_by_id == established_record.id

def test_soft_and_hard_deletion(client: TestClient, test_db):
    """
    Test soft deletion (state marked deleted, audit log kept) and hard deletion (purged from DB + vector store).
    """
    agent = IdentityService.register_agent(test_db, "intelx")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://intelx/private")

    # Ingest record 1 for soft delete
    mem1 = MemoryRecord(
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Temporary research scrap to be soft deleted.",
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add(mem1)
    test_db.commit()

    # Soft delete
    soft_resp = client.delete(f"/v1/memories/{mem1.id}?hard=false", headers={"X-Agent-Name": "intelx"})
    assert soft_resp.status_code == 200
    assert soft_resp.json()["status"] == "soft_deleted"
    test_db.refresh(mem1)
    assert mem1.lifecycle_state == LifecycleState.DELETED

    # Ingest record 2 for hard delete
    mem2 = MemoryRecord(
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Sensitive test token record to be hard purged.",
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add(mem2)
    test_db.commit()
    mem2_id = mem2.id

    vector_adapter.upsert_embedding(mem2_id, [0.1] * 1536)
    assert mem2_id in vector_adapter._mock_store

    # Hard delete
    hard_resp = client.delete(f"/v1/memories/{mem2_id}?hard=true", headers={"X-Agent-Name": "intelx"})
    assert hard_resp.status_code == 200
    assert hard_resp.json()["status"] == "hard_deleted"

    # Confirmed removed from DB and vector store
    assert test_db.query(MemoryRecord).filter(MemoryRecord.id == mem2_id).first() is None
    assert mem2_id not in vector_adapter._mock_store

def test_time_based_decay_and_auto_archival(client: TestClient, test_db):
    """
    Test time-based decay reducing importance and archiving unverified aged memories.
    """
    agent = IdentityService.register_agent(test_db, "nexus")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://nexus/private")

    # Aged unverified memory (created 30 days ago)
    old_time = datetime.now(timezone.utc) - timedelta(days=30)
    aged_memory = MemoryRecord(
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Ephemeral transient web page render cache log.",
        confidence=0.70,
        importance=0.25,
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=old_time
    )
    test_db.add(aged_memory)
    test_db.commit()

    # Trigger decay endpoint
    decay_resp = client.post(
        "/v1/memories/decay",
        json={"decay_rate_per_day": 0.05, "unverified_threshold_days": 7, "archive_threshold": 0.15},
        headers={"X-Agent-Name": "friday"}
    )
    assert decay_resp.status_code == 200
    decay_data = decay_resp.json()
    assert decay_data["decayed_count"] >= 1
    assert decay_data["archived_count"] >= 1

    test_db.refresh(aged_memory)
    assert aged_memory.importance <= 0.15
    assert aged_memory.lifecycle_state == LifecycleState.ARCHIVED