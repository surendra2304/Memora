"""
Memora/core/memory/real_viking_context.py — Real Viking Context Database.

Memora's context database is a virtual filesystem: every resource, memory,
skill and session is a file addressable as ``viking://...``. Directories are
not dumb containers — each one carries generated summaries, so an agent can
read a whole subtree at L0 (abstract) or L1 (overview) before paying for L2
(full content).

Real working implementations:

- VikingPath: ``viking://`` URI parsing, validation, parents and children
- strip_frontmatter / abstract_of / overview_of: text projection primitives
- LayerProjector: L0 / L1 / L2 projection of any document
- VfsNode / DirSummary / VikingFS: an in-memory tree with mkdir, write, read,
  ls, tree, stat, move, remove, grep, find, generated directory summaries and
  token accounting
- Bm25Ranker: real BM25 scoring over virtual documents
- ScopedSearchEngine: semantic-ish search restricted to a subtree, with
  layer selection, kind/tag filters and an explanation mode
- SessionTurn / Session: transcript capture with token accounting
- MemoryExtractor: rule-driven extraction of durable preferences, decisions,
  todos and facts into markdown memories
- CommitResult / ContextDatabase: session commit (archive + extract + supersede)
- WikiCompiler: wiki page compilation, explicit and tag links, orphan/hub
  detection and connected-component counting

Standard library only.
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator

SCHEME = "viking://"
SEGMENT_RE = re.compile(r"^[A-Za-z0-9._\-]+$")
WORD_RE = re.compile(r"[a-z0-9]+")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")
BULLET_RE = re.compile(r"^\s*[-*+]\s+")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
WIKI_LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
VIKING_REF_RE = re.compile(r"viking://[A-Za-z0-9._\-/]+")

LAYERS = {"L0": "abstract", "L1": "overview", "L2": "full"}


# ── Paths ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VikingPath:
    """An immutable, validated ``viking://`` path."""

    segments: tuple[str, ...] = ()

    @classmethod
    def parse(cls, uri: str) -> "VikingPath":
        raw = (uri or "").strip()
        if not raw.startswith(SCHEME):
            raise ValueError(f"not a viking uri: {uri!r}")
        rest = raw[len(SCHEME):].strip("/")
        if not rest:
            return cls(())
        parts = tuple(rest.split("/"))
        if len(parts) == 1 and parts[0] == "":
            return cls(())
        for part in parts:
            if not part or part in (".", "..") or not SEGMENT_RE.match(part):
                raise ValueError(f"invalid path segment {part!r}")
        return cls(parts)

    @property
    def uri(self) -> str:
        return SCHEME + "/".join(self.segments)

    @property
    def name(self) -> str:
        return self.segments[-1] if self.segments else ""

    @property
    def scope(self) -> str:
        return self.segments[0] if self.segments else ""

    @property
    def is_root(self) -> bool:
        return not self.segments

    @property
    def parent(self) -> "VikingPath | None":
        if self.is_root:
            return None
        return VikingPath(self.segments[:-1])

    def child(self, name: str) -> "VikingPath":
        if not name or "/" in name or not SEGMENT_RE.match(name):
            raise ValueError(f"invalid child name {name!r}")
        return VikingPath(self.segments + (name,))

    def relative_to(self, other: "VikingPath") -> tuple[str, ...]:
        if self.segments[:len(other.segments)] != other.segments:
            raise ValueError(f"{self.uri} is not inside {other.uri}")
        return self.segments[len(other.segments):]

    def is_inside(self, other: "VikingPath") -> bool:
        return self.segments[:len(other.segments)] == other.segments

    def __str__(self) -> str:
        return self.uri


# ── Text projection ───────────────────────────────────────────────────────

def strip_frontmatter(text: str) -> str:
    match = FRONTMATTER_RE.match(text or "")
    return text[match.end():] if match else (text or "")


def parse_frontmatter(text: str) -> dict[str, str]:
    match = FRONTMATTER_RE.match(text or "")
    if not match:
        return {}
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            fields[key.strip().lower()] = value.strip()
    return fields


def collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def first_sentence(text: str) -> str:
    body = collapse(strip_frontmatter(text))
    if not body:
        return ""
    return SENTENCE_RE.split(body)[0].strip()


def abstract_of(text: str, max_chars: int = 200) -> str:
    """L0: the single most informative line, hard-capped."""
    body = strip_frontmatter(text)
    for raw in body.splitlines():
        line = collapse(BULLET_RE.sub("", raw))
        if not line or line.startswith("#") or set(line) <= {"-"}:
            continue
        if len(line) > max_chars:
            cut = line[:max_chars].rsplit(" ", 1)[0] or line[:max_chars]
            return cut.rstrip(" ,;:") + "…"
        return line
    for raw in body.splitlines():
        if raw.strip():
            return collapse(raw.lstrip("#"))[:max_chars]
    return ""


