# tests/test_planner.py
"""Tests for veronica.planner -- SimplePlanner."""

from __future__ import annotations

import pytest

from veronica.planner import SimplePlanner
from veronica.types import AnalysisResult, BudgetState, CostEstimate


def _cost(usd: float = 0.01) -> CostEstimate:
    return CostEstimate(
        estimated_usd=usd,
        confidence=0.7,
        model_used="gpt-4",
        basis="pricing_table",
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
