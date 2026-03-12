# Planner

## What the Planner Does

The Planner decides what limits to apply to an upcoming execution context.
It reads the current state of the agent's execution graph, applies rules, and produces a `PolicyConfig` that veronica-core will enforce.

The Planner is not an orchestrator. It does not decide what the agent does -- routing, model selection, and prompt construction are outside its scope. It decides how much budget to allow, how long to wait, and what to do when those limits are hit.

---

## Scope Boundary

The Planner owns one decision: **what constraints to set**.

| In scope | Out of scope |
|---|---|
| Cost ceiling (USD) | Which model to call |
| Step limit | What prompt to send |
| Timeout | Routing decisions |
| Escalation policy (halt vs degrade) | Retry strategies inside the agent |
| Fallback model on degrade | Tool selection |

Crossing this boundary turns the Planner into an orchestrator. That is a different product.

---

## Feedback Loop

```
veronica-core --[graph_snapshot]--> Planner --[PolicyConfig]--> veronica-core
```

After each execution context, the caller retrieves a `graph_snapshot()` from the context and passes it to `planner.update()`. The Planner uses the snapshot to adjust its internal state -- spending rate, failure rate, depth patterns -- and applies those adjustments the next time `create_config()` is called.

The kernel never modifies its behavior based on snapshot data mid-execution. Adaptation always flows through a new `PolicyConfig` issued by the Planner before the next context starts.

---

## Phase Roadmap

### Phase 1: SimplePlanner (current)

Rule-based. Local only. No persistent storage, no cloud dependency, no external coordination.

The `SimplePlanner` reads from `graph_snapshot` and maintains a lightweight in-memory model of recent execution history. It adjusts ceilings within configured bounds when it sees anomalies: cost spikes, depth increases, rising halt rates.

### Phase 2: Adaptive Planner

Budget-aware, history-based. Uses spend history to predict cost before issuing a config, and tightens or loosens ceilings progressively. Adds deadline-aware allocation across concurrent chains.

### Phase 3: Multi-context Coordination

Cross-service circuit state, shared budget pools across agents, org-level policy overrides from VERONICA's control plane.

---

## SimplePlanner -- API

```python
from veronica_core import ExecutionContext
from veronica_core.containment import ExecutionConfig
from veronica.planner import SimplePlanner

planner = SimplePlanner()
config = planner.create_config(estimated_steps=10, priority=50)

with ExecutionContext(config=config) as ctx:
    for step in steps:
        ctx.wrap_llm_call(fn=step)
    snapshot = ctx.graph_snapshot()

planner.update(snapshot)
```

### `SimplePlanner()`

No required arguments. Accepts optional keyword arguments to override defaults:

```python
planner = SimplePlanner(
    base_ceiling_usd=1.00,       # Starting ceiling for new contexts
    max_ceiling_usd=10.00,       # Hard upper bound; create_config never exceeds this
    min_ceiling_usd=0.10,        # Hard lower bound
    default_timeout_ms=30_000,   # Wall-clock timeout applied to all contexts
    default_on_exceed="halt",    # "halt" or "degrade"
    fallback_model=None,         # Used when on_exceed="degrade"
)
```

### `create_config(estimated_steps, priority) -> PolicyConfig`

Produces a `PolicyConfig` for the next execution context.

| Parameter | Type | Description |
|---|---|---|
| `estimated_steps` | `int` | Caller's estimate of how many steps the chain will take. Used to set `ceiling_steps`. |
| `priority` | `int` | 0–100. Higher-priority contexts receive a more generous ceiling when the Planner is tightening. |

The Planner reads its current internal state (adjusted ceiling, timeout, escalation policy) and writes those values into the returned `PolicyConfig`. The caller does not set the ceiling directly -- that is the Planner's decision.

### `update(snapshot: dict)`

Ingests a `graph_snapshot()` dict from a completed `ExecutionContext`. Updates internal state:

- Tracks rolling spend rate (USD per step) across recent contexts.
- Adjusts the effective ceiling up or down based on halt rate.
- Records maximum depth to detect runaway recursion patterns.

`update` does not issue a new `PolicyConfig`. It only modifies internal state that `create_config` will read on the next call.

---

## Ceiling Adjustment Rules (Phase 1)

`SimplePlanner` applies three rules after each `update()`:

**Rule 1 -- Halt tightening.** If the most recent context halted on a cost ceiling, reduce the effective ceiling by 10%, down to `min_ceiling_usd`.

**Rule 2 -- Clean run loosening.** If the most recent context completed without any halt or degrade event, increase the effective ceiling by 5%, up to `max_ceiling_usd`.

**Rule 3 -- Depth guard.** If `aggregates.max_depth >= 8`, set `on_exceed="halt"` regardless of the configured default. Deep recursion is treated as a signal of an uncontrolled loop.

These rules are applied in order. Rule 3 takes precedence over the `default_on_exceed` setting but does not override a `priority >= 90` context.

---

## What the Planner Does Not Do

- It does not read the agent's prompt or task description.
- It does not pick models.
- It does not modify an `ExecutionContext` while it is running.
- It does not coordinate with other Planner instances (Phase 3 handles that).
- It does not persist state across process restarts in Phase 1.

---

## Example: Tightening After a Cost Spike

```python
planner = SimplePlanner(base_ceiling_usd=2.00)

# First run: completes normally, cost $0.40
config = planner.create_config(estimated_steps=5, priority=50)
with ExecutionContext(config=config) as ctx:
    ...
    snapshot = ctx.graph_snapshot()
planner.update(snapshot)
# -> Rule 2: clean run, ceiling rises to $2.10

# Second run: halts at $2.10 ceiling
config = planner.create_config(estimated_steps=5, priority=50)
with ExecutionContext(config=config) as ctx:
    ...
    snapshot = ctx.graph_snapshot()
planner.update(snapshot)
# -> Rule 1: halt detected, ceiling drops to $1.89

# Third run uses ceiling $1.89
config = planner.create_config(estimated_steps=5, priority=50)
```

---

## Status

Phase 1 implementation pending `PlannerProtocol` definition in veronica-core (planned v1.0).

Tracking: [veronica-core roadmap](https://github.com/amabito/veronica-core#roadmap)
