# src/veronica/replay/models.py
"""Dataclass models for the incident replay engine."""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReplayRequest:
    """Request to replay events for a given chain within a time range.

    Fields
    ------
    chain_id        : chain scope to replay
    from_timestamp  : Unix epoch seconds (inclusive lower bound)
    to_timestamp    : Unix epoch seconds (inclusive upper bound)
    override_policy : optional dict with PolicyConfig-like fields to apply
                      during replay. If None, decisions are replayed as-is.
    """

    chain_id: str
    from_timestamp: float
    to_timestamp: float
    override_policy: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if not math.isfinite(self.from_timestamp) or not math.isfinite(self.to_timestamp):
            raise ValueError("from_timestamp and to_timestamp must be finite")
        if self.from_timestamp > self.to_timestamp:
            raise ValueError("from_timestamp must be <= to_timestamp")
        if self.override_policy is not None:
            object.__setattr__(self, "override_policy", copy.deepcopy(self.override_policy))


@dataclass(frozen=True)
class DecisionDiff:
    """Comparison between original and replayed decision for one step.

    Fields
    ------
    step_id            : unique step identifier
    original_decision  : decision recorded in the store
    replayed_decision  : decision under override policy (or same as original)
    changed            : True if replayed_decision != original_decision
    """

    step_id: str
    original_decision: str
    replayed_decision: str
    changed: bool


@dataclass(frozen=True)
class ReplayResult:
    """Aggregated result of replaying a chain's events.

    Fields
    ------
    chain_id      : chain scope that was replayed
    event_count   : number of events in the time range
    diffs         : per-step decision comparisons
    changed_count : number of steps where the decision would differ
    summary       : human-readable result summary
    """

    chain_id: str
    event_count: int
    diffs: list[DecisionDiff] = field(default_factory=list)
    changed_count: int = 0
    summary: str = ""
