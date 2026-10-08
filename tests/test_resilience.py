"""
Tests for the circuit breaker and the self-healing supervisor.

Before core/resilience existed there was no protection around the optional
external dependencies (Qdrant, Turso, Redis): every request paid the full
connection timeout against a dead backend, and /health could not report that a
dependency had gone away.
"""
import threading

import pytest

from core.resilience.circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitRegistry,
    CircuitState,
)


class FakeClock:
    """Deterministic time, so recovery windows can be tested without sleeping."""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _isolate_vector_store_and_circuits():
    """vector_adapter and circuit_registry are process-wide singletons.

    Without this, a vector written by one test is visible to the next, which
    made these tests pass alone and fail in the full suite.
    """
    from core.resilience.circuit_breaker import circuit_registry
    from storage.vector.qdrant_adapter import vector_adapter

    vector_adapter._mock_store.clear()
    circuit_registry.reset_all()
    yield
    vector_adapter._mock_store.clear()
    circuit_registry.reset_all()


def _boom():
    raise ConnectionError("backend down")


def _ok():
    return "ok"


def test_calls_pass_through_while_closed():
    breaker = CircuitBreaker("dep", failure_threshold=3, clock=FakeClock())
    assert breaker.call(_ok) == "ok"
    assert breaker.state is CircuitState.CLOSED
    assert breaker.snapshot()["successes"] == 1


def test_opens_after_the_threshold_of_consecutive_failures():
    clock = FakeClock()
    breaker = CircuitBreaker("dep", failure_threshold=3, recovery_timeout=30, clock=clock)

    for _ in range(2):
        with pytest.raises(ConnectionError):
            breaker.call(_boom)
    assert breaker.state is CircuitState.CLOSED, "must not open before the threshold"

    with pytest.raises(ConnectionError):
        breaker.call(_boom)
    assert breaker.state is CircuitState.OPEN


def test_an_open_circuit_refuses_calls_without_touching_the_backend():
    """The whole point: a dead dependency must not add latency to every request."""
    clock = FakeClock()
    breaker = CircuitBreaker("dep", failure_threshold=1, recovery_timeout=30, clock=clock)

    with pytest.raises(ConnectionError):
        breaker.call(_boom)
    assert breaker.state is CircuitState.OPEN

    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return "ok"

    for _ in range(5):
        with pytest.raises(CircuitOpenError) as exc_info:
            breaker.call(counted)

    assert calls["n"] == 0, "the backend was reached while the circuit was open"
    assert exc_info.value.retry_after > 0
    assert breaker.snapshot()["rejections"] == 5


def test_a_successful_probe_closes_the_circuit():
    clock = FakeClock()
    breaker = CircuitBreaker("dep", failure_threshold=1, recovery_timeout=30, clock=clock)

    with pytest.raises(ConnectionError):
        breaker.call(_boom)
    assert breaker.state is CircuitState.OPEN

    clock.advance(31)
    assert breaker.state is CircuitState.HALF_OPEN
    assert breaker.call(_ok) == "ok"
    assert breaker.state is CircuitState.CLOSED


def test_a_failed_probe_reopens_the_circuit():
    clock = FakeClock()
    breaker = CircuitBreaker("dep", failure_threshold=1, recovery_timeout=30, clock=clock)

    with pytest.raises(ConnectionError):
        breaker.call(_boom)
    clock.advance(31)
    assert breaker.state is CircuitState.HALF_OPEN

    with pytest.raises(ConnectionError):
        breaker.call(_boom)
    assert breaker.state is CircuitState.OPEN
    # And the cool-off restarts, so an immediate call is refused again.
    with pytest.raises(CircuitOpenError):
        breaker.call(_ok)


