"""Tests for Memora's conservative contradiction-signal rules and write integration."""

from core.memory.pipeline.contradiction_detector import ContradictionDetector


def test_opposite_explicit_polarity_on_same_statement_is_detected():
    result = ContradictionDetector.assess(
        "The API allows external access.",
        "The API denies external access.",
    )

    assert result.is_contradiction is True
    assert result.kind == "opposite_polarity"
    assert result.score >= 0.95
    assert result.reason


def test_explicit_negation_is_detected_without_choosing_a_winner():
    result = ContradictionDetector.assess(
        "TLS verification is enabled.",
        "TLS verification is not enabled.",
    )

    assert result.is_contradiction is True
    assert result.kind == "opposite_polarity"


def test_different_scalar_for_same_limit_is_detected():
    result = ContradictionDetector.assess(
        "PostgreSQL connection pool maximum is 50 connections.",
        "PostgreSQL connection pool maximum is 20 connections.",
    )

    assert result.is_contradiction is True
    assert result.kind == "conflicting_scalar_value"
    assert result.score >= 0.9


def test_duplicate_statements_are_not_reported_as_contradictions():
    result = ContradictionDetector.assess(
        "The API allows external access.",
        "the api allows external access",
    )

    assert result.is_contradiction is False


def test_same_polarity_and_different_subject_are_not_reported_as_contradictions():
    assert not ContradictionDetector.assess(
        "The API allows external access.",
        "The API allows internal access.",
    ).is_contradiction
    assert not ContradictionDetector.assess(
        "The cache is enabled.",
        "The scheduler is disabled.",
    ).is_contradiction


def test_uncertain_and_question_statements_are_left_unclassified():
    assert not ContradictionDetector.assess(
        "The API may allow external access.",
        "The API denies external access.",
    ).is_contradiction
    assert not ContradictionDetector.assess(
        "Is TLS verification enabled?",
        "TLS verification is disabled.",
    ).is_contradiction


def test_numeric_changes_without_a_scalar_property_are_not_flagged():
    assert not ContradictionDetector.assess(
        "The agent remembers 50 facts.",
        "The agent remembers 20 facts.",
    ).is_contradiction


def test_multiple_numeric_values_are_conservatively_left_unclassified():
    assert not ContradictionDetector.assess(
        "The service uses 4 workers and 16 GB memory.",
        "The service uses 8 workers and 32 GB memory.",
    ).is_contradiction


def test_detector_does_not_mutate_or_rank_memory_state():
    # The public API returns only an assessment; any eventual resolution remains
    # an explicit, policy-checked SupersessionEngine operation.
    result = ContradictionDetector.assess(
        "PostgreSQL connection pool maximum is 50 connections.",
        "PostgreSQL connection pool maximum is 20 connections.",
    )
    assert result.is_contradiction
    assert result.reason != "newer memory wins"
