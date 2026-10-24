"""
Memora API Application Entrypoint
FastAPI server providing persistent memory and context infrastructure.
"""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from apps.api.dependencies import authenticate_agent
from apps.api.middleware import RequestBodyLimitMiddleware
from core.config import settings, validate_startup_config
from storage.relational.session import init_db, storage_ready, get_db
from storage.relational.models import Agent, Namespace, MemoryRecord
from storage.vector.qdrant_adapter import vector_adapter
from core.events.emitter import event_emitter
from apps.api.routers.health import readiness_check
from apps.api.routers import (
    health_router,
    agents_router,
    namespaces_router,
    memories_router,
    audit_router,
    v1_memories_router,
    v1_context_router,
    v1_metrics_router,
    v1_namespaces_router,
    v1_task_router,
    v1_events_router,
    mesh_events_router,
    v1_resilience_router,
    v1_collaboration_router,
    v1_reflection_router,
    semantic_search_router,
    encrypted_vector_router,
    causal_graph_router,
)

import os
import requests
import logging
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [memora]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("memora")

def _startup_turso_sync_enabled() -> bool:
    """Only an explicit migration opt-in may import data from legacy Turso."""
    return os.getenv("MEMORA_SYNC_TURSO_ON_STARTUP", "").strip().lower() in {"1", "true", "yes"}

