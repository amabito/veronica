"""VERONICA -- Execution OS for LLM systems."""
from __future__ import annotations

from veronica.adaptive_planner import AdaptivePlanner
from veronica.buffered_emitter import BufferedEmitter
from veronica.file_store import FileStore
from veronica.history_analyzer import HistoryAnalyzer
from veronica.os import VeronicaOS
from veronica.proportional_arbiter import ProportionalArbiter
from veronica.regression_cost_model import RegressionCostModel
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

__version__ = "0.2.0"

__all__ = [
    # Core
    "VeronicaOS",
    # Phase 2 components
    "AdaptivePlanner",
    "BufferedEmitter",
    "FileStore",
    "HistoryAnalyzer",
    "ProportionalArbiter",
    "RegressionCostModel",
    # Types
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
