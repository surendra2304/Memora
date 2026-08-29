"""
FastAPI Dependencies for Memora API
"""
from typing import Optional, Generator
from fastapi import Header, Depends, HTTPException, status
from sqlalchemy.orm import Session
from storage.relational.session import get_db

def get_actor_header(
    x_agent_name: Optional[str] = Header(default="friday", alias="X-Agent-Name")
) -> str:
    """Extracts the requesting agent identity from request headers."""
    return x_agent_name.lower()

def get_purpose_header(
    x_access_purpose: Optional[str] = Header(default=None, alias="X-Access-Purpose")
) -> Optional[str]:
    """Extracts the stated purpose / intent for audit and policy verification."""
    return x_access_purpose