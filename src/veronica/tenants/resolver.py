from __future__ import annotations

import dataclasses

import math

from veronica.tenants.registry import TenantRegistry
from veronica.types import PolicyConfig

# Fields that can be overridden via policy_overrides
_POLICY_FIELDS = {f.name for f in dataclasses.fields(PolicyConfig)}

# Allowed values for on_exceed (must match PolicyDistributor._ALLOWED_ON_EXCEED)
_ON_EXCEED_VALUES = {"halt", "degrade", "queue"}


def _validate_override_value(key: str, value: object) -> None:
    """Validate a single policy_overrides entry.

    Raises ValueError with a descriptive message on invalid type or value.
    """
    if key == "ceiling_usd":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"policy_overrides[{key!r}] must be a number, got {type(value).__name__!r}"
            )
        if not math.isfinite(value) or value < 0:
            raise ValueError(
                f"policy_overrides[{key!r}] must be a finite non-negative number, got {value!r}"
            )

    elif key == "priority":
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(
                f"policy_overrides[{key!r}] must be an int, got {type(value).__name__!r}"
            )
        if not (0 <= value <= 100):
            raise ValueError(
                f"policy_overrides[{key!r}] must be in range 0-100, got {value!r}"
            )

    elif key == "timeout_ms":
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"policy_overrides[{key!r}] must be a number or None, got {type(value).__name__!r}"
                )
            if not math.isfinite(value) or value < 0:
                raise ValueError(
                    f"policy_overrides[{key!r}] must be a finite non-negative number, got {value!r}"
                )

    elif key in ("ceiling_tokens_out", "ceiling_steps", "rate_ceiling_calls"):
        if value is not None:
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(
                    f"policy_overrides[{key!r}] must be an int or None, got {type(value).__name__!r}"
                )
            if value < 0:
                raise ValueError(
                    f"policy_overrides[{key!r}] must be >= 0, got {value!r}"
                )

    elif key == "rate_window_seconds":
        if value is not None:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(
                    f"policy_overrides[{key!r}] must be a number or None, got {type(value).__name__!r}"
                )
            if not math.isfinite(value) or value < 0:
                raise ValueError(
                    f"policy_overrides[{key!r}] must be a finite non-negative number, got {value!r}"
                )

    elif key == "on_exceed":
        if not isinstance(value, str):
            raise ValueError(
                f"policy_overrides[{key!r}] must be a str, got {type(value).__name__!r}"
            )
        if value not in _ON_EXCEED_VALUES:
            raise ValueError(
                f"policy_overrides[{key!r}] must be one of {sorted(_ON_EXCEED_VALUES)!r}, got {value!r}"
            )

    elif key in ("fallback_model", "planner_version"):
        if value is not None and not isinstance(value, str):
            raise ValueError(
                f"policy_overrides[{key!r}] must be a str or None, got {type(value).__name__!r}"
            )
        if isinstance(value, str) and len(value) > 1024:
            raise ValueError(
                f"policy_overrides[{key!r}] must be <= 1024 chars, got {len(value)}"
            )

    elif key in ("deadline_ts", "expires_at", "issued_at"):
        if value is not None:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(
                    f"policy_overrides[{key!r}] must be a number or None, got {type(value).__name__!r}"
                )
            if not math.isfinite(value) or value < 0:
                raise ValueError(
                    f"policy_overrides[{key!r}] must be a finite non-negative number, got {value!r}"
                )

    else:
        raise ValueError(f"policy_overrides[{key!r}] has no validation rule")


class PolicyResolver:
    """Resolves effective policy for a tenant by walking the ancestor chain."""

    def resolve(
        self,
        tenant_id: str,
        registry: TenantRegistry,
        base_policy: PolicyConfig,
    ) -> PolicyConfig:
        """Return effective PolicyConfig for tenant_id.

        Walks the ancestor chain from root to the tenant itself, applying
        policy_overrides at each level (child wins over parent).
        Only fields that exist in PolicyConfig are applied.
        Each override value is validated before application.

        Returns base_policy unchanged if tenant_id is not found.
        Raises ValueError if any override value has an invalid type or value.
        """
        tenant = registry.get(tenant_id)
        if tenant is None:
            return dataclasses.replace(base_policy)

        # Build ordered list: root ancestors + self
        ancestors = registry.get_ancestors(tenant_id)
        chain = [*ancestors, tenant]

        policy = dataclasses.replace(base_policy)
        for node in chain:
            overrides: dict[str, object] = {}
            for k, v in node.policy_overrides.items():
                if k not in _POLICY_FIELDS:
                    continue
                _validate_override_value(k, v)
                overrides[k] = v
            if overrides:
                policy = dataclasses.replace(policy, **overrides)

        return policy
