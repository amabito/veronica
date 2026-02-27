# Phase 7: Org Policy Engine -- Design

**Goal:** Organization-wide containment rules that constrain what Planner can decide, without changing any Protocol interface.

**Approach:** Frozen dataclass (`OrgPolicy`) with two methods: `validate()` (pre-Planner hard block) and `clamp()` (post-Planner numerical cap). Injected into `VeronicaOS` via constructor. No Protocol changes. No Planner/Arbiter modifications.

**Scope:**
1. `OrgPolicy` frozen dataclass with `validate()` and `clamp()` methods
2. `OrgPolicyDenied` typed exception
3. `StepContext._check_denial()` guard (explicit, not ExecutionContext-dependent)
4. `VeronicaOS.before_step()` integration: validate (pre) + clamp (post)
5. `step_denied` event emission
6. `MetricsSubscriber` extension: `veronica_denied_total` Counter
7. `StructuredLogSubscriber` extension: `step_denied` event logging
8. `DecisionMeta.org_denial` field (backward-compatible, default None)
9. Tests (14)

**Protocol changes:** None. PlannerProtocol, ArbiterProtocol, all 7 interfaces unchanged.
**os.py pipeline structure:** Two insertion points only. before_step/after_step signatures unchanged.

---

## 1. OrgPolicy Dataclass

Defined in `types.py`, after `DecisionMeta`.

```python
from dataclasses import dataclass, field
from typing import Any

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

    def validate(self, intent: StepIntent) -> str | None:
        """Return denial reason if intent violates org policy, else None."""
        if intent.model and intent.model.casefold() in self._blocked_models_cf:
            return f"model '{intent.model}' is blocked by org policy"
        if intent.tool_name and intent.tool_name.casefold() in self._blocked_tools_cf:
            return f"tool '{intent.tool_name}' is blocked by org policy"
        return None

    def clamp(self, desired: DesiredPolicy, intent: StepIntent) -> DesiredPolicy:
        """Cap DesiredPolicy fields to org limits. Returns new instance if changed."""
        changes: dict[str, Any] = {}

        if self.max_ceiling_usd is not None and desired.ceiling_usd > self.max_ceiling_usd:
            changes["ceiling_usd"] = self.max_ceiling_usd
        if self.max_timeout_ms is not None and desired.timeout_ms > self.max_timeout_ms:
            changes["timeout_ms"] = self.max_timeout_ms
        if self.max_priority is not None and desired.priority > self.max_priority:
            changes["priority"] = self.max_priority
        # Block fallback to forbidden models
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

### Design decisions

- `validate()` returns `str | None` -- denial reason or None. Simple, no custom result type.
- `clamp(desired, intent)` -- intent is needed for fallback_model regulation and future kind-dependent rules.
- `_blocked_models_cf` / `_blocked_tools_cf` -- casefold sets cached in `__post_init__`, not rebuilt per call.
- `compare=False` on cached fields -- equality ignores derived state.
- `dataclasses.replace()` -- no deep copy unlike `asdict()`, type-safe.
- `max_priority` -- prevents low-priority chains from claiming high priority.
- No `min_ceiling_usd` -- YAGNI for now.

---

## 2. DecisionMeta Extension

```python
@dataclass(frozen=True)
class DecisionMeta:
    risk_level: str
    recommendation: str
    degraded: bool
    stage_time_ms: Mapping[str, float]
    org_denial: str | None = None  # NEW -- backward compatible
```

Default `None` means all existing code is unaffected.

---

## 3. OrgPolicyDenied Exception

Defined in `os.py`.

```python
class OrgPolicyDenied(Exception):
    """Raised when org policy denies the step."""
```

---

## 4. StepContext._check_denial()

Explicit guard in `StepContext`. Does NOT rely on ExecutionContext behavior with ceiling=0.

```python
class StepContext:
    def _check_denial(self) -> None:
        if self.handle.decision_meta.org_denial is not None:
            raise OrgPolicyDenied(self.handle.decision_meta.org_denial)

    def run(self, fn: Callable[[], T]) -> T:
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
```

When `OrgPolicyDenied` is raised, it propagates out of the `with vos.step()` block. The `finally` clause in `step()` still runs `after_step`. Budget settle, store commit, event emission all execute.

---

## 5. VeronicaOS Integration

### Constructor

```python
class VeronicaOS:
    def __init__(
        self,
        ...
        org_policy: OrgPolicy | None = None,  # NEW
    ) -> None:
        ...
        self._org_policy = org_policy
