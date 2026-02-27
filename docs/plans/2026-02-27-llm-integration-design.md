# Phase 6b: LLM Integration Adapter -- Design

**Goal:** Provide `vos.step()` context manager and `vos.run_step()` convenience wrapper so applications can execute LLM/tool calls through the full VERONICA OS pipeline in one line, with guaranteed `after_step` execution.

**Approach:** Context manager as core API (`step()`), thin sugar on top (`run_step()`). No LLM provider dependency. No protocol changes. No os.py pipeline changes.

**Scope:**
1. `StepContext` class -- wraps `ExecutionContext`, exposes `run()` / `run_llm()` / `run_tool()`
2. `VeronicaOS.step()` -- context manager, guarantees `after_step` in `finally`
3. `_make_fallback_snapshot()` -- defensive fallback when `get_snapshot()` fails
4. `VeronicaOS._normalize_intent()` -- fills empty StepIntent fields with safe defaults
5. `VeronicaOS.run_step()` -- sugar: `normalize + step + run` in one call
6. Tests (6+)

**Protocol changes:** None.
**os.py pipeline structure:** Unchanged. `before_step()` and `after_step()` are not modified.

---

## 1. StepContext

Defined in `os.py`, before `VeronicaOS`.

```python
from typing import Callable, TypeVar

T = TypeVar("T")


class StepContext:
    """Yielded by vos.step(). Wraps ExecutionContext."""

    def __init__(self, handle: StepHandle, exec_ctx: ExecutionContext) -> None:
        self.handle = handle
        self.exec_ctx = exec_ctx

    def run(self, fn: Callable[[], T]) -> T:
        """Execute fn, dispatching by intent.kind.

        kind="tool" -> wrap_tool_call, otherwise -> wrap_llm_call.
        kind="system" is treated as llm (no wrap_system_call in veronica-core).
        """
        if self.handle.intent.kind == "tool":
            return self.exec_ctx.wrap_tool_call(fn)
        return self.exec_ctx.wrap_llm_call(fn)

    def run_llm(self, fn: Callable[[], T]) -> T:
        return self.exec_ctx.wrap_llm_call(fn)

    def run_tool(self, fn: Callable[[], T]) -> T:
        return self.exec_ctx.wrap_tool_call(fn)

    @property
    def policy(self) -> PolicyConfig:
        return self.handle.policy
```

---

## 2. vos.step() Context Manager

```python
from contextlib import contextmanager
from typing import Iterator

# VeronicaOS method
@contextmanager
def step(self, intent: StepIntent) -> Iterator[StepContext]:
    handle = self.before_step(intent)
    ctx = ExecutionContext(config=handle.policy.to_exec_config())
    step_ctx = StepContext(handle=handle, exec_ctx=ctx)
    try:
        yield step_ctx
    finally:
        try:
            snapshot = ctx.get_snapshot()
        except Exception:
            logger.exception("[VERONICA_OS] snapshot retrieval failed; using fallback")
            snapshot = _make_fallback_snapshot(intent, "snapshot_retrieval_failed")
        self.after_step(handle, snapshot)
```

### Guarantees

- `after_step` **always** runs (exception or not).
- On `get_snapshot()` failure, a fallback `ContextSnapshot` is constructed. `after_step` still runs with it.
- Budget settle, store commit, event emission all execute even on application error.

---

## 3. _make_fallback_snapshot()

Module-level helper in `os.py`. Isolated so veronica-core schema changes break this function (and its test), not the caller.

```python
def _make_fallback_snapshot(intent: StepIntent, reason: str) -> ContextSnapshot:
    """Build a minimal ContextSnapshot when get_snapshot() fails.

    Defensive: even if SafetyEvent creation fails, the snapshot
    is still returned (with empty events). The snapshot itself
    must never raise.
    """
    events: list[Any] = []
    try:
        from veronica_core.shield.event import SafetyEvent
        events = [SafetyEvent(
            event_type="snapshot_failed",
            decision="HALT",
            reason=reason,
            hook="veronica_os",
            ts=time.time(),
            metadata={"step_id": intent.step_id},
        )]
    except Exception:
        pass  # events stays empty; snapshot still valid

    return ContextSnapshot(
        chain_id=intent.chain_id,
        request_id=intent.request_id,
        step_count=0,
        cost_usd_accumulated=0.0,
        retries_used=0,
        aborted=True,
        abort_reason=reason,
        elapsed_ms=0.0,
        nodes=[],
        events=events,
    )
```

### Design decisions

- `aborted=True`, `abort_reason=reason` -- marks the step as failed in veronica-core terms.
- `events` contains one SafetyEvent with `step_id` in metadata (ContextSnapshot has no step_id field).
- SafetyEvent creation is wrapped in try/except -- if veronica-core changes SafetyEvent's API, fallback still works (with empty events).
- `cost_usd_accumulated=0.0` -- conservative; avoids double-counting.

---

## 4. _normalize_intent()

