# Phase 6b: LLM Integration Adapter -- Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `vos.step()` context manager and `vos.run_step()` convenience wrapper so applications can execute LLM/tool calls through the full VERONICA OS pipeline with guaranteed `after_step` execution.

**Architecture:** Context manager (`step()`) as core API, thin sugar (`run_step()`) on top. `StepContext` wraps `ExecutionContext`. `_make_fallback_snapshot()` provides defensive fallback when `get_snapshot()` fails. `_normalize_intent()` fills empty fields with safe defaults. No protocol changes. No os.py pipeline changes.

**Tech Stack:** Python 3.10+, veronica-core (ExecutionContext, ContextSnapshot, SafetyEvent, Decision), pytest, dataclasses

**Design doc:** `docs/plans/2026-02-27-llm-integration-design.md`

---

## Important API Notes (Discovered During Plan Writing)

The design doc contains two inaccuracies in `_make_fallback_snapshot()`:

1. `decision="HALT"` -- actual API requires `Decision.HALT` (enum from `veronica_core.shield.hooks`)
2. `ts=time.time()` -- actual API requires `datetime` object (from `datetime` module)

These are wrapped in `try/except` in the design (so it would still work), but the implementation should use the correct types. The plan below uses the correct API.

---

### Task 1: `_make_fallback_snapshot()` + Tests

**Files:**
- Modify: `src/veronica/os.py:1-7` (add import) and after line 55 (add function)
- Create: `tests/test_step_integration.py`

**Step 1: Write two failing tests**

Create `tests/test_step_integration.py`:

```python
# tests/test_step_integration.py
"""Tests for Phase 6b: LLM integration adapter (step/run_step)."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from veronica_core.containment.execution_context import ContextSnapshot
from veronica_core.shield.event import SafetyEvent

from veronica.collector import SimpleCollector
from veronica.os import _make_fallback_snapshot
from veronica.types import StepIntent


def _empty_intent(
    step_id: str = "s1",
    chain_id: str = "c1",
    request_id: str = "r1",
) -> StepIntent:
    return StepIntent(
        step_id=step_id, request_id=request_id, chain_id=chain_id,
        kind="llm", model="gpt-4", tool_name=None,
        timeout_ms=30_000, metadata={},
    )


class TestFallbackSnapshot:
    def test_fallback_snapshot_passes_collector(self) -> None:
        """Fallback snapshot is consumable by SimpleCollector and events contain step_id."""
        intent = _empty_intent(step_id="test-step-1", chain_id="chain-a", request_id="req-x")
        snapshot = _make_fallback_snapshot(intent, "test_reason")

        # Verify snapshot fields
        assert snapshot.chain_id == "chain-a"
        assert snapshot.request_id == "req-x"
        assert snapshot.aborted is True
        assert snapshot.abort_reason == "test_reason"
        assert snapshot.cost_usd_accumulated == 0.0
        assert snapshot.nodes == []

        # Verify events contain step_id in metadata
        assert len(snapshot.events) == 1
        event = snapshot.events[0]
        assert isinstance(event, SafetyEvent)
        assert event.metadata["step_id"] == "test-step-1"

        # Verify SimpleCollector can consume it
        collector = SimpleCollector()
        outcome = collector.collect(snapshot)
        assert outcome.chain_id == "chain-a"
        assert outcome.status == "ok"
        assert len(outcome.events) == 1

    def test_fallback_snapshot_without_safety_event(self) -> None:
        """Fallback works even when SafetyEvent import fails (monkeypatch)."""
        intent = _empty_intent()

        with patch(
            "veronica.os._make_fallback_snapshot.__module__",
            side_effect=ImportError("mocked"),
        ):
            # We can't easily patch module-level import inside a function.
            # Instead, patch SafetyEvent constructor to raise.
            with patch(
                "veronica_core.shield.event.SafetyEvent",
                side_effect=TypeError("mocked API change"),
            ):
                snapshot = _make_fallback_snapshot(intent, "import_failed")

        assert snapshot.chain_id == "c1"
        assert snapshot.aborted is True
        assert snapshot.events == []  # empty because SafetyEvent failed

        # Still consumable by collector
        collector = SimpleCollector()
        outcome = collector.collect(snapshot)
        assert outcome.chain_id == "c1"
```

