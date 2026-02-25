from __future__ import annotations

import time
import uuid
from collections import deque

from veronica.planner._types import PolicyConfig

_PLANNER_VERSION = "simple/0.1.0"
_DEFAULT_TOKENS_OUT = 4096


class SimplePlanner:
    """Lightweight adaptive planner for a single execution chain.

    Adjusts cost ceilings based on feedback snapshots from the executor.
    """

    def __init__(
        self,
        base_ceiling_usd: float = 1.00,
        max_ceiling_usd: float = 10.00,
        min_ceiling_usd: float = 0.10,
        default_steps_multiplier: float = 2.0,
        default_timeout_ms: int = 30_000,
        default_on_exceed: str = "halt",
        fallback_model: str | None = None,
    ) -> None:
        self._ceiling_usd = base_ceiling_usd
        self._max_ceiling_usd = max_ceiling_usd
        self._min_ceiling_usd = min_ceiling_usd
        self._default_steps_multiplier = default_steps_multiplier
        self._default_timeout_ms = default_timeout_ms
        self._default_on_exceed = default_on_exceed
        self._fallback_model = fallback_model
        self._force_halt = False
        self._history: deque[dict] = deque(maxlen=10)

    def create_config(
        self,
        estimated_steps: int,
        priority: int = 50,
    ) -> PolicyConfig:
        """Create a PolicyConfig for a new execution chain."""
        ceiling_steps = max(
            int(estimated_steps * self._default_steps_multiplier),
            estimated_steps + 5,
        )
        on_exceed = self._default_on_exceed
        if self._force_halt and priority < 90:
            on_exceed = "halt"

        return PolicyConfig(
            ceiling_usd=self._ceiling_usd,
            ceiling_tokens_out=_DEFAULT_TOKENS_OUT,
            ceiling_steps=ceiling_steps,
            on_exceed=on_exceed,
            chain_id=str(uuid.uuid4()),
            issued_at=time.time(),
            planner_version=_PLANNER_VERSION,
            priority=priority,
            fallback_model=self._fallback_model,
            timeout_ms=self._default_timeout_ms,
        )

    def update(self, snapshot: dict) -> None:
        """Adjust internal state based on an executor feedback snapshot.

        Rules:
            1. halt_count > 0  -> ceiling *= 0.90 (floor at min)
            2. halt_count == 0 and fail_count == 0 -> ceiling *= 1.05 (cap at max)
            3. max_depth >= 8  -> _force_halt = True
        """
        self._history.append(snapshot)

        aggregates = snapshot.get("aggregates", {})
        halt_count: int = aggregates.get("halt_count", 0)
        fail_count: int = aggregates.get("fail_count", 0)
        max_depth: int = aggregates.get("max_depth", 0)

        if halt_count > 0:
            self._ceiling_usd = max(
                self._ceiling_usd * 0.90,
                self._min_ceiling_usd,
            )
        elif fail_count == 0:
            self._ceiling_usd = min(
                self._ceiling_usd * 1.05,
                self._max_ceiling_usd,
            )

        if max_depth >= 8:
            self._force_halt = True
