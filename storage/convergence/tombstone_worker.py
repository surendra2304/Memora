"""
Multi-Store Deletion Convergence Worker for Memora
Guarantees eventual consistency and hard delete convergence across
relational SQL, Qdrant vector store, Redis/in-memory cache, knowledge graph relations,
and optional Turso cloud replicas using DeletionTombstone outbox records.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional
from sqlalchemy import or_
from sqlalchemy.orm import Session

from storage.relational.models import DeletionTombstone, MemoryRecord, MemoryRelationship
from storage.relational.session import SessionLocal
from storage.vector.qdrant_adapter import vector_adapter
from core.cache.memory_cache import memory_cache
from storage.relational.turso_sync import delete_memory_from_turso, turso_replica_enabled

logger = logging.getLogger(__name__)


class TombstoneConvergenceWorker:
    """
    Autonomous worker that polls unconverged DeletionTombstones and executes
    convergent multi-store deletions across all storage layers.
    """

    def __init__(
        self,
        batch_size: int = 100,
        max_retries: int = 5,
        default_tenant_id: str = "default",
    ):
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.default_tenant_id = default_tenant_id
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def process_single_tombstone(self, db: Session, tombstone: DeletionTombstone) -> bool:
        """
        Execute multi-store deletion convergence for a single tombstone.
        Returns True if the tombstone is fully converged across all stores.
        """
        record_tenant_id = db.query(MemoryRecord.tenant_id).filter(
            MemoryRecord.id == tombstone.memory_id
        ).scalar()
        if record_tenant_id is not None and record_tenant_id != tombstone.tenant_id:
            logger.warning(
                "Tombstone %s tenant %s does not own memory for tenant %s",
                tombstone.memory_id,
                tombstone.tenant_id,
                record_tenant_id,
            )
            tombstone.retry_count += 1
            if tombstone.retry_count >= self.max_retries:
                tombstone.status = "FAILED"
            else:
                tombstone.status = "PENDING_RETRY"
            return False

        # 1. Vector Store Deletion
        if not tombstone.vector_deleted:
            try:
                vec_ok = vector_adapter.delete_embedding(
                    tombstone.memory_id,
                    tenant_id=tombstone.tenant_id,
                )
                tombstone.vector_deleted = bool(vec_ok)
            except Exception as exc:
                logger.warning(
                    "Tombstone %s vector deletion retry failed: %s",
                    tombstone.memory_id,
                    exc,
                )

        # 2. Knowledge Graph Relationship Deletion
        if not tombstone.graph_deleted:
            try:
                with db.begin_nested():
                    db.query(MemoryRelationship).filter(
                        or_(
                            MemoryRelationship.source_memory_id == tombstone.memory_id,
                            MemoryRelationship.target_memory_id == tombstone.memory_id,
                        )
                    ).delete(synchronize_session=False)
                tombstone.graph_deleted = True
            except Exception as exc:
                logger.warning(
                    "Tombstone %s graph deletion retry failed: %s",
                    tombstone.memory_id,
                    exc,
                )

        # 3. Cache Eviction (Tenant-scoped LRU + Redis if enabled)
        if not tombstone.cache_deleted:
            try:
                memory_cache.delete(
                    tenant_id=tombstone.tenant_id,
                    key=tombstone.memory_id,
                )
                tombstone.cache_deleted = True
            except Exception as exc:
                logger.warning(
                    "Tombstone %s cache deletion retry failed: %s",
                    tombstone.memory_id,
                    exc,
                )

        # 4. Turso Cloud Write-Through Replica Deletion
        if not tombstone.turso_deleted:
            try:
                if turso_replica_enabled():
                    turso_ok = delete_memory_from_turso(
                        tombstone.memory_id,
                        tenant_id=tombstone.tenant_id,
                    )
                    tombstone.turso_deleted = bool(turso_ok)
                else:
                    tombstone.turso_deleted = True
            except Exception as exc:
                logger.warning(
                    "Tombstone %s Turso deletion retry failed: %s",
                    tombstone.memory_id,
                    exc,
                )

        # 5. Relational SQL Hard Deletion
        if not tombstone.relational_deleted:
            try:
                with db.begin_nested():
                    record = db.query(MemoryRecord).filter(
                        MemoryRecord.id == tombstone.memory_id,
                        MemoryRecord.tenant_id == tombstone.tenant_id,
                    ).first()
                    if record is not None:
                        db.delete(record)
                        db.flush()
                tombstone.relational_deleted = True
            except Exception as exc:
                logger.warning(
                    "Tombstone %s relational deletion retry failed: %s",
                    tombstone.memory_id,
                    exc,
                )

        # 6. Evaluation and State Transition
        tombstone.retry_count += 1
        if tombstone.is_converged():
            tombstone.status = "CONVERGED"
        elif tombstone.retry_count >= self.max_retries:
            tombstone.status = "FAILED"
        else:
            tombstone.status = "PENDING_RETRY"

        return tombstone.is_converged()

    def run_convergence_pass(
        self,
        db: Session,
        tenant_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Execute one synchronous convergence pass over unconverged tombstones.
        Returns a receipt describing actions taken and final states.
        """
        max_items = limit or self.batch_size
        query = db.query(DeletionTombstone).filter(
            DeletionTombstone.status.in_(["PENDING", "PENDING_RETRY", "DELETE_REQUESTED"])
        )
        if tenant_id:
            query = query.filter(DeletionTombstone.tenant_id == tenant_id)

        pending = query.order_by(DeletionTombstone.created_at.asc()).limit(max_items).all()

        converged_count = 0
        failed_count = 0
        pending_count = 0
        details: List[str] = []

        for tombstone in pending:
            converged = self.process_single_tombstone(db, tombstone)
            if converged:
                converged_count += 1
                details.append(f"{tombstone.memory_id}: CONVERGED")
            elif tombstone.status == "FAILED":
                failed_count += 1
                details.append(f"{tombstone.memory_id}: FAILED (retries exhausted)")
            else:
                pending_count += 1
                details.append(f"{tombstone.memory_id}: PENDING_RETRY (try {tombstone.retry_count})")

        if pending:
            db.commit()

        return {
            "processed": len(pending),
            "converged": converged_count,
            "pending": pending_count,
            "failed": failed_count,
            "details": details[:20],
        }

    def start_background_worker(self, interval_seconds: float = 15.0) -> None:
        """Launch background worker thread if not already running."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                args=(interval_seconds,),
                daemon=True,
                name="TombstoneConvergenceWorkerThread",
            )
            self._thread.start()
            logger.info("TombstoneConvergenceWorker background thread started.")

    def stop_background_worker(self) -> None:
        """Signal background worker thread to stop and wait for termination."""
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            self._running = False
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=5.0)
            logger.info("TombstoneConvergenceWorker background thread stopped.")

    def _worker_loop(self, interval_seconds: float) -> None:
        while not self._stop_event.is_set():
            try:
                db = SessionLocal()
                try:
                    self.run_convergence_pass(db)
                finally:
                    db.close()
            except Exception as exc:
                logger.error("Error in TombstoneConvergenceWorker loop: %s", exc)

            self._stop_event.wait(interval_seconds)


# Global singleton instance for system convergence
tombstone_worker = TombstoneConvergenceWorker()
