from __future__ import annotations
from dataclasses import dataclass
from collections import defaultdict, deque
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Edge:
    source: str
    target: str
    relationship: str
    weight: float = 1.0


class MemoryGraph:
    """Tenant-aware memory/entity graph for entity-centric and multi-hop recall."""

    def __init__(self):
        self._edges: dict[str, list[Edge]] = defaultdict(list)
        self._tenant: dict[str, str] = {}

    def add_node(self, node_id: str, tenant_id: str) -> None:
        existing = self._tenant.get(node_id)
        if existing and existing != tenant_id:
            raise ValueError("cross-tenant node collision")
        self._tenant[node_id] = tenant_id

    def add_edge(self, edge: Edge, tenant_id: str) -> None:
        self.add_node(edge.source, tenant_id)
        self.add_node(edge.target, tenant_id)
        self._edges[edge.source].append(edge)

    def neighbors(self, node_id: str, tenant_id: str, depth: int = 1) -> set[str]:
        if self._tenant.get(node_id) != tenant_id:
            return set()
        seen = {node_id}
        q = deque([(node_id, 0)])
        while q:
            current, d = q.popleft()
            if d >= depth:
                continue
            for edge in self._edges.get(current, []):
                if self._tenant.get(edge.target) != tenant_id:
                    continue
                if edge.target not in seen:
                    seen.add(edge.target)
                    q.append((edge.target, d + 1))
        seen.remove(node_id)
        return seen

    def boost(self, memory_entity_ids: Iterable[str], query_entity_ids: set[str], tenant_id: str) -> float:
        values = set(memory_entity_ids)
        score = 0.0
        for entity in values:
            if self._tenant.get(entity) != tenant_id:
                continue
            if entity in query_entity_ids:
                score += 1.0
            elif self.neighbors(entity, tenant_id, depth=1) & query_entity_ids:
                score += 0.5
        return min(1.0, score / max(1, len(query_entity_ids)))
