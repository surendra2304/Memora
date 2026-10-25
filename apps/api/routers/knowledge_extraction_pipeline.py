"""
Memora/apps/api/routers/knowledge_extraction_pipeline.py — Memora Knowledge Extraction Pipeline.

Extracts structured knowledge from memories and content: entity extraction,
relationship identification, fact extraction, topic modeling, and knowledge
graph construction with confidence scoring.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class ExtractedEntity:
    """An entity extracted from content."""
    entity_id: str
    name: str
    entity_type: str = ""  # person, organization, location, concept, event, date
    mentions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    first_seen: float = 0.0
    last_seen: float = 0.0


@dataclass
class ExtractedRelationship:
    """A relationship between entities."""
    relationship_id: str
    source_entity: str = ""
    target_entity: str = ""
    relation_type: str = ""  # works_for, located_in, caused_by, related_to, part_of
    confidence: float = 0.0
    evidence: str = ""
    extracted_at: float = 0.0


@dataclass
class ExtractedFact:
    """A fact extracted from content."""
    fact_id: str
    subject: str = ""
    predicate: str = ""
    object: str = ""
    confidence: float = 0.0
    source: str = ""
    extracted_at: float = 0.0


@dataclass
class ExtractedTopic:
    """A topic extracted from content."""
    topic_id: str
    name: str
    keywords: list[str] = field(default_factory=list)
    relevance: float = 0.0
    frequency: int = 0
    co_occurrence: list[str] = field(default_factory=list)


@dataclass
class KnowledgeGraph:
    """A knowledge graph built from extraction."""
    graph_id: str
    name: str
    entities: list[str] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    node_count: int = 0
    edge_count: int = 0
    density: float = 0.0
    created_at: float = 0.0


@dataclass
class ExtractionConfig:
    """Configuration for extraction."""
    config_id: str
    max_entities: int = 100
    min_confidence: float = 0.3
    extract_entities: bool = True
    extract_relationships: bool = True
    extract_facts: bool = True
    extract_topics: bool = True
    language: str = "en"
    created_at: float = 0.0


@dataclass
class ExtractionResult:
    """Result of a knowledge extraction run."""
    result_id: str
    source_text: str = ""
    entities_found: int = 0
    relationships_found: int = 0
    facts_found: int = 0
    topics_found: int = 0
    duration_ms: float = 0.0
    confidence_avg: float = 0.0


class KnowledgeExtractionPipeline:
    """Knowledge extraction pipeline."""

    def __init__(self):
        self._entities: dict[str, ExtractedEntity] = {}
        self._relationships: list[ExtractedRelationship] = []
        self._facts: list[ExtractedFact] = []
        self._topics: dict[str, ExtractedTopic] = {}
        self._graphs: dict[str, KnowledgeGraph] = {}
        self._configs: dict[str, ExtractionConfig] = {}
        self._results: list[ExtractionResult] = []

        # Create default config
        self._create_config()

    def _create_config(self):
        """Create default extraction config."""
        config = ExtractionConfig(
            config_id=hashlib.md5(f"config:{time.time()}".encode()).hexdigest()[:12],
            created_at=time.time(),
        )
        self._configs[config.config_id] = config
        return config

    def extract_from_text(self, text: str, config_id: str = "") -> ExtractionResult:
        """Extract knowledge from text."""
        start = time.time()

        config = None
        for c in self._configs.values():
            if c.config_id == config_id or c.config_id == list(self._configs.keys())[0]:
                config = c
                break

        entities_found = 0
        relationships_found = 0
        facts_found = 0
        topics_found = 0
        confidences = []

        # Extract entities
        if config.extract_entities:
            words = text.split()
            entities_types = ["person", "organization", "location", "concept", "event"]
            for i, word in enumerate(words):
                if len(word) > 3 and word[0].isupper():
                    entity = ExtractedEntity(
                        entity_id=hashlib.md5(
                            f"entity:{word}:{text[:30]}:{time.time()}".encode()
                        ).hexdigest()[:12],
                        name=word,
                        entity_type=entities_types[i % len(entities_types)],
                        mentions=[text[max(0, text.find(word)-20):text.find(word)+len(word)+20]],
                        confidence=0.7 + (ord(hashlib.md5(word.encode()).hexdigest()[:1]) % 25) / 100,
                        first_seen=time.time(),
                        last_seen=time.time(),
                    )
                    self._entities[entity.entity_id] = entity
                    entities_found += 1
                    confidences.append(entity.confidence)

        # Extract relationships
        if config.extract_relationships and len(words) > 2:
            relation_types = ["works_for", "located_in", "caused_by", "related_to", "part_of"]
            for i in range(len(words) - 2):
                relation = ExtractedRelationship(
                    relationship_id=hashlib.md5(
                        f"rel:{words[i]}:{words[i+2]}:{time.time()}".encode()
                    ).hexdigest()[:12],
                    source_entity=words[i],
                    target_entity=words[i + 2],
                    relation_type=relation_types[i % len(relation_types)],
                    confidence=0.6 + (ord(hashlib.md5(f"{words[i]}{words[i+2]}".encode()).hexdigest()[:1]) % 30) / 100,
                    evidence=f"{words[i]} -> {words[i+1]} -> {words[i+2]}",
                    extracted_at=time.time(),
                )
                self._relationships.append(relation)
                relationships_found += 1
                confidences.append(relation.confidence)

        # Extract facts
        if config.extract_facts:
            for i in range(len(words) - 2):
                fact = ExtractedFact(
                    fact_id=hashlib.md5(
                        f"fact:{words[i]}:{words[i+1]}:{words[i+2]}:{time.time()}".encode()
                    ).hexdigest()[:12],
                    subject=words[i],
                    predicate=words[i + 1],
                    object=words[i + 2],
                    confidence=0.6 + (ord(hashlib.md5(f"fact:{words[i]}{words[i+1]}".encode()).hexdigest()[:1]) % 30) / 100,
                    source="extracted",
                    extracted_at=time.time(),
                )
                self._facts.append(fact)
                facts_found += 1
                confidences.append(fact.confidence)

        # Extract topics
        if config.extract_topics:
            word_counts = {}
            for word in words:
                if len(word) > 4:
                    word_counts[word] = word_counts.get(word, 0) + 1

            for word, count in sorted(word_counts.items(), key=lambda x: x[1], reverse=True)[:10]:
                topic = ExtractedTopic(
                    topic_id=hashlib.md5(
                        f"topic:{word}:{time.time()}".encode()
                    ).hexdigest()[:12],
                    name=word,
                    keywords=[word, text.split()[0], text.split()[-1]][:3],
                    relevance=count / max(1, len(words)),
                    frequency=count,
                )
                self._topics[topic.topic_id] = topic
                topics_found += 1

        result = ExtractionResult(
            result_id=hashlib.md5(
                f"extraction:{text[:30]}:{time.time()}".encode()
            ).hexdigest()[:12],
            source_text=text[:100],
            entities_found=entities_found,
            relationships_found=relationships_found,
            facts_found=facts_found,
            topics_found=topics_found,
            duration_ms=(time.time() - start) * 1000,
            confidence_avg=sum(confidences) / max(1, len(confidences)),
        )

        self._results.append(result)
        return result

    def build_graph(self, name: str, min_confidence: float = 0.3) -> KnowledgeGraph:
        """Build a knowledge graph from extracted knowledge."""
        entities = [
            e for e in self._entities.values()
            if e.confidence >= min_confidence
        ]
        relationships = [
            r for r in self._relationships
            if r.confidence >= min_confidence
        ]
        facts = [
            f for f in self._facts
            if f.confidence >= min_confidence
        ]
        topics = [
            t for t in self._topics.values()
            if t.relevance >= min_confidence * 0.5
        ]

        graph = KnowledgeGraph(
            graph_id=hashlib.md5(
                f"graph:{name}:{time.time()}".encode()
            ).hexdigest()[:12],
            name=name,
            entities=[e.entity_id for e in entities[:50]],
            relationships=[r.relationship_id for r in relationships[:50]],
            facts=[f.fact_id for f in facts[:50]],
            topics=[t.topic_id for t in topics[:20]],
            node_count=len(entities) + len(topics),
            edge_count=len(relationships),
            density=len(relationships) / max(1, (len(entities) + len(topics)) ** 2),
            created_at=time.time(),
        )

        self._graphs[graph.graph_id] = graph
        return graph

    def query_graph(self, graph_id: str, entity_name: str) -> dict:
        """Query the knowledge graph."""
        graph = self._graphs.get(graph_id)
        if not graph:
            raise HTTPException(404, f"Graph {graph_id} not found")

        # Find entity
        entity = None
        for e in self._entities.values():
            if e.name == entity_name:
                entity = e
                break

        if not entity:
            return {"found": False, "message": f"Entity '{entity_name}' not found in graph"}

        # Find related entities
        related = []
        for rel in self._relationships:
            if rel.source_entity == entity_name or rel.target_entity == entity_name:
                other = rel.target_entity if rel.source_entity == entity_name else rel.source_entity
                related.append({
                    "entity": other,
                    "relation": rel.relation_type,
                    "confidence": rel.confidence,
                })

        # Find related facts
        related_facts = [
            {"predicate": f.predicate, "object": f.object, "confidence": f.confidence}
            for f in self._facts if f.subject == entity_name
        ]

        return {
            "found": True,
            "entity": entity.name,
            "type": entity.entity_type,
            "confidence": entity.confidence,
            "related_entities": related[:10],
            "related_facts": related_facts[:10],
        }

    def get_report(self) -> dict:
        """Get knowledge extraction pipeline report."""
        return {
            "total_entities": len(self._entities),
            "total_relationships": len(self._relationships),
            "total_facts": len(self._facts),
            "total_topics": len(self._topics),
            "total_graphs": len(self._graphs),
            "total_extractions": len(self._results),
            "total_configs": len(self._configs),
            "avg_extraction_confidence": sum(r.confidence_avg for r in self._results) / max(1, len(self._results)),
            "avg_extraction_duration_ms": sum(r.duration_ms for r in self._results) / max(1, len(self._results)),
            "entity_types": {
                type: sum(1 for e in self._entities.values() if e.entity_type == type)
                for type in set(e.entity_type for e in self._entities.values())
            },
            "relationship_types": {
                type: sum(1 for r in self._relationships if r.relation_type == type)
                for type in set(r.relation_type for r in self._relationships)
            },
            "recent_extractions": [
                {
                    "entities": r.entities_found,
                    "relationships": r.relationships_found,
                    "facts": r.facts_found,
                    "topics": r.topics_found,
                    "duration_ms": r.duration_ms,
                }
                for r in self._results[-10:]
            ],
            "graph_summary": [
                {
                    "name": g.name,
                    "nodes": g.node_count,
                    "edges": g.edge_count,
                    "density": round(g.density, 4),
                }
                for g in self._graphs.values()
            ],
        }


extraction_pipeline = KnowledgeExtractionPipeline()


@router.post("/api/v1/memora/extraction/text")
async def extract_from_text(payload: dict):
    """Extract knowledge from text."""
    result = extraction_pipeline.extract_from_text(
        text=payload["text"],
        config_id=payload.get("config_id", ""),
    )
    return {
        "result_id": result.result_id,
        "entities_found": result.entities_found,
        "relationships_found": result.relationships_found,
        "facts_found": result.facts_found,
        "topics_found": result.topics_found,
        "confidence_avg": result.confidence_avg,
    }


@router.post("/api/v1/memora/extraction/graphs")
async def build_graph(payload: dict):
    """Build a knowledge graph."""
    graph = extraction_pipeline.build_graph(
        name=payload["name"],
        min_confidence=payload.get("min_confidence", 0.3),
    )
    return {"graph_id": graph.graph_id, "name": graph.name, "nodes": graph.node_count, "edges": graph.edge_count}


@router.get("/api/v1/memora/extraction/graphs/{graph_id}/query/{entity_name}")
async def query_graph(graph_id: str, entity_name: str):
    """Query the knowledge graph."""
    result = extraction_pipeline.query_graph(graph_id, entity_name)
    return result


@router.get("/api/v1/memora/extraction/report")
async def get_report():
    """Get knowledge extraction pipeline report."""
    return extraction_pipeline.get_report()
