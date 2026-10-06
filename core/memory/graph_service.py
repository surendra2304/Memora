"""
Graph and Relationship Service for Memora
Manages semantic knowledge graph edges, dependencies, entity resolution, and neighborhood traversals.
"""
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import or_
import logging

from storage.relational.models import MemoryRelationship, MemoryRecord, LifecycleState
from core.memory.pipeline.entity_extractor import EntityExtractor
from core.policy.engine import PolicyEngine

logger = logging.getLogger(__name__)

#: Canonical lifecycle states eligible for graph linkage. Kept as enum members
#: only; mixing raw strings into the same in_() clause relies on implicit
#: coercion the SQLAlchemy Enum type does not guarantee.
_LINKABLE_STATES = [LifecycleState.ACTIVE, LifecycleState.VERIFIED]


class InvalidRelationshipError(Exception):
    """Raised when a requested graph edge cannot exist."""


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
        # A self-edge carries no information and previously returned None, which
        # the API layer then dereferenced into an AttributeError.
        if source_memory_id == target_memory_id:
            raise InvalidRelationshipError("A memory cannot be linked to itself.")

        # Reject dangling edges. Both endpoints must exist, otherwise the graph
        # accumulates references to records that were never written or were since
        # purged, and traversal silently reports phantom neighbours.
        endpoint_ids = {source_memory_id, target_memory_id}
        found = {
            row[0]
            for row in db.query(MemoryRecord.id).filter(MemoryRecord.id.in_(endpoint_ids)).all()
        }
        missing = endpoint_ids - found
        if missing:
            raise InvalidRelationshipError(
                f"Cannot link to unknown memory id(s): {', '.join(sorted(missing))}."
            )

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

        # Find existing active memories with overlapping text/entities. Scoped to
        # the writing record's tenant: without this the graph was wired across
        # tenant boundaries, linking memories that no shared policy would ever
        # allow to be read together.
        existing_records = db.query(MemoryRecord).filter(
            MemoryRecord.id != memory_record.id,
            MemoryRecord.tenant_id == getattr(memory_record, "tenant_id", "default"),
            MemoryRecord.lifecycle_state.in_(_LINKABLE_STATES)
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
        max_hops: int = 2,
        actor: Optional[Any] = None,
        purpose: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Traverses 1-hop and 2-hop neighborhoods from a focal memory node.

        When an `actor` is supplied, traversal is filtered through the policy
        engine: nodes the caller may not read are neither returned nor walked
        through. Without this the endpoint disclosed the ids of memories in other
        agents' private namespaces, which is a leak even with no content shown.
        """
        visited_nodes = {memory_id}
        visited_edge_keys = set()
        edges = []
        current_layer = {memory_id}
        #: Ids the caller may not see, cached so each record is checked once.
        readable: Dict[str, bool] = {}

        def _may_read(candidate_id: str) -> bool:
            """Fail closed: an unreadable neighbour is never revealed or traversed."""
            if actor is None:
                return True
            if candidate_id in readable:
                return readable[candidate_id]
            record = db.query(MemoryRecord).filter(MemoryRecord.id == candidate_id).first()
            if record is None:
                readable[candidate_id] = False
                return False
            decision = PolicyEngine.evaluate_access(
                db,
                actor=actor,
                namespace=record.namespace,
                action="read",
                purpose=purpose,
                memory_id=record.id,
                log_audit=False,
            )
            readable[candidate_id] = bool(decision.allowed)
            return readable[candidate_id]

        if actor is not None and not _may_read(memory_id):
            # The caller cannot even read the focal node, so there is no
            # neighbourhood to disclose.
            return {
                "root_memory_id": memory_id,
                "connected_memory_ids": [],
                "total_nodes": 0,
                "edges": []
            }

        for hop in range(1, max_hops + 1):
            next_layer = set()
            rels = db.query(MemoryRelationship).filter(
                or_(
                    MemoryRelationship.source_memory_id.in_(current_layer),
                    MemoryRelationship.target_memory_id.in_(current_layer)
                )
            ).all()

            for r in rels:
                neighbor = r.target_memory_id if r.source_memory_id in current_layer else r.source_memory_id

                # Both endpoints must be readable before an edge is disclosed:
                # the edge itself proves the existence of the far node.
                if not (_may_read(r.source_memory_id) and _may_read(r.target_memory_id)):
                    continue

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