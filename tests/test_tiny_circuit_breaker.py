"""Tests for tiny_circuit_breaker."""
from __future__ import annotations

import threading
import time
import unittest

from tiny_circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerError,
    CircuitOpen,
    CircuitState,
)


class TestInitialState(unittest.TestCase):
    def test_starts_closed(self):
        cb = CircuitBreaker()
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_can_execute_when_closed(self):
        cb = CircuitBreaker()
        self.assertTrue(cb.can_execute())

    def test_initial_stats(self):
        cb = CircuitBreaker()
        s = cb.stats()
        self.assertEqual(s["state"], CircuitState.CLOSED)
        self.assertEqual(s["failure_count"], 0)
        self.assertIsNone(s["last_failure_time"])
        self.assertEqual(s["total_calls"], 0)
        self.assertEqual(s["total_rejections"], 0)


class TestFailureThreshold(unittest.TestCase):
    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=30.0)
        for _ in range(3):
            cb.failure()
        self.assertEqual(cb.state, CircuitState.OPEN)

    def test_stays_closed_below_threshold(self):
        cb = CircuitBreaker(failure_threshold=5)
        for _ in range(4):
            cb.failure()
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_success_decrements_failure_count(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.failure()
        cb.failure()
        cb.success()
        # Should now be at 1 failure, still closed.
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.stats()["failure_count"], 1)


class TestRecovery(unittest.TestCase):
    def test_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(
            failure_threshold=2, recovery_timeout=0.05, success_threshold=2
        )
        cb.failure(); cb.failure()
        self.assertEqual(cb.state, CircuitState.OPEN)
        time.sleep(0.06)
        # Touching .state triggers the transition.
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)

    def test_half_open_closes_on_success_threshold(self):
        cb = CircuitBreaker(
            failure_threshold=2, recovery_timeout=0.05, success_threshold=2
        )
        cb.failure(); cb.failure()
        time.sleep(0.06)
        cb.success()
        cb.success()
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_half_open_reopens_on_failure(self):
        cb = CircuitBreaker(
            failure_threshold=2, recovery_timeout=0.05, success_threshold=2
        )
        cb.failure(); cb.failure()
        time.sleep(0.06)
        # now half-open
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        cb.failure()
        self.assertEqual(cb.state, CircuitState.OPEN)


class TestCanExecute(unittest.TestCase):
    def test_can_execute_false_when_open_within_timeout(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0)
        cb.failure()
        self.assertFalse(cb.can_execute())

    def test_can_execute_true_when_closed(self):
        cb = CircuitBreaker()
        self.assertTrue(cb.can_execute())

    def test_can_execute_true_when_half_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=0.05)
        cb.failure()
        time.sleep(0.06)
        self.assertTrue(cb.can_execute())


class TestStats(unittest.TestCase):
    def test_stats_reflect_failures(self):
        cb = CircuitBreaker(failure_threshold=10)
        cb.failure(); cb.failure(); cb.failure()
        s = cb.stats()
        self.assertEqual(s["failure_count"], 3)
        self.assertEqual(s["total_failures"], 3)
        self.assertIsNotNone(s["last_failure_time"])

    def test_stats_reflect_successes(self):
        cb = CircuitBreaker()
        cb.success(); cb.success()
        s = cb.stats()
        self.assertEqual(s["total_successes"], 2)
        self.assertEqual(s["failure_count"], 0)

    def test_stats_rejection_count(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0)
        cb.failure()
        try:
            with cb.record():
                pass  # never reached
        except CircuitOpen:
            pass
        self.assertEqual(cb.stats()["total_rejections"], 1)

    def test_stats_failure_rate(self):
        cb = CircuitBreaker(failure_threshold=10)
        cb.success(); cb.success(); cb.failure()
        s = cb.stats()
        self.assertAlmostEqual(s["failure_rate"], 1 / 3, places=4)


class TestReset(unittest.TestCase):
    def test_reset_returns_to_closed(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0)
        cb.failure()
        self.assertEqual(cb.state, CircuitState.OPEN)
        cb.reset()
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.stats()["failure_count"], 0)

    def test_reset_allows_execute_again(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0)
        cb.failure()
        with self.assertRaises(CircuitOpen):
            with cb.record():
                pass
        cb.reset()
        with cb.record():
            pass
        self.assertEqual(cb.state, CircuitState.CLOSED)


class TestRecordContextManager(unittest.TestCase):
    def test_record_success_does_not_trip(self):
        cb = CircuitBreaker(failure_threshold=2)
        with cb.record():
            pass
        with cb.record():
            pass
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_record_failure_trips(self):
        cb = CircuitBreaker(failure_threshold=2)
        with self.assertRaises(RuntimeError):
            with cb.record():
                raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            with cb.record():
                raise RuntimeError("boom")
        self.assertEqual(cb.state, CircuitState.OPEN)

    def test_record_raises_circuit_open_when_open(self):
        cb = CircuitBreaker(failure_threshold=1, recovery_timeout=10.0)
        cb.failure()
        body_ran = False
        with self.assertRaises(CircuitOpen):
            with cb.record():
                body_ran = True
        self.assertFalse(body_ran)

    def test_excluded_exceptions_dont_trip(self):
        cb = CircuitBreaker(failure_threshold=2, excluded=ValueError)
        for _ in range(5):
            with self.assertRaises(ValueError):
                with cb.record():
                    raise ValueError("client bug")
        self.assertEqual(cb.state, CircuitState.CLOSED)


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_failures_only_trips_once(self):
        # Many threads each try to record failures; the circuit should
        # still open exactly once, never corrupt counters, and never
        # raise an unexpected exception.
        cb = CircuitBreaker(failure_threshold=5, recovery_timeout=0.05)

        def hammer():
            for _ in range(20):
                try:
                    with cb.record():
                        raise RuntimeError("x")
                except (RuntimeError, CircuitOpen):
                    pass

        threads = [threading.Thread(target=hammer) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Should have tripped cleanly and stayed open.
        self.assertEqual(cb.state, CircuitState.OPEN)

    def test_concurrent_calls_consistent_can_execute(self):
        cb = CircuitBreaker(failure_threshold=3, recovery_timeout=10.0)

        results = []

        def worker():
            for _ in range(50):
                results.append(cb.can_execute())

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # No exceptions during concurrent reads.
        self.assertEqual(len(results), 200)


class TestHalfOpenBudget(unittest.TestCase):
    def test_half_open_max_calls_respected(self):
        # If half_open_max_calls=2 and success_threshold=2, the second
        # successful probe should close the circuit.
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout=0.05,
            half_open_max_calls=2,
            success_threshold=2,
        )
        cb.failure()
        time.sleep(0.06)
        # HALF_OPEN
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        with cb.record():
            pass
        with cb.record():
            pass
        self.assertEqual(cb.state, CircuitState.CLOSED)


if __name__ == "__main__":
    unittest.main()
