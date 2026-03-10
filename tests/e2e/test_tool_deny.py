# tests/e2e/test_tool_deny.py
"""E2E test: tool deny scenario.

Verifies full stack integration when a tool call is denied:
  OrgPolicy(blocked_tools) -> VeronicaOS.before_step -> step_denied event
  -> PolicyConfig with zero ceiling -> ExecutionContext -> Decision.HALT
  -> CPStepOutcome stored with tool_name, deny reason, policy_hash

Uses real veronica-core ExecutionContext (not mocked).
Uses OrgPolicy to block specific tools.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from veronica_core.containment.execution_context import (
    ChainMetadata,
    ContextSnapshot,
    ExecutionContext,
    NodeRecord,
    WrapOptions,
)
from veronica_core.shield.event import SafetyEvent
from veronica_core.shield.types import Decision

from veronica.buffered_emitter import BufferedEmitter
from veronica.distribution.policy_distributor import PolicyDistributor
from veronica.ingest.event_ingestor import CPStepOutcomeStore, EventIngestor
from veronica.os import VeronicaOS
from veronica.schemas.events import StepOutcome as CPStepOutcome
from veronica.types import OrgPolicy, PolicyConfig, StepIntent


def _intent(
    tool_name: str,
    chain_id: str = "e2e-tool-deny",
    step_id: str = "s1",
    request_id: str = "r1",
) -> StepIntent:
    return StepIntent(
        step_id=step_id,
        request_id=request_id,
        chain_id=chain_id,
        kind="tool",
        model=None,
        tool_name=tool_name,
        timeout_ms=30_000,
        metadata={},
    )


def _snapshot(chain_id: str, request_id: str, cost: float = 0.0) -> ContextSnapshot:
    node = NodeRecord(
        node_id="n1", parent_id=None, kind="tool",
        operation_name="tool_call",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc),
        status="ok", cost_usd=cost, retries_used=0,
    )
    return ContextSnapshot(
        chain_id=chain_id, request_id=request_id, step_count=1,
        cost_usd_accumulated=cost, retries_used=0,
        aborted=False, abort_reason=None,
        elapsed_ms=10.0, nodes=[node], events=[],
    )


class TestToolDenyViaOrgPolicy:
    def test_org_policy_denies_blocked_tool(self) -> None:
        """OrgPolicy blocks tool -> step_denied event emitted."""
        org = OrgPolicy(blocked_tools=frozenset({"bash_tool", "code_interpreter"}))
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter, org_policy=org)

        handle = vos.before_step(_intent(tool_name="bash_tool"))

        events = emitter.snapshot()
        assert len(events) == 1
        event_type, payload = events[0]
        assert event_type == "step_denied"
        assert "bash_tool" in payload["reason"]

    def test_denied_step_handle_has_zero_ceiling(self) -> None:
        """Denied StepHandle has ceiling_usd=0.0 to prevent execution."""
        org = OrgPolicy(blocked_tools=frozenset({"bash_tool"}))
        vos = VeronicaOS(org_policy=org)

        handle = vos.before_step(_intent(tool_name="bash_tool"))
        assert handle.policy.ceiling_usd == 0.0

    def test_denied_step_handle_org_denial_set(self) -> None:
        """decision_meta.org_denial carries the deny reason."""
        org = OrgPolicy(blocked_tools=frozenset({"code_interpreter"}))
        vos = VeronicaOS(org_policy=org)

        handle = vos.before_step(_intent(tool_name="code_interpreter"))
        assert handle.decision_meta.org_denial is not None
        assert "code_interpreter" in handle.decision_meta.org_denial

    def test_allowed_tool_not_denied(self) -> None:
        """Non-blocked tool passes through without denial event."""
        org = OrgPolicy(blocked_tools=frozenset({"bash_tool"}))
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter, org_policy=org)

        handle = vos.before_step(_intent(tool_name="web_search"))
        vos.after_step(handle, _snapshot("e2e-tool-deny", "r1"))

        events = emitter.snapshot()
        assert len(events) == 1
        event_type, _ = events[0]
        assert event_type == "step_completed"

    def test_step_denied_payload_has_tool_name(self) -> None:
        """step_denied payload includes tool_name field."""
        org = OrgPolicy(blocked_tools=frozenset({"bash_tool"}))
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter, org_policy=org)

        vos.before_step(_intent(tool_name="bash_tool"))

        _, payload = emitter.snapshot()[0]
        assert payload.get("tool_name") == "bash_tool"

    def test_step_denied_payload_has_schema_version(self) -> None:
        """step_denied payload always has schema_version=1."""
        org = OrgPolicy(blocked_tools=frozenset({"bash_tool"}))
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter, org_policy=org)

        vos.before_step(_intent(tool_name="bash_tool"))

        _, payload = emitter.snapshot()[0]
        assert payload["schema_version"] == 1


class TestToolDenyKernelLevel:
    def test_kernel_halts_when_zero_ceiling(self) -> None:
        """ExecutionContext with max_cost=0 halts all tool calls."""
        from veronica_core.containment.execution_context import ExecutionConfig

        config = ExecutionConfig(
            max_cost_usd=0.0, max_steps=0, max_retries_total=0, timeout_ms=0,
        )
        metadata = ChainMetadata(request_id="r1", chain_id="c-kernel-halt")
        ctx = ExecutionContext(config=config, metadata=metadata)

        decision = ctx.wrap_tool_call(lambda: "tool_result")
        assert decision == Decision.HALT

    def test_kernel_halt_event_ingested_as_deny(self) -> None:
        """SafetyEvent with HALT decision is stored as 'halt' in CP StepOutcome."""
        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)

        policy = PolicyConfig(
            chain_id="e2e-kernel-deny",
            ceiling_usd=0.0,
            on_exceed="halt",
            issued_at=time.time(),
        )
        distributor = PolicyDistributor()
        bundle = distributor.distribute(policy)

        halt_event = SafetyEvent(
            event_type="tool_call_halted",
            decision=Decision.HALT,
            reason="tool call denied: zero budget ceiling",
            hook="ExecutionContext",
            request_id="req-tool-deny-1",
            ts=datetime.now(timezone.utc),
            metadata={
                "step_id": "step-tool-1",
                "chain_id": policy.chain_id,
                "policy_hash": bundle.policy_hash,
                "tool_name": "bash_tool",
            },
        )
        ingestor.ingest(halt_event)

        records = store.snapshot()
        assert len(records) == 1
        outcome = records[0]
        assert outcome.decision == "halt"
        assert outcome.chain_id == policy.chain_id
        assert outcome.policy_hash == bundle.policy_hash


class TestFullToolDenyPipeline:
    def test_full_e2e_tool_deny_pipeline(self) -> None:
        """Full integration: org policy deny -> kernel halt -> CP ingest -> verify hash chain.

        1. OrgPolicy blocks 'bash_tool'
        2. VeronicaOS.before_step detects denial, emits step_denied
        3. Denied policy has ceiling_usd=0 -> kernel returns HALT for tool call
        4. EventIngestor captures the SafetyEvent with policy_hash
        5. CPStepOutcome records deny reason and policy_hash
        """
        org = OrgPolicy(blocked_tools=frozenset({"bash_tool"}))
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter, org_policy=org)

        # Step 1: OS layer denial
        intent = _intent(tool_name="bash_tool", chain_id="e2e-full-tool-deny")
        handle = vos.before_step(intent)

        assert handle.decision_meta.org_denial is not None
        assert handle.policy.ceiling_usd == 0.0

        # Step 2: Kernel execution with denied policy (zero ceiling)
        from veronica_core.containment.execution_context import ExecutionConfig

        exec_config = handle.policy.to_exec_config()
        assert exec_config.max_cost_usd == 0.0

        metadata = ChainMetadata(request_id=intent.request_id, chain_id=intent.chain_id)
        ctx = ExecutionContext(config=exec_config, metadata=metadata)
        decision = ctx.wrap_tool_call(lambda: "tool_result")
        assert decision == Decision.HALT

        # Step 3: CP event emitted
        events = emitter.snapshot()
        assert len(events) == 1
        event_type, payload = events[0]
        assert event_type == "step_denied"
        assert payload["reason"] is not None

        # Step 4: Ingest kernel HALT event to CP store
        distributor = PolicyDistributor()
        bundle = distributor.distribute(handle.policy)

        store = CPStepOutcomeStore()
        ingestor = EventIngestor(store=store, batch_size=0)

        kernel_halt = SafetyEvent(
            event_type="tool_call_halted",
            decision=Decision.HALT,
            reason=handle.decision_meta.org_denial,
            hook="ExecutionContext",
            request_id=intent.request_id,
            ts=datetime.now(timezone.utc),
            metadata={
                "step_id": intent.step_id,
                "chain_id": intent.chain_id,
                "policy_hash": bundle.policy_hash,
                "tool_name": intent.tool_name,
            },
        )
        ingestor.ingest(kernel_halt)

        # Step 5: Verify CP record
        records = store.snapshot()
        assert len(records) == 1
        outcome = records[0]

        assert outcome.decision == "halt"
        assert outcome.chain_id == intent.chain_id
        assert outcome.policy_hash == bundle.policy_hash
        assert outcome.step_id == intent.step_id

    def test_tool_deny_with_policy_hash_label_in_metrics(self) -> None:
        """Denied tool step tracked in Prometheus with correct chain_id."""
        from prometheus_client import CollectorRegistry
        from veronica.metrics import PrometheusSubscriber

        org = OrgPolicy(blocked_tools=frozenset({"bash_tool"}))
        reg = CollectorRegistry()
        prom = PrometheusSubscriber(prefix="tool_deny_test", registry=reg)
        emitter = BufferedEmitter()
        emitter.subscribe("prom", prom)

        vos = VeronicaOS(emitter=emitter, org_policy=org)
        vos.before_step(_intent(tool_name="bash_tool", chain_id="deny-chain"))

        val = reg.get_sample_value("tool_deny_test_active_chains")
        assert val == 1.0  # deny-chain was seen

    def test_multiple_denied_tools_tracked_independently(self) -> None:
        """Multiple distinct blocked tools generate separate step_denied events."""
        org = OrgPolicy(blocked_tools=frozenset({"bash_tool", "code_interpreter"}))
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter, org_policy=org)

        vos.before_step(_intent("bash_tool", step_id="s1"))
        vos.before_step(_intent("code_interpreter", step_id="s2"))
        vos.before_step(_intent("web_search", step_id="s3"))  # allowed -- no event

        events = emitter.snapshot()
        denied = [et for et, _ in events if et == "step_denied"]
        assert len(denied) == 2

    def test_case_insensitive_tool_deny(self) -> None:
        """OrgPolicy matches tool names case-insensitively."""
        org = OrgPolicy(blocked_tools=frozenset({"Bash_Tool"}))
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter, org_policy=org)

        handle = vos.before_step(_intent(tool_name="bash_tool"))

        assert handle.decision_meta.org_denial is not None
