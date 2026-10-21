# Task 1 checkpoint — Memora

Updated: 2026-10-20

## Mission and constraints

Continue production-quality Memora implementation and automated testing for durable memory, retrieval, access scopes, and safe migrations. Work only in this checkout. Preserve uncommitted work. Do not commit, push, or modify Git configuration.

## Latest pass — prevent cross-tenant tombstone deletion

Added `tests/test_tombstone_convergence.py::test_tombstone_with_wrong_tenant_does_not_delete_foreign_memory_or_edges`. It creates a tenant-B memory and incident graph edge, then a tenant-A tombstone that references the tenant-B memory. The pre-fix test reproduced the issue: the worker converged the mismatched tombstone and could delete the foreign edge.

In `storage/convergence/tombstone_worker.py`, `process_single_tombstone` now checks the existing memory's tenant before any vector, graph, cache, Turso, or relational deletion. A known tenant mismatch is logged, increments the bounded retry counter, and remains `PENDING_RETRY` until `FAILED`; no deletion-completion flags are changed. If the memory is genuinely missing, normal idempotent cleanup continues. The regression exercises all three bounded attempts, verifies no store flag changes, and confirms the foreign record and edge remain.

Existing convergence tests also verify missing-record tombstones still converge, graph and relational failures use separate savepoints, incoming/outgoing edges are handled, and failed SQL deletion leaves the session usable and permits retry. Existing worktree changes continue to cover tenant-bound graph/search/context access, private/shared/universal memory scopes, and lifecycle behavior.

## Validation

- Pre-fix reproduction: `.venv/bin/pytest -q tests/test_tombstone_convergence.py::test_tombstone_with_wrong_tenant_does_not_delete_foreign_memory_or_edges` — **1 failed as expected**, showing `receipt["converged"]` was 1 instead of 0 before the ownership guard.
- `.venv/bin/pytest -q tests/test_tombstone_convergence.py::test_tombstone_with_wrong_tenant_does_not_delete_foreign_memory_or_edges tests/test_tombstone_convergence.py::test_run_convergence_pass_batch` — **2 passed, 0 failed**; verifies both mismatch protection and missing-record idempotency. One existing Starlette `TestClient` deprecation warning recommends `httpx2`.
- `.venv/bin/pytest -q tests/test_prompt3_memora.py tests/test_lifecycle_manager.py tests/test_tombstone_convergence.py tests/test_graph_and_adapter_fixes.py tests/test_context_pipeline.py tests/test_hybrid_search.py tests/test_turso_replica_deletion.py` — **58 passed, 0 failed** in 4.03s; one existing Starlette deprecation warning recommending `httpx2`.
- `.venv/bin/ruff check core/memory/context/builder.py core/memory/graph_service.py core/memory/search_service.py core/memory/service.py scripts/verify_all_agents_storage.py storage/convergence/tombstone_worker.py tests/test_context_pipeline.py tests/test_graph_and_adapter_fixes.py tests/test_hybrid_search.py tests/test_lifecycle_manager.py tests/test_metrics_collector.py tests/test_prompt3_memora.py tests/test_tombstone_convergence.py tests/test_turso_replica_deletion.py && git diff --check` — **passed**.
- An intermediate test command used the invalid selector `tests/test_run_convergence_pass_batch` (missing the file and `::` separator), exiting 4 without running tests; the corrected focused command above passed.
- PostgreSQL and live vector/Turso services were not exercised; the tests use the local SQLite and mocked/local adapters. No live storage was touched.

## Worktree

Preserve existing uncommitted modifications in `core/memory/context/builder.py`, `core/memory/graph_service.py`, `core/memory/search_service.py`, `core/memory/service.py`, `scripts/verify_all_agents_storage.py`, `storage/convergence/tombstone_worker.py`, `tests/test_context_pipeline.py`, `tests/test_graph_and_adapter_fixes.py`, `tests/test_hybrid_search.py`, `tests/test_lifecycle_manager.py`, `tests/test_metrics_collector.py`, `tests/test_prompt3_memora.py`, and `tests/test_tombstone_convergence.py`. `.github/friday-continuation/task-1-checkpoint.md` and `.github/friday-continuation/task-1-next-run.md` are untracked continuation files. Nothing was committed.

## Next concrete coding action

In `tests/test_tombstone_convergence.py`, add a regression for an unfiltered `run_convergence_pass(test_db)` containing both (1) a tenant-A tombstone that incorrectly references a tenant-B memory with an incident graph edge and (2) a valid tenant-B tombstone for another memory. Assert the malformed tombstone cannot delete the tenant-B target or its edges, reaches bounded retry state, and the valid tenant-B tombstone still converges in the same global batch. Then run the convergence, graph, deletion, retrieval, Ruff, and diff checks recorded above and rewrite this checkpoint and the next-run prompt with exact results.


## Scheduled run summary (2026-10-20T23:01:43Z)

- Result: completed
- Active coding seconds: 1937
- Coding passes: 15
- Changed source paths: 13
- Failures: none


## Runner-enforced checkpoint (2026-10-21T00:48:23Z)

Pass 1 ended: failed with exit code 1. The coding engine did not revise this checkpoint; the next pass must inspect the source diff and test output.


## Scheduled run summary (2026-10-21T00:48:24Z)

- Result: failed
- Active coding seconds: 3
- Coding passes: 1
- Changed source paths: 14
- Failures: Pass 1 failed with exit 1. See log pass-1.log.
