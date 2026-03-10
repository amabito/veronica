from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field

from veronica.tenants.models import TenantNode
from veronica.tenants.resolver import PolicyResolver
from veronica.types import PolicyConfig

router = APIRouter(prefix="/tenants", tags=["tenants"])

_resolver = PolicyResolver()

_SAFE_ID_RE = r"^[a-zA-Z0-9_-]+$"
SafeTenantId = Annotated[str, Path(min_length=1, max_length=128, pattern=_SAFE_ID_RE)]

_KNOWN_OVERRIDE_KEYS = {
    "ceiling_usd",
    "on_exceed",
    "ceiling_tokens_out",
    "ceiling_steps",
    "fallback_model",
    "timeout_ms",
    "rate_window_seconds",
    "rate_ceiling_calls",
    "priority",
    "deadline_ts",
    "expires_at",
    "planner_version",
}


def _validate_overrides(overrides: dict) -> None:
    """Validate policy_overrides keys against the known field set.

    Raises HTTPException(422) for unknown keys or if the key count exceeds 20.
    """
    unknown = set(overrides.keys()) - _KNOWN_OVERRIDE_KEYS
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown policy_overrides keys: {sorted(unknown)}",
        )
    if len(overrides) > 20:
        raise HTTPException(
            status_code=422,
            detail="policy_overrides limited to 20 keys",
        )


# ---------------------------------------------------------------------------
# Request / response schemas
# ---------------------------------------------------------------------------


class TenantCreateRequest(BaseModel):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=256)
    parent_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    policy_overrides: dict[str, Any] = {}


class TenantUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    policy_overrides: dict[str, Any] | None = None


class TenantResponse(BaseModel):
    id: str
    name: str
    parent_id: str | None
    policy_overrides: dict[str, Any]
    created_at: float


class EffectivePolicyResponse(BaseModel):
    tenant_id: str
    chain_id: str
    ceiling_usd: float
    on_exceed: str
    issued_at: float
    ceiling_tokens_out: int | None
    ceiling_steps: int | None
    fallback_model: str | None
    timeout_ms: int | None
    rate_window_seconds: float | None
    rate_ceiling_calls: int | None
    priority: int
    deadline_ts: float | None
    expires_at: float | None
    planner_version: str | None


def _to_response(node: TenantNode) -> TenantResponse:
    return TenantResponse(
        id=node.id,
        name=node.name,
        parent_id=node.parent_id,
        policy_overrides=node.policy_overrides,
        created_at=node.created_at,
    )


def _policy_to_response(tenant_id: str, policy: PolicyConfig) -> EffectivePolicyResponse:
    return EffectivePolicyResponse(
        tenant_id=tenant_id,
        chain_id=policy.chain_id,
        ceiling_usd=policy.ceiling_usd,
        on_exceed=policy.on_exceed,
        issued_at=policy.issued_at,
        ceiling_tokens_out=policy.ceiling_tokens_out,
        ceiling_steps=policy.ceiling_steps,
        fallback_model=policy.fallback_model,
        timeout_ms=policy.timeout_ms,
        rate_window_seconds=policy.rate_window_seconds,
        rate_ceiling_calls=policy.rate_ceiling_calls,
        priority=policy.priority,
        deadline_ts=policy.deadline_ts,
        expires_at=policy.expires_at,
        planner_version=policy.planner_version,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[TenantResponse], summary="List all tenants")
async def list_tenants(request: Request) -> list[TenantResponse]:
    registry = request.app.state.tenant_registry
    return [_to_response(t) for t in registry.list_all()]


@router.get("/{tenant_id}", response_model=TenantResponse, summary="Get tenant by ID")
async def get_tenant(tenant_id: SafeTenantId, request: Request) -> TenantResponse:
    registry = request.app.state.tenant_registry
    node = registry.get(tenant_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    return _to_response(node)


@router.post("", response_model=TenantResponse, status_code=201, summary="Create tenant")
async def create_tenant(body: TenantCreateRequest, request: Request) -> TenantResponse:
    _validate_overrides(body.policy_overrides)
    registry = request.app.state.tenant_registry
    node = TenantNode(
        id=body.id,
        name=body.name,
        parent_id=body.parent_id,
        policy_overrides=dict(body.policy_overrides),
    )
    try:
        registry.register(node)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _to_response(node)


@router.put("/{tenant_id}", response_model=TenantResponse, summary="Update tenant")
async def update_tenant(
    tenant_id: SafeTenantId, body: TenantUpdateRequest, request: Request
) -> TenantResponse:
    if body.policy_overrides is not None:
        _validate_overrides(body.policy_overrides)
    registry = request.app.state.tenant_registry
    updates: dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.policy_overrides is not None:
        updates["policy_overrides"] = body.policy_overrides
    try:
        node = registry.update(tenant_id, updates)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    return _to_response(node)


@router.delete("/{tenant_id}", status_code=204, summary="Delete tenant")
async def delete_tenant(tenant_id: SafeTenantId, request: Request) -> Response:
    registry = request.app.state.tenant_registry
    try:
        registry.delete(tenant_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return Response(status_code=204)


@router.get(
    "/{tenant_id}/effective-policy",
    response_model=EffectivePolicyResponse,
    summary="Get effective policy for tenant (with inheritance)",
)
async def get_effective_policy(tenant_id: SafeTenantId, request: Request) -> EffectivePolicyResponse:
    registry = request.app.state.tenant_registry
    node = registry.get(tenant_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found")
    base_policy = PolicyConfig(
        chain_id=tenant_id,
        ceiling_usd=10.0,
        on_exceed="halt",
        issued_at=time.time(),
    )
    effective = _resolver.resolve(tenant_id, registry, base_policy)
    return _policy_to_response(tenant_id, effective)
