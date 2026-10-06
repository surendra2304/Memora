"""
Database Engine & Session Management for Memora
Supports PostgreSQL as primary with automatic fallback/test SQLite support.
"""
import os
import logging
from typing import Generator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
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


def _turso_sqlalchemy_url(url: str) -> str:
    """Convert a Turso URL to the URL accepted by sqlalchemy-libsql."""
    value = url.strip()
    if value.startswith("sqlite+libsql://"):
        return value
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "libsql"} or not parsed.netloc:
        raise ValueError("Turso URL must use https:// or libsql:// and include a host")
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("secure", "true")
    return urlunsplit(("sqlite+libsql", parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def _new_sqlite_engine(url: str):
    _ensure_sqlite_dir(url)

    # Under concurrent load the default journal mode serialises readers against
    # the single writer, and connections give up almost immediately with
    # "database is locked". Verified with scripts/stress_memora.py: 6 agents
    # writing while 6 readers queried produced OperationalError on both the
    # event_log insert and the read path.
    #
    # WAL lets readers proceed concurrently with one writer, and busy_timeout
    # makes a contended connection wait instead of failing. Both are set per
    # connection because SQLite pragmas are not inherited from the file.
    timeout_seconds = float(os.getenv("MEMORA_SQLITE_BUSY_TIMEOUT", "30"))

    engine = create_engine(
        url,
        connect_args={
            "check_same_thread": False,
            "timeout": timeout_seconds,
        } if "sqlite" in url else {},
        echo=settings.DB_ECHO,
    )

    if "sqlite" in url:
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute(f"PRAGMA busy_timeout={int(timeout_seconds * 1000)}")
                # WAL's recommended durability/performance trade-off.
                cursor.execute("PRAGMA synchronous=NORMAL")
            finally:
                cursor.close()

    return engine

def create_db_engine():
    global _active_storage_backend, _active_storage_durable
    db_url = settings.DATABASE_URL
    configured_turso_url = settings.TURSO_DATABASE_URL or os.getenv("TURSO_DATABASE_URL", "")
    turso_token = settings.TURSO_AUTH_TOKEN or os.getenv("TURSO_AUTH_TOKEN", "")

    # Render's manifest historically supplied an ephemeral SQLite DATABASE_URL.
    # In production, prefer the configured Turso primary when credentials exist.
    if production_mode() and configured_turso_url and turso_token:
        db_url = configured_turso_url

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

    # 2. Turso cloud database connection through Turso's SQLAlchemy dialect.
    if "turso.io" in db_url or db_url.startswith(("libsql://", "sqlite+libsql://")):
        try:
            if not turso_token:
                raise RuntimeError("TURSO_AUTH_TOKEN is not configured")
            clean_url = _turso_sqlalchemy_url(db_url)
            engine = create_engine(
                clean_url,
                connect_args={"auth_token": turso_token},
                pool_pre_ping=True,
                echo=settings.DB_ECHO,
            )
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Successfully connected to Turso cloud database.")
            _active_storage_backend = "turso"
            _active_storage_durable = True
            return engine
        except Exception as e:
            fallback_url = settings.SQLITE_FALLBACK_URL
            logger.warning(
                "Turso ORM connection unavailable (%s); local SQLite fallback is non-authoritative.",
                type(e).__name__,
            )
            _active_storage_backend = "sqlite_fallback"
            _active_storage_durable = False
            return _new_sqlite_engine(fallback_url)

    # 3. Standard SQLite connection
    _active_storage_backend = "sqlite"
    _active_storage_durable = False
    return _new_sqlite_engine(db_url)

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
