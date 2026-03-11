# src/veronica/analyzer.py
"""VERONICA OS analyzer -- RuleAnalyzer with 3 ceiling adjustment rules."""

from __future__ import annotations

from veronica.types import AnalysisResult, HistoryView, Signal, StepIntent, StepOutcome

_DEPTH_THRESHOLD = 8
_FAILURE_STREAK_ELEVATED = 2
_FAILURE_STREAK_CRITICAL = 5


class RuleAnalyzer:
    """Phase 1 rule-based analyzer.

    Rule 1 -- Halt tightening: if outcome is halted, recommend tighten.
    Rule 2 -- Clean run loosening: if outcome is ok, recommend loosen.
    Rule 3 -- Depth guard: if depth >= 8, recommend halt (critical).

    Signals are emitted for: depth_anomaly, repeated_failure, intent_deviation.
    """

    def analyze(
        self,
        intent: StepIntent,
        outcome: StepOutcome,
        history: HistoryView,
    ) -> AnalysisResult:
        signals: list[Signal] = []
        risk_level = "nominal"
        recommendation = "continue"

        # --- Signal detection ---

        # Intent vs outcome kind mismatch
        if intent.kind != outcome.kind:
            signals.append(
                Signal(
                    kind="intent_deviation",
                    severity="warning",
                    detail=f"intended {intent.kind}, got {outcome.kind}",
                )
            )

        # Failure streak (current outcome counts toward streak)
        streak = history.failure_streak + (1 if outcome.status != "ok" else 0)
        if streak >= _FAILURE_STREAK_CRITICAL:
            signals.append(
                Signal(
                    kind="repeated_failure",
                    severity="critical",
                    detail=f"{streak} consecutive failures",
                )
            )
            risk_level = "critical"
        elif streak >= _FAILURE_STREAK_ELEVATED:
            signals.append(
                Signal(
                    kind="repeated_failure",
                    severity="warning",
                    detail=f"{streak} consecutive failures",
                )
            )
            risk_level = "elevated"

        # Depth guard (Rule 3 -- takes precedence)
        if history.depth >= _DEPTH_THRESHOLD:
            signals.append(
                Signal(
                    kind="depth_anomaly",
                    severity="critical",
                    detail=f"depth {history.depth} >= {_DEPTH_THRESHOLD}",
                )
            )
            risk_level = "critical"
            recommendation = "halt"
            return AnalysisResult(
                signals=tuple(signals),
                risk_level=risk_level,
                recommendation=recommendation,
            )

        # --- Recommendation rules ---

        # Rule 1: halt tightening
        if outcome.status in ("halted", "error", "timeout"):
            recommendation = "tighten"
            if risk_level == "nominal":
                risk_level = "elevated"
        # Rule 2: clean run loosening
        elif outcome.status == "ok" and history.failure_streak == 0:
            recommendation = "loosen"

        return AnalysisResult(
            signals=tuple(signals),
            risk_level=risk_level,
            recommendation=recommendation,
        )
