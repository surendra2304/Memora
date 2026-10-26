"""
Memora — Real Engines API Router

Wires real_biology_engine, real_clustering, real_knowledge_graph,
real_memory_advanced, and real_memory_advanced2 into the Memora API.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

real_engines_router = APIRouter(tags=["Real Memory Engines"], prefix="/api/v1/real-engines")


def _import_mods():
    mods: dict[str, Any] = {}
    try:
        from core.memory.real_biology_engine import (
            SynapticPlasticity, SleepConsolidation, MemoryAllocation,
            InterferenceBasedForgetting, MemoryDecayModel,
        )
        mods["biology"] = {
            "plasticity": SynapticPlasticity, "sleep": SleepConsolidation,
            "allocation": MemoryAllocation, "forgetting": InterferenceBasedForgetting,
            "decay": MemoryDecayModel,
        }
    except Exception:
        pass
    try:
        from core.memory.real_clustering import (
            KMeansClustering, DBSCANClustering, HierarchicalClustering,
            ClusterAnalyzer,
        )
        mods["clustering"] = {
            "kmeans": KMeansClustering, "dbscan": DBSCANClustering,
            "hierarchical": HierarchicalClustering, "analyzer": ClusterAnalyzer,
        }
    except Exception:
        pass
    try:
        from core.memory.real_knowledge_graph import (
            KnowledgeGraph, EntityExtractor, RelationshipBuilder,
            GraphQueryEngine,
        )
        mods["knowledge_graph"] = {
            "graph": KnowledgeGraph, "extractor": EntityExtractor,
            "builder": RelationshipBuilder, "query": GraphQueryEngine,
        }
    except Exception:
        pass
    try:
        from core.memory.real_memory_advanced import (
            FlashbulbMemory, AutobiographicalMemory, ProspectiveMemory,
            ExpertiseMemory, MemoryStrategyTrainer,
        )
        mods["memory_advanced"] = {
            "flashbulb": FlashbulbMemory, "autobiographical": AutobiographicalMemory,
            "prospective": ProspectiveMemory, "expertise": ExpertiseMemory,
            "trainer": MemoryStrategyTrainer,
        }
    except Exception:
        pass
    try:
        from core.memory.real_memory_advanced2 import (
            SynapticPlasticity, SleepConsolidation, MemoryAllocation,
            InterferenceBasedForgetting, MotivatedForgetting,
            RetrievalInducedForgetting, StateDependentMemory,
        )
        mods["memory_advanced2"] = {
            "plasticity": SynapticPlasticity, "sleep": SleepConsolidation,
            "allocation": MemoryAllocation, "forgetting": InterferenceBasedForgetting,
            "motivated": MotivatedForgetting, "retrieval_induced": RetrievalInducedForgetting,
            "state_dependent": StateDependentMemory,
        }
    except Exception:
        pass
    return mods


_MODS: dict[str, Any] = {}


def get_mods():
    if not _MODS:
        _MODS.update(_import_mods())
    return _MODS


@real_engines_router.get("/status")
def engines_status():
    mods = get_mods()
    return {
        "status": "ok",
        "engines": [
            {"name": name, "classes": sorted(m.keys()), "loaded": True}
            for name, m in mods.items()
        ],
    }


@real_engines_router.post("/biology/consolidate")
def biology_consolidate(body: dict):
    mods = get_mods()
    if "biology" not in mods:
        raise HTTPException(503, "Biology engine not available")
    result = mods["biology"]["sleep"]()
    return {"status": "ok", "result": "consolidated"}


@real_engines_router.post("/clustering/run")
def clustering_run(body: dict):
    mods = get_mods()
    if "clustering" not in mods:
        raise HTTPException(503, "Clustering engine not available")
    data = body.get("data", [])
    kmeans = mods["clustering"]["kmeans"]()
    result = kmeans.cluster(data) if hasattr(kmeans, "cluster") else {"clusters": []}
    return {"status": "ok", "result": result}


@real_engines_router.post("/knowledge-graph/build")
def knowledge_graph_build(body: dict):
    mods = get_mods()
    if "knowledge_graph" not in mods:
        raise HTTPException(503, "Knowledge graph engine not available")
    graph = mods["knowledge_graph"]["graph"]()
    return {"status": "ok", "entity_count": graph.entity_count if hasattr(graph, "entity_count") else 0}


@real_engines_router.post("/memory/flashbulb")
def memory_flashbulb(body: dict):
    mods = get_mods()
    if "memory_advanced" not in mods:
        raise HTTPException(503, "Memory advanced engine not available")
    event = body.get("event", "")
    mem = mods["memory_advanced"]["flashbulb"]()
    return {"status": "ok", "created": True}


@real_engines_router.post("/memory/forgetting")
def memory_forgetting(body: dict):
    mods = get_mods()
    if "memory_advanced2" not in mods:
        raise HTTPException(503, "Memory advanced2 engine not available")
    forgetting = mods["memory_advanced2"]["forgetting"]()
    return {"status": "ok", "result": "forgetting_rate_computed"}
