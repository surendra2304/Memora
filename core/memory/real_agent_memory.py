"""
Memora/core/memory/real_agent_memory.py — Real Agent Memory Runtime.

The agent-memory architecture, implemented: markdown files in one store are the
single source of truth, every index beside them is a rebuildable cache, recall
answers with paths and anchors instead of pasted text, and a sleep-time pass
consolidates and forgets by value.

Real working implementations:
- FrontmatterCodec: parse / serialize the memory frontmatter block
- MarkdownMemory: one memory file with read levels (abstract/outline/full)
- Tokenizer + Bm25Index: real Okapi BM25 ranking over the store
- TfidfIndex + RrfFusion: vector track fused by reciprocal rank fusion
- MemoryStore: schema-placed writes, supersede chains, MEMORY.md root index,
  as-of reads, rebuildable in-memory index
- AnchorSelector: which paragraph of a file actually matched
- RecallEngine: recall (L0) → read by level → context expansion
- ThreeTrackRecall: deterministic injection + ranked recall + directory tree
- SleepConsolidator: value scoring, duplicate merge, deletion proposals
- SessionBoundaryWriter: distillation of conversation turns into memories
"""
from __future__ import annotations

import math
import os
import re
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable


# ── Text utilities ────────────────────────────────────────────────────────

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "of", "to", "in", "on",
    "for", "with", "as", "at", "by", "from", "is", "are", "was", "were", "be",
    "been", "being", "it", "its", "this", "that", "these", "those", "we", "you",
    "i", "they", "he", "she", "not", "no", "do", "does", "did", "so", "than",
    "which", "who", "what", "when", "where", "how", "why", "can", "will",
}


def tokenize(text: str) -> list[str]:
    """Lowercase word tokenizer with light suffix stripping."""
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    out: list[str] = []
    for word in words:
        if len(word) <= 2 or word in STOPWORDS:
            continue
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 4 and word.endswith("ing"):
            word = word[:-3]
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out


def token_estimate(text: str) -> int:
    return max(1, math.ceil(len(text or "") / 4.0))


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def first_sentence(text: str, limit: int = 200) -> str:
    body = re.sub(r"^#.*$", "", text or "", flags=re.MULTILINE).strip()
    body = re.sub(r"\s+", " ", body)
    match = re.match(r"(.+?[.!?])(\s|$)", body)
    sentence = match.group(1) if match else body
    return sentence[:limit].strip()


# ── Frontmatter ───────────────────────────────────────────────────────────

LIST_FIELDS = {"tags", "links", "supersedes", "sources"}
INT_FIELDS = {"access_count", "revision"}


@dataclass
class Frontmatter:
    """Typed frontmatter of a memory file."""

    name: str
    type: str = "note"
    group: str = "general"
    abstract: str = ""
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    supersedes: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    authority: str = "session"      # session < turn < curated < canonical
    confidence: float = 0.5
    created: str = ""
    updated: str = ""
    access_count: int = 0
    revision: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "group": self.group,
            "abstract": self.abstract,
            "tags": list(self.tags),
            "links": list(self.links),
            "supersedes": list(self.supersedes),
            "sources": list(self.sources),
            "authority": self.authority,
            "confidence": round(self.confidence, 4),
            "created": self.created,
            "updated": self.updated,
            "access_count": self.access_count,
            "revision": self.revision,
        }