def overview_of(text: str, max_lines: int = 8, max_chars: int = 600) -> str:
    """L1: headings, bullets and the opening statement, capped."""
    body = strip_frontmatter(text)
    picked: list[str] = []
    used = 0
    seen_prose = False
    for raw in body.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or set(stripped) <= {"-"}:
            continue
        if HEADING_RE.match(stripped):
            candidate = stripped
        elif BULLET_RE.match(line):
            candidate = "- " + collapse(BULLET_RE.sub("", stripped))
        elif not seen_prose:
            candidate = collapse(stripped)
            seen_prose = True
        else:
            continue
        if len(candidate) > 140:
            candidate = candidate[:140].rsplit(" ", 1)[0] + "…"
        if used + len(candidate) > max_chars:
            break
        picked.append(candidate)
        used += len(candidate) + 1
        if len(picked) >= max_lines:
            break
    return "\n".join(picked)


def glob_match(pattern: str, value: str) -> bool:
    regex = "^" + "".join(
        ".*" if char == "*" else "." if char == "?" else re.escape(char)
        for char in pattern) + "$"
    return re.match(regex, value) is not None


@dataclass
class LayerProjector:
    """Projects a document into the L0 / L1 / L2 read levels."""

    abstract_chars: int = 200
    overview_lines: int = 8
    overview_chars: int = 600

    def project(self, text: str, level: str = "L2") -> str:
        key = (level or "L2").upper()
        if key not in LAYERS:
            raise ValueError(f"unknown level {level!r}; use L0, L1 or L2")
        if key == "L2":
            return text or ""
        if key == "L0":
            return abstract_of(text, self.abstract_chars)
        return overview_of(text, self.overview_lines, self.overview_chars)

    def describe(self) -> dict[str, str]:
        return dict(LAYERS)


PROJECTOR = LayerProjector()


# ── Filesystem nodes ──────────────────────────────────────────────────────

@dataclass
class VfsNode:
    """A file or directory in the virtual context filesystem."""

    path: VikingPath
    is_dir: bool = False
    content: str = ""
    tags: tuple[str, ...] = ()
    created: float = field(default_factory=time.time)
    modified: float = field(default_factory=time.time)
    children: dict[str, "VfsNode"] = field(default_factory=dict)

    @property
    def tokens(self) -> int:
        if self.is_dir:
            return sum(child.tokens for child in self.children.values())
        return len(WORD_RE.findall(self.content)) + len(self.content) // 8

    @property
    def lines(self) -> int:
        return len(self.content.splitlines())

    def abstract(self, max_chars: int = 200) -> str:
        return abstract_of(self.content, max_chars)

    def overview(self, max_lines: int = 8) -> str:
        return overview_of(self.content, max_lines)

    def read(self, level: str = "L2") -> str:
        return PROJECTOR.project(self.content, level)

    def as_dict(self) -> dict[str, Any]:
        return {
            "uri": self.path.uri,
            "name": self.path.name,
            "is_dir": self.is_dir,
            "tokens": self.tokens,
            "tags": list(self.tags),
            "modified": self.modified,
        }


@dataclass
class DirSummary:
    """Generated summary attached to every directory."""

    uri: str
    abstract: str
    overview: str
    files: int
    dirs: int
    tokens: int

    def as_dict(self) -> dict[str, Any]:
        return {"uri": self.uri, "abstract": self.abstract, "overview": self.overview,
                "files": self.files, "dirs": self.dirs, "tokens": self.tokens}


@dataclass
class GrepHit:
    uri: str
    line: int
    text: str

    def as_dict(self) -> dict[str, Any]:
        return {"uri": self.uri, "line": self.line, "text": self.text}


