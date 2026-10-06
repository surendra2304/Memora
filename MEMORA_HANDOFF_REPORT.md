# Memora — Engineering Handoff Report

**Branch:** `arena/01a10cc6-memora` · **HEAD:** `93dceb4` · **Base:** `812c973`
**Date:** 2026-10-06 · **Commits ahead of base:** 16 · **CI:** green (run `37501477169`)

---

## 1. What this project is

Memora is a **Multi-Tier Cognitive Memory Engine** for the nine-agent "FRIDAY
Universe". FastAPI + SQLAlchemy 2.0 + Alembic, on SQLite/Turso-LibSQL locally and
PostgreSQL in compose, with optional Qdrant vectors and Redis. It stores memories
as typed records, scores them on write, retrieves them through hybrid
vector + keyword + graph reciprocal-rank fusion, decays them over time, and
gates every access through a five-dimension policy engine over RBAC namespaces
(`memora://<agent>/private`).

The dream state in the docs is an agent mesh that shares one durable, auditable,
self-maintaining memory. The gap when I started: the memory was neither safe
under concurrency nor genuinely shared, and nothing in it ever reasoned about its
own contents.

## 2. Verified final state

Every number below comes from a command I ran, not from reading code.

| Check | Command | Result |
|---|---|---|
| Tests | `pytest -q` | **367 passed** (was 171 at base) |
| Lint | `ruff check .` | **All checks passed** |
| Typecheck | — | none configured in the repo |
| Boot | real ASGI lifespan + `GET /health` | **200 healthy** |
| Migrations | `upgrade head → downgrade base → upgrade head` | **converges, fully reversible** |
| Migrations vs real data | `c2407f92e1ab → head` on a populated copy of `data/memora.db` | **4,960 rows preserved, 0 NULLs** |
| Concurrency | `scripts/stress_memora.py --agents 6 --writes 25 --readers 6` | **PASSED** |
| Secrets | `SecretScanner` over 202 tracked files | **no real credentials** |
| OpenAPI surface | `app.openapi()['paths']` | **45 paths** (was 35) |

Stress harness, before → after the concurrency fixes:

| | before | after |
|---|---|---|
| writes accepted | 140×201, **10×400** | **150×201** |
| reads | 2,139, **12 `OperationalError`** | **1,832 all 200, 0 errors** |
| verdict | **FAILED — 2 violations** | **PASSED** |

## 3. Bugs found and fixed

Full detail with file:line and repro is in `MEMORA_PHASE2_BUG_REPORT.md`.
Summary by severity:

**CRITICAL**
1. **Unauthenticated privilege escalation** — `agents.py`, `namespaces.py`,
   `audit.py` had no auth dependency. An unauthenticated `POST
   /namespaces/grants` gave `intelx` `["*"]` on `memora://friday/private`. Fixed
   with router-level `Depends(authenticate_agent)` plus `_authorize_namespace_admin`.
2. **`v1_task.py` dead write path** — called a non-existent `process_write`;
   returned HTTP 200 with `status:"ERROR"` while storing nothing.
