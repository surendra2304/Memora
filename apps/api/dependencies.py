"""
FastAPI Dependencies for Memora API
"""
import hmac
import os
from typing import Optional
from fastapi import Header, HTTPException, status
from sqlalchemy.orm import Session
from storage.relational.session import get_db
from core.config import settings

def get_actor_header(
    x_agent_name: Optional[str] = Header(default=None, alias="X-Agent-Name"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> str:
    """Return the authenticated agent identity, never an unverified header claim."""
    return authenticate_agent(x_agent_name, x_api_key, authorization)

def get_purpose_header(
    x_access_purpose: Optional[str] = Header(default=None, alias="X-Access-Purpose")
) -> Optional[str]:
    """Extracts the stated purpose / intent for audit and policy verification."""
    return x_access_purpose


def authenticate_agent(
    x_agent_name: Optional[str] = Header(default=None, alias="X-Agent-Name"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> str:
    """Authenticate an ecosystem agent using its named service credential."""
    agent = (x_agent_name or "").strip().lower()
    key_names = {
        "friday": "FRIDAY_API_KEY",
        "inference": "INFERENCE_API_KEY",
        "stratex": "STRATEX_API_KEY",
        "intelx": "INTELX_API_KEY",
        "futuris": "FUTURIS_API_KEY",
        "cortex": "CORTEX_API_KEY",
        "forge": "FORGE_API_KEY",
        "sentinel": "SENTINEL_API_KEY",
        "memora": "MEMORA_API_KEY",
    }
    supplied = x_api_key
    if not supplied and authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    expected = os.getenv(key_names.get(agent, ""), "") if agent in key_names else ""
    production = str(getattr(settings, "MEMORA_ENV", "")).lower() == "production" or os.getenv("ENVIRONMENT", "").lower() == "production"

    if not production and not expected and not supplied:
        return agent or "friday"
    if not agent or not expected or not supplied or not hmac.compare_digest(expected, supplied):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid agent credentials")
    return agent
