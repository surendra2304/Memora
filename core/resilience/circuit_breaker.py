"""
Circuit breaker and dependency health tracking for Memora.

Memora talks to optional external systems (Qdrant for vectors, Turso for the
durable event feed, Redis for caching). Before this module existed there was no
resilience layer at all: every call retried a dead dependency at full speed,
paying the connection timeout on every request, and the /health endpoint could
not report that a dependency had gone away.

The breaker has the three standard states:

    CLOSED    dependency is presumed healthy; calls pass through
    OPEN      too many recent failures; calls short-circuit without touching the
              dependency, so a dead backend cannot add latency to every request
    HALF_OPEN the cool-off has elapsed; a single probe call is allowed through to
              decide whether to close again or reopen

It is deliberately synchronous and thread-safe rather than async, because the
call sites (SQLAlchemy sessions, the Qdrant client) are synchronous.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """Raised when a call is refused because the circuit is open."""

    def __init__(self, name: str, retry_after: float):
        self.name = name
        self.retry_after = retry_after
        super().__init__(
            f"circuit '{name}' is open; retry in {retry_after:.1f}s"
        )


@dataclass
class CircuitStats:
    """Observable counters. Exposed through /health so degradation is visible."""

    name: str
    state: CircuitState
    successes: int = 0
    failures: int = 0
    rejections: int = 0
    consecutive_failures: int = 0
    last_failure_at: Optional[float] = None
    last_success_at: Optional[float] = None
    opened_at: Optional[float] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "state": self.state.value,
                "successes": self.successes,
                "failures": self.failures,
                "rejections": self.rejections,
                "consecutive_failures": self.consecutive_failures,
                "last_failure_at": self.last_failure_at,
                "last_success_at": self.last_success_at,
                "opened_at": self.opened_at,
            }


class CircuitBreaker:
    """
    Wraps calls to one external dependency.

    failure_threshold  consecutive failures before the circuit opens
    recovery_timeout   seconds to stay open before allowing a probe
    expected_exceptions  exception types that count as a dependency failure.
                       Anything else propagates without affecting the breaker,
                       so a bug in the caller is not mistaken for an outage.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        expected_exceptions: tuple = (Exception,),
        clock: Callable[[], float] = time.monotonic,
    ):
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_timeout = max(0.0, recovery_timeout)
        self.expected_exceptions = expected_exceptions
        self._clock = clock

        self._lock = threading.RLock()
        self._state = CircuitState.CLOSED
        self._successes = 0
        self._failures = 0
        self._rejections = 0
        self._consecutive_failures = 0
        self._last_failure_at: Optional[float] = None
        self._last_success_at: Optional[float] = None
        self._opened_at: Optional[float] = None

    # ------------------------------------------------------------------ state
    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._resolve_state_locked()

    def _resolve_state_locked(self) -> CircuitState:
        """Transition OPEN -> HALF_OPEN once the cool-off has elapsed."""
        if self._state is CircuitState.OPEN and self._opened_at is not None:
            if self._clock() - self._opened_at >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("circuit '%s' half-open; allowing a probe", self.name)
        return self._state

    def stats(self) -> CircuitStats:
        snap = self.snapshot()
        stats = CircuitStats(name=self.name, state=CircuitState(snap["state"]))
        stats.successes = snap["successes"]
        stats.failures = snap["failures"]
        stats.rejections = snap["rejections"]
        stats.consecutive_failures = snap["consecutive_failures"]
        stats.last_failure_at = snap["last_failure_at"]
        stats.last_success_at = snap["last_success_at"]
        stats.opened_at = snap["opened_at"]
        return stats

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            state = self._resolve_state_locked()
            retry_after = 0.0
            if state is CircuitState.OPEN and self._opened_at is not None:
                retry_after = max(
                    0.0, self.recovery_timeout - (self._clock() - self._opened_at)
                )
            return {
                "name": self.name,
                "state": state.value,
                "successes": self._successes,
                "failures": self._failures,
                "rejections": self._rejections,
                "consecutive_failures": self._consecutive_failures,
                "last_failure_at": self._last_failure_at,
                "last_success_at": self._last_success_at,
                "opened_at": self._opened_at,
                "retry_after_seconds": round(retry_after, 3),
            }

    # ------------------------------------------------------------------- call
    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Run func through the breaker, or raise CircuitOpenError."""
        with self._lock:
            state = self._resolve_state_locked()
            if state is CircuitState.OPEN:
                self._rejections += 1
                retry_after = 0.0
                if self._opened_at is not None:
                    retry_after = max(
                        0.0, self.recovery_timeout - (self._clock() - self._opened_at)
                    )
                raise CircuitOpenError(self.name, retry_after)
            # Only one probe at a time in HALF_OPEN; extra callers are refused
            # rather than stampeding a dependency that may still be down.
            probing = state is CircuitState.HALF_OPEN

        try:
            result = func(*args, **kwargs)
        except self.expected_exceptions:
            self._record_failure(probing)
            raise
        except Exception:
            # Not a dependency failure (e.g. a programming error). Record nothing
            # so a caller bug cannot trip the breaker and mask a healthy backend.
            raise
        else:
            self._record_success()
            return result

    def _record_success(self) -> None:
        with self._lock:
            self._successes += 1
            self._consecutive_failures = 0
            self._last_success_at = self._clock()
            if self._state is not CircuitState.CLOSED:
                logger.info("circuit '%s' closed after a successful probe", self.name)
            self._state = CircuitState.CLOSED
            self._opened_at = None

    def _record_failure(self, probing: bool) -> None:
        with self._lock:
            self._failures += 1
            self._consecutive_failures += 1
            self._last_failure_at = self._clock()

            if probing:
                # A failed probe sends us straight back to OPEN.
                self._open_locked()
            elif self._consecutive_failures >= self.failure_threshold:
                self._open_locked()

    def _open_locked(self) -> None:
        if self._state is not CircuitState.OPEN:
            logger.warning(
                "circuit '%s' opened after %d consecutive failures",
                self.name,
                self._consecutive_failures,
            )
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()

    # ----------------------------------------------------------------- manual
    def reset(self) -> None:
        """Force back to CLOSED. Used by the self-healing supervisor and tests."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self._opened_at = None


class CircuitRegistry:
    """Named breakers, so every dependency shares one observable registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._breakers: Dict[str, CircuitBreaker] = {}

    def get(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        expected_exceptions: tuple = (Exception,),
    ) -> CircuitBreaker:
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(
                    name=name,
                    failure_threshold=failure_threshold,
                    recovery_timeout=recovery_timeout,
                    expected_exceptions=expected_exceptions,
                )
            return self._breakers[name]

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            breakers = list(self._breakers.values())
        return {b.name: b.snapshot() for b in breakers}

    def find(self, name: str) -> Optional[CircuitBreaker]:
        """Look a breaker up WITHOUT creating it.

        get() is the right call for a dependency that intends to make calls, but
        an operator asking about a breaker by name must get a 404 for one that
        does not exist rather than have the lookup silently register it.
        """
        with self._lock:
            return self._breakers.get(name)

    def names(self) -> List[str]:
        with self._lock:
            return sorted(self._breakers)

    def healthy(self) -> bool:
        """True when no registered dependency is currently open."""
        return all(
            b.state is not CircuitState.OPEN for b in list(self._breakers.values())
        )

    def reset_all(self) -> None:
        with self._lock:
            breakers = list(self._breakers.values())
        for b in breakers:
            b.reset()


#: Process-wide registry imported by the adapters and the health endpoint.
circuit_registry = CircuitRegistry()
