# Phases 16–17 — Iterative hardening, adversarial workflows, and pressure checks

**Work date:** 2026-10-08  
**Repository:** `surendra2304/Memora`  
**Branch:** `arena/9112d5a3-memora`  
**Scope:** Local synthetic testing and remediation after the Phase 00–15 baseline report. No real user data or deployed services were used. The user has authorized commits and a push of verified checklist work only to `arena/9112d5a3-memora`; earlier verified commits are already on that branch.

> This is a follow-on to `REPO_ANALYSIS.md`, whose counts and risk ledger describe the checkout at the time of the 2026-10-07 baseline review. That report is not a current-state assurance document after the source and tests changed; this note records the later work and its verification limits.

## Work completed in this pass

### Credential identity and tenant binding

- `resolve_agent_selector` now uses the identity authenticated by the credential, not an agent selector from the request body. A context request may select only the authenticated principal or a registered direct sub-agent with a bounded scope.
- If the authenticated principal has no row in the API-bound `default` tenant, the API bootstraps that exact principal locally. It does not adopt a same-named identity from another tenant. This preserves first-use SDK workflows without weakening credential authentication or tenant selection.
- Synthetic regression coverage places a Friday identity and private marker only in a foreign tenant, then exercises context, direct write, search, and task query/store routes. The default-tenant identity remains distinct and the foreign marker/ID is not returned.
- Grant and share paths continue to reject unregistered recipients rather than silently minting them. The appropriate test fixtures now provision their known recipients explicitly.
- The `ai_universe` adapter was registered but absent from the production authentication map, so its valid named credential could never authenticate. Added the dedicated `AI_UNIVERSE_API_KEY` mapping to the fail-closed auth path and exposed it in `.env.example`, Compose, and Render configuration. The focused authentication/adapter checks pass (19 tests); hosted credential injection remains unverified.

### Wheel packaging

- A local `pip wheel --no-deps --no-build-isolation .` probe reproduced setuptools' multiple-top-level-package discovery failure. Added explicit package inclusion for the runtime modules and their namespaces, plus package data for adapter configuration, retrieval JSON, the API HTML, and migration scripts. The build then produced a 121-entry wheel; a temporary target install imported the API, adapter registry, SDK, and retrieval loader successfully, loaded the packaged weights, and verified expected runtime files (including the new deletion migration) were present without bundling tests, notes, research, diaries, or the local database.
- This is a local wheel build/import smoke test only. It does not exercise migration CLI configuration from an installed wheel or prove compatibility with a built Docker image.

### Namespace and collaboration boundaries

- Ordinary first-write creation remains denied for `universe` and `public` roots, and arbitrary paths under the open `team` root remain blocked.
- The established `memora://team/shared` first-write flow is preserved. A first writer may initialize only that canonical team-shared path; the resulting namespace remains grant-controlled, and the writer is its initial namespace owner. Tests verify that another agent cannot read without a grant, can use granted read/query material through collaboration, and still cannot write under a read/query-only grant.
- The namespaced administration API continues to require the deployment identity to create open-root namespaces. Admin-provisioned `memora://universe/global` setup is explicit in tests that exercise ordinary writes or global-memory lifecycle policy.
- Wildcard grants are not authority, unsupported grant actions are refused, and collaboration contribution does not turn namespace-level write access into blanket permission to contribute records the agent does not own.

### Bounded inputs and read/lifecycle behavior

- Added a global 1 MiB HTTP request-body ceiling, checked against `Content-Length` and while consuming chunked bodies. Oversized requests receive HTTP 413 before route/model handling. Tests exercise both fixed-length and streamed/chunked inputs.
- Task envelopes are bounded by top-level field count, serialized bytes, nesting depth, and item count. Memory/task content, query text, experience batches and string fields, and idempotency keys have explicit limits; API and schema idempotency-key limits agree at 128 characters.
- Direct memory read policy rejects deleted and temporally inactive records, consistent with ordinary retrieval. The existing explicit `include_expired=True` contract is preserved as a deliberate opt-in for direct and hybrid search; deleted, future-valid, and otherwise inactive records remain unavailable by default.
- Hybrid search now reapplies namespace and memory-type filters to the final lexical/vector/graph candidate union. Predictive experience prefetch also carries requested namespace, user, workspace, and task scopes instead of reintroducing same-agent records from unrelated scopes.
- `context.generated` telemetry no longer persists the raw request query. The API response still returns the query to its caller, while the committed event payload and optional downstream event sync contain only non-query metadata.

