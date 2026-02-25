# Integration Guide

How to connect veronica with veronica-core in real systems.

---

## Overview

veronica sits on the **management path**, not the critical path.

```
                    Management Path
                  +-----------------+
                  |                 |
Application  -->  veronica (Planner) --> PolicyConfig
                  |                 |
                  +-----------------+
                          |
                          | PolicyConfig (before each context)
                          v
Application  -->  veronica-core (ExecutionContext)  -->  LLM Providers
                        Critical Path
```

veronica-core's latency guarantees hold regardless of veronica's state.
If the Planner is slow or unavailable, you can fall back to a hardcoded `PolicyConfig` without modifying the enforcement boundary.

---

## Pattern 1: Per-request Planning

**When to use:** Each incoming request is independent. You want adaptive ceilings without coordinating state across requests.

One `SimplePlanner` instance per service. Feed it each request's snapshot after completion.

```python
from veronica_core import ExecutionContext
from veronica_core.exceptions import ShieldBlockedError
from veronica.planner import SimplePlanner

# Created once at service startup
planner = SimplePlanner(base_ceiling_usd=0.50)


def handle_request(user_query: str) -> str:
    config = planner.create_config(estimated_steps=5, priority=50)

    try:
        with ExecutionContext(config=config) as ctx:
            result = ctx.wrap_llm_call(fn=lambda: call_llm(user_query))
            snapshot = ctx.get_graph_snapshot()
    except ShieldBlockedError:
        snapshot = None
        return "Request could not be completed within cost limits."

    if snapshot is not None:
        planner.update(snapshot)

    return result
```

The Planner adjusts its internal state across requests. If a run halts (ceiling hit), the next request gets a tighter ceiling. If runs complete cleanly, ceilings gradually expand.

---

## Pattern 2: Long-running Agent

**When to use:** An agent session spans many steps and many `ExecutionContext` calls. You want the ceiling to adapt based on what the agent has spent so far in this session.

One `SimplePlanner` per agent session. Create it when the session starts, call `update()` after each context.

```python
from veronica_core import ExecutionContext
from veronica_core.exceptions import ShieldBlockedError
from veronica.planner import SimplePlanner


class AgentSession:
    def __init__(self) -> None:
        self.planner = SimplePlanner(
            base_ceiling_usd=2.00,
            default_timeout_ms=60_000,
        )

    def run_step(self, step_fn: callable) -> dict | None:
        config = self.planner.create_config(estimated_steps=1, priority=50)

        try:
            with ExecutionContext(config=config) as ctx:
                output = ctx.wrap_llm_call(fn=step_fn)
                snapshot = ctx.get_graph_snapshot()
        except ShieldBlockedError:
            # Ceiling hit: Planner will tighten on next create_config
            # Session continues; caller decides whether to proceed
            self.planner.update(self._empty_halt_snapshot())
            return None

        self.planner.update(snapshot)
        return output

    def _empty_halt_snapshot(self) -> dict:
        # Minimal snapshot signaling a halt; exact schema depends on veronica-core version
        return {"halted": True, "aggregates": {"max_depth": 0, "total_cost_usd": 0.0}}
```

The Planner tracks depth patterns across the session. If the agent starts recursing deeply, Rule 3 activates and forces `on_exceed="halt"` automatically.

---

## Pattern 3: Batch Job

**When to use:** You are processing hundreds of identical chains (e.g., document summarization, data extraction). Each chain is structurally similar. Calling `update()` after every single chain is unnecessary overhead.

Create the config once, reuse the ceiling values, and call `update()` every N chains.

