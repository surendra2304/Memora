"""
Cryptographic Provenance and Integrity Verification for Memora
Enforces Safety Invariant 2: HMAC-SHA256 cryptographic provenance on all memory records
to detect unauthorized direct database tampering.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from typing import Any, Dict, Optional, Union

from core.config import settings

logger = logging.getLogger(__name__)

SIGNATURE_VERSION = "v1"


def get_provenance_secret(secret: Optional[str] = None) -> str:
    """Retrieve the master key used for signing memory provenance."""
    if secret:
        return secret
    return settings.get_memora_api_key()


def build_signature_payload(
    memory_id: str,
    tenant_id: str,
    owner_id: str,
    content_hash: str,
    memory_type: str,
) -> str:
    """
    Construct the canonical string representation for HMAC-SHA256 signature computation.
    Format: v1:{memory_id}:{tenant_id}:{owner_id}:{content_hash}:{memory_type}
    """
    clean_id = str(memory_id).strip()
    clean_tenant = str(tenant_id).strip()
    clean_owner = str(owner_id).strip()
    clean_hash = str(content_hash).strip()
    clean_type = str(memory_type).strip().lower()
    return f"{SIGNATURE_VERSION}:{clean_id}:{clean_tenant}:{clean_owner}:{clean_hash}:{clean_type}"


def compute_memory_signature(
    record_or_dict: Union[Any, Dict[str, Any]],
    secret: Optional[str] = None,
) -> str:
    """
    Compute HMAC-SHA256 signature for a memory record or dict.
    """
    key = get_provenance_secret(secret)

    if isinstance(record_or_dict, dict):
        memory_id = record_or_dict.get("id") or record_or_dict.get("memory_id", "")
        tenant_id = record_or_dict.get("tenant_id", "default")
        owner_id = record_or_dict.get("owner_id") or record_or_dict.get("owner", "")
        content_hash = record_or_dict.get("content_hash", "")
        mem_type = record_or_dict.get("memory_type", "")
    else:
        memory_id = getattr(record_or_dict, "id", "")
        tenant_id = getattr(record_or_dict, "tenant_id", "default")
        owner_id = getattr(record_or_dict, "owner_id", "")
        content_hash = getattr(record_or_dict, "content_hash", "")
        mem_type = getattr(record_or_dict, "memory_type", "")

    if hasattr(mem_type, "value"):
        mem_type = mem_type.value

    payload = build_signature_payload(
        memory_id=str(memory_id),
        tenant_id=str(tenant_id),
        owner_id=str(owner_id),
        content_hash=str(content_hash),
        memory_type=str(mem_type),
    )

    return hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_memory_signature(
    record_or_dict: Union[Any, Dict[str, Any]],
    signature: Optional[str] = None,
    secret: Optional[str] = None,
) -> bool:
    """
    Verify HMAC-SHA256 signature of a memory record against tampering.
    Returns True if valid, False if signature is missing, invalid, or tampered.
    """
    if signature is None:
        if isinstance(record_or_dict, dict):
            prov = record_or_dict.get("provenance") or {}
        else:
            prov = getattr(record_or_dict, "provenance", None) or {}
        signature = prov.get("hmac_signature")

    if not signature or not isinstance(signature, str):
        return False

    expected_sig = compute_memory_signature(record_or_dict, secret=secret)
    return hmac.compare_digest(expected_sig, signature)


def sign_memory_provenance(
    provenance: Dict[str, Any],
    memory_id: str,
    tenant_id: str,
    owner_id: str,
    content_hash: str,
    memory_type: str,
    secret: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Return a new provenance dictionary containing the HMAC-SHA256 signature.
    Preserves immutability by creating a new dict.
    """
    key = get_provenance_secret(secret)
    payload = build_signature_payload(
        memory_id=memory_id,
        tenant_id=tenant_id,
        owner_id=owner_id,
        content_hash=content_hash,
        memory_type=memory_type,
    )
    sig = hmac.new(key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        **provenance,
        "hmac_signature": sig,
        "signature_version": SIGNATURE_VERSION,
    }
