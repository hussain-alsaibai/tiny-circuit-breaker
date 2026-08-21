"""
tiny-circuit-breaker — Zero-dependency Circuit Breaker for LLM API calls.

Three-state FSM: CLOSED -> OPEN -> HALF_OPEN -> CLOSED.
Pure Python stdlib (time, threading, enum, contextlib). MIT License.

When an LLM provider goes flaky inside an agent loop, the circuit
"opens" and rejects calls immediately instead of stacking timeouts
and burning tokens. After a cooldown, a few probe calls test
recovery; if they succeed, the circuit closes again.

    from tiny_circuit_breaker import CircuitBreaker, CircuitOpen

    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30.0)
    with cb.record():
        call_llm(prompt)
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from enum import Enum
from typing import Any, Callable, Dict

__all__ = ["CircuitState", "CircuitBreaker", "CircuitBreakerError", "CircuitOpen"]
__version__ = "0.1.0"


class CircuitBreakerError(Exception):
    """Base error raised by the circuit breaker."""


class CircuitOpen(CircuitBreakerError):
    """Raised when a call is rejected because the circuit is OPEN."""


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe circuit breaker. See README.md for details."""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
        success_threshold: int = 2,
        *,
        excluded=None,
        name: str = "default",
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if success_threshold < 1:
            raise ValueError("success_threshold must be >= 1")
        if half_open_max_calls < 1:
            raise ValueError("half_open_max_calls must be >= 1")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be > 0")

        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.success_threshold = success_threshold
        self.name = name

        if excluded is None:
            self.excluded_exceptions = ()
        elif isinstance(excluded, type):
            self.excluded_exceptions = (excluded,)
        else:
            self.excluded_exceptions = tuple(excluded)

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._opened_at = None
        self._last_failure_time = None
        self._total_calls = 0
        self._total_failures = 0
        self._total_successes = 0
        self._total_rejections = 0

    # -- state accessors --

    @property
    def state(self) -> CircuitState:
        """Current state, applying any time-based transitions."""
        with self._lock:
            self._maybe_half_open()
            return self._state

    def can_execute(self) -> bool:
        """True if a call may proceed. False only when OPEN and within recovery timeout."""
        with self._lock:
            self._maybe_half_open()
            return self._state != CircuitState.OPEN

    # -- manual recording --

    def success(self) -> None:
        """Record a success. Decrements failure counter; in HALF_OPEN may close circuit."""
        with self._lock:
            self._maybe_half_open()
            self._total_successes += 1
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._close()
            elif self._failure_count > 0:
                self._failure_count -= 1

    def failure(self) -> None:
        """Record a failure. May open the circuit (or re-open from HALF_OPEN)."""
        with self._lock:
            self._maybe_half_open()
            self._total_failures += 1
            self._failure_count += 1
            self._success_count = 0
            self._last_failure_time = time.monotonic()
            if self._state == CircuitState.HALF_OPEN:
                self._open()  # any probe failure trips it back
            elif (
                self._state == CircuitState.CLOSED
                and self._failure_count >= self.failure_threshold
            ):
                self._open()

    # -- context manager --

    @contextmanager
    def record(self):
        """``with cb.record(): call_llm(prompt)``. Raises CircuitOpen
        if OPEN; records success/failure on exit."""
        with self._lock:
            self._maybe_half_open()
            if self._state == CircuitState.OPEN:
                self._total_rejections += 1
                raise CircuitOpen(f"circuit {self.name!r} is OPEN — call rejected")
            self._total_calls += 1
            if self._state == CircuitState.HALF_OPEN:
                self._half_open_calls += 1

        try:
            yield None
        except self.excluded_exceptions:
            raise
        except BaseException:
            self.failure()
            raise
        self.success()

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Execute ``func`` through the breaker."""
        with self.record():
            return func(*args, **kwargs)

    # -- stats / reset --

    def stats(self) -> Dict[str, Any]:
        """Snapshot of breaker statistics."""
        with self._lock:
            self._maybe_half_open()
            denom = self._total_successes + self._total_failures
            failure_rate = self._total_failures / denom if denom > 0 else 0.0
            last_fail_wall = None
            if self._last_failure_time is not None:
                last_fail_wall = time.time() - (
                    time.monotonic() - self._last_failure_time
                )
            return {
                "state": self._state,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "last_failure_time": last_fail_wall,
                "total_calls": self._total_calls,
                "total_failures": self._total_failures,
                "total_successes": self._total_successes,
                "total_rejections": self._total_rejections,
                "failure_rate": failure_rate,
            }

    def reset(self) -> None:
        """Reset to CLOSED with zero consecutive-failure counters."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._half_open_calls = 0
            self._opened_at = None
            self._last_failure_time = None

    # -- internal helpers --

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = time.monotonic()
        self._success_count = 0
        self._half_open_calls = 0

    def _close(self) -> None:
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._half_open_calls = 0
        self._opened_at = None

    def _maybe_half_open(self) -> None:
        if (
            self._state == CircuitState.OPEN
            and self._opened_at is not None
            and time.monotonic() - self._opened_at >= self.recovery_timeout
        ):
            self._state = CircuitState.HALF_OPEN
            self._success_count = 0
            self._half_open_calls = 0

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"CircuitBreaker(name={self.name!r}, "
            f"state={self.state.value}, "
            f"failures={self._failure_count}/{self.failure_threshold})"
        )
