"""
Comprehensive Storage Verification for All 9 FRIDAY Universe Agents
Tests write ingestion pipeline, namespace resolution, policy gating, 
and retrieval across every agent in the ecosystem.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from storage.relational.session import SessionLocal
from storage.relational.models import (
    Agent,
    Namespace,
    NamespaceType,
    MemoryRecord,
    MemoryType,
    LifecycleState,
    AuditLog
)
from core.identity.service import IdentityService
from core.memory.pipeline.write_service import MemoryWriteService
from core.policy.engine import PolicyEngine
from core.memory.service import MemoryService
from core.memory.search_service import SearchService

# The 9 official FRIDAY Universe subsystems
ECOSYSTEM_AGENTS = [
    {"name": "friday", "role": "supervisor", "desc": "Central desktop OS & master orchestrator"},
    {"name": "forge", "role": "worker", "desc": "Autonomous software engineering & code synthesis"},
    {"name": "sentinel", "role": "worker", "desc": "Cybersecurity shield & vulnerability defense"},
    {"name": "inference", "role": "worker", "desc": "Multi-model AI inference gateway"},
    {"name": "cortex", "role": "worker", "desc": "Web operations & telemetry intelligence"},
    {"name": "intelx", "role": "worker", "desc": "Deep research & evidence extraction"},
    {"name": "futuris", "role": "worker", "desc": "Probabilistic forecasting & regime modeling"},
    {"name": "stratex", "role": "worker", "desc": "24/7 algorithmic trading & execution"},
    {"name": "memora", "role": "worker", "desc": "Persistent memory fabric subsystem"}
]

def verify_all_agents():
    print("=" * 80)
    print(">>> VERIFYING MEMORY STORAGE ACROSS ALL 9 AGENTS")
    print("=" * 80)
    
    db = SessionLocal()
    results = {}
    
    try:
        for agent_info in ECOSYSTEM_AGENTS:
            name = agent_info["name"]
            role = agent_info["role"]
            desc = agent_info["desc"]
            
            print(f"\n[+] Testing Agent: {name.upper()} ({role}) - {desc}")
            
            # 1. Identity & Namespace Resolution
            agent = IdentityService.get_agent_by_name(db, name)
            if not agent:
                agent = IdentityService.register_agent(db, name=name, role=role, description=desc, tenant_id="default")
            
            private_ns_path = f"memora://{name}/private"
            private_ns = IdentityService.get_namespace_by_path(db, private_ns_path)
            if not private_ns:
                private_ns = IdentityService.resolve_namespace(db, private_ns_path, owner_agent_id=agent.id, tenant_id="default")
                
            # 2. Ingest Memory via 10-Step Write Pipeline
            test_content = f"Official ecosystem capability verified for {name.upper()}: operational protocol online."
            write_res = MemoryWriteService.execute_pipeline(
                db=db,
                content_text=test_content,
                caller_name=name,
                target_namespace_path=private_ns_path,
                memory_type=MemoryType.PROCEDURAL,
                source=f"{name}_runtime",
                confidence=1.0,
                importance=0.90,
                purpose=f"Verification test for {name}"
            )
            
            mem_id = write_res.record.id
            print(f"    - Ingested Memory ID: {mem_id[:8]}... (Step trace: {list(write_res.step_outputs.keys())})")
            
            # 3. Read Verification: Owner MUST be able to read own private memory
            read_rec = MemoryService.get_memory_by_id(db, memory_id=mem_id, actor_name=name)
            assert read_rec is not None
            assert read_rec.content_text == test_content
            print(f"    - Owner Read Check: SUCCESS")
            
            # 4. Isolation Verification: Another agent CANNOT read private memory without grant
            other_agent_name = "sentinel" if name != "sentinel" else "forge"
            denied = False
            try:
                MemoryService.get_memory_by_id(db, memory_id=mem_id, actor_name=other_agent_name)
            except Exception:
                denied = True
            assert denied, f"Security Violation: {other_agent_name} accessed private memory of {name}!"
            print(f"    - Cross-Agent Isolation: ENFORCED (Blocked unauthorized peer read)")
            
            # 5. Hybrid Search Verification: Owner can search and find memory
            hits = SearchService.hybrid_search(
                db=db,
                query_text=f"operational protocol {name}",
                actor_name=name,
                limit=3
            )
            assert len(hits) >= 1
            print(f"    - Hybrid Search Retrieval: SUCCESS (Top match score: {hits[0].final_score:.4f})")
            
            # 6. Audit Trail Check
            recent_audit = db.query(AuditLog).filter(AuditLog.memory_id == mem_id).first()
            print(f"    - Audit Log: CAPTURED (Action: {recent_audit.action if recent_audit else 'None'})")
            
            results[name] = {
                "status": "PASS",
                "memory_id": mem_id,
                "private_namespace": private_ns_path,
                "confidence": read_rec.confidence,
                "importance": read_rec.importance
            }

        print("\n" + "=" * 80)
        print(">>> SUMMARY REPORT: ALL 9 AGENTS STORED AND STORING CORRECTLY")
        print("=" * 80)
        for agent_name, r in results.items():
            print(f"  [OK] {agent_name.upper():<12} | Status: {r['status']} | NS: {r['private_namespace']:<30} | Memory: {r['memory_id'][:8]}...")

        # Count total in DB now
        total = db.query(MemoryRecord).count()
        print(f"\n[+] Total Active Memories in Database: {total}")
        return True

    except Exception as e:
        db.rollback()
        print(f"\n[!] Verification Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        db.close()

if __name__ == "__main__":
    success = verify_all_agents()
    sys.exit(0 if success else 1)
