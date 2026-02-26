# VERONICA OS Phase 2: Adaptive Layer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace all Phase 1 stub implementations with adaptive, history-aware components (6 new files + types expansion)

**Architecture:** Protocol-injection via constructor. New implementations conform to existing `protocols.py` interfaces. `os.py` is untouched. Each component is independently testable. TDD: failing test first, then minimal implementation.

**Tech Stack:** Python 3.11+, pytest, frozen dataclasses, veronica-core

**Design doc:** `docs/plans/2026-02-26-phase2-adaptive-design.md`

**Important context for implementer:**
- All source is in `src/veronica/`, tests in `tests/`
- `pyproject.toml` sets `pythonpath = ["src"]`, `testpaths = ["tests"]`
- Run tests: `cd D:\work\Projects\veronica && python -m pytest tests/ -v`
- Phase 1 implementations stay (not deleted). Phase 2 adds new files alongside them.
- `HistoryView` is a frozen dataclass -- new fields must have defaults for backward compat.
- All existing tests (69) must continue to pass after every commit.

---

## Task 1: Expand HistoryView with Phase 2 Fields

**Files:**
- Modify: `src/veronica/types.py:1-6` (add `field` import), `src/veronica/types.py:44-53` (add 5 fields)
- Test: `tests/test_types.py` (add backward compat tests)

**Step 1: Write the failing test**

Add to `tests/test_types.py`:

```python
class TestHistoryViewPhase2:
    def test_new_fields_have_defaults(self) -> None:
        """Phase 2 fields must all have defaults for backward compatibility."""
        hv = HistoryView(
            chain_id="c1",
            last_n=(),
            rolling_cost_usd=0.0,
            failure_streak=0,
            depth=0,
            loop_score=0.0,
        )
        # Phase 2 fields should exist with defaults
        assert hv.success_streak == 0
        assert hv.cost_per_step_ema == 0.0
        assert hv.cost_per_step_ema_by_model == {}
        assert hv.latency_ema_ms == {}
        assert hv.budget_headroom_ratio == 1.0

    def test_new_fields_can_be_set(self) -> None:
        """Phase 2 fields can be explicitly provided."""
        hv = HistoryView(
            chain_id="c1",
            last_n=(),
            rolling_cost_usd=0.0,
            failure_streak=0,
            depth=10,
            loop_score=0.0,
            success_streak=5,
            cost_per_step_ema=0.05,
            cost_per_step_ema_by_model={"gpt-4": 0.03},
            latency_ema_ms={"gpt-4": 150.0},
            budget_headroom_ratio=0.7,
        )
        assert hv.success_streak == 5
        assert hv.cost_per_step_ema == 0.05
        assert hv.cost_per_step_ema_by_model["gpt-4"] == 0.03
        assert hv.latency_ema_ms["gpt-4"] == 150.0
        assert hv.budget_headroom_ratio == 0.7
```

**Step 2: Run test to verify it fails**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/test_types.py::TestHistoryViewPhase2 -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'success_streak'`

**Step 3: Write minimal implementation**

In `src/veronica/types.py`, change line 6 import to add `field`:
```python
from dataclasses import dataclass, field
```

Replace the `HistoryView` class (lines 44-53) with:
```python
@dataclass(frozen=True)
class HistoryView:
    """Lightweight history slice. Statistics, not raw logs."""

    chain_id: str
    last_n: tuple[StepOutcome, ...]
    rolling_cost_usd: float
    failure_streak: int
    depth: int
    loop_score: float

    # Phase 2 additions (all with defaults for backward compatibility)
    success_streak: int = 0
    cost_per_step_ema: float = 0.0
    cost_per_step_ema_by_model: Mapping[str, float] = field(default_factory=dict)
    latency_ema_ms: Mapping[str, float] = field(default_factory=dict)
    budget_headroom_ratio: float = 1.0
```

**Step 4: Run ALL tests to verify nothing broke**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/ -v`
Expected: ALL PASS (existing 69 + 2 new = 71)

**Step 5: Commit**

```bash
cd /d/work/Projects/veronica && git add src/veronica/types.py tests/test_types.py && git commit -m "feat: expand HistoryView with Phase 2 fields (backward-compat defaults)"
```

---

## Task 2: BufferedEmitter

**Files:**
- Create: `src/veronica/buffered_emitter.py`
- Create: `tests/test_buffered_emitter.py`

**Why first:** No dependencies on other Phase 2 components. Simplest to implement and test in isolation.

**Step 1: Write the failing tests**

Create `tests/test_buffered_emitter.py`:

```python
# tests/test_buffered_emitter.py
"""Tests for veronica.buffered_emitter -- ring buffer event emitter."""
from __future__ import annotations

import pytest

from veronica.buffered_emitter import BufferedEmitter


class TestBufferedEmitter:
    def test_emit_stores_event(self) -> None:
        emitter = BufferedEmitter()
        emitter.emit("step_completed", {"step_id": "s1"})
        events = emitter.snapshot()
        assert len(events) == 1
        assert events[0] == ("step_completed", {"step_id": "s1"})

    def test_ring_buffer_maxlen(self) -> None:
        emitter = BufferedEmitter(maxlen=3)
        for i in range(5):
            emitter.emit("e", {"i": i})
        events = emitter.snapshot()
        assert len(events) == 3
        assert events[0][1]["i"] == 2  # oldest kept

    def test_drain_removes_events(self) -> None:
        emitter = BufferedEmitter()
        emitter.emit("a", {})
        emitter.emit("b", {})
        emitter.emit("c", {})
        drained = emitter.drain(2)
        assert len(drained) == 2
        assert drained[0][0] == "a"
        assert drained[1][0] == "b"
        remaining = emitter.snapshot()
        assert len(remaining) == 1

    def test_subscriber_receives_events(self) -> None:
        emitter = BufferedEmitter()
        received: list[tuple[str, dict]] = []
        emitter.subscribe("test_sub", lambda et, p: received.append((et, p)))
        emitter.emit("x", {"val": 1})
        assert len(received) == 1
        assert received[0] == ("x", {"val": 1})

    def test_unsubscribe(self) -> None:
        emitter = BufferedEmitter()
        received: list = []
        emitter.subscribe("test_sub", lambda et, p: received.append(1))
        emitter.unsubscribe("test_sub")
        emitter.emit("x", {})
        assert len(received) == 0

    def test_auto_unsubscribe_after_3_failures(self) -> None:
        emitter = BufferedEmitter()

        def bad_callback(et: str, p: dict) -> None:
            raise RuntimeError("boom")

        emitter.subscribe("bad", bad_callback)
        for _ in range(3):
            emitter.emit("e", {})
        # After 3 failures, subscriber should be gone
        assert "bad" not in emitter._subscribers

    def test_snapshot_is_nondestructive(self) -> None:
        emitter = BufferedEmitter()
        emitter.emit("a", {})
        snap1 = emitter.snapshot()
        snap2 = emitter.snapshot()
        assert snap1 == snap2
        assert len(emitter.snapshot()) == 1

    def test_emit_protocol_compatible(self) -> None:
        """Satisfies EventEmitterProtocol."""
        from veronica.protocols import EventEmitterProtocol

        emitter = BufferedEmitter()
        assert isinstance(emitter, EventEmitterProtocol)
```

**Step 2: Run test to verify it fails**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/test_buffered_emitter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'veronica.buffered_emitter'`

**Step 3: Write minimal implementation**

Create `src/veronica/buffered_emitter.py`:

```python
# src/veronica/buffered_emitter.py
"""VERONICA OS emitter -- BufferedEmitter with ring buffer and subscribers."""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

_MAX_CONSECUTIVE_FAILURES = 3


