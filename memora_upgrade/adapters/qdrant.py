from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence, Any


@dataclass(frozen=True, slots=True)
class QdrantPoint:
    tenant_id: str
    memory_id: str
    vector: tuple[float, ...]
    payload: dict[str, Any]


class TenantScopedQdrantAdapter:
    """Adapter contract emphasizing tenant filters on every vector operation."""

    def __init__(self, client: Any, collection: str):
        self.client = client
        self.collection = collection

    def upsert(self, point: QdrantPoint) -> None:
        from qdrant_client.http.models import PointStruct
        payload = dict(point.payload)
        payload["tenant_id"] = point.tenant_id
        self.client.upsert(
            collection_name=self.collection,
            points=[PointStruct(id=point.memory_id, vector=list(point.vector), payload=payload)],
        )

    def search(self, tenant_id: str, vector: Sequence[float], limit: int):
        query_filter = {"must": [{"key": "tenant_id", "match": {"value": tenant_id}}]}
        return self.client.search(
            collection_name=self.collection,
            query_vector=list(vector),
            query_filter=query_filter,
            limit=limit,
        )

    def delete(self, tenant_id: str, memory_id: str) -> None:
        query_filter = {
            "must": [
                {"key": "tenant_id", "match": {"value": tenant_id}},
                {"key": "memory_id", "match": {"value": memory_id}},
            ]
        }
        self.client.delete(collection_name=self.collection, points_selector=query_filter)
