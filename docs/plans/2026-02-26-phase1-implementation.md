# VERONICA OS Phase 1 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build the minimum viable VERONICA OS -- a synchronous pipeline library with 7 default components that produces PolicyConfigs for veronica-core.

**Architecture:** StepHandle-based before_step/after_step pipeline. Each stage is a Protocol with a default implementation. VeronicaOS is the single orchestrator and sole Store writer. All stages are pure functions.

**Tech Stack:** Python 3.10+, hatchling (build), pytest (test), veronica-core >=1.0.0 (dependency). No other runtime deps.

**Reference:** `docs/plans/2026-02-26-veronica-os-design.md` (architecture design)

---

## Task 0: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/veronica/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

**Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "veronica"
version = "0.1.0"
description = "Execution OS for LLM systems. Policy planning, budget allocation, and governance built on veronica-core."
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.10"
authors = [
    { name = "amabito" },
]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.10",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
    "Typing :: Typed",
]
keywords = [
    "llm", "agent", "planner", "budget", "policy", "governance",
    "multi-agent", "cost-prediction", "execution-os",
]
dependencies = [
    "veronica-core>=1.0.0",
]

[project.urls]
Repository = "https://github.com/amabito/veronica"

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
]

[tool.hatch.build.targets.wheel]
packages = ["src/veronica"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]

[tool.coverage.run]
source = ["veronica"]

[tool.coverage.report]
fail_under = 80
show_missing = true
```

**Step 2: Create src/veronica/__init__.py**

```python
"""VERONICA -- Execution OS for LLM systems."""
from __future__ import annotations

__version__ = "0.1.0"
```

**Step 3: Create tests/__init__.py and tests/conftest.py**

`tests/__init__.py`: empty file.

```python
# tests/conftest.py
"""Shared fixtures for VERONICA tests."""
from __future__ import annotations
```

**Step 4: Verify scaffold**

Run: `cd D:/work/Projects/veronica && pip install -e ".[dev]" && python -c "import veronica; print(veronica.__version__)"`
Expected: `0.1.0`

**Step 5: Commit**

```bash
git add pyproject.toml src/ tests/
git commit -m "chore: project scaffold with pyproject.toml"
```

---

## Task 1: Types Module

**Files:**
- Create: `src/veronica/types.py`
- Create: `tests/test_types.py`

All frozen dataclasses from the design doc. These are the data backbone -- every other module depends on them.

**Step 1: Write tests for type construction and immutability**

```python
# tests/test_types.py
"""Tests for veronica.types -- all frozen dataclasses."""
from __future__ import annotations

import time

import pytest

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


class TestStepIntent:
    def test_construction(self) -> None:
        intent = StepIntent(
            step_id="s1",
            request_id="r1",
            chain_id="c1",
            kind="llm",
            model="gpt-4",
            tool_name=None,
            timeout_ms=30_000,
            metadata={},
        )
        assert intent.step_id == "s1"
        assert intent.kind == "llm"

    def test_frozen(self) -> None:
        intent = StepIntent(
            step_id="s1", request_id="r1", chain_id="c1",
            kind="llm", model=None, tool_name=None,
            timeout_ms=0, metadata={},
        )
        with pytest.raises(AttributeError):
            intent.step_id = "s2"  # type: ignore[misc]


class TestStepOutcome:
    def test_construction(self) -> None:
        outcome = StepOutcome(
            step_id="s1",
            request_id="r1",
            chain_id="c1",
            kind="llm",
            status="ok",
            cost_usd=0.005,
            tokens_in=100,
            tokens_out=50,
            elapsed_ms=123.4,
            model="gpt-4",
            events=(),
            timestamp_ms=int(time.time() * 1000),
        )
        assert outcome.status == "ok"
        assert outcome.cost_usd == 0.005


class TestPolicyConfig:
    def test_minimal(self) -> None:
        pc = PolicyConfig(
            chain_id="c1",
            ceiling_usd=1.0,
            on_exceed="halt",
            issued_at=time.time(),
        )
        assert pc.ceiling_usd == 1.0
        assert pc.ceiling_tokens_out is None

    def test_to_exec_config(self) -> None:
        pc = PolicyConfig(
            chain_id="c1",
            ceiling_usd=2.50,
            ceiling_steps=30,
            ceiling_tokens_out=50_000,
            on_exceed="halt",
            timeout_ms=30_000,
            issued_at=time.time(),
        )
        ec = pc.to_exec_config()
        assert ec.max_cost_usd == 2.50
        assert ec.max_steps == 30
        assert ec.timeout_ms == 30_000

    def test_to_exec_config_defaults(self) -> None:
        """None fields get safe defaults."""
        pc = PolicyConfig(
            chain_id="c1",
            ceiling_usd=1.0,
            on_exceed="halt",
            issued_at=time.time(),
        )
        ec = pc.to_exec_config()
        assert ec.max_steps > 0
        assert ec.max_retries_total > 0

    def test_frozen(self) -> None:
        pc = PolicyConfig(
            chain_id="c1", ceiling_usd=1.0,
            on_exceed="halt", issued_at=time.time(),
        )
        with pytest.raises(AttributeError):
            pc.ceiling_usd = 99.0  # type: ignore[misc]


class TestHistoryView:
    def test_empty_history(self) -> None:
        hv = HistoryView(
            chain_id="c1", last_n=(), rolling_cost_usd=0.0,
            failure_streak=0, depth=0, loop_score=0.0,
        )
        assert len(hv.last_n) == 0
        assert hv.failure_streak == 0


class TestAnalysisResult:
    def test_nominal(self) -> None:
        ar = AnalysisResult(
            signals=(), risk_level="nominal", recommendation="continue",
        )
        assert ar.risk_level == "nominal"


class TestSignal:
    def test_construction(self) -> None:
        sig = Signal(kind="cost_acceleration", severity="warning", detail="2x spike")
        assert sig.severity == "warning"


class TestCostEstimate:
    def test_construction(self) -> None:
        ce = CostEstimate(
            estimated_usd=0.01, confidence=0.8,
            model_used="gpt-4", basis="pricing_table",
        )
        assert ce.confidence == 0.8


class TestBudgetState:
    def test_construction(self) -> None:
        bs = BudgetState(
            request_remaining_usd=5.0,
            chain_remaining_usd=2.0,
            window_remaining_steps=10,
        )
        assert bs.chain_remaining_usd == 2.0


class TestDesiredPolicy:
    def test_construction(self) -> None:
        dp = DesiredPolicy(
            chain_id="c1", ceiling_usd=1.0, ceiling_steps=10,
            ceiling_tokens_out=5000, on_exceed="halt",
            fallback_model=None, timeout_ms=30_000, priority=50,
        )
        assert dp.priority == 50


class TestDecisionMeta:
    def test_construction(self) -> None:
        dm = DecisionMeta(
            risk_level="nominal", recommendation="continue",
            degraded=False, stage_time_ms={"collector": 1.2},
        )
        assert not dm.degraded


class TestStepHandle:
    def test_construction(self) -> None:
        intent = StepIntent(
            step_id="s1", request_id="r1", chain_id="c1",
            kind="llm", model="gpt-4", tool_name=None,
            timeout_ms=30_000, metadata={},
        )
        pc = PolicyConfig(
            chain_id="c1", ceiling_usd=1.0,
            on_exceed="halt", issued_at=time.time(),
        )
        dp = DesiredPolicy(
            chain_id="c1", ceiling_usd=1.0, ceiling_steps=10,
            ceiling_tokens_out=5000, on_exceed="halt",
            fallback_model=None, timeout_ms=30_000, priority=50,
        )
        ce = CostEstimate(
            estimated_usd=0.01, confidence=0.8,
            model_used="gpt-4", basis="pricing_table",
        )
        dm = DecisionMeta(
            risk_level="nominal", recommendation="continue",
            degraded=False, stage_time_ms={},
        )
        handle = StepHandle(
            intent=intent, policy=pc, desired=dp,
            cost=ce, decision_meta=dm,
        )
        assert handle.intent.step_id == "s1"
        assert handle.policy.ceiling_usd == 1.0
```

**Step 2: Run tests to verify they fail**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_types.py -v`
Expected: FAIL -- `ModuleNotFoundError: No module named 'veronica.types'`

**Step 3: Implement types.py**

