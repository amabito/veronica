# src/veronica/redis_arbiter.py
"""VERONICA OS arbiter -- RedisArbiter for multi-process budget allocation."""

from __future__ import annotations

import json
import logging
import math
import time
from contextvars import ContextVar
from typing import Mapping, Sequence

from veronica._redis_scripts import LUA_RESERVE, LUA_SETTLE
from veronica.proportional_arbiter import ProportionalArbiter
from veronica.types import DesiredPolicy, PolicyConfig

logger = logging.getLogger(__name__)

_MICRO = 1_000_000

_ctx_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_ctx_step_id: ContextVar[str | None] = ContextVar("step_id", default=None)


def _to_micro(usd: float) -> int:
    """Convert USD float to integer microusd (1 USD = 1,000,000)."""
    return round(usd * _MICRO)


def _from_micro(micro: int) -> float:
    """Convert integer microusd to USD float."""
    return micro / _MICRO


class RedisArbiter:
    """Multi-process budget arbiter using Redis for shared state.

    Uses Lua scripts for atomic reserve/settle operations.
    Falls back to ProportionalArbiter on Redis failure.

    Context injection via set_arbitration_context() uses contextvars,
    safe for concurrent async/threaded callers.

    Contract: Unsettled reservations are a budget leak. TTL expiry is
    a safety valve, not a refund mechanism. Call settle() promptly.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        budget_scope: str = "default",
        total_budget_usd: float = 100.0,
        reservation_ttl_s: int = 300,
        fallback: ProportionalArbiter | None = None,
    ) -> None:
        try:
            import redis as redis_lib
        except ImportError as e:
            raise ImportError(
                "redis package required for RedisArbiter. "
                "Install with: pip install veronica-cp[redis]"
            ) from e

        self._redis = redis_lib.Redis.from_url(redis_url, decode_responses=True)
        self._redis_lib = redis_lib
        self._scope = budget_scope
        self._total_budget_micro = _to_micro(total_budget_usd)
        self._ttl = reservation_ttl_s
        self._fallback = fallback or ProportionalArbiter()

        self._reserve_script = self._redis.register_script(LUA_RESERVE)
        self._settle_script = self._redis.register_script(LUA_SETTLE)

    def set_arbitration_context(
        self,
        request_id: str,
        step_id: str,
    ) -> None:
        """Inject request/step identity for idempotent reservation."""
        _ctx_request_id.set(request_id)
        _ctx_step_id.set(step_id)

    def arbitrate(
        self,
        desires: Sequence[DesiredPolicy],
        budget_remaining_usd: float,
    ) -> Mapping[str, PolicyConfig]:
        """Reserve budget via Redis. Falls back to local arbiter on failure."""
        request_id = _ctx_request_id.get()
        step_id = _ctx_step_id.get()

        if request_id is None or step_id is None:
            return self._fallback.arbitrate(desires, budget_remaining_usd)

        try:
            return self._arbitrate_redis(
                desires,
                budget_remaining_usd,
                request_id,
                step_id,
            )
        except self._redis_lib.RedisError:
            logger.warning("Redis unavailable, falling back to local arbiter")
            return self._fallback.arbitrate(desires, budget_remaining_usd)

    def settle(
        self,
        request_id: str,
        step_id: str,
        actual_cost_usd: float,
    ) -> None:
        """Settle reservation: refund surplus or deduct extra."""

        remaining_key = f"veronica:{self._scope}:budget:remaining"
        alloc_key = f"veronica:{self._scope}:alloc:{request_id}:{step_id}"
        if not math.isfinite(actual_cost_usd):
            actual_cost_usd = 0.0
        actual_micro = _to_micro(actual_cost_usd)

        try:
            self._settle_script(
                keys=[remaining_key, alloc_key],
                args=[str(actual_micro)],
            )
        except self._redis_lib.RedisError:
            logger.warning(
                "Redis settle failed for %s:%s",
                request_id,
                step_id,
            )

    def _arbitrate_redis(
        self,
        desires: Sequence[DesiredPolicy],
        budget_remaining_usd: float,
        request_id: str,
        step_id: str,
    ) -> Mapping[str, PolicyConfig]:
        """Perform atomic budget reservation via Redis Lua script.

        Note: only desires[0] is processed. Multi-chain arbitration (len(desires) > 1)
        is not supported in the Redis path; callers must issue one desire at a time.
        Additional desires beyond the first are silently ignored.
        """
        if not desires:
            return {}

        if len(desires) > 1:
            logger.warning(
                "RedisArbiter only processes desires[0]; %d additional desires "
                "were silently dropped. Issue one desire at a time.",
                len(desires) - 1,
            )

        desire = desires[0]
        reserve_micro = _to_micro(desire.ceiling_usd)

        remaining_key = f"veronica:{self._scope}:budget:remaining"
        alloc_key = f"veronica:{self._scope}:alloc:{request_id}:{step_id}"

        alloc_json = json.dumps(
            {
                "status": "reserved",
                "chain_id": desire.chain_id,
                "total_reserved_micro": reserve_micro,
                "ceiling_usd": desire.ceiling_usd,
                "reserved_at": time.time(),
            }
        )

        result = self._reserve_script(
            keys=[remaining_key, alloc_key],
            args=[
                alloc_json,
                str(reserve_micro),
                str(self._ttl),
                str(self._total_budget_micro),
            ],
        )

        parsed = json.loads(result)

        if parsed["status"] == "insufficient":
            ceiling = _from_micro(int(parsed["remaining"]))
        else:
            ceiling = desire.ceiling_usd

        now = time.time()
        return {
            desire.chain_id: PolicyConfig(
                chain_id=desire.chain_id,
                ceiling_usd=ceiling,
                ceiling_steps=desire.ceiling_steps,
                ceiling_tokens_out=desire.ceiling_tokens_out,
                on_exceed=desire.on_exceed,
                fallback_model=desire.fallback_model,
                timeout_ms=desire.timeout_ms,
                priority=desire.priority,
                issued_at=now,
                planner_version="0.3.0",
            ),
        }
