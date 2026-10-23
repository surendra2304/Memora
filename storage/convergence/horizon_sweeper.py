"""
Automated Horizon Sweeper Worker for Memora.
Enforces the mandatory Zero-Work-Lost Pre-Pruning Retention Policy:
NO WORK DONE BY ANY AGENT MAY EVER BE LOST.

Autonomously runs periodic condensation and promotion passes across
working memory (30-day horizon) and episodic memory (90-day horizon),
extracting compact milestone summaries into memora://<agent>/milestones
with permanent retention before purging verbose raw payloads.
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from storage.relational.models import MemoryRecord
from storage.relational.session import SessionLocal
from memora_upgrade.consolidation import Consolidator, ConsolidationConfig

logger = logging.getLogger(__name__)


class HorizonSweeperWorker:
    """
    Autonomous background worker that polls expiring working and episodic memories
    across all agent namespaces and executes pre-pruning condensation passes.
    """

    def __init__(
        self,
        consolidator: Optional[Consolidator] = None,
        default_tenant_id: str = "default",
    ):
        self.consolidator = consolidator or Consolidator(ConsolidationConfig())
        self.default_tenant_id = default_tenant_id
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def run_sweep_pass(
        self,
        db: Session,
        tenant_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Execute pre-pruning summarization and promotion pass.
        If tenant_id is provided, sweeps only that tenant; otherwise scans all active tenants.
        """
        now = now or datetime.now(timezone.utc)
        if tenant_id:
            tenant_ids = [tenant_id]
        else:
            found = [
                row[0]
                for row in db.query(MemoryRecord.tenant_id).distinct().all()
                if row[0]
            ]
            tenant_ids = found if found else [self.default_tenant_id]
            if self.default_tenant_id not in tenant_ids:
                tenant_ids.append(self.default_tenant_id)

        total_condensed = 0
        sweep_details: List[Dict[str, Any]] = []

        for tid in tenant_ids:
            try:
                results = self.consolidator.condense_and_prune_expiring_records(
                    db,
                    tenant_id=tid,
                    now=now,
                )
                total_condensed += len(results)
                if results:
                    sweep_details.append(
                        {
                            "tenant_id": tid,
                            "condensed_count": len(results),
                            "promoted_milestones": [
                                {"raw_id": raw_id, "milestone_id": m_id}
                                for raw_id, m_id in results
                            ],
                        }
                    )
            except Exception as exc:
                logger.error("Horizon sweeper failed for tenant %s: %s", tid, exc)
                db.rollback()

        return {
            "status": "completed",
            "timestamp": now.isoformat(),
            "tenants_scanned": len(tenant_ids),
            "total_condensed": total_condensed,
            "details": sweep_details,
        }

    def start_background_worker(self, interval_seconds: Optional[float] = None) -> None:
        """Launch background worker thread if not already running."""
        with self._lock:
            if self._running:
                return
            if interval_seconds is None:
                env_val = os.getenv("MEMORA_SWEEPER_INTERVAL_SECONDS", "300.0").strip()
                try:
                    interval_seconds = float(env_val)
                except ValueError:
                    interval_seconds = 300.0

            self._running = True
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                args=(interval_seconds,),
                daemon=True,
                name="HorizonSweeperWorkerThread",
            )
            self._thread.start()
            logger.info(
                "HorizonSweeperWorker background thread started (interval=%.1fs).",
                interval_seconds,
            )

    def stop_background_worker(self) -> None:
        """Signal background worker thread to stop and wait for termination."""
        with self._lock:
            if not self._running:
                return
            self._stop_event.set()
            self._running = False
            if self._thread and self._thread.is_alive():
                self._thread.join(timeout=5.0)
            logger.info("HorizonSweeperWorker background thread stopped.")

    def _worker_loop(self, interval_seconds: float) -> None:
        while not self._stop_event.is_set():
            try:
                db = SessionLocal()
                try:
                    self.run_sweep_pass(db)
                finally:
                    db.close()
            except Exception as exc:
                logger.error("Error in HorizonSweeperWorker loop: %s", exc)

            self._stop_event.wait(interval_seconds)


# Global singleton instance for autonomous pre-pruning summarization
horizon_sweeper = HorizonSweeperWorker()