```python
# src/veronica/types.py
"""VERONICA OS data types -- all frozen dataclasses."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from veronica_core.containment.execution_context import ExecutionConfig
from veronica_core.shield.event import SafetyEvent


@dataclass(frozen=True)
class StepIntent:
    """Pre-execution declaration. What the application intends to do."""

    step_id: str
    request_id: str
    chain_id: str
    kind: Literal["llm", "tool", "system"]
    model: str | None
    tool_name: str | None
    timeout_ms: int
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class StepOutcome:
    """Post-execution record. What actually happened."""

    step_id: str
    request_id: str
    chain_id: str
    kind: Literal["llm", "tool", "system"]
    status: Literal["ok", "halted", "error", "timeout"]
    cost_usd: float
    tokens_in: int
    tokens_out: int
    elapsed_ms: float
    model: str | None
    events: tuple[SafetyEvent, ...]
    timestamp_ms: int


@dataclass(frozen=True)
class HistoryView:
    """Lightweight history slice. Statistics, not raw logs."""

    chain_id: str
    last_n: tuple[StepOutcome, ...]
    rolling_cost_usd: float
    failure_streak: int
    depth: int
    loop_score: float


@dataclass(frozen=True)
class Signal:
    """One detected pattern."""

    kind: str
    severity: Literal["info", "warning", "critical"]
    detail: str


@dataclass(frozen=True)
class AnalysisResult:
    """Analyzer output. Detected patterns from intent vs outcome."""

    signals: tuple[Signal, ...]
    risk_level: Literal["nominal", "elevated", "critical"]
    recommendation: Literal["continue", "tighten", "loosen", "halt"]


@dataclass(frozen=True)
class CostEstimate:
    """CostModel output. Predicted cost of the next step."""

    estimated_usd: float
    confidence: float
    model_used: str
    basis: Literal["historical", "pricing_table", "fallback"]


@dataclass(frozen=True)
class BudgetState:
    """Current budget position. Planner's input."""

    request_remaining_usd: float
    chain_remaining_usd: float
    window_remaining_steps: int


@dataclass(frozen=True)
class DesiredPolicy:
    """Planner output. One chain's desired limits (local optimum)."""

    chain_id: str
    ceiling_usd: float
    ceiling_steps: int
    ceiling_tokens_out: int
    on_exceed: Literal["halt", "degrade", "queue"]
    fallback_model: str | None
    timeout_ms: int
    priority: int


@dataclass(frozen=True)
class DecisionMeta:
    """Audit record. Why this PolicyConfig was chosen."""

    risk_level: str
    recommendation: str
    degraded: bool
    stage_time_ms: Mapping[str, float]


# --- PolicyConfig: the contract between OS and Engine ---

_DEFAULT_STEPS = 100
_DEFAULT_RETRIES = 10


@dataclass(frozen=True)
class PolicyConfig:
    """Contract between Planner (VERONICA) and Executor (veronica-core).

    Immutable after issue. The Planner produces it; the Executor enforces it.
    See docs/policy-config.md for the full specification.
    """

    # Required
    chain_id: str
    ceiling_usd: float
    on_exceed: Literal["halt", "degrade", "queue"]
    issued_at: float

    # Budget (optional)
    ceiling_tokens_out: int | None = None
    ceiling_steps: int | None = None

    # Escalation
    fallback_model: str | None = None

    # Time
    timeout_ms: int | None = None
    rate_window_seconds: float | None = None
    rate_ceiling_calls: int | None = None

    # Arbitration
    priority: int = 50
    deadline_ts: float | None = None

    # Metadata
    expires_at: float | None = None
    planner_version: str | None = None

    def to_exec_config(self) -> ExecutionConfig:
        """Convert to veronica-core ExecutionConfig.

        This is the sole bridge between the OS and the engine.
        None fields receive safe defaults.
        """
        return ExecutionConfig(
            max_cost_usd=self.ceiling_usd,
            max_steps=self.ceiling_steps if self.ceiling_steps is not None else _DEFAULT_STEPS,
            max_retries_total=_DEFAULT_RETRIES,
            timeout_ms=self.timeout_ms if self.timeout_ms is not None else 0,
        )


@dataclass(frozen=True)
class StepHandle:
    """Returned by before_step. Passed to after_step."""

    intent: StepIntent
    policy: PolicyConfig
    desired: DesiredPolicy
    cost: CostEstimate
    decision_meta: DecisionMeta
```

**Step 4: Run tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_types.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/veronica/types.py tests/test_types.py
git commit -m "feat: add all frozen dataclass types and PolicyConfig.to_exec_config bridge"
```

---

## Task 2: Protocols Module

**Files:**
- Create: `src/veronica/protocols.py`
- Create: `tests/test_protocols.py`

**Step 1: Write tests for Protocol structural subtyping**

```python
# tests/test_protocols.py
"""Tests for veronica.protocols -- structural subtyping checks."""
from __future__ import annotations

from veronica.protocols import (
    AnalyzerProtocol,
    ArbiterProtocol,
    CollectorProtocol,
    CostModelProtocol,
    EventEmitterProtocol,
    PlannerProtocol,
    StoreProtocol,
)


class _FakeCollector:
    def collect(self, snapshot):
        return None


class _FakeAnalyzer:
    def analyze(self, intent, outcome, history):
        return None


class _FakeCostModel:
    def estimate(self, intent, history, last_analysis):
        return None


class _FakePlanner:
    def plan(self, analysis, cost, budget):
        return None


class _FakeArbiter:
    def arbitrate(self, desires, budget_remaining_usd):
        return {}


class _FakeStore:
    def commit(self, outcome, analysis, cost, desired, policy, meta):
        pass

    def build_history(self, chain_id, limit=50):
        return None


class _FakeEmitter:
    def emit(self, event_type, payload):
        pass


def test_collector_structural() -> None:
    assert isinstance(_FakeCollector(), CollectorProtocol)


def test_analyzer_structural() -> None:
    assert isinstance(_FakeAnalyzer(), AnalyzerProtocol)


def test_cost_model_structural() -> None:
    assert isinstance(_FakeCostModel(), CostModelProtocol)


def test_planner_structural() -> None:
    assert isinstance(_FakePlanner(), PlannerProtocol)


def test_arbiter_structural() -> None:
    assert isinstance(_FakeArbiter(), ArbiterProtocol)


def test_store_structural() -> None:
    assert isinstance(_FakeStore(), StoreProtocol)


def test_emitter_structural() -> None:
    assert isinstance(_FakeEmitter(), EventEmitterProtocol)
```

**Step 2: Run tests to verify they fail**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_protocols.py -v`
Expected: FAIL -- `ModuleNotFoundError`

**Step 3: Implement protocols.py**

```python
# src/veronica/protocols.py
"""VERONICA OS protocols -- runtime_checkable interfaces for pipeline stages."""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from veronica_core.containment.execution_context import ContextSnapshot

from veronica.types import (
    AnalysisResult,
    BudgetState,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    HistoryView,
    PolicyConfig,
    StepIntent,
    StepOutcome,
)


@runtime_checkable
class CollectorProtocol(Protocol):
    def collect(self, snapshot: ContextSnapshot) -> StepOutcome: ...


@runtime_checkable
class AnalyzerProtocol(Protocol):
    def analyze(
        self,
        intent: StepIntent,
        outcome: StepOutcome,
        history: HistoryView,
    ) -> AnalysisResult: ...


@runtime_checkable
class CostModelProtocol(Protocol):
    def estimate(
        self,
        intent: StepIntent,
        history: HistoryView,
        last_analysis: AnalysisResult | None,
    ) -> CostEstimate: ...


@runtime_checkable
class PlannerProtocol(Protocol):
    def plan(
        self,
        analysis: AnalysisResult | None,
        cost: CostEstimate,
        budget: BudgetState,
    ) -> DesiredPolicy: ...


@runtime_checkable
class ArbiterProtocol(Protocol):
    def arbitrate(
        self,
        desires: Sequence[DesiredPolicy],
        budget_remaining_usd: float,
    ) -> Mapping[str, PolicyConfig]: ...


@runtime_checkable
class StoreProtocol(Protocol):
    def commit(
        self,
        outcome: StepOutcome,
        analysis: AnalysisResult,
        cost: CostEstimate,
        desired: DesiredPolicy,
        policy: PolicyConfig,
        meta: DecisionMeta,
    ) -> None: ...

    def build_history(self, chain_id: str, limit: int = 50) -> HistoryView: ...


@runtime_checkable
class EventEmitterProtocol(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None: ...
```

**Step 4: Run tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_protocols.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/veronica/protocols.py tests/test_protocols.py
git commit -m "feat: add all 7 Protocol definitions (runtime_checkable)"
```

---

## Task 3: MemoryStore and SimpleCollector

**Files:**
- Create: `src/veronica/store.py`
- Create: `src/veronica/collector.py`
- Create: `tests/test_store.py`
- Create: `tests/test_collector.py`

These are the data foundation. MemoryStore is the in-memory StoreProtocol for testing. SimpleCollector maps ContextSnapshot to StepOutcome.

**Step 1: Write MemoryStore tests**

```python
# tests/test_store.py
"""Tests for veronica.store -- MemoryStore and FileStore."""
from __future__ import annotations

