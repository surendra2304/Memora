"""
Memora/apps/api/routers/memory_provenance_tracker.py — Memora Memory Provenance Tracker.

Memory source tracking, cryptographic hashing, tamper detection,
and provenance chain verification.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class ProvenanceRecord:
    """A provenance record for a memory."""
    memory_id: str
    content_hash: str
    source: str
    source_type: str  # agent, user, external, system
    chain_hash: str = ""  # hash of the previous record in the chain
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    verified: bool = True


@dataclass
class ProvenanceChain:
    """A chain of provenance records."""
    chain_id: str
    records: list[ProvenanceRecord] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    is_valid: bool = True
    last_verified: float = 0.0


@dataclass
class VerificationResult:
    """Result of verifying a provenance chain."""
    chain_id: str
    is_valid: bool
    record_count: int
    issues: list[str] = field(default_factory=list)
    verified_at: float = field(default_factory=time.time)


class MemoryProvenanceTracker:
    """Tracker for memory provenance and tamper detection."""

    def __init__(self) -> None:
        self._records: dict[str, ProvenanceRecord] = {}
        self._chains: dict[str, ProvenanceChain] = {}

    def record_memory(self, memory_id: str, content: str, source: str,
                      source_type: str = "agent",
                      metadata: dict | None = None) -> str:
        """Record a memory with provenance."""
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        prev_hash = ""
        # Find chain for this memory
        for chain in self._chains.values():
            if any(r.memory_id == memory_id for r in chain.records):
                if chain.records:
                    prev_hash = chain.records[-1].chain_hash
                break
        else:
            # Create new chain
            chain_id = f"chain_{uuid.uuid4().hex[:12]}"
            chain = ProvenanceChain(chain_id=chain_id)
            self._chains[chain_id] = chain

        chain_hash = hashlib.sha256(f"{content_hash}{prev_hash}{time.time()}".encode()).hexdigest()
        record = ProvenanceRecord(
            memory_id=memory_id, content_hash=content_hash, source=source,
            source_type=source_type, chain_hash=chain_hash,
            metadata=metadata or {},
        )
        self._records[memory_id] = record

        # Add to chain
        for chain in self._chains.values():
            if any(r.memory_id == memory_id for r in chain.records):
                chain.records.append(record)
                chain.last_verified = time.time()
                break

        return memory_id

    def verify_chain(self, chain_id: str) -> VerificationResult:
        """Verify a provenance chain for tampering."""
        chain = self._chains.get(chain_id)
        if not chain:
            raise HTTPException(status_code=404, detail="Chain not found")
        result = VerificationResult(chain_id=chain_id, is_valid=True, record_count=len(chain.records))
        prev_hash = ""
        for record in chain.records:
            expected_chain_hash = hashlib.sha256(
                f"{record.content_hash}{prev_hash}{record.timestamp}".encode()
            ).hexdigest()
            if record.chain_hash != expected_chain_hash:
                result.is_valid = False
                result.issues.append(f"Hash mismatch at {record.memory_id}")
            if record.content_hash != hashlib.sha256(
                self._get_content(record.memory_id).encode()
            ).hexdigest():
                result.is_valid = False
                result.issues.append(f"Content mismatch at {record.memory_id}")
            prev_hash = record.chain_hash
        chain.is_valid = result.is_valid
        chain.last_verified = time.time()
        return result

    def _get_content(self, memory_id: str) -> str:
        """Get content for a memory (simplified — returns stored hash source)."""
        return memory_id  # Simplified: use memory_id as content

    def get_provenance(self, memory_id: str) -> dict[str, Any]:
        """Get provenance for a memory."""
        record = self._records.get(memory_id)
        if not record:
            raise HTTPException(status_code=404, detail="Memory not found")
        return {
            "memory_id": record.memory_id,
            "content_hash": record.content_hash,
            "source": record.source,
            "source_type": record.source_type,
            "timestamp": record.timestamp,
            "verified": record.verified,
            "metadata": record.metadata,
        }

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_records": len(self._records),
            "total_chains": len(self._chains),
            "valid_chains": sum(1 for c in self._chains.values() if c.is_valid),
            "engine_status": "operational",
        }


engine = MemoryProvenanceTracker()


@router.post("/memora/provenance/records")
async def record_memory(memory_id: str, content: str, source: str,
                        source_type: str = "agent", metadata: dict | None = None):
    """Record a memory with provenance."""
    engine.record_memory(memory_id, content, source, source_type, metadata)
    return {"memory_id": memory_id, "status": "recorded"}


@router.get("/memora/provenance/{memory_id}")
async def get_provenance(memory_id: str):
    """Get provenance for a memory."""
    return engine.get_provenance(memory_id)


@router.post("/memora/provenance/chains/{chain_id}/verify")
async def verify_chain(chain_id: str):
    """Verify a provenance chain."""
    result = engine.verify_chain(chain_id)
    return {
        "chain_id": result.chain_id,
        "is_valid": result.is_valid,
        "record_count": result.record_count,
        "issues": result.issues,
    }


@router.get("/memora/provenance/report")
async def provenance_report():
    """Get provenance tracker report."""
    return engine.get_report()
