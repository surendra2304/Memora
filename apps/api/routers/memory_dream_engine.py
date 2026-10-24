"""
Memora/apps/api/routers/memory_dream_engine.py — Memora Memory Dream Engine.

Creative memory association, pattern synthesis, insight generation
from latent memory combinations, and subconscious memory processing.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class DreamFragment:
    """A fragment of a dream (memory association)."""
    fragment_id: str
    source_memory: str
    associated_memory: str
    connection_type: str = "similarity"  # similarity, contrast, sequence, causal
    intensity: float = 0.5
    description: str = ""


@dataclass
class DreamSequence:
    """A sequence of dream fragments."""
    dream_id: str
    fragments: list[DreamFragment] = field(default_factory=list)
    theme: str = ""
    insight: str = ""
    emotional_tone: str = "neutral"  # positive, negative, neutral, mixed
    created_at: float = field(default_factory=time.time)


@dataclass
class DreamReport:
    """A dream processing report."""
    report_id: str
    total_dreams: int = 0
    recurring_themes: list[str] = field(default_factory=list)
    new_insights: list[str] = field(default_factory=list)
    memory_connections: int = 0
    generated_at: float = field(default_factory=time.time)


class MemoryDreamEngine:
    """Engine for creative memory association and dream processing."""

    def __init__(self) -> None:
        self._memories: dict[str, str] = {}
        self._dreams: list[DreamSequence] = []
        self._reports: list[DreamReport] = []

    def add_memory(self, memory_id: str, content: str, tags: list[str] | None = None) -> str:
        """Add a memory for dream processing."""
        self._memories[memory_id] = content
        return memory_id

    def dream(self, theme: str = "", memory_ids: list[str] | None = None) -> DreamSequence:
        """Generate a dream sequence from memory associations."""
        dream_id = f"dream_{uuid.uuid4().hex[:12]}"
        dream = DreamSequence(dream_id=dream_id, theme=theme)

        # Select memories
        candidates = memory_ids or list(self._memories.keys())[:5]
        for i in range(len(candidates) - 1):
            frag = DreamFragment(
                fragment_id=f"fr_{uuid.uuid4().hex[:8]}",
                source_memory=candidates[i],
                associated_memory=candidates[i + 1],
                connection_type="similarity",
                intensity=0.5 + (i * 0.1),
                description=f"Connecting {self._memories.get(candidates[i], 'memory')[:40]}"
                            f" with {self._memories.get(candidates[i + 1], 'memory')[:40]}",
            )
            dream.fragments.append(frag)

        dream.emotional_tone = "positive" if len(dream.fragments) > 2 else "neutral"
        dream.insight = f"Dream revealed {len(dream.fragments)} connections around theme: {theme or 'unspecified'}"

        self._dreams.append(dream)
        return dream

    def generate_insight(self, dream_id: str) -> str:
        """Generate an insight from a dream."""
        dream = next((d for d in self._dreams if d.dream_id == dream_id), None)
        if not dream:
            raise HTTPException(status_code=404, detail="Dream not found")

        if not dream.insight:
            themes = [self._memories.get(f.source_memory, "unknown")[:30] for f in dream.fragments[:3]]
            dream.insight = f"Pattern synthesis across memories: {'; '.join(themes)}"

        return dream.insight

    def get_recurring_themes(self) -> list[str]:
        """Get recurring dream themes."""
        theme_count: dict[str, int] = {}
        for dream in self._dreams:
            if dream.theme:
                theme_count[dream.theme] = theme_count.get(dream.theme, 0) + 1
        return sorted(theme_count.keys(), key=lambda t: theme_count[t], reverse=True)

    def generate_report(self) -> DreamReport:
        """Generate a dream processing report."""
        report_id = f"drep_{uuid.uuid4().hex[:12]}"
        report = DreamReport(report_id=report_id)
        report.total_dreams = len(self._dreams)
        report.recurring_themes = self.get_recurring_themes()[:5]
        report.new_insights = [d.insight for d in self._dreams if d.insight][:5]
        report.memory_connections = sum(len(d.fragments) for d in self._dreams)
        self._reports.append(report)
        return report

    def get_dreams(self, limit: int = 10) -> list[dict[str, Any]]:
        """Get dream sequences."""
        return [
            {"dream_id": d.dream_id, "theme": d.theme, "fragment_count": len(d.fragments),
             "emotional_tone": d.emotional_tone, "insight": d.insight[:80]}
            for d in self._dreams[-limit:]
        ]

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_memories": len(self._memories),
            "total_dreams": len(self._dreams),
            "total_reports": len(self._reports),
            "engine_status": "operational",
        }


engine = MemoryDreamEngine()


@router.post("/memora/dream/memories")
async def add_memory(memory_id: str, content: str, tags: list[str] | None = None):
    """Add a memory for dream processing."""
    engine.add_memory(memory_id, content, tags)
    return {"memory_id": memory_id, "status": "added"}


@router.post("/memora/dream/dream")
async def dream(theme: str = "", memory_ids: list[str] | None = None):
    """Generate a dream sequence."""
    dream = engine.dream(theme, memory_ids)
    return {"dream_id": dream.dream_id, "theme": dream.theme, "fragment_count": len(dream.fragments)}


@router.get("/memora/dream/insight/{dream_id}")
async def generate_insight(dream_id: str):
    """Generate an insight from a dream."""
    insight = engine.generate_insight(dream_id)
    return {"dream_id": dream_id, "insight": insight}


@router.get("/memora/dream/themes")
async def get_themes():
    """Get recurring dream themes."""
    return {"themes": engine.get_recurring_themes()}


@router.post("/memora/dream/report")
async def generate_report():
    """Generate dream report."""
    report = engine.generate_report()
    return {
        "report_id": report.report_id,
        "total_dreams": report.total_dreams,
        "themes": report.recurring_themes,
        "connections": report.memory_connections,
    }


@router.get("/memora/dream/dreams")
async def get_dreams(limit: int = 10):
    """Get dream sequences."""
    return {"dreams": engine.get_dreams(limit)}


@router.get("/memora/dream/report")
async def dream_report():
    """Get dream engine report."""
    return engine.get_report()
