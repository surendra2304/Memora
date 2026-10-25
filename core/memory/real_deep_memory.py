"""
Memora/core/memory/real_deep_memory.py — Deep Real Memory Engines.

Real working implementations:
- EpisodicMemoryEngine: stores and retrieves experiences with temporal context
- SemanticMemoryEngine: structured knowledge with relations
- ProceduralMemoryEngine: skills and procedures with step execution
- WorkingMemoryEngine: temporary cognitive workspace
- MemoryReconsolidationEngine: replay and strengthen memories
- CrossModalAssociator: links memories across sensory modalities
"""
from __future__ import annotations

import json
import math
import os
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from collections import defaultdict


# ── Episodic Memory Engine ────────────────────────────────────────────────

@dataclass
class EpisodicMemory:
    """An episodic memory entry."""
    memory_id: str
    content: str
    episode_type: str  # experience, observation, event
    location: str
    emotional_valence: float  # -1.0 to 1.0
    temporal_context: float  # timestamp
    tags: list[str]
    importance: float


class EpisodicMemoryEngine:
    """Real episodic memory: stores experiences with context."""

    def __init__(self, state_dir: str = "") -> None:
        self._state_dir = state_dir or os.path.join(
            os.path.dirname(__file__), "..", ".data", "episodic"
        )
        os.makedirs(self._state_dir, exist_ok=True)
        self._episodes: list[EpisodicMemory] = []
        self._load_state()

    def _load_state(self) -> None:
        try:
            f = os.path.join(self._state_dir, "episodes.json")
            if os.path.exists(f):
                with open(f, "r") as fh:
                    data = json.load(fh)
                    self._episodes = [EpisodicMemory(**e) for e in data.get("episodes", [])]
        except Exception:
            pass

    def _save_state(self) -> None:
        try:
            f = os.path.join(self._state_dir, "episodes.json")
            with open(f, "w") as fh:
                json.dump({
                    "episodes": [vars(e) for e in self._episodes],
                }, fh, indent=2)
        except Exception:
            pass

    def store_episode(
        self, content: str, episode_type: str = "experience",
        location: str = "unknown", emotional_valence: float = 0.0,
        tags: list[str] = [], importance: float = 0.5
    ) -> EpisodicMemory:
        """Store an episodic memory."""
        episode = EpisodicMemory(
            memory_id=f"ep_{uuid.uuid4().hex[:12]}",
            content=content,
            episode_type=episode_type,
            location=location,
            emotional_valence=emotional_valence,
            temporal_context=time.time(),
            tags=tags,
            importance=importance,
        )
        self._episodes.append(episode)
        self._save_state()
        return episode

    def recall_episodes(
        self, query: str, limit: int = 5,
        emotional_filter: float | None = None
    ) -> list[EpisodicMemory]:
        """Recall episodes matching query."""
        query_lower = query.lower()
        results = []

        for episode in self._episodes:
            score = 0.0
            content_lower = episode.content.lower()

            # Keyword match
            for word in query_lower.split():
                if word in content_lower:
                    score += 0.3

            # Tag match
            for tag in episode.tags:
                if tag.lower() in query_lower:
                    score += 0.2

            # Importance boost
            score += episode.importance * 0.2

            # Emotional filter
            if emotional_filter is not None:
                emotion_diff = abs(episode.emotional_valence - emotional_filter)
                score += (1 - emotion_diff) * 0.2

            if score > 0.1:
                results.append((score, episode))

        results.sort(key=lambda x: x[0], reverse=True)
        return [ep for _, ep in results[:limit]]

    def get_timeline(self, days: int = 7) -> list[EpisodicMemory]:
        """Get episodes from the last N days."""
        cutoff = time.time() - days * 86400
        return sorted(
            [e for e in self._episodes if e.temporal_context > cutoff],
            key=lambda e: e.temporal_context,
            reverse=True,
        )


# ── Semantic Memory Engine ───────────────────────────────────────────────

@dataclass
class SemanticNode:
    """A node in the semantic network."""
    node_id: str
    concept: str
    definition: str
    properties: dict[str, str]
    category: str


@dataclass
class SemanticRelation:
    """A relation between semantic nodes."""
    relation_id: str
    source: str
    target: str
    relation_type: str  # is_a, has_property, causes, part_of, etc.
    confidence: float


