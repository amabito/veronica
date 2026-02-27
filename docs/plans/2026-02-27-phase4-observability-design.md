# Phase 4: Observability -- Prometheus Metrics + Structured Logging

**Goal:** Add production observability to VeronicaOS via BufferedEmitter subscribers, without changing Protocol interfaces.

**Approach:** Expand `step_completed` event payload to carry full execution context. Two new subscriber classes consume the events: MetricsSubscriber (Prometheus) and StructuredLogSubscriber (JSON log).

**Scope:**
1. Expand emit payload in os.py (1 event = 1 record, 16 fields)
2. MetricsSubscriber (Prometheus counters/histograms)
3. StructuredLogSubscriber (JSON structured log)
4. Measure store/emit stage times in os.py

**Protocol changes:** None.
**os.py pipeline structure:** Unchanged (emit payload expanded, store/emit timing added).

---

## 1. emit Payload Expansion

### Current Payload (5 fields)

```python
{"step_id", "chain_id", "status", "cost_usd", "risk_level"}
```

### New Payload (16 fields)

```python
_KNOWN_STAGES = frozenset({
    "collector", "analyzer", "cost_model", "planner", "arbiter",
    "store", "emit",
})

self._emitter.emit("step_completed", {
    "schema_version": 1,
    # Identity
    "request_id": outcome.request_id,
    "step_id": outcome.step_id,
    "chain_id": outcome.chain_id,
    # Execution
    "kind": outcome.kind,
    "status": outcome.status,
    "cost_usd": outcome.cost_usd,
    "tokens_in": outcome.tokens_in,
    "tokens_out": outcome.tokens_out,
    "elapsed_ms": outcome.elapsed_ms,
    # Analysis
    "risk_level": analysis.risk_level,
    "recommendation": analysis.recommendation,
    "degraded": handle.decision_meta.degraded,
    "degrade_reason": self._degrade_reason(handle),
    "signals": [
        {"kind": s.kind, "severity": s.severity}
        for s in analysis.signals
    ],
    # Timing (fixed key set only)
    "stage_time_ms": {
        k: v for k, v in stage_times.items()
        if k in _KNOWN_STAGES
    },
})
```

### Fields Source

All fields come from existing objects -- no new data is created:

| Field | Source |
|-------|--------|
| `schema_version` | Constant `1` |
| `request_id`, `step_id`, `chain_id` | `StepOutcome` |
| `kind`, `status`, `cost_usd`, `tokens_in`, `tokens_out`, `elapsed_ms` | `StepOutcome` |
| `risk_level`, `recommendation` | `AnalysisResult` |
| `degraded` | `DecisionMeta` |
| `degrade_reason` | Computed from `DecisionMeta` + `PolicyConfig` |
| `signals` | `AnalysisResult.signals` (kind + severity only, no detail) |
| `stage_time_ms` | `DecisionMeta.stage_time_ms` + after_step measurements |

### Rules

- **No bulk data**: No snapshot, no full-text logs, no raw events. Observation references only.
- **schema_version**: Always present. Enables future payload evolution.
- **signals**: `{kind, severity}` pairs only. `detail` excluded (can be arbitrarily large).
- **stage_time_ms**: Filtered to `_KNOWN_STAGES`. Unknown keys dropped at emission site.

---

## 2. Store/Emit Stage Timing

### Problem

Current os.py only measures before_step stages (cost_model, planner, arbiter). The after_step stages (collector, analyzer, store, emit) are not timed. Store and emit are the most likely I/O bottlenecks.

### Design

Add `time.monotonic()` measurements around `store.commit()` and `emitter.emit()` in after_step(). Merge before_step and after_step stage times into a single `stage_times` dict for the payload.

```python
def after_step(self, handle, snapshot):
    stage_times = {}

    # 1. Collector (already timed)
    ...
    stage_times["collector"] = elapsed

    # 3. Analyzer (already timed)
    ...
    stage_times["analyzer"] = elapsed

    # 5. Store commit (NEW timing)
    t0 = time.monotonic()
    self._store.commit(...)
    stage_times["store"] = (time.monotonic() - t0) * 1000

    # Merge before_step stage times
    stage_times.update(handle.decision_meta.stage_time_ms)

    # 6. EventEmitter (NEW timing)
    t0 = time.monotonic()
    try:
        self._emitter.emit("step_completed", payload)
    except Exception:
        logger.debug("[VERONICA_OS] EventEmitter error (swallowed)")
    stage_times["emit"] = (time.monotonic() - t0) * 1000
```

---

## 3. degrade_reason

