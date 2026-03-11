# src/veronica/protocols.py
"""VERONICA OS protocols -- runtime_checkable interfaces for pipeline stages."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from veronica_core.containment.execution_context import ContextSnapshot

from veronica.types import (
    AnalysisResult,
    BudgetState,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    HistoryView,
    PolicyConfig,
    StepIntent,
    StepOutcome,
)


@runtime_checkable
class CollectorProtocol(Protocol):
    def collect(self, snapshot: ContextSnapshot) -> StepOutcome: ...


@runtime_checkable
class AnalyzerProtocol(Protocol):
    def analyze(
        self,
        intent: StepIntent,
        outcome: StepOutcome,
        history: HistoryView,
    ) -> AnalysisResult: ...


@runtime_checkable
class CostModelProtocol(Protocol):
    def estimate(
        self,
        intent: StepIntent,
        history: HistoryView,
        last_analysis: AnalysisResult | None,
    ) -> CostEstimate: ...


@runtime_checkable
class PlannerProtocol(Protocol):
    def plan(
        self,
        analysis: AnalysisResult | None,
        cost: CostEstimate,
        budget: BudgetState,
    ) -> DesiredPolicy: ...


@runtime_checkable
class ArbiterProtocol(Protocol):
    def arbitrate(
        self,
        desires: Sequence[DesiredPolicy],
        budget_remaining_usd: float,
    ) -> Mapping[str, PolicyConfig]: ...


@runtime_checkable
class StoreProtocol(Protocol):
    def commit(
        self,
        outcome: StepOutcome,
        analysis: AnalysisResult,
        cost: CostEstimate,
        desired: DesiredPolicy,
        policy: PolicyConfig,
        meta: DecisionMeta,
    ) -> None: ...

    def build_history(self, chain_id: str, limit: int = 50) -> HistoryView: ...


@runtime_checkable
class EventEmitterProtocol(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None: ...
