"""
Memora/apps/api/routers/causal_memory_graph.py — Memora Causal Memory Graph.

Builds and queries causal graphs from memory: cause-effect relationships,
temporal chains, counterfactual analysis, and causal explanation generation.
Uses structured causal models for reasoning about memory dependencies.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class CausalNode:
    """A node in the causal graph."""
    node_id: str
    memory_id: str
    variable: str  # The causal variable
    value: str = ""
    node_type: str = "event"  # event, state, action, outcome, factor
    description: str = ""
    timestamp: float = 0.0
    confidence: float = 0.5


@dataclass
class CausalEdge:
    """A directed edge (causal relationship) in the graph."""
    edge_id: str
    source_node: str
    target_node: str
    relationship: str  # causes, enables, prevents, contributes_to, correlates_with
    strength: float = 0.5  # 0-1
    evidence: list[str] = field(default_factory=list)
    direction: str = "forward"  # forward, backward
    created_at: float = 0.0


@dataclass
class CausalGraph:
    """A causal graph."""
    graph_id: str
    name: str
    description: str = ""
    nodes: list[CausalNode] = field(default_factory=list)
    edges: list[CausalEdge] = field(default_factory=list)
    created_at: float = 0.0
    node_count: int = 0
    edge_count: int = 0
    density: float = 0.0


@dataclass
class CausalPath:
    """A path in the causal graph."""
    path_id: str
    graph_id: str
    source_node: str
    target_node: str
    nodes: list[str] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)
    length: int = 0
    total_strength: float = 0.0
    causal_explanation: str = ""


@dataclass
class CounterfactualAnalysis:
    """Counterfactual analysis result."""
    analysis_id: str
    graph_id: str
    query: str
    actual_outcome: str = ""
    counterfactual_outcome: str = ""
    difference: str = ""
    intervention: str = ""
    explanation: str = ""
    confidence: float = 0.0


@dataclass
class TemporalCausalChain:
    """A temporal chain of causal events."""
    chain_id: str
    graph_id: str
    events: list[str] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0
    duration: float = 0.0
    causal_strength: float = 0.0
    description: str = ""


@dataclass
class CausalExplanation:
    """A causal explanation for an outcome."""
    explanation_id: str
    graph_id: str
    outcome_node: str
    causes: list[str] = field(default_factory=list)
    root_causes: list[str] = field(default_factory=list)
    contributing_factors: list[str] = field(default_factory=list)
    confidence: float = 0.0
    explanation_text: str = ""


class CausalMemoryGraph:
    """Causal memory graph engine."""

    def __init__(self):
        self._graphs: dict[str, CausalGraph] = {}
        self._nodes: dict[str, CausalNode] = {}
        self._edges: dict[str, CausalEdge] = {}
        self._counterfactuals: dict[str, CounterfactualAnalysis] = {}
        self._explanations: dict[str, CausalExplanation] = {}

    def create_graph(self, name: str, description: str = "") -> CausalGraph:
        """Create a causal graph."""
        graph = CausalGraph(
            graph_id=hashlib.md5(
                f"graph:{name}:{time.time()}".encode()
            ).hexdigest()[:12],
            name=name,
            description=description,
            created_at=time.time(),
        )
        self._graphs[graph.graph_id] = graph
        return graph

    def add_node(
        self,
        graph_id: str,
        memory_id: str,
        variable: str,
        value: str = "",
        node_type: str = "event",
        description: str = "",
        confidence: float = 0.5,
    ) -> CausalNode:
        """Add a node to a causal graph."""
        graph = self._graphs.get(graph_id)
        if not graph:
            raise HTTPException(404, f"Graph {graph_id} not found")

        node = CausalNode(
            node_id=hashlib.md5(
                f"node:{graph_id}:{variable}:{time.time()}".encode()
            ).hexdigest()[:12],
            memory_id=memory_id,
            variable=variable,
            value=value,
            node_type=node_type,
            description=description,
            timestamp=time.time(),
            confidence=confidence,
        )

        graph.nodes.append(node)
        graph.node_count = len(graph.nodes)
        self._nodes[node.node_id] = node
        return node

    def add_edge(
        self,
        graph_id: str,
        source_node: str,
        target_node: str,
        relationship: str,
        strength: float = 0.5,
        evidence: list[str] | None = None,
        direction: str = "forward",
    ) -> CausalEdge:
        """Add a causal edge between nodes."""
        graph = self._graphs.get(graph_id)
        if not graph:
            raise HTTPException(404, f"Graph {graph_id} not found")

        # Validate nodes exist
        source = next((n for n in graph.nodes if n.node_id == source_node), None)
        target = next((n for n in graph.nodes if n.node_id == target_node), None)

        if not source or not target:
            raise HTTPException(400, "Source or target node not found")

        edge = CausalEdge(
            edge_id=hashlib.md5(
                f"edge:{graph_id}:{source_node}:{target_node}:{time.time()}".encode()
            ).hexdigest()[:12],
            source_node=source_node,
            target_node=target_node,
            relationship=relationship,
            strength=strength,
            evidence=evidence or [],
            direction=direction,
            created_at=time.time(),
        )

        graph.edges.append(edge)
        graph.edge_count = len(graph.edges)

        # Update density
        if graph.node_count > 1:
            max_edges = graph.node_count * (graph.node_count - 1)
            graph.density = graph.edge_count / max(1, max_edges)

        self._edges[edge.edge_id] = edge
        return edge

    def find_causal_path(
        self,
        graph_id: str,
        source_node: str,
        target_node: str,
    ) -> CausalPath:
        """Find a causal path between two nodes."""
        graph = self._graphs.get(graph_id)
        if not graph:
            raise HTTPException(404, f"Graph {graph_id} not found")

        # BFS to find shortest path
        queue = [(source_node, [source_node], 1.0)]
        visited = {source_node}

        while queue:
            current, path, strength = queue.pop(0)

            if current == target_node:
                edges_in_path = []
                for i in range(len(path) - 1):
                    edge = next(
                        (e for e in graph.edges if e.source_node == path[i] and e.target_node == path[i + 1]),
                        None,
                    )
                    if edge:
                        edges_in_path.append(edge.edge_id)

                explanation = self._generate_causal_explanation(graph, path, edges_in_path)

                return CausalPath(
                    path_id=hashlib.md5(
                        f"path:{graph_id}:{source_node}:{target_node}:{time.time()}".encode()
                    ).hexdigest()[:12],
                    graph_id=graph_id,
                    source_node=source_node,
                    target_node=target_node,
                    nodes=path,
                    edges=edges_in_path,
                    length=len(path),
                    total_strength=strength,
                    causal_explanation=explanation,
                )

            # Find outgoing edges
            for edge in graph.edges:
                if edge.source_node == current and edge.target_node not in visited:
                    visited.add(edge.target_node)
                    queue.append((
                        edge.target_node,
                        path + [edge.target_node],
                        strength * edge.strength,
                    ))

        # No path found
        return CausalPath(
            path_id=hashlib.md5(
                f"path:nofound:{time.time()}".encode()
            ).hexdigest()[:12],
            graph_id=graph_id,
            source_node=source_node,
            target_node=target_node,
            length=0,
            causal_explanation="No causal path found between the specified nodes",
        )

    def _generate_causal_explanation(
        self,
        graph: CausalGraph,
        path: list[str],
        edges: list[str],
    ) -> str:
        """Generate a causal explanation for a path."""
        explanation = "Causal chain: "

        for i, node_id in enumerate(path):
            node = next((n for n in graph.nodes if n.node_id == node_id), None)
            if node:
                if i == 0:
                    explanation += f"{node.variable} ({node.value})"
                else:
                    edge = next((e for e in graph.edges if e.edge_id == edges[i - 1]), None)
                    if edge:
                        explanation += f" --[{edge.relationship}]--> {node.variable} ({node.value})"

        explanation += "."
        return explanation

    def counterfactual_analysis(
        self,
        graph_id: str,
        query: str,
        intervention: str,
        actual_outcome: str = "",
    ) -> CounterfactualAnalysis:
        """Perform counterfactual analysis."""
        graph = self._graphs.get(graph_id)
        if not graph:
            raise HTTPException(404, f"Graph {graph_id} not found")

        # Simulate counterfactual
        counterfactual_outcome = (
            f"If {intervention} had not occurred, the outcome would have been: "
            f"{actual_outcome} would not have happened. Instead, "
            f"the system would have continued on an alternative causal path."
        )

        analysis = CounterfactualAnalysis(
            analysis_id=hashlib.md5(
                f"counterfactual:{graph_id}:{query}:{time.time()}".encode()
            ).hexdigest()[:12],
            graph_id=graph_id,
            query=query,
            actual_outcome=actual_outcome,
            counterfactual_outcome=counterfactual_outcome,
            difference=f"Intervention '{intervention}' is a contributing cause to '{actual_outcome}'",
            intervention=intervention,
            explanation=counterfactual_outcome,
            confidence=0.6,
        )

        self._counterfactuals[analysis.analysis_id] = analysis
        return analysis

    def explain_outcome(self, graph_id: str, outcome_node: str) -> CausalExplanation:
        """Generate causal explanation for an outcome."""
        graph = self._graphs.get(graph_id)
        if not graph:
            raise HTTPException(404, f"Graph {graph_id} not found")

        # Find all incoming edges to the outcome
        causes = []
        root_causes = []
        contributing_factors = []

        for edge in graph.edges:
            if edge.target_node == outcome_node:
                causes.append(edge.source_node)
                contributing_factors.append(edge.source_node)

        # Find root causes (nodes with no incoming edges)
        for cause_node in causes:
            has_incoming = any(e.target_node == cause_node for e in graph.edges)
            if not has_incoming:
                root_causes.append(cause_node)

        # Build explanation text
        cause_names = []
        for node_id in causes:
            node = next((n for n in graph.nodes if n.node_id == node_id), None)
            if node:
                cause_names.append(f"{node.variable} ({node.value})")

        root_cause_names = []
        for node_id in root_causes:
            node = next((n for n in graph.nodes if n.node_id == node_id), None)
            if node:
                root_cause_names.append(f"{node.variable} ({node.value})")

        explanation_text = (
            f"Outcome '{outcome_node}' is caused by: {', '.join(cause_names) if cause_names else 'no direct causes found'}. "
            f"Root causes: {', '.join(root_cause_names) if root_cause_names else 'none identified'}."
        )

        explanation = CausalExplanation(
            explanation_id=hashlib.md5(
                f"explanation:{graph_id}:{outcome_node}:{time.time()}".encode()
            ).hexdigest()[:12],
            graph_id=graph_id,
            outcome_node=outcome_node,
            causes=causes,
            root_causes=root_causes,
            contributing_factors=contributing_factors,
            confidence=0.7,
            explanation_text=explanation_text,
        )

        self._explanations[explanation.explanation_id] = explanation
        return explanation

    def get_report(self) -> dict:
        """Get causal memory graph report."""
        return {
            "total_graphs": len(self._graphs),
            "total_nodes": len(self._nodes),
            "total_edges": len(self._edges),
            "total_counterfactuals": len(self._counterfactuals),
            "total_explanations": len(self._explanations),
            "graphs": [
                {
                    "name": g.name,
                    "nodes": g.node_count,
                    "edges": g.edge_count,
                    "density": g.density,
                }
                for g in self._graphs.values()
            ],
        }


# Module-level singleton
causal_graph = CausalMemoryGraph()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/memora/causal/graphs")
async def create_graph(payload: dict):
    """Create a causal graph."""
    graph = causal_graph.create_graph(
        name=payload["name"],
        description=payload.get("description", ""),
    )
    return {"graph_id": graph.graph_id, "name": graph.name, "status": "created"}


@router.post("/api/v1/memora/causal/graphs/{graph_id}/nodes")
async def add_node(graph_id: str, payload: dict):
    """Add a node to a causal graph."""
    node = causal_graph.add_node(
        graph_id=graph_id,
        memory_id=payload["memory_id"],
        variable=payload["variable"],
        value=payload.get("value", ""),
        node_type=payload.get("node_type", "event"),
        description=payload.get("description", ""),
        confidence=payload.get("confidence", 0.5),
    )
    return {"node_id": node.node_id, "variable": node.variable, "status": "added"}


@router.post("/api/v1/memora/causal/graphs/{graph_id}/edges")
async def add_edge(graph_id: str, payload: dict):
    """Add a causal edge."""
    edge = causal_graph.add_edge(
        graph_id=graph_id,
        source_node=payload["source_node"],
        target_node=payload["target_node"],
        relationship=payload["relationship"],
        strength=payload.get("strength", 0.5),
        evidence=payload.get("evidence", []),
        direction=payload.get("direction", "forward"),
    )
    return {"edge_id": edge.edge_id, "relationship": edge.relationship, "strength": edge.strength}


@router.get("/api/v1/memora/causal/graphs/{graph_id}/path")
async def find_causal_path(graph_id: str, source_node: str, target_node: str):
    """Find a causal path between nodes."""
    path = causal_graph.find_causal_path(graph_id, source_node, target_node)
    return {
        "path_id": path.path_id,
        "source_node": path.source_node,
        "target_node": path.target_node,
        "nodes": path.nodes,
        "edges": path.edges,
        "length": path.length,
        "total_strength": path.total_strength,
        "causal_explanation": path.causal_explanation,
    }


@router.post("/api/v1/memora/causal/graphs/{graph_id}/counterfactual")
async def counterfactual_analysis(graph_id: str, payload: dict):
    """Perform counterfactual analysis."""
    analysis = causal_graph.counterfactual_analysis(
        graph_id=graph_id,
        query=payload["query"],
        intervention=payload["intervention"],
        actual_outcome=payload.get("actual_outcome", ""),
    )
    return analysis.__dict__


@router.get("/api/v1/memora/causal/graphs/{graph_id}/explain")
async def explain_outcome(graph_id: str, outcome_node: str):
    """Generate causal explanation for an outcome."""
    explanation = causal_graph.explain_outcome(graph_id, outcome_node)
    return {
        "explanation_id": explanation.explanation_id,
        "causes": explanation.causes,
        "root_causes": explanation.root_causes,
        "contributing_factors": explanation.contributing_factors,
        "explanation_text": explanation.explanation_text,
    }


@router.get("/api/v1/memora/causal/report")
async def get_report():
    """Get causal memory graph report."""
    return causal_graph.get_report()
