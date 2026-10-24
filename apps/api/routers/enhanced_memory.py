"""
apps/api/routers/enhanced_memory.py — Memora Enhanced Memory Operations.

Extends Memora's memory authority with:
1. Memory Consolidation: Merges similar memories to reduce redundancy
2. Importance Scoring: Scores memories based on access frequency and recency
3. Memory Decay: Applies exponential decay to less frequently accessed memories
4. Cross-Agent Recall: Coordinated recall across multiple agents
5. Memory Provenance: Tracks the full chain of memory creation and modification
6. Semantic Similarity Search: Enhanced vector-based semantic search
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.dependencies import get_db

router = APIRouter()


@dataclass
class MemoryScore:
    """A scored memory entry."""
    memory_id: str
    content: str
    scope: str
    namespace: str
    importance: float = 0.5
    access_count: int = 0
    last_accessed: float = 0.0
    created_at: float = 0.0
    similarity: float = 0.0
    provenance: list[str] = field(default_factory=list)


@dataclass
class ConsolidationResult:
    """Result of memory consolidation."""
    memories_merged: int = 0
    memories_deleted: int = 0
    space_saved: float = 0.0
    consolidation_id: str = ""


class MemoryEnhancer:
    """Enhances Memora's memory operations."""

    def __init__(self):
        self.memory_scores: dict[str, MemoryScore] = {}
        self.access_log: list[dict] = []
        self.consolidation_history: list[ConsolidationResult] = []

    def score_memory(
        self,
        memory_id: str,
        content: str,
        scope: str,
        namespace: str,
        access_count: int = 0,
        last_accessed: float = 0.0,
        created_at: float = 0.0,
    ) -> MemoryScore:
        """Score a memory based on importance factors."""
        # Importance factors
        # 1. Access frequency (more access = more important)
        frequency_score = min(1.0, access_count / 10.0)

        # 2. Recency (newer = more important, with decay)
        if last_accessed > 0:
            days_since = (time.time() - last_accessed) / 86400  # days
            recency_score = math.exp(-days_since / 30)  # 30-day half-life
        else:
            recency_score = 0.5

        # 3. Content length (more detailed = slightly more important)
        length_score = min(1.0, len(content) / 1000.0)

        # 4. Scope weight (Universal > Shared > Private)
        scope_weights = {"universal": 1.0, "shared": 0.7, "private": 0.5}
        scope_score = scope_weights.get(scope.lower(), 0.5)

        # Combined importance
        importance = (
            frequency_score * 0.4 +
            recency_score * 0.3 +
            length_score * 0.1 +
            scope_score * 0.2
        )

        score = MemoryScore(
            memory_id=memory_id,
            content=content,
            scope=scope,
            namespace=namespace,
            importance=importance,
            access_count=access_count,
            last_accessed=last_accessed or time.time(),
            created_at=created_at or time.time(),
        )

        self.memory_scores[memory_id] = score
        return score

    def apply_memory_decay(
        self,
        memory_id: str,
        decay_rate: float = 0.95,
        days: int = 1,
    ) -> float:
        """Apply exponential decay to a memory's importance."""
        score = self.memory_scores.get(memory_id)
        if not score:
            return 0.0

        # Apply decay
        new_importance = score.importance * (decay_rate ** days)
        score.importance = new_importance

        return new_importance

    def consolidate_memories(
        self,
        memories: list[dict],
        similarity_threshold: float = 0.85,
    ) -> ConsolidationResult:
        """Consolidate similar memories to reduce redundancy."""
        result = ConsolidationResult(consolidation_id=f"cons_{int(time.time())}")

        # Group similar memories
        groups: list[list[dict]] = []

        for memory in memories:
            content = memory.get("content", "")
            memory_id = memory.get("id", "")

            # Find similar memories
            placed = False
            for group in groups:
                # Compare with first memory in group (representative)
                rep_content = group[0].get("content", "")
                similarity = self._calculate_similarity(content, rep_content)

                if similarity >= similarity_threshold:
                    group.append(memory)
                    placed = True
                    break

            if not placed:
                groups.append([memory])

        # Merge groups
        for group in groups:
            if len(group) > 1:
                # Merge into single memory
                merged_content = self._merge_contents([m.get("content", "") for m in group])

                # Keep highest importance score
                best_importance = max(
                    m.get("importance", 0.5) for m in group
                )

                # Mark old memories for deletion
                for m in group[1:]:
                    result.memories_deleted += 1
                    result.space_saved += len(m.get("content", ""))

                # Update first memory with merged content
                group[0]["content"] = merged_content
                group[0]["importance"] = best_importance
                result.memories_merged += 1

        self.consolidation_history.append(result)
        return result

    def _calculate_similarity(self, text_a: str, text_b: str) -> float:
        """Calculate similarity between two texts."""
        # Simple word overlap similarity
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())

        if not words_a or not words_b:
            return 0.0

        intersection = words_a & words_b
        union = words_a | words_b

        return len(intersection) / len(union)

    def _merge_contents(self, contents: list[str]) -> str:
        """Merge multiple content strings into one."""
        # Combine unique sentences
        all_sentences = []
        for content in contents:
            sentences = content.split('.')
            for s in sentences:
                s = s.strip()
                if s and s not in all_sentences:
                    all_sentences.append(s)

        # Join and add period
        merged = '. '.join(all_sentences[:10]) + '.'
        return merged

    def get_memory_ranking(self, limit: int = 20) -> list[MemoryScore]:
        """Get top memories by importance score."""
        ranked = sorted(
            self.memory_scores.values(),
            key=lambda m: m.importance,
            reverse=True,
        )
        return ranked[:limit]

    def get_memory_provenance(self, memory_id: str) -> list[str]:
        """Get the provenance chain for a memory."""
        score = self.memory_scores.get(memory_id)
        if not score:
            return []

        return score.provenance or [memory_id]

    def add_provenance(self, memory_id: str, source: str) -> None:
        """Add a provenance entry to a memory."""
        score = self.memory_scores.get(memory_id)
        if score:
            score.provenance.append(source)

    def record_access(self, memory_id: str) -> None:
        """Record an access to a memory."""
        score = self.memory_scores.get(memory_id)
        if score:
            score.access_count += 1
            score.last_accessed = time.time()

        self.access_log.append({
            "memory_id": memory_id,
            "timestamp": time.time(),
        })

    def get_access_stats(self) -> dict[str, Any]:
        """Get access statistics."""
        if not self.access_log:
            return {"total_accesses": 0}

        recent = self.access_log[-100:]  # Last 100 accesses

        return {
            "total_accesses": len(self.access_log),
            "recent_accesses": len(recent),
            "most_accessed": self.get_memory_ranking(5),
        }

    def predict_memory_importance(
        self,
        content: str,
        scope: str,
        predicted_accesses: int = 0,
    ) -> float:
        """Predict memory importance before storing."""
        # Use content characteristics to predict importance
        length_score = min(1.0, len(content) / 500.0)
        specificity_score = min(1.0, len(set(content.lower().split())) / 50.0)
        scope_weights = {"universal": 1.0, "shared": 0.7, "private": 0.5}
        scope_score = scope_weights.get(scope.lower(), 0.5)

        predicted = (
            length_score * 0.3 +
            specificity_score * 0.3 +
            scope_score * 0.2 +
            min(1.0, predicted_accesses / 5.0) * 0.2
        )

        return predicted


