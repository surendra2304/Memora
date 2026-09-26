"""
Health Check Endpoints
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from storage.relational.session import get_db
from storage.relational.turso_events import configured as turso_events_configured, probe as probe_turso_events, production_mode

router = APIRouter(prefix="/health", tags=["Health"])

@router.get("")
@router.head("")
def health_check(db: Session = Depends(get_db)):
    db_status = "healthy"
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"degraded ({e})"

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

    return {
        "status": "healthy" if db_status == "healthy" and event_store_ok else "degraded",
        "service": "memora-api",
        "database": db_status,
        "event_store": event_store,
        "event_store_durable": event_store == "turso_available",
        "version": "2.0.0"
    }
