# Phase 7: Org Policy Engine -- Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add organization-wide containment rules that constrain what Planner can decide, with explicit denial guard and observability integration.

**Architecture:** `OrgPolicy` frozen dataclass with `validate()` (pre-Planner hard block) and `clamp()` (post-Planner cap). `OrgPolicyDenied` exception raised by `StepContext._check_denial()`. `step_denied` event for observability. No Protocol changes.

**Tech Stack:** Python 3.10+, veronica-core, pytest, dataclasses, prometheus-client (optional)

**Design doc:** `docs/plans/2026-02-27-org-policy-design.md`

---

## Important Notes

- `DesiredPolicy` is a frozen dataclass. Use `dataclasses.replace()`, NOT `dataclasses.asdict()`.
- `blocked_models`/`blocked_tools` comparisons must be case-insensitive via `str.casefold()`.
- Casefold sets are cached in `__post_init__` (frozen dataclass, use `object.__setattr__`).
- `DecisionMeta.org_denial` has default `None` -- backward compatible.
- `OrgPolicyDenied` propagates out of `with vos.step()` -- `finally` block still runs `after_step`.

---

### Task 1: OrgPolicy dataclass + validate/clamp unit tests

**Files:**
- Modify: `src/veronica/types.py` (add `OrgPolicy` after `DecisionMeta`, add `org_denial` to `DecisionMeta`)
- Create: `tests/test_org_policy.py`

**Step 1: Write 8 failing tests**

Create `tests/test_org_policy.py`:

```python
# tests/test_org_policy.py
"""Tests for Phase 7: Org Policy Engine."""
from __future__ import annotations

import time

import pytest

from veronica.types import (
    DesiredPolicy,
    OrgPolicy,
    StepIntent,
)


def _intent(
    model: str | None = "gpt-4",
    tool_name: str | None = None,
    kind: str = "llm",
) -> StepIntent:
    return StepIntent(
        step_id="s1", request_id="r1", chain_id="c1",
        kind=kind, model=model, tool_name=tool_name,
        timeout_ms=30_000, metadata={},
    )


def _desired(
    ceiling_usd: float = 10.0,
    timeout_ms: int = 30_000,
    priority: int = 50,
    fallback_model: str | None = None,
) -> DesiredPolicy:
    return DesiredPolicy(
        chain_id="c1", ceiling_usd=ceiling_usd, ceiling_steps=100,
        ceiling_tokens_out=50_000, on_exceed="halt",
        fallback_model=fallback_model,
        timeout_ms=timeout_ms, priority=priority,
    )


class TestOrgPolicyValidate:
    def test_blocks_model(self) -> None:
        """Blocked model returns denial reason."""
        policy = OrgPolicy(blocked_models=frozenset({"gpt-4"}))
        result = policy.validate(_intent(model="gpt-4"))
        assert result is not None
        assert "gpt-4" in result
        assert "blocked" in result

    def test_blocks_tool(self) -> None:
        """Blocked tool returns denial reason."""
        policy = OrgPolicy(blocked_tools=frozenset({"dangerous_tool"}))
        result = policy.validate(_intent(tool_name="dangerous_tool"))
        assert result is not None
        assert "dangerous_tool" in result

    def test_allows_clean_intent(self) -> None:
        """Clean intent returns None."""
        policy = OrgPolicy(blocked_models=frozenset({"gpt-4o"}))
        result = policy.validate(_intent(model="gpt-4"))
        assert result is None

    def test_casefold(self) -> None:
        """Case-insensitive model/tool matching."""
        policy = OrgPolicy(
            blocked_models=frozenset({"GPT-4"}),
            blocked_tools=frozenset({"DangerousTool"}),
        )
        assert policy.validate(_intent(model="gpt-4")) is not None
        assert policy.validate(_intent(model="Gpt-4")) is not None
        assert policy.validate(_intent(tool_name="dangeroustool")) is not None


class TestOrgPolicyClamp:
    def test_clamp_ceiling(self) -> None:
        """ceiling_usd capped to max."""
        policy = OrgPolicy(max_ceiling_usd=5.0)
        result = policy.clamp(_desired(ceiling_usd=10.0), _intent())
        assert result.ceiling_usd == 5.0
        # Other fields preserved
        assert result.ceiling_steps == 100
        assert result.on_exceed == "halt"

    def test_clamp_timeout(self) -> None:
        """timeout_ms capped to max."""
        policy = OrgPolicy(max_timeout_ms=10_000)
        result = policy.clamp(_desired(timeout_ms=30_000), _intent())
        assert result.timeout_ms == 10_000

    def test_clamp_fallback_model_blocked(self) -> None:
        """Blocked fallback_model set to None."""
        policy = OrgPolicy(blocked_models=frozenset({"gpt-4o"}))
        result = policy.clamp(_desired(fallback_model="GPT-4o"), _intent())
        assert result.fallback_model is None

    def test_clamp_no_change(self) -> None:
        """Within limits returns same instance."""
        policy = OrgPolicy(max_ceiling_usd=100.0, max_timeout_ms=60_000)
        desired = _desired(ceiling_usd=10.0, timeout_ms=30_000)
        result = policy.clamp(desired, _intent())
        assert result is desired
```