class VikingFS:
    """An in-memory ``viking://`` tree with generated directory summaries."""

    def __init__(self) -> None:
        self.root = VfsNode(VikingPath(), is_dir=True)
        self._cache: dict[str, DirSummary] = {}

    # -- internals --------------------------------------------------------
    def _node(self, path: VikingPath) -> VfsNode | None:
        node = self.root
        for segment in path.segments:
            if not node.is_dir:
                return None
            node = node.children.get(segment)
            if node is None:
                return None
        return node

    def _require(self, path: VikingPath) -> VfsNode:
        node = self._node(path)
        if node is None:
            raise FileNotFoundError(path.uri)
        return node

    def _invalidate(self) -> None:
        self._cache.clear()

    def _touch(self, path: VikingPath) -> None:
        self._invalidate()
        now = time.time()
        current = path.parent
        while current is not None:
            node = self._node(current)
            if node is not None:
                node.modified = now
            current = current.parent

    def _ensure_dir(self, path: VikingPath) -> VfsNode:
        node = self.root
        for segment in path.segments:
            child = node.children.get(segment)
            if child is None:
                child = VfsNode(VikingPath(node.path.segments + (segment,)), is_dir=True)
                node.children[segment] = child
                self._invalidate()
            elif not child.is_dir:
                raise NotADirectoryError(child.path.uri)
            node = child
        return node

    # -- mutation ---------------------------------------------------------
    def mkdir(self, uri: str) -> VfsNode:
        path = VikingPath.parse(uri)
        node = self._node(path)
        if node is not None:
            if node.is_dir:
                return node
            raise FileExistsError(path.uri)
        node = self._ensure_dir(path)
        node.modified = time.time()
        self._touch(path)
        return node

    def write(self, uri: str, content: str, tags: Iterable[str] = ()) -> VfsNode:
        path = VikingPath.parse(uri)
        if path.is_root:
            raise IsADirectoryError(path.uri)
        existing = self._node(path)
        if existing is not None and existing.is_dir:
            raise IsADirectoryError(path.uri)
        parent = self._ensure_dir(path.parent or VikingPath())
        now = time.time()
        if existing is None:
            node = VfsNode(path, is_dir=False, content=content,
                           tags=tuple(tags), created=now, modified=now)
            parent.children[path.name] = node
        else:
            existing.content = content
            existing.tags = tuple(tags)
            existing.modified = now
            node = existing
        self._touch(path)
        return node

    def remove(self, uri: str) -> bool:
        path = VikingPath.parse(uri)
        if path.is_root:
            raise ValueError("cannot remove the viking root")
        node = self._node(path)
        if node is None:
            return False
        parent = self._node(path.parent or VikingPath())
        if parent is not None:
            parent.children.pop(path.name, None)
        self._touch(path)
        return True

    def move(self, source: str, target: str) -> VfsNode:
        src_path = VikingPath.parse(source)
        dst_path = VikingPath.parse(target)
        node = self._require(src_path)
        if src_path.is_root:
            raise ValueError("cannot move the viking root")
        if self._node(dst_path) is not None:
            raise FileExistsError(dst_path.uri)
        if dst_path.segments[:len(src_path.segments)] == src_path.segments and node.is_dir:
            raise ValueError("cannot move a directory inside itself")
        parent = self._ensure_dir(dst_path.parent or VikingPath())
        src_parent = self._node(src_path.parent or VikingPath())
        if src_parent is not None:
            src_parent.children.pop(src_path.name, None)
        self._rebase(node, dst_path)
        parent.children[dst_path.name] = node
        self._touch(src_path)
        self._touch(dst_path)
        return node

    def _rebase(self, node: VfsNode, path: VikingPath) -> None:
        node.path = path
        if node.is_dir:
            for name, child in list(node.children.items()):
                self._rebase(child, path.child(name))

    # -- reads ------------------------------------------------------------
    def exists(self, uri: str) -> bool:
        return self._node(VikingPath.parse(uri)) is not None

    def read(self, uri: str, level: str = "L2") -> str:
        path = VikingPath.parse(uri)
        node = self._require(path)
        if node.is_dir:
            raise IsADirectoryError(path.uri)
        return node.read(level)

    def ls(self, uri: str = SCHEME) -> list[dict[str, Any]]:
        node = self._require(VikingPath.parse(uri))
        if not node.is_dir:
            raise NotADirectoryError(node.path.uri)
        entries = []
        for child in sorted(node.children.values(),
                            key=lambda item: (not item.is_dir, item.path.name)):
            entries.append({
                "name": child.path.name,
                "uri": child.path.uri,
                "is_dir": child.is_dir,
                "tokens": child.tokens,
                "abstract": (self.dir_summary(child.path.uri).abstract
                             if child.is_dir else child.abstract()),
                "modified": child.modified,
            })
        return entries

    def tree(self, uri: str = SCHEME, depth: int = 3) -> str:
        path = VikingPath.parse(uri)
        node = self._require(path)
        if depth < 1:
            raise ValueError("depth must be >= 1")
        lines = [path.uri]

        def render(current: VfsNode, prefix: str, level: int) -> None:
            if level > depth or not current.is_dir:
                return
            entries = sorted(current.children.values(),
                             key=lambda item: (not item.is_dir, item.path.name))
            for index, child in enumerate(entries):
                last = index == len(entries) - 1
                connector = "└── " if last else "├── "
                suffix = "/" if child.is_dir else f" ({child.tokens} tokens)"
                lines.append(prefix + connector + child.path.name + suffix)
                if child.is_dir:
                    render(child, prefix + ("    " if last else "│   "), level + 1)

        render(node, "", 1)
        return "\n".join(lines)

    def stat(self, uri: str) -> dict[str, Any]:
        path = VikingPath.parse(uri)
        node = self._require(path)
        payload = node.as_dict()
        payload["lines"] = node.lines
        if node.is_dir:
            summary = self.dir_summary(path.uri)
            payload.update({"files": summary.files, "dirs": summary.dirs,
                            "abstract": summary.abstract})
        else:
            payload["abstract"] = node.abstract()
        return payload

    def walk(self, uri: str = SCHEME) -> list[VikingPath]:
        node = self._require(VikingPath.parse(uri))
        out: list[VikingPath] = []

        def visit(current: VfsNode) -> None:
            for child in sorted(current.children.values(), key=lambda item: item.path.name):
                out.append(child.path)
                if child.is_dir:
                    visit(child)

        if node.is_dir:
            visit(node)
        else:
            out.append(node.path)
        return out

    def files(self, uri: str = SCHEME) -> list[str]:
        return [path.uri for path in self.walk(uri)
                if (node := self._node(path)) is not None and not node.is_dir]

    # -- queries ----------------------------------------------------------
    def summary(self, uri: str) -> DirSummary:
        """Alias for :meth:`dir_summary`, matching the ``ov summary`` command."""
        return self.dir_summary(uri)

    def dir_summary(self, uri: str) -> DirSummary:
        path = VikingPath.parse(uri)
        node = self._require(path)
        if not node.is_dir:
            raise NotADirectoryError(path.uri)
        cached = self._cache.get(path.uri)
        if cached is not None:
            return cached
        children = sorted(node.children.values(), key=lambda item: item.path.name)
        files = [child for child in children if not child.is_dir]
        dirs = [child for child in children if child.is_dir]
        abstracts: list[str] = []
        for child in files[:4]:
            text = child.abstract(80)
            if text:
                abstracts.append(f"{child.path.name}: {text}")
        for child in dirs[:3]:
            nested = self.dir_summary(child.path.uri).abstract
            if nested:
                abstracts.append(f"{child.path.name}/: {nested}")
        abstract = f"{len(files)} file(s), {len(dirs)} folder(s)"
        if abstracts:
            abstract += " — " + "; ".join(abstracts)
        abstract = abstract[:220] + ("…" if len(abstract) > 220 else "")
        overview_lines = [f"{path.uri} — {len(files)} file(s), {len(dirs)} folder(s)"]
        for child in children:
            if child.is_dir:
                nested = self.dir_summary(child.path.uri)
                overview_lines.append(f"- {child.path.name}/ — {nested.abstract}")
            else:
                overview_lines.append(f"- {child.path.name} ({child.tokens} tokens) — {child.abstract(90)}")
        overview = "\n".join(overview_lines)[:900]
        summary = DirSummary(
            uri=path.uri, abstract=abstract, overview=overview,
            files=len(files), dirs=len(dirs),
            tokens=sum(child.tokens for child in children),
        )
        self._cache[path.uri] = summary
        return summary

    def grep(self, pattern: str, uri: str = SCHEME, ignore_case: bool = False
             ) -> list[GrepHit]:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
        hits: list[GrepHit] = []
        for file_uri in self.files(uri):
            node = self._node(VikingPath.parse(file_uri))
            if node is None:
                continue
            for number, line in enumerate(node.content.splitlines(), start=1):
                if regex.search(line):
                    hits.append(GrepHit(file_uri, number, line.strip()))
        return hits

    def find(self, pattern: str, uri: str = SCHEME) -> list[str]:
        return [file_uri for file_uri in self.files(uri)
                if glob_match(pattern, VikingPath.parse(file_uri).name)]

    # -- views ------------------------------------------------------------
    def read_subtree(self, uri: str, level: str = "L1") -> str:
        """Read a directory as one document at the requested layer."""
        path = VikingPath.parse(uri)
        node = self._require(path)
        if not node.is_dir:
            return node.read(level)
        summary = self.dir_summary(path.uri)
        if level.upper() == "L0":
            return summary.abstract
        if level.upper() == "L2":
            parts = [summary.overview]
            for file_uri in self.files(path.uri):
                child = self._node(VikingPath.parse(file_uri))
                if child is not None:
                    parts.append(f"## {child.path.uri}\n{child.content}")
            return "\n\n".join(parts)
        return summary.overview


