"""
Multi-Factor Reranker for Memora Context Pipeline
Scores candidate memories using:
relevance_score * confidence_weight * freshness_weight * importance_weight
"""
import math
from typing import List, Tuple
from datetime import datetime, timezone
from storage.relational.models import MemoryRecord
from core.memory.search_service import SearchResultItem

class RerankedMemoryItem:
    def __init__(
        self,
        record: MemoryRecord,
        final_score: float,
        relevance_score: float,
        confidence_weight: float,
        freshness_weight: float,
        importance_weight: float,
        rank: int = 1
    ):
        self.record = record
        self.final_score = final_score
        self.relevance_score = relevance_score
        self.confidence_weight = confidence_weight
        self.freshness_weight = freshness_weight
        self.importance_weight = importance_weight
        self.rank = rank

class ContextReranker:
    @classmethod
    def calculate_freshness(cls, created_at: datetime, half_life_days: float = 60.0) -> float:
        now = datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
        # Exponential freshness curve
        return math.exp(-age_days / half_life_days)

    @classmethod
    def rerank(
        cls,
        search_results: List[SearchResultItem],
        half_life_days: float = 60.0
    ) -> List[RerankedMemoryItem]:
        reranked = []

        for item in search_results:
            r = item.record
            rel_score = max(0.05, item.final_score)
            conf_weight = 0.4 + (0.6 * r.confidence)
            fresh_weight = cls.calculate_freshness(r.created_at, half_life_days=half_life_days)
            imp_weight = 0.4 + (0.6 * r.importance)

            final_score = rel_score * conf_weight * fresh_weight * imp_weight

            reranked.append(
                RerankedMemoryItem(
                    record=r,
                    final_score=round(final_score, 4),
                    relevance_score=round(rel_score, 4),
                    confidence_weight=round(conf_weight, 4),
                    freshness_weight=round(fresh_weight, 4),
                    importance_weight=round(imp_weight, 4)
                )
            )

        reranked.sort(key=lambda x: x.final_score, reverse=True)
        for idx, item in enumerate(reranked):
            item.rank = idx + 1

        return reranked