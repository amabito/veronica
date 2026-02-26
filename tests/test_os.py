# tests/test_os.py
"""Tests for veronica.os -- VeronicaOS orchestrator."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from veronica_core.containment.execution_context import ContextSnapshot, NodeRecord

from veronica.os import VeronicaOS
from veronica.store import MemoryStore
from veronica.types import StepIntent


def _intent(
    step_id: str = "s1",
    chain_id: str = "c1",
    model: str = "gpt-4",
) -> StepIntent:
    return StepIntent(
        step_id=step_id, request_id="r1", chain_id=chain_id,
        kind="llm", model=model, tool_name=None,
        timeout_ms=30_000, metadata={},
    )


def _snapshot(
    chain_id: str = "c1",
    cost: float = 0.01,
    status: str = "ok",
) -> ContextSnapshot:
    node = NodeRecord(
        node_id="n1", parent_id=None, kind="llm",
        operation_name="test_op",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc),
        status=status, cost_usd=cost, retries_used=0,
    )
    return ContextSnapshot(
        chain_id=chain_id, request_id="r1", step_count=1,
        cost_usd_accumulated=cost, retries_used=0,
        aborted=False, abort_reason=None,
        elapsed_ms=100.0, nodes=[node], events=[],
    )


class TestVeronicaOS:
    def test_before_step_returns_handle(self) -> None:
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        assert handle.intent.step_id == "s1"
        assert handle.policy.ceiling_usd > 0
        assert handle.policy.chain_id == "c1"

    def test_after_step_commits_to_store(self) -> None:
        store = MemoryStore()
        vos = VeronicaOS(store=store)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())
        hv = store.build_history("c1")
        assert len(hv.last_n) == 1

    def test_full_cycle(self) -> None:
        """before_step -> (simulated execution) -> after_step."""
        store = MemoryStore()
        vos = VeronicaOS(store=store)

        # Step 1
        handle1 = vos.before_step(_intent(step_id="s1"))
        vos.after_step(handle1, _snapshot(cost=0.01))

        # Step 2
        handle2 = vos.before_step(_intent(step_id="s2"))
        vos.after_step(handle2, _snapshot(cost=0.02))

        hv = store.build_history("c1")
        assert len(hv.last_n) == 2

    def test_to_exec_config_bridge(self) -> None:
        """PolicyConfig.to_exec_config produces valid ExecutionConfig."""
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        ec = handle.policy.to_exec_config()
        assert ec.max_cost_usd > 0
        assert ec.max_steps > 0

    def test_tighten_after_halt(self) -> None:
        """After a halted step, ceiling should decrease."""
        store = MemoryStore()
        vos = VeronicaOS(store=store)

        handle1 = vos.before_step(_intent())
        ceiling1 = handle1.policy.ceiling_usd

        vos.after_step(handle1, _snapshot(status="halted"))

        handle2 = vos.before_step(_intent(step_id="s2"))
        ceiling2 = handle2.policy.ceiling_usd

        assert ceiling2 < ceiling1

    def test_loosen_after_clean_run(self) -> None:
        """After a clean step, ceiling should increase."""
        store = MemoryStore()
        vos = VeronicaOS(store=store)

        handle1 = vos.before_step(_intent())
        ceiling1 = handle1.policy.ceiling_usd

        vos.after_step(handle1, _snapshot(status="ok"))

        handle2 = vos.before_step(_intent(step_id="s2"))
        ceiling2 = handle2.policy.ceiling_usd

        assert ceiling2 > ceiling1

    def test_custom_components(self) -> None:
        """Accepts custom Protocol implementations."""
        store = MemoryStore()
        vos = VeronicaOS(store=store)
        assert vos is not None

    def test_decision_meta_not_degraded(self) -> None:
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        assert not handle.decision_meta.degraded

    def test_budget_state_defaults(self) -> None:
        """Default budget state allows execution."""
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        assert handle.policy.ceiling_usd > 0