# ── Ranking ───────────────────────────────────────────────────────────────

class Bm25Ranker:
    """Okapi BM25 ranking over virtual documents."""

    def __init__(self, documents: dict[str, str], k1: float = 1.5, b: float = 0.75) -> None:
        if not 0.0 <= b <= 1.0:
            raise ValueError("b must be within [0, 1]")
        if k1 <= 0:
            raise ValueError("k1 must be positive")
        self.k1 = k1
        self.b = b
        self.tokens: dict[str, list[str]] = {
            uri: WORD_RE.findall((text or "").lower())
            for uri, text in documents.items()
            if (text or "").strip()
        }
        self.frequencies = {uri: Counter(tokens) for uri, tokens in self.tokens.items()}
        self.count = len(self.tokens)
        self.average_length = (
            sum(len(tokens) for tokens in self.tokens.values()) / self.count
            if self.count else 1.0)
        self.document_frequency: Counter[str] = Counter()
        for tokens in self.tokens.values():
            self.document_frequency.update(set(tokens))

    def idf(self, term: str) -> float:
        df = self.document_frequency.get(term, 0)
        return math.log(1.0 + (self.count - df + 0.5) / (df + 0.5))

    def scores(self, query: str) -> dict[str, float]:
        terms = WORD_RE.findall((query or "").lower())
        if not terms or not self.count:
            return {}
        results: dict[str, float] = {}
        for uri, frequencies in self.frequencies.items():
            length = len(self.tokens[uri])
            score = 0.0
            for term in terms:
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + self.k1 * (
                    1.0 - self.b + self.b * length / (self.average_length or 1.0))
                score += self.idf(term) * (frequency * (self.k1 + 1.0)) / denominator
            if score > 0:
                results[uri] = round(score, 4)
        return dict(sorted(results.items(), key=lambda item: (-item[1], item[0])))

    def rank(self, query: str, limit: int | None = None) -> list[tuple[str, float]]:
        ranked = list(self.scores(query).items())
        return ranked[:limit] if limit is not None else ranked


@dataclass
class SearchHit:
    uri: str
    score: float
    level: str
    kind: str
    abstract: str

    def as_dict(self) -> dict[str, Any]:
        return {"uri": self.uri, "score": self.score, "level": self.level,
                "kind": self.kind, "abstract": self.abstract}


