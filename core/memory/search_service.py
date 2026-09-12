"""
Hybrid Multi-Modal Search Service for Memora
Combines Dense Semantic Vector Search, Keyword Full-Text Search, Graph Traversal, and Reciprocal Rank Fusion (RRF).
"""
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func

from storage.relational.models import MemoryRecord, Namespace, Agent, LifecycleState, MemoryType
from storage.vector.qdrant_adapter import vector_adapter, VectorSearchResult
from storage.vector.embedding import EmbeddingGenerator
from core.identity.service import IdentityService
from core.policy.engine import PolicyEngine
from core.memory.graph_service import GraphService

class SearchResultItem:
    def __init__(
        self,
        record: MemoryRecord,
        final_score: float,
        semantic_score: float = 0.0,
        keyword_score: float = 0.0,
        graph_boost: float = 0.0,
        match_reasons: Optional[List[str]] = None
    ):
        self.record = record
        self.final_score = final_score
        self.semantic_score = semantic_score
        self.keyword_score = keyword_score
        self.graph_boost = graph_boost
        self.match_reasons = match_reasons or []

    def to_dict(self) -> Dict[str, Any]:
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
            "content_text": self.record.content_text,
            "source": self.record.source,
            "provenance": self.record.provenance or {},
            "confidence": self.record.confidence,
            "importance": self.record.importance,
            "lifecycle_state": self.record.lifecycle_state.value,
            "created_at": self.record.created_at.isoformat() if self.record.created_at else None,
            "final_score": round(self.final_score, 4),
            "semantic_score": round(self.semantic_score, 4),
            "keyword_score": round(self.keyword_score, 4),
            "graph_boost": round(self.graph_boost, 4),
            "match_reasons": self.match_reasons
        }

