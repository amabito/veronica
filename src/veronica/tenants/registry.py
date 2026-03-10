from __future__ import annotations

import threading

from veronica.tenants.models import TenantNode


class TenantRegistry:
    """Thread-safe in-memory registry for tenant nodes."""

    def __init__(self) -> None:
        self._tenants: dict[str, TenantNode] = {}
        self._lock = threading.Lock()

    def register(self, tenant: TenantNode) -> TenantNode:
        """Register a new tenant node.

        Raises ValueError if the id already exists or if parent_id is specified
        but does not exist.
        """
        with self._lock:
            if tenant.id in self._tenants:
                raise ValueError(f"Tenant '{tenant.id}' already exists")
            if tenant.parent_id is not None and tenant.parent_id not in self._tenants:
                raise ValueError(f"Parent tenant '{tenant.parent_id}' not found")
            self._tenants[tenant.id] = tenant
        return tenant

    def get(self, tenant_id: str) -> TenantNode | None:
        """Return the tenant node or None if not found."""
        with self._lock:
            return self._tenants.get(tenant_id)

    def list_all(self) -> list[TenantNode]:
        """Return all registered tenants (unordered)."""
        with self._lock:
            return list(self._tenants.values())

    def update(self, tenant_id: str, updates: dict) -> TenantNode:
        """Apply field updates to an existing tenant.

        Raises KeyError if tenant_id is not found.
        Only 'name' and 'policy_overrides' may be updated.
        """
        with self._lock:
            tenant = self._tenants.get(tenant_id)
            if tenant is None:
                raise KeyError(f"Tenant '{tenant_id}' not found")
            if "name" in updates:
                tenant.name = updates["name"]
            if "policy_overrides" in updates:
                tenant.policy_overrides = updates["policy_overrides"]
        return tenant

    def delete(self, tenant_id: str) -> None:
        """Delete a tenant.

        Raises KeyError if not found.
        Raises ValueError if the tenant has children.
        """
        with self._lock:
            if tenant_id not in self._tenants:
                raise KeyError(f"Tenant '{tenant_id}' not found")
            # Check for children without releasing lock
            children = [t for t in self._tenants.values() if t.parent_id == tenant_id]
            if children:
                child_ids = ", ".join(c.id for c in children)
                raise ValueError(
                    f"Cannot delete tenant '{tenant_id}': has children: {child_ids}"
                )
            del self._tenants[tenant_id]

    def get_children(self, tenant_id: str) -> list[TenantNode]:
        """Return direct children of the given tenant."""
        with self._lock:
            return [t for t in self._tenants.values() if t.parent_id == tenant_id]

    def get_ancestors(self, tenant_id: str) -> list[TenantNode]:
        """Return ancestors of the given tenant, root first (excludes self).

        Returns an empty list if the tenant has no parent or is not found.
        """
        with self._lock:
            ancestors: list[TenantNode] = []
            current = self._tenants.get(tenant_id)
            if current is None:
                return ancestors
            parent_id = current.parent_id
            while parent_id is not None:
                parent = self._tenants.get(parent_id)
                if parent is None:
                    break
                ancestors.append(parent)
                parent_id = parent.parent_id
            # ancestors is leaf->root order, reverse to root->leaf
            ancestors.reverse()
        return ancestors
