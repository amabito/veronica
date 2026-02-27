# tests/test_redis_arbiter.py
"""Tests for RedisArbiter -- multi-process budget allocation."""
from __future__ import annotations

import time

import fakeredis
import pytest

from veronica.redis_arbiter import RedisArbiter, _from_micro, _to_micro
from veronica.types import DesiredPolicy


def _desire(
    chain_id: str = "c1",
    ceiling_usd: float = 10.0,
    priority: int = 50,
) -> DesiredPolicy:
    return DesiredPolicy(
        chain_id=chain_id,
        ceiling_usd=ceiling_usd,
        ceiling_steps=100,
        ceiling_tokens_out=50_000,
        on_exceed="halt",
        fallback_model=None,
        timeout_ms=30_000,
        priority=priority,
    )


@pytest.fixture()
def arbiter():
    """RedisArbiter backed by fakeredis."""
    from veronica.redis_arbiter import _ctx_request_id, _ctx_step_id

    # Reset contextvars to ensure test isolation
    _ctx_request_id.set(None)
    _ctx_step_id.set(None)

    fake_redis = fakeredis.FakeRedis(decode_responses=True)
    arb = RedisArbiter.__new__(RedisArbiter)
    import redis as redis_lib
    arb._redis = fake_redis
    arb._redis_lib = redis_lib
    arb._scope = "test"
    arb._total_budget_micro = _to_micro(100.0)
    arb._ttl = 300
    from veronica.proportional_arbiter import ProportionalArbiter
    arb._fallback = ProportionalArbiter()
    from veronica._redis_scripts import LUA_RESERVE, LUA_SETTLE
    arb._reserve_script = fake_redis.register_script(LUA_RESERVE)
    arb._settle_script = fake_redis.register_script(LUA_SETTLE)
    return arb


class TestMicrousd:
    def test_to_micro(self) -> None:
        assert _to_micro(0.01) == 10_000

    def test_from_micro(self) -> None:
        assert _from_micro(10_000) == 0.01

    def test_roundtrip(self) -> None:
        for usd in [0.0, 0.01, 1.0, 99.999999, 100.0]:
            assert abs(_from_micro(_to_micro(usd)) - usd) < 1e-6


class TestReserve:
    def test_reserve_basic(self, arbiter: RedisArbiter) -> None:
        """Reserve 10 USD from 100 USD budget."""
        arbiter.set_arbitration_context("r1", "s1")
        configs = arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)
        assert "c1" in configs
        assert configs["c1"].ceiling_usd == 10.0

        # Check Redis remaining: 100 - 10 = 90 USD
        remaining = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining == _to_micro(90.0)

    def test_reserve_idempotent(self, arbiter: RedisArbiter) -> None:
        """Same request_id + step_id called twice -> budget deducted once."""
        arbiter.set_arbitration_context("r1", "s1")
        arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)

        arbiter.set_arbitration_context("r1", "s1")
        arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)

        remaining = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining == _to_micro(90.0)  # deducted once, not twice

    def test_reserve_insufficient(self, arbiter: RedisArbiter) -> None:
        """Reserve more than available -> returns remaining amount."""
        arbiter.set_arbitration_context("r1", "s1")
        configs = arbiter.arbitrate([_desire(ceiling_usd=200.0)], 100.0)
        assert configs["c1"].ceiling_usd == 100.0  # gets remaining

    def test_fallback_no_context(self, arbiter: RedisArbiter) -> None:
        """arbitrate() without set_arbitration_context() uses fallback."""
        # Don't call set_arbitration_context
        configs = arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)
        assert "c1" in configs
        # Redis should not have been touched
        assert arbiter._redis.get("veronica:test:budget:remaining") is None


class TestSettle:
    def test_settle_refund(self, arbiter: RedisArbiter) -> None:
        """Reserve 10, settle 7 -> remaining increases by 3."""
        arbiter.set_arbitration_context("r1", "s1")
        arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)

        arbiter.settle("r1", "s1", actual_cost_usd=7.0)

        remaining = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining == _to_micro(93.0)  # 100 - 10 + 3 = 93

    def test_settle_extra_deduction(self, arbiter: RedisArbiter) -> None:
        """Reserve 5, settle 8 -> remaining decreases by 3."""
        arbiter.set_arbitration_context("r1", "s1")
        arbiter.arbitrate([_desire(ceiling_usd=5.0)], 100.0)

        arbiter.settle("r1", "s1", actual_cost_usd=8.0)

        remaining = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining == _to_micro(92.0)  # 100 - 5 - 3 = 92

    def test_settle_expired(self, arbiter: RedisArbiter) -> None:
        """TTL-expired reservation: settle deducts actual cost."""
        arbiter._ttl = 1  # 1 second TTL
        arbiter.set_arbitration_context("r1", "s1")
        arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)

        # remaining = 90 after reserve
        # Simulate TTL expiry by deleting the alloc key
        arbiter._redis.delete("veronica:test:alloc:r1:s1")

        arbiter.settle("r1", "s1", actual_cost_usd=7.0)

        # Expired path: deducts actual (7) from remaining (90) = 83
        remaining = int(arbiter._redis.get("veronica:test:budget:remaining"))
        assert remaining == _to_micro(83.0)

    def test_settle_alloc_key_cleaned(self, arbiter: RedisArbiter) -> None:
        """After settle, alloc key is deleted."""
        arbiter.set_arbitration_context("r1", "s1")
        arbiter.arbitrate([_desire(ceiling_usd=10.0)], 100.0)

        assert arbiter._redis.get("veronica:test:alloc:r1:s1") is not None
        arbiter.settle("r1", "s1", actual_cost_usd=10.0)
        assert arbiter._redis.get("veronica:test:alloc:r1:s1") is None