**Step 2: Run tests to verify they fail**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_org_policy.py -v`

Expected: FAIL with `ImportError: cannot import name 'OrgPolicy' from 'veronica.types'`

**Step 3: Implement OrgPolicy + DecisionMeta.org_denial**

In `src/veronica/types.py`, add `org_denial` field to `DecisionMeta` (line ~121):

```python
@dataclass(frozen=True)
class DecisionMeta:
    """Audit record. Why this PolicyConfig was chosen."""

    risk_level: str
    recommendation: str
    degraded: bool
    stage_time_ms: Mapping[str, float]
    org_denial: str | None = None
```

Add `OrgPolicy` after `StepHandle` (end of file):

```python
@dataclass(frozen=True)
class OrgPolicy:
    """Organization-wide containment rules.

    Injected into VeronicaOS. Constrains what Planner can decide.
    validate() blocks forbidden intents before Planner runs.
    clamp() caps DesiredPolicy after Planner runs.
    """

    max_ceiling_usd: float | None = None
    max_timeout_ms: int | None = None
    blocked_models: frozenset[str] = frozenset()
    blocked_tools: frozenset[str] = frozenset()
    max_priority: int | None = None
    _blocked_models_cf: frozenset[str] = field(init=False, repr=False, compare=False)
    _blocked_tools_cf: frozenset[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "_blocked_models_cf",
            frozenset(m.casefold() for m in self.blocked_models),
        )
        object.__setattr__(
            self, "_blocked_tools_cf",
            frozenset(t.casefold() for t in self.blocked_tools),
        )

    def validate(self, intent: "StepIntent") -> str | None:
        """Return denial reason if intent violates org policy, else None."""
        if intent.model and intent.model.casefold() in self._blocked_models_cf:
            return f"model '{intent.model}' is blocked by org policy"
        if intent.tool_name and intent.tool_name.casefold() in self._blocked_tools_cf:
            return f"tool '{intent.tool_name}' is blocked by org policy"
        return None

    def clamp(self, desired: "DesiredPolicy", intent: "StepIntent") -> "DesiredPolicy":
        """Cap DesiredPolicy fields to org limits. Returns new instance if changed."""
        changes: dict[str, Any] = {}

        if self.max_ceiling_usd is not None and desired.ceiling_usd > self.max_ceiling_usd:
            changes["ceiling_usd"] = self.max_ceiling_usd
        if self.max_timeout_ms is not None and desired.timeout_ms > self.max_timeout_ms:
            changes["timeout_ms"] = self.max_timeout_ms
        if self.max_priority is not None and desired.priority > self.max_priority:
            changes["priority"] = self.max_priority
        if (
            desired.fallback_model
            and desired.fallback_model.casefold() in self._blocked_models_cf
        ):
            changes["fallback_model"] = None

        if not changes:
            return desired

        from dataclasses import replace
        return replace(desired, **changes)
```

**Step 4: Run tests to verify they pass**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_org_policy.py -v`

