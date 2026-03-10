# src/veronica/rollouts/models.py
"""Rollout pipeline data models."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from veronica.types import PolicyConfig


class RolloutState(str, Enum):
    DRAFT = "draft"
    SIMULATED = "simulated"
    APPROVED = "approved"
    PROMOTED = "promoted"
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass
class StateTransition:
    from_state: RolloutState
    to_state: RolloutState
    timestamp: datetime
    actor: str


@dataclass
class Rollout:
    id: str
    policy_config: PolicyConfig
    state: RolloutState
    created_at: datetime
    updated_at: datetime
    created_by: str
    history: list[StateTransition] = field(default_factory=list)
    simulation_result: dict[str, Any] | None = None


def new_rollout(policy_config: PolicyConfig, created_by: str) -> Rollout:
    """Factory: create a new Rollout in DRAFT state."""
    now = datetime.now(timezone.utc)
    return Rollout(
        id=str(uuid.uuid4()),
        policy_config=policy_config,
        state=RolloutState.DRAFT,
        created_at=now,
        updated_at=now,
        created_by=created_by,
        history=[],
        simulation_result=None,
    )
