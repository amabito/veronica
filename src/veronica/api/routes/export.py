# src/veronica/api/routes/export.py
"""GET /export endpoint -- full JSON dump of policies and recent events."""

from __future__ import annotations

import heapq
import logging
import time
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["export"])

_DEFAULT_EVENT_LIMIT = 10_000
_MAX_EVENT_LIMIT = 100_000
_POLICY_PAGE_SIZE = 1_000
_MAX_POLICY_PAGES = 1_000  # circuit breaker: 1M policies max


@router.get("/export", summary="Export policies and events as JSON")
async def export_data(
    request: Request,
    event_limit: int = Query(
        default=_DEFAULT_EVENT_LIMIT,
        ge=1,
        le=_MAX_EVENT_LIMIT,
        description="Maximum number of recent events to include",
    ),
) -> JSONResponse:
    """Return a full JSON snapshot of all policies and recent events.

    Suitable for backup, migration, and integrity verification.
    Each policy entry includes policy_hash for content verification.

    Response shape::

        {
          "exported_at": 1234567890.123,
          "veronica_version": "<current version>",
          "policies": [ { ...PolicyResponse fields... } ],
          "events": [ { ...EventItem fields... } ],
          "warnings": []
        }
    """
    warnings: list[str] = []
    registry = request.app.state.registry

    # Collect all policies with pagination to avoid silent truncation
    policies: list[dict[str, Any]] = []
    all_bundles: list[Any] = []
    page = 1
    while page <= _MAX_POLICY_PAGES:
        chunk, total = registry.list_policies(page=page, per_page=_POLICY_PAGE_SIZE)
        all_bundles.extend(chunk)
        if len(all_bundles) >= total or not chunk:
            break
        page += 1
    for bundle in all_bundles:
        p = bundle.policy
        policies.append(
            {
                "chain_id": p.chain_id,
                "ceiling_usd": p.ceiling_usd,
                "on_exceed": p.on_exceed,
                "issued_at": p.issued_at,
                "ceiling_tokens_out": p.ceiling_tokens_out,
                "ceiling_steps": p.ceiling_steps,
                "fallback_model": p.fallback_model,
                "timeout_ms": p.timeout_ms,
                "rate_window_seconds": p.rate_window_seconds,
                "rate_ceiling_calls": p.rate_ceiling_calls,
                "priority": p.priority,
                "deadline_ts": p.deadline_ts,
                "expires_at": p.expires_at,
                "planner_version": p.planner_version,
                "policy_hash": bundle.policy_hash,
                "version": bundle.version,
            }
        )

    # Collect recent events from EventIngestor store (CPStepOutcomeStore)
    events: list[dict[str, Any]] = []
    try:
        ingestor = request.app.state.ingestor
        all_outcomes = ingestor.store.snapshot()
        outcomes = heapq.nlargest(event_limit, all_outcomes, key=lambda o: o.timestamp)

        for outcome in outcomes:
            events.append(
                {
                    "step_id": outcome.step_id,
                    "chain_id": outcome.chain_id,
                    "operation_name": outcome.operation_name,
                    "decision": outcome.decision,
                    "cost_usd": outcome.cost_usd,
                    "tokens": outcome.tokens,
                    "duration_ms": outcome.duration_ms,
                    "policy_hash": outcome.policy_hash,
                    "audit_id": outcome.audit_id,
                    "timestamp": outcome.timestamp,
                }
            )
    except Exception as exc:
        logger.warning("Event collection failed during export: %s", exc)
        warnings.append("Event collection failed; events list may be incomplete")
        events = []

    try:
        from veronica import __version__ as _version
    except Exception:
        _version = "unknown"

    payload: dict[str, Any] = {
        "exported_at": time.time(),
        "veronica_version": _version,
        "policies": policies,
        "events": events,
    }
    if warnings:
        payload["warnings"] = warnings
    return JSONResponse(content=payload)
