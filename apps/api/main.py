"""
Memora API Application Entrypoint
FastAPI server providing persistent memory and context infrastructure.
"""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from apps.api.dependencies import authenticate_agent
from core.config import settings
from storage.relational.session import init_db, get_db, storage_ready
from storage.relational.models import Agent, Namespace, MemoryRecord
from storage.vector.qdrant_adapter import vector_adapter
from core.events.emitter import event_emitter
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
)

import os
import requests
import logging

logger = logging.getLogger(__name__)

def _startup_turso_sync_enabled() -> bool:
    """Only production or an explicit opt-in may import the cloud DB at boot."""
    environment = (os.getenv("ENVIRONMENT", "") or settings.MEMORA_ENV).lower()
    explicit = os.getenv("MEMORA_SYNC_TURSO_ON_STARTUP", "").lower() in {"1", "true", "yes"}
    return environment == "production" or explicit

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

        VALID_AGENTS = {"friday", "forge", "sentinel", "inference", "cortex", "intelx", "futuris", "stratex", "memora"}
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
    try:
        yield
    finally:
        event_emitter.stop_cloud_sync()

app = FastAPI(
    title="MEMORA API",
    description="Persistent Memory & Context Infrastructure Layer for AI Agent Ecosystems",
    version="2.0.0",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False)
@app.api_route("/dashboard", methods=["GET", "HEAD"], response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    if STATIC_INDEX.exists():
        return HTMLResponse(content=STATIC_INDEX.read_text(encoding="utf-8"))
    return RedirectResponse(url="/docs")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("apps.api.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
