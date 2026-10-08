from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading

from core.identity.service import IdentityService
from core.memory.search_service import SearchService
from core.metrics.collector import MetricsCollector
from storage.relational.models import LifecycleState, MemoryRecord, MemoryType


def test_hybrid_search_records_relevance_and_age_metrics(test_db, monkeypatch):
    from core.memory import search_service

    agent = IdentityService.register_agent(test_db, "retrieval-metrics-agent")
    namespace = IdentityService.resolve_namespace(
        test_db, "memora://retrieval-metrics-agent/private"
    )
    record = MemoryRecord(
        namespace_id=namespace.id,
        owner_id=agent.id,
        memory_type=MemoryType.EPISODIC,
        content_text="synthetic telemetry calibration needle",
        confidence=0.9,
        importance=0.5,
        lifecycle_state=LifecycleState.ACTIVE,
        created_at=datetime.now(timezone.utc) - timedelta(days=45),
    )
    test_db.add(record)
    test_db.commit()

    collector = MetricsCollector()
    monkeypatch.setattr(search_service, "metrics_collector", collector)
    monkeypatch.setattr(
        search_service.EmbeddingGenerator,
        "generate_embedding",
        staticmethod(lambda _text: [0.0]),
    )
    monkeypatch.setattr(search_service.vector_adapter, "search_similarity", lambda **_kwargs: [])
    monkeypatch.setattr(search_service.GraphService, "get_graph_neighbors", lambda _db, _ids: {})

    results = SearchService.hybrid_search(test_db, "telemetry calibration needle")

    assert len(results) == 1
    summary = collector.get_metrics_summary()
    assert summary["retrieval_relevance_avg"] == round(results[0].final_score, 4)
    assert summary["staleness_rate"] == 1.0
    assert summary["latencies_ms"]["p50"] > 0


def test_write_pipeline_records_idempotency_hits_as_deduplication(test_db, monkeypatch):
    from core.memory.pipeline import write_service

    collector = MetricsCollector()
    monkeypatch.setattr(write_service, "metrics_collector", collector)
    arguments = {
        "db": test_db,
        "tenant_id": "default",
        "caller_name": "dedup-metrics-agent",
        "content_text": "Synthetic idempotency telemetry sample",
        "target_namespace_path": "memora://dedup-metrics-agent/private",
        "memory_type": MemoryType.EPISODIC,
        "idempotency_key": "synthetic-dedup-key",
    }

    first = write_service.MemoryWriteService.execute_pipeline(**arguments)
    retry = write_service.MemoryWriteService.execute_pipeline(**arguments)

    assert first.is_duplicate is False
    assert retry.is_duplicate is True
    summary = collector.get_metrics_summary()
    assert summary["total_writes"] == 2
    assert summary["deduplication_hits"] == 1
    assert summary["deduplication_hit_rate"] == 0.5


def test_deduplication_hits_are_counted_separately_from_contradictions():
    collector = MetricsCollector()
    collector.record_write(success=True, is_duplicate=True)
    collector.record_write(success=True, is_contradiction=True)

    summary = collector.get_metrics_summary()
    assert summary["deduplication_hits"] == 1
    assert summary["deduplication_hit_rate"] == 0.5
    assert summary["contradiction_rate"] == 0.5
    assert "memora_deduplication_hits 1" in collector.get_prometheus_format()


def test_metrics_collector_is_safe_for_concurrent_writes_and_snapshots():
    collector = MetricsCollector(max_history=16_000)
    workers = 8
    iterations = 1_000
    start = threading.Barrier(workers + 1)
    stop_reader = threading.Event()
    snapshot_count = []

    def writer():
        start.wait()
        for index in range(iterations):
            collector.record_write(success=True, latency_ms=1.0)
            collector.record_policy_check(allowed=index % 2 == 0)
            collector.record_retrieval([0.5], [45.0], latency_ms=2.0)

    def reader():
        start.wait()
        while not stop_reader.is_set():
            snapshot_count.append(collector.get_metrics_summary()["total_writes"])

    with ThreadPoolExecutor(max_workers=workers + 1) as pool:
        reader_future = pool.submit(reader)
        writers = [pool.submit(writer) for _ in range(workers)]
        for future in writers:
            future.result()
        stop_reader.set()
        reader_future.result()

    summary = collector.get_metrics_summary()
    expected = workers * iterations
    assert snapshot_count
    assert summary["total_writes"] == expected
    assert summary["write_success_rate"] == 1.0
    assert summary["total_policy_evaluations"] == expected
    assert summary["policy_denial_rate"] == 0.5
    assert summary["retrieval_relevance_avg"] == 0.5
    assert summary["staleness_rate"] == 1.0
