# tests/test_phase2_integration.py
"""Integration tests -- full Phase 2 pipeline through VeronicaOS."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from veronica_core.containment.execution_context import ContextSnapshot, NodeRecord

from veronica.adaptive_planner import AdaptivePlanner
from veronica.buffered_emitter import BufferedEmitter
from veronica.file_store import FileStore
from veronica.history_analyzer import HistoryAnalyzer
from veronica.os import VeronicaOS
from veronica.proportional_arbiter import ProportionalArbiter
from veronica.regression_cost_model import RegressionCostModel
from veronica.types import StepIntent


def _intent(step_id: str = "s1", chain_id: str = "c1", model: str = "gpt-4") -> StepIntent:
    return StepIntent(
        step_id=step_id, request_id="r1", chain_id=chain_id,
        kind="llm", model=model, tool_name=None,
        timeout_ms=30_000, metadata={},
    )


def _snapshot(chain_id: str = "c1", cost: float = 0.01, status: str = "ok") -> ContextSnapshot:
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


class TestPhase2Integration:
    def test_full_phase2_pipeline(self, tmp_path) -> None:
        """All Phase 2 components wired through VeronicaOS."""
        emitter = BufferedEmitter()
        vos = VeronicaOS(
            analyzer=HistoryAnalyzer(),
            cost_model=RegressionCostModel(),
            planner=AdaptivePlanner(),
            arbiter=ProportionalArbiter(),
            emitter=emitter,
            store=FileStore(data_dir=str(tmp_path)),
        )

        # Run 5 successful steps
        for i in range(5):
            handle = vos.before_step(_intent(step_id=f"s{i}"))
            assert handle.policy.ceiling_usd > 0
            vos.after_step(handle, _snapshot(cost=0.01))

        # Verify emitter received events
        events = emitter.snapshot()
        assert len(events) == 5

    def test_tighten_after_halt_phase2(self, tmp_path) -> None:
        """Ceiling decreases after halted step with Phase 2 components."""
        vos = VeronicaOS(
            analyzer=HistoryAnalyzer(),
            cost_model=RegressionCostModel(),
            planner=AdaptivePlanner(),
            arbiter=ProportionalArbiter(),
            store=FileStore(data_dir=str(tmp_path)),
        )

        handle1 = vos.before_step(_intent())
        ceiling1 = handle1.policy.ceiling_usd
        vos.after_step(handle1, _snapshot(status="halted"))

        handle2 = vos.before_step(_intent(step_id="s2"))
        ceiling2 = handle2.policy.ceiling_usd
        assert ceiling2 < ceiling1

    def test_loosen_after_sustained_success(self, tmp_path) -> None:
        """Ceiling increases after 3+ consecutive ok steps (Phase 2 clean_loosen)."""
        vos = VeronicaOS(
            analyzer=HistoryAnalyzer(),
            cost_model=RegressionCostModel(),
            planner=AdaptivePlanner(),
            arbiter=ProportionalArbiter(),
            store=FileStore(data_dir=str(tmp_path)),
        )

        # Build up success streak (need >= 3 for clean_loosen)
        for i in range(4):
            handle = vos.before_step(_intent(step_id=f"s{i}"))
            vos.after_step(handle, _snapshot(cost=0.01))

        handle_before = vos.before_step(_intent(step_id="s_before"))
        ceiling_before = handle_before.policy.ceiling_usd
        vos.after_step(handle_before, _snapshot(cost=0.01))

        handle_after = vos.before_step(_intent(step_id="s_after"))
        ceiling_after = handle_after.policy.ceiling_usd
        assert ceiling_after >= ceiling_before  # May be equal due to cooldown, but never less

    def test_file_store_persists_across_os_instances(self, tmp_path) -> None:
        """Data survives VeronicaOS reconstruction."""
        store = FileStore(data_dir=str(tmp_path))
        vos1 = VeronicaOS(
            analyzer=HistoryAnalyzer(),
            cost_model=RegressionCostModel(),
            planner=AdaptivePlanner(),
            arbiter=ProportionalArbiter(),
            store=store,
        )
        handle = vos1.before_step(_intent())
        vos1.after_step(handle, _snapshot(cost=0.05))
        store.close()

        # Reconstruct with new FileStore pointing to same dir
        store2 = FileStore(data_dir=str(tmp_path))
        hv = store2.build_history("c1")
        assert hv.depth == 1
        assert hv.cost_per_step_ema > 0

    def test_phase1_tests_still_pass(self) -> None:
        """Phase 1 default VeronicaOS still works (backward compat)."""
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        assert handle.policy.ceiling_usd > 0
        vos.after_step(handle, _snapshot())

    def test_headroom_injected_by_os(self, tmp_path) -> None:
        """VeronicaOS injects budget context into FileStore."""
        store = FileStore(data_dir=str(tmp_path))
        vos = VeronicaOS(
            analyzer=HistoryAnalyzer(),
            cost_model=RegressionCostModel(),
            planner=AdaptivePlanner(),
            arbiter=ProportionalArbiter(),
            store=store,
            request_budget_usd=10.0,
        )

        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot(cost=1.0))

        hv = store.build_history("c1")
        # request_budget=10.0, spent=1.0 -> remaining=9.0
        # headroom = remaining / ceiling (ceiling set by arbiter)
        assert 0.0 < hv.budget_headroom_ratio < 1.0
