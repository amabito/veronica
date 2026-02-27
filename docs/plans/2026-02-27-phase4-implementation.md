# Phase 4: Observability Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add Prometheus metrics and structured JSON logging to VeronicaOS via BufferedEmitter subscribers, with expanded emit payload.

**Architecture:** Expand the `step_completed` event payload in os.py to 16 fields. Two new subscriber classes (MetricsSubscriber, StructuredLogSubscriber) consume events via BufferedEmitter.subscribe(). No Protocol changes.

**Tech Stack:** Python 3.10+, prometheus-client, pytest, standard logging

**Design doc:** `docs/plans/2026-02-27-phase4-observability-design.md`

---

## Dependency Graph

```
Task 1 (pyproject.toml deps)
     |
Task 2 (os.py payload + timing + _degrade_reason)
     |
     +------------------+
     |                  |
Task 3 (MetricsSub)  Task 4 (LogSub)
     |                  |
     +------------------+
     |
Task 5 (payload tests)
     |
Task 6 (MetricsSubscriber tests)
     |
Task 7 (StructuredLogSubscriber tests)
     |
Task 8 (integration tests)
     |
Task 9 (__init__.py + version bump)
     |
Task 10 (final verification + tag)
```

**Parallel opportunities:** Tasks 3 and 4 are independent.

---

### Task 1: Add prometheus-client Optional Dependency

**Files:**
- Modify: `pyproject.toml:37-44`

**Step 1: Add metrics optional dep**

In `pyproject.toml`, add the `metrics` optional dependency group after the `redis` group:

```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "fakeredis[lua]>=2.0",
    "prometheus-client>=0.20",
]
redis = [
    "redis[hiredis]>=5.0",
]
metrics = [
    "prometheus-client>=0.20",
]
```

Note: `prometheus-client` is added to both `dev` (for testing) and `metrics` (for production use).

**Step 2: Install dev dependencies**

Run: `cd D:/work/Projects/veronica && pip install -e ".[dev]"`
Expected: prometheus-client installed successfully

**Step 3: Verify prometheus-client works**

Run: `python -c "from prometheus_client import Counter, Histogram, Gauge; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add prometheus-client optional dependency"
```

---

### Task 2: Expand os.py Payload + Store/Emit Timing + _degrade_reason

**Files:**
- Modify: `src/veronica/os.py:250-282` (after_step method)

**Step 1: Add `_KNOWN_STAGES` constant and `_degrade_reason` method**

Add after line 49 (after `_DEFAULT_REQUEST_BUDGET_USD`):

```python
_KNOWN_STAGES = frozenset({
    "collector", "analyzer", "cost_model", "planner", "arbiter",
    "store", "emit",
})
```

Add as a method of `VeronicaOS` class, after `after_step()` (at end of class):

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

**Step 2: Add store timing around commit**

Replace lines 250-261 (the store commit section) with:

```python
        # 5. Store commit (atomic, timed)
        if hasattr(self._store, "set_budget_context"):
            remaining = self._request_budget_usd - self._total_spent_usd
            self._store.set_budget_context(
                ceiling_usd=self._request_budget_usd,
                remaining_usd=remaining,
            )
        t0 = time.monotonic()
        self._store.commit(
            outcome, analysis, handle.cost,
            handle.desired, handle.policy, handle.decision_meta,
        )
        stage_times["store"] = (time.monotonic() - t0) * 1000
```

**Step 3: Replace the emit section (lines 271-281) with expanded payload + emit timing**

Replace the entire `# 6. EventEmitter` section with:

