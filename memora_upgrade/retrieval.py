from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence
import math
import re

from .models import MemoryItem, QueryContext, RetrievalCandidate, RetrievalResult, Lifecycle, token_estimate
from .policy import MemoryPolicy


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> set[str]:
    return {x.casefold() for x in TOKEN_RE.findall(text)}


def lexical_score(query: str, text: str) -> float:
    q = tokenize(query)
    t = tokenize(text)
    if not q or not t:
        return 0.0
    overlap = len(q & t)
    return overlap / math.sqrt(len(q) * len(t))


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


@dataclass(frozen=True, slots=True)
class EmbeddedMemory:
    memory: MemoryItem
    vector: tuple[float, ...]


class InMemoryVectorIndex:
    """Reference implementation. Production adapters MUST namespace vectors by tenant."""

    def __init__(self):
        self._items: dict[str, EmbeddedMemory] = {}

    def upsert(self, memory: MemoryItem, vector: Sequence[float]) -> None:
        self._items[memory.id] = EmbeddedMemory(memory, tuple(float(x) for x in vector))

    def delete(self, memory_id: str) -> None:
        self._items.pop(memory_id, None)

    def search(self, tenant_id: str, vector: Sequence[float], limit: int = 20) -> list[tuple[MemoryItem, float]]:
        scored = []
        for item in self._items.values():
            if item.memory.tenant_id != tenant_id:
                continue
            scored.append((item.memory, cosine(vector, item.vector)))
        scored.sort(key=lambda x: (-x[1], x[0].id))
        return scored[:limit]


class HybridRetriever:
    def __init__(self, memories: Callable[[str], Iterable[MemoryItem]], policy: MemoryPolicy, vector_index: InMemoryVectorIndex | None = None, embed: Callable[[str], Sequence[float]] | None = None):
        self.memories = memories
        self.policy = policy
        self.vector_index = vector_index
        self.embed = embed

    def retrieve(self, query: str, context: QueryContext, entities: set[str] | None = None, temporal_intent: Callable[[str, MemoryItem], float] | None = None) -> list[RetrievalResult]:
        all_items = []
        for memory in self.memories(context.tenant_id):
            namespace_key = f"tenant/{memory.tenant_id}/agent/{memory.owner_agent_id}"
            decision = self.policy.can_read(
                context.tenant_id,
                context.actor_agent_id,
                memory.tenant_id,
                memory.owner_agent_id,
                namespace_key,
                context.purpose,
                int(context.now.timestamp() * 1e9),
            )
            if not decision.allowed:
                continue
            if memory.lifecycle == Lifecycle.DELETED:
                continue
            if memory.lifecycle == Lifecycle.ARCHIVED and not context.include_archived:
                continue
            if memory.expires_at and context.now >= memory.expires_at:
                continue
            if not memory.temporal.is_valid_at(context.now):
                continue
            all_items.append(memory)

        lexical = sorted(
            [(m, lexical_score(query, m.text)) for m in all_items],
            key=lambda x: (-x[1], x[0].id),
        )
        semantic = []
        if self.vector_index and self.embed:
            qv = self.embed(query)
            semantic = self.vector_index.search(context.tenant_id, qv, limit=max(20, context.max_results * 2))

        lex_rank = {m.id: i + 1 for i, (m, score) in enumerate(lexical) if score > 0}
        sem_rank = {m.id: i + 1 for i, (m, score) in enumerate(semantic)}
        lex_score = {m.id: score for m, score in lexical}
        sem_score = {m.id: score for m, score in semantic}

        candidates: list[RetrievalCandidate] = []
        universe = {m.id: m for m in all_items}
        for mid, m in universe.items():
            candidates.append(
                RetrievalCandidate(
                    memory=m,
                    lexical_rank=lex_rank.get(mid),
                    semantic_rank=sem_rank.get(mid),
                    lexical_score=lex_score.get(mid, 0.0),
                    semantic_score=sem_score.get(mid, 0.0),
                    entity_score=self._entity_score(m, entities or set()),
                    temporal_score=temporal_intent(query, m) if temporal_intent else m.freshness_score(context.now),
                )
            )

        scored = []
        for c in candidates:
            rrf = (1.0 / (60 + c.lexical_rank) if c.lexical_rank else 0.0) + (
                1.0 / (60 + c.semantic_rank) if c.semantic_rank else 0.0
            )
            score = (
                0.50 * rrf
                + 0.20 * c.entity_score
                + 0.15 * c.temporal_score
                + 0.15 * c.memory.authority_score()
            )
            scored.append(c.with_score(score))

        scored.sort(key=lambda c: (-c.final_score, c.memory.id))

        results = []
        budget = context.max_context_tokens
        for c in scored:
            cost = token_estimate(c.memory.text)
            if not results or budget - cost >= 0:
                budget -= cost
                rationale = []
                if c.lexical_rank: rationale.append(f"lexical_rank={c.lexical_rank}")
                if c.semantic_rank: rationale.append(f"semantic_rank={c.semantic_rank}")
                if c.entity_score: rationale.append("entity_overlap")
                if c.temporal_score: rationale.append("temporal_relevance")
                rationale.append(f"authority={c.memory.authority_score():.3f}")
                results.append(
                    RetrievalResult(
                        memory_id=c.memory.id,
                        text=c.memory.text,
                        score=round(c.final_score, 6),
                        scope=c.memory.scope,
                        provenance=c.memory.provenance,
                        lifecycle=c.memory.lifecycle,
                        rationale=tuple(rationale),
                        citation=c.memory.provenance.evidence_uri,
                    )
                )
            if len(results) >= context.max_results:
                break
        return results

    @staticmethod
    def _entity_score(memory: MemoryItem, entities: set[str]) -> float:
        if not entities or not memory.entities:
            return 0.0
        a = {x.casefold() for x in memory.entities}
        b = {x.casefold() for x in entities}
        return len(a & b) / max(1, len(b))
