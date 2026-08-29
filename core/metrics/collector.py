"""
Observability Metrics Engine for Memora
Tracks Retrieval relevance, Context usefulness, Staleness rate, Contradiction rate,
Write success rate, Policy denial rate, and Latencies.
"""
import time
from typing import Dict, Any, List
from collections import deque
from datetime import datetime, timezone

class MetricsCollector:
    def __init__(self, max_history: int = 1000):
        self.max_history = max_history

        # Counters & Accumulators
        self.write_attempts = 0
        self.write_successes = 0
        self.policy_evaluations = 0
        self.policy_denials = 0
        self.contradictions_detected = 0

        # Rolling sample deques
        self.relevance_scores: deque = deque(maxlen=max_history)
        self.context_token_utilizations: deque = deque(maxlen=max_history)
        self.memory_ages_days: deque = deque(maxlen=max_history)
        self.latencies_ms: deque = deque(maxlen=max_history)

    def record_write(self, success: bool = True, is_contradiction: bool = False, latency_ms: float = 0.0):
        self.write_attempts += 1
        if success:
            self.write_successes += 1
        if is_contradiction:
            self.contradictions_detected += 1
        if latency_ms > 0:
            self.latencies_ms.append(latency_ms)

    def record_policy_check(self, allowed: bool):
        self.policy_evaluations += 1
        if not allowed:
            self.policy_denials += 1

    def record_retrieval(self, relevance_scores: List[float], ages_days: List[float], latency_ms: float = 0.0):
        for s in relevance_scores:
            self.relevance_scores.append(s)
        for a in ages_days:
            self.memory_ages_days.append(a)
        if latency_ms > 0:
            self.latencies_ms.append(latency_ms)

    def record_context_generation(self, tokens_used: int, token_budget: int, latency_ms: float = 0.0):
        ratio = (tokens_used / max(1, token_budget)) if token_budget > 0 else 0.0
        self.context_token_utilizations.append(min(1.0, ratio))
        if latency_ms > 0:
            self.latencies_ms.append(latency_ms)

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
        # Calculations
        write_rate = (self.write_successes / self.write_attempts) if self.write_attempts > 0 else 1.0
        denial_rate = (self.policy_denials / self.policy_evaluations) if self.policy_evaluations > 0 else 0.0
        contradiction_rate = (self.contradictions_detected / max(1, self.write_attempts)) if self.write_attempts > 0 else 0.0

        avg_relevance = sum(self.relevance_scores) / len(self.relevance_scores) if self.relevance_scores else 0.0
        avg_usefulness = sum(self.context_token_utilizations) / len(self.context_token_utilizations) if self.context_token_utilizations else 0.0

        stale_count = sum(1 for a in self.memory_ages_days if a > 30.0)
        staleness_rate = (stale_count / len(self.memory_ages_days)) if self.memory_ages_days else 0.0

        lat_list = list(self.latencies_ms)
        p50 = self._percentile(lat_list, 0.50)
        p95 = self._percentile(lat_list, 0.95)
        p99 = self._percentile(lat_list, 0.99)

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "write_success_rate": round(write_rate, 4),
            "policy_denial_rate": round(denial_rate, 4),
            "contradiction_rate": round(contradiction_rate, 4),
            "retrieval_relevance_avg": round(avg_relevance, 4),
            "context_usefulness_avg": round(avg_usefulness, 4),
            "staleness_rate": round(staleness_rate, 4),
            "total_writes": self.write_attempts,
            "total_policy_evaluations": self.policy_evaluations,
            "latencies_ms": {
                "p50": p50,
                "p95": p95,
                "p99": p99
            }
        }

    def get_prometheus_format(self) -> str:
        s = self.get_metrics_summary()
        lines = [
            f"# HELP memora_write_success_rate Ratio of successful writes to total attempts",
            f"# TYPE memora_write_success_rate gauge",
            f"memora_write_success_rate {s['write_success_rate']}",
            f"# HELP memora_policy_denial_rate Ratio of denied access evaluations",
            f"# TYPE memora_policy_denial_rate gauge",
            f"memora_policy_denial_rate {s['policy_denial_rate']}",
            f"# HELP memora_contradiction_rate Rate of writes causing supersession",
            f"# TYPE memora_contradiction_rate gauge",
            f"memora_contradiction_rate {s['contradiction_rate']}",
            f"# HELP memora_retrieval_relevance_avg Average relevance score of retrieved memories",
            f"# TYPE memora_retrieval_relevance_avg gauge",
            f"memora_retrieval_relevance_avg {s['retrieval_relevance_avg']}",
            f"# HELP memora_context_usefulness_avg Average token budget utilization",
            f"# TYPE memora_context_usefulness_avg gauge",
            f"memora_context_usefulness_avg {s['context_usefulness_avg']}",
            f"# HELP memora_staleness_rate Percentage of retrieved memories > 30 days old",
            f"# TYPE memora_staleness_rate gauge",
            f"memora_staleness_rate {s['staleness_rate']}",
            f"# HELP memora_latency_ms API latency in milliseconds",
            f"# TYPE memora_latency_ms summary",
            f'memora_latency_ms{{quantile="0.5"}} {s["latencies_ms"]["p50"]}',
            f'memora_latency_ms{{quantile="0.95"}} {s["latencies_ms"]["p95"]}',
            f'memora_latency_ms{{quantile="0.99"}} {s["latencies_ms"]["p99"]}',
        ]
        return "\n".join(lines) + "\n"

metrics_collector = MetricsCollector()