def sync_from_turso():
    if (os.getenv("ENVIRONMENT", "") or settings.MEMORA_ENV).lower() == "production" and not storage_ready():
        return {"status": "unavailable", "reason": "non_authoritative_local_storage", "agents_imported": 0, "namespaces_imported": 0, "memories_imported": 0}
    from storage.relational.session import SessionLocal
    db = SessionLocal()
    try:
        turso_url = os.getenv("TURSO_DATABASE_URL", settings.DATABASE_URL)
        turso_token = os.getenv("TURSO_AUTH_TOKEN", settings.TURSO_AUTH_TOKEN)
        if not (turso_url and turso_token and "turso.io" in turso_url):
            return {"status": "unconfigured", "agents_imported": 0, "namespaces_imported": 0, "memories_imported": 0}

        pipeline_url = f"{turso_url.rstrip('/')}/v2/pipeline"
        headers = {"Authorization": f"Bearer {turso_token}", "Content-Type": "application/json"}
        payload = {
            "requests": [
                {"type": "execute", "stmt": {"sql": "SELECT id, name, description, created_at, role, parent_agent_id, bounded_scope, tenant_id FROM agents;"}},
                {"type": "execute", "stmt": {"sql": "SELECT id, path, type, agent_id, created_at, tenant_id FROM namespaces;"}},
                {"type": "execute", "stmt": {"sql": "SELECT id, namespace_id, owner_id, memory_type, content_text, source, provenance, confidence, importance, lifecycle_state, created_at, last_verified_at, superseded_by_id, tenant_id FROM memory_records;"}}
            ]
        }
        resp = requests.post(pipeline_url, headers=headers, json=payload, timeout=10)
        if resp.status_code != 200:
            return {"status": "unavailable", "reason": f"http_{resp.status_code}", "agents_imported": 0, "namespaces_imported": 0, "memories_imported": 0}
        results = resp.json().get("results", [])
        if len(results) < 3:
            return {"status": "invalid_response", "agents_imported": 0, "namespaces_imported": 0, "memories_imported": 0}

        VALID_AGENTS = {"friday", "forge", "sentinel", "inference", "cortex", "intelx", "futuris", "stratex", "memora", "vyralx"}
        valid_agent_ids = set()

        agents_imported = 0
        namespaces_imported = 0
        memories_imported = 0
        for row in results[0]["response"]["result"]["rows"]:
            aname = str(row[1]["value"]).lower()
            if aname not in VALID_AGENTS:
                continue
            aid = row[0]["value"]
            valid_agent_ids.add(aid)
            if not db.query(Agent).filter(Agent.id == aid).first():
                db.add(Agent(
                    id=aid,
                    name=row[1]["value"],
                    description=row[2]["value"] if row[2] else None,
                    role=row[4]["value"] if row[4] else "worker",
                    parent_agent_id=row[5]["value"] if row[5] else None,
                    bounded_scope=row[6]["value"] if row[6] else None,
                    tenant_id=row[7]["value"] if len(row) > 7 and row[7] else "default"
                ))
                agents_imported += 1

        for row in results[1]["response"]["result"]["rows"]:
            nid = row[0]["value"]
            agent_id = row[3]["value"] if row[3] else None
            if agent_id and agent_id not in valid_agent_ids:
                continue
            if not db.query(Namespace).filter(Namespace.id == nid).first():
                db.add(Namespace(
                    id=nid,
                    path=row[1]["value"],
                    type=row[2]["value"],
                    agent_id=agent_id,
                    tenant_id=row[5]["value"] if len(row) > 5 and row[5] else "default"
                ))
                namespaces_imported += 1

        for row in results[2]["response"]["result"]["rows"]:
            mid = row[0]["value"]
            owner_id = row[2]["value"]
            if owner_id not in valid_agent_ids:
                continue
            if not db.query(MemoryRecord).filter(MemoryRecord.id == mid).first():
                db.add(MemoryRecord(
                    id=mid,
                    namespace_id=row[1]["value"],
                    owner_id=owner_id,
                    memory_type=row[3]["value"],
                    content_text=row[4]["value"],
                    source=row[5]["value"] if row[5] else "api",
                    confidence=float(row[7]["value"]) if row[7]["value"] is not None else 1.0,
                    importance=float(row[8]["value"]) if row[8]["value"] is not None else 0.8,
                    lifecycle_state=row[9]["value"] if row[9] else "active",
                    tenant_id=row[13]["value"] if len(row) > 13 and row[13] else "default"
                ))
                memories_imported += 1
        db.commit()
        return {
            "status": "success",
            "agents_imported": agents_imported,
            "namespaces_imported": namespaces_imported,
            "memories_imported": memories_imported,
            "source": "turso_import_merge",
            "deletions": 0,
        }
    except Exception as exc:
        db.rollback()
        logger.warning("Turso import failed (%s)", type(exc).__name__)
        return {"status": "error", "reason": type(exc).__name__, "agents_imported": 0, "namespaces_imported": 0, "memories_imported": 0}
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Validate required production environment variables
    validate_startup_config()
    # Initialize database tables, vector connection, and event bus
    init_db()
    sync_result = sync_from_turso() if _startup_turso_sync_enabled() and storage_ready() else {"status": "disabled", "agents_imported": 0, "namespaces_imported": 0, "memories_imported": 0}
    if sync_result["status"] != "success" and sync_result["status"] != "unconfigured":
        logger.warning("Turso startup import status: %s", sync_result["status"])
    vector_adapter.connect()
    event_emitter.connect()
    event_sync_started = event_emitter.start_cloud_sync()
    if not event_sync_started:
        from storage.relational.turso_events import production_mode, configured
        if production_mode() and not configured():
            logger.error("Production durable event feed is unavailable: Turso event storage is not configured.")

    if storage_ready():
        from storage.convergence.tombstone_worker import tombstone_worker
        tombstone_worker.start_background_worker(interval_seconds=15.0)
        from storage.convergence.horizon_sweeper import horizon_sweeper
        horizon_sweeper.start_background_worker()

    vyralx_key = os.getenv("VYRALX_API_KEY", "").strip()
    if len(vyralx_key) >= 32 and storage_ready():
        try:
            from scripts.register_vyralx_identity import register_vyralx_identity
            agent_id, ns_path = register_vyralx_identity()
            logger.info("VyralX identity verified/provisioned (%s, %s)", agent_id, ns_path)
        except Exception as exc:
            logger.warning("VyralX bootstrap skipped or failed: %s", exc)

    try:
        yield
    finally:
        logger.info("Initiating graceful shutdown sequence for Memora...")
        try:
            from storage.convergence.horizon_sweeper import horizon_sweeper
            horizon_sweeper.stop_background_worker()
        except Exception as exc:
            logger.warning("Horizon sweeper shutdown encountered exception: %s", exc)
        try:
            from storage.convergence.tombstone_worker import tombstone_worker
            tombstone_worker.stop_background_worker()
        except Exception as exc:
            logger.warning("Tombstone worker shutdown encountered exception: %s", exc)
        event_emitter.stop_cloud_sync()
        try:
            from core.resilience.http_client import close_async_client
            await close_async_client()
        except Exception:
            pass
        try:
            from storage.relational.session import checkpoint_wal, engine
            checkpoint_wal()
            engine.dispose()
            logger.info("Relational database connections cleanly closed.")
        except Exception as exc:
            logger.warning("Database shutdown clean-up encountered exception: %s", exc)
        try:
            vector_adapter.disconnect()
        except Exception:
            pass
        logger.info("Memora graceful shutdown sequence completed.")

