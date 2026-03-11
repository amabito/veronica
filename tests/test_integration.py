# tests/test_integration.py
"""Integration test: full VeronicaOS pipeline with veronica-core types."""

from __future__ import annotations

from datetime import datetime, timezone

from veronica_core.containment.execution_context import (
    ContextSnapshot,
    ExecutionConfig,
    NodeRecord,
)

from veronica import VeronicaOS
from veronica.store import MemoryStore
from veronica.types import StepIntent


def test_full_pipeline_three_steps() -> None:
    """Simulate 3 LLM calls with feedback loop."""
    store = MemoryStore()
    vos = VeronicaOS(store=store)

    ceilings: list[float] = []

    for i in range(3):
        intent = StepIntent(
            step_id=f"s{i}",
            request_id="r1",
            chain_id="c1",
            kind="llm",
            model="gpt-4",
            tool_name=None,
            timeout_ms=30_000,
            metadata={},
        )

        # before_step
        handle = vos.before_step(intent)
        ceilings.append(handle.policy.ceiling_usd)

        # Verify bridge to veronica-core
        ec = handle.policy.to_exec_config()
        assert isinstance(ec, ExecutionConfig)
        assert ec.max_cost_usd == handle.policy.ceiling_usd

        # Simulate execution (mock snapshot)
        node = NodeRecord(
            node_id=f"n{i}",
            parent_id=None,
            kind="llm",
            operation_name=f"step_{i}",
            start_ts=datetime.now(timezone.utc),
            end_ts=datetime.now(timezone.utc),
            status="ok",
            cost_usd=0.005,
            retries_used=0,
        )
        snapshot = ContextSnapshot(
            chain_id="c1",
            request_id="r1",
            step_count=i + 1,
            cost_usd_accumulated=0.005 * (i + 1),
            retries_used=0,
            aborted=False,
            abort_reason=None,
            elapsed_ms=100.0,
            nodes=[node],
            events=[],
        )

        # after_step
        vos.after_step(handle, snapshot)

    # Verify store has all 3 outcomes
    hv = store.build_history("c1")
    assert len(hv.last_n) == 3

    # Clean runs -> ceiling should be increasing (Rule 2: +5%)
    assert ceilings[1] > ceilings[0]
    assert ceilings[2] > ceilings[1]


def test_halt_feedback_loop() -> None:
    """Halt in step 1 -> tighter ceiling in step 2."""
    store = MemoryStore()
    vos = VeronicaOS(store=store)

    # Step 1: halted
    intent1 = StepIntent(
        step_id="s0",
        request_id="r1",
        chain_id="c1",
        kind="llm",
        model="gpt-4",
        tool_name=None,
        timeout_ms=30_000,
        metadata={},
    )
    handle1 = vos.before_step(intent1)
    ceiling1 = handle1.policy.ceiling_usd

    node = NodeRecord(
        node_id="n0",
        parent_id=None,
        kind="llm",
        operation_name="halted_op",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc),
        status="halted",
        cost_usd=0.5,
        retries_used=0,
    )
    snapshot = ContextSnapshot(
        chain_id="c1",
        request_id="r1",
        step_count=1,
        cost_usd_accumulated=0.5,
        retries_used=0,
        aborted=False,
        abort_reason=None,
        elapsed_ms=50.0,
        nodes=[node],
        events=[],
    )
    vos.after_step(handle1, snapshot)

    # Step 2: should have tighter ceiling
    intent2 = StepIntent(
        step_id="s1",
        request_id="r1",
        chain_id="c1",
        kind="llm",
        model="gpt-4",
        tool_name=None,
        timeout_ms=30_000,
        metadata={},
    )
    handle2 = vos.before_step(intent2)
    ceiling2 = handle2.policy.ceiling_usd

    assert ceiling2 < ceiling1, f"Expected tighter: {ceiling2} < {ceiling1}"


def test_policy_config_all_fields_populated() -> None:
    """PolicyConfig from before_step has all required fields."""
    vos = VeronicaOS()
    intent = StepIntent(
        step_id="s0",
        request_id="r1",
        chain_id="c1",
        kind="llm",
        model="gpt-4",
        tool_name=None,
        timeout_ms=30_000,
        metadata={},
    )
    handle = vos.before_step(intent)
    pc = handle.policy

    assert pc.chain_id == "c1"
    assert pc.ceiling_usd > 0
    assert pc.on_exceed in ("halt", "degrade", "queue")
    assert pc.issued_at > 0