```python
        # 5b. Settle reservation with actual cost
        if hasattr(self._arbiter, "settle"):
            self._arbiter.settle(
                request_id=handle.intent.request_id,
                step_id=handle.intent.step_id,
                actual_cost_usd=outcome.cost_usd,
            )

        # 6. Merge before_step + after_step stage times
        all_stage_times = dict(handle.decision_meta.stage_time_ms)
        all_stage_times.update(stage_times)

        # 7. EventEmitter (fire-and-forget, timed)
        payload: dict[str, Any] = {
            "schema_version": 1,
            "request_id": outcome.request_id,
            "step_id": outcome.step_id,
            "chain_id": outcome.chain_id,
            "kind": outcome.kind,
            "status": outcome.status,
            "cost_usd": outcome.cost_usd,
            "tokens_in": outcome.tokens_in,
            "tokens_out": outcome.tokens_out,
            "elapsed_ms": outcome.elapsed_ms,
            "risk_level": analysis.risk_level,
            "recommendation": analysis.recommendation,
            "degraded": handle.decision_meta.degraded,
            "degrade_reason": self._degrade_reason(handle),
            "signals": [
                {"kind": s.kind, "severity": s.severity}
                for s in analysis.signals
            ],
            "stage_time_ms": {
                k: v for k, v in all_stage_times.items()
                if k in _KNOWN_STAGES
            },
        }
        t0 = time.monotonic()
        try:
            self._emitter.emit("step_completed", payload)
        except Exception:
            logger.debug("[VERONICA_OS] EventEmitter error (swallowed)")
        stage_times["emit"] = (time.monotonic() - t0) * 1000
```

**Step 4: Run existing tests to verify no regression**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/ -v --tb=short`
Expected: All existing tests PASS

**Step 5: Commit**

```bash
git add src/veronica/os.py
git commit -m "feat: expand emit payload to 16 fields, add store/emit timing"
```

---

### Task 3: MetricsSubscriber Implementation

**Files:**
- Create: `src/veronica/metrics_subscriber.py`

**Step 1: Write MetricsSubscriber**

```python
# src/veronica/metrics_subscriber.py
"""VERONICA OS observability -- Prometheus metrics subscriber."""
from __future__ import annotations

from typing import Any, Mapping

_KNOWN_STAGES = frozenset({
    "collector", "analyzer", "cost_model", "planner", "arbiter",
    "store", "emit",
})

_MICRO = 1_000_000


class MetricsSubscriber:
    """Prometheus metrics collector. Subscribe to BufferedEmitter.

    Usage::

        emitter = BufferedEmitter()
        metrics = MetricsSubscriber()
        emitter.subscribe("prometheus", metrics)

    All payload values are accessed defensively via .get() with
    try/except guards. Missing or malformed values are silently skipped.
    """

    def __init__(self, prefix: str = "veronica") -> None:
        from prometheus_client import Counter, Histogram

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
        """Callback for BufferedEmitter.subscribe()."""
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
            self.cost_total.inc(round(float(payload["cost_usd"]) * _MICRO))
        except (KeyError, TypeError, ValueError):
            pass

        if payload.get("degraded"):
            reason = payload.get("degrade_reason", "other")
            if reason:
                self.degrade_total.labels(degrade_reason=reason).inc()
```

**Step 2: Verify import works**

Run: `cd D:/work/Projects/veronica && python -c "from veronica.metrics_subscriber import MetricsSubscriber; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add src/veronica/metrics_subscriber.py
git commit -m "feat: add MetricsSubscriber (Prometheus counters/histograms)"
```

---

### Task 4: StructuredLogSubscriber Implementation

**Files:**
- Create: `src/veronica/structured_log_subscriber.py`

**Step 1: Write StructuredLogSubscriber**

```python
# src/veronica/structured_log_subscriber.py
"""VERONICA OS observability -- structured JSON log subscriber."""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping

_MAX_SIGNALS_LOG = 16


class StructuredLogSubscriber:
    """JSON structured log emitter. Subscribe to BufferedEmitter.

    Usage::

        emitter = BufferedEmitter()
        emitter.subscribe("structured_log", StructuredLogSubscriber())

    Note: When using a JSON log formatter on the root logger,
    the message field will contain a JSON string, causing double-encoding.
    In that case, subclass and override to use the ``extra`` approach::

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
        """Callback for BufferedEmitter.subscribe()."""
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

**Step 2: Verify import works**

Run: `cd D:/work/Projects/veronica && python -c "from veronica.structured_log_subscriber import StructuredLogSubscriber; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add src/veronica/structured_log_subscriber.py
git commit -m "feat: add StructuredLogSubscriber (JSON structured logging)"
```

---

### Task 5: Payload Tests (7 tests)

**Files:**
- Create: `tests/test_observability.py`

**Step 1: Write payload tests**

```python
# tests/test_observability.py
"""Tests for Phase 4 observability -- payload, metrics, structured logging."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import pytest

