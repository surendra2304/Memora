"""
Graph and Relationship Service for Memora
Manages semantic knowledge graph edges, dependencies, entity resolution, and neighborhood traversals.
"""
from typing import List, Optional, Dict, Any, Set
from sqlalchemy.orm import Session
from sqlalchemy import or_
import json
import logging

from storage.relational.models import MemoryRelationship, MemoryRecord
from core.memory.pipeline.entity_extractor import EntityExtractor

logger = logging.getLogger(__name__)

class GraphService:
    @staticmethod
    def resolve_canonical_entity(raw_entity: str) -> str:
        """Resolves an entity string or alias to its canonical node identifier."""
        return EntityExtractor.resolve_canonical(raw_entity)

    @staticmethod
    def create_relationship(
        db: Session,
        source_memory_id: str,
        target_memory_id: str,
        relationship_type: str = "relates_to",
        weight: float = 1.0
    ) -> MemoryRelationship:
        if source_memory_id == target_memory_id:
            return None

        existing = db.query(MemoryRelationship).filter(
            MemoryRelationship.source_memory_id == source_memory_id,
            MemoryRelationship.target_memory_id == target_memory_id,
            MemoryRelationship.relationship_type == relationship_type
        ).first()

        if existing:
            existing.weight = max(existing.weight, weight)
            db.commit()
            db.refresh(existing)
            return existing

        rel = MemoryRelationship(
            source_memory_id=source_memory_id,
            target_memory_id=target_memory_id,
            relationship_type=relationship_type,
            weight=weight
        )
        db.add(rel)
        db.commit()
        db.refresh(rel)
        return rel

    @classmethod
    def auto_link_entity_memories(
        cls,
        db: Session,
        memory_record: MemoryRecord,
        extraction_data: Dict[str, Any]
    ) -> List[MemoryRelationship]:
        """
        Entity Resolution & Automatic Knowledge Graph Wiring:
        Resolves entities to canonical clusters and links memories sharing common canonical entities.
        """
        canonical_entities = set(extraction_data.get("resolved_canonical_entities", []))
        canonical_entities.update(extraction_data.get("entities", []))
        
        if not canonical_entities:
            return []

        # Find existing active memories with overlapping text/entities
        existing_records = db.query(MemoryRecord).filter(
            MemoryRecord.id != memory_record.id,
            MemoryRecord.lifecycle_state.in_(["active", "verified"])
        ).order_by(MemoryRecord.created_at.desc()).limit(50).all()

        created_edges = []
        for other in existing_records:
            other_text = other.content_text.lower()
            # Extract or retrieve other record's canonical entities
            other_canonicals = set()
            for entity in canonical_entities:
                # Check if alias or canonical exists in other record
                aliases = EntityExtractor.CANONICAL_ENTITIES.get(entity, {entity})
                if any(alias in other_text for alias in aliases):
                    other_canonicals.add(entity)

            # If shared canonical entities exist, wire graph relationship
            if other_canonicals:
                rel_type = "shares_entity"
                weight = min(1.0, 0.70 + (0.10 * len(other_canonicals)))
                
                # Check for explicit triple relationship hints
                triples = extraction_data.get("triples", [])
                for t in triples:
                    if t["predicate"] in ["depends_on", "implements", "verified", "secures"]:
                        rel_type = t["predicate"]
                        weight = 0.95
                        break

                rel = cls.create_relationship(
                    db=db,
                    source_memory_id=memory_record.id,
                    target_memory_id=other.id,
                    relationship_type=rel_type,
                    weight=weight
                )
                if rel:
                    created_edges.append(rel)

        return created_edges

    @staticmethod
    def get_connected_memories(
        db: Session,
        memory_id: str,
        max_hops: int = 2
    ) -> Dict[str, Any]:
        """
        Traverses 1-hop and 2-hop neighborhoods from a focal memory node.
        """
        visited_nodes = {memory_id}
        visited_edge_keys = set()
        edges = []
        current_layer = {memory_id}

        for hop in range(1, max_hops + 1):
            next_layer = set()
            rels = db.query(MemoryRelationship).filter(
                or_(
                    MemoryRelationship.source_memory_id.in_(current_layer),
                    MemoryRelationship.target_memory_id.in_(current_layer)
                )
            ).all()

            for r in rels:
                edge_key = (r.source_memory_id, r.target_memory_id, r.relationship_type)
                if edge_key not in visited_edge_keys:
                    visited_edge_keys.add(edge_key)
                    edges.append({
                        "source_id": r.source_memory_id,
                        "target_id": r.target_memory_id,
                        "type": r.relationship_type,
                        "weight": r.weight,
                        "hop": hop
                    })

                neighbor = r.target_memory_id if r.source_memory_id in current_layer else r.source_memory_id
                if neighbor not in visited_nodes:
                    visited_nodes.add(neighbor)
                    next_layer.add(neighbor)

            current_layer = next_layer
            if not current_layer:
                break

        return {
            "root_memory_id": memory_id,
            "connected_memory_ids": list(visited_nodes - {memory_id}),
            "total_nodes": len(visited_nodes),
            "edges": edges
        }

    @staticmethod
    def get_graph_neighbors(
        db: Session,
        memory_ids: List[str]
    ) -> Dict[str, float]:
        if not memory_ids:
            return {}

        rels = db.query(MemoryRelationship).filter(
            or_(
                MemoryRelationship.source_memory_id.in_(memory_ids),
                MemoryRelationship.target_memory_id.in_(memory_ids)
            )
        ).all()

        neighbor_weights: Dict[str, float] = {}
        for r in rels:
            for node_id in [r.source_memory_id, r.target_memory_id]:
                if node_id not in memory_ids:
                    neighbor_weights[node_id] = neighbor_weights.get(node_id, 0.0) + (r.weight * 0.15)

        return neighbor_weights