"""Production storage policy regression tests."""

import pytest

from storage.relational.session import validate_production_database_url


@pytest.mark.parametrize("url", [
    "sqlite:///./data/memora.db",
    "sqlite+libsql://legacy.turso.io",
    "https://legacy.turso.io/database",
])
def test_production_rejects_non_postgresql_database_url(monkeypatch, url):
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="self-hosted PostgreSQL DATABASE_URL"):
        validate_production_database_url(url)


@pytest.mark.parametrize("url", [
    "postgresql://memora:example@postgres:5432/memora_db",
    "postgresql+psycopg://memora:example@postgres:5432/memora_db",
])
def test_production_accepts_postgresql_url(monkeypatch, url):
    monkeypatch.setenv("ENVIRONMENT", "production")
    validate_production_database_url(url)
