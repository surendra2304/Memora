from apps.api.routers.health import router as health_router
from apps.api.routers.agents import router as agents_router
from apps.api.routers.namespaces import router as namespaces_router
from apps.api.routers.memories import router as memories_router
from apps.api.routers.audit import router as audit_router

__all__ = [
    "health_router",
    "agents_router",
    "namespaces_router",
    "memories_router",
    "audit_router",
]