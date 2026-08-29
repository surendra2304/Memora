"""
Base Agent Adapter for Memora Ecosystem
Standardizes how external ecosystem agents (FRIDAY, FORGE, FUTURIS, IntelX, MT5, NEXUS, SENTINEL)
authenticate and interact with the MEMORA Persistent Memory Infrastructure API.
"""
from abc import ABC
from typing import Optional, Dict, Any, List, Union
import json
import logging

logger = logging.getLogger(__name__)

class MemoraAdapterError(Exception):
    """Base exception for Memora adapter client errors."""
    def __init__(self, message: str, status_code: Optional[int] = None, response_data: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}

class MemoraAccessDeniedError(MemoraAdapterError):
    """Raised when an agent is denied access due to policy or namespace isolation."""
    pass

class MemoraNotFoundError(MemoraAdapterError):
    """Raised when a requested memory or namespace is not found."""
    pass

class MemoraSecurityViolationError(MemoraAdapterError):
    """Raised when a write is rejected due to security scanning (e.g. secret leakage)."""
    pass

class BaseAgentAdapter(ABC):
    """
    Abstract Base Adapter standardizing agent-MEMORA communication.
    Provides convenience methods for memory write, search, context retrieval, verification, and sharing.
    """
    def __init__(
        self,
        agent_name: str,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        default_namespace: Optional[str] = None,
        role: str = "worker",
        http_client: Optional[Any] = None
    ):
        self.agent_name = agent_name.lower()
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_namespace = default_namespace or f"memora://{self.agent_name}/private"
        self.role = role
        self._http_client = http_client

    def _get_headers(self, purpose: Optional[str] = None) -> Dict[str, str]:
        """Constructs canonical authentication and metadata headers for requests."""
        headers = {
            "X-Agent-Name": self.agent_name,
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if self.api_key:
            headers["X-Agent-Key"] = self.api_key
        if purpose:
            headers["X-Purpose"] = purpose
        return headers

    def _dispatch_request(
        self,
        method: str,
        endpoint: str,
        json_body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        purpose: Optional[str] = None
    ) -> Dict[str, Any]:
        """Dispatches HTTP request to the MEMORA API with standardized error handling."""
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers(purpose=purpose)

        # Dispatch via provided custom client (e.g. TestClient or httpx.Client) or default httpx
        if self._http_client:
            client = self._http_client
            if hasattr(client, "request"):
                resp = client.request(method=method, url=endpoint if not endpoint.startswith("http") else endpoint, json=json_body, params=params, headers=headers)
            elif method.upper() == "GET":
                resp = client.get(endpoint, params=params, headers=headers)
            elif method.upper() == "POST":
                resp = client.post(endpoint, json=json_body, headers=headers)
            elif method.upper() == "DELETE":
                resp = client.delete(endpoint, params=params, headers=headers)
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")
        else:
            import httpx
            with httpx.Client(base_url=self.base_url, timeout=15.0) as client:
                resp = client.request(method=method, url=endpoint, json=json_body, params=params, headers=headers)

        # Handle Responses & Error Mapping
        status_code = resp.status_code
        try:
            data = resp.json()
        except Exception:
            data = {"text": resp.text}

        if 200 <= status_code < 300:
            return data

        # Map Specific Errors
        if status_code == 403:
            detail = data.get("detail", "Access denied by 5D Policy Engine.") if isinstance(data, dict) else str(data)
            logger.warning(f"Memora Access Denied (403) for agent '{self.agent_name}': {detail}")
            raise MemoraAccessDeniedError(f"Access Denied for agent '{self.agent_name}': {detail}", status_code=403, response_data=data)

        if status_code == 404:
            detail = data.get("detail", "Resource not found.") if isinstance(data, dict) else str(data)
            raise MemoraNotFoundError(f"Resource not found: {detail}", status_code=404, response_data=data)

        if status_code == 422:
            detail = data.get("detail", "Security or validation error.") if isinstance(data, dict) else str(data)
            raise MemoraSecurityViolationError(f"Security/Validation Violation: {detail}", status_code=422, response_data=data)

        detail = data.get("detail", str(data)) if isinstance(data, dict) else str(data)
        raise MemoraAdapterError(f"Memora API Error ({status_code}): {detail}", status_code=status_code, response_data=data)

    # -------------------------------------------------------------
    # MEMORA API WRAPPER METHODS
    # -------------------------------------------------------------

    def write_memory(
        self,
        content_text: str,
        memory_type: str = "episodic",
        target_namespace_path: Optional[str] = None,
        confidence: Optional[float] = 1.0,
        importance: Optional[float] = 0.6,
        provenance: Optional[Dict[str, Any]] = None,
        purpose: Optional[str] = None,
        allow_duplicates: bool = False
    ) -> Dict[str, Any]:
        """
        Writes a new memory record through the 10-step write pipeline.
        """
        payload = {
            "content_text": content_text,
            "target_namespace_path": target_namespace_path or self.default_namespace,
            "memory_type": memory_type,
            "source": f"agent:{self.agent_name}",
            "provenance": provenance or {},
            "confidence": confidence,
            "importance": importance,
            "allow_duplicates": allow_duplicates
        }
        return self._dispatch_request("POST", "/v1/memories", json_body=payload, purpose=purpose)

    def search_memories(
        self,
        query: str,
        namespace_path: Optional[str] = None,
        limit: int = 10,
        min_score: float = 0.0,
        include_superseded: bool = False,
        include_archived: bool = False,
        purpose: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Executes tri-modal hybrid search (Vector + Keyword + Graph RRF).
        """
        params = {
            "q": query,
            "limit": limit,
            "min_score": min_score,
            "include_superseded": include_superseded,
            "include_archived": include_archived
        }
        if namespace_path:
            params["namespace_path"] = namespace_path
        return self._dispatch_request("GET", "/v1/memories/search", params=params, purpose=purpose)

    def get_context(
        self,
        task_query: str,
        token_budget: int = 4000,
        namespace_path: Optional[str] = None,
        purpose: Optional[str] = None,
        max_candidates: int = 30
    ) -> Dict[str, Any]:
        """
        Retrieves a curated, token-budgeted, policy-filtered Context Bundle.
        """
        payload = {
            "agent_id": self.agent_name,
            "task_query": task_query,
            "token_budget": token_budget,
            "namespace_path": namespace_path,
            "purpose": purpose,
            "max_candidates": max_candidates
        }
        return self._dispatch_request("POST", "/v1/context", json_body=payload, purpose=purpose)

    def verify_memory(
        self,
        memory_id: str,
        notes: Optional[str] = None,
        purpose: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Verifies a memory record, promoting its state to 'verified'.
        """
        payload = {"notes": notes} if notes else {}
        return self._dispatch_request("POST", f"/v1/memories/{memory_id}/verify", json_body=payload, purpose=purpose)

    def share_memory(
        self,
        memory_id: str,
        target_agent_name: str,
        actions: Optional[List[str]] = None,
        purpose: Optional[str] = None,
        ttl_hours: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Explicitly shares/grants access permissions for a memory's namespace to another agent.
        """
        payload = {
            "target_agent_name": target_agent_name,
            "actions": actions or ["read"],
            "purpose": purpose,
            "ttl_hours": ttl_hours
        }
        return self._dispatch_request("POST", f"/v1/memories/{memory_id}/share", json_body=payload, purpose=purpose)

    def supersede_memory(
        self,
        memory_id: str,
        new_memory_id: str,
        reason: Optional[str] = None,
        purpose: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Marks an old memory as superseded by a new canonical memory.
        """
        payload = {"new_memory_id": new_memory_id, "reason": reason}
        return self._dispatch_request("POST", f"/v1/memories/{memory_id}/supersede", json_body=payload, purpose=purpose)

    def delete_memory(
        self,
        memory_id: str,
        hard: bool = False,
        purpose: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Soft-deletes or hard-purges a memory record.
        """
        params = {"hard": hard}
        return self._dispatch_request("DELETE", f"/v1/memories/{memory_id}", params=params, purpose=purpose)

    def get_namespace_policy(
        self,
        namespace_id_or_path: str,
        purpose: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Inspects effective policy and active access grants for a namespace.
        """
        return self._dispatch_request("GET", f"/v1/namespaces/{namespace_id_or_path}/policy", purpose=purpose)