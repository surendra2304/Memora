# Task 1 checkpoint — Memora — 2026-10-22

## Verified fixes and CI
- PR #15 commit `593a81d0e2c64a7b5e5962ca29a47d64f35c2d03`, `tests/test_final_infra.py): updated an assertion to match the actual published payload `{"action":"heartbeat","status":"ok"}` instead of {}. Run `39672447315` passed all stages: Ruff, Alembic, pytest, PostgreSQL 18 integration, app health and Docker.
- PR #16 default-deny event payload branch: added explicit `vyralx.custom_render` allowlist in `apps/api/routers/v1_events.py` with only `source_agent`, `correlation_id`, `video_title`, `render_quality`. Initial source commit `9cf0404fd85b94d5c2406623ee56c80648530b41`; latest PR head `1b22f91aadcea7be0e44e6eeb577e7cd3e4689e1` passed complete run `39673599696` (Ruff, Alembic, pytest, PostgreSQL 18, app health, Docker). PR #16 remains open/mergeable.
- PR #14 agent-scoped idempotency tests head `72cb7bf3150e64edba84785e2da6d5446eb1b792`: prior run `39551432656` passed, later run `39671891785` failed on the old payload assertion fixed by PR #15. Current main head `a239bc93188aba5a2c10d831aa75bdd6f8e0e90b` run `39674007513` passed; recheck PR #14 with current base before marking green.
- PR #7 tombstone branch remains divergent. Endpoint idempotency branch `fix/task1-idempotent-task-write-20261021` at `4f0fac92082eac989b3f8e2f6eee02077a1d2372` has not been freshly CI verified.

## Limits / next
- No local tests (laptop MCP 404), active duration unmeasured, no merge/force-push.
- Next: verify PR #14 current state and CI; resume endpoint-level idempotency tests without creating duplicates.