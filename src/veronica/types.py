# src/veronica/types.py
"""VERONICA OS data types -- all frozen dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

from veronica_core.containment.execution_context import ExecutionConfig
from veronica_core.shield.event import SafetyEvent


@dataclass(frozen=True)
class StepIntent:
    """Pre-execution declaration. What the application intends to do."""

    step_id: str
    request_id: str
    chain_id: str
    kind: Literal["llm", "tool", "system"]
    model: str | None
    tool_name: str | None
    timeout_ms: int
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class StepOutcome:
    """Post-execution record. What actually happened."""

    step_id: str
    request_id: str
    chain_id: str
    kind: Literal["llm", "tool", "system"]
    status: Literal["ok", "halted", "error", "timeout"]
    cost_usd: float
    tokens_in: int
    tokens_out: int
    elapsed_ms: float
    model: str | None
    events: tuple[SafetyEvent, ...]
    timestamp_ms: int


@dataclass(frozen=True)
class HistoryView:
    """Lightweight history slice. Statistics, not raw logs."""

    chain_id: str
    last_n: tuple[StepOutcome, ...]
    rolling_cost_usd: float
    failure_streak: int
    depth: int
    loop_score: float

    # Phase 2 additions (all with defaults for backward compatibility)
    success_streak: int = 0
    cost_per_step_ema: float = 0.0
    cost_per_step_ema_by_model: Mapping[str, float] = field(default_factory=dict)
    latency_ema_ms: Mapping[str, float] = field(default_factory=dict)
    budget_headroom_ratio: float = 1.0


@dataclass(frozen=True)
class Signal:
    """One detected pattern."""

    kind: str
    severity: Literal["info", "warning", "critical"]
    detail: str


@dataclass(frozen=True)
class AnalysisResult:
    """Analyzer output. Detected patterns from intent vs outcome."""

    signals: tuple[Signal, ...]
    risk_level: Literal["nominal", "elevated", "critical"]
    recommendation: Literal["continue", "tighten", "loosen", "halt"]


@dataclass(frozen=True)
class CostEstimate:
    """CostModel output. Predicted cost of the next step."""

    estimated_usd: float
    confidence: float
    model_used: str
    basis: Literal["historical", "pricing_table", "fallback"]


@dataclass(frozen=True)
class BudgetState:
    """Current budget position. Planner's input."""

    request_remaining_usd: float
    chain_remaining_usd: float
    window_remaining_steps: int


@dataclass(frozen=True)
class DesiredPolicy:
    """Planner output. One chain's desired limits (local optimum)."""

    chain_id: str
    ceiling_usd: float
    ceiling_steps: int
    ceiling_tokens_out: int
    on_exceed: Literal["halt", "degrade", "queue"]
    fallback_model: str | None
    timeout_ms: int
    priority: int


@dataclass(frozen=True)
class DecisionMeta:
    """Audit record. Why this PolicyConfig was chosen."""

    risk_level: str
    recommendation: str
    degraded: bool
    stage_time_ms: Mapping[str, float]


# --- PolicyConfig: the contract between OS and Engine ---

_DEFAULT_STEPS = 100
_DEFAULT_RETRIES = 10


@dataclass(frozen=True)
class PolicyConfig:
    """Contract between Planner (VERONICA) and Executor (veronica-core).

    Immutable after issue. The Planner produces it; the Executor enforces it.
    See docs/policy-config.md for the full specification.
    """

    # Required
    chain_id: str
    ceiling_usd: float
    on_exceed: Literal["halt", "degrade", "queue"]
    issued_at: float

    # Budget (optional)
    ceiling_tokens_out: int | None = None
    ceiling_steps: int | None = None

    # Escalation
    fallback_model: str | None = None

    # Time
    timeout_ms: int | None = None
    rate_window_seconds: float | None = None
    rate_ceiling_calls: int | None = None

    # Arbitration
    priority: int = 50
    deadline_ts: float | None = None

    # Metadata
    expires_at: float | None = None
    planner_version: str | None = None

    def to_exec_config(self) -> ExecutionConfig:
        """Convert to veronica-core ExecutionConfig.

        This is the sole bridge between the OS and the engine.
        None fields receive safe defaults.
        """
        return ExecutionConfig(
            max_cost_usd=self.ceiling_usd,
            max_steps=self.ceiling_steps if self.ceiling_steps is not None else _DEFAULT_STEPS,
            max_retries_total=_DEFAULT_RETRIES,
            timeout_ms=self.timeout_ms if self.timeout_ms is not None else 0,
        )


@dataclass(frozen=True)
class StepHandle:
    """Returned by before_step. Passed to after_step."""

    intent: StepIntent
    policy: PolicyConfig
    desired: DesiredPolicy
    cost: CostEstimate
    decision_meta: DecisionMeta
