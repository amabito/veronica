"""VERONICA -- Execution OS for LLM systems."""
from __future__ import annotations

from veronica.os import VeronicaOS
from veronica.types import (
    AnalysisResult,
    BudgetState,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    HistoryView,
    PolicyConfig,
    Signal,
    StepHandle,
    StepIntent,
    StepOutcome,
)

__version__ = "0.1.0"

__all__ = [
    "VeronicaOS",
    "AnalysisResult",
    "BudgetState",
    "CostEstimate",
    "DecisionMeta",
    "DesiredPolicy",
    "HistoryView",
    "PolicyConfig",
    "Signal",
    "StepHandle",
    "StepIntent",
    "StepOutcome",
]
