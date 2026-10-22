# Project: Memora Tier-1 Production Upgrade

## Architecture
Memora is the persistent episodic memory, knowledge graph, and vector recall microservice for the FRIDAY Universe, deployed on Oracle Cloud Always Free (`friday_mesh`).

- **API Layer (`apps/api`)**: FastAPI application providing 39 routes across 15 router modules, running on Uvicorn (internal container port 8000, host port 8006). Enforces decoupled liveness (`GET /health`), readiness (`GET /ready`), telemetry metrics (`GET /metrics`, `GET /v1/metrics`), uniform error envelopes (`{"error": ..., "message": ..., "success": false}`), and graceful shutdown signal trapping.
- **Resilience Layer (`core/resilience`)**: Asynchronous circuit breaker pattern with CLOSED, OPEN, HALF_OPEN states and exponential backoff with full jitter for all external network dependencies (`Qdrant`, `Turso`, `Redis`, `OpenAI`, `Anthropic`, and peer microservices in `friday_mesh`).
- **Core Memory & Cognition (`core/memory`, `core/policy`, `core/events`)**: 10-step memory write ingestion pipeline, 5D policy governance engine, tri-modal hybrid search (Dense + FTS + Graph) with RRF, token-budgeted hierarchical context summarizer, and durable replay event feed.
- **Caching Layer (`core/cache`)**: Thread-safe in-memory cache (`TenantScopedCache` with TTL/LRU) and Redis event pub/sub. Caches high-frequency embedding generations and hybrid search queries with tenant isolation.
- **Storage Layer (`storage/relational`, `storage/vector`)**: SQLite 3 in WAL mode (`journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`) with Alembic migrations (11 revisions) as authoritative persistent single-node storage on Oracle Cloud Always Free, plus optional Turso libSQL synchronization. Vector store backed by Qdrant with in-memory offline fallback.
- **Container & DevOps (`Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml`)**: Hardened `python:3.11-slim` container running under non-root `appuser` (UID 1000) with least-privilege read-only application code, startup environment validation, and automated CI enforcing linting, migration rollback/upgrade, 85%+ test coverage, and container build.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| F1 | Async HTTP Execution | Replace synchronous blocking HTTP calls (`requests`, `urllib`) with `httpx.AsyncClient` with connection pooling and timeouts | M1 | Survey (R1) |
| F2 | Circuit Breakers | Async circuit breaker pattern (CLOSED/OPEN/HALF_OPEN) with failure thresholds and recovery timeouts across external dependencies | M1 | Survey (R1) |
| F3 | Retries & Fallbacks | Exponential backoff with jitter and structured graceful fallbacks for external dependencies | M1 | Survey (R1) |
| F4 | Pure Liveness Probe | Decoupled `GET /health` returning process liveness and service metadata (`{"status": "healthy", "service": "memora", "version": "2.0.0"}`) | M3 | Survey (R2) |
| F5 | Readiness Probe | Dedicated `GET /ready` verifying DB pool, SQLite WAL readiness, Qdrant and Redis reachability (HTTP 200 vs 503) | M3 | Survey (R2) |
| F6 | Structured Telemetry | `GET /metrics` and `GET /v1/metrics` exporting uptime, request throughput, error counts (4xx/5xx), latency percentiles, and memory RSS | M3 | Survey (R2) |
| F7 | Uniform Error Envelope | Global exception handlers enforcing `{"error": "<error_code>", "message": "<human_readable_detail>", "success": false}` | M3 | Survey (R2) |
| F8 | Port Architecture | Listen on internal container port 8000 (host port 8006), respecting `PORT` environment variable with fallback to 8000 | M3 | Survey (R2) |
| F9 | SQLite WAL & Pragmas | Enforce `PRAGMA journal_mode=WAL;`, `busy_timeout=5000;`, `synchronous=NORMAL;` across all lifecycles; fix `storage_ready()` for SQLite in production | M2 | Survey (R3) |
| F10 | Thread-Safe Caching | Thread-safe in-memory caching (TTL/LRU) with tenant isolation for high-frequency queries and embeddings | M2 | Survey (R3) |
| F11 | Signal Trapping & Checkpoint | Clean `SIGTERM`/`SIGINT` handling: drain in-flight writes, flush WAL checkpoint (`wal_checkpoint(TRUNCATE)`), disconnect pools in <=5s; Docker `exec uvicorn` | M2 | Survey (R3) |
| F12 | Startup Env Validation | Validate required environment variables at startup; fail fast with clear diagnostics pointing to `.env.example` | M4 | Survey (R4) |
| F13 | Zero Hardcoded Secrets | Eliminate hardcoded tokens and dead Render URLs across repository | M4 | Survey (R4) |
| F14 | Hardened Dockerfile | `python:3.11-slim`, non-root `appuser` (UID 1000), read-only code, writable `/app/data` & `/app/logs`, native Docker `HEALTHCHECK` | M4 | Survey (R4) |
| F15 | 85%+ Test Coverage | Expand pytest suite to achieve verified 85%+ coverage across core pipelines, policy engines, search, and routers | M5 | Survey (R5) |
| F16 | Route Path Testing | Integration tests for all route 200 OK success paths and 400, 401, 403, 404, 422, 503 failure paths | M5 | Survey (R5) |
| F17 | 100% Offline Mocks | Fully mock third-party network APIs (OpenAI, Anthropic, Qdrant, Redis, peer services) in `conftest.py` | M5 | Survey (R5) |
| F18 | Alembic Full Cycle Test | Full `alembic upgrade head -> downgrade base -> upgrade head` integration test | M2 | Survey (R5) |
| F19 | World-Class README | Comprehensive `README.md` with role & scope, Mermaid architecture diagram, full API spec, env table, run instructions | M4 | Survey (R6) |
| F20 | Comprehensive .env.example | Complete `.env.example` mirroring all configurations, circuit breakers, and timeouts | M4 | Survey (R6) |
| F21 | CI/CD Automation | Hardened `.github/workflows/ci.yml` running linting, Alembic test, pytest with 85%+ coverage gate, live probes, and Docker build | M4 | Survey (R6) |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Async Resilience & Networking | F1, F2, F3: httpx.AsyncClient connection pooling, async circuit breakers, jittered retries, graceful fallbacks | none | COMPLETED |
| M2 | Storage Durability & Lifecycle | F9, F10, F11, F18: SQLite WAL & pragmas, production storage authority, in-memory cache, signal trapping, Alembic lifecycle | none | COMPLETED |
| M3 | Contracts, Probes & Error Envelopes | F4, F5, F6, F7, F8: /health, /ready, /metrics, uniform error envelope, PORT 8000/8006 compliance | M1, M2 | COMPLETED |
| M4 | Security Governance & DevOps | F12, F13, F14, F19, F20, F21: Startup validator, secrets scrub, Dockerfile hardening, README, .env.example, CI workflow | M3 | COMPLETED |
| M5 | Final Milestone: 100% E2E Pass & Adversarial Hardening | F15, F16, F17: Pass all E2E tests (Tiers 1-4), 85%+ coverage gate, Tier 5 adversarial testing & forensic audit | M4, E2E Track | COMPLETED |

