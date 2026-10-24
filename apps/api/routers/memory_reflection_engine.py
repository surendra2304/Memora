"""
Memora/apps/api/routers/memory_reflection_engine.py — Memora Memory Reflection Engine.

Meta-cognitive reflection on stored memories, insight generation,
pattern recognition, and wisdom distillation.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class ReflectionInsight:
    """An insight generated from reflection."""
    insight_id: str
    topic: str
    insight: str
    confidence: float = 0.5
    source_memories: list[str] = field(default_factory=list)
    category: str = "pattern"  # pattern, lesson, prediction, connection


@dataclass
class ReflectionSession:
    """A reflection session."""
    session_id: str
    topic: str = ""
    start_time: float = field(default_factory=time.time)
    insights: list[ReflectionInsight] = field(default_factory=list)
    reflections: list[str] = field(default_factory=list)
    end_time: float = 0.0


@dataclass
class WisdomItem:
    """A distilled wisdom item."""
    wisdom_id: str
    principle: str
    description: str = ""
    source_insights: list[str] = field(default_factory=list)
    importance: float = 0.5
    created_at: float = field(default_factory=time.time)


class MemoryReflectionEngine:
    """Engine for meta-cognitive memory reflection."""

    def __init__(self) -> None:
        self._sessions: list[ReflectionSession] = []
        self._wisdom: list[WisdomItem] = {}

    def start_reflection(self, topic: str = "") -> ReflectionSession:
        """Start a reflection session."""
        session_id = f"refl_{uuid.uuid4().hex[:12]}"
        session = ReflectionSession(session_id=session_id, topic=topic)
        self._sessions.append(session)
        return session

    def generate_insight(self, session_id: str, topic: str, insight: str,
                        confidence: float = 0.5,
                        source_memories: list[str] | None = None,
                        category: str = "pattern") -> ReflectionInsight:
        """Generate an insight in a reflection session."""
        session = next((s for s in self._sessions if s.session_id == session_id), None)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        insight_item = ReflectionInsight(
            insight_id=f"ins_{uuid.uuid4().hex[:8]}",
            topic=topic, insight=insight, confidence=confidence,
            source_memories=source_memories or [], category=category,
        )
        session.insights.append(insight_item)
        return insight_item

    def distill_wisdom(self, principle: str, description: str = "",
                       importance: float = 0.5) -> str:
        """Distill wisdom from insights."""
        wisdom_id = f"wis_{uuid.uuid4().hex[:12]}"
        wisdom = WisdomItem(
            wisdom_id=wisdom_id, principle=principle,
            description=description, importance=importance,
        )
        wisdom.source_insights = [
            i.insight_id for s in self._sessions for i in s.insights
            if i.confidence > 0.7
        ]
        self._wisdom[wisdom_id] = wisdom
        return wisdom_id

    def end_reflection(self, session_id: str) -> ReflectionSession:
        """End a reflection session."""
        session = next((s for s in self._sessions if s.session_id == session_id), None)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        session.end_time = time.time()
        return session

    def get_wisdom(self) -> list[dict[str, Any]]:
        """Get distilled wisdom."""
        return [
            {"principle": w.principle, "description": w.description,
             "importance": w.importance, "source_count": len(w.source_insights)}
            for w in self._wisdom.values()
        ]

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_sessions": len(self._sessions),
            "total_wisdom": len(self._wisdom),
            "total_insights": sum(len(s.insights) for s in self._sessions),
            "engine_status": "operational",
        }


engine = MemoryReflectionEngine()


@router.post("/memora/reflection/start")
async def start_reflection(topic: str = ""):
    """Start a reflection session."""
    session = engine.start_reflection(topic)
    return {"session_id": session.session_id, "topic": session.topic}


@router.post("/memora/reflection/insights")
async def generate_insight(session_id: str, topic: str, insight: str,
                           confidence: float = 0.5,
                           source_memories: list[str] | None = None,
                           category: str = "pattern"):
    """Generate an insight."""
    insight_item = engine.generate_insight(session_id, topic, insight, confidence, source_memories, category)
    return {"insight_id": insight_item.insight_id, "confidence": insight_item.confidence}


@router.post("/memora/reflection/wisdom")
async def distill_wisdom(principle: str, description: str = "", importance: float = 0.5):
    """Distill wisdom from insights."""
    wisdom_id = engine.distill_wisdom(principle, description, importance)
    return {"wisdom_id": wisdom_id, "status": "distilled"}


@router.post("/memora/reflection/end")
async def end_reflection(session_id: str):
    """End a reflection session."""
    session = engine.end_reflection(session_id)
    return {"session_id": session.session_id, "insight_count": len(session.insights)}


@router.get("/memora/reflection/wisdom")
async def get_wisdom():
    """Get distilled wisdom."""
    return {"wisdom": engine.get_wisdom()}


@router.get("/memora/reflection/report")
async def reflection_report():
    """Get reflection engine report."""
    return engine.get_report()
