# Memora — Phase 0/1/2 Audit

Scope: `812c973` on branch `arena/01a10cc6-memora`. Every claim below is backed by a
command run against this checkout. No source files were modified to produce it.

---

## PHASE 0 — COMPREHENSION

### (a) What this project IS

Memora is the **persistent memory fabric for a 9-agent AI mesh** ("FRIDAY Universe":
friday, forge, sentinel, inference, cortex, intelx, futuris, stratex, memora). It is a
FastAPI service that gives those agents a shared, policy-partitioned long-term memory:
write a memory, retrieve it later by hybrid lexical+vector+graph search, and get back a
token-budgeted context bundle scoped to what the caller is allowed to see.

**Dream state (inferred — the placeholder in your prompt was left as a template):** a
memory substrate where an autonomous agent mesh *actually remembers* across restarts —
private by default, shareable by explicit grant, self-correcting (contradictions resolved
by evidence, not recency), self-improving (failures distilled into reusable experience),
and honest about its own durability. `SYSTEM_MANIFEST.md` and the `diary/` logs state this
directly; the code's own comments keep returning to one idea: *never let a non-durable
store or an unverified source masquerade as authority*.

### (b) Request/execution flow (real code paths)

**Write** — `POST /v1/memories`
`apps/api/routers/v1_memories.py::write_memory_event`
→ `apps/api/dependencies.py::get_actor_header` → `authenticate_agent`
→ `core/memory/pipeline/write_service.py::MemoryWriteService.execute_pipeline` — a 10-step
pipeline: receive → `core/identity/service.py::IdentityService.resolve_namespace` →
`SecretScanner.validate_content_safety` + `PoisonDetector.validate_content_safety` +
untrusted-SEMANTIC guard → normalize + sha256 →
`EntityExtractor.extract_entities_and_relationships` →
`DeduplicationEngine.check_duplicates_and_contradictions` → confidence/importance/retention →
`core/policy/engine.py::PolicyEngine.evaluate_access` → persist + `EmbeddingGenerator` +
`vector_adapter.upsert_embedding` + `GraphService.auto_link_entity_memories` →
`event_emitter.publish` + `PolicyEngine.log_audit_decision` + one `db.commit()`.

**Read** — `GET /v1/memories/search`
`v1_memories.py::search_memories_get`
→ `core/memory/search_service.py::SearchService.hybrid_search`: vector leg
(`vector_adapter.search_similarity`), lexical leg (Python substring scan),
graph leg (`GraphService.get_graph_neighbors`), fused by Reciprocal Rank Fusion, then a
per-record `PolicyEngine.evaluate_access` gate.

**Context** — `POST /v1/context`
`apps/api/routers/v1_context.py::build_context_bundle_endpoint`
→ `core/memory/context/builder.py::ContextBuilderService.build_context_bundle`
→ `SearchService.hybrid_search` (with a keyword-only degraded retry) → predictive
experience pre-fetch → `ContextReranker.rerank` (coarse 4D filter + neural cross-encoder)
→ fail-closed policy filter → `ContextBudgeter.fit_to_budget` (LLM or deterministic
hierarchical summarisation) → graph edge extraction.

**Durability spine** — `storage/relational/session.py::create_db_engine` picks
PostgreSQL → Turso/libSQL → SQLite, and sets `_active_storage_durable`.
`storage_ready()` gates `get_db()`; in production a non-durable store returns 503 instead
of silently serving. Events go to `EventLog` inside the caller's transaction and are only
released to Redis/in-memory by `core/events/emitter.py::_after_commit`.

### (c) The 5 most important files

| File | Why |
|---|---|
| `core/policy/engine.py` | The entire security model. `PolicyDecision.__bool__` is defined, so `if not decision:` throughout `MemoryService` is correct, not a bug. Tenant isolation, bounded sub-agent scope, private-by-default, and grant expiry all live here. |
| `core/memory/pipeline/write_service.py` | The only correct write path. Every other writer should route through it; the ones that don't (`v1_task.py`, `MemoryService.create_memory`) are where bugs cluster. |
| `storage/relational/session.py` | Decides whether Memora is *truthful*. `storage_receipt()` / `storage_ready()` are the reason this codebase can claim "durable" without lying. |
| `apps/api/dependencies.py` | Single authentication chokepoint. Fails closed. Three routers bypass it entirely — see CRITICAL-1. |
| `core/memory/search_service.py` | The RRF hybrid retrieval that the README leads with. Also the N+1 policy gate. |

