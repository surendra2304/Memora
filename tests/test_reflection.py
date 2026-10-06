"""
Tests for the self-reflection engine.

Each reflection must be grounded in real corpus data, so these tests build the
corpus that should trigger a given insight and assert it is drawn — and, just as
importantly, build corpora that should NOT trigger it, because a reflection that
fires on noise is worse than no reflection at all.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from core.identity.service import IdentityService
from core.memory.pipeline.write_service import MemoryWriteService
from core.reflection.engine import ReflectionEngine, reflection_engine
from storage.relational.models import EventLog, LifecycleState, MemoryRecord, MemoryType


@pytest.fixture(autouse=True)
def _isolate_vector_store():
    from storage.vector.qdrant_adapter import vector_adapter

    vector_adapter._mock_store.clear()
    yield
    vector_adapter._mock_store.clear()


def _write(db, agent, text, days_ago=0):
    """Write a memory and backdate it, so age-based reflections can be tested."""
    IdentityService.register_agent(db, agent)
    result = MemoryWriteService.execute_pipeline(
        db=db, caller_name=agent, content_text=text
    )
    record = (
        db.query(MemoryRecord).filter(MemoryRecord.id == result.record.id).first()
    )
    record.created_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
    db.commit()
    return record


# ---------------------------------------------------------------------------
# recurring_theme
# ---------------------------------------------------------------------------

def test_a_theme_spanning_agents_and_days_is_detected(test_db):
    for agent, day in [("friday", 0), ("forge", 1), ("sentinel", 2), ("friday", 3)]:
        _write(test_db, agent, f"xenon compressor seal torque check {day}", days_ago=day)

    insights = reflection_engine.recurring_themes(
        test_db, reflection_engine._load_corpus(test_db, "default"), {}, "default"
    )
    subjects = {i.subject for i in insights}
    assert "compressor" in subjects
    theme = next(i for i in insights if i.subject == "compressor")
    assert theme.kind == "recurring_theme"
    assert "3 agents" in theme.summary


def test_one_agent_repeating_itself_is_not_a_theme(test_db):
    """A habit is not a pattern; a theme needs more than one perspective."""
    for day in range(5):
        _write(test_db, "friday", f"xenon compressor note {day}", days_ago=day)

    insights = reflection_engine.recurring_themes(
        test_db, reflection_engine._load_corpus(test_db, "default"), {}, "default"
    )
    assert all(i.subject != "compressor" for i in insights), (
        "a single agent repeating itself was reported as a recurring theme"
    )


def test_a_single_mention_is_not_a_theme(test_db):
    _write(test_db, "friday", "xenon compressor torque")
    _write(test_db, "forge", "something entirely different about networking")

    insights = reflection_engine.recurring_themes(
        test_db, reflection_engine._load_corpus(test_db, "default"), {}, "default"
    )
    assert all(i.subject != "compressor" for i in insights)


def test_themes_outside_the_window_are_ignored(test_db):
    """Reflection is about what is current, not about all history."""
    for agent, day in [("friday", 200), ("forge", 201), ("sentinel", 202)]:
        _write(test_db, agent, f"xenon compressor ancient {day}", days_ago=day)

    insights = reflection_engine.recurring_themes(
        test_db, reflection_engine._load_corpus(test_db, "default"), {}, "default"
    )
    assert all(i.subject != "compressor" for i in insights)


# ---------------------------------------------------------------------------
# contradiction
# ---------------------------------------------------------------------------

def test_conflicting_measurements_for_one_subject_are_flagged(test_db):
    _write(test_db, "friday", "compressor seal torque must be 42 Nm")
    _write(test_db, "forge", "compressor seal torque must be 38 Nm")

    insights = reflection_engine.contradictions(
        test_db, reflection_engine._load_corpus(test_db, "default"), {}, "default"
    )
    kinds = {i.kind for i in insights}
    assert "contradiction" in kinds
    conflict = next(i for i in insights if i.kind == "contradiction")
    assert "42" in conflict.summary and "38" in conflict.summary
    assert "supersede" in (conflict.suggested_action or "")


def test_agreeing_measurements_are_not_a_contradiction(test_db):
    _write(test_db, "friday", "compressor seal torque must be 42 Nm")
    _write(test_db, "forge", "compressor seal torque must be 42 Nm exactly")

    insights = reflection_engine.contradictions(
        test_db, reflection_engine._load_corpus(test_db, "default"), {}, "default"
    )
    assert insights == [], "identical values were reported as a conflict"


def test_different_units_are_not_compared(test_db):
    """42 Nm and 38 psi are not the same claim, so they must not conflict."""
    _write(test_db, "friday", "compressor seal torque must be 42 Nm")
    _write(test_db, "forge", "compressor seal pressure must be 38 psi")

    insights = reflection_engine.contradictions(
        test_db, reflection_engine._load_corpus(test_db, "default"), {}, "default"
    )
    torque_conflicts = [i for i in insights if "torque" in i.subject and "nm" in i.subject]
    assert torque_conflicts == []


def test_prose_without_measurements_produces_no_false_conflicts(test_db):
    _write(test_db, "friday", "the compressor needs regular maintenance")
    _write(test_db, "forge", "the compressor is generally reliable")

    insights = reflection_engine.contradictions(
        test_db, reflection_engine._load_corpus(test_db, "default"), {}, "default"
    )
    assert insights == [], "free prose was reported as contradictory"


# ---------------------------------------------------------------------------
# stale_knowledge
# ---------------------------------------------------------------------------

def test_old_high_importance_memories_are_flagged_stale(test_db):
    record = _write(
        test_db, "friday", "critical xenon calibration procedure", days_ago=200
    )
    record.importance = 0.95
    test_db.commit()

    insights = reflection_engine.stale_knowledge(
        test_db, reflection_engine._load_corpus(test_db, "default"), {}, "default"
    )
    assert len(insights) == 1
    assert insights[0].kind == "stale_knowledge"
    assert "200 days" in insights[0].summary


def test_a_recent_memory_is_not_stale(test_db):
    record = _write(test_db, "friday", "fresh calibration note", days_ago=2)
    record.importance = 0.95
    test_db.commit()

    insights = reflection_engine.stale_knowledge(
        test_db, reflection_engine._load_corpus(test_db, "default"), {}, "default"
    )
    assert insights == []


def test_a_low_importance_old_memory_is_not_stale(test_db):
    """Decay should handle trivia; stale_knowledge is about consequential claims."""
    record = _write(test_db, "friday", "trivial old aside", days_ago=300)
    record.importance = 0.2
    test_db.commit()

    insights = reflection_engine.stale_knowledge(
        test_db, reflection_engine._load_corpus(test_db, "default"), {}, "default"
    )
    assert insights == []


# ---------------------------------------------------------------------------
# knowledge_gap
# ---------------------------------------------------------------------------

def _log_query(db, text, tenant_id="default"):
    # event_id is unique per row, so a repeated identical query still needs a
    # distinct id; deriving it from the text collided.
    db.add(EventLog(
        event_id=f"q-{uuid.uuid4().hex}",
        event_type="memory.query",
        tenant_id=tenant_id,
        payload={"query": text},
    ))
    db.commit()


def test_repeatedly_asked_but_uncovered_subjects_are_gaps(test_db):
    _write(test_db, "friday", "notes about the xenon compressor")
    for _ in range(3):
        _log_query(test_db, "quantum flux capacitor alignment")

    insights = reflection_engine.knowledge_gaps(
        test_db, reflection_engine._load_corpus(test_db, "default"), {}, "default"
    )
    subjects = {i.subject for i in insights}
    assert "quantum" in subjects or "capacitor" in subjects


def test_a_well_covered_subject_is_not_a_gap(test_db):
    for agent in ("friday", "forge", "sentinel"):
        _write(test_db, agent, "xenon compressor calibration procedure")
    for _ in range(3):
        _log_query(test_db, "xenon compressor calibration")

    insights = reflection_engine.knowledge_gaps(
        test_db, reflection_engine._load_corpus(test_db, "default"), {}, "default"
    )
    assert all(i.subject != "compressor" for i in insights)


# ---------------------------------------------------------------------------
# agent_specialisation
# ---------------------------------------------------------------------------

def test_the_agent_with_most_memories_on_a_topic_is_identified(test_db):
    for i in range(5):
        _write(test_db, "forge", f"xenon compressor repair procedure {i}")
    _write(test_db, "friday", "xenon compressor aside")

    owners = {
        a.id: a.name
        for a in test_db.query(__import__("storage.relational.models", fromlist=["Agent"]).Agent).all()
    }
    insights = reflection_engine.agent_specialisation(
        test_db, reflection_engine._load_corpus(test_db, "default"), owners, "default"
    )
    compressor = [i for i in insights if i.subject == "compressor"]
    assert compressor, "no specialisation insight for a clearly owned topic"
    assert "forge" in compressor[0].summary


def test_an_evenly_split_topic_has_no_single_authority(test_db):
    for agent in ("friday", "forge", "sentinel", "cortex"):
        _write(test_db, agent, "xenon compressor shared responsibility")

    owners = {a.id: a.name for a in IdentityService.list_agents(test_db)} \
        if hasattr(IdentityService, "list_agents") else {}
    insights = reflection_engine.agent_specialisation(
        test_db, reflection_engine._load_corpus(test_db, "default"), owners, "default"
    )
    assert all(i.subject != "compressor" for i in insights), (
        "a topic with no majority owner was assigned an authority"
    )


# ---------------------------------------------------------------------------
# run-level behaviour
# ---------------------------------------------------------------------------

def test_reflecting_over_an_empty_corpus_is_safe(test_db):
    report = reflection_engine.reflect(test_db, tenant_id="default")
    assert report.scanned_memories == 0
    assert report.insights == []
    assert report.stored == 0


def test_insights_are_stored_as_experience_memories(test_db):
    for agent, day in [("friday", 0), ("forge", 1), ("sentinel", 2), ("friday", 3)]:
        _write(test_db, agent, f"xenon compressor seal torque check {day}", days_ago=day)

    report = reflection_engine.reflect(test_db, tenant_id="default", store_insights=True)

    assert report.stored > 0
    stored = (
        test_db.query(MemoryRecord)
        .filter(MemoryRecord.memory_type == MemoryType.EXPERIENCE)
        .all()
    )
    assert len(stored) == report.stored
    assert all(
        (r.provenance or {}).get("source") == "reflection_engine" for r in stored
    )
    assert all((r.provenance or {}).get("reflection_fingerprint") for r in stored)


def test_a_second_run_does_not_duplicate_insights(test_db):
    """Reflection must compound, not repeat itself every run."""
    for agent, day in [("friday", 0), ("forge", 1), ("sentinel", 2), ("friday", 3)]:
        _write(test_db, agent, f"xenon compressor seal torque check {day}", days_ago=day)

    first = reflection_engine.reflect(test_db, tenant_id="default", store_insights=True)
    assert first.stored > 0

    second = reflection_engine.reflect(test_db, tenant_id="default", store_insights=True)
    assert second.stored == 0, (
        f"reflection re-stored {second.stored} insights it had already recorded"
    )
    # But it still reports them, so the API stays useful.
    assert len(second.insights) == len(first.insights)


def test_dry_run_stores_nothing(test_db):
    for agent, day in [("friday", 0), ("forge", 1), ("sentinel", 2), ("friday", 3)]:
        _write(test_db, agent, f"xenon compressor seal torque check {day}", days_ago=day)

    report = reflection_engine.reflect(test_db, tenant_id="default", store_insights=False)
    assert report.stored == 0
    assert len(report.insights) > 0
    assert (
        test_db.query(MemoryRecord)
        .filter(MemoryRecord.memory_type == MemoryType.EXPERIENCE)
        .count()
        == 0
    )


def test_reflection_never_mutates_existing_memories(test_db):
    """The engine observes and concludes; acting stays an explicit decision."""
    original = _write(test_db, "friday", "xenon compressor torque must be 42 Nm")
    before = {
        "importance": original.importance,
        "state": original.lifecycle_state,
        "text": original.content_text,
    }
    _write(test_db, "forge", "xenon compressor torque must be 38 Nm")

    reflection_engine.reflect(test_db, tenant_id="default", store_insights=True)

    test_db.refresh(original)
    assert original.importance == before["importance"]
    assert original.lifecycle_state == before["state"]
    assert original.content_text == before["text"]


def test_the_report_serialises_to_json(test_db):
    for agent, day in [("friday", 0), ("forge", 1), ("sentinel", 2), ("friday", 3)]:
        _write(test_db, agent, f"xenon compressor seal torque check {day}", days_ago=day)

    report = reflection_engine.reflect(test_db, tenant_id="default", store_insights=False)
    payload = report.to_dict()
    assert set(payload) == {
        "tenant_id", "scanned_memories", "insight_count", "stored",
        "by_kind", "duration_ms", "created_at", "insights",
    }
    import json
    json.dumps(payload)


def test_reflection_is_scoped_to_one_tenant(test_db):
    for agent, day in [("friday", 0), ("forge", 1), ("sentinel", 2), ("friday", 3)]:
        _write(test_db, agent, f"xenon compressor seal torque check {day}", days_ago=day)

    # A second tenant with no memories must see nothing.
    report = reflection_engine.reflect(test_db, tenant_id="other-tenant")
    assert report.scanned_memories == 0
    assert report.insights == []


def test_a_failing_reflection_does_not_abort_the_run(test_db, monkeypatch):
    for agent, day in [("friday", 0), ("forge", 1), ("sentinel", 2), ("friday", 3)]:
        _write(test_db, agent, f"xenon compressor seal torque check {day}", days_ago=day)

    def explode(db, records, owners, tenant_id):
        raise RuntimeError("reflection blew up")

    monkeypatch.setattr(reflection_engine, "contradictions", explode, raising=True)
    report = reflection_engine.reflect(test_db, tenant_id="default", store_insights=False)

    kinds = {i.kind for i in report.insights}
    assert "contradiction" not in kinds
    assert "recurring_theme" in kinds, "the other reflections should still have run"