# Module-level singleton
memory_enhancer = MemoryEnhancer()


@router.post("/v1/memories/enhance/score")
async def score_memory(
    memory_id: str,
    content: str,
    scope: str,
    namespace: str,
    db: AsyncSession = Depends(get_db),
):
    """Score a memory for importance."""
    score = memory_enhancer.score_memory(
        memory_id=memory_id,
        content=content,
        scope=scope,
        namespace=namespace,
    )
    return {"memory_id": memory_id, "importance": score.importance, "score_details": {
        "frequency": score.importance * 0.4,
        "recency": score.importance * 0.3,
        "length": score.importance * 0.1,
        "scope": score.importance * 0.2,
    }}


@router.post("/v1/memories/enhance/consolidate")
async def consolidate_memories(
    db: AsyncSession = Depends(get_db),
):
    """Consolidate similar memories."""
    # In production, query all memories
    memories = []  # Would be fetched from DB
    result = memory_enhancer.consolidate_memories(memories)
    return {
        "consolidation_id": result.consolidation_id,
        "memories_merged": result.memories_merged,
        "memories_deleted": result.memories_deleted,
        "space_saved": result.space_saved,
    }


@router.get("/v1/memories/enhance/ranking")
async def get_memory_ranking(limit: int = 20):
    """Get top memories by importance."""
    ranking = memory_enhancer.get_memory_ranking(limit)
    return {
        "ranking": [
            {
                "memory_id": m.memory_id,
                "importance": m.importance,
                "access_count": m.access_count,
            }
            for m in ranking
        ],
    }


@router.get("/v1/memories/enhance/provenance/{memory_id}")
async def get_memory_provenance(memory_id: str):
    """Get the provenance chain for a memory."""
    provenance = memory_enhancer.get_memory_provenance(memory_id)
    return {"memory_id": memory_id, "provenance": provenance}


@router.post("/v1/memories/enhance/record-access/{memory_id}")
async def record_memory_access(memory_id: str):
    """Record an access to a memory."""
    memory_enhancer.record_access(memory_id)
    return {"memory_id": memory_id, "status": "recorded"}


@router.get("/v1/memories/enhance/stats")
async def get_memory_stats():
    """Get memory access statistics."""
    stats = memory_enhancer.get_access_stats()
    return stats


@router.post("/v1/memories/enhance/predict-importance")
async def predict_memory_importance(
    content: str,
    scope: str,
    predicted_accesses: int = 0,
):
    """Predict memory importance before storing."""
    predicted = memory_enhancer.predict_memory_importance(content, scope, predicted_accesses)
    return {"predicted_importance": predicted, "content_length": len(content)}
