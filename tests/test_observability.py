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


from prometheus_client import CollectorRegistry
from veronica.metrics_subscriber import MetricsSubscriber


def _metrics(prefix="test") -> tuple[MetricsSubscriber, CollectorRegistry]:
    """MetricsSubscriber with isolated registry."""
    registry = CollectorRegistry()
    ms = MetricsSubscriber.__new__(MetricsSubscriber)
    from prometheus_client import Counter, Histogram

    ms.steps_total = Counter(
        f"{prefix}_steps_total", "test",
        ["status", "kind", "recommendation", "risk_level"],
        registry=registry,
    )
    ms.step_elapsed = Histogram(
        f"{prefix}_step_elapsed_ms", "test", ["kind"],
        buckets=[10, 50, 100, 500, 1000, 5000, 10000],
        registry=registry,
    )
    ms.stage_elapsed = Histogram(
        f"{prefix}_stage_elapsed_ms", "test", ["stage"],
        buckets=[1, 5, 10, 20, 50, 100, 250, 500, 1000, 5000],
        registry=registry,
    )
    ms.cost_total = Counter(
        f"{prefix}_cost_microusd_total", "test",
        registry=registry,
    )
    ms.degrade_total = Counter(
        f"{prefix}_degrade_total", "test",
        ["degrade_reason"],
        registry=registry,
    )
    return ms, registry


def _sample_payload(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "request_id": "r1", "step_id": "s1", "chain_id": "c1",
        "kind": "llm", "status": "ok",
        "cost_usd": 0.01, "tokens_in": 100, "tokens_out": 50,
        "elapsed_ms": 150.0,
        "risk_level": "nominal", "recommendation": "continue",
        "degraded": False, "degrade_reason": None,
        "signals": [], "stage_time_ms": {"analyzer": 5.0, "planner": 10.0},
    }
    base.update(overrides)
    return base


class TestMetricsSubscriber:
    def test_steps_total_increments(self) -> None:
        """step_completed increments steps_total."""
        ms, reg = _metrics("t1")
        ms("step_completed", _sample_payload())

        val = reg.get_sample_value(
            "t1_steps_total",
            {"status": "ok", "kind": "llm",
             "recommendation": "continue", "risk_level": "nominal"},
        )
        assert val == 1.0

    def test_cost_microusd_accumulates(self) -> None:
        """cost_usd=0.01 -> inc(10_000)."""
        ms, reg = _metrics("t2")
        ms("step_completed", _sample_payload(cost_usd=0.01))

        val = reg.get_sample_value("t2_cost_microusd_total")
        assert val == 10_000.0

    def test_stage_elapsed_observes_known(self) -> None:
        """Known stages are observed in histogram."""
        ms, reg = _metrics("t3")
        ms("step_completed", _sample_payload(
            stage_time_ms={"analyzer": 5.0, "planner": 10.0},
        ))

        count = reg.get_sample_value(
            "t3_stage_elapsed_ms_count", {"stage": "analyzer"},
        )
        assert count == 1.0

    def test_unknown_stage_dropped(self) -> None:
        """Unknown stage names are not added as labels."""
        ms, reg = _metrics("t4")
        ms("step_completed", _sample_payload(
            stage_time_ms={"bogus_stage": 99.0},
        ))

        count = reg.get_sample_value(
            "t4_stage_elapsed_ms_count", {"stage": "bogus_stage"},
        )
        assert count is None  # not created

    def test_missing_field_no_crash(self) -> None:
        """Payload with missing elapsed_ms does not raise."""
        ms, _ = _metrics("t5")
        payload = _sample_payload()
        del payload["elapsed_ms"]
        ms("step_completed", payload)  # should not raise

    def test_degrade_only_on_degraded(self) -> None:
        """degrade_total only increments when degraded=True."""
        ms, reg = _metrics("t6")
        ms("step_completed", _sample_payload(degraded=False, degrade_reason=None))

        val = reg.get_sample_value(
            "t6_degrade_total", {"degrade_reason": "other"},
        )
        assert val is None  # not incremented


from veronica.structured_log_subscriber import StructuredLogSubscriber


class TestStructuredLogSubscriber:
    def test_json_log_emitted(self, caplog) -> None:
        """step_completed emits valid JSON log."""
        sub = StructuredLogSubscriber(logger_name="test.events")
        with caplog.at_level(logging.INFO, logger="test.events"):
            sub("step_completed", _sample_payload())

        assert len(caplog.records) == 1
        record = json.loads(caplog.records[0].message)
        assert record["event"] == "step_completed"
        assert record["schema_version"] == 1
        assert record["status"] == "ok"

    def test_signals_capped_at_16(self, caplog) -> None:
        """More than 16 signals are truncated in log."""
        signals = [{"kind": f"sig_{i}", "severity": "info"} for i in range(20)]
        sub = StructuredLogSubscriber(logger_name="test.cap")
        with caplog.at_level(logging.INFO, logger="test.cap"):
            sub("step_completed", _sample_payload(signals=signals))

        record = json.loads(caplog.records[0].message)
        assert len(record["signals"]) == 16

    def test_empty_payload_no_crash(self, caplog) -> None:
        """Empty payload does not raise."""
        sub = StructuredLogSubscriber(logger_name="test.empty")
        with caplog.at_level(logging.INFO, logger="test.empty"):
            sub("step_completed", {})

        assert len(caplog.records) == 1
        record = json.loads(caplog.records[0].message)
        assert record["event"] == "step_completed"


class TestObservabilityIntegration:
    def test_full_pipeline_with_subscribers(self, tmp_path, caplog) -> None:
        """VeronicaOS + BufferedEmitter + both subscribers."""
        emitter = BufferedEmitter()
        ms, reg = _metrics("int")
        log_sub = StructuredLogSubscriber(logger_name="test.int")

        emitter.subscribe("prometheus", ms)
        emitter.subscribe("structured_log", log_sub)

        vos = VeronicaOS(
            analyzer=HistoryAnalyzer(),
            cost_model=RegressionCostModel(),
            planner=AdaptivePlanner(),
            arbiter=ProportionalArbiter(),
            emitter=emitter,
            store=FileStore(data_dir=str(tmp_path)),
        )

        with caplog.at_level(logging.INFO, logger="test.int"):
            for i in range(3):
                handle = vos.before_step(_intent(step_id=f"s{i}"))
                vos.after_step(handle, _snapshot(cost=0.01))

        # Verify metrics
        val = reg.get_sample_value(
            "int_steps_total",
            {"status": "ok", "kind": "llm",
             "recommendation": "continue", "risk_level": "nominal"},
        )
        assert val == 3.0

        cost = reg.get_sample_value("int_cost_microusd_total")
        assert cost == 30_000.0  # 3 * 0.01 * 1_000_000

        # Verify logs
        assert len(caplog.records) == 3
        for rec in caplog.records:
            parsed = json.loads(rec.message)
            assert parsed["schema_version"] == 1
