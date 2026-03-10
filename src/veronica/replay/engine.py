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
import time
from typing import Any

from veronica.distribution.policy_distributor import PolicyDistributor, PolicyValidationError
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

    on_exceed = override.get("on_exceed", "halt")
    if on_exceed not in {"halt", "degrade", "queue"}:
        raise ValueError(f"override_policy.on_exceed must be halt/degrade/queue, got {on_exceed!r}")

    try:
        ceiling_usd = float(ceiling_usd)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"override_policy.ceiling_usd must be numeric, got {ceiling_usd!r}") from exc
    if ceiling_usd < 0:
        raise ValueError(f"override_policy.ceiling_usd must be >= 0, got {ceiling_usd!r}")

    ceiling_steps_raw = override.get("ceiling_steps")
    ceiling_steps = int(ceiling_steps_raw) if ceiling_steps_raw is not None else None
    if ceiling_steps is not None and ceiling_steps < 0:
        raise ValueError(f"override_policy.ceiling_steps must be >= 0, got {ceiling_steps!r}")

    ceiling_tokens_out_raw = override.get("ceiling_tokens_out")
    ceiling_tokens_out = int(ceiling_tokens_out_raw) if ceiling_tokens_out_raw is not None else None
    if ceiling_tokens_out is not None and ceiling_tokens_out < 0:
        raise ValueError(f"override_policy.ceiling_tokens_out must be >= 0, got {ceiling_tokens_out!r}")

    return PolicyConfig(
        chain_id=str(chain_id),
        ceiling_usd=ceiling_usd,
        on_exceed=on_exceed,
        issued_at=float(override.get("issued_at", time.time())),
        ceiling_steps=ceiling_steps,
        ceiling_tokens_out=ceiling_tokens_out,
        fallback_model=override.get("fallback_model"),
        timeout_ms=override.get("timeout_ms"),
        priority=int(override.get("priority", 50)),
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
    projected_cost = cumulative_cost + event.cost_usd
    projected_steps = step_index + 1

    if projected_cost > policy.ceiling_usd:
        return policy.on_exceed

    if policy.ceiling_steps is not None and projected_steps > policy.ceiling_steps:
        return policy.on_exceed

    if policy.ceiling_tokens_out is not None and event.tokens > policy.ceiling_tokens_out:
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
            e for e in all_events
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
                raise ValueError(f"Invalid override_policy: {exc}") from exc

        # Cap at _MAX_REPLAY_EVENTS to prevent excessive memory usage
        if len(events) > _MAX_REPLAY_EVENTS:
            logger.warning(
                "[replay] chain '%s' has %d events, truncating to %d",
                request.chain_id,
                len(events),
                _MAX_REPLAY_EVENTS,
            )
            events = events[:_MAX_REPLAY_EVENTS]

        # Sort deterministically by timestamp then step_id
        events.sort(key=lambda e: (e.timestamp, e.step_id))

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

            if override_policy is not None:
                replayed = _replay_decision(event, idx, cumulative_cost, override_policy)
            else:
                # No override: replayed decision matches original
                replayed = original

            changed = replayed != original
            if changed:
                changed_count += 1

            diffs.append(DecisionDiff(
                step_id=event.step_id,
                original_decision=original,
                replayed_decision=replayed,
                changed=changed,
            ))

            cumulative_cost += event.cost_usd

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