## Parallel Track: E2E Testing Track
- **Owner**: `teamwork_preview_test_writer` / Dedicated E2E Test Suite Orchestration
- **Scope**: Requirements-driven, opaque-box test suite covering Tiers 1-4 (minimum 11 * 21 + 11 = ~242 test cases) derived strictly from `ORIGINAL_REQUEST.md`.
- **Output**: `TEST_INFRA.md` and `TEST_READY.md`.

## Interface Contracts
### Networking (`core/resilience`) ↔ Routers & Storage
- `AsyncCircuitBreaker`:
  - Methods: `async def call(func, *args, **kwargs) -> Any`
  - States: `CircuitState.CLOSED`, `CircuitState.OPEN`, `CircuitState.HALF_OPEN`
  - Metrics: `failure_count`, `success_count`, `last_failure_time`
  - Fallback: returns predefined structured fallback or raises `CircuitOpenError` caught by router fallback handlers.
- `AsyncHttpClientPool`:
  - Singleton `get_async_client() -> httpx.AsyncClient` with default timeout 10.0s, connection limits (max_connections=100, max_keepalive_connections=20).

### Storage (`storage/relational`) ↔ Application Lifecycle
- `storage_ready() -> bool`:
  - In production mode, returns `True` for persistent SQLite when directory exists and WAL mode is verified.
- `checkpoint_wal() -> Tuple[bool, int, int]`:
  - Returns `(is_busy, log_frames, checkpointed_frames)`. Checks that `is_busy == 0`.
- `graceful_shutdown(timeout=5.0)`:
  - Traps `SIGTERM`/`SIGINT`, stops accepting new writes, flushes in-flight write queue, runs `checkpoint_wal()`, disposes SQLAlchemy pool, disconnects Qdrant (`vector_adapter.disconnect()`), and stops event sync.

### Probes & Error Envelope (`apps/api`) ↔ Microservice Mesh
- `GET /health`:
  - Status 200: `{"status": "healthy", "service": "memora", "version": "2.0.0"}`
- `GET /ready`:
  - Status 200: `{"status": "ready", "database": "healthy", "wal": "ready", "vector_store": "available", "event_bus": "available"}`
  - Status 503: `{"status": "unready", "database": "...", "wal": "...", "vector_store": "...", "event_bus": "..."}`
- Uniform Error Envelope (all 4xx/5xx):
  - `{"error": "<error_code>", "message": "<human_readable_detail>", "success": false}`

## Code Layout
- `apps/api/` — FastAPI routes, probes, exception handlers, middlewares, ASGI lifespan.
- `core/resilience/` — Circuit breakers, retry mechanisms, timeouts, fallback policies.
- `core/cache/` — Thread-safe in-memory cache (`TenantScopedCache`), Redis adapters.
- `core/config.py` — Settings, startup environment validation.
- `storage/relational/` — SQLAlchemy engine, session lifecycle, WAL pragmas, SQLite durability.
- `storage/vector/` — Qdrant adapter, disconnect lifecycle, embedding generator with caching.
- `tests/` — Unit, integration, and E2E test suites with mock fixtures.
- `Dockerfile`, `docker-compose.yml`, `.github/workflows/ci.yml` — Deployment and automation.
