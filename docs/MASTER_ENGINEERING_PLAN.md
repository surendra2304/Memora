# Memora Master Engineering Plan

**Owner:** Memora maintainers
**Baseline reviewed:** 2026-10-22
**Default branch:** `main`
**Plan rule:** Every implementation task lands on a clean, focused branch, carries regression tests, and merges only after the repository CI suite passes. Do not equate local mocks with verification of hosted PostgreSQL, Qdrant, Redis, Turso, credentials, or deployments.

## Executive objective

Make Memora dependable as a multi-tenant memory service: preserve tenant and namespace confidentiality, keep writes and multi-store deletion recoverable, ensure event feeds expose only reviewed data, make memory lifecycle/retrieval behavior deterministic, and keep the package/deployment build reproducible. The target is not a line-count quota. The target is evidence-backed behavior with useful tests, safe defaults, measurable performance, and clear operational ownership.

## Current baseline and immediate risks

- The current `main` head is `410e1bf1a8c448a517c404637b1fcb4925ab7244`. The latest main-branch CI run was red: the event-feed test expects unknown event types to have an empty payload, while the reader's blacklist-based fallback returned unreviewed fields such as `action` and `status`.
- The full CI workflow is capable of gating Ruff, Alembic upgrade/downgrade/upgrade, pytest, PostgreSQL 18 storage integration, API boot/health/readiness and Docker image build. Keep all of those as merge gates.
- PR #7 (`automation/friday-universe-task-1`) is open, eight commits behind the current base and reported as conflicted. Its earlier run failed two logging assertions, but the branch has moved since that run. Re-run and port only useful, non-duplicated changes onto a clean branch; do not merge it blindly.
- PR #14 covers idempotency isolation but its failing run also hit the same main-branch event-feed assertion. The shared test file already exists on main; inspect the exact diff before assuming the PR still adds unique coverage.
- The repository's own hardening notes state that a database credential remains in historical Git data and that external credential rotation, live hosted services, and deployment behavior have not been verified. Treat the old credential as exposed; it must be rotated/revoked by its owner. History rewriting, if desired, is a coordinated maintainer operation, not an incidental code patch.
- The conservative contradiction detector is merged and tested. It is a review signal only; it must not auto-select a winner or supersede a memory.

## Delivery phases

### Phase 0 — Restore a trustworthy green baseline (P0)

**Scope**
1. Fix the unknown-event payload regression with default-deny behavior.
2. Add a focused regression proving arbitrary fields on an unregistered event type never reach API consumers.
3. Re-run all checks on the feature PR and again on the resulting `main` commit.
4. Reconcile duplicate/stale open PRs and record why each is merged, reworked, or closed.

**Acceptance**
- All GitHub Actions stages are green on `main`: Ruff, all pytest tests, migration round trip, PostgreSQL integration, boot/readiness, Docker build.
- No unknown event type can leak payload fields by adding a new field name that is absent from a denylist.

### Phase 1 — Security and tenant isolation (P0/P1)

**Scope**
1. Verify every write/read/deduplication/idempotency/graph/event operation uses the authenticated principal and resolved tenant.
2. Add negative tests for cross-tenant record IDs, idempotency keys, namespace grants, graph relationships, and replay/cursor access.
3. Treat payload schemas and event types as explicit allowlists. Reject unsupported privileged/system-managed event types at ingress.
4. Rotate exposed historic credentials, audit actual secret injection and remove tracked secret history only through a coordinated repository-owner process.
5. Audit request error bodies, logs, provenance, context-generation events, and mesh envelopes for prompt/query/credential leakage.

**Acceptance**
- Cross-tenant negative tests prove no foreign IDs, contents, cursors, graph links, or secrets are observable.
- Production credentials are rotated and tested without printing values; deployment secret injection is evidenced separately from repository tests.

### Phase 2 — Transaction and multi-store correctness (P1)

**Scope**
1. Reconcile tombstone convergence across relational rows, relationships, vector store, cache, and optional Turso replica.
2. Preserve savepoint rollback boundaries and independently retry failed store operations.
3. Test worker startup/shutdown idempotency, interval/input validation, thread/session cleanup, duplicate worker prevention, retry exhaustion, and observability.
4. Ensure delayed write-through cannot resurrect a hard-deleted memory; verify the durable deletion fence and old-replica cleanup strategy.
5. Test vector-upsert compensation after relational/event/audit transaction failure, while explicitly documenting the non-atomic distributed boundary.

**Acceptance**
- Failure injection at each store yields truthful tombstone bits/status and unrelated records remain intact.
- Retry tests pass on supported local databases; PostgreSQL integration is included where semantics differ.
- Hosted Qdrant/Turso behavior is not marked verified until an actual authorized integration check is run.

### Phase 3 — Memory quality and lifecycle correctness (P1/P2)

