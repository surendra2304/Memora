"""
Memora/apps/api/routers/knowledge_consolidation.py — Memora Knowledge Consolidation Engine.

Extends Memora's capabilities with:
1. Knowledge Merging: merge related knowledge entries
2. Conflict Resolution: resolve conflicting knowledge
3. Knowledge Pruning: remove outdated or redundant knowledge
4. Knowledge Validation: validate knowledge against facts
5. Knowledge Enrichment: enrich knowledge with related information
6. Knowledge Distillation: distill large knowledge bases
7. Knowledge Consolidation Reports: generate consolidation reports
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

router = APIRouter()


@dataclass
class KnowledgeEntry:
    """A knowledge entry to be consolidated."""
    entry_id: str
    content: str
    source: str
    confidence: float = 0.5
    created_at: float = 0.0
    last_updated: float = 0.0
    access_count: int = 0
    tags: list[str] = field(default_factory=list)
    is_verified: bool = False
    scope: str = ""
    scope_id: str = ""


@dataclass
class ConsolidationResult:
    """Result of knowledge consolidation."""
    consolidated_id: str
    content: str
    merged_from: list[str]
    conflicts_resolved: list[str]
    confidence: float
    tags: list[str]
    status: str  # consolidated, failed, partial


@dataclass
class Conflict:
    """A conflict between knowledge entries."""
    conflict_id: str
    entry_a_id: str
    entry_b_id: str
    description: str
    resolution: str
    resolution_reason: str
    resolved_at: float = 0.0


@dataclass
class ValidationResult:
    """Result of knowledge validation."""
    entry_id: str
    is_valid: bool
    confidence: float
    issues: list[str]
    evidence: list[str]
    recommendation: str


@dataclass
class EnrichmentResult:
    """Result of knowledge enrichment."""
    entry_id: str
    original_content: str
    enriched_content: str
    added_info: list[str]
    related_topics: list[str]
    confidence: float


class KnowledgeConsolidation:
    """Knowledge consolidation engine."""

    def __init__(self):
        self._entries: dict[str, KnowledgeEntry] = {}
        self._consolidations: dict[str, ConsolidationResult] = {}
        self._conflicts: list[Conflict] = []
        self._validations: dict[str, ValidationResult] = {}
        self._enrichments: dict[str, EnrichmentResult] = {}

    # ── Knowledge Merging ──────────────────────────────────────────────────

    def merge_entries(
        self,
        entry_ids: list[str],
    ) -> ConsolidationResult | None:
        """Merge multiple knowledge entries into one."""
        entries = [self._entries.get(eid) for eid in entry_ids]
        entries = [e for e in entries if e]

        if len(entries) < 2:
            return None

        # Combine content
        combined_content = " ".join(e.content for e in entries)

        # Combine tags
        all_tags = set()
        for e in entries:
            all_tags.update(e.tags)

        # Calculate confidence (weighted by access count and verification)
        total_weight = 0.0
        total_confidence = 0.0
        for e in entries:
            weight = 1.0 + e.access_count * 0.1 + (1.0 if e.is_verified else 0.0)
            total_weight += weight
            total_confidence += e.confidence * weight

        merged_confidence = total_confidence / max(1.0, total_weight)

        # Check for conflicts
        conflicts_resolved = self._check_conflicts(entries)

        # Create consolidated entry
        consolidated_id = hashlib.md5(
            f"consolidated:{time.time()}".encode()
        ).hexdigest()[:12]

        result = ConsolidationResult(
            consolidated_id=consolidated_id,
            content=combined_content[:1000],
            merged_from=entry_ids,
            conflicts_resolved=conflicts_resolved,
            confidence=merged_confidence,
            tags=list(all_tags),
            status="consolidated",
        )

        self._consolidations[consolidated_id] = result
        return result

    def _check_conflicts(self, entries: list[KnowledgeEntry]) -> list[str]:
        """Check for conflicts between entries."""
        resolved = []

        for i, e1 in enumerate(entries):
            for e2 in entries[i+1:]:
                # Check for contradictory content
                if self._detect_contradiction(e1.content, e2.content):
                    # Resolve by preferring the more recent or verified entry
                    if e1.is_verified and not e2.is_verified:
                        winner = e1.entry_id
                    elif e2.is_verified and not e1.is_verified:
                        winner = e2.entry_id
                    elif e1.last_updated > e2.last_updated:
                        winner = e1.entry_id
                    else:
                        winner = e2.entry_id

                    resolved.append(f"Conflict between {e1.entry_id} and {e2.entry_id} resolved in favor of {winner}")

                    # Record conflict
                    conflict = Conflict(
                        conflict_id=hashlib.md5(
                            f"conflict:{e1.entry_id}:{e2.entry_id}:{time.time()}".encode()
                        ).hexdigest()[:12],
                        entry_a_id=e1.entry_id,
                        entry_b_id=e2.entry_id,
                        description="Contradictory content detected",
                        resolution=winner,
                        resolution_reason="Preferred verified/recent entry",
                        resolved_at=time.time(),
                    )
                    self._conflicts.append(conflict)

        return resolved

    def _detect_contradiction(self, text_a: str, text_b: str) -> bool:
        """Detect if two texts contradict each other."""
        # Simple contradiction detection
        a_lower = text_a.lower()
        b_lower = text_b.lower()

        # Check for negation patterns
        negation_patterns = [
            ("always", "never"),
            ("increases", "decreases"),
            ("positive", "negative"),
            ("true", "false"),
            ("yes", "no"),
        ]

        for pos, neg in negation_patterns:
            if pos in a_lower and neg in b_lower:
                return True
            if neg in a_lower and pos in b_lower:
                return True

        return False

    # ── Knowledge Pruning ───────────────────────────────────────────────────

    def prune_knowledge(
        self,
        entries: dict[str, KnowledgeEntry],
        max_age_days: float = 365.0,
        min_confidence: float = 0.1,
        max_duplicates: int = 3,
    ) -> dict[str, Any]:
        """Prune outdated, low-confidence, or duplicate knowledge."""
        now = time.time()
        to_remove = []

        for entry_id, entry in entries.items():
            # Check age
            age = now - entry.last_updated
            if age > max_age_days * 86400 and not entry.is_verified:
                to_remove.append((entry_id, "outdated"))

            # Check confidence
            if entry.confidence < min_confidence:
                to_remove.append((entry_id, "low_confidence"))

            # Check for duplicates
            duplicates = self._find_duplicates(entry, entries)
            if len(duplicates) > max_duplicates:
                # Keep the one with highest confidence
                if entry.confidence < max(e.confidence for e in duplicates.values()):
                    to_remove.append((entry_id, "duplicate"))

        return {
            "total_entries": len(entries),
            "entries_to_remove": len(to_remove),
            "removals": [{"id": eid, "reason": reason} for eid, reason in to_remove],
            "recommendation": (
                f"Remove {len(to_remove)} entries" if to_remove else
                "Knowledge base is well-maintained"
            ),
        }

    def _find_duplicates(
        self,
        entry: KnowledgeEntry,
        all_entries: dict[str, KnowledgeEntry],
    ) -> dict[str, KnowledgeEntry]:
        """Find duplicate entries."""
        duplicates = {}

        for other_id, other in all_entries.items():
            if other_id == entry.entry_id:
                continue

            # Check text similarity
            similarity = self._text_similarity(entry.content, other.content)
            if similarity > 0.8:
                duplicates[other_id] = other

        return duplicates

    def _text_similarity(self, text_a: str, text_b: str) -> float:
        """Calculate text similarity."""
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())

        if not words_a or not words_b:
            return 0.0

        overlap = len(words_a & words_b)
        return overlap / max(len(words_a), len(words_b))

    # ── Knowledge Validation ────────────────────────────────────────────────

    def validate_knowledge(
        self,
        entry: KnowledgeEntry,
        evidence_sources: list[dict],
    ) -> ValidationResult:
        """Validate knowledge against evidence sources."""
        issues = []
        evidence_found = []

        # Check against each source
        for source in evidence_sources:
            text = source.get("text", "").lower()
            entry_text = entry.content.lower()

            # Check if source supports or contradicts
            supports = any(word in text for word in entry_text.split()[:5])
            if supports:
                evidence_found.append(source.get("id", "unknown"))

        # Determine validity
        if evidence_found:
            is_valid = True
            confidence = min(0.9, 0.5 + len(evidence_found) * 0.1)
        else:
            is_valid = False
            confidence = entry.confidence * 0.5
            issues.append("No supporting evidence found")

        # Check for common issues
        if len(entry.content) < 20:
            issues.append("Content is too brief")

        if "maybe" in entry.content.lower() or "unclear" in entry.content.lower():
            issues.append("Content contains uncertain language")

        # Recommendation
        if is_valid:
            recommendation = "Knowledge is validated and can be trusted"
        elif confidence < 0.3:
            recommendation = "Knowledge should be verified or removed"
        else:
            recommendation = "Knowledge needs additional verification"

        result = ValidationResult(
            entry_id=entry.entry_id,
            is_valid=is_valid,
            confidence=confidence,
            issues=issues,
            evidence=evidence_found,
            recommendation=recommendation,
        )

        self._validations[entry.entry_id] = result
        return result

    # ── Knowledge Enrichment ─────────────────────────────────────────────────

    def enrich_knowledge(
        self,
        entry: KnowledgeEntry,
        related_knowledge: list[KnowledgeEntry],
    ) -> EnrichmentResult:
        """Enrich knowledge with related information."""
        added_info = []
        related_topics = []

        for related in related_knowledge:
            if related.entry_id == entry.entry_id:
                continue

            # Check if related entry adds new information
            new_words = set(related.content.lower().split()) - set(entry.content.lower().split())

            if len(new_words) > 5:
                added_info.append(related.content[:200])
                related_topics.extend(related.tags)

        # Create enriched content
        enriched = entry.content + " " + " ".join(added_info[:3])

        confidence = min(0.8, entry.confidence + 0.1)

        result = EnrichmentResult(
            entry_id=entry.entry_id,
            original_content=entry.content,
            enriched_content=enriched[:1000],
            added_info=added_info,
            related_topics=list(set(related_topics))[:10],
            confidence=confidence,
        )

        self._enrichments[entry.entry_id] = result
        return result

    # ── Knowledge Distillation ───────────────────────────────────────────────

    def distill_knowledge(
        self,
        entries: dict[str, KnowledgeEntry],
        max_entries: int = 100,
    ) -> list[str]:
        """Distill large knowledge base into key insights."""
        # Sort by importance (confidence * access_count)
        sorted_entries = sorted(
            entries.values(),
            key=lambda e: -(e.confidence * (1 + e.access_count * 0.1))
        )

        # Take top entries
        top_entries = sorted_entries[:max_entries]

        # Extract key insights
        insights = []
        for entry in top_entries:
            # Extract first sentence as insight
            first_sentence = entry.content.split(".")[0] + "."
            insights.append(first_sentence)

        return insights[:50]

    # ── Reporting ──────────────────────────────────────────────────────────

    def get_consolidation_report(self) -> dict[str, Any]:
        """Generate a consolidation report."""
        return {
            "total_entries": len(self._entries),
            "total_consolidations": len(self._consolidations),
            "total_conflicts": len(self._conflicts),
            "total_validations": len(self._validations),
            "total_enrichments": len(self._enrichments),
            "unresolved_conflicts": sum(
                1 for c in self._conflicts if not c.resolved_at
            ),
            "avg_consolidation_confidence": sum(
                c.confidence for c in self._consolidations.values()
            ) / max(1, len(self._consolidations)),
        }


# Module-level singleton
knowledge_consolidation = KnowledgeConsolidation()


# ── API Routes ──────────────────────────────────────────────────────────────


@router.post("/api/v1/memory/consolidate/merge")
async def merge_entries(payload: dict):
    """Merge knowledge entries."""
    result = knowledge_consolidation.merge_entries(payload["entry_ids"])
    if not result:
        raise HTTPException(400, "Need at least 2 entries to merge")
    return result.__dict__


@router.post("/api/v1/memory/consolidate/prune")
async def prune_knowledge(payload: dict):
    """Prune outdated knowledge."""
    entries = {
        eid: knowledge_consolidation._entries.get(eid, KnowledgeEntry(
            entry_id=eid, content=e.get("content", ""), source=e.get("source", ""),
            confidence=e.get("confidence", 0.5), tags=e.get("tags", [])
        ))
        for eid, e in payload["entries"].items()
    }
    return knowledge_consolidation.prune_knowledge(
        entries,
        max_age_days=payload.get("max_age_days", 365.0),
        min_confidence=payload.get("min_confidence", 0.1),
    )


@router.post("/api/v1/memory/consolidate/validate")
async def validate_knowledge(payload: dict):
    """Validate knowledge."""
    entry = KnowledgeEntry(
        entry_id=payload["entry_id"],
        content=payload["content"],
        source=payload.get("source", ""),
        confidence=payload.get("confidence", 0.5),
    )
    result = knowledge_consolidation.validate_knowledge(
        entry,
        payload.get("evidence_sources", []),
    )
    return result.__dict__


@router.post("/api/v1/memory/consolidate/enrich")
async def enrich_knowledge(payload: dict):
    """Enrich knowledge."""
    entry = KnowledgeEntry(
        entry_id=payload["entry_id"],
        content=payload["content"],
        source=payload.get("source", ""),
    )
    related = [
        KnowledgeEntry(
            entry_id=r["entry_id"],
            content=r["content"],
            source=r.get("source", ""),
            tags=r.get("tags", []),
        )
        for r in payload.get("related_knowledge", [])
    ]
    result = knowledge_consolidation.enrich_knowledge(entry, related)
    return result.__dict__


@router.post("/api/v1/memory/consolidate/distill")
async def distill_knowledge(payload: dict):
    """Distill knowledge base."""
    entries = {
        eid: KnowledgeEntry(
            entry_id=eid,
            content=e["content"],
            source=e.get("source", ""),
            confidence=e.get("confidence", 0.5),
            access_count=e.get("access_count", 0),
            tags=e.get("tags", []),
        )
        for eid, e in payload["entries"].items()
    }
    insights = knowledge_consolidation.distill_knowledge(
        entries,
        max_entries=payload.get("max_entries", 100),
    )
    return {"insights": insights, "count": len(insights)}


@router.get("/api/v1/memory/consolidate/report")
async def consolidation_report():
    """Get consolidation report."""
    return knowledge_consolidation.get_consolidation_report()
