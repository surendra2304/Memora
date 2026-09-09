"""
End-to-End Verification Test for Autonomous Persistent Memory
Simulates user stating preferences, Memora extracting and storing them,
cross-session recall in new turns, and multi-agent memory ingestion.
"""
import sys
import os
import sqlite3
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.memory.pipeline.preference_extractor import PreferenceExtractor
from sdk.memora_client import memora_client

def run_e2e_test():
    print("=" * 80)
    print(">>> UNIVERSAL PERSISTENT MEMORY & AUTONOMOUS RECALL VERIFICATION")
    print("=" * 80)

    # -------------------------------------------------------------------------
    # TEST 1: Semantic Fact & User Preference Extraction
    # -------------------------------------------------------------------------
    print("\n[TEST 1] Testing Semantic Fact & Preference Extractor...")
    test_input = "I like prawns"
    facts = PreferenceExtractor.extract_facts(test_input)
    print(f"  Input text: '{test_input}'")
    assert len(facts) > 0, "Failed to extract facts from 'I like prawns'"
    fact = facts[0]
    print(f"  Extracted normalized fact: '{fact.normalized_fact}'")
    print(f"  Extracted entities: {fact.entities}")
    print(f"  Importance: {fact.importance}, Confidence: {fact.confidence}")
    assert "prawn" in fact.normalized_fact.lower(), "Fact missing 'prawn'"
    assert "curry" in fact.entities or "curry" in fact.normalized_fact.lower(), "Entities missing curry expansion"
    print("  [PASS] Test 1 Succeeded!")

    # -------------------------------------------------------------------------
    # TEST 2: Autonomous Turn Recording into Memora
    # -------------------------------------------------------------------------
    print("\n[TEST 2] Testing Autonomous Turn Recording...")
    turn_res = memora_client.record_interaction(
        agent_name="friday",
        user_input=test_input,
        agent_output="Noted! I'll remember that you love prawns.",
        event_type="dialogue"
    )
    print(f"  Record result: {turn_res}")
    assert turn_res.get("status") == "success", f"Recording failed: {turn_res}"
    print("  [PASS] Test 2 Succeeded!")

    # -------------------------------------------------------------------------
    # TEST 3: Cross-Session Semantic Recall
    # -------------------------------------------------------------------------
    print("\n[TEST 3] Testing Cross-Session Recall for 'What is my favourite curry?'...")
    query = "What is my favourite curry?"
    recalled = memora_client.recall_memories("friday", query, limit=5)
    print(f"  Query: '{query}'")
    print(f"  Recalled {len(recalled)} memories:")
    for idx, r in enumerate(recalled):
        print(f"    {idx + 1}. [{r.get('memory_type')}] {r.get('content_text')} (Score: {r.get('final_score')})")

    assert len(recalled) > 0, "No memories recalled for favourite curry"
    top_match = recalled[0]["content_text"]
    assert "prawn" in top_match.lower(), f"Top recalled memory doesn't mention prawns: '{top_match}'"
    print("  [PASS] Test 3 Succeeded!")

    # -------------------------------------------------------------------------
    # TEST 4: Context Block Generation for LLM Prompt Injection
    # -------------------------------------------------------------------------
    print("\n[TEST 4] Testing Context Prompt Block Generation...")
    context_block = memora_client.build_context_prompt("friday", query)
    print(f"  Generated Context Block:\n---\n{context_block}\n---")
    assert "[PERSISTENT LONG-TERM MEMORY (MEMORA)]" in context_block
    assert "prawns" in context_block.lower()
    print("  [PASS] Test 4 Succeeded!")

    # -------------------------------------------------------------------------
    # TEST 5: Multi-Agent Persistent Memory Recording
    # -------------------------------------------------------------------------
    print("\n[TEST 5] Testing Multi-Agent Memory Recording across Ecosystem...")
    # Forge
    forge_res = memora_client.record_interaction(
        agent_name="forge",
        user_input="Build microservice authentication module",
        agent_output="Implemented JWT RSA-256 verifier with 100% test pass rate",
        event_type="software_task"
    )
    print(f"  Forge recorded: {forge_res.get('status')}")
    assert forge_res.get("status") == "success"

    # Sentinel
    sentinel_res = memora_client.record_fact(
        agent_name="sentinel",
        fact_text="Sentinel Security Audit: Port 8003 zero-trust perimeter verified. 0 CVEs detected.",
        category="security",
        importance=0.9
    )
    print(f"  Sentinel recorded: {sentinel_res.get('status')}")
    assert sentinel_res.get("status") == "success"

    # Cortex
    cortex_res = memora_client.record_fact(
        agent_name="cortex",
        fact_text="Cortex Web Ops: High-intent lead from IP 203.0.113.44 qualified for Enterprise Tier.",
        category="telemetry",
        importance=0.85
    )
    print(f"  Cortex recorded: {cortex_res.get('status')}")
    assert cortex_res.get("status") == "success"

    print("  [PASS] Test 5 Succeeded!")

    print("\n" + "=" * 80)
    print(">>> ALL 5 END-TO-END VERIFICATION TESTS PASSED SUCCESSFULLY! <<<")
    print("=" * 80)

if __name__ == "__main__":
    run_e2e_test()
