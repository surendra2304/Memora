"""
Database Engine & Session Management for Memora
Supports PostgreSQL as primary with automatic fallback/test SQLite support.
"""
import os
import logging
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from fastapi import HTTPException
from core.config import settings
from storage.relational.base import Base

logger = logging.getLogger(__name__)
_active_storage_backend = "uninitialized"
_active_storage_durable = False


def production_mode() -> bool:
    return (os.getenv("ENVIRONMENT", "") or settings.MEMORA_ENV).lower() == "production"


def storage_receipt() -> dict[str, str | bool]:
    """Describe the storage actually used by the ORM, not a configured replica."""
    return {
        "backend": _active_storage_backend,
        "durable": _active_storage_durable,
        "durability": "shared_durable" if _active_storage_durable else "process_local",
    }


def storage_ready() -> bool:
    return not production_mode() or _active_storage_durable

def _ensure_sqlite_dir(url: str):
    if "sqlite:///" in url:
        path = url.replace("sqlite:///", "")
        if "?" in path:
            path = path.split("?")[0]
        if path and path != ":memory:":
            dirname = os.path.dirname(os.path.abspath(path))
            if dirname:
                os.makedirs(dirname, exist_ok=True)

def create_db_engine():
    global _active_storage_backend, _active_storage_durable
    db_url = settings.DATABASE_URL
    turso_token = settings.TURSO_AUTH_TOKEN or os.getenv("TURSO_AUTH_TOKEN", "")

    # 1. PostgreSQL connection
    if db_url.startswith("postgresql"):
        try:
            engine = create_engine(
                db_url,
                pool_pre_ping=True,
                pool_size=10,
                max_overflow=20,
                echo=settings.DB_ECHO,
                connect_args={"connect_timeout": 3}
            )
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Successfully connected to PostgreSQL database.")
            _active_storage_backend = "postgresql"
            _active_storage_durable = True
            return engine
        except Exception as e:
            if settings.USE_SQLITE_FALLBACK:
                fallback_url = settings.SQLITE_FALLBACK_URL
                _ensure_sqlite_dir(fallback_url)
                logger.warning(f"PostgreSQL unavailable ({e}). Falling back to SQLite: {fallback_url}")
                _active_storage_backend = "sqlite_fallback"
                _active_storage_durable = False
                return create_engine(
                    fallback_url,
                    connect_args={"check_same_thread": False},
                    echo=settings.DB_ECHO
                )
            raise e

    # 2. Turso cloud database connection (libsql:// or https://)
    if "turso.io" in db_url or db_url.startswith("libsql://"):
        try:
            # Format SQLite driver URL for Turso / libSQL
            clean_url = db_url.replace("libsql://", "sqlite+https://") if db_url.startswith("libsql://") else f"sqlite+{db_url}"
            if turso_token and "authToken" not in clean_url:
                clean_url = f"{clean_url}?authToken={turso_token}&secure=true"
            engine = create_engine(clean_url, echo=settings.DB_ECHO)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Successfully connected to Turso cloud database.")
            _active_storage_backend = "turso"
            _active_storage_durable = True
            return engine
        except Exception as e:
            fallback_url = settings.SQLITE_FALLBACK_URL
            _ensure_sqlite_dir(fallback_url)
            logger.warning("Turso ORM connection unavailable (%s); local SQLite fallback is non-authoritative.", type(e).__name__)
            _active_storage_backend = "sqlite_fallback"
            _active_storage_durable = False
            return create_engine(
                fallback_url,
                connect_args={"check_same_thread": False},
                echo=settings.DB_ECHO
            )

    # 3. Standard SQLite connection
    _ensure_sqlite_dir(db_url)
    _active_storage_backend = "sqlite"
    _active_storage_durable = False
    return create_engine(
        db_url,
        connect_args={"check_same_thread": False} if "sqlite" in db_url else {},
        echo=settings.DB_ECHO
    )

engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Initializes tables in database."""
    if not storage_ready():
        logger.error("Skipping local schema initialization: production storage is not durable and authoritative.")
        return
    Base.metadata.create_all(bind=engine)

def get_db() -> Generator[Session, None, None]:
    """Dependency for obtaining a database session."""
    if not storage_ready():
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Memora production storage is unavailable: configure a supported durable authoritative database.",
                **storage_receipt(),
            },
        )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