```python
def _degrade_reason(self, handle: StepHandle) -> str | None:
    """Determine why this step was degraded."""
    if not handle.decision_meta.degraded:
        return None
    if handle.policy.fallback_model is not None:
        return "fallback_model"
    for stage, elapsed in handle.decision_meta.stage_time_ms.items():
        budget = self._budgets.get(stage, 0.0)
        if budget > 0 and elapsed > budget:
            return "time_budget"
    return "other"
```

Values:
- `None` -- not degraded
- `"time_budget"` -- a stage exceeded its time budget
- `"fallback_model"` -- policy specified a fallback model
- `"other"` -- degraded but reason not determined (future: `"budget_exceed"`)

---

## 4. MetricsSubscriber

```python
from prometheus_client import Counter, Histogram

_KNOWN_STAGES = frozenset({
    "collector", "analyzer", "cost_model", "planner", "arbiter",
    "store", "emit",
})

class MetricsSubscriber:
    """Prometheus metrics collector. Subscribe to BufferedEmitter.

    Usage:
        emitter = BufferedEmitter()
        metrics = MetricsSubscriber()
        emitter.subscribe("prometheus", metrics)
    """

    def __init__(self, prefix: str = "veronica") -> None:
        self.steps_total = Counter(
            f"{prefix}_steps_total",
            "Total steps executed",
            ["status", "kind", "recommendation", "risk_level"],
        )
        self.step_elapsed = Histogram(
            f"{prefix}_step_elapsed_ms",
            "Step elapsed time in ms",
            ["kind"],
            buckets=[10, 50, 100, 500, 1000, 5000, 10000],
        )
        self.stage_elapsed = Histogram(
            f"{prefix}_stage_elapsed_ms",
            "Pipeline stage elapsed time in ms",
            ["stage"],
            buckets=[1, 5, 10, 20, 50, 100, 250, 500, 1000, 5000],
        )
        self.cost_total = Counter(
            f"{prefix}_cost_microusd_total",
            "Total cost in microusd (1 USD = 1,000,000)",
        )
        self.degrade_total = Counter(
            f"{prefix}_degrade_total",
            "Total degraded steps",
            ["degrade_reason"],
        )

    def __call__(
        self, event_type: str, payload: Mapping[str, Any],
    ) -> None:
        if event_type != "step_completed":
            return

        self.steps_total.labels(
            status=payload.get("status", "unknown"),
            kind=payload.get("kind", "unknown"),
            recommendation=payload.get("recommendation", "unknown"),
            risk_level=payload.get("risk_level", "unknown"),
        ).inc()

        try:
            self.step_elapsed.labels(
                kind=payload.get("kind", "unknown"),
            ).observe(float(payload["elapsed_ms"]))
        except (KeyError, TypeError, ValueError):
            pass

        for stage, ms in payload.get("stage_time_ms", {}).items():
            if stage not in _KNOWN_STAGES:
                continue
            try:
                self.stage_elapsed.labels(stage=stage).observe(float(ms))
            except (TypeError, ValueError):
                pass

        try:
            self.cost_total.inc(round(float(payload["cost_usd"]) * 1_000_000))
        except (KeyError, TypeError, ValueError):
            pass

        if payload.get("degraded"):
            reason = payload.get("degrade_reason", "other")
            if reason:
                self.degrade_total.labels(degrade_reason=reason).inc()
```

### Label Cardinality (Safe)

| Metric | Labels | Max cardinality |
|--------|--------|-----------------|
| `steps_total` | status(4) x kind(3) x recommendation(4) x risk_level(3) | 144 |
| `step_elapsed_ms` | kind(3) | 3 |
| `stage_elapsed_ms` | stage(7) | 7 |
| `cost_microusd_total` | none | 1 |
| `degrade_total` | degrade_reason(3) | 3 |

**Total: max 158 series.** Safe for Prometheus.

### Safety Rules

- **No high-cardinality labels**: No request_id, step_id, chain_id on any metric.
- **Type-safe access**: All `payload` values wrapped in `try/except (KeyError, TypeError, ValueError)`. Missing or malformed values are silently skipped.
- **Unknown stage defense**: `stage not in _KNOWN_STAGES` dropped even if payload contains them (double defense with os.py filter).
- **Integer cost**: `cost_microusd_total` uses `int(round(float * 1_000_000))` -- no floating-point accumulation error.

---

## 5. StructuredLogSubscriber

