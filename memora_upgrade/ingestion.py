from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Mapping
import time
import uuid

from .models import MemoryItem, MemoryKind, MemoryScope, Provenance, Lifecycle
from .security import SecretScanner, SecretRedactor
from .store import MemoryStore


@dataclass(frozen=True, slots=True)
class IngestionPolicy:
    reject_secrets: bool = True
    redact_secrets: bool = False
    min_confidence: float = 0.0
    min_importance: float = 0.0
    max_text_chars: int = 50000


class IngestionService:
    """Crash-aware memory write boundary with deterministic dedup semantics."""

    def __init__(self, store: MemoryStore, secret_scanner: SecretScanner | None = None, redactor: SecretRedactor | None = None, policy: IngestionPolicy | None = None):
        self.store = store
        self.scanner = secret_scanner or SecretScanner()
        self.redactor = redactor or SecretRedactor()
        self.policy = policy or IngestionPolicy()

    def ingest(
        self,
        *,
        tenant_id: str,
        owner_agent_id: str,
        text: str,
        kind: MemoryKind,
        scope: MemoryScope,
        source: str,
        confidence: float,
        importance: float,
        entities: tuple[str, ...] = (),
        tags: tuple[str, ...] = (),
        provenance: Mapping[str, object] | None = None,
        event_id: str | None = None,
        lifecycle: Lifecycle = Lifecycle.CANDIDATE,
        metadata: Mapping[str, object] | None = None,
    ) -> MemoryItem:
        normalized = " ".join(text.split()).strip()
        if not normalized:
            raise ValueError("empty memory")
        if len(normalized) > self.policy.max_text_chars:
            raise ValueError("memory exceeds maximum size")
        findings = self.scanner.scan(normalized)
        if findings and self.policy.reject_secrets and not self.policy.redact_secrets:
            raise ValueError("secret-like content rejected")
        if findings and self.policy.redact_secrets:
            normalized = self.redactor.redact(normalized)
        if not self.policy.min_confidence <= confidence <= 1.0:
            raise ValueError("invalid confidence")
        if not self.policy.min_importance <= importance <= 1.0:
            raise ValueError("invalid importance")
        event_id = event_id or str(uuid.uuid4())

        memory_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"memora:{tenant_id}:{owner_agent_id}:{kind.value}:{normalized.casefold()}"))
        memory = MemoryItem(
            id=memory_id,
            tenant_id=tenant_id,
            owner_agent_id=owner_agent_id,
            kind=kind,
            text=normalized,
            scope=scope,
            confidence=confidence,
            importance=importance,
            lifecycle=lifecycle,
            provenance=Provenance(
                source=source,
                source_id=str(provenance.get("source_id")) if provenance and provenance.get("source_id") else None,
                observed_at=datetime.now(timezone.utc),
                author=str(provenance.get("author")) if provenance and provenance.get("author") else None,
                authority=float(str(provenance.get("authority", 0.5))) if provenance else 0.5,
                evidence_uri=str(provenance.get("evidence_uri")) if provenance and provenance.get("evidence_uri") else None,
            ),
            tags=tuple(tags),
            entities=tuple(entities),
            source_event_id=event_id,
            metadata=dict(metadata or {}),
        )
        existing = self.store.get(memory_id)
        if existing:
            return existing
        self.store.put(memory)
        return memory
