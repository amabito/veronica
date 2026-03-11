# src/veronica/api/routes/incidents.py
"""GET /incidents/{chain_id}/{step_id} endpoint -- incident detail."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel

from veronica.api.routes._validators import CHAIN_ID_RE

router = APIRouter(tags=["incidents"])
_HALT_DECISIONS = frozenset({"halt", "degrade"})


class TimelineEvent(BaseModel):
    """Single event in the incident timeline."""

    step_id: str
    decision: str
    cost_usd: float
    tokens: int
    duration_ms: float
    timestamp: float
    operation_name: str
    audit_id: str


class IncidentDetail(BaseModel):
    """Full detail for one incident (a specific chain_id + step_id)."""

    incident_id: str
    chain_id: str
    step_id: str
    decision: str
    reason_code: str | None
    policy_hash: str | None
    audit_id: str
    timeline: list[TimelineEvent]
    root_cause: dict[str, Any] | None


@router.get(
    "/incidents/{chain_id}/{step_id}",
    response_model=IncidentDetail,
    summary="Get incident detail",
    description=(
        "Returns full detail for the incident identified by chain_id and step_id. "
        "Includes a timeline of all events for the chain, sorted by timestamp. "
        "root_cause is the first halt/degrade event in the chain. "
        "Returns 404 if the step is not found."
    ),
)
async def get_incident(
    request: Request,
    chain_id: Annotated[
        str, Path(min_length=1, max_length=256, pattern=CHAIN_ID_RE, description="Chain ID")
    ],
    step_id: Annotated[
        str, Path(min_length=1, max_length=256, pattern=CHAIN_ID_RE, description="Step ID")
    ],
) -> IncidentDetail:
    """Return incident detail for one chain+step combination."""
    ingestor = request.app.state.ingestor
    outcomes = ingestor.store.snapshot()

    # Find the target step
    target = next(
        (o for o in outcomes if o.chain_id == chain_id and o.step_id == step_id),
        None,
    )
    if target is None:
        raise HTTPException(
            status_code=404,
            detail=f"No event found for chain_id={chain_id!r}, step_id={step_id!r}",
        )

    # Build timeline: all events for this chain, sorted by timestamp ascending
    chain_outcomes = sorted(
        [o for o in outcomes if o.chain_id == chain_id],
        key=lambda o: o.timestamp,
    )

    timeline = [
        TimelineEvent(
            step_id=o.step_id,
            decision=o.decision,
            cost_usd=o.cost_usd,
            tokens=o.tokens,
            duration_ms=o.duration_ms,
            timestamp=o.timestamp,
            operation_name=o.operation_name,
            audit_id=o.audit_id,
        )
        for o in chain_outcomes
    ]

    # root_cause: first halt/degrade event in the chain
    first_bad = next((o for o in chain_outcomes if o.decision in _HALT_DECISIONS), None)
    root_cause: dict[str, Any] | None = None
    if first_bad is not None:
        root_cause = {
            "step_id": first_bad.step_id,
            "decision": first_bad.decision,
            "timestamp": first_bad.timestamp,
            "operation_name": first_bad.operation_name,
            "audit_id": first_bad.audit_id,
        }

    incident_id = f"{chain_id}:{step_id}"

    return IncidentDetail(
        incident_id=incident_id,
        chain_id=chain_id,
        step_id=step_id,
        decision=target.decision,
        reason_code=None,  # not yet available in StepOutcome schema
        policy_hash=target.policy_hash,
        audit_id=target.audit_id,
        timeline=timeline,
        root_cause=root_cause,
    )
