# src/veronica/replay/engine.py
"""ReplayEngine -- side-effect-free incident replay for VERONICA control plane.

Architecture
------------
ReplayEngine reads events from the store, filters by chain_id and time range,
and re-evaluates each event's decision under an optional override policy.
The engine is read-only: no state is mutated in the store or distributor.

Replay heuristic
----------------
When override_policy is provided, the engine simulates enforcement:
  - Cumulative cost exceeds ceiling_usd  -> on_exceed (default "halt")
  - Cumulative step count exceeds ceiling_steps -> on_exceed
  - Otherwise -> "allow"
Steps are processed in timestamp order.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from veronica.distribution.policy_distributor import (
    PolicyDistributor,
    PolicyValidationError,
)
from veronica.replay.models import DecisionDiff, ReplayRequest, ReplayResult
from veronica.schemas.events import StepOutcome
from veronica.types import PolicyConfig

logger = logging.getLogger(__name__)

_HALT = "halt"
_ALLOW = "allow"
_MAX_REPLAY_EVENTS = 100_000


def _build_override_policy(override: dict[str, Any]) -> PolicyConfig:
    """Build a PolicyConfig from a raw dict.

    Missing optional fields receive safe defaults. Required fields
    chain_id, ceiling_usd, and on_exceed must be present.

    Raises ValueError on invalid input.
    """
    chain_id = override.get("chain_id")
    if not isinstance(chain_id, str) or not chain_id.strip():
        raise ValueError("override_policy.chain_id must be a non-empty string")

    ceiling_usd = override.get("ceiling_usd")
    if ceiling_usd is None:
        raise ValueError("override_policy.ceiling_usd is required")
    if isinstance(ceiling_usd, bool):
        raise ValueError("override_policy.ceiling_usd must be numeric")

    on_exceed = override.get("on_exceed", "halt")
    if on_exceed not in {"halt", "degrade", "queue"}:
        raise ValueError("override_policy.on_exceed must be halt/degrade/queue")

    try:
        ceiling_usd = float(ceiling_usd)
    except (TypeError, ValueError) as exc:
        raise ValueError("override_policy.ceiling_usd must be numeric") from exc
    if not math.isfinite(ceiling_usd) or ceiling_usd < 0:
        raise ValueError(
            "override_policy.ceiling_usd must be a finite non-negative number"
        )

    ceiling_steps_raw = override.get("ceiling_steps")
    if ceiling_steps_raw is not None:
        if not isinstance(ceiling_steps_raw, int) or isinstance(
            ceiling_steps_raw, bool
        ):
            raise ValueError("override_policy.ceiling_steps must be an integer")
        ceiling_steps = ceiling_steps_raw
        if ceiling_steps < 0:
            raise ValueError("override_policy.ceiling_steps must be >= 0")
    else:
        ceiling_steps = None

    ceiling_tokens_out_raw = override.get("ceiling_tokens_out")
    if ceiling_tokens_out_raw is not None:
        if not isinstance(ceiling_tokens_out_raw, int) or isinstance(
            ceiling_tokens_out_raw, bool
        ):
            raise ValueError("override_policy.ceiling_tokens_out must be an integer")
        ceiling_tokens_out = ceiling_tokens_out_raw
        if ceiling_tokens_out < 0:
            raise ValueError("override_policy.ceiling_tokens_out must be >= 0")
    else:
        ceiling_tokens_out = None

    timeout_ms_raw = override.get("timeout_ms")
    if timeout_ms_raw is not None:
        if isinstance(timeout_ms_raw, bool):
            raise ValueError("override_policy.timeout_ms must be a number")
        try:
            timeout_ms = float(timeout_ms_raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("override_policy.timeout_ms must be numeric") from exc
        if not math.isfinite(timeout_ms) or timeout_ms < 0:
            raise ValueError(
                "override_policy.timeout_ms must be a finite non-negative number"
            )
    else:
        timeout_ms = None

    priority_raw = override.get("priority", 50)
    if isinstance(priority_raw, bool):
        raise ValueError("override_policy.priority must be an integer")
    if not isinstance(priority_raw, int):
        raise ValueError("override_policy.priority must be an integer")
    priority = priority_raw
    if not (0 <= priority <= 100):
        raise ValueError(f"override_policy.priority must be 0-100, got {priority!r}")

    issued_at_raw = override.get("issued_at", time.time())
    if isinstance(issued_at_raw, bool):
        raise ValueError("override_policy.issued_at must be a number")
    try:
        issued_at = float(issued_at_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("override_policy.issued_at must be numeric") from exc
    if not math.isfinite(issued_at) or issued_at < 0:
        raise ValueError(
            "override_policy.issued_at must be a finite non-negative number"
        )

    return PolicyConfig(
        chain_id=str(chain_id).strip(),
        ceiling_usd=ceiling_usd,
        on_exceed=on_exceed,
        issued_at=issued_at,
        ceiling_steps=ceiling_steps,
        ceiling_tokens_out=ceiling_tokens_out,
        fallback_model=override.get("fallback_model"),
        timeout_ms=timeout_ms,
        priority=priority,
    )


def _replay_decision(
    event: StepOutcome,
    step_index: int,
    cumulative_cost: float,
    policy: PolicyConfig,
) -> str:
    """Compute what the decision would be under override policy.

    Returns the on_exceed value ("halt", "degrade", "queue") when a ceiling
    is breached, otherwise "allow".
    """
    event_cost = event.cost_usd if math.isfinite(event.cost_usd) else 0.0
    projected_cost = cumulative_cost + event_cost
    projected_steps = step_index + 1

    if projected_cost > policy.ceiling_usd:
        return policy.on_exceed

    if policy.ceiling_steps is not None and projected_steps > policy.ceiling_steps:
        return policy.on_exceed

    # ceiling_tokens_out is a per-step limit (not cumulative) by design:
    # individual step outputs exceeding the ceiling trigger enforcement.
    event_tokens = event.tokens if math.isfinite(event.tokens) else 0
    if (
        policy.ceiling_tokens_out is not None
        and event_tokens > policy.ceiling_tokens_out
    ):
        return policy.on_exceed

    return _ALLOW


class ReplayEngine:
    """Replays recorded chain events against an optional override policy.

    Parameters
    ----------
    store       : object with a `snapshot()` method returning list[StepOutcome]
    distributor : PolicyDistributor (used to validate override policies)
    """

    def __init__(self, store: Any, distributor: PolicyDistributor) -> None:
        self._store = store
        self._distributor = distributor

    def replay(self, request: ReplayRequest) -> ReplayResult:
        """Replay events for chain_id within the given time range.

        This method is side-effect free: the store is not modified.

        Parameters
        ----------
        request : ReplayRequest

        Returns
        -------
        ReplayResult with per-step DecisionDiff entries and summary.
        """
        all_events: list[StepOutcome] = self._store.snapshot()

        # Filter by chain_id and time range (both bounds inclusive)
        events = [
            e
            for e in all_events
            if (
                e.chain_id == request.chain_id
                and request.from_timestamp <= e.timestamp <= request.to_timestamp
            )
        ]

        # Build and validate override policy BEFORE touching events so that
        # invalid inputs always return an error regardless of event count.
        override_policy: PolicyConfig | None = None
        if request.override_policy is not None:
            try:
                override_policy = _build_override_policy(request.override_policy)
                self._distributor._validate(override_policy)
            except (ValueError, PolicyValidationError) as exc:
                raise ValueError("Invalid override_policy: validation failed") from exc

        # Sort deterministically by timestamp then step_id
        events.sort(key=lambda e: (e.timestamp, e.step_id))

        # Cap at _MAX_REPLAY_EVENTS AFTER sort to keep chronologically earliest
        if len(events) > _MAX_REPLAY_EVENTS:
            logger.warning(
                "[replay] chain '%s' has %d events, truncating to %d",
                request.chain_id,
                len(events),
                _MAX_REPLAY_EVENTS,
            )
            events = events[:_MAX_REPLAY_EVENTS]

        if not events:
            return ReplayResult(
                chain_id=request.chain_id,
                event_count=0,
                diffs=[],
                changed_count=0,
                summary=f"No events found for chain '{request.chain_id}' in the requested time range.",
            )

        diffs: list[DecisionDiff] = []
        cumulative_cost = 0.0
        changed_count = 0

        for idx, event in enumerate(events):
            original = event.decision

            # Clamp non-finite cost_usd to prevent NaN/Inf from bypassing ceiling checks
            event_cost = event.cost_usd if math.isfinite(event.cost_usd) else 0.0

            if override_policy is not None:
                replayed = _replay_decision(
                    event, idx, cumulative_cost, override_policy
                )
            else:
                # No override: replayed decision matches original
                replayed = original

            changed = replayed != original
            if changed:
                changed_count += 1

            diffs.append(
                DecisionDiff(
                    step_id=event.step_id,
                    original_decision=original,
                    replayed_decision=replayed,
                    changed=changed,
                )
            )

            cumulative_cost += event_cost

        total = len(events)
        if override_policy is not None and changed_count > 0:
            summary = (
                f"Replayed {total} event(s) for chain '{request.chain_id}'. "
                f"{changed_count} decision(s) changed under override policy "
                f"(ceiling_usd={override_policy.ceiling_usd}, "
                f"ceiling_steps={override_policy.ceiling_steps})."
            )
        elif override_policy is not None:
            summary = (
                f"Replayed {total} event(s) for chain '{request.chain_id}'. "
                f"All decisions unchanged under override policy."
            )
        else:
            summary = (
                f"Replayed {total} event(s) for chain '{request.chain_id}'. "
                f"No override policy applied; all decisions match original."
            )

        return ReplayResult(
            chain_id=request.chain_id,
            event_count=total,
            diffs=diffs,
            changed_count=changed_count,
            summary=summary,
        )
