# tiny-circuit-breaker

[![Install from GitHub](https://img.shields.io/badge/install-git%2Bhttps-blue)](https://github.com/hussain-alsaibai/tiny-circuit-breaker#install)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://github.com/hussain-alsaibai/tiny-circuit-breaker#install)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Zero Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](#)
[![CI](https://github.com/hussain-alsaibai/tiny-circuit-breaker/actions/workflows/ci.yml/badge.svg)](https://github.com/hussain-alsaibai/tiny-circuit-breaker/actions)

## Circuit Breaker for LLM API Calls

A tiny, zero-dependency circuit breaker for protecting LLM API calls (and
any other unreliable dependency) inside an AI agent loop. Prevents
cascading failures when your provider goes down or rate-limits you.

```bash
# From GitHub (recommended -- not yet on PyPI)
pip install git+https://github.com/hussain-alsaibai/tiny-circuit-breaker.git
```

---

### Why circuit breakers matter for LLM agents

Agent loops call LLMs in tight cycles: think → tool → think → tool → ….
When the provider is degraded — a regional outage, a 429 storm, a slow
streaming API — every retry compounds the problem:

- **Token burn.** A flapping call costs you a full prompt before failing.
- **Wall-clock blow-up.** Each call waits for a 60s timeout before the next attempt.
- **Cascading failures.** The downstream rate-limiter sees a flood, blocks you longer.
- **Agent deadlock.** The loop stalls in retry hell and never reaches the user.

A circuit breaker **fails fast** once a provider has proven unreliable.
After `failure_threshold` consecutive failures the circuit OPENS — every
subsequent call is rejected immediately with `CircuitOpen`, no network
round-trip. After `recovery_timeout` seconds the breaker enters HALF_OPEN
and lets a few probe calls through. If they succeed, the circuit CLOSES
and traffic resumes normally.

This pattern keeps your agent responsive (it can swap providers, surface
a clean error to the user, or back off intelligently) instead of melting
through tokens while the upstream is on fire.

---

### Quick start

```python
from tiny_circuit_breaker import CircuitBreaker, CircuitOpen

cb = CircuitBreaker(
    failure_threshold=5,      # open after 5 consecutive failures
    recovery_timeout=30.0,    # wait 30s before probing recovery
    half_open_max_calls=3,    # allow up to 3 probes in HALF_OPEN
    success_threshold=2,      # need 2 successful probes to close
)

# Context-manager pattern — auto-records success/failure.
for step in agent_loop():
    with cb.record():
        response = call_llm(step)
        handle(response)
```

If the circuit is OPEN when `record()` is entered, `CircuitOpen` is raised
immediately — the body never runs.

Manual control:

```python
if not cb.can_execute():
    raise CircuitOpen("LLM provider looks down, backing off")

try:
    response = call_llm(step)
    cb.success()
except Exception:
    cb.failure()
    raise
```

---

### State machine

```
                   failure_threshold reached
        ┌──────────────────────────────────────────┐
        │                                          ▼
   ┌─────────┐   recovery_timeout elapsed     ┌───────────┐
   │ CLOSED  │ ─────────────────────────────► │ HALF_OPEN │
   └─────────┘                                └───────────┘
        ▲                                          │
        │ success_threshold successes             │ any failure
        └──────────────────────────────────────────┘
                                                   │
                              ┌────────────────────┘
                              ▼
                          ┌───────┐
                          │ OPEN  │   can_execute() → False
                          └───────┘
```

- **CLOSED** — normal traffic. Each failure increments `failure_count`.
  When it reaches `failure_threshold`, transition to OPEN.
- **OPEN** — calls are rejected immediately with `CircuitOpen`. After
  `recovery_timeout` seconds, transition to HALF_OPEN.
- **HALF_OPEN** — probe calls are allowed through (up to
  `half_open_max_calls`). `success_threshold` consecutive successes
  return the breaker to CLOSED. Any single failure trips it back to OPEN.

---

### API reference

#### `CircuitState`

```python
class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"
```

#### `CircuitBreaker(failure_threshold=5, recovery_timeout=30.0, half_open_max_calls=3, success_threshold=2, *, excluded=None, name="default")`

| Parameter            | Default | Description                                                |
|----------------------|---------|------------------------------------------------------------|
| `failure_threshold`  | `5`     | Consecutive failures to open the circuit                   |
| `recovery_timeout`   | `30.0`  | Seconds to wait in OPEN before allowing HALF_OPEN probes   |
| `half_open_max_calls`| `3`     | Max concurrent probe calls in HALF_OPEN                    |
| `success_threshold`  | `2`     | Consecutive successes in HALF_OPEN to close the circuit    |
| `excluded`           | `None`  | Exception type(s) that should NOT count as failures        |
| `name`               | `"default"` | Label used in error messages                          |

#### Methods

- **`state`** → `CircuitState` — current state (auto-transitions OPEN → HALF_OPEN when timeout elapses).
- **`can_execute()`** → `bool` — `False` only when OPEN and within recovery timeout.
- **`success()`** — record a success. Decrements failure counter; closes the circuit in HALF_OPEN.
- **`failure()`** — record a failure. Increments failure counter; opens the circuit at threshold; re-opens from HALF_OPEN.
- **`record()`** — context manager. Yields once; on success calls `success()`, on exception calls `failure()` (then re-raises). Raises `CircuitOpen` immediately if circuit is OPEN.
- **`call(fn, *args, **kwargs)`** — execute `fn` through the breaker (sugar over `record()`).
- **`stats()`** → `dict` with `state`, `failure_count`, `success_count`, `last_failure_time`, `total_calls`, `total_failures`, `total_successes`, `total_rejections`, `failure_rate`.
- **`reset()`** — return to CLOSED with zero counters (lifetime totals preserved).

#### Exceptions

- **`CircuitBreakerError`** — base class.
- **`CircuitOpen`** — raised when a call is rejected because the circuit is OPEN.

---

### Thread safety

`CircuitBreaker` uses an internal `threading.Lock` to guard every state
transition and counter mutation, so it's safe to call from multiple
threads concurrently. Useful when an agent fans out parallel LLM calls
and you want a single shared breaker.

---

### Why tiny?

- **One file.** ~190 LOC, no dependencies, copy-paste friendly.
- **Stdlib only.** `time`, `threading`, `enum`, `contextlib`.
- **Predictable.** No decorators, no async wrappers, no surprises.
- **Auditable.** Read every line in 5 minutes.

---

### Development

```bash
git clone https://github.com/hussain-alsaibai/tiny-circuit-breaker.git
cd tiny-circuit-breaker
pip install -e ".[dev]"
pytest -v
```

---

### License

MIT © 2026 — see [LICENSE](LICENSE).
