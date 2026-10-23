"""
Unit and integration tests for Memora's Dual-Mode Hybrid Embedder.
Tests Gemini Embedding API (gemini-embedding-2, 384-dimensional)
and zero-latency fail-soft deterministic feature hashing fallback.
"""
from __future__ import annotations

import math
from unittest.mock import patch
import pytest

from storage.vector.embedding import EmbeddingGenerator


def _l2_norm(vec: list[float]) -> float:
    return math.sqrt(sum(v * v for v in vec))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


@pytest.fixture(autouse=True)
def reset_embedder_mode(monkeypatch):
    monkeypatch.setenv("MEMORA_EMBEDDING_MODE", "auto")
    EmbeddingGenerator.set_mode("auto")
    yield
    EmbeddingGenerator.set_mode("auto")


def test_empty_and_whitespace_text_returns_zero_vector():
    vec_empty = EmbeddingGenerator.generate_embedding("")
    vec_spaces = EmbeddingGenerator.generate_embedding("    ")
    assert len(vec_empty) == 384
    assert len(vec_spaces) == 384
    assert all(v == 0.0 for v in vec_empty)
    assert all(v == 0.0 for v in vec_spaces)


def test_deterministic_hashing_generation():
    text = "Sovereign memory authority across the entire FRIDAY Universe."
    vec = EmbeddingGenerator.generate_deterministic_embedding(text)
    assert len(vec) == 384
    norm = _l2_norm(vec)
    assert 0.99 <= norm <= 1.01
    assert EmbeddingGenerator.generate_hashing_embedding(text) == vec


def test_forced_hashing_mode():
    EmbeddingGenerator.set_mode("hashing")
    text = "PostgreSQL relational authority and multi-store convergence."
    vec = EmbeddingGenerator.generate_embedding(text)
    assert len(vec) == 384
    assert EmbeddingGenerator.get_mode() == "deterministic_hashing"


def test_gemini_primary_embedding_live():
    """Verify live Gemini embedding with 384 dimensions if API key is present."""
    api_key = EmbeddingGenerator._get_gemini_api_key()
    if not api_key:
        pytest.skip("GEMINI_API_KEY not present in environment or settings")

    text_a = "Sovereign memory fabric indexing agent state in PostgreSQL"
    text_b = "Relational memory storage tracking ecosystem state in Postgres"
    text_unrelated = "Recipe for baking sourdough bread with active starter"

    vec_a = EmbeddingGenerator.generate_embedding(text_a)
    assert len(vec_a) == 384
    assert EmbeddingGenerator.get_mode() == "gemini"
    assert 0.99 <= _l2_norm(vec_a) <= 1.01

    vec_b = EmbeddingGenerator.generate_embedding(text_b)
    vec_unrelated = EmbeddingGenerator.generate_embedding(text_unrelated)

    sim_related = _cosine_similarity(vec_a, vec_b)
    sim_unrelated = _cosine_similarity(vec_a, vec_unrelated)

    assert sim_related > sim_unrelated, (
        f"Semantic similarity failed: related={sim_related:.4f}, unrelated={sim_unrelated:.4f}"
    )


def test_fail_soft_fallback_when_gemini_fails():
    """Verify that when Gemini raises an exception, the embedder automatically falls back to deterministic hashing."""
    with patch.object(
        EmbeddingGenerator,
        "generate_gemini_embedding",
        side_effect=RuntimeError("Simulated Gemini quota limit / network outage"),
    ):
        text = "Critical agent operation requiring persistent storage."
        vec = EmbeddingGenerator.generate_embedding(text, prefer_gemini=True)
        assert len(vec) == 384
        assert EmbeddingGenerator.get_mode() == "deterministic_hashing"
        # Must match deterministic hashing
        expected = EmbeddingGenerator.generate_deterministic_embedding(text)
        assert vec == expected


def test_invalid_mode_raises():
    with pytest.raises(ValueError):
        EmbeddingGenerator.set_mode("invalid_mode")
