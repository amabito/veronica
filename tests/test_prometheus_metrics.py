# tests/test_prometheus_metrics.py
"""Tests for PrometheusSubscriber -- CP-level Prometheus metrics."""

from __future__ import annotations

import threading

from prometheus_client import CollectorRegistry

from veronica.metrics import PrometheusSubscriber


def _make_subscriber(
    prefix: str = "test",
) -> tuple[PrometheusSubscriber, CollectorRegistry]:
    """Create an isolated PrometheusSubscriber for testing."""
    registry = CollectorRegistry()
    sub = PrometheusSubscriber(prefix=prefix, registry=registry)
    return sub, registry


def _completed_payload(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "request_id": "r1",
        "step_id": "s1",
        "chain_id": "c1",
        "policy_hash": "abc123" + "0" * 58,
        "kind": "llm",
        "status": "ok",
        "cost_usd": 0.01,
        "tokens_in": 100,
        "tokens_out": 50,
        "elapsed_ms": 150.0,
        "risk_level": "nominal",
        "recommendation": "continue",
        "degraded": False,
        "degrade_reason": None,
        "signals": [],
        "stage_time_ms": {},
    }
    base.update(overrides)
    return base


def _denied_payload(**overrides) -> dict:
    base = {
        "schema_version": 1,
        "request_id": "r1",
        "step_id": "s1",
        "chain_id": "c1",
        "policy_hash": "abc123" + "0" * 58,
        "kind": "llm",
        "reason": "model 'gpt-4' is blocked by org policy",
    }
    base.update(overrides)
    return base


class TestStepCompletedCounter:
    def test_step_completed_increments(self) -> None:
        sub, reg = _make_subscriber("c1")
        sub("step_completed", _completed_payload())

        val = reg.get_sample_value(
            "c1_step_completed_total",
            {"chain_id": "c1", "decision_type": "ok"},
        )
        assert val == 1.0

    def test_step_completed_accumulates(self) -> None:
        sub, reg = _make_subscriber("c2")
        payload = _completed_payload(chain_id="chain-x")
        sub("step_completed", payload)
        sub("step_completed", payload)
        sub("step_completed", payload)

        val = reg.get_sample_value(
            "c2_step_completed_total",
            {"chain_id": "chain-x", "decision_type": "ok"},
        )
        assert val == 3.0

    def test_decision_type_uses_status(self) -> None:
        sub, reg = _make_subscriber("c3")
        sub("step_completed", _completed_payload(status="halted"))

        val = reg.get_sample_value(
            "c3_step_completed_total",
            {"chain_id": "c1", "decision_type": "halted"},
        )
        assert val == 1.0


class TestHaltCounter:
    def test_halt_total_increments_on_halted_status(self) -> None:
        sub, reg = _make_subscriber("h1")
        sub("step_completed", _completed_payload(status="halted"))

        val = reg.get_sample_value(
            "h1_halt_total",
            {"chain_id": "c1"},
        )
        assert val == 1.0

    def test_halt_total_not_incremented_on_ok(self) -> None:
        sub, reg = _make_subscriber("h2")
        sub("step_completed", _completed_payload(status="ok"))

        val = reg.get_sample_value(
            "h2_halt_total",
            {"chain_id": "c1"},
        )
        assert val is None  # not created

    def test_multiple_halts_accumulate(self) -> None:
        sub, reg = _make_subscriber("h3")
        for _ in range(5):
            sub("step_completed", _completed_payload(status="halted"))

        val = reg.get_sample_value(
            "h3_halt_total",
            {"chain_id": "c1"},
        )
        assert val == 5.0


class TestDegradeCounter:
    def test_degrade_total_increments_when_degraded(self) -> None:
        sub, reg = _make_subscriber("d1")
        sub("step_completed", _completed_payload(degraded=True))

        val = reg.get_sample_value(
            "d1_degrade_total",
            {"chain_id": "c1"},
        )
        assert val == 1.0

    def test_degrade_total_not_incremented_when_not_degraded(self) -> None:
        sub, reg = _make_subscriber("d2")
        sub("step_completed", _completed_payload(degraded=False))

        val = reg.get_sample_value(
            "d2_degrade_total",
            {"chain_id": "c1"},
        )
        assert val is None


class TestDeniedCounter:
    def test_step_denied_increments(self) -> None:
        # decision_type now uses "status" field, not "reason", to avoid high cardinality
        sub, reg = _make_subscriber("dn1")
        payload = _denied_payload()
        payload["status"] = "denied"
        sub("step_denied", payload)

        val = reg.get_sample_value(
            "dn1_step_denied_total",
            {
                "chain_id": "c1",
                "decision_type": "denied",
            },
        )
        assert val == 1.0

    def test_step_denied_missing_status_uses_unknown(self) -> None:
        # When status is absent, decision_type falls back to "unknown"
        sub, reg = _make_subscriber("dn2")
        payload = _denied_payload()
        del payload["policy_hash"]
        sub("step_denied", payload)

        val = reg.get_sample_value(
            "dn2_step_denied_total",
            {
                "chain_id": "c1",
                "decision_type": "unknown",
            },
        )
        assert val == 1.0


