"""
Memory Lifecycle State Machine
Governs valid state transitions:
CANDIDATE -> ACTIVE -> VERIFIED -> SUPERSEDED / ARCHIVED / DELETED
"""
from typing import Set, Tuple, Optional
from datetime import datetime, timezone
from storage.relational.models import MemoryRecord, LifecycleState

class InvalidStateTransitionError(Exception):
    pass

class MemoryLifecycleEngine:
    VALID_TRANSITIONS: Set[Tuple[LifecycleState, LifecycleState]] = {
        # Promotion / Initial Activation
        (LifecycleState.CANDIDATE, LifecycleState.ACTIVE),
        (LifecycleState.CANDIDATE, LifecycleState.VERIFIED),
        (LifecycleState.CANDIDATE, LifecycleState.SUPERSEDED),
        (LifecycleState.CANDIDATE, LifecycleState.DELETED),
        (LifecycleState.CANDIDATE, LifecycleState.ARCHIVED),

        # Verification
        (LifecycleState.ACTIVE, LifecycleState.VERIFIED),

        # Supersession (replacing older memories with newer facts)
        (LifecycleState.ACTIVE, LifecycleState.SUPERSEDED),
        (LifecycleState.VERIFIED, LifecycleState.SUPERSEDED),

        # Archival / Consolidation
        (LifecycleState.ACTIVE, LifecycleState.ARCHIVED),
        (LifecycleState.VERIFIED, LifecycleState.ARCHIVED),
        (LifecycleState.SUPERSEDED, LifecycleState.ARCHIVED),

        # Reactivation / Un-archival
        (LifecycleState.ARCHIVED, LifecycleState.ACTIVE),

        # Deletion
        (LifecycleState.ACTIVE, LifecycleState.DELETED),
        (LifecycleState.VERIFIED, LifecycleState.DELETED),
        (LifecycleState.SUPERSEDED, LifecycleState.DELETED),
        (LifecycleState.ARCHIVED, LifecycleState.DELETED),
    }

    @classmethod
    def can_transition(cls, current_state: LifecycleState, target_state: LifecycleState) -> bool:
        if current_state == target_state:
            return True
        return (current_state, target_state) in cls.VALID_TRANSITIONS

    @classmethod
    def transition(
        cls,
        record: MemoryRecord,
        target_state: LifecycleState,
        superseded_by_id: Optional[str] = None
    ) -> MemoryRecord:
        if not cls.can_transition(record.lifecycle_state, target_state):
            raise InvalidStateTransitionError(
                f"Cannot transition memory {record.id} from {record.lifecycle_state} to {target_state}"
            )

        record.lifecycle_state = target_state

        if target_state == LifecycleState.VERIFIED:
            record.last_verified_at = datetime.now(timezone.utc)
            record.confidence = min(1.0, record.confidence + 0.1)

        if target_state == LifecycleState.SUPERSEDED:
            if superseded_by_id:
                record.superseded_by_id = superseded_by_id

        return record