Expected: 8 PASSED

**Step 5: Run full test suite**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/ -x -q -m "not docker"`

Expected: All 216+ tests pass

**Step 6: Commit**

```bash
cd "D:/work/Projects/veronica"
git add src/veronica/types.py tests/test_org_policy.py
git commit -m "feat: add OrgPolicy dataclass with validate/clamp and DecisionMeta.org_denial"
```

---

### Task 2: OrgPolicyDenied + StepContext._check_denial() + tests

**Files:**
- Modify: `src/veronica/os.py` (add `OrgPolicyDenied`, add `_check_denial` to `StepContext`)
- Modify: `tests/test_org_policy.py` (add test)

**Step 1: Write failing test**

Append to `tests/test_org_policy.py`:

```python
from unittest.mock import MagicMock

from veronica_core.containment.execution_context import ExecutionContext

from veronica.os import OrgPolicyDenied, StepContext, VeronicaOS
from veronica.types import (
    CostEstimate,
    DecisionMeta,
    OrgPolicy,
    PolicyConfig,
    StepHandle,
)


def _make_denied_handle(intent: StepIntent | None = None) -> StepHandle:
    intent = intent or _intent()
    return StepHandle(
        intent=intent,
        policy=PolicyConfig(
            chain_id="c1", ceiling_usd=0.0, on_exceed="halt", issued_at=time.time(),
        ),
        desired=DesiredPolicy(
            chain_id="c1", ceiling_usd=0.0, ceiling_steps=0,
            ceiling_tokens_out=0, on_exceed="halt",
            fallback_model=None, timeout_ms=30_000, priority=0,
        ),
        cost=CostEstimate(
            estimated_usd=0.0, confidence=1.0,
            model_used="gpt-4", basis="fallback",
        ),
        decision_meta=DecisionMeta(
            risk_level="critical", recommendation="halt",
            degraded=True, stage_time_ms={"org_policy": 0.0},
            org_denial="model 'gpt-4' is blocked by org policy",
        ),
    )


class TestOrgPolicyDenied:
    def test_deny_fn_not_called(self) -> None:
        """Denied step raises OrgPolicyDenied, fn is never called."""
        vos = VeronicaOS(
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4"})),
        )
        intent = _intent(model="gpt-4")
        fn = MagicMock(return_value="should_not_run")

        with pytest.raises(OrgPolicyDenied, match="blocked"):
            with vos.step(intent) as ctx:
                ctx.run(fn)

        fn.assert_not_called()

    def test_deny_after_step_still_runs(self) -> None:
        """Denied step still commits to Store via after_step (step() finally block)."""
        vos = VeronicaOS(
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4"})),
        )
        intent = _intent(model="gpt-4")

        with pytest.raises(OrgPolicyDenied):
            with vos.step(intent) as ctx:
                ctx.run(lambda: None)

        # after_step committed to store
        history = vos._store.build_history("c1")
        assert history.depth == 1
```

**Step 2: Run tests to verify they fail**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_org_policy.py::TestOrgPolicyDenied -v`

Expected: FAIL with `ImportError: cannot import name 'OrgPolicyDenied' from 'veronica.os'`

**Step 3: Implement OrgPolicyDenied + _check_denial**

In `src/veronica/os.py`, add `OrgPolicyDenied` before `StepContext` class (around line 107):

```python
class OrgPolicyDenied(Exception):
    """Raised when org policy denies the step."""
```

Add `_check_denial` method to `StepContext` and update `run`/`run_llm`/`run_tool`:

```python
class StepContext:
    """Yielded by vos.step(). Wraps ExecutionContext."""

    def __init__(self, handle: StepHandle, exec_ctx: ExecutionContext) -> None:
        self.handle = handle
        self.exec_ctx = exec_ctx

    def _check_denial(self) -> None:
        if self.handle.decision_meta.org_denial is not None:
            raise OrgPolicyDenied(self.handle.decision_meta.org_denial)

    def run(self, fn: Callable[[], T]) -> T:
        """Execute fn, dispatching by intent.kind."""
        self._check_denial()
        if self.handle.intent.kind == "tool":
            return self.exec_ctx.wrap_tool_call(fn)
        return self.exec_ctx.wrap_llm_call(fn)

    def run_llm(self, fn: Callable[[], T]) -> T:
        self._check_denial()
        return self.exec_ctx.wrap_llm_call(fn)

    def run_tool(self, fn: Callable[[], T]) -> T:
        self._check_denial()
        return self.exec_ctx.wrap_tool_call(fn)

    @property
    def policy(self) -> PolicyConfig:
        return self.handle.policy
```