app = FastAPI(
    title="MEMORA API",
    description="Persistent Memory & Context Infrastructure Layer for AI Agent Ecosystems",
    version="2.0.0",
    lifespan=lifespan
)

# Standardized Global Error Envelopes
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    detail = exc.detail
    if isinstance(detail, dict):
        error_code = detail.get("error", f"http_{exc.status_code}")
        message = detail.get("message", str(detail))
    elif isinstance(detail, list):
        error_code = f"http_{exc.status_code}"
        message = str(detail)
    else:
        error_code = "not_found" if exc.status_code == 404 else f"http_{exc.status_code}"
        message = str(detail)

    return JSONResponse(
        status_code=exc.status_code,
        headers=getattr(exc, "headers", None),
        content={
            "detail": detail,
            "error": error_code,
            "message": message,
            "success": False,
        },
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    # Pydantic v2 may include a ValueError instance in ctx when a custom
    # validator rejects a payload. JSONResponse cannot serialize exceptions,
    # so encode the structured validation report with a safe exception stringifier.
    safe_errors = jsonable_encoder(
        errors,
        custom_encoder={BaseException: lambda exc: str(exc)},
    )
    message = "; ".join(f"{'.'.join(str(l) for l in err.get('loc', []))}: {err.get('msg')}" for err in errors)
    return JSONResponse(
        status_code=422,
        content={
            "detail": safe_errors,
            "error": "validation_error",
            "message": message or "Request validation failed",
            "success": False,
        },
    )

# CORS.
#
# This previously hardcoded allow_origins=["*"] together with
# allow_credentials=True. That pairing is invalid under the CORS spec — a
# browser will not honour a wildcard Access-Control-Allow-Origin on a
# credentialed request — and if a browser did accept it, any site could issue
# authenticated cross-origin calls to this API.
#
# Origins are now configured via MEMORA_CORS_ORIGINS. The default is empty,
# which allows no cross-origin browser access: correct for an agent-to-agent
# API, where callers are servers and are unaffected by CORS. Credentials are
# only enabled when the operator lists explicit origins, never with "*".
# Apply the body limiter before CORS is added so oversized responses still pass
# through the CORS layer and chunked requests receive the same cap as fixed ones.
app.add_middleware(RequestBodyLimitMiddleware, max_body_bytes=1_048_576)
_cors_origins = settings.get_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=bool(_cors_origins) and "*" not in _cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info(
    "CORS configured for %s",
    ", ".join(_cors_origins) if _cors_origins else "no cross-origin browser access",
)

# Mount Routers
app.include_router(health_router)
app.include_router(agents_router)
app.include_router(namespaces_router)
app.include_router(memories_router)
app.include_router(v1_memories_router)
app.include_router(v1_context_router)
app.include_router(v1_metrics_router)
app.include_router(v1_namespaces_router)
app.include_router(v1_task_router)
app.include_router(v1_events_router)
app.include_router(mesh_events_router)
app.include_router(audit_router)
app.include_router(v1_resilience_router)
app.include_router(v1_collaboration_router)
from apps.api.routers.enhanced_memory import router as enhanced_memory_router
from apps.api.routers.knowledge_graph import router as knowledge_graph_router
from apps.api.routers.multimodal_memory import router as multimodal_memory_router
from apps.api.routers.knowledge_distillation import router as knowledge_distillation_router
from apps.api.routers.temporal_knowledge import router as temporal_knowledge_router

app.include_router(enhanced_memory_router, prefix="/api/v1")
app.include_router(knowledge_graph_router, prefix="/api/v1")
app.include_router(multimodal_memory_router, prefix="/api/v1")
app.include_router(knowledge_distillation_router, prefix="/api/v1")
app.include_router(temporal_knowledge_router, prefix="/api/v1")
from apps.api.routers.knowledge_consolidation import router as knowledge_consolidation_router
from apps.api.routers.memory_retrieval_optimizer import router as memory_retrieval_optimizer_router

app.include_router(knowledge_consolidation_router, prefix="/api/v1")
app.include_router(memory_retrieval_optimizer_router, prefix="/api/v1")
app.include_router(semantic_search_router, prefix="/api/v1")
app.include_router(encrypted_vector_router, prefix="/api/v1")
app.include_router(causal_graph_router, prefix="/api/v1")

# --- Working Memory Manager Integration ---
from apps.api.routers.working_memory_manager import router as working_memory_router
app.include_router(working_memory_router, prefix="/api/v1/memora")

# --- Memory Access Controller Integration ---
from apps.api.routers.memory_access_controller import router as memory_access_router
app.include_router(memory_access_router, prefix="/api/v1/memora")

# --- Memory Provenance Tracker Integration ---
from apps.api.routers.memory_provenance_tracker import router as memory_provenance_router
app.include_router(memory_provenance_router, prefix="/api/v1/memora")

@app.api_route("/api/dashboard/sync", methods=["GET", "POST"], include_in_schema=False)
def dashboard_sync(agent_name: str = Depends(authenticate_agent)):
    if agent_name != "memora":
        raise HTTPException(status_code=403, detail="Only the Memora service identity may trigger a database sync.")
    if (os.getenv("ENVIRONMENT", "") or settings.MEMORA_ENV).lower() == "production" and not storage_ready():
        raise HTTPException(status_code=503, detail="Turso import is disabled while the ORM database is non-authoritative.")
    result = sync_from_turso()
    status_code = 200 if result["status"] == "success" else 503
    return JSONResponse(status_code=status_code, content=result)


@app.get("/api/dashboard/overview", include_in_schema=False)
def dashboard_overview():
    raise HTTPException(
        status_code=410,
        detail="The global memory dump was removed. Use authenticated, policy-scoped /v1/memories/search instead.",
    )

STATIC_INDEX = Path(__file__).resolve().parent / "static" / "index.html"

@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def root(request: Request):
    accept = request.headers.get("accept", "")
    if "text/html" in accept and "application/json" not in accept:
        if STATIC_INDEX.exists():
            return HTMLResponse(content=STATIC_INDEX.read_text(encoding="utf-8"))
        return RedirectResponse(url="/docs")
    return JSONResponse(status_code=200, content={
        "status": "ok",
        "service": "memora",
        "evidence_class": "process_liveness",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "storage": "sqlite_wal",
    })

@app.api_route("/dashboard", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    if STATIC_INDEX.exists():
        return HTMLResponse(content=STATIC_INDEX.read_text(encoding="utf-8"))
    return RedirectResponse(url="/docs")


@app.api_route("/ready", methods=["GET", "HEAD"], tags=["Health"])
def root_ready(db: Session = Depends(get_db)):
    """Dedicated root readiness probe for container orchestration."""
    return readiness_check(db)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("apps.api.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
