"""
tiny-circuit-breaker — Zero-dependency Circuit Breaker.

Three-state FSM: CLOSED → OPEN → HALF-OPEN → CLOSED.
Pure Python stdlib. MIT License.

The circuit breaker pattern prevents cascading failures by short-circuiting
calls to a failing service. When failures exceed a threshold, the circuit
"opens" and rejects calls immediately. After a timeout, it lets one call
through ("half-open") to test recovery.

Usage:
    cb = CircuitBreaker(failure_threshold=5, recovery_timeout=30)
    result = cb.call(risky_function, arg1, arg2)
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Callable, Type, Union
from contextlib import contextmanager

import sys


# ----------------------------------------------------------------------
# States
# ----------------------------------------------------------------------

class State(Enum):
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class CircuitBreakerError(Exception):
    """Raised when a call is rejected due to an open circuit."""
    pass


class CircuitOpen(CircuitBreakerError):
    """The circuit is open; call was rejected."""
    pass


class CircuitError(CircuitBreakerError):
    """Wraps an underlying failure that tripped the circuit."""
    pass


# ----------------------------------------------------------------------
# Stats
# ----------------------------------------------------------------------

@dataclass
class CircuitStats:
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    rejected_calls: int = 0
    opened_at: float | None = None

    @property
    def failure_rate(self) -> float:
        denom = self.successful_calls + self.failed_calls
        return self.failed_calls / denom if denom else 0.0


# ----------------------------------------------------------------------
# CircuitBreaker
# ----------------------------------------------------------------------

class CircuitBreaker:
    """
    A thread-safe circuit breaker.

    Parameters
    ----------
    failure_threshold : int
        Number of consecutive failures before opening the circuit.
    success_threshold : int
        Number of consecutive successes in HALF_OPEN before closing.
    recovery_timeout : float
        Seconds to wait before transitioning OPEN -> HALF_OPEN.
    excluded : type or tuple of types
        Exception types that should NOT trip the circuit.
    name : str
        Human-readable name for this circuit.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        recovery_timeout: float = 60.0,
        excluded: Type[Exception] | tuple = (),
        name: str | None = None,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.recovery_timeout = recovery_timeout
        self.excluded_exceptions: tuple = (excluded,) if isinstance(excluded, type) else excluded
        self.name = name or "default"

        self._state = State.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: float | None = None
        self._lock = threading.RLock()
        self._stats = CircuitStats()

    # ---- Properties ----

    @property
    def state(self) -> State:
        with self._lock:
            self._check_transition()
            return self._state

    @property
    def stats(self) -> CircuitStats:
        with self._lock:
            return CircuitStats(
                total_calls=self._stats.total_calls,
                successful_calls=self._stats.successful_calls,
                failed_calls=self._stats.failed_calls,
                rejected_calls=self._stats.rejected_calls,
                opened_at=self._opened_at,
            )

    def is_closed(self) -> bool:
        return self.state == State.CLOSED

    def is_open(self) -> bool:
        return self.state == State.OPEN

    def is_half_open(self) -> bool:
        return self.state == State.HALF_OPEN

    def available(self) -> bool:
        """True if the circuit allows a call through."""
        return self.state != State.OPEN

    def reset(self) -> None:
        """Manually reset the circuit to CLOSED."""
        with self._lock:
            self._state = State.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._opened_at = None
            self._stats = CircuitStats()

    # ---- State transitions ----

    def _check_transition(self) -> None:
        """Check if OPEN -> HALF_OPEN transition should fire."""
        if self._state == State.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.recovery_timeout:
                self._state = State.HALF_OPEN
                self._success_count = 0

    def _trip(self) -> None:
        self._state = State.OPEN
        self._opened_at = time.monotonic()

    def _record_success(self) -> None:
        with self._lock:
            self._stats.successful_calls += 1
            if self._state == State.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = State.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
                    self._opened_at = None

    def _record_failure(self) -> None:
        with self._lock:
            self._stats.failed_calls += 1
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._trip()

    def _record_rejected(self) -> None:
        with self._lock:
            self._stats.rejected_calls += 1

    # ---- Context manager ----

    def __enter__(self) -> "CircuitBreaker":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        """Exit the context manager. Returns True to suppress the exception."""
        if exc_type is None:
            # No exception — success path
            self._record_success()
        elif isinstance(exc_val, self.excluded_exceptions):
            # Excluded exception — don't trip, don't suppress
            pass
        else:
            # Failure path
            self._record_failure()
        # Never suppress the original exception
        return False

    # ---- Direct call ----

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute `func` through the circuit breaker.

        Raises CircuitOpen if the circuit is open.
        Raises CircuitError wrapping the original exception on failure.
        """
        with self._lock:
            self._stats.total_calls += 1

        if not self.available():
            self._record_rejected()
            raise CircuitOpen(
                f"Circuit {self.name!r} is OPEN. Call rejected."
            )

        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
        except self.excluded_exceptions:
            raise
        except Exception as exc:
            self._record_failure()
            raise CircuitError(f"Circuit {self.name!r} caught: {exc}") from exc

    def run(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """Alias for call()."""
        return self.call(func, *args, **kwargs)

    # ---- Guard context manager ----

    @contextmanager
    def guard(self):
        """
        Context manager that records success/failure on exit.

        Usage:
            with cb.guard():
                risky_call()
        """
        try:
            yield self
        except BaseException as e:
            if isinstance(e, self.excluded_exceptions):
                raise
            self._record_failure()
            raise

    # ---- Repr ----

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self.name!r}, state={self.state.name}, "
            f"failures={self._failure_count}/{self.failure_threshold}, "
            f"recovery_timeout={self.recovery_timeout}s)"
        )
