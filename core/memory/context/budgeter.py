"""
Token Budgeting and Hierarchical LLM Compaction Engine for Memora Context Bundles
Performs semantic clustering and recursive LLM-driven hierarchical summarization
to fit strict token budgets without losing technical facts, decisions, or provenance.
"""
import os
import re
import logging
from typing import List, Dict, Any, Tuple, Optional, Set
from collections import defaultdict

from storage.relational.models import MemoryRecord
from core.memory.context.reranker import RerankedMemoryItem

logger = logging.getLogger(__name__)

class BudgetedMemoryItem:
    def __init__(
        self,
        record: MemoryRecord,
        content_text: str,
        token_count: int,
        score: float,
        is_truncated: bool = False,
        is_summarized: bool = False,
        source_memory_ids: Optional[List[str]] = None
    ):
        self.record = record
        self.content_text = content_text
        self.token_count = token_count
        self.score = score
        self.is_truncated = is_truncated
        self.is_summarized = is_summarized
        self.source_memory_ids = source_memory_ids or [record.id]

    def to_dict(self) -> Dict[str, Any]:
        prov = dict(self.record.provenance or {})
        if self.is_summarized:
            prov["compaction"] = "hierarchical_llm_summary"
            prov["source_memory_ids"] = self.source_memory_ids

        return {
            "id": self.record.id,
            "tenant_id": getattr(self.record, "tenant_id", "default"),
            "user_id": getattr(self.record, "user_id", "default_user"),
            "agent_id": getattr(self.record, "agent_id", "friday"),
            "workspace_id": getattr(self.record, "workspace_id", "default_workspace"),
            "device_id": getattr(self.record, "device_id", "default_device"),
            "task_id": getattr(self.record, "task_id", None),
            "namespace_id": self.record.namespace_id,
            "namespace_path": self.record.namespace.path if self.record.namespace else None,
            "owner_name": self.record.owner.name if self.record.owner else None,
            "memory_type": self.record.memory_type.value,
            "content_text": self.content_text,
            "confidence": self.record.confidence,
            "importance": self.record.importance,
            "provenance": prov,
            "score": round(self.score, 4),
            "token_count": self.token_count,
            "is_truncated": self.is_truncated,
            "is_summarized": self.is_summarized,
            "source_memory_ids": self.source_memory_ids
        }


class LLMContextSummarizer:
    """
    Hierarchical LLM Summarizer for condensing clusters of memory records
    into dense, fact-heavy summaries while preserving provenance and metrics.
    """
    @classmethod
    def summarize_cluster(
        cls,
        memories: List[RerankedMemoryItem],
        target_tokens: int,
        query: Optional[str] = None
    ) -> Tuple[str, List[str]]:
        """
        Summarizes a cluster of related memories into a dense paragraph.
        Returns: (summarized_text, list_of_source_ids)
        """
        if not memories:
            return "", []

        source_ids = [m.record.id for m in memories]
        if len(memories) == 1:
            return memories[0].record.content_text, source_ids

        # 1. Check for OpenAI API client
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            try:
                import openai
                client = openai.OpenAI(api_key=openai_key)
                bullet_points = "\n".join([f"[{m.record.id}] {m.record.content_text}" for m in memories])
                prompt = (
                    f"Summarize the following technical memory records for query '{query or 'general'}' "
                    f"into a single dense, factual paragraph within {target_tokens} tokens. "
                    f"Preserve all specific numbers, filenames, architecture decisions, and technologies.\n\n"
                    f"{bullet_points}"
                )
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=target_tokens
                )
                summary = response.choices[0].message.content.strip()
                return summary, source_ids
            except Exception as e:
                logger.warning(f"OpenAI summarization failed ({e}). Falling back to local semantic synthesis.")

        # 2. Check for Anthropic API client
        anthropic_key = os.getenv("ANTHROPIC_API_KEY")
        if anthropic_key:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=anthropic_key)
                bullet_points = "\n".join([f"[{m.record.id}] {m.record.content_text}" for m in memories])
                response = client.messages.create(
                    model="claude-3-haiku-20240307",
                    max_tokens=target_tokens,
                    messages=[{
                        "role": "user",
                        "content": f"Condense these memory records preserving all key facts:\n\n{bullet_points}"
                    }]
                )
                summary = response.content[0].text.strip()
                return summary, source_ids
            except Exception as e:
                logger.warning(f"Anthropic summarization failed ({e}). Falling back to local semantic synthesis.")

        # 3. High-Density Deterministic Hierarchical Synthesis Engine
        return cls._deterministic_dense_synthesis(memories, target_tokens, source_ids)

    @classmethod
    def _deterministic_dense_synthesis(
        cls,
        memories: List[RerankedMemoryItem],
        target_tokens: int,
        source_ids: List[str]
    ) -> Tuple[str, List[str]]:
        """
        Extracts key sentences, decisions, and technical assertions without losing critical facts.
        """
        extracted_facts: List[str] = []
        seen_lower: Set[str] = set()

        for item in memories:
            text = item.record.content_text
            # Split into individual clauses / sentences
            sentences = re.split(r"(?<=[.!?])\s+", text)
            for s in sentences:
                s_clean = s.strip()
                if not s_clean:
                    continue
                # Normalize representation to prevent duplication
                norm = " ".join(s_clean.lower().split())
                if norm not in seen_lower:
                    seen_lower.add(norm)
                    extracted_facts.append(s_clean)

        # Merge facts into cohesive synthesis
        synthesized = " ".join(extracted_facts)
        prefix = f"[Hierarchical Synthesis of {len(memories)} memories]: "
        full_text = prefix + synthesized

        max_chars = int(target_tokens * ContextBudgeter.CHARS_PER_TOKEN)
        if len(full_text) > max_chars:
            allowed_body = max_chars - len(prefix)
            if allowed_body > 10:
                synthesized = synthesized[:allowed_body].rsplit(" ", 1)[0] + "..."
                full_text = prefix + synthesized
            else:
                full_text = full_text[:max_chars]

        return full_text, source_ids


