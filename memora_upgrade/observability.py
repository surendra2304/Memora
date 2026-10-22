from __future__ import annotations
from dataclasses import dataclass
from collections import defaultdict
from contextlib import contextmanager
from typing import Iterator
import time


@dataclass(frozen=True, slots=True)
class RetrievalMetric:
    tenant_id: str
    query_id: str
    candidates: int
    results: int
    latency_ms: float
    cache_hit: bool


class Metrics:
    def __init__(self):
        self._counts = defaultdict(int)
        self._latencies: list[float] = []

    @contextmanager
    def timed_retrieval(self, tenant_id: str, cache_hit: bool = False) -> Iterator[dict]:
        started = time.perf_counter()
        state = {"tenant_id": tenant_id, "cache_hit": cache_hit, "candidates": 0, "results": 0}
        try:
            yield state
        finally:
            self._latencies.append((time.perf_counter() - started) * 1000)
            self._counts[("retrieval", tenant_id, "cache_hit" if cache_hit else "miss")] += 1

    def count(self, name: str, tenant_id: str) -> None:
        self._counts[(name, tenant_id)] += 1

    def snapshot(self) -> dict:
        return {
            "counts": {str(k): v for k, v in self._counts.items()},
            "latency_p50_ms": self._percentile(self._latencies, 0.50),
            "latency_p95_ms": self._percentile(self._latencies, 0.95),
        }

    @staticmethod
    def _percentile(values: list[float], p: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * p))))
        return round(ordered[idx], 3)
