"""
SENTINEL Specialized Ecosystem Adapter for Memora
Provides cybersecurity auditing, sensitive finding retention, explicit security remediation promotion,
and bounded security context retrieval.
"""
from typing import Optional, Dict, Any, List
import logging

from adapters.base_adapter import BaseAgentAdapter

logger = logging.getLogger(__name__)

class SentinelAdapter(BaseAgentAdapter):
    """
    Specialized adapter for SENTINEL, the cybersecurity auditor and ecosystem security controller.
    """
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        default_namespace: str = "memora://sentinel/private",
        http_client: Optional[Any] = None
    ):
        super().__init__(
            agent_name="sentinel",
            base_url=base_url,
            api_key=api_key,
            default_namespace=default_namespace,
            role="security",
            http_client=http_client
        )

    def record_private_finding(
        self,
        finding_details: str,
        asset_id: str,
        severity: str = "HIGH",
        confidence: float = 1.0,
        importance: float = 0.95
    ) -> Dict[str, Any]:
        """
        Stores an unredacted, sensitive vulnerability finding strictly within SENTINEL's private namespace.
        """
        prov = {
            "asset_id": asset_id,
            "severity": severity,
            "classification": "CONFIDENTIAL_SECURITY",
            "audited_by": "sentinel_daemon"
        }
        return self.write_memory(
            content_text=finding_details,
            memory_type="experience",
            target_namespace_path=self.default_namespace,
            confidence=confidence,
            importance=importance,
            provenance=prov,
            purpose=f"Confidential vulnerability analysis on asset {asset_id}"
        )

    def publish_approved_remediation(
        self,
        remediation_text: str,
        project_id: str,
        private_finding_id: Optional[str] = None,
        target_agent: str = "forge",
        ttl_hours: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Explicit Promotion Mechanism:
        Takes an approved, sanitized remediation guideline and publishes it to the shared project
        namespace memora://shared/projects/{project_id}, explicitly delegating access to FORGE.
        """
        target_shared_namespace = f"memora://shared/projects/{project_id}"
        prov = {
            "source_finding_id": private_finding_id,
            "sanitized": True,
            "promotion_gate": "SENTINEL_APPROVED",
            "target_agent": target_agent,
            "project_id": project_id
        }

        # 1. Ingest sanitized remediation into shared namespace
        write_res = self.write_memory(
            content_text=remediation_text,
            memory_type="procedural",
            target_namespace_path=target_shared_namespace,
            confidence=1.0,
            importance=0.9,
            provenance=prov,
            purpose=f"Promoting security remediation guideline for project {project_id}"
        )
        memory_id = write_res["id"]

        # 2. Explicitly share / delegate access to target agent (e.g. FORGE)
        share_res = self.share_memory(
            memory_id=memory_id,
            target_agent_name=target_agent,
            actions=["read", "query"],
            purpose=f"Security remediation guidance for project {project_id}",
            ttl_hours=ttl_hours
        )

        return {
            "status": "promoted",
            "memory_id": memory_id,
            "shared_namespace": target_shared_namespace,
            "target_agent": target_agent,
            "share_result": share_res,
            "remediation_summary": remediation_text
        }

    def get_security_context(
        self,
        asset_id: str
    ) -> Dict[str, Any]:
        """
        Retrieves security audit context strictly bounded to the SENTINEL private namespace.
        """
        return self.get_context(
            task_query=f"vulnerability security finding audit {asset_id}",
            token_budget=4000,
            namespace_path=self.default_namespace,
            purpose=f"Security posture analysis for asset {asset_id}"
        )