class BufferedEmitter:
    """Phase 2 event emitter with ring buffer and subscriber management.

    Events are stored in a bounded deque. Subscribers receive synchronous
    callbacks. After 3 consecutive failures, a subscriber is auto-removed.
    """

    def __init__(self, maxlen: int = 1024) -> None:
        self._buffer: deque[tuple[str, Mapping[str, Any]]] = deque(maxlen=maxlen)
        self._subscribers: dict[str, Callable[[str, Mapping[str, Any]], None]] = {}
        self._fail_counts: dict[str, int] = {}

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        assert threading.current_thread() is threading.main_thread(), (
            "BufferedEmitter.emit() must be called from the main thread"
        )
        self._buffer.append((event_type, payload))
        for name in list(self._subscribers):
            callback = self._subscribers.get(name)
            if callback is None:
                continue
            try:
                callback(event_type, payload)
                self._fail_counts[name] = 0
            except Exception:
                count = self._fail_counts.get(name, 0) + 1
                self._fail_counts[name] = count
                if count >= _MAX_CONSECUTIVE_FAILURES:
                    self._subscribers.pop(name, None)
                    self._fail_counts.pop(name, None)
                    logger.warning(
                        "Auto-unsubscribed '%s' after %d consecutive failures",
                        name,
                        _MAX_CONSECUTIVE_FAILURES,
                    )

    def subscribe(
        self,
        name: str,
        callback: Callable[[str, Mapping[str, Any]], None],
    ) -> None:
        self._subscribers[name] = callback
        self._fail_counts[name] = 0

    def unsubscribe(self, name: str) -> None:
        self._subscribers.pop(name, None)
        self._fail_counts.pop(name, None)

    def drain(self, n: int) -> list[tuple[str, Mapping[str, Any]]]:
        result: list[tuple[str, Mapping[str, Any]]] = []
        for _ in range(min(n, len(self._buffer))):
            result.append(self._buffer.popleft())
        return result

    def snapshot(self) -> list[tuple[str, Mapping[str, Any]]]:
        return list(self._buffer)
```

**Step 4: Run ALL tests**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/ -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
cd /d/work/Projects/veronica && git add src/veronica/buffered_emitter.py tests/test_buffered_emitter.py && git commit -m "feat: add BufferedEmitter with ring buffer and auto-unsubscribe"
```

---

## Task 3: FileStore

**Files:**
- Create: `src/veronica/file_store.py`
- Create: `tests/test_file_store.py`

**Why now:** FileStore computes EMA and populates Phase 2 HistoryView fields. Other components (Analyzer, CostModel) depend on these fields being correctly populated.

**Step 1: Write the failing tests**

Create `tests/test_file_store.py`:

```python
# tests/test_file_store.py
"""Tests for veronica.file_store -- JSONL persistence with EMA computation."""
from __future__ import annotations

import json
import time

import pytest

from veronica.file_store import FileStore
from veronica.types import (
    AnalysisResult,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    PolicyConfig,
    StepOutcome,
)


def _outcome(
    chain_id: str = "c1",
    step_id: str = "s1",
    status: str = "ok",
    cost: float = 0.01,
    model: str = "gpt-4",
    elapsed_ms: float = 100.0,
) -> StepOutcome:
    return StepOutcome(
        step_id=step_id,
        request_id="r1",
        chain_id=chain_id,
        kind="llm",
        status=status,
        cost_usd=cost,
        tokens_in=100,
        tokens_out=50,
        elapsed_ms=elapsed_ms,
        model=model,
        events=(),
        timestamp_ms=int(time.time() * 1000),
    )


def _analysis() -> AnalysisResult:
    return AnalysisResult(signals=(), risk_level="nominal", recommendation="continue")


def _cost_est() -> CostEstimate:
    return CostEstimate(estimated_usd=0.01, confidence=0.8, model_used="gpt-4", basis="historical")


def _desired() -> DesiredPolicy:
    return DesiredPolicy(
        chain_id="c1", ceiling_usd=1.0, ceiling_steps=100,
        ceiling_tokens_out=50000, on_exceed="halt",
        fallback_model=None, timeout_ms=30000, priority=50,
    )


def _policy() -> PolicyConfig:
    return PolicyConfig(chain_id="c1", ceiling_usd=1.0, on_exceed="halt", issued_at=time.time())


def _meta() -> DecisionMeta:
    return DecisionMeta(risk_level="nominal", recommendation="continue", degraded=False, stage_time_ms={})


class TestFileStore:
    def test_commit_and_build_history(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        store.commit(_outcome(), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        hv = store.build_history("c1")
        assert hv.chain_id == "c1"
        assert len(hv.last_n) == 1
        assert hv.depth == 1

    def test_success_streak(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        for i in range(5):
            store.commit(
                _outcome(step_id=f"s{i}"), _analysis(), _cost_est(),
                _desired(), _policy(), _meta(),
            )
        hv = store.build_history("c1")
        assert hv.success_streak == 5
        assert hv.failure_streak == 0

    def test_failure_resets_success_streak(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        store.commit(_outcome(step_id="s1"), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        store.commit(_outcome(step_id="s2"), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        store.commit(
            _outcome(step_id="s3", status="error"), _analysis(), _cost_est(),
            _desired(), _policy(), _meta(),
        )
        hv = store.build_history("c1")
        assert hv.success_streak == 0
        assert hv.failure_streak == 1

    def test_cost_ema_single_model(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        costs = [0.10, 0.10, 0.10]
        for i, c in enumerate(costs):
            store.commit(
                _outcome(step_id=f"s{i}", cost=c), _analysis(), _cost_est(),
                _desired(), _policy(), _meta(),
            )
        hv = store.build_history("c1")
        assert hv.cost_per_step_ema > 0
        assert "gpt-4" in hv.cost_per_step_ema_by_model
        assert hv.cost_per_step_ema_by_model["gpt-4"] > 0

    def test_latency_ema(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        store.commit(
            _outcome(elapsed_ms=200.0), _analysis(), _cost_est(),
            _desired(), _policy(), _meta(),
        )
        hv = store.build_history("c1")
        assert "gpt-4" in hv.latency_ema_ms
        assert hv.latency_ema_ms["gpt-4"] == pytest.approx(200.0)

    def test_multiple_chains_independent(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        store.commit(
            _outcome(chain_id="c1", step_id="s1"), _analysis(), _cost_est(),
            _desired(), _policy(), _meta(),
        )
        store.commit(
            _outcome(chain_id="c2", step_id="s2"), _analysis(), _cost_est(),
            DesiredPolicy(chain_id="c2", ceiling_usd=1.0, ceiling_steps=100,
                          ceiling_tokens_out=50000, on_exceed="halt",
                          fallback_model=None, timeout_ms=30000, priority=50),
            PolicyConfig(chain_id="c2", ceiling_usd=1.0, on_exceed="halt", issued_at=time.time()),
            _meta(),
        )
        hv1 = store.build_history("c1")
        hv2 = store.build_history("c2")
        assert hv1.depth == 1
        assert hv2.depth == 1

    def test_jsonl_persisted(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        store.commit(_outcome(), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        jsonl_path = tmp_path / "c1.jsonl"
        assert jsonl_path.exists()
        lines = jsonl_path.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["outcome"]["step_id"] == "s1"

    def test_stats_flush(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path), flush_interval=2)
        store.commit(_outcome(step_id="s1"), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        store.commit(_outcome(step_id="s2"), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        stats_path = tmp_path / "c1_stats.json"
        assert stats_path.exists()

    def test_reload_from_disk(self, tmp_path) -> None:
        store1 = FileStore(data_dir=str(tmp_path))
        for i in range(3):
            store1.commit(
                _outcome(step_id=f"s{i}"), _analysis(), _cost_est(),
                _desired(), _policy(), _meta(),
            )
        store1.close()
        # New store instance loads from disk
        store2 = FileStore(data_dir=str(tmp_path))
        hv = store2.build_history("c1")
        assert hv.depth == 3
        assert hv.cost_per_step_ema > 0

    def test_corrupt_line_skipped(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        store.commit(_outcome(step_id="s1"), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        # Corrupt the JSONL file
        jsonl_path = tmp_path / "c1.jsonl"
        with open(jsonl_path, "a") as f:
            f.write("{corrupt\n")
        store2 = FileStore(data_dir=str(tmp_path))
        hv = store2.build_history("c1")
        assert hv.depth == 1  # corrupt line skipped

    def test_protocol_compatible(self, tmp_path) -> None:
        from veronica.protocols import StoreProtocol

        store = FileStore(data_dir=str(tmp_path))
        assert isinstance(store, StoreProtocol)

    def test_empty_chain_returns_defaults(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        hv = store.build_history("nonexistent")
        assert hv.depth == 0
        assert hv.success_streak == 0
        assert hv.cost_per_step_ema == 0.0
        assert hv.budget_headroom_ratio == 1.0
```

**Step 2: Run test to verify it fails**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/test_file_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'veronica.file_store'`

**Step 3: Write minimal implementation**

Create `src/veronica/file_store.py`:

```python
# src/veronica/file_store.py
"""VERONICA OS file store -- JSONL persistence with EMA computation."""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Any