### (d) What surprised me

1. **`memora_upgrade/` is a complete second Memora.** 1,266 lines, 16 modules — its own
   `MemoraEngine`, store, policy, retriever, graph, forgetting engine, consolidator, cache,
   metrics. It is imported by *nothing* except its own tests (verified below). It is not a
   stub; it is a parallel implementation, unwired.
2. **CI never runs the tests.** `.github/workflows/verify.yml` runs only
   `scripts/verify_diary.py`, which checks that diary markdown files are ≤ some line count.
   171 real tests never execute in CI.
3. **The test suite is green and still misses everything below**, because
   `tests/conftest.py:9` sets `MEMORA_ALLOW_ANONYMOUS_DEV=1`, which switches off the exact
   authentication code that two of the critical bugs break.
4. **A 9.3 MB binary SQLite database is committed**, containing 4,960 memory records from
   the author's real Windows desktop.
5. **The reranker is tuned to a specific benchmark.** `core/memory/context/reranker.py`
   hardcodes "solution bridges" for argon2/pg_trgm/vite and penalises the literal words
   `"counter"`, `"incremented"`, `"arrive"`, `"generic"`, `"notes"`. That is a test set
   embedded in production scoring logic.

---

## PHASE 1 — TRUTH AUDIT

### 1. Fresh install / lint / typecheck / tests

```
$ python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt   → EXIT=0
$ ./.venv/bin/python -m pytest -q
171 passed, 4 warnings in 8.76s
```

**Test suite: 171/171 pass.** No failures to capture verbatim.

```
$ ./.venv/bin/ruff check . --exclude .venv
Found 1322 errors.      (default ruleset; no [tool.ruff] config exists, so line-length=88 applies repo-wide)
$ ./.venv/bin/ruff check . --exclude .venv --select F,E9 --statistics
186  F401  unused-import
 28  F541  f-string-missing-placeholders
 24  F841  unused-variable
Found 238 errors.
```

No `F821` (undefined name) anywhere. **No type checker is configured** —
`grep -nE "mypy|pyright" pyproject.toml requirements.txt .github/workflows/verify.yml`
returns nothing. There is no typecheck to run; treating that as a Phase 4 gap, not a
Phase 1 failure.

**Dependency drift:** `pyproject.toml` `[project.dependencies]` omits `qdrant-client`,
`requests`, `pyyaml`, `python-dotenv` — all four are imported by the code and present in
`requirements.txt`. `pip install -e .` would produce a broken install.

### 2. Boot + exercised flows

```
$ uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
INFO: Application startup complete.  Uvicorn running on http://0.0.0.0:8000
```

| Flow | Result |
|---|---|
| `GET /health` | 200 `healthy`, honest: `database_backend: sqlite`, `durable: false`, `durability: process_local` |
| `POST /v1/memories` | 201, id returned |
| `GET /v1/memories/search?q=coolant+valve` | 200, 1 hit, correct record |
| `POST /v1/context` | 200, `memories_count: 1`, `tokens: 13`, `strategy: none` |
| `POST /v1/task/execute` `action=store` | **HTTP 200 with `status: "ERROR"`, `error: "MemoryWriteService() takes no arguments"`** |
| `POST /v1/task/execute` `action=query` | 200, works but unscoped (see HIGH-2) |
| `GET /openapi.json` | 35 paths |

### 3. README claims vs. code

| README claim | Reality | Evidence |
|---|---|---|
| "SQLite 3 FTS5 lexical keyword matching" | **Not implemented.** `grep -rn "fts5\|MATCH " --include=*.py` finds one hit: the *string* `"fts5"` in a tech-keyword list at `entity_extractor.py:64`. Real lexical search is `core/memory/search_service.py:132-139` — a Python loop doing `token in text_lower` over every row returned by `kw_query.all()`. | grep + code read |
| "Nightly Consolidation & Decay: autonomous background job" | **No scheduler exists.** `grep -rn "scheduler\|cron\|nightly\|APScheduler"` finds one hit, the literal string `"nightly_maintenance"` used as a task *name* in a test script. Decay is a manual `POST /v1/memories/decay`. The only background thread is `EventEmitter.start_cloud_sync` (Turso event flush). | grep |
| "Strict capability gating: `memory_control` > `knowledge_indexing` > `retrieval_access`" | **These identifiers appear nowhere in the codebase.** `grep -rn "memory_control\|knowledge_indexing\|retrieval_access" --include=*.py` → empty. The real model is namespace-type + access-grant based, which is *different and arguably better*, but the README describes a system that does not exist. | grep |
| "Complete ACID compliance with zero uncommitted state leakages" | Broadly true for the write pipeline (single commit, event tied to transaction via `_after_commit`). Not true for `GraphService`, which commits mid-pipeline. | code read |

