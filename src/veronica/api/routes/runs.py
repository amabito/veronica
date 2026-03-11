# src/veronica/api/routes/runs.py
"""GET /runs/{chain_id} endpoint -- aggregate run detail for a chain."""

from __future__ import annotations

import heapq
import math
import time as _time
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel

router = APIRouter(tags=["runs"])

_ACTIVE_WINDOW_SECONDS = 300  # chain is "active" if event in last 5 minutes
_CHAIN_ID_RE = r"^[a-zA-Z0-9_:@.\-]+$"


class RunDetail(BaseModel):
    """Aggregate summary of all events for a chain_id."""

    chain_id: str
    status: str  # "active" | "completed"
    total_cost: float
    total_steps: int
    events_count: int
    first_event_at: float | None
    last_event_at: float | None
    policy_hash: str | None


@router.get(
    "/runs/{chain_id}",
    response_model=RunDetail,
    summary="Get run detail",
    description=(
        "Returns aggregate summary of all events for the given chain_id. "
        "Status is 'active' if the most recent event is within the last 5 minutes, "
        "otherwise 'completed'. Returns 404 if no events found."
    ),
)
async def get_run(
    request: Request,
    chain_id: Annotated[
        str,
        Path(min_length=1, max_length=256, pattern=_CHAIN_ID_RE, description="Chain ID to look up"),
    ],
) -> RunDetail:
    """Return aggregate event data for one chain."""
    ingestor = request.app.state.ingestor
    outcomes = ingestor.store.snapshot()

    chain_outcomes = [o for o in outcomes if o.chain_id == chain_id]
    if not chain_outcomes:
        raise HTTPException(
            status_code=404, detail=f"No events found for chain_id: {chain_id!r}"
        )

    total_cost = sum(
        o.cost_usd for o in chain_outcomes if math.isfinite(o.cost_usd)
    )
    events_count = len(chain_outcomes)

    timestamps = [o.timestamp for o in chain_outcomes if math.isfinite(o.timestamp)]
    if not timestamps:
        raise HTTPException(status_code=404, detail=f"No valid events for chain_id: {chain_id!r}")
    first_event_at = min(timestamps)
    last_event_at = max(timestamps)

    now = _time.time()
    status = (
        "active" if (now - last_event_at) <= _ACTIVE_WINDOW_SECONDS else "completed"
    )

    # Use the policy_hash of the most recent event; normalize empty string to None
    most_recent = heapq.nlargest(1, chain_outcomes, key=lambda o: o.timestamp)[0]
    policy_hash = most_recent.policy_hash or None

    return RunDetail(
        chain_id=chain_id,
        status=status,
        total_cost=total_cost,
        total_steps=events_count,
        events_count=events_count,
        first_event_at=first_event_at,
        last_event_at=last_event_at,
        policy_hash=policy_hash,
    )
