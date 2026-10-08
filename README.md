# 🧠 Memora — Multi-Tier Cognitive Memory Engine

[![CI / Diary Verification](https://github.com/surendra2304/Memora/actions/workflows/verify.yml/badge.svg)](https://github.com/surendra2304/Memora)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**Memora** is a long-term cognitive memory fabric designed for AI agent ecosystems (such as the FRIDAY Universe). It provides multi-tier persistent memory, hybrid retrieval (lexical + dense vector embeddings with Reciprocal Rank Fusion), episodic event graphing, and on-demand knowledge consolidation without heavy vendor dependencies.

---

## 🌟 Key Architecture & Capabilities

1. **Multi-Tier Memory Fabric**:
   - **Episodic Memory**: Temporal event sequences, conversation logs, and causal action chains.
   - **Semantic Memory**: Persistent entities, relational facts, and domain knowledge graphs.
   - **Procedural Memory**: Reusable tool execution patterns, skills, and validated workflows.
   - **Working Memory**: Low-latency conversational context buffer for active task reasoning.

2. **Reciprocal Rank Fusion (RRF) Hybrid Search**:
   - Lexical keyword matching over the candidate set, with weights read from `config/retrieval_config.json`.
   - Pluggable dense vector embedding matching for semantic similarity.
   - Calibrated RRF scoring merging lexical, semantic, and graph-expansion result sets.

3. **On-Demand Consolidation & Decay**:
   - Importance decay, archival, and supersession run through lifecycle services. The `/v1/memories/decay` maintenance endpoint is restricted to the `memora` service identity and scoped to its tenant. Decay is idempotent: it is derived from a stored importance baseline plus the record's absolute age, so repeated runs converge rather than compounding.
   - Experience extraction is available via `/v1/memories/learn-experience`; promotion into semantic memory is a separate `/v1/memories/{memory_id}/promote` workflow that requires explicit evidence.
   - **There is no scheduler.** Nothing runs these on a timer. The repository has no cron, APScheduler, Celery, or background-task wiring, so decay only happens when something calls it. Schedule it externally (system cron, Render Cron Jobs, a Kubernetes CronJob) if you want it nightly.

4. **Security Gates**:
   - Requests fail closed and authenticate to an agent via `X-Agent-Name` + `X-API-Key` (or `Authorization: Bearer`). Anonymous access exists only as an explicit local-development opt-in (`MEMORA_ALLOW_ANONYMOUS_DEV=true`) and must remain disabled in deployed environments.
   - Authorization is evaluated per operation by a five-dimension policy engine (`core/policy/engine.py`) over namespace RBAC grants, access purpose, trust level, and lifecycle state. Public/global spaces allow reads and appends; mutation of an existing record is restricted to its owner or the `memora` service identity.
   - Reflection previews and non-admin insight listings are filtered to namespaces the requesting agent can read. Direct semantic/system writes are restricted to the `memora` service identity; ordinary agent trust labels are downgraded to candidate. Policy-authorized promotion requires evidence references and confidence bounds, but those references are metadata—the service does not fetch or cryptographically verify external evidence.
   - **Tenant limitation:** current API credentials identify a service name but carry no tenant claim. HTTP identity, memory, and admin operations are therefore bound to the `default` tenant and fail closed on same-named identities in other tenants; this is not a tenant-aware credential system.
   - **There are no named capability tiers.** An earlier revision of this README described a `memory_control` > `knowledge_indexing` > `retrieval_access` hierarchy; no such constants exist in the codebase and access is not modelled that way.
   - Writes and grants commit transactionally, and audit entries are written in the same session as the operation they describe.

---

## 📖 Engineering Diary & Progress Tracking

Memora follows a strict, day-wise engineering diary protocol to track every architectural decision, implementation milestone, bug fix, and test metric:

- **Executive Summary & Master Index**: [MEMORA_DIARY.md](MEMORA_DIARY.md)
- **Daily Logs**: Located in the [`diary/`](diary/) directory (e.g., [Day 1: 2026-08-29](diary/2026-08-29.md))

---

## 🐳 Local startup

For SQLite-backed development, copy `.env.example` to `.env`, create a virtual environment, install `requirements.txt`, and run `uvicorn apps.api.main:app --host 0.0.0.0 --port 8000`. Anonymous development requests are disabled by default; configure an agent key or explicitly opt in with `MEMORA_ALLOW_ANONYMOUS_DEV=true` only on an isolated local instance.

For the Compose stack, first copy `.env.example` to `.env`, set a URL-safe random `POSTGRES_PASSWORD` (for example, generate one with `openssl rand -hex 32`), and set `MEMORA_API_KEY`. Add only the mesh agent keys you actually deploy, then run `docker compose up --build`. Compose binds published ports to loopback, disables SQLite fallback, waits for PostgreSQL/Redis readiness, and stops if migrations fail. Do not commit the populated `.env` file.

**Credential hygiene:** an earlier committed Compose revision contained a literal PostgreSQL development credential. It has been removed from the current Compose configuration; treat it as exposed and rotate it if it was reused anywhere outside isolated local development. Its value is intentionally not reproduced here.

## 🚀 Quick Verification

Run the full test suite, lint, migrations, and the diary invariants:

```bash
pip install -r requirements.txt && pip install "ruff>=0.3.0"

pytest -q                 # full suite
ruff check .              # lint (config lives in pyproject.toml)
alembic upgrade head      # apply migrations
python scripts/verify_diary.py
```

CI runs all of the above plus a boot check that starts the ASGI app and asserts
on a live `/health` response (`.github/workflows/verify.yml`).

### Known gaps worth knowing about

Stated plainly so nobody builds on a false assumption:

- **Keyword search is not FTS5.** The lexical leg of `SearchService.hybrid_search`
  loads the candidate rows and does an in-Python substring scan
  (`core/memory/search_service.py`). It is correct but O(rows) per query. A real
  FTS5 or `pg_trgm` index is the obvious next step at scale.
- **No background scheduler.** See section 3 above — decay is callable, not automatic.
- **Dead subsystems.** `memora_upgrade/`, `adapters/`, and `sdk/` are not reachable
  from the API; only their own tests and a couple of scripts import them. Treat
  them as a library surface, not as wired-in features.
- **`memora_upgrade/embeddings.py` hashes, it does not embed.** It is a SHA-512
  stand-in self-labelled "replace in production" and must not be used for real
  semantic retrieval.
