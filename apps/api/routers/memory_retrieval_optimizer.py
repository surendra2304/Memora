"""
Memora/apps/api/routers/memory_retrieval_optimizer.py — Memora Memory Retrieval Optimizer.

Extends Memora's capabilities with:
1. Semantic Search Optimization: optimize semantic memory retrieval
2. Context Compression: compress retrieved context for efficiency
3. Memory Prioritization: prioritize memories for retrieval
4. Multi-Hop Retrieval: retrieve memories across multiple hops
5. Temporal Retrieval: retrieve memories based on temporal relevance
6. Memory Consolidation Triggers: trigger consolidation when needed
7. Retrieval Caching: cache retrieval results
8. Cross-Namespace Search: search across memory namespaces
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class MemoryItem:
    """A memory item for retrieval."""
    memory_id: str
    content: str
    namespace: str
    importance: float = 0.5
    relevance: float = 0.0
    confidence: float = 0.5
    timestamp: float = 0.0
    tags: list[str] = field(default_factory=list)
    access_count: int = 0
    last_accessed: float = 0.0
    embedding: list[float] = field(default_factory=list)


@dataclass
class RetrievalResult:
    """Result of memory retrieval."""
    memories: list[MemoryItem]
    query: str
    total_count: int
    search_time_ms: float
    strategy: str  # semantic, keyword, temporal, multi_hop
    compressed_context: str = ""


@dataclass
class ContextCompression:
    """Compressed context."""
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    summary: str
    retained_memories: int
    lost_information: float


@dataclass
class PriorityScore:
    """Memory priority score."""
    memory_id: str
    score: float
    factors: dict[str, float]
    recommendation: str  # retrieve, cache, archive, forget


@dataclass
class MultiHopPath:
    """A multi-hop retrieval path."""
    path_id: str
    start_memory: str
    intermediate: list[str]
    end_memory: str
    hop_count: int
    relevance: float
    connections: list[str]


@dataclass
class TemporalRetrieval:
    """Temporal memory retrieval."""
    query: str
    memories: list[MemoryItem]
    time_range: dict[str, float]
    relevance_decay: float
    recency_boost: float


@dataclass
class ConsolidationTrigger:
    """Memory consolidation trigger."""
    trigger_id: str
    reason: str
    memories_to_consolidate: list[str]
    estimated_savings: float
    priority: str  # high, medium, low


@dataclass
class RetrievalCache:
    """Retrieval cache entry."""
    cache_key: str
    query: str
    result: dict
    hit_count: int = 0
    last_accessed: float = 0.0
    ttl: float = 3600.0  # 1 hour


@dataclass
class CrossNamespaceResult:
    """Cross-namespace search result."""
    namespace: str
    memories: list[MemoryItem]
    total_in_namespace: int
    relevant_count: int


class MemoryRetrievalOptimizer:
    """Memory retrieval optimization engine."""

    def __init__(self):
        self._memories: dict[str, MemoryItem] = {}
        self._cache: dict[str, RetrievalCache] = {}
        self._priorities: dict[str, PriorityScore] = {}
        self._multi_hop_paths: list[MultiHopPath] = []
        self._consolidation_triggers: list[ConsolidationTrigger] = []

    # ── Semantic Search Optimization ─────────────────────────────────────

    def semantic_search(
        self,
        query: str,
        namespace: str | None = None,
        top_k: int = 10,
    ) -> RetrievalResult:
        """Optimized semantic search."""
        start_time = time.time()

        # Check cache
        cache_key = hashlib.md5(
            f"semantic:{query}:{namespace}:{top_k}".encode()
        ).hexdigest()

        cached = self._cache.get(cache_key)
        if cached:
            cached.hit_count += 1
            cached.last_accessed = time.time()
            return RetrievalResult(
                memories=cached.result["memories"],
                query=query,
                total_count=len(cached.result["memories"]),
                search_time_ms=0.0,
                strategy="cached",
                compressed_context=cached.result.get("compressed_context", ""),
            )

        # Filter memories
        candidates = list(self._memories.values())
        if namespace:
            candidates = [m for m in candidates if m.namespace == namespace]

        # Simple semantic scoring (keyword overlap + importance + recency)
        query_words = set(query.lower().split())

        scored = []
        for memory in candidates:
            # Content overlap
            content_words = set(memory.content.lower().split())
            overlap = len(query_words & content_words) / max(1, len(query_words))

            # Importance weight
            importance_score = memory.importance

            # Recency boost
            days_old = (time.time() - memory.timestamp) / 86400
            recency = 1.0 / (1.0 + days_old * 0.01)

            # Access frequency
            access_score = min(1.0, memory.access_count * 0.1)

            # Combined score
            score = overlap * 0.5 + importance_score * 0.2 + recency * 0.15 + access_score * 0.15

            memory.relevance = score
            scored.append((score, memory))

        # Sort and take top
        scored.sort(key=lambda x: -x[0])
        results = [m for _, m in scored[:top_k]]

        # Update access counts
        for m in results:
            m.access_count += 1
            m.last_accessed = time.time()

        # Cache result
        self._cache[cache_key] = RetrievalCache(
            cache_key=cache_key,
            query=query,
            result={
                "memories": [m.__dict__ for m in results],
                "compressed_context": self._compress_context([m.content for m in results]),
            },
            last_accessed=time.time(),
        )

        elapsed = (time.time() - start_time) * 1000

        return RetrievalResult(
            memories=results,
            query=query,
            total_count=len(results),
            search_time_ms=elapsed,
            strategy="semantic",
            compressed_context=self._compress_context([m.content for m in results]),
        )

    def _compress_context(self, contents: list[str]) -> str:
        """Compress context into a summary."""
        # Simple compression: take first sentence of each, join
        summaries = []
        for content in contents[:5]:
            first_sentence = content.split(".")[0] + "."
            summaries.append(first_sentence)

        compressed = " ".join(summaries)
        original_tokens = sum(len(c.split()) for c in contents)
        compressed_tokens = len(compressed.split())

        return compressed

    # ── Context Compression ───────────────────────────────────────────────

    def compress_context(
        self,
        memories: list[MemoryItem],
        target_tokens: int = 200,
    ) -> ContextCompression:
        """Compress retrieved context."""
        original_tokens = sum(len(m.content.split()) for m in memories)

        # Sort by importance * relevance
        ranked = sorted(memories, key=lambda m: -(m.importance * (1 + m.relevance)))

        # Select memories that fit within target
        compressed_parts = []
        current_tokens = 0
        retained = 0

        for memory in ranked:
            token_count = len(memory.content.split())
            if current_tokens + token_count <= target_tokens:
                compressed_parts.append(memory.content)
                current_tokens += token_count
                retained += 1

        compressed_text = " ".join(compressed_parts)
        lost_info = 1.0 - (retained / max(1, len(memories)))

        return ContextCompression(
            original_tokens=original_tokens,
            compressed_tokens=current_tokens,
            compression_ratio=current_tokens / max(1, original_tokens),
            summary=compressed_text,
            retained_memories=retained,
            lost_information=lost_info,
        )

    # ── Memory Prioritization ────────────────────────────────────────────

    def prioritize_memories(self) -> list[PriorityScore]:
        """Prioritize memories for retrieval."""
        for mid, memory in self._memories.items():
            # Factors
            importance = memory.importance
            recency = 1.0 / (1 + (time.time() - memory.timestamp) / 86400)
            access_freq = min(1.0, memory.access_count * 0.1)
            relevance = memory.relevance

            # Combined score
            score = (
                importance * 0.4 +
                recency * 0.25 +
                access_freq * 0.2 +
                relevance * 0.15
            )

            # Recommendation
            if score > 0.7:
                rec = "retrieve"
            elif score > 0.4:
                rec = "cache"
            elif score > 0.2:
                rec = "archive"
            else:
                rec = "forget"

            self._priorities[mid] = PriorityScore(
                memory_id=mid,
                score=score,
                factors={
                    "importance": importance,
                    "recency": recency,
                    "access_frequency": access_freq,
                    "relevance": relevance,
                },
                recommendation=rec,
            )

        return sorted(self._priorities.values(), key=lambda p: -p.score)

    # ── Multi-Hop Retrieval ──────────────────────────────────────────────

    def multi_hop_retrieval(
        self,
        start_query: str,
        max_hops: int = 3,
    ) -> list[MultiHopPath]:
        """Retrieve memories across multiple hops."""
        # First hop: semantic search
        first_result = self.semantic_search(start_query, top_k=5)

        if not first_result.memories:
            return []

        paths = []
        # Explore from each first-hop memory
        for memory in first_result.memories:
            # Find connections based on shared tags or content overlap
            connections = []
            for other in self._memories.values():
                if other.memory_id == memory.memory_id:
                    continue

                # Tag overlap
                tag_overlap = len(set(memory.tags) & set(other.tags))

                # Content overlap
                content_overlap = len(
                    set(memory.content.lower().split()) &
                    set(other.content.lower().split())
                )

                if tag_overlap > 0 or content_overlap > 5:
                    connections.append(other)

            for conn in connections[:3]:
                # Create path
                relevance = memory.relevance * 0.5 + conn.importance * 0.5

                path = MultiHopPath(
                    path_id=hashlib.md5(
                        f"path:{memory.memory_id}:{conn.memory_id}:{time.time()}".encode()
                    ).hexdigest()[:12],
                    start_memory=memory.memory_id,
                    intermediate=[],
                    end_memory=conn.memory_id,
                    hop_count=2,
                    relevance=relevance,
                    connections=[
                        f"{memory.memory_id} -> {conn.memory_id}"
                    ],
                )
                paths.append(path)

        self._multi_hop_paths.extend(paths)
        return sorted(paths, key=lambda p: -p.relevance)[:10]

    # ── Temporal Retrieval ───────────────────────────────────────────────

    def temporal_retrieval(
        self,
        query: str,
        time_range_days: float = 30.0,
        boost_recency: bool = True,
    ) -> TemporalRetrieval:
        """Temporal memory retrieval."""
        cutoff = time.time() - time_range_days * 86400

        # Filter by time
        temporal_memories = [
            m for m in self._memories.values()
            if m.timestamp >= cutoff
        ]

        # Score with recency boost
        query_words = set(query.lower().split())

        scored = []
        for memory in temporal_memories:
            content_words = set(memory.content.lower().split())
            overlap = len(query_words & content_words) / max(1, len(query_words))

            # Recency
            days_old = (time.time() - memory.timestamp) / 86400
            recency = 1.0 / (1.0 + days_old / time_range_days)

            # Combined with recency boost
            if boost_recency:
                score = overlap * 0.6 + memory.importance * 0.2 + recency * 0.2
            else:
                score = overlap * 0.7 + memory.importance * 0.3

            memory.relevance = score
            scored.append((score, memory))

        scored.sort(key=lambda x: -x[0])
        results = [m for _, m in scored[:20]]

        return TemporalRetrieval(
            query=query,
            memories=results,
            time_range={
                "start": cutoff,
                "end": time.time(),
                "days": time_range_days,
            },
            relevance_decay=0.01,
            recency_boost=boost_recency,
        )

    # ── Memory Consolidation Triggers ───────────────────────────────────

    def check_consolidation_triggers(self) -> list[ConsolidationTrigger]:
        """Check if memory consolidation is needed."""
        triggers = []

        # Check for high-similarity memories
        memory_list = list(self._memories.values())
        for i, m1 in enumerate(memory_list):
            for m2 in memory_list[i+1:]:
                if m1.namespace != m2.namespace:
                    continue

                similarity = len(
                    set(m1.content.lower().split()) &
                    set(m2.content.lower().split())
                ) / max(1, min(len(m1.content.split()), len(m2.content.split())))

                if similarity > 0.8 and m1.memory_id != m2.memory_id:
                    triggers.append(ConsolidationTrigger(
                        trigger_id=hashlib.md5(
                            f"trigger:{m1.memory_id}:{m2.memory_id}:{time.time()}".encode()
                        ).hexdigest()[:12],
                        reason=f"High similarity ({similarity:.0%}) between memories",
                        memories_to_consolidate=[m1.memory_id, m2.memory_id],
                        estimated_savings=similarity * 10,
                        priority="high" if similarity > 0.9 else "medium",
                    ))

        # Check for low-priority memories
        priorities = self.prioritize_memories()
        low_priority = [p for p in priorities if p.recommendation == "forget"]

        if len(low_priority) > 10:
            triggers.append(ConsolidationTrigger(
                trigger_id=hashlib.md5(f"trigger:low_priority:{time.time()}").hexdigest()[:12],
                reason=f"{len(low_priority)} low-priority memories detected",
                memories_to_consolidate=[p.memory_id for p in low_priority[:10]],
                estimated_savings=len(low_priority) * 5,
                priority="medium",
            ))

        self._consolidation_triggers.extend(triggers)
        return triggers

    # ── Retrieval Caching ────────────────────────────────────────────────

    def get_cache_stats(self) -> dict:
        """Get cache statistics."""
        total = len(self._cache)
        hits = sum(c.hit_count for c in self._cache.values())
        total_accesses = total + hits

        return {
            "cache_size": total,
            "total_hits": hits,
            "hit_rate": hits / max(1, total_accesses),
            "avg_ttl": sum(c.ttl for c in self._cache.values()) / max(1, total),
        }

    def clear_cache(self) -> int:
        """Clear expired cache entries."""
        now = time.time()
        expired = [
            key for key, cache in self._cache.items()
            if now - cache.last_accessed > cache.ttl
        ]

        for key in expired:
            del self._cache[key]

        return len(expired)

    # ── Cross-Namespace Search ───────────────────────────────────────────

    def cross_namespace_search(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[CrossNamespaceResult]:
        """Search across all namespaces."""
        namespaces = set(m.namespace for m in self._memories.values())
        results = []

        for namespace in namespaces:
            result = self.semantic_search(query, namespace=namespace, top_k=top_k)
            results.append(CrossNamespaceResult(
                namespace=namespace,
                memories=result.memories,
                total_in_namespace=len([
                    m for m in self._memories.values()
                    if m.namespace == namespace
                ]),
                relevant_count=len(result.memories),
            ))

        return sorted(results, key=lambda r: -r.relevant_count)

    # ── Reporting ────────────────────────────────────────────────────────

    def get_retrieval_report(self) -> dict:
        """Get retrieval optimization report."""
        return {
            "total_memories": len(self._memories),
            "cache_stats": self.get_cache_stats(),
            "consolidation_triggers": len(self._consolidation_triggers),
            "multi_hop_paths": len(self._multi_hop_paths),
            "prioritized_memories": len(self._priorities),
            "namespaces": list(set(m.namespace for m in self._memories.values())),
        }


# Module-level singleton
retrieval_optimizer = MemoryRetrievalOptimizer()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/memory/retrieve/semantic")
async def semantic_search(payload: dict):
    """Semantic search."""
    result = retrieval_optimizer.semantic_search(
        payload["query"],
        namespace=payload.get("namespace"),
        top_k=payload.get("top_k", 10),
    )
    return {
        "memories": [m.__dict__ for m in result.memories],
        "query": result.query,
        "total_count": result.total_count,
        "search_time_ms": result.search_time_ms,
        "strategy": result.strategy,
        "compressed_context": result.compressed_context,
    }


@router.post("/api/v1/memory/retrieve/compress")
async def compress_context(payload: dict):
    """Compress context."""
    memories = [
        MemoryItem(
            memory_id=m["memory_id"],
            content=m["content"],
            namespace=m["namespace"],
            importance=m.get("importance", 0.5),
            relevance=m.get("relevance", 0.0),
            timestamp=m.get("timestamp", time.time()),
        )
        for m in payload["memories"]
    ]
    result = retrieval_optimizer.compress_context(
        memories,
        target_tokens=payload.get("target_tokens", 200),
    )
    return result.__dict__


@router.get("/api/v1/memory/retrieve/priorities")
async def prioritize_memories():
    """Prioritize memories."""
    priorities = retrieval_optimizer.prioritize_memories()
    return [p.__dict__ for p in priorities]


@router.post("/api/v1/memory/retrieve/multi_hop")
async def multi_hop_retrieval(payload: dict):
    """Multi-hop retrieval."""
    paths = retrieval_optimizer.multi_hop_retrieval(
        payload["query"],
        max_hops=payload.get("max_hops", 3),
    )
    return [p.__dict__ for p in paths]


@router.post("/api/v1/memory/retrieve/temporal")
async def temporal_retrieval(payload: dict):
    """Temporal retrieval."""
    result = retrieval_optimizer.temporal_retrieval(
        payload["query"],
        time_range_days=payload.get("time_range_days", 30.0),
        boost_recency=payload.get("boost_recency", True),
    )
    return {
        "query": result.query,
        "memories": [m.__dict__ for m in result.memories],
        "time_range": result.time_range,
        "relevance_decay": result.relevance_decay,
        "recency_boost": result.recency_boost,
    }


@router.get("/api/v1/memory/retrieve/consolidation_triggers")
async def check_consolidation_triggers():
    """Check consolidation triggers."""
    triggers = retrieval_optimizer.check_consolidation_triggers()
    return [t.__dict__ for t in triggers]


@router.get("/api/v1/memory/retrieve/cache_stats")
async def get_cache_stats():
    """Get cache stats."""
    return retrieval_optimizer.get_cache_stats()


@router.post("/api/v1/memory/retrieve/cache/clear")
async def clear_cache():
    """Clear expired cache."""
    cleared = retrieval_optimizer.clear_cache()
    return {"cleared": cleared}


@router.post("/api/v1/memory/retrieve/cross_namespace")
async def cross_namespace_search(payload: dict):
    """Cross-namespace search."""
    results = retrieval_optimizer.cross_namespace_search(
        payload["query"],
        top_k=payload.get("top_k", 5),
    )
    return [
        {
            "namespace": r.namespace,
            "memories": [m.__dict__ for m in r.memories],
            "total_in_namespace": r.total_in_namespace,
            "relevant_count": r.relevant_count,
        }
        for r in results
    ]


@router.get("/api/v1/memory/retrieve/report")
async def get_retrieval_report():
    """Get retrieval report."""
    return retrieval_optimizer.get_retrieval_report()
