# src/veronica/collector.py
"""VERONICA OS collector -- maps ContextSnapshot to StepOutcome."""
from __future__ import annotations

import time

from veronica_core.containment.execution_context import ContextSnapshot

from veronica.types import StepOutcome

_VALID_STATUSES = frozenset({"ok", "halted", "error", "timeout"})


class SimpleCollector:
    """Maps a ContextSnapshot to a StepOutcome.

    Extracts the most recent node from the snapshot. If no nodes
    exist, produces a synthetic "system" outcome.
    """

    def collect(self, snapshot: ContextSnapshot) -> StepOutcome:
        now_ms = int(time.time() * 1000)

        if not snapshot.nodes:
            return StepOutcome(
                step_id=f"{snapshot.chain_id}-{snapshot.step_count}",
                request_id=snapshot.request_id,
                chain_id=snapshot.chain_id,
                kind="system",
                status="ok",
                cost_usd=snapshot.cost_usd_accumulated,
                tokens_in=0,
                tokens_out=0,
                elapsed_ms=snapshot.elapsed_ms,
                model=None,
                events=tuple(snapshot.events),
                timestamp_ms=now_ms,
            )

        last_node = snapshot.nodes[-1]
        status = last_node.status if last_node.status in _VALID_STATUSES else "error"
        return StepOutcome(
            step_id=last_node.node_id,
            request_id=snapshot.request_id,
            chain_id=snapshot.chain_id,
            kind=last_node.kind,
            status=status,
            cost_usd=last_node.cost_usd,
            tokens_in=0,
            tokens_out=0,
            elapsed_ms=snapshot.elapsed_ms,
            model=None,
            events=tuple(snapshot.events),
            timestamp_ms=now_ms,
        )
