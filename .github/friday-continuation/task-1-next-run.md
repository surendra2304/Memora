# Task 1 next-run instructions — Memora

Last updated: 2026-10-20T23:01:43Z

Read .github/friday-continuation/task-1-checkpoint.md before coding. Resume branch automation/friday-universe-task-1; continue existing implementation rather than restarting repository review.

## Previous run evidence

- Result: completed
- Active coding seconds: 1937
- Coding passes: 15
- Source paths changed against main: 13

### Changed source paths

- core/memory/graph_service.py
- core/memory/search_service.py
- core/memory/service.py
- ore/memory/context/builder.py
- scripts/verify_all_agents_storage.py
- storage/convergence/tombstone_worker.py
- tests/test_context_pipeline.py
- tests/test_graph_and_adapter_fixes.py
- tests/test_hybrid_search.py
- tests/test_lifecycle_manager.py
- tests/test_metrics_collector.py
- tests/test_prompt3_memora.py
- tests/test_tombstone_convergence.py

### Runner failures or blockers

- None recorded by the runner.

## Detailed continuation notes from the previous coding pass

# Task 1 next-run instructions — Memora

Read `.github/friday-continuation/task-1-checkpoint.md` and continue directly from its next concrete coding action. Work only in this repository; preserve all current uncommitted changes. Do not commit, push, or change Git configuration.

The latest fix is in `storage/convergence/tombstone_worker.py`: before any storage deletion, `process_single_tombstone` checks the tenant of an existing relational memory. A tombstone referencing a foreign-tenant memory is logged, retried up to the configured limit, and never marked converged; missing records retain idempotent cleanup. `tests/test_tombstone_convergence.py::test_tombstone_with_wrong_tenant_does_not_delete_foreign_memory_or_edges` covers the foreign record, edge, untouched store flags, and the transition through `PENDING_RETRY` to `FAILED`.

Validation completed:

- `.venv/bin/pytest -q tests/test_tombstone_convergence.py::test_tombstone_with_wrong_tenant_does_not_delete_foreign_memory_or_edges tests/test_tombstone_convergence.py::test_run_convergence_pass_batch` — **2 passed, 0 failed**.
- `.venv/bin/pytest -q tests/test_prompt3_memora.py tests/test_lifecycle_manager.py tests/test_tombstone_convergence.py tests/test_graph_and_adapter_fixes.py tests/test_context_pipeline.py tests/test_hybrid_search.py tests/test_turso_replica_deletion.py` — **58 passed, 0 failed** in 4.03s.
- `.venv/bin/ruff check core/memory/context/builder.py core/memory/graph_service.py core/memory/search_service.py core/memory/service.py scripts/verify_all_agents_storage.py storage/convergence/tombstone_worker.py tests/test_context_pipeline.py tests/test_graph_and_adapter_fixes.py tests/test_hybrid_search.py tests/test_lifecycle_manager.py tests/test_metrics_collector.py tests/test_prompt3_memora.py tests/test_tombstone_convergence.py tests/test_turso_replica_deletion.py && git diff --check` — **passed**.
- Tests emit one existing Starlette `TestClient` deprecation warning recommending `httpx2`; live PostgreSQL/vector/Turso integrations were not exercised.
- A pre-fix run intentionally failed and reproduced the bug; an intermediate command had an invalid pytest selector, and its corrected focused test run passed. See the checkpoint for exact outcomes.

First coding action: add a `tests/test_tombstone_convergence.py` global-batch regression with one malformed tenant-A tombstone targeting a tenant-B memory and one valid tenant-B tombstone. Verify the foreign target and incident edges survive, the malformed tombstone is bounded/retryable, and the valid tombstone converges in the same unfiltered pass. Then run the convergence, graph, deletion, and retrieval tests plus Ruff and `git diff --check`; update both handoff files with exact results and the following action.


## First action next run

Read the checkpoint and these previous notes, inspect the branch diff, run the focused tests for the last implementation, fix failures, and implement the next highest-impact feature or regression fix. Record exact commands/results and revise both handoff files before ending.