import time

from veronica.store import MemoryStore
from veronica.types import (
    AnalysisResult,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    HistoryView,
    PolicyConfig,
    StepOutcome,
)


def _make_outcome(
    step_id: str = "s1",
    chain_id: str = "c1",
    status: str = "ok",
    cost_usd: float = 0.005,
) -> StepOutcome:
    return StepOutcome(
        step_id=step_id, request_id="r1", chain_id=chain_id,
        kind="llm", status=status, cost_usd=cost_usd,
        tokens_in=100, tokens_out=50, elapsed_ms=100.0,
        model="gpt-4", events=(), timestamp_ms=int(time.time() * 1000),
    )


def _make_analysis(risk: str = "nominal", rec: str = "continue") -> AnalysisResult:
    return AnalysisResult(signals=(), risk_level=risk, recommendation=rec)


def _make_cost() -> CostEstimate:
    return CostEstimate(
        estimated_usd=0.01, confidence=0.8,
        model_used="gpt-4", basis="pricing_table",
    )


def _make_desired(chain_id: str = "c1") -> DesiredPolicy:
    return DesiredPolicy(
        chain_id=chain_id, ceiling_usd=1.0, ceiling_steps=10,
        ceiling_tokens_out=5000, on_exceed="halt",
        fallback_model=None, timeout_ms=30_000, priority=50,
    )


def _make_policy(chain_id: str = "c1") -> PolicyConfig:
    return PolicyConfig(
        chain_id=chain_id, ceiling_usd=1.0,
        on_exceed="halt", issued_at=time.time(),
    )


def _make_meta() -> DecisionMeta:
    return DecisionMeta(
        risk_level="nominal", recommendation="continue",
        degraded=False, stage_time_ms={},
    )


class TestMemoryStore:
    def test_empty_history(self) -> None:
        store = MemoryStore()
        hv = store.build_history("c1")
        assert hv.chain_id == "c1"
        assert len(hv.last_n) == 0
        assert hv.rolling_cost_usd == 0.0
        assert hv.failure_streak == 0

    def test_commit_and_history(self) -> None:
        store = MemoryStore()
        store.commit(
            _make_outcome(), _make_analysis(), _make_cost(),
            _make_desired(), _make_policy(), _make_meta(),
        )
        hv = store.build_history("c1")
        assert len(hv.last_n) == 1
        assert hv.rolling_cost_usd == 0.005

    def test_history_limit(self) -> None:
        store = MemoryStore()
        for i in range(60):
            store.commit(
                _make_outcome(step_id=f"s{i}"),
                _make_analysis(), _make_cost(),
                _make_desired(), _make_policy(), _make_meta(),
            )
        hv = store.build_history("c1", limit=50)
        assert len(hv.last_n) == 50

    def test_failure_streak(self) -> None:
        store = MemoryStore()
        for _ in range(3):
            store.commit(
                _make_outcome(status="error"),
                _make_analysis(), _make_cost(),
                _make_desired(), _make_policy(), _make_meta(),
            )
        hv = store.build_history("c1")
        assert hv.failure_streak == 3

    def test_failure_streak_resets(self) -> None:
        store = MemoryStore()
        store.commit(
            _make_outcome(status="error"),
            _make_analysis(), _make_cost(),
            _make_desired(), _make_policy(), _make_meta(),
        )
        store.commit(
            _make_outcome(status="ok"),
            _make_analysis(), _make_cost(),
            _make_desired(), _make_policy(), _make_meta(),
        )
        hv = store.build_history("c1")
        assert hv.failure_streak == 0

    def test_separate_chains(self) -> None:
        store = MemoryStore()
        store.commit(
            _make_outcome(chain_id="c1"),
            _make_analysis(), _make_cost(),
            _make_desired("c1"), _make_policy("c1"), _make_meta(),
        )
        store.commit(
            _make_outcome(chain_id="c2"),
            _make_analysis(), _make_cost(),
            _make_desired("c2"), _make_policy("c2"), _make_meta(),
        )
        assert len(store.build_history("c1").last_n) == 1
        assert len(store.build_history("c2").last_n) == 1
```

**Step 2: Write SimpleCollector tests**

```python
# tests/test_collector.py
"""Tests for veronica.collector -- SimpleCollector."""
from __future__ import annotations

from datetime import datetime, timezone

from veronica_core.containment.execution_context import ContextSnapshot, NodeRecord

from veronica.collector import SimpleCollector


def _make_snapshot(
    chain_id: str = "c1",
    cost: float = 0.01,
    step_count: int = 1,
) -> ContextSnapshot:
    node = NodeRecord(
        node_id="n1",
        parent_id=None,
        kind="llm",
        operation_name="test_op",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc),
        status="ok",
        cost_usd=cost,
        retries_used=0,
    )
    return ContextSnapshot(
        chain_id=chain_id,
        request_id="r1",
        step_count=step_count,
        cost_usd_accumulated=cost,
        retries_used=0,
        aborted=False,
        abort_reason=None,
        elapsed_ms=100.0,
        nodes=[node],
        events=[],
    )


class TestSimpleCollector:
    def test_collect_basic(self) -> None:
        collector = SimpleCollector()
        snapshot = _make_snapshot(cost=0.01)
        outcome = collector.collect(snapshot)
        assert outcome.chain_id == "c1"
        assert outcome.cost_usd == 0.01
        assert outcome.status == "ok"
        assert outcome.kind == "llm"

    def test_collect_halted(self) -> None:
        node = NodeRecord(
            node_id="n1", parent_id=None, kind="llm",
            operation_name="op", start_ts=datetime.now(timezone.utc),
            end_ts=datetime.now(timezone.utc), status="halted",
            cost_usd=0.0, retries_used=0,
        )
        snapshot = ContextSnapshot(
            chain_id="c1", request_id="r1", step_count=1,
            cost_usd_accumulated=0.5, retries_used=0,
            aborted=False, abort_reason=None,
            elapsed_ms=50.0, nodes=[node], events=[],
        )
        outcome = SimpleCollector().collect(snapshot)
        assert outcome.status == "halted"

    def test_collect_no_nodes(self) -> None:
        snapshot = ContextSnapshot(
            chain_id="c1", request_id="r1", step_count=0,
            cost_usd_accumulated=0.0, retries_used=0,
            aborted=False, abort_reason=None,
            elapsed_ms=0.0, nodes=[], events=[],
        )
        outcome = SimpleCollector().collect(snapshot)
        assert outcome.kind == "system"
        assert outcome.status == "ok"
```

**Step 3: Run tests to verify they fail**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_store.py tests/test_collector.py -v`
Expected: FAIL

**Step 4: Implement store.py**

```python
# src/veronica/store.py
"""VERONICA OS store implementations."""
from __future__ import annotations

from collections import defaultdict
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


class MemoryStore:
    """In-memory StoreProtocol implementation.

    Stores step outcomes per chain_id. Suitable for testing
    and single-process use. No persistence across restarts.
    """

    def __init__(self) -> None:
        self._chains: dict[str, list[StepOutcome]] = defaultdict(list)

    def commit(
        self,
        outcome: StepOutcome,
        analysis: AnalysisResult,
        cost: CostEstimate,
        desired: DesiredPolicy,
        policy: PolicyConfig,
        meta: DecisionMeta,
    ) -> None:
        self._chains[outcome.chain_id].append(outcome)

    def build_history(self, chain_id: str, limit: int = 50) -> HistoryView:
        outcomes = self._chains.get(chain_id, [])
        last_n = tuple(outcomes[-limit:])

        rolling_cost = sum(o.cost_usd for o in last_n)

        failure_streak = 0
        for o in reversed(last_n):
            if o.status != "ok":
                failure_streak += 1
            else:
                break

        depth = len(last_n)
        loop_score = 0.0

        return HistoryView(
            chain_id=chain_id,
            last_n=last_n,
            rolling_cost_usd=rolling_cost,
            failure_streak=failure_streak,
            depth=depth,
            loop_score=loop_score,
        )
```

**Step 5: Implement collector.py**