```python
from veronica_core import ExecutionContext
from veronica_core.exceptions import ShieldBlockedError
from veronica.planner import SimplePlanner

UPDATE_INTERVAL = 10  # Update Planner state every 10 chains


def process_batch(documents: list[str]) -> list[str | None]:
    planner = SimplePlanner(base_ceiling_usd=0.20)
    results: list[str | None] = []
    pending_snapshots: list[dict] = []

    for i, doc in enumerate(documents):
        config = planner.create_config(estimated_steps=3, priority=50)

        try:
            with ExecutionContext(config=config) as ctx:
                result = ctx.wrap_llm_call(fn=lambda: summarize(doc))
                snapshot = ctx.get_graph_snapshot()
                pending_snapshots.append(snapshot)
        except ShieldBlockedError:
            results.append(None)
            continue

        results.append(result)

        # Feed accumulated snapshots to the Planner periodically
        if len(pending_snapshots) >= UPDATE_INTERVAL:
            for snap in pending_snapshots:
                planner.update(snap)
            pending_snapshots.clear()

    # Flush remaining snapshots
    for snap in pending_snapshots:
        planner.update(snap)

    return results
```

The ceiling adjusts over the batch run. If early chains are cheap, later chains get slightly looser ceilings. If early chains hit limits, the Planner tightens before the next group.

---

## PolicyConfig Lifecycle

Each `PolicyConfig` carries metadata for audit and validity checking.

| Field | Purpose |
|---|---|
| `chain_id` | Links the config to a specific execution chain. Set by the Planner. |
| `issued_at` | Unix timestamp when the config was produced. |
| `expires_at` | Config is invalid after this timestamp. `None` means no expiry. |
| `planner_version` | Planner version string, recorded in audit trail. |

If you store configs for deferred execution (e.g., queuing), check expiry before use:

```python
import time
from veronica.planner import SimplePlanner

planner = SimplePlanner()
config = planner.create_config(estimated_steps=5, priority=50)

# Later, before using the config:
if config.expires_at is not None and time.time() > config.expires_at:
    # Config has expired; request a fresh one
    config = planner.create_config(estimated_steps=5, priority=50)
```

---

## What Happens When the Ceiling Is Hit

When any ceiling is breached and `on_exceed="halt"`, veronica-core raises `ShieldBlockedError`.
The `ExecutionContext` context manager propagates the exception out of the `with` block.

```python
from veronica_core.exceptions import ShieldBlockedError

config = planner.create_config(estimated_steps=10, priority=50)

try:
    with ExecutionContext(config=config) as ctx:
        ctx.wrap_llm_call(fn=expensive_step)
        ctx.wrap_llm_call(fn=another_step)  # May not be reached
        snapshot = ctx.get_graph_snapshot()
except ShieldBlockedError as e:
    # The context was halted before completion.
    # Any work completed up to the halt is preserved.
    # snapshot is not available here; handle the partial result.
    log_halt(reason=str(e))
    return fallback_response()
```

If `on_exceed="degrade"`, veronica-core switches to `fallback_model` instead of halting.
`ShieldBlockedError` is not raised. Execution continues with the fallback model.

```python
from veronica.planner import SimplePlanner

planner = SimplePlanner(
    default_on_exceed="degrade",
    fallback_model="gpt-4o-mini",
)
```

---

## Limitations (Phase 1)

These are honest limitations of the current implementation. They will be addressed in later phases.

- **No persistence across process restarts.** `SimplePlanner` holds state in memory only. If the process exits, the Planner resets to `base_ceiling_usd`. You lose any learned adjustments.
- **No multi-process coordination.** If you run multiple replicas of a service, each replica has its own Planner instance with independent state. There is no shared ceiling or shared halt detection across replicas.
- **No cross-agent coordination.** Each Planner instance is scoped to one agent or service. Budget pressure in one agent does not affect ceilings in another. Phase 3 addresses this.
- **No cost prediction.** The Planner adjusts ceilings reactively based on past runs. It does not estimate cost before a call is made. Predictive allocation is Phase 2.
- **No deadline-aware allocation.** `PolicyConfig` carries a `deadline_ts` field, but `SimplePlanner` does not act on it in Phase 1. It is recorded in the audit trail only.
