"""
Health Check Endpoints
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import text
from storage.relational.session import get_db, storage_receipt
from storage.relational.turso_events import configured as turso_events_configured, probe as probe_turso_events, production_mode
from storage.vector.qdrant_adapter import vector_adapter

router = APIRouter(prefix="/health", tags=["Health"])

@router.get("")
@router.head("")
def health_check(db: Session = Depends(get_db)):
    database = storage_receipt()
    db_status = "healthy"
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"degraded ({type(e).__name__})"

    event_store = "local_only"
    event_store_ok = True
    if turso_events_configured():
        try:
            probe_turso_events()
            event_store = "turso_available"
        except Exception:
            event_store = "turso_unavailable"
            event_store_ok = False
    elif production_mode():
        event_store = "turso_not_configured"
        event_store_ok = False

    vector_store = vector_adapter.readiness()

    status_ok = db_status == "healthy" and event_store_ok and vector_store["available"]
    return JSONResponse(status_code=200 if status_ok else 503, content={
        "status": "healthy" if status_ok else "degraded",
        "service": "memora-api",
        "database": db_status,
        "database_backend": database["backend"],
        "database_durable": database["durable"],
        "database_durability": database["durability"],
        "event_store": event_store,
        "event_store_durable": event_store == "turso_available",
        "vector_store": vector_store,
        "version": "2.0.0"
    })
