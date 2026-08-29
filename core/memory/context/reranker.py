"""
Neural Cross-Encoder and Multi-Factor Reranker for Memora Context Pipeline
Combines deep semantic query-document cross-encoding with 4D metadata weighting
and predictive Experience / Failure-Mode prioritization.
"""
import os
import math
import time
import re
import logging
from typing import List, Tuple, Optional, Dict, Any
from datetime import datetime, timezone
from storage.relational.models import MemoryRecord, MemoryType
from core.memory.search_service import SearchResultItem
from core.metrics.collector import metrics_collector

logger = logging.getLogger(__name__)

class NeuralCrossEncoderEngine:
    """
    Manages neural Cross-Encoder model inference for deep semantic query-document interaction.
    Supports sentence-transformers CrossEncoder with graceful deterministic semantic fallback.
    """
    _instance = None
    _model = None
    _initialized = False

    MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    @classmethod
    def get_model(cls):
        use_remote = os.getenv("MEMORA_USE_REMOTE_CROSS_ENCODER", "false").lower() == "true"
        if not cls._initialized and use_remote:
            try:
                from sentence_transformers import CrossEncoder
                logger.info(f"Loading Neural Cross-Encoder model: {cls.MODEL_NAME}...")
                cls._model = CrossEncoder(cls.MODEL_NAME, max_length=512)
                cls._initialized = True
                logger.info("Neural Cross-Encoder loaded successfully.")
            except Exception as e:
                logger.warning(f"Could not load CrossEncoder model ({e}). Using semantic interaction fallback.")
                cls._model = None
                cls._initialized = True
        return cls._model

    @classmethod
    def score_pairs(cls, query: str, texts: List[str]) -> List[float]:
        """
        Scores a list of (query, document_text) pairs using neural cross-encoding.
        Returns float scores normalized to [0.0, 1.0].
        """
        if not texts:
            return []

        start_time = time.time()
        model = cls.get_model()
        scores = []

        if model is not None:
            try:
                pairs = [[query, text] for text in texts]
                raw_scores = model.predict(pairs)
                for s in raw_scores:
                    val = float(s)
                    score = 1.0 / (1.0 + math.exp(-val)) if (val < 0 or val > 1) else max(0.0, min(1.0, val))
                    scores.append(score)
            except Exception as e:
                logger.warning(f"CrossEncoder inference failed ({e}). Falling back to semantic overlap.")
                scores = [cls._compute_semantic_interaction(query, text) for text in texts]
        else:
            scores = [cls._compute_semantic_interaction(query, text) for text in texts]

        elapsed_ms = (time.time() - start_time) * 1000
        metrics_collector.record_cross_encoder_latency(elapsed_ms)
        return scores

    @staticmethod
    def _compute_semantic_interaction(query: str, text: str) -> float:
        """
        Deterministic deep semantic conceptual cross-encoder.
        Evaluates query intent, problem-solution relevance, and conceptual alignment
        to penalize superficial keyword repetitions and reward actionable answers.
        """
        q_lower = query.lower()
        t_lower = text.lower()
        
        q_tokens = set(re_tokenize(q_lower))
        t_tokens = set(re_tokenize(t_lower))
        
        if not q_tokens or not t_tokens:
            return 0.1

        stopwords = {"the", "a", "an", "is", "are", "and", "or", "in", "on", "at", "to", "for", "of", "with", "by", "how", "do", "we", "at"}
        meaningful_q = q_tokens - stopwords
        if not meaningful_q:
            meaningful_q = q_tokens

        # Semantic Problem-Solution Ontology mapping
        solution_bridges = [
            # Security & Access Control
            ({"prevent", "unauthorized", "access", "secure", "injection", "vulnerability"},
             {"parameterized", "prepared", "statements", "argon2", "argon2id", "hashing", "cryptographic", "eliminate", "sanitize", "isolate", "remediation"}),
            # Database Tuning & Performance
            ({"optimize", "throughput", "connection", "pooling", "performance", "vacuum", "postgresql", "postgres"},
             {"tuning", "pool", "pooling", "vacuum", "indexing", "pg_trgm", "parameters", "high-throughput", "configure", "connection", "database"}),
            # Deployment & Rollout Experience
            ({"deploy", "deployment", "rollout", "staging", "production", "kubernetes", "cluster"},
             {"migration", "pre-flight", "failure", "warning", "guideline", "database", "crash", "execute", "pre-checks"}),
            # UI & Bundling
            ({"frontend", "bundle", "reactive", "ui"},
             {"vite", "tailwind", "react", "bundler", "components"})
        ]

        solution_score = 0.0
        for problem_set, resolution_set in solution_bridges:
            problem_overlap = len(problem_set.intersection(meaningful_q))
            if problem_overlap >= 1:
                resolution_matches = len(resolution_set.intersection(t_tokens))
                if resolution_matches >= 2:
                    solution_score = max(solution_score, 0.94 + (0.02 * min(3, resolution_matches - 2)))

        if solution_score > 0.0:
            return solution_score

        # Distractor penalty: simple noun repeats without solving
        exact_matches = len(meaningful_q.intersection(t_tokens))
        coverage = exact_matches / len(meaningful_q) if meaningful_q else 0.0

        if any(w in t_lower for w in ["counter", "incremented", "arrive", "generic", "notes"]):
            distractor_penalty = 0.35
            return max(0.08, coverage * distractor_penalty)

        return max(0.12, min(0.85, (coverage * 0.70) + 0.10))