**Step 4: Run tests to verify they pass**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_org_policy.py::TestOrgPolicyDenied -v`

Expected: 2 PASSED

**Step 5: Run full test suite**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/ -x -q -m "not docker"`

Expected: All tests pass (existing StepContext tests still pass -- _check_denial is no-op when org_denial is None)

**Step 6: Commit**

```bash
cd "D:/work/Projects/veronica"
git add src/veronica/os.py tests/test_org_policy.py
git commit -m "feat: add OrgPolicyDenied exception and StepContext._check_denial guard"
```

---

### Task 3: VeronicaOS.before_step() integration (validate + clamp) + tests

**Files:**
- Modify: `src/veronica/os.py` (constructor + before_step)
- Modify: `tests/test_org_policy.py` (add tests)

**Step 1: Write 4 failing tests**

Append to `tests/test_org_policy.py`:

```python
class TestOrgPolicyIntegration:
    def test_deny_emits_step_denied_event(self) -> None:
        """Denied step emits step_denied event with kind."""
        from veronica.buffered_emitter import BufferedEmitter

        emitter = BufferedEmitter()
        events: list[tuple[str, dict]] = []
        emitter.subscribe("test", lambda et, p: events.append((et, p)))

        vos = VeronicaOS(
            emitter=emitter,
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4"})),
        )
        intent = _intent(model="gpt-4")

        with pytest.raises(OrgPolicyDenied):
            with vos.step(intent) as ctx:
                ctx.run(lambda: None)

        denied_events = [(et, p) for et, p in events if et == "step_denied"]
        assert len(denied_events) == 1
        _, payload = denied_events[0]
        assert payload["kind"] == "llm"
        assert "blocked" in payload["reason"]
        assert payload["model"] == "gpt-4"

    def test_deny_emitter_failure_no_crash(self) -> None:
        """Emitter exception during step_denied does not break before_step."""
        from unittest.mock import MagicMock as Mock

        bad_emitter = Mock()
        bad_emitter.emit.side_effect = RuntimeError("emitter broken")

        vos = VeronicaOS(
            emitter=bad_emitter,
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4"})),
        )
        intent = _intent(model="gpt-4")

        # before_step still returns a StepHandle (doesn't crash)
        handle = vos.before_step(intent)
        assert handle.decision_meta.org_denial is not None

    def test_clamp_integration(self) -> None:
        """Planner output is clamped by org policy before reaching Arbiter."""
        from unittest.mock import MagicMock as Mock

        spy_arbiter = Mock()
        spy_arbiter.arbitrate.return_value = {
            "c1": PolicyConfig(
                chain_id="c1", ceiling_usd=5.0, on_exceed="halt",
                issued_at=time.time(),
            ),
        }

        vos = VeronicaOS(
            arbiter=spy_arbiter,
            org_policy=OrgPolicy(max_ceiling_usd=5.0),
        )
        intent = _intent(model="gpt-4")

        handle = vos.before_step(intent)

        # Verify arbiter was called with clamped desired
        call_args = spy_arbiter.arbitrate.call_args
        desired_list = call_args[0][0]  # first positional arg
        assert desired_list[0].ceiling_usd <= 5.0

    def test_no_org_policy_passthrough(self) -> None:
        """org_policy=None means all existing behavior unchanged."""
        vos = VeronicaOS()  # no org_policy
        intent = _intent(model="gpt-4")

        handle = vos.before_step(intent)

        assert handle.decision_meta.org_denial is None
        assert handle.policy.ceiling_usd > 0
```