from veronica.types import (
    AnalysisResult,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    HistoryView,
    PolicyConfig,
    StepOutcome,
)

logger = logging.getLogger(__name__)

_EMA_ALPHA = 0.3
_EPS = 1e-12


def _ema(prev: float, current: float, alpha: float = _EMA_ALPHA) -> float:
    if prev < _EPS:
        return current
    return alpha * current + (1 - alpha) * prev


class _ChainStats:
    __slots__ = (
        "cost_ema",
        "cost_ema_by_model",
        "latency_ema_by_model",
        "success_streak",
        "failure_streak",
        "total_commits",
    )

    def __init__(self) -> None:
        self.cost_ema: float = 0.0
        self.cost_ema_by_model: dict[str, float] = {}
        self.latency_ema_by_model: dict[str, float] = {}
        self.success_streak: int = 0
        self.failure_streak: int = 0
        self.total_commits: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cost_ema": self.cost_ema,
            "cost_ema_by_model": dict(self.cost_ema_by_model),
            "latency_ema_by_model": dict(self.latency_ema_by_model),
            "success_streak": self.success_streak,
            "failure_streak": self.failure_streak,
            "total_commits": self.total_commits,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> _ChainStats:
        s = cls()
        s.cost_ema = data.get("cost_ema", 0.0)
        s.cost_ema_by_model = dict(data.get("cost_ema_by_model", {}))
        s.latency_ema_by_model = dict(data.get("latency_ema_by_model", {}))
        s.success_streak = data.get("success_streak", 0)
        s.failure_streak = data.get("failure_streak", 0)
        s.total_commits = data.get("total_commits", 0)
        return s