class ScopedSearchEngine:
    """Search restricted to a subtree, with layer and metadata filters."""

    def __init__(self, fs: VikingFS) -> None:
        self.fs = fs

    def _documents(self, root: VikingPath, level: str,
                   kinds: Iterable[str] | None, tags: Iterable[str] | None
                   ) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
        wanted_kinds = set(kinds) if kinds else {"file", "dir"}
        wanted_tags = {tag.lower() for tag in tags} if tags else set()
        documents: dict[str, str] = {}
        meta: dict[str, tuple[str, str]] = {}
        for path in self.fs.walk(root.uri):
            node = self.fs._node(path)
            if node is None:
                continue
            kind = "dir" if node.is_dir else "file"
            if kind not in wanted_kinds:
                continue
            if wanted_tags and not (wanted_tags & {tag.lower() for tag in node.tags}):
                continue
            if node.is_dir:
                summary = self.fs.dir_summary(path.uri)
                text = {"L0": summary.abstract, "L1": summary.overview,
                        "L2": summary.overview}.get(level.upper(), summary.overview)
                meta[path.uri] = (kind, summary.abstract)
            else:
                text = node.read(level)
                meta[path.uri] = (kind, node.abstract())
            documents[path.uri] = text
        return documents, meta

    def search(self, query: str, root: str = SCHEME, level: str = "L1",
               limit: int = 10, kinds: Iterable[str] | None = None,
               tags: Iterable[str] | None = None) -> list[SearchHit]:
        if not (query or "").strip():
            return []
        path = VikingPath.parse(root)
        self.fs._require(path)
        level = (level or "L1").upper()
        if level not in LAYERS:
            raise ValueError(f"unknown level {level!r}")
        documents, meta = self._documents(path, level, kinds, tags)
        ranked = Bm25Ranker(documents).rank(query)
        hits: list[SearchHit] = []
        for uri, score in ranked[:max(0, limit)]:
            kind, abstract = meta[uri]
            hits.append(SearchHit(uri=uri, score=score, level=level,
                                  kind=kind, abstract=abstract))
        return hits

    def explain(self, query: str, root: str = SCHEME, level: str = "L1") -> dict[str, Any]:
        documents, _ = self._documents(VikingPath.parse(root), level, None, None)
        ranker = Bm25Ranker(documents)
        terms = sorted(set(WORD_RE.findall((query or "").lower())))
        return {
            "query": query,
            "root": root,
            "level": level,
            "terms": terms,
            "candidates": len(documents),
            "matched": len(ranker.scores(query)),
            "idf": {term: round(ranker.idf(term), 4) for term in terms},
            "top": [uri for uri, _ in ranker.rank(query, 3)],
        }


# ── Sessions ──────────────────────────────────────────────────────────────

ROLES = ("user", "assistant", "tool", "system")


@dataclass
class SessionTurn:
    role: str
    content: str
    timestamp: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.role not in ROLES:
            raise ValueError(f"unknown role {self.role!r}")

    @property
    def tokens(self) -> int:
        return len(WORD_RE.findall(self.content)) + len(self.content) // 8

    def as_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content,
                "timestamp": self.timestamp, "tokens": self.tokens}


@dataclass
class Session:
    session_id: str
    user_id: str = "default"
    turns: list[SessionTurn] = field(default_factory=list)
    started: float = field(default_factory=time.time)

    def add(self, role: str, content: str) -> SessionTurn:
        turn = SessionTurn(role=role, content=content)
        self.turns.append(turn)
        return turn

    def tokens(self) -> int:
        return sum(turn.tokens for turn in self.turns)

    def transcript(self) -> str:
        blocks = []
        for turn in self.turns:
            blocks.append(f"**{turn.role}:** {turn.content.strip()}")
        return "\n\n".join(blocks)

    def stats(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "turns": len(self.turns),
            "user_turns": sum(1 for turn in self.turns if turn.role == "user"),
            "assistant_turns": sum(1 for turn in self.turns if turn.role == "assistant"),
            "tool_turns": sum(1 for turn in self.turns if turn.role == "tool"),
            "questions": sum(1 for turn in self.turns if turn.content.strip().endswith("?")),
            "tokens": self.tokens(),
        }


# ── Memory extraction ─────────────────────────────────────────────────────

@dataclass
class ExtractionRule:
    kind: str
    pattern: re.Pattern[str]
    confidence: float

    def match(self, sentence: str) -> bool:
        return bool(self.pattern.search(sentence))


EXTRACTION_RULES: list[ExtractionRule] = [
    ExtractionRule("preference", re.compile(
        r"\bi (?:really |strongly )?prefer\b|\bi (?:always|never)\b|"
        r"\balways use\b|\bnever use\b|\bmy preference is\b", re.IGNORECASE), 0.7),
    ExtractionRule("decision", re.compile(
        r"\bwe (?:have )?decided\b|\bwe'?re going with\b|\blet'?s (?:go with|use)\b|"
        r"\bfinal (?:answer|choice|decision) is\b", re.IGNORECASE), 0.8),
    ExtractionRule("todo", re.compile(
        r"\bremember to\b|\bdon'?t forget\b|\bfollow up on\b|\btodo:?\b", re.IGNORECASE), 0.75),
    ExtractionRule("fact", re.compile(
        r"^(?:the|our|this|that|a|an)\s+\w[^.?]{0,60}?\s(?:is|are)\s+\S", re.IGNORECASE), 0.6),
]