**Working but undocumented:** the durable event feed (`/v1/events`, `/v1/events/cursor`,
`/v1/events/ack`) with per-agent monotonic cursors and Turso cloud replay; `/mesh/envelope`
with HMAC signature verification and strict Futuris/Stratex payload validation; the
multi-store deletion tombstone (`DeletionTombstone.is_converged`); the whole
`storage_receipt()` durability-honesty layer; `/v1/memories/learn-outcome` and
`/learn-experience`. None of these are in the README.

### 4. Tests

171 tests exist and pass. The safety net is real — it just has a hole exactly where the
auth boundary is, because `tests/conftest.py:9` opts the whole suite into anonymous mode.

---

## SECRETS SCAN

No credentials are committed. Verified two ways:

```
$ # ran the project's own SecretScanner over every git-tracked file
scripts/run_e2e_system_test.py: ['OpenAI API Key']
tests/test_agent_adapters.py:   ['OpenAI API Key']
tests/test_write_pipeline.py:   ['GitHub PAT', 'Google API Key', 'Hardcoded Password', 'OpenAI API Key']
```

All are synthetic fixtures (`sk-1234567890abcdef…`, `AIzaSyD1234…`, `ghp_1234…`).
`.env.example` has empty values. `render.yaml` uses `sync: false` for every secret.

**However — one real PII leak (HIGH-4 below):** the committed `data/memora.db` contains a
real personal mobile number:

```
User: send hi to 9014603029 | Assistant: Sending message with message 'hi'
to +919014603029 on WhatsApp.
```

---

## PHASE 2 — PRIORITIZED BUG REPORT

### CRITICAL

**CRITICAL-1 — Unauthenticated privilege escalation via `POST /namespaces/grants`**
`apps/api/routers/namespaces.py:11`, `audit.py:11`, `agents.py:11`

Root cause: three routers declare `APIRouter(prefix=...)` with no
`dependencies=[Depends(authenticate_agent)]` and no per-route `Depends(get_actor_header)`.
Every other router in the app has one. `authenticate_agent` itself fails closed correctly
— these routes simply never call it.

Reproduction (ran against the real app, `MEMORA_ALLOW_ANONYMOUS_DEV` unset, `INTELX_API_KEY`
and `FRIDAY_API_KEY` provisioned):

```
friday stores private memory -> 201 d9c8aa78-eae8-408a-814c-953f21926b61
intelx reads friday private BEFORE grant -> 403
UNAUTHENTICATED POST /namespaces/grants (intelx -> friday/private, actions=['*']) -> 201
intelx reads friday private AFTER grant  -> 200
   >>> PRIVATE CONTENT LEAKED: friday private launch codes alpha-omega-77
intelx enumerates friday/private -> 200 | rows: 1
```

Wider blast radius, all confirmed with no credentials at all:

```
GET     /audit                    -> 200   (entire policy-decision trail, incl. memory ids)
GET     /agents                   -> 200
POST    /agents                   -> 201   (register arbitrary identity)
POST    /namespaces               -> 201
POST    /namespaces/grants        -> 201   (self-grant '*' on any namespace)
GET     /metrics, /v1/metrics     -> 200
```

Proposed fix: add `dependencies=[Depends(authenticate_agent)]` to all three routers, and
require that grant creation/revocation be performed by the *namespace owner* (or `memora`),
checked against `PolicyEngine`, not just "is authenticated". Add a regression test that
asserts 401 with `MEMORA_ALLOW_ANONYMOUS_DEV` unset.

---

**CRITICAL-2 — `/v1/task/execute` store path calls an API that does not exist**
`apps/api/routers/v1_task.py:73-74`

```python
write_service = MemoryWriteService(db)          # class takes no arguments
write_res = write_service.process_write(...)    # method does not exist
```

