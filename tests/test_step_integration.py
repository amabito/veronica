# tests/test_step_integration.py
"""Tests for Phase 6b: LLM integration adapter (step/run_step)."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from veronica_core.shield.event import SafetyEvent

from veronica.collector import SimpleCollector
from veronica.os import _make_fallback_snapshot
from veronica.types import StepIntent


def _empty_intent(
    step_id: str = "s1",
    chain_id: str = "c1",
    request_id: str = "r1",
) -> StepIntent:
    return StepIntent(
        step_id=step_id,
        request_id=request_id,
        chain_id=chain_id,
        kind="llm",
        model="gpt-4",
        tool_name=None,
        timeout_ms=30_000,
        metadata={},
    )


class TestFallbackSnapshot:
    def test_fallback_snapshot_passes_collector(self) -> None:
        """Fallback snapshot is consumable by SimpleCollector and events contain step_id."""
        intent = _empty_intent(
            step_id="test-step-1", chain_id="chain-a", request_id="req-x"
        )
        snapshot = _make_fallback_snapshot(intent, "test_reason")

        # Verify snapshot fields
        assert snapshot.chain_id == "chain-a"
        assert snapshot.request_id == "req-x"
        assert snapshot.aborted is True
        assert snapshot.abort_reason == "test_reason"
        assert snapshot.cost_usd_accumulated == 0.0
        assert snapshot.nodes == []

        # Verify events contain step_id in metadata
        assert len(snapshot.events) == 1
        event = snapshot.events[0]
        assert isinstance(event, SafetyEvent)
        assert event.metadata["step_id"] == "test-step-1"

        # Verify SimpleCollector can consume it
        collector = SimpleCollector()
        outcome = collector.collect(snapshot)
        assert outcome.chain_id == "chain-a"
        assert outcome.status == "ok"
        assert len(outcome.events) == 1

    def test_fallback_snapshot_without_safety_event(self) -> None:
        """Fallback works even when SafetyEvent import fails (monkeypatch)."""
        intent = _empty_intent()

        with patch(
            "veronica.os._make_fallback_snapshot.__module__",
            side_effect=ImportError("mocked"),
        ):
            # We can't easily patch module-level import inside a function.
            # Instead, patch SafetyEvent constructor to raise.
            with patch(
                "veronica_core.shield.event.SafetyEvent",
                side_effect=TypeError("mocked API change"),
            ):
                snapshot = _make_fallback_snapshot(intent, "import_failed")

        assert snapshot.chain_id == "c1"
        assert snapshot.aborted is True
        assert snapshot.events == []  # empty because SafetyEvent failed

        # Still consumable by collector
        collector = SimpleCollector()
        outcome = collector.collect(snapshot)
        assert outcome.chain_id == "c1"


from unittest.mock import MagicMock

from veronica_core.containment.execution_context import ExecutionContext
from veronica_core.shield.hooks import Decision

from veronica.os import StepContext
from veronica.types import (
    PolicyConfig,
    StepHandle,
    CostEstimate,
    DesiredPolicy,
    DecisionMeta,
)


def _make_handle(kind: str = "llm") -> StepHandle:
    intent = StepIntent(
        step_id="s1",
        request_id="r1",
        chain_id="c1",
        kind=kind,
        model="gpt-4",
        tool_name=None,
        timeout_ms=30_000,
        metadata={},
    )
    policy = PolicyConfig(
        chain_id="c1",
        ceiling_usd=1.0,
        on_exceed="halt",
        issued_at=time.time(),
    )
    desired = DesiredPolicy(
        chain_id="c1",
        ceiling_usd=1.0,
        ceiling_steps=100,
        ceiling_tokens_out=50_000,
        on_exceed="halt",
        fallback_model=None,
        timeout_ms=30_000,
        priority=50,
    )
    cost = CostEstimate(
        estimated_usd=0.01,
        confidence=0.9,
        model_used="gpt-4",
        basis="pricing_table",
    )
    meta = DecisionMeta(
        risk_level="nominal",
        recommendation="continue",
        degraded=False,
        stage_time_ms={},
    )
    return StepHandle(
        intent=intent, policy=policy, desired=desired, cost=cost, decision_meta=meta
    )


class TestStepContext:
    def test_run_dispatches_llm_for_llm_kind(self) -> None:
        """run() calls wrap_llm_call for kind='llm'."""
        handle = _make_handle(kind="llm")
        mock_ctx = MagicMock(spec=ExecutionContext)
        mock_ctx.wrap_llm_call.return_value = Decision.ALLOW
        step_ctx = StepContext(handle=handle, exec_ctx=mock_ctx)

        result = step_ctx.run(lambda: "llm_result")
        mock_ctx.wrap_llm_call.assert_called_once()
        mock_ctx.wrap_tool_call.assert_not_called()

    def test_run_dispatches_tool_for_tool_kind(self) -> None:
        """run() calls wrap_tool_call for kind='tool'."""
        handle = _make_handle(kind="tool")
        mock_ctx = MagicMock(spec=ExecutionContext)
        mock_ctx.wrap_tool_call.return_value = Decision.ALLOW
        step_ctx = StepContext(handle=handle, exec_ctx=mock_ctx)

        result = step_ctx.run(lambda: "tool_result")
        mock_ctx.wrap_tool_call.assert_called_once()
        mock_ctx.wrap_llm_call.assert_not_called()

    def test_run_dispatches_llm_for_system_kind(self) -> None:
        """run() calls wrap_llm_call for kind='system' (no wrap_system_call in core)."""
        handle = _make_handle(kind="system")
        mock_ctx = MagicMock(spec=ExecutionContext)
        mock_ctx.wrap_llm_call.return_value = Decision.ALLOW
        step_ctx = StepContext(handle=handle, exec_ctx=mock_ctx)

        result = step_ctx.run(lambda: "system_result")
        mock_ctx.wrap_llm_call.assert_called_once()

    def test_run_llm_calls_wrap_llm_call(self) -> None:
        """run_llm() directly calls wrap_llm_call."""
        handle = _make_handle()
        mock_ctx = MagicMock(spec=ExecutionContext)
        mock_ctx.wrap_llm_call.return_value = Decision.ALLOW
        step_ctx = StepContext(handle=handle, exec_ctx=mock_ctx)

        step_ctx.run_llm(lambda: "x")
        mock_ctx.wrap_llm_call.assert_called_once()

    def test_run_tool_calls_wrap_tool_call(self) -> None:
        """run_tool() directly calls wrap_tool_call."""
        handle = _make_handle()
        mock_ctx = MagicMock(spec=ExecutionContext)
        mock_ctx.wrap_tool_call.return_value = Decision.ALLOW
        step_ctx = StepContext(handle=handle, exec_ctx=mock_ctx)

        step_ctx.run_tool(lambda: "x")
        mock_ctx.wrap_tool_call.assert_called_once()

    def test_policy_property(self) -> None:
        """policy property returns the handle's policy."""
        handle = _make_handle()
        mock_ctx = MagicMock(spec=ExecutionContext)
        step_ctx = StepContext(handle=handle, exec_ctx=mock_ctx)

        assert step_ctx.policy is handle.policy
        assert step_ctx.policy.ceiling_usd == 1.0


