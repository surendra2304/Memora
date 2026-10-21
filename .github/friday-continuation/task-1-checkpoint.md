# Task 1 checkpoint — Memora — 2026-10-22

- PR #15: https://github.com/surendra2304/Memora/pull/15, commit `593a81d0e2c64a7b5e5962ca29a47d64f35c2d03`, file `tests/test_final_infra.py`: corrected event assertion to exact published payload `{"action":"heartbeat","status":"ok"}`. CI `39672447315` passed Ruff, Alembic, pytest, PostgreSQL 18 integration, health, Docker.
- PR #16: default-deny event payload; `apps/api/routers/v1_events.py` explicitly allows only `source_agent`, `correlation_id`, `video_title`, `render_quality` for `vyralx.custom_render`, source commit `9cf0404fd85b94d5c2406623ee56c80648530b41`. Latest observed head `1b22f91aadcea7be0e44e6eeb577e7cd3e4689e1` passed `39673599696` across Ruff, migrations, pytest, PostgreSQL 18, health and Docker.
- Main SHA `a239bc93188aba5a2c10d831aa75bdd6f8e0e90b` run `39674007513` passed.
- PR #14 (agent-scoped idempotency tests) was closed, not merged, at `2026-10-21T20:54:30Z`. Comment says Codex code review could not complete and hit usage limit; no actionable source findings were supplied. Preserve branch `test/idempotency-isolation-regressions-20261021` at `72cb7bf3150e64edba84785e2da6d5446eb1b792`; previous run `39551432656` passed, later `39671891785` failed the now-fixed event assertion. Do not reopen blindly.
- PR #7 tombstone branch remains divergent. Separate endpoint idempotency branch `fix/task1-idempotent-task-write-20261021` SHA `4f0fac92082eac989b3f8e2f6eee02077a1d2372` contains `apps/api/routers/v1_task.py`, `tests/test_task_idempotency.py`; not freshly CI-verified.
- Local test execution unavailable (laptop MCP HTTP 404). Duration unmeasured; no merge/force-push.

Next: recheck PR #15/#16 exact heads/CI; continue endpoint idempotency test/PR only after duplicate check; review PR #7 divergence separately.