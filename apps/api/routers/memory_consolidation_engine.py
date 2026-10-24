"""
Memora/apps/api/routers/memory_consolidation_engine.py — Memora Memory Consolidation Engine.

Automates memory consolidation: importance scoring, decay management,
similarity-based merging, knowledge extraction, and sleep-like consolidation
cycles that strengthen important memories while pruning weak ones.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class ConsolidationPolicy:
    """Policy for memory consolidation."""
    policy_id: str
    importance_threshold: float = 0.3
    max_memories: int = 10000
    decay_rate: float = 0.1
    merge_similarity_threshold: float = 0.85
    retention_period_days: float = 365.0
    importance_factors: list[str] = field(default_factory=lambda: ["access_frequency", "emotional_weight", "novelty", "relevance"])
    auto_prune: bool = True
    auto_merge: bool = True
    created_at: float = 0.0


@dataclass
class MemoryItem:
    """A memory item for consolidation."""
    memory_id: str
    content: str
    importance_score: float = 0.0
    access_count: int = 0
    last_accessed: float = 0.0
    created_at: float = 0.0
    tags: list[str] = field(default_factory=list)
    emotional_weight: float = 0.0
    novelty: float = 0.0
    source: str = ""
    status: str = "active"  # active, consolidated, pruned, archived


@dataclass
class ConsolidationResult:
    """Result of a consolidation cycle."""
    consolidation_id: str
    total_memories_processed: int = 0
    memories_merged: int = 0
    memories_pruned: int = 0
    memories_strengthened: int = 0
    knowledge_extracted: int = 0
    importance_updated: int = 0
    duration_ms: float = 0.0
    started_at: float = 0.0
    completed_at: float = 0.0
    report: dict = field(default_factory=dict)


@dataclass
class SimilarityPair:
    """A pair of similar memories for potential merging."""
    memory_a_id: str
    memory_b_id: str
    similarity: float = 0.0
    merge_score: float = 0.0
    recommended_action: str = ""  # merge, keep_both, review


@dataclass
class KnowledgeExtraction:
    """Knowledge extracted from memories."""
    extraction_id: str
    content: str
    source_memories: list[str] = field(default_factory=list)
    confidence: float = 0.0
    category: str = ""
    created_at: float = 0.0


@dataclass
class ImportanceScore:
    """Importance score for a memory."""
    memory_id: str
    access_frequency_score: float = 0.0
    emotional_weight_score: float = 0.0
    novelty_score: float = 0.0
    relevance_score: float = 0.0
    total_score: float = 0.0
    ranking: int = 0


@dataclass
class DecayReport:
    """Report on memory decay."""
    total_memories: int = 0
    decayed_memories: int = 0
    stale_memories: int = 0
    preserved_memories: int = 0
    average_age_days: float = 0.0
    pruning_candidates: int = 0


class MemoryConsolidationEngine:
    """Memory consolidation engine."""

    def __init__(self):
        self._policies: dict[str, ConsolidationPolicy] = {}
        self._memories: dict[str, MemoryItem] = {}
        self._consolidations: list[ConsolidationResult] = []
        self._extractions: dict[str, KnowledgeExtraction] = {}
        self._similarity_pairs: list[SimilarityPair] = []

        # Create default policy
        self._create_policy("default", 0.3, 10000, 0.1, 0.85, 365)

    def _create_policy(
        self,
        name: str,
        importance_threshold: float,
        max_memories: int,
        decay_rate: float,
        merge_threshold: float,
        retention_days: float,
    ) -> ConsolidationPolicy:
        """Create a consolidation policy."""
        policy = ConsolidationPolicy(
            policy_id=hashlib.md5(
                f"policy:{name}:{time.time()}".encode()
            ).hexdigest()[:12],
            importance_threshold=importance_threshold,
            max_memories=max_memories,
            decay_rate=decay_rate,
            merge_similarity_threshold=merge_threshold,
            retention_period_days=retention_days,
            created_at=time.time(),
        )
        self._policies[policy.policy_id] = policy
        return policy

    def add_memory(
        self,
        content: str,
        source: str = "",
        tags: list[str] | None = None,
        emotional_weight: float = 0.0,
    ) -> MemoryItem:
        """Add a memory for consolidation."""
        memory = MemoryItem(
            memory_id=hashlib.md5(
                f"memory:{content[:50]}:{time.time()}".encode()
            ).hexdigest()[:12],
            content=content,
            access_count=1,
            last_accessed=time.time(),
            created_at=time.time(),
            tags=tags or [],
            emotional_weight=emotional_weight,
            novelty=0.8,  # New memories are novel
            source=source,
        )
        self._memories[memory.memory_id] = memory
        return memory

    def consolidate(self, policy_id: str = "") -> ConsolidationResult:
        """Run a consolidation cycle."""
        start = time.time()

        policy = None
        for p in self._policies.values():
            if p.policy_id == policy_id or p.policy_id == "default":
                policy = p
                break

        result = ConsolidationResult(
            consolidation_id=hashlib.md5(
                f"consolidation:{time.time()}".encode()
            ).hexdigest()[:12],
            started_at=start,
        )

        memories = list(self._memories.values())
        result.total_memories_processed = len(memories)

        # 1. Update importance scores
        scored = []
        for memory in memories:
            importance = self._calculate_importance(memory, policy)
            memory.importance_score = importance
            scored.append(importance)

        scored.sort(reverse=True)
        for i, score in enumerate(scored):
            for memory in memories:
                if memory.importance_score == score:
                    memory.importance_score = score
                    break

        result.importance_updated = len(memories)

        # 2. Apply decay
        now = time.time()
        decayed = 0
        for memory in memories:
            age_days = (now - memory.created_at) / 86400
            if age_days > 30:
                decay_amount = policy.decay_rate * (age_days / 30)
                memory.importance_score = max(0, memory.importance_score - decay_amount)
                if memory.importance_score < 0.1:
                    decayed += 1

        result.memories_strengthened = len(memories) - decayed

        # 3. Find and merge similar memories
        if policy.auto_merge and len(memories) > 1:
            for i, m1 in enumerate(memories):
                for j, m2 in enumerate(memories):
                    if i >= j:
                        continue
                    similarity = self._calculate_similarity(m1, m2)
                    if similarity > policy.merge_similarity_threshold:
                        pair = SimilarityPair(
                            memory_a_id=m1.memory_id,
                            memory_b_id=m2.memory_id,
                            similarity=similarity,
                            merge_score=(similarity + (m1.importance_score + m2.importance_score) / 2) / 2,
                            recommended_action="merge",
                        )
                        self._similarity_pairs.append(pair)
                        result.memories_merged += 1

        # 4. Prune low-importance memories
        if policy.auto_prune:
            pruned = 0
            for memory in list(self._memories.values()):
                if memory.importance_score < policy.importance_threshold and memory.status == "active":
                    memory.status = "pruned"
                    pruned += 1
            result.memories_pruned = pruned

        # 5. Extract knowledge from high-importance memories
        high_importance = [m for m in memories if m.importance_score > 0.7]
        for memory in high_importance[:10]:
            extraction = KnowledgeExtraction(
                extraction_id=hashlib.md5(
                    f"extraction:{memory.memory_id}:{time.time()}".encode()
                ).hexdigest()[:12],
                content=f"Key insight from memory: {memory.content[:200]}",
                source_memories=[memory.memory_id],
                confidence=memory.importance_score,
                category=memory.tags[0] if memory.tags else "general",
                created_at=time.time(),
            )
            self._extractions[extraction.extraction_id] = extraction
            result.knowledge_extracted += 1

        result.completed_at = time.time()
        result.duration_ms = (time.time() - start) * 1000

        result.report = {
            "total_memories": len(self._memories),
            "active_memories": sum(1 for m in self._memories.values() if m.status == "active"),
            "pruned_memories": sum(1 for m in self._memories.values() if m.status == "pruned"),
            "avg_importance": sum(m.importance_score for m in self._memories.values()) / max(1, len(self._memories)),
            "similarity_pairs_found": len(self._similarity_pairs),
            "knowledge_items": len(self._extractions),
        }

        self._consolidations.append(result)
        return result

    def _calculate_importance(self, memory: MemoryItem, policy: ConsolidationPolicy) -> float:
        """Calculate importance score for a memory."""
        now = time.time()
        age_days = (now - memory.created_at) / 86400

        # Access frequency score (recent accesses are more important)
        access_score = min(1.0, memory.access_count / 10)

        # Emotional weight
        emotional_score = memory.emotional_weight

        # Novelty (degrades over time)
        novelty_score = memory.novelty * max(0, 1 - age_days / 90)

        # Relevance (based on tags and source)
        relevance_score = 0.5 + (len(memory.tags) * 0.05)

        total = (
            access_score * 0.3 +
            emotional_score * 0.2 +
            novelty_score * 0.25 +
            relevance_score * 0.25
        )

        return total

    def _calculate_similarity(self, m1: MemoryItem, m2: MemoryItem) -> float:
        """Calculate similarity between two memories."""
        # Content similarity (simple substring match)
        content_sim = 0.0
        if m1.content[:20] in m2.content or m2.content[:20] in m1.content:
            content_sim = 0.9

        # Tag similarity
        shared_tags = set(m1.tags) & set(m2.tags)
        tag_sim = len(shared_tags) / max(1, len(set(m1.tags) | set(m2.tags)))

        # Source similarity
        source_sim = 1.0 if m1.source == m2.source else 0.3

        total_similarity = content_sim * 0.5 + tag_sim * 0.3 + source_sim * 0.2
        return total_similarity

    def get_decay_report(self) -> DecayReport:
        """Get memory decay report."""
        memories = list(self._memories.values())
        now = time.time()

        decayed = 0
        stale = 0
        preserved = 0
        total_age = 0
        pruning_candidates = 0

        for memory in memories:
            age_days = (now - memory.created_at) / 86400
            total_age += age_days

            if memory.importance_score < 0.3:
                stale += 1
            if memory.status == "pruned":
                decayed += 1
            elif memory.status == "active" and memory.importance_score > 0.5:
                preserved += 1
            if memory.importance_score < 0.2 and memory.status == "active":
                pruning_candidates += 1

        return DecayReport(
            total_memories=len(memories),
            decayed_memories=decayed,
            stale_memories=stale,
            preserved_memories=preserved,
            average_age_days=total_age / max(1, len(memories)),
            pruning_candidates=pruning_candidates,
        )

    def get_report(self) -> dict:
        """Get memory consolidation report."""
        return {
            "total_policies": len(self._policies),
            "total_memories": len(self._memories),
            "total_consolidations": len(self._consolidations),
            "total_extractions": len(self._extractions),
            "total_similarity_pairs": len(self._similarity_pairs),
            "active_memories": sum(1 for m in self._memories.values() if m.status == "active"),
            "pruned_memories": sum(1 for m in self._memories.values() if m.status == "pruned"),
            "avg_importance": sum(m.importance_score for m in self._memories.values()) / max(1, len(self._memories)),
            "recent_consolidations": [
                {
                    "memories_merged": c.memories_merged,
                    "memories_pruned": c.memories_pruned,
                    "knowledge_extracted": c.knowledge_extracted,
                    "duration_ms": c.duration_ms,
                }
                for c in self._consolidations[-5:]
            ],
            "decay_report": self.get_decay_report().__dict__,
        }


# Module-level singleton
consolidation_engine = MemoryConsolidationEngine()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/memora/consolidation/memories")
async def add_memory(payload: dict):
    """Add a memory for consolidation."""
    memory = consolidation_engine.add_memory(
        content=payload["content"],
        source=payload.get("source", ""),
        tags=payload.get("tags", []),
        emotional_weight=payload.get("emotional_weight", 0.0),
    )
    return {"memory_id": memory.memory_id, "importance": memory.importance_score}


@router.post("/api/v1/memora/consolidation/cycle")
async def consolidate(payload: dict | None = None):
    """Run a consolidation cycle."""
    policy_id = (payload or {}).get("policy_id", "")
    result = consolidation_engine.consolidate(policy_id)
    return {
        "consolidation_id": result.consolidation_id,
        "total_memories_processed": result.total_memories_processed,
        "memories_merged": result.memories_merged,
        "memories_pruned": result.memories_pruned,
        "memories_strengthened": result.memories_strengthened,
        "knowledge_extracted": result.knowledge_extracted,
        "duration_ms": result.duration_ms,
    }


@router.get("/api/v1/memora/consolidation/decay-report")
async def get_decay_report():
    """Get memory decay report."""
    return consolidation_engine.get_decay_report().__dict__


@router.get("/api/v1/memora/consolidation/report")
async def get_report():
    """Get memory consolidation report."""
    return consolidation_engine.get_report()