class FrontmatterCodec:
    """Parse and emit the YAML-ish frontmatter used by the memory store."""

    DELIMITER = "---"

    @classmethod
    def parse(cls, text: str) -> tuple[dict[str, Any], str]:
        lines = (text or "").splitlines()
        if not lines or lines[0].strip() != cls.DELIMITER:
            return {}, text or ""
        data: dict[str, Any] = {}
        end = None
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == cls.DELIMITER:
                end = i
                break
            if ":" not in line:
                continue
            key, raw = line.split(":", 1)
            key = key.strip()
            raw = raw.strip()
            if key in LIST_FIELDS:
                inner = raw.strip("[]")
                data[key] = [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
            elif key in INT_FIELDS:
                try:
                    data[key] = int(raw)
                except ValueError:
                    data[key] = 0
            elif key in ("confidence",):
                try:
                    data[key] = float(raw)
                except ValueError:
                    data[key] = 0.5
            else:
                data[key] = raw.strip("'\"")
        body = "\n".join(lines[(end + 1):]).strip() if end is not None else text
        return data, body

    @classmethod
    def serialize(cls, fm: Frontmatter) -> str:
        lines = [cls.DELIMITER]
        data = fm.as_dict()
        for key in (
            "name", "type", "group", "abstract", "tags", "links", "supersedes",
            "sources", "authority", "confidence", "created", "updated",
            "access_count", "revision",
        ):
            value = data[key]
            if isinstance(value, list):
                lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
            else:
                lines.append(f"{key}: {value}")
        lines.append(cls.DELIMITER)
        return "\n".join(lines)


# ── One memory file ───────────────────────────────────────────────────────

@dataclass
class Heading:
    """A markdown heading with its line span."""

    level: int
    title: str
    start: int
    end: int = 0

    @property
    def anchor(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.title.lower()).strip("-")
        return slug or "section"


@dataclass
class Paragraph:
    """A body paragraph, addressable as an anchor."""

    heading: str
    text: str
    index: int


class MarkdownMemory:
    """A single memory file: frontmatter + body, rendered at a read level."""

    def __init__(self, frontmatter: Frontmatter, body: str = "", path: str = "") -> None:
        self.frontmatter = frontmatter
        self.body = (body or "").strip()
        self.path = path

    # ── construction ─────────────────────────────────────────────────

    @classmethod
    def parse(cls, text: str, path: str = "") -> "MarkdownMemory":
        data, body = FrontmatterCodec.parse(text)
        if not data:
            data = {"name": os.path.splitext(os.path.basename(path))[0] or "untitled"}
        name = data.get("name") or os.path.splitext(os.path.basename(path))[0]
        fm = Frontmatter(
            name=name,
            type=data.get("type", "note") or "note",
            group=data.get("group", "general") or "general",
            abstract=data.get("abstract", ""),
            tags=data.get("tags", []) or [],
            links=data.get("links", []) or [],
            supersedes=data.get("supersedes", []) or [],
            sources=data.get("sources", []) or [],
            authority=data.get("authority", "session") or "session",
            confidence=float(data.get("confidence", 0.5) or 0.5),
            created=data.get("created", ""),
            updated=data.get("updated", ""),
            access_count=int(data.get("access_count", 0) or 0),
            revision=int(data.get("revision", 1) or 1),
        )
        if not fm.abstract:
            fm.abstract = first_sentence(body)
        return cls(fm, body, path)

    def render(self) -> str:
        return f"{FrontmatterCodec.serialize(self.frontmatter)}\n\n{self.body}\n"

    # ── read levels ──────────────────────────────────────────────────

    @property
    def abstract(self) -> str:
        return self.frontmatter.abstract or first_sentence(self.body)

    def headings(self) -> list[Heading]:
        lines = self.body.splitlines()
        found: list[Heading] = []
        for i, line in enumerate(lines):
            match = re.match(r"^(#{1,6})\s+(.*)$", line)
            if match:
                found.append(Heading(level=len(match.group(1)), title=match.group(2).strip(), start=i))
        for i, heading in enumerate(found):
            heading.end = found[i + 1].start if i + 1 < len(found) else len(lines)
        return found

    def paragraphs(self) -> list[Paragraph]:
        lines = self.body.splitlines()
        heads = {h.start: h.title for h in self.headings()}
        current = ""
        out: list[Paragraph] = []
        buffer: list[str] = []
        for i, line in enumerate(lines):
            if i in heads:
                if buffer and " ".join(buffer).strip():
                    out.append(Paragraph(current, " ".join(buffer).strip(), len(out)))
                buffer = []
                current = heads[i]
                continue
            if not line.strip():
                if buffer and " ".join(buffer).strip():
                    out.append(Paragraph(current, " ".join(buffer).strip(), len(out)))
                buffer = []
                continue
            buffer.append(line.strip())
        if buffer and " ".join(buffer).strip():
            out.append(Paragraph(current, " ".join(buffer).strip(), len(out)))
        return out

    def outline(self) -> list[tuple[int, str, str]]:
        """Headings only, with anchors — the cheapest non-index read."""
        return [(h.level, h.title, h.anchor) for h in self.headings()]

    def read_level(self, level: str = "full", anchor: str = "") -> str:
        """Render at a read level: abstract | outline | anchor | full."""
        level = (level or "full").lower()
        if level == "abstract":
            return f"{self.frontmatter.name}: {self.abstract}"
        if level == "outline":
            rows = [" > ".join([self.frontmatter.name] + [f"#{a}" for _, _, a in self.outline()])]
            rows += [f"{'  ' * (lvl - 1)}- {title} (#{anchor})" for lvl, title, anchor in self.outline()]
            return "\n".join(rows)
        if level == "anchor" and anchor:
            return self._read_anchor(anchor)
        return self.body

    def _read_anchor(self, anchor: str) -> str:
        needle = anchor.lstrip("#").lower()
        lines = self.body.splitlines()
        start = None
        for i, line in enumerate(lines):
            match = re.match(r"^(#{1,6})\s+(.*)$", line)
            if match and Heading(len(match.group(1)), match.group(2), i).anchor == needle:
                start = i
                break
        if start is None:
            for para in self.paragraphs():
                if needle and needle in f"{para.heading} {para.text}".lower().replace(" ", "-"):
                    return para.text
            return self.abstract
        end = len(lines)
        for j in range(start + 1, len(lines)):
            if re.match(r"^#{1,6}\s+", lines[j]):
                end = j
                break
        return "\n".join(lines[start:end]).strip()

    @property
    def tokens(self) -> int:
        return token_estimate(self.render())

    def touch(self) -> None:
        self.frontmatter.access_count += 1
        self.frontmatter.updated = datetime.now(timezone.utc).isoformat(timespec="microseconds")


# ── Ranking ───────────────────────────────────────────────────────────────

class Bm25Index:
    """Okapi BM25 over memory documents. Fully rebuildable."""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: dict[str, Counter[str]] = {}
        self._lengths: dict[str, int] = {}
        self._df: Counter[str] = Counter()

    def __len__(self) -> int:
        return len(self._docs)

    def add(self, doc_id: str, text: str) -> None:
        self.remove(doc_id)
        counts = Counter(tokenize(text))
        self._docs[doc_id] = counts
        self._lengths[doc_id] = sum(counts.values())
        for term in counts:
            self._df[term] += 1

    def remove(self, doc_id: str) -> None:
        if doc_id not in self._docs:
            return
        for term in self._docs[doc_id]:
            self._df[term] -= 1
            if self._df[term] <= 0:
                del self._df[term]
        del self._docs[doc_id]
        del self._lengths[doc_id]

    @property
    def avg_length(self) -> float:
        if not self._docs:
            return 0.0
        return sum(self._lengths.values()) / len(self._docs)

    def idf(self, term: str) -> float:
        n = len(self._docs)
        df = self._df.get(term, 0)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def score(self, query: str, doc_id: str) -> float:
        counts = self._docs.get(doc_id)
        if not counts:
            return 0.0
        terms = tokenize(query)
        if not terms:
            return 0.0
        avgdl = self.avg_length or 1.0
        dl = self._lengths[doc_id]
        total = 0.0
        for term in terms:
            tf = counts.get(term, 0)
            if tf == 0:
                continue
            denom = tf + self.k1 * (1 - self.b + self.b * dl / avgdl)
            total += self.idf(term) * (tf * (self.k1 + 1)) / denom
        return total

    def search(self, query: str, limit: int = 10) -> list[tuple[str, float]]:
        scored = [(doc_id, self.score(query, doc_id)) for doc_id in self._docs]
        scored = [(d, s) for d, s in scored if s > 0]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:limit]

    def terms(self) -> list[str]:
        return sorted(self._df)


