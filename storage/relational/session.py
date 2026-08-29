"""
Database Engine & Session Management for Memora
Supports PostgreSQL as primary with automatic fallback/test SQLite support.
"""
import logging
from typing import Generator
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from core.config import settings
from storage.relational.base import Base

logger = logging.getLogger(__name__)

def create_db_engine():
    db_url = settings.DATABASE_URL
    try:
        if db_url.startswith("postgresql"):
            # Test if postgres is available
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
            return engine
    except Exception as e:
        if settings.USE_SQLITE_FALLBACK:
            logger.warning(f"PostgreSQL unavailable ({e}). Falling back to SQLite: {settings.SQLITE_FALLBACK_URL}")
            fallback_engine = create_engine(
                settings.SQLITE_FALLBACK_URL,
                connect_args={"check_same_thread": False},
                echo=settings.DB_ECHO
            )
            return fallback_engine
        else:
            raise e

    # Default to sqlite if specified directly
    return create_engine(
        db_url,
        connect_args={"check_same_thread": False} if "sqlite" in db_url else {},
        echo=settings.DB_ECHO
    )

engine = create_db_engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Initializes tables in database."""
    Base.metadata.create_all(bind=engine)

def get_db() -> Generator[Session, None, None]:
    """Dependency for obtaining a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
