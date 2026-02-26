# src/veronica/history_analyzer.py
"""VERONICA OS analyzer -- HistoryAnalyzer with 6 adaptive detection patterns."""
from __future__ import annotations

from veronica.types import AnalysisResult, HistoryView, Signal, StepIntent, StepOutcome

_DEPTH_SOFT = 6
_DEPTH_HARD = 10
_COST_SPIKE_FACTOR = 2.0
_COST_MIN_HISTORY = 5
_LOOP_THRESHOLD = 0.7
_LATENCY_SPIKE_FACTOR = 3.0
_LOOSEN_MIN_STREAK = 3
_LOOSEN_MIN_HEADROOM = 0.5
_EPS = 1e-12

_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
_RISK_BY_MAX_SEVERITY = {0: "nominal", 1: "elevated", 2: "critical"}
_RECOMMENDATION_RANK = {"continue": 0, "loosen": 1, "tighten": 2, "halt": 3}


class HistoryAnalyzer:
    """Phase 2 analyzer. 6 detection patterns, pure function (no internal state).

    All statistics come from HistoryView (computed by Store).
    Emits all matching signals; risk_level = max severity;
    recommendation = highest-priority single value.
    """

    def analyze(
        self,
        intent: StepIntent,
        outcome: StepOutcome,
        history: HistoryView,
    ) -> AnalysisResult:
        signals: list[Signal] = []
        recommendations: list[str] = []

        # Pattern 1: halt_tighten
        if outcome.status in ("halted", "error", "timeout"):
            severity = "critical" if outcome.status == "halted" else "warning"
            signals.append(Signal(
                kind="halt_tighten",
                severity=severity,
                detail=f"outcome status={outcome.status}",
            ))
            recommendations.append("tighten")

        # Pattern 2: clean_loosen
        if (
            outcome.status == "ok"
            and history.success_streak >= _LOOSEN_MIN_STREAK
            and history.budget_headroom_ratio >= _LOOSEN_MIN_HEADROOM
        ):
            signals.append(Signal(
                kind="clean_loosen",
                severity="info",
                detail=f"streak={history.success_streak}, headroom={history.budget_headroom_ratio:.2f}",
            ))
            recommendations.append("loosen")

        # Pattern 3: depth_guard (2-stage)
        if history.depth >= _DEPTH_HARD:
            signals.append(Signal(
                kind="depth_guard",
                severity="critical",
                detail=f"depth {history.depth} >= hard limit {_DEPTH_HARD}",
            ))
            recommendations.append("halt")
        elif history.depth >= _DEPTH_SOFT:
            signals.append(Signal(
                kind="depth_guard",
                severity="warning",
                detail=f"depth {history.depth} >= soft limit {_DEPTH_SOFT}",
            ))
            recommendations.append("tighten")

        # Pattern 4: cost_acceleration
        if (
            history.depth >= _COST_MIN_HISTORY
            and history.cost_per_step_ema > _EPS
            and outcome.cost_usd > history.cost_per_step_ema * _COST_SPIKE_FACTOR
        ):
            signals.append(Signal(
                kind="cost_acceleration",
                severity="warning",
                detail=f"cost {outcome.cost_usd:.4f} > {_COST_SPIKE_FACTOR}x EMA {history.cost_per_step_ema:.4f}",
            ))
            recommendations.append("tighten")

        # Pattern 5: loop_detection
        if history.loop_score >= _LOOP_THRESHOLD:
            signals.append(Signal(
                kind="loop_detection",
                severity="warning",
                detail=f"loop_score={history.loop_score:.2f}",
            ))
            recommendations.append("tighten")

        # Pattern 6: latency_anomaly (info-only, no recommendation change)
        model_key = outcome.model or "unknown"
        latency_ema = history.latency_ema_ms.get(model_key)
        if latency_ema is not None and latency_ema > _EPS:
            if outcome.elapsed_ms > latency_ema * _LATENCY_SPIKE_FACTOR:
                signals.append(Signal(
                    kind="latency_anomaly",
                    severity="info",
                    detail=f"elapsed {outcome.elapsed_ms:.0f}ms > {_LATENCY_SPIKE_FACTOR}x EMA {latency_ema:.0f}ms",
                ))
                # No recommendation change (info-only)

        # Compose risk_level
        if signals:
            max_sev = max(_SEVERITY_RANK.get(s.severity, 0) for s in signals)
            risk_level = _RISK_BY_MAX_SEVERITY[max_sev]
        else:
            risk_level = "nominal"

        # Compose recommendation (highest priority wins)
        if recommendations:
            recommendation = max(recommendations, key=lambda r: _RECOMMENDATION_RANK.get(r, 0))
        else:
            recommendation = "continue"

        return AnalysisResult(
            signals=tuple(signals),
            risk_level=risk_level,
            recommendation=recommendation,
        )
