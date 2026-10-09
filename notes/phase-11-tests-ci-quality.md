# Phase 11 — Test suite, CI and quality evidence

**Status:** Complete. Tests and local build/runtime checks were executed in the supplied Python 3.11 virtual environment. All writes created by these checks were isolated under `/tmp`; no repository source or tracked data was changed.

## Inventory and CI contract

- [FACT] The repository has 49 `tests/test_*.py` files, 372 directly declared `test_*` functions and 2 test classes; parametrization increases the collected cases (`tests/`; inventory command run 2026-10-07).
- [FACT] The single workflow, `.github/workflows/verify.yml:1-80`, uses Python 3.11 and runs dependency installation, `ruff check .`, `pytest -q`, Alembic `upgrade head → downgrade base → upgrade head`, `scripts/verify_diary.py`, and an ASGI `TestClient` boot/health check. It does not run a Docker build or produce a coverage report (`.github/workflows/verify.yml:21-80`; `pyproject.toml:38-79`).
- [FACT] The suite fixture uses an in-memory SQLite database for each function and overrides API `get_db`; its `TestClient` context starts the FastAPI lifespan (`tests/conftest.py:21-48`). Tests that exercise Turso/network calls use monkeypatched `urlopen`/injected clients (`tests/test_durable_event_feed.py`; `tests/test_turso_sync_safety.py`).

## Executed checks (2026-10-07)

- [FACT] `./.venv/bin/ruff check .` → **All checks passed** (Ruff 0.16.10).
- [FACT] Full `pytest -q` with Python 3.11.2 / pytest 9.1.1 and local-only optional services → **434 passed, 1 xfailed, 7 warnings in 23.30 s**. Installed versions observed include FastAPI 0.142.2, SQLAlchemy 2.1.3, Pydantic 2.13.5, pytest-asyncio 1.4.0, and HTTPX 0.28.1. No external service credentials were present in the test environment; the run explicitly disabled Turso/Redis and pointed Qdrant at local port 1. Tests completed without contacting a hosted service.
- [FACT] The single xfail is strict and explicitly documents the unresolved concurrent idempotency race (`tests/test_idempotency_race_and_error_leakage.py:51-67`). Running that case with `--runxfail` confirmed the behavior: in one 12-thread SQLite race, **11 of 12 writers failed** with `IntegrityError: UNIQUE constraint failed: memory_records.tenant_id, memory_records.agent_id, memory_records.idempotency_key` (`:67-106`). This is a current reproduced local test failure, not merely the historical stress result quoted in the test marker.
- [FACT] The 7 warnings comprised one Starlette warning that `httpx` with `starlette.testclient` is deprecated, three `HTTP_422_UNPROCESSABLE_ENTITY` deprecations, and three Alembic warnings that `path_separator` is not configured. They did not fail the run.
- [FACT] In a fresh temporary SQLite DB, the migration sequence `alembic upgrade head`, `alembic downgrade base`, `alembic upgrade head` **passed** across all 9 revisions (`migrations/versions/`; commands executed with `/tmp` DB). This complements, but does not negate, Phase 8's separate failure when `Base.metadata.create_all()` pre-created tables before the migration chain.
- [FACT] `python scripts/verify_diary.py` **passed all 7 diary files** it enumerated (2026-08-29 through 2026-09-04).
- [FACT] The CI-shaped local application smoke check opened the API with lifespan and isolated SQLite, asserted required OpenAPI paths, then requested `/health`: **45 OpenAPI paths**, HTTP 200, status `healthy`, backend `sqlite`, vector status `in_memory`. The configured remote services were disabled; the Qdrant URL was a local refused connection, not a hosted call.
- [FACT] No repository data file was created by these checks; test/migration/boot SQLite databases were placed under `/tmp`.

## Limitations and exit check

- [FACT] Passing test-suite results do not establish behavior against production PostgreSQL/Turso, Redis, or Qdrant; this run used SQLite, process-local vector fallback and network mocks/disabled external integrations.
- [FACT] CI does not run Docker image build, coverage instrumentation/reporting, production-backend integration tests, or an external service smoke test. The in-memory test fixture and one strict xfail leave the described concurrent idempotency bug outside the passing portion of the suite.
- [FACT] Phase 11 is complete; Phase 12 will conduct controlled, synthetic security/privacy probes and separate observed behavior from static hypotheses.