**Step 2: Run tests to verify they fail**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_org_policy.py::TestOrgPolicyIntegration -v`

Expected: FAIL (VeronicaOS constructor doesn't accept org_policy)

**Step 3: Implement before_step integration**

In `src/veronica/os.py`, modify `VeronicaOS.__init__` to accept `org_policy`:

```python
    def __init__(
        self,
        collector: CollectorProtocol | None = None,
        analyzer: AnalyzerProtocol | None = None,
        cost_model: CostModelProtocol | None = None,
        planner: PlannerProtocol | None = None,
        arbiter: ArbiterProtocol | None = None,
        store: StoreProtocol | None = None,
        emitter: EventEmitterProtocol | None = None,
        stage_budgets_ms: Mapping[str, float] | None = None,
        request_budget_usd: float = _DEFAULT_REQUEST_BUDGET_USD,
        org_policy: "OrgPolicy | None" = None,
    ) -> None:
        ...  # existing assignments
        self._org_policy = org_policy
```

Add import at top of os.py:

```python
from veronica.types import (
    ...  # existing imports
    OrgPolicy,
)
```

Modify `before_step` -- insert validate at top and clamp after Planner:

```python
    def before_step(self, intent: StepIntent) -> StepHandle:
        """Pre-execution pipeline. Returns a StepHandle carrying the policy."""
        stage_times: dict[str, float] = {}
        degraded = False

        # 0. Org policy validation (hard block)
        if self._org_policy is not None:
            denial = self._org_policy.validate(intent)
            if denial is not None:
                logger.warning("[VERONICA_OS] org policy denied: %s", denial)
                try:
                    self._emitter.emit("step_denied", {
                        "schema_version": 1,
                        "request_id": intent.request_id,
                        "step_id": intent.step_id,
                        "chain_id": intent.chain_id,
                        "kind": intent.kind,
                        "reason": denial,
                        "model": intent.model,
                        "tool_name": intent.tool_name,
                    })
                except Exception:
                    pass  # fire-and-forget
                policy = PolicyConfig(
                    chain_id=intent.chain_id,
                    ceiling_usd=0.0,
                    on_exceed="halt",
                    issued_at=time.time(),
                )
                return StepHandle(
                    intent=intent,
                    policy=policy,
                    desired=DesiredPolicy(
                        chain_id=intent.chain_id,
                        ceiling_usd=0.0, ceiling_steps=0,
                        ceiling_tokens_out=0, on_exceed="halt",
                        fallback_model=None,
                        timeout_ms=intent.timeout_ms, priority=0,
                    ),
                    cost=CostEstimate(
                        estimated_usd=0.0, confidence=1.0,
                        model_used=intent.model or "unknown",
                        basis="fallback",
                    ),
                    decision_meta=DecisionMeta(
                        risk_level="critical",
                        recommendation="halt",
                        degraded=True,
                        stage_time_ms={"org_policy": 0.0},
                        org_denial=denial,
                    ),
                )

        # 1. Build history
        history = self._store.build_history(intent.chain_id)

        # ... (existing CostModel, BudgetState, Planner code unchanged)

        # After Planner (step 4), before chain_id fill (step 5):

        # 4.5 Org policy clamp
        if self._org_policy is not None:
            desired = self._org_policy.clamp(desired, intent)

        # ... (existing chain_id fill, arbitration, Arbiter code unchanged)
```

**Step 4: Run tests to verify they pass**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_org_policy.py::TestOrgPolicyIntegration -v`

Expected: 4 PASSED

