from __future__ import annotations

import dataclasses

from veronica.tenants.registry import TenantRegistry
from veronica.types import PolicyConfig

# Fields that can be overridden via policy_overrides
_POLICY_FIELDS = {f.name for f in dataclasses.fields(PolicyConfig)}


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

        Returns base_policy unchanged if tenant_id is not found.
        """
        tenant = registry.get(tenant_id)
        if tenant is None:
            return base_policy

        # Build ordered list: root ancestors + self
        ancestors = registry.get_ancestors(tenant_id)
        chain = [*ancestors, tenant]

        policy = base_policy
        for node in chain:
            overrides = {
                k: v for k, v in node.policy_overrides.items() if k in _POLICY_FIELDS
            }
            if overrides:
                policy = dataclasses.replace(policy, **overrides)

        return policy
