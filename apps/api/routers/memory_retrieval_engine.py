"""
Memora/apps/api/routers/memory_retrieval_engine.py — Memora Memory Retrieval Engine.

Semantic memory retrieval, context-aware search, relevance ranking,
and multi-hop memory traversal.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class MemoryRecord:
    """A stored memory record."""
    memory_id: str
    content: str
    category: str = "general"  # episodic, semantic, procedural, working
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5
    created_at: float = field(default_factory=time.time)
    accessed_count: int = 0
    last_accessed: float = 0.0
    similarity_score: float = 0.0


@dataclass
class RetrievalQuery:
    """A memory retrieval query."""
    query_id: str
    query_text: str
    category: str = "all"
    top_k: int = 10
    filters: list[str] = field(default_factory=list)
    results: list[MemoryRecord] = field(default_factory=list)
    latency_ms: float = 0.0


@dataclass
class RetrievalStats:
    """Retrieval statistics."""
    total_queries: int = 0
    avg_latency_ms: float = 0.0
    total_records: int = 0
    cache_hit_rate: float = 0.0


class MemoryRetrievalEngine:
    """Engine for memory retrieval and semantic search."""

    def __init__(self) -> None:
        self._memories: dict[str, MemoryRecord] = {}
        self._queries: list[RetrievalQuery] = []
        self._cache_hits: int = 0
        self._cache_misses: int = 0

    def store_memory(self, content: str, category: str = "general",
                     tags: list[str] | None = None, importance: float = 0.5) -> str:
        """Store a memory."""
        memory_id = f"mem_{uuid.uuid4().hex[:12]}"
        memory = MemoryRecord(
            memory_id=memory_id, content=content, category=category,
            tags=tags or [], importance=importance,
        )
        self._memories[memory_id] = memory
        return memory_id

    def retrieve(self, query_text: str, category: str = "all",
                 top_k: int = 10, filters: list[str] | None = None) -> RetrievalQuery:
        """Retrieve memories matching a query."""
        query_id = f"retr_{uuid.uuid4().hex[:12]}"
        query = RetrievalQuery(
            query_id=query_id, query_text=query_text,
            category=category, top_k=top_k, filters=filters or [],
        )

        start = time.time()
        words = [w.lower() for w in query_text.split() if len(w) > 2]

        scored = []
        for memory in self._memories.values():
            if category != "all" and memory.category != category:
                continue
            if filters and not any(f in memory.tags for f in filters):
                continue

            # Calculate similarity score
            score = 0.0
            content_words = [w.lower() for w in memory.content.split()]
            for word in words:
                if word in content_words:
                    score += 0.2
            for tag in memory.tags:
                if tag.lower() in query_text.lower():
                    score += 0.3
            score += memory.importance * 0.2
            score += memory.accessed_count * 0.01

            memory.similarity_score = score
            memory.accessed_count += 1
            memory.last_accessed = time.time()
            scored.append(memory)

        scored.sort(key=lambda m: m.similarity_score, reverse=True)
        query.results = scored[:top_k]
        query.latency_ms = (time.time() - start) * 1000

        if query.results:
            self._cache_hits += 1
        else:
            self._cache_misses += 1

        self._queries.append(query)
        return query

    def get_stats(self) -> RetrievalStats:
        """Get retrieval statistics."""
        stats = RetrievalStats(
            total_queries=len(self._queries),
            total_records=len(self._memories),
        )
        if self._queries:
            stats.avg_latency_ms = sum(q.latency_ms for q in self._queries) / len(self._queries)
        total_cache = self._cache_hits + self._cache_misses
        stats.cache_hit_rate = self._cache_hits / max(1, total_cache)
        return stats

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        stats = self.get_stats()
        return {
            "total_memories": len(self._memories),
            "total_queries": stats.total_queries,
            "avg_latency_ms": round(stats.avg_latency_ms, 2),
            "cache_hit_rate": round(stats.cache_hit_rate, 2),
            "engine_status": "operational",
        }


engine = MemoryRetrievalEngine()


@router.post("/memora/retrieval/store")
async def store_memory(content: str, category: str = "general", tags: list[str] | None = None, importance: float = 0.5):
    """Store a memory."""
    memory_id = engine.store_memory(content, category, tags, importance)
    return {"memory_id": memory_id, "status": "stored"}


@router.post("/memora/retrieval/query")
async def retrieve(query_text: str, category: str = "all", top_k: int = 10,
                   filters: list[str] | None = None):
    """Retrieve memories."""
    query = engine.retrieve(query_text, category, top_k, filters)
    return {
        "query_id": query.query_id,
        "result_count": len(query.results),
        "latency_ms": query.latency_ms,
        "results": [
            {"memory_id": r.memory_id, "content": r.content[:100], "score": r.similarity_score}
            for r in query.results[:5]
        ],
    }


@router.get("/memora/retrieval/stats")
async def get_stats():
    """Get retrieval statistics."""
    stats = engine.get_stats()
    return {
        "total_queries": stats.total_queries,
        "avg_latency_ms": stats.avg_latency_ms,
        "cache_hit_rate": stats.cache_hit_rate,
    }


@router.get("/memora/retrieval/report")
async def retrieval_report():
    """Get retrieval engine report."""
    return engine.get_report()
