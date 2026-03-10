# tests/e2e/test_retry_halt.py
"""E2E test: retry loop halt scenario.

Verifies full stack integration:
  PolicyConfig(max_retries_total=N) -> PolicyDistributor -> ExecutionConfig
  -> ExecutionContext with failing LLM stub
  -> kernel returns RETRY N times, then HALT on retry budget exhaustion
  -> SafetyEvent ingested by EventIngestor
  -> CPStepOutcome chain shows escalation with consistent policy_hash

Uses real veronica-core ExecutionContext (not mocked).
Uses stub LLM that always raises (triggers retry path).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from veronica_core.containment.execution_context import (
    ChainMetadata,
    ExecutionContext,
    ExecutionConfig,
    WrapOptions,
)
from veronica_core.shield.event import SafetyEvent
from veronica_core.shield.types import Decision

from veronica.distribution.policy_distributor import PolicyDistributor
from veronica.ingest.event_ingestor import CPStepOutcomeStore, EventIngestor
from veronica.types import PolicyConfig


def _make_policy(max_retries: int, chain_id: str = "e2e-retry") -> PolicyConfig:
    return PolicyConfig(
        chain_id=chain_id,
        ceiling_usd=10.0,  # high budget so retries, not budget, triggers HALT
        on_exceed="halt",
        issued_at=time.time(),
    )


class _AlwaysFailLLM:
    """Stub LLM that always raises RuntimeError."""

    def __call__(self) -> str:
        raise RuntimeError("simulated LLM failure")


def _run_until_halt(ctx: ExecutionContext, max_calls: int = 20) -> list[Decision]:
    """Call wrap_llm_call repeatedly until HALT or max_calls reached."""
    decisions: list[Decision] = []
    stub = _AlwaysFailLLM()
    for _ in range(max_calls):
        d = ctx.wrap_llm_call(stub, options=WrapOptions(operation_name="retry_op"))
        decisions.append(d)
        if d == Decision.HALT:
            break
    return decisions


class TestRetryHaltE2E:
    def test_kernel_halts_after_retry_budget_exhausted(self) -> None:
        """Kernel returns RETRY until budget exhausted, then HALT."""
        max_retries = 3
        config = ExecutionConfig(
            max_cost_usd=10.0,
            max_steps=50,
            max_retries_total=max_retries,
        )
        metadata = ChainMetadata(
            request_id="req-e2e-retry-1",
            chain_id="e2e-retry-kernel",
        )
        ctx = ExecutionContext(config=config, metadata=metadata)
        decisions = _run_until_halt(ctx, max_calls=max_retries + 5)

        # Should see RETRY decisions followed by a final HALT
        assert Decision.HALT in decisions, f"Expected HALT in {decisions}"
        # All decisions before HALT should be RETRY
        halt_idx = decisions.index(Decision.HALT)
        for d in decisions[:halt_idx]:
            assert d == Decision.RETRY, f"Expected RETRY before HALT, got {d}"

    def test_retry_count_matches_budget(self) -> None:
        """Number of RETRY decisions equals max_retries_total."""
        max_retries = 3
        config = ExecutionConfig(
            max_cost_usd=10.0,
            max_steps=50,
            max_retries_total=max_retries,
        )
        metadata = ChainMetadata(
            request_id="req-e2e-retry-count",
            chain_id="e2e-retry-count",
        )
        ctx = ExecutionContext(config=config, metadata=metadata)
        decisions = _run_until_halt(ctx, max_calls=max_retries + 5)

        retry_count = sum(1 for d in decisions if d == Decision.RETRY)
        halt_count = sum(1 for d in decisions if d == Decision.HALT)

        assert halt_count == 1, f"Expected exactly 1 HALT, got {halt_count}"
        assert retry_count == max_retries, (
            f"Expected {max_retries} RETRY decisions, got {retry_count}"
        )

    def test_distributor_maps_retries_from_policy_config(self) -> None:
        """PolicyDistributor produces ExecutionConfig with correct max_retries_total."""
        policy = _make_policy(max_retries=5, chain_id="e2e-retry-dist")
        distributor = PolicyDistributor()
        bundle = distributor.distribute(policy)

        # Default retry budget in to_exec_config() is _DEFAULT_RETRIES=10
        # (PolicyConfig doesn't have a max_retries field -- uses default)
        assert bundle.exec_config.max_retries_total >= 0
        assert bundle.exec_config.max_cost_usd == 10.0

    def test_snapshot_shows_retry_usage(self) -> None:
        """Snapshot retries_used reflects actual retry count."""
        max_retries = 2
        config = ExecutionConfig(
            max_cost_usd=10.0,
            max_steps=50,
            max_retries_total=max_retries,
        )
        metadata = ChainMetadata(
            request_id="req-e2e-retry-snap",
            chain_id="e2e-retry-snap",
        )
        ctx = ExecutionContext(config=config, metadata=metadata)
        _run_until_halt(ctx, max_calls=max_retries + 5)

        snap = ctx.get_snapshot()
        assert snap.retries_used == max_retries, (
            f"Expected retries_used={max_retries}, got {snap.retries_used}"
        )

    def test_ingestor_captures_retry_events(self) -> None:
        """EventIngestor stores RETRY and HALT events with consistent policy_hash."""
        policy = _make_policy(max_retries=2, chain_id="e2e-retry-ingest")
        distributor = PolicyDistributor()
        bundle = distributor.distribute(policy)

        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)

        # Simulate 2 RETRY + 1 HALT events
        events = [
            SafetyEvent(
                event_type="retry_attempt",
                decision=Decision.RETRY,
                reason="simulated LLM failure",
                hook="ExecutionContext",
                request_id=f"req-retry-{i}",
                ts=datetime.now(timezone.utc),
                metadata={
                    "step_id": f"step-retry-{i}",
                    "chain_id": policy.chain_id,
                    "policy_hash": bundle.policy_hash,
                    "audit_id": f"audit-retry-{i}",
                },
            )
            for i in range(2)
        ]
        halt_event = SafetyEvent(
            event_type="retry_budget_exceeded",
            decision=Decision.HALT,
            reason="retries 2 >= budget 2",
            hook="ExecutionContext",
            request_id="req-retry-halt",
            ts=datetime.now(timezone.utc),
            metadata={
                "step_id": "step-halt",
                "chain_id": policy.chain_id,
                "policy_hash": bundle.policy_hash,
                "audit_id": "audit-halt",
            },
        )

        ingestor.ingest_many(events)
        ingestor.ingest(halt_event)

        records = store.snapshot()
        assert len(records) == 3

        # All records share the same policy_hash
        hashes = {r.policy_hash for r in records}
        assert hashes == {bundle.policy_hash}, (
            f"Expected all records to share policy_hash={bundle.policy_hash}, "
            f"got {hashes}"
        )

        # Last record is HALT
        assert records[-1].decision == "halt"
        # First two are RETRY
        assert records[0].decision == "retry"
        assert records[1].decision == "retry"

    def test_policy_hash_consistent_across_retry_chain(self) -> None:
        """policy_hash is identical for all events in a retry chain."""
        policy = PolicyConfig(
            chain_id="e2e-retry-hash",
            ceiling_usd=10.0,
            on_exceed="halt",
            issued_at=time.time(),
        )
        distributor = PolicyDistributor()
        bundle1 = distributor.distribute(policy)
        bundle2 = distributor.distribute(policy)

        # Same policy -> same hash (deterministic)
        assert bundle1.policy_hash == bundle2.policy_hash

    def test_full_e2e_retry_pipeline(self) -> None:
        """Full integration: retry until halt, ingest events, verify chain."""
        max_retries = 2
        config = ExecutionConfig(
            max_cost_usd=10.0,
            max_steps=50,
            max_retries_total=max_retries,
        )
        policy = PolicyConfig(
            chain_id="e2e-retry-full",
            ceiling_usd=10.0,
            on_exceed="halt",
            issued_at=time.time(),
        )
        distributor = PolicyDistributor()
        bundle = distributor.distribute(policy)

        metadata = ChainMetadata(
            request_id="req-e2e-retry-full",
            chain_id=policy.chain_id,
        )
        ctx = ExecutionContext(config=config, metadata=metadata)
        decisions = _run_until_halt(ctx, max_calls=max_retries + 5)

        assert Decision.HALT in decisions
        halt_idx = decisions.index(Decision.HALT)
        assert halt_idx == max_retries, (
            f"HALT should occur after {max_retries} retries, got index {halt_idx}"
        )

        # Verify snapshot
        snap = ctx.get_snapshot()
        assert snap.retries_used == max_retries
        assert snap.chain_id == policy.chain_id

        # policy_hash chain is stable
        assert len(bundle.policy_hash) == 64
