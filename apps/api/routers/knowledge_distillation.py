"""
Memora/apps/api/routers/knowledge_distillation.py — Memora Knowledge Distillation Engine.

Extends Memora's capabilities with:
1. Knowledge Compression: distill large knowledge bases into concise summaries
2. Hierarchical Summarization: create multi-level summaries
3. Key Insight Extraction: extract the most important insights
4. Concept Graph Construction: build hierarchical concept graphs
5. Learning Path Generation: generate personalized learning paths
6. Knowledge Retention Scoring: score how well knowledge is retained
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

router = APIRouter()


@dataclass
class SummaryLevel:
    """A level in the hierarchical summary."""
    level: int
    content: str
    key_points: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    compression_ratio: float = 0.0


@dataclass
class Insight:
    """An extracted insight."""
    insight_id: str
    statement: str
    importance: float
    confidence: float
    supporting_memories: list[str]
    category: str = ""
    novelty: float = 0.0


@dataclass
class ConceptNode:
    """A node in the concept hierarchy."""
    concept_id: str
    name: str
    level: int  # 0 = root, 1 = category, 2 = subcategory, 3 = detail
    parent_id: str = ""
    children: list[str] = field(default_factory=list)
    description: str = ""
    related_memories: list[str] = field(default_factory=list)
    importance: float = 0.0


@dataclass
class LearningPath:
    """A personalized learning path."""
    path_id: str
    user_id: str
    topics: list[str]
    steps: list[dict]
    estimated_time: float
    prerequisites: list[str]
    difficulty: float
    completion_probability: float


@dataclass
class RetentionScore:
    """Knowledge retention score."""
    memory_id: str
    recall_count: int
    last_recall: float
    retention_strength: float
    decay_rate: float
    recommendation: str


class KnowledgeDistillation:
    """Knowledge distillation engine."""

    def __init__(self):
        self._summaries: dict[str, list[SummaryLevel]] = {}
        self._insights: dict[str, Insight] = {}
        self._concept_graph: dict[str, ConceptNode] = {}
        self._learning_paths: dict[str, LearningPath] = {}
        self._retention_scores: dict[str, RetentionScore] = {}

    # ── Knowledge Compression ──────────────────────────────────────────────

    def compress_knowledge(
        self,
        namespace: str,
        memories: list[dict],
        levels: int = 3,
    ) -> list[SummaryLevel]:
        """Compress knowledge into hierarchical summaries."""
        if not memories:
            return []

        # Combine all memories
        full_text = "\n".join(
            mem.get("content", mem.get("text", "")) for mem in memories
        )

        # Create hierarchical summaries
        summary_levels = []

        for level in range(1, levels + 1):
            # Simulate different compression levels
            if level == 1:
                # Top level: very brief summary
                summary = f"Summary of {len(memories)} memories in '{namespace}'. "
                topics = set()
                for mem in memories:
                    for tag in mem.get("tags", []):
                        topics.add(tag)
                summary += f"Key topics: {', '.join(list(topics)[:5])}."
                key_points = list(topics)[:3]

            elif level == 2:
                # Mid level: more detail
                summary = "Detailed summary: "
                key_points = []
                for mem in memories[:10]:
                    text = mem.get("content", mem.get("text", ""))[:100]
                    key_points.append(text)
                summary += " ".join(key_points[:3])

            else:
                # Detailed level
                summary = "Full details: "
                key_points = [
                    mem.get("content", mem.get("text", ""))[:200]
                    for mem in memories[:5]
                ]
                summary += " ".join(key_points[:2])

            # Calculate compression ratio
            original_length = len(full_text)
            summary_length = len(summary)
            compression_ratio = summary_length / max(1, original_length)

            level_summary = SummaryLevel(
                level=level,
                content=summary,
                key_points=key_points,
                concepts=[f"concept_{i}" for i in range(min(3, level))],
                compression_ratio=compression_ratio,
            )

            summary_levels.append(level_summary)

        self._summaries[namespace] = summary_levels
        return summary_levels

    # ── Key Insight Extraction ──────────────────────────────────────────────

    def extract_insights(
        self,
        memories: list[dict],
        top_k: int = 10,
    ) -> list[Insight]:
        """Extract key insights from memories."""
        insights = []

        # Group memories by category
        categories: dict[str, list[dict]] = {}
        for mem in memories:
            category = mem.get("category", mem.get("tags", ["general"])[0] if mem.get("tags") else "general")
            categories.setdefault(category, []).append(mem)

        # Extract insights from each category
        for category, cat_memories in categories.items():
            # Find common themes
            texts = [
                mem.get("content", mem.get("text", "")) for mem in cat_memories
            ]

            # Extract most frequent meaningful words
            word_freq: dict[str, int] = {}
            for text in texts:
                for word in text.lower().split():
                    if len(word) > 4:
                        word_freq[word] = word_freq.get(word, 0) + 1

            # Top words become insights
            top_words = sorted(word_freq.items(), key=lambda x: -x[1])[:5]

            for word, freq in top_words:
                importance = min(1.0, freq / max(1, len(texts)))

                insight = Insight(
                    insight_id=hashlib.md5(
                        f"{word}:{category}:{time.time()}".encode()
                    ).hexdigest()[:12],
                    statement=f"Key concept '{word}' appears {freq} times across {len(cat_memories)} memories",
                    importance=importance,
                    confidence=importance,
                    supporting_memories=[
                        mem.get("memory_id", f"mem_{i}")
                        for i, mem in enumerate(cat_memories[:3])
                    ],
                    category=category,
                    novelty=importance * 0.5,
                )

                insights.append(insight)
                self._insights[insight.insight_id] = insight

        insights.sort(key=lambda i: -i.importance)
        return insights[:top_k]

    # ── Concept Graph Construction ───────────────────────────────────────────

    def build_concept_graph(
        self,
        insights: list[Insight],
        memories: list[dict],
    ) -> dict[str, ConceptNode]:
        """Build a hierarchical concept graph."""
        graph = {}

        # Create root
        root = ConceptNode(
            concept_id="root",
            name="Knowledge Base",
            level=0,
            description="Root of the knowledge hierarchy",
        )
        graph["root"] = root

        # Create category nodes (level 1)
        categories = set()
        for insight in insights:
            categories.add(insight.category)

        cat_nodes = {}
        for cat in categories:
            node = ConceptNode(
                concept_id=f"cat_{cat}",
                name=cat,
                level=1,
                parent_id="root",
                description=f"Category: {cat}",
            )
            graph[node.concept_id] = node
            graph["root"].children.append(node.concept_id)
            cat_nodes[cat] = node

        # Create concept nodes (level 2) from insights
        for insight in insights:
            cat_node = cat_nodes.get(insight.category)
            if not cat_node:
                continue

            concept_id = hashlib.md5(
                f"{insight.insight_id}:concept".encode()
            ).hexdigest()[:12]

            concept = ConceptNode(
                concept_id=concept_id,
                name=insight.statement[:50],
                level=2,
                parent_id=cat_node.concept_id,
                description=insight.statement,
                related_memories=insight.supporting_memories,
                importance=insight.importance,
            )

            graph[concept_id] = concept
            cat_node.children.append(concept_id)

        # Create detail nodes (level 3) from memories
        for mem in memories[:20]:
            memory_text = mem.get("content", mem.get("text", ""))[:80]
            mem_id = mem.get("memory_id", f"mem_{hashlib.md5(memory_text.encode()).hexdigest()[:8]}")

            concept_id = hashlib.md5(
                f"{mem_id}:detail".encode()
            ).hexdigest()[:12]

            detail = ConceptNode(
                concept_id=concept_id,
                name=memory_text,
                level=3,
                parent_id=cat_nodes.get(mem.get("category", "general"), ConceptNode("", "", 0)).concept_id if mem.get("category") in cat_nodes else "root",
                description=mem.get("content", mem.get("text", ""))[:200],
                related_memories=[mem_id],
                importance=mem.get("importance", 0.5),
            )

            graph[concept_id] = detail
            if detail.parent_id in graph:
                graph[detail.parent_id].children.append(concept_id)

        self._concept_graph = graph
        return graph

    # ── Learning Path Generation ───────────────────────────────────────────

    def generate_learning_path(
        self,
        user_id: str,
        target_topics: list[str],
        current_knowledge: dict[str, float],
    ) -> LearningPath:
        """Generate a personalized learning path."""
        steps = []
        total_time = 0.0

        for topic in target_topics:
            # Check current knowledge level
            current_level = current_knowledge.get(topic, 0.0)

            # Determine required steps
            if current_level < 0.3:
                # Need to start from basics
                steps.append({
                    "step_id": f"step_{len(steps)}",
                    "topic": topic,
                    "level": "introductory",
                    "description": f"Introduction to {topic}",
                    "estimated_time": 30.0,  # minutes
                    "prerequisites": [],
                })

                steps.append({
                    "step_id": f"step_{len(steps)}",
                    "topic": topic,
                    "level": "basic",
                    "description": f"Basic concepts of {topic}",
                    "estimated_time": 45.0,
                    "prerequisites": [f"step_{len(steps) - 1}"],
                })

                total_time += 75.0
            elif current_level < 0.6:
                steps.append({
                    "step_id": f"step_{len(steps)}",
                    "topic": topic,
                    "level": "intermediate",
                    "description": f"Intermediate concepts of {topic}",
                    "estimated_time": 45.0,
                    "prerequisites": [],
                })
                total_time += 45.0
            else:
                steps.append({
                    "step_id": f"step_{len(steps)}",
                    "topic": topic,
                    "level": "advanced",
                    "description": f"Advanced concepts of {topic}",
                    "estimated_time": 30.0,
                    "prerequisites": [],
                })
                total_time += 30.0

        # Calculate difficulty
        difficulty = sum(
            0.3 if s["level"] == "introductory" else
            0.6 if s["level"] == "basic" else
            0.8 if s["level"] == "intermediate" else 1.0
            for s in steps
        ) / max(1, len(steps))

        # Completion probability
        avg_current = sum(current_knowledge.values()) / max(1, len(current_knowledge))
        completion_prob = min(0.9, 0.5 + avg_current * 0.4 - difficulty * 0.2)

        path = LearningPath(
            path_id=hashlib.md5(
                f"{user_id}:{time.time()}".encode()
            ).hexdigest()[:12],
            user_id=user_id,
            topics=target_topics,
            steps=steps,
            estimated_time=total_time,
            prerequisites=[],
            difficulty=difficulty,
            completion_probability=completion_prob,
        )

        self._learning_paths[path.path_id] = path
        return path

    # ── Knowledge Retention Scoring ────────────────────────────────────────

    def score_retention(
        self,
        memory_id: str,
        recall_history: list[float],
        days_elapsed: float = 0.0,
    ) -> RetentionScore:
        """Score knowledge retention."""
        recall_count = len(recall_history)
        last_recall = recall_history[-1] if recall_history else 0.0

        # Ebbinghaus forgetting curve
        decay_rate = 0.5  # Memory halves every certain period
        retention_strength = recall_count * 0.2 + (1.0 - min(1.0, days_elapsed / 30.0)) * 0.3
        retention_strength = max(0.0, min(1.0, retention_strength))

        # Recommendation
        if retention_strength < 0.3:
            recommendation = "Review this knowledge soon - retention is low"
        elif retention_strength < 0.6:
            recommendation = "Review within a week to maintain retention"
        else:
            recommendation = "Retention is good - no immediate review needed"

        score = RetentionScore(
            memory_id=memory_id,
            recall_count=recall_count,
            last_recall=last_recall,
            retention_strength=retention_strength,
            decay_rate=decay_rate,
            recommendation=recommendation,
        )

        self._retention_scores[memory_id] = score
        return score

    # ── Reporting ──────────────────────────────────────────────────────────

    def get_distillation_report(self) -> dict[str, Any]:
        """Generate a distillation report."""
        return {
            "namespaces_compressed": len(self._summaries),
            "total_insights": len(self._insights),
            "concept_graph_size": len(self._concept_graph),
            "learning_paths_generated": len(self._learning_paths),
            "retention_scores": len(self._retention_scores),
        }


# Module-level singleton
knowledge_distillation = KnowledgeDistillation()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/memory/distill")
async def compress_knowledge(payload: dict):
    """Compress knowledge into hierarchical summaries."""
    levels = knowledge_distillation.compress_knowledge(
        namespace=payload["namespace"],
        memories=payload["memories"],
        levels=payload.get("levels", 3),
    )
    return [level.__dict__ for level in levels]


@router.post("/api/v1/memory/insights")
async def extract_insights(payload: dict):
    """Extract key insights from memories."""
    insights = knowledge_distillation.extract_insights(
        memories=payload["memories"],
        top_k=payload.get("top_k", 10),
    )
    return [insight.__dict__ for insight in insights]


@router.post("/api/v1/memory/concept-graph")
async def build_concept_graph(payload: dict):
    """Build concept hierarchy."""
    insights = [
        Insight(**i) for i in payload["insights"]
    ] if payload.get("insights") else []
    graph = knowledge_distillation.build_concept_graph(
        insights=insights,
        memories=payload["memories"],
    )
    return {
        concept_id: node.__dict__
        for concept_id, node in graph.items()
    }


@router.post("/api/v1/memory/learning-path")
async def generate_learning_path(payload: dict):
    """Generate a personalized learning path."""
    path = knowledge_distillation.generate_learning_path(
        user_id=payload["user_id"],
        target_topics=payload["target_topics"],
        current_knowledge=payload.get("current_knowledge", {}),
    )
    return path.__dict__


@router.post("/api/v1/memory/retention-score")
async def score_retention(payload: dict):
    """Score knowledge retention."""
    score = knowledge_distillation.score_retention(
        memory_id=payload["memory_id"],
        recall_history=payload.get("recall_history", []),
        days_elapsed=payload.get("days_elapsed", 0.0),
    )
    return score.__dict__


@router.get("/api/v1/memory/distillation/report")
async def distillation_report():
    """Get distillation report."""
    return knowledge_distillation.get_distillation_report()
