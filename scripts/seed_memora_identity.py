"""
Seed script for Memora: Seeds identity-scoped memories and contracts for all 8 peer agents.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from storage.relational.session import SessionLocal
from storage.relational.models import MemoryRecord, MemoryType, LifecycleState
from core.identity.service import IdentityService
from adapters.ecosystem import EcosystemMemoryAdapter
from core.memory.service import MemoryService

def seed_database():
    db = SessionLocal()
    try:
        print("Seeding Memora with identity-scoped records across all 8 peer agents...")
        
        # 1. Register Agents
        agents = ["friday", "inference", "forge", "sentinel", "cortex", "intelx", "futuris", "stratex"]
        for a in agents:
            IdentityService.register_agent(db, name=a, role="worker" if a != "friday" else "supervisor")
        
        user_id = "user_surendra"

        # 2. Seed FRIDAY task memory
        friday_task = EcosystemMemoryAdapter.format_friday_task_memory(
            user_id=user_id,
            task_id="task_seed_001",
            task_summary="Orchestrated universe deployment across 8 micro-agents with 100% verified health.",
            idempotency_key="idemp_seed_friday_001"
        )
        MemoryService.create_memory(db, friday_task, actor_name="friday")

        # 3. Seed Inference deliberation
        inference_record = EcosystemMemoryAdapter.format_inference_deliberation(
            user_id=user_id,
            task_id="task_seed_001",
            reasoning_summary="ASTRA multi-agent synthesis resolved distributed consensus with 0.98 confidence.",
            idempotency_key="idemp_seed_inference_001"
        )
        MemoryService.create_memory(db, inference_record, actor_name="inference")

        # 4. Seed Forge build artifact
        forge_record = EcosystemMemoryAdapter.format_forge_build_artifact(
            user_id=user_id,
            task_id="task_seed_001",
            repo_name="Memora",
            commit_hash="46a53198abcdef",
            build_summary="Clean build with all 86 unit and integration test assertions green.",
            idempotency_key="idemp_seed_forge_001"
        )
        MemoryService.create_memory(db, forge_record, actor_name="forge")

        # 5. Seed Sentinel security finding
        sentinel_record = EcosystemMemoryAdapter.format_sentinel_audit_finding(
            user_id=user_id,
            task_id="task_seed_001",
            asset_id="memora_api_v1",
            severity="low",
            finding_details="Verified fail-closed security posture; zero secret leakage.",
            idempotency_key="idemp_seed_sentinel_001"
        )
        MemoryService.create_memory(db, sentinel_record, actor_name="sentinel")

        # 6. Seed Cortex research memory
        cortex_record = EcosystemMemoryAdapter.format_cortex_research_memory(
            user_id=user_id,
            topic="Hierarchical Neural Context Compaction",
            synthesized_findings="Information density preserved at 4.2x compaction ratio via cluster centroids.",
            citations=["arxiv:2403.12345", "nature:machine-intel-2026"],
            idempotency_key="idemp_seed_cortex_001"
        )
        MemoryService.create_memory(db, cortex_record, actor_name="cortex")

        # 7. Seed IntelX market signal
        intelx_record = EcosystemMemoryAdapter.format_intelx_market_signal(
            user_id=user_id,
            ticker="NVDA",
            signal_type="MOMENTUM_SURGE",
            signal_summary="Cluster compute order flow exceeds 90th percentile.",
            data_sources=["sec_filing:10-Q", "news_feed:reuters"],
            idempotency_key="idemp_seed_intelx_001"
        )
        MemoryService.create_memory(db, intelx_record, actor_name="intelx")

        # 8. Seed Futuris projection
        futuris_record = EcosystemMemoryAdapter.format_futuris_scenario_projection(
            user_id=user_id,
            scenario_id="SCN_COMPUTE_SCALING",
            horizon_days=90,
            projected_impact="Cluster throughput capacity remains optimal under high load.",
            idempotency_key="idemp_seed_futuris_001"
        )
        MemoryService.create_memory(db, futuris_record, actor_name="futuris")

        # 9. Seed Stratex roadmap
        stratex_record = EcosystemMemoryAdapter.format_stratex_strategy_roadmap(
            user_id=user_id,
            strategy_name="Universe_Resilience_2026",
            roadmap_summary="Enforce zero-trust peer communication, strict identity scopes, and multi-store convergence.",
            governing_rules=["rule:identity_isolation", "rule:multi_store_convergence"],
            idempotency_key="idemp_seed_stratex_001"
        )
        MemoryService.create_memory(db, stratex_record, actor_name="stratex")

        print("Seeding completed successfully!")
    finally:
        db.close()

if __name__ == "__main__":
    seed_database()
