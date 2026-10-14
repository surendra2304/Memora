"""Resilience primitives: circuit breaking and self-healing integrity repair."""
from core.resilience.circuit_breaker import (
    CircuitBreaker,
    AsyncCircuitBreaker,
    CircuitOpenError,
    CircuitRegistry,
    CircuitState,
    circuit_registry,
    retry_with_backoff_async,
)
from core.resilience.http_client import get_async_client, close_async_client
from core.resilience.self_healing import (
    CheckResult,
    HealingReport,
    SelfHealingSupervisor,
    self_healing_supervisor,
)

__all__ = [
    "CircuitBreaker",
    "AsyncCircuitBreaker",
    "CircuitOpenError",
    "CircuitRegistry",
    "CircuitState",
    "circuit_registry",
    "retry_with_backoff_async",
    "get_async_client",
    "close_async_client",
    "CheckResult",
    "HealingReport",
    "SelfHealingSupervisor",
    "self_healing_supervisor",
]
