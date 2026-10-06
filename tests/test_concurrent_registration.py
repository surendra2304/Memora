"""
Regression tests for the check-then-insert races in IdentityService.

register_agent and create_namespace both did:

    row = db.query(...).filter(...).first()
    if not row:
        db.add(...)
        db.commit()

Under concurrency two callers both see "not found", both insert, and the loser
raises IntegrityError. That surfaced as an HTTP 400 to the caller. Reproduced
with scripts/stress_memora.py: 6 agents x 25 concurrent writes produced 10
rejections with

    (sqlite3.IntegrityError) UNIQUE constraint failed: agents.tenant_id, agents.name
    (sqlite3.IntegrityError) UNIQUE constraint failed: namespaces.tenant_id, namespaces.path

Both now catch IntegrityError, roll back, and adopt the row the winning request
created.

These tests use a file-backed SQLite shared across threads rather than the
in-memory StaticPool from conftest, because StaticPool serialises everything
onto one connection and cannot reproduce the race.
"""
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from core.identity.service import IdentityService
from storage.relational.base import Base
from storage.relational.models import Agent, Namespace


@pytest.fixture
def shared_db():
    """A file-backed SQLite that real threads can contend on."""
    tmpdir = tempfile.mkdtemp(prefix="memora_race_")
    path = os.path.join(tmpdir, "race.db")
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    yield factory
    engine.dispose()


def _register(factory, name, tenant_id):
    """Register and return plain values.

    The session must stay open while attributes are read, so identity is
    extracted here rather than returning a detached ORM instance the caller
    would touch after close().
    """
    db = factory()
    try:
        agent = IdentityService.register_agent(db, name, tenant_id=tenant_id)
        return {"id": agent.id, "tenant_id": agent.tenant_id, "name": agent.name}
    finally:
        db.close()


def _resolve(factory, path, tenant_id):
    db = factory()
    try:
        ns = IdentityService.resolve_namespace(db, path, tenant_id=tenant_id)
        return {"id": ns.id, "path": ns.path, "tenant_id": ns.tenant_id}
    finally:
        db.close()


def test_concurrent_register_agent_creates_exactly_one_row(shared_db):
    """Thirty threads racing to register the same agent must converge on one row."""
    workers = 30
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda _: _register(shared_db, "friday", "acme"), range(workers)))

    ids = {r["id"] for r in results}
    assert len(ids) == 1, f"concurrent registration produced {len(ids)} distinct agents: {ids}"

    db = shared_db()
    try:
        count = db.query(func.count(Agent.id)).filter(Agent.name == "friday").scalar()
    finally:
        db.close()
    assert count == 1, f"expected exactly one agent row, found {count}"


def test_concurrent_resolve_namespace_creates_exactly_one_row(shared_db):
    workers = 30
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(
            pool.map(lambda _: _resolve(shared_db, "memora://friday/private", "acme"), range(workers))
        )

    ids = {r["id"] for r in results}
    assert len(ids) == 1, f"concurrent resolve produced {len(ids)} namespaces: {ids}"

    db = shared_db()
    try:
        count = (
            db.query(func.count(Namespace.id))
            .filter(Namespace.path == "memora://friday/private")
            .scalar()
        )
    finally:
        db.close()
    assert count == 1, f"expected exactly one namespace row, found {count}"


def test_concurrent_registration_across_tenants_stays_isolated(shared_db):
    """Racing tenants must each get their own agent, never collide."""
    tenants = [f"tenant{i}" for i in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda t: _register(shared_db, "forge", t), tenants))

    assert len({r["id"] for r in results}) == len(tenants)
    assert {r["tenant_id"] for r in results} == set(tenants)


def test_the_unique_index_is_what_catches_the_loser(shared_db):
    """Guard the precondition: without the composite index the race would corrupt data."""
    db = shared_db()
    try:
        db.add(Agent(tenant_id="acme", name="dup", role="worker"))
        db.commit()
        db.add(Agent(tenant_id="acme", name="dup", role="worker"))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
    finally:
        db.close()
