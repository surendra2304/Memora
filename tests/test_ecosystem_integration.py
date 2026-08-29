"""
End-to-End (E2E) Ecosystem Integration Test Suite for MEMORA
Simulates a real-world multi-agent collaboration scenario between FRIDAY, SENTINEL, and FORGE:
1. FRIDAY receives private user instructions.
2. SENTINEL scans and logs confidential vulnerability findings privately.
3. SENTINEL promotes sanitized remediations to a shared project namespace.
4. FORGE requests context and receives only authorized shared guidance while remaining strictly isolated from private stores.
5. FORGE encounters a deployment failure and saves it as MemoryType.EXPERIENCE via learn-experience API.
6. FORGE requests context for a new similar deployment -> predictive context pre-fetches the failure mode to Rank #1 within token budget.
7. Observability metrics verify LLM compaction, neural cross-encoder latency, and predictive hits.
"""
import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from adapters.adapter_registry import adapter_registry
from adapters.friday.adapter import FridayAdapter
from adapters.sentinel.adapter import SentinelAdapter
from adapters.forge.adapter import ForgeAdapter
from adapters.base_adapter import MemoraAccessDeniedError
from core.identity.service import IdentityService
from core.metrics.collector import metrics_collector
from storage.relational.models import NamespaceType

@pytest.fixture
def api_client():
    return TestClient(app)

