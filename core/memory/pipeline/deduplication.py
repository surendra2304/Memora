"""
Duplicate detection and conservative contradiction signaling for the Memora write pipeline.

Duplicates may be safely coalesced after authorization. Contradiction signals are
review-only metadata: this module never picks a winner or mutates memory lifecycle.
"""
from typing import Optional

from sqlalchemy.orm import Session

from core.memory.pipeline.contradiction_detector import ContradictionDetector
from storage.relational.models import LifecycleState, MemoryRecord


class DeduplicationResult:
    def __init__(
        self,
        is_duplicate: bool,
        duplicate_of_id: Optional[str] = None,
        similarity_score: float = 0.0,
        duplicate_warning: Optional[str] = None,
        is_contradiction: bool = False,
        contradiction_of_id: Optional[str] = None,
        contradiction_score: float = 0.0,
        contradiction_kind: Optional[str] = None,
        contradiction_reason: Optional[str] = None,
    ):
        self.is_duplicate = is_duplicate
        self.duplicate_of_id = duplicate_of_id
        self.similarity_score = similarity_score
        self.duplicate_warning = duplicate_warning
        self.is_contradiction = is_contradiction
        self.contradiction_of_id = contradiction_of_id
        self.contradiction_score = contradiction_score
        self.contradiction_kind = contradiction_kind
        self.contradiction_reason = contradiction_reason


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
        similarity_threshold: float = 0.85,
        tenant_id: Optional[str] = None,
    ) -> DeduplicationResult:
        """Detect exact/near duplicates and high-confidence candidate conflicts.

        The namespace and optional tenant constrain the candidate set. Conflicts
        take precedence over duplicate matches, and uncertain statements are
        deliberately ignored by the underlying detector.
        """
        query = db.query(MemoryRecord).filter(
            MemoryRecord.namespace_id == namespace_id,
            MemoryRecord.lifecycle_state.in_([
                LifecycleState.ACTIVE,
                LifecycleState.VERIFIED,
                LifecycleState.CANDIDATE,
            ]),
        )
        if tenant_id is not None:
            query = query.filter(MemoryRecord.tenant_id == tenant_id)
        records = query.order_by(MemoryRecord.created_at.asc(), MemoryRecord.id.asc()).all()

        cleaned_input = " ".join(content_text.strip().split())
        duplicate_match: Optional[DeduplicationResult] = None

        for record in records:
            cleaned_existing = " ".join(record.content_text.strip().split())

            assessment = ContradictionDetector.assess(cleaned_existing, cleaned_input)
            if assessment.is_contradiction:
                return DeduplicationResult(
                    is_duplicate=False,
                    similarity_score=assessment.score,
                    is_contradiction=True,
                    contradiction_of_id=record.id,
                    contradiction_score=assessment.score,
                    contradiction_kind=assessment.kind,
                    contradiction_reason=assessment.reason,
                )

            # Preserve existing exact-duplicate and lexical-overlap behavior.
            if cleaned_input.casefold() == cleaned_existing.casefold():
                if duplicate_match is None or duplicate_match.similarity_score < 1.0:
                    duplicate_match = DeduplicationResult(
                        is_duplicate=True,
                        duplicate_of_id=record.id,
                        similarity_score=1.0,
                        duplicate_warning="Exact content duplicate detected in target namespace.",
                    )
                continue

            similarity = cls._token_jaccard(cleaned_input, cleaned_existing)
            if similarity >= similarity_threshold and duplicate_match is None:
                duplicate_match = DeduplicationResult(
                    is_duplicate=True,
                    duplicate_of_id=record.id,
                    similarity_score=similarity,
                    duplicate_warning="High semantic/lexical overlap with existing record.",
                )

        if duplicate_match is not None:
            return duplicate_match
        return DeduplicationResult(is_duplicate=False, similarity_score=0.0)
