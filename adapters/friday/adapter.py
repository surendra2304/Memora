"""
FRIDAY Specialized Ecosystem Adapter for Memora
Provides high-level methods for executive orchestration, user preference retention,
session context bundle retrieval, and sub-agent bounded context delegation.
"""
from typing import Optional, Dict, Any, List
import logging

from adapters.base_adapter import BaseAgentAdapter

logger = logging.getLogger(__name__)

class FridayAdapter(BaseAgentAdapter):
    """
    Specialized adapter for FRIDAY, the primary ecosystem orchestrator and supervisor.
    """
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        default_namespace: str = "memora://friday/private",
        http_client: Optional[Any] = None
    ):
        super().__init__(
            agent_name="friday",
            base_url=base_url,
            api_key=api_key,
            default_namespace=default_namespace,
            role="supervisor",
            http_client=http_client
        )

    def save_user_preference(
        self,
        preference_text: str,
        confidence: float = 1.0,
        importance: float = 0.9,
        provenance: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Saves a persistent user preference memory record into FRIDAY's private namespace.
        """
        prov = {
            **(provenance or {}),
            "category": "user_preference",
            "recorded_by": "friday_orchestrator"
        }
        return self.write_memory(
            content_text=preference_text,
            memory_type="preference",
            target_namespace_path=self.default_namespace,
            confidence=confidence,
            importance=importance,
            provenance=prov,
            purpose="User profile personalization"
        )

    def get_session_context(
        self,
        user_query: str,
        token_budget: int = 8000,
        namespace_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Retrieves a rich curated Context Bundle for a user's conversational session query.
        """
        return self.get_context(
            task_query=user_query,
            token_budget=token_budget,
            namespace_path=namespace_path,
            purpose="Executive session context assembly"
        )

    def delegate_task_with_context(
        self,
        sub_agent_name: str,
        task_description: str,
        bounded_scope: Optional[str] = None,
        token_budget: int = 4000
    ) -> Dict[str, Any]:
        """
        Demonstrates sub-agent bounding: queries MEMORA on behalf of a sub-agent with
        strict bounded namespace scoping so the sub-agent receives only authorized context.
        """
        formatted_subname = sub_agent_name.lower()
        if not formatted_subname.startswith("friday:"):
            formatted_subname = f"friday:{formatted_subname}"

        target_scope = bounded_scope or f"memora://friday/projects/{sub_agent_name.lower().replace('friday:', '')}"

        # Request context bundle restricted to the sub-agent's bounded scope
        payload = {
            "agent_id": formatted_subname,
            "task_query": task_description,
            "token_budget": token_budget,
            "namespace_path": target_scope,
            "purpose": f"Sub-agent task delegation for {formatted_subname}",
            "max_candidates": 20
        }
        return self._dispatch_request("POST", "/v1/context", json_body=payload, purpose=f"Delegating to {formatted_subname}")