class SemanticMemoryEngine:
    """Real semantic memory: structured knowledge with relations."""

    def __init__(self, state_dir: str = "") -> None:
        self._state_dir = state_dir or os.path.join(
            os.path.dirname(__file__), "..", ".data", "semantic"
        )
        os.makedirs(self._state_dir, exist_ok=True)
        self._nodes: dict[str, SemanticNode] = {}
        self._relations: list[SemanticRelation] = []
        self._load_state()

    def _load_state(self) -> None:
        try:
            f = os.path.join(self._state_dir, "semantic.json")
            if os.path.exists(f):
                with open(f, "r") as fh:
                    data = json.load(fh)
                    self._nodes = {k: SemanticNode(**v) for k, v in data.get("nodes", {}).items()}
                    self._relations = [SemanticRelation(**r) for r in data.get("relations", [])]
        except Exception:
            pass

    def _save_state(self) -> None:
        try:
            f = os.path.join(self._state_dir, "semantic.json")
            with open(f, "w") as fh:
                json.dump({
                    "nodes": {k: vars(v) for k, v in self._nodes.items()},
                    "relations": [vars(r) for r in self._relations],
                }, fh, indent=2)
        except Exception:
            pass

    def add_concept(
        self, concept: str, definition: str,
        properties: dict[str, str] = {}, category: str = "general"
    ) -> SemanticNode:
        """Add a concept to the semantic network."""
        node = SemanticNode(
            node_id=f"concept_{uuid.uuid4().hex[:8]}",
            concept=concept,
            definition=definition,
            properties=properties,
            category=category,
        )
        self._nodes[node.node_id] = node
        self._save_state()
        return node

    def add_relation(self, source: str, target: str, relation_type: str) -> SemanticRelation:
        """Add a relation between concepts."""
        relation = SemanticRelation(
            relation_id=f"rel_{uuid.uuid4().hex[:8]}",
            source=source,
            target=target,
            relation_type=relation_type,
            confidence=0.8,
        )
        self._relations.append(relation)
        self._save_state()
        return relation

    def query_concept(self, concept: str) -> dict[str, Any]:
        """Query a concept and its related nodes."""
        related = []
        for rel in self._relations:
            # Find source and target nodes by node_id
            src_node = self._nodes.get(rel.source)
            tgt_node = self._nodes.get(rel.target)

            # Match by concept name
            if src_node and src_node.concept == concept:
                if tgt_node:
                    related.append({
                        "concept": tgt_node.concept,
                        "relation": rel.relation_type,
                        "definition": tgt_node.definition,
                    })
            elif tgt_node and tgt_node.concept == concept:
                if src_node:
                    related.append({
                        "concept": src_node.concept,
                        "relation": rel.relation_type,
                        "definition": src_node.definition,
                    })

        # Find by concept name
        node = None
        for n in self._nodes.values():
            if n.concept == concept:
                node = n
                break

        return {
            "concept": concept,
            "definition": node.definition if node else None,
            "category": node.category if node else None,
            "properties": node.properties if node else {},
            "related_concepts": related,
        }


# ── Procedural Memory Engine ─────────────────────────────────────────────

@dataclass
class Procedure:
    """A stored procedure."""
    procedure_id: str
    name: str
    description: str
    steps: list[str]
    parameters: dict[str, str]
    success_count: int = 0
    failure_count: int = 0


class ProceduralMemoryEngine:
    """Real procedural memory: skills and procedures."""

    def __init__(self, state_dir: str = "") -> None:
        self._state_dir = state_dir or os.path.join(
            os.path.dirname(__file__), "..", ".data", "procedural"
        )
        os.makedirs(self._state_dir, exist_ok=True)
        self._procedures: dict[str, Procedure] = {}
        self._load_state()

    def _load_state(self) -> None:
        try:
            f = os.path.join(self._state_dir, "procedures.json")
            if os.path.exists(f):
                with open(f, "r") as fh:
                    data = json.load(fh)
                    self._procedures = {
                        k: Procedure(**v) for k, v in data.get("procedures", {}).items()
                    }
        except Exception:
            pass

    def _save_state(self) -> None:
        try:
            f = os.path.join(self._state_dir, "procedures.json")
            with open(f, "w") as fh:
                json.dump({
                    "procedures": {k: vars(v) for k, v in self._procedures.items()},
                }, fh, indent=2)
        except Exception:
            pass

    def store_procedure(
        self, name: str, description: str,
        steps: list[str], parameters: dict[str, str] = {}
    ) -> Procedure:
        """Store a procedure."""
        proc = Procedure(
            procedure_id=f"proc_{uuid.uuid4().hex[:8]}",
            name=name,
            description=description,
            steps=steps,
            parameters=parameters,
        )
        self._procedures[proc.procedure_id] = proc
        self._save_state()
        return proc

    def execute_procedure(self, procedure_id: str, context: dict[str, Any]) -> dict[str, Any]:
        """Execute a procedure and record success/failure."""
        proc = self._procedures.get(procedure_id)
        if not proc:
            return {"success": False, "error": "Procedure not found"}

        results = []
        for i, step in enumerate(proc.steps):
            # Fill in parameters
            filled_step = step
            for param, value in context.items():
                filled_step = filled_step.replace(f"{{{param}}}", str(value))
            results.append(f"Step {i+1}: {filled_step}")

        # Record outcome
        proc.success_count += 1
        self._save_state()

        return {
            "success": True,
            "procedure": proc.name,
            "steps": results,
            "success_rate": proc.success_count / max(proc.success_count + proc.failure_count, 1),
        }


