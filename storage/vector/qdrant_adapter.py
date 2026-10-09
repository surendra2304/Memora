"""
Vector Store Adapter for Memora
Interfaces with Qdrant for dense semantic embeddings and similarity search.
"""
import bisect
import math
import logging
import os
from typing import List, Dict, Any, Optional
from core.config import settings
from core.resilience.circuit_breaker import CircuitOpenError, circuit_registry

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
        # Without a breaker, every request paid the full Qdrant connection
        # timeout once the server went away. Three consecutive failures now open
        # the circuit for 30s so calls short-circuit instead.
        self._breaker = circuit_registry.get(
            "qdrant",
            failure_threshold=int(os.getenv("MEMORA_QDRANT_FAILURE_THRESHOLD", "3")),
            recovery_timeout=float(os.getenv("MEMORA_QDRANT_RECOVERY_SECONDS", "30")),
        )

    def readiness(self) -> Dict[str, Any]:
        """Describe whether vector operations can actually serve this process.

        The exact key set here is part of the contract /health returns verbatim
        and is asserted by tests/test_health_readiness_truth.py, so breaker state
        is exposed separately through circuit_state() rather than added here.
        """
        if self._initialized:
            return {"status": "connected", "available": True, "backend": "qdrant"}
        if self.is_production():
            return {"status": "unavailable", "available": False, "backend": "qdrant"}
        return {"status": "in_memory", "available": True, "backend": "process_local"}

    def circuit_state(self) -> Dict[str, Any]:
        """Breaker state for this dependency, kept out of readiness()."""
        return self._breaker.snapshot()

    def connect(self):
        # In cloud without dedicated Qdrant instance, operate in internal vector mode
        if "localhost" in self.url and self.is_production():
            logger.info("Operating in internal dense vector mode.")
            self._initialized = False
            return

        try:
            from qdrant_client import QdrantClient
            client = QdrantClient(url=self.url, timeout=1.0, check_compatibility=False)
            client.get_collections()
            self._client = client
            self._initialized = True
            logger.info(f"Connected to Qdrant at {self.url}")
        except Exception as e:
            logger.info(f"Qdrant vector database operating in internal fallback mode: {e}")
            self._initialized = False

    def is_production(self) -> bool:
        # Render and other deployment manifests configure ENVIRONMENT. Keep
        # vector durability decisions aligned with relational storage, which
        # treats this variable as authoritative over the local settings file.
        environment = os.getenv("ENVIRONMENT", "") or getattr(settings, "MEMORA_ENV", "development")
        return environment.lower() == "production"

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

        def _do_upsert():
            from qdrant_client.http.models import PointStruct
            point = PointStruct(id=memory_id, vector=vector, payload=stored_payload)
            self._client.upsert(collection_name=self.collection_name, points=[point])

        try:
            self._breaker.call(_do_upsert)
            self._mock_store[memory_id] = {"vector": vector, "payload": stored_payload, "tenant_id": resolved_tenant}
            return True
        except CircuitOpenError as e:
            # The backend is already known-down; do not pay its timeout again.
            logger.warning(f"Qdrant circuit open, skipping upsert for '{memory_id}': {e}")
            if self.is_production():
                raise VectorUnavailableError(f"VECTOR_UNAVAILABLE: circuit open ({e})")
            self._mock_store[memory_id] = {"vector": vector, "payload": stored_payload, "tenant_id": resolved_tenant}
            return False
        except Exception as e:
            logger.error(f"Failed to upsert vector to Qdrant: {e}")
            if self.is_production():
                raise VectorUnavailableError(f"VECTOR_UNAVAILABLE: {e}")
            self._mock_store[memory_id] = {"vector": vector, "payload": stored_payload, "tenant_id": resolved_tenant}
            return False

    @staticmethod
    def _entry_tenant_id(entry: Dict[str, Any]) -> Optional[str]:
        """Return an unambiguous tenant label; missing/conflicting labels fail closed."""
        if not isinstance(entry, dict):
            return None
        payload = entry.get("payload")
        payload_tenant = payload.get("tenant_id") if isinstance(payload, dict) else None
        top_level_tenant = entry.get("tenant_id")
        if (
            top_level_tenant is not None
            and payload_tenant is not None
            and top_level_tenant != payload_tenant
        ):
            return None
        return top_level_tenant if top_level_tenant is not None else payload_tenant

    def delete_embedding(self, memory_id: str, tenant_id: str = "default") -> bool:
        entry = self._mock_store.get(memory_id)
        if entry is not None:
            if self._entry_tenant_id(entry) != tenant_id:
                logger.warning(
                    "Refusing vector delete for '%s': tenant metadata does not match '%s'.",
                    memory_id,
                    tenant_id,
                )
                return False
            del self._mock_store[memory_id]

        if not self._initialized:
            if self.is_production():
                logger.error(f"VECTOR_UNAVAILABLE: Qdrant client unavailable in production for delete of '{memory_id}'")
                return False
            return True

        def _do_delete():
            from qdrant_client.http.models import (
                FieldCondition,
                Filter,
                FilterSelector,
                HasIdCondition,
                MatchValue,
            )
            tenant_filter = Filter(
                must=[
                    FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                    HasIdCondition(has_id=[memory_id]),
                ]
            )
            self._client.delete(
                collection_name=self.collection_name,
                points_selector=FilterSelector(filter=tenant_filter),
            )

        try:
            self._breaker.call(_do_delete)
            return True
        except CircuitOpenError as e:
            logger.warning(f"Qdrant circuit open, skipping delete for '{memory_id}': {e}")
            return False
        except Exception as e:
            logger.error(f"Failed to delete vector from Qdrant: {e}")
            if self.is_production():
                return False
            return True

    def list_memory_ids(
        self,
        tenant_id: str = "default",
        limit: int = 500,
        offset: Any = None,
    ) -> tuple[List[str], Any]:
        """Return one bounded, tenant-filtered page of vector IDs.

        Production reconciliation must inspect Qdrant itself rather than the
        process-local mirror, which is empty after a restart. The in-memory
        adapter uses the same last-point-ID cursor pattern for deterministic tests.
        """
        if not 1 <= limit <= 1000:
            raise ValueError("Vector reconciliation page size must be between 1 and 1000")

        if not self._initialized:
            if self.is_production():
                raise VectorUnavailableError("Qdrant is unavailable for vector reconciliation")
            ids = sorted(
                memory_id
                for memory_id, entry in self._mock_store.items()
                if self._entry_tenant_id(entry) == tenant_id
            )
            # Use the last point ID as the cursor, like Qdrant, so removing a
            # reconciled page does not shift an integer index past later rows.
            start = bisect.bisect_right(ids, str(offset)) if offset is not None else 0
            page = ids[start:start + limit]
            next_offset = page[-1] if start + len(page) < len(ids) and page else None
            return page, next_offset

        def _do_scroll():
            from qdrant_client.http.models import Filter, FieldCondition, MatchValue

            tenant_filter = Filter(
                must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]
            )
            return self._client.scroll(
                collection_name=self.collection_name,
                scroll_filter=tenant_filter,
                limit=limit,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )

        try:
            points, next_offset = self._breaker.call(_do_scroll)
            return [str(point.id) for point in points], next_offset
        except Exception as exc:
            logger.warning("Qdrant vector reconciliation scan failed (%s)", type(exc).__name__)
            raise VectorUnavailableError("Qdrant vector listing is temporarily unavailable") from exc

    @staticmethod
    def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
        if len(v1) != len(v2):
            return 0.0
        dot = sum(a * b for a, b in zip(v1, v2, strict=True))
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

        if self.is_production():
            raise VectorUnavailableError("VECTOR_UNAVAILABLE: Qdrant vector store is offline in production.")

        # In-memory cosine search fallback (strictly filtered by tenant_id)
        scored = []
        for mem_id, data in self._mock_store.items():
            if self._entry_tenant_id(data) != tenant_id:
                continue
            sim = self._cosine_similarity(query_vector, data["vector"])
            if sim >= score_threshold:
                scored.append(VectorSearchResult(memory_id=mem_id, score=round(sim, 4), payload=data.get("payload", {})))

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored[:limit]

vector_adapter = QdrantVectorAdapter()
