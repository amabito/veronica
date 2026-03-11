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
            model,
            self._default_tokens_in,
            self._default_tokens_out,
        )
        return CostEstimate(
            estimated_usd=cost,
            confidence=0.7,
            model_used=model,
            basis="pricing_table",
        )
