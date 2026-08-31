"""
Memora API Application Entrypoint
FastAPI server providing persistent memory and context infrastructure.
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from core.config import settings
from storage.relational.session import init_db
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

@app.get("/", include_in_schema=False)
@app.head("/", include_in_schema=False)
def root_redirect():
    return RedirectResponse(url="/docs")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("apps.api.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)