**Step 2: Run tests to verify they fail**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_step_integration.py::TestFallbackSnapshot -v`

Expected: FAIL with `ImportError: cannot import name '_make_fallback_snapshot' from 'veronica.os'`

**Step 3: Implement `_make_fallback_snapshot()`**

Add import at `src/veronica/os.py` line 7 (after existing `from typing import Any, Mapping`):

```python
from typing import Any, Callable, Mapping, TypeVar
```

Add function between the `_KNOWN_STAGES` constant (line 54) and the `VeronicaOS` class (line 57):

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
        from veronica_core.shield.hooks import Decision
        from datetime import datetime, timezone

        events = [SafetyEvent(
            event_type="snapshot_failed",
            decision=Decision.HALT,
            reason=reason,
            hook="veronica_os",
            ts=datetime.now(timezone.utc),
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

**Step 4: Run tests to verify they pass**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_step_integration.py::TestFallbackSnapshot -v`

Expected: 2 PASSED

**Step 5: Run full test suite**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/ -x -q --timeout=60 -m "not docker"`

Expected: All 201+ tests pass (no regressions)

**Step 6: Commit**

```bash
cd "D:/work/Projects/veronica"
git add src/veronica/os.py tests/test_step_integration.py
git commit -m "feat: add _make_fallback_snapshot for defensive ContextSnapshot creation"
```

---

### Task 2: `StepContext` Class + Tests

**Files:**
- Modify: `src/veronica/os.py` (add class before `VeronicaOS`, after `_make_fallback_snapshot`)
- Modify: `tests/test_step_integration.py` (add test class)

**Step 1: Write failing test**

Append to `tests/test_step_integration.py`:

```python
from unittest.mock import MagicMock

from veronica_core.containment.execution_context import ExecutionContext, ExecutionConfig
from veronica_core.shield.hooks import Decision

from veronica.os import StepContext
from veronica.types import PolicyConfig, StepHandle, CostEstimate, DesiredPolicy, DecisionMeta


def _make_handle(kind: str = "llm") -> StepHandle:
    intent = StepIntent(
        step_id="s1", request_id="r1", chain_id="c1",
        kind=kind, model="gpt-4", tool_name=None,
        timeout_ms=30_000, metadata={},
    )
    policy = PolicyConfig(
        chain_id="c1", ceiling_usd=1.0, on_exceed="halt", issued_at=time.time(),
    )
    desired = DesiredPolicy(
        chain_id="c1", ceiling_usd=1.0, ceiling_steps=100,
        ceiling_tokens_out=50_000, on_exceed="halt",
        fallback_model=None, timeout_ms=30_000, priority=50,
    )
    cost = CostEstimate(
        estimated_usd=0.01, confidence=0.9, model_used="gpt-4", basis="pricing_table",
    )
    meta = DecisionMeta(
        risk_level="nominal", recommendation="continue",
        degraded=False, stage_time_ms={},
    )
    return StepHandle(intent=intent, policy=policy, desired=desired, cost=cost, decision_meta=meta)


class TestStepContext:
    def test_run_dispatches_llm_for_llm_kind(self) -> None:
        """run() calls wrap_llm_call for kind='llm'."""
        handle = _make_handle(kind="llm")
        mock_ctx = MagicMock(spec=ExecutionContext)
        mock_ctx.wrap_llm_call.return_value = Decision.ALLOW
        step_ctx = StepContext(handle=handle, exec_ctx=mock_ctx)

        result = step_ctx.run(lambda: "llm_result")
        mock_ctx.wrap_llm_call.assert_called_once()
        mock_ctx.wrap_tool_call.assert_not_called()

    def test_run_dispatches_tool_for_tool_kind(self) -> None:
        """run() calls wrap_tool_call for kind='tool'."""
        handle = _make_handle(kind="tool")
        mock_ctx = MagicMock(spec=ExecutionContext)
        mock_ctx.wrap_tool_call.return_value = Decision.ALLOW
        step_ctx = StepContext(handle=handle, exec_ctx=mock_ctx)

        result = step_ctx.run(lambda: "tool_result")
        mock_ctx.wrap_tool_call.assert_called_once()
        mock_ctx.wrap_llm_call.assert_not_called()

    def test_run_dispatches_llm_for_system_kind(self) -> None:
        """run() calls wrap_llm_call for kind='system' (no wrap_system_call in core)."""
        handle = _make_handle(kind="system")
        mock_ctx = MagicMock(spec=ExecutionContext)
        mock_ctx.wrap_llm_call.return_value = Decision.ALLOW
        step_ctx = StepContext(handle=handle, exec_ctx=mock_ctx)

        result = step_ctx.run(lambda: "system_result")
        mock_ctx.wrap_llm_call.assert_called_once()

    def test_run_llm_calls_wrap_llm_call(self) -> None:
        """run_llm() directly calls wrap_llm_call."""
        handle = _make_handle()
        mock_ctx = MagicMock(spec=ExecutionContext)
        mock_ctx.wrap_llm_call.return_value = Decision.ALLOW
        step_ctx = StepContext(handle=handle, exec_ctx=mock_ctx)

        step_ctx.run_llm(lambda: "x")
        mock_ctx.wrap_llm_call.assert_called_once()

    def test_run_tool_calls_wrap_tool_call(self) -> None:
        """run_tool() directly calls wrap_tool_call."""
        handle = _make_handle()
        mock_ctx = MagicMock(spec=ExecutionContext)
        mock_ctx.wrap_tool_call.return_value = Decision.ALLOW
        step_ctx = StepContext(handle=handle, exec_ctx=mock_ctx)

        step_ctx.run_tool(lambda: "x")
        mock_ctx.wrap_tool_call.assert_called_once()

    def test_policy_property(self) -> None:
        """policy property returns the handle's policy."""
        handle = _make_handle()
        mock_ctx = MagicMock(spec=ExecutionContext)
        step_ctx = StepContext(handle=handle, exec_ctx=mock_ctx)

        assert step_ctx.policy is handle.policy
        assert step_ctx.policy.ceiling_usd == 1.0
