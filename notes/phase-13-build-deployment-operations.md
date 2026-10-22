# Phase 13 — Build, configuration, deployment and operations

**Status:** Complete. Deployment files were inspected without starting services or contacting external hosts. YAML syntax was parsed locally. The repository declares Docker and Render deployment paths, but this environment has neither Docker nor Podman; a Python wheel build was attempted offline and failed during setuptools package discovery. No source or deployment file was changed.

## Deployment inventory

| Surface | Tracked configuration | Observed shape |
|---|---|---|
| Container image | `Dockerfile:1-32`, `.dockerignore:1-17` | Python 3.11 slim; pip installs `requirements.txt`; copies the filtered build context; runs migrations then Uvicorn. |
| Local multi-service stack | `docker-compose.yml:1-65` | Four services: API, PostgreSQL, Redis, Qdrant; three named data volumes. |
| Hosted manifest | `render.yaml:1-57` | One Docker web service, free plan, `/health` check, production environment, SQLite fallback URL plus configured Turso URL and `sync: false` secret slots. No Render-managed Redis or Qdrant service is declared. |
| Python package metadata | `pyproject.toml:1-44`, `requirements.txt:1-18` | Setuptools PEP 517 project metadata and runtime/dev dependency groups; the requirements file also installs test tooling. |
| Local environment example | `.env.example:1-35`, `core/config.py:8-60` | Development/SQLite/local-service defaults; example contains empty `MEMORA_API_KEY`/`MEMORA_MASTER_KEY` values but no named agent API-key variables. |
| CI | `.github/workflows/verify.yml:1-80` | Python lint/test/migration/boot checks; no container build step (Phase 11). |

## Checks actually run

- [FACT] `PyYAML.safe_load` parsed `docker-compose.yml` and `render.yaml` successfully. The Compose document contains four services and five published host ports. This is a syntax parse only, not Docker Compose semantic validation.
- [FACT] Neither `docker` nor `podman` is installed in the execution environment. Consequently `docker compose config`, `docker build`, image inspection, and container startup were not run.
- [FACT] An offline packaging attempt used `PIP_NO_INDEX=1 ./.venv/bin/python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/... .`. It failed during metadata generation: setuptools refused automatic flat-layout discovery because multiple top-level packages/directories were found and no explicit package discovery is configured (`pyproject.toml:1-4`). No wheel was produced and no build artifact remained in the repository. This does not prove the Dockerfile build fails: the Dockerfile installs `requirements.txt` and runs source directly rather than building a wheel.
- [FACT] The deployment manifests and endpoint URLs were not exercised; no Render, Turso, PostgreSQL, Redis, or Qdrant service was contacted. The Phase 11 suite and local app boot remain the actual application-level runtime evidence.

## Configuration and operations findings

### Container build and startup

- [FACT] The image uses a floating `python:3.11-slim` tag, installs build tools/`libpq-dev`/`curl`, installs the full `requirements.txt`, then uses `COPY . .` (`Dockerfile:1-16`). There is no `USER`, `HEALTHCHECK`, or image digest pin in the Dockerfile (`:1-32`).
- [FACT] The container command runs `alembic upgrade head`, but on failure it prints a message and proceeds to Uvicorn (`Dockerfile:22-32`).
- [INFERENCE — operations risk] A migration error does not fail the container command. The application health endpoint can report a degraded database/event/vector status (`apps/api/routers/health.py:18-54`), but Compose itself defines no API healthcheck or readiness dependency; operators must monitor that endpoint rather than infer schema readiness from a running process.
- [FACT] `.dockerignore` excludes `.env`, environment files, tests, local `data/*`, and logs, but does not exclude `MEMORA_PHASE2_BUG_REPORT.md` or `notes/` (`.dockerignore:1-17`). The Dockerfile copies the remaining context (`Dockerfile:12-16`).
- [INFERENCE — privacy/packaging risk] A Docker build from this checkout would include the historical bug report and review-note files unless the build context is filtered elsewhere. The historical report has personal information (`MEMORA_PHASE2_BUG_REPORT.md:182,372`); that information is not reproduced in these notes. No image was built, so actual image contents were not inspected.
- [FACT] The first `.dockerignore` line begins with a UTF-8 BOM (`.dockerignore:1`). [HYPOTHESIS] If the Docker ignore parser in use does not strip that marker, its `.git` exclusion might not match; this behavior was not verifiable without Docker, so this report does not claim that `.git` is copied into an image.

