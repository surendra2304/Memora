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
    DATABASE_URL: str = "sqlite:///./data/memora.db"
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
    MEMORA_API_KEY: str = "memora_api"
    MEMORA_MASTER_KEY: Optional[str] = None
    DEFAULT_CONFIDENCE_THRESHOLD: float = 0.75
    DEFAULT_IMPORTANCE_THRESHOLD: float = 0.50

    def get_memora_api_key(self) -> str:
        return self.MEMORA_API_KEY or self.MEMORA_MASTER_KEY or "memora_api"

settings = Settings()