```

**Step 2: Run tests to verify they fail**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_step_integration.py::TestStepContext -v`

Expected: FAIL with `ImportError: cannot import name 'StepContext' from 'veronica.os'`

**Step 3: Implement `StepContext`**

Add to `src/veronica/os.py`, after `_make_fallback_snapshot()` and before `class VeronicaOS`:

```python
T = TypeVar("T")


class StepContext:
    """Yielded by vos.step(). Wraps ExecutionContext."""

    def __init__(self, handle: StepHandle, exec_ctx: "ExecutionContext") -> None:
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

Also add `ExecutionContext` import at the top of `os.py`:

```python
from veronica_core.containment.execution_context import (
    ContextSnapshot,
    ExecutionContext,
)
```

**Step 4: Run tests to verify they pass**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_step_integration.py::TestStepContext -v`

Expected: 6 PASSED

**Step 5: Commit**

```bash
cd "D:/work/Projects/veronica"
git add src/veronica/os.py tests/test_step_integration.py
git commit -m "feat: add StepContext class with kind-dispatched run/run_llm/run_tool"
```

---

### Task 3: `VeronicaOS.step()` Context Manager + Tests

**Files:**
- Modify: `src/veronica/os.py` (add `step()` method to `VeronicaOS`, add `contextmanager` import)
- Modify: `tests/test_step_integration.py` (add test class)

**Step 1: Write failing tests**

Append to `tests/test_step_integration.py`:

```python
from veronica.os import VeronicaOS


class TestStepContextManager:
    def test_step_calls_after_step(self) -> None:
        """after_step runs after normal execution inside step()."""
        vos = VeronicaOS()
        intent = _empty_intent()

        with vos.step(intent) as ctx:
            assert isinstance(ctx, StepContext)
            assert ctx.policy.ceiling_usd > 0

        # after_step committed to store -- verify via store history
        history = vos._store.build_history("c1")
        assert history.depth == 1

    def test_step_calls_after_step_on_exception(self) -> None:
        """after_step runs even when the body raises an exception."""
        vos = VeronicaOS()
        intent = _empty_intent()

        with pytest.raises(ValueError, match="boom"):
            with vos.step(intent) as ctx:
                raise ValueError("boom")

        # after_step still committed
        history = vos._store.build_history("c1")
        assert history.depth == 1

    def test_step_uses_fallback_on_snapshot_failure(self) -> None:
        """When get_snapshot() fails, fallback snapshot is used and after_step still runs."""
        vos = VeronicaOS()
        intent = _empty_intent()

        with vos.step(intent) as ctx:
            # Force get_snapshot to fail
            ctx.exec_ctx.get_snapshot = MagicMock(side_effect=RuntimeError("boom"))

        # after_step still committed (via fallback snapshot)
        history = vos._store.build_history("c1")
        assert history.depth == 1
```