`MemoryWriteService` (`core/memory/pipeline/write_service.py:72`) has no `__init__` and only
one classmethod, `execute_pipeline`. Verified — the only 4 call sites in `apps/` and `core/`
all use `MemoryWriteService.execute_pipeline(db=db, ...)`; `v1_task.py` is the sole outlier.

```
action=store     HTTP 200  status='ERROR'  error='MemoryWriteService() takes no arguments'
action=remember  HTTP 200  status='ERROR'  error='MemoryWriteService() takes no arguments'
action=query     HTTP 200  status='SUCCESS'
```

Two bugs in one: the write never happens, **and** the endpoint returns HTTP 200 for it.
Any agent in the mesh using the Universal Task Protocol to store memory has been silently
losing every write. No test covers `action in (store, remember, add, record)`.

Proposed fix: call `MemoryWriteService.execute_pipeline(db=db, caller_name=caller, ...)`
and map `write_res.record.id` / `write_res.is_duplicate`; return a non-2xx status (or at
minimum stop reporting `status: SUCCESS`-shaped 200s) when the pipeline raises.

---

**CRITICAL-3 — Idempotency keys collide across agents, causing silent data loss + cross-agent disclosure**
`storage/relational/models.py:158`, `write_service.py:210`, `core/memory/service.py:82`

Root cause: `idempotency_key` has a **global** `unique=True` constraint, but both lookup
sites scope the check to `(tenant_id, idempotency_key)` only — never by agent or owner.
Every mesh agent shares `tenant_id = "default"`, so one agent's key shadows every other's.

```
friday  -> 88b3edd8-… | owner_agent: friday | "friday: reactor coolant valve serial 8891"
intelx  -> 88b3edd8-… | owner_agent: friday | "friday: reactor coolant valve serial 8891"

same record returned to intelx?  True
intelx's content actually stored? False
is_duplicate flag reported to intelx: True
rows matching 'lithium' anywhere: 0
```

intelx's memory was **never written** and intelx was handed back **friday's private
content**. Any two agents that independently choose the same idempotency key
(e.g. `"job-42"`, a task UUID from a shared queue) silently overwrite each other.

Proposed fix: scope the key to the writing identity — uniqueness and lookup on
`(tenant_id, agent_id, idempotency_key)` — and return the existing record only when the
identity matches, otherwise 409. Requires a migration for the index.

---

### HIGH

**HIGH-1 — The entire agent adapter SDK sends the wrong auth header names**
`adapters/base_adapter.py:61,63`

The adapter sends `X-Agent-Key` and `X-Purpose`. The server reads `X-API-Key`
(`dependencies.py:14,29`) and `X-Access-Purpose` (`dependencies.py:21`).

```
adapter builds these headers: {'X-Agent-Name': 'friday', …, 'X-Agent-Key': 'k-friday', 'X-Purpose': 'incident triage'}
server expects              : X-Agent-Name / X-API-Key / Authorization / X-Access-Purpose
write_memory() RAISED MemoraAdapterError: Memora API Error (401): Missing agent credentials
```

This breaks **all 8 adapters** (friday, forge, sentinel, ai_universe, + `ecosystem.py`)
against any correctly-configured deployment. It has gone unnoticed because
`tests/conftest.py:9` enables anonymous mode, in which a missing credential is waved
through — the tests exercise the bypass, not the auth.

Proposed fix: send `X-API-Key` (and/or `Authorization: Bearer`) and `X-Access-Purpose`;
add a test that runs the adapter against a fail-closed app with a real key.

---

**HIGH-2 — `/v1/task/execute` query path has no tenant, namespace, or policy filtering**
`apps/api/routers/v1_task.py:109-115`

```python
q = db.query(MemoryRecord).filter(MemoryRecord.lifecycle_state == LifecycleState.ACTIVE)
if query:
    q = q.filter(MemoryRecord.content_text.ilike(f"%{query[:50]}%"))
records = q.order_by(MemoryRecord.created_at.desc()).limit(5).all()
```

Any authenticated agent gets the 5 most recent matching ACTIVE memories **from every
tenant and every private namespace**. This is exactly the leak that `routers/memories.py`
was hardened against (see its comment at lines 34-36) — the fix was applied there and not
here.

