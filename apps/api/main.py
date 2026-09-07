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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database tables, vector connection, and event bus
    init_db()
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

@app.get("/api/dashboard/overview", include_in_schema=False)
def dashboard_overview(db: Session = Depends(get_db)):
    agents = db.query(Agent).all()
    namespaces = db.query(Namespace).all()
    records = db.query(MemoryRecord).order_by(MemoryRecord.created_at.desc()).all()

    agent_map = {a.id: a for a in agents}
    ns_map = {n.id: n for n in namespaces}

    agent_counts = {}
    for r in records:
        agent_counts[r.owner_id] = agent_counts.get(r.owner_id, 0) + 1

    agents_list = []
    for a in agents:
        agents_list.append({
            "id": a.id,
            "name": a.name,
            "role": a.role or "worker",
            "bounded_scope": a.bounded_scope,
            "memory_count": agent_counts.get(a.id, 0)
        })

    agents_list.sort(key=lambda x: x["memory_count"], reverse=True)

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

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    if STATIC_INDEX.exists():
        return HTMLResponse(content=STATIC_INDEX.read_text(encoding="utf-8"))
    return RedirectResponse(url="/docs")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("apps.api.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)