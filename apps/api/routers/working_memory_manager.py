"""
Memora/apps/api/routers/working_memory_manager.py — Memora Working Memory Manager.

Short-term working memory, context window management, task-scoped
memory, and session-based context retention.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException

router = APIRouter()


@dataclass
class WorkingMemoryItem:
    """An item in working memory."""
    item_id: str
    session_id: str
    content: str
    category: str = "general"  # context, task, decision, reference
    importance: float = 0.5
    token_count: int = 0
    created_at: float = field(default_factory=time.time)
    last_accessed: float = 0.0
    access_count: int = 0


@dataclass
class SessionContext:
    """A session's working context."""
    session_id: str
    items: list[WorkingMemoryItem] = field(default_factory=list)
    token_budget: int = 4096
    total_tokens: int = 0
    created_at: float = field(default_factory=time.time)
    last_updated: float = 0.0


class WorkingMemoryManager:
    """Manager for short-term working memory."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionContext] = {}
        self._item_index: dict[str, str] = {}  # item_id -> session_id

    def create_session(self, session_id: str, token_budget: int = 4096) -> SessionContext:
        """Create a working memory session."""
        ctx = SessionContext(session_id=session_id, token_budget=token_budget)
        ctx.last_updated = time.time()
        self._sessions[session_id] = ctx
        return ctx

    def add_item(self, session_id: str, content: str, category: str = "general",
                 importance: float = 0.5) -> str:
        """Add an item to working memory."""
        ctx = self._sessions.get(session_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Session not found")
        item_id = f"wmi_{uuid.uuid4().hex[:12]}"
        tokens = max(1, len(content) // 4)
        if ctx.total_tokens + tokens > ctx.token_budget:
            # Evict lowest importance items
            ctx.items.sort(key=lambda x: x.importance)
            while ctx.items and ctx.total_tokens + tokens > ctx.token_budget:
                evicted = ctx.items.pop(0)
                ctx.total_tokens -= evicted.token_count
                del self._item_index[evicted.item_id]
        item = WorkingMemoryItem(
            item_id=item_id, session_id=session_id, content=content,
            category=category, importance=importance, token_count=tokens,
            last_accessed=time.time(),
        )
        ctx.items.append(item)
        ctx.total_tokens += tokens
        ctx.last_updated = time.time()
        self._item_index[item_id] = session_id
        return item_id

    def retrieve_context(self, session_id: str, limit: int = 10) -> list[WorkingMemoryItem]:
        """Retrieve working memory items for a session."""
        ctx = self._sessions.get(session_id)
        if not ctx:
            return []
        # Sort by importance and recency
        sorted_items = sorted(ctx.items, key=lambda x: (x.importance, x.last_accessed), reverse=True)
        for item in sorted_items:
            item.access_count += 1
            item.last_accessed = time.time()
        return sorted_items[:limit]

    def get_session_info(self, session_id: str) -> dict[str, Any]:
        """Get session info."""
        ctx = self._sessions.get(session_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Session not found")
        return {
            "session_id": ctx.session_id,
            "item_count": len(ctx.items),
            "total_tokens": ctx.total_tokens,
            "token_budget": ctx.token_budget,
            "last_updated": ctx.last_updated,
        }

    def clear_session(self, session_id: str) -> bool:
        """Clear a session's working memory."""
        ctx = self._sessions.get(session_id)
        if not ctx:
            return False
        for item in ctx.items:
            del self._item_index[item.item_id]
        ctx.items.clear()
        ctx.total_tokens = 0
        ctx.last_updated = time.time()
        return True

    def get_report(self) -> dict[str, Any]:
        """Get engine report."""
        return {
            "total_sessions": len(self._sessions),
            "total_items": len(self._item_index),
            "engine_status": "operational",
        }


engine = WorkingMemoryManager()


@router.post("/memora/working/sessions")
async def create_session(session_id: str, token_budget: int = 4096):
    """Create a working memory session."""
    ctx = engine.create_session(session_id, token_budget)
    return {"session_id": ctx.session_id, "token_budget": ctx.token_budget, "status": "created"}


@router.post("/memora/working/sessions/{session_id}/items")
async def add_item(session_id: str, content: str, category: str = "general", importance: float = 0.5):
    """Add an item to working memory."""
    item_id = engine.add_item(session_id, content, category, importance)
    return {"item_id": item_id, "status": "added"}


@router.get("/memora/working/sessions/{session_id}/context")
async def retrieve_context(session_id: str, limit: int = 10):
    """Retrieve working memory context."""
    items = engine.retrieve_context(session_id, limit)
    return {
        "session_id": session_id,
        "items": [
            {"content": i.content[:100], "category": i.category, "importance": i.importance}
            for i in items
        ],
    }


@router.get("/memora/working/sessions/{session_id}")
async def get_session_info(session_id: str):
    """Get session info."""
    return engine.get_session_info(session_id)


@router.delete("/memora/working/sessions/{session_id}")
async def clear_session(session_id: str):
    """Clear a session's working memory."""
    if not engine.clear_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "status": "cleared"}


@router.get("/memora/working/report")
async def working_report():
    """Get working memory report."""
    return engine.get_report()
