# src/veronica/api/routes/events.py
"""GET /events endpoint -- paginated, filterable event log."""
from __future__ import annotations

import heapq
import math
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter(tags=["events"])

_VALID_DECISIONS = frozenset({"allow", "halt", "degrade", "retry", "quarantine", "queue", "unknown"})

MAX_LIMIT = 1000
DEFAULT_LIMIT = 100
MAX_RESULTS_CAP = 10000


class EventItem(BaseModel):
    """API representation of a CP StepOutcome."""

    step_id: str
    chain_id: str
    operation_name: str
    decision: str
    cost_usd: float
    tokens: int
    duration_ms: float
    policy_hash: str
    audit_id: str
    timestamp: float

    model_config = {"from_attributes": True}

    @classmethod
    def from_outcome(cls, outcome: Any) -> "EventItem":
        return cls(
            step_id=outcome.step_id,
            chain_id=outcome.chain_id,
            operation_name=outcome.operation_name,
            decision=outcome.decision,
            cost_usd=outcome.cost_usd,
            tokens=outcome.tokens,
            duration_ms=outcome.duration_ms,
            policy_hash=outcome.policy_hash,
            audit_id=outcome.audit_id,
            timestamp=outcome.timestamp,
        )


class EventsResponse(BaseModel):
    """Paginated events response."""

    items: list[EventItem]
    total: int
    offset: int
    limit: int
    truncated: bool = False


@router.get(
    "/events",
    response_model=EventsResponse,
    summary="List events",
    description=(
        "Returns CP-level StepOutcome events in reverse chronological order. "
        "Supports filtering by chain_id, decision verdict (repeatable), policy_hash, "
        "and time range. Pagination via limit/offset."
    ),
)
async def list_events(
    request: Request,
    chain_id: Annotated[str | None, Query(max_length=256, description="Filter by chain_id")] = None,
    decision: Annotated[
        list[str] | None,
        Query(description="Filter by decision verdict (repeatable: ?decision=halt&decision=degrade)"),
    ] = None,
    policy_hash: Annotated[str | None, Query(max_length=64, description="Filter by policy SHA-256 hex")] = None,
    since: Annotated[
        float | None,
        Query(description="Filter events with timestamp >= since (Unix epoch seconds)"),
    ] = None,
    until: Annotated[
        float | None,
        Query(description="Filter events with timestamp <= until (Unix epoch seconds)"),
    ] = None,
    offset: Annotated[int, Query(ge=0, le=MAX_RESULTS_CAP, description="Pagination offset")] = 0,
    limit: Annotated[
        int, Query(ge=1, le=MAX_LIMIT, description=f"Page size (max {MAX_LIMIT})")
    ] = DEFAULT_LIMIT,
) -> EventsResponse:
    """Return a paginated, filterable list of ingested CP StepOutcome events.

    Results are sorted by timestamp descending (newest first).
    Multiple decision values may be specified; any matching decision is included.
    """
    # Validate decision values up front
    if decision is not None:
        if len(decision) > 20:
            raise HTTPException(status_code=400, detail="Too many decision filter values (max 20)")
        invalid = [d for d in decision if d not in _VALID_DECISIONS]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid decision filter value(s). Valid: {sorted(_VALID_DECISIONS)}",
            )

    ingestor = request.app.state.ingestor
    # store is a public property on EventIngestor; snapshot() is thread-safe
    outcomes = ingestor.store.snapshot()

    # Apply filters
    if chain_id is not None:
        outcomes = [o for o in outcomes if o.chain_id == chain_id]
    if decision is not None:
        decision_set = set(decision)
        outcomes = [o for o in outcomes if o.decision in decision_set]
    if policy_hash is not None:
        outcomes = [o for o in outcomes if o.policy_hash == policy_hash]
    if since is not None:
        if not math.isfinite(since):
            raise HTTPException(status_code=400, detail="since must be a finite number")
        outcomes = [o for o in outcomes if o.timestamp >= since]
    if until is not None:
        if not math.isfinite(until):
            raise HTTPException(status_code=400, detail="until must be a finite number")
        outcomes = [o for o in outcomes if o.timestamp <= until]

    # Bounded selection: O(N) heap selection instead of O(N log N) full sort.
    # Caps memory before sorting to prevent DoS via unbounded in-memory sort.
    truncated = len(outcomes) > MAX_RESULTS_CAP
    bound = min(len(outcomes), MAX_RESULTS_CAP)
    outcomes = heapq.nlargest(bound, outcomes, key=lambda o: o.timestamp)

    total = len(outcomes)
    page = outcomes[offset : offset + limit]

    return EventsResponse(
        items=[EventItem.from_outcome(o) for o in page],
        total=total,
        offset=offset,
        limit=limit,
        truncated=truncated,
    )
