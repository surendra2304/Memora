"""
Memora Core Configuration
Manages environment variables, connection URLs, and system constants.
"""
import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    MEMORA_ENV: str = "development"
    DEBUG: bool = True
    PORT: int = 8000
    HOST: str = "0.0.0.0"

    # Database
    DATABASE_URL: str = "postgresql+psycopg2://memora_user:memora_password@localhost:5432/memora_db"
    SQLITE_FALLBACK_URL: str = "sqlite:///./data/memora.db"
    USE_SQLITE_FALLBACK: bool = True
    DB_ECHO: bool = False

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Vector DB (Qdrant)
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "memora_embeddings"
    VECTOR_DIMENSION: int = 1536

    # Security & Policy
    MEMORA_MASTER_KEY: str = "memora_secret_dev_key_2026"
    DEFAULT_CONFIDENCE_THRESHOLD: float = 0.75
    DEFAULT_IMPORTANCE_THRESHOLD: float = 0.50

settings = Settings()
