# tests/e2e/test_degraded.py
"""E2E test: degraded execution scenario.

Verifies full stack integration when the control plane falls back to a
degraded execution mode:
  - Stage timeout (TimeBudgetExceeded) -> DecisionMeta.degraded=True
  - fallback_model in PolicyConfig -> degraded=True, degrade_reason="fallback_model"
  - VeronicaOS emits step_completed with degraded=True and degrade_reason
  - EventIngestor and Prometheus both capture the degradation

Uses real veronica-core ExecutionContext.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone


from veronica_core.containment.execution_context import (
    ContextSnapshot,
    NodeRecord,
)
from veronica_core.shield.event import SafetyEvent

from veronica.buffered_emitter import BufferedEmitter
from veronica.distribution.policy_distributor import PolicyDistributor
from veronica.ingest.event_ingestor import CPStepOutcomeStore, EventIngestor
from veronica.os import VeronicaOS
from veronica.types import (
    AnalysisResult,
    BudgetState,
    CostEstimate,
    DesiredPolicy,
    PolicyConfig,
    StepIntent,
)


def _intent(
    chain_id: str = "e2e-degraded",
    step_id: str = "s1",
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


def _snapshot(chain_id: str, request_id: str, cost: float = 0.01) -> ContextSnapshot:
    node = NodeRecord(
        node_id="n1", parent_id=None, kind="llm",
        operation_name="llm_call",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc),
        status="ok", cost_usd=cost, retries_used=0,
    )
    return ContextSnapshot(
        chain_id=chain_id, request_id=request_id, step_count=1,
        cost_usd_accumulated=cost, retries_used=0,
        aborted=False, abort_reason=None,
        elapsed_ms=100.0, nodes=[node], events=[],
    )


class _SlowPlanner:
    """Planner that sleeps longer than the stage budget to trigger degradation."""

    def __init__(self, sleep_ms: float = 100.0) -> None:
        self._sleep_s = sleep_ms / 1000.0

    def plan(
        self,
        analysis: AnalysisResult | None,
        cost: CostEstimate,
        budget: BudgetState,
    ) -> DesiredPolicy:
        time.sleep(self._sleep_s)
        return DesiredPolicy(
            chain_id="e2e-degraded",
            ceiling_usd=1.0,
            ceiling_steps=100,
            ceiling_tokens_out=50_000,
            on_exceed="halt",
            fallback_model=None,
            timeout_ms=30_000,
            priority=50,
        )


class TestDegradedViaStageTimeout:
    def test_planner_timeout_triggers_degraded(self) -> None:
        """Planner exceeding stage budget sets degraded=True in DecisionMeta."""
        vos = VeronicaOS(
            planner=_SlowPlanner(sleep_ms=100.0),
            stage_budgets_ms={"planner": 1.0},
        )
        handle = vos.before_step(_intent())
        assert handle.decision_meta.degraded is True

    def test_degraded_payload_in_emitter(self) -> None:
        """step_completed event has degraded=True when stage timed out."""
        emitter = BufferedEmitter()
        vos = VeronicaOS(
            emitter=emitter,
            planner=_SlowPlanner(sleep_ms=100.0),
            stage_budgets_ms={"planner": 1.0},
        )
        intent = _intent()
        handle = vos.before_step(intent)
        vos.after_step(handle, _snapshot("e2e-degraded", "r1"))

        events = emitter.snapshot()
        assert len(events) == 1
        event_type, payload = events[0]
        assert event_type == "step_completed"
        assert payload["degraded"] is True

    def test_degrade_reason_is_time_budget(self) -> None:
        """degrade_reason='time_budget' when a stage times out."""
        emitter = BufferedEmitter()
        vos = VeronicaOS(
            emitter=emitter,
            planner=_SlowPlanner(sleep_ms=100.0),
            stage_budgets_ms={"planner": 1.0},
        )
        intent = _intent()
        handle = vos.before_step(intent)
        vos.after_step(handle, _snapshot("e2e-degraded", "r1"))

        _, payload = emitter.snapshot()[0]
        assert payload["degrade_reason"] == "time_budget"

    def test_policy_hash_present_on_degraded_step(self) -> None:
        """policy_hash is always present, even on degraded steps."""
        emitter = BufferedEmitter()
        vos = VeronicaOS(
            emitter=emitter,
            planner=_SlowPlanner(sleep_ms=100.0),
            stage_budgets_ms={"planner": 1.0},
        )
        intent = _intent()
        handle = vos.before_step(intent)
        vos.after_step(handle, _snapshot("e2e-degraded", "r1"))

        _, payload = emitter.snapshot()[0]
        assert "policy_hash" in payload
        assert len(payload["policy_hash"]) == 64

    def test_audit_id_present_on_degraded_step(self) -> None:
        """audit_id is always present, even on degraded steps."""
        emitter = BufferedEmitter()
        vos = VeronicaOS(
            emitter=emitter,
            planner=_SlowPlanner(sleep_ms=100.0),
            stage_budgets_ms={"planner": 1.0},
        )
        intent = _intent()
        handle = vos.before_step(intent)
        vos.after_step(handle, _snapshot("e2e-degraded", "r1"))

        _, payload = emitter.snapshot()[0]
        assert "audit_id" in payload
        assert len(payload["audit_id"]) == 32


class TestDegradedViaFallbackModel:
    def test_fallback_model_in_policy_degrade_reason(self) -> None:
        """degrade_reason='fallback_model' when policy has fallback_model and stage timed out.

        The degraded flag is set by TimeBudgetExceeded during a stage.
        Once degraded=True, _degrade_reason checks fallback_model first.
        """
        emitter = BufferedEmitter()
        vos = VeronicaOS(
            emitter=emitter,
            planner=_SlowPlanner(sleep_ms=100.0),  # triggers degraded=True
            stage_budgets_ms={"planner": 1.0},
        )

        # Inject fallback_model into the policy after the fact via a custom arbiter
        class FallbackArbiter:
            def arbitrate(self, desires, budget_remaining_usd):
                return {
                    d.chain_id: PolicyConfig(
                        chain_id=d.chain_id,
                        ceiling_usd=d.ceiling_usd,
                        on_exceed=d.on_exceed,
                        issued_at=time.time(),
                        fallback_model="gpt-3.5-turbo",
                    )
                    for d in desires
                }

        vos._arbiter = FallbackArbiter()
        intent = _intent(chain_id="e2e-fallback")
        handle = vos.before_step(intent)
        vos.after_step(handle, _snapshot("e2e-fallback", "r1"))

        _, payload = emitter.snapshot()[0]
        assert payload["degraded"] is True
        # fallback_model is checked before time_budget in _degrade_reason
        assert payload["degrade_reason"] == "fallback_model"
        assert payload["policy_hash"] is not None

    def test_distributor_preserves_fallback_model(self) -> None:
        """PolicyDistributor handles policies with fallback_model correctly."""
        policy = PolicyConfig(
            chain_id="e2e-fallback-dist",
            ceiling_usd=1.0,
            on_exceed="halt",
            issued_at=time.time(),
            fallback_model="gpt-3.5-turbo",
        )
        dist = PolicyDistributor()
        bundle = dist.distribute(policy)

        assert bundle.policy.fallback_model == "gpt-3.5-turbo"
        assert len(bundle.policy_hash) == 64


class TestDegradedEventIngest:
    def test_degraded_safety_event_stored_as_degrade(self) -> None:
        """SafetyEvent with DEGRADE decision is stored as 'degrade' in CP."""
        from veronica_core.shield.types import Decision as CoreDecision

        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)

        policy = PolicyConfig(
            chain_id="e2e-degrade-ingest",
            ceiling_usd=1.0,
            on_exceed="degrade",
            issued_at=time.time(),
        )
        dist = PolicyDistributor()
        bundle = dist.distribute(policy)

        degrade_event = SafetyEvent(
            event_type="execution_degraded",
            decision=CoreDecision.DEGRADE,
            reason="model downgraded to fallback due to budget pressure",
            hook="ShieldPipeline",
            request_id="req-degrade-1",
            ts=datetime.now(timezone.utc),
            metadata={
                "step_id": "step-degrade-1",
                "chain_id": policy.chain_id,
                "policy_hash": bundle.policy_hash,
                "degradation_tier": "model_downgrade",
            },
        )
        ingestor.ingest(degrade_event)

        records = store.snapshot()
        assert len(records) == 1
        outcome = records[0]
        assert outcome.decision == "degrade"
        assert outcome.chain_id == policy.chain_id
        assert outcome.policy_hash == bundle.policy_hash

    def test_degraded_event_has_unique_audit_id(self) -> None:
        """Each degraded event gets a unique audit_id."""
        from veronica_core.shield.types import Decision as CoreDecision

        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)

        def _make_event(i: int) -> SafetyEvent:
            return SafetyEvent(
                event_type="execution_degraded",
                decision=CoreDecision.DEGRADE,
                reason="degraded",
                hook="ShieldPipeline",
                request_id=f"req-{i}",
                ts=datetime.now(timezone.utc),
                metadata={"step_id": f"step-{i}", "chain_id": "e2e-audit"},
            )

        for i in range(5):
            ingestor.ingest(_make_event(i))

        records = store.snapshot()
        audit_ids = [r.audit_id for r in records]
        assert len(set(audit_ids)) == 5


class TestFullDegradedPipeline:
    def test_full_e2e_degraded_execution(self) -> None:
        """Full integration: stage timeout -> degraded=True in OS -> CP event.

        1. VeronicaOS with tight planner budget -> TimeBudgetExceeded
        2. DecisionMeta.degraded=True, degrade_reason='time_budget'
        3. step_completed event emitted with degraded=True, policy_hash, audit_id
        4. SafetyEvent (DEGRADE) ingested to CP store
        5. Prometheus captures degraded step
        """
        from prometheus_client import CollectorRegistry
        from veronica.metrics import PrometheusSubscriber

        reg = CollectorRegistry()
        prom = PrometheusSubscriber(prefix="degrade_e2e", registry=reg)
        emitter = BufferedEmitter()
        emitter.subscribe("prom", prom)

        vos = VeronicaOS(
            emitter=emitter,
            planner=_SlowPlanner(sleep_ms=100.0),
            stage_budgets_ms={"planner": 1.0},
        )
        intent = _intent(chain_id="e2e-full-degrade")
        handle = vos.before_step(intent)
        vos.after_step(handle, _snapshot("e2e-full-degrade", "r1"))

        # Verify OS-level degradation
        assert handle.decision_meta.degraded is True

        # Verify emitter payload
        events = emitter.snapshot()
        assert len(events) == 1
        _, payload = events[0]
        assert payload["degraded"] is True
        assert payload["degrade_reason"] == "time_budget"
        assert len(payload["policy_hash"]) == 64
        assert len(payload["audit_id"]) == 32

        # Verify Prometheus degrade counter (policy_hash removed from labels, log-only)
        degrade_val = reg.get_sample_value(
            "degrade_e2e_degrade_total",
            {"chain_id": "e2e-full-degrade"},
        )
        assert degrade_val == 1.0

        # Ingest a synthetic DEGRADE event to CP store
        from veronica_core.shield.types import Decision as CoreDecision

        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)

        dist = PolicyDistributor()
        bundle = dist.distribute(handle.policy)

        degrade_event = SafetyEvent(
            event_type="execution_degraded",
            decision=CoreDecision.DEGRADE,
            reason="time_budget: planner exceeded budget",
            hook="VeronicaOS",
            request_id=intent.request_id,
            ts=datetime.now(timezone.utc),
            metadata={
                "step_id": intent.step_id,
                "chain_id": intent.chain_id,
                "policy_hash": bundle.policy_hash,
                "degradation_tier": "time_budget",
            },
        )
        ingestor.ingest(degrade_event)

        records = store.snapshot()
        assert len(records) == 1
        outcome = records[0]
        assert outcome.decision == "degrade"
        assert outcome.policy_hash == bundle.policy_hash

    def test_non_degraded_step_no_degrade_counter(self) -> None:
        """Normal step does not increment degrade metrics."""
        from prometheus_client import CollectorRegistry
        from veronica.metrics import PrometheusSubscriber

        reg = CollectorRegistry()
        prom = PrometheusSubscriber(prefix="no_degrade_e2e", registry=reg)
        emitter = BufferedEmitter()
        emitter.subscribe("prom", prom)

        vos = VeronicaOS(emitter=emitter)
        intent = _intent(chain_id="e2e-no-degrade")
        handle = vos.before_step(intent)
        vos.after_step(handle, _snapshot("e2e-no-degrade", "r1"))

        _, payload = emitter.snapshot()[0]
        assert payload["degraded"] is False

        # degrade_total should not have been created for this chain
        policy_hash = payload["policy_hash"]
        degrade_val = reg.get_sample_value(
            "no_degrade_e2e_degrade_total",
            {"chain_id": "e2e-no-degrade", "policy_hash": policy_hash},
        )
        assert degrade_val is None  # not created
