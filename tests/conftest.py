"""Pytest Fixtures for Memora Test Suite

The suite runs against an explicitly-passed, file-backed SQLite database. That
is the only place SQLite is allowed: runtime code fails closed unless an Oracle
Autonomous Database DSN is configured, and the fixture below sets the URL
before ``storage.relational.session`` is imported so the lazy engine picks up
the test path rather than a production DSN.
"""
import os
import tempfile

os.environ.setdefault("MEMORA_ALLOW_ANONYMOUS_DEV", "1")
os.environ.setdefault("MEMORA_EMBEDDING_MODE", "hashing")
os.environ["MEMORA_ENV"] = "testing"
os.environ["ENVIRONMENT"] = "testing"

_TEST_DB_DIR = tempfile.mkdtemp(prefix="memora_tests_")
os.environ["DATABASE_URL"] = os.getenv(
    "MEMORA_TEST_DATABASE_URL", f"sqlite:///{_TEST_DB_DIR}/memora_test.db"
)

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from storage.relational.base import Base
from storage.relational.session import get_db
from apps.api.main import app

@pytest.fixture(scope="function")
def test_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)

@pytest.fixture(scope="function")
def client(test_db):
    def override_get_db():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
