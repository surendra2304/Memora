"""
Graph and Relationship Service for Memora
Manages semantic knowledge graph edges, dependencies, and neighborhood traversals.
"""
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_
from storage.relational.models import MemoryRelationship, MemoryRecord

class GraphService:
    @staticmethod
    def create_relationship(
        db: Session,
        source_memory_id: str,
        target_memory_id: str,
        relationship_type: str = "relates_to",
        weight: float = 1.0
    ) -> MemoryRelationship:
        existing = db.query(MemoryRelationship).filter(
            MemoryRelationship.source_memory_id == source_memory_id,
            MemoryRelationship.target_memory_id == target_memory_id,
            MemoryRelationship.relationship_type == relationship_type
        ).first()

        if existing:
            existing.weight = weight
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