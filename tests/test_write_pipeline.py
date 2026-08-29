"""
Comprehensive Tests for the MEMORA 10-Step Memory Write Pipeline
"""
import pytest
from fastapi.testclient import TestClient
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
from core.memory.pipeline.secret_scanner import SecretScanner, SecretDetectedSecurityViolation
from core.memory.pipeline.entity_extractor import EntityExtractor
from core.memory.pipeline.deduplication import DeduplicationEngine
from core.memory.pipeline.write_service import MemoryWriteService

def test_secret_scanner_detection_and_rejection():
    """
    Test Step 3: SecretScanner catches tokens and credentials.
    """
    safe_text = "FRIDAY coordinated morning briefings across the AI Universe."
    assert len(SecretScanner.scan_content(safe_text)) == 0
    SecretScanner.validate_content_safety(safe_text)

    # OpenAI Key
    with pytest.raises(SecretDetectedSecurityViolation) as exc_info:
        SecretScanner.validate_content_safety("System configuration sk-1234567890abcdef1234567890abcdef for worker.")
    assert "OpenAI API Key" in exc_info.value.secret_types

    # Google API Key
    with pytest.raises(SecretDetectedSecurityViolation) as exc_info:
        SecretScanner.validate_content_safety("Access key is AIzaSyD1234567890abcdef1234567890abcde for gemini.")
    assert "Google API Key" in exc_info.value.secret_types

    # GitHub Token
    with pytest.raises(SecretDetectedSecurityViolation) as exc_info:
        SecretScanner.validate_content_safety("Token ghp_1234567890abcdef1234567890abcdef1234 exported.")
    assert "GitHub Personal Access Token" in exc_info.value.secret_types

    # Hardcoded Password
    with pytest.raises(SecretDetectedSecurityViolation) as exc_info:
        SecretScanner.validate_content_safety("db_config: password='super_secret_master_password'")
    assert "Hardcoded Password" in exc_info.value.secret_types

def test_entity_extractor():
    """
    Test Step 5: EntityExtractor identifies agents, URIs, components, and action triples.
    """
    text = "FRIDAY built FastAPI relational persistence at memora://friday/projects/alpha with PostgreSQL and Redis."
    entities = EntityExtractor.extract_entities_and_relationships(text)

    assert "friday" in entities["agents"]
    assert "memora://friday/projects/alpha" in entities["uris"]
    assert "fastapi" in entities["components"]
    assert "postgresql" in entities["components"]
    assert "redis" in entities["components"]
    assert len(entities["triples"]) >= 1

def test_deduplication_engine(test_db):
    """
    Test Step 6: DeduplicationEngine detects exact and near duplicates in the same namespace.
    """
    agent = IdentityService.register_agent(test_db, "forge")
    ns = IdentityService.get_namespace_by_path(test_db, "memora://forge/private")

    # Ingest baseline
    mem = MemoryRecord(
        namespace_id=ns.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="Forge generated React frontend dashboard layout.",
        lifecycle_state=LifecycleState.ACTIVE
    )
    test_db.add(mem)
    test_db.commit()

    # Exact duplicate check
    dup_res = DeduplicationEngine.check_duplicates_and_contradictions(
        test_db,
        namespace_id=ns.id,
        content_text="Forge generated React frontend dashboard layout."
    )
    assert dup_res.is_duplicate is True
    assert dup_res.duplicate_of_id == mem.id
    assert dup_res.similarity_score == 1.0

    # Non-duplicate check
    non_dup = DeduplicationEngine.check_duplicates_and_contradictions(
        test_db,
        namespace_id=ns.id,
        content_text="Completely different memory regarding PostgreSQL optimization."
    )
    assert non_dup.is_duplicate is False

def test_memory_write_service_10_steps_end_to_end(test_db):
    """
    Test full 10-step pipeline execution via MemoryWriteService.
    """
    result = MemoryWriteService.execute_pipeline(
        db=test_db,
        content_text="NEXUS deployed high-performance CDN edge caching to memora://nexus/projects/web-engine.",
        caller_name="nexus",
        target_namespace_path="memora://nexus/projects/web-engine",
        memory_type=MemoryType.EXPERIENCE,
        source="nexus_deployer",
        provenance={"build_id": "b-902", "region": "us-east"}
    )

    assert result.record.id is not None
    assert result.is_duplicate is False
    assert result.record.memory_type == MemoryType.EXPERIENCE
    assert result.record.owner.name == "nexus"

    # Verify step traces
    traces = result.step_outputs
    assert "step_1_receive_event" in traces
    assert "step_2_authenticate_and_resolve" in traces
    assert "step_3_classify_and_scan" in traces
    assert "step_4_normalize_content" in traces
    assert "step_5_extract_entities" in traces
    assert "step_6_deduplication" in traces
    assert "step_7_assign_metadata" in traces
    assert "step_8_apply_policy" in traces
    assert "step_9_persistence" in traces
    assert "step_10_emit_event_and_audit" in traces

    # Verify audit log created
    audit = test_db.query(AuditLog).filter(AuditLog.memory_id == result.record.id).first()
    assert audit is not None

def test_api_v1_memories_post_endpoint(client: TestClient):
    """
    Test POST /v1/memories API endpoint with successful write.
    """
    payload = {
        "content_text": "Sentinel mitigated DDoS attack vector at gateway.",
        "target_namespace_path": "memora://sentinel/private",
        "memory_type": "decision",
        "source": "sentinel_operator",
        "confidence": 0.99,
        "importance": 0.90
    }

    resp = client.post("/v1/memories", json=payload, headers={"X-Agent-Name": "sentinel", "X-Access-Purpose": "Security telemetry"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["memory_type"] == "decision"
    assert data["is_duplicate"] is False
    assert "step_trace" in data

def test_api_v1_memories_secret_rejection(client: TestClient):
    """
    Test POST /v1/memories rejects payload with credentials (HTTP 422).
    """
    leaked_payload = {
        "content_text": "API credentials configured: sk-1234567890abcdef1234567890abcdef for prod.",
        "target_namespace_path": "memora://nexus/private"
    }

    resp = client.post("/v1/memories", json=leaked_payload, headers={"X-Agent-Name": "nexus"})
    assert resp.status_code == 422
    err_data = resp.json()
    assert "SecurityPolicyViolation" in str(err_data)