```python
# src/veronica/collector.py
"""VERONICA OS collector -- maps ContextSnapshot to StepOutcome."""
from __future__ import annotations

import time

from veronica_core.containment.execution_context import ContextSnapshot

from veronica.types import StepOutcome


class SimpleCollector:
    """Maps a ContextSnapshot to a StepOutcome.

    Extracts the most recent node from the snapshot. If no nodes
    exist, produces a synthetic "system" outcome.
    """

    def collect(self, snapshot: ContextSnapshot) -> StepOutcome:
        now_ms = int(time.time() * 1000)

        if not snapshot.nodes:
            return StepOutcome(
                step_id=f"{snapshot.chain_id}-{snapshot.step_count}",
                request_id=snapshot.request_id,
                chain_id=snapshot.chain_id,
                kind="system",
                status="ok",
                cost_usd=snapshot.cost_usd_accumulated,
                tokens_in=0,
                tokens_out=0,
                elapsed_ms=snapshot.elapsed_ms,
                model=None,
                events=tuple(snapshot.events),
                timestamp_ms=now_ms,
            )

        last_node = snapshot.nodes[-1]
        return StepOutcome(
            step_id=last_node.node_id,
            request_id=snapshot.request_id,
            chain_id=snapshot.chain_id,
            kind=last_node.kind,
            status=last_node.status if last_node.status in ("ok", "halted", "error", "timeout") else "error",
            cost_usd=last_node.cost_usd,
            tokens_in=0,
            tokens_out=0,
            elapsed_ms=snapshot.elapsed_ms,
            model=None,
            events=tuple(snapshot.events),
            timestamp_ms=now_ms,
        )
```

**Step 6: Run tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_store.py tests/test_collector.py -v`
Expected: ALL PASS

**Step 7: Commit**

```bash
git add src/veronica/store.py src/veronica/collector.py tests/test_store.py tests/test_collector.py
git commit -m "feat: add MemoryStore and SimpleCollector"
```

---

## Task 4: RuleAnalyzer

**Files:**
- Create: `src/veronica/analyzer.py`
- Create: `tests/test_analyzer.py`

3 rules from planner.md: halt tightening, clean run loosening, depth guard.

**Step 1: Write tests**

```python
# tests/test_analyzer.py
"""Tests for veronica.analyzer -- RuleAnalyzer with 3 rules."""
from __future__ import annotations

import time

from veronica.analyzer import RuleAnalyzer
from veronica.types import HistoryView, StepIntent, StepOutcome


def _intent(kind: str = "llm", model: str = "gpt-4") -> StepIntent:
    return StepIntent(
        step_id="s1", request_id="r1", chain_id="c1",
        kind=kind, model=model, tool_name=None,
        timeout_ms=30_000, metadata={},
    )


def _outcome(status: str = "ok", cost: float = 0.005) -> StepOutcome:
    return StepOutcome(
        step_id="s1", request_id="r1", chain_id="c1",
        kind="llm", status=status, cost_usd=cost,
        tokens_in=100, tokens_out=50, elapsed_ms=100.0,
        model="gpt-4", events=(),
        timestamp_ms=int(time.time() * 1000),
    )


def _history(
    failure_streak: int = 0,
    depth: int = 1,
    rolling_cost: float = 0.01,
) -> HistoryView:
    return HistoryView(
        chain_id="c1", last_n=(), rolling_cost_usd=rolling_cost,
        failure_streak=failure_streak, depth=depth, loop_score=0.0,
    )


class TestRuleAnalyzer:
    def test_nominal_clean_run(self) -> None:
        """Rule 2: clean run -> loosen."""
        analyzer = RuleAnalyzer()
        result = analyzer.analyze(_intent(), _outcome("ok"), _history())
        assert result.risk_level == "nominal"
        assert result.recommendation == "loosen"

    def test_halt_triggers_tighten(self) -> None:
        """Rule 1: halt -> tighten."""
        analyzer = RuleAnalyzer()
        result = analyzer.analyze(_intent(), _outcome("halted"), _history())
        assert result.recommendation == "tighten"

    def test_depth_guard(self) -> None:
        """Rule 3: depth >= 8 -> halt recommendation."""
        analyzer = RuleAnalyzer()
        result = analyzer.analyze(
            _intent(), _outcome("ok"), _history(depth=8),
        )
        assert result.recommendation == "halt"
        assert result.risk_level == "critical"
        signals = [s.kind for s in result.signals]
        assert "depth_anomaly" in signals

    def test_failure_streak_elevated(self) -> None:
        """Consecutive failures -> elevated risk."""
        analyzer = RuleAnalyzer()
        result = analyzer.analyze(
            _intent(), _outcome("error"), _history(failure_streak=3),
        )
        assert result.risk_level in ("elevated", "critical")
        assert result.recommendation == "tighten"

    def test_intent_model_mismatch(self) -> None:
        """Intent model != outcome model -> intent_deviation signal."""
        analyzer = RuleAnalyzer()
        outcome = StepOutcome(
            step_id="s1", request_id="r1", chain_id="c1",
            kind="tool", status="ok", cost_usd=0.0,
            tokens_in=0, tokens_out=0, elapsed_ms=10.0,
            model=None, events=(),
            timestamp_ms=int(time.time() * 1000),
        )
        result = analyzer.analyze(
            _intent(kind="llm"), outcome, _history(),
        )
        signals = [s.kind for s in result.signals]
        assert "intent_deviation" in signals
```

**Step 2: Run tests to verify they fail**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_analyzer.py -v`
Expected: FAIL

**Step 3: Implement analyzer.py**

```python
# src/veronica/analyzer.py
"""VERONICA OS analyzer -- RuleAnalyzer with 3 ceiling adjustment rules."""
from __future__ import annotations

from veronica.types import AnalysisResult, HistoryView, Signal, StepIntent, StepOutcome

_DEPTH_THRESHOLD = 8
_FAILURE_STREAK_ELEVATED = 2
_FAILURE_STREAK_CRITICAL = 5


class RuleAnalyzer:
    """Phase 1 rule-based analyzer.

    Rule 1 -- Halt tightening: if outcome is halted, recommend tighten.
    Rule 2 -- Clean run loosening: if outcome is ok, recommend loosen.
    Rule 3 -- Depth guard: if depth >= 8, recommend halt (critical).

    Signals are emitted for: depth_anomaly, repeated_failure, intent_deviation.
    """

    def analyze(
        self,
        intent: StepIntent,
        outcome: StepOutcome,
        history: HistoryView,
    ) -> AnalysisResult:
        signals: list[Signal] = []
        risk_level = "nominal"
        recommendation = "continue"

        # --- Signal detection ---

        # Intent vs outcome kind mismatch
        if intent.kind != outcome.kind:
            signals.append(Signal(
                kind="intent_deviation",
                severity="warning",
                detail=f"intended {intent.kind}, got {outcome.kind}",
            ))

        # Failure streak
        streak = history.failure_streak + (1 if outcome.status != "ok" else 0)
        if streak >= _FAILURE_STREAK_CRITICAL:
            signals.append(Signal(
                kind="repeated_failure",
                severity="critical",
                detail=f"{streak} consecutive failures",
            ))
            risk_level = "critical"
        elif streak >= _FAILURE_STREAK_ELEVATED:
            signals.append(Signal(
                kind="repeated_failure",
                severity="warning",
                detail=f"{streak} consecutive failures",
            ))
            risk_level = "elevated"

        # Depth guard (Rule 3 -- takes precedence)
        if history.depth >= _DEPTH_THRESHOLD:
            signals.append(Signal(
                kind="depth_anomaly",
                severity="critical",
                detail=f"depth {history.depth} >= {_DEPTH_THRESHOLD}",
            ))
            risk_level = "critical"
            recommendation = "halt"
            return AnalysisResult(
                signals=tuple(signals),
                risk_level=risk_level,
                recommendation=recommendation,
            )

        # --- Recommendation rules ---

        # Rule 1: halt tightening
        if outcome.status in ("halted", "error", "timeout"):
            recommendation = "tighten"
            if risk_level == "nominal":
                risk_level = "elevated"
        # Rule 2: clean run loosening
        elif outcome.status == "ok" and history.failure_streak == 0:
            recommendation = "loosen"

        return AnalysisResult(
            signals=tuple(signals),
            risk_level=risk_level,
            recommendation=recommendation,
        )
```

**Step 4: Run tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_analyzer.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/veronica/analyzer.py tests/test_analyzer.py
git commit -m "feat: add RuleAnalyzer with 3 ceiling adjustment rules"
```

---

## Task 5: TableCostModel

**Files:**
- Create: `src/veronica/cost_model.py`
- Create: `tests/test_cost_model.py`

Delegates to veronica-core's `estimate_cost_usd` for pricing table lookups.

**Step 1: Write tests**

```python
# tests/test_cost_model.py
"""Tests for veronica.cost_model -- TableCostModel."""
from __future__ import annotations

import time

from veronica.cost_model import TableCostModel
from veronica.types import AnalysisResult, HistoryView, StepIntent


def _intent(model: str = "gpt-4") -> StepIntent:
    return StepIntent(
        step_id="s1", request_id="r1", chain_id="c1",
        kind="llm", model=model, tool_name=None,
        timeout_ms=30_000, metadata={},
    )


