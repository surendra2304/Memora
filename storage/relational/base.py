"""
SQLAlchemy Base Model and Metadata Definitions
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import DateTime

def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)

def generate_uuid() -> str:
    return str(uuid.uuid4())

class Base(DeclarativeBase):
    """Base class for all SQLAlchemy declarative models in Memora."""
    pass
