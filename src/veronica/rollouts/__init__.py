# src/veronica/rollouts/__init__.py
"""Rollout pipeline -- 6-state lifecycle management for policy promotions."""
from __future__ import annotations

from veronica.rollouts.models import Rollout, RolloutState, StateTransition
from veronica.rollouts.registry import InvalidTransitionError, RolloutRegistry
from veronica.rollouts import router  # noqa: F401 -- re-exported for app.py

__all__ = [
    "Rollout",
    "RolloutState",
    "StateTransition",
    "InvalidTransitionError",
    "RolloutRegistry",
    "router",
]
