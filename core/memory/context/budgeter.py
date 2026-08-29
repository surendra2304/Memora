"""
Token Budgeting and Compaction Engine for Memora Context Bundles
"""
from typing import List, Dict, Any, Tuple
from storage.relational.models import MemoryRecord
from core.memory.context.reranker import RerankedMemoryItem

class BudgetedMemoryItem:
    def __init__(
        self,
        record: MemoryRecord,
        content_text: str,
        token_count: int,
        score: float,
        is_truncated: bool = False
    ):
        self.record = record
        self.content_text = content_text
        self.token_count = token_count
        self.score = score
        self.is_truncated = is_truncated

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.record.id,
            "namespace_id": self.record.namespace_id,
            "namespace_path": self.record.namespace.path if self.record.namespace else None,
            "owner_name": self.record.owner.name if self.record.owner else None,
            "memory_type": self.record.memory_type.value,
            "content_text": self.content_text,
            "confidence": self.record.confidence,
            "importance": self.record.importance,
            "provenance": self.record.provenance or {},
            "score": round(self.score, 4),
            "token_count": self.token_count,
            "is_truncated": self.is_truncated
        }

class ContextBudgeter:
    CHARS_PER_TOKEN = 4.0

    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        return max(1, int(len(text) / cls.CHARS_PER_TOKEN))

    @classmethod
    def fit_to_budget(
        cls,
        reranked_items: List[RerankedMemoryItem],
        max_tokens: int = 4000,
        similarity_dedup_threshold: float = 0.85
    ) -> Tuple[List[BudgetedMemoryItem], int]:
        budgeted_items: List[BudgetedMemoryItem] = []
        current_token_sum = 0
        seen_token_sets: List[set] = []

        for item in reranked_items:
            content = item.record.content_text
            content_tokens = set(content.lower().split())

            # Fact Deduplication Check
            is_redundant = False
            for seen_set in seen_token_sets:
                if not content_tokens or not seen_set:
                    continue
                overlap = len(content_tokens.intersection(seen_set)) / len(content_tokens.union(seen_set))
                if overlap >= similarity_dedup_threshold:
                    is_redundant = True
                    break

            if is_redundant:
                continue

            item_tokens = cls.estimate_tokens(content)

            # If item fits fully
            if current_token_sum + item_tokens <= max_tokens:
                budgeted_items.append(
                    BudgetedMemoryItem(
                        record=item.record,
                        content_text=content,
                        token_count=item_tokens,
                        score=item.final_score,
                        is_truncated=False
                    )
                )
                current_token_sum += item_tokens
                seen_token_sets.append(content_tokens)
            else:
                # Boundary item: Truncate to fill remaining budget if at least 15 tokens left
                remaining_tokens = max_tokens - current_token_sum
                if remaining_tokens >= 15:
                    max_chars = int(remaining_tokens * cls.CHARS_PER_TOKEN)
                    truncated_content = content[:max_chars].rsplit(" ", 1)[0] + " ... [truncated]"
                    budgeted_items.append(
                        BudgetedMemoryItem(
                            record=item.record,
                            content_text=truncated_content,
                            token_count=remaining_tokens,
                            score=item.final_score,
                            is_truncated=True
                        )
                    )
                    current_token_sum += remaining_tokens
                break

        return budgeted_items, current_token_sum