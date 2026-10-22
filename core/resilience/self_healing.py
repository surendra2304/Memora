"""
Self-healing supervisor for Memora.

Memora spans several stores (relational, vector, graph, cache) and has optional
external dependencies. Partial failures leave the system in states that are
*correct enough to keep serving* but wrong in ways nothing currently notices:

  - a deletion that converged in SQL but not in the vector store, so a deleted
    memory is still retrievable by semantic search
  - a tombstone stuck at PENDING_RETRY because Qdrant was briefly down
  - a memory pointing its superseded_by_id at a row that no longer exists
  - a circuit breaker left open after the dependency actually recovered
  - an importance-decay backlog because nothing scheduled the job

The supervisor runs a set of independent checks, each reporting findings and
optionally repairing them. It is deliberately conservative: repairs are scoped,
counted, and reported, and `dry_run` (the default for the read-only endpoint)
never mutates anything.

Every check returns a CheckResult with the same shape so the API and the tests
can assert on a uniform contract.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """Outcome of one integrity check."""

    name: str
    healthy: bool
    findings: int = 0
    repaired: int = 0
    details: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "healthy": self.healthy,
            "findings": self.findings,
            "repaired": self.repaired,
            "details": self.details[:20],
            "error": self.error,
        }


@dataclass
class HealingReport:
    """Aggregate outcome of one supervisor run."""

    checks: List[CheckResult] = field(default_factory=list)
    dry_run: bool = True

    @property
    def healthy(self) -> bool:
        return all(c.healthy for c in self.checks)

    @property
    def total_findings(self) -> int:
        return sum(c.findings for c in self.checks)

    @property
    def total_repaired(self) -> int:
        return sum(c.repaired for c in self.checks)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "healthy": self.healthy,
            "dry_run": self.dry_run,
            "total_findings": self.total_findings,
            "total_repaired": self.total_repaired,
            "checks": [c.to_dict() for c in self.checks],
        }


class SelfHealingSupervisor:
    """Runs integrity checks and repairs what it safely can."""

    #: Cap on rows any single repair will touch in one run, so a badly corrupted
    #: store cannot turn a health check into an unbounded write storm.
    MAX_REPAIRS_PER_CHECK = 500

    def __init__(self) -> None:
        # Qdrant reconciliation is paged. Keep a process-local cursor per tenant
        # so repeated repair runs eventually inspect the full collection without
        # loading an unbounded number of points into memory.
        self._vector_offsets: Dict[str, Any] = {}
        self._vector_offsets_lock = threading.Lock()

    # ------------------------------------------------------------------ run
    def run(
        self,
        db: Session,
        dry_run: bool = True,
        tenant_id: str = "default",
    ) -> HealingReport:
        """Run checks for one tenant. Never raises: a broken check is reported."""
        report = HealingReport(dry_run=dry_run)
        checks: List[Callable[[Session, bool, str], CheckResult]] = [
            self.check_unconverged_tombstones,
            self.check_orphaned_vectors,
            self.check_dangling_supersession,
            self.check_open_circuits,
            self.check_decay_backlog,
        ]
        for check in checks:
            try:
                report.checks.append(check(db, dry_run, tenant_id))
            except Exception as exc:  # a failing check must not abort the run
                name = getattr(check, "__name__", "unknown")
                logger.exception("self-healing check %s failed", name)
                report.checks.append(
                    CheckResult(name=name, healthy=False, error=f"{type(exc).__name__}: {exc}")
                )
                db.rollback()
        return report

    # ------------------------------------------------------------- checks
    def check_unconverged_tombstones(
        self, db: Session, dry_run: bool, tenant_id: str = "default"
    ) -> CheckResult:
        """Retry deletions that never converged across all stores."""
        from storage.relational.models import DeletionTombstone
        from storage.vector.qdrant_adapter import vector_adapter
        from storage.relational.turso_sync import delete_memory_from_turso

        name = "unconverged_tombstones"
        pending = (
            db.query(DeletionTombstone)
            .filter(
                DeletionTombstone.tenant_id == tenant_id,
                DeletionTombstone.status != "CONVERGED",
            )
            .limit(self.MAX_REPAIRS_PER_CHECK)
            .all()
        )
        if not pending:
            return CheckResult(name=name, healthy=True)

        repaired = 0
        details = []
        for tombstone in pending:
            missing = [
                store
                for store, done in (
                    ("relational", tombstone.relational_deleted),
                    ("vector", tombstone.vector_deleted),
                    ("cache", tombstone.cache_deleted),
                    ("graph", tombstone.graph_deleted),
                    ("turso", tombstone.turso_deleted),
                )
                if not done
            ]
            details.append(f"{tombstone.memory_id}: missing {','.join(missing)}")

            if dry_run:
                continue

            if not tombstone.vector_deleted:
                try:
                    tombstone.vector_deleted = bool(
                        vector_adapter.delete_embedding(
                            tombstone.memory_id, tenant_id=tombstone.tenant_id
                        )
                    )
                except Exception as exc:
                    logger.warning("tombstone %s vector retry failed: %s", tombstone.memory_id, exc)

            if not tombstone.turso_deleted:
                try:
                    tombstone.turso_deleted = bool(
                        delete_memory_from_turso(
                            tombstone.memory_id, tenant_id=tombstone.tenant_id
                        )
                    )
                except Exception as exc:
                    logger.warning("Turso deletion retry failed (%s)", type(exc).__name__)

            tombstone.retry_count += 1
            if tombstone.is_converged():
                tombstone.status = "CONVERGED"
                repaired += 1
            else:
                tombstone.status = "PENDING_RETRY"

        if not dry_run:
            db.commit()

        return CheckResult(
            name=name,
            healthy=not pending,
            findings=len(pending),
            repaired=repaired,
            details=details,
        )

    def check_orphaned_vectors(
        self, db: Session, dry_run: bool, tenant_id: str = "default"
    ) -> CheckResult:
        """Find vector embeddings whose memory row is gone or soft-deleted.

        A soft delete transitions the record to LifecycleState.DELETED but never
        removes its embedding, so the memory stays retrievable by semantic search
        after being deleted. This check reports those and can evict them.
        """
        from storage.relational.models import LifecycleState, MemoryRecord
        from storage.vector.qdrant_adapter import vector_adapter

        name = "orphaned_vectors"
        with self._vector_offsets_lock:
            offset = self._vector_offsets.get(tenant_id)
        try:
            vector_ids, next_offset = vector_adapter.list_memory_ids(
                tenant_id=tenant_id,
                limit=self.MAX_REPAIRS_PER_CHECK,
                offset=offset,
            )
        except Exception as exc:
            logger.warning("orphaned vector scan failed for tenant %s (%s)", tenant_id, type(exc).__name__)
            return CheckResult(
                name=name,
                healthy=False,
                error=f"Vector scan unavailable: {type(exc).__name__}",
            )

        if not vector_ids:
            if not dry_run:
                with self._vector_offsets_lock:
                    if next_offset is None:
                        self._vector_offsets.pop(tenant_id, None)
                    else:
                        self._vector_offsets[tenant_id] = next_offset
            return CheckResult(
                name=name,
                healthy=True,
                details=["no vectors on the current tenant-scoped page"],
            )

        live_ids = {
            row[0]
            for row in db.query(MemoryRecord.id)
            .filter(
                MemoryRecord.id.in_(vector_ids),
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.lifecycle_state != LifecycleState.DELETED,
            )
            .all()
        }
        orphans = sorted(set(vector_ids) - live_ids)

        repaired = 0
        if not dry_run and orphans:
            for memory_id in orphans[: self.MAX_REPAIRS_PER_CHECK]:
                try:
                    if vector_adapter.delete_embedding(memory_id, tenant_id=tenant_id):
                        repaired += 1
                except Exception as exc:
                    logger.warning("orphan eviction failed for %s: %s", memory_id, exc)
            db.commit()

        # Advance only after a dry-run-free page was completely reconciled. A
        # failed deletion leaves the cursor in place so the next run retries it.
        page_reconciled = not orphans or repaired == len(orphans)
        if not dry_run and page_reconciled:
            with self._vector_offsets_lock:
                if next_offset is None:
                    self._vector_offsets.pop(tenant_id, None)
                else:
                    self._vector_offsets[tenant_id] = next_offset

        return CheckResult(
            name=name,
            healthy=not orphans,
            findings=len(orphans),
            repaired=repaired,
            details=[f"vector present for deleted/absent memory {m}" for m in orphans],
        )

    def check_dangling_supersession(
        self, db: Session, dry_run: bool, tenant_id: str = "default"
    ) -> CheckResult:
        """Clear superseded_by_id pointers at rows that no longer exist."""
        from storage.relational.models import MemoryRecord

        name = "dangling_supersession"
        dangling = (
            db.query(MemoryRecord)
            .filter(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.superseded_by_id.isnot(None),
                ~MemoryRecord.superseded_by_id.in_(
                    db.query(MemoryRecord.id).filter(MemoryRecord.tenant_id == tenant_id)
                ),
            )
            .limit(self.MAX_REPAIRS_PER_CHECK)
            .all()
        )
        if not dangling:
            return CheckResult(name=name, healthy=True)

        repaired = 0
        details = [
            f"{r.id}: superseded_by_id={r.superseded_by_id} does not exist" for r in dangling
        ]
        if not dry_run:
            for record in dangling:
                record.superseded_by_id = None
                repaired += 1
            db.commit()

        return CheckResult(
            name=name,
            healthy=False,
            findings=len(dangling),
            repaired=repaired,
            details=details,
        )

    def check_open_circuits(
        self, db: Session, dry_run: bool, tenant_id: str = "default"
    ) -> CheckResult:
        """Report dependencies whose breaker is open.

        Deliberately does NOT force-reset: the breaker's own half-open probe is
        the correct recovery mechanism, and resetting from here would let callers
        stampede a backend that is still down.
        """
        from core.resilience.circuit_breaker import CircuitState, circuit_registry

        name = "open_circuits"
        snapshot = circuit_registry.snapshot()
        open_names = [
            f"{dep}: state={info['state']}, failures={info['failures']}, "
            f"retry_in={info['retry_after_seconds']}s"
            for dep, info in snapshot.items()
            if info["state"] == CircuitState.OPEN.value
        ]
        half_open = [
            dep for dep, info in snapshot.items() if info["state"] == CircuitState.HALF_OPEN.value
        ]
        details = open_names + [f"{dep}: probing (half-open)" for dep in half_open]
        return CheckResult(
            name=name,
            healthy=not open_names,
            findings=len(open_names),
            repaired=0,
            details=details or ["all registered dependencies closed"],
        )

    def check_decay_backlog(
        self, db: Session, dry_run: bool, tenant_id: str = "default"
    ) -> CheckResult:
        """Report memories past the decay threshold that no job has processed."""
        from datetime import datetime, timedelta, timezone

        from core.config import settings
        from storage.relational.models import LifecycleState, MemoryRecord

        name = "decay_backlog"
        threshold = float(getattr(settings, "DEFAULT_IMPORTANCE_THRESHOLD", 0.50))
        # Mirrors DecayService's age gate: only records old enough to decay count.
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)

        backlog = (
            db.query(MemoryRecord)
            .filter(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.lifecycle_state.in_(
                    [LifecycleState.ACTIVE, LifecycleState.VERIFIED]
                ),
                MemoryRecord.importance > threshold,
                MemoryRecord.created_at < cutoff,
            )
            .count()
        )
        return CheckResult(
            name=name,
            healthy=backlog == 0,
            findings=backlog,
            details=(
                [f"{backlog} memories are past the decay threshold and awaiting a decay run"]
                if backlog
                else ["no decay backlog"]
            ),
        )


#: Process-wide supervisor used by the API and the tests.
self_healing_supervisor = SelfHealingSupervisor()
