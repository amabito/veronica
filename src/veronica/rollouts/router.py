# src/veronica/rollouts/router.py
"""FastAPI router for rollout pipeline endpoints."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from veronica.api.routes.simulate import _build_policy
from veronica.rollouts.models import Rollout, RolloutState
from veronica.rollouts.registry import InvalidTransitionError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/rollouts", tags=["rollouts"])


# ---------------------------------------------------------------------------
# Pydantic API schemas
# ---------------------------------------------------------------------------


class CreateRolloutRequest(BaseModel):
    policy_config: dict[str, Any]
    created_by: str


class ActorRequest(BaseModel):
    actor: str


class StateTransitionResponse(BaseModel):
    from_state: str
    to_state: str
    timestamp: str
    actor: str


class RolloutResponse(BaseModel):
    id: str
    state: str
    created_by: str
    created_at: str
    updated_at: str
    history: list[StateTransitionResponse]
    simulation_result: dict[str, Any] | None


class RolloutListResponse(BaseModel):
    items: list[RolloutResponse]
    total: int
    page: int
    per_page: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rollout_to_response(r: Rollout) -> RolloutResponse:
    return RolloutResponse(
        id=r.id,
        state=r.state.value,
        created_by=r.created_by,
        created_at=r.created_at.isoformat(),
        updated_at=r.updated_at.isoformat(),
        history=[
            StateTransitionResponse(
                from_state=t.from_state.value,
                to_state=t.to_state.value,
                timestamp=t.timestamp.isoformat(),
                actor=t.actor,
            )
            for t in r.history
        ],
        simulation_result=r.simulation_result,
    )


def _get_rollout_or_404(request: Request, rollout_id: str) -> Rollout:
    rollout_registry = request.app.state.rollout_registry
    rollout = rollout_registry.get(rollout_id)
    if rollout is None:
        raise HTTPException(status_code=404, detail=f"Rollout '{rollout_id}' not found")
    return rollout


def _do_transition(request: Request, rollout_id: str, target: RolloutState, actor: str) -> Rollout:
    rollout_registry = request.app.state.rollout_registry
    try:
        return rollout_registry.transition(rollout_id, target, actor)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Rollout '{rollout_id}' not found")
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=201, response_model=RolloutResponse, summary="Create rollout")
async def create_rollout(body: CreateRolloutRequest, request: Request) -> RolloutResponse:
    """Create a new rollout in DRAFT state."""
    policy = _build_policy(body.policy_config)
    rollout_registry = request.app.state.rollout_registry
    rollout = rollout_registry.create(policy_config=policy, created_by=body.created_by)
    return _rollout_to_response(rollout)


@router.get("", response_model=RolloutListResponse, summary="List rollouts")
async def list_rollouts(
    request: Request,
    state: RolloutState | None = Query(default=None, description="Filter by state"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=1, le=200),
) -> RolloutListResponse:
    """Return a paginated list of rollouts, optionally filtered by state."""
    rollout_registry = request.app.state.rollout_registry
    items, total = rollout_registry.list_all(state_filter=state, page=page, per_page=per_page)
    return RolloutListResponse(
        items=[_rollout_to_response(r) for r in items],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{rollout_id}", response_model=RolloutResponse, summary="Get rollout")
async def get_rollout(rollout_id: str, request: Request) -> RolloutResponse:
    """Return a rollout by ID. 404 if not found."""
    rollout = _get_rollout_or_404(request, rollout_id)
    return _rollout_to_response(rollout)


@router.post("/{rollout_id}/simulate", response_model=RolloutResponse, summary="Simulate rollout")
async def simulate_rollout(rollout_id: str, request: Request) -> RolloutResponse:
    """Run PolicyDistributor simulation, store result, and transition to SIMULATED."""
    rollout = _get_rollout_or_404(request, rollout_id)

    distributor = request.app.state.distributor
    try:
        bundle = distributor.distribute(rollout.policy_config)
        sim_result = {
            "policy_hash": bundle.policy_hash,
            "distributed_at": bundle.distributed_at,
            "version": bundle.version,
        }
    except Exception as exc:
        logger.warning("[rollout simulate] distributor error: %s", exc)
        raise HTTPException(status_code=422, detail=f"Simulation failed: {exc}") from exc

    rollout_registry = request.app.state.rollout_registry
    rollout_registry.set_simulation_result(rollout_id, sim_result)

    rollout = _do_transition(request, rollout_id, RolloutState.SIMULATED, actor="system")
    return _rollout_to_response(rollout)


@router.post("/{rollout_id}/approve", response_model=RolloutResponse, summary="Approve rollout")
async def approve_rollout(
    rollout_id: str, body: ActorRequest, request: Request
) -> RolloutResponse:
    """Transition rollout to APPROVED."""
    rollout = _do_transition(request, rollout_id, RolloutState.APPROVED, actor=body.actor)
    return _rollout_to_response(rollout)


@router.post("/{rollout_id}/promote", response_model=RolloutResponse, summary="Promote rollout")
async def promote_rollout(
    rollout_id: str, body: ActorRequest, request: Request
) -> RolloutResponse:
    """Transition rollout to PROMOTED."""
    rollout = _do_transition(request, rollout_id, RolloutState.PROMOTED, actor=body.actor)
    return _rollout_to_response(rollout)


@router.post("/{rollout_id}/activate", response_model=RolloutResponse, summary="Activate rollout")
async def activate_rollout(
    rollout_id: str, body: ActorRequest, request: Request
) -> RolloutResponse:
    """Transition rollout to ACTIVE and register the policy."""
    rollout = _do_transition(request, rollout_id, RolloutState.ACTIVE, actor=body.actor)

    # Register policy in PolicyRegistry (best-effort, don't fail activation on error)
    try:
        policy_registry = request.app.state.registry
        policy_registry.register(rollout.policy_config)
    except Exception as exc:
        logger.warning("[rollout activate] policy registration error: %s", exc)

    return _rollout_to_response(rollout)


@router.post("/{rollout_id}/revoke", response_model=RolloutResponse, summary="Revoke rollout")
async def revoke_rollout(
    rollout_id: str, body: ActorRequest, request: Request
) -> RolloutResponse:
    """Transition rollout to REVOKED (terminal state)."""
    rollout = _do_transition(request, rollout_id, RolloutState.REVOKED, actor=body.actor)
    return _rollout_to_response(rollout)
