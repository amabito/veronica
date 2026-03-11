# src/veronica/api/policy_registry.py
"""Thread-safe in-memory registry of active PolicyConfig objects.

Wraps PolicyDistributor to provide versioned CRUD operations on policies.
The registry is mounted on app.state.registry at startup.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import TYPE_CHECKING

from veronica.distribution.policy_distributor import PolicyBundle, PolicyDistributor
from veronica.types import PolicyConfig

if TYPE_CHECKING:
    pass

_NOT_SET = object()


class PolicyRegistry:
    """CRUD layer over PolicyDistributor.

    - Stores the latest PolicyBundle per chain_id.
    - Exposes paginated list, single-item lookup, and update operations.
    - Thread-safe: all mutations hold _lock.
    """

    def __init__(self, distributor: PolicyDistributor) -> None:
        self._distributor = distributor
        self._lock = threading.Lock()
        # chain_id -> PolicyBundle
        self._store: dict[str, PolicyBundle] = {}

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def list_policies(
        self, page: int = 1, per_page: int = 20
    ) -> tuple[list[PolicyBundle], int]:
        """Return a paginated snapshot of all stored policies.

        Returns (page_items, total_count).
        """
        with self._lock:
            all_bundles = list(self._store.values())

        total = len(all_bundles)
        start = (page - 1) * per_page
        end = start + per_page
        return all_bundles[start:end], total

    def get_policy(self, chain_id: str) -> PolicyBundle | None:
        """Return the PolicyBundle for chain_id, or None if not found."""
        with self._lock:
            return self._store.get(chain_id)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def register(self, policy: PolicyConfig) -> PolicyBundle:
        """Validate, hash, and store a new policy.

        Raises PolicyValidationError on invalid policy.
        """
        bundle = self._distributor.distribute(policy)
        with self._lock:
            self._store[policy.chain_id] = bundle
        return bundle

    def update(
        self,
        chain_id: str,
        current_version: int,
        **fields: object,
    ) -> PolicyBundle:
        """Apply field updates to an existing policy.

        Parameters
        ----------
        chain_id        : policy to update (must exist, else KeyError)
        current_version : optimistic concurrency token; raises ValueError if stale
        **fields        : non-None overrides for PolicyConfig fields

        Raises
        ------
        KeyError            : policy not found
        ValueError          : stale version (409 in HTTP layer)
        PolicyValidationError : updated policy fails validation
        """
        with self._lock:
            existing = self._store.get(chain_id)
            if existing is None:
                raise KeyError(chain_id)

            if existing.version != current_version:
                raise ValueError(
                    f"stale version: client has {current_version}, server has {existing.version}"
                )

            # Build updated PolicyConfig (frozen dataclass)
            update_kwargs = {k: v for k, v in fields.items() if v is not None}
            update_kwargs["issued_at"] = time.time()
            updated_policy = replace(existing.policy, **update_kwargs)

        # Validate + hash outside the lock (CPU work)
        new_bundle = self._distributor.distribute(updated_policy)

        with self._lock:
            # Re-check version after re-acquiring lock (TOCTOU guard)
            current = self._store.get(chain_id)
            if current is None or current.version != current_version:
                raise ValueError("stale version (concurrent update detected)")
            self._store[chain_id] = new_bundle

        return new_bundle
