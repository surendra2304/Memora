"""
Context Builder Service for Memora
Generates curated, token-budgeted, policy-filtered Context Bundles for AI Agents
with Predictive Experience and Failure-Mode Pre-Fetching.
"""
import time
import uuid
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import or_

from storage.relational.models import MemoryRelationship, Namespace, Agent, MemoryRecord, MemoryType, LifecycleState
from core.identity.service import IdentityService
from core.policy.engine import PolicyEngine
from core.memory.search_service import SearchService, SearchResultItem
from core.memory.context.reranker import ContextReranker
from core.memory.context.budgeter import ContextBudgeter, BudgetedMemoryItem
from core.metrics.collector import metrics_collector
from core.events.emitter import event_emitter

class ContextBundle:
    def __init__(
        self,
        bundle_id: str,
        query: str,
        target_agent: str,
        total_tokens_estimated: int,
        token_budget_limit: int,
        summary: str,
        memories: List[Dict[str, Any]],
        graph_edges: List[Dict[str, Any]],
        created_at: str,
        compaction_strategy: str = "none",
        is_degraded: bool = False
    ):
        self.bundle_id = bundle_id
        self.query = query
        self.target_agent = target_agent
        self.total_tokens_estimated = total_tokens_estimated
        self.token_budget_limit = token_budget_limit
        self.summary = summary
        self.memories = memories
        self.graph_edges = graph_edges
        self.created_at = created_at
        self.compaction_strategy = compaction_strategy
        self.is_degraded = is_degraded

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "query": self.query,
            "target_agent": self.target_agent,
            "total_tokens_estimated": self.total_tokens_estimated,
            "token_budget_limit": self.token_budget_limit,
            "summary": self.summary,
            "compaction_strategy": self.compaction_strategy,
            "is_degraded": self.is_degraded,
            "memories_count": len(self.memories),
            "memories": self.memories,
            "graph_edges_count": len(self.graph_edges),
            "graph_edges": self.graph_edges,
            "created_at": self.created_at
        }