**Step 2: Run tests to verify they fail**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_step_integration.py::TestStepContextManager -v`

Expected: FAIL with `AttributeError: 'VeronicaOS' object has no attribute 'step'`

**Step 3: Implement `VeronicaOS.step()`**

Add import at top of `src/veronica/os.py`:

```python
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Mapping, TypeVar
```

Add method to `VeronicaOS` class (after `__init__`, before `before_step`):

```python
    @contextmanager
    def step(self, intent: StepIntent) -> Iterator[StepContext]:
        """Context manager for one execution step.

        Guarantees after_step always runs, even on exception.
        On get_snapshot() failure, a fallback ContextSnapshot is used.
        """
        handle = self.before_step(intent)
        ctx = ExecutionContext(config=handle.policy.to_exec_config())
        step_ctx = StepContext(handle=handle, exec_ctx=ctx)
        try:
            yield step_ctx
        finally:
            try:
                snapshot = ctx.get_snapshot()
            except Exception:
                logger.exception(
                    "[VERONICA_OS] snapshot retrieval failed; using fallback"
                )
                snapshot = _make_fallback_snapshot(
                    intent, "snapshot_retrieval_failed"
                )
            self.after_step(handle, snapshot)
```

**Step 4: Run tests to verify they pass**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_step_integration.py::TestStepContextManager -v`

Expected: 3 PASSED

**Step 5: Run full test suite**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/ -x -q -m "not docker"`

Expected: All tests pass

**Step 6: Commit**

```bash
cd "D:/work/Projects/veronica"
git add src/veronica/os.py tests/test_step_integration.py
git commit -m "feat: add VeronicaOS.step() context manager with guaranteed after_step"
```

---

### Task 4: `_normalize_intent()` + `run_step()` + Tests

**Files:**
- Modify: `src/veronica/os.py` (add `_normalize_intent` and `run_step` methods, add module-level counter)
- Modify: `tests/test_step_integration.py` (add test classes)

**Step 1: Write failing tests**

Append to `tests/test_step_integration.py`:

```python
import re


class TestNormalizeIntent:
    def test_fills_defaults(self) -> None:
        """Empty fields get UUID/default/step-N/30000/{}."""
        vos = VeronicaOS()
        intent = StepIntent(
            step_id="", request_id="", chain_id="",
            kind="llm", model="gpt-4", tool_name=None,
            timeout_ms=0, metadata={},
        )
        result = vos._normalize_intent(intent)

        # request_id: 32-char hex UUID
        assert len(result.request_id) == 32
        assert re.match(r"^[0-9a-f]{32}$", result.request_id)

        # chain_id: "default"
        assert result.chain_id == "default"

        # step_id: "step-N"
        assert result.step_id.startswith("step-")
        n = int(result.step_id.split("-")[1])
        assert n >= 1

        # timeout_ms: 30000
        assert result.timeout_ms == 30_000

        # metadata: {}
        assert result.metadata == {}

        # kind and model preserved
        assert result.kind == "llm"
        assert result.model == "gpt-4"

    def test_preserves_explicit_values(self) -> None:
        """Explicit values are not overwritten; returns same instance."""
        vos = VeronicaOS()
        intent = StepIntent(
            step_id="my-step", request_id="my-req", chain_id="my-chain",
            kind="tool", model=None, tool_name="search",
            timeout_ms=5000, metadata={"key": "val"},
        )
        result = vos._normalize_intent(intent)

        # Same instance (no changes needed)
        assert result is intent

    def test_partial_fill(self) -> None:
        """Only empty fields are filled; explicit fields preserved."""
        vos = VeronicaOS()
        intent = StepIntent(
            step_id="custom-step", request_id="", chain_id="my-chain",
            kind="llm", model="gpt-4", tool_name=None,
            timeout_ms=5000, metadata={"x": 1},
        )
        result = vos._normalize_intent(intent)

        assert result.step_id == "custom-step"  # preserved
        assert len(result.request_id) == 32  # filled
        assert result.chain_id == "my-chain"  # preserved
        assert result.timeout_ms == 5000  # preserved
        assert result.metadata == {"x": 1}  # preserved


class TestRunStep:
    def test_run_step_sugar(self) -> None:
        """run_step produces a result and commits to store."""
        vos = VeronicaOS()
        intent = StepIntent(
            step_id="", request_id="", chain_id="",
            kind="llm", model="gpt-4", tool_name=None,
            timeout_ms=0, metadata={},
        )

        result = vos.run_step(intent, lambda: "hello")

        # run_step returns the Decision from wrap_llm_call, not "hello"
        # (wrap_llm_call wraps the fn and returns Decision.ALLOW on success)
        assert result is not None

        # after_step committed to store via "default" chain
        history = vos._store.build_history("default")
        assert history.depth == 1
