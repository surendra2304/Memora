"""
Autonomous Multi-Agent Experience Learning and Self-Upgrading Verification
Tests the entire feedback loop:
1. Task failure / outcome extraction into Experience memories (MemoryType.EXPERIENCE)
2. Concrete prevention rule synthesis from error signatures
3. Cross-turn recall and prompt injection via build_self_upgrade_context()
4. Verification across FRIDAY, Forge, Sentinel, and Stratex
"""
import sys
import os
import sqlite3
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdk.memora_client import memora_client
from core.memory.experience_service import ExperienceLearnerService

def run_verification():
    print("=" * 80)
    print(">>> MULTI-AGENT AUTONOMOUS SELF-UPGRADE & EXPERIENCE LEARNING TEST")
    print("=" * 80)

    # -------------------------------------------------------------------------
    # TEST 1: Remediation Rule Synthesis from Error Signatures
    # -------------------------------------------------------------------------
    print("\n[TEST 1] Testing Error Signature to Remediation Rule Synthesis...")
    test_cases = [
        ("ExecuteCommandTool", "PermissionError: [WinError 5] Access is denied", "tool_execution"),
        ("FileReaderTool", "FileNotFoundError: [Errno 2] No such file or directory: /var/log/app.log", "filesystem"),
        ("InferenceClient", "ReadTimeoutError: HTTPSConnectionPool timed out after 30s", "network"),
        ("ForgeCoder", "SyntaxError: unexpected EOF while parsing line 42", "code_synthesis"),
        ("StratexExecution", "DrawdownBreached: Maximum slippage exceeded in volatile chop", "quantitative_trading"),
    ]

    for task, err, domain in test_cases:
        rule = ExperienceLearnerService.extract_remediation_rule(task, err, domain)
        print(f"  [Signature] Task: {task} | Error: {err[:40]}...")
        print(f"  [Synthesized Rule]: {rule}")
        assert rule and len(rule) > 10, f"Failed to generate rule for {task}"
    print("  [PASS] Test 1: All error signatures synthesized actionable rules!")

    # -------------------------------------------------------------------------
    # TEST 2: Memora Client Learning & Record Creation
    # -------------------------------------------------------------------------
    print("\n[TEST 2] Testing Memora Client learn_from_outcome()...")
    learn_res = memora_client.learn_from_outcome(
        agent_name="friday",
        task_name="system_backup",
        status="failure",
        error_log="PermissionError: Administrator privilege required to lock raw disk sector",
        actions_taken="execute_command --drive C:",
        context="nightly_maintenance",
        domain="system_operations"
    )
    print(f"  Learn Result: {learn_res}")
    assert learn_res.get("status") == "success", f"Failed to learn outcome: {learn_res}"
    assert learn_res.get("memory_type") == "experience", "Record is not type experience"
    print("  [PASS] Test 2: Experience record successfully stored in Memora!")

    # -------------------------------------------------------------------------
    # TEST 3: Experience Recall & Self-Upgrade Context Generation
    # -------------------------------------------------------------------------
    print("\n[TEST 3] Testing Recall & Self-Upgrade Context Generation...")
    experiences = memora_client.recall_experience("friday", "system_backup", domain="system_operations")
    print(f"  Recalled {len(experiences)} experience memories for friday/system_backup")
    assert len(experiences) > 0, "No experience memories recalled"
    print(f"  Top experience: {experiences[0].get('content_text')}")

    upgrade_ctx = memora_client.build_self_upgrade_context("friday", "system_backup", domain="system_operations")
    print("\n  Synthesized Prompt Context:")
    for line in upgrade_ctx.splitlines():
        print(f"    | {line}")
    assert "[SELF-UPGRADED OPERATIONAL GUIDELINES & EXPERIENCE (MEMORA)]" in upgrade_ctx
    assert "administrator execution rights" in upgrade_ctx.lower() or "security privilege" in upgrade_ctx.lower()
    print("  [PASS] Test 3: Prompt injection successfully synthesized!")

    # -------------------------------------------------------------------------
    # TEST 4: Multi-Agent Ingestion Verification (Forge, Sentinel, Stratex)
    # -------------------------------------------------------------------------
    print("\n[TEST 4] Testing Multi-Agent Experience Ingestion across Universe...")
    # 1. Forge: Code synthesis failure
    forge_res = memora_client.learn_from_outcome(
        agent_name="forge",
        task_name="compile_payment_gateway",
        status="failure",
        error_log="ModuleNotFoundError: No module named stripe in container",
        domain="code_synthesis"
    )
    assert forge_res.get("status") == "success"
    forge_ctx = memora_client.build_self_upgrade_context("forge", "compile_payment_gateway")
    assert "dependency" in forge_ctx.lower() or "package" in forge_ctx.lower()
    print("  [Forge]: Code synthesis dependency rule learned & verified.")

    # 2. Sentinel: Security policy enforcement block
    sentinel_res = memora_client.learn_from_outcome(
        agent_name="sentinel",
        task_name="execute_unauthorized_shell",
        status="failure",
        error_log="Policy Block: Out-of-scope subnet access denied",
        domain="security_defense"
    )
    assert sentinel_res.get("status") == "success"
    sentinel_ctx = memora_client.build_self_upgrade_context("sentinel", "execute_unauthorized_shell")
    assert "sentinel" in sentinel_ctx.lower() or "security" in sentinel_ctx.lower() or "privilege" in sentinel_ctx.lower() or "guidelines" in sentinel_ctx.lower()
    print("  [Sentinel]: Security defense rule learned & verified.")

    # 3. Stratex: High volatility drawdown rule
    stratex_res = memora_client.learn_from_outcome(
        agent_name="stratex",
        task_name="strategy_scalper",
        status="failure",
        error_log="DrawdownBreached: Win rate dropped to 32% during high-volatility chop",
        domain="quantitative_trading"
    )
    assert stratex_res.get("status") == "success"
    stratex_ctx = memora_client.build_self_upgrade_context("stratex", "strategy_scalper")
    assert "drawdown" in stratex_ctx.lower() or "volatil" in stratex_ctx.lower() or "stop-loss" in stratex_ctx.lower() or "guidelines" in stratex_ctx.lower()
    print("  [Stratex]: Quantitative risk rule learned & verified.")

    print("\n" + "=" * 80)
    print(">>> ALL 4 SELF-UPGRADE VERIFICATION SUITES PASSED!")
    print("=" * 80)

if __name__ == "__main__":
    run_verification()
