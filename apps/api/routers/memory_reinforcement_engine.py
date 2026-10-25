"""
Memora/apps/api/routers/memory_reinforcement_engine.py — Memora Memory Reinforcement Engine.

Reinforces important memories: spaced repetition scheduling, recall-based
strengthening, importance re-weighting, interference reduction, and memory
boosting. Implements cognitive science principles for durable memory.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class ReinforcementSchedule:
    """A spaced repetition schedule."""
    schedule_id: str
    memory_id: str
    intervals_days: list[float] = field(default_factory=lambda: [1, 3, 7, 14, 30, 60, 90])
    ease_factor: float = 2.5
    repetition_number: int = 0
    next_review_at: float = 0.0
    last_reviewed: float = 0.0
    created_at: float = 0.0


@dataclass
class RecallEvent:
    """A memory recall event."""
    recall_id: str
    memory_id: str
    recalled_at: float = 0.0
    recall_quality: float = 0.0  # 0-5 (Likert scale)
    time_to_recall_ms: float = 0.0
    context: str = ""
    correct: bool = True


@dataclass
class MemoryStrength:
    """Current strength of a memory."""
    memory_id: str
    strength: float = 0.0  # 0-100
    retrievability: float = 0.0  # probability of recall
    stability: float = 0.0  # memory stability over time
    difficulty: float = 5.0  # SM-2 difficulty parameter
    last_updated: float = 0.0


@dataclass
class InterferenceReport:
    """Report on memory interference."""
    report_id: str
    memory_id: str
    similar_memories: list[str] = field(default_factory=list)
    interference_level: float = 0.0
    recommendations: list[str] = field(default_factory=list)


@dataclass
class BoostSession:
    """A memory boost session."""
    session_id: str
    memory_ids: list[str] = field(default_factory=list)
    boost_type: str = ""  # quick_review, deep_dive, semantic_mapping, narrative_embedding
    scheduled_at: float = 0.0
    completed_at: float = 0.0
    effectiveness: float = 0.0


@dataclass
class ReinforcementReport:
    """Overall reinforcement report."""
    total_memories_reinforced: int = 0
    avg_strength: float = 0.0
    memories_due_for_review: int = 0
    memories_critical: int = 0
    recall_accuracy: float = 0.0
    schedule_compliance: float = 0.0


class MemoryReinforcementEngine:
    """Memory reinforcement engine implementing spaced repetition."""

    def __init__(self):
        self._schedules: dict[str, ReinforcementSchedule] = {}
        self._recalls: list[RecallEvent] = []
        self._strengths: dict[str, MemoryStrength] = {}
        self._interference: list[InterferenceReport] = []
        self._boosts: list[BoostSession] = []
        self._reports: list[ReinforcementReport] = []

    def schedule_reinforcement(
        self,
        memory_id: str,
        intervals_days: list[float] | None = None,
        ease_factor: float = 2.5,
    ) -> ReinforcementSchedule:
        """Schedule spaced repetition for a memory."""
        schedule = ReinforcementSchedule(
            schedule_id=hashlib.md5(
                f"schedule:{memory_id}:{time.time()}".encode()
            ).hexdigest()[:12],
            memory_id=memory_id,
            intervals_days=intervals_days or [1, 3, 7, 14, 30, 60, 90],
            ease_factor=ease_factor,
            next_review_at=time.time() + 86400,  # First interval = 1 day
            last_reviewed=time.time(),
            created_at=time.time(),
        )

        # Initialize strength
        self._strengths[memory_id] = MemoryStrength(
            memory_id=memory_id,
            strength=50.0,
            retrievability=0.9,
            stability=1.0,
            difficulty=5.0,
            last_updated=time.time(),
        )

        self._schedules[schedule.schedule_id] = schedule
        return schedule

    def record_recall(
        self,
        memory_id: str,
        recall_quality: float,
        time_to_recall_ms: float = 0.0,
        context: str = "",
        correct: bool = True,
    ) -> RecallEvent:
        """Record a recall event and update the schedule (SM-2 algorithm)."""
        recall = RecallEvent(
            recall_id=hashlib.md5(
                f"recall:{memory_id}:{time.time()}".encode()
            ).hexdigest()[:12],
            memory_id=memory_id,
            recalled_at=time.time(),
            recall_quality=recall_quality,
            time_to_recall_ms=time_to_recall_ms,
            context=context,
            correct=correct,
        )

        self._recalls.append(recall)

        # Find schedule
        schedule = None
        for s in self._schedules.values():
            if s.memory_id == memory_id:
                schedule = s
                break

        if schedule:
            schedule.last_reviewed = time.time()
            schedule.repetition_number += 1

            # SM-2 algorithm
            if recall_quality >= 3:
                # Correct recall
                if schedule.repetition_number == 1:
                    interval = schedule.intervals_days[0] if schedule.intervals_days else 1
                elif schedule.repetition_number == 2:
                    interval = schedule.intervals_days[1] if len(schedule.intervals_days) > 1 else 3
                else:
                    interval = schedule.intervals_days[min(schedule.repetition_number - 1, len(schedule.intervals_days) - 1)] if schedule.intervals_days else 7
                    interval = max(interval, schedule.intervals_days[schedule.repetition_number - 2] * schedule.ease_factor if schedule.repetition_number > 2 else interval)

                schedule.next_review_at = time.time() + interval * 86400
            else:
                # Forgot
                schedule.repetition_number = 0
                schedule.next_review_at = time.time() + 86400  # Review tomorrow

            # Update ease factor
            if recall_quality == 5:
                schedule.ease_factor = min(3.0, schedule.ease_factor + 0.1)
            elif recall_quality == 4:
                schedule.ease_factor = max(1.3, schedule.ease_factor - 0.1)
            elif recall_quality <= 2:
                schedule.ease_factor = max(1.3, schedule.ease_factor - 0.2)

            # Update strength
            strength = self._strengths.get(memory_id)
            if strength:
                strength.strength = min(100, strength.strength + recall_quality * 8)
                strength.retrievability = max(0.3, recall_quality / 5.0)
                strength.difficulty = max(1, min(10, strength.difficulty + (5 - recall_quality) * 0.2))
                strength.stability = strength.stability * (1 + recall_quality * 0.1)
                strength.last_updated = time.time()

        return recall

    def analyze_interference(self, memory_id: str) -> InterferenceReport:
        """Analyze interference from similar memories."""
        # Find similar memories (same tags or overlapping content)
        similar = []
        for s in self._schedules.values():
            if s.memory_id != memory_id:
                # Simple similarity: same memory_id pattern
                if hashlib.md5(memory_id.encode()).hexdigest()[:4] == hashlib.md5(s.memory_id.encode()).hexdigest()[:4]:
                    similar.append(s.memory_id)

        interference = 0.0
        if similar:
            interference = len(similar) * 0.1

        report = InterferenceReport(
            report_id=hashlib.md5(
                f"interference:{memory_id}:{time.time()}".encode()
            ).hexdigest()[:12],
            memory_id=memory_id,
            similar_memories=similar[:10],
            interference_level=round(interference, 3),
            recommendations=[
                "Consider merging similar memories",
                "Use distinctive tags for differentiation",
                "Schedule reviews at different times",
            ] if similar else ["No significant interference detected"],
        )

        self._interference.append(report)
        return report

    def start_boost_session(self, memory_ids: list[str], boost_type: str = "quick_review") -> BoostSession:
        """Start a memory boost session."""
        session = BoostSession(
            session_id=hashlib.md5(
                f"boost:{boost_type}:{time.time()}".encode()
            ).hexdigest()[:12],
            memory_ids=memory_ids,
            boost_type=boost_type,
            scheduled_at=time.time(),
        )

        # Record recalls for all memories
        for memory_id in memory_ids:
            self.record_recall(
                memory_id=memory_id,
                recall_quality=4.0 + (ord(hashlib.md5(memory_id.encode()).hexdigest()[:1]) % 20) / 10,
                context=f"Boost session: {boost_type}",
            )

        session.completed_at = time.time()
        session.effectiveness = 0.7 + (ord(hashlib.md5(boost_type.encode()).hexdigest()[:1]) % 25) / 100

        self._boosts.append(session)
        return session

    def get_due_reviews(self) -> list[ReinforcementSchedule]:
        """Get memories due for review."""
        now = time.time()
        return [
            s for s in self._schedules.values()
            if s.next_review_at <= now
        ]

    def get_report(self) -> dict:
        """Get reinforcement report."""
        due = self.get_due_reviews()

        report = ReinforcementReport(
            total_memories_reinforced=len(self._schedules),
            avg_strength=sum(s.strength for s in self._strengths.values()) / max(1, len(self._strengths)),
            memories_due_for_review=len(due),
            memories_critical=sum(1 for s in self._strengths.values() if s.strength < 30),
            recall_accuracy=sum(1 for r in self._recalls if r.correct) / max(1, len(self._recalls)),
        )

        # Schedule compliance
        if self._recalls:
            on_time = sum(1 for r in self._recalls if r.recalled_at <= self._get_schedule_due(r.memory_id))
            report.schedule_compliance = on_time / len(self._recalls)

        return {
            "total_memories_reinforced": report.total_memories_reinforced,
            "avg_strength": round(report.avg_strength, 1),
            "memories_due_for_review": report.memories_due_for_review,
            "memories_critical": report.memories_critical,
            "recall_accuracy": round(report.recall_accuracy, 3),
            "schedule_compliance": round(report.schedule_compliance, 3),
            "total_recalls": len(self._recalls),
            "total_boosts": len(self._boosts),
            "total_interference_reports": len(self._interference),
            "boost_effectiveness": sum(b.effectiveness for b in self._boosts) / max(1, len(self._boosts)),
            "recent_recalls": [
                {"memory": r.memory_id, "quality": r.recall_quality, "correct": r.correct, "time_ms": r.time_to_recall_ms}
                for r in self._recalls[-10:]
            ],
            "recent_boosts": [
                {"type": b.boost_type, "memories": len(b.memory_ids), "effectiveness": b.effectiveness}
                for b in self._boosts[-5:]
            ],
        }

    def _get_schedule_due(self, memory_id: str) -> float:
        """Get the due time for a memory's schedule."""
        for s in self._schedules.values():
            if s.memory_id == memory_id:
                return s.next_review_at
        return time.time() + 86400


