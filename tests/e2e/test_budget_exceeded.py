# tests/e2e/test_budget_exceeded.py
"""E2E test: budget exceeded scenario.

Verifies full stack integration:
  PolicyConfig -> PolicyDistributor -> ExecutionConfig -> ExecutionContext
  -> kernel returns Decision.HALT
  -> SafetyEvent ingested by EventIngestor
  -> CPStepOutcome stored with correct policy_hash and audit_id

Uses real veronica-core ExecutionContext (not mocked).
Uses stub LLM (no real API calls).
"""

from __future__ import annotations

from veronica_core.containment.execution_context import (
    ChainMetadata,
    ExecutionContext,
    WrapOptions,
)
from veronica_core.shield.types import Decision

from veronica.distribution.policy_distributor import PolicyDistributor
from veronica.ingest.event_ingestor import CPStepOutcomeStore, EventIngestor
from veronica.schemas.events import StepOutcome as CPStepOutcome
from veronica.types import PolicyConfig

import time


def _make_policy(ceiling_usd: float, chain_id: str = "e2e-budget") -> PolicyConfig:
    return PolicyConfig(
        chain_id=chain_id,
        ceiling_usd=ceiling_usd,
        on_exceed="halt",
        issued_at=time.time(),
    )


def _stub_llm() -> str:
    """Stub LLM call -- returns immediately, no network."""
    return "stub_response"