def test_e2e_cross_agent_collaboration_and_isolation_workflow(api_client, test_db, capsys):
    """
    E2E Simulation: FRIDAY -> SENTINEL -> FORGE Workflow with strict isolation, explicit promotion,
    and Phase 6 Experience Learning, Predictive Pre-Fetching & Neural Reranking.
    """
    print("\n" + "=" * 80)
    print(">>> [MEMORA E2E] STARTING FULL ECOSYSTEM WORKFLOW & PHASE 6 VALIDATION")
    print("=" * 80)

    # -------------------------------------------------------------
    # 1. SETUP & INITIALIZATION
    # -------------------------------------------------------------
    friday_adapter: FridayAdapter = adapter_registry.get_adapter("friday", http_client=api_client)
    sentinel_adapter: SentinelAdapter = adapter_registry.get_adapter("sentinel", http_client=api_client)
    forge_adapter: ForgeAdapter = adapter_registry.get_adapter("forge", http_client=api_client)

    assert friday_adapter.agent_name == "friday"
    assert sentinel_adapter.agent_name == "sentinel"
    assert forge_adapter.agent_name == "forge"
    print("[SETUP] Initialized specialized adapters for FRIDAY, SENTINEL, and FORGE.")

    # -------------------------------------------------------------
    # ACTION 1: FRIDAY WRITES PRIVATE USER INSTRUCTION
    # -------------------------------------------------------------
    friday_instruction = "Ensure the new auth module is completely secure against all external attack vectors."
    friday_write = friday_adapter.save_user_preference(
        preference_text=friday_instruction,
        confidence=1.0,
        importance=0.95
    )
    friday_mem_id = friday_write["id"]
    print(f"[ACTION 1 - FRIDAY] Recorded private executive directive (ID: {friday_mem_id})")

    # -------------------------------------------------------------
    # ACTION 2: SENTINEL SCANS & WRITES CONFIDENTIAL VULNERABILITY FINDING
    # -------------------------------------------------------------
    sentinel_finding = "CRITICAL: Found unauthenticated SQL injection vulnerability in auth.py user login query."
    sentinel_write = sentinel_adapter.record_private_finding(
        finding_details=sentinel_finding,
        asset_id="auth-module",
        severity="CRITICAL"
    )
    sentinel_mem_id = sentinel_write["id"]
    print(f"[ACTION 2 - SENTINEL] Logged confidential security finding (ID: {sentinel_mem_id})")

    # Verify FORGE is immediately BLOCKED if trying to write to SENTINEL's private memory
    with pytest.raises(MemoraAccessDeniedError):
        forge_adapter.write_memory(
            content_text="Tampering attempt",
            target_namespace_path="memora://sentinel/private"
        )
    print("[POLICY GATE] Verified FORGE is strictly blocked from SENTINEL's private store (403 Forbidden).")

    # -------------------------------------------------------------
    # ACTION 3: SENTINEL EXPLICIT PROMOTION TO SHARED PROJECT STORE
    # -------------------------------------------------------------
    sanitized_remediation = "SECURITY REMEDIATION: Implement parameterized SQLAlchemy ORM queries and Argon2id hashing for auth-module."
    promotion_result = sentinel_adapter.publish_approved_remediation(
        remediation_text=sanitized_remediation,
        project_id="auth-module",
        private_finding_id=sentinel_mem_id,
        target_agent="forge"
    )
    shared_mem_id = promotion_result["memory_id"]
    print(f"[ACTION 3 - SENTINEL] Promoted sanitized remediation to shared project store (ID: {shared_mem_id})")

    # -------------------------------------------------------------
    # ACTION 4: FORGE REQUESTS CONTEXT BUNDLE FOR THE PROJECT
    # -------------------------------------------------------------
    print("[ACTION 4 - FORGE] Requesting Context Bundle for auth-module implementation...")
    forge_context = forge_adapter.get_context(
        task_query="build secure auth-module database login queries and hashing",
        token_budget=4000
    )

    retrieved_memory_ids = [m["id"] for m in forge_context["memories"]]
    retrieved_contents = [m["content_text"] for m in forge_context["memories"]]

    # Assertions on isolation and promotion
    has_shared_remediation = any("parameterized SQLAlchemy ORM queries" in text for text in retrieved_contents)
    assert has_shared_remediation is True, "FORGE context bundle must include the promoted shared remediation memory!"
    
    has_sentinel_private = any("CRITICAL: Found unauthenticated SQL injection" in text for text in retrieved_contents)
    assert has_sentinel_private is False, "FORGE context bundle must NEVER leak SENTINEL's private finding!"
    assert sentinel_mem_id not in retrieved_memory_ids

    has_friday_private = any("Ensure the new auth module is completely secure" in text for text in retrieved_contents)
    assert has_friday_private is False, "FORGE context bundle must NEVER leak FRIDAY's private instructions!"
    assert friday_mem_id not in retrieved_memory_ids
    print("[ASSERTION PASS] Verified Rule 1 (Private by Default) and Rule 2 (Explicit Promotion) isolation.")

    # -------------------------------------------------------------
    # ACTION 5: FORGE ENCOUNTERS DEPLOYMENT FAILURE -> SAVES AS EXPERIENCE
    # -------------------------------------------------------------
    print("[ACTION 5 - FORGE] Logging past deployment failure to learn-experience...")
    learn_resp = api_client.post(
        "/v1/memories/learn-experience",
        json={
            "agent_id": "forge",
            "namespace_path": "memora://forge/private",
            "outcomes": [
                {
                    "task_name": "kubernetes-staging-rollout",
                    "status": "failure",
                    "domain": "deployment",
                    "error_log": "CrashLoopBackOff: missing database migration on auth_tokens table before pod rollout.",
                    "actions_taken": "helm upgrade --install auth-service ./chart",
                    "context": "Staging Cluster deployment"
                }
            ]
        },
        headers={"X-Agent-Name": "forge"}
    )
    assert learn_resp.status_code == 201
    exp_data = learn_resp.json()
    assert exp_data["memory_type"] == "experience"
    print(f"[EXPERIENCE LEARNED] Stored Failure Mode Warning (ID: {exp_data['id']})")

    # -------------------------------------------------------------
    # ACTION 6: FORGE STARTS NEW DEPLOYMENT -> REQUESTS CONTEXT
    # -------------------------------------------------------------
    print("[ACTION 6 - FORGE] Requesting deployment context bundle...")
    deploy_context_resp = api_client.post(
        "/v1/context",
        json={
            "task_query": "Deploy auth-module to production cluster",
            "token_budget": 4000
        },
        headers={"X-Agent-Name": "forge"}
    )
    assert deploy_context_resp.status_code == 200
    deploy_bundle = deploy_context_resp.json()

    # -------------------------------------------------------------
    # 7. PHASE 6 E2E ASSERTIONS & OBSERVABILITY METRICS
    # -------------------------------------------------------------
    # Assertion 1: Total tokens within budget
    assert deploy_bundle["total_tokens_estimated"] <= 4000, "Context bundle must fit within 4000 token budget!"
    
    # Assertion 2: Context Bundle includes the past failure experience
    deploy_memories = deploy_bundle["memories"]
    assert len(deploy_memories) >= 1
    
    # Assertion 3: Cross-encoder and experience multiplier placed the failure mode at Rank #1
    top_mem = deploy_memories[0]
    assert top_mem["memory_type"] == "experience" or "Failure Mode Warning" in top_mem["content_text"]
    assert "migration" in top_mem["content_text"].lower()
    print(f"[ASSERTION PASS] Experience lesson placed at Rank #1: {top_mem['content_text'][:60]}...")

    # Assertion 4: Observability metrics verify Phase 6 metrics
    metrics = metrics_collector.get_metrics_summary()
    assert "llm_compaction_tokens_saved" in metrics
    assert "cross_encoder_reranking_latency_ms" in metrics
    assert "predictive_context_hits" in metrics
    assert metrics["predictive_context_hits"] >= 1
    assert metrics_collector.get_prometheus_format().find("memora_predictive_context_hits") != -1
    print(f"[METRICS PASS] Observability Metrics: predictive_hits={metrics['predictive_context_hits']}, cross_encoder_latency={metrics['cross_encoder_reranking_latency_ms']}ms.")

    print("\n" + "=" * 80)
    print("[MEMORA E2E PROOF] ALL MULTI-AGENT, EXPERIENCE & PHASE 6 INVARIANTS VERIFIED 100%!")
    print("=" * 80 + "\n")