# tests/test_event_ingestor.py
"""Tests for EventIngestor (ingest/event_ingestor.py)."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

import pytest

from veronica_core.shield.event import SafetyEvent
from veronica_core.shield.hooks import Decision

from veronica.ingest.event_ingestor import (
    CPStepOutcomeStore,
    EventIngestor,
    _convert_event,
    _derive_audit_id,
    _derive_policy_hash,
    _map_decision,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    event_type: str = "step_completed",
    decision: Decision = Decision.ALLOW,
    hook: str = "test_hook",
    request_id: str | None = "req-1",
    metadata: dict[str, Any] | None = None,
) -> SafetyEvent:
    return SafetyEvent(
        event_type=event_type,
        decision=decision,
        reason="test",
        hook=hook,
        request_id=request_id,
        ts=datetime(2024, 1, 1, tzinfo=timezone.utc),
        metadata=metadata or {
            "step_id": "step-1",
            "chain_id": "chain-abc",
        },
    )


# ---------------------------------------------------------------------------
# _map_decision
# ---------------------------------------------------------------------------


class TestMapDecision:
    @pytest.mark.parametrize("decision,expected", [
        (Decision.ALLOW, "allow"),
        (Decision.HALT, "halt"),
        (Decision.DEGRADE, "degrade"),
        (Decision.RETRY, "retry"),
        (Decision.QUARANTINE, "quarantine"),
        (Decision.QUEUE, "queue"),
    ])
    def test_all_decisions(self, decision: Decision, expected: str) -> None:
        assert _map_decision(decision) == expected

    def test_fallback_on_unknown_value(self) -> None:
        class FakeDecision:
            value = "NONEXISTENT"
        # Unknown decisions must map to "unknown" (not "allow") to avoid fail-open behavior
        assert _map_decision(FakeDecision()) == "unknown"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _derive_policy_hash
# ---------------------------------------------------------------------------


class TestDerivePolicyHash:
    def test_uses_metadata_policy_hash_when_present(self) -> None:
        event = _make_event(metadata={"policy_hash": "abc123", "step_id": "s", "chain_id": "c"})
        assert _derive_policy_hash(event) == "abc123"

    def test_derives_sha256_when_not_present(self) -> None:
        event = _make_event(metadata={"step_id": "s", "chain_id": "c"})
        h = _derive_policy_hash(event)
        assert len(h) == 64  # SHA-256 hex
        assert h.isalnum()

    def test_same_hook_and_event_type_yields_same_hash(self) -> None:
        e1 = _make_event(event_type="step_completed", hook="hook_a")
        e2 = _make_event(event_type="step_completed", hook="hook_a")
        assert _derive_policy_hash(e1) == _derive_policy_hash(e2)

    def test_different_hook_yields_different_hash(self) -> None:
        e1 = _make_event(hook="hook_a")
        e2 = _make_event(hook="hook_b")
        assert _derive_policy_hash(e1) != _derive_policy_hash(e2)


# ---------------------------------------------------------------------------
# _derive_audit_id
# ---------------------------------------------------------------------------


class TestDeriveAuditId:
    def test_uses_metadata_audit_id_when_present(self) -> None:
        event = _make_event(metadata={"audit_id": "my-id", "step_id": "s", "chain_id": "c"})
        assert _derive_audit_id(event) == "my-id"

    def test_generates_hex_uuid_when_not_present(self) -> None:
        event = _make_event()
        aid = _derive_audit_id(event)
        assert len(aid) == 32
        assert aid.isalnum()

    def test_each_call_generates_unique_id(self) -> None:
        event = _make_event()
        ids = {_derive_audit_id(event) for _ in range(10)}
        assert len(ids) == 10  # All unique


# ---------------------------------------------------------------------------
# _convert_event
# ---------------------------------------------------------------------------


class TestConvertEvent:
    def test_basic_conversion(self) -> None:
        event = _make_event(
            metadata={
                "step_id": "s-1",
                "chain_id": "c-1",
                "cost_usd": 0.005,
                "tokens_in": 100,
                "tokens_out": 50,
                "elapsed_ms": 250.0,
            }
        )
        outcome = _convert_event(event)
        assert outcome.step_id == "s-1"
        assert outcome.chain_id == "c-1"
        assert outcome.cost_usd == 0.005
        assert outcome.tokens == 150
        assert outcome.duration_ms == 250.0
        assert outcome.decision == "allow"

    def test_operation_name_override(self) -> None:
        event = _make_event()
        outcome = _convert_event(event, operation_name="gpt-4o")
        assert outcome.operation_name == "gpt-4o"

    def test_operation_name_from_metadata(self) -> None:
        event = _make_event(metadata={
            "step_id": "s", "chain_id": "c",
            "operation_name": "claude-3-sonnet",
        })
        outcome = _convert_event(event)
        assert outcome.operation_name == "claude-3-sonnet"

    def test_operation_name_fallback_to_hook(self) -> None:
        event = _make_event(hook="budget_hook", metadata={"step_id": "s", "chain_id": "c"})
        outcome = _convert_event(event)
        assert outcome.operation_name == "budget_hook"

    def test_timestamp_from_event_ts(self) -> None:
        event = _make_event()
        outcome = _convert_event(event)
        # Event ts = 2024-01-01T00:00:00 UTC
        assert outcome.timestamp == datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp()

    def test_missing_step_id_falls_back_to_request_id(self) -> None:
        event = _make_event(request_id="req-42", metadata={"chain_id": "c"})
        outcome = _convert_event(event)
        assert outcome.step_id == "req-42"

    def test_halt_decision_maps_correctly(self) -> None:
        event = _make_event(decision=Decision.HALT)
        outcome = _convert_event(event)
        assert outcome.decision == "halt"

    def test_tokens_zero_when_missing(self) -> None:
        event = _make_event(metadata={"step_id": "s", "chain_id": "c"})
        outcome = _convert_event(event)
        assert outcome.tokens == 0

    def test_cost_zero_when_missing(self) -> None:
        event = _make_event(metadata={"step_id": "s", "chain_id": "c"})
        outcome = _convert_event(event)
        assert outcome.cost_usd == 0.0

    def test_tokens_combined_from_in_and_out(self) -> None:
        event = _make_event(metadata={
            "step_id": "s", "chain_id": "c",
            "tokens_in": 200, "tokens_out": 300,
        })
        outcome = _convert_event(event)
        assert outcome.tokens == 500


# ---------------------------------------------------------------------------
# CPStepOutcomeStore
# ---------------------------------------------------------------------------


class TestCPStepOutcomeStore:
    def _make_outcome(self, step_id: str = "s") -> "CPStepOutcome":  # noqa: F821
        from veronica.schemas.events import StepOutcome as CPStepOutcome
        return CPStepOutcome(
            step_id=step_id, chain_id="c", operation_name="op",
            decision="allow", cost_usd=0.0, tokens=0, duration_ms=0.0,
            policy_hash="a" * 64, audit_id="u",
        )

    def test_put_and_snapshot(self) -> None:
        store = CPStepOutcomeStore()
        o = self._make_outcome()
        store.put(o)
        assert store.snapshot() == [o]

    def test_put_many(self) -> None:
        store = CPStepOutcomeStore()
        outcomes = [self._make_outcome(f"s{i}") for i in range(5)]
        store.put_many(outcomes)
        assert len(store) == 5

    def test_snapshot_is_copy(self) -> None:
        store = CPStepOutcomeStore()
        store.put(self._make_outcome())
        snap1 = store.snapshot()
        store.put(self._make_outcome("s2"))
        snap2 = store.snapshot()
        assert len(snap1) == 1
        assert len(snap2) == 2

    def test_concurrent_put(self) -> None:
        store = CPStepOutcomeStore()
        outcomes = [self._make_outcome(f"s{i}") for i in range(100)]
        threads = [threading.Thread(target=store.put, args=(o,)) for o in outcomes]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(store) == 100


# ---------------------------------------------------------------------------
# EventIngestor
# ---------------------------------------------------------------------------


class TestEventIngestor:
    def test_ingest_single_event(self) -> None:
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)
        ingestor.ingest(_make_event())
        assert len(store) == 1
        assert ingestor.ingested_total == 1

    def test_batch_not_flushed_until_full(self) -> None:
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=3)
        ingestor.ingest(_make_event())
        ingestor.ingest(_make_event())
        assert len(store) == 0  # Not flushed yet
        ingestor.ingest(_make_event())
        assert len(store) == 3  # Flushed

    def test_flush_writes_partial_batch(self) -> None:
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=10)
        ingestor.ingest(_make_event())
        ingestor.ingest(_make_event())
        flushed = ingestor.flush()
        assert flushed == 2
        assert len(store) == 2

    def test_flush_empty_returns_zero(self) -> None:
        ingestor = EventIngestor(batch_size=0)
        assert ingestor.flush() == 0

    def test_ingest_many(self) -> None:
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)
        events = [_make_event() for _ in range(5)]
        ingestor.ingest_many(events)
        assert len(store) == 5
        assert ingestor.ingested_total == 5

    def test_on_event_ignores_missing_safety_event(self) -> None:
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)
        ingestor.on_event("step_completed", {"schema_version": 1})
        assert len(store) == 0

    def test_on_event_processes_safety_event(self) -> None:
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)
        event = _make_event()
        ingestor.on_event("step_completed", {"safety_event": event})
        assert len(store) == 1

    def test_error_counter_incremented_on_conversion_failure(self) -> None:
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)
        # Pass None as event -- will fail _convert_event
        ingestor.ingest(None)  # type: ignore[arg-type]
        assert ingestor.error_total == 1
        assert ingestor.ingested_total == 0

    def test_default_store_created_if_not_provided(self) -> None:
        ingestor = EventIngestor()
        assert isinstance(ingestor.store, CPStepOutcomeStore)

    def test_operation_name_override_propagates(self) -> None:
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0, operation_name="my-model")
        ingestor.ingest(_make_event())
        outcomes = store.snapshot()
        assert outcomes[0].operation_name == "my-model"


# ---------------------------------------------------------------------------
# Adversarial tests
# ---------------------------------------------------------------------------


class TestEventIngestorAdversarial:
    def test_concurrent_ingest_race(self) -> None:
        """100 threads ingesting concurrently must not lose events."""
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=10)
        events = [_make_event() for _ in range(100)]

        threads = [threading.Thread(target=ingestor.ingest, args=(e,)) for e in events]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        ingestor.flush()
        assert ingestor.ingested_total == 100
        assert len(store) == 100

    def test_concurrent_flush_is_safe(self) -> None:
        """Concurrent flush calls must not double-write."""
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=1000)
        for _ in range(50):
            ingestor.ingest(_make_event())

        results: list[int] = []

        def do_flush() -> None:
            results.append(ingestor.flush())

        threads = [threading.Thread(target=do_flush) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Only one flush should have written 50 items; others 0
        assert sum(results) == 50
        assert len(store) == 50

    def test_store_write_failure_does_not_raise(self) -> None:
        """If store.put_many raises, ingestor must not propagate."""
        class BrokenStore(CPStepOutcomeStore):
            def put_many(self, outcomes):  # type: ignore[override]
                raise RuntimeError("backend down")

        ingestor = EventIngestor(store=BrokenStore(), batch_size=0)
        ingestor.ingest(_make_event())  # Must not raise

    def test_event_with_null_ts_handled(self) -> None:
        """Event with ts=None must not crash conversion."""
        from veronica_core.shield.event import SafetyEvent
        event = SafetyEvent(
            event_type="test", decision=Decision.ALLOW,
            reason="r", hook="h", request_id="r1",
            ts=None,  # type: ignore[arg-type]
            metadata={"step_id": "s", "chain_id": "c"},
        )
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)
        before = time.time()
        ingestor.ingest(event)
        after = time.time()
        outcomes = store.snapshot()
        assert len(outcomes) == 1
        # Timestamp should fall back to time.time() within test window
        assert before <= outcomes[0].timestamp <= after + 1

    def test_batch_size_zero_flushes_immediately(self) -> None:
        """batch_size=0 means every event goes directly to store."""
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)
        for i in range(5):
            ingestor.ingest(_make_event())
            assert len(store) == i + 1  # Flushed after each ingest

    def test_negative_batch_size_treated_as_zero(self) -> None:
        """Negative batch_size must not crash."""
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=-5)
        ingestor.ingest(_make_event())
        assert len(store) == 1
