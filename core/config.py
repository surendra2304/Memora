"""
Memora Core Configuration
Manages environment variables, connection URLs, and system constants.
"""
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
    TURSO_DATABASE_URL: Optional[str] = None
    TURSO_AUTH_TOKEN: Optional[str] = None
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
    MEMORA_API_KEY: Optional[str] = None
    MEMORA_MASTER_KEY: Optional[str] = None
    DEFAULT_CONFIDENCE_THRESHOLD: float = 0.75
    DEFAULT_IMPORTANCE_THRESHOLD: float = 0.50

    # CORS. Comma-separated list of allowed browser origins, e.g.
    # MEMORA_CORS_ORIGINS=https://app.example.com,https://admin.example.com
    # An empty value (the default) allows no cross-origin browser access, which
    # is correct for an agent-to-agent API. "*" is permitted but is mutually
    # exclusive with credentialed requests per the CORS spec, so credentials are
    # disabled automatically when it is used.
    MEMORA_CORS_ORIGINS: str = ""

    def get_cors_origins(self) -> list[str]:
        return [o.strip() for o in self.MEMORA_CORS_ORIGINS.split(",") if o.strip()]

    def get_memora_api_key(self) -> str:
        key = self.MEMORA_API_KEY or self.MEMORA_MASTER_KEY
        if not key:
            if str(self.MEMORA_ENV).lower() == "production":
                raise ValueError(
                    "Production Security Violation: MEMORA_API_KEY or MEMORA_MASTER_KEY "
                    "must be configured via environment variable in production."
                )
            return "memora_api_dev"
        return key

settings = Settings()
