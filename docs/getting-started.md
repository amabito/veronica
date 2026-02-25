# Getting Started

This document is for backend engineers who have used veronica-core and want to understand what veronica adds.

---

## What You Get

- **SimplePlanner** — a rule-based planner that reads execution history and adjusts cost ceilings across runs. You call it before each context to get a `PolicyConfig`; you feed it the snapshot after.
- **PolicyConfig** — the typed contract between the Planner and veronica-core's executor. The Planner produces it; veronica-core enforces it. You do not construct it directly.
- **Feedback loop** — after each run, `planner.update(snapshot)` ingests the graph snapshot and adjusts internal state. The next `create_config()` call reflects that adjustment. Tighter ceilings after halts, looser ceilings after clean runs.

---

## Install

Not yet on PyPI. Install from source:

```bash
git clone https://github.com/amabito/veronica.git
cd veronica
pip install -e .
```

veronica-core must also be installed:

```bash
pip install veronica-core
```

---

## Five-Minute Example

```python
from veronica_core import ExecutionContext
from veronica.planner import SimplePlanner

planner = SimplePlanner(base_ceiling_usd=1.00)

# --- Run 1 ---
config = planner.create_config(estimated_steps=10, priority=50)

with ExecutionContext(config=config) as ctx:
    # your agent steps here
    ctx.wrap_llm_call(fn=lambda: call_llm("step 1"))
    ctx.wrap_llm_call(fn=lambda: call_llm("step 2"))

    snapshot = ctx.get_graph_snapshot()

planner.update(snapshot)
# Clean run: ceiling rises to $1.05 (Rule 2)

# --- Run 2 ---
config = planner.create_config(estimated_steps=10, priority=50)

with ExecutionContext(config=config) as ctx:
    # This run hits the ceiling and halts
    ctx.wrap_llm_call(fn=lambda: expensive_llm_call())

    snapshot = ctx.get_graph_snapshot()

planner.update(snapshot)
# Halt detected: ceiling drops to $0.945 (Rule 1)

# --- Run 3 ---
# create_config now returns a tighter ceiling ($0.945)
config = planner.create_config(estimated_steps=10, priority=50)
print(config.ceiling_usd)  # 0.945
```

The Planner adjusts without you touching any ceiling value directly. You only call `create_config` and `update`.

---

## When to Use the Planner

**"I want consistent cost limits across runs without tuning them manually."**
Use `SimplePlanner` with the defaults. The feedback loop handles gradual adjustment.

**"My agent can recursively call itself."**
SimplePlanner has a depth guard (Rule 3): if `aggregates.max_depth >= 8`, it forces `on_exceed="halt"` regardless of your default. You get automatic protection against uncontrolled recursion without writing any extra logic.

**"I need org-level budget caps shared across multiple services."**
That is Phase 3 (multi-context coordination). Not available yet. Track progress in [veronica-core roadmap](https://github.com/amabito/veronica-core#roadmap).

---

## What veronica Does Not Do

- **No model selection.** `create_config` does not pick which LLM to call. The Planner decides limits, not routing.
- **No prompt construction.** PolicyConfig contains no LLM-facing content. What you send to the model is entirely outside veronica's scope.
- **No routing decisions.** Which tool or chain to invoke is your agent's concern, not the Planner's.
- **Not a replacement for veronica-core.** veronica adds a coordination and governance layer on top of veronica-core. Every veronica deployment requires veronica-core at the enforcement boundary.

---

## Next Steps

- [Planner reference](planner.md) — ceiling adjustment rules, SimplePlanner API, phase roadmap
- [PolicyConfig specification](policy-config.md) — all fields, escalation options, audit metadata
