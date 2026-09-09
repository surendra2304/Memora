"""
Vector Store Adapter for Memora
Interfaces with Qdrant for dense semantic embeddings and similarity search.
"""
import math
import logging
from typing import List, Dict, Any, Optional
from core.config import settings

logger = logging.getLogger(__name__)

class VectorUnavailableError(Exception):
    """Raised when vector database operations fail or are unavailable in production."""
    pass

class VectorSearchResult:
    def __init__(self, memory_id: str, score: float, payload: Dict[str, Any]):
        self.memory_id = memory_id
        self.score = score
        self.payload = payload

    def __repr__(self):
        return f"<VectorSearchResult(id={self.memory_id}, score={self.score:.4f})>"

class QdrantVectorAdapter:
    def __init__(self, url: Optional[str] = None, collection_name: Optional[str] = None):
        self.url = url or getattr(settings, "QDRANT_URL", "http://localhost:6333")
        self.collection_name = collection_name or getattr(settings, "QDRANT_COLLECTION", "memora_memories")
        self._client = None
        self._initialized = False
        self._mock_store: Dict[str, Dict[str, Any]] = {}

    def connect(self):
        # In cloud without dedicated Qdrant instance, operate in internal vector mode
        if "localhost" in self.url and getattr(settings, "MEMORA_ENV", "").lower() == "production":
            logger.info("Operating in internal dense vector mode.")
            self._initialized = False
            return

        try:
            from qdrant_client import QdrantClient
            self._client = QdrantClient(url=self.url, timeout=2.0, check_compatibility=False)
            self._initialized = True
            logger.info(f"Connected to Qdrant at {self.url}")
        except Exception as e:
            logger.info(f"Qdrant vector database operating in internal fallback mode: {e}")
            self._initialized = False

    def is_production(self) -> bool:
        return getattr(settings, "MEMORA_ENV", "development").lower() == "production"

    def upsert_embedding(
        self,
        memory_id: str,
        vector: List[float],
        arg3: Any = None,
        arg4: Any = None,
        tenant_id: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> bool:
        if not memory_id:
            raise ValueError("memory_id is required for vector upsert.")

        resolved_tenant = "default"
        stored_payload: Dict[str, Any] = {}

        if isinstance(arg3, dict):
            stored_payload = dict(arg3)
            if isinstance(arg4, str):
                resolved_tenant = arg4
        elif isinstance(arg3, str):
            resolved_tenant = arg3
            if isinstance(arg4, dict):
                stored_payload = dict(arg4)

        if tenant_id is not None:
            resolved_tenant = tenant_id
        if payload is not None:
            stored_payload = dict(payload)

        stored_payload["tenant_id"] = resolved_tenant
        stored_payload["memory_id"] = memory_id

        if not self._initialized:
            if self.is_production():
                logger.error(f"VECTOR_UNAVAILABLE: Qdrant client unavailable in production for memory '{memory_id}'")
                raise VectorUnavailableError(f"VECTOR_UNAVAILABLE: Qdrant vector store is offline in production for tenant '{tenant_id}'.")
            self._mock_store[memory_id] = {"vector": vector, "payload": stored_payload, "tenant_id": resolved_tenant}
            return True

        try:
            from qdrant_client.http.models import PointStruct
            point = PointStruct(id=memory_id, vector=vector, payload=stored_payload)
            self._client.upsert(collection_name=self.collection_name, points=[point])
            self._mock_store[memory_id] = {"vector": vector, "payload": stored_payload, "tenant_id": resolved_tenant}
            return True
        except Exception as e:
            logger.error(f"Failed to upsert vector to Qdrant: {e}")
            if self.is_production():
                raise VectorUnavailableError(f"VECTOR_UNAVAILABLE: {e}")
            self._mock_store[memory_id] = {"vector": vector, "payload": stored_payload, "tenant_id": resolved_tenant}
            return False

    def delete_embedding(self, memory_id: str, tenant_id: str = "default") -> bool:
        if memory_id in self._mock_store:
            del self._mock_store[memory_id]

        if not self._initialized:
            if self.is_production():
                logger.error(f"VECTOR_UNAVAILABLE: Qdrant client unavailable in production for delete of '{memory_id}'")
                return False
            return True

        try:
            from qdrant_client.http.models import PointIdsList
            self._client.delete(
                collection_name=self.collection_name,
                points_selector=PointIdsList(points=[memory_id])
            )
            return True
        except Exception as e:
            logger.error(f"Failed to delete vector from Qdrant: {e}")
            return False

    @staticmethod
    def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
        if len(v1) != len(v2):
            return 0.0
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = math.sqrt(sum(a * a for a in v1))
        norm2 = math.sqrt(sum(b * b for b in v2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    def search_similarity(
        self,
        query_vector: List[float],
        tenant_id: str = "default",
        limit: int = 10,
        score_threshold: float = 0.50
    ) -> List[VectorSearchResult]:
        if self._initialized:
            try:
                from qdrant_client.http.models import Filter, FieldCondition, MatchValue
                query_filter = Filter(
                    must=[
                        FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
                    ]
                )
                results = self._client.search(
                    collection_name=self.collection_name,
                    query_vector=query_vector,
                    query_filter=query_filter,
                    limit=limit,
                    score_threshold=score_threshold
                )
                return [
                    VectorSearchResult(
                        memory_id=str(hit.id),
                        score=hit.score,
                        payload=hit.payload or {}
                    )
                    for hit in results
                ]
            except Exception as e:
                logger.error(f"Vector search failed on client: {e}")

        # In-memory cosine search fallback (strictly filtered by tenant_id)
        scored = []
        for mem_id, data in self._mock_store.items():
            if data.get("tenant_id", data.get("payload", {}).get("tenant_id", "default")) != tenant_id:
                continue
            sim = self._cosine_similarity(query_vector, data["vector"])
            if sim >= score_threshold:
                scored.append(VectorSearchResult(memory_id=mem_id, score=round(sim, 4), payload=data.get("payload", {})))

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:limit]

vector_adapter = QdrantVectorAdapter()