class TestDurationHistogram:
    def test_step_duration_observed(self) -> None:
        sub, reg = _make_subscriber("dur1")
        sub("step_completed", _completed_payload(elapsed_ms=250.0))

        count = reg.get_sample_value(
            "dur1_step_duration_seconds_count",
            {"chain_id": "c1"},
        )
        assert count == 1.0

    def test_elapsed_ms_converted_to_seconds(self) -> None:
        sub, reg = _make_subscriber("dur2")
        sub("step_completed", _completed_payload(elapsed_ms=1000.0))

        total = reg.get_sample_value(
            "dur2_step_duration_seconds_sum",
            {"chain_id": "c1"},
        )
        assert abs(total - 1.0) < 1e-9  # 1000ms == 1.0s

    def test_missing_elapsed_ms_no_crash(self) -> None:
        sub, _ = _make_subscriber("dur3")
        payload = _completed_payload()
        del payload["elapsed_ms"]
        sub("step_completed", payload)  # must not raise


class TestActiveChains:
    def test_new_chain_increments_gauge(self) -> None:
        sub, reg = _make_subscriber("ac1")
        sub("step_completed", _completed_payload(chain_id="chain-a"))

        val = reg.get_sample_value("ac1_active_chains")
        assert val == 1.0

    def test_same_chain_does_not_double_count(self) -> None:
        sub, reg = _make_subscriber("ac2")
        for _ in range(5):
            sub("step_completed", _completed_payload(chain_id="chain-a"))

        val = reg.get_sample_value("ac2_active_chains")
        assert val == 1.0

    def test_multiple_chains_counted(self) -> None:
        sub, reg = _make_subscriber("ac3")
        for i in range(4):
            sub("step_completed", _completed_payload(chain_id=f"chain-{i}"))

        val = reg.get_sample_value("ac3_active_chains")
        assert val == 4.0

    def test_unknown_chain_id_not_counted(self) -> None:
        sub, reg = _make_subscriber("ac4")
        payload = _completed_payload()
        del payload["chain_id"]
        sub("step_completed", payload)

        val = reg.get_sample_value("ac4_active_chains")
        assert val == 0.0  # gauge starts at 0, unknown chains not tracked


class TestDoubleInstantiation:
    def test_same_prefix_same_registry_no_raise(self) -> None:
        reg = CollectorRegistry()
        sub1 = PrometheusSubscriber(prefix="dup", registry=reg)
        sub2 = PrometheusSubscriber(prefix="dup", registry=reg)
        assert sub1.step_completed_total is sub2.step_completed_total
        assert sub1.halt_total is sub2.halt_total


class TestResilienceAdversarial:
    def test_unknown_event_type_no_crash(self) -> None:
        sub, _ = _make_subscriber("res1")
        sub("unknown_event", {"foo": "bar"})  # must not raise

    def test_empty_payload_no_crash(self) -> None:
        sub, _ = _make_subscriber("res2")
        sub("step_completed", {})  # must not raise

    def test_none_values_use_unknown_label(self) -> None:
        sub, reg = _make_subscriber("res3")
        sub(
            "step_completed",
            _completed_payload(chain_id=None, policy_hash=None, status=None),
        )

        val = reg.get_sample_value(
            "res3_step_completed_total",
            {"chain_id": "unknown", "decision_type": "unknown"},
        )
        assert val == 1.0

    def test_malformed_elapsed_ms_no_crash(self) -> None:
        sub, _ = _make_subscriber("res4")
        sub("step_completed", _completed_payload(elapsed_ms="not_a_float"))

    def test_concurrent_events_no_race(self) -> None:
        sub, reg = _make_subscriber("res5")
        errors: list[Exception] = []

        def emit(i: int) -> None:
            try:
                sub("step_completed", _completed_payload(chain_id=f"c{i % 3}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=emit, args=(i,)) for i in range(30)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        total = sum(
            reg.get_sample_value(
                "res5_step_completed_total",
                {"chain_id": f"c{i}", "decision_type": "ok"},
            )
            or 0.0
            for i in range(3)
        )
        assert total == 30.0


class TestIntegrationWithVeronicaOS:
    def test_policy_hash_label_matches_payload(self, tmp_path) -> None:
        """Metric is recorded for the step; policy_hash is in payload but not labels."""
        from veronica.buffered_emitter import BufferedEmitter
        from veronica.os import VeronicaOS
        from veronica.types import StepIntent

        intent = StepIntent(
            step_id="s1",
            request_id="r1",
            chain_id="chain-audit",
            kind="llm",
            model="gpt-4",
            tool_name=None,
            timeout_ms=30_000,
            metadata={},
        )

        reg = CollectorRegistry()
        sub = PrometheusSubscriber(prefix="integ", registry=reg)
        emitter = BufferedEmitter()
        emitter.subscribe("prom_cp", sub)

        vos = VeronicaOS(emitter=emitter)
        from datetime import datetime, timezone
        from veronica_core.containment.execution_context import (
            ContextSnapshot,
            NodeRecord,
        )

        node = NodeRecord(
            node_id="n1",
            parent_id=None,
            kind="llm",
            operation_name="op",
            start_ts=datetime.now(timezone.utc),
            end_ts=datetime.now(timezone.utc),
            status="ok",
            cost_usd=0.01,
            retries_used=0,
        )
        snapshot = ContextSnapshot(
            chain_id="chain-audit",
            request_id="r1",
            step_count=1,
            cost_usd_accumulated=0.01,
            retries_used=0,
            aborted=False,
            abort_reason=None,
            elapsed_ms=100.0,
            nodes=[node],
            events=[],
        )

        handle = vos.before_step(intent)
        vos.after_step(handle, snapshot)

        # policy_hash is no longer a label (cardinality guard) -- verify the
        # metric is recorded with chain_id and decision_type only.
        val = reg.get_sample_value(
            "integ_step_completed_total",
            {"chain_id": "chain-audit", "decision_type": "ok"},
        )
        assert val == 1.0
