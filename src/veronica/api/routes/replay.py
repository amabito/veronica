# src/veronica/api/routes/replay.py
"""POST /replay endpoint -- side-effect-free incident replay."""
from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator

from veronica.replay.engine import ReplayEngine
from veronica.replay.models import ReplayRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["replay"])


class DecisionDiffResponse(BaseModel):
    """Wire representation of a per-step decision comparison."""

    step_id: str
    original_decision: str
    replayed_decision: str
    changed: bool


class ReplayResponse(BaseModel):
    """Wire representation of a replay result."""

    chain_id: str
    event_count: int
    diffs: list[DecisionDiffResponse]
    changed_count: int
    summary: str
    store_unchanged: bool = True


class ReplayRequestBody(BaseModel):
    """Request body for POST /replay."""

    chain_id: str = Field(
        ...,
        min_length=1,
        max_length=256,
        pattern=r"^[a-zA-Z0-9_:@.\-]+$",
        description="Chain scope to replay",
    )
    from_timestamp: float = Field(..., description="Unix epoch seconds (inclusive lower bound)")
    to_timestamp: float = Field(..., description="Unix epoch seconds (inclusive upper bound)")
    override_policy: dict[str, Any] | None = Field(
        default=None,
        description="Optional override policy fields (PolicyConfig-like). "
                    "chain_id, ceiling_usd, and on_exceed are required if provided.",
    )

    @field_validator("from_timestamp", "to_timestamp")
    @classmethod
    def _timestamps_positive(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("timestamps must be finite numbers")
        if v < 0:
            raise ValueError("timestamps must be non-negative")
        return v

    @model_validator(mode="after")
    def _from_before_to(self) -> "ReplayRequestBody":
        if self.from_timestamp >= self.to_timestamp:
            raise ValueError("from_timestamp must be less than to_timestamp")
        return self


@router.post(
    "/replay",
    response_model=ReplayResponse,
    summary="Replay recorded chain events against an optional override policy",
)
async def replay(request: Request, body: ReplayRequestBody) -> ReplayResponse:
    """Replay recorded events for a chain within a time window.

    Optionally applies an override policy to show how decisions would
    differ under stricter or looser constraints. The store is never
    modified; this is a read-only, side-effect-free operation.

    Returns 422 for invalid input. Returns an empty result (event_count=0)
    if no events are found for the given chain and time range.
    """
    store = request.app.state.ingestor.store
    distributor = request.app.state.distributor

    engine = ReplayEngine(store=store, distributor=distributor)

    replay_request = ReplayRequest(
        chain_id=body.chain_id,
        from_timestamp=body.from_timestamp,
        to_timestamp=body.to_timestamp,
        override_policy=body.override_policy,
    )

    try:
        result = engine.replay(replay_request)
    except ValueError as exc:
        logger.warning("[replay] invalid override_policy: %s", exc)
        raise HTTPException(status_code=422, detail="Invalid override policy") from exc
    except Exception as exc:
        logger.exception("[replay] unexpected error: %s", exc)
        raise HTTPException(status_code=500, detail="Replay failed") from exc

    return ReplayResponse(
        chain_id=result.chain_id,
        event_count=result.event_count,
        diffs=[
            DecisionDiffResponse(
                step_id=d.step_id,
                original_decision=d.original_decision,
                replayed_decision=d.replayed_decision,
                changed=d.changed,
            )
            for d in result.diffs
        ],
        changed_count=result.changed_count,
        summary=result.summary,
        store_unchanged=True,
    )
