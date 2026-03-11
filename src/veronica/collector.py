# src/veronica/collector.py
"""VERONICA OS collector -- maps ContextSnapshot to StepOutcome."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from veronica_core.containment.execution_context import ContextSnapshot

from veronica.types import StepOutcome

if TYPE_CHECKING:
    from veronica_core.containment.types import NodeRecord

_VALID_STATUSES = frozenset({"ok", "halted", "error", "timeout"})


def _extract_tokens(node: "NodeRecord") -> tuple[int, int]:
    """Extract tokens_in and tokens_out from a NodeRecord.

    veronica-core stores token counts in NodeRecord.partial_buffer.metadata
    under the keys 'tokens_in' and 'tokens_out'. Falls back to 0 if the
    buffer is absent or the keys are missing.
    """
    buf = getattr(node, "partial_buffer", None)
    if buf is None:
        return 0, 0
    try:
        meta = buf.metadata
    except Exception:
        return 0, 0
    try:
        tokens_in = max(0, int(meta.get("tokens_in", 0) or 0))
    except (TypeError, ValueError):
        tokens_in = 0
    try:
        tokens_out = max(0, int(meta.get("tokens_out", 0) or 0))
    except (TypeError, ValueError):
        tokens_out = 0
    return tokens_in, tokens_out


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
        tokens_in, tokens_out = _extract_tokens(last_node)
        return StepOutcome(
            step_id=last_node.node_id,
            request_id=snapshot.request_id,
            chain_id=snapshot.chain_id,
            kind=last_node.kind,
            status=status,
            cost_usd=last_node.cost_usd,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            elapsed_ms=snapshot.elapsed_ms,
            model=None,
            events=tuple(snapshot.events),
            timestamp_ms=now_ms,
        )
