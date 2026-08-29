"""
MEMORA v1 Context Pipeline Endpoints
Provides POST /v1/context to build curated, token-budgeted, policy-filtered Context Bundles.
"""
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from storage.relational.session import get_db
from core.memory.context.builder import ContextBuilderService
from core.policy.engine import PolicyEngine, PolicyDecision
from apps.api.dependencies import get_actor_header, get_purpose_header

router = APIRouter(prefix="/v1/context", tags=["v1 Context Pipeline"])

class ContextBuildRequest(BaseModel):
    agent_id: Optional[str] = Field(default=None, description="Agent ID or name (falls back to X-Agent-Name header)")
    task_query: str = Field(..., min_length=1, description="Task query or context requirement for the agent")
    token_budget: int = Field(default=4000, ge=100, le=32000, description="Max token budget for the returned context bundle")
    namespace_path: Optional[str] = Field(default=None, description="Optional namespace constraint")
    purpose: Optional[str] = Field(default=None, description="Access intent justification")
    max_candidates: int = Field(default=30, ge=1, le=100)

class ContextBundleResponse(BaseModel):
    bundle_id: str
    query: str
    target_agent: str
    total_tokens_estimated: int
    token_budget_limit: int
    summary: str
    compaction_strategy: str = "none"
    memories_count: int
    memories: List[Dict[str, Any]]
    graph_edges_count: int
    graph_edges: List[Dict[str, Any]]
    created_at: str

@router.post("", response_model=ContextBundleResponse, status_code=status.HTTP_200_OK)
def build_context_bundle_endpoint(
    req: ContextBuildRequest,
    actor_name: str = Depends(get_actor_header),
    purpose: Optional[str] = Depends(get_purpose_header),
    db: Session = Depends(get_db)
):
    target_agent = req.agent_id or actor_name
    resolved_purpose = req.purpose or purpose

    try:
        bundle = ContextBuilderService.build_context_bundle(
            db=db,
            agent_id_or_name=target_agent,
            task_query=req.task_query,
            token_budget=req.token_budget,
            namespace_path=req.namespace_path,
            purpose=resolved_purpose,
            max_candidates=req.max_candidates
        )
        return bundle.to_dict()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))