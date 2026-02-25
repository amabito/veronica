from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from veronica_core.containment import ExecutionConfig


@dataclass(frozen=True)
class PolicyConfig:
    """Contract between the Planner and the Executor.

    Immutable snapshot of execution limits for a single chain.
    """

    ceiling_usd: float
    ceiling_tokens_out: int
    ceiling_steps: int
    on_exceed: str  # "halt" | "degrade"
    chain_id: str
    issued_at: float  # time.time()
    planner_version: str  # e.g. "simple/0.1.0"
    priority: int = 50
    fallback_model: str | None = None
    timeout_ms: int | None = None
    rate_window_seconds: float | None = None
    rate_ceiling_calls: int | None = None
    deadline_ts: float | None = None
    expires_at: float | None = None

    def to_exec_config(self) -> ExecutionConfig:
        """Map PolicyConfig to veronica-core's ExecutionConfig.

        Phase 1 bridge. When PlannerProtocol is defined in veronica-core v1.0,
        ExecutionContext will accept PolicyConfig directly.

        Field mapping:
            ceiling_usd   -> max_cost_usd
            ceiling_steps -> max_steps
            timeout_ms    -> timeout_ms (0 if None — veronica-core treats 0 as disabled)
        """
        from veronica_core.containment import ExecutionConfig

        return ExecutionConfig(
            max_cost_usd=self.ceiling_usd,
            max_steps=self.ceiling_steps,
            max_retries_total=3,  # sensible Phase 1 default; not yet in PolicyConfig
            timeout_ms=self.timeout_ms or 0,
        )