```python
import uuid
from itertools import count

_step_counter = count(1)
_DEFAULT_TIMEOUT_MS = 30_000

# VeronicaOS method
def _normalize_intent(self, intent: StepIntent) -> StepIntent:
    """Fill missing StepIntent fields with safe defaults.

    Does not mutate the original (frozen dataclass). Returns a new
    instance only if any field was empty.
    """
    changes: dict[str, Any] = {}
    if not intent.request_id:
        changes["request_id"] = uuid.uuid4().hex
    if not intent.chain_id:
        changes["chain_id"] = "default"
    if not intent.step_id:
        changes["step_id"] = f"step-{next(_step_counter)}"
    if not intent.timeout_ms:
        changes["timeout_ms"] = _DEFAULT_TIMEOUT_MS
    if not intent.metadata:
        changes["metadata"] = {}

    if not changes:
        return intent

    from dataclasses import asdict
    fields = asdict(intent)
    fields.update(changes)
    return StepIntent(**fields)
```

### Default rules

| Field | Default | Rationale |
|-------|---------|-----------|
| `request_id` | `uuid4().hex` | Unique per call |
| `chain_id` | `"default"` | Single-chain apps don't need to think about it |
| `step_id` | `step-{N}` (monotonic) | Readable, no collision within process |
| `timeout_ms` | `30000` | Safe default for LLM calls |
| `metadata` | `{}` | Prevent None propagation |

### Scope

`_normalize_intent` is called by `run_step()` only. `step()` does not normalize -- advanced users manage their own intents.

---

## 5. run_step() Sugar

```python
# VeronicaOS method
def run_step(self, intent: StepIntent, fn: Callable[[], T]) -> T:
    """Execute one step with full OS pipeline. Convenience wrapper.

    Equivalent to::

        with vos.step(intent) as s:
            return s.run(fn)

    Empty intent fields are auto-filled (see _normalize_intent).
    """
    with self.step(self._normalize_intent(intent)) as s:
        return s.run(fn)
```

---

## 6. Usage Examples

### Minimal (1-line)

```python
from veronica import VeronicaOS, StepIntent

vos = VeronicaOS()
result = vos.run_step(
    StepIntent(step_id="", request_id="", chain_id="",
               kind="llm", model="gpt-4", tool_name=None,
               timeout_ms=0, metadata={}),
    fn=lambda: client.chat.completions.create(model="gpt-4", messages=[...]),
)
```

### Full control (context manager)

```python
intent = StepIntent(
    step_id="analyze-1", request_id="req-abc", chain_id="main",
    kind="llm", model="gpt-4", tool_name=None,
    timeout_ms=10000, metadata={"user": "alice"},
)

with vos.step(intent) as step:
    # Access policy decided by the OS
    print(f"Budget: ${step.policy.ceiling_usd}")

    # LLM call
    response = step.run_llm(lambda: client.chat.completions.create(...))

    # Tool call in same step
    tool_result = step.run_tool(lambda: search_engine.query(...))
```

### With observability

```python
from veronica import (
    VeronicaOS, BufferedEmitter, MetricsSubscriber, StepIntent,
)
from veronica.metrics_exporter import start_metrics_server

start_metrics_server()
emitter = BufferedEmitter()
emitter.subscribe("prometheus", MetricsSubscriber())
vos = VeronicaOS(emitter=emitter)

# Every run_step automatically emits metrics + structured logs
result = vos.run_step(
    StepIntent(step_id="", request_id="", chain_id="",
               kind="llm", model="gpt-4", tool_name=None,
               timeout_ms=0, metadata={}),
    fn=lambda: my_llm_call(),
)
```

---

## 7. Tests Summary

| Test | Validates |
|------|-----------|
| `test_fallback_snapshot_passes_collector` | Fallback snapshot is consumable by SimpleCollector, events contain step_id |
| `test_fallback_snapshot_without_safety_event` | Fallback works even when SafetyEvent import fails (monkeypatch) |
| `test_step_context_manager_calls_after_step` | `after_step` runs after normal execution |
| `test_step_context_manager_calls_after_step_on_exception` | `after_step` runs even when fn() raises |
| `test_step_run_dispatches_by_kind` | `run()` calls `wrap_tool_call` for kind="tool", `wrap_llm_call` otherwise |
| `test_normalize_fills_defaults` | Empty fields get UUID/default/step-N/30000/{} |
| `test_normalize_preserves_explicit_values` | Explicit values are not overwritten; returns same instance |
| `test_run_step_sugar` | `run_step` produces the same result as manual `step()` + `run()` |

---

## 8. Files Summary

| File | Change |
|------|--------|
| `src/veronica/os.py` | Add: `StepContext`, `_make_fallback_snapshot()`, `VeronicaOS.step()`, `VeronicaOS._normalize_intent()`, `VeronicaOS.run_step()` |
| `src/veronica/__init__.py` | Export `StepContext` |
| `tests/test_step_integration.py` | New: 8 tests |

**Protocol changes:** None.
**os.py pipeline structure:** Unchanged.