### Multi-store rollback compensation

- A new failure injection showed that a late event-publish exception rolled back the relational memory and graph edge but left the already-upserted vector behind.
- The write pipeline now makes a tenant-scoped best-effort vector delete when an attempted upsert belongs to a transaction that fails before commit. It does not delete a vector after a successful relational commit. The regression compares the vector index before/after the injected late failure as well as checking database rows and graph edges.
- This is compensation, not a distributed transaction: a real vector backend can still be unavailable during cleanup. The failure is logged, and production Qdrant behavior was not exercised here.

### Turso mirror hard-delete convergence

- Source tracing confirmed that optional asynchronous Turso write-through had no matching hard-delete path. A delayed write could recreate a row after a delete; the local tombstone did not track the remote replica.
- Added `DeletionTombstone.turso_deleted` plus an Alembic migration. Hard delete commits the local tombstone first, attempts a tenant-scoped remote delete, and leaves `PENDING_RETRY` if the remote operation fails. The self-healing check retries the remote delete and only reports convergence after all tracked stores succeed.
- Turso writes now consult a durable remote deletion fence before insert and update, so an already-scheduled asynchronous write cannot resurrect a fenced memory. Conflicting IDs from a different tenant cannot update the remote row. When Turso is already the authoritative SQL backend, redundant async upserts are skipped.
- Synthetic tests execute the upsert SQL against local SQLite, fake the Turso HTTP pipeline, exercise failure/retry through the service and self-healer, and verify existing tombstones backfill as complete. No Turso endpoint or credential was contacted.

## Synthetic scenarios and pressure coverage

The added/extended tests exercise complete API and service paths, not just unchanged historical assertions:

1. Same-named foreign/default principals across context, write, search, and task routes.
2. Canonical team-shared first-write, denied pre-grant access, explicit read/query grant, usable assistance result, and denied write escalation.
3. Namespace ownership and audit metadata scoped to tenant; foreign namespace/audit records not adopted.
4. Delegation confined to the parent’s effective read/query/write permissions and bounded scope.
5. Unknown grant/share recipients, wildcard grants, expired grants, and read-only contribution boundaries.
6. Direct reads/search for deleted, expired, and not-yet-valid memories.
7. Cross-tenant graph edge rejection and rollback after a late event failure, including external-vector cleanup.
8. Search-scope enforcement against vector-only out-of-namespace/out-of-type candidates; namespace, workspace, and task scope enforcement on predictive prefetch; explicit hybrid-search expired-record opt-in.
9. Context response/event separation: the caller receives its query while the persisted `context.generated` event omits it.
10. Request boundary cases: 33 task fields, 17 nested levels, more than 4,096 JSON nodes, a 128 KiB payload, 4,097-character query, 129-character idempotency key, and HTTP bodies over 1 MiB in both fixed-length and chunked form.
11. Concurrent registration and namespace creation against file-backed SQLite, plus 16 concurrent identical API idempotency retries and concurrent circuit-breaker state transitions.
12. Turso mirror hard-delete fencing, async upsert non-resurrection, cross-tenant ID conflict protection, remote failure/retry, and migration backfill using only local SQLite and mocked HTTP.
13. Concurrent HALF_OPEN circuit callers: exactly one recovery probe is admitted; unexpected probe exceptions release the reservation; late completions cannot close a newer generation. The single-probe test failed before the fix and passes after it.
14. Mesh event tenant boundaries: local and mocked-Turso duplicate event-ID collisions across tenants or publishers return generic 409 without another owner's cursor; system-managed events cannot be forged through agent envelopes; a payload-supplied tenant claim is overwritten with the server-bound default tenant.
15. Retrieval score/age/latency instrumentation, write-pipeline dedup/idempotency hit metrics, and concurrent `MetricsCollector` recording/snapshot tests; the contradiction metric is explicitly left unresolved because there is no actual contradiction detector.
16. Positive `learn-outcome` SDK/API/SQLite round trip with a synthetic authenticated Friday identity; package-local Alembic configuration was exercised from an installed wheel outside the source checkout.
17. Remote Qdrant self-healing behavior was tested with a fake scroll/delete client and a tenant filter; local pagination and capped repair behavior were exercised against the in-memory adapter.

