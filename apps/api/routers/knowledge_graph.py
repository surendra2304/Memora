"""
Memora/apps/api/routers/knowledge_graph.py — Memora Knowledge Graph Engine.

Extends Memora's capabilities with:
1. Knowledge Graph: build a semantic knowledge graph from memories
2. Semantic Search: find related concepts using embeddings
3. Concept Clustering: group related memories into concept clusters
4. Relationship Extraction: extract relationships between entities
5. Graph Reasoning: reason over the knowledge graph
6. Graph Visualization: export graph for visualization
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

router = APIRouter()


@dataclass
class GraphNode:
    """A node in the knowledge graph."""
    node_id: str
    name: str
    type: str  # concept, entity, memory, event
    properties: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    access_count: int = 0
    last_accessed: float = 0.0


@dataclass
class GraphEdge:
    """An edge in the knowledge graph."""
    edge_id: str
    source: str
    target: str
    relationship: str  # related_to, causes, part_of, derived_from, etc.
    weight: float = 1.0
    properties: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0


@dataclass
class ConceptCluster:
    """A cluster of related concepts."""
    cluster_id: str
    name: str
    nodes: list[str] = field(default_factory=list)
    centroid: dict[str, float] = field(default_factory=dict)
    density: float = 0.0
    cohesion: float = 0.0
    created_at: float = 0.0


class KnowledgeGraph:
    """Semantic knowledge graph engine."""

    def __init__(self):
        self._nodes: dict[str, GraphNode] = {}
        self._edges: dict[str, GraphEdge] = {}
        self._clusters: dict[str, ConceptCluster] = {}
        self._adjacency: dict[str, list[tuple[str, str]]] = {}
        self._embeddings: dict[str, list[float]] = {}

    # ── Node Management ───────────────────────────────────────────────────

    def add_node(
        self,
        name: str,
        type: str = "concept",
        properties: dict[str, Any] | None = None,
    ) -> GraphNode:
        """Add a node to the graph."""
        node_id = hashlib.md5(
            f"{name}:{type}:{time.time()}".encode()
        ).hexdigest()[:12]

        node = GraphNode(
            node_id=node_id,
            name=name,
            type=type,
            properties=properties or {},
            created_at=time.time(),
        )

        self._nodes[node_id] = node
        self._adjacency[node_id] = []

        # Generate simple embedding (hash-based)
        self._embeddings[node_id] = self._hash_embed(name)

        return node

    def _hash_embed(self, text: str, dims: int = 64) -> list[float]:
        """Simple hash-based embedding."""
        vec = [0.0] * dims
        for i, char in enumerate(text):
            vec[hash(char) % dims] += 1.0 / (i + 1)

        # Normalize
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]

        return vec

    def get_node(self, node_id: str) -> GraphNode | None:
        """Get a node by ID."""
        return self._nodes.get(node_id)

    def find_nodes(self, name: str, type: str | None = None) -> list[GraphNode]:
        """Find nodes by name."""
        results = []
        for node in self._nodes.values():
            if node.name.lower() == name.lower():
                if type is None or node.type == type:
                    results.append(node)
        return results

    def get_all_nodes(self, type: str | None = None) -> list[GraphNode]:
        """Get all nodes."""
        if type:
            return [n for n in self._nodes.values() if n.type == type]
        return list(self._nodes.values())

    def remove_node(self, node_id: str) -> bool:
        """Remove a node from the graph."""
        if node_id not in self._nodes:
            return False

        # Remove edges
        for edge_id in list(self._edges.keys()):
            edge = self._edges[edge_id]
            if edge.source == node_id or edge.target == node_id:
                del self._edges[edge_id]

        # Remove from adjacency
        for other_id in list(self._adjacency.keys()):
            self._adjacency[other_id] = [
                (s, r) for s, r in self._adjacency[other_id]
                if s != node_id
            ]

        del self._nodes[node_id]
        del self._adjacency[node_id]
        self._embeddings.pop(node_id, None)

        return True

    # ── Edge Management ───────────────────────────────────────────────────

    def add_edge(
        self,
        source: str,
        target: str,
        relationship: str = "related_to",
        weight: float = 1.0,
        properties: dict[str, Any] | None = None,
    ) -> GraphEdge:
        """Add an edge between two nodes."""
        if source not in self._nodes:
            raise ValueError(f"Source node not found: {source}")
        if target not in self._nodes:
            raise ValueError(f"Target node not found: {target}")

        edge_id = hashlib.md5(
            f"{source}:{target}:{relationship}:{time.time()}".encode()
        ).hexdigest()[:12]

        edge = GraphEdge(
            edge_id=edge_id,
            source=source,
            target=target,
            relationship=relationship,
            weight=weight,
            properties=properties or {},
            created_at=time.time(),
        )

        self._edges[edge_id] = edge
        self._adjacency[source].append((target, relationship))
        self._adjacency[target].append((source, relationship))

        return edge

    def get_edges(self, node_id: str) -> list[GraphEdge]:
        """Get all edges for a node."""
        return [
            e for e in self._edges.values()
            if e.source == node_id or e.target == node_id
        ]

    def get_all_edges(self) -> list[GraphEdge]:
        """Get all edges."""
        return list(self._edges.values())

    def remove_edge(self, edge_id: str) -> bool:
        """Remove an edge."""
        if edge_id not in self._edges:
            return False

        edge = self._edges[edge_id]

        # Remove from adjacency
        if edge.source in self._adjacency:
            self._adjacency[edge.source] = [
                (s, r) for s, r in self._adjacency[edge.source]
                if s != edge.target
            ]
        if edge.target in self._adjacency:
            self._adjacency[edge.target] = [
                (s, r) for s, r in self._adjacency[edge.target]
                if s != edge.source
            ]

        del self._edges[edge_id]
        return True

    # ── Semantic Search ───────────────────────────────────────────────────

    def semantic_search(
        self,
        query: str,
        limit: int = 10,
        threshold: float = 0.1,
    ) -> list[tuple[GraphNode, float]]:
        """Search nodes by semantic similarity."""
        query_embed = self._hash_embed(query)

        results = []
        for node_id, node in self._nodes.items():
            similarity = self._cosine_similarity(
                query_embed, self._embeddings.get(node_id, [])
            )
            if similarity >= threshold:
                results.append((node, similarity))

        results.sort(key=lambda x: -x[1])
        return results[:limit]

    def _cosine_similarity(self, v1: list[float], v2: list[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        if not v1 or not v2:
            return 0.0

        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot / (norm1 * norm2)

    def find_related(
        self,
        node_id: str,
        depth: int = 2,
        relationship: str | None = None,
    ) -> list[GraphNode]:
        """Find nodes related to a given node."""
        if node_id not in self._nodes:
            return []

        visited = set()
        queue = [(node_id, 0)]
        results = []

        while queue:
            current_id, current_depth = queue.pop(0)

            if current_id in visited:
                continue
            visited.add(current_id)

            if current_id != node_id:
                results.append(self._nodes[current_id])

            if current_depth >= depth:
                continue

            for neighbor_id, rel in self._adjacency.get(current_id, []):
                if relationship is None or rel == relationship:
                    queue.append((neighbor_id, current_depth + 1))

        return results

    # ── Concept Clustering ─────────────────────────────────────────────────

    def build_clusters(
        self,
        max_clusters: int = 10,
        threshold: float = 0.3,
    ) -> list[ConceptCluster]:
        """Build concept clusters from the graph."""
        self._clusters = {}

        nodes = list(self._nodes.values())
        if not nodes:
            return []

        # Simple clustering: group nodes by embedding similarity
        assigned = set()
        clusters = []

        for i, node in enumerate(nodes):
            if node.node_id in assigned:
                continue

            # Create new cluster
            cluster = ConceptCluster(
                cluster_id=hashlib.md5(
                    f"cluster:{i}:{time.time()}".encode()
                ).hexdigest()[:12],
                name=node.name,
                created_at=time.time(),
            )

            # Find similar nodes
            for other_node in nodes:
                if other_node.node_id in assigned:
                    continue
                if other_node.node_id == node.node_id:
                    continue

                similarity = self._cosine_similarity(
                    self._embeddings[node.node_id],
                    self._embeddings.get(other_node.node_id, []),
                )

                if similarity >= threshold:
                    cluster.nodes.append(other_node.node_id)
                    assigned.add(other_node.node_id)

            cluster.nodes.append(node.node_id)
            assigned.add(node.node_id)

            # Calculate centroid
            if cluster.nodes:
                centroid = [0.0] * len(self._embeddings[node.node_id])
                for nid in cluster.nodes:
                    embed = self._embeddings.get(nid, [])
                    for j, v in enumerate(embed):
                        centroid[j] += v
                norm = math.sqrt(sum(c * c for c in centroid))
                if norm > 0:
                    centroid = [c / norm for c in centroid]
                cluster.centroid = centroid

            # Calculate density (average edge density within cluster)
            density = 0.0
            if len(cluster.nodes) > 1:
                edge_count = 0
                for i, nid1 in enumerate(cluster.nodes):
                    for nid2 in cluster.nodes[i+1:]:
                        if (nid2, "related_to") in self._adjacency.get(nid1, []) or \
                           (nid1, "related_to") in self._adjacency.get(nid2, []):
                            edge_count += 1
                density = edge_count / (len(cluster.nodes) * (len(cluster.nodes) - 1) / 2)

            cluster.density = density

            clusters.append(cluster)

        for cluster in clusters:
            self._clusters[cluster.cluster_id] = cluster

        return clusters[:max_clusters]

    def get_clusters(self) -> list[ConceptCluster]:
        """Get all clusters."""
        return list(self._clusters.values())

    # ── Relationship Extraction ────────────────────────────────────────────

    def extract_relationships(
        self,
        text: str,
        source_node: str,
    ) -> list[GraphEdge]:
        """Extract relationships from text and add edges."""
        relationships = []

        # Simple patterns
        patterns = [
            (r"(\w+)\s+is\s+(?:a|an)\s+(\w+)", "is_a"),
            (r"(\w+)\s+part\s+of\s+(\w+)", "part_of"),
            (r"(\w+)\s+causes\s+(\w+)", "causes"),
            (r"(\w+)\s+derived\s+from\s+(\w+)", "derived_from"),
            (r"(\w+)\s+related\s+to\s+(\w+)", "related_to"),
        ]

        for pattern, rel_type in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                target_name = match[-1] if isinstance(match, tuple) else match[1]

                # Find or create target node
                target_nodes = self.find_nodes(target_name)
                if not target_nodes:
                    target_node = self.add_node(target_name, type="concept")
                else:
                    target_node = target_nodes[0]

                edge = self.add_edge(
                    source=source_node,
                    target=target_node.node_id,
                    relationship=rel_type,
                )
                relationships.append(edge)

        return relationships

    # ── Graph Reasoning ───────────────────────────────────────────────────

    def reason_over_graph(
        self,
        query: str,
        depth: int = 3,
    ) -> dict[str, Any]:
        """Reason over the knowledge graph to answer a query."""
        # Find relevant starting nodes
        starting_nodes = self.semantic_search(query, limit=5)

        if not starting_nodes:
            return {"query": query, "answer": "No relevant nodes found", "confidence": 0.0}

        # Traverse graph
        visited = set()
        evidence = []
        inferences = []

        for node, similarity in starting_nodes:
            queue = [(node.node_id, 0, similarity)]

            while queue:
                current_id, current_depth, current_confidence = queue.pop(0)

                if current_id in visited:
                    continue
                visited.add(current_id)

                current_node = self._nodes.get(current_id)
                if not current_node:
                    continue

                evidence.append({
                    "node": current_node.name,
                    "type": current_node.type,
                    "confidence": current_confidence,
                    "depth": current_depth,
                })

                if current_depth >= depth:
                    continue

                for neighbor_id, rel in self._adjacency.get(current_id, []):
                    neighbor_confidence = current_confidence * 0.9
                    queue.append((neighbor_id, current_depth + 1, neighbor_confidence))

        # Generate answer
        answer = f"Based on {len(evidence)} nodes in the knowledge graph, "
        answer += f"related to '{query}': "
        answer += ", ".join(e["node"] for e in evidence[:5])

        avg_confidence = sum(e["confidence"] for e in evidence) / max(1, len(evidence))

        return {
            "query": query,
            "answer": answer,
            "confidence": avg_confidence,
            "evidence_count": len(evidence),
            "evidence": evidence[:10],
        }

    # ── Graph Visualization ────────────────────────────────────────────────

    def export_graph(self, format: str = "json") -> dict[str, Any]:
        """Export the graph for visualization."""
        nodes = [
            {
                "id": n.node_id,
                "name": n.name,
                "type": n.type,
                "properties": n.properties,
            }
            for n in self._nodes.values()
        ]

        edges = [
            {
                "id": e.edge_id,
                "source": e.source,
                "target": e.target,
                "relationship": e.relationship,
                "weight": e.weight,
            }
            for e in self._edges.values()
        ]

        return {"nodes": nodes, "edges": edges, "format": format}

    def get_graph_stats(self) -> dict[str, Any]:
        """Get graph statistics."""
        type_counts: dict[str, int] = {}
        rel_counts: dict[str, int] = {}

        for node in self._nodes.values():
            type_counts[node.type] = type_counts.get(node.type, 0) + 1

        for edge in self._edges.values():
            rel_counts[edge.relationship] = rel_counts.get(edge.relationship, 0) + 1

        return {
            "total_nodes": len(self._nodes),
            "total_edges": len(self._edges),
            "node_types": type_counts,
            "relationship_types": rel_counts,
            "clusters": len(self._clusters),
            "density": len(self._edges) / max(1, (len(self._nodes) * (len(self._nodes) - 1) / 2)),
        }


# Module-level singleton
knowledge_graph = KnowledgeGraph()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/knowledge/nodes")
async def add_node(payload: dict):
    """Add a node to the knowledge graph."""
    node = knowledge_graph.add_node(
        name=payload["name"],
        type=payload.get("type", "concept"),
        properties=payload.get("properties", {}),
    )
    return {"node_id": node.node_id, "name": node.name, "type": node.type}


@router.post("/api/v1/knowledge/edges")
async def add_edge(payload: dict):
    """Add an edge to the knowledge graph."""
    try:
        edge = knowledge_graph.add_edge(
            source=payload["source"],
            target=payload["target"],
            relationship=payload.get("relationship", "related_to"),
            weight=payload.get("weight", 1.0),
            properties=payload.get("properties", {}),
        )
        return {"edge_id": edge.edge_id, "relationship": edge.relationship}
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/api/v1/knowledge/search")
async def semantic_search(q: str, limit: int = 10):
    """Search the knowledge graph semantically."""
    results = knowledge_graph.semantic_search(q, limit=limit)
    return [
        {"node_id": n.node_id, "name": n.name, "type": n.type, "similarity": s}
        for n, s in results
    ]


@router.get("/api/v1/knowledge/related/{node_id}")
async def find_related(node_id: str, depth: int = 2):
    """Find nodes related to a given node."""
    nodes = knowledge_graph.find_related(node_id, depth=depth)
    return [
        {"node_id": n.node_id, "name": n.name, "type": n.type}
        for n in nodes
    ]


@router.get("/api/v1/knowledge/reason")
async def reason(q: str, depth: int = 3):
    """Reason over the knowledge graph."""
    return knowledge_graph.reason_over_graph(q, depth=depth)


@router.post("/api/v1/knowledge/clusters")
async def build_clusters(payload: dict | None = None):
    """Build concept clusters."""
    payload = payload or {}
    clusters = knowledge_graph.build_clusters(
        max_clusters=payload.get("max_clusters", 10),
        threshold=payload.get("threshold", 0.3),
    )
    return [
        {
            "cluster_id": c.cluster_id,
            "name": c.name,
            "node_count": len(c.nodes),
            "density": c.density,
        }
        for c in clusters
    ]


@router.get("/api/v1/knowledge/stats")
async def graph_stats():
    """Get knowledge graph statistics."""
    return knowledge_graph.get_graph_stats()


@router.get("/api/v1/knowledge/export")
async def export_graph():
    """Export the knowledge graph."""
    return knowledge_graph.export_graph()
