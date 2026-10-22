"""
Asynchronous HTTP client pool and connection management for Memora.
Provides connection pooling, keepalive, and timeout configurations across the mesh.
"""
from __future__ import annotations

import logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

_async_client: Optional[httpx.AsyncClient] = None


def get_async_client(
    timeout: float = 10.0,
    max_connections: int = 100,
    max_keepalive_connections: int = 20,
) -> httpx.AsyncClient:
    """Obtain or instantiate the shared singleton AsyncClient with connection pooling."""
    global _async_client
    if _async_client is None or _async_client.is_closed:
        limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
            keepalive_expiry=30.0,
        )
        _async_client = httpx.AsyncClient(
            limits=limits,
            timeout=httpx.Timeout(timeout, connect=5.0),
            follow_redirects=True,
        )
    return _async_client


async def close_async_client() -> None:
    """Cleanly close the connection pool during application shutdown."""
    global _async_client
    if _async_client is not None and not _async_client.is_closed:
        await _async_client.aclose()
        _async_client = None
        logger.info("[memora]: Asynchronous HTTP client connection pool closed.")