from veronica_core.containment.execution_context import ContextSnapshot, NodeRecord

from veronica.adaptive_planner import AdaptivePlanner
from veronica.buffered_emitter import BufferedEmitter
from veronica.file_store import FileStore
from veronica.history_analyzer import HistoryAnalyzer
from veronica.os import VeronicaOS
from veronica.proportional_arbiter import ProportionalArbiter
from veronica.regression_cost_model import RegressionCostModel
from veronica.types import StepIntent


def _intent(
    step_id: str = "s1",
    request_id: str = "r1",
    chain_id: str = "c1",
) -> StepIntent:
    return StepIntent(
        step_id=step_id, request_id=request_id, chain_id=chain_id,
        kind="llm", model="gpt-4", tool_name=None,
        timeout_ms=30_000, metadata={},
    )


def _snapshot(
    chain_id: str = "c1",
    cost: float = 0.01,
    status: str = "ok",
) -> ContextSnapshot:
    node = NodeRecord(
        node_id="n1", parent_id=None, kind="llm",
        operation_name="test_op",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc),
        status=status, cost_usd=cost, retries_used=0,
    )
    return ContextSnapshot(
        chain_id=chain_id, request_id="r1", step_count=1,
        cost_usd_accumulated=cost, retries_used=0,
        aborted=False, abort_reason=None,
        elapsed_ms=100.0, nodes=[node], events=[],
    )


def _make_os(tmp_path, emitter=None):
    """Create a VeronicaOS with Phase 2 components."""
    if emitter is None:
        emitter = BufferedEmitter()
    return VeronicaOS(
        analyzer=HistoryAnalyzer(),
        cost_model=RegressionCostModel(),
        planner=AdaptivePlanner(),
        arbiter=ProportionalArbiter(),
        emitter=emitter,
        store=FileStore(data_dir=str(tmp_path)),
    ), emitter


class TestPayload:
    def test_payload_has_all_16_fields(self, tmp_path) -> None:
        """Emitted payload contains all 16 required fields."""
        vos, emitter = _make_os(tmp_path)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        events = emitter.snapshot()
        assert len(events) == 1
        event_type, payload = events[0]
        assert event_type == "step_completed"

        required = {
            "schema_version", "request_id", "step_id", "chain_id",
            "kind", "status", "cost_usd", "tokens_in", "tokens_out",
            "elapsed_ms", "risk_level", "recommendation", "degraded",
            "degrade_reason", "signals", "stage_time_ms",
        }
        assert required.issubset(payload.keys())

    def test_schema_version_is_1(self, tmp_path) -> None:
        """schema_version is always 1."""
        vos, emitter = _make_os(tmp_path)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        _, payload = emitter.snapshot()[0]
        assert payload["schema_version"] == 1

    def test_signals_contain_kind_and_severity(self, tmp_path) -> None:
        """Signals are dicts with kind and severity."""
        vos, emitter = _make_os(tmp_path)
        # Trigger a halt to generate signals
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot(status="halted"))

        _, payload = emitter.snapshot()[0]
        signals = payload["signals"]
        if signals:  # halt should generate at least one signal
            for sig in signals:
                assert "kind" in sig
                assert "severity" in sig

    def test_stage_time_ms_filtered(self, tmp_path) -> None:
        """stage_time_ms only contains known stage names."""
        vos, emitter = _make_os(tmp_path)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        _, payload = emitter.snapshot()[0]
        known = {
            "collector", "analyzer", "cost_model", "planner",
            "arbiter", "store", "emit",
        }
        for key in payload["stage_time_ms"]:
            assert key in known

    def test_store_timing_present(self, tmp_path) -> None:
        """stage_time_ms contains 'store' key."""
        vos, emitter = _make_os(tmp_path)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        _, payload = emitter.snapshot()[0]
        assert "store" in payload["stage_time_ms"]
        assert payload["stage_time_ms"]["store"] >= 0

    def test_degrade_reason_none_when_not_degraded(self, tmp_path) -> None:
        """Normal step has degrade_reason=None."""
        vos, emitter = _make_os(tmp_path)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        _, payload = emitter.snapshot()[0]
        assert payload["degraded"] is False
        assert payload["degrade_reason"] is None

    def test_identity_fields_match_intent(self, tmp_path) -> None:
        """request_id, step_id, chain_id match the original intent."""
        vos, emitter = _make_os(tmp_path)
        handle = vos.before_step(_intent(
            step_id="s42", request_id="r99", chain_id="c7",
        ))
        vos.after_step(handle, _snapshot(chain_id="c7"))

        _, payload = emitter.snapshot()[0]
        assert payload["request_id"] == "r99"
        assert payload["step_id"] == "s42"
        assert payload["chain_id"] == "c7"
