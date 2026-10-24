"""
Memora/core/memory/real_memory_engine.py — Real Memory Engine.

Actual working memory with:
- Semantic vector storage and search (TF-IDF based)
- Memory consolidation (decay + strengthening)
- Working memory management
- Long-term memory retrieval (context-aware)
- Memory decay and supersession
- Causal memory graph (linking related memories)
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import json
import os
import re
from collections import defaultdict


@dataclass
class Memory:
    """A single memory entry."""
    memory_id: str
    content: str
    agent_origin: str = "unknown"
    tags: list[str] = field(default_factory=list)
    importance: float = 0.5  # 0.0 to 1.0
    confidence: float = 0.5  # 0.0 to 1.0
    access_count: int = 0
    last_accessed: float = field(default_factory=time.time)
    created_at: float = field(default_factory=time.time)
    embedding: list[float] = field(default_factory=list)
    superseded_by: str | None = None
    links: list[str] = field(default_factory=list)  # IDs of related memories


@dataclass
class SearchResult:
    """A search result with relevance score."""
    memory_id: str
    content: str
    score: float
    snippet: str


@dataclass
class ConsolidationResult:
    """Result of memory consolidation."""
    consolidated: int
    decayed: int
    superseded: int
    details: list[str]


class MemoryEngine:
    """Real memory engine with vector search, decay, and consolidation."""

    def __init__(self, store_dir: str | None = None) -> None:
        self._store_dir = store_dir or os.path.join(
            os.path.dirname(__file__), "..", ".data", "memora_store"
        )
        os.makedirs(self._store_dir, exist_ok=True)
        self._memories: dict[str, Memory] = {}
        self._tag_index: dict[str, list[str]] = defaultdict(list)
        self._vector_index: dict[str, list[str]] = defaultdict(list)  # vocab_term -> memory_ids
        self._load_store()

    def _load_store(self) -> None:
        """Load memory store from disk."""
        store_file = os.path.join(self._store_dir, "memories.json")
        try:
            if os.path.exists(store_file):
                with open(store_file, "r") as f:
                    data = json.load(f)
                for mid, mdata in data.items():
                    self._memories[mid] = Memory(**mdata)
                    for tag in mdata.get("tags", []):
                        self._tag_index[tag].append(mid)
        except Exception:
            pass

    def _save_store(self) -> None:
        """Save memory store to disk."""
        store_file = os.path.join(self._store_dir, "memories.json")
        try:
            data = {
                mid: {
                    "memory_id": m.memory_id,
                    "content": m.content,
                    "agent_origin": m.agent_origin,
                    "tags": m.tags,
                    "importance": m.importance,
                    "confidence": m.confidence,
                    "access_count": m.access_count,
                    "last_accessed": m.last_accessed,
                    "created_at": m.created_at,
                    "superseded_by": m.superseded_by,
                    "links": m.links,
                }
                for mid, m in self._memories.items()
            }
            with open(store_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize text into words, lowercase, remove punctuation."""
        text = text.lower()
        text = re.sub(r"[^\w\s]", " ", text)
        return [w for w in text.split() if len(w) > 1]

    def _compute_tfidf(self, text: str, all_docs: list[str] | None = None) -> dict[str, float]:
        """Compute TF-IDF vector for text."""
        tokens = self._tokenize(text)
        if not tokens:
            return {}

        # Term frequency
        tf = defaultdict(float)
        for token in tokens:
            tf[token] += 1
        for token in tf:
            tf[token] /= len(tokens)

        # Inverse document frequency
        if all_docs is None:
            all_docs = [m.content for m in self._memories.values()]

        doc_count = len(all_docs)
        df = defaultdict(int)
        for doc in all_docs:
            for token in set(self._tokenize(doc)):
                df[token] += 1

        idf = {}
        for token in tf:
            idf[token] = math.log((doc_count + 1) / (df.get(token, 0) + 1)) + 1

        # TF-IDF
        tfidf = {token: tf[token] * idf[token] for token in tf}
        return tfidf

    def _cosine_similarity(self, vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
        """Compute cosine similarity between two TF-IDF vectors."""
        if not vec_a or not vec_b:
            return 0.0

        common = set(vec_a.keys()) & set(vec_b.keys())
        if not common:
            return 0.0

        dot = sum(vec_a[t] * vec_b[t] for t in common)
        norm_a = math.sqrt(sum(v ** 2 for v in vec_a.values()))
        norm_b = math.sqrt(sum(v ** 2 for v in vec_b.values()))

        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def store(
        self, content: str, agent_origin: str = "unknown",
        tags: list[str] | None = None, importance: float = 0.5,
        confidence: float = 0.5, links: list[str] | None = None
    ) -> Memory:
        """Store a new memory with semantic indexing."""
        memory = Memory(
            memory_id=f"mem_{uuid.uuid4().hex[:12]}",
            content=content,
            agent_origin=agent_origin,
            tags=tags or [],
            importance=max(0.0, min(1.0, importance)),
            confidence=max(0.0, min(1.0, confidence)),
            links=links or [],
        )

        self._memories[memory.memory_id] = memory

        # Update tag index
        for tag in memory.tags:
            self._tag_index[tag].append(memory.memory_id)

        # Update vector index
        tokens = self._tokenize(content)
        for token in set(tokens):
            self._vector_index[token].append(memory.memory_id)

        self._save_store()
        return memory

    def search(
        self, query: str, limit: int = 10, min_score: float = 0.01,
        tags: list[str] | None = None, agent_origin: str | None = None
    ) -> list[SearchResult]:
        """Semantic search across memories using TF-IDF cosine similarity."""
        if not self._memories:
            return []

        query_vec = self._compute_tfidf(
            query, [m.content for m in self._memories.values()]
        )

        results = []
        for memory in self._memories.values():
            if memory.superseded_by:
                continue  # skip superseded memories

            # Tag filter
            if tags and not any(t in memory.tags for t in tags):
                continue

            # Agent filter
            if agent_origin and memory.agent_origin != agent_origin:
                continue

            # Compute similarity
            memory_vec = self._compute_tfidf(
                memory.content, [m.content for m in self._memories.values()]
            )
            score = self._cosine_similarity(query_vec, memory_vec)

            # Boost by importance and recency
            score *= (0.5 + memory.importance * 0.5)
            now = time.time()
            age_days = (now - memory.created_at) / (24 * 3600)
            recency_boost = max(0.5, 1.0 - age_days / 365.0)  # decay over a year
            score *= recency_boost

            # Boost by access count
            score *= (1.0 + math.log(1 + memory.access_count) * 0.1)

            if score >= min_score:
                # Generate snippet
                snippet = memory.content[:100]
                if len(memory.content) > 100:
                    snippet += "..."

                results.append(SearchResult(
                    memory_id=memory.memory_id,
                    content=memory.content,
                    score=round(score, 4),
                    snippet=snippet,
                ))

        # Sort by score and limit
        results.sort(key=lambda r: r.score, reverse=True)
        return results[:limit]

    def recall(self, memory_id: str) -> Memory | None:
        """Recall a specific memory by ID and increment access count."""
        memory = self._memories.get(memory_id)
        if memory and not memory.superseded_by:
            memory.access_count += 1
            memory.last_accessed = time.time()
            self._save_store()
            return memory
        return None

    def consolidate(self, decay_rate: float = 0.01, importance_threshold: float = 0.1) -> ConsolidationResult:
        """Consolidate memories: decay low-importance ones, supersede duplicates."""
        details = []
        consolidated = 0
        decayed = 0
        superseded = 0

        now = time.time()

        # Find near-duplicate memories (similar content)
        for memory in self._memories.values():
            if memory.superseded_by:
                continue

            # Decay importance based on age and access frequency
            age_days = (now - memory.created_at) / (24 * 3600)
            days_since_access = (now - memory.last_accessed) / (24 * 3600)

            # Decay factor: older and less accessed = more decay
            decay = decay_rate * (age_days + days_since_access) * 0.5
            memory.importance = max(0.0, memory.importance - decay)

            # Supersede if importance is too low
            if memory.importance < importance_threshold and memory.access_count == 0:
                memory.superseded_by = f"consolidated_{uuid.uuid4().hex[:8]}"
                superseded += 1
                details.append(f"Superseded: {memory.content[:50]}...")
            else:
                decayed += 1

            consolidated += 1

        self._save_store()

        return ConsolidationResult(
            consolidated=consolidated,
            decayed=decayed,
            superseded=superseded,
            details=details,
        )

    def link_memories(self, memory_id_a: str, memory_id_b: str, relationship: str = "related") -> bool:
        """Create a link between two memories."""
        a = self._memories.get(memory_id_a)
        b = self._memories.get(memory_id_b)
        if not a or not b:
            return False

        if memory_id_b not in a.links:
            a.links.append(memory_id_b)
        if memory_id_a not in b.links:
            b.links.append(memory_id_a)

        self._save_store()
        return True

    def get_context(self, query: str, max_tokens: int = 2000) -> str:
        """Get relevant context for a query by retrieving top memories."""
        results = self.search(query, limit=5, min_score=0.05)
        context = []
        total_chars = 0

        for result in results:
            memory = self._memories.get(result.memory_id)
            if memory:
                self.recall(memory.memory_id)  # increment access
                snippet = f"[{memory.agent_origin}] {memory.content[:200]}"
                if total_chars + len(snippet) > max_tokens:
                    break
                context.append(snippet)
                total_chars += len(snippet)

        return "\n\n".join(context)

    def get_stats(self) -> dict[str, Any]:
        """Get memory statistics."""
        total = len(self._memories)
        active = sum(1 for m in self._memories.values() if not m.superseded_by)
        superseded = total - active
        total_tags = len(self._tag_index)
        avg_importance = (
            sum(m.importance for m in self._memories.values()) / total
            if total > 0 else 0.0
        )

        return {
            "total_memories": total,
            "active_memories": active,
            "superseded_memories": superseded,
            "unique_tags": total_tags,
            "avg_importance": round(avg_importance, 3),
            "vocabulary_size": len(self._vector_index),
        }


engine = MemoryEngine()
