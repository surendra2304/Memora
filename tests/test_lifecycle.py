"""
Tests for Memory Lifecycle State Transitions
"""
import pytest
from storage.relational.models import (
    Agent,
    Namespace,
    NamespaceType,
    MemoryRecord,
    MemoryType,
    LifecycleState
)
from core.lifecycle.state_machine import MemoryLifecycleEngine, InvalidStateTransitionError

def test_lifecycle_transitions(test_db):
    agent = Agent(name="forge", description="Autonomous Code Generation System")
    test_db.add(agent)
    test_db.commit()

    ns = Namespace(
        path="memora://forge/private",
        type=NamespaceType.AGENT_PRIVATE,
        agent_id=agent.id
    )
    test_db.add(ns)
    test_db.commit()

    memory = MemoryRecord(
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.PROCEDURAL,
        content_text="Forge auto-generated FastAPI test suite.",
        source="forge_builder",
        confidence=0.8,
        importance=0.7,
        lifecycle_state=LifecycleState.CANDIDATE
    )
    test_db.add(memory)
    test_db.commit()

    # CANDIDATE -> ACTIVE
    MemoryLifecycleEngine.transition(memory, LifecycleState.ACTIVE)
    assert memory.lifecycle_state == LifecycleState.ACTIVE

    # ACTIVE -> VERIFIED
    MemoryLifecycleEngine.transition(memory, LifecycleState.VERIFIED)
    assert memory.lifecycle_state == LifecycleState.VERIFIED
    assert memory.last_verified_at is not None
    assert memory.confidence > 0.8

    # VERIFIED -> SUPERSEDED
    MemoryLifecycleEngine.transition(memory, LifecycleState.SUPERSEDED, superseded_by_id="newer-memory-id")
    assert memory.lifecycle_state == LifecycleState.SUPERSEDED
    assert memory.superseded_by_id == "newer-memory-id"

    # SUPERSEDED -> ARCHIVED
    MemoryLifecycleEngine.transition(memory, LifecycleState.ARCHIVED)
    assert memory.lifecycle_state == LifecycleState.ARCHIVED

def test_invalid_state_transition(test_db):
    agent = Agent(name="sentinel")
    test_db.add(agent)
    test_db.commit()

    ns = Namespace(path="memora://sentinel/private", type=NamespaceType.AGENT_PRIVATE, agent_id=agent.id)
    test_db.add(ns)
    test_db.commit()

    memory = MemoryRecord(
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.SYSTEM,
        content_text="Vulnerability alert.",
        lifecycle_state=LifecycleState.DELETED
    )
    test_db.add(memory)
    test_db.commit()

    with pytest.raises(InvalidStateTransitionError):
        MemoryLifecycleEngine.transition(memory, LifecycleState.ACTIVE)