from __future__ import annotations

from copy import deepcopy
import threading

from veronica.tenants.models import TenantNode

MAX_DEPTH = 100


class TenantRegistry:
    """Thread-safe in-memory registry for tenant nodes."""

    def __init__(self) -> None:
        self._tenants: dict[str, TenantNode] = {}
        self._lock = threading.Lock()

    def _walk_ancestors_unsafe(self, start_id: str) -> list[str]:
        """Walk ancestor IDs from start_id upward. Caller must hold self._lock.

        Raises RuntimeError if a circular reference is detected (depth > MAX_DEPTH).
        """
        visited: list[str] = []
        current_id: str | None = start_id
        depth = 0
        while current_id is not None:
            if depth >= MAX_DEPTH:
                raise RuntimeError(
                    f"Circular parent reference detected while walking ancestors of '{start_id}'"
                )
            node = self._tenants.get(current_id)
            if node is None:
                break
            visited.append(current_id)
            current_id = node.parent_id
            depth += 1
        return visited

    def register(self, tenant: TenantNode) -> TenantNode:
        """Register a new tenant node.

        Raises ValueError if the id already exists or if parent_id is specified
        but does not exist.
        Raises RuntimeError if registering the tenant would create a circular
        parent reference.
        """
        with self._lock:
            if tenant.id in self._tenants:
                raise ValueError(f"Tenant '{tenant.id}' already exists")
            if tenant.parent_id is not None and tenant.parent_id not in self._tenants:
                raise ValueError(f"Parent tenant '{tenant.parent_id}' not found")
            # Validate no circular reference: walk the parent chain before inserting
            if tenant.parent_id is not None:
                self._walk_ancestors_unsafe(tenant.parent_id)
            self._tenants[tenant.id] = deepcopy(tenant)
        return deepcopy(tenant)

    def get(self, tenant_id: str) -> TenantNode | None:
        """Return a copy of the tenant node or None if not found."""
        with self._lock:
            node = self._tenants.get(tenant_id)
            return deepcopy(node) if node is not None else None

    def list_all(self) -> list[TenantNode]:
        """Return copies of all registered tenants (unordered)."""
        with self._lock:
            return [deepcopy(t) for t in self._tenants.values()]

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
                if not isinstance(updates["name"], str):
                    raise TypeError("name must be a str")
                tenant.name = updates["name"]
            if "policy_overrides" in updates:
                if not isinstance(updates["policy_overrides"], dict):
                    raise TypeError("policy_overrides must be a dict")
                tenant.policy_overrides = deepcopy(updates["policy_overrides"])
            return deepcopy(tenant)

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
                raise ValueError(
                    f"Cannot delete tenant '{tenant_id}': has {len(children)} child tenant(s)"
                )
            del self._tenants[tenant_id]

    def get_children(self, tenant_id: str) -> list[TenantNode]:
        """Return copies of direct children of the given tenant."""
        with self._lock:
            return [
                deepcopy(t) for t in self._tenants.values() if t.parent_id == tenant_id
            ]

    def get_ancestors(self, tenant_id: str) -> list[TenantNode]:
        """Return ancestors of the given tenant, root first (excludes self).

        Returns an empty list if the tenant has no parent or is not found.
        Raises RuntimeError if a circular parent reference is detected.
        """
        with self._lock:
            ancestors: list[TenantNode] = []
            current = self._tenants.get(tenant_id)
            if current is None:
                return ancestors
            parent_id = current.parent_id
            depth = 0
            while parent_id is not None:
                if depth >= MAX_DEPTH:
                    raise RuntimeError(
                        f"Circular parent reference detected while resolving ancestors of '{tenant_id}'"
                    )
                parent = self._tenants.get(parent_id)
                if parent is None:
                    break
                ancestors.append(deepcopy(parent))
                parent_id = parent.parent_id
                depth += 1
            # ancestors is leaf->root order, reverse to root->leaf
            ancestors.reverse()
        return ancestors
