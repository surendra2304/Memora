"""
Observability Metrics Engine for Memora
Tracks retrieval relevance, context usefulness, staleness, deduplication and contradiction signals,
write success, policy denial, latencies, and Phase 6 advanced metrics
(llm_compaction_tokens_saved, cross_encoder_reranking_latency_ms, predictive_context_hits).
"""
from typing import Dict, Any, List
from collections import deque
from datetime import datetime, timezone
import threading

class MetricsCollector:
    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        self._lock = threading.RLock()

        # Counters & Accumulators
        self.write_attempts = 0
        self.write_successes = 0
        self.policy_evaluations = 0
        self.policy_denials = 0
        self.contradictions_detected = 0
        self.deduplication_hits = 0

        # Phase 6 Advanced Metrics
        self.llm_compaction_tokens_saved = 0
        self.predictive_context_hits = 0
        self.cross_encoder_latencies_ms: deque = deque(maxlen=max_history)

        # Rolling sample deques
        self.relevance_scores: deque = deque(maxlen=max_history)
        self.context_token_utilizations: deque = deque(maxlen=max_history)
        self.memory_ages_days: deque = deque(maxlen=max_history)
        self.latencies_ms: deque = deque(maxlen=max_history)

    def record_write(
        self,
        success: bool = True,
        is_contradiction: bool = False,
        latency_ms: float = 0.0,
        is_duplicate: bool = False,
    ):
        with self._lock:
            self.write_attempts += 1
            if success:
                self.write_successes += 1
            if is_contradiction:
                self.contradictions_detected += 1
            if is_duplicate:
                self.deduplication_hits += 1
            if latency_ms > 0:
                self.latencies_ms.append(latency_ms)

    def record_policy_check(self, allowed: bool):
        with self._lock:
            self.policy_evaluations += 1
            if not allowed:
                self.policy_denials += 1

    def record_retrieval(self, relevance_scores: List[float], ages_days: List[float], latency_ms: float = 0.0):
        with self._lock:
            self.relevance_scores.extend(relevance_scores)
            self.memory_ages_days.extend(ages_days)
            if latency_ms > 0:
                self.latencies_ms.append(latency_ms)

    def record_context_generation(self, tokens_used: int, token_budget: int, latency_ms: float = 0.0):
        ratio = (tokens_used / max(1, token_budget)) if token_budget > 0 else 0.0
        with self._lock:
            self.context_token_utilizations.append(min(1.0, ratio))
            if latency_ms > 0:
                self.latencies_ms.append(latency_ms)

    def record_compaction(self, tokens_saved: int):
        if tokens_saved > 0:
            with self._lock:
                self.llm_compaction_tokens_saved += tokens_saved

    def record_cross_encoder_latency(self, latency_ms: float):
        if latency_ms > 0:
            with self._lock:
                self.cross_encoder_latencies_ms.append(latency_ms)

    def record_predictive_hit(self):
        with self._lock:
            self.predictive_context_hits += 1

    def _percentile(self, values: List[float], p: float) -> float:
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        k = (len(sorted_vals) - 1) * p
        f = int(k)
        c = min(len(sorted_vals) - 1, f + 1)
        d = k - f
        return round(sorted_vals[f] + d * (sorted_vals[c] - sorted_vals[f]), 2)

    def get_metrics_summary(self) -> Dict[str, Any]:
        # Take one consistent snapshot under the lock, then perform percentile
        # sorting without blocking request threads that are recording metrics.
        with self._lock:
            write_attempts = self.write_attempts
            write_successes = self.write_successes
            policy_evaluations = self.policy_evaluations
            policy_denials = self.policy_denials
            contradictions_detected = self.contradictions_detected
            deduplication_hits = self.deduplication_hits
            tokens_saved = self.llm_compaction_tokens_saved
            predictive_context_hits = self.predictive_context_hits
            relevance_scores = list(self.relevance_scores)
            context_utilizations = list(self.context_token_utilizations)
            memory_ages = list(self.memory_ages_days)
            latencies = list(self.latencies_ms)
            cross_encoder_latencies = list(self.cross_encoder_latencies_ms)

        write_rate = (write_successes / write_attempts) if write_attempts > 0 else 1.0
        denial_rate = (policy_denials / policy_evaluations) if policy_evaluations > 0 else 0.0
        contradiction_rate = (contradictions_detected / max(1, write_attempts)) if write_attempts > 0 else 0.0
        deduplication_rate = (deduplication_hits / write_attempts) if write_attempts > 0 else 0.0

        avg_relevance = sum(relevance_scores) / len(relevance_scores) if relevance_scores else 0.0
        avg_usefulness = sum(context_utilizations) / len(context_utilizations) if context_utilizations else 0.0

        stale_count = sum(1 for age in memory_ages if age > 30.0)
        staleness_rate = (stale_count / len(memory_ages)) if memory_ages else 0.0

        p50 = self._percentile(latencies, 0.50)
        p95 = self._percentile(latencies, 0.95)
        p99 = self._percentile(latencies, 0.99)
        ce_avg = sum(cross_encoder_latencies) / len(cross_encoder_latencies) if cross_encoder_latencies else 0.0

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "write_success_rate": round(write_rate, 4),
            "policy_denial_rate": round(denial_rate, 4),
            "contradiction_rate": round(contradiction_rate, 4),
            "deduplication_hit_rate": round(deduplication_rate, 4),
            "deduplication_hits": deduplication_hits,
            "retrieval_relevance_avg": round(avg_relevance, 4),
            "context_usefulness_avg": round(avg_usefulness, 4),
            "staleness_rate": round(staleness_rate, 4),
            "total_writes": write_attempts,
            "total_policy_evaluations": policy_evaluations,
            "llm_compaction_tokens_saved": tokens_saved,
            "cross_encoder_reranking_latency_ms": round(ce_avg, 2),
            "predictive_context_hits": predictive_context_hits,
            "latencies_ms": {
                "p50": p50,
                "p95": p95,
                "p99": p99
            }
        }

    def get_prometheus_format(self) -> str:
        s = self.get_metrics_summary()
        lines = [
            "# HELP memora_write_success_rate Ratio of successful writes to total attempts",
            "# TYPE memora_write_success_rate gauge",
            f"memora_write_success_rate {s['write_success_rate']}",
            "# HELP memora_policy_denial_rate Ratio of denied access evaluations",
            "# TYPE memora_policy_denial_rate gauge",
            f"memora_policy_denial_rate {s['policy_denial_rate']}",
            "# HELP memora_contradiction_rate Fraction of write attempts marked contradictory by a caller",
            "# TYPE memora_contradiction_rate gauge",
            f"memora_contradiction_rate {s['contradiction_rate']}",
            "# HELP memora_deduplication_hit_rate Fraction of write attempts flagged for duplicate content or idempotency",
            "# TYPE memora_deduplication_hit_rate gauge",
            f"memora_deduplication_hit_rate {s['deduplication_hit_rate']}",
            "# HELP memora_deduplication_hits Total write attempts flagged for duplicate content or idempotency",
            "# TYPE memora_deduplication_hits counter",
            f"memora_deduplication_hits {s['deduplication_hits']}",
            "# HELP memora_retrieval_relevance_avg Average relevance score of retrieved memories",
            "# TYPE memora_retrieval_relevance_avg gauge",
            f"memora_retrieval_relevance_avg {s['retrieval_relevance_avg']}",
            "# HELP memora_context_usefulness_avg Average token budget utilization",
            "# TYPE memora_context_usefulness_avg gauge",
            f"memora_context_usefulness_avg {s['context_usefulness_avg']}",
            "# HELP memora_staleness_rate Percentage of retrieved memories > 30 days old",
            "# TYPE memora_staleness_rate gauge",
            f"memora_staleness_rate {s['staleness_rate']}",
            "# HELP memora_llm_compaction_tokens_saved Total tokens saved via LLM summarization compaction",
            "# TYPE memora_llm_compaction_tokens_saved counter",
            f"memora_llm_compaction_tokens_saved {s['llm_compaction_tokens_saved']}",
            "# HELP memora_cross_encoder_reranking_latency_ms Average latency of neural cross-encoder in milliseconds",
            "# TYPE memora_cross_encoder_reranking_latency_ms gauge",
            f"memora_cross_encoder_reranking_latency_ms {s['cross_encoder_reranking_latency_ms']}",
            "# HELP memora_predictive_context_hits Number of times experience memories were predictively injected",
            "# TYPE memora_predictive_context_hits counter",
            f"memora_predictive_context_hits {s['predictive_context_hits']}",
            "# HELP memora_latency_ms API latency in milliseconds",
            "# TYPE memora_latency_ms summary",
            f'memora_latency_ms{{quantile="0.5"}} {s["latencies_ms"]["p50"]}',
            f'memora_latency_ms{{quantile="0.95"}} {s["latencies_ms"]["p95"]}',
            f'memora_latency_ms{{quantile="0.99"}} {s["latencies_ms"]["p99"]}',
        ]
        return "\n".join(lines) + "\n"

metrics_collector = MetricsCollector()