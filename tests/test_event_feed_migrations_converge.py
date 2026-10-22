"""Regression tests for the durable-event-feed migrations.

Real failure found2026-10-03 while starting Memora locally against its own
database:

    sqlalchemy.exc.OperationalError: (sqlite3.OperationalError)
    no such column: event_log.target_agent

`storage/relational/session.py` runs `Base.metadata.create_all()` on startup.
That had already built `event_log` from an EARLIER ORM model with no
`target_agent` and no `cloud_synced`, and create_all never ALTERs an existing
table. `alembic upgrade head` could not repair it either, because
`b1206b9e4a61` did a bare `op.create_table("event_log")` against a table that
already existed — so the database could never reach head, and Memora could
never start locally at all.

These tests build the exact legacy shape and assert both migrations converge.
"""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _load(filename: str):
    """Import a migration module by path (its module name is not importable)."""
    spec = importlib.util.spec_from_file_location(filename[:-3], MIGRATIONS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LEGACY_EVENT_LOG = """
CREATE TABLE event_log (
    id INTEGER NOT NULL PRIMARY KEY,
    event_id VARCHAR(64) NOT NULL,
    event_type VARCHAR(128) NOT NULL,
    tenant_id VARCHAR(64) NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
)
"""


@pytest.fixture
def legacy_db(tmp_path):
    """A database with the pre-durable-feed shape create_all used to leave."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.exec_driver_sql(LEGACY_EVENT_LOG)
        conn.exec_driver_sql(
            "INSERT INTO event_log (event_id, event_type, tenant_id, payload, created_at)"
            " VALUES ('evt-1', 'friday.probe', 'default', '{}', '2026-09-01T00:00:00')"
        )
    return engine


def _columns(engine, table):
    return {c["name"] for c in sa.inspect(engine).get_columns(table)}


class TestDurableEventFeedMigrationConverges:
    def test_adds_missing_columns_to_a_preexisting_table(self, legacy_db):
        mod = _load("b1206b9e4a61_add_durable_event_feed.py")

        with legacy_db.begin() as conn:
            mod.op = _bind_op(conn)
            mod.upgrade()

        cols = _columns(legacy_db, "event_log")
        assert "target_agent" in cols, "the column the ORM queries and the crash named"
        assert "cloud_synced" in cols

    def test_preserves_existing_rows(self, legacy_db):
        """This is shared memory. Upgrading a schema must never drop memories."""
        mod = _load("b1206b9e4a61_add_durable_event_feed.py")

        with legacy_db.begin() as conn:
            mod.op = _bind_op(conn)
            mod.upgrade()

        with legacy_db.connect() as conn:
            rows = conn.exec_driver_sql("SELECT event_id FROM event_log").fetchall()
        assert [r[0] for r in rows] == ["evt-1"]

    def test_creates_the_table_when_absent(self, tmp_path):
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
        mod = _load("b1206b9e4a61_add_durable_event_feed.py")

        with engine.begin() as conn:
            mod.op = _bind_op(conn)
            mod.upgrade()

        assert "event_log" in sa.inspect(engine).get_table_names()

    def test_is_idempotent_when_run_twice(self, legacy_db):
        """Re-running must not explode; startup paths can retry."""
        mod = _load("b1206b9e4a61_add_durable_event_feed.py")

        with legacy_db.begin() as conn:
            mod.op = _bind_op(conn)
            mod.upgrade()
            mod.upgrade()  # must not raise "duplicate column"

    def test_creates_the_cursor_indexes_the_replay_path_needs(self, legacy_db):
        """The durable feed replays on (target_agent, id); that index is the feature."""
        mod = _load("b1206b9e4a61_add_durable_event_feed.py")

        with legacy_db.begin() as conn:
            mod.op = _bind_op(conn)
            mod.upgrade()

        names = {ix["name"] for ix in sa.inspect(legacy_db).get_indexes("event_log")}
        assert "ix_event_log_target_cursor" in names


class TestConsumerCursorMigrationConverges:
    def test_adds_missing_columns_to_a_preexisting_table(self, tmp_path):
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'cursors.db'}")
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "CREATE TABLE event_consumer_cursors ("
                " tenant_id VARCHAR(64) NOT NULL,"
                " agent VARCHAR(64) NOT NULL,"
                " PRIMARY KEY (tenant_id, agent))"
            )
        mod = _load("c2407f92e1ab_add_event_consumer_cursors.py")

        with engine.begin() as conn:
            mod.op = _bind_op(conn)
            mod.upgrade()

        cols = _columns(engine, "event_consumer_cursors")
        assert {"consumer_id", "last_event_id", "updated_at"} <= cols

    def test_creates_the_table_when_absent(self, tmp_path):
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'fresh2.db'}")
        mod = _load("c2407f92e1ab_add_event_consumer_cursors.py")

        with engine.begin() as conn:
            mod.op = _bind_op(conn)
            mod.upgrade()

        assert "event_consumer_cursors" in sa.inspect(engine).get_table_names()

    def test_is_idempotent(self, tmp_path):
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'idem.db'}")
        mod = _load("c2407f92e1ab_add_event_consumer_cursors.py")

        with engine.begin() as conn:
            mod.op = _bind_op(conn)
            mod.upgrade()
            mod.upgrade()  # must not raise "table already exists"


def _bind_op(conn):
    """Run a migration's `op` calls against an explicit connection."""
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    ctx = MigrationContext.configure(conn)
    return Operations(ctx)