```

**Step 2: Run tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_observability.py -v`
Expected: 7 tests PASS

**Step 3: Commit**

```bash
git add tests/test_observability.py
git commit -m "test: add payload tests for Phase 4 observability (7 tests)"
```

---

### Task 6: MetricsSubscriber Tests (6 tests)

**Files:**
- Modify: `tests/test_observability.py` (append)

**Step 1: Append MetricsSubscriber tests**

```python
from prometheus_client import CollectorRegistry
from veronica.metrics_subscriber import MetricsSubscriber


def _metrics(prefix="test") -> tuple[MetricsSubscriber, CollectorRegistry]:
    """MetricsSubscriber with isolated registry."""
    registry = CollectorRegistry()
    ms = MetricsSubscriber.__new__(MetricsSubscriber)
    from prometheus_client import Counter, Histogram

    ms.steps_total = Counter(
        f"{prefix}_steps_total", "test",
        ["status", "kind", "recommendation", "risk_level"],
        registry=registry,
    )
    ms.step_elapsed = Histogram(
        f"{prefix}_step_elapsed_ms", "test", ["kind"],
        buckets=[10, 50, 100, 500, 1000, 5000, 10000],
        registry=registry,
    )
    ms.stage_elapsed = Histogram(
        f"{prefix}_stage_elapsed_ms", "test", ["stage"],
        buckets=[1, 5, 10, 20, 50, 100, 250, 500, 1000, 5000],
        registry=registry,
    )
    ms.cost_total = Counter(
        f"{prefix}_cost_microusd_total", "test",
        registry=registry,
    )
    ms.degrade_total = Counter(
        f"{prefix}_degrade_total", "test",
        ["degrade_reason"],
        registry=registry,
    )
    return ms, registry


def _sample_payload(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "request_id": "r1", "step_id": "s1", "chain_id": "c1",
        "kind": "llm", "status": "ok",
        "cost_usd": 0.01, "tokens_in": 100, "tokens_out": 50,
        "elapsed_ms": 150.0,
        "risk_level": "nominal", "recommendation": "continue",
        "degraded": False, "degrade_reason": None,
        "signals": [], "stage_time_ms": {"analyzer": 5.0, "planner": 10.0},
    }
    base.update(overrides)
    return base


class TestMetricsSubscriber:
    def test_steps_total_increments(self) -> None:
        """step_completed increments steps_total."""
        ms, reg = _metrics("t1")
        ms("step_completed", _sample_payload())

        val = reg.get_sample_value(
            "t1_steps_total",
            {"status": "ok", "kind": "llm",
             "recommendation": "continue", "risk_level": "nominal"},
        )
        assert val == 1.0

    def test_cost_microusd_accumulates(self) -> None:
        """cost_usd=0.01 -> inc(10_000)."""
        ms, reg = _metrics("t2")
        ms("step_completed", _sample_payload(cost_usd=0.01))

        val = reg.get_sample_value("t2_cost_microusd_total")
        assert val == 10_000.0

    def test_stage_elapsed_observes_known(self) -> None:
        """Known stages are observed in histogram."""
        ms, reg = _metrics("t3")
        ms("step_completed", _sample_payload(
            stage_time_ms={"analyzer": 5.0, "planner": 10.0},
        ))

        count = reg.get_sample_value(
            "t3_stage_elapsed_ms_count", {"stage": "analyzer"},
        )
        assert count == 1.0

    def test_unknown_stage_dropped(self) -> None:
        """Unknown stage names are not added as labels."""
        ms, reg = _metrics("t4")
        ms("step_completed", _sample_payload(
            stage_time_ms={"bogus_stage": 99.0},
        ))

        count = reg.get_sample_value(
            "t4_stage_elapsed_ms_count", {"stage": "bogus_stage"},
        )
        assert count is None  # not created

    def test_missing_field_no_crash(self) -> None:
        """Payload with missing elapsed_ms does not raise."""
        ms, _ = _metrics("t5")
        payload = _sample_payload()
        del payload["elapsed_ms"]
        ms("step_completed", payload)  # should not raise

    def test_degrade_only_on_degraded(self) -> None:
        """degrade_total only increments when degraded=True."""
        ms, reg = _metrics("t6")
        ms("step_completed", _sample_payload(degraded=False, degrade_reason=None))

        val = reg.get_sample_value(
            "t6_degrade_total", {"degrade_reason": "other"},
        )
        assert val is None  # not incremented
```

