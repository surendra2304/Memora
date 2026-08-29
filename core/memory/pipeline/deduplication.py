"""
Deduplication and Contradiction Detection for Memora Write Pipeline
"""
from typing import Optional, Tuple, List
from sqlalchemy.orm import Session
from storage.relational.models import MemoryRecord, LifecycleState

class DeduplicationResult:
    def __init__(
        self,
        is_duplicate: bool,
        duplicate_of_id: Optional[str] = None,
        similarity_score: float = 0.0,
        contradiction_warning: Optional[str] = None
    ):
        self.is_duplicate = is_duplicate
        self.duplicate_of_id = duplicate_of_id
        self.similarity_score = similarity_score
        self.contradiction_warning = contradiction_warning

class DeduplicationEngine:
    @staticmethod
    def _token_jaccard(text1: str, text2: str) -> float:
        tokens1 = set(text1.lower().split())
        tokens2 = set(text2.lower().split())
        if not tokens1 or not tokens2:
            return 0.0
        intersection = len(tokens1.intersection(tokens2))
        union = len(tokens1.union(tokens2))
        return intersection / union if union > 0 else 0.0

    @classmethod
    def check_duplicates_and_contradictions(
        cls,
        db: Session,
        namespace_id: str,
        content_text: str,
        similarity_threshold: float = 0.85
    ) -> DeduplicationResult:
        # Check active records in the same namespace
        records = db.query(MemoryRecord).filter(
            MemoryRecord.namespace_id == namespace_id,
            MemoryRecord.lifecycle_state.in_([LifecycleState.ACTIVE, LifecycleState.VERIFIED, LifecycleState.CANDIDATE])
        ).all()

        cleaned_input = " ".join(content_text.strip().split())

        for r in records:
            cleaned_existing = " ".join(r.content_text.strip().split())
            # Exact match check
            if cleaned_input.lower() == cleaned_existing.lower():
                return DeduplicationResult(
                    is_duplicate=True,
                    duplicate_of_id=r.id,
                    similarity_score=1.0,
                    contradiction_warning="Exact content duplicate detected in target namespace."
                )

            # Jaccard lexical similarity
            sim = cls._token_jaccard(cleaned_input, cleaned_existing)
            if sim >= similarity_threshold:
                return DeduplicationResult(
                    is_duplicate=True,
                    duplicate_of_id=r.id,
                    similarity_score=sim,
                    contradiction_warning="High semantic/lexical overlap with existing record."
                )

        return DeduplicationResult(is_duplicate=False, similarity_score=0.0)