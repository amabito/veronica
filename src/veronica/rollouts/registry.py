# src/veronica/rollouts/registry.py
"""Thread-safe in-memory registry for rollout lifecycle management."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from veronica.rollouts.models import Rollout, RolloutState, StateTransition, new_rollout
from veronica.types import PolicyConfig

_MAX_SIM_RESULT_SIZE = 65_536  # 64 KB
_MAX_HISTORY_LENGTH = 1000


class InvalidTransitionError(Exception):
    """Raised when a requested state transition is not allowed."""


VALID_TRANSITIONS: dict[RolloutState, set[RolloutState]] = {
    RolloutState.DRAFT: {RolloutState.SIMULATED, RolloutState.REVOKED},
    RolloutState.SIMULATED: {RolloutState.APPROVED, RolloutState.REVOKED, RolloutState.DRAFT},
    RolloutState.APPROVED: {RolloutState.PROMOTED, RolloutState.REVOKED, RolloutState.DRAFT},
    RolloutState.PROMOTED: {RolloutState.ACTIVE, RolloutState.REVOKED, RolloutState.APPROVED},
    RolloutState.ACTIVE: {RolloutState.REVOKED},
    RolloutState.REVOKED: set(),
}


class RolloutRegistry:
    """Thread-safe in-memory rollout registry.

    Manages the full lifecycle: DRAFT -> SIMULATED -> APPROVED -> PROMOTED -> ACTIVE.
    REVOKED is terminal and reachable from any non-REVOKED state.
    """

    def __init__(self) -> None:
        self._rollouts: dict[str, Rollout] = {}
        self._lock = threading.Lock()

    def create(self, policy_config: PolicyConfig, created_by: str) -> Rollout:
        """Create a new rollout in DRAFT state."""
        rollout = new_rollout(policy_config=policy_config, created_by=created_by)
        with self._lock:
            self._rollouts[rollout.id] = rollout
        return rollout

    def get(self, rollout_id: str) -> Rollout | None:
        """Return the rollout with the given id, or None if not found."""
        with self._lock:
            return self._rollouts.get(rollout_id)

    def list_all(
        self,
        state_filter: RolloutState | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> tuple[list[Rollout], int]:
        """Return a paginated list of rollouts.

        Args:
            state_filter: If provided, only rollouts in this state are returned.
            page: 1-indexed page number.
            per_page: Number of items per page.

        Returns:
            (items, total_count) where items is the page slice.
        """
        with self._lock:
            if state_filter is not None:
                all_items = [r for r in self._rollouts.values() if r.state == state_filter]
            else:
                all_items = list(self._rollouts.values())

        total = len(all_items)
        start = (page - 1) * per_page
        end = start + per_page
        return all_items[start:end], total

    def transition(self, rollout_id: str, target_state: RolloutState, actor: str) -> Rollout:
        """Transition a rollout to a new state.

        Args:
            rollout_id: ID of the rollout to transition.
            target_state: The desired new state.
            actor: Who is performing the transition (for audit history).

        Returns:
            The updated rollout.

        Raises:
            KeyError: If the rollout does not exist.
            InvalidTransitionError: If the transition is not allowed.
        """
        with self._lock:
            rollout = self._rollouts.get(rollout_id)
            if rollout is None:
                raise KeyError(rollout_id)

            allowed = VALID_TRANSITIONS.get(rollout.state, set())
            if target_state not in allowed:
                raise InvalidTransitionError(
                    f"Cannot transition from {rollout.state.value!r} to "
                    f"{target_state.value!r}. Allowed: "
                    f"{[s.value for s in sorted(allowed, key=lambda s: s.value)]}"
                )

            now = datetime.now(timezone.utc)
            transition = StateTransition(
                from_state=rollout.state,
                to_state=target_state,
                timestamp=now,
                actor=actor,
            )
            if len(rollout.history) >= _MAX_HISTORY_LENGTH:
                raise InvalidTransitionError(
                    f"History limit of {_MAX_HISTORY_LENGTH} entries exceeded for rollout '{rollout_id}'"
                )
            rollout.history.append(transition)
            rollout.state = target_state
            rollout.updated_at = now
            return rollout

    def set_simulation_result(
        self, rollout_id: str, result: dict
    ) -> Rollout:
        """Store simulation result on an existing rollout (must exist).

        Raises ValueError if the serialized result exceeds _MAX_SIM_RESULT_SIZE bytes.
        The size check is performed inside the lock to avoid a TOCTOU race where
        the caller could swap ``result`` between the check and the store.
        """
        with self._lock:
            rollout = self._rollouts.get(rollout_id)
            if rollout is None:
                raise KeyError(rollout_id)
            serialized = json.dumps(result)
            if len(serialized) > _MAX_SIM_RESULT_SIZE:
                raise ValueError(
                    f"simulation_result exceeds {_MAX_SIM_RESULT_SIZE} byte limit "
                    f"({len(serialized)} bytes)"
                )
            rollout.simulation_result = result
            return rollout
