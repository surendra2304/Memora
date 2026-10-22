"""
Regression test for the provenance precedence bug in MemoryWriteService.

`combined_provenance` was built with the caller's dict spread AFTER the
canonical fields, so a caller could overwrite trust-critical and integrity
fields. Verified before the fix: forge wrote a record carrying
trust_level="verified", created_by="friday", source="human_executive".

A caller's provenance bundle cannot authenticate a trust claim. Confidence and
source classification remain useful caller-supplied metadata, but untrusted
agents' ``verified``/``operator_confirmed`` labels are canonicalized to
``candidate``; trusted tiers require the separate Memora promotion boundary.
"""
import hashlib

from core.identity.service import IdentityService
from core.memory.pipeline.write_service import MemoryWriteService

CONTENT = "ordinary note about the xenon compressor"

#: Fields the pipeline owns. A caller must not be able to set these.
OWNED_FIELDS = {
    "source",
    "created_by",
    "created_at",
    "expires_at",
    "content_sha256",
    "extracted_entities",
    "retention_tier",
    "pipeline_version",
}

# Caller metadata may be retained, but a caller's trust assertion is never
# accepted as verification. The pipeline owns trust classification; verified
# status is established only through the authorized promotion workflow.



def _write(test_db, provenance):
    IdentityService.register_agent(test_db, "forge")
    result = MemoryWriteService.execute_pipeline(
        test_db, caller_name="forge", content_text=CONTENT, provenance=provenance
    )
    return result.record.provenance or {}


def test_caller_cannot_spoof_attribution(test_db):
    """The headline bug: created_by and source were caller-writable."""
    provenance = _write(
        test_db,
        {
            "created_by": "friday",
            "source": "human_executive",
            "trust_level": "verified",
            "confidence": 1.0,
        },
    )

    assert provenance["created_by"] == "forge", "attribution must name the real caller"
    assert provenance["source"] == "api", "source must be the real ingest channel"
    assert provenance["trust_level"] == "candidate", "caller-supplied trust is not proof"


def test_caller_cannot_forge_the_content_hash(test_db):
    """content_sha256 backs dedup and tamper detection, so it must be computed."""
    provenance = _write(test_db, {"content_sha256": "0" * 64})

    assert provenance["content_sha256"] == hashlib.sha256(CONTENT.encode()).hexdigest()


def test_caller_cannot_overwrite_pipeline_integrity_fields(test_db):
    provenance = _write(
        test_db,
        {
            "extracted_entities": ["fake"],
            "retention_tier": "permanent",
            "pipeline_version": "9.9.9",
        },
    )

    # The extractor's real output is a dict; the caller's list must not survive.
    assert isinstance(provenance["extracted_entities"], dict)
    assert "fake" not in str(provenance["extracted_entities"])

    assert provenance["retention_tier"] != "permanent"
    assert provenance["pipeline_version"] == "2.0.0"


def test_unrelated_caller_keys_are_still_preserved(test_db):
    """Provenance is an open bag; only the owned fields are protected."""
    provenance = _write(test_db, {"custom_tag": "kept", "ingest_batch": "b-42"})

    assert provenance["custom_tag"] == "kept"
    assert provenance["ingest_batch"] == "b-42"


def test_agent_claimed_verified_trust_is_downgraded_but_metadata_is_retained(test_db):
    """A write credential cannot self-assert a verified trust level."""
    IdentityService.register_agent(test_db, "forge")
    result = MemoryWriteService.execute_pipeline(
        test_db,
        caller_name="forge",
        content_text=CONTENT,
        trust_level="verified",
        confidence=0.42,
        source_type="verified_fact",
    )
    provenance = result.record.provenance or {}

    assert provenance["trust_level"] == "candidate"
    assert provenance["confidence"] == 0.42
    assert provenance["source_type"] == "verified_fact"
    # ...while attribution stays honest.
    assert provenance["created_by"] == "forge"
