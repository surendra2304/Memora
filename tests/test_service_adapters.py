"""
Tests for Core Memory Service and Ecosystem Adapters
"""
import pytest
from core.identity.service import IdentityService
from core.memory.service import MemoryService, MemoryNotFoundError, PermissionDeniedError
from core.memory.schemas import MemoryRecordCreate, MemoryQuery
from adapters.ecosystem import EcosystemMemoryAdapter
from storage.relational.models import MemoryType, LifecycleState, NamespaceType

def test_identity_and_namespace_creation(test_db):
    agent = IdentityService.register_agent(test_db, "friday", "Supervisor")
    assert agent.name == "friday"
    
    # Auto-created private namespace check
    ns = IdentityService.get_namespace_by_path(test_db, "memora://friday/private")
    assert ns is not None
    assert ns.agent_id == agent.id

    # Create team shared namespace
    team_ns = IdentityService.create_namespace(test_db, "memora://team/shared", NamespaceType.TEAM_SHARED)
    assert team_ns.type == NamespaceType.TEAM_SHARED

def test_memory_crud_service(test_db):
    agent = IdentityService.register_agent(test_db, "nexus")
    
    # Ingest
    mem_create = MemoryRecordCreate(
        owner_name="nexus",
        namespace_path="memora://nexus/private",
        memory_type=MemoryType.EXPERIENCE,
        content_text="Successfully migrated server cluster without downtime.",
        source="nexus_engine",
        confidence=0.99,
        importance=0.95,
        lifecycle_state=LifecycleState.ACTIVE
    )
    record = MemoryService.create_memory(test_db, mem_create, actor_name="nexus")
    assert record.id is not None
    assert record.owner.name == "nexus"

    # Query
    q = MemoryQuery(query_text="server cluster", owner_name="nexus")
    results = MemoryService.query_memories(test_db, q, actor_name="nexus")
    assert len(results) == 1
    assert results[0].id == record.id

    # Retrieve
    retrieved = MemoryService.get_memory_by_id(test_db, record.id, actor_name="nexus")
    assert retrieved.content_text == record.content_text

    # Unauthorized access rejection
    other_agent = IdentityService.register_agent(test_db, "rogue_agent")
    with pytest.raises(PermissionDeniedError):
        MemoryService.get_memory_by_id(test_db, record.id, actor_name="rogue_agent")

def test_ecosystem_adapters(test_db):
    friday = IdentityService.register_agent(test_db, "friday")
    
    # Format episodic event
    episodic_in = EcosystemMemoryAdapter.format_episodic_event(
        agent_name="friday",
        event_summary="Orchestrated 8-system emergency freeze drill.",
        source="emergency_controller"
    )
    rec = MemoryService.create_memory(test_db, episodic_in, actor_name="friday")
    assert rec.memory_type == MemoryType.EPISODIC

    # Format procedural skill
    procedural_in = EcosystemMemoryAdapter.format_procedural_skill(
        agent_name="friday",
        skill_name="MasterHalt",
        procedure_text="Execute sequential freeze cascade across all active ports."
    )
    skill_rec = MemoryService.create_memory(test_db, procedural_in, actor_name="friday")
    assert skill_rec.memory_type == MemoryType.PROCEDURAL
    assert skill_rec.lifecycle_state == LifecycleState.VERIFIED