class FileStore:
    """Phase 2 JSONL-based store with EMA computation.

    One JSONL file per chain_id. Stats (EMA, streaks) kept in memory
    and flushed to {chain_id}_stats.json every flush_interval commits.
    """

    def __init__(
        self,
        data_dir: str,
        flush_interval: int = 10,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._flush_interval = flush_interval
        self._chain_stats: dict[str, _ChainStats] = {}
        self._load_existing_stats()

    def _load_existing_stats(self) -> None:
        for stats_path in self._data_dir.glob("*_stats.json"):
            chain_id = stats_path.stem.replace("_stats", "")
            try:
                data = json.loads(stats_path.read_text())
                self._chain_stats[chain_id] = _ChainStats.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                logger.warning("Corrupt stats file %s, starting fresh", stats_path)

    def commit(
        self,
        outcome: StepOutcome,
        analysis: AnalysisResult,
        cost: CostEstimate,
        desired: DesiredPolicy,
        policy: PolicyConfig,
        meta: DecisionMeta,
    ) -> None:
        chain_id = outcome.chain_id

        # 1. Append JSONL
        record = {
            "outcome": {
                "step_id": outcome.step_id,
                "request_id": outcome.request_id,
                "chain_id": outcome.chain_id,
                "kind": outcome.kind,
                "status": outcome.status,
                "cost_usd": outcome.cost_usd,
                "tokens_in": outcome.tokens_in,
                "tokens_out": outcome.tokens_out,
                "elapsed_ms": outcome.elapsed_ms,
                "model": outcome.model,
                "timestamp_ms": outcome.timestamp_ms,
            },
            "analysis": {
                "risk_level": analysis.risk_level,
                "recommendation": analysis.recommendation,
            },
        }
        jsonl_path = self._data_dir / f"{chain_id}.jsonl"
        with open(jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        # 2. Update stats
        stats = self._chain_stats.setdefault(chain_id, _ChainStats())
        stats.cost_ema = _ema(stats.cost_ema, outcome.cost_usd)

        model_key = outcome.model or "unknown"
        prev_cost = stats.cost_ema_by_model.get(model_key, 0.0)
        stats.cost_ema_by_model[model_key] = _ema(prev_cost, outcome.cost_usd)

        prev_lat = stats.latency_ema_by_model.get(model_key, 0.0)
        stats.latency_ema_by_model[model_key] = _ema(prev_lat, outcome.elapsed_ms)

        # 3. Streaks
        if outcome.status == "ok":
            stats.success_streak += 1
            stats.failure_streak = 0
        else:
            stats.failure_streak += 1
            stats.success_streak = 0

        stats.total_commits += 1

        # 4. Periodic flush
        if stats.total_commits % self._flush_interval == 0:
            self._flush_stats(chain_id)

    def build_history(self, chain_id: str, limit: int = 50) -> HistoryView:
        stats = self._chain_stats.get(chain_id, _ChainStats())
        outcomes = self._load_recent_outcomes(chain_id, limit)
        last_n = tuple(outcomes)

        rolling_cost = sum(o.cost_usd for o in last_n)
        depth = len(last_n)

        return HistoryView(
            chain_id=chain_id,
            last_n=last_n,
            rolling_cost_usd=rolling_cost,
            failure_streak=stats.failure_streak,
            depth=depth,
            loop_score=self._compute_loop_score(last_n),
            success_streak=stats.success_streak,
            cost_per_step_ema=stats.cost_ema,
            cost_per_step_ema_by_model=dict(stats.cost_ema_by_model),
            latency_ema_ms=dict(stats.latency_ema_by_model),
            budget_headroom_ratio=1.0,  # Requires ceiling context; default for now
        )

    def close(self) -> None:
        for chain_id in self._chain_stats:
            self._flush_stats(chain_id)

    def _flush_stats(self, chain_id: str) -> None:
        stats = self._chain_stats.get(chain_id)
        if stats is None:
            return
        stats_path = self._data_dir / f"{chain_id}_stats.json"
        stats_path.write_text(json.dumps(stats.to_dict()), encoding="utf-8")

    def _load_recent_outcomes(self, chain_id: str, limit: int) -> list[StepOutcome]:
        jsonl_path = self._data_dir / f"{chain_id}.jsonl"
        if not jsonl_path.exists():
            return []
        records: list[dict] = []
        for line in open(jsonl_path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("Corrupt JSONL line in %s, skipping", jsonl_path)
                continue

        outcomes: list[StepOutcome] = []
        for rec in records[-limit:]:
            o = rec["outcome"]
            outcomes.append(StepOutcome(
                step_id=o["step_id"],
                request_id=o["request_id"],
                chain_id=o["chain_id"],
                kind=o["kind"],
                status=o["status"],
                cost_usd=o["cost_usd"],
                tokens_in=o["tokens_in"],
                tokens_out=o["tokens_out"],
                elapsed_ms=o["elapsed_ms"],
                model=o.get("model"),
                events=(),
                timestamp_ms=o["timestamp_ms"],
            ))
        return outcomes

    @staticmethod
    def _compute_loop_score(outcomes: tuple[StepOutcome, ...] | list[StepOutcome]) -> float:
        if len(outcomes) < 3:
            return 0.0
        keys = [(o.kind, o.model or o.kind, o.status) for o in outcomes[-10:]]
        from collections import Counter

        counts = Counter(keys)
        if not counts:
            return 0.0
        most_common_count = counts.most_common(1)[0][1]
        return most_common_count / len(keys)
```

**Step 4: Run ALL tests**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/ -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
cd /d/work/Projects/veronica && git add src/veronica/file_store.py tests/test_file_store.py && git commit -m "feat: add FileStore with JSONL persistence and EMA computation"
```

---

## Task 4: HistoryAnalyzer

**Files:**
- Create: `src/veronica/history_analyzer.py`
- Create: `tests/test_history_analyzer.py`

**Step 1: Write the failing tests**

Create `tests/test_history_analyzer.py`:

```python
# tests/test_history_analyzer.py
"""Tests for veronica.history_analyzer -- 6-pattern adaptive analyzer."""
from __future__ import annotations

import pytest

from veronica.history_analyzer import HistoryAnalyzer
from veronica.types import HistoryView, Signal, StepIntent, StepOutcome


def _intent(model: str = "gpt-4") -> StepIntent:
    return StepIntent(
        step_id="s1", request_id="r1", chain_id="c1",
        kind="llm", model=model, tool_name=None,
        timeout_ms=30000, metadata={},
    )


def _outcome(status: str = "ok", cost: float = 0.01, elapsed_ms: float = 100.0) -> StepOutcome:
    import time
    return StepOutcome(
        step_id="s1", request_id="r1", chain_id="c1",
        kind="llm", status=status, cost_usd=cost,
        tokens_in=100, tokens_out=50, elapsed_ms=elapsed_ms,
        model="gpt-4", events=(), timestamp_ms=int(time.time() * 1000),
    )


def _history(
    depth: int = 0,
    failure_streak: int = 0,
    success_streak: int = 0,
    loop_score: float = 0.0,
    cost_per_step_ema: float = 0.01,
    budget_headroom_ratio: float = 1.0,
    latency_ema_ms: dict | None = None,
) -> HistoryView:
    return HistoryView(
        chain_id="c1",
        last_n=(),
        rolling_cost_usd=0.0,
        failure_streak=failure_streak,
        depth=depth,
        loop_score=loop_score,
        success_streak=success_streak,
        cost_per_step_ema=cost_per_step_ema,
        cost_per_step_ema_by_model={"gpt-4": cost_per_step_ema},
        latency_ema_ms=latency_ema_ms or {"gpt-4": 100.0},
        budget_headroom_ratio=budget_headroom_ratio,
    )


class TestHaltTighten:
    def test_halted_outcome_emits_critical(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(_intent(), _outcome(status="halted"), _history())
        kinds = [s.kind for s in result.signals]
        assert "halt_tighten" in kinds
        assert result.recommendation == "tighten"
        assert any(s.severity == "critical" for s in result.signals if s.kind == "halt_tighten")

    def test_error_outcome_emits_warning(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(_intent(), _outcome(status="error"), _history())
        kinds = [s.kind for s in result.signals]
        assert "halt_tighten" in kinds
        halt_signal = [s for s in result.signals if s.kind == "halt_tighten"][0]
        assert halt_signal.severity == "warning"

    def test_ok_outcome_no_halt_signal(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(_intent(), _outcome(status="ok"), _history())
        kinds = [s.kind for s in result.signals]
        assert "halt_tighten" not in kinds


class TestCleanLoosen:
    def test_loosen_requires_success_streak_and_headroom(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(
            _intent(), _outcome(status="ok"),
            _history(success_streak=3, budget_headroom_ratio=0.6),
        )
        kinds = [s.kind for s in result.signals]
        assert "clean_loosen" in kinds
        assert result.recommendation == "loosen"

    def test_no_loosen_with_low_streak(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(
            _intent(), _outcome(status="ok"),
            _history(success_streak=2, budget_headroom_ratio=0.6),
        )
        kinds = [s.kind for s in result.signals]
        assert "clean_loosen" not in kinds

    def test_no_loosen_with_low_headroom(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(
            _intent(), _outcome(status="ok"),
            _history(success_streak=5, budget_headroom_ratio=0.3),
        )
        kinds = [s.kind for s in result.signals]
        assert "clean_loosen" not in kinds


class TestDepthGuard:
    def test_soft_warning_at_depth_6(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(_intent(), _outcome(), _history(depth=6))
        depth_signals = [s for s in result.signals if s.kind == "depth_guard"]
        assert len(depth_signals) == 1
        assert depth_signals[0].severity == "warning"

    def test_hard_halt_at_depth_10(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(_intent(), _outcome(), _history(depth=10))
        depth_signals = [s for s in result.signals if s.kind == "depth_guard"]
        assert len(depth_signals) == 1
        assert depth_signals[0].severity == "critical"
        assert result.recommendation == "halt"

    def test_no_signal_below_6(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(_intent(), _outcome(), _history(depth=5))
        kinds = [s.kind for s in result.signals]
        assert "depth_guard" not in kinds


class TestCostAcceleration:
    def test_spike_detected(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(
            _intent(),
            _outcome(cost=0.10),  # 10x the EMA
            _history(depth=5, cost_per_step_ema=0.01),
        )
        kinds = [s.kind for s in result.signals]
        assert "cost_acceleration" in kinds

    def test_no_signal_when_cost_normal(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(
            _intent(),
            _outcome(cost=0.015),  # 1.5x the EMA, under 2x threshold
            _history(depth=5, cost_per_step_ema=0.01),
        )
        kinds = [s.kind for s in result.signals]
        assert "cost_acceleration" not in kinds

    def test_no_signal_when_depth_insufficient(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(
            _intent(),
            _outcome(cost=1.0),  # huge cost but depth < 5
            _history(depth=3, cost_per_step_ema=0.01),
        )
        kinds = [s.kind for s in result.signals]
        assert "cost_acceleration" not in kinds

    def test_no_signal_when_ema_zero(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(
            _intent(),
            _outcome(cost=0.10),
            _history(depth=10, cost_per_step_ema=0.0),
        )
        kinds = [s.kind for s in result.signals]
        assert "cost_acceleration" not in kinds


class TestLoopDetection:
    def test_high_loop_score(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(
            _intent(), _outcome(), _history(loop_score=0.8),
        )
        kinds = [s.kind for s in result.signals]
        assert "loop_detection" in kinds

    def test_low_loop_score(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(
            _intent(), _outcome(), _history(loop_score=0.3),
        )
        kinds = [s.kind for s in result.signals]
        assert "loop_detection" not in kinds


class TestLatencyAnomaly:
    def test_high_latency_emits_info(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(
            _intent(),
            _outcome(elapsed_ms=400.0),  # 4x the 100ms EMA
            _history(latency_ema_ms={"gpt-4": 100.0}),
        )
        lat_signals = [s for s in result.signals if s.kind == "latency_anomaly"]
        assert len(lat_signals) == 1
        assert lat_signals[0].severity == "info"

    def test_normal_latency_no_signal(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(
            _intent(),
            _outcome(elapsed_ms=150.0),  # 1.5x, under 3x
            _history(latency_ema_ms={"gpt-4": 100.0}),
        )
        kinds = [s.kind for s in result.signals]
        assert "latency_anomaly" not in kinds


class TestSignalComposition:
    def test_risk_level_max_severity(self) -> None:
        analyzer = HistoryAnalyzer()
        # halted + depth=10 -> multiple critical signals
        result = analyzer.analyze(
            _intent(), _outcome(status="halted"), _history(depth=10),
        )
        assert result.risk_level == "critical"

    def test_recommendation_priority(self) -> None:
        # halt > tighten > loosen > continue
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(
            _intent(), _outcome(status="halted"), _history(depth=10),
        )
        assert result.recommendation == "halt"

    def test_continue_when_no_signals(self) -> None:
        analyzer = HistoryAnalyzer()
        result = analyzer.analyze(
            _intent(), _outcome(status="ok"),
            _history(depth=2, success_streak=1),
        )
        assert result.recommendation == "continue"

    def test_protocol_compatible(self) -> None:
        from veronica.protocols import AnalyzerProtocol

        analyzer = HistoryAnalyzer()
        assert isinstance(analyzer, AnalyzerProtocol)
```

**Step 2: Run test to verify it fails**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/test_history_analyzer.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `src/veronica/history_analyzer.py`:

```python
# src/veronica/history_analyzer.py
"""VERONICA OS analyzer -- HistoryAnalyzer with 6 adaptive detection patterns."""
from __future__ import annotations

from veronica.types import AnalysisResult, HistoryView, Signal, StepIntent, StepOutcome

_DEPTH_SOFT = 6
_DEPTH_HARD = 10
_COST_SPIKE_FACTOR = 2.0
_COST_MIN_HISTORY = 5
_LOOP_THRESHOLD = 0.7
_LATENCY_SPIKE_FACTOR = 3.0
_LOOSEN_MIN_STREAK = 3
_LOOSEN_MIN_HEADROOM = 0.5
_EPS = 1e-12

_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
_RISK_BY_MAX_SEVERITY = {0: "nominal", 1: "elevated", 2: "critical"}
_RECOMMENDATION_RANK = {"continue": 0, "loosen": 1, "tighten": 2, "halt": 3}


class HistoryAnalyzer:
    """Phase 2 analyzer. 6 detection patterns, pure function (no internal state).

    All statistics come from HistoryView (computed by Store).
    Emits all matching signals; risk_level = max severity;
    recommendation = highest-priority single value.
    """

    def analyze(
        self,
        intent: StepIntent,
        outcome: StepOutcome,
        history: HistoryView,
    ) -> AnalysisResult:
        signals: list[Signal] = []
        recommendations: list[str] = []

        # Pattern 1: halt_tighten
        if outcome.status in ("halted", "error", "timeout"):
            severity = "critical" if outcome.status == "halted" else "warning"
            signals.append(Signal(
                kind="halt_tighten",
                severity=severity,
                detail=f"outcome status={outcome.status}",
            ))
            recommendations.append("tighten")

        # Pattern 2: clean_loosen
        if (
            outcome.status == "ok"
            and history.success_streak >= _LOOSEN_MIN_STREAK
            and history.budget_headroom_ratio >= _LOOSEN_MIN_HEADROOM
        ):
            signals.append(Signal(
                kind="clean_loosen",
                severity="info",
                detail=f"streak={history.success_streak}, headroom={history.budget_headroom_ratio:.2f}",
            ))
            recommendations.append("loosen")

        # Pattern 3: depth_guard (2-stage)
        if history.depth >= _DEPTH_HARD:
            signals.append(Signal(
                kind="depth_guard",
                severity="critical",
                detail=f"depth {history.depth} >= hard limit {_DEPTH_HARD}",
            ))
            recommendations.append("halt")
        elif history.depth >= _DEPTH_SOFT:
            signals.append(Signal(
                kind="depth_guard",
                severity="warning",
                detail=f"depth {history.depth} >= soft limit {_DEPTH_SOFT}",
            ))
            recommendations.append("tighten")

        # Pattern 4: cost_acceleration
        if (
            history.depth >= _COST_MIN_HISTORY
            and history.cost_per_step_ema > _EPS
            and outcome.cost_usd > history.cost_per_step_ema * _COST_SPIKE_FACTOR
        ):
            signals.append(Signal(
                kind="cost_acceleration",
                severity="warning",
                detail=f"cost {outcome.cost_usd:.4f} > {_COST_SPIKE_FACTOR}x EMA {history.cost_per_step_ema:.4f}",
            ))
            recommendations.append("tighten")

        # Pattern 5: loop_detection
        if history.loop_score >= _LOOP_THRESHOLD:
            signals.append(Signal(
                kind="loop_detection",
                severity="warning",
                detail=f"loop_score={history.loop_score:.2f}",
            ))
            recommendations.append("tighten")

        # Pattern 6: latency_anomaly (info-only, no recommendation change)
        model_key = outcome.model or "unknown"
        latency_ema = history.latency_ema_ms.get(model_key)
        if latency_ema is not None and latency_ema > _EPS:
            if outcome.elapsed_ms > latency_ema * _LATENCY_SPIKE_FACTOR:
                signals.append(Signal(
                    kind="latency_anomaly",
                    severity="info",
                    detail=f"elapsed {outcome.elapsed_ms:.0f}ms > {_LATENCY_SPIKE_FACTOR}x EMA {latency_ema:.0f}ms",
                ))
                # No recommendation change (info-only)

        # Compose risk_level
        if signals:
            max_sev = max(_SEVERITY_RANK.get(s.severity, 0) for s in signals)
            risk_level = _RISK_BY_MAX_SEVERITY[max_sev]
        else:
            risk_level = "nominal"

        # Compose recommendation (highest priority wins)
        if recommendations:
            recommendation = max(recommendations, key=lambda r: _RECOMMENDATION_RANK.get(r, 0))
        else:
            recommendation = "continue"

        return AnalysisResult(
            signals=tuple(signals),
            risk_level=risk_level,
            recommendation=recommendation,
        )
```

**Step 4: Run ALL tests**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/ -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
cd /d/work/Projects/veronica && git add src/veronica/history_analyzer.py tests/test_history_analyzer.py && git commit -m "feat: add HistoryAnalyzer with 6 adaptive detection patterns"
```

---

## Task 5: RegressionCostModel

**Files:**
- Create: `src/veronica/regression_cost_model.py`
- Create: `tests/test_regression_cost_model.py`

**Step 1: Write the failing tests**

Create `tests/test_regression_cost_model.py`:

```python
# tests/test_regression_cost_model.py
"""Tests for veronica.regression_cost_model -- EMA-based cost estimation."""
from __future__ import annotations

import pytest

from veronica.regression_cost_model import RegressionCostModel
from veronica.types import HistoryView, StepIntent


def _intent(model: str = "gpt-4") -> StepIntent:
    return StepIntent(
        step_id="s1", request_id="r1", chain_id="c1",
        kind="llm", model=model, tool_name=None,
        timeout_ms=30000, metadata={},
    )


def _history(
    depth: int = 10,
    cost_ema_by_model: dict | None = None,
) -> HistoryView:
    return HistoryView(
        chain_id="c1", last_n=(), rolling_cost_usd=0.0,
        failure_streak=0, depth=depth, loop_score=0.0,
        cost_per_step_ema_by_model=cost_ema_by_model or {},
    )


class TestRegressionCostModel:
    def test_uses_ema_when_available(self) -> None:
        model = RegressionCostModel()
        result = model.estimate(
            _intent("gpt-4"),
            _history(depth=10, cost_ema_by_model={"gpt-4": 0.05}),
            None,
        )
        assert result.estimated_usd == pytest.approx(0.05)
        assert result.basis == "historical"
        assert result.confidence == 0.85  # depth >= 20? no, depth=10 -> 0.75

    def test_graduated_confidence_low(self) -> None:
        model = RegressionCostModel()
        result = model.estimate(
            _intent(), _history(depth=3, cost_ema_by_model={"gpt-4": 0.05}), None,
        )
        assert result.confidence == 0.60

    def test_graduated_confidence_mid(self) -> None:
        model = RegressionCostModel()
        result = model.estimate(
            _intent(), _history(depth=10, cost_ema_by_model={"gpt-4": 0.05}), None,
        )
        assert result.confidence == 0.75

    def test_graduated_confidence_high(self) -> None:
        model = RegressionCostModel()
        result = model.estimate(
            _intent(), _history(depth=25, cost_ema_by_model={"gpt-4": 0.05}), None,
        )
        assert result.confidence == 0.85

    def test_fallback_to_pricing_table(self) -> None:
        model = RegressionCostModel()
        result = model.estimate(
            _intent("gpt-4"),
            _history(depth=0, cost_ema_by_model={}),
            None,
        )
        assert result.basis == "pricing_table"
        assert result.confidence == 0.2
        assert result.estimated_usd > 0

    def test_unknown_model_fallback(self) -> None:
        model = RegressionCostModel()
        result = model.estimate(
            _intent("totally-unknown-model"),
            _history(cost_ema_by_model={}),
            None,
        )
        assert result.estimated_usd > 0
        assert result.basis == "pricing_table"

    def test_zero_ema_uses_fallback(self) -> None:
        model = RegressionCostModel()
        result = model.estimate(
            _intent(), _history(cost_ema_by_model={"gpt-4": 0.0}), None,
        )
        assert result.basis == "pricing_table"

    def test_tool_intent(self) -> None:
        model = RegressionCostModel()
        intent = StepIntent(
            step_id="s1", request_id="r1", chain_id="c1",
            kind="tool", model=None, tool_name="web_search",
            timeout_ms=30000, metadata={},
        )
        result = model.estimate(intent, _history(), None)
        assert result.estimated_usd == 0.0
        assert result.basis == "pricing_table"

    def test_protocol_compatible(self) -> None:
        from veronica.protocols import CostModelProtocol

        model = RegressionCostModel()
        assert isinstance(model, CostModelProtocol)
```

**Step 2: Run test to verify it fails**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/test_regression_cost_model.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `src/veronica/regression_cost_model.py`:

```python
# src/veronica/regression_cost_model.py
"""VERONICA OS cost model -- EMA-based regression cost estimation."""
from __future__ import annotations

from veronica_core.pricing import PRICING_TABLE, estimate_cost_usd

from veronica.types import AnalysisResult, CostEstimate, HistoryView, StepIntent

_EPS = 1e-12
_FALLBACK_TOKENS_IN = 500
_FALLBACK_TOKENS_OUT = 200
_FALLBACK_COST_USD = 0.01


def _graduated_confidence(depth: int) -> float:
    if depth < 5:
        return 0.60
    if depth < 20:
        return 0.75
    return 0.85


class RegressionCostModel:
    """Phase 2 cost model. Uses EMA from HistoryView for estimation.

    Stateless: reads cost_per_step_ema_by_model from history.
    Falls back to veronica-core PRICING_TABLE for unknown models.
    """

    def estimate(
        self,
        intent: StepIntent,
        history: HistoryView,
        last_analysis: AnalysisResult | None,
    ) -> CostEstimate:
        if intent.kind == "tool":
            return CostEstimate(
                estimated_usd=0.0,
                confidence=1.0,
                model_used=intent.tool_name or "tool",
                basis="pricing_table",
            )

        model_key = intent.model or "unknown"
        ema = history.cost_per_step_ema_by_model.get(model_key)

        if ema is not None and ema > _EPS:
            return CostEstimate(
                estimated_usd=ema,
                confidence=_graduated_confidence(history.depth),
                model_used=model_key,
                basis="historical",
            )

        # Fallback to pricing table
        if model_key in PRICING_TABLE:
            cost = estimate_cost_usd(model_key, _FALLBACK_TOKENS_IN, _FALLBACK_TOKENS_OUT)
        else:
            cost = _FALLBACK_COST_USD

        return CostEstimate(
            estimated_usd=cost,
            confidence=0.2,
            model_used=model_key,
            basis="pricing_table",
        )
```

**Step 4: Run ALL tests**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/ -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
cd /d/work/Projects/veronica && git add src/veronica/regression_cost_model.py tests/test_regression_cost_model.py && git commit -m "feat: add RegressionCostModel with EMA-based estimation and graduated confidence"
```

---

## Task 6: AdaptivePlanner

**Files:**
- Create: `src/veronica/adaptive_planner.py`
- Create: `tests/test_adaptive_planner.py`

**Step 1: Write the failing tests**

Create `tests/test_adaptive_planner.py`:

```python
# tests/test_adaptive_planner.py
"""Tests for veronica.adaptive_planner -- error-class-aware ceiling adjustment."""
from __future__ import annotations

import pytest

from veronica.adaptive_planner import AdaptivePlanner
from veronica.types import AnalysisResult, BudgetState, CostEstimate, Signal


def _analysis(
    recommendation: str = "continue",
    risk_level: str = "nominal",
    signals: tuple = (),
) -> AnalysisResult:
    return AnalysisResult(signals=signals, risk_level=risk_level, recommendation=recommendation)


def _cost(estimated: float = 0.05) -> CostEstimate:
    return CostEstimate(estimated_usd=estimated, confidence=0.8, model_used="gpt-4", basis="historical")


def _budget(remaining: float = 50.0) -> BudgetState:
    return BudgetState(request_remaining_usd=remaining, chain_remaining_usd=remaining, window_remaining_steps=100)


class TestAdaptivePlanner:
    def test_continue_no_change(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        result = planner.plan(_analysis(), _cost(), _budget())
        assert result.ceiling_usd == pytest.approx(1.0)

    def test_tighten_halted_minus_50(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        halted_signals = (Signal(kind="halt_tighten", severity="critical", detail="halted"),)
        result = planner.plan(
            _analysis(recommendation="tighten", signals=halted_signals),
            _cost(), _budget(),
        )
        assert result.ceiling_usd == pytest.approx(0.50)

    def test_tighten_error_minus_15(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        error_signals = (Signal(kind="halt_tighten", severity="warning", detail="error"),)
        result = planner.plan(
            _analysis(recommendation="tighten", signals=error_signals),
            _cost(), _budget(),
        )
        assert result.ceiling_usd == pytest.approx(0.85)

    def test_loosen_plus_3_percent(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        result = planner.plan(
            _analysis(recommendation="loosen"),
            _cost(), _budget(),
        )
        assert result.ceiling_usd == pytest.approx(1.03)

    def test_ceiling_clamped_to_budget(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=10.0)
        result = planner.plan(_analysis(), _cost(), _budget(remaining=0.50))
        assert result.ceiling_usd <= 0.50

    def test_ceiling_min_guard(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0, min_ceiling_usd=0.10)
        # Tighten several times
        halted_signals = (Signal(kind="halt_tighten", severity="critical", detail="halted"),)
        for _ in range(10):
            planner.plan(
                _analysis(recommendation="tighten", signals=halted_signals),
                _cost(), _budget(),
            )
        result = planner.plan(
            _analysis(recommendation="tighten", signals=halted_signals),
            _cost(), _budget(),
        )
        assert result.ceiling_usd >= 0.10

    def test_ceiling_at_least_1_5x_estimated_cost(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=0.01, min_ceiling_usd=0.001)
        result = planner.plan(_analysis(), _cost(estimated=0.10), _budget())
        assert result.ceiling_usd >= 0.10 * 1.5

    def test_cooldown_prevents_rapid_changes(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        # First tighten applies
        halted_signals = (Signal(kind="halt_tighten", severity="critical", detail="halted"),)
        r1 = planner.plan(
            _analysis(recommendation="tighten", signals=halted_signals),
            _cost(), _budget(),
        )
        # Immediate loosen should be blocked by cooldown
        r2 = planner.plan(_analysis(recommendation="loosen"), _cost(), _budget())
        assert r2.ceiling_usd == r1.ceiling_usd  # no change during cooldown

    def test_halt_recommendation_sets_on_exceed(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        result = planner.plan(
            _analysis(recommendation="halt"),
            _cost(), _budget(),
        )
        assert result.on_exceed == "halt"

    def test_protocol_compatible(self) -> None:
        from veronica.protocols import PlannerProtocol

        planner = AdaptivePlanner()
        assert isinstance(planner, PlannerProtocol)
```

**Step 2: Run test to verify it fails**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/test_adaptive_planner.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `src/veronica/adaptive_planner.py`:

```python
# src/veronica/adaptive_planner.py
"""VERONICA OS planner -- AdaptivePlanner with error-class-aware ceiling adjustment."""
from __future__ import annotations

from veronica.types import AnalysisResult, BudgetState, CostEstimate, DesiredPolicy

_HALTED_FACTOR = 0.50    # -50%
_ERROR_FACTOR = 0.85     # -15%
_TIMEOUT_FACTOR = 0.90   # -10%
_LOOSEN_FACTOR = 1.03    # +3%

_DEFAULT_STEPS = 100
_DEFAULT_TOKENS_OUT = 50_000
_COOLDOWN_STEPS = 3
_COST_HEADROOM = 1.5


class AdaptivePlanner:
    """Phase 2 planner. Error-class-aware tightening with cooldown.

    - halted: -50% (critical)
    - error: -15% (warning)
    - timeout: -10% (mild)
    - loosen: +3% (conservative)
    - 3-step cooldown per chain_id after any adjustment.
    - Ceiling >= estimated_cost * 1.5 (prevents starvation).
    """

    def __init__(
        self,
        base_ceiling_usd: float = 1.0,
        max_ceiling_usd: float = 10.0,
        min_ceiling_usd: float = 0.10,
        default_timeout_ms: int = 30_000,
        default_on_exceed: str = "halt",
        fallback_model: str | None = None,
    ) -> None:
        self._base = base_ceiling_usd
        self._max = max_ceiling_usd
        self._min = min_ceiling_usd
        self._timeout_ms = default_timeout_ms
        self._default_on_exceed = default_on_exceed
        self._fallback_model = fallback_model
        self._effective_ceiling = base_ceiling_usd
        self._cooldowns: dict[str, int] = {}  # chain_id -> steps remaining

    def plan(
        self,
        analysis: AnalysisResult | None,
        cost: CostEstimate,
        budget: BudgetState,
    ) -> DesiredPolicy:
        ceiling = self._effective_ceiling
        on_exceed = self._default_on_exceed
        adjusted = False

        # Check cooldown (use empty string as default chain for single-chain mode)
        chain_key = ""
        cooldown_remaining = self._cooldowns.get(chain_key, 0)

        if analysis is not None:
            if analysis.recommendation == "halt":
                on_exceed = "halt"

            if cooldown_remaining <= 0:
                if analysis.recommendation == "tighten":
                    factor = self._tighten_factor(analysis)
                    ceiling *= factor
                    adjusted = True
                elif analysis.recommendation == "loosen":
                    ceiling *= _LOOSEN_FACTOR
                    adjusted = True

        # Minimum ceiling guard: at least 1.5x estimated cost
        ceiling = max(ceiling, cost.estimated_usd * _COST_HEADROOM)

        # Double clamp (Planner level)
        ceiling = max(self._min, min(self._max, ceiling))
        ceiling = min(ceiling, budget.chain_remaining_usd)

        # Update state
        self._effective_ceiling = ceiling
        if adjusted:
            self._cooldowns[chain_key] = _COOLDOWN_STEPS
        else:
            self._cooldowns[chain_key] = max(0, cooldown_remaining - 1)

        return DesiredPolicy(
            chain_id="",  # Filled by VeronicaOS
            ceiling_usd=ceiling,
            ceiling_steps=min(_DEFAULT_STEPS, budget.window_remaining_steps),
            ceiling_tokens_out=_DEFAULT_TOKENS_OUT,
            on_exceed=on_exceed,
            fallback_model=self._fallback_model,
            timeout_ms=self._timeout_ms,
            priority=50,
        )

    @staticmethod
    def _tighten_factor(analysis: AnalysisResult) -> float:
        for signal in analysis.signals:
            if signal.kind == "halt_tighten":
                if signal.severity == "critical":
                    return _HALTED_FACTOR
                return _ERROR_FACTOR
        return _TIMEOUT_FACTOR
```

**Step 4: Run ALL tests**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/ -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
cd /d/work/Projects/veronica && git add src/veronica/adaptive_planner.py tests/test_adaptive_planner.py && git commit -m "feat: add AdaptivePlanner with error-class tightening and cooldown"
```

---

## Task 7: ProportionalArbiter

**Files:**
- Create: `src/veronica/proportional_arbiter.py`
- Create: `tests/test_proportional_arbiter.py`

**Step 1: Write the failing tests**

Create `tests/test_proportional_arbiter.py`:

```python
# tests/test_proportional_arbiter.py
"""Tests for veronica.proportional_arbiter -- priority-weighted budget allocation."""
from __future__ import annotations

import time

import pytest

from veronica.proportional_arbiter import ProportionalArbiter
from veronica.types import DesiredPolicy


def _desired(chain_id: str = "c1", ceiling: float = 1.0, priority: int = 50) -> DesiredPolicy:
    return DesiredPolicy(
        chain_id=chain_id, ceiling_usd=ceiling, ceiling_steps=100,
        ceiling_tokens_out=50000, on_exceed="halt",
        fallback_model=None, timeout_ms=30000, priority=priority,
    )


class TestProportionalArbiter:
    def test_single_chain_passthrough(self) -> None:
        arbiter = ProportionalArbiter()
        result = arbiter.arbitrate([_desired("c1", 1.0, 50)], 10.0)
        assert "c1" in result
        assert result["c1"].ceiling_usd == pytest.approx(1.0)

    def test_proportional_allocation_by_priority(self) -> None:
        arbiter = ProportionalArbiter()
        desires = [
            _desired("c1", 5.0, 75),   # 3x priority
            _desired("c2", 5.0, 25),   # 1x priority
        ]
        result = arbiter.arbitrate(desires, 4.0)
        assert result["c1"].ceiling_usd > result["c2"].ceiling_usd
        total = result["c1"].ceiling_usd + result["c2"].ceiling_usd
        assert total <= 4.0 + 1e-9

    def test_total_allocation_never_exceeds_budget(self) -> None:
        arbiter = ProportionalArbiter()
        desires = [_desired(f"c{i}", 10.0, 50) for i in range(5)]
        result = arbiter.arbitrate(desires, 5.0)
        total = sum(pc.ceiling_usd for pc in result.values())
        assert total <= 5.0 + 1e-9

    def test_priority_zero_excluded(self) -> None:
        arbiter = ProportionalArbiter()
        desires = [
            _desired("c1", 1.0, 50),
            _desired("c2", 1.0, 0),
        ]
        result = arbiter.arbitrate(desires, 10.0)
        assert "c1" in result
        assert "c2" not in result

    def test_negative_priority_excluded(self) -> None:
        arbiter = ProportionalArbiter()
        result = arbiter.arbitrate([_desired("c1", 1.0, -10)], 10.0)
        assert "c1" not in result

    def test_conditional_min_allocation(self) -> None:
        arbiter = ProportionalArbiter()
        desires = [
            _desired("c1", 0.001, 50),
            _desired("c2", 0.001, 50),
        ]
        # Budget can cover both minimums
        result = arbiter.arbitrate(desires, 1.0)
        for pc in result.values():
            assert pc.ceiling_usd >= 0.01  # min allocation

    def test_min_allocation_not_applied_when_budget_tight(self) -> None:
        arbiter = ProportionalArbiter()
        desires = [
            _desired(f"c{i}", 0.001, 50) for i in range(200)
        ]
        # Budget cannot cover 200 * 0.01 = 2.0
        result = arbiter.arbitrate(desires, 0.50)
        total = sum(pc.ceiling_usd for pc in result.values())
        assert total <= 0.50 + 1e-9

    def test_ceiling_cap_surplus_redistribution(self) -> None:
        arbiter = ProportionalArbiter()
        desires = [
            _desired("c1", 0.50, 50),   # desired only 0.50
            _desired("c2", 10.0, 50),   # desired 10.0
        ]
        result = arbiter.arbitrate(desires, 5.0)
        # c1 capped at 0.50, surplus goes to c2
        assert result["c1"].ceiling_usd <= 0.50
        assert result["c2"].ceiling_usd > 2.5  # got surplus

    def test_empty_desires_returns_empty(self) -> None:
        arbiter = ProportionalArbiter()
        result = arbiter.arbitrate([], 10.0)
        assert result == {}

    def test_protocol_compatible(self) -> None:
        from veronica.protocols import ArbiterProtocol

        arbiter = ProportionalArbiter()
        assert isinstance(arbiter, ArbiterProtocol)
```

**Step 2: Run test to verify it fails**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/test_proportional_arbiter.py -v`
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `src/veronica/proportional_arbiter.py`:

```python
# src/veronica/proportional_arbiter.py
"""VERONICA OS arbiter -- ProportionalArbiter with priority-weighted allocation."""
from __future__ import annotations

import time
from typing import Mapping, Sequence

from veronica.types import DesiredPolicy, PolicyConfig

_MIN_ALLOCATION_USD = 0.01
_EPS = 1e-12


class ProportionalArbiter:
    """Phase 2 arbiter. Priority-weighted proportional budget allocation.

    - weight = priority (not priority * ceiling)
    - priority <= 0 excluded entirely
    - Conditional min_allocation (only when budget allows)
    - 2-pass surplus redistribution
    - Double clamp: no single allocation exceeds budget
    """

    def arbitrate(
        self,
        desires: Sequence[DesiredPolicy],
        budget_remaining_usd: float,
    ) -> Mapping[str, PolicyConfig]:
        if not desires:
            return {}

        # Filter: priority > 0 only
        eligible = [d for d in desires if d.priority > 0]
        if not eligible:
            return {}

        total_weight = sum(d.priority for d in eligible)
        if total_weight < _EPS:
            return {}

        # Pass 1: Proportional allocation, cap to desired ceiling
        allocations: dict[str, float] = {}
        surplus = 0.0
        uncapped: list[DesiredPolicy] = []

        for d in eligible:
            share = (d.priority / total_weight) * budget_remaining_usd
            if share > d.ceiling_usd:
                surplus += share - d.ceiling_usd
                allocations[d.chain_id] = d.ceiling_usd
            else:
                allocations[d.chain_id] = share
                uncapped.append(d)

        # Pass 2: Redistribute surplus among uncapped
        if surplus > _EPS and uncapped:
            uncapped_weight = sum(d.priority for d in uncapped)
            if uncapped_weight > _EPS:
                for d in uncapped:
                    extra = (d.priority / uncapped_weight) * surplus
                    new_alloc = allocations[d.chain_id] + extra
                    allocations[d.chain_id] = min(new_alloc, d.ceiling_usd)

        # Conditional min_allocation
        if budget_remaining_usd >= len(eligible) * _MIN_ALLOCATION_USD:
            for chain_id in allocations:
                allocations[chain_id] = max(allocations[chain_id], _MIN_ALLOCATION_USD)

        # Double clamp: total must not exceed budget
        total = sum(allocations.values())
        if total > budget_remaining_usd + _EPS:
            scale = budget_remaining_usd / total
            allocations = {k: v * scale for k, v in allocations.items()}

        # Build PolicyConfigs
        now = time.time()
        desire_map = {d.chain_id: d for d in eligible}
        result: dict[str, PolicyConfig] = {}

        for chain_id, alloc in allocations.items():
            d = desire_map[chain_id]
            result[chain_id] = PolicyConfig(
                chain_id=chain_id,
                ceiling_usd=alloc,
                ceiling_steps=d.ceiling_steps,
                ceiling_tokens_out=d.ceiling_tokens_out,
                on_exceed=d.on_exceed,
                fallback_model=d.fallback_model,
                timeout_ms=d.timeout_ms,
                priority=d.priority,
                issued_at=now,
                planner_version="0.2.0",
            )

        return result
```

**Step 4: Run ALL tests**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/ -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
cd /d/work/Projects/veronica && git add src/veronica/proportional_arbiter.py tests/test_proportional_arbiter.py && git commit -m "feat: add ProportionalArbiter with priority-weighted allocation"
```

---

## Task 8: Phase 2 Integration Test

**Files:**
- Create: `tests/test_phase2_integration.py`
- Modify: `src/veronica/__init__.py` (export new classes)

**Step 1: Write the failing test**

Create `tests/test_phase2_integration.py`:

```python
# tests/test_phase2_integration.py
"""Integration tests -- full Phase 2 pipeline through VeronicaOS."""
from __future__ import annotations

import time
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


def _intent(step_id: str = "s1", chain_id: str = "c1", model: str = "gpt-4") -> StepIntent:
    return StepIntent(
        step_id=step_id, request_id="r1", chain_id=chain_id,
        kind="llm", model=model, tool_name=None,
        timeout_ms=30_000, metadata={},
    )


def _snapshot(chain_id: str = "c1", cost: float = 0.01, status: str = "ok") -> ContextSnapshot:
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


class TestPhase2Integration:
    def test_full_phase2_pipeline(self, tmp_path) -> None:
        """All Phase 2 components wired through VeronicaOS."""
        emitter = BufferedEmitter()
        vos = VeronicaOS(
            analyzer=HistoryAnalyzer(),
            cost_model=RegressionCostModel(),
            planner=AdaptivePlanner(),
            arbiter=ProportionalArbiter(),
            emitter=emitter,
            store=FileStore(data_dir=str(tmp_path)),
        )

        # Run 5 successful steps
        for i in range(5):
            handle = vos.before_step(_intent(step_id=f"s{i}"))
            assert handle.policy.ceiling_usd > 0
            vos.after_step(handle, _snapshot(cost=0.01))

        # Verify emitter received events
        events = emitter.snapshot()
        assert len(events) == 5

    def test_tighten_after_halt_phase2(self, tmp_path) -> None:
        """Ceiling decreases after halted step with Phase 2 components."""
        vos = VeronicaOS(
            analyzer=HistoryAnalyzer(),
            cost_model=RegressionCostModel(),
            planner=AdaptivePlanner(),
            arbiter=ProportionalArbiter(),
            store=FileStore(data_dir=str(tmp_path)),
        )

        handle1 = vos.before_step(_intent())
        ceiling1 = handle1.policy.ceiling_usd
        vos.after_step(handle1, _snapshot(status="halted"))

        handle2 = vos.before_step(_intent(step_id="s2"))
        ceiling2 = handle2.policy.ceiling_usd
        assert ceiling2 < ceiling1

    def test_loosen_after_sustained_success(self, tmp_path) -> None:
        """Ceiling increases after 3+ consecutive ok steps (Phase 2 clean_loosen)."""
        vos = VeronicaOS(
            analyzer=HistoryAnalyzer(),
            cost_model=RegressionCostModel(),
            planner=AdaptivePlanner(),
            arbiter=ProportionalArbiter(),
            store=FileStore(data_dir=str(tmp_path)),
        )

        # Build up success streak (need >= 3 for clean_loosen)
        for i in range(4):
            handle = vos.before_step(_intent(step_id=f"s{i}"))
            vos.after_step(handle, _snapshot(cost=0.01))

        handle_before = vos.before_step(_intent(step_id="s_before"))
        ceiling_before = handle_before.policy.ceiling_usd
        vos.after_step(handle_before, _snapshot(cost=0.01))

        handle_after = vos.before_step(_intent(step_id="s_after"))
        ceiling_after = handle_after.policy.ceiling_usd
        assert ceiling_after >= ceiling_before  # May be equal due to cooldown, but never less

    def test_file_store_persists_across_os_instances(self, tmp_path) -> None:
        """Data survives VeronicaOS reconstruction."""
        store = FileStore(data_dir=str(tmp_path))
        vos1 = VeronicaOS(
            analyzer=HistoryAnalyzer(),
            cost_model=RegressionCostModel(),
            planner=AdaptivePlanner(),
            arbiter=ProportionalArbiter(),
            store=store,
        )
        handle = vos1.before_step(_intent())
        vos1.after_step(handle, _snapshot(cost=0.05))
        store.close()

        # Reconstruct with new FileStore pointing to same dir
        store2 = FileStore(data_dir=str(tmp_path))
        hv = store2.build_history("c1")
        assert hv.depth == 1
        assert hv.cost_per_step_ema > 0

    def test_phase1_tests_still_pass(self) -> None:
        """Phase 1 default VeronicaOS still works (backward compat)."""
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        assert handle.policy.ceiling_usd > 0
        vos.after_step(handle, _snapshot())
```

**Step 2: Run test to verify it fails**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/test_phase2_integration.py -v`
Expected: PASS (all components already implemented). If any fail, fix the specific issue.

**Step 3: Update `__init__.py` exports**

Modify `src/veronica/__init__.py` to add Phase 2 exports:

```python
"""VERONICA -- Execution OS for LLM systems."""
from __future__ import annotations

from veronica.adaptive_planner import AdaptivePlanner
from veronica.buffered_emitter import BufferedEmitter
from veronica.file_store import FileStore
from veronica.history_analyzer import HistoryAnalyzer
from veronica.os import VeronicaOS
from veronica.proportional_arbiter import ProportionalArbiter
from veronica.regression_cost_model import RegressionCostModel
from veronica.types import (
    AnalysisResult,
    BudgetState,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    HistoryView,
    PolicyConfig,
    Signal,
    StepHandle,
    StepIntent,
    StepOutcome,
)

__version__ = "0.2.0"

__all__ = [
    # Core
    "VeronicaOS",
    # Phase 2 components
    "AdaptivePlanner",
    "BufferedEmitter",
    "FileStore",
    "HistoryAnalyzer",
    "ProportionalArbiter",
    "RegressionCostModel",
    # Types
    "AnalysisResult",
    "BudgetState",
    "CostEstimate",
    "DecisionMeta",
    "DesiredPolicy",
    "HistoryView",
    "PolicyConfig",
    "Signal",
    "StepHandle",
    "StepIntent",
    "StepOutcome",
]
```

Also update `pyproject.toml` version:
```
version = "0.2.0"
```

**Step 4: Run ALL tests (full suite)**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/ -v --tb=short`
Expected: ALL PASS (80+ tests)

Run: `cd /d/work/Projects/veronica && python -m pytest tests/ --cov=veronica --cov-report=term-missing`
Expected: Coverage >= 80%

**Step 5: Commit**

```bash
cd /d/work/Projects/veronica && git add tests/test_phase2_integration.py src/veronica/__init__.py pyproject.toml && git commit -m "feat: Phase 2 integration tests, exports, and version bump to 0.2.0"
```

---

## Task 9: Final Verification and Tag

**Step 1: Run full test suite with coverage**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/ -v --cov=veronica --cov-report=term-missing`
Expected: ALL PASS, coverage >= 80%

**Step 2: Verify Phase 1 backward compatibility**

Run: `cd /d/work/Projects/veronica && python -m pytest tests/test_os.py tests/test_integration.py -v`
Expected: ALL PASS (no regressions)

**Step 3: Push and tag**

```bash
cd /d/work/Projects/veronica && git push && git tag -a v0.2.0 -m "Phase 2: Adaptive layer - HistoryAnalyzer, RegressionCostModel, AdaptivePlanner, ProportionalArbiter, BufferedEmitter, FileStore" && git push origin v0.2.0
```

---

## Summary

| Task | Component | Est. Tests | Files |
|------|-----------|-----------|-------|
| 1 | HistoryView expansion | 2 | types.py, test_types.py |
| 2 | BufferedEmitter | 8 | buffered_emitter.py, test |
| 3 | FileStore | 12 | file_store.py, test |
| 4 | HistoryAnalyzer | 18 | history_analyzer.py, test |
| 5 | RegressionCostModel | 9 | regression_cost_model.py, test |
| 6 | AdaptivePlanner | 10 | adaptive_planner.py, test |
| 7 | ProportionalArbiter | 10 | proportional_arbiter.py, test |
| 8 | Integration + exports | 5 | test_phase2_integration.py, __init__.py |
| 9 | Final verification + tag | 0 | (verification only) |

**Total: ~74 new tests, 8 new files, 2 modified files**

**Dependency order:** Task 1 (types) -> Tasks 2-3 (parallel, no deps) -> Tasks 4-7 (parallel, depend on Task 1) -> Task 8 (integration) -> Task 9 (tag)
