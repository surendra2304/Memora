"""
Memora/core/memory/context_service.py — Memora Context Database Service.

Memora's memory stack now has a second, complementary backend: the ``viking://``
context database. Markdown memories stay the source of truth; the context
database gives agents a *filesystem* view of the same knowledge with generated
directory abstracts (L0), subtree overviews (L1) and full text (L2), plus scoped
search and session commits.

This module is the piece that makes it usable by the running agent:

* ``MemoraContextService`` — one object the API, the mesh and the other agents
  call: ``write``/``read``/``ls``/``tree``/``grep``/``find``/``search``,
  ``remember``/``recall``, ``commit_session``, ``compile_wiki``, ``stats``.
  It also speaks the layered-recall protocol of the markdown store: every read
  can be requested at L0/L1/L2 and every recall returns abstracts first.
* ``router`` — REST surface mounted by ``Memora/apps/api/main.py`` under
  ``/v1/context`` (alongside the existing relational routes).
* ``fabric_bridge`` — the adapter ``mesh/memory_fabric.py`` uses so every agent
  in the universe can keep a viking subtree next to its SQLite memories.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

_HERE = Path(__file__).resolve().parent
_MODULE_PATH = _HERE / "real_viking_context.py"
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:  # normal package import
    from . import real_viking_context as vk
except Exception:  # pragma: no cover - loaded as a bare file
    spec = importlib.util.spec_from_file_location("memora_viking_context", _MODULE_PATH)
    vk = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["memora_viking_context"] = vk
    spec.loader.exec_module(vk)  # type: ignore[union-attr]

try:  # pragma: no cover - only when FastAPI is installed
    from fastapi import APIRouter, HTTPException
    from pydantic import BaseModel, Field

    _ROUTER_AVAILABLE = True
except Exception:  # pragma: no cover
    APIRouter = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]
    BaseModel = object  # type: ignore[assignment]

    def Field(*_args: Any, **_kwargs: Any) -> Any:  # type: ignore[misc]
        return None

    _ROUTER_AVAILABLE = False

LAYERS = ("L0", "L1", "L2")
SCHEME = "viking://"


@dataclass
class RecallHit:
    """One recall result: abstract first, path always, full read on demand."""

    uri: str
    score: float
    abstract: str
    level: str = "L0"
    kind: str = "file"

    def as_dict(self) -> dict[str, Any]:
        return {"uri": self.uri, "score": self.score, "abstract": self.abstract,
                "level": self.level, "kind": self.kind}


class MemoraContextService:
    """Service facade over the viking context database."""

    def __init__(self, user: str = "memora", root: str | None = None,
                 database: Any | None = None) -> None:
        self.user = user
        self.root = root or f"{SCHEME}user/{user}"
        self.db = database or vk.ContextDatabase(user=user)
        self._lock = threading.Lock()

    # -- filesystem -------------------------------------------------------
    def write(self, uri: str, content: str, tags: Iterable[str] = ()) -> dict[str, Any]:
        try:
            node = self.db.write(uri, content, tags)
        except (ValueError, FileExistsError, IsADirectoryError) as exc:
            raise ValueError(str(exc)) from exc
        return {"uri": node.path.uri, "tokens": node.tokens, "tags": list(node.tags)}

    def read(self, uri: str, level: str = "L2") -> dict[str, Any]:
        level = self._level(level)
        try:
            text = self.db.read(uri, level)
        except FileNotFoundError as exc:
            raise KeyError(str(exc)) from exc
        except IsADirectoryError:
            summary = self.db.summary(uri)
            return {"uri": uri, "level": level,
                    "content": summary.overview if level != "L0" else summary.abstract,
                    "is_dir": True}
        return {"uri": uri, "level": level, "content": text, "is_dir": False,
                "tokens": len(text.split()) + len(text) // 8}

    def ls(self, uri: str = SCHEME) -> list[dict[str, Any]]:
        try:
            return self.db.ls(uri)
        except FileNotFoundError as exc:
            raise KeyError(str(exc)) from exc

    def tree(self, uri: str | None = None, depth: int = 3) -> str:
        return self.db.tree(uri or SCHEME, depth)

    def grep(self, pattern: str, uri: str | None = None) -> list[dict[str, Any]]:
        return [hit.as_dict() for hit in self.db.grep(pattern, uri or SCHEME)]

    def find(self, pattern: str, uri: str | None = None) -> list[str]:
        return self.db.find(pattern, uri or SCHEME)

    def stat(self, uri: str) -> dict[str, Any]:
        try:
            return self.db.stat(uri)
        except FileNotFoundError as exc:
            raise KeyError(str(exc)) from exc

    # -- memory -----------------------------------------------------------
    def remember(self, kind: str, key: str, title: str, body: str,
                 confidence: float = 0.8) -> dict[str, Any]:
        uri = self.db.remember(kind, key, title, body, confidence)
        return {"uri": uri, "kind": kind, "confidence": confidence}

    def recall(self, query: str, limit: int = 5, level: str = "L0",
               uri: str | None = None) -> list[dict[str, Any]]:
        """Scoped recall: abstracts by default, full text on request."""
        level = self._level(level)
        hits = self.db.search(query, root=uri or self.root, level=level, limit=limit)
        return [RecallHit(uri=hit.uri, score=hit.score, abstract=hit.abstract,
                          level=hit.level, kind=hit.kind).as_dict() for hit in hits]

    def commit_session(self, session_id: str, turns: list[dict[str, Any]]) -> dict[str, Any]:
        session = vk.Session(session_id, self.user)
        for turn in turns:
            session.add(str(turn.get("role", "user")), str(turn.get("content", "")))
        result = self.db.commit_session(session)
        return result.as_dict()

    def compile_wiki(self, persist: bool = True) -> dict[str, Any]:
        report = self.db.compile(root=self.root, persist=persist)
        payload = report.as_dict()
        payload["index"] = report.render_index()
        return payload

    def layers(self) -> dict[str, str]:
        return dict(vk.LAYERS)

    def stats(self) -> dict[str, Any]:
        payload = self.db.stats()
        payload["root"] = self.root
        return payload

    def bootstrap(self, documents: Iterable[dict[str, Any]]) -> int:
        """Bulk-seed documents (used by the mesh bridge and migrations)."""
        written = 0
        with self._lock:
            for document in documents:
                uri = document.get("uri") or (
                    f"{self.root}/resources/{vk.slugify(document.get('title', 'doc'))}.md")
                content = document.get("content", "")
                if not content.strip():
                    continue
                self.db.write(uri, content, tags=tuple(document.get("tags", ())))
                written += 1
        return written

    @staticmethod
    def _level(level: str) -> str:
        key = (level or "L2").upper()
        if key not in LAYERS:
            raise ValueError(f"unknown level {level!r}; use L0, L1 or L2")
        return key


# ── Shared instance used by the API ───────────────────────────────────────

_default: MemoraContextService | None = None


def get_service() -> MemoraContextService:
    global _default
    if _default is None:
        _default = MemoraContextService(user=os.environ.get("MEMORA_CONTEXT_USER", "memora"))
    return _default


def reset_service(service: MemoraContextService | None = None) -> MemoraContextService:
    """Test/helper hook: replace the process-wide service."""
    global _default
    _default = service or MemoraContextService()
    return _default


# ── Mesh bridge ───────────────────────────────────────────────────────────

@dataclass
class FabricBridge:
    """Keeps a per-agent viking subtree alongside the shared memory fabric."""

    service: MemoraContextService
    agents: dict[str, str] = field(default_factory=dict)

    def agent_root(self, agent: str) -> str:
        slug = vk.slugify(agent, 3, 24)
        self.agents[agent.lower()] = f"{SCHEME}user/{slug}"
        return self.agents[agent.lower()]

    def save(self, agent: str, task: str, lesson: str, tags: Iterable[str] = (),
             kind: str = "lesson") -> str:
        """Write an agent memory into ``<agent>/memories/<kind>/<slug>.md``."""
        root = self.agent_root(agent)
        kind_slug = vk.slugify(kind, 2, 24)
        tag_list = list(tags)
        uri = f"{root}/memories/{kind_slug}/{vk.slugify(task)}.md"
        self.service.write(
            uri,
            f"---\ntitle: {task}\nkind: {kind_slug}\nagent: {agent}\n"
            f"tags: {', '.join(tag_list)}\n---\n\n# {task}\n\n{lesson}\n",
            tags=tuple(tag_list),
        )
        return uri

    def recall(self, agent: str, query: str, limit: int = 3) -> list[dict[str, Any]]:
        return self.service.recall(query, limit=limit, uri=self.agent_root(agent))

    def summary(self, agent: str) -> str:
        return self.service.db.summary(self.agent_root(agent)).abstract


# ── REST surface ──────────────────────────────────────────────────────────

if _ROUTER_AVAILABLE:

    class WriteRequest(BaseModel):  # type: ignore[misc]
        uri: str = Field(min_length=1)
        content: str = ""
        tags: list[str] = Field(default_factory=list)

    class ReadQuery(BaseModel):  # type: ignore[misc]
        level: str = "L2"

    class RememberRequest(BaseModel):  # type: ignore[misc]
        kind: str = Field(default="fact")
        key: str = Field(min_length=1)
        title: str = Field(min_length=1)
        body: str = Field(min_length=1)
        confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    class TurnModel(BaseModel):  # type: ignore[misc]
        role: str = "user"
        content: str = ""

    class CommitRequest(BaseModel):  # type: ignore[misc]
        session_id: str = Field(min_length=1)
        turns: list[TurnModel] = Field(default_factory=list)

    router = APIRouter(prefix="/v1/context", tags=["Memora Context Database"])

    @router.get("/stats")
    def stats() -> dict[str, Any]:
        return get_service().stats()

    @router.get("/layers")
    def layers() -> dict[str, Any]:
        return {"layers": get_service().layers()}

    @router.get("/ls")
    def ls(uri: str = SCHEME) -> dict[str, Any]:
        try:
            return {"uri": uri, "entries": get_service().ls(uri)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/tree")
    def tree(uri: str = SCHEME, depth: int = 3) -> dict[str, Any]:
        return {"uri": uri, "tree": get_service().tree(uri, depth)}

    @router.get("/read")
    def read(uri: str, level: str = "L2") -> dict[str, Any]:
        try:
            return get_service().read(uri, level)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post("/write")
    def write(request: WriteRequest) -> dict[str, Any]:
        try:
            return get_service().write(request.uri, request.content, request.tags)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/grep")
    def grep(pattern: str, uri: str = SCHEME) -> dict[str, Any]:
        return {"pattern": pattern, "hits": get_service().grep(pattern, uri)}

    @router.get("/find")
    def find(pattern: str, uri: str = SCHEME) -> dict[str, Any]:
        return {"pattern": pattern, "matches": get_service().find(pattern, uri)}

    @router.get("/search")
    def search(query: str, level: str = "L0", limit: int = 5) -> dict[str, Any]:
        try:
            return {"query": query, "hits": get_service().recall(query, limit, level)}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.post("/remember")
    def remember(request: RememberRequest) -> dict[str, Any]:
        return get_service().remember(request.kind, request.key, request.title,
                                      request.body, request.confidence)

    @router.post("/sessions/commit")
    def commit(request: CommitRequest) -> dict[str, Any]:
        return get_service().commit_session(
            request.session_id, [turn.model_dump() for turn in request.turns])

    @router.post("/compile")
    def compile_wiki(persist: bool = True) -> dict[str, Any]:
        return get_service().compile_wiki(persist=persist)

else:  # pragma: no cover
    router = None


__all__ = [
    "FabricBridge",
    "MemoraContextService",
    "RecallHit",
    "get_service",
    "reset_service",
    "router",
    "vk",
]
