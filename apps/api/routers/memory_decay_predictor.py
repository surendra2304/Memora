"""
Memora/apps/api/routers/memory_decay_predictor.py — Memora Memory Decay Predictor.

Memory retention modeling, decay curve prediction, reinforcement optimization,
and memory lifecycle management.
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class MemoryRecord:
    """A memory with decay tracking."""
    memory_id: str
    content: str
    importance: float = 0.5
    created_at: float = field(default_factory=time.time)
    last_accessed: float = 0.0
    access_count: int = 0
    decay_rate: float = 0.05
    retention_score: float = 1.0


@dataclass
class DecayCurve:
    """A memory decay curve."""
    curve_id: str
    memory_id: str
    time_points: list[float] = field(default_factory=list)
    retention_points: list[float] = field(default_factory=list)
    half_life: float = 0.0
    predicted_retention: float = 0.0


@dataclass
class ReinforcementPlan:
    """A plan for memory reinforcement."""
    plan_id: str
    memory_id: str
    reinforcement_schedule: list[dict] = field(default_factory=list)
    estimated_retention_boost: float = 0.0
    priority: str = "medium"


class MemoryDecayPredictor:
    """Engine for memory decay prediction and reinforcement."""

    def __init__(self) -> None:
        self._memories: dict[str, MemoryRecord] = {}
        self._curves: list[DecayCurve] = []
        self._plans: list[ReinforcementPlan] = []

    def store_memory(self, content: str, importance: float = 0.5,
                     decay_rate: float = 0.05) -> MemoryRecord:
        """Store a memory with decay tracking."""
        memory_id = f"mem_{uuid.uuid4().hex[:12]}"
        memory = MemoryRecord(
            memory_id=memory_id, content=content, importance=importance,
            decay_rate=decay_rate * (1.0 - importance * 0.5),
        )
        memory.last_accessed = memory.created_at
        self._memories[memory_id] = memory
        return memory

    def access_memory(self, memory_id: str) -> MemoryRecord:
        """Access a memory (reinforces it)."""
        memory = self._memories.get(memory_id)
        if not memory:
            raise HTTPException(status_code=404, detail="Memory not found")
        memory.last_accessed = time.time()
        memory.access_count += 1
        memory.retention_score = min(1.0, memory.retention_score + 0.1)
        return memory

    def predict_decay(self, memory_id: str, time_horizon: float = 86400) -> DecayCurve:
        """Predict memory decay over a time horizon."""
        memory = self._memories.get(memory_id)
        if not memory:
            raise HTTPException(status_code=404, detail="Memory not found")

        curve_id = f"curve_{uuid.uuid4().hex[:12]}"
        curve = DecayCurve(curve_id=curve_id, memory_id=memory_id)

        elapsed = time.time() - memory.last_accessed
        steps = 10
        for i in range(steps + 1):
            t = (elapsed + (time_horizon / steps) * i) / time_horizon
            curve.time_points.append(t)
            # Ebbinghaus decay model
            retention = memory.retention_score * math.exp(-memory.decay_rate * t * 10)
            # Boost from access count
            retention *= (1.0 + memory.access_count * 0.05)
            curve.retention_points.append(round(retention, 4))

        # Half-life
        if memory.decay_rate > 0:
            curve.half_life = math.log(2) / memory.decay_rate / 10
        curve.predicted_retention = curve.retention_points[-1]

        self._curves.append(curve)
        return curve

    def create_reinforcement_plan(self, memory_id: str, reinforcement_count: int = 3) -> ReinforcementPlan:
        """Create a reinforcement plan for a memory."""
        memory = self._memories.get(memory_id)
        if not memory:
            raise HTTPException(status_code=404, detail="Memory not found")

        plan_id = f"plan_{uuid.uuid4().hex[:12]}"
        plan = ReinforcementPlan(plan_id=plan_id, memory_id=memory_id)

        # Schedule reinforcements at optimal intervals (Ebbinghaus)
        intervals = [60, 3600, 86400, 604800]  # 1min, 1hr, 1day, 1week
        for i, interval in enumerate(intervals[:reinforcement_count]):
            plan.reinforcement_schedule.append({
                "reinforcement_num": i + 1,
                "interval_sec": interval,
                "expected_boost": 0.15 * (interval / 86400 + 0.1),
            })

        plan.estimated_retention_boost = sum(r["expected_boost"] for r in plan.reinforcement_schedule)
        plan.priority = "high" if memory.importance > 0.7 else "medium"

        self._plans.append(plan)
        return plan

    def get_memories(self, min_retention: float = 0.0) -> list[dict[str, Any]]:
        """Get memories with retention above threshold."""
        memories = list(self._memories.values())
        if min_retention > 0:
            memories = [m for m in memories if m.retention_score >= min_retention]
        return [
            {"content": m.content[:80], "importance": m.importance,
             "retention": m.retention_score, "accesses": m.access_count}
            for m in sorted(memories, key=lambda m: m.retention_score, reverse=True)
        ]

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        avg_retention = (
            sum(m.retention_score for m in self._memories.values()) / max(1, len(self._memories))
        ) if self._memories else 0
        return {
            "total_memories": len(self._memories),
            "total_curves": len(self._curves),
            "total_plans": len(self._plans),
            "avg_retention": round(avg_retention, 2),
            "engine_status": "operational",
        }


engine = MemoryDecayPredictor()


@router.post("/memora/decay/memories")
async def store_memory(content: str, importance: float = 0.5, decay_rate: float = 0.05):
    """Store a memory with decay tracking."""
    memory = engine.store_memory(content, importance, decay_rate)
    return {"memory_id": memory.memory_id, "importance": memory.importance}


@router.post("/memora/decay/access/{memory_id}")
async def access_memory(memory_id: str):
    """Access a memory (reinforces it)."""
    memory = engine.access_memory(memory_id)
    return {"memory_id": memory.memory_id, "access_count": memory.access_count, "retention": memory.retention_score}


@router.post("/memora/decay/predict/{memory_id}")
async def predict_decay(memory_id: str, time_horizon: float = 86400):
    """Predict memory decay."""
    curve = engine.predict_decay(memory_id, time_horizon)
    return {
        "curve_id": curve.curve_id,
        "half_life": curve.half_life,
        "predicted_retention": curve.predicted_retention,
        "points": len(curve.retention_points),
    }


@router.post("/memora/decay/plans")
async def create_reinforcement_plan(memory_id: str, reinforcement_count: int = 3):
    """Create a reinforcement plan."""
    plan = engine.create_reinforcement_plan(memory_id, reinforcement_count)
    return {
        "plan_id": plan.plan_id,
        "estimated_boost": plan.estimated_retention_boost,
        "priority": plan.priority,
        "schedule_count": len(plan.reinforcement_schedule),
    }


@router.get("/memora/decay/memories")
async def get_memories(min_retention: float = 0.0):
    """Get memories."""
    return {"memories": engine.get_memories(min_retention)}


@router.get("/memora/decay/report")
async def decay_report():
    """Get decay predictor report."""
    return engine.get_report()
