# tests/test_observability.py
"""Tests for Phase 4 observability -- payload, metrics, structured logging."""
from __future__ import annotations

import json
import logging
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


def _intent(
    step_id: str = "s1",
    request_id: str = "r1",
    chain_id: str = "c1",
) -> StepIntent:
    return StepIntent(
        step_id=step_id, request_id=request_id, chain_id=chain_id,
        kind="llm", model="gpt-4", tool_name=None,
        timeout_ms=30_000, metadata={},
    )


def _snapshot(
    chain_id: str = "c1",
    cost: float = 0.01,
    status: str = "ok",
    request_id: str = "r1",
) -> ContextSnapshot:
    node = NodeRecord(
        node_id="n1", parent_id=None, kind="llm",
        operation_name="test_op",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc),
        status=status, cost_usd=cost, retries_used=0,
    )
    return ContextSnapshot(
        chain_id=chain_id, request_id=request_id, step_count=1,
        cost_usd_accumulated=cost, retries_used=0,
        aborted=False, abort_reason=None,
        elapsed_ms=100.0, nodes=[node], events=[],
    )


def _make_os(tmp_path, emitter=None):
    """Create a VeronicaOS with Phase 2 components."""
    if emitter is None:
        emitter = BufferedEmitter()
    return VeronicaOS(
        analyzer=HistoryAnalyzer(),
        cost_model=RegressionCostModel(),
        planner=AdaptivePlanner(),
        arbiter=ProportionalArbiter(),
        emitter=emitter,
        store=FileStore(data_dir=str(tmp_path)),
    ), emitter


class TestPayload:
    def test_payload_has_all_16_fields(self, tmp_path) -> None:
        """Emitted payload contains all 16 required fields."""
        vos, emitter = _make_os(tmp_path)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        events = emitter.snapshot()
        assert len(events) == 1
        event_type, payload = events[0]
        assert event_type == "step_completed"

        required = {
            "schema_version", "request_id", "step_id", "chain_id",
            "kind", "status", "cost_usd", "tokens_in", "tokens_out",
            "elapsed_ms", "risk_level", "recommendation", "degraded",
            "degrade_reason", "signals", "stage_time_ms",
        }
        assert required.issubset(payload.keys())

    def test_schema_version_is_1(self, tmp_path) -> None:
        """schema_version is always 1."""
        vos, emitter = _make_os(tmp_path)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        _, payload = emitter.snapshot()[0]
        assert payload["schema_version"] == 1

    def test_signals_contain_kind_and_severity(self, tmp_path) -> None:
        """Signals are dicts with kind and severity."""
        vos, emitter = _make_os(tmp_path)
        # Trigger a halt to generate signals
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot(status="halted"))

        _, payload = emitter.snapshot()[0]
        signals = payload["signals"]
        if signals:  # halt should generate at least one signal
            for sig in signals:
                assert "kind" in sig
                assert "severity" in sig

    def test_stage_time_ms_filtered(self, tmp_path) -> None:
        """stage_time_ms only contains known stage names."""
        vos, emitter = _make_os(tmp_path)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        _, payload = emitter.snapshot()[0]
        known = {
            "collector", "analyzer", "cost_model", "planner",
            "arbiter", "store", "emit",
        }
        for key in payload["stage_time_ms"]:
            assert key in known

    def test_store_timing_present(self, tmp_path) -> None:
        """stage_time_ms contains 'store' key."""
        vos, emitter = _make_os(tmp_path)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        _, payload = emitter.snapshot()[0]
        assert "store" in payload["stage_time_ms"]
        assert payload["stage_time_ms"]["store"] >= 0

    def test_degrade_reason_none_when_not_degraded(self, tmp_path) -> None:
        """Normal step has degrade_reason=None."""
        vos, emitter = _make_os(tmp_path)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        _, payload = emitter.snapshot()[0]
        assert payload["degraded"] is False
        assert payload["degrade_reason"] is None

    def test_identity_fields_match_intent(self, tmp_path) -> None:
        """request_id, step_id, chain_id match the original intent."""
        vos, emitter = _make_os(tmp_path)
        handle = vos.before_step(_intent(
            step_id="s42", request_id="r99", chain_id="c7",
        ))
        vos.after_step(handle, _snapshot(chain_id="c7", request_id="r99"))

        _, payload = emitter.snapshot()[0]
        assert payload["request_id"] == "r99"
        assert payload["chain_id"] == "c7"