CHITCHAT = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|ok|okay|sure|great|nice|cool|bye)[.! ]*$",
    re.IGNORECASE)


def slugify(text: str, max_words: int = 6, max_chars: int = 48) -> str:
    words = [word for word in WORD_RE.findall((text or "").lower()) if word][:max_words]
    slug = "-".join(words)[:max_chars].strip("-.")
    return slug or "memory"


@dataclass
class ExtractedMemory:
    key: str
    kind: str
    title: str
    body: str
    confidence: float
    source: str

    def tags(self) -> list[str]:
        return [self.kind, slugify(self.title, 3, 24)]

    def to_markdown(self) -> str:
        return (
            "---\n"
            f"key: {self.key}\n"
            f"kind: {self.kind}\n"
            f"title: {self.title}\n"
            f"confidence: {round(self.confidence, 2)}\n"
            f"source: {self.source}\n"
            f"tags: {', '.join(self.tags())}\n"
            "---\n\n"
            f"# {self.title}\n\n"
            f"{self.body}\n"
        )

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "kind": self.kind, "title": self.title,
                "confidence": self.confidence, "source": self.source}


class MemoryExtractor:
    """Turns session prose into durable, deduplicated memories."""

    def __init__(self, rules: list[ExtractionRule] | None = None,
                 min_confidence: float = 0.5, min_chars: int = 12) -> None:
        self.rules = list(rules or EXTRACTION_RULES)
        self.min_confidence = min_confidence
        self.min_chars = min_chars

    def sentences(self, session: Session) -> Iterator[str]:
        for turn in session.turns:
            if turn.role not in ("user", "assistant"):
                continue
            for sentence in SENTENCE_RE.split(turn.content):
                cleaned = collapse(sentence)
                if cleaned:
                    yield cleaned

    def classify(self, sentence: str) -> tuple[str, float] | None:
        if len(sentence) < self.min_chars or CHITCHAT.match(sentence):
            return None
        for rule in self.rules:
            if rule.match(sentence):
                confidence = rule.confidence
                if len(sentence) > 40:
                    confidence = min(0.95, confidence + 0.1)
                return rule.kind, round(confidence, 2)
        return None

    def extract(self, session: Session) -> list[ExtractedMemory]:
        memories: list[ExtractedMemory] = []
        seen: set[str] = set()
        for sentence in self.sentences(session):
            classified = self.classify(sentence)
            if classified is None:
                continue
            kind, confidence = classified
            if confidence < self.min_confidence:
                continue
            key = slugify(sentence)
            if key in seen:
                continue
            seen.add(key)
            title = sentence.rstrip(".! ")
            if len(title) > 80:
                title = title[:77].rsplit(" ", 1)[0] + "…"
            memories.append(ExtractedMemory(
                key=key, kind=kind, title=title, body=sentence,
                confidence=confidence, source=session.session_id))
        return memories


# ── Commits ───────────────────────────────────────────────────────────────

@dataclass
class CommitResult:
    session_id: str
    archive_uri: str
    memories: list[str] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    turns: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"session_id": self.session_id, "archive_uri": self.archive_uri,
                "memories": list(self.memories), "superseded": list(self.superseded),
                "skipped": list(self.skipped), "turns": self.turns,
                "extracted": len(self.memories) + len(self.superseded)}