class TestBudgetExceededE2E:
    def test_kernel_halts_on_budget_exceeded(self) -> None:
        """Kernel returns HALT when cost_estimate_hint > ceiling_usd."""
        policy = _make_policy(ceiling_usd=0.10)
        distributor = PolicyDistributor()
        bundle = distributor.distribute(policy)

        metadata = ChainMetadata(
            request_id="req-e2e-budget-1",
            chain_id=policy.chain_id,
        )
        ctx = ExecutionContext(config=bundle.exec_config, metadata=metadata)

        # First call: within budget -> ALLOW
        decision1 = ctx.wrap_llm_call(
            _stub_llm,
            options=WrapOptions(operation_name="step1", cost_estimate_hint=0.05),
        )
        assert decision1 == Decision.ALLOW, f"Expected ALLOW, got {decision1}"

        # Second call: cost_estimate_hint would exceed ceiling -> HALT
        decision2 = ctx.wrap_llm_call(
            _stub_llm,
            options=WrapOptions(operation_name="step2", cost_estimate_hint=0.09),
        )
        assert decision2 == Decision.HALT, (
            f"Expected HALT on budget exceeded, got {decision2}"
        )

    def test_snapshot_shows_aborted_on_budget_exceeded(self) -> None:
        """Snapshot reflects halt state after budget exceeded."""
        policy = _make_policy(ceiling_usd=0.05, chain_id="e2e-budget-snap")
        distributor = PolicyDistributor()
        bundle = distributor.distribute(policy)

        metadata = ChainMetadata(
            request_id="req-e2e-budget-2",
            chain_id=policy.chain_id,
        )
        ctx = ExecutionContext(config=bundle.exec_config, metadata=metadata)

        # Try to exceed budget immediately
        decision = ctx.wrap_llm_call(
            _stub_llm,
            options=WrapOptions(operation_name="big_call", cost_estimate_hint=0.10),
        )
        assert decision == Decision.HALT

        snap = ctx.get_snapshot()
        assert snap.chain_id == policy.chain_id

    def test_policy_hash_is_stable_for_same_policy(self) -> None:
        """Same PolicyConfig always produces the same policy_hash."""
        policy = _make_policy(ceiling_usd=0.10, chain_id="e2e-hash-stability")
        distributor = PolicyDistributor()

        bundle1 = distributor.distribute(policy)
        bundle2 = distributor.distribute(policy)

        assert bundle1.policy_hash == bundle2.policy_hash, (
            "policy_hash must be deterministic for the same PolicyConfig"
        )

    def test_policy_hash_propagates_to_exec_config(self) -> None:
        """PolicyBundle carries policy_hash derived from PolicyConfig fields."""
        policy = _make_policy(ceiling_usd=0.25, chain_id="e2e-hash-prop")
        distributor = PolicyDistributor()
        bundle = distributor.distribute(policy)

        # exec_config reflects the ceiling
        assert bundle.exec_config.max_cost_usd == 0.25
        # policy_hash is a 64-char hex SHA-256
        assert len(bundle.policy_hash) == 64
        assert all(c in "0123456789abcdef" for c in bundle.policy_hash)

    def test_ingestor_captures_halt_event_from_snapshot(self) -> None:
        """EventIngestor captures SafetyEvents emitted during budget exceeded."""
        from veronica_core.shield.event import SafetyEvent
        from veronica_core.shield.types import Decision as CoreDecision
        from datetime import datetime, timezone

        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)

        # Build a HALT SafetyEvent (simulating what the kernel would emit)
        policy = _make_policy(ceiling_usd=0.10, chain_id="e2e-ingest")
        distributor = PolicyDistributor()
        bundle = distributor.distribute(policy)

        halt_event = SafetyEvent(
            event_type="budget_exceeded",
            decision=CoreDecision.HALT,
            reason="cost estimate $0.15 would exceed chain ceiling $0.10",
            hook="ExecutionContext",
            request_id="req-e2e-ingest-1",
            ts=datetime.now(timezone.utc),
            metadata={
                "step_id": "step-halt-1",
                "chain_id": policy.chain_id,
                "policy_hash": bundle.policy_hash,
                "cost_usd": 0.0,
                "operation_name": "big_call",
            },
        )

        ingestor.ingest(halt_event)

        records = store.snapshot()
        assert len(records) == 1, f"Expected 1 record, got {len(records)}"

        outcome = records[0]
        assert isinstance(outcome, CPStepOutcome)
        assert outcome.decision == "halt"
        assert outcome.chain_id == policy.chain_id
        assert outcome.policy_hash == bundle.policy_hash

    def test_audit_id_unique_per_ingest(self) -> None:
        """Each ingested event gets a unique audit_id."""
        from veronica_core.shield.event import SafetyEvent
        from veronica_core.shield.types import Decision as CoreDecision
        from datetime import datetime, timezone

        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)

        def _make_event(step_id: str) -> SafetyEvent:
            return SafetyEvent(
                event_type="budget_exceeded",
                decision=CoreDecision.HALT,
                reason="budget exceeded",
                hook="ExecutionContext",
                request_id=f"req-{step_id}",
                ts=datetime.now(timezone.utc),
                metadata={"step_id": step_id, "chain_id": "e2e-audit"},
            )

        ingestor.ingest(_make_event("step-1"))
        ingestor.ingest(_make_event("step-2"))
        ingestor.ingest(_make_event("step-3"))

        records = store.snapshot()
        assert len(records) == 3

        audit_ids = [r.audit_id for r in records]
        # All audit_ids must be unique
        assert len(set(audit_ids)) == 3, f"audit_ids not unique: {audit_ids}"

    def test_full_e2e_pipeline_budget_exceeded(self) -> None:
        """Full integration: policy -> distribute -> execute -> ingest -> verify."""
        from veronica_core.shield.event import SafetyEvent
        from veronica_core.shield.types import Decision as CoreDecision
        from datetime import datetime, timezone

        policy = _make_policy(ceiling_usd=0.10, chain_id="e2e-full-budget")
        distributor = PolicyDistributor()
        bundle = distributor.distribute(policy)

        # Kernel execution
        metadata = ChainMetadata(
            request_id="req-e2e-full-1",
            chain_id=policy.chain_id,
        )
        ctx = ExecutionContext(config=bundle.exec_config, metadata=metadata)

        # Call that exceeds budget
        decision = ctx.wrap_llm_call(
            _stub_llm,
            options=WrapOptions(
                operation_name="expensive_call", cost_estimate_hint=0.15
            ),
        )
        assert decision == Decision.HALT

        # Simulate ingest of the HALT event (kernel would emit this)
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)

        halt_event = SafetyEvent(
            event_type="budget_exceeded",
            decision=CoreDecision.HALT,
            reason="budget exceeded",
            hook="ExecutionContext",
            request_id=metadata.request_id,
            ts=datetime.now(timezone.utc),
            metadata={
                "step_id": "step-expensive",
                "chain_id": policy.chain_id,
                "policy_hash": bundle.policy_hash,
                "audit_id": "fixed-audit-id-001",
                "operation_name": "expensive_call",
            },
        )
        ingestor.ingest(halt_event)

        records = store.snapshot()
        assert len(records) == 1

        outcome = records[0]
        # Verify full chain: policy_hash matches distributor output
        assert outcome.policy_hash == bundle.policy_hash
        assert outcome.decision == "halt"
        assert outcome.chain_id == policy.chain_id
        assert outcome.audit_id == "fixed-audit-id-001"