3. **Cross-agent idempotency collision** — one agent's replay could suppress
   another's write. Now `Index("uq_memory_idempotency_scope", tenant_id, agent_id,
   idempotency_key, unique=True)` + migration `d7f1c93ab210`.

**HIGH**
1. **Adapter auth headers** — adapters sent `X-Agent-Key`/`X-Purpose`; the server
   reads `X-API-Key`/`X-Access-Purpose`, so all 8 adapters got 401.
2. **Graph authorisation** — `intelx` could link `friday`'s memory. Now 422/404
   plus policy-filtered traversal.
3. **`data/memora.db` tracked in git** with a real personal phone number. Now
   untracked and ignored. **History purge deliberately not performed** — see §7.
4. **`record-interaction` swallowed errors** — `except: pass` then reported
   `"status":"success"`. Now per-item outcomes (422/207/400), plus a latent
   `NameError` on `JSONResponse`.
5. **Multi-tenancy not enforced** — global `unique=True` on `Agent.name` and
   `Namespace.path` let one tenant block another. Now
   `UniqueConstraint("tenant_id","name")` / `("tenant_id","path")` + migration
   `e4a1c7f90b23`.
6. **Cross-tenant context leak** — `build_context_bundle` passed only
   `actor.name`, `hybrid_search` resolved its actor unscoped, and prefetch was
   unbounded. Now tenant-scoped and `.limit(200)`.
7. **Registration races and SQLite lock contention** — TOCTOU in
   `register_agent`/`create_namespace` surfaced `IntegrityError` as HTTP 400, and
   no WAL/busy-timeout produced `database is locked`. Fixed with
   IntegrityError-retry and WAL + `busy_timeout=30000`.

**MEDIUM** — decay compounded cumulatively (0.50→0.18 over 8 cycles) and is now
baseline-derived and idempotent; CORS had the spec-invalid `["*"]` +
`allow_credentials=True` pairing, now config-driven via `MEMORA_CORS_ORIGINS`;
provenance precedence let `forge` store `created_by=friday`; re-granting turned a
time-boxed grant permanent; `config/retrieval_config.json` was being ignored; CI
ran only `verify_diary.py`.

**Test-isolation bug found by CI (not by me)** — `tests/test_agent_adapters.py`
used a bare `TestClient(app)` with no `get_db` override, so it queried whatever
real database file existed. It passed locally only because `data/memora.db`
happened to be populated; on CI's clean checkout the first query failed with `no
such table: agents`. Reproduced deterministically by deleting `data/ci_memora.db`,
A/B verified, fixed in `93dceb4`.

## 4. Upgrades delivered (Phase 4)

| Subsystem | Files | Tests |
|---|---|---|
| Circuit breaking | `core/resilience/circuit_breaker.py` (292 lines) | 12 |
| Self-healing supervisor | `core/resilience/self_healing.py` (327) | 15 |
| Collaboration | `core/collaboration/service.py` (410) | 14 |
| Reflection ("self-brain") | `core/reflection/engine.py` (548) | 23 |
| HTTP surface for all three | 3 routers (338) | 21 |

**Resilience** — 3-state thread-safe breaker with injectable clock and
`expected_exceptions` so caller bugs cannot trip it, wired around the Qdrant
adapter. A supervisor runs five integrity checks (unconverged tombstones,
orphaned vectors, dangling supersession, open circuits, decay backlog), caps
repairs at 500 per check, defaults to dry run, and never raises. It found a real
defect: soft-delete transitions to `LifecycleState.DELETED` but never calls
`delete_embedding`, leaving orphaned vectors the supervisor now evicts.

**Collaboration** — `request_assistance` finds peers who can help, returning
material the asker may already read inline and identifying candidates *without
reading peers' private content*; `contribute` is owner-only and creates a grant
expiring in 24h by default; `delegate` creates a bounded sub-agent. All audited
under `COLLABORATION_*` rules.

**Reflection** — the piece the product was missing. Five insight kinds, each
grounded in data: recurring themes (spanning multiple agents *and* days),
contradictions (differing values for the same measured subject and unit), stale
knowledge, knowledge gaps from the query log, and agent specialisation derived
from what agents actually wrote rather than their declared role. Insights are
stored as `EXPERIENCE` memories tagged with a fingerprint, so runs compound
instead of repeating.

**API** — 10 new routes. Actor identity always comes from the authenticated
header, never the request body; every mutating endpoint is admin-gated and
defaults to a dry run.

## 5. REAL vs CONFIGURED-BUT-UNVERIFIED

| Item | Status | Evidence / reason |
|---|---|---|
| Full test suite (367) | **REAL** | `pytest -q` → 367 passed |
| Lint gate | **REAL** | `ruff check .` clean; CI enforces it |
| Migration chain reversibility | **REAL** | upgrade→base→head on a fresh DB; CI enforces |
| Migrations against real data | **REAL** | `c2407f92e1ab → head` on a populated copy of `data/memora.db`: 4,960 memory_records / 10 agents / 29 namespaces all preserved, `tenant_id` backfilled with 0 NULLs |
| Per-tenant uniqueness (HIGH-6) | **REAL** | verified behaviourally on that migrated copy: duplicate `(default, friday)` rejected, same name in a *different* tenant accepted, duplicate namespace path rejected |
| App boot + `/health` | **REAL** | ASGI lifespan served 200, locally and in CI |
| Concurrency safety | **REAL** | stress harness PASSED, 150/150 writes, 0 lock errors |
| WAL + busy_timeout engaged | **REAL** | `PRAGMA` probe: `journal_mode=wal`, `busy_timeout=30000`, `synchronous=1` |
| Circuit breaker under contention | **REAL** | 16-thread/320-call race test passes |
| Self-healing repairs | **REAL** | 15 tests each prove detect *and* repair |
| Collaboration flows | **REAL** | 14 service + 21 endpoint tests |
| Reflection insights | **REAL** | 23 tests incl. idempotency over 3 runs |
| Secret scanning | **REAL** | 37 tests; 7/7 caught, 0 false positives on 13 benign inputs |
| **Qdrant in production** | **CONFIGURED-BUT-UNVERIFIED** | no Qdrant instance here; adapter exercised against its in-memory mock only |
| **Turso/LibSQL cloud** | **CONFIGURED-BUT-UNVERIFIED** | no credentials in this sandbox; `storage/relational/turso_*` code paths unexercised |
| **Render deployment** | **CONFIGURED-BUT-UNVERIFIED** | `render.yaml` present; no deployment performed |
| **PostgreSQL via compose** | **CONFIGURED-BUT-UNVERIFIED** | Docker not available here |
| **Redis** | **CONFIGURED-BUT-UNVERIFIED** | optional dependency, not exercised |
| **Real LLM embedding calls** | **CONFIGURED-BUT-UNVERIFIED** | no API keys; embedding path uses local fallback |
| `adapters/` in production use | **CONFIGURED-BUT-UNVERIFIED** | correct now and test-covered, but only 1 script imports it in-repo |
| `sdk/memora_client.py` | **CONFIGURED-BUT-UNVERIFIED** | imported only by 2 scripts + 2 test files |

## 6. What I deliberately did not do

- **Did not purge git history.** `data/memora.db` (with a real personal phone
  number) is untracked and ignored now, but it remains in history at `812c973`.
  Purging rewrites SHAs for every collaborator and is your call, not mine.
  **The phone number should be treated as exposed and the file's contents
  reviewed.** No API keys, tokens, or passwords were ever committed.
- **Did not delete dead code.** `memora_upgrade/` is still 1,256 lines imported
  only by its own test. I left it because removing a subsystem is a product
  decision, not a bug fix.
- **Did not refactor while fixing.** Every fix is a surgical diff; upgrades are
  separate commits.
- **Abandoned the HIGH-7 reranker generalisation.** I attempted it, A/B proved my
  rewrite caused the regression, and I reverted it. It is a known limitation, not
  a fix.

## 7. Prioritised roadmap

1. **Rotate and purge.** Treat the phone number in `data/memora.db` history as
   exposed. Decide on a history rewrite (`git filter-repo`) and coordinate the
   force-push. This is the only open item with a privacy consequence.
2. **Verify the cloud paths or delete them.** Turso, Qdrant, PostgreSQL, Render
   and Redis are all configured but unexercised. Each is a plausible place for a
   latent failure because no test has ever run against it. Stand up a staging
   environment and run the stress harness against Turso + Qdrant, or remove the
   integrations you do not intend to run.
3. **Delete or adopt the dead subsystems.** `memora_upgrade/` (1,256 lines),
   `adapters/`, and `sdk/` are reachable only from scripts and their own tests.
   Dead code is where the next bug hides — I found `v1_task.py` calling a method
   that never existed for exactly this reason.
4. **Fix the unbounded reads.** `search_service.py:154` and `:191` both call
   `.all()`, and `query_memories` evaluates policy per candidate in a Python loop
   (`core/memory/service.py:288`) before paginating. Correct today, but retrieval cost grows linearly with
   corpus size and will become the bottleneck first.
5. **Make reflection and self-healing scheduled.** Both are now reachable over
   HTTP and both default to dry run. Nothing calls them on a timer, so the
   self-maintaining part of the dream state still requires an operator. Add a
   scheduler and promote self-healing from dry run once you trust its output.

## 8. Notes for the next engineer

- Use `./.venv/bin/python -m pytest` and `./.venv/bin/ruff`. Gate on the
  configured `F,E9,B` rules; `ruff` with default rules reports ~1,300 style
  findings and is not a useful gate.
- `tests/conftest.py` sets `MEMORA_ALLOW_ANONYMOUS_DEV=1` and a StaticPool
  in-memory SQLite. **StaticPool serialises onto one connection and cannot
  reproduce concurrency races** — use a file-backed SQLite in a tmpdir for those.
- `vector_adapter._mock_store` and `circuit_registry` are process-wide singletons.
  Any test touching them needs an autouse fixture clearing them before *and*
  after, or it will pass alone and fail in the suite.
- `bounded_scope` is an enforced namespace-path prefix (`core/policy/engine.py`
  RULE_3), not free text. Setting it to prose silently locks an agent out of
  every namespace.
- Reading ORM attributes after `db.close()` raises `DetachedInstanceError`.
  SQLite round-trips `DateTime` as naive. `query_memories` returns a bare
  `List[MemoryRecord]`, not an envelope.
