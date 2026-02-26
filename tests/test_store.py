# tests/test_store.py
"""Tests for veronica.store -- MemoryStore."""
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