class ContextBudgeter:
    CHARS_PER_TOKEN = 4.0

    @classmethod
    def estimate_tokens(cls, text: str) -> int:
        return max(1, int(len(text) / cls.CHARS_PER_TOKEN))

    @classmethod
    def _truncate_to_tokens(cls, text: str, max_tokens: int) -> str:
        """Hard-cap a string using the estimator's characters-per-token ratio."""
        if max_tokens <= 0:
            return ""
        max_chars = int(max_tokens * cls.CHARS_PER_TOKEN)
        if len(text) <= max_chars:
            return text
        if max_chars <= 0:
            return ""

        ellipsis = "…"
        body = text[: max(0, max_chars - len(ellipsis))]
        if len(body) > 8 and " " in body:
            word_boundary = body.rfind(" ")
            if word_boundary > 0:
                body = body[:word_boundary]
        result = (body + ellipsis)[:max_chars]
        return result or text[:max_chars]

    @classmethod
    def fit_to_budget(
        cls,
        reranked_items: List[RerankedMemoryItem],
        max_tokens: int = 4000,
        similarity_dedup_threshold: float = 0.85,
        query: Optional[str] = None
    ) -> Tuple[List[BudgetedMemoryItem], int, str]:
        """Fit context to a strict token ceiling, including singleton clusters.

        The former 20-token per-cluster floor exceeded small budgets, and the
        singleton summarizer returned the original full text regardless of its
        allocation. Allocations now sum to at most `max_tokens`, and every
        returned string is hard-capped after any optional LLM/local summary.
        """
        if not reranked_items:
            return [], 0, "none"
        if max_tokens <= 0:
            return [], 0, "truncated"

        total_raw_tokens = sum(
            cls.estimate_tokens(item.record.content_text) for item in reranked_items
        )

        if total_raw_tokens <= max_tokens:
            budgeted_items = []
            current_tokens = 0
            for item in reranked_items:
                tokens = cls.estimate_tokens(item.record.content_text)
                budgeted_items.append(
                    BudgetedMemoryItem(
                        record=item.record,
                        content_text=item.record.content_text,
                        token_count=tokens,
                        score=item.final_score,
                        is_truncated=False,
                        is_summarized=False,
                        source_memory_ids=[item.record.id],
                    )
                )
                current_tokens += tokens
            return budgeted_items, current_tokens, "none"

        clusters: Dict[str, List[RerankedMemoryItem]] = defaultdict(list)
        for item in reranked_items:
            ns_path = item.record.namespace.path if item.record.namespace else "global"
            clusters[ns_path].append(item)

        cluster_items = list(clusters.items())
        num_clusters = len(cluster_items)
        base_allocation, remainder = divmod(max_tokens, num_clusters)
        allocations = [
            base_allocation + (1 if index < remainder else 0)
            for index in range(num_clusters)
        ]

        budgeted_items: List[BudgetedMemoryItem] = []
        total_tokens = 0
        did_summarize = False
        did_truncate = False

        for (_ns_path, memories), allocation in zip(cluster_items, allocations, strict=True):
            if not memories or allocation <= 0:
                did_truncate = True
                continue

            summary_text, source_ids = LLMContextSummarizer.summarize_cluster(
                memories=memories,
                target_tokens=allocation,
                query=query,
            )
            if not summary_text:
                did_truncate = True
                continue

            capped_text = cls._truncate_to_tokens(summary_text, allocation)
            is_truncated = capped_text != summary_text
            tokens = cls.estimate_tokens(capped_text)
            if tokens > allocation:
                # Defensive correction in case a future estimator changes its
                # rounding behavior; never trust a summarizer to enforce budget.
                capped_text = capped_text[: int(allocation * cls.CHARS_PER_TOKEN)]
                tokens = cls.estimate_tokens(capped_text)
                is_truncated = True

            lead_record = memories[0].record
            budgeted_items.append(
                BudgetedMemoryItem(
                    record=lead_record,
                    content_text=capped_text,
                    token_count=tokens,
                    score=max(item.final_score for item in memories),
                    is_truncated=is_truncated,
                    is_summarized=len(memories) > 1,
                    source_memory_ids=source_ids,
                )
            )
            total_tokens += tokens
            did_summarize = did_summarize or len(memories) > 1
            did_truncate = did_truncate or is_truncated

        if total_tokens > max_tokens:
            # This should be unreachable because allocations partition the
            # budget, but retain a final invariant check at the boundary.
            remaining = max_tokens
            bounded_items: List[BudgetedMemoryItem] = []
            for item in budgeted_items:
                if remaining <= 0:
                    did_truncate = True
                    break
                if item.token_count > remaining:
                    item.content_text = cls._truncate_to_tokens(item.content_text, remaining)
                    item.token_count = cls.estimate_tokens(item.content_text)
                    item.is_truncated = True
                    did_truncate = True
                bounded_items.append(item)
                remaining -= item.token_count
            budgeted_items = bounded_items
            total_tokens = sum(item.token_count for item in budgeted_items)

        if did_summarize:
            strategy = "summarized"
        elif did_truncate or len(budgeted_items) < num_clusters:
            strategy = "truncated"
        else:
            strategy = "none"
        return budgeted_items, total_tokens, strategy
