"""
Self-reflection ("self-brain") for Memora.

Everything else in Memora reacts to a request: a write is scored, a query is
ranked, a decay run is triggered by a caller. Nothing ever steps back and asks
what the corpus *means*. That is the difference between a store and a memory.

The reflection engine periodically analyses the memory corpus and produces
insights that are themselves stored as memories, so the system's understanding of
itself compounds over time. Five reflections, each grounded in real data rather
than a heuristic that looks plausible:

  recurring_theme      topics that keep coming back, weighted by how many
                       distinct agents and days they span — a theme one agent
                       mentioned once is not a pattern
  contradiction        pairs of active memories that assert conflicting values for
                       the same subject, surfaced for supersession rather than
                       silently resolved
  stale_knowledge      high-importance memories that nothing has touched in a
                       long time, which are quietly misleading
  knowledge_gap        subjects asked about in queries that the corpus cannot
                       answer, derived from the event log
  agent_specialisation which agent actually holds authority on which topic, from
                       what they have written rather than from their declared role

Insights are written back as MemoryType.EXPERIENCE records owned by the memora
agent, tagged so a later reflection can tell what it has already concluded and
avoid re-deriving the same insight every run.

The engine never mutates existing memories. It observes and concludes; acting on
a conclusion (superseding, decaying, promoting) stays an explicit decision.
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy.orm import Session

from core.identity.service import IdentityService
from storage.relational.models import (
    Agent,
    EventLog,
    LifecycleState,
    MemoryRecord,
    MemoryType,
)

logger = logging.getLogger(__name__)

#: Words that carry no topical signal.
_STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "but", "is", "are", "was", "were", "be",
    "been", "being", "to", "of", "in", "on", "at", "by", "for", "with", "from",
    "as", "into", "about", "that", "this", "these", "those", "it", "its",
    "we", "our", "you", "your", "they", "their", "he", "she", "his", "her",
    "not", "no", "yes", "do", "does", "did", "have", "has", "had", "will",
    "would", "can", "could", "should", "must", "may", "might", "shall",
    "when", "where", "which", "who", "whom", "how", "what", "why", "all",
    "any", "some", "more", "most", "other", "than", "then", "there", "here",
    "after", "before", "during", "while", "because", "so", "if", "only",
    "just", "very", "also", "each", "every", "both", "either", "neither",
    "memory", "memora", "record", "note", "notes", "item", "entry",
}

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_\-]{2,}")


def _topics(text: str, min_length: int = 4) -> List[str]:
    """Extract topical tokens from free text."""
    if not text:
        return []
    return [
        tok
        for tok in _TOKEN_RE.findall(text.lower())
        if len(tok) >= min_length and tok not in _STOPWORDS and not tok.isdigit()
    ]


@dataclass
class Insight:
    """One conclusion the engine drew about the corpus."""

    kind: str
    subject: str
    summary: str
    confidence: float
    evidence: List[str] = field(default_factory=list)
    suggested_action: Optional[str] = None

    def fingerprint(self) -> str:
        """Stable identity, so a repeat run does not re-store the same insight."""
        return f"{self.kind}:{self.subject}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "summary": self.summary,
            "confidence": round(self.confidence, 4),
            "evidence": self.evidence[:10],
            "suggested_action": self.suggested_action,
            "fingerprint": self.fingerprint(),
        }


@dataclass
class ReflectionReport:
    """Outcome of one reflection run."""

    tenant_id: str
    insights: List[Insight] = field(default_factory=list)
    stored: int = 0
    scanned_memories: int = 0
    duration_ms: float = 0.0
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def by_kind(self) -> Dict[str, int]:
        counts: Counter = Counter(i.kind for i in self.insights)
        return dict(sorted(counts.items()))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "scanned_memories": self.scanned_memories,
            "insight_count": len(self.insights),
            "stored": self.stored,
            "by_kind": self.by_kind,
            "duration_ms": round(self.duration_ms, 2),
            "created_at": self.created_at,
            "insights": [i.to_dict() for i in self.insights],
        }


class ReflectionEngine:
    """Analyses the corpus and records what it concludes."""

    #: A theme must span at least this many distinct days to count as recurring,
    #: so one busy afternoon does not look like a pattern.
    MIN_THEME_DAYS = 2
    #: ...and at least this many memories.
    MIN_THEME_OCCURRENCES = 3
    #: Ignore memories older than this when looking for current themes.
    THEME_WINDOW_DAYS = 90
    #: High-importance memories untouched for this long are "stale".
    STALE_DAYS = 120
    STALE_IMPORTANCE = 0.7
    #: Cap on records examined, so reflection cost is bounded on a large corpus.
    MAX_SCAN = 5000

    # ------------------------------------------------------------------ run
    def reflect(
        self,
        db: Session,
        tenant_id: str = "default",
        store_insights: bool = True,
    ) -> ReflectionReport:
        """Run every reflection over the tenant's corpus."""
        started = datetime.now(timezone.utc)

        records = self._load_corpus(db, tenant_id)
        report = ReflectionReport(tenant_id=tenant_id, scanned_memories=len(records))

        if not records:
            report.duration_ms = (
                datetime.now(timezone.utc) - started
            ).total_seconds() * 1000
            return report

        owners = {a.id: a.name for a in db.query(Agent).filter(Agent.tenant_id == tenant_id).all()}

        reflections = [
            self.recurring_themes,
            self.contradictions,
            self.stale_knowledge,
            self.knowledge_gaps,
            self.agent_specialisation,
        ]
        for reflection in reflections:
            try:
                report.insights.extend(reflection(db, records, owners, tenant_id))
            except Exception:
                logger.exception("reflection %s failed", reflection.__name__)
                db.rollback()

        already_known = self._known_fingerprints(db, tenant_id)
        novel = [i for i in report.insights if i.fingerprint() not in already_known]

        if store_insights and novel:
            report.stored = self._store_insights(db, novel, tenant_id)

        report.duration_ms = (datetime.now(timezone.utc) - started).total_seconds() * 1000
        return report

    # ------------------------------------------------------------- corpus
    def _load_corpus(self, db: Session, tenant_id: str) -> List[MemoryRecord]:
        """The memories to reason about.

        Excludes insights the reflection engine itself previously stored. Without
        this the engine analyses its own conclusions, so each run invents new
        topics ("reflection", "memories", ...) derived from the text of the last
        run's insights, and the corpus of reflections grows without bound.
        """
        records = (
            db.query(MemoryRecord)
            .filter(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.lifecycle_state.in_(
                    [LifecycleState.ACTIVE, LifecycleState.VERIFIED]
                ),
            )
            .order_by(MemoryRecord.created_at.desc())
            .limit(self.MAX_SCAN)
            .all()
        )
        return [r for r in records if not self._is_reflection(r)]

    @staticmethod
    def _is_reflection(record: MemoryRecord) -> bool:
        provenance = record.provenance
        return (
            isinstance(provenance, dict)
            and provenance.get("source") == "reflection_engine"
        )

    # --------------------------------------------------------- reflections
    def recurring_themes(
        self,
        db: Session,
        records: Iterable[MemoryRecord],
        owners: Dict[str, str],
        tenant_id: str,
    ) -> List[Insight]:
        """Topics that recur across multiple agents and multiple days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.THEME_WINDOW_DAYS)

        by_topic_days: Dict[str, Set[str]] = defaultdict(set)
        by_topic_agents: Dict[str, Set[str]] = defaultdict(set)
        by_topic_count: Counter = Counter()

        for record in records:
            created = record.created_at
            if created is not None:
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created < cutoff:
                    continue
            day = created.date().isoformat() if created else "unknown"
            for topic in set(_topics(record.content_text)):
                by_topic_count[topic] += 1
                by_topic_days[topic].add(day)
                by_topic_agents[topic].add(record.owner_id)

        insights: List[Insight] = []
        for topic, count in by_topic_count.most_common():
            days = len(by_topic_days[topic])
            agents = len(by_topic_agents[topic])
            if count < self.MIN_THEME_OCCURRENCES or days < self.MIN_THEME_DAYS:
                continue
            if agents < 2:
                # A single agent repeating itself is a habit, not a theme.
                continue
            confidence = min(
                0.95, 0.4 + (min(count, 20) / 40) + (min(days, 10) / 25) + (min(agents, 5) / 25)
            )
            names = sorted({owners.get(a, a[:8]) for a in by_topic_agents[topic]})
            insights.append(
                Insight(
                    kind="recurring_theme",
                    subject=topic,
                    summary=(
                        f"'{topic}' recurs across {count} memories, {days} distinct days "
                        f"and {agents} agents ({', '.join(names)})"
                    ),
                    confidence=confidence,
                    evidence=[f"{count} memories", f"{days} days", f"agents: {', '.join(names)}"],
                    suggested_action="consider consolidating into a semantic memory",
                )
            )
        return insights[:20]

    def contradictions(
        self,
        db: Session,
        records: Iterable[MemoryRecord],
        owners: Dict[str, str],
        tenant_id: str,
    ) -> List[Insight]:
        """Active memories that share a subject but assert different values.

        Only pairs sharing a distinctive subject token AND a numeric/measured
        value are reported. Comparing free prose produces endless false
        positives; a differing number attached to the same subject is a real
        conflict worth resolving.
        """
        value_re = re.compile(r"(\d+(?:\.\d+)?)\s*([a-zA-Z%°]{1,8})")

        by_subject: Dict[str, List[Tuple[MemoryRecord, str, str]]] = defaultdict(list)
        for record in records:
            text = record.content_text or ""
            measurements = value_re.findall(text)
            if not measurements:
                continue
            topics = set(_topics(text))
            for value, unit in measurements:
                for topic in topics:
                    by_subject[topic].append((record, value, unit.lower()))

        insights: List[Insight] = []
        seen: Set[str] = set()
        for subject, entries in by_subject.items():
            if len(entries) < 2:
                continue
            # Group by unit so "42 Nm" is only compared against other Nm values.
            by_unit: Dict[str, Set[str]] = defaultdict(set)
            for _record, value, unit in entries:
                by_unit[unit].add(value)
            for unit, values in by_unit.items():
                if len(values) < 2:
                    continue
                key = f"{subject}:{unit}"
                if key in seen:
                    continue
                seen.add(key)
                conflicting = sorted(entries, key=lambda e: e[0].created_at or datetime.min.replace(tzinfo=timezone.utc))
                ids = [r.id for r, _v, _u in conflicting][:4]
                owners_involved = sorted({owners.get(r.owner_id, r.owner_id[:8]) for r, _v, _u in conflicting})
                insights.append(
                    Insight(
                        kind="contradiction",
                        subject=f"{subject} ({unit})",
                        summary=(
                            f"conflicting values for '{subject}' in {unit}: "
                            f"{', '.join(sorted(values))}"
                        ),
                        confidence=0.75,
                        evidence=[f"memory {i}" for i in ids] + [f"agents: {', '.join(owners_involved)}"],
                        suggested_action="resolve via SupersessionService.resolve_contradiction_and_supersede",
                    )
                )
        return insights[:20]

    def stale_knowledge(
        self,
        db: Session,
        records: Iterable[MemoryRecord],
        owners: Dict[str, str],
        tenant_id: str,
    ) -> List[Insight]:
        """High-importance memories nothing has revisited in a long time."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.STALE_DAYS)
        stale: List[MemoryRecord] = []
        for record in records:
            if (record.importance or 0) < self.STALE_IMPORTANCE:
                continue
            created = record.created_at
            if created is None:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created < cutoff:
                stale.append(record)

        stale.sort(key=lambda r: (r.importance or 0), reverse=True)
        return [
            Insight(
                kind="stale_knowledge",
                subject=(r.content_text or "")[:60],
                summary=(
                    f"importance {r.importance:.2f} but untouched for "
                    f"{(datetime.now(timezone.utc) - (r.created_at.replace(tzinfo=timezone.utc) if r.created_at.tzinfo is None else r.created_at)).days} days"
                ),
                confidence=0.6,
                evidence=[f"memory {r.id}", f"owner {owners.get(r.owner_id, r.owner_id[:8])}"],
                suggested_action="re-verify or allow decay to archive it",
            )
            for r in stale[:20]
        ]

    def knowledge_gaps(
        self,
        db: Session,
        records: Iterable[MemoryRecord],
        owners: Dict[str, str],
        tenant_id: str,
    ) -> List[Insight]:
        """Subjects that were asked about but the corpus cannot answer.

        Derived from the event log: a query event whose topics have little or no
        coverage in stored memories is a gap worth filling.
        """
        corpus_topics: Counter = Counter()
        for record in records:
            corpus_topics.update(set(_topics(record.content_text)))

        asked: Counter = Counter()
        try:
            events = (
                db.query(EventLog)
                .filter(
                    EventLog.tenant_id == tenant_id,
                    EventLog.event_type.in_(["memory.query", "query", "context.build"]),
                )
                .order_by(EventLog.id.desc())
                .limit(500)
                .all()
            )
        except Exception:
            events = []

        for event in events:
            payload = event.payload if isinstance(event.payload, dict) else {}
            text = str(payload.get("query") or payload.get("query_text") or "")
            asked.update(set(_topics(text)))

        insights: List[Insight] = []
        for topic, times in asked.most_common():
            coverage = corpus_topics.get(topic, 0)
            if coverage >= 2 or times < 2:
                continue
            insights.append(
                Insight(
                    kind="knowledge_gap",
                    subject=topic,
                    summary=(
                        f"'{topic}' was asked about {times} time(s) but only "
                        f"{coverage} memory/memories cover it"
                    ),
                    confidence=min(0.8, 0.35 + (times / 20)),
                    evidence=[f"{times} queries", f"{coverage} memories"],
                    suggested_action="capture knowledge on this subject",
                )
            )
        return insights[:20]

    def agent_specialisation(
        self,
        db: Session,
        records: Iterable[MemoryRecord],
        owners: Dict[str, str],
        tenant_id: str,
    ) -> List[Insight]:
        """Which agent actually holds authority on a topic, from what it wrote.

        Derived from the corpus rather than the declared role, so it surfaces
        where real expertise sits — which may not match the org chart.
        """
        by_topic_agent: Dict[str, Counter] = defaultdict(Counter)
        for record in records:
            name = owners.get(record.owner_id)
            if not name:
                continue
            for topic in set(_topics(record.content_text)):
                by_topic_agent[topic][name] += 1

        insights: List[Insight] = []
        for topic, counts in by_topic_agent.items():
            if sum(counts.values()) < self.MIN_THEME_OCCURRENCES:
                continue
            leader, leader_count = counts.most_common(1)[0]
            total = sum(counts.values())
            share = leader_count / total
            if share < 0.5:
                continue  # no clear authority
            insights.append(
                Insight(
                    kind="agent_specialisation",
                    subject=topic,
                    summary=(
                        f"'{leader}' holds {leader_count}/{total} memories on '{topic}' "
                        f"({share:.0%} share)"
                    ),
                    confidence=min(0.9, 0.4 + share / 2),
                    evidence=[f"{leader_count} of {total} memories"],
                    suggested_action=f"route '{topic}' questions to {leader}",
                )
            )
        insights.sort(key=lambda i: i.confidence, reverse=True)
        return insights[:20]

    # ----------------------------------------------------------- persistence
    def _known_fingerprints(self, db: Session, tenant_id: str) -> Set[str]:
        """Fingerprints already stored, so reflection does not repeat itself."""
        rows = (
            db.query(MemoryRecord.provenance)
            .filter(
                MemoryRecord.tenant_id == tenant_id,
                MemoryRecord.memory_type == MemoryType.EXPERIENCE,
            )
            .all()
        )
        known: Set[str] = set()
        for (provenance,) in rows:
            if isinstance(provenance, dict):
                fp = provenance.get("reflection_fingerprint")
                if fp:
                    known.add(str(fp))
        return known

    def _store_insights(
        self, db: Session, insights: List[Insight], tenant_id: str
    ) -> int:
        """Write insights back as experience memories owned by the memora agent."""
        owner = IdentityService.get_agent_by_name(db, "memora", tenant_id=tenant_id)
        if not owner:
            owner = IdentityService.register_agent(
                db, "memora", role="supervisor", tenant_id=tenant_id
            )
        namespace = IdentityService.resolve_namespace(
            db, "memora://memora/reflections", owner_agent_id=owner.id, tenant_id=tenant_id
        )

        stored = 0
        for insight in insights:
            record = MemoryRecord(
                tenant_id=tenant_id,
                namespace_id=namespace.id,
                owner_id=owner.id,
                memory_type=MemoryType.EXPERIENCE,
                content_text=f"[REFLECTION:{insight.kind}] {insight.summary}",
                confidence=insight.confidence,
                importance=max(0.4, min(0.9, insight.confidence)),
                lifecycle_state=LifecycleState.ACTIVE,
                provenance={
                    "source": "reflection_engine",
                    "reflection_kind": insight.kind,
                    "reflection_fingerprint": insight.fingerprint(),
                    "reflection_subject": insight.subject,
                    "reflection_evidence": insight.evidence[:10],
                    "suggested_action": insight.suggested_action,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            db.add(record)
            stored += 1
        db.commit()
        return stored


#: Process-wide engine used by the API and the tests.
reflection_engine = ReflectionEngine()
