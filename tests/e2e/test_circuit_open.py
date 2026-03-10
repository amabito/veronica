# tests/e2e/test_circuit_open.py
"""E2E test: circuit breaker open scenario.

Verifies full stack integration:
  PolicyConfig -> PolicyDistributor -> ExecutionConfig
  -> ExecutionContext + CircuitBreaker(failure_threshold=N)
  -> N failures trip the circuit -> OPEN state
  -> Next call returns HALT (circuit OPEN)
  -> Control plane captures circuit transition events
  -> StepOutcome has policy_hash

Uses real veronica-core ExecutionContext + CircuitBreaker (not mocked).
Uses stub LLM that always raises (to record failures).
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from veronica_core.circuit_breaker import CircuitBreaker, CircuitState
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


def _make_policy(chain_id: str = "e2e-circuit") -> PolicyConfig:
    return PolicyConfig(
        chain_id=chain_id,
        ceiling_usd=100.0,  # high budget -- circuit, not budget, triggers HALT
        on_exceed="halt",
        issued_at=time.time(),
    )


class _AlwaysFailLLM:
    """Stub LLM that always raises RuntimeError."""

    def __call__(self) -> str:
        raise RuntimeError("simulated LLM failure for circuit breaker")


class TestCircuitOpenE2E:
    def test_circuit_opens_after_threshold_failures(self) -> None:
        """Circuit transitions CLOSED -> OPEN after failure_threshold failures."""
        failure_threshold = 2
        breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=60.0,
        )
        config = ExecutionConfig(
            max_cost_usd=100.0,
            max_steps=50,
            max_retries_total=100,  # large -- retries won't block circuit test
        )
        metadata = ChainMetadata(
            request_id="req-e2e-circuit-1",
            chain_id="e2e-circuit-open",
        )
        ctx = ExecutionContext(
            config=config,
            metadata=metadata,
            circuit_breaker=breaker,
        )

        stub = _AlwaysFailLLM()

        # Record failures to trip the circuit
        for _ in range(failure_threshold):
            d = ctx.wrap_llm_call(stub, options=WrapOptions(operation_name="failing_call"))
            assert d == Decision.RETRY, f"Expected RETRY while recording failures, got {d}"

        # Circuit should now be OPEN
        assert breaker.state == CircuitState.OPEN, (
            f"Expected circuit OPEN after {failure_threshold} failures, got {breaker.state}"
        )

    def test_call_halts_when_circuit_is_open(self) -> None:
        """wrap_llm_call returns HALT when circuit is OPEN."""
        failure_threshold = 2
        breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=60.0,
        )
        config = ExecutionConfig(
            max_cost_usd=100.0,
            max_steps=50,
            max_retries_total=100,
        )
        metadata = ChainMetadata(
            request_id="req-e2e-circuit-halt",
            chain_id="e2e-circuit-halt",
        )
        ctx = ExecutionContext(
            config=config,
            metadata=metadata,
            circuit_breaker=breaker,
        )

        stub = _AlwaysFailLLM()

        # Trip the circuit
        for _ in range(failure_threshold):
            ctx.wrap_llm_call(stub, options=WrapOptions(operation_name="fail"))

        assert breaker.state == CircuitState.OPEN

        # Next call: circuit OPEN -> HALT (fn should not be called)
        call_count = 0
        def _counted_stub() -> str:
            nonlocal call_count
            call_count += 1
            return "should_not_reach_here"

        decision = ctx.wrap_llm_call(_counted_stub, options=WrapOptions(operation_name="blocked_call"))
        assert decision == Decision.HALT, f"Expected HALT when circuit OPEN, got {decision}"
        assert call_count == 0, "fn should not be called when circuit is OPEN"

    def test_policy_hash_present_in_circuit_events(self) -> None:
        """EventIngestor captures circuit OPEN events with policy_hash."""
        policy = _make_policy(chain_id="e2e-circuit-hash")
        distributor = PolicyDistributor()
        bundle = distributor.distribute(policy)

        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)

        # Simulate circuit OPEN event from kernel
        circuit_event = SafetyEvent(
            event_type="circuit_open",
            decision=Decision.HALT,
            reason="Circuit OPEN: 2 consecutive failures",
            hook="ExecutionContext",
            request_id="req-circuit-open",
            ts=datetime.now(timezone.utc),
            metadata={
                "step_id": "step-circuit-blocked",
                "chain_id": policy.chain_id,
                "policy_hash": bundle.policy_hash,
                "audit_id": "audit-circuit-001",
                "circuit_state": "OPEN",
            },
        )
        ingestor.ingest(circuit_event)

        records = store.snapshot()
        assert len(records) == 1
        outcome = records[0]

        assert outcome.decision == "halt"
        assert outcome.policy_hash == bundle.policy_hash
        assert outcome.chain_id == policy.chain_id

    def test_circuit_state_accessible_after_failure(self) -> None:
        """CircuitBreaker state is OPEN and failure_count equals threshold."""
        failure_threshold = 3
        breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=60.0,
        )
        config = ExecutionConfig(
            max_cost_usd=100.0,
            max_steps=50,
            max_retries_total=100,
        )
        metadata = ChainMetadata(
            request_id="req-e2e-circuit-state",
            chain_id="e2e-circuit-state",
        )
        ctx = ExecutionContext(
            config=config,
            metadata=metadata,
            circuit_breaker=breaker,
        )

        stub = _AlwaysFailLLM()
        for _ in range(failure_threshold):
            ctx.wrap_llm_call(stub, options=WrapOptions(operation_name="fail"))

        assert breaker.state == CircuitState.OPEN
        assert breaker.failure_count == failure_threshold

    def test_distributor_policy_hash_stable_for_circuit_policy(self) -> None:
        """policy_hash is deterministic for a fixed PolicyConfig."""
        policy = _make_policy(chain_id="e2e-circuit-hash-stability")
        distributor = PolicyDistributor()

        bundle1 = distributor.distribute(policy)
        bundle2 = distributor.distribute(policy)

        assert bundle1.policy_hash == bundle2.policy_hash
        assert len(bundle1.policy_hash) == 64

    def test_ingestor_captures_failure_progression_events(self) -> None:
        """EventIngestor handles multiple RETRY events then HALT (circuit open)."""
        policy = _make_policy(chain_id="e2e-circuit-progression")
        distributor = PolicyDistributor()
        bundle = distributor.distribute(policy)

        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)

        # Simulate: 2 RETRY (failures recorded) then 1 HALT (circuit open)
        for i in range(2):
            ev = SafetyEvent(
                event_type="llm_call_failed",
                decision=Decision.RETRY,
                reason="simulated failure",
                hook="ExecutionContext",
                request_id=f"req-fail-{i}",
                ts=datetime.now(timezone.utc),
                metadata={
                    "step_id": f"step-fail-{i}",
                    "chain_id": policy.chain_id,
                    "policy_hash": bundle.policy_hash,
                    "audit_id": f"audit-fail-{i}",
                },
            )
            ingestor.ingest(ev)

        halt_ev = SafetyEvent(
            event_type="circuit_open",
            decision=Decision.HALT,
            reason="Circuit OPEN: 2 consecutive failures",
            hook="ExecutionContext",
            request_id="req-circuit-halt",
            ts=datetime.now(timezone.utc),
            metadata={
                "step_id": "step-circuit-halt",
                "chain_id": policy.chain_id,
                "policy_hash": bundle.policy_hash,
                "audit_id": "audit-circuit-halt",
            },
        )
        ingestor.ingest(halt_ev)

        records = store.snapshot()
        assert len(records) == 3

        # First 2: retry
        assert records[0].decision == "retry"
        assert records[1].decision == "retry"
        # Last: halt
        assert records[2].decision == "halt"

        # All share same policy_hash
        hashes = {r.policy_hash for r in records}
        assert hashes == {bundle.policy_hash}

    def test_full_e2e_circuit_breaker_pipeline(self) -> None:
        """Full integration: trip circuit -> HALT -> ingest events -> verify."""
        failure_threshold = 2
        policy = _make_policy(chain_id="e2e-circuit-full")
        distributor = PolicyDistributor()
        bundle = distributor.distribute(policy)

        breaker = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=60.0,
        )
        config = ExecutionConfig(
            max_cost_usd=100.0,
            max_steps=50,
            max_retries_total=100,
        )
        metadata = ChainMetadata(
            request_id="req-e2e-circuit-full",
            chain_id=policy.chain_id,
        )
        ctx = ExecutionContext(
            config=config,
            metadata=metadata,
            circuit_breaker=breaker,
        )

        stub = _AlwaysFailLLM()
        # Trip the circuit
        for _ in range(failure_threshold):
            ctx.wrap_llm_call(stub, options=WrapOptions(operation_name="failing_op"))

        # Verify circuit is OPEN
        assert breaker.state == CircuitState.OPEN

        # Next call must HALT
        decision = ctx.wrap_llm_call(
            lambda: "unreachable",
            options=WrapOptions(operation_name="blocked_op"),
        )
        assert decision == Decision.HALT

        # Ingest the HALT event
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)

        halt_event = SafetyEvent(
            event_type="circuit_open",
            decision=Decision.HALT,
            reason=f"Circuit OPEN: {failure_threshold} consecutive failures",
            hook="ExecutionContext",
            request_id=metadata.request_id,
            ts=datetime.now(timezone.utc),
            metadata={
                "step_id": "step-blocked",
                "chain_id": policy.chain_id,
                "policy_hash": bundle.policy_hash,
                "audit_id": "audit-e2e-circuit-full",
                "circuit_state": "OPEN",
                "failure_count": failure_threshold,
            },
        )
        ingestor.ingest(halt_event)

        records = store.snapshot()
        assert len(records) == 1
        outcome = records[0]

        # Verify full audit chain
        assert outcome.policy_hash == bundle.policy_hash
        assert outcome.decision == "halt"
        assert outcome.chain_id == policy.chain_id
        assert outcome.audit_id == "audit-e2e-circuit-full"
