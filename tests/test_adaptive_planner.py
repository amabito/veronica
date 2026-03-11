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
    return AnalysisResult(
        signals=signals, risk_level=risk_level, recommendation=recommendation
    )


def _cost(estimated: float = 0.05) -> CostEstimate:
    return CostEstimate(
        estimated_usd=estimated, confidence=0.8, model_used="gpt-4", basis="historical"
    )


def _budget(remaining: float = 50.0) -> BudgetState:
    return BudgetState(
        request_remaining_usd=remaining,
        chain_remaining_usd=remaining,
        window_remaining_steps=100,
    )


class TestAdaptivePlanner:
    def test_continue_no_change(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        result = planner.plan(_analysis(), _cost(), _budget())
        assert result.ceiling_usd == pytest.approx(1.0)

    def test_tighten_halted_minus_50(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        halted_signals = (
            Signal(kind="halt_tighten", severity="critical", detail="halted"),
        )
        result = planner.plan(
            _analysis(recommendation="tighten", signals=halted_signals),
            _cost(),
            _budget(),
        )
        assert result.ceiling_usd == pytest.approx(0.50)

    def test_tighten_error_minus_15(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        error_signals = (
            Signal(kind="halt_tighten", severity="warning", detail="error"),
        )
        result = planner.plan(
            _analysis(recommendation="tighten", signals=error_signals),
            _cost(),
            _budget(),
        )
        assert result.ceiling_usd == pytest.approx(0.85)

    def test_loosen_plus_3_percent(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        result = planner.plan(
            _analysis(recommendation="loosen"),
            _cost(),
            _budget(),
        )
        assert result.ceiling_usd == pytest.approx(1.03)

    def test_ceiling_clamped_to_budget(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=10.0)
        result = planner.plan(_analysis(), _cost(), _budget(remaining=0.50))
        assert result.ceiling_usd <= 0.50

    def test_ceiling_min_guard(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0, min_ceiling_usd=0.10)
        # Tighten several times
        halted_signals = (
            Signal(kind="halt_tighten", severity="critical", detail="halted"),
        )
        for _ in range(10):
            planner.plan(
                _analysis(recommendation="tighten", signals=halted_signals),
                _cost(),
                _budget(),
            )
        result = planner.plan(
            _analysis(recommendation="tighten", signals=halted_signals),
            _cost(),
            _budget(),
        )
        assert result.ceiling_usd >= 0.10

    def test_ceiling_at_least_1_5x_estimated_cost(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=0.01, min_ceiling_usd=0.001)
        result = planner.plan(_analysis(), _cost(estimated=0.10), _budget())
        assert result.ceiling_usd >= 0.10 * 1.5

    def test_cooldown_prevents_rapid_changes(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        # First tighten applies
        halted_signals = (
            Signal(kind="halt_tighten", severity="critical", detail="halted"),
        )
        r1 = planner.plan(
            _analysis(recommendation="tighten", signals=halted_signals),
            _cost(),
            _budget(),
        )
        # Immediate loosen should be blocked by cooldown
        r2 = planner.plan(_analysis(recommendation="loosen"), _cost(), _budget())
        assert r2.ceiling_usd == r1.ceiling_usd  # no change during cooldown

    def test_halt_recommendation_sets_on_exceed(self) -> None:
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        result = planner.plan(
            _analysis(recommendation="halt"),
            _cost(),
            _budget(),
        )
        assert result.on_exceed == "halt"

    def test_protocol_compatible(self) -> None:
        from veronica.protocols import PlannerProtocol

        planner = AdaptivePlanner()
        assert isinstance(planner, PlannerProtocol)

    # --- Bug fix: RuleAnalyzer-style signals (no halt_tighten) ---

    def test_tighten_with_rule_analyzer_halt_recommendation(self) -> None:
        """AdaptivePlanner with RuleAnalyzer signals (no halt_tighten) + recommendation=halt
        should still tighten by _HALTED_FACTOR (-50%) via recommendation fallback."""
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        # RuleAnalyzer emits repeated_failure (critical) and sets recommendation="tighten"
        # But for a halted outcome with critical risk, recommendation might be "halt"
        # Use recommendation="tighten" + critical signal (not halt_tighten kind)
        critical_signals = (
            Signal(kind="repeated_failure", severity="critical", detail="5 failures"),
        )
        result = planner.plan(
            _analysis(
                recommendation="tighten",
                risk_level="critical",
                signals=critical_signals,
            ),
            _cost(),
            _budget(),
        )
        # Should use _HALTED_FACTOR (0.50) since signal severity is critical
        assert result.ceiling_usd == pytest.approx(0.50)

    def test_tighten_with_rule_analyzer_warning_recommendation(self) -> None:
        """AdaptivePlanner with RuleAnalyzer signals (no halt_tighten) + recommendation=tighten
        and warning severity should use _ERROR_FACTOR (-15%) via recommendation fallback."""
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        # RuleAnalyzer emits repeated_failure (warning)
        warning_signals = (
            Signal(kind="repeated_failure", severity="warning", detail="2 failures"),
        )
        result = planner.plan(
            _analysis(
                recommendation="tighten", risk_level="elevated", signals=warning_signals
            ),
            _cost(),
            _budget(),
        )
        # Should use _ERROR_FACTOR (0.85) since max severity is warning
        assert result.ceiling_usd == pytest.approx(0.85)

    def test_tighten_no_signals_uses_timeout_factor(self) -> None:
        """When recommendation=tighten but no signals, use _TIMEOUT_FACTOR (-10%)."""
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        result = planner.plan(
            _analysis(recommendation="tighten", signals=()),
            _cost(),
            _budget(),
        )
        # No signals: fallback to _TIMEOUT_FACTOR (0.90)
        assert result.ceiling_usd == pytest.approx(0.90)

    # --- Adversarial: severity levels and signal ordering ---

    def test_tighten_info_severity_uses_error_factor(self) -> None:
        """info severity (not critical) should use _ERROR_FACTOR (-15%)."""
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        info_signals = (Signal(kind="some_pattern", severity="info", detail="minor"),)
        result = planner.plan(
            _analysis(recommendation="tighten", signals=info_signals),
            _cost(),
            _budget(),
        )
        # info is not critical, so _ERROR_FACTOR (0.85) applies
        assert result.ceiling_usd == pytest.approx(0.85)

    def test_tighten_mixed_severity_critical_wins(self) -> None:
        """When signals have mixed severities, critical must win."""
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        mixed_signals = (
            Signal(kind="minor_issue", severity="info", detail="low"),
            Signal(kind="major_issue", severity="warning", detail="medium"),
            Signal(kind="critical_issue", severity="critical", detail="high"),
        )
        result = planner.plan(
            _analysis(recommendation="tighten", signals=mixed_signals),
            _cost(),
            _budget(),
        )
        # critical present -> _HALTED_FACTOR (0.50)
        assert result.ceiling_usd == pytest.approx(0.50)

    def test_tighten_critical_last_in_tuple_still_wins(self) -> None:
        """Critical signal at end of tuple must still be detected."""
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        signals = (
            Signal(kind="a", severity="warning", detail="w"),
            Signal(kind="b", severity="info", detail="i"),
            Signal(kind="c", severity="critical", detail="c"),
        )
        result = planner.plan(
            _analysis(recommendation="tighten", signals=signals),
            _cost(),
            _budget(),
        )
        assert result.ceiling_usd == pytest.approx(0.50)

    def test_tighten_warning_only_signals(self) -> None:
        """All warning signals -> _ERROR_FACTOR (-15%)."""
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        signals = (
            Signal(kind="a", severity="warning", detail="w1"),
            Signal(kind="b", severity="warning", detail="w2"),
        )
        result = planner.plan(
            _analysis(recommendation="tighten", signals=signals),
            _cost(),
            _budget(),
        )
        assert result.ceiling_usd == pytest.approx(0.85)

    def test_halt_tighten_takes_priority_over_severity(self) -> None:
        """halt_tighten signal must take priority over severity-based fallback."""
        planner = AdaptivePlanner(base_ceiling_usd=1.0)
        signals = (
            Signal(kind="halt_tighten", severity="warning", detail="halt"),
            Signal(kind="other", severity="critical", detail="loud"),
        )
        result = planner.plan(
            _analysis(recommendation="tighten", signals=signals),
            _cost(),
            _budget(),
        )
        # halt_tighten with warning -> _ERROR_FACTOR (0.85), NOT _HALTED_FACTOR
        assert result.ceiling_usd == pytest.approx(0.85)
