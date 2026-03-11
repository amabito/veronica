# src/veronica/api/routes/policies.py
"""Policy CRUD endpoints: GET /policies, GET /policies/{id}, PUT /policies/{id}."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request

from veronica.api.schemas import (
    PolicyListResponse,
    PolicyResponse,
    PolicySummary,
    PolicyUpdateRequest,
)
from veronica.distribution.policy_distributor import PolicyBundle, PolicyValidationError

router = APIRouter(prefix="/policies", tags=["policies"])


def _bundle_to_response(bundle: PolicyBundle) -> PolicyResponse:
    """Convert a PolicyBundle to the full PolicyResponse wire model."""
    p = bundle.policy
    return PolicyResponse(
        chain_id=p.chain_id,
        ceiling_usd=p.ceiling_usd,
        on_exceed=p.on_exceed,
        issued_at=p.issued_at,
        ceiling_tokens_out=p.ceiling_tokens_out,
        ceiling_steps=p.ceiling_steps,
        fallback_model=p.fallback_model,
        timeout_ms=p.timeout_ms,
        rate_window_seconds=p.rate_window_seconds,
        rate_ceiling_calls=p.rate_ceiling_calls,
        priority=p.priority,
        deadline_ts=p.deadline_ts,
        expires_at=p.expires_at,
        planner_version=p.planner_version,
        policy_hash=bundle.policy_hash,
        version=bundle.version,
    )


def _bundle_to_summary(bundle: PolicyBundle) -> PolicySummary:
    """Convert a PolicyBundle to the lightweight PolicySummary."""
    p = bundle.policy
    return PolicySummary(
        chain_id=p.chain_id,
        on_exceed=p.on_exceed,
        ceiling_usd=p.ceiling_usd,
        priority=p.priority,
        issued_at=p.issued_at,
        policy_hash=bundle.policy_hash,
        version=bundle.version,
    )


@router.get("", response_model=PolicyListResponse, summary="List policies")
async def list_policies(
    request: Request,
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(default=20, ge=1, le=200, description="Items per page"),
) -> PolicyListResponse:
    """Return a paginated list of all registered policies."""
    registry = request.app.state.registry
    items, total = registry.list_policies(page=page, per_page=per_page)
    return PolicyListResponse(
        items=[_bundle_to_summary(b) for b in items],
        total=total,
        page=page,
        per_page=per_page,
    )


_CHAIN_ID_RE = r"^[a-zA-Z0-9_:@.\-]+$"
SafeChainId = Annotated[str, Path(min_length=1, max_length=256, pattern=_CHAIN_ID_RE)]


@router.get("/{chain_id}", response_model=PolicyResponse, summary="Get policy by ID")
async def get_policy(chain_id: SafeChainId, request: Request) -> PolicyResponse:
    """Return the full PolicyConfig for a given chain_id.

    Returns 404 if the policy does not exist.
    """
    registry = request.app.state.registry
    bundle = registry.get_policy(chain_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail=f"Policy '{chain_id}' not found")
    return _bundle_to_response(bundle)


def _is_immutable_config() -> bool:
    """Return True if VERONICA_IMMUTABLE_CONFIG env var is set to a truthy value."""
    import os

    return os.environ.get("VERONICA_IMMUTABLE_CONFIG", "").lower() in (
        "1",
        "true",
        "yes",
    )


@router.put("/{chain_id}", response_model=PolicyResponse, summary="Update policy")
async def update_policy(
    chain_id: SafeChainId,
    body: PolicyUpdateRequest,
    request: Request,
) -> PolicyResponse:
    """Update an existing policy.

    Requires ``current_version`` for optimistic concurrency control.

    - **403** if immutable config mode is enabled (VERONICA_IMMUTABLE_CONFIG=1).
    - **404** if the policy does not exist.
    - **409** if ``current_version`` does not match the server's version (stale).
    - **422** if the updated policy fails validation (e.g. negative ceiling_usd).
    """
    if _is_immutable_config():
        raise HTTPException(
            status_code=403,
            detail="Policy updates disabled: immutable config mode",
        )

    registry = request.app.state.registry

    # Build the field overrides dict (exclude None and the version sentinel)
    update_fields = body.model_dump(
        exclude={"current_version"},
        exclude_none=True,
    )

    try:
        bundle = registry.update(
            chain_id=chain_id,
            current_version=body.current_version,
            **update_fields,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Policy '{chain_id}' not found")
    except PolicyValidationError:
        # Must be caught before ValueError (PolicyValidationError is a subclass)
        # Use generic message to avoid leaking internal validation details
        raise HTTPException(status_code=422, detail="Policy validation failed")
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=409, detail="Version conflict or invalid update fields"
        )

    return _bundle_to_response(bundle)
