"""
Regression tests for the local embedding generator.

The previous implementation projected each token with
sin((h >> (i % 32)) * 0.001 + pos * 0.1). `h` is a 256-bit digest, so the sine
argument was astronomically large and its value was governed by floating-point
noise rather than by the token. The vectors it produced did not measure
similarity: measured on this codebase, unrelated sentences drawn from a shared
vocabulary scored a mean cosine of 0.366 (max 0.852), while a query scored only
0.313 against the record that actually answered it - an inverted ordering.

That mattered because dense_vector carries weight 0.6 of the hybrid score and the
vector search threshold is 0.30, so roughly half of all irrelevant records
cleared the bar and ranking was noise-driven. Confirmed live: for "What is the
XC-4 compressor seal torque specification?" the correct record ranked 4th behind
three records that merely shared the word "compressor".

These tests pin the property that was missing: similarity must order by topical
overlap. They are written as measurable inequalities rather than fixed vector
values, so they still hold if the scheme is later replaced by a real model.
"""
import math

import pytest

from storage.vector.embedding import EmbeddingGenerator


def cos(a, b):
    """Cosine similarity; both vectors are L2-normalised by the generator."""
    return sum(x * y for x, y in zip(a, b, strict=True))


QUERY = "What is the XC-4 compressor seal torque specification?"
CORRECT = ("The XC-4 compressor seal torque specification is 42 Nm with a "
           "locking compound applied to the retaining bolts.")
PARAPHRASE = "XC-4 compressor seal torque spec"
DISTRACTOR = "The compressor was serviced and returned to service."
UNRELATED = "The quarterly budget review is scheduled for Thursday afternoon."


def emb(text, dimension=EmbeddingGenerator.DEFAULT_DIMENSION):
    return EmbeddingGenerator.generate_embedding(text, dimension)


# ---------------------------------------------------------------------------
# The property that was broken: ordering by topical overlap
# ---------------------------------------------------------------------------

def test_relevant_text_scores_above_irrelevant_text():
    """The ordering a retrieval system needs, and the one that was inverted."""
    q = emb(QUERY)
    assert cos(q, emb(CORRECT)) > cos(q, emb(UNRELATED)), (
        f"a query scored {cos(q, emb(CORRECT)):.4f} against the record that "
        f"answers it and {cos(q, emb(UNRELATED)):.4f} against an unrelated one"
    )


def test_relevant_text_scores_above_a_partial_distractor():
    """Sharing one word must not outrank sharing the whole topic."""
    q = emb(QUERY)
    assert cos(q, emb(CORRECT)) > cos(q, emb(DISTRACTOR))


def test_paraphrase_is_the_closest_match():
    q = emb(QUERY)
    assert cos(q, emb(PARAPHRASE)) > cos(q, emb(UNRELATED))


def test_texts_with_no_vocabulary_in_common_score_near_zero():
    """Genuinely unrelated text must not clear the 0.30 search threshold.

    Drawn from disjoint vocabularies on purpose: sampling two sentences from one
    small word list is not a test of unrelatedness, since such pairs really do
    share a third of their words and deserve a high similarity.
    """
    mechanical = "pump valve seal torque pressure bearing housing gasket".split()
    financial = "invoice ledger revenue accrual dividend margin quota audit".split()
    sims = []
    for i in range(len(mechanical) - 5):
        a = " ".join(mechanical[i:i + 6])
        b = " ".join(financial[i:i + 6])
        sims.append(cos(emb(a), emb(b)))
    mean = sum(sims) / len(sims)
    assert mean < 0.15, (
        f"texts sharing no vocabulary average {mean:.4f}; that is high enough to "
        f"admit irrelevant records through the 0.30 vector threshold"
    )


def test_answer_outranks_a_same_domain_baseline():
    """Ranking needs the answer above other same-domain records, not just above
    text from an unrelated field.

    This is the comparison that was inverted before: the old scheme scored the
    query 0.313 against its answer while same-domain pairs averaged 0.366.
    """
    q = emb(QUERY)
    domain = ("pump valve seal torque pressure reading sensor bearing "
              "housing gasket filter outlet inlet motor flange impeller").split()
    sims = []
    for i in range(60):
        a = " ".join(domain[(i * 7 + j) % len(domain)] for j in range(6))
        b = " ".join(domain[(i * 11 + j + 3) % len(domain)] for j in range(6))
        if a == b:
            continue
        sims.append(cos(emb(a), emb(b)))
    baseline = sum(sims) / len(sims)
    assert cos(q, emb(CORRECT)) > baseline, (
        f"the answer scores {cos(q, emb(CORRECT)):.4f} but same-domain noise "
        f"averages {baseline:.4f}, so noise outranks the answer"
    )


# ---------------------------------------------------------------------------
# Contract the rest of the system depends on
# ---------------------------------------------------------------------------

def test_identical_text_is_self_similar():
    assert cos(emb(CORRECT), emb(CORRECT)) == pytest.approx(1.0, abs=1e-6)


def test_output_is_l2_normalised():
    v = emb(CORRECT)
    assert math.sqrt(sum(x * x for x in v)) == pytest.approx(1.0, abs=1e-4)


def test_empty_text_returns_a_zero_vector():
    assert emb("") == [0.0] * EmbeddingGenerator.DEFAULT_DIMENSION
    assert emb("   ") == [0.0] * EmbeddingGenerator.DEFAULT_DIMENSION


def test_dimension_is_honoured():
    assert len(emb("some text", 64)) == 64
    assert len(emb("some text", 128)) == 128


def test_embedding_is_deterministic():
    """Vector search is only reproducible if the same text embeds the same way."""
    assert emb(CORRECT) == emb(CORRECT)


def test_all_stopword_input_still_produces_a_usable_vector():
    """Dropping function words must not leave such a query matching nothing."""
    v = emb("to be or not to be")
    assert any(v), "an all-stopword query embedded to the zero vector"


def test_function_words_do_not_create_false_similarity():
    """Two sentences sharing only 'the/and/of' must not look alike."""
    a = "the summary of the report and the notes of the meeting"
    b = "the analysis of the invoice and the terms of the contract"
    assert cos(emb(a), emb(b)) < 0.5


def test_repeated_terms_do_not_dominate():
    """Sublinear term frequency: repeating a word should not swamp the vector."""
    once = emb("compressor seal torque")
    repeated = emb("compressor compressor compressor compressor seal torque")
    assert cos(once, repeated) > 0.7


def test_case_and_punctuation_are_normalised():
    assert cos(emb("XC-4 compressor seal!"), emb("xc-4 compressor seal")) == pytest.approx(1.0, abs=1e-6)