class ContextBuilderService:
    @classmethod
    def build_context_bundle(
        cls,
        db: Session,
        agent_id_or_name: str,
        task_query: str,
        token_budget: int = 4000,
        namespace_path: Optional[str] = None,
        purpose: Optional[str] = None,
        max_candidates: int = 30
    ) -> ContextBundle:
        start_time = time.time()
        is_degraded = False

        # -------------------------------------------------------------
        # 1. RESOLVE IDENTITY & SCOPE
        # -------------------------------------------------------------
        actor = IdentityService.get_agent_by_id(db, agent_id_or_name)
        if not actor:
            actor = IdentityService.get_agent_by_name(db, agent_id_or_name)
        if not actor:
            actor = IdentityService.register_agent(db, agent_id_or_name)

        # -------------------------------------------------------------
        # 2. HYBRID RETRIEVAL WITH GRACEFUL DEGRADATION
        # -------------------------------------------------------------
        try:
            search_results = SearchService.hybrid_search(
                db=db,
                query_text=task_query,
                actor_name=actor.name,
                namespace_path=namespace_path,
                limit=max_candidates
            )
        except Exception as e:
            is_degraded = True
            search_results = SearchService.hybrid_search(
                db=db,
                query_text=task_query,
                actor_name=actor.name,
                namespace_path=namespace_path,
                limit=max_candidates,
                vector_weight=0.0,
                keyword_weight=0.85,
                graph_weight=0.15
            )

        # -------------------------------------------------------------
        # 2b. PREDICTIVE CONTEXT PRE-FETCHING (Experience & Procedural Stores)
        # -------------------------------------------------------------
        existing_result_ids = {item.record.id for item in search_results}
        exp_candidates = db.query(MemoryRecord).filter(
            MemoryRecord.memory_type.in_([MemoryType.EXPERIENCE, MemoryType.PROCEDURAL]),
            MemoryRecord.lifecycle_state.in_([LifecycleState.ACTIVE, LifecycleState.VERIFIED])
        ).all()

        q_tokens = set(re.split(r"[\s,.\-_/\\:;!?\"'()\[\]{}]+", task_query.lower()))
        stopwords = {"the", "a", "an", "is", "are", "and", "or", "in", "on", "at", "to", "for", "of", "with", "by", "how", "do", "we"}
        meaningful_q = q_tokens - stopwords

        predictive_hits = 0
        for exp in exp_candidates:
            if exp.id in existing_result_ids:
                continue

            exp_tokens = set(re.split(r"[\s,.\-_/\\:;!?\"'()\[\]{}]+", exp.content_text.lower()))
            overlap = meaningful_q.intersection(exp_tokens)

            # If task keywords overlap with experience or high-confidence operational guideline
            if overlap or exp.importance >= 0.95:
                search_results.append(
                    SearchResultItem(
                        record=exp,
                        final_score=0.90,
                        match_reasons=["predictive_experience_prefetch"]
                    )
                )
                existing_result_ids.add(exp.id)
                predictive_hits += 1

        if predictive_hits > 0:
            metrics_collector.record_predictive_hit()

        # -------------------------------------------------------------
        # 3. MULTI-FACTOR NEURAL CROSS-ENCODER RERANKING (WITH EXPERIENCE BOOST)
        # -------------------------------------------------------------
        reranked_items = ContextReranker.rerank(search_results, query=task_query)

        # -------------------------------------------------------------
        # 4. FAIL-CLOSED POLICY FILTER
        # -------------------------------------------------------------
        policy_filtered = []
        for item in reranked_items:
            decision = PolicyEngine.evaluate_access(
                db=db,
                actor=actor,
                namespace=item.record.namespace,
                action="read",
                purpose=purpose,
                memory_id=item.record.id,
                log_audit=False
            )
            if decision.allowed:
                policy_filtered.append(item)

        # Calculate raw uncompressed tokens
        raw_tokens = sum(ContextBudgeter.estimate_tokens(i.record.content_text) for i in policy_filtered)

        # -------------------------------------------------------------
        # 5. TOKEN BUDGETING & HIERARCHICAL LLM COMPACTION
        # -------------------------------------------------------------
        budgeted_memories, total_tokens, compaction_strategy = ContextBudgeter.fit_to_budget(
            policy_filtered,
            max_tokens=token_budget,
            query=task_query
        )

        if compaction_strategy == "summarized":
            tokens_saved = max(0, raw_tokens - total_tokens)
            metrics_collector.record_compaction(tokens_saved)

        included_memory_ids = []
        for m in budgeted_memories:
            included_memory_ids.extend(m.source_memory_ids)
        included_memory_ids = list(set(included_memory_ids))

        # -------------------------------------------------------------
        # 6. GRAPH RELATIONSHIP EDGES EXTRACTION
        # -------------------------------------------------------------
        graph_edges = []
        if len(included_memory_ids) > 1:
            rels = db.query(MemoryRelationship).filter(
                MemoryRelationship.source_memory_id.in_(included_memory_ids),
                MemoryRelationship.target_memory_id.in_(included_memory_ids)
            ).all()

            for r in rels:
                graph_edges.append({
                    "source_id": r.source_memory_id,
                    "target_id": r.target_memory_id,
                    "relationship_type": r.relationship_type,
                    "weight": r.weight
                })

        summary_lines = [
            f"Curated {len(budgeted_memories)} contextual memories for query '{task_query}'.",
            f"Agent: {actor.name} (Scope: {actor.bounded_scope or 'global/unbounded'}).",
            f"Tokens utilized: {total_tokens} / {token_budget} max budget.",
            f"Compaction strategy: {compaction_strategy}."
        ]
        if is_degraded:
            summary_lines.append("Warning: Executed under degraded vector store fallback.")
        if graph_edges:
            summary_lines.append(f"Discovered {len(graph_edges)} relational dependency edges between memories.")

        summary_text = " ".join(summary_lines)
        bundle_id = str(uuid.uuid4())

        elapsed_ms = (time.time() - start_time) * 1000
        metrics_collector.record_context_generation(tokens_used=total_tokens, token_budget=token_budget, latency_ms=elapsed_ms)

        event_emitter.publish("context.generated", {
            "bundle_id": bundle_id,
            "query": task_query,
            "agent": actor.name,
            "tokens": total_tokens,
            "memories_count": len(budgeted_memories),
            "compaction_strategy": compaction_strategy,
            "is_degraded": is_degraded
        })

        return ContextBundle(
            bundle_id=bundle_id,
            query=task_query,
            target_agent=actor.name,
            total_tokens_estimated=total_tokens,
            token_budget_limit=token_budget,
            summary=summary_text,
            memories=[m.to_dict() for m in budgeted_memories],
            graph_edges=graph_edges,
            created_at=datetime.now(timezone.utc).isoformat(),
            compaction_strategy=compaction_strategy,
            is_degraded=is_degraded
        )