class TfidfIndex:
    """Cosine-similarity vector track — the fuse-in plugin, not the main index."""

    def __init__(self) -> None:
        self._vectors: dict[str, Counter[str]] = {}
        self._norms: dict[str, float] = {}

    def __len__(self) -> int:
        return len(self._vectors)

    def add(self, doc_id: str, text: str) -> None:
        counts = Counter(tokenize(text))
        self._vectors[doc_id] = counts
        self._norms[doc_id] = math.sqrt(sum(v * v for v in counts.values())) or 1.0

    def remove(self, doc_id: str) -> None:
        self._vectors.pop(doc_id, None)
        self._norms.pop(doc_id, None)

    def score(self, query: str, doc_id: str) -> float:
        vector = self._vectors.get(doc_id)
        if not vector:
            return 0.0
        q = Counter(tokenize(query))
        if not q:
            return 0.0
        dot = sum(q[t] * vector.get(t, 0) for t in q)
        q_norm = math.sqrt(sum(v * v for v in q.values())) or 1.0
        return dot / (q_norm * self._norms[doc_id])

    def search(self, query: str, limit: int = 10) -> list[tuple[str, float]]:
        scored = [(doc_id, self.score(query, doc_id)) for doc_id in self._vectors]
        scored = [(d, s) for d, s in scored if s > 0]
        scored.sort(key=lambda pair: (-pair[1], pair[0]))
        return scored[:limit]


class RrfFusion:
    """Reciprocal rank fusion of several ranked lists."""

    def __init__(self, k: int = 60) -> None:
        self.k = k

    def fuse(
        self,
        rankings: dict[str, list[tuple[str, float]]],
        weights: dict[str, float] | None = None,
    ) -> list[tuple[str, float]]:
        weights = weights or {}
        fused: dict[str, float] = defaultdict(float)
        for track, ranked in rankings.items():
            weight = weights.get(track, 1.0)
            for rank, (doc_id, _score) in enumerate(ranked, start=1):
                fused[doc_id] += weight / (self.k + rank)
        return sorted(fused.items(), key=lambda pair: (-pair[1], pair[0]))


# ── Store ─────────────────────────────────────────────────────────────────

@dataclass
class SchemaType:
    """How one memory type is written and grouped."""

    name: str
    group_field: str = "group"
    write_mode: str = "append"     # append | supersede | proposal
    requires_links: bool = False


DEFAULT_SCHEMAS = {
    "decision": SchemaType("decision", "group", "supersede", requires_links=True),
    "preference": SchemaType("preference", "group", "supersede"),
    "fact": SchemaType("fact", "group", "append"),
    "skill": SchemaType("skill", "group", "append"),
    "session": SchemaType("session", "group", "append"),
    "provenance": SchemaType("provenance", "group", "append"),
}

AUTHORITY_RANK = {"session": 0, "turn": 1, "curated": 2, "canonical": 3}


@dataclass
class WriteResult:
    """Outcome of a store write."""

    ok: bool
    name: str
    path: str = ""
    action: str = "created"      # created | updated | superseded | proposal
    reason: str = ""
    proposal: "ConsolidationProposal | None" = None


@dataclass
class ConsolidationProposal:
    """A change the unattended pass may only propose."""

    kind: str                     # merge | forget | promote | supersede
    targets: list[str]
    reason: str
    confidence: float = 0.5
    value: float = 0.0

    def describe(self) -> str:
        return f"{self.kind}({', '.join(self.targets)}): {self.reason}"


