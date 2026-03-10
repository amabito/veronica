# src/veronica/distribution/policy_distributor.py
"""Policy bundle distribution -- converts PolicyConfig to ExecutionConfig.

Thread-safe. Each PolicyDistributor instance maintains an internal version
counter. The resulting PolicyBundle captures policy_hash (SHA-256) for audit.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Sequence

from veronica_core.containment.execution_context import ExecutionConfig

from veronica.policy_audit import compute_policy_hash
from veronica.types import PolicyConfig


class PolicyValidationError(ValueError):
    """Raised when a PolicyConfig fails pre-distribution validation."""


@dataclass(frozen=True)
class PolicyBundle:
    """Output of PolicyDistributor.distribute().

    Carries the original policy, the derived kernel config, a
    content-addressed hash, and a monotonic version number.
    """

    policy: PolicyConfig
    exec_config: ExecutionConfig
    policy_hash: str
    distributed_at: float
    version: int


_ALLOWED_ON_EXCEED = frozenset({"halt", "degrade", "queue"})


class PolicyDistributor:
    """Converts PolicyConfig (control-plane) to ExecutionConfig (kernel).

    Thread-safe: ``distribute()`` and ``distribute_many()`` may be called
    from multiple threads concurrently.

    Args:
        strict: When True, ``distribute()`` raises ``PolicyValidationError``
            if ``ceiling_steps``, ``ceiling_tokens_out``, or ``timeout_ms``
            are ``None``.
    """

    def __init__(self, strict: bool = False) -> None:
        self._strict = strict
        self._lock = threading.Lock()
        self._version = 0

    def _next_version(self) -> int:
        with self._lock:
            self._version += 1
            return self._version

    def _validate(self, policy: PolicyConfig) -> None:
        """Validate PolicyConfig fields. Raises PolicyValidationError on failure."""
        if not math.isfinite(policy.ceiling_usd):
            raise PolicyValidationError(
                f"ceiling_usd must be finite, got {policy.ceiling_usd!r}"
            )
        if policy.ceiling_usd < 0:
            raise PolicyValidationError(
                f"ceiling_usd must be >= 0, got {policy.ceiling_usd!r}"
            )
        if policy.on_exceed not in _ALLOWED_ON_EXCEED:
            raise PolicyValidationError(
                f"on_exceed must be one of {sorted(_ALLOWED_ON_EXCEED)}, "
                f"got {policy.on_exceed!r}"
            )
        if policy.ceiling_steps is not None and policy.ceiling_steps < 0:
            raise PolicyValidationError(
                f"ceiling_steps must be >= 0, got {policy.ceiling_steps!r}"
            )
        if policy.ceiling_tokens_out is not None and not math.isfinite(policy.ceiling_tokens_out):
            raise PolicyValidationError(
                f"ceiling_tokens_out must be finite, got {policy.ceiling_tokens_out!r}"
            )
        if policy.ceiling_tokens_out is not None and policy.ceiling_tokens_out < 0:
            raise PolicyValidationError(
                f"ceiling_tokens_out must be >= 0, got {policy.ceiling_tokens_out!r}"
            )
        if policy.timeout_ms is not None and not math.isfinite(policy.timeout_ms):
            raise PolicyValidationError(
                f"timeout_ms must be finite, got {policy.timeout_ms!r}"
            )
        if policy.timeout_ms is not None and policy.timeout_ms < 0:
            raise PolicyValidationError(
                f"timeout_ms must be >= 0, got {policy.timeout_ms!r}"
            )
        if not isinstance(policy.priority, int) or isinstance(policy.priority, bool):
            raise PolicyValidationError(
                f"priority must be an int, got {type(policy.priority).__name__!r}"
            )
        if self._strict:
            missing = [
                field
                for field in ("ceiling_steps", "ceiling_tokens_out", "timeout_ms")
                if getattr(policy, field) is None
            ]
            if missing:
                raise PolicyValidationError(
                    f"strict mode: the following fields must not be None: "
                    f"{missing}"
                )

    def distribute(self, policy: PolicyConfig) -> PolicyBundle:
        """Validate, hash, and convert a single PolicyConfig.

        Args:
            policy: The policy produced by the control plane.

        Returns:
            PolicyBundle containing the original policy, the kernel-ready
            ExecutionConfig, the content-addressed hash, timestamp, and version.

        Raises:
            PolicyValidationError: If the policy fails validation.
        """
        self._validate(policy)
        policy_hash = compute_policy_hash(policy)
        exec_config = policy.to_exec_config()
        version = self._next_version()
        return PolicyBundle(
            policy=policy,
            exec_config=exec_config,
            policy_hash=policy_hash,
            distributed_at=time.time(),
            version=version,
        )

    def distribute_many(
        self, policies: Sequence[PolicyConfig]
    ) -> list[PolicyBundle]:
        """Distribute multiple policies, collecting all validation errors.

        Args:
            policies: Sequence of PolicyConfig objects to distribute.

        Returns:
            List of PolicyBundle in the same order as input.

        Raises:
            PolicyValidationError: If one or more policies fail validation.
                The message lists all failures.
        """
        errors: list[str] = []
        results: list[PolicyBundle | None] = []

        for i, policy in enumerate(policies):
            try:
                results.append(self.distribute(policy))
            except PolicyValidationError as exc:
                errors.append(f"policy[{i}] (chain_id={policy.chain_id!r}): {exc}")
                results.append(None)

        if errors:
            raise PolicyValidationError(
                f"{len(errors)} policy validation error(s):\n"
                + "\n".join(f"  - {e}" for e in errors)
            )

        return [r for r in results if r is not None]
