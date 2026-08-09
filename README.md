# tiny-circuit-breaker

> **Zero-dependency Circuit Breaker.** Three-state FSM: CLOSED → OPEN → HALF-OPEN. Thread-safe. Pure Python stdlib. One file. MIT.

```
pip install tiny-circuit-breaker
```
or just copy `tiny_circuit_breaker.py` into your project.

---

## What Is a Circuit Breaker?

The **circuit breaker** pattern prevents cascading failures. When a service starts failing, the circuit "opens" and rejects calls immediately — instead of waiting for timeouts and piling up requests.

```
CLOSED (normal)          OPEN (failing)           HALF-OPEN (testing)
─────────────────       ─────────────────       ──────────────────
Calls pass through       Calls rejected           One call through
Failure count ↑          No calls allowed         ↓
  threshold →            recovery_timeout         Success threshold →
  OPEN                 ←─────────────           CLOSED or OPEN
```

---

## Features

- ✅ **Three-state FSM** — CLOSED → OPEN → HALF-OPEN → CLOSED
- ✅ **Configurable thresholds** — `failure_threshold`, `success_threshold`, `recovery_timeout`
- ✅ **Excluded exceptions** — mark types that should NOT trip the circuit (e.g. `ValueError`)
- ✅ **Context manager** — `with cb: risky_call()` records success/failure automatically
- ✅ **Explicit call** — `cb.call(func, *args)` returns result or raises `CircuitOpen`
- ✅ **Statistics** — track total calls, failures, rejections, failure rate
- ✅ **Manual reset** — `cb.reset()` to manually close the circuit
- ✅ **Thread-safe** — `threading.RLock` guards all state transitions
- ✅ **Zero dependencies** — stdlib only

---

## Quick Start

```python
from tiny_circuit_breaker import CircuitBreaker, CircuitOpen

cb = CircuitBreaker(
    failure_threshold=5,   # Open after 5 consecutive failures
    recovery_timeout=30.0,  # Wait 30s before testing recovery
    success_threshold=2,   # Need 2 successes to close
)

# Context manager style
for i in range(100):
    with cb:
        result = risky_api_call()   # Auto-records success/failure
        print(f"Succeeded: {result}")
    # Circuit is open — CircuitOpen raised on exit from `with`

# Explicit call style
try:
    result = cb.call(risky_api_call)
except CircuitOpen:
    print("Circuit is open — service unavailable")
```

---

## Handling Excluded Exceptions

```python
# Don't trip on ValueError (client bug, not service bug)
cb = CircuitBreaker(failure_threshold=3, excluded=(ValueError,))

for _ in range(10):
    with cb:
        validate_input()  # raises ValueError on bad input
        call_service()
# Circuit stays CLOSED — ValueError excluded
```

---

## Stats & Monitoring

```python
cb = CircuitBreaker(failure_threshold=3)

for _ in range(10):
    with cb:
        call_api()

stats = cb.stats
print(f"Total: {stats.total_calls}, "
      f"Failed: {stats.failed_calls}, "
      f"Rejected: {stats.rejected_calls}, "
      f"Failure rate: {stats.failure_rate:.1%}")
```

---

## When to Use It

| Use case | Example |
|----------|---------|
| External API calls | Stripe, Twilio, payment gateway |
| Database connections | PostgreSQL replica that might go down |
| File I/O | NFS mounts that might stall |
| LLM API calls | OpenAI, Anthropic — token budgets, rate limits |
| Any unreliable service | Microservices, third-party webhooks |

---

## Why Tiny?

Because Hystrix is 3MB of JAR. Because `circuitbreaker` has dependencies.
`tiny-circuit-breaker` is **1 file, 0 deps, ~200 LOC**, and you can read every line in 5 minutes.

---

## Install

```bash
pip install tiny-circuit-breaker
```

Or copy the single file:
```bash
curl -O https://raw.githubusercontent.com/hussain-alsaibai/tiny-circuit-breaker/main/tiny_circuit_breaker.py
```

---

## License

MIT © 2026 Hussain Al-Saibai
