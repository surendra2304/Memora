# Phase 3 — Application lifecycle, API inventory and OpenAPI

**Status:** Complete. **Verification:** imported the app in a local environment and generated its FastAPI OpenAPI schema; no external service was contacted.

## Runtime composition

- [FACT] `apps/api/main.py:148-165` defines ASGI lifespan startup/shutdown: initialize DB tables; optionally run a Turso import when production or explicitly enabled; connect Qdrant; connect the event emitter; start event cloud sync; stop it on shutdown. `apps/api/main.py:167-172` creates a FastAPI 2.0.0 app and `:199-214` mounts the 15 named routers.
- [FACT] CORS reads `MEMORA_CORS_ORIGINS`; the default empty string means no browser cross-origin origins are enabled (`core/config.py:38-47`, `apps/api/main.py:174-197`).
- [FACT] Public root/dashboard handlers serve `apps/api/static/index.html` if present, and `/api/dashboard/overview` returns 410; dashboard sync is restricted to the `memora` service identity (`apps/api/main.py:216-241`). These dashboard routes are omitted from OpenAPI.

## Route inventory

- [FACT] OpenAPI exposes **45 paths / 51 operations**. Grouped inventory (distinct paths / operations): agents 3/4; audit 1/1; health 1/2; legacy memories 4/5; mesh 1/1; metrics 1/1; namespaces 2/4; v1 collaboration 3/3; v1 context 1/1; v1 events 3/3; v1 memories 15/16; v1 metrics 1/1; v1 namespaces 1/1; v1 reflection 2/2; v1 resilience 5/5; v1 task 1/1. The schema includes GET and HEAD on `/health` and the two methods on `/agents` and `/namespaces` as counted above.
- [FACT] Seven app-authored operations are excluded from OpenAPI by `include_in_schema=False`: GET/POST `/api/dashboard/sync`, GET `/api/dashboard/overview`, GET/HEAD `/`, GET/HEAD `/dashboard` (`apps/api/main.py:216-241`). FastAPI's built-in `/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc` are a separate framework surface.
- [FACT] The OpenAPI document has no `securitySchemes` or global `security` declaration. Authentication appears as explicit header parameters on protected operations; `/health`, `/v1/metrics`, and `/metrics` have no auth header parameters. `/mesh/envelope` documents only Authorization and performs its credential/signature checks inside its handler.
- [FACT] Both JSON `/v1/metrics` and Prometheus `/metrics` are defined without a FastAPI dependency (`apps/api/routers/v1_metrics.py:11-20`). Their HTTP 200 behavior without credentials was separately observed in the prior local probe (Phase 0).

## Exit check / limits

- [FACT] The schema count was generated against this checkout with FastAPI loaded from the restored `requirements.txt` environment; it is not a deployed-route scan.
- [FACT] No cloud runtime or live service was accessed. The ASGI lifespan sequence is static source evidence; the earlier `/health` 200 was a local development `TestClient` check, not production readiness proof.
- [FACT] Phase 3 is complete; next is authentication, identity and tenant boundaries.
