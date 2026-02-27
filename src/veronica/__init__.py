"""VERONICA -- Execution OS for LLM systems."""
from __future__ import annotations

from veronica.adaptive_planner import AdaptivePlanner
from veronica.buffered_emitter import BufferedEmitter
from veronica.file_store import FileStore
from veronica.history_analyzer import HistoryAnalyzer
from veronica.os import OrgPolicyDenied, StepContext, VeronicaOS
from veronica.proportional_arbiter import ProportionalArbiter
from veronica.metrics_subscriber import MetricsSubscriber
from veronica.redis_arbiter import RedisArbiter
from veronica.regression_cost_model import RegressionCostModel
from veronica.structured_log_subscriber import StructuredLogSubscriber
from veronica.metrics_exporter import start_metrics_server
from veronica.types import (
    AnalysisResult,
    BudgetState,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    HistoryView,
    OrgPolicy,
    PolicyConfig,
    Signal,
    StepHandle,
    StepIntent,
    StepOutcome,
)

__version__ = "0.7.1"

__all__ = [
    # Core
    "VeronicaOS",
    "StepContext",
    "OrgPolicy",
    "OrgPolicyDenied",
    # Phase 2 components
    "AdaptivePlanner",
    "BufferedEmitter",
    "FileStore",
    "HistoryAnalyzer",
    "ProportionalArbiter",
    "RegressionCostModel",
    # Phase 3 components
    "RedisArbiter",
    # Phase 4 components
    "MetricsSubscriber",
    "StructuredLogSubscriber",
    # Phase 5 components
    "start_metrics_server",
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