class ContextDatabase:
    """The ``ov`` facade: filesystem, scoped search, sessions and compilation."""

    def __init__(self, fs: VikingFS | None = None, user: str = "default",
                 extractor: MemoryExtractor | None = None) -> None:
        if not SEGMENT_RE.match(user or ""):
            raise ValueError(f"invalid user id {user!r}")
        self.fs = fs or VikingFS()
        self.user = user
        self.extractor = extractor or MemoryExtractor()
        self.sessions: dict[str, Session] = {}
        self.commits: list[CommitResult] = []
        self._sequence = 0
        self.fs.mkdir(f"{SCHEME}resources")
        self.fs.mkdir(f"{SCHEME}user/{user}/memories")
        self.fs.mkdir(f"{SCHEME}user/{user}/sessions")

    # -- filesystem passthrough ------------------------------------------
    def ls(self, uri: str = SCHEME) -> list[dict[str, Any]]:
        return self.fs.ls(uri)

    def tree(self, uri: str = SCHEME, depth: int = 3) -> str:
        return self.fs.tree(uri, depth)

    def read(self, uri: str, level: str = "L2") -> str:
        return self.fs.read(uri, level)

    def write(self, uri: str, content: str, tags: Iterable[str] = ()) -> VfsNode:
        return self.fs.write(uri, content, tags)

    def mkdir(self, uri: str) -> VfsNode:
        return self.fs.mkdir(uri)

    def rm(self, uri: str) -> bool:
        return self.fs.remove(uri)

    def move(self, source: str, target: str) -> VfsNode:
        return self.fs.move(source, target)

    def stat(self, uri: str) -> dict[str, Any]:
        return self.fs.stat(uri)

    def exists(self, uri: str) -> bool:
        return self.fs.exists(uri)

    def grep(self, pattern: str, uri: str = SCHEME, ignore_case: bool = False
             ) -> list[GrepHit]:
        return self.fs.grep(pattern, uri, ignore_case)

    def find(self, pattern: str, uri: str = SCHEME) -> list[str]:
        return self.fs.find(pattern, uri)

    def summary(self, uri: str) -> DirSummary:
        return self.fs.dir_summary(uri)

    # -- search -----------------------------------------------------------
    def search(self, query: str, root: str | None = None, level: str = "L1",
               limit: int = 10, kinds: Iterable[str] | None = None,
               tags: Iterable[str] | None = None) -> list[SearchHit]:
        root = root or f"{SCHEME}user/{self.user}"
        return ScopedSearchEngine(self.fs).search(
            query, root=root, level=level, limit=limit, kinds=kinds, tags=tags)

    def explain_search(self, query: str, root: str | None = None,
                       level: str = "L1") -> dict[str, Any]:
        root = root or f"{SCHEME}user/{self.user}"
        return ScopedSearchEngine(self.fs).explain(query, root=root, level=level)

    # -- sessions ---------------------------------------------------------
    def open_session(self, user_id: str | None = None) -> Session:
        user = user_id or self.user
        while True:
            self._sequence += 1
            session_id = f"s-{self._sequence:03d}"
            archived = self.fs.exists(f"{SCHEME}user/{user}/sessions/{session_id}.md")
            if session_id not in self.sessions and not archived:
                break
        session = Session(session_id=session_id, user_id=user)
        self.sessions[session.session_id] = session
        return session

    def commit_session(self, session: Session | str) -> CommitResult:
        if isinstance(session, str):
            session = self.sessions[session]
        self.sessions.setdefault(session.session_id, session)
        user = session.user_id
        archive_uri = f"{SCHEME}user/{user}/sessions/{session.session_id}.md"
        stats = session.stats()
        archive = (
            "---\n"
            f"session: {session.session_id}\n"
            f"user: {user}\n"
            f"turns: {stats['turns']}\n"
            f"tokens: {stats['tokens']}\n"
            "kind: session\n"
            "---\n\n"
            f"# Session {session.session_id}\n\n"
            f"{session.transcript()}\n"
        )
        self.fs.write(archive_uri, archive, tags=("session",))
        result = CommitResult(session_id=session.session_id, archive_uri=archive_uri,
                              turns=stats["turns"])
        for memory in self.extractor.extract(session):
            uri = f"{SCHEME}user/{user}/memories/{memory.kind}/{memory.key}.md"
            markdown = memory.to_markdown()
            if self.fs.exists(uri):
                existing = self.fs.read(uri, "L2")
                if collapse(existing) == collapse(markdown):
                    result.skipped.append(uri)
                    continue
                history_uri = (f"{SCHEME}user/{user}/memories/.history/"
                               f"{memory.kind}-{memory.key}-{len(result.superseded) + 1}.md")
                self.fs.write(history_uri, existing, tags=("history", memory.kind))
                result.superseded.append(uri)
            else:
                result.memories.append(uri)
            self.fs.write(uri, markdown, tags=tuple(memory.tags()))
        self.commits.append(result)
        return result

    # -- direct memory write ---------------------------------------------
    def remember(self, kind: str, key: str, title: str, body: str,
                 confidence: float = 0.8) -> str:
        memory = ExtractedMemory(key=slugify(key), kind=kind, title=title,
                                 body=body, confidence=confidence, source="manual")
        uri = f"{SCHEME}user/{self.user}/memories/{kind}/{memory.key}.md"
        self.fs.write(uri, memory.to_markdown(), tags=tuple(memory.tags()))
        return uri

    # -- compilation ------------------------------------------------------
    def compile(self, root: str | None = None, persist: bool = False) -> "WikiReport":
        root = root or f"{SCHEME}user/{self.user}"
        return WikiCompiler(self.fs).compile(root, persist=persist)

    def stats(self) -> dict[str, Any]:
        files = self.fs.files()
        dirs = [path for path in self.fs.walk() if self.fs._node(path).is_dir]
        memories = [uri for uri in files if "/memories/" in uri and "/.history/" not in uri]
        return {
            "files": len(files),
            "dirs": len(dirs),
            "tokens": sum(self.fs._node(VikingPath.parse(uri)).tokens for uri in files),
            "sessions": len(self.sessions),
            "commits": len(self.commits),
            "memories": len(memories),
            "user": self.user,
            "layers": dict(LAYERS),
        }


# ── Wiki compilation ──────────────────────────────────────────────────────

@dataclass
class WikiPage:
    uri: str
    title: str
    kind: str
    tags: tuple[str, ...]
    links: list[str] = field(default_factory=list)
    tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"uri": self.uri, "title": self.title, "kind": self.kind,
                "tags": list(self.tags), "links": list(self.links),
                "tokens": self.tokens}


@dataclass
class WikiReport:
    root: str
    pages: list[WikiPage]
    orphans: list[str]
    hubs: list[tuple[str, int]]
    components: int
    index_uri: str = ""

    def render_index(self) -> str:
        lines = ["# Memora context wiki", ""]
        for page in sorted(self.pages, key=lambda item: item.title.lower()):
            lines.append(f"- [{page.title}]({page.uri}) — {len(page.links)} link(s)")
        lines.append("")
        lines.append("## Hubs")
        lines.extend(f"- {uri} ({degree})" for uri, degree in self.hubs)
        lines.append("")
        lines.append("## Orphans")
        lines.extend(f"- {uri}" for uri in self.orphans) if self.orphans else lines.append("- none")
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "pages": [page.as_dict() for page in self.pages],
            "orphans": list(self.orphans),
            "hubs": [list(item) for item in self.hubs],
            "components": self.components,
            "index_uri": self.index_uri,
        }


