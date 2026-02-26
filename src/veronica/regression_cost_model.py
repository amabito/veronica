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
