"""
Conservative, deterministic contradiction signals for Memora's write pipeline.

This module deliberately does not decide which statement is true and never
supersedes or deletes an existing memory. It identifies narrow, explainable
conflicts that are safe to surface for downstream review:
- opposite explicit polarity markers over the same normalized statement;
- different single scalar values for the same normalized property.

Broad semantic similarity, recency, and shared vocabulary are not evidence of
contradiction. Ambiguous or unsupported statements return a negative result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Sequence, Tuple


@dataclass(frozen=True)
class ContradictionAssessment:
    """A review signal; it is not an adjudication of which memory is correct."""

    is_contradiction: bool
    score: float = 0.0
    kind: Optional[str] = None
    reason: Optional[str] = None


class ContradictionDetector:
    """Rules-based conflict detector designed to prefer precision over recall."""

    VERSION = "conservative-rules-v1"

    _TOKEN_RE = re.compile(r"(?u)[^\W_]+(?:['/-][^\W_]+)*")
    _NUMBER_RE = re.compile(r"(?<![A-Za-z_])[+-]?\d+(?:\.\d+)*(?![A-Za-z_])")

    _POSITIVE_MARKERS = frozenset({
        "enabled", "enable", "active", "available", "supported", "support",
        "allows", "allow", "allowed", "permits", "permit", "permitted",
        "true", "present", "valid", "online", "healthy", "open", "success",
        "successful", "configured", "reachable", "running", "exists", "exist",
        "yes", "on",
    })
    _NEGATIVE_MARKERS = frozenset({
        "disabled", "disable", "inactive", "unavailable", "unsupported",
        "denies", "deny", "denied", "forbids", "forbid", "forbidden",
        "false", "absent", "invalid", "offline", "unhealthy", "closed",
        "failure", "failed", "unconfigured", "unreachable", "stopped",
        "missing", "no", "off",
    })
    _NEGATORS = frozenset({"not", "never"})
    _UNCERTAINTY_MARKERS = frozenset({
        "may", "might", "could", "possibly", "perhaps", "maybe", "unknown",
        "uncertain", "unclear", "allegedly", "reportedly", "probably",
    })
    _STOPWORDS = frozenset({
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "to", "of", "for", "in", "on", "at", "as", "and", "or", "with", "by",
        "it", "this", "that", "these", "those", "has", "have", "had", "do",
        "does", "did", "will", "would", "should", "can", "from", "there",
    })
    _SCALAR_PROPERTY_MARKERS = frozenset({
        "limit", "limits", "size", "timeout", "duration", "count", "rate",
        "threshold", "capacity", "pool", "ttl", "retry", "retries", "port",
        "budget", "worker", "workers", "batch", "memory", "cpu", "age",
        "max", "min", "maximum", "minimum", "interval", "delay", "version",
        "quota", "percentage", "percent", "days", "hours", "seconds",
        "minutes", "milliseconds", "kb", "mb", "gb", "tb", "ocpu", "ram",
        "storage", "replica", "replicas", "connections", "connection",
        "concurrency", "threads", "processes", "attempts", "retries",
    })

    @classmethod
    def assess(cls, existing_text: str, candidate_text: str) -> ContradictionAssessment:
        """Return a narrow contradiction signal for a pair of memory statements."""
        if not isinstance(existing_text, str) or not isinstance(candidate_text, str):
            return ContradictionAssessment(False)
        left_text = " ".join(existing_text.strip().split())
        right_text = " ".join(candidate_text.strip().split())
        if not left_text or not right_text or left_text.casefold() == right_text.casefold():
            return ContradictionAssessment(False)
        # Questions and uncertain claims should not be promoted to conflicts.
        if "?" in left_text or "?" in right_text:
            return ContradictionAssessment(False)

        left_tokens = cls._tokens(left_text)
        right_tokens = cls._tokens(right_text)
        if not left_tokens or not right_tokens:
            return ContradictionAssessment(False)
        if (
            cls._UNCERTAINTY_MARKERS.intersection(left_tokens)
            or cls._UNCERTAINTY_MARKERS.intersection(right_tokens)
        ):
            return ContradictionAssessment(False)

        polarity = cls._assess_opposite_polarity(left_tokens, right_tokens)
        if polarity.is_contradiction:
            return polarity

        return cls._assess_scalar_conflict(left_text, right_text)

    @classmethod
    def _tokens(cls, text: str) -> Tuple[str, ...]:
        return tuple(token.casefold() for token in cls._TOKEN_RE.findall(text))

    @classmethod
    def _effective_polarities(cls, tokens: Sequence[str]) -> set[str]:
        polarities: set[str] = set()
        markers = cls._POSITIVE_MARKERS | cls._NEGATIVE_MARKERS
        for index, token in enumerate(tokens):
            if token not in markers:
                continue
            polarity = "positive" if token in cls._POSITIVE_MARKERS else "negative"
            # Handle explicit constructions such as "not enabled" and
            # "never disabled". Do not interpret "not only" as negation.
            previous = tokens[index - 1] if index > 0 else ""
            if previous in cls._NEGATORS:
                polarity = "negative" if polarity == "positive" else "positive"
            polarities.add(polarity)
        return polarities

    @classmethod
    def _polarity_skeleton(cls, tokens: Sequence[str]) -> Tuple[str, ...]:
        removed = cls._POSITIVE_MARKERS | cls._NEGATIVE_MARKERS | cls._NEGATORS
        return tuple(
            token for token in tokens
            if token not in removed and token not in cls._STOPWORDS
        )

    @classmethod
    def _assess_opposite_polarity(
        cls, left_tokens: Sequence[str], right_tokens: Sequence[str]
    ) -> ContradictionAssessment:
        left_polarities = cls._effective_polarities(left_tokens)
        right_polarities = cls._effective_polarities(right_tokens)
        # Mixed-polarity sentences are usually qualified or compound. Leave
        # them to a stronger semantic evaluator rather than guessing.
        if len(left_polarities) != 1 or len(right_polarities) != 1:
            return ContradictionAssessment(False)
        if left_polarities == right_polarities:
            return ContradictionAssessment(False)

        left_skeleton = cls._polarity_skeleton(left_tokens)
        right_skeleton = cls._polarity_skeleton(right_tokens)
        if not left_skeleton or left_skeleton != right_skeleton:
            return ContradictionAssessment(False)

        return ContradictionAssessment(
            True,
            score=0.98,
            kind="opposite_polarity",
            reason="Opposite explicit polarity markers describe the same normalized statement.",
        )

    @classmethod
    def _numeric_values(cls, text: str) -> Tuple[Decimal, ...]:
        values = []
        for raw_value in cls._NUMBER_RE.findall(text):
            try:
                values.append(Decimal(raw_value))
            except InvalidOperation:
                return ()
        return tuple(values)

    @classmethod
    def _numeric_skeleton(cls, text: str) -> Tuple[str, ...]:
        without_numbers = cls._NUMBER_RE.sub(" NUM ", text)
        return tuple(
            token for token in cls._tokens(without_numbers)
            if token != "num" and token not in cls._STOPWORDS
        )

    @classmethod
    def _assess_scalar_conflict(
        cls, existing_text: str, candidate_text: str
    ) -> ContradictionAssessment:
        left_values = cls._numeric_values(existing_text)
        right_values = cls._numeric_values(candidate_text)
        # Multiple numeric values often encode dates, versions, ranges, or
        # unrelated quantities. This first rule set only accepts one scalar
        # value per statement to avoid turning those cases into false alarms.
        if len(left_values) != 1 or len(right_values) != 1:
            return ContradictionAssessment(False)
        if left_values == right_values:
            return ContradictionAssessment(False)

        left_skeleton = cls._numeric_skeleton(existing_text)
        right_skeleton = cls._numeric_skeleton(candidate_text)
        if not left_skeleton or left_skeleton != right_skeleton:
            return ContradictionAssessment(False)
        skeleton_tokens = set(left_skeleton)
        if not skeleton_tokens.intersection(cls._SCALAR_PROPERTY_MARKERS):
            return ContradictionAssessment(False)

        return ContradictionAssessment(
            True,
            score=0.94,
            kind="conflicting_scalar_value",
            reason="Different scalar values are asserted for the same normalized property.",
        )
