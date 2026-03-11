# tests/test_arbiter.py
"""Tests for veronica.arbiter -- PassthroughArbiter."""

from __future__ import annotations

from veronica.arbiter import PassthroughArbiter
from veronica.types import DesiredPolicy


def _desired(chain_id: str = "c1", ceiling: float = 1.0) -> DesiredPolicy:
    return DesiredPolicy(
        chain_id=chain_id,
        ceiling_usd=ceiling,
        ceiling_steps=10,
        ceiling_tokens_out=5000,
        on_exceed="halt",
        fallback_model=None,
        timeout_ms=30_000,
        priority=50,
    )


class TestPassthroughArbiter:
    def test_single_chain(self) -> None:
        arbiter = PassthroughArbiter()
        result = arbiter.arbitrate([_desired("c1")], 10.0)
        assert "c1" in result
        assert result["c1"].ceiling_usd == 1.0
        assert result["c1"].chain_id == "c1"

    def test_multi_chain(self) -> None:
        arbiter = PassthroughArbiter()
        result = arbiter.arbitrate(
            [_desired("c1", 3.0), _desired("c2", 5.0)],
            10.0,
        )
        assert len(result) == 2
        assert result["c1"].ceiling_usd == 3.0
        assert result["c2"].ceiling_usd == 5.0

    def test_budget_capping(self) -> None:
        """When desires exceed total budget, cap proportionally."""
        arbiter = PassthroughArbiter()
        result = arbiter.arbitrate(
            [_desired("c1", 8.0), _desired("c2", 8.0)],
            10.0,
        )
        total = result["c1"].ceiling_usd + result["c2"].ceiling_usd
        assert total <= 10.0

    def test_empty_desires(self) -> None:
        arbiter = PassthroughArbiter()
        result = arbiter.arbitrate([], 10.0)
        assert result == {}
