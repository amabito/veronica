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
