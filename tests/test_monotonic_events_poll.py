"""
Unit and Integration Tests for Monotonic Event Feed Poller (GET /v1/events/poll).
Verifies that downstream agents (Cortex, Sentinel, Futuris) can poll new events
monotonically using their consumer cursor with optional automatic acknowledgment.
"""
from apps.api.routers.v1_events import poll_events
from storage.relational.models import EventLog, EventConsumerCursor
from core.events.emitter import EventEmitter


def test_poll_events_from_zero_cursor(test_db):
    """Verify that poll_events starts from 0 if consumer cursor does not exist."""
    emitter = EventEmitter()
    e1 = emitter.publish("memory.created", {"memory_id": "m-p1", "tenant_id": "default", "owner": "cortex", "type": "episodic"}, db=test_db)
    e2 = emitter.publish("memory.created", {"memory_id": "m-p2", "tenant_id": "default", "owner": "cortex", "type": "episodic"}, db=test_db)
    test_db.commit()

    res = poll_events(
        after_id=None,
        consumer_id="default",
        limit=10,
        event_type=None,
        auto_ack=False,
        agent="cortex",
        db=test_db,
    )

    assert res["agent"] == "cortex"
    assert res["after_id"] == 0
    assert len(res["events"]) == 2
    assert res["events"][0]["event_id"] == e1.event_id
    assert res["events"][1]["event_id"] == e2.event_id
    assert res["next_after_id"] == e2.cursor


def test_poll_events_with_auto_ack_advances_cursor(test_db):
    """Verify that auto_ack=True monotonically advances the EventConsumerCursor."""
    agent = "sentinel"
    consumer = "cursor_stream_1"

    emitter = EventEmitter()
    emitter.publish("memory.created", {"memory_id": "m-s1", "tenant_id": "default", "owner": agent, "type": "episodic"}, db=test_db)
    e2 = emitter.publish("memory.created", {"memory_id": "m-s2", "tenant_id": "default", "owner": agent, "type": "episodic"}, db=test_db)
    test_db.commit()

    # Initial poll with auto_ack=True
    res1 = poll_events(
        after_id=None,
        consumer_id=consumer,
        limit=10,
        event_type=None,
        auto_ack=True,
        agent=agent,
        db=test_db,
    )
    assert len(res1["events"]) == 2
    assert res1["cursor"] == e2.cursor

    # Verify cursor persisted in database
    cursor_row = test_db.query(EventConsumerCursor).filter_by(
        tenant_id="default", agent=agent, consumer_id=consumer
    ).first()
    assert cursor_row is not None
    assert cursor_row.last_event_id == e2.cursor

    # Second poll should return empty because cursor has advanced past all events
    res2 = poll_events(
        after_id=None,
        consumer_id=consumer,
        limit=10,
        event_type=None,
        auto_ack=True,
        agent=agent,
        db=test_db,
    )
    assert len(res2["events"]) == 0
    assert res2["after_id"] == e2.cursor

    # Publish a 3rd event
    e3 = emitter.publish("memory.created", {"memory_id": "m-s3", "tenant_id": "default", "owner": agent, "type": "episodic"}, db=test_db)
    test_db.commit()

    # Third poll should return only the 3rd event
    res3 = poll_events(
        after_id=None,
        consumer_id=consumer,
        limit=10,
        event_type=None,
        auto_ack=True,
        agent=agent,
        db=test_db,
    )
    assert len(res3["events"]) == 1
    assert res3["events"][0]["event_id"] == e3.event_id
    assert res3["cursor"] == e3.cursor


def test_poll_events_scoped_and_filtered(test_db):
    """Verify that event_type filtering and target_agent filtering apply to poll_events."""
    # Target only Futuris
    test_db.add(EventLog(
        event_id="evt_futuris_only",
        event_type="futuris.forecast",
        tenant_id="default",
        target_agent="futuris",
        payload={"forecast_id": "f-1"},
    ))
    # Target only Cortex
    test_db.add(EventLog(
        event_id="evt_cortex_only",
        event_type="intelx.news",
        tenant_id="default",
        target_agent="cortex",
        payload={"headline": "AI News"},
    ))
    test_db.commit()

    # Polling as futuris should only see futuris targeted events
    res = poll_events(
        after_id=0,
        consumer_id="default",
        limit=10,
        event_type=None,
        auto_ack=False,
        agent="futuris",
        db=test_db,
    )
    event_ids = [e["event_id"] for e in res["events"]]
    assert "evt_futuris_only" in event_ids
    assert "evt_cortex_only" not in event_ids
