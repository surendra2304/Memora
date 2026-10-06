"""
Regression tests for query_text matching in MemoryService.query_memories.

The primary query endpoint filtered with

    MemoryRecord.content_text.ilike(f"%{query.query_text}%")

which requires the ENTIRE query to appear as one contiguous run of characters.
A natural question therefore returned zero rows while a single word worked fine
— verified against a live corpus: 0 rows for "xenon compressor seal torque
specification", 4 rows for "xenon". The main retrieval endpoint was unusable for
exactly the queries people actually ask.

Terms are now OR'd together, matching the semantics hybrid_search already uses,
and LIKE wildcards in user input are escaped.
"""
import pytest

from core.identity.service import IdentityService
from core.memory.schemas import MemoryQuery
from core.memory.service import MemoryService


@pytest.fixture
def corpus(test_db):
    """A small corpus with known content."""
    for agent in ("forge", "friday", "sentinel"):
        IdentityService.register_agent(test_db, agent)
    from core.memory.pipeline.write_service import MemoryWriteService

    MemoryWriteService.execute_pipeline(
        db=test_db,
        caller_name="forge",
        content_text="Replaced the xenon compressor seal on unit XC-4. "
                     "Correct torque is 42 Nm.",
    )
    MemoryWriteService.execute_pipeline(
        db=test_db,
        caller_name="friday",
        content_text="Quarterly forecast revised downward after the supplier delay.",
    )
    return test_db


def test_a_natural_language_question_finds_the_record(corpus):
    """The regression: a multi-word question used to match nothing."""
    rows = MemoryService.query_memories(
        corpus,
        MemoryQuery(query_text="xenon compressor seal torque specification", limit=10),
        actor_name="forge",
        purpose="test",
    )
    assert len(rows) == 1, (
        "a natural-language question returned no rows; query_text is being "
        "matched as one contiguous phrase again"
    )
    assert "XC-4" in rows[0].content_text


def test_a_single_word_still_works(corpus):
    rows = MemoryService.query_memories(
        corpus, MemoryQuery(query_text="xenon", limit=10),
        actor_name="forge", purpose="test",
    )
    assert len(rows) == 1


def test_terms_match_any_not_all(corpus):
    """A term absent from the record must not suppress the ones present."""
    rows = MemoryService.query_memories(
        corpus,
        MemoryQuery(query_text="xenon spectrometer", limit=10),
        actor_name="forge",
        purpose="test",
    )
    assert len(rows) == 1, "one unmatched term suppressed the whole query"


def test_a_query_matching_nothing_returns_empty(corpus):
    rows = MemoryService.query_memories(
        corpus, MemoryQuery(query_text="nonexistent gibberish", limit=10),
        actor_name="forge", purpose="test",
    )
    assert rows == []


def test_percent_wildcard_does_not_match_the_whole_corpus(corpus):
    """'%' is a LIKE wildcard; unescaped it matched every record."""
    rows = MemoryService.query_memories(
        corpus, MemoryQuery(query_text="%", limit=50),
        actor_name="forge", purpose="test",
    )
    assert rows == [], "a bare % matched records it should not"


def test_underscore_wildcard_does_not_match_everything(corpus):
    rows = MemoryService.query_memories(
        corpus, MemoryQuery(query_text="_", limit=50),
        actor_name="forge", purpose="test",
    )
    assert rows == [], "a bare _ matched records it should not"


def test_matching_is_case_insensitive(corpus):
    for term in ("XENON", "Xenon", "xenon"):
        rows = MemoryService.query_memories(
            corpus, MemoryQuery(query_text=term, limit=10),
            actor_name="forge", purpose="test",
        )
        assert len(rows) == 1, f"{term} should match case-insensitively"


def test_whitespace_only_query_does_not_filter(corpus):
    """A blank query is not a filter; other criteria should still apply."""
    rows = MemoryService.query_memories(
        corpus, MemoryQuery(query_text="   ", limit=50),
        actor_name="forge", purpose="test",
    )
    assert len(rows) == 1, "forge's own records should come back unfiltered"


def test_a_very_long_query_cannot_build_an_unbounded_expression(corpus):
    """A pathological query must not explode into thousands of OR clauses."""
    huge = " ".join(f"term{i}" for i in range(5000))
    rows = MemoryService.query_memories(
        corpus, MemoryQuery(query_text=huge, limit=10),
        actor_name="forge", purpose="test",
    )
    assert rows == []


def test_query_and_hybrid_search_agree_on_whether_something_matches(corpus):
    """The two retrieval paths must not disagree about existence."""
    from core.memory.search_service import SearchService

    q = "xenon compressor seal torque specification"
    direct = MemoryService.query_memories(
        corpus, MemoryQuery(query_text=q, limit=10),
        actor_name="forge", purpose="test",
    )
    hybrid = SearchService.hybrid_search(
        corpus, query_text=q, actor_name="forge", purpose="test", limit=10
    )
    assert bool(direct) == bool(hybrid), (
        f"query_memories found {len(direct)} rows but hybrid_search found "
        f"{len(hybrid)} for the same query"
    )