### Compose topology and credentials

- [FACT] Compose publishes API port 8000, PostgreSQL 5432, Redis 6379, and Qdrant 6333/6334; the host-IP field is omitted (`docker-compose.yml:7-8,27-28,41-42,55-57`). PostgreSQL and Redis have healthchecks; API and Qdrant do not. API dependencies are listed without `service_healthy` conditions (`:14-17,31-60`).
- [INFERENCE — network exposure risk] Under standard Compose port-publishing behavior, omitted host IPs bind published ports on host interfaces. If this stack is run on an untrusted/public host without network controls, the database/cache/vector ports should not be assumed private merely because they are internal services.
- [FACT — security notice] The Compose API and PostgreSQL service definitions contain literal local database credentials at `docker-compose.yml:10,24-25`; the values are omitted from this and the final report. The Compose file also exposes the database/cache/vector ports as above. Treat the credentials as exposed, and do not reuse them in a shared or production environment.
- [FACT] The API Compose service sets only database, Redis, Qdrant and `MEMORA_ENV` variables; it does not pass any of the named agent credentials required by `authenticate_agent` (`docker-compose.yml:9-17`; `apps/api/dependencies.py:57-96`). A local production-mode call to `authenticate_agent` with a Friday identity and no configured `FRIDAY_API_KEY` returned HTTP 503, consistent with the code's explicit missing-credential branch (`:87-93`).
- [INFERENCE — configuration defect] As checked in, Compose does not configure the API keys needed for authenticated agent traffic. Unless they are added through an unshown override or deployment-level injection, requests for configured agent identities will receive 503 rather than authenticate. The default `MEMORA_API_KEY` in `.env.example` does not replace the per-agent key mapping used by `authenticate_agent`.
- [FACT] PostgreSQL, Redis, and Qdrant use named data volumes (`docker-compose.yml:29-30,43-44,58-65`). Redis and Qdrant have no password/API-key configuration in this manifest; Qdrant image is pinned to tag `v1.9.0`, while base Python/Postgres/Redis image tags are not digest-pinned (`:21,38-39,52-60`; `Dockerfile:1`).

### Render manifest and service readiness

- [FACT] The Render manifest defines only one Docker web service, sets `ENVIRONMENT=production`, uses `/health`, supplies a SQLite file URL and a Turso endpoint, and leaves Turso plus service API keys as `sync: false` values (`render.yaml:1-57`). It does not declare `QDRANT_URL`, `REDIS_URL`, a Qdrant service, or a Redis service.
- [FACT] In production, relational storage prefers the configured Turso URL only when both its URL and token are available; otherwise a SQLite backend is non-durable and `storage_ready()` is false (`storage/relational/session.py:20-34,99-109,141-172`). The manifest's unset `sync: false` token slots therefore require operator-supplied secrets. This was not tested against the named Turso service.
- [FACT] `QDRANT_URL` defaults to `http://localhost:6333` (`core/config.py:27-30`). In production, the vector adapter does not connect to localhost; a local-only adapter probe returned readiness `available: false` (`storage/vector/qdrant_adapter.py:43-83`). `/health` requires vector availability and returns 503 otherwise (`apps/api/routers/health.py:39-54`).
- [INFERENCE — deployment readiness risk] If deployed exactly from the checked-in Render manifest without an out-of-band `QDRANT_URL`, the Qdrant default remains localhost, the production vector adapter is unavailable, and `/health` will report 503 even if Turso is configured. A dashboard-supplied Qdrant URL or later manifest change could alter this; neither is represented in the repository manifest.
- [FACT] With production and the default local Redis URL, the event emitter deliberately avoids connecting to localhost and operates in process-local mode (`core/events/emitter.py:49-69`). The durable event store separately depends on Turso credentials in production (`storage/relational/turso_events.py:17-34`).

## Exit check and handoff

- [FACT] The local YAML parse passed, offline wheel metadata generation failed, Docker/Podman tooling was absent, and no deployment target was exercised. These are recorded as distinct outcomes rather than treating source configuration as a successful deployed build.
- [FACT] Phase 13 is complete. The final synthesis in Phase 14 must preserve the committed-credential alert, unverified deployment caveats, build/test separation, and all Phase 12 authorization and tenant findings.
