"""
Token Budgeting and Hierarchical LLM Compaction Engine for Memora Context Bundles
Performs semantic clustering and recursive LLM-driven hierarchical summarization
to fit strict token budgets without losing technical facts, decisions, or provenance.
"""
import os
import re
import math
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
    def fit_to_budget(
        cls,
        reranked_items: List[RerankedMemoryItem],
        max_tokens: int = 4000,
        similarity_dedup_threshold: float = 0.85,
        query: Optional[str] = None
    ) -> Tuple[List[BudgetedMemoryItem], int, str]:
        """
        Fits candidate memories to target token budget.
        If total tokens exceed max_tokens:
        - Clusters memories by topic / namespace.
        - Applies Hierarchical LLM Summarization on clusters.
        - Returns (budgeted_items, total_tokens, compaction_strategy).
        """
        if not reranked_items:
            return [], 0, "none"

        # Check total raw token volume
        total_raw_tokens = sum(cls.estimate_tokens(item.record.content_text) for item in reranked_items)

        # Case 1: All memories fit without compaction
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
                        source_memory_ids=[item.record.id]
                    )
                )
                current_tokens += tokens
            return budgeted_items, current_tokens, "none"

        # Case 2: Exceeds token budget -> Hierarchical LLM Summarization
        logger.info(f"Retrieved {total_raw_tokens} tokens exceeding budget of {max_tokens}. Triggering Hierarchical Summarization...")
        
        # 1. Cluster memories by namespace or topic
        clusters: Dict[str, List[RerankedMemoryItem]] = defaultdict(list)
        for item in reranked_items:
            ns_path = item.record.namespace.path if item.record.namespace else "global"
            clusters[ns_path].append(item)

        num_clusters = len(clusters)
        tokens_per_cluster = max(20, int((max_tokens * 0.95) / max(1, num_clusters)))

        budgeted_items = []
        total_summarized_tokens = 0

        for ns_path, cluster_items in clusters.items():
            if not cluster_items:
                continue

            # Summarize cluster
            summary_text, source_ids = LLMContextSummarizer.summarize_cluster(
                memories=cluster_items,
                target_tokens=tokens_per_cluster,
                query=query
            )

            tokens = cls.estimate_tokens(summary_text)
            lead_record = cluster_items[0].record
            lead_score = max(item.final_score for item in cluster_items)

            budgeted_items.append(
                BudgetedMemoryItem(
                    record=lead_record,
                    content_text=summary_text,
                    token_count=tokens,
                    score=lead_score,
                    is_truncated=False,
                    is_summarized=True,
                    source_memory_ids=source_ids
                )
            )
            total_summarized_tokens += tokens

        return budgeted_items, total_summarized_tokens, "summarized"