class WikiCompiler:
    """Builds a wiki, a link graph and health signals from memory files."""

    def __init__(self, fs: VikingFS) -> None:
        self.fs = fs

    def _page(self, uri: str) -> WikiPage:
        node = self.fs._node(VikingPath.parse(uri))
        content = node.content if node else ""
        front = parse_frontmatter(content)
        title = front.get("title", "")
        if not title:
            for line in content.splitlines():
                heading = HEADING_RE.match(line.strip())
                if heading:
                    title = heading.group(1).strip()
                    break
        title = title or VikingPath.parse(uri).name.rsplit(".", 1)[0]
        kind = front.get("kind", "note")
        tags = tuple(tag.strip() for tag in front.get("tags", "").split(",") if tag.strip())
        return WikiPage(uri=uri, title=title, kind=kind, tags=tags,
                        tokens=node.tokens if node else 0)

    def compile(self, root: str = SCHEME, persist: bool = False) -> WikiReport:
        root_path = VikingPath.parse(root)
        self.fs._require(root_path)
        all_files = self.fs.files(root)
        memory_files = [uri for uri in all_files
                        if "/memories/" in uri and "/.history/" not in uri]
        chosen = memory_files or [uri for uri in all_files if uri.endswith(".md")]
        pages = [self._page(uri) for uri in sorted(chosen)]
        by_title = {page.title.lower(): page.uri for page in pages}
        by_key = {}
        for page in pages:
            key = parse_frontmatter(self.fs.read(page.uri, "L2")).get("key")
            if key:
                by_key[key.lower()] = page.uri

        adjacency: dict[str, set[str]] = {page.uri: set() for page in pages}
        for page in pages:
            content = self.fs.read(page.uri, "L2")
            explicit: set[str] = set()
            for match in WIKI_LINK_RE.findall(content):
                name = match.strip().lower()
                target = by_title.get(name) or by_key.get(name)
                if target and target != page.uri:
                    explicit.add(target)
            for reference in VIKING_REF_RE.findall(content):
                if reference in adjacency and reference != page.uri:
                    explicit.add(reference)
            page.links = sorted(explicit)
            adjacency[page.uri] |= explicit
            for target in explicit:
                adjacency[target].add(page.uri)

        # tag co-membership links (undirected, deterministic)
        by_tag: dict[str, list[str]] = {}
        for page in pages:
            for tag in page.tags:
                by_tag.setdefault(tag.lower(), []).append(page.uri)
        for tag, uris in sorted(by_tag.items()):
            if len(uris) < 2 or len(uris) > 12:
                continue
            for index, left in enumerate(sorted(uris)):
                for right in sorted(uris)[index + 1:]:
                    adjacency[left].add(right)
                    adjacency[right].add(left)
        for page in pages:
            page.links = sorted(adjacency[page.uri])

        orphans = sorted(uri for uri, neighbours in adjacency.items() if not neighbours)
        hubs = sorted(((uri, len(neighbours)) for uri, neighbours in adjacency.items()
                       if neighbours), key=lambda item: (-item[1], item[0]))[:5]
        components = self._components(adjacency, [page.uri for page in pages])
        report = WikiReport(root=root, pages=pages, orphans=orphans,
                            hubs=hubs, components=components)
        if persist:
            index_uri = f"{root.rstrip('/')}/_index.md"
            self.fs.write(index_uri, report.render_index(), tags=("wiki", "index"))
            report.index_uri = index_uri
        return report

    @staticmethod
    def _components(adjacency: dict[str, set[str]], nodes: list[str]) -> int:
        seen: set[str] = set()
        components = 0
        for start in nodes:
            if start in seen:
                continue
            components += 1
            stack = [start]
            while stack:
                current = stack.pop()
                if current in seen:
                    continue
                seen.add(current)
                stack.extend(neighbour for neighbour in adjacency.get(current, ())
                             if neighbour not in seen)
        return components


# ── Convenience ───────────────────────────────────────────────────────────

def default_database(user: str = "default") -> ContextDatabase:
    """A fresh, isolated context database (no shared mutable module state)."""
    return ContextDatabase(user=user)


def seed_resources(db: ContextDatabase) -> ContextDatabase:
    """Populate a small but realistic resource tree for demos and tests."""
    db.write(f"{SCHEME}resources/design/architecture.md",
             "# Architecture\n\nThe staging database is Postgres 16.\n"
             "- API: FastAPI\n- Queue: Redis\n", tags=("design", "infra"))
    db.write(f"{SCHEME}resources/design/api.md",
             "# API notes\n\nAll endpoints return envelopes.\n"
             "See [[Architecture]] for the deployment picture.\n", tags=("design", "api"))
    db.write(f"{SCHEME}resources/runbooks/oncall.md",
             "# On-call runbook\n\nCheck dashboards first.\n", tags=("ops",))
    return db
