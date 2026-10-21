# Task 1 next-run instructions — Memora (2026-10-22)

1. Poll https://github.com/surendra2304/Memora/actions/runs/39671891785 for PR #14 branch `test/idempotency-isolation-regressions-20261021` at `72cb7bf3150e64edba84785e2da6d5446eb1b792`. Prior run `39551432656` passed on the exact same SHA; latest run was in progress during container initialization. Record actual final result.
2. PR #14 tests agent-scoped idempotency in `tests/test_write_service.py`, not task endpoint. Keep coverage claims precise.
3. Next code change: resume endpoint branch `fix/task1-idempotent-task-write-20261021`, files `apps/api/routers/v1_task.py`, `tests/test_task_idempotency.py`, verify same-key replay returns same memory ID, duplicate true, and one record. Avoid duplicate PR if one exists.
4. PR #7 https://github.com/surendra2304/Memora/pull/7 has divergent/unmergeable tombstone work; inspect current diff/compare, don't merge blindly.
5. Laptop execution returns HTTP 404; no local tests ran this invocation. Update checkpoint, next-run and last-run JSON with exact SHA, files, test results and CI URLs/status. No auto-merge.