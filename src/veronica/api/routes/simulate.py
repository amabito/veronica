# src/veronica/api/routes/simulate.py
"""POST /simulate endpoint -- side-effect-free policy simulation."""
from __future__ import annotations

import logging
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request

from veronica.api.schemas import SimulateRequest, SimulateResponse, SimulationStepResult
from veronica.distribution.policy_distributor import PolicyValidationError
from veronica.types import PolicyConfig

logger = logging.getLogger(__name__)

router = APIRouter(tags=["simulate"])

_REQUIRED_POLICY_FIELDS = {"chain_id", "ceiling_usd", "on_exceed"}
_VALID_ON_EXCEED = frozenset({"halt", "degrade", "queue"})


def _build_policy(policy_dict: dict) -> PolicyConfig:
    """Validate and construct a PolicyConfig from a raw dict.

    Raises HTTPException(422) on invalid input.
    """
    missing = _REQUIRED_POLICY_FIELDS - policy_dict.keys()
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required policy fields: {sorted(missing)}",
        )

    on_exceed = policy_dict.get("on_exceed")
    if on_exceed not in _VALID_ON_EXCEED:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid on_exceed value. Must be one of: {sorted(_VALID_ON_EXCEED)}",
        )

    ceiling_usd = policy_dict.get("ceiling_usd")
    if not isinstance(ceiling_usd, (int, float)) or ceiling_usd < 0:
        raise HTTPException(
            status_code=422,
            detail="ceiling_usd must be a non-negative number",
        )

    # Validate numeric timestamp fields before constructing PolicyConfig.
    # Non-numeric values cause TypeError in _evaluate_step (time.time() > deadline_ts).
    deadline_ts = policy_dict.get("deadline_ts")
    if deadline_ts is not None:
        try:
            deadline_ts = float(deadline_ts)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail="Invalid policy configuration",
            ) from exc

    expires_at = policy_dict.get("expires_at")
    if expires_at is not None:
        try:
            expires_at = float(expires_at)
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=422,
                detail="Invalid policy configuration",
            ) from exc

    try:
        return PolicyConfig(
            chain_id=str(policy_dict["chain_id"]),
            ceiling_usd=float(policy_dict["ceiling_usd"]),
            on_exceed=on_exceed,
            issued_at=float(policy_dict.get("issued_at", time.time())),
            ceiling_tokens_out=policy_dict.get("ceiling_tokens_out"),
            ceiling_steps=policy_dict.get("ceiling_steps"),
            fallback_model=policy_dict.get("fallback_model"),
            timeout_ms=policy_dict.get("timeout_ms"),
            rate_window_seconds=policy_dict.get("rate_window_seconds"),
            rate_ceiling_calls=policy_dict.get("rate_ceiling_calls"),
            priority=int(policy_dict.get("priority", 50)),
            deadline_ts=deadline_ts,
            expires_at=expires_at,
            planner_version=policy_dict.get("planner_version"),
        )
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=422,
            detail="Invalid policy configuration",
        ) from exc


def _evaluate_step(
    step_cost: float,
    step_tokens_out: int,
    step_index: int,
    step_elapsed_ms: float,
    step_kind: str,
    cumulative_cost: float,
    cumulative_steps: int,
    cumulative_tokens_out: int,
    policy: PolicyConfig,
) -> tuple[Literal["allow", "halt", "degrade", "queue"], str]:
    """Evaluate one simulated step against the policy.

    Returns (decision, reason). No side effects.
    """
    projected_cost = cumulative_cost + step_cost
    projected_tokens = cumulative_tokens_out + step_tokens_out
    projected_steps = cumulative_steps + 1

    # Budget ceiling check
    if projected_cost > policy.ceiling_usd:
        return policy.on_exceed, (  # type: ignore[return-value]
            f"cost ceiling exceeded: {projected_cost:.6f} > {policy.ceiling_usd}"
        )

    # Token ceiling check
    if policy.ceiling_tokens_out is not None and projected_tokens > policy.ceiling_tokens_out:
        return policy.on_exceed, (  # type: ignore[return-value]
            f"token ceiling exceeded: {projected_tokens} > {policy.ceiling_tokens_out}"
        )

    # Step ceiling check
    if policy.ceiling_steps is not None and projected_steps > policy.ceiling_steps:
        return policy.on_exceed, (  # type: ignore[return-value]
            f"step ceiling exceeded: {projected_steps} > {policy.ceiling_steps}"
        )

    # Deadline check
    if policy.deadline_ts is not None and time.time() > policy.deadline_ts:
        return "halt", f"deadline exceeded: {policy.deadline_ts}"

    # Expiry check
    if policy.expires_at is not None and time.time() > policy.expires_at:
        return "halt", f"policy expired at {policy.expires_at}"

    return "allow", "within budget"


@router.post("/simulate", response_model=SimulateResponse, summary="Simulate policy evaluation")
async def simulate(request: Request, body: SimulateRequest) -> SimulateResponse:
    """Run a side-effect-free simulation of policy enforcement.

    Evaluates each step in the scenario sequentially against the provided
    policy. The Store is never modified.
    """
    distributor = request.app.state.distributor

    # Build and validate the policy
    try:
        policy = _build_policy(body.policy)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Invalid simulation request") from exc

    # Distribute to get the policy_hash (read-only operation on distributor)
    try:
        bundle = distributor.distribute(policy)
    except (PolicyValidationError, TypeError, ValueError) as exc:
        logger.warning("[simulate] policy validation error: %s", exc)
        raise HTTPException(status_code=422, detail="Invalid policy configuration") from exc

    step_results: list[SimulationStepResult] = []
    cumulative_cost = 0.0
    cumulative_tokens_out = 0
    steps_allowed = 0
    steps_halted = 0
    final_decision: Literal["allow", "halt", "degrade", "queue"] = "allow"

    for idx, step in enumerate(body.steps):
        decision, reason = _evaluate_step(
            step_cost=step.cost_usd,
            step_tokens_out=step.tokens_out,
            step_index=idx,
            step_elapsed_ms=step.elapsed_ms,
            step_kind=step.kind,
            cumulative_cost=cumulative_cost,
            cumulative_steps=idx,
            cumulative_tokens_out=cumulative_tokens_out,
            policy=policy,
        )

        cumulative_cost += step.cost_usd
        cumulative_tokens_out += step.tokens_out
        final_decision = decision

        step_results.append(
            SimulationStepResult(
                step_index=idx,
                kind=step.kind,
                cost_usd=step.cost_usd,
                tokens_out=step.tokens_out,
                elapsed_ms=step.elapsed_ms,
                cumulative_cost_usd=cumulative_cost,
                decision=decision,
                reason=reason,
            )
        )

        if decision == "allow":
            steps_allowed += 1
        else:
            steps_halted += 1
            # Stop simulation on first non-allow decision
            break

    return SimulateResponse(
        policy_hash=bundle.policy_hash,
        total_steps=len(step_results),
        steps_allowed=steps_allowed,
        steps_halted=steps_halted,
        total_cost_usd=cumulative_cost,
        final_decision=final_decision,
        step_results=step_results,
        store_unchanged=True,
    )
