"""
Memora API Application Entrypoint
FastAPI server providing persistent memory and context infrastructure.
"""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from core.config import settings
from storage.relational.session import init_db, get_db
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
)

import os
import requests

def sync_from_turso():
    from storage.relational.session import SessionLocal
    db = SessionLocal()
    try:
        turso_url = os.getenv("TURSO_DATABASE_URL", settings.DATABASE_URL)
        turso_token = os.getenv("TURSO_AUTH_TOKEN", settings.TURSO_AUTH_TOKEN)
        if not (turso_url and turso_token and "turso.io" in turso_url):
            return

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
            return
        results = resp.json().get("results", [])
        if len(results) < 3:
            return

        VALID_AGENTS = {"friday", "forge", "sentinel", "inference", "cortex", "intelx", "futuris", "stratex", "memora"}
        valid_agent_ids = set()

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
        db.commit()

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
        db.commit()

        turso_mem_ids = {row[0]["value"] for row in results[2]["response"]["result"]["rows"]}
        if turso_mem_ids:
            db.query(MemoryRecord).filter(~MemoryRecord.id.in_(turso_mem_ids)).delete(synchronize_session=False)
            db.commit()

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
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables, vector connection, and event bus
    init_db()
    sync_from_turso()
    vector_adapter.connect()
    event_emitter.connect()
    yield

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
app.include_router(audit_router)

@app.api_route("/api/dashboard/sync", methods=["GET", "POST"], include_in_schema=False)
def dashboard_sync():
    sync_from_turso()
    return {"status": "success", "message": "Synchronized with Turso Cloud database"}

@app.get("/api/dashboard/overview", include_in_schema=False)
def dashboard_overview(db: Session = Depends(get_db)):
    agents = db.query(Agent).all()
    namespaces = db.query(Namespace).all()
    records = db.query(MemoryRecord).order_by(MemoryRecord.created_at.desc()).all()

    # If local database has no records (e.g. freshly booted Docker container), fetch directly from Turso Cloud
    if not records:
        turso_url = os.getenv("TURSO_DATABASE_URL", settings.DATABASE_URL)
        turso_token = os.getenv("TURSO_AUTH_TOKEN", settings.TURSO_AUTH_TOKEN)
        if turso_url and turso_token and "turso.io" in turso_url:
            try:
                pipeline_url = f"{turso_url.rstrip('/')}/v2/pipeline"
                headers = {"Authorization": f"Bearer {turso_token}", "Content-Type": "application/json"}
                payload = {
                    "requests": [
                        {"type": "execute", "stmt": {"sql": "SELECT m.id, a.name, a.role, n.path, m.memory_type, m.content_text, m.confidence, m.importance, m.lifecycle_state, m.created_at FROM memory_records m LEFT JOIN agents a ON m.owner_id = a.id LEFT JOIN namespaces n ON m.namespace_id = n.id ORDER BY m.created_at DESC"}},
                        {"type": "execute", "stmt": {"sql": "SELECT id, name, role, bounded_scope FROM agents"}},
                        {"type": "execute", "stmt": {"sql": "SELECT id, path, type FROM namespaces"}}
                    ]
                }
                resp = requests.post(pipeline_url, headers=headers, json=payload, timeout=8)
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    if len(results) >= 3:
                        mem_rows = results[0].get("response", {}).get("result", {}).get("rows", [])
                        agent_rows = results[1].get("response", {}).get("result", {}).get("rows", [])
                        ns_rows = results[2].get("response", {}).get("result", {}).get("rows", [])

                        t_memories = []
                        agent_counts = {}
                        for r in mem_rows:
                            owner_name = r[1]["value"] if r[1] else "unknown"
                            agent_counts[owner_name] = agent_counts.get(owner_name, 0) + 1
                            t_memories.append({
                                "id": r[0]["value"],
                                "owner_name": owner_name,
                                "owner_role": r[2]["value"] if r[2] else "worker",
                                "namespace_path": r[3]["value"] if r[3] else "memora://global",
                                "memory_type": r[4]["value"] if r[4] else "episodic",
                                "content_text": r[5]["value"] if r[5] else "",
                                "confidence": float(r[6]["value"]) if r[6] and r[6]["value"] is not None else 1.0,
                                "importance": float(r[7]["value"]) if r[7] and r[7]["value"] is not None else 0.8,
                                "lifecycle_state": r[8]["value"] if r[8] else "active",
                                "created_at": r[9]["value"] if r[9] else None,
                                "entities": [],
                                "tenant_id": "default"
                            })

                        t_agents = []
                        for r in agent_rows:
                            name = r[1]["value"]
                            t_agents.append({
                                "id": r[0]["value"],
                                "name": name,
                                "role": r[2]["value"] if r[2] else "worker",
                                "bounded_scope": r[3]["value"] if r[3] else None,
                                "memory_count": agent_counts.get(name, 0)
                            })
                        t_agents.sort(key=lambda x: x["memory_count"], reverse=True)

                        return {
                            "total_memories": len(t_memories),
                            "total_agents": len(t_agents),
                            "total_namespaces": len(ns_rows),
                            "agents": t_agents,
                            "memories": t_memories
                        }
            except Exception:
                pass

    agent_map = {a.id: a for a in agents}
    ns_map = {n.id: n for n in namespaces}

    agent_counts = {}
    for r in records:
        agent_counts[r.owner_id] = agent_counts.get(r.owner_id, 0) + 1

    VALID_AGENTS = {"friday", "forge", "sentinel", "inference", "cortex", "intelx", "futuris", "stratex", "memora"}
    agents_list = []
    for a in agents:
        if a.name.lower() in VALID_AGENTS:
            agents_list.append({
                "id": a.id,
                "name": a.name,
                "role": a.role or "worker",
                "description": a.description or "",
                "bounded_scope": a.bounded_scope,
                "memory_count": agent_counts.get(a.id, 0)
            })

    # Sort with supervisor first, then by name
    agents_list.sort(key=lambda x: (0 if x["role"] == "supervisor" else 1, x["name"]))

    memories_list = []
    for r in records:
        owner = agent_map.get(r.owner_id)
        ns = ns_map.get(r.namespace_id)
        memories_list.append({
            "id": r.id,
            "owner_name": owner.name if owner else "unknown",
            "owner_role": owner.role if owner else "worker",
            "namespace_path": ns.path if ns else "memora://global",
            "memory_type": r.memory_type.value if hasattr(r.memory_type, "value") else str(r.memory_type),
            "content_text": r.content_text,
            "confidence": float(r.confidence) if r.confidence is not None else 1.0,
            "importance": float(r.importance) if r.importance is not None else 0.8,
            "lifecycle_state": r.lifecycle_state.value if hasattr(r.lifecycle_state, "value") else str(r.lifecycle_state),
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "entities": r.entities or [],
            "tenant_id": r.tenant_id
        })

    return {
        "total_memories": len(records),
        "total_agents": len(agents),
        "total_namespaces": len(namespaces),
        "agents": agents_list,
        "memories": memories_list
    }

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