**Scope**
1. Keep contradiction signals distinct from duplicate/overlap detection and keep resolution behind explicit evidence/policy checks.
2. Verify time decay is idempotent across repeated runs and tenant-scoped; test pinned, verified, expired and borderline records.
3. Bound duplicate/retrieval candidate sets and use database-side filtering; benchmark with synthetic 5k/50k/100k record fixtures.
4. Verify graph mutations authorize both endpoints, reject unknown/self links with meaningful status codes, and stay within the caller's tenant.
5. Test identity/grant expiry semantics, namespace canonicalization, direct reads, supersession, retention, and hard-delete lifecycle interactions.

**Acceptance**
- Repeating an unchanged lifecycle job does not repeatedly apply the same decay penalty.
- Search latency/memory usage has reproducible baseline numbers and a chosen candidate cap, with result quality regressions covered.
- No automatic contradiction winner is selected solely by recency, lexical overlap, or detector score.

### Phase 4 — Async jobs, architecture and package health (P2)

**Scope**
1. Inventory every background thread/task and assign explicit lifecycle, cancellation, retry, and health semantics.
2. Design a restart-safe maintenance scheduler for decay/consolidation only after idempotency and distributed-lock behavior are proven. Avoid adding an in-process cron that multiplies work across API replicas.
3. Evaluate `memora_upgrade/` as a potential second source of truth. Either wire it through a documented compatibility boundary with parity tests or quarantine/remove it in a separate migration; do not maintain two overlapping engines invisibly.
4. Verify project runtime dependencies against all import roots, build/install a wheel, run migrations from the installed wheel, and check the runtime image contains no tests, diaries, internal reports, or development-only dependencies.
5. Add bounded async queues/backpressure only where existing synchronous hot paths or I/O blocks are measured bottlenecks.

**Acceptance**
- Background work shuts down cleanly, has bounded resource use, cannot silently run duplicate destructive jobs, and is restart-safe.
- Wheel installation/import/migrations and Docker build are CI checks. Optional-dependency degradation is explicit and observable.

### Phase 5 — Observability, load and release operations (P2/P3)

**Scope**
1. Make every exported metric's numerator/denominator and call sites truthful; no placeholder rates presented as measured outcomes.
2. Add stress tests for concurrent idempotency retries, tenant-isolated writes, search, event polling/acks, breaker state transitions and tombstone retries.
3. Run bounded load/soak profiles at 9/25/50 concurrent agents; store results with reproducible dataset and command details.
4. Define SLOs for API p95/p99 latency, write success, retrieval availability, tombstone age/retry exhaustion and event delivery lag.
5. Add release checklist/runbook for migration backup/rollback, database credential rotation, restore test, health/readiness, and external-service verification.

**Acceptance**
- Stress results include failures, throughput and latency distributions rather than only a success statement.
- Operational readiness lists which service integrations were truly exercised and which remain assumed/mocked.

## Execution ledger

| Priority | Work item | Status | Evidence / next action |
|---|---|---|---|
| P0 | Fix event payload default-deny and restore main CI | In progress | Branch `fix/events-default-deny-master-plan`; regression test and full CI required |
| P0 | Rotate the credential exposed in historical Git data | Blocked on service/credential owner | Rotate/revoke first, verify new secret injection, then coordinate history remediation |
| P1 | Reconcile tombstone-convergence PR #7 | Planned | Branch is conflicted and behind; re-implement useful changes against current `main`, then run fault-injection suite |
| P1 | Re-evaluate idempotency test PR #14 | Planned | Inspect per-file diff; avoid duplicating tests already present on `main` |
| P1 | Audit graph endpoint tenant/policy boundaries | Planned | Build adversarial API regression matrix |
| P1 | Verify hosted Turso/Qdrant/PostgreSQL behavior | Not yet verified | Requires authorized target environment and non-production fixtures |
| P2 | Benchmark search and duplicate candidate scaling | Planned | Capture baseline then introduce bounded/query-side candidate selection where needed |
| P2 | Resolve `memora_upgrade/` architecture | Planned | Dependency/call-graph and behavior-parity review before promotion/quarantine |
| P2 | Restart-safe maintenance scheduler | Planned | Design only after lifecycle idempotency and multi-replica ownership are proven |
| P3 | Load/soak and SLO runbook | Planned | Reproducible synthetic profiles plus deployment-owner evidence |

## Rules of execution

- Never suppress or weaken a regression test simply to make CI green; update contracts only when code behavior is demonstrably intended and safe.
- Never claim 100% production readiness from unit tests or mocked services alone.
- Do not force-push over another in-flight branch. Recreate clean branches from current `main` and transfer only reviewed, non-duplicated changes.
- Ship small PRs with explicit scope, regression coverage, CI evidence, and rollback notes. Merge one green PR at a time so the main-branch health signal remains meaningful.