```python
_MAX_SIGNALS_LOG = 16

class StructuredLogSubscriber:
    """JSON structured log emitter. Subscribe to BufferedEmitter.

    Usage:
        emitter = BufferedEmitter()
        emitter.subscribe("structured_log", StructuredLogSubscriber())

    Note: When using a JSON log formatter on the root logger,
    the message field will contain a JSON string, causing double-encoding.
    In that case, subclass and override to use the `extra` approach:
        self._logger.log(level, "veronica_event", extra={"veronica": record})
    """

    def __init__(
        self,
        logger_name: str = "veronica.events",
        level: int = logging.INFO,
    ) -> None:
        self._logger = logging.getLogger(logger_name)
        self._level = level

    def __call__(
        self, event_type: str, payload: Mapping[str, Any],
    ) -> None:
        if event_type != "step_completed":
            return

        signals = payload.get("signals", [])

        record = {
            "event": event_type,
            "schema_version": payload.get("schema_version", 0),
            "request_id": payload.get("request_id"),
            "step_id": payload.get("step_id"),
            "chain_id": payload.get("chain_id"),
            "kind": payload.get("kind"),
            "status": payload.get("status"),
            "cost_usd": payload.get("cost_usd"),
            "tokens_in": payload.get("tokens_in"),
            "tokens_out": payload.get("tokens_out"),
            "elapsed_ms": payload.get("elapsed_ms"),
            "risk_level": payload.get("risk_level"),
            "recommendation": payload.get("recommendation"),
            "degraded": payload.get("degraded"),
            "degrade_reason": payload.get("degrade_reason"),
            "signals": signals[:_MAX_SIGNALS_LOG],
            "stage_time_ms": payload.get("stage_time_ms", {}),
        }

        self._logger.log(self._level, json.dumps(record, default=str))
```

### Safety Rules

- **signals cap**: Max 16 entries logged. Prevents unbounded growth.
- **default=str**: `json.dumps` fallback for unserializable values.
- **No bulk data**: Only structured fields from payload. No snapshot, no raw events.

---

## 6. pyproject.toml Changes

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "fakeredis[lua]>=2.0",
]
redis = [
    "redis[hiredis]>=5.0",
]
metrics = [
    "prometheus-client>=0.20",
]
```

`prometheus-client` is an optional dependency. MetricsSubscriber imports it lazily in `__init__`.

---

## 7. Tests

### Payload Tests

1. **Payload has all 16 fields**: Run before_step + after_step, capture emitted event, verify all fields present.
2. **schema_version is 1**: Verify constant.
3. **signals contain kind+severity**: Trigger a halt, verify signals format.
4. **stage_time_ms filtered**: Inject unknown stage key, verify it's dropped from payload.
5. **degrade_reason time_budget**: Force TimeBudgetExceeded, verify reason.
6. **degrade_reason None when not degraded**: Normal step, verify None.
7. **store/emit timing present**: Verify stage_time_ms contains "store" key.

### MetricsSubscriber Tests

8. **steps_total increments**: Send step_completed, verify counter.
9. **cost_microusd_total accumulates**: Send cost_usd=0.01, verify inc(10_000).
10. **stage_elapsed observes known stages**: Send stage_time_ms with known keys.
11. **unknown stage dropped**: Send stage_time_ms with "bogus" key, verify no label created.
12. **missing field does not crash**: Send payload with missing elapsed_ms, no exception.
13. **degrade_total only on degraded**: Send degraded=False, verify no increment.

### StructuredLogSubscriber Tests

14. **JSON log emitted**: Capture log output, parse JSON, verify fields.
15. **signals capped at 16**: Send 20 signals, verify log has 16.
16. **missing field safe**: Send empty payload, no exception.

### Integration Tests

17. **Full pipeline with both subscribers**: VeronicaOS + BufferedEmitter + MetricsSubscriber + StructuredLogSubscriber. Run 3 steps, verify metrics and logs.

---

## Files Summary

| File | Change |
|------|--------|
| `src/veronica/os.py:271-281` | Expand emit payload (5 -> 16 fields), add store/emit timing, add `_degrade_reason()` |
| `src/veronica/metrics_subscriber.py` | New: MetricsSubscriber class |
| `src/veronica/structured_log_subscriber.py` | New: StructuredLogSubscriber class |
| `src/veronica/__init__.py` | Export MetricsSubscriber, StructuredLogSubscriber |
| `pyproject.toml` | Add `metrics` optional dep (prometheus-client) |
| `tests/test_observability.py` | New: 17 tests (payload + metrics + log + integration) |

**Protocol changes:** None.
**os.py pipeline structure:** Unchanged (emit payload expanded, timing added).