## Regressions found and resolutions

- The collaboration visibility test initially searched the entire response for the request marker, even though the response correctly echoes the caller-provided query. It now checks candidate/usable-memory collections and record IDs instead.
- The new open-root rule initially rejected the existing canonical team-shared path. The pipeline rule now distinguishes protected `universe`/`public` roots, arbitrary `team` paths, and the canonical `team/shared` collaboration path.
- Adapter and collaboration tests that used an unprovisioned recipient failed when automatic target-agent creation was removed. Tests now seed synthetic principals explicitly; authentication and tenant isolation were not relaxed to resolve those failures.
- A global-memory deletion test depended on implicitly creating `universe/global`. Its setup now models administrator provisioning before testing the non-owner deletion denial.
- Adding direct temporal read checks initially overrode the documented explicit `include_expired=True` query behavior. The policy now receives that explicit query opt-in; direct GET and default search remain fail-closed.
- A deliberately injected late pipeline failure exposed a vector orphan despite SQL rollback. Tenant-scoped vector compensation was added and its regression now passes.
- A scoped hybrid-search probe showed that vector/graph candidates could re-enter after lexical namespace/type filtering, and predictive experience prefetch ignored workspace/task/namespace constraints. Those scopes are now reapplied; deterministic synthetic regressions cover each prefetch scope and a vector-only out-of-scope candidate.
- The baseline event risk included raw context queries in persisted `context.generated` payloads. That event no longer stores the query; a regression confirms the response remains compatible and the database event does not contain the synthetic query marker.
- The registered AI Universe adapter's `ai_universe` identity was missing from the production credential allowlist, making that caller impossible to authenticate. Added its dedicated credential mapping and deployment/example configuration, with a fail-closed authentication regression.
- Turso write-through had no remote hard-delete or durable retry, and a late async upsert could resurrect a deleted row. Added a tenant-scoped remote deletion fence, a local outbox state column/migration, retry through self-healing, and a guard against redundant writes when Turso is already the primary store.
- HALF_OPEN lacked an atomic single-probe reservation: a second recovery caller was not rejected while the first probe was blocked. Added a generation-tagged reservation under the breaker lock, release on unexpected exceptions, and stale-completion protection. The concurrency regression reproduced the defect before the fix.
- Mesh ingestion looked up `EventLog.event_id` globally and could return a foreign row's cursor. Local and Turso append paths now reject cross-tenant or cross-publisher ID collisions with a non-disclosing conflict. Agent-supplied memory/context/access system events are rejected, and envelope payloads cannot claim a tenant other than the server-bound default.
- `SearchService.hybrid_search` never called `record_retrieval`, and the singleton metrics collector updated counters/deques without synchronization. Retrieval now records returned scores, ages and latency; collector writes/snapshots use a lock; write-pipeline idempotency/duplicate hits feed a separate deduplication metric. There is still no sound way to increment the contradiction counter: duplicate/overlap warnings are not genuine contradiction detection and are deliberately not conflated.
- The Phase 14 `learn-outcome` route-missing claim was stale. Added a real SDK invocation bridged through the local TestClient route; the authenticated synthetic principal's EXPERIENCE record persisted in isolated SQLite.
- Self-healing inspected only the process-local vector mirror, empty after a process restart. Reconciliation now calls tenant-filtered Qdrant `scroll` in bounded pages and evicts only IDs without a non-deleted relational row for that same tenant. A fake Qdrant test validates filter construction and deletion; live Qdrant remains unverified.
- The first installed-wheel smoke verified the migration file but not the CLI. Added a package-local Alembic config to the wheel and successfully ran `upgrade head`/`current` from a temporary install and working directory.

## Verification

Latest complete local run, after the 2026-10-08 continuation changes:

```text
.venv/bin/pytest -q
512 passed in 29.48s

.venv/bin/ruff check .
All checks passed!

git diff --check
passed (no output)

.venv/bin/alembic heads
f35ecb0a7c12 (head)
```

The latest focused resilience/self-healing/event/SDK/metrics/write-pipeline batch passed **114 tests in 6.28s**. The updated concurrency/idempotency/circuit-breaker/metrics pressure battery ran three consecutive times, with **31 passed each** (3.01s, 3.18s, 3.53s). The single-probe race regression was also run before the circuit-breaker fix and failed as expected; all three circuit-breaker tests pass after the fix.