Proposed fix: route through `SearchService.hybrid_search(actor_name=actor, ...)` or
`MemoryService.query_memories`, both of which apply the policy gate.

---

**HIGH-3 — Graph endpoints have no authorization and leak memory IDs across namespaces**
`core/memory/graph_service.py:23,116`, `apps/api/routers/v1_memories.py:455,481`

Four confirmed defects:

```
[A] POST /v1/memories/{friday_id}/relationships (as intelx) -> 200
    (no check that the caller may read either memory)
[B] GET  /v1/memories/{intelx_id}/graph -> 200
    connected ids: ['3074830c-…']   <<< friday's private memory id
    >>> friday id visible to intelx: True
[C] self-link -> 400 {'detail': "'NoneType' object has no attribute 'id'"}
    (create_relationship returns None at graph_service.py:31; endpoint dereferences it)
[D] edge to non-existent id "does-not-exist-000" -> 200  (dangling edges accepted)
```

`get_connected_memories` returns IDs with no policy evaluation at all.
`auto_link_entity_memories` (`graph_service.py:74-77`) also queries candidates with no
tenant filter and mixes enum members with raw strings in the same `in_()`:
`[LifecycleState.ACTIVE, LifecycleState.VERIFIED, "active", "verified"]`.

Proposed fix: policy-check both endpoints of every edge; 404 unknown IDs; return a
distinct 422 for self-links instead of `None`.

---

**HIGH-4 — 9.3 MB binary database with 4,960 real memory records is committed**
`data/memora.db`

```
$ git ls-files data/          → data/memora.db
$ ls -l data/memora.db        → 9756672 bytes
tables: agents=10, namespaces=29, memory_records=4960, audit_logs=470, event_log=3620
```

`.gitignore:9` has `*.db`, but ignore rules don't apply to already-tracked files, so it is
committed and **mutates on any local run** — my probes grew it 8,630,272 → 9,756,672 bytes
before I restored it with `git checkout`.

Contents are the author's real desktop telemetry (hardware profile, voice commands) and
include a real personal mobile number:

```
User: send hi to 9014603029 | Assistant: … to +919014603029 on WhatsApp.
```

Scanned all 4,960 records with the project's own `SecretScanner`: **no credentials**.
40 hits of `john@example.com` (synthetic), 1 real phone number.

Proposed fix: `git rm --cached data/memora.db`, keep the `*.db` ignore, and treat the
existing history as exposed — that number should be considered public. Flagging per your
directive 3; **please rotate/redact anything tied to it.**

---

**HIGH-5 — `record-interaction` swallows every failure and reports success**
`apps/api/routers/v1_memories.py:576`, `:605`

```python
except Exception as e:
    pass  # Duplicate or policy error, continue
```

```
[1] content containing a live-format secret:
    -> 201 {"status":"success", "recorded_count":0, "memory_ids":[], "extracted_facts":[]}
[2] content containing a prompt injection:
    -> 201 {"status":"success", "recorded_count":0, "memory_ids":[], "extracted_facts":[]}
```

A rejected secret and a rejected injection are indistinguishable from a successful
no-op. The security scanners *did* fire (`PoisonMemoryViolation` appears in the log) —
the endpoint just throws the result away.

Proposed fix: collect per-item outcomes and surface them; return 422 when a security
scanner rejected content, and stop reporting `"status": "success"` at `recorded_count: 0`.

---

### MEDIUM

**MEDIUM-1 — Decay is not idempotent; repeated cycles destroy memory far too fast**
`core/lifecycle/decay.py:73-74`

```python
decay_factor = decay_rate_per_day * (age_days - unverified_threshold_days + 1)
new_importance = max(0.01, round(r.importance - decay_factor, 4))
```

`decay_factor` is recomputed from *absolute age* every cycle but subtracted from the
*already-decayed* importance, so the full cumulative penalty is re-applied on each run.
Measured on a 15-day-old record (rate 0.02, threshold 14d, archive ≤ 0.15, start 0.50):

```
run  importance  state
1    0.46        active     <- correct per the documented model
2    0.42        active
3    0.38        active
…
8    0.18        active     <- archives on run 9, same calendar day
```

It should hold at 0.46 on repeat. An hourly scheduler would archive a healthy memory in
under a day.

Proposed fix: derive importance from a stored baseline and absolute age (idempotent), or
decay by the elapsed interval since the last cycle rather than cumulative age.