class MemoryStore:
    """Markdown memory store with a rebuildable in-memory index."""

    def __init__(self, root: str, schemas: dict[str, SchemaType] | None = None) -> None:
        self.root = root
        os.makedirs(self.root, exist_ok=True)
        self.schemas = dict(DEFAULT_SCHEMAS)
        if schemas:
            self.schemas.update(schemas)
        self.bm25 = Bm25Index()
        self.tfidf = TfidfIndex()
        self.records: dict[str, MarkdownMemory] = {}
        self._dirty = True

    # ── paths ────────────────────────────────────────────────────────

    def path_for(self, mem_type: str, group: str, name: str) -> str:
        safe = re.sub(r"[^a-z0-9._-]+", "-", name.lower()).strip("-")
        group_safe = re.sub(r"[^a-z0-9._-]+", "-", (group or "general").lower()).strip("-")
        return os.path.join(self.root, mem_type, group_safe, f"{safe}.md")

    def memory_md_path(self) -> str:
        return os.path.join(self.root, "MEMORY.md")

    # ── index ────────────────────────────────────────────────────────

    def load(self) -> "MemoryStore":
        self.records.clear()
        self.bm25 = Bm25Index()
        self.tfidf = TfidfIndex()
        for dirpath, _dirs, files in os.walk(self.root):
            if os.path.basename(dirpath) == "archive":
                continue
            for fname in sorted(files):
                if not fname.endswith(".md") or fname == "MEMORY.md":
                    continue
                full = os.path.join(dirpath, fname)
                try:
                    with open(full, "r", encoding="utf-8") as fh:
                        memory = MarkdownMemory.parse(fh.read(), full)
                except OSError:
                    continue
                self._index(memory)
        self._dirty = False
        return self

    def _index(self, memory: MarkdownMemory) -> None:
        key = memory.frontmatter.name
        self.records[key] = memory
        haystack = " ".join([
            memory.frontmatter.name,
            memory.frontmatter.abstract,
            " ".join(memory.frontmatter.tags),
            memory.body,
        ])
        self.bm25.add(key, haystack)
        self.tfidf.add(key, haystack)

    def rebuild(self) -> dict[str, int]:
        """Delete-and-rebuild the index from the markdown files."""
        files_before = self.count_files()
        self.load()
        self.write_root_index()
        return {
            "files": files_before,
            "indexed": len(self.records),
            "terms": len(self.bm25.terms()),
        }

    def count_files(self) -> int:
        total = 0
        for dirpath, _dirs, files in os.walk(self.root):
            total += len([f for f in files if f.endswith(".md") and f != "MEMORY.md"])
        return total

    # ── write ────────────────────────────────────────────────────────

    def write(
        self,
        name: str,
        body: str,
        mem_type: str = "note",
        group: str = "general",
        abstract: str = "",
        links: list[str] | None = None,
        tags: list[str] | None = None,
        authority: str = "session",
        confidence: float = 0.5,
        sources: list[str] | None = None,
        now: str | None = None,
        force_update: bool = False,
    ) -> WriteResult:
        schema = self.schemas.get(mem_type, SchemaType(mem_type, "group", "append"))
        if force_update:
            schema = SchemaType(schema.name, schema.group_field, "append", schema.requires_links)
        links = list(links or [])
        if schema.requires_links and not links:
            group_hint = [r for r in self.records if self.records[r].frontmatter.group == group]
            if group_hint:
                links = [group_hint[0]]
        stamp = now or datetime.now(timezone.utc).isoformat(timespec="microseconds")
        existing = self.records.get(name)
        supersedes: list[str] = []
        revision = 1
        action = "created"
        proposal: ConsolidationProposal | None = None

        if existing is not None:
            existing_rank = AUTHORITY_RANK.get(existing.frontmatter.authority, 0)
            if AUTHORITY_RANK.get(authority, 0) < existing_rank:
                return WriteResult(
                    ok=False, name=name, action="proposal",
                    reason=(
                        f"authority '{authority}' cannot overwrite "
                        f"'{existing.frontmatter.authority}'"
                    ),
                    proposal=ConsolidationProposal(
                        kind="supersede", targets=[name],
                        reason="lower-authority write needs confirmation",
                        confidence=confidence,
                    ),
                )
            schema_mode = schema.write_mode
            if schema_mode == "proposal" and existing_rank >= AUTHORITY_RANK["curated"]:
                return WriteResult(
                    ok=False, name=name, action="proposal",
                    reason="curated memory requires a confirmed proposal",
                    proposal=ConsolidationProposal(
                        kind="supersede", targets=[name],
                        reason="proposal mode schema", confidence=confidence,
                    ),
                )
            if schema_mode == "supersede":
                new_name = f"{name}--r{existing.frontmatter.revision + 1}"
                supersedes = [name]
                name = new_name
                revision = existing.frontmatter.revision + 1
                action = "superseded"
            else:
                revision = existing.frontmatter.revision + 1
                action = "updated"

        fm = Frontmatter(
            name=name,
            type=mem_type,
            group=group,
            abstract=abstract or first_sentence(body),
            tags=list(tags or []),
            links=links,
            supersedes=supersedes,
            sources=list(sources or []),
            authority=authority,
            confidence=confidence,
            created=(existing.frontmatter.created if existing and action == "updated" else stamp),
            updated=stamp,
            access_count=(existing.frontmatter.access_count if existing and action == "updated" else 0),
            revision=revision,
        )
        memory = MarkdownMemory(fm, body)
        path = self.path_for(mem_type, group, fm.name)
        memory.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(memory.render())
        self._index(memory)
        self.write_root_index()
        return WriteResult(ok=True, name=fm.name, path=path, action=action, proposal=proposal)

    def write_root_index(self) -> str:
        """Regenerate MEMORY.md — the only resident injection."""
        lines = ["# MEMORY", "", f"<!-- {len(self.records)} memories -->", ""]
        by_type: dict[str, list[MarkdownMemory]] = defaultdict(list)
        for memory in self.records.values():
            by_type[memory.frontmatter.type].append(memory)
        for mem_type in sorted(by_type):
            lines.append(f"## {mem_type}")
            for memory in sorted(by_type[mem_type], key=lambda m: m.frontmatter.name):
                fm = memory.frontmatter
                lines.append(f"- [{fm.name}]({fm.type}/{fm.group}/{fm.name}.md) — {fm.abstract}")
            lines.append("")
        text = "\n".join(lines)
        with open(self.memory_md_path(), "w", encoding="utf-8") as fh:
            fh.write(text)
        return text

    # ── read ─────────────────────────────────────────────────────────

    def get(self, name: str, as_of: str = "") -> MarkdownMemory | None:
        if not as_of:
            return self.records.get(name)
        chain = self.chain(name)
        eligible = [m for m in chain if (m.frontmatter.updated or "") <= as_of]
        if not eligible:
            return None
        return sorted(eligible, key=lambda m: m.frontmatter.updated)[-1]

    def chain(self, name: str) -> list[MarkdownMemory]:
        """All revisions connected by supersedes links, oldest first."""
        lineage: set[str] = {name}
        changed = True
        while changed:
            changed = False
            for memory in self.records.values():
                if memory.frontmatter.name in lineage:
                    continue
                if any(parent in lineage for parent in memory.frontmatter.supersedes):
                    lineage.add(memory.frontmatter.name)
                    changed = True
        members = [self.records[n] for n in lineage if n in self.records]
        return sorted(members, key=lambda m: (m.frontmatter.updated, m.frontmatter.revision))

    def read(self, name: str, level: str = "full", anchor: str = "", as_of: str = "") -> str:
        memory = self.get(name, as_of=as_of)
        if memory is None:
            return ""
        return memory.read_level(level, anchor)

    def tree(self) -> list[str]:
        paths = []
        for dirpath, _dirs, files in os.walk(self.root):
            for fname in sorted(files):
                if fname.endswith(".md") and fname != "MEMORY.md":
                    paths.append(os.path.relpath(os.path.join(dirpath, fname), self.root).replace(chr(92), "/"))
        return sorted(paths)

    def trace(self, name: str) -> list[str]:
        memory = self.records.get(name)
        if memory is None:
            return []
        return list(memory.frontmatter.sources)

    def stats(self) -> dict[str, Any]:
        types: Counter[str] = Counter()
        authorities: Counter[str] = Counter()
        tokens = 0
        for memory in self.records.values():
            types[memory.frontmatter.type] += 1
            authorities[memory.frontmatter.authority] += 1
            tokens += memory.tokens
        return {
            "memories": len(self.records),
            "types": dict(types),
            "authorities": dict(authorities),
            "tokens": tokens,
        }