```

**Step 2: Run tests to verify they fail**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_step_integration.py::TestNormalizeIntent tests/test_step_integration.py::TestRunStep -v`

Expected: FAIL with `AttributeError: 'VeronicaOS' object has no attribute '_normalize_intent'`

**Step 3: Implement `_normalize_intent()` and `run_step()`**

Add module-level constants at `src/veronica/os.py`, after the existing imports and before `_DEFAULT_STAGE_BUDGETS`:

```python
import uuid
from itertools import count
```

And after `_KNOWN_STAGES`:

```python
_step_counter = count(1)
_DEFAULT_TIMEOUT_MS = 30_000
```

Add methods to `VeronicaOS` class, after `step()` and before `before_step()`:

```python
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

**Step 4: Run tests to verify they pass**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_step_integration.py::TestNormalizeIntent tests/test_step_integration.py::TestRunStep -v`

Expected: 4 PASSED

**Step 5: Run full test suite**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/ -x -q -m "not docker"`

Expected: All tests pass

**Step 6: Commit**

```bash
cd "D:/work/Projects/veronica"
git add src/veronica/os.py tests/test_step_integration.py
git commit -m "feat: add _normalize_intent and run_step sugar for 1-line LLM execution"
```

---

### Task 5: Export `StepContext` from `__init__.py`

**Files:**
- Modify: `src/veronica/__init__.py`

**Step 1: Add import and export**

In `src/veronica/__init__.py`, add import:

```python
from veronica.os import StepContext, VeronicaOS
```

(Replace existing `from veronica.os import VeronicaOS`)

Add `"StepContext"` to `__all__` after `"VeronicaOS"`:

```python
__all__ = [
    # Core
    "VeronicaOS",
    "StepContext",
    ...
```

**Step 2: Verify import works**

Run: `cd "D:/work/Projects/veronica" && uv run python -c "from veronica import StepContext; print(StepContext)"`

Expected: `<class 'veronica.os.StepContext'>`

**Step 3: Run full test suite**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/ -x -q -m "not docker"`

Expected: All tests pass

**Step 4: Commit**

```bash
cd "D:/work/Projects/veronica"
git add src/veronica/__init__.py
git commit -m "feat: export StepContext from veronica package"
```

---

### Task 6: Final Verification + Version Bump

**Files:**
- Modify: `src/veronica/__init__.py` (version bump)
- Modify: `pyproject.toml` (version bump)

**Step 1: Run full test suite with coverage**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/ -v --cov=veronica --cov-report=term-missing -m "not docker"`

Expected: All tests pass, coverage >= 80%

**Step 2: Verify all new tests pass**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_step_integration.py -v`

Expected: All 14 tests pass (2 fallback + 6 StepContext + 3 step CM + 3 normalize/run_step)

**Step 3: Bump version to 0.6.0**

In `pyproject.toml` change `version = "0.5.0"` to `version = "0.6.0"`.

In `src/veronica/__init__.py` change `__version__ = "0.5.0"` to `__version__ = "0.6.0"`.

**Step 4: Update README Status table**

In `README.md`, add row to Status table:

```markdown
| v0.6.0 | Phase 6b | LLM integration adapter: `step()` context manager, `run_step()` sugar |
```

And update the **Current** line:

```markdown
**Current:** v0.6.0 -- XXX tests, XX% coverage. Protocol interfaces stable since v0.1.0.
```

(Replace XXX with actual test count and coverage from Step 1)

**Step 5: Commit**

```bash
cd "D:/work/Projects/veronica"
git add pyproject.toml src/veronica/__init__.py README.md
git commit -m "chore: bump version to 0.6.0 (Phase 6b LLM integration adapter)"
```

**Step 6: Push**

```bash
cd "D:/work/Projects/veronica" && git push origin main
```

---

## Summary

| Task | Description | Tests |
|------|-------------|-------|
| 1 | `_make_fallback_snapshot()` | 2 |
| 2 | `StepContext` class | 6 |
| 3 | `VeronicaOS.step()` context manager | 3 |
| 4 | `_normalize_intent()` + `run_step()` | 4 |
| 5 | Export `StepContext` | 0 (verify only) |
| 6 | Final verification + version bump | 0 (full suite) |

**Total new tests:** 15

**Files modified:**
- `src/veronica/os.py` (Tasks 1-4)
- `tests/test_step_integration.py` (Tasks 1-4, new file)
- `src/veronica/__init__.py` (Tasks 5-6)
- `pyproject.toml` (Task 6)
- `README.md` (Task 6)
