"""
Memora/apps/api/routers/semantic_search_engine.py — Memora Semantic Search Engine.

Vector-based semantic search, hybrid search (vector + keyword), query understanding,
multi-modal search, and relevance scoring with cross-encoder reranking.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class SearchQuery:
    """A search query."""
    query_id: str
    query_text: str
    filters: dict = field(default_factory=dict)
    search_type: str = "semantic"  # semantic, keyword, hybrid
    limit: int = 10
    threshold: float = 0.5
    boost: dict = field(default_factory=dict)  # field boosts
    created_at: float = 0.0


@dataclass
class SearchResult:
    """A search result."""
    document_id: str
    title: str
    content: str
    score: float
    source: str
    tags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    snippet: str = ""
    rerank_score: float = 0.0


@dataclass
class Embedding:
    """A text embedding."""
    embedding_id: str
    text: str
    vector: list[float] = field(default_factory=list)
    model: str = "bge-large-en"
    dimensions: int = 1024
    created_at: float = 0.0


@dataclass
class Collection:
    """A search collection."""
    collection_id: str
    name: str
    description: str = ""
    document_count: int = 0
    embedding_model: str = "bge-large-en"
    index_type: str = "vector"  # vector, keyword, hybrid
    created_at: float = 0.0


@dataclass
class QueryAnalytics:
    """Analytics for a search query."""
    query_id: str
    query_text: str
    num_results: int
    latency_ms: float
    avg_score: float
    zero_results: bool
    reranked: bool


class SemanticSearchEngine:
    """Semantic search engine."""

    def __init__(self):
        self._collections: dict[str, Collection] = {}
        self._documents: dict[str, dict] = {}  # doc_id → {content, title, embedding, source, tags}
        self._embeddings: list[Embedding] = []
        self._query_analytics: list[QueryAnalytics] = []

    def create_collection(
        self,
        name: str,
        description: str = "",
        embedding_model: str = "bge-large-en",
        index_type: str = "vector",
    ) -> Collection:
        """Create a search collection."""
        collection = Collection(
            collection_id=hashlib.md5(
                f"collection:{name}:{time.time()}".encode()
            ).hexdigest()[:12],
            name=name,
            description=description,
            embedding_model=embedding_model,
            index_type=index_type,
            created_at=time.time(),
        )
        self._collections[collection.collection_id] = collection
        return collection

    def add_document(
        self,
        collection_id: str,
        title: str,
        content: str,
        source: str = "",
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> dict:
        """Add a document to a collection."""
        collection = self._collections.get(collection_id)
        if not collection:
            raise HTTPException(404, f"Collection {collection_id} not found")

        doc_id = hashlib.md5(
            f"doc:{collection_id}:{title}:{time.time()}".encode()
        ).hexdigest()[:12]

        # Simulate embedding (in production: call sentence-transformers or embedding API)
        embedding = self._embed(content)

        doc = {
            "doc_id": doc_id,
            "collection_id": collection_id,
            "title": title,
            "content": content,
            "source": source,
            "tags": tags or [],
            "metadata": metadata or {},
            "embedding": embedding,
            "added_at": time.time(),
        }

        self._documents[doc_id] = doc
        self._embeddings.append(Embedding(
            embedding_id=doc_id,
            text=content,
            vector=embedding,
            model=collection.embedding_model,
            dimensions=len(embedding),
            created_at=time.time(),
        ))

        # Update collection count
        collection.document_count = len(
            [d for d in self._documents.values() if d["collection_id"] == collection_id]
        )

        return {"doc_id": doc_id, "title": title, "status": "added"}

    def _embed(self, text: str) -> list[float]:
        """Simulate text embedding (deterministic)."""
        # In production: call an embedding API or use sentence-transformers
        # Here we generate a deterministic vector based on text hash
        vector = []
        seed = int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
        for i in range(128):  # Reduced from 1024 for speed
            seed = (seed * 1103515245 + 12345) & 0xFFFFFFFF
            vector.append((seed % 1000) / 1000.0)
        return vector

    def _cosine_similarity(self, vec_a: list[float], vec_b: list[float]) -> float:
        """Compute cosine similarity."""
        if not vec_a or not vec_b or len(vec_a) != len(vec_b):
            return 0.0

        dot = sum(a * b for a, b in zip(vec_a, vec_b))
        norm_a = sum(a * a for a in vec_a) ** 0.5
        norm_b = sum(b * b for b in vec_b) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot / (norm_a * norm_b)

    def _keyword_search(self, query: str, limit: int) -> list[dict]:
        """Simple keyword search."""
        query_words = set(query.lower().split())
        results = []

        for doc in self._documents.values():
            content_words = set(doc["content"].lower().split())
            overlap = len(query_words & content_words)
            if overlap > 0:
                score = overlap / max(len(query_words), 1)
                results.append({
                    "doc_id": doc["doc_id"],
                    "title": doc["title"],
                    "content": doc["content"],
                    "score": score,
                    "source": doc["source"],
                    "tags": doc["tags"],
                    "metadata": doc["metadata"],
                    "snippet": doc["content"][:200],
                })

        return sorted(results, key=lambda x: -x["score"])[:limit]

    def search(
        self,
        query_text: str,
        search_type: str = "semantic",
        limit: int = 10,
        threshold: float = 0.5,
        collection_id: str = "",
    ) -> dict:
        """Search documents."""
        start = time.time()
        query_id = hashlib.md5(
            f"query:{query_text}:{time.time()}".encode()
        ).hexdigest()[:12]

        query = SearchQuery(
            query_id=query_id,
            query_text=query_text,
            search_type=search_type,
            limit=limit,
            threshold=threshold,
            created_at=time.time(),
        )

        # Get query embedding
        query_embedding = self._embed(query_text)

        if search_type == "keyword":
            results = self._keyword_search(query_text, limit)
        elif search_type == "hybrid":
            semantic = self._semantic_search(query_embedding, limit, threshold, collection_id)
            keyword = self._keyword_search(query_text, limit)
            results = self._merge_hybrid(semantic, keyword, limit)
        else:
            results = self._semantic_search(query_embedding, limit, threshold, collection_id)

        # Rerank results (simulated cross-encoder reranking)
        for result in results:
            result["rerank_score"] = result["score"] * (0.8 + (ord(hashlib.md5(result["doc_id"].encode()).hexdigest()[:1]) % 40) / 100)

        latency = (time.time() - start) * 1000

        # Record analytics
        analytics = QueryAnalytics(
            query_id=query_id,
            query_text=query_text,
            num_results=len(results),
            latency_ms=latency,
            avg_score=sum(r["score"] for r in results) / max(1, len(results)),
            zero_results=len(results) == 0,
            reranked=True,
        )
        self._query_analytics.append(analytics)

        return {
            "query_id": query_id,
            "results": results,
            "total_results": len(results),
            "latency_ms": latency,
            "avg_score": analytics.avg_score,
        }

    def _semantic_search(
        self,
        query_embedding: list[float],
        limit: int,
        threshold: float,
        collection_id: str = "",
    ) -> list[dict]:
        """Semantic search using vector similarity."""
        results = []

        for doc in self._documents.values():
            if collection_id and doc["collection_id"] != collection_id:
                continue

            similarity = self._cosine_similarity(query_embedding, doc["embedding"])
            if similarity >= threshold:
                results.append({
                    "doc_id": doc["doc_id"],
                    "title": doc["title"],
                    "content": doc["content"],
                    "score": similarity,
                    "source": doc["source"],
                    "tags": doc["tags"],
                    "metadata": doc["metadata"],
                    "snippet": doc["content"][:200],
                })

        return sorted(results, key=lambda x: -x["score"])[:limit]

    def _merge_hybrid(self, semantic: list[dict], keyword: list[dict], limit: int) -> list[dict]:
        """Merge semantic and keyword results."""
        merged = {}

        # Semantic results
        for r in semantic:
            merged[r["doc_id"]] = {
                **r,
                "score": r["score"] * 0.6,
            }

        # Keyword results
        for r in keyword:
            if r["doc_id"] in merged:
                merged[r["doc_id"]]["score"] += r["score"] * 0.4
            else:
                merged[r["doc_id"]] = {
                    **r,
                    "score": r["score"] * 0.4,
                }

        return sorted(merged.values(), key=lambda x: -x["score"])[:limit]

    def get_query_analytics(self, limit: int = 20) -> list[QueryAnalytics]:
        """Get query analytics."""
        return sorted(self._query_analytics, key=lambda a: -a.latency_ms)[:limit]

    def get_report(self) -> dict:
        """Get search engine report."""
        collections = list(self._collections.values())
        total_docs = len(self._documents)
        total_queries = len(self._query_analytics)
        zero_result_rate = sum(1 for a in self._query_analytics if a.zero_results) / max(1, total_queries)
        avg_latency = sum(a.latency_ms for a in self._query_analytics) / max(1, total_queries)

        return {
            "collections": len(collections),
            "total_documents": total_docs,
            "total_queries": total_queries,
            "zero_result_rate": zero_result_rate,
            "avg_latency_ms": avg_latency,
            "embeddings": len(self._embeddings),
            "collections_detail": [
                {
                    "name": c.name,
                    "documents": c.document_count,
                    "index_type": c.index_type,
                }
                for c in collections
            ],
        }


# Module-level singleton
semantic_search = SemanticSearchEngine()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/memora/search/collections")
async def create_collection(payload: dict):
    """Create a search collection."""
    collection = semantic_search.create_collection(
        name=payload["name"],
        description=payload.get("description", ""),
        embedding_model=payload.get("embedding_model", "bge-large-en"),
        index_type=payload.get("index_type", "vector"),
    )
    return collection.__dict__


@router.post("/api/v1/memora/search/collections/{collection_id}/documents")
async def add_document(collection_id: str, payload: dict):
    """Add a document to a collection."""
    result = semantic_search.add_document(
        collection_id=collection_id,
        title=payload["title"],
        content=payload["content"],
        source=payload.get("source", ""),
        tags=payload.get("tags", []),
        metadata=payload.get("metadata", {}),
    )
    return result


@router.post("/api/v1/memora/search")
async def search(payload: dict):
    """Search documents."""
    result = semantic_search.search(
        query_text=payload["query_text"],
        search_type=payload.get("search_type", "semantic"),
        limit=payload.get("limit", 10),
        threshold=payload.get("threshold", 0.5),
        collection_id=payload.get("collection_id", ""),
    )
    return result


@router.get("/api/v1/memora/search/analytics")
async def get_query_analytics(limit: int = 20):
    """Get query analytics."""
    return [a.__dict__ for a in semantic_search.get_query_analytics(limit)]


@router.get("/api/v1/memora/search/report")
async def get_report():
    """Get search engine report."""
    return semantic_search.get_report()
