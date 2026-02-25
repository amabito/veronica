# PolicyConfig Specification

## Status

Draft. Implementation pending `PlannerProtocol` definition in veronica-core (planned v1.0).

---

## Purpose

`PolicyConfig` is the contract between the Planner (VERONICA) and the Executor (veronica-core).

The Planner produces a `PolicyConfig`. The Executor enforces it.
The Planner has no visibility into Executor internals.
The Executor has no visibility into how the Planner arrived at the config.

---

## Design Principles

**High-level, not structural.**
PolicyConfig expresses *what* limits apply. The Executor decides *how* to enforce them.
PolicyConfig does not map 1:1 to veronica-core's internal `ShieldConfig`.

**Immutable after issue.**
Once submitted to the Executor, a PolicyConfig is not modified mid-execution.
The Planner issues a new PolicyConfig for the next execution context.

**Auditable.**
Every PolicyConfig carries metadata identifying its origin and validity window.

---

## Fields

### Budget

| Field | Type | Description |
|---|---|---|
| `ceiling_usd` | `float` | Maximum cost in USD for this execution context |
| `ceiling_tokens_out` | `int \| None` | Maximum output token count (None = unlimited) |
| `ceiling_steps` | `int \| None` | Maximum agent steps (None = unlimited) |

At least one budget field must be set.

### Escalation

| Field | Type | Default | Description |
|---|---|---|---|
| `on_exceed` | `"halt" \| "degrade"` | `"halt"` | Action when any ceiling is breached |
| `fallback_model` | `str \| None` | `None` | Model to use when `on_exceed = "degrade"` |

### Time

| Field | Type | Default | Description |
|---|---|---|---|
| `timeout_ms` | `int \| None` | `None` | Wall-clock timeout for the execution context |
| `rate_window_seconds` | `float \| None` | `None` | Rolling window for rate-based ceiling |
| `rate_ceiling_calls` | `int \| None` | `None` | Max calls within `rate_window_seconds` |

`rate_window_seconds` and `rate_ceiling_calls` must be set together or not at all.

### Arbitration

These fields are consumed by the Planner during multi-agent allocation.
The Executor records them in the audit trail but does not act on them.

| Field | Type | Default | Description |
|---|---|---|---|
| `priority` | `int` | `50` | 0 (lowest) to 100 (highest). Used by Planner for contention resolution |
| `deadline_ts` | `float \| None` | `None` | Unix timestamp. Planner uses this for deadline-aware allocation |

### Metadata

| Field | Type | Description |
|---|---|---|
| `chain_id` | `str` | Links this policy to a specific execution chain |
| `issued_at` | `float` | Unix timestamp when this config was produced |
| `expires_at` | `float \| None` | Config is invalid after this timestamp (None = no expiry) |
| `planner_version` | `str \| None` | Planner version string, for audit trail |

---

## Minimal Example

```python
PolicyConfig(
    chain_id="chain-abc123",
    ceiling_usd=1.00,
    on_exceed="halt",
    issued_at=1740000000.0,
)
```

## Full Example

```python
PolicyConfig(
    chain_id="chain-abc123",
    ceiling_usd=2.50,
    ceiling_tokens_out=50_000,
    ceiling_steps=30,
    on_exceed="degrade",
    fallback_model="gpt-4o-mini",
    timeout_ms=30_000,
    rate_window_seconds=60.0,
    rate_ceiling_calls=10,
    priority=80,
    deadline_ts=1740003600.0,
    issued_at=1740000000.0,
    expires_at=1740007200.0,
    planner_version="0.1.0",
)
```

---

## What PolicyConfig Is Not

- Not a replacement for `ShieldConfig`. The Executor translates PolicyConfig into its
  internal representation. That translation is the Executor's responsibility.
- Not adaptive. PolicyConfig is a snapshot. Adaptation happens in the Planner between
  execution contexts, not inside the Executor during execution.
- Not a prompt or instruction. PolicyConfig contains no LLM-facing content.

---

## Feedback Loop

After execution, the Executor emits a `SafetyEvent` stream.
The Planner observes this stream and issues a new `PolicyConfig` for the next context.

```
Executor --[SafetyEvents]--> Planner --[PolicyConfig]--> Executor
```

The Executor never modifies its behavior based on SafetyEvents mid-execution.
Adaptation is always via a new PolicyConfig from the Planner.
