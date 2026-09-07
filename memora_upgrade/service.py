from __future__ import annotations
from dataclasses import replace
from datetime import datetime, timezone
from typing import Mapping

from .models import MemoryItem, MemoryKind, MemoryScope, QueryContext, RetrievalResult, Lifecycle
from .store import MemoryStore
from .ingestion import IngestionService
from .policy import MemoryPolicy
from .retrieval import HybridRetriever, InMemoryVectorIndex
from .embeddings import DeterministicEmbedding
from .context import ContextAssembler, ContextBundle
from .consolidation import Consolidator, TemporalResolver
from .forgetting import ForgettingEngine
from .security import SecretScanner, SecretRedactor
from .observability import Metrics


class MemoraEngine:
    """Reference composition root for the new memory path."""

    def __init__(self, store: MemoryStore | None = None, policy: MemoryPolicy | None = None):
        self.store = store or MemoryStore()
        self.policy = policy or MemoryPolicy()
        self.ingestion = IngestionService(
            self.store,
            secret_scanner=SecretScanner(),
            redactor=SecretRedactor(),
        )
        self.embedding = DeterministicEmbedding()
        self.vector = InMemoryVectorIndex()
        self.retriever = HybridRetriever(self.store.list_tenant, self.policy, self.vector, self.embedding.embed)
        self.context = ContextAssembler()
        self.consolidator = Consolidator()
        self.forgetting = ForgettingEngine()
        self.resolver = TemporalResolver()
        self.metrics = Metrics()

    def remember(self, **kwargs) -> MemoryItem:
        memory = self.ingestion.ingest(**kwargs)
        self.vector.upsert(memory, self.embedding.embed(memory.text))
        self.store.append_event(
            __import__("memora_upgrade.models", fromlist=["MemoryEvent"]).MemoryEvent.create(
                "memory.created", memory.tenant_id, memory.owner_agent_id, memory.id, {"kind": memory.kind.value}
            )
        )
        return memory

    def search(self, query: str, context: QueryContext) -> list[RetrievalResult]:
        with self.metrics.timed_retrieval(context.tenant_id) as state:
            results = self.retriever.retrieve(query, context)
            state["results"] = len(results)
            state["candidates"] = len(self.store.list_tenant(context.tenant_id))
        return results

    def build_context(self, results: list[RetrievalResult], max_tokens: int = 3000, max_items: int = 12) -> ContextBundle:
        return self.context.build(results, max_tokens, max_items)

    def apply_lifecycle(self, memory_id: str) -> MemoryItem:
        memory = self.store.get(memory_id)
        if not memory:
            raise KeyError(memory_id)
        decision = self.consolidator.decide(memory)
        if decision.action == "ARCHIVE":
            return self.store.transition(memory_id, Lifecycle.ARCHIVED)
        if decision.action == "DELETE":
            return self.store.transition(memory_id, Lifecycle.DELETED)
        if decision.action == "ACTIVATE":
            return self.store.transition(memory_id, Lifecycle.ACTIVE)
        return memory

    def resolve_conflict(self, existing_id: str, incoming_id: str) -> tuple[str, str]:
        existing = self.store.get(existing_id)
        incoming = self.store.get(incoming_id)
        if not existing or not incoming:
            raise KeyError("memory missing")
        winner, reason = self.resolver.resolve(existing, incoming)
        loser = incoming if winner.id == existing.id else existing
        self.store.transition(loser.id, Lifecycle.SUPERSEDED, superseded_by_id=winner.id)
        return winner.id, reason
