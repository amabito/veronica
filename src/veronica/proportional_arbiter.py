# src/veronica/proportional_arbiter.py
"""VERONICA OS arbiter -- ProportionalArbiter with priority-weighted allocation."""
from __future__ import annotations

import time
from typing import Mapping, Sequence

from veronica.types import DesiredPolicy, PolicyConfig

_MIN_ALLOCATION_USD = 0.01
_EPS = 1e-12


class ProportionalArbiter:
    """Phase 2 arbiter. Priority-weighted proportional budget allocation.

    - weight = priority (not priority * ceiling)
    - priority <= 0 excluded entirely
    - Conditional min_allocation (only when budget allows)
    - 2-pass surplus redistribution
    - Double clamp: no single allocation exceeds budget
    """

    def arbitrate(
        self,
        desires: Sequence[DesiredPolicy],
        budget_remaining_usd: float,
    ) -> Mapping[str, PolicyConfig]:
        if not desires:
            return {}

        # Filter: priority > 0 only
        eligible = [d for d in desires if d.priority > 0]
        if not eligible:
            return {}

        total_weight = sum(d.priority for d in eligible)
        if total_weight < _EPS:
            return {}

        # Pass 1: Proportional allocation, cap to desired ceiling
        allocations: dict[str, float] = {}
        surplus = 0.0
        uncapped: list[DesiredPolicy] = []

        for d in eligible:
            share = (d.priority / total_weight) * budget_remaining_usd
            if share > d.ceiling_usd:
                surplus += share - d.ceiling_usd
                allocations[d.chain_id] = d.ceiling_usd
            else:
                allocations[d.chain_id] = share
                uncapped.append(d)

        # Pass 2: Redistribute surplus among uncapped
        if surplus > _EPS and uncapped:
            uncapped_weight = sum(d.priority for d in uncapped)
            if uncapped_weight > _EPS:
                for d in uncapped:
                    extra = (d.priority / uncapped_weight) * surplus
                    new_alloc = allocations[d.chain_id] + extra
                    allocations[d.chain_id] = min(new_alloc, d.ceiling_usd)

        # Conditional min_allocation
        if budget_remaining_usd >= len(eligible) * _MIN_ALLOCATION_USD:
            for chain_id in allocations:
                allocations[chain_id] = max(allocations[chain_id], _MIN_ALLOCATION_USD)

        # Double clamp: total must not exceed budget
        total = sum(allocations.values())
        if total > budget_remaining_usd + _EPS:
            scale = budget_remaining_usd / total
            allocations = {k: v * scale for k, v in allocations.items()}

        # Build PolicyConfigs
        now = time.time()
        desire_map = {d.chain_id: d for d in eligible}
        result: dict[str, PolicyConfig] = {}

        for chain_id, alloc in allocations.items():
            d = desire_map[chain_id]
            result[chain_id] = PolicyConfig(
                chain_id=chain_id,
                ceiling_usd=alloc,
                ceiling_steps=d.ceiling_steps,
                ceiling_tokens_out=d.ceiling_tokens_out,
                on_exceed=d.on_exceed,
                fallback_model=d.fallback_model,
                timeout_ms=d.timeout_ms,
                priority=d.priority,
                issued_at=now,
                planner_version="0.2.0",
            )

        return result
