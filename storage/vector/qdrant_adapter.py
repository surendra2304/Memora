"""
Vector Store Adapter for Memora
Interfaces with Qdrant for dense semantic embeddings and similarity search.
"""
import math
import logging
from typing import List, Dict, Any, Optional
from core.config import settings

logger = logging.getLogger(__name__)

class VectorSearchResult:
    def __init__(self, memory_id: str, score: float, payload: Dict[str, Any]):
        self.memory_id = memory_id
        self.score = score
        self.payload = payload

    def __repr__(self):
        return f"<VectorSearchResult(id={self.memory_id}, score={self.score:.4f})>"

class QdrantVectorAdapter:
    def __init__(self, url: Optional[str] = None, collection_name: Optional[str] = None):
        self.url = url or settings.QDRANT_URL
        self.collection_name = collection_name or settings.QDRANT_COLLECTION
        self._client = None
        self._initialized = False
        self._mock_store: Dict[str, Dict[str, Any]] = {}

    def connect(self):
        try:
            from qdrant_client import QdrantClient
            self._client = QdrantClient(url=self.url, timeout=3.0)
            self._initialized = True
            logger.info(f"Connected to Qdrant at {self.url}")
        except Exception as e:
            logger.warning(f"Could not connect to Qdrant vector database: {e}. Vector operations will operate in mock/fallback mode.")
            self._initialized = False

    def upsert_embedding(
        self,
        memory_id: str,
        vector: List[float],
        payload: Optional[Dict[str, Any]] = None
    ) -> bool:
        self._mock_store[memory_id] = {"vector": vector, "payload": payload or {}}
        if not self._initialized:
            return True
        try:
            from qdrant_client.http.models import PointStruct
            point = PointStruct(id=memory_id, vector=vector, payload=payload or {})
            self._client.upsert(collection_name=self.collection_name, points=[point])
            return True
        except Exception as e:
            logger.error(f"Failed to upsert vector to Qdrant: {e}")
            return False

    def delete_embedding(self, memory_id: str) -> bool:
        if memory_id in self._mock_store:
            del self._mock_store[memory_id]
        if not self._initialized:
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
        limit: int = 10,
        score_threshold: float = 0.50
    ) -> List[VectorSearchResult]:
        if self._initialized:
            try:
                results = self._client.search(
                    collection_name=self.collection_name,
                    query_vector=query_vector,
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

        # In-memory cosine search fallback
        scored = []
        for mem_id, data in self._mock_store.items():
            sim = self._cosine_similarity(query_vector, data["vector"])
            if sim >= score_threshold:
                scored.append(VectorSearchResult(memory_id=mem_id, score=round(sim, 4), payload=data.get("payload", {})))

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:limit]

vector_adapter = QdrantVectorAdapter()