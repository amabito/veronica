# src/veronica/adaptive_planner.py
"""VERONICA OS planner -- AdaptivePlanner with error-class-aware ceiling adjustment."""
from __future__ import annotations

from veronica.types import AnalysisResult, BudgetState, CostEstimate, DesiredPolicy

_HALTED_FACTOR = 0.50    # -50%
_ERROR_FACTOR = 0.85     # -15%
_TIMEOUT_FACTOR = 0.90   # -10%
_LOOSEN_FACTOR = 1.03    # +3%

_DEFAULT_STEPS = 100
_DEFAULT_TOKENS_OUT = 50_000
_COOLDOWN_STEPS = 3
_COST_HEADROOM = 1.5


class AdaptivePlanner:
    """Phase 2 planner. Error-class-aware tightening with cooldown.

    - halted: -50% (critical)
    - error: -15% (warning)
    - timeout: -10% (mild)
    - loosen: +3% (conservative)
    - 3-step cooldown per chain_id after any adjustment.
    - Ceiling >= estimated_cost * 1.5 (prevents starvation).

    Works best with HistoryAnalyzer (emits halt_tighten signals for precise
    severity detection). When used with RuleAnalyzer or other analyzers that
    do not emit halt_tighten signals, _tighten_factor falls back to the
    maximum severity across all signals to select the appropriate factor.
    If no signals are present, _TIMEOUT_FACTOR (-10%) is used.
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
        self._cooldowns: dict[str, int] = {}  # chain_id -> steps remaining

    def plan(
        self,
        analysis: AnalysisResult | None,
        cost: CostEstimate,
        budget: BudgetState,
    ) -> DesiredPolicy:
        ceiling = self._effective_ceiling
        on_exceed = self._default_on_exceed
        adjusted = False

        # Check cooldown (use empty string as default chain for single-chain mode)
        chain_key = ""
        cooldown_remaining = self._cooldowns.get(chain_key, 0)

        if analysis is not None:
            if analysis.recommendation == "halt":
                on_exceed = "halt"

            if cooldown_remaining <= 0:
                if analysis.recommendation == "tighten":
                    factor = self._tighten_factor(analysis)
                    ceiling *= factor
                    adjusted = True
                elif analysis.recommendation == "loosen":
                    ceiling *= _LOOSEN_FACTOR
                    adjusted = True

        # Minimum ceiling guard: at least 1.5x estimated cost
        ceiling = max(ceiling, cost.estimated_usd * _COST_HEADROOM)

        # Double clamp (Planner level)
        ceiling = max(self._min, min(self._max, ceiling))
        ceiling = min(ceiling, budget.chain_remaining_usd)

        # Update state
        self._effective_ceiling = ceiling
        if adjusted:
            self._cooldowns[chain_key] = _COOLDOWN_STEPS
        else:
            self._cooldowns[chain_key] = max(0, cooldown_remaining - 1)

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

    @staticmethod
    def _tighten_factor(analysis: AnalysisResult) -> float:
        # Prefer halt_tighten signals for precise HistoryAnalyzer-based detection
        for signal in analysis.signals:
            if signal.kind == "halt_tighten":
                if signal.severity == "critical":
                    return _HALTED_FACTOR
                return _ERROR_FACTOR

        # Fallback: derive factor from max severity across all signals.
        # This allows RuleAnalyzer (which emits repeated_failure/depth_anomaly)
        # to produce the correct tightening factor without halt_tighten signals.
        if analysis.signals:
            has_critical_severity = any(s.severity == "critical" for s in analysis.signals)
            if has_critical_severity:
                return _HALTED_FACTOR
            return _ERROR_FACTOR

        return _TIMEOUT_FACTOR
