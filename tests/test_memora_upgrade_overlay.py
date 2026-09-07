"""
Tests for memora_upgrade overlay package modules.
Ported from MEMORA_DEEP_UPGRADE overlay package tests.
"""
from datetime import datetime, timezone, timedelta
import pytest

from memora_upgrade.models import (
    MemoryKind,
    MemoryScope,
    MemoryItem,
    Provenance,
    Lifecycle,
    QueryContext,
    RetrievalResult,
)
from memora_upgrade.policy import MemoryPolicy, AccessGrant
from memora_upgrade.context import ContextAssembler
from memora_upgrade.graph import MemoryGraph, Edge
from memora_upgrade.cache import TenantScopedCache
from memora_upgrade.ingestion import IngestionService
from memora_upgrade.store import MemoryStore
from memora_upgrade.retrieval import HybridRetriever, InMemoryVectorIndex
from memora_upgrade.security import SecretScanner, SecretRedactor


# ---------------------------------------------------------------------------
# Policy Tests
# ---------------------------------------------------------------------------
def test_overlay_policy_default_deny():
    p = MemoryPolicy()
    d = p.can_read("t1", "a2", "t1", "a1", "tenant/t1/agent/a1", None, 1)
    assert not d.allowed


def test_overlay_policy_explicit_grant():
    p = MemoryPolicy([AccessGrant("t1", "a2", "tenant/t1/agent/a1", frozenset({"read"}))])
    d = p.can_read("t1", "a2", "t1", "a1", "tenant/t1/agent/a1", None, 1)
    assert d.allowed


# ---------------------------------------------------------------------------
# Context Tests
# ---------------------------------------------------------------------------
def test_overlay_context_budget():
    r = [
        RetrievalResult("1", "a" * 100, 0.9, MemoryScope("t1", "a1"), Provenance("s1"), Lifecycle.ACTIVE),
        RetrievalResult("2", "b" * 100, 0.8, MemoryScope("t1", "a1"), Provenance("s2"), Lifecycle.ACTIVE),
    ]
    b = ContextAssembler().build(r, max_tokens=100, max_items=5)
    assert b.total_tokens > 0


# ---------------------------------------------------------------------------
# Graph & Cache Tests
# ---------------------------------------------------------------------------
def test_overlay_graph_tenant_boundary():
    g = MemoryGraph()
    g.add_edge(Edge("alice", "project", "works_on"), "t1")
    g.add_edge(Edge("bob", "project2", "works_on"), "t2")
    assert "project" in g.neighbors("alice", "t1")
    assert g.neighbors("alice", "t2") == set()


def test_overlay_cache_key_scoped():
    c = TenantScopedCache()
    assert c.key("t1", "a", "q") != c.key("t2", "a", "q")


# ---------------------------------------------------------------------------
# Ingestion Tests
# ---------------------------------------------------------------------------
def test_overlay_deterministic_ingestion_and_dedup():
    store = MemoryStore()
    svc = IngestionService(store)
    kwargs = dict(
        tenant_id="t1",
        owner_agent_id="a1",
        text="I prefer concise answers.",
        kind=MemoryKind.PREFERENCE,
        scope=MemoryScope("t1", agent_id="a1"),
        source="friday",
        confidence=0.9,
        importance=0.8,
    )
    a = svc.ingest(**kwargs)
    b = svc.ingest(**kwargs)
    assert a.id == b.id


# ---------------------------------------------------------------------------
# Lifecycle Tests
# ---------------------------------------------------------------------------
def _sample_memory():
    return MemoryItem(
        "m", "t1", "a1", MemoryKind.EPISODIC, "hello",
        MemoryScope("t1", "a1"), 0.9, 0.9,
        provenance=Provenance("friday", authority=0.9)
    )


def test_overlay_lifecycle_transition():
    store = MemoryStore()
    store.put(_sample_memory())
    assert store.transition("m", Lifecycle.ACTIVE).lifecycle == Lifecycle.ACTIVE
    assert store.transition("m", Lifecycle.VERIFIED).lifecycle == Lifecycle.VERIFIED


def test_overlay_invalid_transition():
    store = MemoryStore()
    store.put(_sample_memory())
    store.transition("m", Lifecycle.ACTIVE)
    store.transition("m", Lifecycle.VERIFIED)
    with pytest.raises(ValueError):
        store.transition("m", Lifecycle.CANDIDATE)


# ---------------------------------------------------------------------------
# Retrieval Tests
# ---------------------------------------------------------------------------
def test_overlay_retrieval_tenant_isolation():
    a = MemoryItem("a", "t1", "owner", MemoryKind.SEMANTIC, "alpha beta", MemoryScope("t1", "owner"), 0.9, 0.8, provenance=Provenance("src"))
    b = MemoryItem("b", "t2", "owner", MemoryKind.SEMANTIC, "alpha beta", MemoryScope("t2", "owner"), 0.9, 0.8, provenance=Provenance("src"))
    data = {"t1": [a], "t2": [b]}
    r = HybridRetriever(lambda tenant: data[tenant], MemoryPolicy())
    ctx = QueryContext("t1", "owner")
    out = r.retrieve("alpha", ctx)
    assert [x.memory_id for x in out] == ["a"]


def test_overlay_retrieval_owner_can_retrieve():
    a = MemoryItem("a", "t1", "owner", MemoryKind.SEMANTIC, "alpha beta", MemoryScope("t1", "owner"), 0.9, 0.8, provenance=Provenance("src"))
    r = HybridRetriever(lambda tenant: [a], MemoryPolicy())
    ctx = QueryContext("t1", "owner")
    assert r.retrieve("alpha", ctx)


# ---------------------------------------------------------------------------
# Security Tests
# ---------------------------------------------------------------------------
def test_overlay_secret_scanner():
    s = SecretScanner()
    assert s.contains_secret("Authorization: Bearer abcdefghijklmnopqrstuv")


def test_overlay_redaction():
    r = SecretRedactor()
    out = r.redact("password=mysecret")
    assert "mysecret" not in out
    assert "[REDACTED]" in out
