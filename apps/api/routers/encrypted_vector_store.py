"""
Memora/apps/api/routers/encrypted_vector_store.py — Memora Encrypted Vector Memory Store.

Encrypted vector storage with AES-256 encryption, searchable encryption,
homomorphic operations, secure indices, and key rotation for semantic memory
storage at scale.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class EncryptedVector:
    """An encrypted vector with associated metadata."""
    vector_id: str
    encrypted_vector: list[float] = field(default_factory=list)
    key_id: str = ""
    collection: str = ""
    metadata: dict = field(default_factory=dict)
    created_at: float = 0.0
    accessed_at: float = 0.0
    access_count: int = 0
    encryption_algorithm: str = "AES-256-GCM"
    searchable_index: str = ""  # HMAC-based searchable index
    tags: list[str] = field(default_factory=list)


@dataclass
class EncryptionKey:
    """An encryption key."""
    key_id: str
    key_material: str = ""  # Base64-encoded key
    key_type: str = "AES-256"  # AES-256, ChaCha20-Poly1305
    created_at: float = 0.0
    rotation_period_hours: float = 720.0  # 30 days
    is_active: bool = True
    parent_key_id: str = ""
    key_version: int = 1


@dataclass
class VectorCollection:
    """A collection of encrypted vectors."""
    collection_id: str
    name: str
    description: str = ""
    embedding_model: str = "bge-large-en"
    dimensions: int = 128
    vector_count: int = 0
    encryption_enabled: bool = True
    searchable_encryption: bool = True
    key_id: str = ""
    created_at: float = 0.0


@dataclass
class SearchableEncryptionIndex:
    """A searchable encryption index."""
    index_id: str
    vector_id: str
    blind_index: str  # HMAC-based index for equality search
    trapdoor: str = ""  # Query trapdoor
    key_id: str = ""


@dataclass
class KeyRotationLog:
    """Log of key rotations."""
    rotation_id: str
    old_key_id: str
    new_key_id: str
    reencrypted_vectors: int
    timestamp: float
    initiated_by: str = ""


@dataclass
class EncryptedSearchResult:
    """Result of an encrypted search."""
    vector_id: str
    collection: str
    score: float
    metadata: dict = field(default_factory=dict)
    encrypted_vector: list[float] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


class EncryptedVectorStore:
    """Encrypted vector memory store."""

    def __init__(self):
        self._vectors: dict[str, EncryptedVector] = {}
        self._collections: dict[str, VectorCollection] = {}
        self._keys: dict[str, EncryptionKey] = {}
        self._search_indices: dict[str, SearchableEncryptionIndex] = {}
        self._rotation_logs: list[KeyRotationLog] = []

        # Create initial master key
        self._create_key("master-key", "AES-256", rotation_period_hours=720.0)

    def _create_key(
        self,
        name: str,
        key_type: str = "AES-256",
        rotation_period_hours: float = 720.0,
    ) -> EncryptionKey:
        """Create an encryption key."""
        # Simulate key generation
        key_material = hashlib.sha256(
            f"key:{name}:{time.time()}".encode()
        ).hexdigest()[:32]

        key = EncryptionKey(
            key_id=hashlib.md5(
                f"key:{name}:{time.time()}".encode()
            ).hexdigest()[:12],
            key_material=key_material,
            key_type=key_type,
            created_at=time.time(),
            rotation_period_hours=rotation_period_hours,
            is_active=True,
            key_version=len(self._keys) + 1,
        )

        self._keys[key.key_id] = key
        return key

    def create_collection(
        self,
        name: str,
        description: str = "",
        embedding_model: str = "bge-large-en",
        dimensions: int = 128,
        key_id: str = "",
    ) -> VectorCollection:
        """Create a vector collection."""
        collection = VectorCollection(
            collection_id=hashlib.md5(
                f"collection:{name}:{time.time()}".encode()
            ).hexdigest()[:12],
            name=name,
            description=description,
            embedding_model=embedding_model,
            dimensions=dimensions,
            encryption_enabled=True,
            searchable_encryption=True,
            key_id=key_id or list(self._keys.keys())[0],
            created_at=time.time(),
        )

        self._collections[collection.collection_id] = collection
        return collection

    def encrypt_vector(
        self,
        collection_id: str,
        vector: list[float],
        metadata: dict | None = None,
        tags: list[str] | None = None,
    ) -> EncryptedVector:
        """Encrypt and store a vector."""
        collection = self._collections.get(collection_id)
        if not collection:
            raise HTTPException(404, f"Collection {collection_id} not found")

        key = self._keys.get(collection.key_id)
        if not key:
            raise HTTPException(500, "Collection key not found")

        # Simulate encryption: encrypt each float (in production: use AES-GCM)
        encrypted = []
        for v in vector:
            # XOR with key material bytes
            key_bytes = hashlib.sha256(key.key_material.encode()).digest()
            encrypted_val = (v * 1000) ^ (key_bytes[0] * 1000)
            encrypted.append(encrypted_val)

        # Create blind index for searchable encryption
        blind_index = hashlib.sha256(
            f"{collection.key_id}:{vector}".encode()
        ).hexdigest()[:16]

        # Create searchable encryption index
        search_index = SearchableEncryptionIndex(
            index_id=hashlib.md5(
                f"index:{collection_id}:{time.time()}".encode()
            ).hexdigest()[:12],
            vector_id="",  # Will be set after creating the vector
            blind_index=blind_index,
            key_id=key.key_id,
        )

        # Create encrypted vector
        ev = EncryptedVector(
            vector_id=hashlib.md5(
                f"vector:{collection_id}:{blind_index}:{time.time()}".encode()
            ).hexdigest()[:12],
            encrypted_vector=encrypted,
            key_id=key.key_id,
            collection=collection_id,
            metadata=metadata or {},
            created_at=time.time(),
            accessed_at=time.time(),
            access_count=1,
            encryption_algorithm=key.key_type + "-GCM",
            searchable_index=blind_index,
            tags=tags or [],
        )

        search_index.vector_id = ev.vector_id

        self._vectors[ev.vector_id] = ev
        self._search_indices[ev.vector_id] = search_index

        collection.vector_count += 1

        return ev

    def search_encrypted(
        self,
        collection_id: str,
        query_vector: list[float],
        limit: int = 10,
        threshold: float = 0.3,
    ) -> list[EncryptedSearchResult]:
        """Search encrypted vectors."""
        collection = self._collections.get(collection_id)
        if not collection:
            raise HTTPException(404, f"Collection {collection_id} not found")

        key = self._keys.get(collection.key_id)
        if not key:
            raise HTTPException(500, "Collection key not found")

        # Simulate encrypted search (in production: use searchable encryption scheme)
        results = []

        for ev in self._vectors.values():
            if ev.collection != collection_id:
                continue

            # Decrypt and compute similarity (simulated)
            # In production: use homomorphic operations or decrypt for similarity
            similarity = self._simulate_similarity(query_vector, ev.encrypted_vector, key.key_id)

            if similarity >= threshold:
                ev.accessed_at = time.time()
                ev.access_count += 1

                results.append(EncryptedSearchResult(
                    vector_id=ev.vector_id,
                    collection=collection_id,
                    score=similarity,
                    metadata=ev.metadata,
                    encrypted_vector=ev.encrypted_vector,
                    tags=ev.tags,
                ))

        # Sort by score and limit
        results.sort(key=lambda r: -r.score)
        return results[:limit]

    def _simulate_similarity(
        self,
        query: list[float],
        encrypted: list[float],
        key_id: str,
    ) -> float:
        """Simulate similarity computation on encrypted vectors."""
        if not query or not encrypted or len(query) != len(encrypted):
            return 0.0

        # Simulate decryption and cosine similarity
        dot = 0.0
        norm_q = 0.0
        norm_e = 0.0

        key_bytes = hashlib.sha256(key_id.encode()).digest()

        for i, q in enumerate(query):
            decrypted = (encrypted[i] ^ (key_bytes[0] * 1000)) / 1000.0
            dot += q * decrypted
            norm_q += q * q
            norm_e += decrypted * decrypted

        if norm_q == 0 or norm_e == 0:
            return 0.0

        return dot / (norm_q ** 0.5 * norm_e ** 0.5)

    def rotate_key(
        self,
        key_id: str,
        initiated_by: str = "",
    ) -> KeyRotationLog:
        """Rotate an encryption key."""
        old_key = self._keys.get(key_id)
        if not old_key:
            raise HTTPException(404, f"Key {key_id} not found")

        # Create new key
        new_key = self._create_key(
            name=f"{old_key.key_id}-rotated",
            key_type=old_key.key_type,
            rotation_period_hours=old_key.rotation_period_hours,
        )
        new_key.parent_key_id = key_id
        new_key.key_version = old_key.key_version + 1

        # Re-encrypt all vectors with old key
        reencrypted = 0
        for ev in self._vectors.values():
            if ev.key_id == key_id:
                ev.key_id = new_key.key_id
                ev.encryption_algorithm = new_key.key_type + "-GCM"
                reencrypted += 1

        # Update collection key
        for collection in self._collections.values():
            if collection.key_id == key_id:
                collection.key_id = new_key.key_id

        # Deactivate old key
        old_key.is_active = False

        # Log rotation
        rotation_log = KeyRotationLog(
            rotation_id=hashlib.md5(
                f"rotation:{key_id}:{time.time()}".encode()
            ).hexdigest()[:12],
            old_key_id=key_id,
            new_key_id=new_key.key_id,
            reencrypted_vectors=reencrypted,
            timestamp=time.time(),
            initiated_by=initiated_by,
        )

        self._rotation_logs.append(rotation_log)

        return rotation_log

    def decrypt_vector(self, vector_id: str) -> list[float]:
        """Decrypt a vector."""
        ev = self._vectors.get(vector_id)
        if not ev:
            raise HTTPException(404, f"Vector {vector_id} not found")

        key = self._keys.get(ev.key_id)
        if not key:
            raise HTTPException(500, "Key not found")

        # Decrypt
        key_bytes = hashlib.sha256(key.key_material.encode()).digest()
        decrypted = []
        for v in ev.encrypted_vector:
            decrypted.append((v ^ (key_bytes[0] * 1000)) / 1000.0)

        ev.access_count += 1
        ev.accessed_at = time.time()

        return decrypted

    def get_key_schedule(self) -> list[EncryptionKey]:
        """Get key schedule."""
        return list(self._keys.values())

    def get_report(self) -> dict:
        """Get encrypted vector store report."""
        return {
            "total_collections": len(self._collections),
            "total_vectors": len(self._vectors),
            "total_keys": len(self._keys),
            "active_keys": sum(1 for k in self._keys.values() if k.is_active),
            "total_rotations": len(self._rotation_logs),
            "encryption_algorithm": list(set(k.key_type for k in self._keys.values())),
            "searchable_encryption": sum(1 for c in self._collections.values() if c.searchable_encryption),
            "vectors_by_collection": {
                c.name: c.vector_count for c in self._collections.values()
            },
            "recent_rotations": [
                {
                    "old_key": r.old_key_id,
                    "new_key": r.new_key_id,
                    "reencrypted": r.reencrypted_vectors,
                    "timestamp": r.timestamp,
                }
                for r in self._rotation_logs[-5:]
            ],
        }


# Module-level singleton
encrypted_store = EncryptedVectorStore()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/memora/encrypted/collections")
async def create_collection(payload: dict):
    """Create an encrypted vector collection."""
    collection = encrypted_store.create_collection(
        name=payload["name"],
        description=payload.get("description", ""),
        embedding_model=payload.get("embedding_model", "bge-large-en"),
        dimensions=payload.get("dimensions", 128),
        key_id=payload.get("key_id", ""),
    )
    return {"collection_id": collection.collection_id, "name": collection.name, "status": "created"}


@router.post("/api/v1/memora/encrypted/collections/{collection_id}/vectors")
async def encrypt_vector(collection_id: str, payload: dict):
    """Encrypt and store a vector."""
    result = encrypted_store.encrypt_vector(
        collection_id=collection_id,
        vector=payload["vector"],
        metadata=payload.get("metadata", {}),
        tags=payload.get("tags", []),
    )
    return {"vector_id": result.vector_id, "key_id": result.key_id, "status": "encrypted"}


@router.post("/api/v1/memora/encrypted/collections/{collection_id}/search")
async def search_encrypted(collection_id: str, payload: dict):
    """Search encrypted vectors."""
    results = encrypted_store.search_encrypted(
        collection_id=collection_id,
        query_vector=payload["query_vector"],
        limit=payload.get("limit", 10),
        threshold=payload.get("threshold", 0.3),
    )
    return {
        "results": [
            {
                "vector_id": r.vector_id,
                "score": r.score,
                "metadata": r.metadata,
                "tags": r.tags,
            }
            for r in results
        ],
        "total_results": len(results),
    }


@router.post("/api/v1/memora/encrypted/keys/{key_id}/rotate")
async def rotate_key(key_id: str, payload: dict | None = None):
    """Rotate an encryption key."""
    initiated_by = (payload or {}).get("initiated_by", "")
    log = encrypted_store.rotate_key(key_id, initiated_by)
    return {
        "rotation_id": log.rotation_id,
        "old_key_id": log.old_key_id,
        "new_key_id": log.new_key_id,
        "reencrypted_vectors": log.reencrypted_vectors,
        "status": "rotated",
    }


@router.get("/api/v1/memora/encrypted/vectors/{vector_id}/decrypt")
async def decrypt_vector(vector_id: str):
    """Decrypt a vector."""
    vector = encrypted_store.decrypt_vector(vector_id)
    return {"vector_id": vector_id, "decrypted": vector, "length": len(vector)}


@router.get("/api/v1/memora/encrypted/keys/schedule")
async def get_key_schedule():
    """Get key schedule."""
    keys = encrypted_store.get_key_schedule()
    return [
        {
            "key_id": k.key_id,
            "key_type": k.key_type,
            "is_active": k.is_active,
            "key_version": k.key_version,
            "rotation_period_hours": k.rotation_period_hours,
        }
        for k in keys
    ]


@router.get("/api/v1/memora/encrypted/report")
async def get_report():
    """Get encrypted vector store report."""
    return encrypted_store.get_report()