def _history() -> HistoryView:
    return HistoryView(
        chain_id="c1", last_n=(), rolling_cost_usd=0.0,
        failure_streak=0, depth=0, loop_score=0.0,
    )


class TestTableCostModel:
    def test_known_model(self) -> None:
        cm = TableCostModel()
        est = cm.estimate(_intent("gpt-4"), _history(), None)
        assert est.estimated_usd > 0
        assert est.basis == "pricing_table"
        assert est.model_used == "gpt-4"
        assert 0.0 <= est.confidence <= 1.0

    def test_unknown_model_fallback(self) -> None:
        cm = TableCostModel()
        est = cm.estimate(_intent("unknown-model-xyz"), _history(), None)
        assert est.estimated_usd > 0
        assert est.basis == "fallback"

    def test_tool_call_zero(self) -> None:
        intent = StepIntent(
            step_id="s1", request_id="r1", chain_id="c1",
            kind="tool", model=None, tool_name="web_search",
            timeout_ms=30_000, metadata={},
        )
        cm = TableCostModel()
        est = cm.estimate(intent, _history(), None)
        assert est.estimated_usd == 0.0
        assert est.basis == "pricing_table"

    def test_no_model_fallback(self) -> None:
        intent = StepIntent(
            step_id="s1", request_id="r1", chain_id="c1",
            kind="llm", model=None, tool_name=None,
            timeout_ms=30_000, metadata={},
        )
        cm = TableCostModel()
        est = cm.estimate(intent, _history(), None)
        assert est.basis == "fallback"
```

**Step 2: Run tests to verify they fail**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_cost_model.py -v`
Expected: FAIL

**Step 3: Implement cost_model.py**

```python
# src/veronica/cost_model.py
"""VERONICA OS cost model -- static pricing table via veronica-core."""
from __future__ import annotations

from veronica_core.pricing import PRICING_TABLE, estimate_cost_usd

from veronica.types import AnalysisResult, CostEstimate, HistoryView, StepIntent

_DEFAULT_TOKENS_IN = 500
_DEFAULT_TOKENS_OUT = 200
_FALLBACK_COST_USD = 0.01


class TableCostModel:
    """Phase 1 cost model. Uses veronica-core's static pricing table.

    For LLM calls with a known model, estimates cost using the pricing
    table and assumed token counts. For tool calls, returns zero.
    Unknown models receive a conservative fallback estimate.
    """

    def __init__(
        self,
        default_tokens_in: int = _DEFAULT_TOKENS_IN,
        default_tokens_out: int = _DEFAULT_TOKENS_OUT,
        fallback_cost_usd: float = _FALLBACK_COST_USD,
    ) -> None:
        self._default_tokens_in = default_tokens_in
        self._default_tokens_out = default_tokens_out
        self._fallback_cost_usd = fallback_cost_usd

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

        model = intent.model
        if model is None or model not in PRICING_TABLE:
            return CostEstimate(
                estimated_usd=self._fallback_cost_usd,
                confidence=0.3,
                model_used=model or "unknown",
                basis="fallback",
            )

        cost = estimate_cost_usd(
            model, self._default_tokens_in, self._default_tokens_out,
        )
        return CostEstimate(
            estimated_usd=cost,
            confidence=0.7,
            model_used=model,
            basis="pricing_table",
        )
```

**Step 4: Run tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_cost_model.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/veronica/cost_model.py tests/test_cost_model.py
git commit -m "feat: add TableCostModel with veronica-core pricing delegation"
```

---

## Task 6: SimplePlanner

**Files:**
- Create: `src/veronica/planner.py`
- Create: `tests/test_planner.py`

Rule-based ceiling from analysis + cost + budget. Produces DesiredPolicy.

**Step 1: Write tests**

```python
# tests/test_planner.py
"""Tests for veronica.planner -- SimplePlanner."""
from __future__ import annotations

from veronica.planner import SimplePlanner
from veronica.types import AnalysisResult, BudgetState, CostEstimate


def _cost(usd: float = 0.01) -> CostEstimate:
    return CostEstimate(
        estimated_usd=usd, confidence=0.7,
        model_used="gpt-4", basis="pricing_table",
    )


def _budget(remaining: float = 10.0, steps: int = 100) -> BudgetState:
    return BudgetState(
        request_remaining_usd=remaining,
        chain_remaining_usd=remaining,
        window_remaining_steps=steps,
    )


def _analysis(rec: str = "continue", risk: str = "nominal") -> AnalysisResult:
    return AnalysisResult(signals=(), risk_level=risk, recommendation=rec)


class TestSimplePlanner:
    def test_initial_no_analysis(self) -> None:
        """First call, no prior analysis -> base ceiling."""
        planner = SimplePlanner(base_ceiling_usd=1.0)
        dp = planner.plan(None, _cost(), _budget())
        assert dp.ceiling_usd == 1.0
        assert dp.on_exceed == "halt"

    def test_tighten_reduces_ceiling(self) -> None:
        """Rule 1: tighten -> -10%."""
        planner = SimplePlanner(base_ceiling_usd=1.0)
        dp = planner.plan(_analysis("tighten"), _cost(), _budget())
        assert dp.ceiling_usd == pytest.approx(0.90)

    def test_loosen_increases_ceiling(self) -> None:
        """Rule 2: loosen -> +5%."""
        planner = SimplePlanner(base_ceiling_usd=1.0)
        dp = planner.plan(_analysis("loosen"), _cost(), _budget())
        assert dp.ceiling_usd == pytest.approx(1.05)

    def test_halt_forces_halt_on_exceed(self) -> None:
        """Rule 3: halt analysis -> on_exceed=halt."""
        planner = SimplePlanner(base_ceiling_usd=1.0, default_on_exceed="degrade")
        dp = planner.plan(_analysis("halt", "critical"), _cost(), _budget())
        assert dp.on_exceed == "halt"

    def test_ceiling_clamped_to_max(self) -> None:
        planner = SimplePlanner(base_ceiling_usd=9.5, max_ceiling_usd=10.0)
        dp = planner.plan(_analysis("loosen"), _cost(), _budget())
        assert dp.ceiling_usd <= 10.0

    def test_ceiling_clamped_to_min(self) -> None:
        planner = SimplePlanner(base_ceiling_usd=0.15, min_ceiling_usd=0.10)
        dp = planner.plan(_analysis("tighten"), _cost(), _budget())
        assert dp.ceiling_usd >= 0.10

    def test_ceiling_clamped_to_remaining_budget(self) -> None:
        planner = SimplePlanner(base_ceiling_usd=5.0)
        dp = planner.plan(None, _cost(), _budget(remaining=2.0))
        assert dp.ceiling_usd <= 2.0

    def test_stateful_ceiling_drift(self) -> None:
        """Multiple calls accumulate ceiling changes."""
        planner = SimplePlanner(base_ceiling_usd=1.0)
        # Tighten twice
        planner.plan(_analysis("tighten"), _cost(), _budget())
        dp = planner.plan(_analysis("tighten"), _cost(), _budget())
        assert dp.ceiling_usd == pytest.approx(0.81)  # 1.0 * 0.9 * 0.9
```

Note: add `import pytest` at top of test file.

**Step 2: Run tests to verify they fail**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_planner.py -v`
Expected: FAIL

**Step 3: Implement planner.py**

```python
# src/veronica/planner.py
"""VERONICA OS planner -- SimplePlanner with 3 ceiling adjustment rules."""
from __future__ import annotations

from veronica.types import AnalysisResult, BudgetState, CostEstimate, DesiredPolicy

_TIGHTEN_FACTOR = 0.90  # -10%
_LOOSEN_FACTOR = 1.05   # +5%
_DEFAULT_STEPS = 100
_DEFAULT_TOKENS_OUT = 50_000


class SimplePlanner:
    """Phase 1 rule-based planner.

    Maintains a drifting effective ceiling that adjusts based on
    AnalysisResult recommendations:
    - tighten: multiply by 0.90
    - loosen: multiply by 1.05
    - halt: force on_exceed="halt"

    The ceiling is clamped to [min, max] and to remaining budget.
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

    def plan(
        self,
        analysis: AnalysisResult | None,
        cost: CostEstimate,
        budget: BudgetState,
    ) -> DesiredPolicy:
        ceiling = self._effective_ceiling
        on_exceed = self._default_on_exceed

        if analysis is not None:
            if analysis.recommendation == "tighten":
                ceiling *= _TIGHTEN_FACTOR
            elif analysis.recommendation == "loosen":
                ceiling *= _LOOSEN_FACTOR
            elif analysis.recommendation == "halt":
                on_exceed = "halt"

        # Clamp
        ceiling = max(self._min, min(self._max, ceiling))
        ceiling = min(ceiling, budget.chain_remaining_usd)

        # Store for next call
        self._effective_ceiling = ceiling

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
```

