from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable, Sequence
from .models import RetrievalResult, token_estimate


@dataclass(frozen=True, slots=True)
class ContextItem:
    memory_id: str
    text: str
    score: float
    tokens: int
    citation: str | None


@dataclass(frozen=True, slots=True)
class ContextBundle:
    items: tuple[ContextItem, ...]
    total_tokens: int


class ContextAssembler:
    """Budget-aware context selection that preserves diversity and provenance."""

    def build(self, results: Sequence[RetrievalResult], max_tokens: int, max_items: int) -> ContextBundle:
        chosen: list[ContextItem] = []
        used = 0
        seen_sources: set[str] = set()
        for result in sorted(results, key=lambda x: (-x.score, x.memory_id)):
            tokens = token_estimate(result.text)
            source = result.provenance.source
            diversity_bonus = 1 if source and source not in seen_sources else 0
            if chosen and used + tokens > max_tokens:
                continue
            if chosen and diversity_bonus == 0 and len(chosen) < 3:
                continue
            chosen.append(ContextItem(result.memory_id, result.text, result.score, tokens, result.citation))
            used += tokens
            seen_sources.add(source)
            if len(chosen) >= max_items:
                break
        return ContextBundle(tuple(chosen), used)