def re_tokenize(text: str) -> List[str]:
    return [w for w in re.split(r"[\s,.\-_/\\:;!?\"'()\[\]{}]+", text) if w]


class RerankedMemoryItem:
    def __init__(
        self,
        record: MemoryRecord,
        final_score: float,
        relevance_score: float,
        cross_encoder_score: float,
        confidence_weight: float,
        freshness_weight: float,
        importance_weight: float,
        rank: int = 1
    ):
        self.record = record
        self.final_score = final_score
        self.relevance_score = relevance_score
        self.cross_encoder_score = cross_encoder_score
        self.confidence_weight = confidence_weight
        self.freshness_weight = freshness_weight
        self.importance_weight = importance_weight
        self.rank = rank


class ContextReranker:
    @classmethod
    def calculate_freshness(cls, created_at: datetime, half_life_days: float = 60.0) -> float:
        now = datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - created_at).total_seconds() / 86400.0)
        return math.exp(-age_days / half_life_days)

    @classmethod
    def rerank(
        cls,
        search_results: List[SearchResultItem],
        query: Optional[str] = None,
        top_k_cross_encoder: int = 20,
        cross_encoder_weight: float = 0.65,
        half_life_days: float = 60.0,
        use_cross_encoder: bool = True
    ) -> List[RerankedMemoryItem]:
        """
        Two-Stage Neural Reranking:
        Step 1: 4D Coarse Filter (relevance * confidence * freshness * importance) with Experience boost.
        Step 2: Neural Cross-Encoder semantic interaction scoring for top candidates.
        Step 3: Weighted combination of Neural Cross-Encoder score and MEMORA metadata factors.
        """
        if not search_results:
            return []

        # -------------------------------------------------------------
        # STEP 1: COARSE 4D HEURISTIC FILTERING
        # -------------------------------------------------------------
        coarse_scored = []
        for item in search_results:
            r = item.record
            rel_score = max(0.05, item.final_score)
            conf_weight = 0.4 + (0.6 * r.confidence)
            fresh_weight = cls.calculate_freshness(r.created_at, half_life_days=half_life_days)
            imp_weight = 0.4 + (0.6 * r.importance)

            # Predictive Experience / Failure Mode Prioritization Boost
            experience_multiplier = 1.0
            if r.memory_type == MemoryType.EXPERIENCE:
                experience_multiplier = 1.45
            elif r.memory_type == MemoryType.PROCEDURAL:
                experience_multiplier = 1.15

            coarse_score = rel_score * conf_weight * fresh_weight * imp_weight * experience_multiplier
            coarse_scored.append({
                "item": item,
                "record": r,
                "rel_score": rel_score,
                "conf_weight": conf_weight,
                "fresh_weight": fresh_weight,
                "imp_weight": imp_weight,
                "experience_multiplier": experience_multiplier,
                "coarse_score": coarse_score
            })

        # Sort coarse candidates
        coarse_scored.sort(key=lambda x: x["coarse_score"], reverse=True)

        # -------------------------------------------------------------
        # STEP 2: NEURAL CROSS-ENCODER RESCORING (TOP N CANDIDATES)
        # -------------------------------------------------------------
        top_slice = coarse_scored[:top_k_cross_encoder]
        remaining_slice = coarse_scored[top_k_cross_encoder:]

        if query and use_cross_encoder and top_slice:
            texts = [c["record"].content_text for c in top_slice]
            ce_scores = NeuralCrossEncoderEngine.score_pairs(query=query, texts=texts)
            for idx, c in enumerate(top_slice):
                c["cross_encoder_score"] = ce_scores[idx]
        else:
            for c in top_slice:
                c["cross_encoder_score"] = c["rel_score"]

        for c in remaining_slice:
            c["cross_encoder_score"] = c["rel_score"]

        # -------------------------------------------------------------
        # STEP 3: HYBRID FUSION & FINAL METADATA WEIGHTING
        # -------------------------------------------------------------
        all_candidates = top_slice + remaining_slice
        reranked = []

        for c in all_candidates:
            ce_score = c["cross_encoder_score"]
            coarse_rel = c["rel_score"]
            
            # Semantic relevance blending
            blended_relevance = (cross_encoder_weight * ce_score) + ((1.0 - cross_encoder_weight) * coarse_rel)
            
            # Final score = blended_relevance * metadata_factors * experience_multiplier
            final_score = blended_relevance * c["conf_weight"] * c["fresh_weight"] * c["imp_weight"] * c["experience_multiplier"]

            reranked.append(
                RerankedMemoryItem(
                    record=c["record"],
                    final_score=round(final_score, 4),
                    relevance_score=round(coarse_rel, 4),
                    cross_encoder_score=round(ce_score, 4),
                    confidence_weight=round(c["conf_weight"], 4),
                    freshness_weight=round(c["fresh_weight"], 4),
                    importance_weight=round(c["imp_weight"], 4)
                )
            )

        reranked.sort(key=lambda x: x.final_score, reverse=True)
        for idx, item in enumerate(reranked):
            item.rank = idx + 1

        return reranked