**Step 4: Run tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_planner.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/veronica/planner.py tests/test_planner.py
git commit -m "feat: add SimplePlanner with stateful ceiling drift"
```

---

## Task 7: PassthroughArbiter and NullEmitter

**Files:**
- Create: `src/veronica/arbiter.py`
- Create: `src/veronica/emitter.py`
- Create: `tests/test_arbiter.py`
- Create: `tests/test_emitter.py`

Minimal implementations. Arbiter converts DesiredPolicy to PolicyConfig. Emitter is no-op.

**Step 1: Write tests**

```python
# tests/test_arbiter.py
"""Tests for veronica.arbiter -- PassthroughArbiter."""
from __future__ import annotations

from veronica.arbiter import PassthroughArbiter
from veronica.types import DesiredPolicy


def _desired(chain_id: str = "c1", ceiling: float = 1.0) -> DesiredPolicy:
    return DesiredPolicy(
        chain_id=chain_id, ceiling_usd=ceiling, ceiling_steps=10,
        ceiling_tokens_out=5000, on_exceed="halt",
        fallback_model=None, timeout_ms=30_000, priority=50,
    )


class TestPassthroughArbiter:
    def test_single_chain(self) -> None:
        arbiter = PassthroughArbiter()
        result = arbiter.arbitrate([_desired("c1")], 10.0)
        assert "c1" in result
        assert result["c1"].ceiling_usd == 1.0
        assert result["c1"].chain_id == "c1"

    def test_multi_chain(self) -> None:
        arbiter = PassthroughArbiter()
        result = arbiter.arbitrate(
            [_desired("c1", 3.0), _desired("c2", 5.0)], 10.0,
        )
        assert len(result) == 2
        assert result["c1"].ceiling_usd == 3.0
        assert result["c2"].ceiling_usd == 5.0

    def test_budget_capping(self) -> None:
        """When desires exceed total budget, cap proportionally."""
        arbiter = PassthroughArbiter()
        result = arbiter.arbitrate(
            [_desired("c1", 8.0), _desired("c2", 8.0)], 10.0,
        )
        total = result["c1"].ceiling_usd + result["c2"].ceiling_usd
        assert total <= 10.0

    def test_empty_desires(self) -> None:
        arbiter = PassthroughArbiter()
        result = arbiter.arbitrate([], 10.0)
        assert result == {}
```

```python
# tests/test_emitter.py
"""Tests for veronica.emitter -- NullEmitter."""
from __future__ import annotations

from veronica.emitter import NullEmitter


class TestNullEmitter:
    def test_emit_noop(self) -> None:
        emitter = NullEmitter()
        emitter.emit("test_event", {"key": "value"})
        # No exception, no side effects

    def test_emit_exception_swallowed(self) -> None:
        """Even if payload is weird, no crash."""
        emitter = NullEmitter()
        emitter.emit("", {})
```

**Step 2: Run tests to verify they fail**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_arbiter.py tests/test_emitter.py -v`
Expected: FAIL

**Step 3: Implement arbiter.py**

```python
# src/veronica/arbiter.py
"""VERONICA OS arbiter -- PassthroughArbiter."""
from __future__ import annotations

import time
from typing import Mapping, Sequence

from veronica.types import DesiredPolicy, PolicyConfig


class PassthroughArbiter:
    """Phase 1 arbiter. Single-chain passthrough, multi-chain proportional cap.

    For a single chain, converts DesiredPolicy to PolicyConfig directly.
    For multiple chains, proportionally scales ceilings if total exceeds budget.
    """

    def arbitrate(
        self,
        desires: Sequence[DesiredPolicy],
        budget_remaining_usd: float,
    ) -> Mapping[str, PolicyConfig]:
        if not desires:
            return {}

        total_desired = sum(d.ceiling_usd for d in desires)
        scale = 1.0
        if total_desired > budget_remaining_usd and total_desired > 0:
            scale = budget_remaining_usd / total_desired

        now = time.time()
        result: dict[str, PolicyConfig] = {}
        for d in desires:
            result[d.chain_id] = PolicyConfig(
                chain_id=d.chain_id,
                ceiling_usd=d.ceiling_usd * scale,
                ceiling_steps=d.ceiling_steps,
                ceiling_tokens_out=d.ceiling_tokens_out,
                on_exceed=d.on_exceed,
                fallback_model=d.fallback_model,
                timeout_ms=d.timeout_ms,
                priority=d.priority,
                issued_at=now,
                planner_version="0.1.0",
            )
        return result
```

**Step 4: Implement emitter.py**

```python
# src/veronica/emitter.py
"""VERONICA OS emitter -- NullEmitter (no-op)."""
from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)


class NullEmitter:
    """No-op event emitter. Discards all events silently."""

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        pass
```

**Step 5: Run tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_arbiter.py tests/test_emitter.py -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/veronica/arbiter.py src/veronica/emitter.py tests/test_arbiter.py tests/test_emitter.py
git commit -m "feat: add PassthroughArbiter and NullEmitter"
```

---

## Task 8: Time Guard

**Files:**
- Create: `src/veronica/_timeguard.py`
- Create: `tests/test_timeguard.py`

Stage time budget enforcement. Each pipeline stage gets a ms budget; exceeded -> DEGRADE.

**Step 1: Write tests**

```python
# tests/test_timeguard.py
"""Tests for veronica._timeguard -- stage time budget enforcement."""
from __future__ import annotations

import time

from veronica._timeguard import run_with_budget, TimeBudgetExceeded


class TestRunWithBudget:
    def test_fast_function_succeeds(self) -> None:
        result = run_with_budget(lambda: 42, budget_ms=100.0, stage_name="test")
        assert result == 42

    def test_returns_elapsed(self) -> None:
        result, elapsed = run_with_budget(
            lambda: 42, budget_ms=100.0, stage_name="test",
            return_elapsed=True,
        )
        assert result == 42
        assert elapsed >= 0.0

    def test_slow_function_raises(self) -> None:
        def slow():
            time.sleep(0.2)
            return 99

        # Budget is 10ms but function takes 200ms.
        # Note: we check AFTER completion (no thread kill).
        import pytest
        with pytest.raises(TimeBudgetExceeded):
            run_with_budget(slow, budget_ms=10.0, stage_name="slow_test")
```

**Step 2: Run tests to verify they fail**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_timeguard.py -v`
Expected: FAIL

**Step 3: Implement _timeguard.py**

```python
# src/veronica/_timeguard.py
"""Stage time budget enforcement.

Runs a callable and checks if it exceeded its budget AFTER completion.
Does NOT kill threads -- just detects overruns for DEGRADE logic.
"""
from __future__ import annotations

import time
from typing import Any, Callable, TypeVar, overload

T = TypeVar("T")


class TimeBudgetExceeded(Exception):
    """Raised when a pipeline stage exceeds its time budget."""

    def __init__(self, stage_name: str, budget_ms: float, actual_ms: float) -> None:
        self.stage_name = stage_name
        self.budget_ms = budget_ms
        self.actual_ms = actual_ms
        super().__init__(
            f"Stage '{stage_name}' exceeded budget: "
            f"{actual_ms:.1f}ms > {budget_ms:.1f}ms"
        )


@overload
def run_with_budget(
    fn: Callable[[], T],
    budget_ms: float,
    stage_name: str,
    *,
    return_elapsed: bool = ...,
) -> T: ...


@overload
def run_with_budget(
    fn: Callable[[], T],
    budget_ms: float,
    stage_name: str,
    *,
    return_elapsed: bool,
) -> tuple[T, float]: ...


def run_with_budget(
    fn: Callable[[], Any],
    budget_ms: float,
    stage_name: str,
    *,
    return_elapsed: bool = False,
) -> Any:
    start = time.monotonic()
    result = fn()
    elapsed_ms = (time.monotonic() - start) * 1000.0

    if elapsed_ms > budget_ms:
        raise TimeBudgetExceeded(stage_name, budget_ms, elapsed_ms)

    if return_elapsed:
        return result, elapsed_ms
    return result
```

