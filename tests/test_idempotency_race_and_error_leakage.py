"""
Regression tests for the concurrent idempotency race and error-detail leakage.

Both were found by hammering the running API with scripts/stress_memora.py at
9 agents x 40 writes: 437 of 1080 writes were rejected, each with a body that
quoted the failing INSERT statement, the bound parameters and the sqlite3
exception class.

The cause was a check-then-insert race. The write pipeline looks for an existing
row carrying the same (tenant_id, agent_id, idempotency_key) and returns it if
found, but two concurrent writes can both clear that check and then collide on
the unique index at commit time. The loser surfaced the IntegrityError to the
caller, which the router reported as a 400 with the raw exception text.

These tests use real threads with separate sessions rather than mocking the
failure, because the whole bug is about two transactions interleaving.
"""
import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from core.identity.service import IdentityService
from core.memory.pipeline.write_service import MemoryWriteService, MemoryPipelineError
from storage.relational.base import Base
from storage.relational.models import MemoryRecord


@pytest.fixture
def file_session_factory(tmp_path):
    """A real on-disk SQLite database with per-thread sessions.

    The shared test_db fixture hands every caller one session, which cannot
    express two transactions racing each other.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    try:
        yield factory
    finally:
        engine.dispose()


def test_concurrent_writes_with_the_same_idempotency_key_all_succeed(file_session_factory):
    """Every racer must be handed the winning record; none may error.

    Before the fix the losers raised IntegrityError, which the API reported as a
    400 quoting the failed SQL.
    """
    setup = file_session_factory()
    IdentityService.register_agent(setup, name="forge", role="worker")
    setup.close()

    key = "shared-retry-token"
    threads = 12
    results: list = []
    errors: list = []
    barrier = threading.Barrier(threads)

    def worker(i: int) -> None:
        db = file_session_factory()
        try:
            barrier.wait(timeout=30)
            res = MemoryWriteService.execute_pipeline(
                db=db,
                content_text="the same logical concurrent write payload for every retry",
                actor_name="forge",
                idempotency_key=key,
                source="api",
            )
            results.append(res)
        except Exception as exc:  # noqa: BLE001 - the point is that none escape
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            db.close()

    pool = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join(timeout=120)

    assert not errors, f"{len(errors)} racer(s) failed: {errors[:3]}"
    assert len(results) == threads, f"only {len(results)}/{threads} writes returned"

    # Every racer must agree on which record won.
    ids = {r.record.id for r in results}
    assert len(ids) == 1, f"racers disagreed on the winning record: {ids}"

    check = file_session_factory()
    try:
        rows = check.query(MemoryRecord).filter(
            MemoryRecord.idempotency_key == key
        ).count()
    finally:
        check.close()
    assert rows == 1, f"expected exactly one stored row, found {rows}"


def test_idempotency_race_recovery_is_flagged_in_the_trace(file_session_factory):
    """A recovered race must be distinguishable from a plain replay."""
    setup = file_session_factory()
    IdentityService.register_agent(setup, name="forge", role="worker")
    key = "trace-probe"
    MemoryWriteService.execute_pipeline(
        db=setup, content_text="first write", actor_name="forge",
        idempotency_key=key, source="api",
    )
    # A plain sequential replay is an idempotent hit, not a recovered race.
    replay = MemoryWriteService.execute_pipeline(
        db=setup, content_text="replayed write", actor_name="forge",
        idempotency_key=key, source="api",
    )
    setup.close()

    assert replay.is_duplicate is True
    dedup = replay.step_outputs["step_6_deduplication"]
    assert dedup["idempotent_hit"] is True
    assert "recovered_from_race" not in dedup, (
        "a sequential replay was reported as a recovered race"
    )


# ---------------------------------------------------------------------------
# The HTTP path, which is where the fix lives
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """A TestClient whose requests each get their own session.

    The shared client fixture hands every request one session, which cannot
    express two transactions racing. Both get_db and the SessionLocal the write
    route uses for its retry are pointed at the same file-backed database, so the
    retry genuinely re-reads what the winning request committed.
    """
    from fastapi.testclient import TestClient

    from apps.api.main import app
    from apps.api.routers import v1_memories
    from storage.relational.session import get_db

    engine = create_engine(
        f"sqlite:///{tmp_path / 'api_race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    setup = factory()
    IdentityService.register_agent(setup, name="forge", role="worker")
    setup.close()

    def override_get_db():
        session = factory()
        try:
            yield session
        finally:
            session.close()

    # Provision forge's credential so the request goes through real
    # authentication rather than the anonymous development path.
    monkeypatch.setenv("FORGE_API_KEY", "test-key")
    # raising=False so this test still runs against a build without the retry,
    # where the route has no SessionLocal to point at - it must then fail on the
    # race itself rather than on fixture setup.
    monkeypatch.setattr(v1_memories, "SessionLocal", factory, raising=False)
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client, factory
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_concurrent_http_writes_sharing_an_idempotency_key_all_succeed(api_client):
    """The race that rejected 437 of 1080 writes under load must not reach callers.

    Every racer must be told the write succeeded and be handed the same record,
    which is what a sequential idempotent replay already returns.
    """
    client, factory = api_client
    key = "http-shared-retry-token"
    threads = 8
    codes: list = []
    ids: list = []
    barrier = threading.Barrier(threads)

    def worker(i: int) -> None:
        barrier.wait(timeout=30)
        resp = client.post(
            "/v1/memories",
            json={
                "content_text": f"http concurrent write {i} for the same logical fact",
                "idempotency_key": key,
            },
            headers={"X-Agent-Name": "forge", "X-API-Key": "test-key"},
        )
        codes.append(resp.status_code)
        if resp.status_code == 201:
            ids.append(resp.json()["id"])

    pool = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join(timeout=120)

    rejected = [c for c in codes if c != 201]
    assert not rejected, f"{len(rejected)} racer(s) rejected with {sorted(set(rejected))}"
    assert len(set(ids)) == 1, f"racers disagreed on the winning record: {set(ids)}"

    check = factory()
    try:
        rows = check.query(MemoryRecord).filter(
            MemoryRecord.idempotency_key == key
        ).count()
    finally:
        check.close()
    assert rows == 1, f"expected exactly one stored row, found {rows}"


# ---------------------------------------------------------------------------
# Error-detail leakage
# ---------------------------------------------------------------------------

def test_client_input_errors_are_still_explained(client: TestClient):
    """A caller submitting bad input must be told what was wrong.

    Blanket-converting every unhandled exception to an opaque 500 would make the
    API impossible to use correctly, so input rejections keep their message.
    """
    resp = client.post("/v1/memories", json={
        "content_text": "x",
        "target_namespace_path": "memora://../../etc/passwd",
    })
    assert resp.status_code == 400, f"got {resp.status_code}: {resp.text}"
    assert "malformed" in resp.text.lower(), (
        f"the caller was not told why the path was rejected: {resp.text}"
    )


def test_empty_content_is_rejected_as_a_client_error(client: TestClient):
    """An empty body is the caller's fault and must be reported as such.

    Whether it is 400 or 422 depends on which layer rejects it first - request
    validation catches this one - but it must never surface as a server fault.
    """
    resp = client.post("/v1/memories", json={"content_text": ""})
    assert resp.status_code in (400, 422), f"got {resp.status_code}: {resp.text}"
    assert resp.status_code != 500


def test_memory_pipeline_error_is_treated_as_client_input():
    """The classification the router relies on."""
    from apps.api.routers.v1_memories import _unexpected_write_error

    resp = _unexpected_write_error(MemoryPipelineError("Content text cannot be empty."), "x")
    assert resp.status_code == 400
    assert resp.detail == "Content text cannot be empty."

    resp = _unexpected_write_error(ValueError("Namespace path is malformed."), "x")
    assert resp.status_code == 400


def test_unexpected_errors_become_500_without_leaking_internals():
    """A database fault must not ship SQL text or exception classes to the caller."""
    from apps.api.routers.v1_memories import _unexpected_write_error

    leaked = ("(sqlite3.IntegrityError) UNIQUE constraint failed: "
              "memory_records.tenant_id, memory_records.agent_id\n"
              "[SQL: INSERT INTO memory_records (id) VALUES (?)]")
    resp = _unexpected_write_error(RuntimeError(leaked), "this request")

    assert resp.status_code == 500, (
        f"an unhandled server fault was reported as {resp.status_code}"
    )
    assert "INSERT INTO" not in str(resp.detail)
    assert "sqlite3" not in str(resp.detail)
    assert "UNIQUE constraint" not in str(resp.detail)


def test_sqlalchemy_errors_are_never_treated_as_client_input():
    """A DB error must not be downgraded to 400 just because it is an Exception."""
    from apps.api.routers.v1_memories import _unexpected_write_error

    resp = _unexpected_write_error(IntegrityError("stmt", {}, Exception("boom")), "x")
    assert resp.status_code == 500