```

Default `None` = no org constraints.

### before_step: validate (pre-Planner)

Inserted at the very top of `before_step()`, before history/CostModel:

```python
def before_step(self, intent: StepIntent) -> StepHandle:
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

    # 1. Build history (existing code, unchanged)
    ...
```

### before_step: clamp (post-Planner)

Inserted after Planner output, before chain_id fill:

```python
    # 4. Planner
    ...  # (existing code produces `desired`)

    # 4.5 Org policy clamp
    if self._org_policy is not None:
        desired = self._org_policy.clamp(desired, intent)

    # 5. Fill chain_id from intent (existing code)
    ...
```

### Pipeline flow summary

```
before_step:
  0. OrgPolicy.validate(intent)     ← NEW (deny → immediate halt StepHandle)
  1. build_history                   (unchanged)
  2. CostModel.estimate()            (unchanged)
  3. BudgetState                     (unchanged)
  4. Planner.plan()                  (unchanged)
  4.5 OrgPolicy.clamp(desired)       ← NEW (numerical cap)
  5. chain_id fill                   (unchanged)
  6. Arbitration context             (unchanged)
  7. Arbiter.arbitrate()             (unchanged)
  8. DecisionMeta                    (unchanged)
  9. return StepHandle               (unchanged)

after_step:                          (entirely unchanged)
```

---

## 6. MetricsSubscriber Extension

```python
class MetricsSubscriber:
    def __init__(self, prefix="veronica", registry=None):
        ...
        self.denied_total = self._get_or_create(
            registry, Counter,
            f"{prefix}_denied_total",
            "Steps denied by org policy",
            ["kind"],
        )

    def __call__(self, event_type, payload):
        if event_type == "step_completed":
            ...  # existing
        elif event_type == "step_denied":
            self._handle_step_denied(payload)

    def _handle_step_denied(self, payload):
        kind = payload.get("kind", "unknown")
        self.denied_total.labels(kind=kind).inc()
```

Label is `kind` only (finite: llm/tool/system). No `reason` (high cardinality).

Also add `"org_policy"` to `_KNOWN_STAGES` so stage_elapsed_ms handles it.

---

## 7. StructuredLogSubscriber Extension

```python
def __call__(self, event_type, payload):
    if event_type in ("step_completed", "step_denied"):
        self._log(event_type, payload)
```

---

## 8. Tests Summary

| # | Test | Validates |
|---|------|-----------|
| 1 | `test_validate_blocks_model` | blocked model returns denial reason |
| 2 | `test_validate_blocks_tool` | blocked tool returns denial reason |
| 3 | `test_validate_allows_clean_intent` | clean intent returns None |
| 4 | `test_validate_casefold` | case-insensitive matching |
| 5 | `test_clamp_ceiling` | ceiling_usd capped to max |
| 6 | `test_clamp_timeout` | timeout_ms capped to max |
| 7 | `test_clamp_fallback_model_blocked` | blocked fallback_model -> None |
| 8 | `test_clamp_no_change` | within limits -> same instance |
| 9 | `test_deny_fn_not_called` | fn mock not called, OrgPolicyDenied raised (via `vos.step()`) |
| 10 | `test_deny_after_step_still_runs` | deny via `vos.step()`, Store.commit called |
| 11 | `test_deny_emits_step_denied_event` | emitter receives `step_denied` with `kind` |
| 12 | `test_deny_emitter_failure_no_crash` | emitter raises, before_step still returns StepHandle |
| 13 | `test_clamp_integration` | Planner $50 -> org $10 -> Arbiter sees $10 (spy on arbiter input) |
| 14 | `test_no_org_policy_passthrough` | org_policy=None, all existing behavior unchanged |

---

## 9. Files Summary

| File | Change |
|------|--------|
| `src/veronica/types.py` | `OrgPolicy` dataclass, `DecisionMeta.org_denial` field |
| `src/veronica/os.py` | `OrgPolicyDenied`, `StepContext._check_denial()`, `before_step` validate/clamp/emit |
| `src/veronica/__init__.py` | Export `OrgPolicy`, `OrgPolicyDenied` |
| `src/veronica/metrics_subscriber.py` | `veronica_denied_total` Counter, `_handle_step_denied()`, `_KNOWN_STAGES` update |
| `src/veronica/structured_log_subscriber.py` | `step_denied` event handling |
| `tests/test_org_policy.py` | New: 14 tests |

**Protocol changes:** None.
**os.py pipeline structure:** Two insertion points (validate pre-Planner, clamp post-Planner). Signatures unchanged.
