"""
Forgetting Model and Time-Based Importance Decay for Memora
Reduces importance of unverified, aging memories and consolidates cold records into archive.
"""
from typing import Dict, Any, List
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session
from storage.relational.models import MemoryRecord, LifecycleState
from core.lifecycle.state_machine import MemoryLifecycleEngine

class MemoryDecayEngine:
    @classmethod
    def apply_time_decay(
        cls,
        db: Session,
        decay_rate_per_day: float = 0.02,
        unverified_threshold_days: int = 14,
        archive_importance_threshold: float = 0.15
    ) -> Dict[str, Any]:
        """
        Iterates over active and candidate memories, reducing importance for unverified records.
        """
        now = datetime.now(timezone.utc)
        records = db.query(MemoryRecord).filter(
            MemoryRecord.lifecycle_state.in_([LifecycleState.ACTIVE, LifecycleState.CANDIDATE])
        ).all()

        decayed_count = 0
        archived_count = 0

        for r in records:
            # Determine baseline age reference (last_verified_at or created_at)
            ref_time = r.last_verified_at or r.created_at
            if ref_time.tzinfo is None:
                ref_time = ref_time.replace(tzinfo=timezone.utc)

            age_days = (now - ref_time).total_seconds() / 86400.0

            if age_days >= unverified_threshold_days:
                # Calculate decay
                decay_factor = decay_rate_per_day * (age_days - unverified_threshold_days + 1)
                new_importance = max(0.01, round(r.importance - decay_factor, 4))

                if new_importance != r.importance:
                    r.importance = new_importance
                    decayed_count += 1

                    # Auto-archive if decayed below retention threshold
                    if r.importance <= archive_importance_threshold:
                        MemoryLifecycleEngine.transition(r, LifecycleState.ARCHIVED)
                        archived_count += 1

        db.commit()
        return {
            "evaluated_total": len(records),
            "decayed_count": decayed_count,
            "archived_count": archived_count,
            "decay_rate_applied": decay_rate_per_day,
            "archive_threshold": archive_importance_threshold
        }