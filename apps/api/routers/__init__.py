from apps.api.routers.health import router as health_router
from apps.api.routers.agents import router as agents_router
from apps.api.routers.namespaces import router as namespaces_router
from apps.api.routers.memories import router as memories_router
from apps.api.routers.audit import router as audit_router
from apps.api.routers.v1_memories import router as v1_memories_router
from apps.api.routers.v1_context import router as v1_context_router
from apps.api.routers.v1_metrics import router as v1_metrics_router
from apps.api.routers.v1_namespaces import router as v1_namespaces_router

__all__ = [
    "health_router",
    "agents_router",
    "namespaces_router",
    "memories_router",
    "audit_router",
    "v1_memories_router",
    "v1_context_router",
    "v1_metrics_router",
    "v1_namespaces_router",
]