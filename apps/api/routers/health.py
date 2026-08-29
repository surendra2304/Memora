"""
Health Check Endpoints
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import text
from storage.relational.session import get_db

router = APIRouter(prefix="/health", tags=["Health"])

@router.get("")
def health_check(db: Session = Depends(get_db)):
    db_status = "healthy"
    try:
        db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"degraded ({e})"

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "service": "memora-api",
        "database": db_status,
        "version": "0.1.0"
    }