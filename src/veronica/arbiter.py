# src/veronica/arbiter.py
"""VERONICA OS arbiter -- PassthroughArbiter."""
from __future__ import annotations

import time
from typing import Mapping, Sequence

from veronica.types import DesiredPolicy, PolicyConfig


class PassthroughArbiter:
    """Phase 1 arbiter. Single-chain passthrough, multi-chain proportional cap.

    For a single chain, converts DesiredPolicy to PolicyConfig directly.
    For multiple chains, proportionally scales ceilings if total exceeds budget.
    """

    def arbitrate(
        self,
        desires: Sequence[DesiredPolicy],
        budget_remaining_usd: float,
    ) -> Mapping[str, PolicyConfig]:
        if not desires:
            return {}

        total_desired = sum(d.ceiling_usd for d in desires)
        scale = 1.0
        if total_desired > budget_remaining_usd and total_desired > 0:
            scale = budget_remaining_usd / total_desired

        now = time.time()
        result: dict[str, PolicyConfig] = {}
        for d in desires:
            result[d.chain_id] = PolicyConfig(
                chain_id=d.chain_id,
                ceiling_usd=d.ceiling_usd * scale,
                ceiling_steps=d.ceiling_steps,
                ceiling_tokens_out=d.ceiling_tokens_out,
                on_exceed=d.on_exceed,
                fallback_model=d.fallback_model,
                timeout_ms=d.timeout_ms,
                priority=d.priority,
                issued_at=now,
                planner_version="0.1.0",
            )
        return result