**Step 4: Run tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_timeguard.py -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/veronica/_timeguard.py tests/test_timeguard.py
git commit -m "feat: add stage time budget enforcement"
```

---

## Task 9: VeronicaOS Orchestrator

**Files:**
- Create: `src/veronica/os.py`
- Create: `tests/test_os.py`
- Modify: `src/veronica/__init__.py` (re-exports)

The main entry point. Wires all stages together.

**Step 1: Write tests**

```python
# tests/test_os.py
"""Tests for veronica.os -- VeronicaOS orchestrator."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from veronica_core.containment.execution_context import ContextSnapshot, NodeRecord

from veronica.os import VeronicaOS
from veronica.store import MemoryStore
from veronica.types import StepIntent


def _intent(
    step_id: str = "s1",
    chain_id: str = "c1",
    model: str = "gpt-4",
) -> StepIntent:
    return StepIntent(
        step_id=step_id, request_id="r1", chain_id=chain_id,
        kind="llm", model=model, tool_name=None,
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


class TestVeronicaOS:
    def test_before_step_returns_handle(self) -> None:
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        assert handle.intent.step_id == "s1"
        assert handle.policy.ceiling_usd > 0
        assert handle.policy.chain_id == "c1"

    def test_after_step_commits_to_store(self) -> None:
        store = MemoryStore()
        vos = VeronicaOS(store=store)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())
        hv = store.build_history("c1")
        assert len(hv.last_n) == 1

    def test_full_cycle(self) -> None:
        """before_step -> (simulated execution) -> after_step."""
        store = MemoryStore()
        vos = VeronicaOS(store=store)

        # Step 1
        handle1 = vos.before_step(_intent(step_id="s1"))
        vos.after_step(handle1, _snapshot(cost=0.01))

        # Step 2
        handle2 = vos.before_step(_intent(step_id="s2"))
        vos.after_step(handle2, _snapshot(cost=0.02))

        hv = store.build_history("c1")
        assert len(hv.last_n) == 2

    def test_to_exec_config_bridge(self) -> None:
        """PolicyConfig.to_exec_config produces valid ExecutionConfig."""
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        ec = handle.policy.to_exec_config()
        assert ec.max_cost_usd > 0
        assert ec.max_steps > 0

    def test_tighten_after_halt(self) -> None:
        """After a halted step, ceiling should decrease."""
        store = MemoryStore()
        vos = VeronicaOS(store=store)

        handle1 = vos.before_step(_intent())
        ceiling1 = handle1.policy.ceiling_usd

        vos.after_step(handle1, _snapshot(status="halted"))

        handle2 = vos.before_step(_intent(step_id="s2"))
        ceiling2 = handle2.policy.ceiling_usd

        assert ceiling2 < ceiling1

    def test_loosen_after_clean_run(self) -> None:
        """After a clean step, ceiling should increase."""
        store = MemoryStore()
        vos = VeronicaOS(store=store)

        handle1 = vos.before_step(_intent())
        ceiling1 = handle1.policy.ceiling_usd

        vos.after_step(handle1, _snapshot(status="ok"))

        handle2 = vos.before_step(_intent(step_id="s2"))
        ceiling2 = handle2.policy.ceiling_usd

        assert ceiling2 > ceiling1

    def test_custom_components(self) -> None:
        """Accepts custom Protocol implementations."""
        store = MemoryStore()
        vos = VeronicaOS(store=store)
        assert vos is not None

    def test_decision_meta_not_degraded(self) -> None:
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        assert not handle.decision_meta.degraded

    def test_budget_state_defaults(self) -> None:
        """Default budget state allows execution."""
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        assert handle.policy.ceiling_usd > 0
```

**Step 2: Run tests to verify they fail**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_os.py -v`
Expected: FAIL

**Step 3: Implement os.py**

```python
# src/veronica/os.py
"""VERONICA OS -- the main orchestrator."""
from __future__ import annotations

import logging
import time
from typing import Any, Mapping

from veronica_core.containment.execution_context import ContextSnapshot

from veronica._timeguard import TimeBudgetExceeded, run_with_budget
from veronica.analyzer import RuleAnalyzer
from veronica.arbiter import PassthroughArbiter
from veronica.collector import SimpleCollector
from veronica.cost_model import TableCostModel
from veronica.emitter import NullEmitter
from veronica.planner import SimplePlanner
from veronica.protocols import (
    AnalyzerProtocol,
    ArbiterProtocol,
    CollectorProtocol,
    CostModelProtocol,
    EventEmitterProtocol,
    PlannerProtocol,
    StoreProtocol,
)
from veronica.store import MemoryStore
from veronica.types import (
    AnalysisResult,
    BudgetState,
    DecisionMeta,
    DesiredPolicy,
    PolicyConfig,
    StepHandle,
    StepIntent,
)

logger = logging.getLogger(__name__)

_DEFAULT_STAGE_BUDGETS: dict[str, float] = {
    "collector": 5.0,
    "analyzer": 20.0,
    "cost_model": 10.0,
    "planner": 30.0,
    "arbiter": 20.0,
}

_DEFAULT_REQUEST_BUDGET_USD = 100.0


class VeronicaOS:
    """VERONICA Execution OS.

    Synchronous pipeline that sits above veronica-core.
    Decides what limits to set; veronica-core enforces them.
    """

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
    ) -> None:
        self._collector = collector or SimpleCollector()
        self._analyzer = analyzer or RuleAnalyzer()
        self._cost_model = cost_model or TableCostModel()
        self._planner = planner or SimplePlanner()
        self._arbiter = arbiter or PassthroughArbiter()
        self._store = store or MemoryStore()
        self._emitter = emitter or NullEmitter()
        self._budgets = dict(stage_budgets_ms or _DEFAULT_STAGE_BUDGETS)
        self._request_budget_usd = request_budget_usd
        self._last_analysis: AnalysisResult | None = None
        self._total_spent_usd: float = 0.0

    def before_step(self, intent: StepIntent) -> StepHandle:
        """Pre-execution pipeline. Returns a StepHandle carrying the policy."""
        stage_times: dict[str, float] = {}
        degraded = False

        # 1. Build history
        history = self._store.build_history(intent.chain_id)

        # 2. CostModel
        try:
            cost, elapsed = run_with_budget(
                lambda: self._cost_model.estimate(
                    intent, history, self._last_analysis,
                ),
                self._budgets.get("cost_model", 10.0),
                "cost_model",
                return_elapsed=True,
            )
            stage_times["cost_model"] = elapsed
        except TimeBudgetExceeded as e:
            logger.warning("[VERONICA_OS] %s", e)
            degraded = True
            stage_times["cost_model"] = e.actual_ms
            from veronica.types import CostEstimate
            cost = CostEstimate(
                estimated_usd=0.01, confidence=0.1,
                model_used="fallback", basis="fallback",
            )

        # 3. Budget state
        remaining = self._request_budget_usd - self._total_spent_usd
        budget = BudgetState(
            request_remaining_usd=remaining,
            chain_remaining_usd=remaining,
            window_remaining_steps=100,
        )

        # 4. Planner
        try:
            desired, elapsed = run_with_budget(
                lambda: self._planner.plan(
                    self._last_analysis, cost, budget,
                ),
                self._budgets.get("planner", 30.0),
                "planner",
                return_elapsed=True,
            )
            stage_times["planner"] = elapsed
        except TimeBudgetExceeded as e:
            logger.warning("[VERONICA_OS] %s", e)
            degraded = True
            stage_times["planner"] = e.actual_ms
            desired = DesiredPolicy(
                chain_id=intent.chain_id,
                ceiling_usd=min(1.0, remaining),
                ceiling_steps=100,
                ceiling_tokens_out=50_000,
                on_exceed="halt",
                fallback_model=None,
                timeout_ms=intent.timeout_ms,
                priority=50,
            )

        # Fill chain_id from intent
        if not desired.chain_id:
            desired = DesiredPolicy(
                chain_id=intent.chain_id,
                ceiling_usd=desired.ceiling_usd,
                ceiling_steps=desired.ceiling_steps,
                ceiling_tokens_out=desired.ceiling_tokens_out,
                on_exceed=desired.on_exceed,
                fallback_model=desired.fallback_model,
                timeout_ms=desired.timeout_ms,
                priority=desired.priority,
            )

        # 5. Arbiter
        try:
            configs, elapsed = run_with_budget(
                lambda: self._arbiter.arbitrate([desired], remaining),
                self._budgets.get("arbiter", 20.0),
                "arbiter",
                return_elapsed=True,
            )
            stage_times["arbiter"] = elapsed
        except TimeBudgetExceeded as e:
            logger.warning("[VERONICA_OS] %s", e)
            degraded = True
            stage_times["arbiter"] = e.actual_ms
            configs = {intent.chain_id: PolicyConfig(
                chain_id=intent.chain_id,
                ceiling_usd=desired.ceiling_usd,
                on_exceed="halt",
                issued_at=time.time(),
            )}

        policy = configs.get(intent.chain_id, PolicyConfig(
            chain_id=intent.chain_id,
            ceiling_usd=1.0,
            on_exceed="halt",
            issued_at=time.time(),
        ))

        meta = DecisionMeta(
            risk_level=self._last_analysis.risk_level if self._last_analysis else "nominal",
            recommendation=self._last_analysis.recommendation if self._last_analysis else "continue",
            degraded=degraded,
            stage_time_ms=stage_times,
        )

        return StepHandle(
            intent=intent,
            policy=policy,
            desired=desired,
            cost=cost,
            decision_meta=meta,
        )

    def after_step(self, handle: StepHandle, snapshot: ContextSnapshot) -> None:
        """Post-execution pipeline. Commits to Store, emits events."""
        stage_times: dict[str, float] = {}

        # 1. Collector
        try:
            outcome, elapsed = run_with_budget(
                lambda: self._collector.collect(snapshot),
                self._budgets.get("collector", 5.0),
                "collector",
                return_elapsed=True,
            )
            stage_times["collector"] = elapsed
        except TimeBudgetExceeded as e:
            logger.warning("[VERONICA_OS] %s", e)
            stage_times["collector"] = e.actual_ms
            return  # Cannot proceed without outcome

        # 2. History
        history = self._store.build_history(outcome.chain_id)

        # 3. Analyzer
        try:
            analysis, elapsed = run_with_budget(
                lambda: self._analyzer.analyze(
                    handle.intent, outcome, history,
                ),
                self._budgets.get("analyzer", 20.0),
                "analyzer",
                return_elapsed=True,
            )
            stage_times["analyzer"] = elapsed
        except TimeBudgetExceeded as e:
            logger.warning("[VERONICA_OS] %s", e)
            stage_times["analyzer"] = e.actual_ms
            analysis = AnalysisResult(
                signals=(), risk_level="nominal", recommendation="continue",
            )

        # 4. Update state
        self._last_analysis = analysis
        self._total_spent_usd += outcome.cost_usd

        # 5. Store commit (atomic)
        self._store.commit(
            outcome, analysis, handle.cost,
            handle.desired, handle.policy, handle.decision_meta,
        )

        # 6. EventEmitter (fire-and-forget)
        try:
            self._emitter.emit("step_completed", {
                "step_id": outcome.step_id,
                "chain_id": outcome.chain_id,
                "status": outcome.status,
                "cost_usd": outcome.cost_usd,
                "risk_level": analysis.risk_level,
            })
        except Exception:
            logger.debug("[VERONICA_OS] EventEmitter error (swallowed)")
```

**Step 4: Update __init__.py with re-exports**

```python
# src/veronica/__init__.py
"""VERONICA -- Execution OS for LLM systems."""
from __future__ import annotations

from veronica.os import VeronicaOS
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

__version__ = "0.1.0"

__all__ = [
    "VeronicaOS",
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

**Step 5: Run all tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/ -v`
Expected: ALL PASS

**Step 6: Commit**

```bash
git add src/veronica/os.py src/veronica/__init__.py tests/test_os.py
git commit -m "feat: add VeronicaOS orchestrator with StepHandle lifecycle"
```

---

## Task 10: Integration Test and Final Verification

**Files:**
- Create: `tests/test_integration.py`

End-to-end test: VeronicaOS -> before_step -> (mock execution) -> after_step -> verify Store, verify feedback loop.

**Step 1: Write integration test**

```python
# tests/test_integration.py
"""Integration test: full VeronicaOS pipeline with veronica-core types."""
from __future__ import annotations

from datetime import datetime, timezone

from veronica_core.containment.execution_context import (
    ContextSnapshot,
    ExecutionConfig,
    NodeRecord,
)

from veronica import VeronicaOS
from veronica.store import MemoryStore
from veronica.types import StepIntent


def test_full_pipeline_three_steps() -> None:
    """Simulate 3 LLM calls with feedback loop."""
    store = MemoryStore()
    vos = VeronicaOS(store=store)

    ceilings: list[float] = []

    for i in range(3):
        intent = StepIntent(
            step_id=f"s{i}",
            request_id="r1",
            chain_id="c1",
            kind="llm",
            model="gpt-4",
            tool_name=None,
            timeout_ms=30_000,
            metadata={},
        )

        # before_step
        handle = vos.before_step(intent)
        ceilings.append(handle.policy.ceiling_usd)

        # Verify bridge to veronica-core
        ec = handle.policy.to_exec_config()
        assert isinstance(ec, ExecutionConfig)
        assert ec.max_cost_usd == handle.policy.ceiling_usd

        # Simulate execution (mock snapshot)
        node = NodeRecord(
            node_id=f"n{i}",
            parent_id=None,
            kind="llm",
            operation_name=f"step_{i}",
            start_ts=datetime.now(timezone.utc),
            end_ts=datetime.now(timezone.utc),
            status="ok",
            cost_usd=0.005,
            retries_used=0,
        )
        snapshot = ContextSnapshot(
            chain_id="c1",
            request_id="r1",
            step_count=i + 1,
            cost_usd_accumulated=0.005 * (i + 1),
            retries_used=0,
            aborted=False,
            abort_reason=None,
            elapsed_ms=100.0,
            nodes=[node],
            events=[],
        )

        # after_step
        vos.after_step(handle, snapshot)

    # Verify store has all 3 outcomes
    hv = store.build_history("c1")
    assert len(hv.last_n) == 3

    # Clean runs -> ceiling should be increasing (Rule 2: +5%)
    assert ceilings[1] > ceilings[0]
    assert ceilings[2] > ceilings[1]


def test_halt_feedback_loop() -> None:
    """Halt in step 1 -> tighter ceiling in step 2."""
    store = MemoryStore()
    vos = VeronicaOS(store=store)

    # Step 1: halted
    intent1 = StepIntent(
        step_id="s0", request_id="r1", chain_id="c1",
        kind="llm", model="gpt-4", tool_name=None,
        timeout_ms=30_000, metadata={},
    )
    handle1 = vos.before_step(intent1)
    ceiling1 = handle1.policy.ceiling_usd

    node = NodeRecord(
        node_id="n0", parent_id=None, kind="llm",
        operation_name="halted_op",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc),
        status="halted", cost_usd=0.5, retries_used=0,
    )
    snapshot = ContextSnapshot(
        chain_id="c1", request_id="r1", step_count=1,
        cost_usd_accumulated=0.5, retries_used=0,
        aborted=False, abort_reason=None,
        elapsed_ms=50.0, nodes=[node], events=[],
    )
    vos.after_step(handle1, snapshot)

    # Step 2: should have tighter ceiling
    intent2 = StepIntent(
        step_id="s1", request_id="r1", chain_id="c1",
        kind="llm", model="gpt-4", tool_name=None,
        timeout_ms=30_000, metadata={},
    )
    handle2 = vos.before_step(intent2)
    ceiling2 = handle2.policy.ceiling_usd

    assert ceiling2 < ceiling1, f"Expected tighter: {ceiling2} < {ceiling1}"


def test_policy_config_all_fields_populated() -> None:
    """PolicyConfig from before_step has all required fields."""
    vos = VeronicaOS()
    intent = StepIntent(
        step_id="s0", request_id="r1", chain_id="c1",
        kind="llm", model="gpt-4", tool_name=None,
        timeout_ms=30_000, metadata={},
    )
    handle = vos.before_step(intent)
    pc = handle.policy

    assert pc.chain_id == "c1"
    assert pc.ceiling_usd > 0
    assert pc.on_exceed in ("halt", "degrade", "queue")
    assert pc.issued_at > 0
```

**Step 2: Run all tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

**Step 3: Run coverage**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/ --cov=veronica --cov-report=term-missing`
Expected: >= 80% coverage

**Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration tests for full VeronicaOS pipeline"
```

**Step 5: Final commit -- tag v0.1.0**

```bash
git tag v0.1.0
git push && git push --tags
```

---

## Summary

| Task | Component | Tests | LOC (est.) |
|------|-----------|-------|------------|
| 0 | Project scaffold | - | 50 |
| 1 | types.py + PolicyConfig | 14 | 150 |
| 2 | protocols.py | 7 | 60 |
| 3 | MemoryStore + SimpleCollector | 9 | 120 |
| 4 | RuleAnalyzer | 5 | 80 |
| 5 | TableCostModel | 4 | 60 |
| 6 | SimplePlanner | 8 | 80 |
| 7 | PassthroughArbiter + NullEmitter | 6 | 60 |
| 8 | Time guard | 3 | 50 |
| 9 | VeronicaOS orchestrator | 10 | 200 |
| 10 | Integration tests | 3 | 120 |
| **Total** | | **69 tests** | **~1030 LOC** |

Dependency order: 0 -> 1 -> 2 -> 3 -> 4,5,6,7,8 (parallel) -> 9 -> 10
