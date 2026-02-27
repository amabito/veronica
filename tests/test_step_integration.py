# tests/test_step_integration.py
"""Tests for Phase 6b: LLM integration adapter (step/run_step)."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from veronica_core.containment.execution_context import ContextSnapshot
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
        step_id=step_id, request_id=request_id, chain_id=chain_id,
        kind="llm", model="gpt-4", tool_name=None,
        timeout_ms=30_000, metadata={},
    )


class TestFallbackSnapshot:
    def test_fallback_snapshot_passes_collector(self) -> None:
        """Fallback snapshot is consumable by SimpleCollector and events contain step_id."""
        intent = _empty_intent(step_id="test-step-1", chain_id="chain-a", request_id="req-x")
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