def test_only_one_half_open_probe_is_admitted_at_a_time():
    """Concurrent recovery traffic must not stampede a still-down dependency."""
    clock = FakeClock()
    breaker = CircuitBreaker("dep", failure_threshold=1, recovery_timeout=5, clock=clock)
    with pytest.raises(ConnectionError):
        breaker.call(_boom)
    clock.advance(6)

    entered = threading.Event()
    release = threading.Event()
    second_calls = []
    probe_results = []

    def blocked_probe():
        entered.set()
        assert release.wait(2), "test did not release the probe"
        return "recovered"

    def run_probe():
        probe_results.append(breaker.call(blocked_probe))

    thread = threading.Thread(target=run_probe, daemon=True)
    thread.start()
    assert entered.wait(2), "first half-open probe did not start"

    try:
        with pytest.raises(CircuitOpenError) as exc_info:
            breaker.call(lambda: second_calls.append("called"))
        assert exc_info.value.reason == "probe_in_flight"
    finally:
        release.set()
        thread.join(2)

    assert not thread.is_alive()
    assert second_calls == []
    assert probe_results == ["recovered"]
    assert breaker.state is CircuitState.CLOSED
    assert breaker.snapshot()["rejections"] == 1


def test_unexpected_probe_exception_releases_the_single_probe_slot():
    clock = FakeClock()
    breaker = CircuitBreaker(
        "dep", failure_threshold=1, recovery_timeout=5,
        expected_exceptions=(ConnectionError,), clock=clock,
    )
    with pytest.raises(ConnectionError):
        breaker.call(_boom)
    clock.advance(6)

    def caller_bug():
        raise ValueError("synthetic caller error")

    with pytest.raises(ValueError):
        breaker.call(caller_bug)
    assert breaker.call(_ok) == "ok"
    assert breaker.state is CircuitState.CLOSED


def test_base_exception_during_probe_does_not_strand_the_reservation():
    clock = FakeClock()
    breaker = CircuitBreaker("dep", failure_threshold=1, recovery_timeout=5, clock=clock)
    with pytest.raises(ConnectionError):
        breaker.call(_boom)
    clock.advance(6)

    def cancelled_probe():
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        breaker.call(cancelled_probe)
    assert breaker.call(_ok) == "ok"
    assert breaker.state is CircuitState.CLOSED


def test_late_closed_call_cannot_close_a_new_half_open_probe():
    """An older in-flight request must not override a newer breaker transition."""
    clock = FakeClock()
    breaker = CircuitBreaker("dep", failure_threshold=1, recovery_timeout=5, clock=clock)
    old_started = threading.Event()
    finish_old = threading.Event()
    probe_started = threading.Event()
    finish_probe = threading.Event()

    def old_success():
        old_started.set()
        assert finish_old.wait(2)
        return "old success"

    old_result = []
    old_thread = threading.Thread(target=lambda: old_result.append(breaker.call(old_success)), daemon=True)
    old_thread.start()
    assert old_started.wait(2)

    with pytest.raises(ConnectionError):
        breaker.call(_boom)
    clock.advance(6)

    def recovery_probe():
        probe_started.set()
        assert finish_probe.wait(2)
        return "probe success"

    probe_result = []
    probe_thread = threading.Thread(
        target=lambda: probe_result.append(breaker.call(recovery_probe)), daemon=True
    )
    probe_thread.start()
    assert probe_started.wait(2)

    finish_old.set()
    old_thread.join(2)
    assert not old_thread.is_alive()
    assert old_result == ["old success"]
    assert breaker.state is CircuitState.HALF_OPEN
    with pytest.raises(CircuitOpenError) as exc_info:
        breaker.call(_ok)
    assert exc_info.value.reason == "probe_in_flight"

    finish_probe.set()
    probe_thread.join(2)
    assert not probe_thread.is_alive()
    assert probe_result == ["probe success"]
    assert breaker.state is CircuitState.CLOSED


