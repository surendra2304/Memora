"""
Memora/apps/api/routers/multimodal_memory.py — Memora Multi-Modal Memory Engine.

Extends Memora's capabilities with:
1. Multi-Modal Storage: store and retrieve text, images, audio, video
2. Cross-Modal Search: search across all modalities
3. Modality Fusion: combine information from multiple modalities
4. Temporal Memory: track how memories evolve over time
5. Emotional Memory: store and retrieve memories with emotional context
6. Spatial Memory: organize memories by spatial context
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

router = APIRouter()


@dataclass
class MultiModalMemory:
    """A multi-modal memory entry."""
    memory_id: str
    namespace: str
    scope: str
    scope_id: str
    modalities: dict[str, Any]  # text, image, audio, video
    fused_representation: str = ""
    emotion: str = "neutral"
    emotional_intensity: float = 0.0
    spatial_context: dict[str, float] = field(default_factory=dict)
    temporal_context: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5
    created_at: float = 0.0
    last_accessed: float = 0.0
    access_count: int = 0
    tags: list[str] = field(default_factory=list)


@dataclass
class CrossModalQuery:
    """A cross-modal search query."""
    query: str
    modalities: list[str] = field(default_factory=lambda: ["text"])
    similarity_threshold: float = 0.1
    limit: int = 10


@dataclass
class FusionResult:
    """Result of fusing multiple modalities."""
    memory_id: str
    fused_text: str
    modalities_fused: list[str]
    fusion_score: float
    confidence: float


@dataclass
class TemporalSnapshot:
    """A temporal snapshot of memories."""
    namespace: str
    timestamp: float
    memory_count: int
    modality_distribution: dict[str, int]
    emotion_distribution: dict[str, int]
    importance_avg: float


class MultiModalMemoryEngine:
    """Multi-modal memory engine."""

    MODALITIES = ["text", "image", "audio", "video", "structured"]

    def __init__(self):
        self._memories: dict[str, MultiModalMemory] = {}
        self._temporal_snapshots: list[TemporalSnapshot] = []

    # ── Multi-Modal Storage ────────────────────────────────────────────────

    def store_memory(
        self,
        namespace: str,
        scope: str,
        scope_id: str,
        modalities: dict[str, Any],
        emotion: str = "neutral",
        emotional_intensity: float = 0.0,
        spatial_context: dict[str, float] | None = None,
        tags: list[str] | None = None,
        importance: float = 0.5,
    ) -> MultiModalMemory:
        """Store a multi-modal memory."""
        memory_id = hashlib.md5(
            f"{namespace}:{scope}:{scope_id}:{time.time()}".encode()
        ).hexdigest()[:12]

        memory = MultiModalMemory(
            memory_id=memory_id,
            namespace=namespace,
            scope=scope,
            scope_id=scope_id,
            modalities=modalities,
            emotion=emotion,
            emotional_intensity=emotional_intensity,
            spatial_context=spatial_context or {},
            temporal_context={
                "created_at": time.time(),
                "time_of_day": time.localtime().tm_hour,
                "day_of_week": time.localtime().tm_wday,
            },
            importance=importance,
            created_at=time.time(),
            last_accessed=time.time(),
            tags=tags or [],
        )

        # Generate fused representation
        memory.fused_representation = self._fuse_modalities(modalities)

        self._memories[memory_id] = memory

        # Take temporal snapshot
        self._snapshot()

        return memory

    def _fuse_modalities(self, modalities: dict[str, Any]) -> str:
        """Fuse multiple modalities into a single representation."""
        parts = []

        if "text" in modalities:
            parts.append(str(modalities["text"])[:200])

        if "image" in modalities:
            desc = modalities["image"]
            if isinstance(desc, dict):
                parts.append(f"[Image: {desc.get('description', 'no description')}]")
            else:
                parts.append(f"[Image: {desc}]")

        if "audio" in modalities:
            desc = modalities["audio"]
            if isinstance(desc, dict):
                parts.append(f"[Audio: {desc.get('description', 'no description')}]")
            else:
                parts.append(f"[Audio: {desc}]")

        if "video" in modalities:
            desc = modalities["video"]
            if isinstance(desc, dict):
                parts.append(f"[Video: {desc.get('description', 'no description')}]")
            else:
                parts.append(f"[Video: {desc}]")

        return " ".join(parts)

    # ── Cross-Modal Search ─────────────────────────────────────────────────

    def cross_modal_search(
        self,
        query: str,
        modalities: list[str] | None = None,
        similarity_threshold: float = 0.1,
        limit: int = 10,
        emotion_filter: str | None = None,
        spatial_filter: dict[str, float] | None = None,
    ) -> list[tuple[MultiModalMemory, float]]:
        """Search across all modalities."""
        modalities = modalities or self.MODALITIES
        results = []

        for memory in self._memories.values():
            score = 0.0

            # Text matching
            if "text" in modalities and "text" in memory.modalities:
                text = str(memory.modalities["text"]).lower()
                query_lower = query.lower()
                if query_lower in text:
                    score += 0.4
                    # Also check word overlap
                    query_words = set(query_lower.split())
                    text_words = set(text.split())
                    overlap = query_words & text_words
                    if overlap:
                        score += 0.1 * len(overlap) / max(1, len(query_words))

            # Tag matching
            if query.lower() in [t.lower() for t in memory.tags]:
                score += 0.2

            # Fused representation matching
            if query.lower() in memory.fused_representation.lower():
                score += 0.1

            # Emotion filter
            if emotion_filter and memory.emotion == emotion_filter:
                score += 0.1

            # Spatial filter
            if spatial_filter:
                spatial_match = sum(
                    1 for k, v in spatial_filter.items()
                    if abs(memory.spatial_context.get(k, 0) - v) < 100
                )
                score += 0.1 * spatial_match / max(1, len(spatial_filter))

            # Recency boost
            if time.time() - memory.created_at < 3600:
                score += 0.05

            # Importance boost
            score += memory.importance * 0.1

            if score >= similarity_threshold:
                results.append((memory, score))

        results.sort(key=lambda x: -x[1])
        return results[:limit]

    # ── Modality Fusion ─────────────────────────────────────────────────────

    def fuse_memories(
        self,
        memory_ids: list[str],
    ) -> FusionResult | None:
        """Fuse multiple memories into a single representation."""
        memories = []
        for mid in memory_ids:
            mem = self._memories.get(mid)
            if mem:
                memories.append(mem)

        if not memories:
            return None

        # Combine modalities
        combined_text = " ".join(
            m.fused_representation for m in memories
        )

        # Count modalities
        fused_modalities = set()
        for mem in memories:
            fused_modalities.update(mem.modalities.keys())

        # Calculate fusion score
        total_importance = sum(m.importance for m in memories)
        fusion_score = total_importance / max(1, len(memories))

        # Confidence based on how many modalities are fused
        confidence = min(1.0, len(fused_modalities) / 4.0)

        return FusionResult(
            memory_id=hashlib.md5(
                f"fusion:{time.time()}".encode()
            ).hexdigest()[:12],
            fused_text=combined_text[:500],
            modalities_fused=list(fused_modalities),
            fusion_score=fusion_score,
            confidence=confidence,
        )

    # ── Temporal Memory ────────────────────────────────────────────────────

    def _snapshot(self) -> TemporalSnapshot:
        """Take a temporal snapshot of memories."""
        latest_by_ns: dict[str, list[MultiModalMemory]] = {}

        for memory in self._memories.values():
            if memory.namespace not in latest_by_ns:
                latest_by_ns[memory.namespace] = []
            latest_by_ns[memory.namespace].append(memory)

        snapshots = []
        for namespace, memories in latest_by_ns.items():
            modality_dist: dict[str, int] = {}
            emotion_dist: dict[str, int] = {}

            for mem in memories:
                for mod in mem.modalities:
                    modality_dist[mod] = modality_dist.get(mod, 0) + 1
                emotion_dist[mem.emotion] = emotion_dist.get(mem.emotion, 0) + 1

            snapshot = TemporalSnapshot(
                namespace=namespace,
                timestamp=time.time(),
                memory_count=len(memories),
                modality_distribution=modality_dist,
                emotion_distribution=emotion_dist,
                importance_avg=sum(m.importance for m in memories) / max(1, len(memories)),
            )
            snapshots.append(snapshot)

        self._temporal_snapshots.extend(snapshots)
        return snapshots[0] if snapshots else None

    def get_temporal_evolution(
        self,
        namespace: str,
        time_window: float = 86400.0,
    ) -> list[TemporalSnapshot]:
        """Get temporal evolution of memories."""
        cutoff = time.time() - time_window
        return [
            s for s in self._temporal_snapshots
            if s.namespace == namespace and s.timestamp >= cutoff
        ]

    # ── Emotional Memory ───────────────────────────────────────────────────

    def search_by_emotion(
        self,
        emotion: str,
        min_intensity: float = 0.0,
        limit: int = 10,
    ) -> list[MultiModalMemory]:
        """Search memories by emotional content."""
        results = []
        for memory in self._memories.values():
            if memory.emotion == emotion and memory.emotional_intensity >= min_intensity:
                results.append(memory)

        results.sort(key=lambda m: -m.emotional_intensity)
        return results[:limit]

    def get_emotional_trends(self, namespace: str) -> dict[str, float]:
        """Get emotional trends for a namespace."""
        emotions: dict[str, int] = {}
        total = 0

        for memory in self._memories.values():
            if memory.namespace == namespace:
                emotions[memory.emotion] = emotions.get(memory.emotion, 0) + 1
                total += 1

        return {
            emotion: count / max(1, total)
            for emotion, count in emotions.items()
        }

    # ── Spatial Memory ─────────────────────────────────────────────────────

    def search_by_location(
        self,
        lat: float,
        lon: float,
        radius_km: float = 10.0,
        limit: int = 10,
    ) -> list[MultiModalMemory]:
        """Search memories by spatial context."""
        results = []

        for memory in self._memories.values():
            if not memory.spatial_context:
                continue

            mem_lat = memory.spatial_context.get("lat", 0)
            mem_lon = memory.spatial_context.get("lon", 0)

            # Simple distance calculation
            dist = self._haversine(lat, lon, mem_lat, mem_lon)

            if dist <= radius_km:
                results.append((memory, dist))

        results.sort(key=lambda x: x[1])
        return [r[0] for r in results[:limit]]

    def _haversine(
        self,
        lat1: float,
        lon1: float,
        lat2: float,
        lon2: float,
    ) -> float:
        """Calculate distance between two coordinates."""
        import math

        r = 6371  # Earth radius in km

        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)

        a = (
            math.sin(dlat / 2) ** 2 +
            math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
            math.sin(dlon / 2) ** 2
        )

        return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    # ── Reporting ──────────────────────────────────────────────────────────

    def get_memory_report(self) -> dict[str, Any]:
        """Generate a multi-modal memory report."""
        modality_counts: dict[str, int] = {}
        emotion_counts: dict[str, int] = {}

        for memory in self._memories.values():
            for mod in memory.modalities:
                modality_counts[mod] = modality_counts.get(mod, 0) + 1
            emotion_counts[memory.emotion] = emotion_counts.get(memory.emotion, 0) + 1

        return {
            "total_memories": len(self._memories),
            "modality_distribution": modality_counts,
            "emotion_distribution": emotion_counts,
            "temporal_snapshots": len(self._temporal_snapshots),
            "avg_importance": (
                sum(m.importance for m in self._memories.values()) /
                max(1, len(self._memories))
            ),
        }


# Module-level singleton
multimodal_memory = MultiModalMemoryEngine()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/memory/multimodal")
async def store_multimodal_memory(payload: dict):
    """Store a multi-modal memory."""
    memory = multimodal_memory.store_memory(
        namespace=payload["namespace"],
        scope=payload.get("scope", "user"),
        scope_id=payload["scope_id"],
        modalities=payload["modalities"],
        emotion=payload.get("emotion", "neutral"),
        emotional_intensity=payload.get("emotional_intensity", 0.0),
        spatial_context=payload.get("spatial_context"),
        tags=payload.get("tags", []),
        importance=payload.get("importance", 0.5),
    )
    return {"memory_id": memory.memory_id, "fused": memory.fused_representation[:100]}


@router.get("/api/v1/memory/multimodal/search")
async def cross_modal_search(q: str, limit: int = 10):
    """Cross-modal search."""
    results = multimodal_memory.cross_modal_search(q, limit=limit)
    return [
        {
            "memory_id": m.memory_id,
            "score": score,
            "emotion": m.emotion,
            "modalities": list(m.modalities.keys()),
        }
        for m, score in results
    ]


@router.post("/api/v1/memory/multimodal/fuse")
async def fuse_memories(payload: dict):
    """Fuse multiple memories."""
    result = multimodal_memory.fuse_memories(payload["memory_ids"])
    if not result:
        raise HTTPException(404, "Memories not found")
    return result.__dict__


@router.get("/api/v1/memory/multimodal/emotion/{emotion}")
async def search_by_emotion(emotion: str, min_intensity: float = 0.0):
    """Search memories by emotion."""
    results = multimodal_memory.search_by_emotion(emotion, min_intensity)
    return [
        {"memory_id": m.memory_id, "emotion": m.emotion, "intensity": m.emotional_intensity}
        for m in results
    ]


@router.get("/api/v1/memory/multimodal/temporal/{namespace}")
async def get_temporal_evolution(namespace: str, window: float = 86400.0):
    """Get temporal evolution."""
    snapshots = multimodal_memory.get_temporal_evolution(namespace, window)
    return [s.__dict__ for s in snapshots]


@router.get("/api/v1/memory/multimodal/report")
async def memory_report():
    """Get multi-modal memory report."""
    return multimodal_memory.get_memory_report()
