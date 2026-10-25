"""
Memora/apps/api/routers/memory_dream_interpreter.py — Memora Memory Dream Interpreter.

Dream symbol analysis, archetype recognition, collective unconscious patterns,
and symbolic dream interpretation.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class DreamSymbol:
    """A symbol in a dream."""
    symbol_id: str
    name: str
    interpretation: str = ""
    category: str = "element"  # element, animal, person, object, abstract
    archetype: str = ""  # Jungian archetype
    emotional_valence: float = 0.0  # -1 to 1


@dataclass
class DreamInterpretation:
    """Interpretation of a dream."""
    interpretation_id: str
    dream_id: str
    symbols: list[DreamSymbol] = field(default_factory=list)
    themes: list[str] = field(default_factory=list)
    message: str = ""
    confidence: float = 0.5
    interpreted_at: float = field(default_factory=time.time)


@dataclass
class ArchetypePattern:
    """A recurring archetype pattern."""
    pattern_id: str
    archetype: str
    description: str = ""
    frequency: int = 0
    meaning: str = ""


class MemoryDreamInterpreter:
    """Engine for dream interpretation and symbol analysis."""

    def __init__(self) -> None:
        self._interpretations: list[DreamInterpretation] = []
        self._archetypes: list[ArchetypePattern] = []

    def interpret_dream(self, dream_id: str, dream_content: str) -> DreamInterpretation:
        """Interpret a dream."""
        interp_id = f"int_{uuid.uuid4().hex[:12]}"
        interp = DreamInterpretation(interpretation_id=interp_id, dream_id=dream_id)

        # Extract symbols from dream content
        symbol_map = {
            "water": ("element", "emotion", "subconscious"),
            "fire": ("element", "transformation", "passion"),
            "flying": ("abstract", "freedom", "transcendence"),
            "falling": ("abstract", "anxiety", "loss"),
            "snake": ("animal", "transformation", "shadow"),
            "wolf": ("animal", "instinct", "wildness"),
            "house": ("object", "self", "safety"),
            "road": ("abstract", "journey", "purpose"),
        }

        words = dream_content.lower()
        for keyword, (cat, archetype, meaning) in symbol_map.items():
            if keyword in words:
                symbol = DreamSymbol(
                    symbol_id=f"sym_{uuid.uuid4().hex[:8]}",
                    name=keyword,
                    interpretation=meaning,
                    category=cat,
                    archetype=archetype,
                    emotional_valence=0.5 if meaning in ("freedom", "passion", "safety") else -0.3,
                )
                interp.symbols.append(symbol)

        interp.themes = [s.archetype for s in interp.symbols[:3]]
        interp.message = (
            f"Dream contains {len(interp.symbols)} symbols. "
            f"Key theme: {interp.themes[0] if interp.themes else 'unknown'}"
        )
        interp.confidence = min(0.9, len(interp.symbols) * 0.2)

        self._interpretations.append(interp)
        return interp

    def detect_archetypes(self) -> list[ArchetypePattern]:
        """Detect recurring archetype patterns."""
        archetype_counts: dict[str, int] = {}
        for interp in self._interpretations:
            for symbol in interp.symbols:
                if symbol.archetype:
                    archetype_counts[symbol.archetype] = archetype_counts.get(symbol.archetype, 0) + 1

        self._archetypes = []
        for archetype, count in sorted(archetype_counts.items(), key=lambda x: x[1], reverse=True):
            pattern = ArchetypePattern(
                pattern_id=f"ap_{uuid.uuid4().hex[:12]}",
                archetype=archetype, frequency=count,
                description=f"Recurring archetype: {archetype}",
                meaning=f"Appears in {count} dream interpretations",
            )
            self._archetypes.append(pattern)

        return self._archetypes

    def get_interpretations(self, dream_id: str | None = None) -> list[dict[str, Any]]:
        """Get dream interpretations."""
        interps = self._interpretations
        if dream_id:
            interps = [i for i in interps if i.dream_id == dream_id]
        return [
            {"dream_id": i.dream_id, "symbol_count": len(i.symbols),
             "themes": i.themes, "message": i.message[:80], "confidence": i.confidence}
            for i in interps
        ]

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_interpretations": len(self._interpretations),
            "total_archetypes": len(self._archetypes),
            "total_symbols": sum(len(i.symbols) for i in self._interpretations),
            "engine_status": "operational",
        }


engine = MemoryDreamInterpreter()


@router.post("/memora/interpreter/dreams")
async def interpret_dream(dream_id: str, dream_content: str):
    """Interpret a dream."""
    interp = engine.interpret_dream(dream_id, dream_content)
    return {
        "interpretation_id": interp.interpretation_id,
        "symbol_count": len(interp.symbols),
        "themes": interp.themes,
        "message": interp.message[:80],
        "confidence": interp.confidence,
    }


@router.post("/memora/interpreter/archetypes")
async def detect_archetypes():
    """Detect recurring archetype patterns."""
    patterns = engine.detect_archetypes()
    return {
        "archetype_count": len(patterns),
        "archetypes": [
            {"archetype": p.archetype, "frequency": p.frequency}
            for p in patterns[:5]
        ],
    }


@router.get("/memora/interpreter/interpretations")
async def get_interpretations(dream_id: str | None = None):
    """Get dream interpretations."""
    return {"interpretations": engine.get_interpretations(dream_id)}


@router.get("/memora/interpreter/report")
async def interpreter_report():
    """Get dream interpreter report."""
    return engine.get_report()