# ── Anchors ───────────────────────────────────────────────────────────────

@dataclass
class AnchorHit:
    """The paragraph of a memory that matched a query."""

    memory: str
    heading: str
    anchor: str
    text: str
    score: float


class AnchorSelector:
    """Pick the paragraph inside a memory that actually answers the query."""

    def select(self, memory: MarkdownMemory, query: str, limit: int = 3) -> list[AnchorHit]:
        query_terms = set(tokenize(query))
        if not query_terms:
            return []
        hits: list[AnchorHit] = []
        for para in memory.paragraphs():
            terms = set(tokenize(para.text))
            if not terms:
                continue
            overlap = len(query_terms & terms)
            if overlap == 0:
                continue
            density = overlap / math.sqrt(len(terms))
            # Earlier paragraphs are slightly preferred as context leads.
            score = overlap * 0.5 + density - para.index * 0.01
            anchor = re.sub(r"[^a-z0-9]+", "-", para.heading.lower()).strip("-") or "body"
            hits.append(AnchorHit(
                memory=memory.frontmatter.name,
                heading=para.heading or "body",
                anchor=anchor,
                text=para.text,
                score=round(score, 4),
            ))
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]


# ── Recall ────────────────────────────────────────────────────────────────

@dataclass
class RecallHit:
    """An L0 recall line: one-line abstract, path, anchor, score."""

    name: str
    path: str
    abstract: str
    anchor: str
    score: float
    authority: str = "session"
    tokens: int = 0

    def as_line(self) -> str:
        return f"- {self.name} ({self.path}#{self.anchor}) [{self.score:.3f}] {self.abstract}"


