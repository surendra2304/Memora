"""
Memora Upgrade Package
Deep upgrade reference architecture components for tenant isolation, hybrid retrieval,
temporal reasoning, deterministic identity, and lifecycle management.
"""
from .models import (
    MemoryItem,
    MemoryKind,
    MemoryScope,
    Provenance,
    TemporalBounds,
    Lifecycle,
    ScopeKind,
    QueryContext,
    RetrievalCandidate,
    RetrievalResult,
    MemoryEvent,
    token_estimate,
    utcnow,
)
from .policy import MemoryPolicy, AccessGrant, PolicyDecision
from .ingestion import IngestionService, IngestionPolicy
from .retrieval import HybridRetriever, InMemoryVectorIndex
from .store import MemoryStore, StoreStats
from .security import SecretScanner, SecretRedactor
from .graph import MemoryGraph, Edge
from .forgetting import ForgettingEngine, ForgettingPolicy
from .consolidation import Consolidator, ConsolidationConfig, TemporalResolver
from .context import ContextAssembler, ContextBundle, ContextItem
from .cache import TenantScopedCache
from .observability import Metrics
from .service import MemoraEngine

__all__ = [
    "MemoryItem",
    "MemoryKind",
    "MemoryScope",
    "Provenance",
    "TemporalBounds",
    "Lifecycle",
    "ScopeKind",
    "QueryContext",
    "RetrievalCandidate",
    "RetrievalResult",
    "MemoryEvent",
    "token_estimate",
    "utcnow",
    "MemoryPolicy",
    "AccessGrant",
    "PolicyDecision",
    "IngestionService",
    "IngestionPolicy",
    "HybridRetriever",
    "InMemoryVectorIndex",
    "MemoryStore",
    "StoreStats",
    "SecretScanner",
    "SecretRedactor",
    "MemoryGraph",
    "Edge",
    "ForgettingEngine",
    "ForgettingPolicy",
    "Consolidator",
    "ConsolidationConfig",
    "TemporalResolver",
    "ContextAssembler",
    "ContextBundle",
    "ContextItem",
    "TenantScopedCache",
    "Metrics",
    "MemoraEngine",
]
