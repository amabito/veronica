# tests/test_analyzer.py
"""Tests for veronica.analyzer -- RuleAnalyzer with 3 rules."""

from __future__ import annotations

import time

from veronica.analyzer import RuleAnalyzer
from veronica.types import HistoryView, StepIntent, StepOutcome


def _intent(kind: str = "llm", model: str = "gpt-4") -> StepIntent:
    return StepIntent(
        step_id="s1",
        request_id="r1",
        chain_id="c1",
        kind=kind,
        model=model,
        tool_name=None,
        timeout_ms=30_000,
        metadata={},
    )


def _outcome(status: str = "ok", cost: float = 0.005) -> StepOutcome:
    return StepOutcome(
        step_id="s1",
        request_id="r1",
        chain_id="c1",
        kind="llm",
        status=status,
        cost_usd=cost,
        tokens_in=100,
        tokens_out=50,
        elapsed_ms=100.0,
        model="gpt-4",
        events=(),
        timestamp_ms=int(time.time() * 1000),
    )


def _history(
    failure_streak: int = 0,
    depth: int = 1,
    rolling_cost: float = 0.01,
) -> HistoryView:
    return HistoryView(
        chain_id="c1",
        last_n=(),
        rolling_cost_usd=rolling_cost,
        failure_streak=failure_streak,
        depth=depth,
        loop_score=0.0,
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
            _intent(),
            _outcome("ok"),
            _history(depth=8),
        )
        assert result.recommendation == "halt"
        assert result.risk_level == "critical"
        signals = [s.kind for s in result.signals]
        assert "depth_anomaly" in signals

    def test_failure_streak_elevated(self) -> None:
        """Consecutive failures -> elevated risk."""
        analyzer = RuleAnalyzer()
        result = analyzer.analyze(
            _intent(),
            _outcome("error"),
            _history(failure_streak=3),
        )
        assert result.risk_level in ("elevated", "critical")
        assert result.recommendation == "tighten"

    def test_intent_model_mismatch(self) -> None:
        """Intent kind != outcome kind -> intent_deviation signal."""
        analyzer = RuleAnalyzer()
        outcome = StepOutcome(
            step_id="s1",
            request_id="r1",
            chain_id="c1",
            kind="tool",
            status="ok",
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            elapsed_ms=10.0,
            model=None,
            events=(),
            timestamp_ms=int(time.time() * 1000),
        )
        result = analyzer.analyze(
            _intent(kind="llm"),
            outcome,
            _history(),
        )
        signals = [s.kind for s in result.signals]
        assert "intent_deviation" in signals