Packaging/deployment checks that were possible locally:

- Built a wheel, installed it to a temporary target, and ran Alembic from outside the source checkout using the packaged `migrations/alembic.ini`; `upgrade head` applied revisions through `f35ecb0a7c12` and `current` reported that head.
- PyYAML parsed `docker-compose.yml` and `render.yaml` as top-level mappings. Docker/Compose CLI is unavailable, so semantic Compose validation and image build/start were not done.
- No hosted PostgreSQL, Turso, Redis, Qdrant, Render, or Docker service was contacted or verified. Fake Turso/Qdrant clients and local SQLite validate only the exercised adapter logic, not external service behavior.

Tests use synthetic identities and isolated local SQLite/TestClient fixtures. The suite result does not prove production storage, network, deployment, credential injection, or operational behavior.

## Partial baseline risk-ledger triage

This is a status delta, not a replacement for the complete Phase 14 ledger:

- Local code/tests address the baseline principal binding, dedup disclosure, public/global mutation, reflection/decay scoping, legacy ingestion trust, idempotency race, context-budget, provenance/trust, search/prefetch scope, direct lifecycle read, and context-event query findings (baseline R1–R6, R9, R12–R16 in part). The individual changes and tests are described above.
- The current working Compose configuration removes its literal database password and loopback-binds published service ports; however, the old value remains in committed Git history. It has not been rotated or verified unused (R7).
- Named service credentials, including the AI Universe principal key, are represented in local deployment configuration. Docker image build, production database/Qdrant readiness, actual secret injection, and hosted Render behavior remain unverified (R8, R10).
- Explicit wheel discovery and the installed-wheel import/config smoke pass; package-local Alembic `upgrade head` and `current` now also pass from a temporary wheel install (R11). Turso deletion retries and anti-resurrection fencing pass only with local SQL/mocked HTTP (R15); old remote orphan backfill and hosted behavior remain unverified.
- Event feed/cursor routes remain tied to the shared `default` tenant because the credential model has no tenant claims. Mesh envelope ID collisions are checked against both local rows and the Turso append response; system-managed event types and caller-supplied tenant claims are rejected/overwritten (R18). This is not general multi-tenant API support.
- `learn-outcome` exists and its SDK payload now has a positive local API round-trip test; hosted request/credential behavior is unverified (R19). HALF_OPEN concurrency, retrieval metrics, thread-safe collector snapshots, and Qdrant-backed repair paging have local regressions. Genuine contradiction detection and its metric remain unimplemented, and Qdrant/Turso external behavior is still unverified (R20).

## Remaining limitations and next steps

- The Phase 14 ledger now has a post-baseline state delta, but that is not a fresh production audit of all 20 risks. Historical risks R1–R14 still need to be read with the per-fix evidence in this note and the limitations recorded here.
- The first writer of `memora://team/shared` becomes its initial namespace owner. Explicit grants still gate other agents; deployments wanting deterministic ownership should pre-provision this path administratively.
- The 1 MiB body limit was exercised through ASGI/TestClient and a chunked ASGI transport, not behind the production reverse proxy or hosted server. Verify proxy/server limits and the fixed default before deployment.
- Exercise idempotency, transaction rollback, vector compensation, Turso write-through/deletion-fence convergence, Qdrant paging/deletion, and migration behavior against intended production backends before claiming those semantics there.
- The `contradiction_rate` remains a zero/default telemetry field until a real contradiction detector and trustworthy callsite exist; duplicate/overlap detection must not be counted as contradiction. The changed-file credential-pattern scan found only two synthetic test-fixture key assignments (paths/categories only; values suppressed), not a production credential. A tracked database credential remains in old Git history; rotate/revoke it and address history exposure through the credential owner/maintainer. Do not reveal the value.
- The user authorized commit and push of verified checklist work only to `arena/9112d5a3-memora`. Finish final diff/secret/artifact review, update progress with the commit and push outcome, and leave material external verification explicitly open.

## Current handoff

The current local verification pass is green: **512 tests**, Ruff, `git diff --check`, the installed-wheel Alembic upgrade/current smoke, and YAML parsing pass; the 31-test concurrency/idempotency/circuit-breaker/metrics battery passed three rounds. No commit/push has yet been made for this continuation. The broader maintenance task remains open pending final patch/credential-pattern audit, branch-only commit/push, and any later verification against external deployment services.