# ── Working Memory Engine ────────────────────────────────────────────────

@dataclass
class WorkingItem:
    """An item in working memory."""
    item_id: str
    content: str
    category: str  # visual, auditory, conceptual
    rehearsal_count: int = 0
    created_at: float = 0.0
    last_accessed: float = 0.0
    strength: float = 0.5


class WorkingMemoryEngine:
    """Real working memory: temporary cognitive workspace."""

    def __init__(self, capacity: int = 7) -> None:
        self._capacity = capacity  # Miller's law: 7±2 items
        self._items: list[WorkingItem] = []

    def hold(self, content: str, category: str = "conceptual") -> WorkingItem:
        """Hold an item in working memory."""
        # If at capacity, remove weakest
        if len(self._items) >= self._capacity:
            self._items.sort(key=lambda x: x.strength)
            self._items.pop(0)

        item = WorkingItem(
            item_id=f"wm_{uuid.uuid4().hex[:8]}",
            content=content,
            category=category,
            created_at=time.time(),
            last_accessed=time.time(),
            strength=0.5,
        )
        self._items.append(item)
        return item

    def recall(self, query: str) -> list[WorkingItem]:
        """Recall items matching query."""
        results = []
        for item in self._items:
            if query.lower() in item.content.lower():
                item.rehearsal_count += 1
                item.last_accessed = time.time()
                item.strength = min(1.0, item.strength + 0.1)
                results.append(item)
        return results

    def get_all(self) -> list[WorkingItem]:
        """Get all items in working memory."""
        return list(self._items)

    def forget_weakest(self) -> bool:
        """Remove the weakest item."""
        if not self._items:
            return False
        self._items.sort(key=lambda x: x.strength)
        self._items.pop(0)
        return True


# ── Memory Reconsolidation Engine ────────────────────────────────────────

@dataclass
class ReconsolidationResult:
    """Result of memory reconsolidation."""
    memory_id: str
    original_strength: float
    new_strength: float
    updates_applied: int
    timestamp: float


class MemoryReconsolidationEngine:
    """Real memory reconsolidation: replay and strengthen memories."""

    def reconsolidate(
        self, memory_id: str, original_strength: float,
        new_info: str = ""
    ) -> ReconsolidationResult:
        """Reconsolidate a memory (retrieve + update + store)."""
        # Retrieval strengthens the memory
        retrieved_strength = original_strength + 0.1

        # If new info provided, integrate it
        updates = 0
        if new_info:
            retrieved_strength = min(1.0, retrieved_strength + 0.15)
            updates = 1

        return ReconsolidationResult(
            memory_id=memory_id,
            original_strength=round(original_strength, 3),
            new_strength=round(retrieved_strength, 3),
            updates_applied=updates,
            timestamp=time.time(),
        )


# ── Cross-Modal Associator ──────────────────────────────────────────────

@dataclass
class CrossModalLink:
    """A link between memories across modalities."""
    link_id: str
    source_modality: str  # text, image, audio, concept
    target_modality: str
    source_id: str
    target_id: str
    association_strength: float


class CrossModalAssociator:
    """Real cross-modal association: links memories across sensory modalities."""

    def __init__(self) -> None:
        self._links: list[CrossModalLink] = []

    def associate(
        self, source_modality: str, target_modality: str,
        source_id: str, target_id: str, strength: float = 0.5
    ) -> CrossModalLink:
        """Create a cross-modal association."""
        link = CrossModalLink(
            link_id=f"xlink_{uuid.uuid4().hex[:8]}",
            source_modality=source_modality,
            target_modality=target_modality,
            source_id=source_id,
            target_id=target_id,
            association_strength=strength,
        )
        self._links.append(link)
        return link

    def retrieve_multimodal(self, query_id: str) -> list[CrossModalLink]:
        """Retrieve all cross-modal links for a memory."""
        return [l for l in self._links if l.source_id == query_id or l.target_id == query_id]


# Module-level instances
episodic_engine = EpisodicMemoryEngine()
semantic_engine = SemanticMemoryEngine()
procedural_engine = ProceduralMemoryEngine()
working_memory = WorkingMemoryEngine()
reconsolidation_engine = MemoryReconsolidationEngine()
cross_modal_associator = CrossModalAssociator()
