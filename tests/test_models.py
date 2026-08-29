"""
Tests for Canonical Relational Data Models
"""
import pytest
from storage.relational.models import (
    Agent,
    Namespace,
    NamespaceType,
    MemoryRecord,
    MemoryType,
    LifecycleState,
    AuditLog
)

def test_create_agent_and_namespaces(test_db):
    agent = Agent(name="friday", description="Ecosystem Supervisor AI")
    test_db.add(agent)
    test_db.commit()
    test_db.refresh(agent)

    assert agent.id is not None
    assert agent.name == "friday"

    ns = Namespace(
        path="memora://friday/private",
        type=NamespaceType.AGENT_PRIVATE,
        agent_id=agent.id
    )
    test_db.add(ns)
    test_db.commit()
    test_db.refresh(ns)

    assert ns.id is not None
    assert ns.agent.name == "friday"

def test_create_memory_record_and_audit(test_db):
    agent = Agent(name="nexus", description="Autonomous Web Engine")
    test_db.add(agent)
    test_db.commit()

    ns = Namespace(
        path="memora://nexus/private",
        type=NamespaceType.AGENT_PRIVATE,
        agent_id=agent.id
    )
    test_db.add(ns)
    test_db.commit()

    memory = MemoryRecord(
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Nexus completed landing page build with responsive UI.",
        source="nexus_engine",
        provenance={"task_id": "build-42", "duration": 12.4},
        confidence=0.98,
        importance=0.85,
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add(memory)
    test_db.commit()
    test_db.refresh(memory)

    assert memory.id is not None
    assert memory.memory_type == MemoryType.EPISODIC
    assert memory.lifecycle_state == LifecycleState.ACTIVE

    audit = AuditLog(
        actor_id=agent.id,
        memory_id=memory.id,
        action="create",
        details={"result": "success"}
    )
    test_db.add(audit)
    test_db.commit()
    test_db.refresh(audit)

    assert audit.id is not None
    assert audit.action == "create"
    assert len(memory.audit_logs) == 1