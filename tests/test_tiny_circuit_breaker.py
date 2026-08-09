import time
import unittest
from tiny_circuit_breaker import (
    CircuitBreaker, CircuitOpen, CircuitError, State,
)


class TestCircuitBreakerStates(unittest.TestCase):

    def test_initial_state_closed(self):
        cb = CircuitBreaker()
        self.assertTrue(cb.is_closed())

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(3):
            try:
                with cb:
                    raise ValueError("fail")
            except ValueError:
                pass
        self.assertTrue(cb.is_open())

    def test_half_open_after_timeout(self):
        cb = CircuitBreaker(failure_threshold=2, recovery_timeout=0.01)
        for _ in range(2):
            try:
                with cb:
                    raise ValueError("fail")
            except ValueError:
                pass
        self.assertTrue(cb.is_open())
        time.sleep(0.02)
        self.assertTrue(cb.is_half_open())

    def test_closes_after_success_threshold(self):
        cb = CircuitBreaker(failure_threshold=1, success_threshold=2, recovery_timeout=0.01)
        try:
            with cb:
                raise ValueError("trip")
        except ValueError:
            pass
        self.assertTrue(cb.is_open())
        time.sleep(0.02)
        self.assertTrue(cb.is_half_open())
        # Now in half-open, two successes close it
        with cb:
            pass
        with cb:
            pass
        self.assertTrue(cb.is_closed())

    def test_rejects_when_open(self):
        cb = CircuitBreaker(failure_threshold=1)
        try:
            with cb:
                raise ValueError("trip")
        except ValueError:
            pass
        self.assertTrue(cb.is_open())
        with self.assertRaises(CircuitOpen):
            cb.call(lambda: "ok")

    def test_excluded_exceptions(self):
        cb = CircuitBreaker(failure_threshold=1, excluded=ValueError)
        for _ in range(5):
            try:
                with cb:
                    raise ValueError("excluded")
            except ValueError:
                pass
        self.assertTrue(cb.is_closed())  # Not tripped


class TestCircuitBreakerStats(unittest.TestCase):

    def test_stats_updated(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(2):
            try:
                with cb:
                    raise ValueError("fail")
            except ValueError:
                pass
        with cb:
            pass
        stats = cb.stats
        self.assertEqual(stats.failed_calls, 2)
        self.assertEqual(stats.successful_calls, 1)

    def test_rejected_calls(self):
        cb = CircuitBreaker(failure_threshold=1)
        try:
            with cb:
                raise ValueError("trip")
        except ValueError:
            pass
        try:
            cb.call(lambda: "ok")
        except CircuitOpen:
            pass
        self.assertEqual(cb.stats.rejected_calls, 1)

    def test_reset(self):
        cb = CircuitBreaker(failure_threshold=1)
        try:
            with cb:
                raise ValueError("fail")
        except ValueError:
            pass
        self.assertTrue(cb.is_open())
        cb.reset()
        self.assertTrue(cb.is_closed())


class TestCircuitBreakerCall(unittest.TestCase):

    def test_call_success(self):
        cb = CircuitBreaker(failure_threshold=3)
        result = cb.call(lambda x: x * 2, 5)
        self.assertEqual(result, 10)

    def test_call_failure_wraps(self):
        cb = CircuitBreaker(failure_threshold=1)
        with self.assertRaises(CircuitError) as ctx:
            cb.call(lambda: 1 / 0)
        self.assertIsInstance(ctx.exception.__cause__, ZeroDivisionError)

    def test_available(self):
        cb = CircuitBreaker(failure_threshold=3)
        self.assertTrue(cb.available())
        for _ in range(3):
            try:
                with cb:
                    raise ValueError("fail")
            except ValueError:
                pass
        self.assertFalse(cb.available())


if __name__ == "__main__":
    unittest.main()
