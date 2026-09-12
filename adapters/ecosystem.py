"""
Ecosystem Ingestion Adapters and Peer Contracts for Memora
Transforms and validates contract payloads across all 8 peer agents:
FRIDAY, Inference, Forge, Sentinel, Cortex, IntelX, Futuris, and Stratex.
"""
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from core.memory.schemas import MemoryRecordCreate, ProvenanceMetadata
from storage.relational.models import MemoryType, LifecycleState


class EcosystemMemoryAdapter:
    @staticmethod
    def format_episodic_event(
        agent_name: str,
        event_summary: str,
        user_id: str = "default_user",
        workspace_id: str = "default_workspace",
        task_id: Optional[str] = None,
        source: str = "workflow",
        provenance: Optional[Dict[str, Any]] = None,
        confidence: float = 1.0,
        importance: float = 0.6,
        idempotency_key: Optional[str] = None
    ) -> MemoryRecordCreate:
        prov = provenance or {}
        prov.setdefault("source", source)
        prov.setdefault("source_type", "agent_generated")
        prov.setdefault("trust_level", "candidate")
        prov.setdefault("created_by", agent_name)
        prov.setdefault("confidence", confidence)

        return MemoryRecordCreate(
            user_id=user_id,
            agent_id=agent_name,
            workspace_id=workspace_id,
            task_id=task_id,
            idempotency_key=idempotency_key,
            owner_name=agent_name,
            namespace_path=f"memora://{agent_name}/private",
            memory_type=MemoryType.EPISODIC,
            content_text=event_summary,
            source=source,
            provenance=prov,
            confidence=confidence,
            importance=importance,
            lifecycle_state=LifecycleState.ACTIVE
        )

    @staticmethod
    def format_procedural_skill(
        agent_name: str,
        skill_name: str,
        procedure_text: str,
        user_id: str = "default_user",
        workspace_id: str = "default_workspace",
        provenance: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None
    ) -> MemoryRecordCreate:
        prov = provenance or {}
        prov.setdefault("skill_name", skill_name)
        prov.setdefault("source", "skill_registry")
        prov.setdefault("source_type", "verified_fact")
        prov.setdefault("trust_level", "verified")
        prov.setdefault("created_by", agent_name)
        prov.setdefault("confidence", 1.0)

        return MemoryRecordCreate(
            user_id=user_id,
            agent_id=agent_name,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            owner_name=agent_name,
            namespace_path="memora://universe/global",
            memory_type=MemoryType.PROCEDURAL,
            content_text=f"Skill [{skill_name}]: {procedure_text}",
            source="skill_registry",
            provenance=prov,
            confidence=1.0,
            importance=0.85,
            lifecycle_state=LifecycleState.VERIFIED
        )

    # -------------------------------------------------------------------------
    # PEER AGENT CONTRACT BUILDERS (8 FRIDAY UNIVERSE AGENTS)
    # -------------------------------------------------------------------------

    @classmethod
    def format_friday_task_memory(
        cls,
        user_id: str,
        task_id: str,
        task_summary: str,
        workspace_id: str = "default_workspace",
        source: str = "friday_orchestrator",
        idempotency_key: Optional[str] = None
    ) -> MemoryRecordCreate:
        """FRIDAY Central OS: Master user task execution record."""
        return MemoryRecordCreate(
            user_id=user_id,
            agent_id="friday",
            workspace_id=workspace_id,
            task_id=task_id,
            idempotency_key=idempotency_key or f"friday_{task_id}",
            owner_name="friday",
            namespace_path="memora://friday/tasks",
            memory_type=MemoryType.EPISODIC,
            content_text=f"Task [{task_id}] Executed: {task_summary}",
            source=source,
            provenance={
                "source": source,
                "source_type": "user_input",
                "trust_level": "candidate",
                "evidence_refs": [f"task://{task_id}"],
                "created_by": "friday",
                "confidence": 1.0
            },
            confidence=1.0,
            importance=0.8,
            lifecycle_state=LifecycleState.ACTIVE
        )

    @classmethod
    def format_inference_deliberation(
        cls,
        user_id: str,
        task_id: str,
        reasoning_summary: str,
        confidence: float = 0.95,
        evidence_refs: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None
    ) -> MemoryRecordCreate:
        """Inference & ASTRA: Reasoning deliberation and consultation summary."""
        return MemoryRecordCreate(
            user_id=user_id,
            agent_id="inference",
            task_id=task_id,
            idempotency_key=idempotency_key or f"inference_{task_id}",
            owner_name="inference",
            namespace_path="memora://inference/deliberations",
            memory_type=MemoryType.DECISION,
            content_text=f"ASTRA Deliberation for {task_id}: {reasoning_summary}",
            source="inference:astra",
            provenance={
                "source": "inference:astra",
                "source_type": "model_output",
                "trust_level": "candidate",
                "evidence_refs": evidence_refs or [f"trace://{task_id}"],
                "created_by": "inference",
                "confidence": confidence
            },
            confidence=confidence,
            importance=0.85,
            lifecycle_state=LifecycleState.ACTIVE
        )

    @classmethod
    def format_forge_build_artifact(
        cls,
        user_id: str,
        task_id: str,
        repo_name: str,
        commit_hash: str,
        build_summary: str,
        idempotency_key: Optional[str] = None
    ) -> MemoryRecordCreate:
        """Forge: Code generation and build verification artifact."""
        return MemoryRecordCreate(
            user_id=user_id,
            agent_id="forge",
            task_id=task_id,
            idempotency_key=idempotency_key or f"forge_{commit_hash[:12]}",
            owner_name="forge",
            namespace_path=f"memora://forge/{repo_name}",
            memory_type=MemoryType.PROCEDURAL,
            content_text=f"Build artifact for {repo_name}@{commit_hash[:8]}: {build_summary}",
            source="forge:build_runner",
            provenance={
                "source": "forge:build_runner",
                "source_type": "tool_output",
                "trust_level": "verified",
                "evidence_refs": [f"git://{repo_name}#{commit_hash}"],
                "created_by": "forge",
                "confidence": 1.0
            },
            confidence=1.0,
            importance=0.9,
            lifecycle_state=LifecycleState.VERIFIED
        )

    @classmethod
    def format_sentinel_audit_finding(
        cls,
        user_id: str,
        task_id: str,
        asset_id: str,
        severity: str,
        finding_details: str,
        idempotency_key: Optional[str] = None
    ) -> MemoryRecordCreate:
        """Sentinel: Security audit finding and vulnerability record."""
        return MemoryRecordCreate(
            user_id=user_id,
            agent_id="sentinel",
            task_id=task_id,
            idempotency_key=idempotency_key or f"sentinel_{asset_id}_{severity.lower()}",
            owner_name="sentinel",
            namespace_path="memora://sentinel/private",
            memory_type=MemoryType.EXPERIENCE,
            content_text=f"[{severity.upper()}] Security finding on {asset_id}: {finding_details}",
            source="sentinel:security_auditor",
            provenance={
                "source": "sentinel:security_auditor",
                "source_type": "verified_fact",
                "trust_level": "verified",
                "evidence_refs": [f"asset://{asset_id}"],
                "created_by": "sentinel",
                "confidence": 0.99
            },
            confidence=0.99,
            importance=0.95,
            lifecycle_state=LifecycleState.ACTIVE
        )

    @classmethod
    def format_cortex_research_memory(
        cls,
        user_id: str,
        topic: str,
        synthesized_findings: str,
        citations: List[str],
        idempotency_key: Optional[str] = None
    ) -> MemoryRecordCreate:
        """Cortex: Deep scientific research and multi-modal knowledge extraction."""
        return MemoryRecordCreate(
            user_id=user_id,
            agent_id="cortex",
            idempotency_key=idempotency_key,
            owner_name="cortex",
            namespace_path="memora://cortex/research",
            memory_type=MemoryType.WORKING,
            content_text=f"Research on [{topic}]: {synthesized_findings}",
            source="cortex:knowledge_engine",
            provenance={
                "source": "cortex:knowledge_engine",
                "source_type": "agent_generated",
                "trust_level": "candidate",
                "evidence_refs": citations,
                "created_by": "cortex",
                "confidence": 0.90
            },
            confidence=0.90,
            importance=0.80,
            lifecycle_state=LifecycleState.ACTIVE
        )

    @classmethod
    def format_intelx_market_signal(
        cls,
        user_id: str,
        ticker: str,
        signal_type: str,
        signal_summary: str,
        data_sources: List[str],
        idempotency_key: Optional[str] = None
    ) -> MemoryRecordCreate:
        """IntelX: Real-time intelligence feed and telemetry observation."""
        return MemoryRecordCreate(
            user_id=user_id,
            agent_id="intelx",
            idempotency_key=idempotency_key,
            owner_name="intelx",
            namespace_path=f"memora://intelx/signals/{ticker.lower()}",
            memory_type=MemoryType.EPISODIC,
            content_text=f"Market signal [{signal_type}] for {ticker}: {signal_summary}",
            source="intelx:stream",
            provenance={
                "source": "intelx:stream",
                "source_type": "tool_output",
                "trust_level": "candidate",
                "evidence_refs": data_sources,
                "created_by": "intelx",
                "confidence": 0.88
            },
            confidence=0.88,
            importance=0.75,
            lifecycle_state=LifecycleState.ACTIVE
        )

    @classmethod
    def format_futuris_scenario_projection(
        cls,
        user_id: str,
        scenario_id: str,
        horizon_days: int,
        projected_impact: str,
        confidence: float = 0.85,
        idempotency_key: Optional[str] = None
    ) -> MemoryRecordCreate:
        """Futuris: Predictive horizon projection and probability distribution."""
        return MemoryRecordCreate(
            user_id=user_id,
            agent_id="futuris",
            idempotency_key=idempotency_key or f"futuris_{scenario_id}",
            owner_name="futuris",
            namespace_path="memora://futuris/scenarios",
            memory_type=MemoryType.WORKING,
            content_text=f"Projection for scenario [{scenario_id}] ({horizon_days}d): {projected_impact}",
            source="futuris:model",
            provenance={
                "source": "futuris:model",
                "source_type": "model_output",
                "trust_level": "candidate",
                "evidence_refs": [f"scenario://{scenario_id}"],
                "created_by": "futuris",
                "confidence": confidence
            },
            confidence=confidence,
            importance=0.82,
            lifecycle_state=LifecycleState.ACTIVE
        )

    @classmethod
    def format_stratex_strategy_roadmap(
        cls,
        user_id: str,
        strategy_name: str,
        roadmap_summary: str,
        governing_rules: List[str],
        idempotency_key: Optional[str] = None
    ) -> MemoryRecordCreate:
        """Stratex: High-level strategic roadmap and execution policy."""
        return MemoryRecordCreate(
            user_id=user_id,
            agent_id="stratex",
            idempotency_key=idempotency_key or f"stratex_{strategy_name.lower()}",
            owner_name="stratex",
            namespace_path="memora://stratex/strategies",
            memory_type=MemoryType.PROCEDURAL,
            content_text=f"Strategy Roadmap [{strategy_name}]: {roadmap_summary}",
            source="stratex:planner",
            provenance={
                "source": "stratex:planner",
                "source_type": "verified_fact",
                "trust_level": "verified",
                "evidence_refs": governing_rules,
                "created_by": "stratex",
                "confidence": 0.98
            },
            confidence=0.98,
            importance=0.92,
            lifecycle_state=LifecycleState.VERIFIED
        )