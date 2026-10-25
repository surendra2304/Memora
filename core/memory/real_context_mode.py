"""
Memora — context-mode Engine

Context window optimization for AI coding agents. Sandboxes tool output,
persists session memory, enforces routing across platforms via MCP + hooks.

Key concepts: context compression, tool output sandboxing, session memory
persistence, context routing, and output filtering.

Tests: tests/test_real_batch4a.py
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextCompressor:
    """Context window compression."""
    @staticmethod
    def token_estimate(text: str) -> int:
        return len(text) // 4

    @staticmethod
    def compress_text(text: str, max_tokens: int = 1000) -> str:
        tokens = ContextCompressor.token_estimate(text)
        if tokens <= max_tokens:
            return text
        ratio = max_tokens / tokens
        words = text.split()
        target_words = int(len(words) * ratio)
        lines = text.split("\n")
        if len(lines) <= 1:
            return " ".join(words[:target_words]) + " ..."
        total_chars = len(text)
        target_chars = int(total_chars * ratio)
        current = 0
        result_lines = []
        for line in lines:
            if current + len(line) > target_chars:
                remaining = target_chars - current
                if remaining > 0:
                    result_lines.append(line[:remaining] + " ...")
                break
            result_lines.append(line)
            current += len(line) + 1
        return "\n".join(result_lines)

    @staticmethod
    def summarize_conversation(messages: list[dict]) -> str:
        if not messages:
            return ""
        summary_parts = []
        for msg in messages[:10]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            content_summary = content[:100] if len(content) > 100 else content
            summary_parts.append(f"{role}: {content_summary}")
        return "\n".join(summary_parts)

    @staticmethod
    def extract_key_points(text: str) -> list[str]:
        sentences = [s.strip() for s in re.split(r'[.!?]', text) if s.strip()]
        key_points = []
        for sentence in sentences:
            words = sentence.split()
            if len(words) >= 5 and len(words) <= 30:
                key_points.append(sentence)
        return key_points[:10]

    @staticmethod
    def priority_rank(items: list[str]) -> list[str]:
        scored = []
        for item in items:
            score = len(item) + sum(1 for c in item if c.isupper()) * 2
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [item for _, item in scored]

    @staticmethod
    def deduplicate(text: str) -> str:
        lines = text.split("\n")
        seen = set()
        result = []
        for line in lines:
            normalized = line.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(line)
        return "\n".join(result)

    @staticmethod
    def context_reduction_percentage(original: str, compressed: str) -> float:
        original_tokens = ContextCompressor.token_estimate(original)
        compressed_tokens = ContextCompressor.token_estimate(compressed)
        if original_tokens == 0:
            return 0.0
        return (original_tokens - compressed_tokens) / original_tokens


@dataclass
class ToolOutputSandbox:
    """Tool output sandboxing for context optimization."""
    @staticmethod
    def sandbox_output(output: str, max_lines: int = 50) -> str:
        lines = output.split("\n")
        if len(lines) <= max_lines:
            return output
        truncated = lines[:max_lines // 2]
        omitted = len(lines) - max_lines
        truncated.append(f"\n[... {omitted} lines omitted ...]")
        truncated.extend(lines[-max_lines // 2:])
        return "\n".join(truncated)

    @staticmethod
    def extract_diff(output: str) -> dict[str, list[str]]:
        added = []
        removed = []
        for line in output.split("\n"):
            if line.startswith("+") and not line.startswith("+++"):
                added.append(line[1:].strip())
            elif line.startswith("-") and not line.startswith("---"):
                removed.append(line[1:].strip())
        return {"added": added, "removed": removed}

    @staticmethod
    def extract_errors(output: str) -> list[str]:
        error_patterns = [
            r'error[:\s]+(.+)',
            r'Error: (.+)',
            r'FAILED: (.+)',
            r'Traceback.*?Error: (.+)',
        ]
        errors = []
        for pattern in error_patterns:
            matches = re.findall(pattern, output, re.IGNORECASE | re.DOTALL)
            for match in matches:
                match = match.strip()
                if match and len(match) < 200:
                    errors.append(match)
        return errors

    @staticmethod
    def extract_summary(output: str) -> str:
        lines = output.split("\n")
        for line in lines:
            if re.search(r'(summary|result|status|success|failure)', line, re.IGNORECASE):
                return line.strip()[:200]
        return lines[-1].strip()[:200] if lines else ""

    @staticmethod
    def sandbox_by_type(output: str, output_type: str) -> str:
        if output_type == "test":
            errors = ToolOutputSandbox.extract_errors(output)
            summary = ToolOutputSandbox.extract_summary(output)
            parts = []
            if summary:
                parts.append(summary)
            if errors:
                parts.append(f"Errors ({len(errors)}):")
                parts.extend(errors[:5])
            return "\n".join(parts)
        elif output_type == "git":
            return ToolOutputSandbox.sandbox_output(output, max_lines=30)
        elif output_type == "build":
            errors = ToolOutputSandbox.extract_errors(output)
            summary = ToolOutputSandbox.extract_summary(output)
            parts = []
            if summary:
                parts.append(summary)
            if errors:
                parts.append(f"Errors ({len(errors)}):")
                parts.extend(errors[:10])
            return "\n".join(parts)
        return ToolOutputSandbox.sandbox_output(output, max_lines=50)

    @staticmethod
    def context_savings(original: str, sandboxed: str) -> float:
        original_tokens = ContextCompressor.token_estimate(original)
        sandboxed_tokens = ContextCompressor.token_estimate(sandboxed)
        if original_tokens == 0:
            return 0.0
        return (original_tokens - sandboxed_tokens) / original_tokens


@dataclass
class SessionMemory:
    """Session memory persistence."""
    session_id: str
    memory_entries: list[dict] = field(default_factory=list)

    @staticmethod
    def create_session(session_id: str) -> SessionMemory:
        return SessionMemory(session_id=session_id)

    def store(self, key: str, value: str, category: str = "general") -> None:
        entry = {
            "key": key,
            "value": value,
            "category": category,
            "timestamp": len(self.memory_entries),
        }
        self.memory_entries.append(entry)

    def retrieve(self, key: str) -> str | None:
        for entry in self.memory_entries:
            if entry["key"] == key:
                return entry["value"]
        return None

    def search(self, query: str) -> list[dict]:
        query_lower = query.lower()
        return [
            e for e in self.memory_entries
            if query_lower in e["key"].lower() or query_lower in e["value"].lower()
        ]

    @property
    def entry_count(self) -> int:
        return len(self.memory_entries)

    @property
    def categories(self) -> list[str]:
        return list(set(e["category"] for e in self.memory_entries))

    def memory_usage_bytes(self) -> int:
        return sum(len(e["value"]) for e in self.memory_entries)

    def memory_usage_tokens(self) -> int:
        return ContextCompressor.token_estimate(str(self.memory_entries))

    def most_recent(self) -> dict | None:
        return self.memory_entries[-1] if self.memory_entries else None

    def by_category(self, category: str) -> list[dict]:
        return [e for e in self.memory_entries if e["category"] == category]

    def session_summary(self) -> str:
        if not self.memory_entries:
            return ""
        categories = defaultdict(int)
        for e in self.memory_entries:
            categories[e["category"]] += 1
        parts = [f"Session {self.session_id}:"]
        for cat, count in categories.items():
            parts.append(f"  {cat}: {count} entries")
        return "\n".join(parts)


@dataclass
class ContextRouter:
    """Context routing across platforms."""
    @staticmethod
    def supported_platforms() -> list[str]:
        return [
            "claude_code", "cursor", "windsurf", "vs_code", "neovim",
            "jetbrains", "zed", "copilot", "continue", "codeium",
            "tabnine", "aider", "amp", "goose", "cline",
            "chatgpt", "gemini", "perplexity",
        ]

    @staticmethod
    def platform_context_limit(platform: str) -> int:
        limits = {
            "claude_code": 200000,
            "cursor": 120000,
            "windsurf": 100000,
            "vs_code": 100000,
            "neovim": 100000,
            "jetbrains": 120000,
            "zed": 100000,
            "copilot": 8000,
            "continue": 100000,
            "codeium": 100000,
            "tabnine": 100000,
            "aider": 100000,
            "amp": 100000,
            "goose": 100000,
            "cline": 100000,
            "chatgpt": 128000,
            "gemini": 1000000,
            "perplexity": 100000,
        }
        return limits.get(platform, 100000)

    @staticmethod
    def route_context(content: str, platform: str) -> str:
        limit = ContextRouter.platform_context_limit(platform)
        tokens = ContextCompressor.token_estimate(content)
        if tokens <= limit:
            return content
        return ContextCompressor.compress_text(content, max_tokens=limit * 0.8)

    @staticmethod
    def adapt_format(content: str, platform: str) -> str:
        if platform in ("vs_code", "neovim", "jetbrains"):
            return content
        elif platform in ("claude_code", "aider", "goose"):
            return content
        elif platform == "chatgpt":
            return ContextCompressor.compress_text(content, max_tokens=8000)
        return content

    @staticmethod
    def platform_supports_hooks(platform: str) -> bool:
        hook_platforms = ["claude_code", "cursor", "windsurf", "vs_code", "neovim", "jetbrains", "zed", "continue", "cline"]
        return platform in hook_platforms

    @staticmethod
    def routing_decision(content: str, platform: str) -> dict[str, Any]:
        limit = ContextRouter.platform_context_limit(platform)
        tokens = ContextCompressor.token_estimate(content)
        needs_compression = tokens > limit * 0.8
        return {
            "platform": platform,
            "token_count": tokens,
            "context_limit": limit,
            "utilization": tokens / limit if limit > 0 else 0.0,
            "needs_compression": needs_compression,
            "supports_hooks": ContextRouter.platform_supports_hooks(platform),
        }


@dataclass
class MCPHookEngine:
    """MCP hook engine for context management."""
    @staticmethod
    def register_hook(hook_id: str, platform: str, trigger: str, action: str) -> dict:
        return {
            "hook_id": hook_id,
            "platform": platform,
            "trigger": trigger,
            "action": action,
        }

    @staticmethod
    def pre_tool_hook(tool_name: str, output: str) -> str:
        if tool_name in ("git_diff", "git_status", "git_log"):
            return ToolOutputSandbox.sandbox_by_type(output, "git")
        elif tool_name in ("pytest", "jest", "unittest"):
            return ToolOutputSandbox.sandbox_by_type(output, "test")
        elif tool_name in ("npm_build", "make", "cargo_build"):
            return ToolOutputSandbox.sandbox_by_type(output, "build")
        return ToolOutputSandbox.sandbox_output(output, max_lines=50)

    @staticmethod
    def post_tool_hook(tool_name: str, output: str) -> dict[str, Any]:
        return {
            "tool": tool_name,
            "original_tokens": ContextCompressor.token_estimate(output),
            "sandboxed_tokens": ContextCompressor.token_estimate(MCPHookEngine.pre_tool_hook(tool_name, output)),
            "savings_percentage": ToolOutputSandbox.context_savings(output, MCPHookEngine.pre_tool_hook(tool_name, output)),
        }

    @staticmethod
    def context_hook_chain(tools: list[str], outputs: list[str]) -> list[str]:
        sandboxed = []
        for tool, output in zip(tools, outputs):
            sandboxed.append(MCPHookEngine.pre_tool_hook(tool, output))
        return sandboxed

    @staticmethod
    def total_savings(original_outputs: list[str], sandboxed_outputs: list[str]) -> float:
        total_original = sum(ContextCompressor.token_estimate(o) for o in original_outputs)
        total_sandboxed = sum(ContextCompressor.token_estimate(o) for o in sandboxed_outputs)
        if total_original == 0:
            return 0.0
        return (total_original - total_sandboxed) / total_original


@dataclass
class ContextModeManager:
    """Unified context mode management."""
    compressor: ContextCompressor = field(default_factory=ContextCompressor)
    sandbox: ToolOutputSandbox = field(default_factory=ToolOutputSandbox)
    session: SessionMemory | None = None
    router: ContextRouter = field(default_factory=ContextRouter)
    hooks: MCPHookEngine = field(default_factory=MCPHookEngine)

    def __post_init__(self) -> None:
        self.session = SessionMemory.create_session("default")

    def optimize_context(self, content: str, platform: str = "claude_code") -> dict[str, Any]:
        decision = ContextRouter.routing_decision(content, platform)
        routed = ContextRouter.route_context(content, platform)
        compressed = ContextCompressor.compress_text(routed)
        reduction = ContextCompressor.context_reduction_percentage(content, compressed)
        return {
            "original_tokens": ContextCompressor.token_estimate(content),
            "optimized_tokens": ContextCompressor.token_estimate(compressed),
            "reduction_percentage": reduction,
            "platform": platform,
            "utilization": decision["utilization"],
        }

    def process_tool_output(self, tool_name: str, output: str) -> dict[str, Any]:
        sandboxed = MCPHookEngine.pre_tool_hook(tool_name, output)
        savings = ToolOutputSandbox.context_savings(output, sandboxed)
        return {
            "tool": tool_name,
            "sandboxed_output": sandboxed,
            "savings": savings,
        }

    def persist_session_memory(self, key: str, value: str, category: str = "general") -> None:
        if self.session:
            self.session.store(key, value, category)

    def retrieve_session_memory(self, key: str) -> str | None:
        if self.session:
            return self.session.retrieve(key)
        return None

    def session_stats(self) -> dict[str, Any]:
        if not self.session:
            return {"entries": 0, "tokens": 0, "bytes": 0}
        return {
            "entries": self.session.entry_count,
            "tokens": self.session.memory_usage_tokens(),
            "bytes": self.session.memory_usage_bytes(),
            "categories": len(self.session.categories),
        }

    def context_report(self, contents: list[str], platforms: list[str]) -> list[dict]:
        report = []
        for content, platform in zip(contents, platforms):
            report.append(self.optimize_context(content, platform))
        return report

    def sandbox_all_outputs(self, tool_outputs: dict[str, str]) -> dict[str, Any]:
        sandboxed = {}
        total_original = 0
        total_sandboxed = 0
        for tool, output in tool_outputs.items():
            sandboxed_output = MCPHookEngine.pre_tool_hook(tool, output)
            sandboxed[tool] = sandboxed_output
            total_original += ContextCompressor.token_estimate(output)
            total_sandboxed += ContextCompressor.token_estimate(sandboxed_output)
        savings = 0.0
        if total_original > 0:
            savings = (total_original - total_sandboxed) / total_original
        return {
            "sandboxed_outputs": sandboxed,
            "total_original_tokens": total_original,
            "total_sandboxed_tokens": total_sandboxed,
            "savings_percentage": savings,
        }

    @property
    def session_id(self) -> str:
        return self.session.session_id if self.session else ""

    @property
    def session_entry_count(self) -> int:
        return self.session.entry_count if self.session else 0