class RecallEngine:
    """Ranked local retrieval that answers with paths, not pasted text."""

    def __init__(self, store: MemoryStore, fusion: RrfFusion | None = None) -> None:
        self.store = store
        self.fusion = fusion or RrfFusion(k=60)
        self.anchors = AnchorSelector()
        self.last_tracks: dict[str, int] = {}

    def _relative(self, memory: MarkdownMemory, name: str) -> str:
        if memory.path:
            return os.path.relpath(memory.path, self.store.root).replace(chr(92), "/")
        fm = memory.frontmatter
        return f"{fm.type}/{fm.group}/{name}.md"

    def _link_boost(self, name: str) -> float:
        """Pages linked from many memories rank slightly higher (free graph)."""
        inbound = sum(
            1 for m in self.store.records.values()
            if name in m.frontmatter.links or name in m.frontmatter.supersedes
        )
        return 0.15 * math.log1p(inbound)

    def recall(
        self, query: str, limit: int = 8, as_of: str = "", expand_links: bool = True,
    ) -> list[RecallHit]:
        if not query.strip():
            return []
        bm25 = self.store.bm25.search(query, limit=limit * 3)
        tfidf = self.store.tfidf.search(query, limit=limit * 3)
        self.last_tracks = {"bm25": len(bm25), "tfidf": len(tfidf)}
        fused = self.fusion.fuse(
            {"bm25": bm25, "tfidf": tfidf}, weights={"bm25": 1.0, "tfidf": 0.6},
        )

        seen: set[str] = set()
        hits: list[RecallHit] = []
        for name, score in fused:
            memory = self.store.get(name, as_of=as_of)
            if memory is None:
                continue
            anchor_hits = self.anchors.select(memory, query, limit=1)
            anchor = anchor_hits[0].anchor if anchor_hits else "body"
            boosted = score + self._link_boost(name)
            hits.append(RecallHit(
                name=name,
                path=self._relative(memory, name),
                abstract=memory.abstract,
                anchor=anchor,
                score=round(boosted, 5),
                authority=memory.frontmatter.authority,
                tokens=memory.tokens,
            ))
            seen.add(name)

        if expand_links and hits:
            # One-hop graph expansion: linked neighbours are a free neighbourhood.
            for hit in list(hits):
                memory = self.store.get(hit.name, as_of=as_of)
                if memory is None:
                    continue
                for neighbour in memory.frontmatter.links:
                    if neighbour in seen or neighbour not in self.store.records:
                        continue
                    neighbour_mem = self.store.get(neighbour, as_of=as_of)
                    if neighbour_mem is None:
                        continue
                    hits.append(RecallHit(
                        name=neighbour,
                        path=self._relative(neighbour_mem, neighbour),
                        abstract=neighbour_mem.abstract,
                        anchor="body",
                        score=round(max(0.0, hit.score * 0.4), 5),
                        authority=neighbour_mem.frontmatter.authority,
                        tokens=neighbour_mem.tokens,
                    ))
                    seen.add(neighbour)
                    if len(hits) >= limit * 2:
                        break

        hits.sort(key=lambda h: (-h.score, h.name))
        return hits[:limit]

    def read(self, name: str, level: str = "full", anchor: str = "", as_of: str = "") -> str:
        text = self.store.read(name, level=level, anchor=anchor, as_of=as_of)
        if text and level == "full" and name in self.store.records:
            self.store.records[name].touch()
        return text

    def context(self, query: str, k: int = 3, as_of: str = "") -> str:
        """Recall lines plus the top-k memories expanded in full."""
        hits = self.recall(query, limit=max(k * 2, k), as_of=as_of)
        if not hits:
            return ""
        lines = [f"## recall: {query}", ""]
        lines += [hit.as_line() for hit in hits]
        lines.append("")
        for hit in hits[:k]:
            body = self.read(hit.name, level="full", as_of=as_of)
            if not body:
                continue
            lines.append(f"### {hit.name} ({hit.path})")
            lines.append(body)
            lines.append("")
        return "\n".join(lines).strip()


# ── Three read tracks ─────────────────────────────────────────────────────

@dataclass
class AssembledContext:
    """A budgeted context block assembled from the three tracks."""

    text: str
    tokens: int
    budget: int
    tracks_used: list[str] = field(default_factory=list)
    hits: list[str] = field(default_factory=list)

    @property
    def utilization(self) -> float:
        return round(self.tokens / self.budget, 4) if self.budget else 0.0


