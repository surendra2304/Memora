"""
Unit and Integration Tests for Cryptographic Provenance (Safety Invariant 2).
Verifies that all memory records carry an HMAC-SHA256 signature to detect database tampering.
"""
from core.security.provenance import (
    compute_memory_signature,
    verify_memory_signature,
    sign_memory_provenance,
    build_signature_payload,
)
from storage.relational.models import MemoryType
from core.memory.pipeline.write_service import MemoryWriteService
from core.identity.service import IdentityService


def test_signature_deterministic_and_canonical():
    """Verify that identical inputs produce the identical HMAC-SHA256 signature."""
    payload1 = build_signature_payload("mem_1", "default", "agent_1", "hash_abc", "episodic")
    payload2 = build_signature_payload("mem_1", "default", "agent_1", "hash_abc", "episodic")
    assert payload1 == payload2
    assert payload1 == "v1:mem_1:default:agent_1:hash_abc:episodic"

    sig1 = compute_memory_signature({
        "id": "mem_1",
        "tenant_id": "default",
        "owner_id": "agent_1",
        "content_hash": "hash_abc",
        "memory_type": "episodic",
    }, secret="test_secret_123")
    sig2 = compute_memory_signature({
        "id": "mem_1",
        "tenant_id": "default",
        "owner_id": "agent_1",
        "content_hash": "hash_abc",
        "memory_type": "episodic",
    }, secret="test_secret_123")
    assert sig1 == sig2
    assert len(sig1) == 64  # SHA256 hex string length


def test_tamper_detection_on_record():
    """Verify that tampering with any signed field invalidates the signature."""
    record_dict = {
        "id": "mem_test_100",
        "tenant_id": "default",
        "owner_id": "agent_friday",
        "content_hash": "abc123456",
        "memory_type": "episodic",
    }
    secret = "sovereign_master_key_999"
    sig = compute_memory_signature(record_dict, secret=secret)
    record_dict["provenance"] = {"hmac_signature": sig}

    # Valid check
    assert verify_memory_signature(record_dict, secret=secret) is True

    # Tamper 1: change content_hash
    tampered_content = {**record_dict, "content_hash": "forged_content_hash"}
    assert verify_memory_signature(tampered_content, secret=secret) is False

    # Tamper 2: change owner_id
    tampered_owner = {**record_dict, "owner_id": "agent_rogue"}
    assert verify_memory_signature(tampered_owner, secret=secret) is False

    # Tamper 3: change memory_type
    tampered_type = {**record_dict, "memory_type": "system"}
    assert verify_memory_signature(tampered_type, secret=secret) is False

    # Tamper 4: change tenant_id
    tampered_tenant = {**record_dict, "tenant_id": "tenant_attacker"}
    assert verify_memory_signature(tampered_tenant, secret=secret) is False


def test_sign_memory_provenance_immutability():
    """Verify that sign_memory_provenance returns a new dict and preserves existing keys."""
    orig_prov = {"source": "api", "trust_level": "candidate"}
    signed_prov = sign_memory_provenance(
        provenance=orig_prov,
        memory_id="mem_42",
        tenant_id="default",
        owner_id="owner_1",
        content_hash="h42",
        memory_type="procedural",
        secret="sec_42",
    )
    assert orig_prov is not signed_prov
    assert signed_prov["source"] == "api"
    assert "hmac_signature" in signed_prov
    assert signed_prov["signature_version"] == "v1"


def test_write_service_attaches_signature_on_insert(test_db):
    """Integration: verify write_service pipeline automatically attaches HMAC signature."""
    agent = IdentityService.register_agent(
        test_db,
        name="provenance_agent",
        role="worker",
        tenant_id="default",
    )
    ns = IdentityService.resolve_namespace(
        test_db,
        path="memora://provenance/audit",
        owner_agent_id=agent.id,
        tenant_id="default",
    )

    result = MemoryWriteService.execute_pipeline(
        db=test_db,
        content_text="Critical distributed architecture fact about Memora persistence.",
        caller_name="provenance_agent",
        target_namespace_path=ns.path,
        memory_type=MemoryType.EPISODIC,
        tenant_id="default",
    )
    test_db.commit()

    record = result.record
    assert record.provenance is not None
    assert "hmac_signature" in record.provenance
    assert record.provenance.get("signature_version") == "v1"

    # Verify the signature matches
    assert verify_memory_signature(record) is True
