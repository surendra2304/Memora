from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from core.memory.real_context_mode import (
        ContextCompressor,
        ToolOutputSandbox,
        SessionMemory,
        ContextRouter,
        MCPHookEngine,
        ContextModeManager,
    )
except ImportError:
    from Memora.core.memory.real_context_mode import (
        ContextCompressor,
        ToolOutputSandbox,
        SessionMemory,
        ContextRouter,
        MCPHookEngine,
        ContextModeManager,
    )

logger = logging.getLogger("memora.real_context_routes")
router = APIRouter(prefix="/context-mode", tags=["Context Mode Optimization"])

_manager = ContextModeManager()


class ContextOptimizeRequest(BaseModel):
    content: str
    platform: str = "claude_code"


class ToolSandboxRequest(BaseModel):
    tool_type: str = "git"  # "git", "test", "build", "general"
    output: str
    max_lines: int = 20


class BatchSandboxRequest(BaseModel):
    outputs: Dict[str, str]


class SessionStoreRequest(BaseModel):
    key: str
    value: str
    category: str = "general"


@router.post("/optimize")
def optimize_context(req: ContextOptimizeRequest):
    """Optimize context window for target LLM platform with intelligent compression and sandboxing."""
    result = _manager.optimize_context(req.content, req.platform)
    limit = ContextRouter.platform_context_limit(req.platform)
    hooks_supported = ContextRouter.platform_supports_hooks(req.platform)

    return {
        "status": "success",
        "engine": "ContextMode",
        "platform": req.platform,
        "platform_token_limit": limit,
        "hooks_supported": hooks_supported,
        "optimization": result,
    }


@router.post("/sandbox-tool")
def sandbox_tool_output(req: ToolSandboxRequest):
    """Sandbox and compress verbose tool outputs (git diff, test runs, build logs)."""
    sandboxed = ToolOutputSandbox.sandbox_by_type(req.output, req.tool_type)
    proc = _manager.process_tool_output(req.tool_type, req.output)

    return {
        "status": "success",
        "engine": "ContextMode",
        "tool_type": req.tool_type,
        "sandboxed_output": sandboxed,
        "metrics": proc,
    }


@router.post("/sandbox-batch")
def sandbox_batch_outputs(req: BatchSandboxRequest):
    """Sandbox multiple tool outputs concurrently and calculate overall context savings."""
    res = _manager.sandbox_all_outputs(req.outputs)
    return {
        "status": "success",
        "engine": "ContextMode",
        "batch_savings": res,
    }


@router.post("/session/store")
def store_session_memory(req: SessionStoreRequest):
    """Persist context entries into active session memory."""
    _manager.persist_session_memory(req.key, req.value, req.category)
    return {
        "status": "success",
        "engine": "ContextMode",
        "key": req.key,
        "category": req.category,
        "stored": True,
    }


@router.get("/session/retrieve")
def retrieve_session_memory(key: str = Query(..., description="Memory key to retrieve")):
    """Retrieve persisted memory from active session context."""
    val = _manager.retrieve_session_memory(key)
    if val is None:
        raise HTTPException(status_code=404, detail=f"Key '{key}' not found in session memory")
    return {
        "status": "success",
        "engine": "ContextMode",
        "key": key,
        "value": val,
    }


@router.get("/platforms")
def list_platforms():
    """List supported AI agent platforms, context window limits, and hook capabilities."""
    platforms = [
        "claude_code", "cursor", "copilot", "chatgpt", "gemini_cli",
        "aider", "windsurf", "devin", "openhands", "cline"
    ]
    info = {
        p: {
            "token_limit": ContextRouter.platform_context_limit(p),
            "hooks_supported": ContextRouter.platform_supports_hooks(p),
        }
        for p in platforms
    }
    return {
        "status": "success",
        "engine": "ContextMode",
        "platforms": info,
    }
