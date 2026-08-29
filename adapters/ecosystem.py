"""
Ecosystem Ingestion Adapters for Memora
Transforms events from FRIDAY, FORGE, NEXUS into canonical memory records.
"""
from typing import Dict, Any, Optional
from core.memory.schemas import MemoryRecordCreate
from storage.relational.models import MemoryType, LifecycleState

class EcosystemMemoryAdapter:
    @staticmethod
    def format_episodic_event(
        agent_name: str,
        event_summary: str,
        source: str = "workflow",
        provenance: Optional[Dict[str, Any]] = None,
        confidence: float = 1.0,
        importance: float = 0.6
    ) -> MemoryRecordCreate:
        return MemoryRecordCreate(
            owner_name=agent_name,
            namespace_path=f"memora://{agent_name}/private",
            memory_type=MemoryType.EPISODIC,
            content_text=event_summary,
            source=source,
            provenance=provenance or {},
            confidence=confidence,
            importance=importance,
            lifecycle_state=LifecycleState.ACTIVE
        )

    @staticmethod
    def format_procedural_skill(
        agent_name: str,
        skill_name: str,
        procedure_text: str,
        provenance: Optional[Dict[str, Any]] = None
    ) -> MemoryRecordCreate:
        return MemoryRecordCreate(
            owner_name=agent_name,
            namespace_path="memora://universe/global",
            memory_type=MemoryType.PROCEDURAL,
            content_text=f"Skill [{skill_name}]: {procedure_text}",
            source="skill_registry",
            provenance=provenance or {"skill_name": skill_name},
            confidence=1.0,
            importance=0.85,
            lifecycle_state=LifecycleState.VERIFIED
        )