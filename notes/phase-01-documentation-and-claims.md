# Phase 1 — Project intent, documentation and claims

**Status:** Complete.  
**Scope:** Read the primary README, manifest, audit/upgrade/handoff reports and master engineering diary; compare test/deployment claims to repository state and source references. No deployment URL was contacted.

## Evidence and findings

- [FACT] `README.md:7-32` describes a multi-tier, persistent agent-memory service with lexical/vector/graph retrieval, on-demand lifecycle operations, and a five-dimension policy engine. It also says there is no scheduler (`README.md:24-27`) and that lexical search is not FTS5 (`README.md:65-69`). These are claims to verify against implementation in Phases 6–10, not live-service evidence.
- [FACT] The README explicitly marks `memora_upgrade/`, `adapters/`, and `sdk/` as not wired into the API, and says the overlay embedding is a stand-in (`README.md:70-75`). A source import scan of `apps/`, `core/`, and `storage/` found no production imports of those three packages. The packages are used by tests/scripts, so they exist as a separate library/tool surface rather than an API subsystem.
- [FACT] The README names `/memories/lifecycle/decay` (`README.md:25`), but the mounted v1 router exposes `POST /v1/memories/decay` (`apps/api/routers/v1_memories.py:641-657`); no route matching `/memories/lifecycle/decay` was found. The README's `learn-experience` route does exist at `apps/api/routers/v1_memories.py:326`.
- [FACT] `SYSTEM_MANIFEST.md:10-20,38-39` labels cloud service, database, host and capacity as configured/runtime-unverified. No live endpoint was called in this review. The manifest says peer communication includes WebSockets (`SYSTEM_MANIFEST.md:29-32`), but a source search found no WebSocket route/decorator in `apps/`, `core/`, `sdk/`, or `adapters/`.
- [FACT] The manifest's stated branch/path and `MEMORA_DIARY.md:13-16`'s branch/host/deployment assertions do not describe this checkout; the actual session branch and commit are recorded in Phase 0. Treat these as historical/operator claims, not evidence of current deployment.
- [FACT] The master diary claims a live Render/Turso topology, eight or nine peer agents, and 86 passing tests (`MEMORA_DIARY.md:13-30,38-44,50-61`). `AUDIT_REPORT.md` is dated 2026-09-01 and claims a 10-phase audit (`:3-11`) while providing headings for Phases 1–8 (`:15-88`); its detailed result says 67 passed (`:66-72`) but its summary table says 68 (`:95-103`). `MEMORA_UPGRADE_AUDIT.md:3-7,21-33,111-141` reports an 86-test run on Windows in September. `MEMORA_HANDOFF_REPORT.md:1-4,23-37` reports a 367-test run on a different Arena branch/HEAD. These are dated reports and their numbers are not the current verification result for this checkout.
- [FACT] `MEMORA_HANDOFF_REPORT.md:152-159` itself distinguishes configured-but-unverified production integrations from local test evidence. Its separate historical-privacy statement (`:163-167`) alleges personal data in a database file at a commit not present in this checkout's available Git graph. [HYPOTHESIS] This is a historical exposure lead only; the referenced object/data could not be inspected here and is not asserted as a verified current-head leak. No personal value is copied.
- [FACT] The repository declares the MIT license (`LICENSE:1-21`).
- [INFERENCE] Documentation is useful for product intent and known gaps, but dated audit summaries and cloud claims require checkout-specific source/runtime verification; none are accepted as current operational evidence merely because they say “verified” or “production ready.”

## Limitations and exit check

- [FACT] No production cloud, Turso, Render, Qdrant, Redis, or external agent service was contacted; the user required local, non-transmitting analysis.
- [FACT] Historical report/test counts differ (67/68, 86, 367) and belong to different dates/checkouts. Phase 11 will record the actual verification result available for this checkout.
- [FACT] No tracked source or docs were changed. Phase 1 is complete; next is the architecture/codebase map.
