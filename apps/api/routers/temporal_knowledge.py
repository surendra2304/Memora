"""
Memora/apps/api/routers/temporal_knowledge.py — Memora Temporal Knowledge Engine.

Extends Memora's capabilities with:
1. Time-Aware Memory: store and retrieve memories with temporal context
2. Event Timelines: build timelines of events from memories
3. Causal Chains: identify and trace causal relationships
4. Temporal Reasoning: reason about events in time
5. Memory Evolution: track how memories change over time
6. Future Prediction: predict future states from temporal patterns
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

router = APIRouter()


@dataclass
class TemporalEvent:
    """An event with temporal context."""
    event_id: str
    description: str
    timestamp: float
    start_time: float | None = None
    end_time: float | None = None
    location: str = ""
    participants: list[str] = field(default_factory=list)
    cause_id: str = ""
    effect_id: str = ""
    importance: float = 0.5
    confidence: float = 0.5
    tags: list[str] = field(default_factory=list)


@dataclass
class Timeline:
    """A timeline of events."""
    timeline_id: str
    name: str
    events: list[TemporalEvent] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    category: str = ""


@dataclass
class CausalChain:
    """A chain of causal relationships."""
    chain_id: str
    description: str
    events: list[TemporalEvent] = field(default_factory=list)
    root_cause: str = ""
    final_effect: str = ""
    strength: float = 0.0
    confidence: float = 0.0


@dataclass
class MemoryEvolution:
    """Evolution of a memory over time."""
    memory_id: str
    versions: list[dict] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)
    current_version: int = 0
    evolution_score: float = 0.0


@dataclass
class FuturePrediction:
    """A prediction about the future."""
    prediction_id: str
    statement: str
    based_on: list[str]
    confidence: float
    time_horizon: float  # seconds
    category: str = ""
    evidence: list[str] = field(default_factory=list)


class TemporalKnowledge:
    """Temporal knowledge engine."""

    def __init__(self):
        self._events: dict[str, TemporalEvent] = {}
        self._timelines: dict[str, Timeline] = {}
        self._causal_chains: dict[str, CausalChain] = {}
        self._evolutions: dict[str, MemoryEvolution] = {}
        self._predictions: dict[str, FuturePrediction] = {}

    # ── Time-Aware Memory ───────────────────────────────────────────────────

    def store_event(
        self,
        description: str,
        timestamp: float,
        location: str = "",
        participants: list[str] | None = None,
        importance: float = 0.5,
        confidence: float = 0.5,
        tags: list[str] | None = None,
    ) -> TemporalEvent:
        """Store a temporal event."""
        event_id = hashlib.md5(
            f"{description}:{timestamp}:{time.time()}".encode()
        ).hexdigest()[:12]

        event = TemporalEvent(
            event_id=event_id,
            description=description,
            timestamp=timestamp,
            location=location,
            participants=participants or [],
            importance=importance,
            confidence=confidence,
            tags=tags or [],
        )

        self._events[event_id] = event
        return event

    def query_events(
        self,
        start_time: float | None = None,
        end_time: float | None = None,
        location: str = "",
        tag: str = "",
        limit: int = 20,
    ) -> list[TemporalEvent]:
        """Query events by temporal constraints."""
        results = []

        for event in self._events.values():
            # Time filter
            if start_time and event.timestamp < start_time:
                continue
            if end_time and event.timestamp > end_time:
                continue

            # Location filter
            if location and location.lower() not in event.location.lower():
                continue

            # Tag filter
            if tag and tag not in event.tags:
                continue

            results.append(event)

        # Sort by time
        results.sort(key=lambda e: e.timestamp)
        return results[:limit]

    # ── Event Timelines ─────────────────────────────────────────────────────

    def build_timeline(
        self,
        name: str,
        event_ids: list[str],
        category: str = "",
    ) -> Timeline:
        """Build a timeline from events."""
        timeline_id = f"timeline_{len(self._timelines) + 1}"

        events = []
        for eid in event_ids:
            event = self._events.get(eid)
            if event:
                events.append(event)

        # Sort by time
        events.sort(key=lambda e: e.timestamp)

        timeline = Timeline(
            timeline_id=timeline_id,
            name=name,
            events=events,
            start_time=events[0].timestamp if events else 0.0,
            end_time=events[-1].timestamp if events else 0.0,
            category=category,
        )

        self._timelines[timeline_id] = timeline
        return timeline

    def get_timeline_summary(self, timeline_id: str) -> dict[str, Any]:
        """Get a timeline summary."""
        timeline = self._timelines.get(timeline_id)
        if not timeline:
            return {"status": "NOT_FOUND"}

        return {
            "name": timeline.name,
            "category": timeline.category,
            "total_events": len(timeline.events),
            "duration": timeline.end_time - timeline.start_time if timeline.start_time else 0,
            "key_moments": [
                e.description for e in timeline.events
                if e.importance > 0.7
            ][:5],
        }

    # ── Causal Chains ───────────────────────────────────────────────────────

    def build_causal_chain(
        self,
        description: str,
        event_ids: list[str],
    ) -> CausalChain:
        """Build a causal chain from events."""
        chain_id = f"chain_{len(self._causal_chains) + 1}"

        events = []
        for eid in event_ids:
            event = self._events.get(eid)
            if event:
                events.append(event)

        # Sort by time
        events.sort(key=lambda e: e.timestamp)

        # Determine strength
        strength = 0.0
        if len(events) > 1:
            # Count direct cause-effect links
            links = 0
            for i in range(len(events) - 1):
                if events[i].effect_id == events[i+1].cause_id or \
                   events[i+1].cause_id == events[i].event_id:
                    links += 1
            strength = links / (len(events) - 1)

        chain = CausalChain(
            chain_id=chain_id,
            description=description,
            events=events,
            root_cause=events[0].description if events else "",
            final_effect=events[-1].description if events else "",
            strength=strength,
            confidence=sum(e.confidence for e in events) / max(1, len(events)),
        )

        self._causal_chains[chain_id] = chain
        return chain

    def set_causation(
        self,
        cause_id: str,
        effect_id: str,
    ) -> bool:
        """Set a causal relationship between two events."""
        cause = self._events.get(cause_id)
        effect = self._events.get(effect_id)

        if cause and effect:
            cause.effect_id = effect_id
            effect.cause_id = cause_id
            return True

        return False

    # ── Temporal Reasoning ──────────────────────────────────────────────────

    def reason_temporally(
        self,
        query: str,
        events: list[TemporalEvent],
    ) -> dict[str, Any]:
        """Reason about events in time."""
        if not events:
            return {"answer": "No events available", "confidence": 0.0}

        # Sort by time
        sorted_events = sorted(events, key=lambda e: e.timestamp)

        # Analyze query
        query_lower = query.lower()

        # Check for "before" queries
        if "before" in query_lower:
            # Find event before a certain time
            for i, event in enumerate(sorted_events):
                if event.description.lower() in query_lower:
                    if i > 0:
                        return {
                            "answer": f"{sorted_events[i-1].description} happened before {event.description}",
                            "confidence": sorted_events[i-1].confidence,
                        }

        # Check for "after" queries
        if "after" in query_lower:
            for i, event in enumerate(sorted_events):
                if event.description.lower() in query_lower:
                    if i < len(sorted_events) - 1:
                        return {
                            "answer": f"{sorted_events[i+1].description} happened after {event.description}",
                            "confidence": sorted_events[i+1].confidence,
                        }

        # Check for "cause" queries
        if "cause" in query_lower or "because" in query_lower:
            for event in sorted_events:
                if event.cause_id and self._events.get(event.cause_id):
                    cause = self._events[event.cause_id]
                    return {
                        "answer": f"{cause.description} caused {event.description}",
                        "confidence": event.confidence,
                    }

        # Default: return timeline summary
        return {
            "answer": f"Timeline from {sorted_events[0].timestamp} to {sorted_events[-1].timestamp} with {len(sorted_events)} events",
            "confidence": sum(e.confidence for e in sorted_events) / len(sorted_events),
        }

    # ── Memory Evolution ────────────────────────────────────────────────────

    def track_evolution(
        self,
        memory_id: str,
        version: dict,
    ) -> MemoryEvolution:
        """Track the evolution of a memory."""
        if memory_id not in self._evolutions:
            self._evolutions[memory_id] = MemoryEvolution(
                memory_id=memory_id,
                versions=[],
                changes=[],
            )

        evolution = self._evolutions[memory_id]
        evolution.versions.append(version)
        evolution.current_version = len(evolution.versions)

        # Calculate changes from previous version
        if len(evolution.versions) > 1:
            prev = evolution.versions[-2]
            current = version

            changes = []
            for key in current:
                if key in prev and prev[key] != current[key]:
                    changes.append(f"{key}: {prev[key]} -> {current[key]}")

            evolution.changes.extend(changes)

        # Calculate evolution score
        evolution.evolution_score = len(evolution.changes) / max(1, evolution.current_version)

        return evolution

    def get_evolution_history(self, memory_id: str) -> dict[str, Any]:
        """Get the evolution history of a memory."""
        evolution = self._evolutions.get(memory_id)
        if not evolution:
            return {"status": "NOT_FOUND"}

        return {
            "memory_id": memory_id,
            "current_version": evolution.current_version,
            "total_changes": len(evolution.changes),
            "evolution_score": evolution.evolution_score,
            "changes": evolution.changes[:20],
            "versions": len(evolution.versions),
        }

    # ── Future Prediction ───────────────────────────────────────────────────

    def predict_future(
        self,
        statement: str,
        based_on_event_ids: list[str],
        time_horizon: float = 86400.0,
        category: str = "",
    ) -> FuturePrediction:
        """Predict the future based on temporal patterns."""
        prediction_id = hashlib.md5(
            f"{statement}:{time.time()}".encode()
        ).hexdigest()[:12]

        # Collect evidence
        evidence = []
        confidence = 0.3

        for eid in based_on_event_ids:
            event = self._events.get(eid)
            if event:
                evidence.append(event.description)
                confidence = min(0.9, confidence + event.confidence * 0.1)

        # Determine category
        if not category:
            if any(word in statement.lower() for word in ["increase", "growth", "rise"]):
                category = "growth"
            elif any(word in statement.lower() for word in ["decrease", "decline", "fall"]):
                category = "decline"
            else:
                category = "general"

        prediction = FuturePrediction(
            prediction_id=prediction_id,
            statement=statement,
            based_on=based_on_event_ids,
            confidence=confidence,
            time_horizon=time_horizon,
            category=category,
            evidence=evidence,
        )

        self._predictions[prediction_id] = prediction
        return prediction

    def get_predictions(
        self,
        category: str | None = None,
        limit: int = 10,
    ) -> list[FuturePrediction]:
        """Get predictions."""
        predictions = list(self._predictions.values())

        if category:
            predictions = [p for p in predictions if p.category == category]

        predictions.sort(key=lambda p: -p.confidence)
        return predictions[:limit]

    # ── Reporting ──────────────────────────────────────────────────────────

    def get_temporal_report(self) -> dict[str, Any]:
        """Generate a temporal knowledge report."""
        return {
            "total_events": len(self._events),
            "total_timelines": len(self._timelines),
            "total_causal_chains": len(self._causal_chains),
            "total_evolutions": len(self._evolutions),
            "total_predictions": len(self._predictions),
            "avg_event_confidence": sum(e.confidence for e in self._events.values()) / max(1, len(self._events)),
        }


# Module-level singleton
temporal_knowledge = TemporalKnowledge()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/knowledge/temporal/events")
async def store_event(payload: dict):
    """Store a temporal event."""
    event = temporal_knowledge.store_event(
        description=payload["description"],
        timestamp=payload["timestamp"],
        location=payload.get("location", ""),
        participants=payload.get("participants", []),
        importance=payload.get("importance", 0.5),
        confidence=payload.get("confidence", 0.5),
        tags=payload.get("tags", []),
    )
    return {"event_id": event.event_id, "description": event.description}


@router.get("/api/v1/knowledge/temporal/events")
async def query_events(
    start: float | None = None,
    end: float | None = None,
    location: str = "",
    tag: str = "",
    limit: int = 20,
):
    """Query temporal events."""
    events = temporal_knowledge.query_events(
        start_time=start,
        end_time=end,
        location=location,
        tag=tag,
        limit=limit,
    )
    return [
        {"event_id": e.event_id, "description": e.description, "timestamp": e.timestamp}
        for e in events
    ]


@router.post("/api/v1/knowledge/temporal/timeline")
async def build_timeline(payload: dict):
    """Build a timeline."""
    timeline = temporal_knowledge.build_timeline(
        name=payload["name"],
        event_ids=payload["event_ids"],
        category=payload.get("category", ""),
    )
    return {"timeline_id": timeline.timeline_id, "events": len(timeline.events)}


@router.post("/api/v1/knowledge/temporal/causal-chain")
async def build_causal_chain(payload: dict):
    """Build a causal chain."""
    chain = temporal_knowledge.build_causal_chain(
        description=payload["description"],
        event_ids=payload["event_ids"],
    )
    return {
        "chain_id": chain.chain_id,
        "strength": chain.strength,
        "confidence": chain.confidence,
    }


@router.post("/api/v1/knowledge/temporal/causation")
async def set_causation(payload: dict):
    """Set a causal relationship."""
    success = temporal_knowledge.set_causation(
        payload["cause_id"],
        payload["effect_id"],
    )
    return {"success": success}


@router.post("/api/v1/knowledge/temporal/reason")
async def reason_temporally(payload: dict):
    """Reason about events in time."""
    events = [
        temporal_knowledge._events[eid]
        for eid in payload.get("event_ids", [])
        if eid in temporal_knowledge._events
    ]
    return temporal_knowledge.reason_temporally(payload["query"], events)


@router.post("/api/v1/knowledge/temporal/evolution")
async def track_evolution(payload: dict):
    """Track memory evolution."""
    evolution = temporal_knowledge.track_evolution(
        memory_id=payload["memory_id"],
        version=payload["version"],
    )
    return {
        "memory_id": evolution.memory_id,
        "current_version": evolution.current_version,
        "changes": evolution.changes[-5:],
    }


@router.post("/api/v1/knowledge/temporal/predict")
async def predict_future(payload: dict):
    """Predict the future."""
    prediction = temporal_knowledge.predict_future(
        statement=payload["statement"],
        based_on_event_ids=payload.get("based_on", []),
        time_horizon=payload.get("time_horizon", 86400.0),
        category=payload.get("category", ""),
    )
    return prediction.__dict__


@router.get("/api/v1/knowledge/temporal/report")
async def temporal_report():
    """Get temporal knowledge report."""
    return temporal_knowledge.get_temporal_report()
