"""
Memora/apps/api/routers/temporal_context_retriever.py — Memora Temporal Context Retriever.

Retrieves context from memory based on temporal proximity, causal chains,
and relevance scoring. Provides time-aware memory retrieval, context windows
that adapt to the query, and temporal summarization.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class TemporalQuery:
    """A temporal context query."""
    query_id: str
    query_text: str = ""
    target_time: float = 0.0
    time_window_hours: float = 24.0
    relevance_threshold: float = 0.3
    max_results: int = 20
    causal_chain_depth: int = 3
    include_summaries: bool = True


@dataclass
class TemporalMemory:
    """A memory item retrieved temporally."""
    memory_id: str
    content: str = ""
    timestamp: float = 0.0
    relevance_score: float = 0.0
    temporal_distance_hours: float = 0.0
    causal_connections: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    importance: float = 0.0


@dataclass
class ContextWindow:
    """A context window assembled from memories."""
    window_id: str
    query_id: str
    memories: list[TemporalMemory] = field(default_factory=list)
    total_relevance: float = 0.0
    summary: str = ""
    time_range_start: float = 0.0
    time_range_end: float = 0.0


@dataclass
class CausalChain:
    """A causal chain of memories."""
    chain_id: str
    root_memory: str = ""
    events: list[dict] = field(default_factory=list)
    length: int = 0
    probability: float = 0.0
    summary: str = ""


@dataclass
class TemporalSummary:
    """A temporal summary of memories."""
    summary_id: str
    query_id: str
    start_time: float = 0.0
    end_time: float = 0.0
    summary_text: str = ""
    key_events: list[str] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    word_count: int = 0


@dataclass
class RelevanceScore:
    """Relevance score for a memory."""
    memory_id: str
    content_similarity: float = 0.0
    temporal_proximity: float = 0.0
    causal_relevance: float = 0.0
    importance_weight: float = 0.0
    total_score: float = 0.0


class TemporalContextRetriever:
    """Temporal context retrieval engine."""

    def __init__(self):
        self._memories: dict[str, TemporalMemory] = {}
        self._queries: list[TemporalQuery] = []
        self._windows: dict[str, ContextWindow] = {}
        self._chains: list[CausalChain] = []
        self._summaries: dict[str, TemporalSummary] = {}

    def add_memory(
        self,
        content: str,
        timestamp: float = 0.0,
        tags: list[str] | None = None,
        importance: float = 0.5,
        causal_connections: list[str] | None = None,
    ) -> TemporalMemory:
        """Add a memory for temporal retrieval."""
        memory = TemporalMemory(
            memory_id=hashlib.md5(
                f"temp_memory:{content[:30]}:{timestamp}:{time.time()}".encode()
            ).hexdigest()[:12],
            content=content,
            timestamp=timestamp or time.time(),
            relevance_score=0.5,
            temporal_distance_hours=0.0,
            causal_connections=causal_connections or [],
            tags=tags or [],
            importance=importance,
        )
        self._memories[memory.memory_id] = memory
        return memory

    def query(
        self,
        query_text: str,
        target_time: float = 0.0,
        time_window_hours: float = 24.0,
        relevance_threshold: float = 0.3,
        max_results: int = 20,
    ) -> ContextWindow:
        """Query memories temporally."""
        query = TemporalQuery(
            query_id=hashlib.md5(
                f"query:{query_text[:30]}:{time.time()}".encode()
            ).hexdigest()[:12],
            query_text=query_text,
            target_time=target_time or time.time(),
            time_window_hours=time_window_hours,
            relevance_threshold=relevance_threshold,
            max_results=max_results,
        )

        self._queries.append(query)

        # Score all memories
        scored = []
        for memory in self._memories.values():
            score = self._calculate_relevance(query, memory)
            if score >= relevance_threshold:
                scored.append((memory, score))

        # Sort by score
        scored.sort(key=lambda x: x[1], reverse=True)

        # Build context window
        window = ContextWindow(
            window_id=hashlib.md5(
                f"window:{query.query_id}:{time.time()}".encode()
            ).hexdigest()[:12],
            query_id=query.query_id,
            memories=[m for m, s in scored[:max_results]],
            total_relevance=sum(s for _, s in scored[:max_results]),
            time_range_start=min((m.timestamp for m in window.memories), default=0),
            time_range_end=max((m.timestamp for m in window.memories), default=time.time()),
        )

        # Generate summary
        window.summary = self._generate_summary(query.query_text, window.memories)

        self._windows[window.window_id] = window
        return window

    def _calculate_relevance(self, query: TemporalQuery, memory: TemporalMemory) -> float:
        """Calculate relevance score for a memory."""
        # Content similarity (simple substring match)
        content_sim = 1.0 if query.query_text[:20] in memory.content or memory.content[:20] in query.query_text else 0.0

        # Temporal proximity
        age_hours = abs(memory.timestamp - query.target_time) / 3600
        temporal_proximity = max(0.0, 1.0 - (age_hours / query.time_window_hours))

        # Causal relevance
        causal_relevance = 0.5 if memory.causal_connections else 0.0

        # Importance weight
        importance = memory.importance

        total = (
            content_sim * 0.4 +
            temporal_proximity * 0.3 +
            causal_relevance * 0.1 +
            importance * 0.2
        )

        return total

    def _generate_summary(self, query: str, memories: list[TemporalMemory]) -> str:
        """Generate a summary of retrieved memories."""
        if not memories:
            return f"No relevant memories found for: {query[:50]}"

        # Extract key content
        content_snippets = [m.content[:100] for m in memories[:5]]
        return f"Found {len(memories)} relevant memories for '{query[:30]}': {' | '.join(content_snippets)}"

    def build_causal_chain(self, root_memory_id: str, depth: int = 3) -> CausalChain:
        """Build a causal chain from a memory."""
        root = self._memories.get(root_memory_id)
        if not root:
            raise HTTPException(404, f"Memory {root_memory_id} not found")

        chain = CausalChain(
            chain_id=hashlib.md5(
                f"chain:{root_memory_id}:{time.time()}".encode()
            ).hexdigest()[:12],
            root_memory=root_memory_id,
            events=[{"memory_id": root_memory_id, "content": root.content[:80]}],
            length=1,
            probability=0.7,
        )

        # Expand chain
        visited = {root_memory_id}
        current_connections = root.causal_connections

        for level in range(depth):
            new_connections = []
            for conn_id in current_connections:
                if conn_id in visited:
                    continue
                visited.add(conn_id)
                memory = self._memories.get(conn_id)
                if memory:
                    chain.events.append({
                        "memory_id": conn_id,
                        "content": memory.content[:80],
                        "level": level + 1,
                    })
                    chain.length += 1
                    new_connections.extend(memory.causal_connections)
            current_connections = new_connections

        chain.summary = f"Causal chain of {chain.length} events starting from root memory"

        self._chains.append(chain)
        return chain

    def create_summary(self, query_id: str) -> TemporalSummary:
        """Create a temporal summary."""
        window = self._windows.get(query_id)
        if not window:
            raise HTTPException(404, f"Window {query_id} not found")

        themes = list(set(m.tags for m in window.memories if m.tags))
        key_events = [m.content[:80] for m in window.memories[:5]]

        summary = TemporalSummary(
            summary_id=hashlib.md5(
                f"summary:{query_id}:{time.time()}".encode()
            ).hexdigest()[:12],
            query_id=query_id,
            start_time=window.time_range_start,
            end_time=window.time_range_end,
            summary_text=window.summary,
            key_events=key_events,
            themes=themes,
            word_count=len(window.summary.split()),
        )

        self._summaries[summary.summary_id] = summary
        return summary

    def get_report(self) -> dict:
        """Get temporal context retriever report."""
        return {
            "total_memories": len(self._memories),
            "total_queries": len(self._queries),
            "total_windows": len(self._windows),
            "total_chains": len(self._chains),
            "total_summaries": len(self._summaries),
            "avg_memory_age_hours": sum(
                (time.time() - m.timestamp) / 3600 for m in self._memories.values()
            ) / max(1, len(self._memories)),
            "recent_queries": [
                {
                    "query": q.query_text[:50],
                    "window_hours": q.time_window_hours,
                    "max_results": q.max_results,
                }
                for q in self._queries[-5:]
            ],
            "recent_chains": [
                {
                    "root": c.root_memory,
                    "length": c.length,
                    "events": len(c.events),
                }
                for c in self._chains[-5:]
            ],
        }


# Module-level singleton
temporal_retriever = TemporalContextRetriever()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/memora/temporal/memories")
async def add_memory(payload: dict):
    """Add a memory for temporal retrieval."""
    memory = temporal_retriever.add_memory(
        content=payload["content"],
        timestamp=payload.get("timestamp", 0),
        tags=payload.get("tags", []),
        importance=payload.get("importance", 0.5),
        causal_connections=payload.get("causal_connections", []),
    )
    return {"memory_id": memory.memory_id, "content": memory.content[:50]}


@router.post("/api/v1/memora/temporal/query")
async def query(payload: dict):
    """Query memories temporally."""
    window = temporal_retriever.query(
        query_text=payload["query_text"],
        target_time=payload.get("target_time", 0),
        time_window_hours=payload.get("time_window_hours", 24.0),
        relevance_threshold=payload.get("relevance_threshold", 0.3),
        max_results=payload.get("max_results", 20),
    )
    return {
        "window_id": window.window_id,
        "memories_found": len(window.memories),
        "total_relevance": window.total_relevance,
        "summary": window.summary[:100],
    }


@router.get("/api/v1/memora/temporal/chains/{memory_id}")
async def build_causal_chain(memory_id: str):
    """Build a causal chain."""
    chain = temporal_retriever.build_causal_chain(memory_id)
    return {
        "chain_id": chain.chain_id,
        "length": chain.length,
        "events": len(chain.events),
        "summary": chain.summary,
    }


@router.get("/api/v1/memora/temporal/report")
async def get_report():
    """Get temporal context retriever report."""
    return temporal_retriever.get_report()
