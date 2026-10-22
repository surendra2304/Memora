# Phase 2 — Architecture and codebase map

**Status:** Complete. **Method:** tracked-file inventory, package-tree review, and static import-edge sampling in the production tree. No source changes.

## Quantified structure

- [FACT] The tracked inventory is 211 files (Phase 0). By top-level location: root 23; `.github` 1; `adapters` 13; `apps` 19; `config` 2; `core` 38; `diary` 7; `memora_upgrade` 19; `migrations` 11; `research` 1; `scripts` 15; `sdk` 3; `storage` 9; `tests` 50. Counts reconcile to 211.
- [FACT] The API/runtime layer is centered on `apps/api/main.py`; its router package imports health, agent, namespace, memory, audit, v1 context/memory/metrics/namespace/task/events/resilience/collaboration/reflection and mesh-event routers (`apps/api/routers/__init__.py:1-14`). The app mounts those routers in the lifespan-composed FastAPI application (`apps/api/main.py:148-172,199-214`).
- [FACT] `core/` separates config, identity, policy, memory, lifecycle, collaboration, reflection, events, metrics and resilience. The memory package contains schemas, CRUD, search, graph, experience, write-pipeline, context-builder/budgeter/reranker components. `storage/` contains SQLAlchemy relational/session models, Turso event/sync adapters, and vector embedding/Qdrant adapters.
- [FACT] `tests/` contains 50 tracked files; `migrations/` has 11 tracked files including the Alembic environment/template and nine version scripts; `adapters/`, `sdk/`, `memora_upgrade/`, `scripts/`, and `research/` are tracked auxiliary surfaces, not all part of the request path.

## Static architecture map

```text
Agent / SDK client
    │ HTTP + named credential
    ▼
apps/api/main.py ── lifespan: init DB, optional Turso import, Qdrant connect,
    │                 event-emitter connect/sync; mounts routers
    ▼
apps/api/routers ── dependencies/auth ── schemas
    │
    ├── core.identity / core.policy
    ├── core.memory: write pipeline, search, graph, context, experience
    ├── core.lifecycle: state machine, decay, supersession
    ├── core.collaboration / reflection / resilience
    └── core.events / metrics
             │
             ├── storage.relational: SQLAlchemy ORM/session; SQLite/PostgreSQL/Turso paths
             ├── storage.vector: embedding + Qdrant (process-local fallback in development)
             └── storage.relational.turso_events: optional durable event feed

Separate/unwired library surfaces (README.md:70-75; no imports from apps/core/storage):
    memora_upgrade/   adapters/ + sdk/   research/ and operator scripts
```

- [FACT] The import direction is not a strict ports-and-adapters boundary: core services directly import SQLAlchemy models, the concrete Qdrant adapter, policy, event emitter and metrics; routers call those services (`core/memory/service.py:10-24`, `core/memory/pipeline/write_service.py:12-23`, `core/memory/search_service.py:9-15`, `apps/api/routers/v1_memories.py:14-33`). [INFERENCE] Replacing a storage or event backend requires changes in core services/adapters rather than only swapping an abstract implementation.
- [FACT] Startup initializes the relational schema, optionally imports from Turso, connects Qdrant and the event emitter, starts cloud event sync, then stops that sync on shutdown (`apps/api/main.py:148-165`). CORS is configured from settings, empty by default (`apps/api/main.py:174-197`).
- [FACT] There is one static dashboard asset at `apps/api/static/index.html`; root and `/dashboard` serve it when present (`apps/api/main.py:234-241`).
- [FACT] The primary API does not import `memora_upgrade`, `adapters`, or `sdk` from `apps/`, `core/`, or `storage/` (static import search found no matches). Separate tests and a small number of scripts do import them, so “unwired” means not on the API service path, not absent or untested.

## Evidence limits and exit check

- [FACT] This map is static source/import evidence; it does not establish deployed topology or external service availability.
- [INFERENCE] The project is one Python API/service with adjacent experimental/compatibility libraries and operator artifacts, rather than independently deployed packages/daemons. This follows from the single FastAPI entrypoint, one dependency manifest and the import map; the actual deployment composition is Phase 13.
- [FACT] Phase 2 is complete; next is the complete route/schema/lifecycle inventory.
