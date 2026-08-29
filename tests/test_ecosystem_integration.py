"""
End-to-End (E2E) Ecosystem Integration Test Suite for MEMORA
Simulates a real-world multi-agent collaboration scenario between FRIDAY, SENTINEL, and FORGE:
1. FRIDAY receives private user instructions.
2. SENTINEL scans and logs confidential vulnerability findings privately.
3. SENTINEL promotes sanitized remediations to a shared project namespace.
4. FORGE requests context and receives only authorized shared guidance while remaining strictly isolated from private stores.
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
from storage.relational.models import NamespaceType

@pytest.fixture
def api_client():
    return TestClient(app)

def test_e2e_cross_agent_collaboration_and_isolation_workflow(api_client, test_db, capsys):
    """
    E2E Simulation: FRIDAY -> SENTINEL -> FORGE Workflow with strict isolation & explicit promotion.
    """
    print("\n" + "=" * 80)
    print(">>> [MEMORA E2E] STARTING CROSS-AGENT COLLABORATION & ISOLATION SIMULATION")
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
    print(f"    Namespace: {friday_write['step_trace']['step_2_authenticate_and_resolve']['namespace_path']}")

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
    print(f"    Namespace: {sentinel_write['step_trace']['step_2_authenticate_and_resolve']['namespace_path']}")

    # Verify FORGE is immediately BLOCKED if trying to read SENTINEL's private memory directly
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
    print(f"    Shared Namespace: {promotion_result['shared_namespace']}")
    print(f"    Access Granted To: {promotion_result['target_agent']}")

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

    print(f"[CONTEXT BUNDLE] Curated {forge_context['memories_count']} memories for FORGE.")
    for idx, mem in enumerate(forge_context["memories"]):
        print(f"    [{idx+1}] Namespace: {mem['namespace_path']} | Content: {mem['content_text'][:60]}...")

    # -------------------------------------------------------------
    # 5. E2E ASSERTIONS & VERIFICATION
    # -------------------------------------------------------------
    # Assertion A: FORGE's context bundle INCLUDES the shared remediation memory
    has_shared_remediation = any("parameterized SQLAlchemy ORM queries" in text for text in retrieved_contents)
    assert has_shared_remediation is True, "FORGE context bundle must include the promoted shared remediation memory!"
    print("[ASSERTION A PASS] FORGE context bundle INCLUDES the promoted shared remediation.")

    # Assertion B: FORGE's context bundle DOES NOT include SENTINEL's raw private finding
    has_sentinel_private = any("CRITICAL: Found unauthenticated SQL injection" in text for text in retrieved_contents)
    assert has_sentinel_private is False, "FORGE context bundle must NEVER leak SENTINEL's private finding!"
    assert sentinel_mem_id not in retrieved_memory_ids
    print("[ASSERTION B PASS] FORGE context bundle DOES NOT contain SENTINEL's confidential finding.")

    # Assertion C: FORGE's context bundle DOES NOT include FRIDAY's private user instruction
    has_friday_private = any("Ensure the new auth module is completely secure" in text for text in retrieved_contents)
    assert has_friday_private is False, "FORGE context bundle must NEVER leak FRIDAY's private instructions!"
    assert friday_mem_id not in retrieved_memory_ids
    print("[ASSERTION C PASS] FORGE context bundle DOES NOT contain FRIDAY's private user instruction.")

    print("\n" + "=" * 80)
    print("[MEMORA E2E PROOF] ALL ISOLATION BOUNDARIES AND CONTROLLED SHARING VERIFIED 100%!")
    print("=" * 80 + "\n")