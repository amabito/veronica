# tests/test_proportional_arbiter.py
"""Tests for veronica.proportional_arbiter -- priority-weighted budget allocation."""
from __future__ import annotations


import pytest

from veronica.proportional_arbiter import ProportionalArbiter
from veronica.types import DesiredPolicy


def _desired(chain_id: str = "c1", ceiling: float = 1.0, priority: int = 50) -> DesiredPolicy:
    return DesiredPolicy(
        chain_id=chain_id, ceiling_usd=ceiling, ceiling_steps=100,
        ceiling_tokens_out=50000, on_exceed="halt",
        fallback_model=None, timeout_ms=30000, priority=priority,
    )


class TestProportionalArbiter:
    def test_single_chain_passthrough(self) -> None:
        arbiter = ProportionalArbiter()
        result = arbiter.arbitrate([_desired("c1", 1.0, 50)], 10.0)
        assert "c1" in result
        assert result["c1"].ceiling_usd == pytest.approx(1.0)

    def test_proportional_allocation_by_priority(self) -> None:
        arbiter = ProportionalArbiter()
        desires = [
            _desired("c1", 5.0, 75),   # 3x priority
            _desired("c2", 5.0, 25),   # 1x priority
        ]
        result = arbiter.arbitrate(desires, 4.0)
        assert result["c1"].ceiling_usd > result["c2"].ceiling_usd
        total = result["c1"].ceiling_usd + result["c2"].ceiling_usd
        assert total <= 4.0 + 1e-9

    def test_total_allocation_never_exceeds_budget(self) -> None:
        arbiter = ProportionalArbiter()
        desires = [_desired(f"c{i}", 10.0, 50) for i in range(5)]
        result = arbiter.arbitrate(desires, 5.0)
        total = sum(pc.ceiling_usd for pc in result.values())
        assert total <= 5.0 + 1e-9

    def test_priority_zero_excluded(self) -> None:
        arbiter = ProportionalArbiter()
        desires = [
            _desired("c1", 1.0, 50),
            _desired("c2", 1.0, 0),
        ]
        result = arbiter.arbitrate(desires, 10.0)
        assert "c1" in result
        assert "c2" not in result

    def test_negative_priority_excluded(self) -> None:
        arbiter = ProportionalArbiter()
        result = arbiter.arbitrate([_desired("c1", 1.0, -10)], 10.0)
        assert "c1" not in result

    def test_conditional_min_allocation(self) -> None:
        arbiter = ProportionalArbiter()
        desires = [
            _desired("c1", 0.001, 50),
            _desired("c2", 0.001, 50),
        ]
        # Budget can cover both minimums
        result = arbiter.arbitrate(desires, 1.0)
        for pc in result.values():
            assert pc.ceiling_usd >= 0.01  # min allocation

    def test_min_allocation_not_applied_when_budget_tight(self) -> None:
        arbiter = ProportionalArbiter()
        desires = [
            _desired(f"c{i}", 0.001, 50) for i in range(200)
        ]
        # Budget cannot cover 200 * 0.01 = 2.0
        result = arbiter.arbitrate(desires, 0.50)
        total = sum(pc.ceiling_usd for pc in result.values())
        assert total <= 0.50 + 1e-9

    def test_ceiling_cap_surplus_redistribution(self) -> None:
        arbiter = ProportionalArbiter()
        desires = [
            _desired("c1", 0.50, 50),   # desired only 0.50
            _desired("c2", 10.0, 50),   # desired 10.0
        ]
        result = arbiter.arbitrate(desires, 5.0)
        # c1 capped at 0.50, surplus goes to c2
        assert result["c1"].ceiling_usd <= 0.50
        assert result["c2"].ceiling_usd > 2.5  # got surplus

    def test_empty_desires_returns_empty(self) -> None:
        arbiter = ProportionalArbiter()
        result = arbiter.arbitrate([], 10.0)
        assert result == {}

    def test_protocol_compatible(self) -> None:
        from veronica.protocols import ArbiterProtocol

        arbiter = ProportionalArbiter()
        assert isinstance(arbiter, ArbiterProtocol)
