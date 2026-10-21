"""
Consolidation and Zero-Work-Lost Pre-Pruning Summarization for Memora.
Enforces the mandatory retention policy:
NO WORK DONE BY ANY AGENT MAY EVER BE LOST.

Before ANY working memory (30-day horizon) or episodic memory (90-day horizon) is purged:
1. Summarize: Automated pre-deletion condensation extracts Agent ID, Timestamp,
   Goal, Action, and Outcome/Artifact into a compact 1-2 sentence milestone summary.
2. Promote to Permanent: Ingests this summary as a permanent record under
   memory_type=MemoryType.SEMANTIC in memora://<agent_name>/milestones with retention_tier=PERMANENT.
3. Purge Raw: Only after the milestone summary is verified written, purge the raw
   verbose token payloads and intermediate scratchpad traces.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union
import uuid

from .models import MemoryItem, MemoryKind, MemoryScope, Lifecycle, Provenance, ScopeKind

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MilestoneSummary:
    agent_id: str
    timestamp: datetime
    goal: str
    action: str
    outcome: str
    summary_text: str

    def to_summary_text(self) -> str:
        return self.summary_text


class MilestoneCondenser:
    """
    Automated pre-deletion condensation engine.
    Extracts Agent ID, Timestamp, Goal, Action, and Outcome/Artifact from
    raw agent episodic and working memories and synthesizes a compact 1-2 sentence
    milestone summary.
    """

    @classmethod
    def extract_fields(
        cls,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        agent_id: str = "unknown_agent",
        created_at: Optional[datetime] = None,
    ) -> Tuple[str, datetime, str, str, str]:
        meta = metadata or {}
        ts = created_at or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        # 1. Goal extraction (from metadata or regex/heuristic)
        goal = meta.get("goal") or meta.get("task") or meta.get("objective")
        if not goal:
            goal_match = re.search(
                r"(?:goal|objective|task|target|purpose)[:\s]+([^.\n;]+)",
                text,
                re.IGNORECASE,
            )
            if goal_match:
                goal = goal_match.group(1).strip()
            else:
                first_sent = text.split(".")[0].strip()
                goal = first_sent[:120] if first_sent else "execute task"

        # 2. Action extraction
        action = meta.get("action") or meta.get("step") or meta.get("operation")
        if not action:
            action_match = re.search(
                r"(?:action|executed|performed|step|implemented)[:\s]+([^.\n;]+)",
                text,
                re.IGNORECASE,
            )
            if action_match:
                action = action_match.group(1).strip()
            else:
                sentences = [s.strip() for s in text.split(".") if s.strip()]
                if len(sentences) > 1:
                    action = sentences[1][:120]
                else:
                    action = "completed task workflow"

        # 3. Outcome/Artifact extraction
        outcome = meta.get("outcome") or meta.get("artifact") or meta.get("result")
        if not outcome:
            outcome_match = re.search(
                r"(?:outcome|artifact|result|status|verified)[:\s]+([^.\n;]+)",
                text,
                re.IGNORECASE,
            )
            if outcome_match:
                outcome = outcome_match.group(1).strip()
            else:
                sentences = [s.strip() for s in text.split(".") if s.strip()]
                if len(sentences) >= 3:
                    outcome = sentences[-1][:140]
                else:
                    outcome = "verified execution and preserved output"

        # Clean punctuation
        goal = goal.strip().rstrip(".")
        action = action.strip().rstrip(".")
        outcome = outcome.strip().rstrip(".")

        return agent_id, ts, goal, action, outcome

    @classmethod
    def condense(
        cls,
        memory: Union[MemoryItem, Any],
        agent_name: Optional[str] = None,
    ) -> MilestoneSummary:
        """
        Synthesize a 1-2 sentence compact milestone summary from a MemoryItem or relational MemoryRecord.
        """
        if isinstance(memory, MemoryItem):
            text = memory.text
            meta = dict(memory.metadata) if memory.metadata else {}
            aid = agent_name or memory.owner_agent_id
            created_at = memory.created_at
        else:
            text = getattr(memory, "content_text", str(memory))
            meta = getattr(memory, "provenance", {}) or {}
            aid = agent_name or getattr(memory, "agent_id", "agent")
            created_at = getattr(memory, "created_at", datetime.now(timezone.utc))

        aid, ts, goal, action, outcome = cls.extract_fields(
            text=text,
            metadata=meta,
            agent_id=aid,
            created_at=created_at,
        )

        time_str = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
        summary_text = (
            f"Milestone [{aid} @ {time_str}]: "
            f"Goal was to {goal}. "
            f"Action taken: {action}, successfully producing outcome: {outcome}."
        )

        return MilestoneSummary(
            agent_id=aid,
            timestamp=ts,
            goal=goal,
            action=action,
            outcome=outcome,
            summary_text=summary_text,
        )


@dataclass(frozen=True, slots=True)
class ConsolidationDecision:
    memory_id: str
    action: str  # SUMMARIZE_AND_PROMOTE, ARCHIVE, DELETE, ACTIVATE, KEEP
    reason: str
    score: float


@dataclass(frozen=True, slots=True)
class ConsolidationConfig:
    min_confidence: float = 0.65
    min_importance: float = 0.40
    merge_similarity_threshold: float = 0.90
    archive_after_days: float = 90.0
    delete_after_days: float = 365.0
    working_memory_horizon_days: float = 30.0
    episodic_memory_horizon_days: float = 90.0
    auto_promote_milestones: bool = True


def consolidation_score(memory: Any, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    if hasattr(memory, "freshness_score"):
        freshness = memory.freshness_score(now)
        authority = memory.authority_score()
        importance = getattr(memory, "importance", 0.5)
        return min(1.0, 0.45 * importance + 0.35 * authority + 0.20 * freshness)
    return 0.5


class Consolidator:
    """
    Turns high-velocity working and episodic memories into permanent semantic milestones.
    Guarantees the core invariant: ZERO WORK DONE BY ANY AGENT MAY EVER BE LOST.
    """

    def __init__(self, config: ConsolidationConfig | None = None):
        self.config = config or ConsolidationConfig()

    def decide(self, memory: Any, now: datetime | None = None) -> ConsolidationDecision:
        now = now or datetime.now(timezone.utc)
        score = consolidation_score(memory, now)

        # Extract creation timestamp
        created_at = getattr(memory, "created_at", None) or datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - created_at).total_seconds() / 86400)

        # Extract memory kind/type and lifecycle
        kind = getattr(memory, "kind", None) or getattr(memory, "memory_type", None)
        kind_str = kind.value.lower() if hasattr(kind, "value") else str(kind).lower()

        lifecycle = getattr(memory, "lifecycle", None) or getattr(memory, "lifecycle_state", None)
        lifecycle_str = lifecycle.value.lower() if hasattr(lifecycle, "value") else str(lifecycle).lower()

        # Check if already a permanent milestone
        provenance = getattr(memory, "provenance", {}) or {}
        if isinstance(provenance, dict) and provenance.get("retention_tier") == "PERMANENT":
            return ConsolidationDecision(memory.id, "KEEP", "permanent milestone", score)

        if lifecycle_str == "deleted":
            return ConsolidationDecision(memory.id, "DELETE", "already deleted", score)

        # MANDATORY ZERO-WORK-LOST RULE:
        # Working memory expiring at 30-day horizon MUST be summarized and promoted
        if kind_str == "working" and age_days >= self.config.working_memory_horizon_days:
            return ConsolidationDecision(
                memory.id,
                "SUMMARIZE_AND_PROMOTE",
                f"working memory 30-day retention horizon reached (age={age_days:.1f}d); zero-loss milestone condensation required",
                score,
            )

        # Episodic memory expiring at 90-day horizon MUST be summarized and promoted
        if kind_str == "episodic" and age_days >= self.config.episodic_memory_horizon_days:
            return ConsolidationDecision(
                memory.id,
                "SUMMARIZE_AND_PROMOTE",
                f"episodic memory 90-day retention horizon reached (age={age_days:.1f}d); zero-loss milestone condensation required",
                score,
            )

        # Before any archived memory is permanently purged at delete_after_days
        if age_days >= self.config.delete_after_days and lifecycle_str == "archived":
            return ConsolidationDecision(
                memory.id,
                "SUMMARIZE_AND_PROMOTE",
                f"archived retention expired (age={age_days:.1f}d); pre-pruning milestone promotion required before purge",
                score,
            )

        confidence = getattr(memory, "confidence", 1.0)
        importance = getattr(memory, "importance", 0.5)

        if confidence < self.config.min_confidence and age_days >= 30:
            return ConsolidationDecision(memory.id, "ARCHIVE", "low confidence aging", score)
        if importance < self.config.min_importance and age_days >= self.config.archive_after_days:
            return ConsolidationDecision(memory.id, "ARCHIVE", "low importance aging", score)
        if lifecycle_str == "candidate" and confidence >= self.config.min_confidence:
            return ConsolidationDecision(memory.id, "ACTIVATE", "confidence threshold", score)

        return ConsolidationDecision(memory.id, "KEEP", "within retention policy", score)

    def promote_milestone(
        self,
        memory: MemoryItem,
        store: Any,
        now: datetime | None = None,
    ) -> MemoryItem:
        """
        Creates and persists a permanent milestone memory item from an expiring memory.
        """
        summary = MilestoneCondenser.condense(memory)
        milestone_id = f"milestone_{uuid.uuid4().hex[:16]}"
        agent_name = memory.owner_agent_id

        milestone_item = MemoryItem(
            id=milestone_id,
            tenant_id=memory.tenant_id,
            owner_agent_id=agent_name,
            kind=MemoryKind.SEMANTIC,
            text=summary.to_summary_text(),
            scope=MemoryScope(
                tenant_id=memory.tenant_id,
                agent_id=agent_name,
                visibility=ScopeKind.AGENT,
            ),
            confidence=1.0,
            importance=1.0,
            lifecycle=Lifecycle.ACTIVE,
            provenance=Provenance(
                source="memora.milestone_condenser",
                author="memora_sovereign",
                authority=1.0,
                evidence_uri=f"memora://{agent_name}/milestones/{milestone_id}",
            ),
            tags=("milestone", "permanent_retention", f"condensed_from:{memory.id}"),
            metadata={
                "retention_tier": "PERMANENT",
                "condensed_from_id": memory.id,
                "condensed_from_kind": memory.kind.value,
                "namespace": f"memora://{agent_name}/milestones",
                "goal": summary.goal,
                "action": summary.action,
                "outcome": summary.outcome,
            },
        )

        persisted = store.put(milestone_item)
        if not persisted or not store.get(milestone_id):
            raise RuntimeError(f"Failed to persist permanent milestone {milestone_id} before pruning {memory.id}")

        logger.info(
            "Promoted permanent milestone %s for agent %s before purging raw memory %s",
            milestone_id,
            agent_name,
            memory.id,
        )
        return persisted

    def promote_and_prune_memory_item(
        self,
        memory: MemoryItem,
        store: Any,
        now: datetime | None = None,
    ) -> Tuple[MemoryItem, MemoryItem]:
        """
        Full zero-loss lifecycle for MemoryItem:
        1. Promotes milestone summary to permanent semantic retention.
        2. Verifies milestone persistence.
        3. Only then transitions raw memory to DELETED.
        """
        milestone = self.promote_milestone(memory, store, now=now)
        purged = store.transition(memory.id, Lifecycle.DELETED)
        return milestone, purged

    def condense_and_prune_relational_record(
        self,
        db: Any,
        record: Any,
        now: datetime | None = None,
    ) -> Tuple[Any, Any]:
        """
        Full zero-loss lifecycle for PostgreSQL relational MemoryRecord:
        1. Condenses raw MemoryRecord into compact milestone summary.
        2. Resolves or creates namespace memora://<agent_name>/milestones.
        3. Writes permanent MemoryRecord with MemoryType.SEMANTIC, retention_tier=PERMANENT,
           and cryptographic HMAC-SHA256 signature.
        4. Flushes and verifies milestone record has an ID in the database.
        5. Deletes raw record and creates DeletionTombstone for multi-store convergence.
        """
        from storage.relational.models import (
            DeletionTombstone,
            MemoryRecord,
            MemoryType,
            LifecycleState,
            NamespaceType,
        )
        from core.identity.service import IdentityService
        from core.security.provenance import sign_memory_provenance

        agent_name = getattr(record.owner, "name", None) or record.agent_id
        summary = MilestoneCondenser.condense(record, agent_name=agent_name)

        milestones_ns_path = f"memora://{agent_name}/milestones"
        ns = IdentityService.get_namespace_by_path(db, milestones_ns_path, tenant_id=record.tenant_id)
        if ns is None:
            ns = IdentityService.resolve_namespace(
                db,
                path=milestones_ns_path,
                default_type=NamespaceType.AGENT_PRIVATE,
                owner_agent_id=record.owner_id,
                tenant_id=record.tenant_id,
            )

        summary_text = summary.to_summary_text()
        content_hash = hashlib.sha256(summary_text.strip().encode("utf-8")).hexdigest()
        milestone_id = f"milestone_{uuid.uuid4().hex[:16]}"

        base_provenance: Dict[str, Any] = {
            "source": "memora.milestone_condenser",
            "retention_tier": "PERMANENT",
            "condensed_from_id": record.id,
            "condensed_from_type": record.memory_type.value if hasattr(record.memory_type, "value") else str(record.memory_type),
            "goal": summary.goal,
            "action": summary.action,
            "outcome": summary.outcome,
        }

        # Cryptographically sign the milestone provenance
        signed_provenance = sign_memory_provenance(
            provenance=base_provenance,
            memory_id=milestone_id,
            tenant_id=record.tenant_id,
            owner_id=record.owner_id,
            content_hash=content_hash,
            memory_type=MemoryType.SEMANTIC.value,
        )

        milestone_record = MemoryRecord(
            id=milestone_id,
            tenant_id=record.tenant_id,
            user_id=record.user_id,
            agent_id=record.agent_id,
            workspace_id=record.workspace_id,
            device_id=record.device_id,
            namespace_id=ns.id,
            owner_id=record.owner_id,
            memory_type=MemoryType.SEMANTIC,
            content_text=summary_text,
            content_hash=content_hash,
            source="memora.milestone_condenser",
            provenance=signed_provenance,
            entities=list(record.entities) if record.entities else [],
            confidence=1.0,
            importance=1.0,
            lifecycle_state=LifecycleState.ACTIVE,
            created_at=datetime.now(timezone.utc),
            observed_at=summary.timestamp,
        )

        db.add(milestone_record)
        db.flush()

        persisted = db.query(MemoryRecord).filter(MemoryRecord.id == milestone_id).first()
        if not persisted:
            db.rollback()
            raise RuntimeError(f"Database verification failed: milestone {milestone_id} not written.")

        raw_id = record.id
        tenant_id = record.tenant_id

        tombstone = DeletionTombstone(
            memory_id=raw_id,
            tenant_id=tenant_id,
            relational_deleted=True,
            vector_deleted=False,
            cache_deleted=False,
            graph_deleted=False,
            turso_deleted=True,
            status="PENDING",
            retry_count=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(tombstone)

        db.delete(record)
        db.flush()

        logger.info(
            "Relational authority: promoted milestone %s and emitted tombstone for purged memory %s",
            milestone_id,
            raw_id,
        )
        return milestone_record, tombstone

    def condense_and_prune_expiring_records(
        self,
        db: Any,
        tenant_id: str = "default",
        now: datetime | None = None,
    ) -> List[Tuple[str, str]]:
        """
        Polls and condenses all expiring working and episodic memories across the tenant.
        Returns a list of (purged_raw_memory_id, promoted_milestone_id).
        """
        from storage.relational.models import MemoryRecord, MemoryType, LifecycleState, Namespace

        now = now or datetime.now(timezone.utc)
        working_cutoff = now - timedelta(days=self.config.working_memory_horizon_days)
        episodic_cutoff = now - timedelta(days=self.config.episodic_memory_horizon_days)

        working_expiring = (
            db.query(MemoryRecord)
            .join(Namespace, MemoryRecord.namespace_id == Namespace.id)
            .filter(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.memory_type == MemoryType.WORKING,
                MemoryRecord.created_at <= working_cutoff,
                MemoryRecord.lifecycle_state != LifecycleState.DELETED,
                ~Namespace.path.like("%/milestones"),
            )
            .all()
        )

        episodic_expiring = (
            db.query(MemoryRecord)
            .join(Namespace, MemoryRecord.namespace_id == Namespace.id)
            .filter(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.memory_type == MemoryType.EPISODIC,
                MemoryRecord.created_at <= episodic_cutoff,
                MemoryRecord.lifecycle_state != LifecycleState.DELETED,
                ~Namespace.path.like("%/milestones"),
            )
            .all()
        )

        results = []
        for rec in working_expiring + episodic_expiring:
            raw_id = rec.id
            milestone_rec, _ = self.condense_and_prune_relational_record(db, rec, now=now)
            results.append((raw_id, milestone_rec.id))

        if results:
            db.commit()
            logger.info("Batch condensed and promoted %d expiring memories for tenant %s", len(results), tenant_id)
        return results


def conflict_score(memory: Any) -> float:
    confidence = getattr(memory, "confidence", 0.5)
    provenance = getattr(memory, "provenance", None)
    authority = getattr(provenance, "authority", 0.5) if provenance else 0.5
    freshness = memory.freshness_score() if hasattr(memory, "freshness_score") else 0.5
    return 0.55 * confidence + 0.25 * authority + 0.20 * freshness


def pick_winner(items: Iterable[Any]) -> Any:
    candidates = list(items)
    if not candidates:
        raise ValueError("no memories")
    return max(candidates, key=lambda m: (conflict_score(m), getattr(m, "id", "")))


class TemporalResolver:
    """Separates observed recency from factual validity; never resolves conflicts by recency alone."""

    def resolve(self, existing: Any, incoming: Any) -> Tuple[Any, str]:
        a = conflict_score(existing)
        b = conflict_score(incoming)
        if b > a:
            return incoming, f"incoming evidence {b:.4f} > existing {a:.4f}"
        if a > b:
            return existing, f"existing evidence {a:.4f} > incoming {b:.4f}"
        
        auth_in = getattr(getattr(incoming, "provenance", None), "authority", 0.5)
        auth_ex = getattr(getattr(existing, "provenance", None), "authority", 0.5)
        if auth_in > auth_ex:
            return incoming, "tie broken by provenance authority"
        return existing, "tie broken deterministically by existing memory id"


def cluster_key(memory: Any) -> Tuple[str, str, Tuple[str, ...]]:
    tenant = getattr(memory, "tenant_id", "default")
    kind = getattr(memory, "kind", None) or getattr(memory, "memory_type", None)
    kind_val = kind.value if hasattr(kind, "value") else str(kind)
    entities = getattr(memory, "entities", ()) or ()
    return tenant, kind_val, tuple(sorted(str(e).casefold() for e in entities))