class ThreeTrackRecall:
    """Deterministic injection + ranked recall + plain directory, in one budget."""

    def __init__(self, store: MemoryStore, recall: RecallEngine | None = None) -> None:
        self.store = store
        self.recall = recall or RecallEngine(store)

    def assemble(self, query: str, budget: int = 800, expand: int = 2, as_of: str = "") -> AssembledContext:
        parts: list[str] = []
        used = 0
        tracks: list[str] = []
        names: list[str] = []

        index_text = self._index_block(max(32, budget // 3))
        if index_text:
            parts.append(index_text)
            used += token_estimate(index_text)
            tracks.append("deterministic_injection")

        hits = self.recall.recall(query, limit=10, as_of=as_of)
        if hits:
            head = "## ranked recall"
            recall_lines = [head] + [hit.as_line() for hit in hits]
            block = "\n".join(recall_lines)
            cost = token_estimate(block)
            if used + cost <= budget:
                parts.append(block)
                used += cost
                tracks.append("bm25+rrf")

            expanded = 0
            for hit in hits:
                if expanded >= expand:
                    break
                body = self.recall.read(hit.name, level="anchor", anchor=hit.anchor, as_of=as_of)
                if not body:
                    continue
                block = f"### {hit.name} ({hit.path}#{hit.anchor})\n{body}"
                cost = token_estimate(block)
                if used + cost > budget:
                    continue
                parts.append(block)
                used += cost
                names.append(hit.name)
                expanded += 1
            if expanded:
                tracks.append("anchored_reads")

        remaining = budget - used
        if remaining > max(40, budget * 0.1):
            tree = self.store.tree()
            block = "## store tree\n" + "\n".join(f"- {p}" for p in tree[:60])
            cost = token_estimate(block)
            if cost <= remaining:
                parts.append(block)
                used += cost
                tracks.append("directory_tree")

        text = "\n\n".join(parts)
        return AssembledContext(
            text=text, tokens=used, budget=budget, tracks_used=tracks, hits=names,
        )

    def _index_block(self, max_tokens: int) -> str:
        path = self.store.memory_md_path()
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read().strip()
        except OSError:
            return ""
        # Cap the resident injection: index lines only, never full bodies.
        kept: list[str] = []
        for line in content.splitlines()[:40]:
            candidate = "\n".join(kept + [line])
            if kept and token_estimate(candidate) > max_tokens:
                break
            kept.append(line)
        return "\n".join(kept)


# ── Consolidation ─────────────────────────────────────────────────────────

class SleepConsolidator:
    """Sleep-time pass: score value, merge duplicates, propose forgetting.

    An unattended pass may add and update. Deletion is only ever a proposal.
    """

    def __init__(
        self,
        store: MemoryStore,
        half_life_days: float = 30.0,
        forget_threshold: float = 0.22,
        merge_similarity: float = 0.65,
    ) -> None:
        self.store = store
        self.half_life_days = half_life_days
        self.forget_threshold = forget_threshold
        self.merge_similarity = merge_similarity

    @staticmethod
    def _parse_ts(stamp: str) -> datetime:
        try:
            return datetime.fromisoformat(stamp)
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)

    def recency(self, memory: MarkdownMemory, now: datetime) -> float:
        age_days = max(0.0, (now - self._parse_ts(memory.frontmatter.updated)).total_seconds() / 86400.0)
        return 0.5 ** (age_days / max(1e-6, self.half_life_days))

    def value(self, memory: MarkdownMemory, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        fm = memory.frontmatter
        recency = self.recency(memory, now)
        usage = min(1.0, math.log1p(fm.access_count) / math.log1p(20))
        inbound = sum(1 for m in self.store.records.values() if fm.name in m.frontmatter.links)
        graph = inbound / (1 + inbound)
        similarity = 0.0
        for other in self.store.records.values():
            if other.frontmatter.name == fm.name:
                continue
            similarity = max(
                similarity,
                jaccard(tokenize(memory.body), tokenize(other.body)),
            )
        uniqueness = 1.0 - similarity
        authority = min(1.0, AUTHORITY_RANK.get(fm.authority, 0) / 3.0)
        score = (
            0.30 * recency
            + 0.20 * usage
            + 0.15 * graph
            + 0.15 * uniqueness
            + 0.20 * authority
        )
        return round(score, 4)

    def duplicate_pairs(self) -> list[tuple[str, str, float]]:
        names = sorted(self.store.records)
        pairs: list[tuple[str, str, float]] = []
        tokens = {n: tokenize(self.store.records[n].body) for n in names}
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                sim = jaccard(tokens[a], tokens[b])
                if sim >= self.merge_similarity:
                    pairs.append((a, b, round(sim, 4)))
        pairs.sort(key=lambda p: -p[2])
        return pairs

    def propose(self, now: datetime | None = None) -> list[ConsolidationProposal]:
        now = now or datetime.now(timezone.utc)
        proposals: list[ConsolidationProposal] = []
        for a, b, sim in self.duplicate_pairs():
            keep = a if self.value(self.store.records[a], now) >= self.value(self.store.records[b], now) else b
            drop = b if keep == a else a
            proposals.append(ConsolidationProposal(
                kind="merge", targets=[keep, drop],
                reason=f"body similarity {sim:.2f}",
                confidence=sim, value=self.value(self.store.records[keep], now),
            ))
        for name, memory in self.store.records.items():
            score = self.value(memory, now)
            if score < self.forget_threshold:
                age_days = (now - self._parse_ts(memory.frontmatter.updated)).total_seconds() / 86400.0
                if age_days >= 7.0:
                    proposals.append(ConsolidationProposal(
                        kind="forget", targets=[name],
                        reason=f"value {score:.2f} below {self.forget_threshold}",
                        confidence=1.0 - score, value=score,
                    ))
            inbound = sum(1 for m in self.store.records.values() if name in m.frontmatter.links)
            if inbound >= 3 and memory.frontmatter.authority != "canonical":
                proposals.append(ConsolidationProposal(
                    kind="promote", targets=[name],
                    reason=f"{inbound} inbound links — promote to canonical",
                    confidence=min(1.0, inbound / 5.0), value=score,
                ))
        return proposals

    def apply(self, proposal: ConsolidationProposal, confirm: bool = False) -> bool:
        """Merges are safe; deletions need an explicit confirmation."""
        if proposal.kind == "forget":
            if not confirm:
                return False
            for name in proposal.targets:
                memory = self.store.records.get(name)
                if memory is None:
                    continue
                archive = os.path.join(self.store.root, "archive")
                os.makedirs(archive, exist_ok=True)
                target = os.path.join(archive, os.path.basename(memory.path or f"{name}.md"))
                try:
                    os.replace(memory.path, target)
                except OSError:
                    continue
                self.store.records.pop(name, None)
                self.store.bm25.remove(name)
                self.store.tfidf.remove(name)
            self.store.write_root_index()
            return True
        if proposal.kind == "merge":
            if len(proposal.targets) < 2:
                return False
            keep, drop = proposal.targets[0], proposal.targets[1]
            keeper = self.store.records.get(keep)
            loser = self.store.records.get(drop)
            if keeper is None or loser is None:
                return False
            merged_body = keeper.body.rstrip() + "\n\n## merged\n" + loser.body.strip()
            links = sorted(set(keeper.frontmatter.links) | set(loser.frontmatter.links) | {drop})
            fm = keeper.frontmatter
            fm.links = [link for link in links if link != keep]
            self.store.write(
                name=keep, body=merged_body, mem_type=fm.type, group=fm.group,
                abstract=fm.abstract, links=fm.links, tags=fm.tags,
                authority=fm.authority, confidence=max(fm.confidence, loser.frontmatter.confidence),
                sources=sorted(set(fm.sources) | set(loser.frontmatter.sources)),
                force_update=True,
            )
            proposal.kind = "merged"
            return True
        if proposal.kind == "promote":
            name = proposal.targets[0]
            memory = self.store.records.get(name)
            if memory is None:
                return False
            memory.frontmatter.authority = "canonical"
            with open(memory.path, "w", encoding="utf-8") as fh:
                fh.write(memory.render())
            self.store.write_root_index()
            return True
        return False


# ── Session boundaries ────────────────────────────────────────────────────

STATEMENT_MARKERS = (
    "prefer", "always", "never", "decided", "we use", "i use", "must",
    "should", "do not", "don't", "requirement", "convention", "instead of",
)


@dataclass
class MemoryDraft:
    """A candidate memory distilled from a conversation turn."""

    name: str
    body: str
    abstract: str
    tags: list[str]
    confidence: float
    source: str


class SessionBoundaryWriter:
    """Distil conversation turns into memories at a boundary, not on recall."""

    def __init__(self, store: MemoryStore, min_confidence: float = 0.45) -> None:
        self.store = store
        self.min_confidence = min_confidence

    @staticmethod
    def _slug(text: str, limit: int = 48) -> str:
        words = tokenize(text)[:8]
        slug = "-".join(words) or f"note-{uuid.uuid4().hex[:6]}"
        return slug[:limit].strip("-")

    def draft(self, turns: list[str], source: str = "session") -> list[MemoryDraft]:
        drafts: list[MemoryDraft] = []
        seen: list[str] = []
        for turn in turns:
            for sentence in re.split(r"(?<=[.!?])\s+", turn.strip()):
                clean = sentence.strip()
                if len(clean.split()) < 4:
                    continue
                lowered = clean.lower()
                marker = next((m for m in STATEMENT_MARKERS if m in lowered), "")
                if not marker:
                    continue
                tags = [w for w in ("preference", "decision", "convention") if marker in (
                    "prefer", "always", "never", "convention",
                )] or [marker.replace(" ", "-")]
                draft = MemoryDraft(
                    name=self._slug(clean),
                    body=clean,
                    abstract=clean[:180],
                    tags=tags,
                    confidence=0.7 if marker in ("must", "should", "requirement") else 0.55,
                    source=source,
                )
                # Deduplicate drafts and everything already in the store.
                tokens = tokenize(draft.body)
                duplicate = any(jaccard(tokens, seen_tokens) > 0.7 for seen_tokens in seen)
                if not duplicate:
                    for memory in self.store.records.values():
                        if jaccard(tokens, tokenize(memory.body)) > 0.7:
                            duplicate = True
                            break
                if duplicate:
                    continue
                seen.append(tokens)
                drafts.append(draft)
        return drafts

    def commit(self, turns: list[str], source: str = "session", authority: str = "turn") -> list[WriteResult]:
        results: list[WriteResult] = []
        for draft in self.draft(turns, source):
            if draft.confidence < self.min_confidence:
                continue
            results.append(self.store.write(
                name=draft.name,
                body=draft.body,
                mem_type="preference" if "preference" in draft.tags else "decision",
                group="session",
                abstract=draft.abstract,
                tags=draft.tags,
                authority=authority,
                confidence=draft.confidence,
                sources=[source],
            ))
        return results


# ── Module-level engine ───────────────────────────────────────────────────

class AgentMemoryRuntime:
    """Facade used by Memora pipelines: store + recall + consolidation."""

    def __init__(self, root: str) -> None:
        self.store = MemoryStore(root).load()
        self.recall = RecallEngine(self.store)
        self.assembly = ThreeTrackRecall(self.store, self.recall)
        self.consolidator = SleepConsolidator(self.store)
        self.boundary = SessionBoundaryWriter(self.store)

    def remember(self, name: str, body: str, **kwargs: Any) -> WriteResult:
        return self.store.write(name=name, body=body, **kwargs)

    def recall_lines(self, query: str, limit: int = 8) -> list[str]:
        return [hit.as_line() for hit in self.recall.recall(query, limit=limit)]

    def session_context(self, query: str, budget: int = 800) -> dict[str, Any]:
        block = self.assembly.assemble(query, budget=budget)
        return {
            "text": block.text,
            "tokens": block.tokens,
            "budget": block.budget,
            "utilization": block.utilization,
            "tracks": block.tracks_used,
            "expanded": block.hits,
        }

    def sleep_pass(self) -> dict[str, Any]:
        proposals = self.consolidator.propose()
        applied = 0
        for proposal in proposals:
            if proposal.kind in ("merge", "promote"):
                applied += int(self.consolidator.apply(proposal))
        return {
            "proposals": [p.describe() for p in proposals],
            "applied": applied,
            "deferred_deletions": len([p for p in proposals if p.kind == "forget"]),
            "stats": self.store.stats(),
        }


# Module-level default root + lazy engine (no I/O at import time)
DEFAULT_ROOT = os.path.join(os.path.dirname(__file__), "..", ".data", "agent_memory")
_engine: AgentMemoryRuntime | None = None


def get_engine(root: str = "") -> AgentMemoryRuntime:
    """Return the shared runtime, creating it against ``root`` when first used."""
    global _engine
    if _engine is None or root:
        _engine = AgentMemoryRuntime(root or DEFAULT_ROOT)
    return _engine
