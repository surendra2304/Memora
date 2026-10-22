"""
Forgetting Model and Time-Based Importance Decay for Memora
Reduces importance of unverified, aging memories and consolidates cold records into archive.
Uses chunked cursor-based processing with bounded batches.
"""
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
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
        archive_importance_threshold: float = 0.15,
        tenant_id: Optional[str] = None,
        batch_size: int = 100
    ) -> Dict[str, Any]:
        """
        Iterates over active and candidate memories using primary-key cursor batches,
        reducing importance for unverified records and archiving expired/cold memories.
        """
        now = datetime.now(timezone.utc)
        evaluated_total = 0
        decayed_count = 0
        archived_count = 0
        last_id = ""

        while True:
            query = db.query(MemoryRecord).filter(
                MemoryRecord.lifecycle_state.in_([LifecycleState.ACTIVE, LifecycleState.CANDIDATE]),
                MemoryRecord.id > last_id
            )
            if tenant_id:
                query = query.filter(MemoryRecord.tenant_id == tenant_id)

            batch: List[MemoryRecord] = query.order_by(MemoryRecord.id.asc()).limit(batch_size).all()
            if not batch:
                break

            for r in batch:
                evaluated_total += 1
                last_id = r.id

                # Check explicit expiration first
                if r.expires_at:
                    exp_time = r.expires_at
                    if exp_time.tzinfo is None:
                        exp_time = exp_time.replace(tzinfo=timezone.utc)
                    if now > exp_time:
                        MemoryLifecycleEngine.transition(r, LifecycleState.ARCHIVED)
                        archived_count += 1
                        continue

                prov = dict(r.provenance) if isinstance(r.provenance, dict) else {}

                # Skip pinned / protected records
                if prov.get("pinned", False) or r.importance >= 0.99:
                    continue

                # Determine baseline age reference (last_verified_at or created_at)
                ref_time = r.last_verified_at or r.created_at
                if ref_time.tzinfo is None:
                    ref_time = ref_time.replace(tzinfo=timezone.utc)

                age_days = (now - ref_time).total_seconds() / 86400.0

                if age_days < unverified_threshold_days:
                    continue

                # Decay is derived from a STORED baseline and the absolute age, not
                # from the already-decayed importance. The previous implementation
                # subtracted a cumulative `rate * (age - threshold + 1)` penalty from
                # r.importance on every cycle, so each run re-applied the whole
                # accumulated penalty: a 15-day-old record dropped 0.50 -> 0.18 over
                # eight same-day cycles and archived on the ninth, instead of holding
                # at the 0.46 the documented model implies. Recomputing from a fixed
                # baseline makes a cycle idempotent, which is what a scheduler needs.
                baseline = prov.get("decay_baseline_importance")
                if baseline is None:
                    baseline = r.importance
                    prov["decay_baseline_importance"] = baseline
                    prov["decay_baseline_at"] = ref_time.isoformat()

                decay_factor = decay_rate_per_day * (age_days - unverified_threshold_days + 1)
                new_importance = max(0.01, round(float(baseline) - decay_factor, 4))

                prov["decay_applied"] = round(decay_factor, 4)
                prov["decay_age_days"] = round(age_days, 4)
                # Reassign so SQLAlchemy sees the JSON column as dirty.
                r.provenance = prov

                if new_importance != r.importance:
                    r.importance = new_importance
                    decayed_count += 1

                # Auto-archive if decayed below retention threshold
                if r.importance <= archive_importance_threshold:
                    if MemoryLifecycleEngine.can_transition(r.lifecycle_state, LifecycleState.ARCHIVED):
                        MemoryLifecycleEngine.transition(r, LifecycleState.ARCHIVED)
                        archived_count += 1

            # Commit per batch for bounded memory and transactions
            db.commit()

        return {
            "processed_count": evaluated_total,
            "evaluated_total": evaluated_total,
            "decayed_count": decayed_count,
            "archived_count": archived_count,
            "decay_rate_applied": decay_rate_per_day,
            "archive_threshold": archive_importance_threshold
        }