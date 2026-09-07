"""
Comprehensive End-to-End System Test for Memora 2.0 (Deep Upgrade)
Simulates complete multi-tenant, multi-agent lifecycle operations directly
against the full application stack.
"""
import sys
import uuid
from pathlib import Path

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient

from apps.api.main import app
from storage.relational.session import SessionLocal
from storage.relational.models import (
    Agent,
    Namespace,
    NamespaceType,
    MemoryRecord,
    AccessGrant,
    AuditLog,
    DeletionTombstone,
)
from core.identity.service import IdentityService
from core.policy.engine import PolicyEngine
from core.memory.service import MemoryService
from core.memory.search_service import SearchService
from core.memory.context.builder import ContextBuilderService
from core.lifecycle.decay import MemoryDecayEngine

def run_e2e_test():
    print("=" * 80)
    print(">>> [MEMORA] EXECUTING COMPLETE END-TO-END SYSTEM TEST")
    print("=" * 80)
    
    db = SessionLocal()
    client = TestClient(app)
    
    # Isolated test run identity
    run_id = uuid.uuid4().hex[:6]
    tenant_a = f"tenant_alpha_{run_id}"
    tenant_b = f"tenant_beta_{run_id}"
    
    friday_name = f"friday_{run_id}"
    sentinel_name = f"sentinel_{run_id}"
    forge_name = f"forge_{run_id}"
    rogue_name = f"rogue_{run_id}"
    
    try:
        # -------------------------------------------------------------
        # STAGE 1: MULTI-TENANT IDENTITY & NAMESPACE INITIALIZATION
        # -------------------------------------------------------------
        print("\n[STAGE 1] MULTI-TENANT IDENTITY & NAMESPACE INITIALIZATION")
        friday = IdentityService.register_agent(db, friday_name, role="supervisor", tenant_id=tenant_a)
        sentinel = IdentityService.register_agent(db, sentinel_name, role="worker", tenant_id=tenant_a)
        forge = IdentityService.register_agent(db, forge_name, role="worker", tenant_id=tenant_a)
        
        # Sub-agent under Forge with bounded context
        subagent_scope = f"memora://{tenant_a}/{forge_name}/projects/quantum-auth"
        worker = IdentityService.register_subagent(
            db,
            parent_agent_name=forge_name,
            subagent_name="crypto-worker",
            bounded_scope=subagent_scope,
            tenant_id=tenant_a
        )
        
        # Tenant B Agent (Strict Isolation Target)
        rogue = IdentityService.register_agent(db, rogue_name, role="worker", tenant_id=tenant_b)
        
        # Namespaces
        ns_global = IdentityService.resolve_namespace(db, f"memora://{tenant_a}/universe/global", default_type=NamespaceType.UNIVERSE_GLOBAL, tenant_id=tenant_a)
        ns_sentinel_private = IdentityService.resolve_namespace(db, f"memora://{tenant_a}/{sentinel_name}/private", owner_agent_id=sentinel.id, tenant_id=tenant_a)
        ns_project = IdentityService.resolve_namespace(db, subagent_scope, owner_agent_id=forge.id, default_type=NamespaceType.TEAM_SHARED, tenant_id=tenant_a)
        
        # Grant Forge lead access to project namespace
        IdentityService.grant_access(
            db,
            agent_id=forge.id,
            namespace_id=ns_project.id,
            tenant_id=tenant_a,
            actions=["read", "write", "query"],
            purpose="Lead architect on quantum-auth project"
        )
        
        db.commit()
        print(f"  [+] Provisioned Tenant A ({tenant_a}) Agents: {friday.name} ({friday.id}), {sentinel.name}, {forge.name}, {worker.name}")
        print(f"  [+] Provisioned Tenant B ({tenant_b}) Agent: {rogue.name} ({rogue.id})")
        print(f"  [+] Namespaces created: global, sentinel-private, quantum-auth shared")

        # -------------------------------------------------------------
        # STAGE 2: PRE-PERSISTENCE SECRET SCANNING GATE
        # -------------------------------------------------------------
        print("\n[STAGE 2] PRE-PERSISTENCE SECURITY & SECRET SCANNING")
        resp_leak = client.post(
            "/v1/memories",
            json={
                "content_text": "Production credential leak: sk-proj-abc1234567890123456789012345678901234567890123456789012345",
                "target_namespace_path": ns_sentinel_private.path
            },
            headers={"X-Agent-Name": sentinel_name}
        )
        assert resp_leak.status_code == 422, f"Expected 422 Unprocessable for secret leak, got {resp_leak.status_code}"
        print(f"  [+] Secret scanning successfully intercepted raw API key leak (HTTP {resp_leak.status_code})")

        # -------------------------------------------------------------
        # STAGE 3: MEMORY INGESTION ACROSS SCOPES & TEMPORAL VALIDITY
        # -------------------------------------------------------------
        print("\n[STAGE 3] 10-STEP WRITE PIPELINE & TEMPORAL VALIDITY")
        now = datetime.now(timezone.utc)
        
        # 3a. Sentinel writes private vulnerability
        resp_m1 = client.post(
            "/v1/memories",
            json={
                "content_text": "CRITICAL: Zero-day elliptic curve timing vulnerability in ECDSA auth layer.",
                "target_namespace_path": ns_sentinel_private.path,
                "memory_type": "experience",
                "confidence": 0.98,
                "importance": 0.95
            },
            headers={"X-Agent-Name": sentinel_name}
        )
        assert resp_m1.status_code == 201, f"Failed resp_m1: {resp_m1.status_code} - {resp_m1.text}"
        m1_id = resp_m1.json()["id"]
        print(f"  [+] Sentinel logged private confidential vulnerability: {m1_id}")

        # 3b. Forge writes shared architecture specification with temporal bounds & entities
        resp_m2 = client.post(
            "/v1/memories",
            json={
                "content_text": "Quantum-resistant post-quantum cryptography upgrade specification with ML-KEM and Dilithium.",
                "target_namespace_path": ns_project.path,
                "memory_type": "decision",
                "confidence": 0.95,
                "importance": 0.90,
                "provenance": {"author": forge_name, "authority": 0.95}
            },
            headers={"X-Agent-Name": forge_name}
        )
        assert resp_m2.status_code == 201, f"Failed resp_m2: {resp_m2.status_code} - {resp_m2.text}"
        m2_id = resp_m2.json()["id"]
        print(f"  [+] Forge published shared project memory: {m2_id}")

        # 3c. Global commons guideline
        resp_m3 = client.post(
            "/v1/memories",
            json={
                "content_text": "Ecosystem coding guideline: enforce strict parameterization and zero hardcoded supervisor bypasses.",
                "target_namespace_path": ns_global.path,
                "memory_type": "procedural",
                "confidence": 1.0,
                "importance": 0.85
            },
            headers={"X-Agent-Name": friday_name}
        )
        assert resp_m3.status_code == 201, f"Failed resp_m3: {resp_m3.status_code} - {resp_m3.text}"
        m3_id = resp_m3.json()["id"]
        print(f"  [+] Global commons guideline created: {m3_id}")

        # -------------------------------------------------------------
        # STAGE 4: 5D POLICY ENGINE & ZERO-BYPASS DEFAULT DENY
        # -------------------------------------------------------------
        print("\n[STAGE 4] POLICY ENGINE EVALUATION & ACCESS CONTROL")
        
        # 4a. Cross-Tenant Attempt: Rogue agent from Tenant B tries to read Sentinel's private memory
        dec_cross = PolicyEngine.evaluate_access(db, rogue, ns_sentinel_private, "read")
        assert dec_cross.allowed is False
        assert dec_cross.rule_matched == "RULE_TENANT_MISMATCH"
        print(f"  [+] Strict Cross-Tenant isolation verified: {dec_cross.rule_matched} (Denied)")

        # 4b. Supervisor Bypass Check: Friday attempts to access Sentinel's private store without grant
        dec_sup = PolicyEngine.evaluate_access(db, friday, ns_sentinel_private, "read")
        assert dec_sup.allowed is False
        print(f"  [+] Supervisor loophole check: Friday without explicit grant is DENIED by default")

        # 4c. Forge attempts to access Sentinel private without promotion
        dec_forge = PolicyEngine.evaluate_access(db, forge, ns_sentinel_private, "read")
        assert dec_forge.allowed is False
        print(f"  [+] Peer agent without explicit grant is DENIED by default")

        # 4d. Explicit Promotion: Sentinel grants Forge read capability on remediation
        IdentityService.grant_access(
            db,
            agent_id=forge.id,
            namespace_id=ns_sentinel_private.id,
            tenant_id=tenant_a,
            actions=["read", "query"],
            purpose="Approved security audit remediation"
        )
        dec_promoted = PolicyEngine.evaluate_access(db, forge, ns_sentinel_private, "read")
        assert dec_promoted.allowed is True
        assert dec_promoted.rule_matched == "RULE_1_EXPLICIT_GRANT_ACCESS"
        print(f"  [+] Explicit promotion capability grant verified: {dec_promoted.rule_matched} (Allowed)")

        # -------------------------------------------------------------
        # STAGE 5: TRI-MODAL HYBRID SEARCH & RRF MERGE
        # -------------------------------------------------------------
        print("\n[STAGE 5] TRI-MODAL HYBRID SEARCH (VECTOR + KEYWORD + GRAPH)")
        search_hits = SearchService.hybrid_search(
            db,
            query_text="quantum cryptography ML-KEM upgrade specification",
            actor_name=forge_name,
            tenant_id=tenant_a,
            limit=5
        )
        assert len(search_hits) >= 1
        top_hit = search_hits[0]
        assert top_hit.record.id == m2_id
        print(f"  [+] Hybrid search matched top record: '{top_hit.record.content_text[:45]}...' (Score: {top_hit.final_score:.4f})")
        print(f"  [+] Match reasons: {top_hit.match_reasons}")

        # -------------------------------------------------------------
        # STAGE 6: CONTEXT BUNDLE ASSEMBLY & TOKEN BUDGETING
        # -------------------------------------------------------------
        print("\n[STAGE 6] CONTEXT BUNDLE ASSEMBLY & TOKEN BUDGETING")
        bundle = ContextBuilderService.build_context_bundle(
            db=db,
            agent_id_or_name=forge_name,
            task_query="quantum cryptography implementation",
            token_budget=2000,
            purpose="Security cryptography implementation"
        )
        assert len(bundle.memories) >= 1
        assert bundle.total_tokens_estimated <= 2000
        print(f"  [+] Context Bundle '{bundle.bundle_id[:8]}' generated with {len(bundle.memories)} memories ({bundle.total_tokens_estimated} tokens)")

        # -------------------------------------------------------------
        # STAGE 7: CURSOR-BASED BATCHED TIME DECAY
        # -------------------------------------------------------------
        print("\n[STAGE 7] CURSOR-BASED DECAY ENGINE WITH BOUNDED BATCHES")
        # Age one record artificially
        rec2 = db.query(MemoryRecord).filter(MemoryRecord.id == m2_id).first()
        rec2.created_at = now - timedelta(days=45)
        db.commit()

        decay_stats = MemoryDecayEngine.apply_time_decay(
            db,
            decay_rate_per_day=0.01,
            unverified_threshold_days=14,
            tenant_id=tenant_a,
            batch_size=5
        )
        assert decay_stats["evaluated_total"] >= 1
        print(f"  [+] Cursor decay executed: {decay_stats['evaluated_total']} evaluated, {decay_stats['decayed_count']} decayed, {decay_stats['archived_count']} archived")

        # -------------------------------------------------------------
        # STAGE 8: MULTI-STORE DURABLE HARD DELETION
        # -------------------------------------------------------------
        print("\n[STAGE 8] DURABLE HARD DELETE & TOMBSTONE CONVERGENCE")
        del_result = MemoryService.delete_memory(
            db,
            memory_id=m1_id,
            actor_name=sentinel_name,
            hard_delete=True
        )
        assert del_result["status"] in ["deleted", "converged", "hard_deleted"]
        
        # Verify SQL expunged
        assert db.query(MemoryRecord).filter(MemoryRecord.id == m1_id).first() is None
        
        # Verify Tombstone Convergence
        tombstone = db.query(DeletionTombstone).filter(DeletionTombstone.memory_id == m1_id).first()
        assert tombstone is not None
        assert tombstone.relational_deleted is True
        assert tombstone.vector_deleted is True
        print(f"  [+] Hard delete converged: SQL expunged, Vector point expunged, Tombstone verified ({tombstone.id[:8]})")

        # -------------------------------------------------------------
        # STAGE 9: AUDIT TRAIL VERIFICATION
        # -------------------------------------------------------------
        print("\n[STAGE 9] AUDIT TRAIL LOGGING INTEGRITY")
        audit_records = db.query(AuditLog).filter(AuditLog.tenant_id == tenant_a).all()
        assert len(audit_records) >= 3
        print(f"  [+] Verified {len(audit_records)} atomic audit records captured for tenant '{tenant_a}'")

        print("\n" + "=" * 80)
        print(">>> [SUCCESS] ALL 9 END-TO-END SYSTEM STAGES COMPLETED PERFECTLY")
        print("=" * 80)
        return True

    finally:
        # Clean up test artifacts
        try:
            db.query(AuditLog).filter(AuditLog.tenant_id.in_([tenant_a, tenant_b])).delete()
            db.query(DeletionTombstone).filter(DeletionTombstone.tenant_id.in_([tenant_a, tenant_b])).delete()
            db.query(MemoryRecord).filter(MemoryRecord.tenant_id.in_([tenant_a, tenant_b])).delete()
            db.query(AccessGrant).filter(AccessGrant.tenant_id.in_([tenant_a, tenant_b])).delete()
            db.query(Namespace).filter(Namespace.tenant_id.in_([tenant_a, tenant_b])).delete()
            db.query(Agent).filter(Agent.tenant_id.in_([tenant_a, tenant_b])).delete()
            db.commit()
        except Exception:
            db.rollback()
        db.close()

if __name__ == "__main__":
    success = run_e2e_test()
    if not success:
        sys.exit(1)