**Step 5: Run full test suite**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/ -x -q -m "not docker"`

Expected: All tests pass

**Step 6: Commit**

```bash
cd "D:/work/Projects/veronica"
git add src/veronica/os.py tests/test_org_policy.py
git commit -m "feat: integrate OrgPolicy validate/clamp into before_step pipeline"
```

---

### Task 4: MetricsSubscriber + StructuredLogSubscriber extensions + tests

**Files:**
- Modify: `src/veronica/metrics_subscriber.py`
- Modify: `src/veronica/structured_log_subscriber.py`
- Modify: `tests/test_org_policy.py` (add metrics test)

**Step 1: Write failing test**

Append to `tests/test_org_policy.py`:

```python
class TestOrgPolicyMetrics:
    def test_denied_total_metric(self) -> None:
        """MetricsSubscriber increments veronica_denied_total on step_denied."""
        from prometheus_client import CollectorRegistry
        from veronica.metrics_subscriber import MetricsSubscriber

        registry = CollectorRegistry()
        subscriber = MetricsSubscriber(registry=registry)

        subscriber("step_denied", {
            "kind": "llm",
            "reason": "model blocked",
            "model": "gpt-4",
        })

        value = registry.get_sample_value(
            "veronica_denied_total",
            {"kind": "llm"},
        )
        assert value == 1.0

    def test_denied_total_unknown_kind(self) -> None:
        """Missing kind defaults to 'unknown'."""
        from prometheus_client import CollectorRegistry
        from veronica.metrics_subscriber import MetricsSubscriber

        registry = CollectorRegistry()
        subscriber = MetricsSubscriber(registry=registry)

        subscriber("step_denied", {"reason": "blocked"})

        value = registry.get_sample_value(
            "veronica_denied_total",
            {"kind": "unknown"},
        )
        assert value == 1.0
```

**Step 2: Run tests to verify they fail**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_org_policy.py::TestOrgPolicyMetrics -v`

Expected: FAIL (MetricsSubscriber ignores step_denied events currently)

**Step 3: Implement MetricsSubscriber extension**

In `src/veronica/metrics_subscriber.py`, update `_KNOWN_STAGES` (line 8):

```python
_KNOWN_STAGES = frozenset({
    "collector", "analyzer", "cost_model", "planner", "arbiter",
    "store", "emit", "org_policy",
})
```

Add `denied_total` Counter in `__init__` (after `degrade_total`):

```python
        self.denied_total = self._get_or_create(
            registry, Counter,
            f"{prefix}_denied_total",
            "Steps denied by org policy",
            ["kind"],
        )
```

Update `__call__` to handle `step_denied`:

```python
    def __call__(
        self, event_type: str, payload: Mapping[str, Any],
    ) -> None:
        """Callback for BufferedEmitter.subscribe()."""
        if event_type == "step_completed":
            self._handle_step_completed(payload)
        elif event_type == "step_denied":
            self._handle_step_denied(payload)

    def _handle_step_completed(self, payload: Mapping[str, Any]) -> None:
        # Move ALL existing __call__ body here (lines 90-120)
        self.steps_total.labels(...  # existing code
        ...

    def _handle_step_denied(self, payload: Mapping[str, Any]) -> None:
        kind = payload.get("kind", "unknown")
        self.denied_total.labels(kind=kind).inc()
```

**Step 4: Implement StructuredLogSubscriber extension**

In `src/veronica/structured_log_subscriber.py`, update `__call__` (line 39):

```python
    def __call__(
        self, event_type: str, payload: Mapping[str, Any],
    ) -> None:
        """Callback for BufferedEmitter.subscribe()."""
        if event_type == "step_completed":
            self._log_step_completed(payload)
        elif event_type == "step_denied":
            self._log_step_denied(payload)

    def _log_step_completed(self, payload: Mapping[str, Any]) -> None:
        # Move existing __call__ body here (lines 42-64)
        signals = payload.get("signals", [])
        record = { ... }  # existing record construction
        self._logger.log(self._level, json.dumps(record, default=str))

    def _log_step_denied(self, payload: Mapping[str, Any]) -> None:
        record = {
            "event": "step_denied",
            "schema_version": payload.get("schema_version", 0),
            "request_id": payload.get("request_id"),
            "step_id": payload.get("step_id"),
            "chain_id": payload.get("chain_id"),
            "kind": payload.get("kind"),
            "reason": payload.get("reason"),
            "model": payload.get("model"),
            "tool_name": payload.get("tool_name"),
        }
        self._logger.log(self._level, json.dumps(record, default=str))
```

**Step 5: Run tests to verify they pass**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_org_policy.py::TestOrgPolicyMetrics -v`

Expected: 2 PASSED

**Step 6: Run full test suite**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/ -x -q -m "not docker"`

