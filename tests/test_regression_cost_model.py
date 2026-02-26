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
        assert result.confidence == 0.75  # depth=10 -> 0.75

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
