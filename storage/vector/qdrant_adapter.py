"""
Vector Store Adapter for Memora
Interfaces with Qdrant for dense semantic embeddings and similarity search.
"""
import logging
from typing import List, Dict, Any, Optional
from core.config import settings

logger = logging.getLogger(__name__)

class VectorSearchResult:
    def __init__(self, memory_id: str, score: float, payload: Dict[str, Any]):
        self.memory_id = memory_id
        self.score = score
        self.payload = payload

class QdrantVectorAdapter:
    def __init__(self, url: Optional[str] = None, collection_name: Optional[str] = None):
        self.url = url or settings.QDRANT_URL
        self.collection_name = collection_name or settings.QDRANT_COLLECTION
        self._client = None
        self._initialized = False

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
        if not self._initialized:
            return True  # Fallback gracefully
        try:
            from qdrant_client.http.models import PointStruct
            point = PointStruct(id=memory_id, vector=vector, payload=payload or {})
            self._client.upsert(collection_name=self.collection_name, points=[point])
            return True
        except Exception as e:
            logger.error(f"Failed to upsert vector to Qdrant: {e}")
            return False

    def search_similarity(
        self,
        query_vector: List[float],
        limit: int = 10,
        score_threshold: float = 0.70
    ) -> List[VectorSearchResult]:
        if not self._initialized:
            return []
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
            logger.error(f"Vector search failed: {e}")
            return []

vector_adapter = QdrantVectorAdapter()