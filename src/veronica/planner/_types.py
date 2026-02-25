from __future__ import annotations

from dataclasses import dataclass


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
