# VERONICA

**The Planner layer for the VERONICA stack.**

VERONICA decides. [veronica-core](https://github.com/amabito/veronica-core) enforces.

---

## What This Is

LLM execution systems need two distinct layers:

**Executor** (veronica-core): deterministic enforcement of cost, step, and retry limits.
Auditable. Dependency-light. Guarantees are unconditional.

**Planner** (this repository): decides *what limits to set* and *how to allocate resources*
across agents, time windows, and competing workloads.

```
VERONICA Planner
  - allocation: how much budget does each agent get?
  - prediction: will this call exceed the ceiling before making it?
  - arbitration: which agent wins when resources are contested?
       |
       | PolicyConfig
       v
veronica-core Executor
  - enforcement: halt / degrade / allow
  - audit trail: SafetyEvent stream
       |
       v
LLM calls
```

---

## Design Principle

The Planner can be AI-driven, rule-based, or human-operated.
The Executor's guarantees are unchanged regardless of Planner strategy.

This separation is intentional:

- veronica-core must be deterministic and auditable. Probabilistic components do not belong there.
- VERONICA can be as adaptive as needed, because it only *proposes* policy — it never *enforces* it.

---

## Status

Early stage. Implementation begins when `PlannerProtocol` is defined in veronica-core (planned v1.0).

Tracking: [veronica-core roadmap](https://github.com/amabito/veronica-core#roadmap)

---

## Docs

- [PolicyConfig specification](docs/policy-config.md) — the Planner/Executor contract

---

## Planned Capabilities

- **Budget allocation** across competing agents (shared pool management)
- **Cost prediction** from prompt characteristics before LLM calls are made
- **Arbitration** under resource contention (priority, fairness, deadline-aware)
- **Policy composition** — combine rule-based and learned policies

---

## Relationship to veronica-core

| | veronica-core | veronica |
|---|---|---|
| Role | Executor | Planner |
| Behavior | Deterministic | Adaptive |
| Dependencies | stdlib + optional extras | ML, statistical models (planned) |
| Stability | Stable (v1.0 target) | Experimental |
| Interface | Enforces `PolicyConfig` | Produces `PolicyConfig` |

---

## License

MIT