class SearchService:
    @classmethod
    def hybrid_search(
        cls,
        db: Session,
        query_text: str,
        actor_name: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        task_id: Optional[str] = None,
        trust_level: Optional[str] = None,
        namespace_path: Optional[str] = None,
        memory_types: Optional[List[MemoryType]] = None,
        min_score: float = 0.0,
        limit: int = 10,
        include_superseded: bool = False,
        include_archived: bool = False,
        include_expired: bool = False,
        vector_weight: float = 0.50,
        keyword_weight: float = 0.35,
        graph_weight: float = 0.15,
        entity_boost_weight: float = 0.10,
        purpose: Optional[str] = None,
        rrf_k: int = 60
    ) -> List[SearchResultItem]:
        actor = IdentityService.get_agent_by_name(db, actor_name) if actor_name else None
        resolved_tenant: str = str(tenant_id or (getattr(actor, "tenant_id", "default") if actor else "default"))

        # -------------------------------------------------------------
        # 1. SEMANTIC VECTOR SEARCH (STRICT TENANT ISOLATION)
        # -------------------------------------------------------------
        query_vector = EmbeddingGenerator.generate_embedding(query_text)
        vector_hits = vector_adapter.search_similarity(
            query_vector=query_vector,
            tenant_id=resolved_tenant,
            limit=limit * 3,
            score_threshold=0.30
        )
        vector_ranks = {hit.memory_id: (rank + 1, hit.score) for rank, hit in enumerate(vector_hits)}

        # -------------------------------------------------------------
        # 2. KEYWORD / FULL-TEXT SEARCH (STRICT TENANT ISOLATION)
        # -------------------------------------------------------------
        allowed_states = [LifecycleState.ACTIVE, LifecycleState.VERIFIED, LifecycleState.CANDIDATE]
        if include_superseded:
            allowed_states.append(LifecycleState.SUPERSEDED)
        if include_archived:
            allowed_states.append(LifecycleState.ARCHIVED)

        kw_query = db.query(MemoryRecord).join(Namespace).filter(
            MemoryRecord.tenant_id == resolved_tenant,
            MemoryRecord.lifecycle_state.in_(allowed_states)
        )
        # Identity and Scope Filters (Requirement 10: Strict User Isolation)
        if user_id:
            kw_query = kw_query.filter(MemoryRecord.user_id == user_id)
        if agent_id:
            kw_query = kw_query.filter(MemoryRecord.agent_id == agent_id)
        if workspace_id:
            kw_query = kw_query.filter(MemoryRecord.workspace_id == workspace_id)
        if task_id:
            kw_query = kw_query.filter(MemoryRecord.task_id == task_id)

        if namespace_path:
            kw_query = kw_query.filter(Namespace.path == namespace_path)
        if memory_types:
            kw_query = kw_query.filter(MemoryRecord.memory_type.in_(memory_types))

        # Query token matching
        tokens = query_text.lower().split()
        lexical_matches = []
        for r in kw_query.all():
            text_lower = r.content_text.lower()
            matched_count = sum(1 for t in tokens if t in text_lower)
            if matched_count > 0:
                lexical_score = matched_count / len(tokens)
                lexical_matches.append((r.id, lexical_score))

        lexical_matches.sort(key=lambda x: x[1], reverse=True)
        keyword_ranks = {item[0]: (rank + 1, item[1]) for rank, item in enumerate(lexical_matches[:limit * 3])}

        # -------------------------------------------------------------
        # 3. GRAPH RELATIONSHIP EXPANSION
        # -------------------------------------------------------------
        candidate_seed_ids = list(set(list(vector_ranks.keys())[:5] + list(keyword_ranks.keys())[:5]))
        graph_boosts = GraphService.get_graph_neighbors(db, candidate_seed_ids)

        # -------------------------------------------------------------
        # 4. RECIPROCAL RANK FUSION (RRF) & SCORE MERGE
        # -------------------------------------------------------------
        all_candidate_ids = set(vector_ranks.keys()) | set(keyword_ranks.keys()) | set(graph_boosts.keys())
        if not all_candidate_ids:
            return []

        cand_q = db.query(MemoryRecord).filter(
            MemoryRecord.id.in_(all_candidate_ids),
            MemoryRecord.tenant_id == resolved_tenant,
            MemoryRecord.lifecycle_state.in_(allowed_states)
        )
        if user_id:
            cand_q = cand_q.filter(MemoryRecord.user_id == user_id)
        if agent_id:
            cand_q = cand_q.filter(MemoryRecord.agent_id == agent_id)
        if workspace_id:
            cand_q = cand_q.filter(MemoryRecord.workspace_id == workspace_id)
        if task_id:
            cand_q = cand_q.filter(MemoryRecord.task_id == task_id)

        records = cand_q.all()
        record_map = {r.id: r for r in records}

        now_utc = datetime.now(timezone.utc)
        def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        scored_results: List[SearchResultItem] = []
        for mem_id, record in record_map.items():
            # Provenance Trust Level Filter
            if trust_level:
                record_trust = (record.provenance or {}).get("trust_level")
                if record_trust != trust_level:
                    continue
            # Temporal validity filter: valid_from <= now <= valid_until and expires_at > now
            if not include_expired:
                exp = _to_utc(record.expires_at)
                v_from = _to_utc(record.valid_from)
                v_until = _to_utc(record.valid_until)
                if exp and now_utc > exp:
                    continue
                if v_from and now_utc < v_from:
                    continue
                if v_until and now_utc > v_until:
                    continue

            # Policy Gate Check
            if actor:
                decision = PolicyEngine.evaluate_access(
                    db,
                    actor=actor,
                    namespace=record.namespace,
                    action="read",
                    purpose=purpose,
                    memory_id=record.id,
                    log_audit=False
                )
                if not decision.allowed:
                    continue

            v_rank, v_score = vector_ranks.get(mem_id, (None, 0.0))
            k_rank, k_score = keyword_ranks.get(mem_id, (None, 0.0))
            g_boost = graph_boosts.get(mem_id, 0.0)

            # RRF Formula
            rrf_score = 0.0
            reasons = []

            if v_rank:
                rrf_score += vector_weight * (1.0 / (rrf_k + v_rank))
                reasons.append(f"Semantic match (rank {v_rank}, sim {v_score:.2f})")
            if k_rank:
                rrf_score += keyword_weight * (1.0 / (rrf_k + k_rank))
                reasons.append(f"Keyword match (rank {k_rank}, overlap {k_score:.2f})")
            if g_boost > 0:
                rrf_score += graph_weight * g_boost
                reasons.append(f"Graph connection boost (+{g_boost:.2f})")

            # Entity boost
            entity_boost = 0.0
            if record.entities:
                query_lower = query_text.lower()
                for ent in record.entities:
                    if ent.lower() in query_lower:
                        entity_boost += entity_boost_weight
                        reasons.append(f"Entity match boost: '{ent}' (+{entity_boost_weight:.2f})")
                        break

            # Scale RRF score to 0.0-1.0 range + entity boost
            normalized_final = min(1.0, (rrf_score * (rrf_k / 2)) + entity_boost)

            if normalized_final >= min_score:
                scored_results.append(
                    SearchResultItem(
                        record=record,
                        final_score=normalized_final,
                        semantic_score=v_score,
                        keyword_score=k_score,
                        graph_boost=g_boost,
                        match_reasons=reasons
                    )
                )

        # Deterministic sort with tie-breaking: (-final_score, -confidence, -created_at timestamp, id)
        scored_results.sort(
            key=lambda x: (
                -round(x.final_score, 4),
                -round(x.record.confidence, 4),
                -(x.record.created_at.timestamp() if x.record.created_at else 0.0),
                x.record.id
            )
        )
        return scored_results[:limit]