def test_success_resets_the_consecutive_failure_count():
    clock = FakeClock()
    breaker = CircuitBreaker("dep", failure_threshold=3, clock=clock)

    for _ in range(2):
        with pytest.raises(ConnectionError):
            breaker.call(_boom)
    breaker.call(_ok)  # resets the streak

    for _ in range(2):
        with pytest.raises(ConnectionError):
            breaker.call(_boom)
    assert breaker.state is CircuitState.CLOSED, "intermittent errors must not open the circuit"


def test_unexpected_exceptions_do_not_trip_the_breaker():
    """A caller bug must not be mistaken for a dependency outage."""
    breaker = CircuitBreaker(
        "dep", failure_threshold=1, expected_exceptions=(ConnectionError,), clock=FakeClock()
    )

    def bad_caller():
        raise ValueError("programming error")

    with pytest.raises(ValueError):
        breaker.call(bad_caller)
    assert breaker.state is CircuitState.CLOSED
    assert breaker.snapshot()["failures"] == 0


def test_the_breaker_is_thread_safe():
    """Many threads failing at once must open it exactly once, not corrupt it."""
    breaker = CircuitBreaker("dep", failure_threshold=5, clock=FakeClock())
    barrier = threading.Barrier(16)

    def worker():
        barrier.wait()
        for _ in range(20):
            try:
                breaker.call(_boom)
            except (ConnectionError, CircuitOpenError):
                pass

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    snap = breaker.snapshot()
    assert snap["state"] == "open"
    assert snap["failures"] + snap["rejections"] == 16 * 20


def test_registry_returns_one_breaker_per_name():
    registry = CircuitRegistry()
    a = registry.get("qdrant")
    b = registry.get("qdrant")
    c = registry.get("turso")
    assert a is b
    assert a is not c
    assert set(registry.snapshot()) == {"qdrant", "turso"}


def test_registry_health_reflects_open_circuits():
    registry = CircuitRegistry()
    registry.get("qdrant", failure_threshold=1).call  # noqa: B018 - attribute access
    assert registry.healthy() is True

    breaker = registry.get("turso", failure_threshold=1)
    with pytest.raises(ConnectionError):
        breaker.call(_boom)
    assert registry.healthy() is False

    registry.reset_all()
    assert registry.healthy() is True


def test_reset_forces_the_circuit_closed():
    clock = FakeClock()
    breaker = CircuitBreaker("dep", failure_threshold=1, recovery_timeout=9999, clock=clock)
    with pytest.raises(ConnectionError):
        breaker.call(_boom)
    assert breaker.state is CircuitState.OPEN

    breaker.reset()
    assert breaker.state is CircuitState.CLOSED
    assert breaker.call(_ok) == "ok"


# ---------------------------------------------------------------------------
# The vector adapter must actually route through the breaker
# ---------------------------------------------------------------------------

def test_the_qdrant_adapter_uses_the_breaker():
    """Guard the wiring, not just the breaker in isolation."""
    from storage.vector.qdrant_adapter import QdrantVectorAdapter

    adapter = QdrantVectorAdapter(url="http://qdrant.invalid:6333")
    # readiness() keeps its exact key contract (/health returns it verbatim and
    # test_health_readiness_truth.py asserts it), so breaker state lives here.
    assert "state" in adapter.circuit_state(), "circuit_state() must report breaker state"

    # Simulate an initialised client whose every call fails.
    class DeadClient:
        def upsert(self, **kwargs):
            raise ConnectionError("qdrant down")

    adapter._client = DeadClient()
    adapter._initialized = True
    adapter._breaker.reset()

    results = [adapter.upsert_embedding(f"m{i}", [0.1, 0.2], tenant_id="default")
               for i in range(5)]

    snap = adapter._breaker.snapshot()
    assert snap["failures"] >= 3, f"failures were not recorded: {snap}"
    assert snap["state"] == "open", f"breaker did not open: {snap}"
    assert snap["rejections"] >= 1, "later calls were not short-circuited"
    assert results.count(False) == len(results)