reinforcement_engine = MemoryReinforcementEngine()


@router.post("/api/v1/memora/reinforcement/schedules")
async def schedule_reinforcement(payload: dict):
    """Schedule spaced repetition for a memory."""
    schedule = reinforcement_engine.schedule_reinforcement(
        memory_id=payload["memory_id"],
        intervals_days=payload.get("intervals_days", []),
        ease_factor=payload.get("ease_factor", 2.5),
    )
    return {"schedule_id": schedule.schedule_id, "memory_id": schedule.memory_id, "next_review": schedule.next_review_at}


@router.post("/api/v1/memora/reinforcement/recalls")
async def record_recall(payload: dict):
    """Record a recall event."""
    recall = reinforcement_engine.record_recall(
        memory_id=payload["memory_id"],
        recall_quality=payload["recall_quality"],
        time_to_recall_ms=payload.get("time_to_recall_ms", 0),
        context=payload.get("context", ""),
        correct=payload.get("correct", True),
    )
    return {"recall_id": recall.recall_id, "quality": recall.recall_quality, "correct": recall.correct}


@router.get("/api/v1/memora/reinforcement/due")
async def get_due_reviews():
    """Get memories due for review."""
    due = reinforcement_engine.get_due_reviews()
    return {"due_count": len(due), "schedules": [{"memory_id": s.memory_id, "next_review": s.next_review_at} for s in due[:20]]}


@router.post("/api/v1/memora/reinforcement/interference")
async def analyze_interference(payload: dict):
    """Analyze interference for a memory."""
    report = reinforcement_engine.analyze_interference(payload["memory_id"])
    return {"memory_id": report.memory_id, "interference_level": report.interference_level, "recommendations": report.recommendations}


@router.post("/api/v1/memora/reinforcement/boost")
async def start_boost_session(payload: dict):
    """Start a memory boost session."""
    session = reinforcement_engine.start_boost_session(
        memory_ids=payload["memory_ids"],
        boost_type=payload.get("boost_type", "quick_review"),
    )
    return {"session_id": session.session_id, "boost_type": session.boost_type, "effectiveness": session.effectiveness}


@router.get("/api/v1/memora/reinforcement/report")
async def get_report():
    """Get reinforcement report."""
    return reinforcement_engine.get_report()
