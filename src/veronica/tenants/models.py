from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class TenantNode:
    """A node in the tenant hierarchy (org -> team -> chain).

    Not frozen -- registry mutates fields via update().
    """

    id: str
    name: str
    parent_id: str | None = None
    policy_overrides: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
