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
