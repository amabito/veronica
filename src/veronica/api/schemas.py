# src/veronica/api/schemas.py
"""Pydantic request/response models for the Veronica control-plane API.

These models are the wire format; they are distinct from the internal
frozen dataclasses in veronica.types and veronica.schemas.events.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Policy schemas
# ---------------------------------------------------------------------------


class PolicyResponse(BaseModel):
    """Full representation of a PolicyConfig, as returned by the API."""

    chain_id: str
    ceiling_usd: float
    on_exceed: Literal["halt", "degrade", "queue"]
    issued_at: float
    ceiling_tokens_out: int | None = None
    ceiling_steps: int | None = None
    fallback_model: str | None = None
    timeout_ms: int | None = None
    rate_window_seconds: float | None = None
    rate_ceiling_calls: int | None = None
    priority: int = 50
    deadline_ts: float | None = None
    expires_at: float | None = None
    planner_version: str | None = None
    policy_hash: str
    version: int


class PolicySummary(BaseModel):
    """Lightweight policy entry for paginated list responses."""

    chain_id: str
    on_exceed: Literal["halt", "degrade", "queue"]
    ceiling_usd: float
    priority: int
    issued_at: float
    policy_hash: str
    version: int


class PolicyListResponse(BaseModel):
    """Paginated list of policies."""

    items: list[PolicySummary]
    total: int
    page: int
    per_page: int


class PolicyUpdateRequest(BaseModel):
    """Fields that may be updated via PUT /policies/{id}.

    All fields are optional; only provided fields are applied.
    chain_id cannot be changed (use the path parameter as the identifier).
    current_version is required for optimistic concurrency control (409 on mismatch).
    """

    current_version: int = Field(
        ...,
        description="The version the client last read. Rejected with 409 if stale.",
    )
    ceiling_usd: float | None = None
    on_exceed: Literal["halt", "degrade", "queue"] | None = None
    ceiling_tokens_out: int | None = None
    ceiling_steps: int | None = None
    fallback_model: str | None = None
    timeout_ms: int | None = None
    rate_window_seconds: float | None = None
    rate_ceiling_calls: int | None = None
    priority: int | None = None
    deadline_ts: float | None = None
    expires_at: float | None = None
    planner_version: str | None = None


# ---------------------------------------------------------------------------
# Simulation schemas
# ---------------------------------------------------------------------------


class SimulationStep(BaseModel):
    """One step in a simulation scenario."""

    kind: Literal["llm", "tool", "system"] = "llm"
    cost_usd: float = Field(default=0.0, ge=0)
    tokens_in: int = 0
    tokens_out: int = Field(default=0, ge=0)
    elapsed_ms: float = 0.0
    model: str | None = None
    tool_name: str | None = None


class SimulationStepResult(BaseModel):
    """Result of evaluating one simulation step against the policy."""

    step_index: int
    kind: str
    cost_usd: float
    tokens_out: int
    elapsed_ms: float
    cumulative_cost_usd: float
    decision: Literal["allow", "halt", "degrade", "queue"]
    reason: str


class SimulateRequest(BaseModel):
    """Request body for POST /simulate."""

    policy: dict[str, Any] = Field(
        ...,
        description="Full PolicyConfig as a dict (same shape as PolicyResponse minus policy_hash/version).",
    )
    steps: list[SimulationStep] = Field(
        default_factory=list,
        max_length=1000,
        description="Ordered scenario steps to simulate.",
    )


class SimulateResponse(BaseModel):
    """Result of a simulation run."""

    policy_hash: str
    total_steps: int
    steps_allowed: int
    steps_halted: int
    total_cost_usd: float
    final_decision: Literal["allow", "halt", "degrade", "queue"]
    step_results: list[SimulationStepResult]
    store_unchanged: bool = True
