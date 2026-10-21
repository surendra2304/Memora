"""
Tenant-aware end-to-end storage verification for the ten FRIDAY Universe agents.

This script is intentionally non-destructive: it registers missing identities and
namespaces, writes one uniquely marked verification memory per agent, verifies
owner access and peer isolation, searches with the explicit default tenant, and
deletes only the verification records it created. It does not clear or reseed the
existing Memora database.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from storage.relational.session import SessionLocal
from storage.relational.models import MemoryRecord, MemoryType, AuditLog
from core.identity.service import IdentityService
from core.memory.pipeline.write_service import MemoryWriteService
from core.memory.service import MemoryService
from core.memory.search_service import SearchService

ECOSYSTEM_AGENTS = [
    {"name": "friday", "role": "supervisor", "desc": "Central desktop OS & master orchestrator"},
    {"name": "forge", "role": "worker", "desc": "Autonomous software engineering & code synthesis"},
    {"name": "sentinel", "role": "worker", "desc": "Cybersecurity shield & vulnerability defense"},
    {"name": "inference", "role": "worker", "desc": "Multi-model AI inference gateway"},
    {"name": "cortex", "role": "worker", "desc": "Web operations & telemetry intelligence"},
    {"name": "intelx", "role": "worker", "desc": "Deep research & evidence extraction"},
    {"name": "futuris", "role": "worker", "desc": "Probabilistic forecasting & regime modeling"},
    {"name": "stratex", "role": "worker", "desc": "24/7 algorithmic trading & execution"},
    {"name": "memora", "role": "worker", "desc": "Persistent memory fabric subsystem"},
    {"name": "vyralx", "role": "worker", "desc": "Autonomous video production & distribution"},
]


def _ensure_identity_and_namespace(db: Any, name: str, role: str, description: str):
    """Resolve an agent and its private namespace only inside the intended tenant."""
    tenant_id = "default"
    agent = IdentityService.get_agent_by_name(db, name, tenant_id=tenant_id)
    if agent is None:
        agent = IdentityService.register_agent(
            db, name=name, role=role, description=description, tenant_id=tenant_id
        )

    path = f"memora://{name}/private"
    namespace = IdentityService.get_namespace_by_path(db, path)
    if namespace is None:
        namespace = IdentityService.resolve_namespace(
            db, path, owner_agent_id=agent.id, tenant_id=tenant_id
        )
    if getattr(namespace, "tenant_id", tenant_id) != tenant_id:
        raise RuntimeError(f"Namespace {path} belongs to an unexpected tenant")
    return agent, namespace, path


def verify_all_agents() -> bool:
    print("=" * 80)
    print("VERIFYING MEMORA STORAGE CONTRACTS ACROSS ALL 10 AGENTS")
    print("=" * 80)

    db = SessionLocal()
    marker = f"memora-e2e-verification:{uuid.uuid4()}"
    created_ids: list[str] = []
    results: dict[str, dict[str, Any]] = {}
    success = False

    try:
        for info in ECOSYSTEM_AGENTS:
            name, role, description = info["name"], info["role"], info["desc"]
            print(f"\n[+] {name.upper()} — resolving tenant-scoped identity and namespace")
            agent, _namespace, namespace_path = _ensure_identity_and_namespace(
                db, name, role, description
            )

            content = f"{marker} Agent {name} verification payload."
            write_result = MemoryWriteService.execute_pipeline(
                db=db,
                content_text=content,
                caller_name=name,
                target_namespace_path=namespace_path,
                memory_type=MemoryType.PROCEDURAL,
                source=f"{name}_e2e_verification",
                confidence=1.0,
                importance=0.90,
                purpose=f"Isolated Memora storage verification for {name}",
            )
            memory_id = write_result.record.id
            created_ids.append(memory_id)

            # Explicit tenant on reads avoids ambiguity when agent names repeat
            # across tenants and aligns verification with production callers.
            read_record = MemoryService.get_memory_by_id(
                db, memory_id=memory_id, actor_name=name, tenant_id="default"
            )
            if read_record is None or read_record.content_text != content:
                raise AssertionError(f"{name} could not read its own verification memory")

            other_name = "sentinel" if name != "sentinel" else "forge"
            denied = False
            try:
                MemoryService.get_memory_by_id(
                    db, memory_id=memory_id, actor_name=other_name, tenant_id="default"
                )
            except Exception:
                denied = True
            if not denied:
                raise AssertionError(
                    f"Isolation failure: {other_name} read {name}'s private verification memory"
                )

            hits = SearchService.hybrid_search(
                db=db,
                query_text=f"{marker} {name} verification payload",
                actor_name=name,
                tenant_id="default",
                limit=5,
                namespace_path=namespace_path,
            )
            if not any(hit.record.id == memory_id for hit in hits):
                raise AssertionError(f"{name}'s own verification memory was not found by scoped search")

            audit_row = db.query(AuditLog).filter(AuditLog.memory_id == memory_id).first()
            if audit_row is None:
                raise AssertionError(f"{name}'s verification memory has no audit record")

            results[name] = {
                "status": "PASS",
                "memory_id": memory_id,
                "private_namespace": namespace_path,
                "tenant_id": "default",
                "audit_recorded": True,
            }
            print("    identity, owner read, peer denial, scoped search, and audit: PASS")

        success = True
        print("\n" + "=" * 80)
        print(f"RESULT: {len(results)}/10 agents passed Memora storage verification")
        for name, result in results.items():
            print(f"  [PASS] {name:<12} | tenant={result['tenant_id']} | {result['private_namespace']}")
        return True

    except Exception as exc:
        db.rollback()
        print(f"\n[FAIL] Memora storage verification: {exc}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        # Remove only records written by this exact invocation. Never delete by
        # namespace, agent, or broad seed selector, and never delete old memory.
        try:
            if created_ids:
                db.query(MemoryRecord).filter(
                    MemoryRecord.id.in_(created_ids),
                    MemoryRecord.content_text.like(f"{marker}%"),
                ).delete(synchronize_session=False)
                db.commit()
        except Exception:
            db.rollback()
            print("[WARN] Could not clean up every marked verification record; inspect the IDs above.")
            if success:
                success = False
        finally:
            db.close()


if __name__ == "__main__":
    sys.exit(0 if verify_all_agents() else 1)