Expected: All tests pass (existing observability tests still pass)

**Step 7: Commit**

```bash
cd "D:/work/Projects/veronica"
git add src/veronica/metrics_subscriber.py src/veronica/structured_log_subscriber.py tests/test_org_policy.py
git commit -m "feat: add veronica_denied_total metric and step_denied log event"
```

---

### Task 5: Export OrgPolicy + OrgPolicyDenied from __init__.py

**Files:**
- Modify: `src/veronica/__init__.py`

**Step 1: Add imports and exports**

In `src/veronica/__init__.py`:

Add import:
```python
from veronica.os import OrgPolicyDenied, StepContext, VeronicaOS
```
(Replace existing `from veronica.os import StepContext, VeronicaOS`)

Add type import:
```python
from veronica.types import (
    ...  # existing
    OrgPolicy,
)
```

Add to `__all__`:
```python
__all__ = [
    # Core
    "VeronicaOS",
    "StepContext",
    "OrgPolicy",
    "OrgPolicyDenied",
    ...
```

**Step 2: Verify imports**

Run: `cd "D:/work/Projects/veronica" && uv run python -c "from veronica import OrgPolicy, OrgPolicyDenied; print(OrgPolicy, OrgPolicyDenied)"`

Expected: `<class 'veronica.types.OrgPolicy'> <class 'veronica.os.OrgPolicyDenied'>`

**Step 3: Run full test suite**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/ -x -q -m "not docker"`

Expected: All tests pass

**Step 4: Commit**

```bash
cd "D:/work/Projects/veronica"
git add src/veronica/__init__.py
git commit -m "feat: export OrgPolicy and OrgPolicyDenied from veronica package"
```

---

### Task 6: Final verification + version bump to 0.7.0

**Files:**
- Modify: `pyproject.toml` (version)
- Modify: `src/veronica/__init__.py` (version)
- Modify: `README.md` (status table)

**Step 1: Run full test suite with coverage**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/ -v --cov=veronica --cov-report=term-missing -m "not docker"`

Expected: All tests pass, coverage >= 80%

**Step 2: Run org policy tests specifically**

Run: `cd "D:/work/Projects/veronica" && uv run pytest tests/test_org_policy.py -v`

Expected: All 14 tests pass

**Step 3: Bump version to 0.7.0**

In `pyproject.toml`: `version = "0.7.0"`

In `src/veronica/__init__.py`: `__version__ = "0.7.0"`

**Step 4: Update README**

Add row to Status table:
```markdown
| v0.7.0 | Phase 7 | Org policy engine: validate/clamp, `step_denied` metric |
```

Update **Current** line with actual test count and coverage.

**Step 5: Commit**

```bash
cd "D:/work/Projects/veronica"
git add pyproject.toml src/veronica/__init__.py README.md
git commit -m "chore: bump version to 0.7.0 (Phase 7 Org Policy Engine)"
```

**Step 6: Push**

```bash
cd "D:/work/Projects/veronica" && git push origin main
```

---

## Summary

| Task | Description | Tests |
|------|-------------|-------|
| 1 | OrgPolicy dataclass + validate/clamp | 8 |
| 2 | OrgPolicyDenied + _check_denial | 2 |
| 3 | before_step integration (validate + clamp + emit) | 4 |
| 4 | MetricsSubscriber + StructuredLogSubscriber | 2 |
| 5 | Export from __init__.py | 0 (verify) |
| 6 | Final verification + version bump | 0 (full suite) |

**Total new tests:** 16 (14 planned + 2 metrics)

**Files modified:**
- `src/veronica/types.py` (Task 1)
- `src/veronica/os.py` (Tasks 2-3)
- `src/veronica/metrics_subscriber.py` (Task 4)
- `src/veronica/structured_log_subscriber.py` (Task 4)
- `src/veronica/__init__.py` (Tasks 5-6)
- `pyproject.toml` (Task 6)
- `README.md` (Task 6)
- `tests/test_org_policy.py` (Tasks 1-4, new file)
