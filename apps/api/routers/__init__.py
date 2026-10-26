from apps.api.routers.health import router as health_router
from apps.api.routers.agents import router as agents_router
from apps.api.routers.namespaces import router as namespaces_router
from apps.api.routers.memories import router as memories_router
from apps.api.routers.audit import router as audit_router
from apps.api.routers.v1_memories import router as v1_memories_router
from apps.api.routers.v1_context import router as v1_context_router
from apps.api.routers.v1_metrics import router as v1_metrics_router
from apps.api.routers.v1_namespaces import router as v1_namespaces_router
from apps.api.routers.v1_task import task_router as v1_task_router
from apps.api.routers.v1_events import router as v1_events_router, mesh_router as mesh_events_router
from apps.api.routers.v1_resilience import router as v1_resilience_router
from apps.api.routers.v1_collaboration import router as v1_collaboration_router
from apps.api.routers.v1_reflection import router as v1_reflection_router
from apps.api.routers.memory_consolidation_engine import router as consolidation_router
from apps.api.routers.temporal_context_retriever import router as temporal_router
from apps.api.routers.knowledge_extraction_pipeline import router as extraction_router
from apps.api.routers.memory_reinforcement_engine import router as reinforcement_router
from apps.api.routers.causal_memory_graph import router as causal_router, router as causal_graph_router
from apps.api.routers.semantic_search_engine import router as semantic_search_router
from apps.api.routers.encrypted_vector_store import router as encrypted_vector_router
from apps.api.routers.context_db import router as context_db_router
from apps.api.routers.real_context_routes import router as real_context_router
from apps.api.routers.real_engines_router import router as real_engines_router

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
    "v1_task_router",
    "v1_events_router",
    "mesh_events_router",
    "v1_resilience_router",
    "v1_collaboration_router",
    "v1_reflection_router",
    "consolidation_router",
    "temporal_router",
    "extraction_router",
    "reinforcement_router",
    "causal_router",
    "causal_graph_router",
    "semantic_search_router",
    "context_db_router",
    "encrypted_vector_router",
    "real_context_router",
]