**MEDIUM-2 — `memora_upgrade/` is 1,266 lines wired to nothing.**
`grep -rn "memora_upgrade" --include=*.py` outside the package and its tests → **empty**.
Complete parallel engine (`MemoraEngine`, `MemoryStore`, `HybridRetriever`, `MemoryGraph`,
`ForgettingEngine`, `Consolidator`, `TenantScopedCache`, `Metrics`), plus its own
`DeterministicEmbedding` and `AtomicJsonFile` persistence. It is tested, so it looks alive.
Either promote it or quarantine it — right now it is a second source of truth that no
request ever reaches.

**MEDIUM-3 — CI runs no tests, no lint, no typecheck.**
`.github/workflows/verify.yml` installs no dependencies and runs one line:
`python scripts/verify_diary.py`. That script checks diary markdown line counts. Output:

```
Checking 2026-08-29.md: Total lines = 53, Summary lines = 27
[PASS] … (×7)
All diary files passed verification successfully!   exit=0
```

171 tests, ruff, and the Dockerfile have never gated a merge.

**MEDIUM-4 — The reranker is overfit to one benchmark.**
`core/memory/context/reranker.py:99-131` hardcodes `solution_bridges` for
argon2/pg_trgm/vite/tailwind and applies a 0.35 penalty when text contains
`"counter"`, `"incremented"`, `"arrive"`, `"generic"`, or `"notes"`. These are vocabulary
from a specific evaluation, not a general relevance signal — any legitimate memory using
those ordinary words is demoted. Also `stopwords` at line 93 lists `"at"` twice.

**MEDIUM-5 — Keyword search is O(rows) in Python, not FTS5.**
`core/memory/search_service.py:132-139` calls `kw_query.all()` and loops in Python. At
4,960 records (already the size of the committed DB) every search materialises the whole
candidate set. The `config/retrieval_config.json` weights (0.6/0.3/0.1) are never read
either — `hybrid_search` takes its own default weights (0.50/0.35/0.15).

**MEDIUM-6 — `pyproject.toml` dependencies are incomplete.**
`qdrant-client`, `requests`, `pyyaml`, `python-dotenv` are imported by the code and listed
in `requirements.txt` but absent from `[project.dependencies]`. `pip install .` yields a
package that cannot import.

**MEDIUM-7 — `IdentityService.grant_access` silently clears expiry on re-grant.**
`core/identity/service.py:215-218`: when a grant already exists, `grant.expires_at =
expires_at` is assigned unconditionally, so re-granting without a TTL converts a
time-boxed grant into a permanent one. Also `Agent.name` is globally `unique=True`
(`models.py:57`) while `register_agent` looks up by `(name, tenant_id)` — genuine
multi-tenancy would hit an IntegrityError on a name reused across tenants.

### LOW

- **L1** `v1_memories.py:575,598`, `builder.py:109`, `write_service.py:361,431` — bare
  `except Exception as e: pass` discards the exception object entirely (ruff F841).
- **L2** `write_service.py:363` — `step_trace["step_10_emit_and_audit"] =
  step_trace["step_10_emit_event_and_audit"]` duplicates one key under two names.
- **L3** `SecretScanner` "Hardcoded Password" requires quotes, so `password=hunter2` passes.
- **L4** `PoisonDetector` flags `| bash`, `DROP TABLE`, `rm -rf` — legitimate memories
  *about* those strings are rejected as injections.
- **L5** 186 unused imports / 28 empty f-strings (ruff F401/F541), no ruff config to enforce.
- **L6** `docker-compose.yml` hardcodes `POSTGRES_PASSWORD: memora_password` (dev-only, but
  published).
- **L7** `alembic` warns on every run: `DeprecationWarning: No path_separator found in
  configuration`.

---

## Summary

| Severity | Count |
|---|---|
| CRITICAL | 3 |
| HIGH | 5 |
| MEDIUM | 7 |
| LOW | 7 |

Baseline: **171/171 tests pass**, app boots and serves, 35 API paths, no committed
credentials, migration chain linear with a single head
(`8a8f529e1cdb → 9cf9d4184551 → 1218edca5327 → 433bb01f2a2a → 5e89a1b2c3d4 →
b1206b9e4a61 → c2407f92e1ab`).

Nothing has been modified yet. Awaiting go-ahead for Phase 3.
