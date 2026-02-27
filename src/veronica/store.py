# src/veronica/store.py
"""VERONICA OS store implementations."""
from __future__ import annotations

from collections import defaultdict

from veronica.types import (
    AnalysisResult,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    HistoryView,
    PolicyConfig,
    StepOutcome,
)


class MemoryStore:
    """In-memory StoreProtocol implementation.

    Stores step outcomes per chain_id. Suitable for testing
    and single-process use. No persistence across restarts.
    """

    def __init__(self) -> None:
        self._chains: dict[str, list[StepOutcome]] = defaultdict(list)

    def commit(
        self,
        outcome: StepOutcome,
        analysis: AnalysisResult,
        cost: CostEstimate,
        desired: DesiredPolicy,
        policy: PolicyConfig,
        meta: DecisionMeta,
    ) -> None:
        """Persist a completed step.

        Only StepOutcome is stored; analysis, cost, desired, policy, and meta
        are accepted to satisfy StoreProtocol but intentionally dropped.
        This is by design for the in-memory/testing implementation.
        """
        self._chains[outcome.chain_id].append(outcome)

    def build_history(self, chain_id: str, limit: int = 50) -> HistoryView:
        outcomes = self._chains.get(chain_id, [])
        last_n = tuple(outcomes[-limit:])

        rolling_cost = sum(o.cost_usd for o in last_n)

        failure_streak = 0
        for o in reversed(last_n):
            if o.status != "ok":
                failure_streak += 1
            else:
                break

        depth = len(last_n)
        loop_score = 0.0

        return HistoryView(
            chain_id=chain_id,
            last_n=last_n,
            rolling_cost_usd=rolling_cost,
            failure_streak=failure_streak,
            depth=depth,
            loop_score=loop_score,
        )