**Step 2: Run tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_observability.py::TestMetricsSubscriber -v`
Expected: 6 tests PASS

**Step 3: Commit**

```bash
git add tests/test_observability.py
git commit -m "test: add MetricsSubscriber tests (6 tests)"
```

---

### Task 7: StructuredLogSubscriber Tests (3 tests)

**Files:**
- Modify: `tests/test_observability.py` (append)

**Step 1: Append StructuredLogSubscriber tests**

```python
from veronica.structured_log_subscriber import StructuredLogSubscriber


class TestStructuredLogSubscriber:
    def test_json_log_emitted(self, caplog) -> None:
        """step_completed emits valid JSON log."""
        sub = StructuredLogSubscriber(logger_name="test.events")
        with caplog.at_level(logging.INFO, logger="test.events"):
            sub("step_completed", _sample_payload())

        assert len(caplog.records) == 1
        record = json.loads(caplog.records[0].message)
        assert record["event"] == "step_completed"
        assert record["schema_version"] == 1
        assert record["status"] == "ok"

    def test_signals_capped_at_16(self, caplog) -> None:
        """More than 16 signals are truncated in log."""
        signals = [{"kind": f"sig_{i}", "severity": "info"} for i in range(20)]
        sub = StructuredLogSubscriber(logger_name="test.cap")
        with caplog.at_level(logging.INFO, logger="test.cap"):
            sub("step_completed", _sample_payload(signals=signals))

        record = json.loads(caplog.records[0].message)
        assert len(record["signals"]) == 16

    def test_empty_payload_no_crash(self, caplog) -> None:
        """Empty payload does not raise."""
        sub = StructuredLogSubscriber(logger_name="test.empty")
        with caplog.at_level(logging.INFO, logger="test.empty"):
            sub("step_completed", {})

        assert len(caplog.records) == 1
        record = json.loads(caplog.records[0].message)
        assert record["event"] == "step_completed"
```

**Step 2: Run tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_observability.py::TestStructuredLogSubscriber -v`
Expected: 3 tests PASS

**Step 3: Commit**

```bash
git add tests/test_observability.py
git commit -m "test: add StructuredLogSubscriber tests (3 tests)"
```

---

### Task 8: Integration Test (1 test)

**Files:**
- Modify: `tests/test_observability.py` (append)

**Step 1: Append integration test**

```python
class TestObservabilityIntegration:
    def test_full_pipeline_with_subscribers(self, tmp_path, caplog) -> None:
        """VeronicaOS + BufferedEmitter + both subscribers."""
        emitter = BufferedEmitter()
        ms, reg = _metrics("int")
        log_sub = StructuredLogSubscriber(logger_name="test.int")

        emitter.subscribe("prometheus", ms)
        emitter.subscribe("structured_log", log_sub)

        vos = VeronicaOS(
            analyzer=HistoryAnalyzer(),
            cost_model=RegressionCostModel(),
            planner=AdaptivePlanner(),
            arbiter=ProportionalArbiter(),
            emitter=emitter,
            store=FileStore(data_dir=str(tmp_path)),
        )

        with caplog.at_level(logging.INFO, logger="test.int"):
            for i in range(3):
                handle = vos.before_step(_intent(step_id=f"s{i}"))
                vos.after_step(handle, _snapshot(cost=0.01))

        # Verify metrics
        val = reg.get_sample_value(
            "int_steps_total",
            {"status": "ok", "kind": "llm",
             "recommendation": "continue", "risk_level": "nominal"},
        )
        assert val == 3.0

        cost = reg.get_sample_value("int_cost_microusd_total")
        assert cost == 30_000.0  # 3 * 0.01 * 1_000_000

        # Verify logs
        assert len(caplog.records) == 3
        for rec in caplog.records:
            parsed = json.loads(rec.message)
            assert parsed["schema_version"] == 1
```

