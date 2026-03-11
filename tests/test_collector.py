# tests/test_collector.py
"""Tests for veronica.collector -- SimpleCollector."""

from __future__ import annotations

from datetime import datetime, timezone

from veronica_core.containment.execution_context import ContextSnapshot, NodeRecord

from veronica.collector import SimpleCollector


def _make_snapshot(
    chain_id: str = "c1",
    cost: float = 0.01,
    step_count: int = 1,
) -> ContextSnapshot:
    node = NodeRecord(
        node_id="n1",
        parent_id=None,
        kind="llm",
        operation_name="test_op",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc),
        status="ok",
        cost_usd=cost,
        retries_used=0,
    )
    return ContextSnapshot(
        chain_id=chain_id,
        request_id="r1",
        step_count=step_count,
        cost_usd_accumulated=cost,
        retries_used=0,
        aborted=False,
        abort_reason=None,
        elapsed_ms=100.0,
        nodes=[node],
        events=[],
    )


class TestSimpleCollector:
    def test_collect_basic(self) -> None:
        collector = SimpleCollector()
        snapshot = _make_snapshot(cost=0.01)
        outcome = collector.collect(snapshot)
        assert outcome.chain_id == "c1"
        assert outcome.cost_usd == 0.01
        assert outcome.status == "ok"
        assert outcome.kind == "llm"

    def test_collect_halted(self) -> None:
        node = NodeRecord(
            node_id="n1",
            parent_id=None,
            kind="llm",
            operation_name="op",
            start_ts=datetime.now(timezone.utc),
            end_ts=datetime.now(timezone.utc),
            status="halted",
            cost_usd=0.0,
            retries_used=0,
        )
        snapshot = ContextSnapshot(
            chain_id="c1",
            request_id="r1",
            step_count=1,
            cost_usd_accumulated=0.5,
            retries_used=0,
            aborted=False,
            abort_reason=None,
            elapsed_ms=50.0,
            nodes=[node],
            events=[],
        )
        outcome = SimpleCollector().collect(snapshot)
        assert outcome.status == "halted"

    def test_collect_no_nodes(self) -> None:
        snapshot = ContextSnapshot(
            chain_id="c1",
            request_id="r1",
            step_count=0,
            cost_usd_accumulated=0.0,
            retries_used=0,
            aborted=False,
            abort_reason=None,
            elapsed_ms=0.0,
            nodes=[],
            events=[],
        )
        outcome = SimpleCollector().collect(snapshot)
        assert outcome.kind == "system"
        assert outcome.status == "ok"
