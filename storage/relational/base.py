"""
SQLAlchemy Base Model and Metadata Definitions
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.types import JSON

@compiles(JSON, "oracle")
def _compile_json_oracle(type_, compiler, **kw):
    return "JSON"

def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)

def generate_uuid() -> str:
    return str(uuid.uuid4())

class Base(DeclarativeBase):
    """Base class for all SQLAlchemy declarative models in Memora."""
    pass