**Step 2: Run all observability tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_observability.py -v`
Expected: 17 tests PASS (7 payload + 6 metrics + 3 log + 1 integration)

**Step 3: Commit**

```bash
git add tests/test_observability.py
git commit -m "test: add observability integration test"
```

---

### Task 9: Export + Version Bump

**Files:**
- Modify: `src/veronica/__init__.py`
- Modify: `pyproject.toml:7`

**Step 1: Add imports and exports to __init__.py**

Add imports after the RedisArbiter import (after line 10):

```python
from veronica.metrics_subscriber import MetricsSubscriber
from veronica.structured_log_subscriber import StructuredLogSubscriber
```

Add to `__all__` after `"RedisArbiter"`:

```python
    # Phase 4 components
    "MetricsSubscriber",
    "StructuredLogSubscriber",
```

**Step 2: Update version to 0.4.0**

In `src/veronica/__init__.py`, change `__version__ = "0.3.0"` to `__version__ = "0.4.0"`.

In `pyproject.toml`, change `version = "0.3.0"` to `version = "0.4.0"`.

**Step 3: Verify import**

Run: `cd D:/work/Projects/veronica && python -c "import veronica; print(veronica.__version__, hasattr(veronica, 'MetricsSubscriber'), hasattr(veronica, 'StructuredLogSubscriber'))"`
Expected: `0.4.0 True True`

**Step 4: Commit**

```bash
git add src/veronica/__init__.py pyproject.toml
git commit -m "chore: export observability subscribers, bump version to 0.4.0"
```

---

### Task 10: Final Verification + Tag

**Files:** None (verification only)

**Step 1: Run full test suite**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 2: Run with coverage**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/ --cov=veronica --cov-report=term-missing`
Expected: Coverage >= 80%

**Step 3: Verify backward compatibility**

Run:
```bash
python -c "
from veronica import VeronicaOS
vos = VeronicaOS()
from veronica.types import StepIntent
intent = StepIntent(step_id='s1', request_id='r1', chain_id='c1', kind='llm', model='gpt-4', tool_name=None, timeout_ms=30000, metadata={})
handle = vos.before_step(intent)
print('Default OS works:', handle.policy.ceiling_usd > 0)
"
```
Expected: `Default OS works: True`

**Step 4: Tag and push**

```bash
git tag v0.4.0
git push origin main --tags
```

---

## Summary

| Task | Description | Files | Tests |
|------|-------------|-------|-------|
| 1 | prometheus-client dep | `pyproject.toml` (mod) | -- |
| 2 | os.py payload + timing + degrade_reason | `os.py` (mod) | regression |
| 3 | MetricsSubscriber | `metrics_subscriber.py` (new) | -- |
| 4 | StructuredLogSubscriber | `structured_log_subscriber.py` (new) | -- |
| 5 | Payload tests (7) | `test_observability.py` (new) | 7 |
| 6 | MetricsSubscriber tests (6) | `test_observability.py` (mod) | 6 |
| 7 | StructuredLogSubscriber tests (3) | `test_observability.py` (mod) | 3 |
| 8 | Integration test (1) | `test_observability.py` (mod) | 1 |
| 9 | Export + version | `__init__.py`, `pyproject.toml` (mod) | -- |
| 10 | Final verification + tag | -- | full suite |

**Total new tests:** 17
**Total new source files:** 2 (`metrics_subscriber.py`, `structured_log_subscriber.py`)
**Total modified files:** 3 (`os.py`, `__init__.py`, `pyproject.toml`)
**Protocol changes:** None