from veronica.os import VeronicaOS


class TestStepContextManager:
    def test_step_calls_after_step(self) -> None:
        """after_step runs after normal execution inside step()."""
        vos = VeronicaOS()
        intent = _empty_intent()

        with vos.step(intent) as ctx:
            assert isinstance(ctx, StepContext)
            assert ctx.policy.ceiling_usd > 0

        # after_step committed to store -- verify via store history
        history = vos._store.build_history("c1")
        assert history.depth == 1

    def test_step_calls_after_step_on_exception(self) -> None:
        """after_step runs even when the body raises an exception."""
        vos = VeronicaOS()
        intent = _empty_intent()

        with pytest.raises(ValueError, match="boom"):
            with vos.step(intent) as ctx:
                raise ValueError("boom")

        # after_step still committed
        history = vos._store.build_history("c1")
        assert history.depth == 1

    def test_step_uses_fallback_on_snapshot_failure(self) -> None:
        """When get_snapshot() fails, fallback snapshot is used and after_step still runs."""
        vos = VeronicaOS()
        intent = _empty_intent()

        with vos.step(intent) as ctx:
            # Force get_snapshot to fail
            ctx.exec_ctx.get_snapshot = MagicMock(side_effect=RuntimeError("boom"))

        # after_step still committed (via fallback snapshot)
        history = vos._store.build_history("c1")
        assert history.depth == 1


import re


class TestNormalizeIntent:
    def test_fills_defaults(self) -> None:
        """Empty fields get UUID/default/step-N/30000/{}."""
        vos = VeronicaOS()
        intent = StepIntent(
            step_id="",
            request_id="",
            chain_id="",
            kind="llm",
            model="gpt-4",
            tool_name=None,
            timeout_ms=0,
            metadata={},
        )
        result = vos._normalize_intent(intent)

        # request_id: 32-char hex UUID
        assert len(result.request_id) == 32
        assert re.match(r"^[0-9a-f]{32}$", result.request_id)

        # chain_id: "default"
        assert result.chain_id == "default"

        # step_id: "step-N"
        assert result.step_id.startswith("step-")
        n = int(result.step_id.split("-")[1])
        assert n >= 1

        # timeout_ms: 30000
        assert result.timeout_ms == 30_000

        # metadata: {}
        assert result.metadata == {}

        # kind and model preserved
        assert result.kind == "llm"
        assert result.model == "gpt-4"

    def test_preserves_explicit_values(self) -> None:
        """Explicit values are not overwritten; returns same instance."""
        vos = VeronicaOS()
        intent = StepIntent(
            step_id="my-step",
            request_id="my-req",
            chain_id="my-chain",
            kind="tool",
            model=None,
            tool_name="search",
            timeout_ms=5000,
            metadata={"key": "val"},
        )
        result = vos._normalize_intent(intent)

        # Same instance (no changes needed)
        assert result is intent

    def test_partial_fill(self) -> None:
        """Only empty fields are filled; explicit fields preserved."""
        vos = VeronicaOS()
        intent = StepIntent(
            step_id="custom-step",
            request_id="",
            chain_id="my-chain",
            kind="llm",
            model="gpt-4",
            tool_name=None,
            timeout_ms=5000,
            metadata={"x": 1},
        )
        result = vos._normalize_intent(intent)

        assert result.step_id == "custom-step"  # preserved
        assert len(result.request_id) == 32  # filled
        assert result.chain_id == "my-chain"  # preserved
        assert result.timeout_ms == 5000  # preserved
        assert result.metadata == {"x": 1}  # preserved


class TestRunStep:
    def test_run_step_sugar(self) -> None:
        """run_step produces a result and commits to store."""
        vos = VeronicaOS()
        intent = StepIntent(
            step_id="",
            request_id="",
            chain_id="",
            kind="llm",
            model="gpt-4",
            tool_name=None,
            timeout_ms=0,
            metadata={},
        )

        result = vos.run_step(intent, lambda: "hello")

        # run_step returns the Decision from wrap_llm_call, not "hello"
        # (wrap_llm_call wraps the fn and returns Decision.ALLOW on success)
        assert result is not None

        # after_step committed to store via "default" chain
        history = vos._store.build_history("default")
        assert history.depth == 1
