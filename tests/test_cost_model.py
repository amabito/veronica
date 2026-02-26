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
