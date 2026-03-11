# tests/test_policy_hash_audit.py
"""Tests for policy_hash + audit_id in all kernel decisions (Task #4)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone


from veronica_core.containment.execution_context import ContextSnapshot, NodeRecord

from veronica.buffered_emitter import BufferedEmitter
from veronica.distribution import PolicyDistributor
from veronica.os import VeronicaOS, _compute_policy_hash
from veronica.types import PolicyConfig, StepIntent


def _intent(
    step_id: str = "s1",
    request_id: str = "r1",
    chain_id: str = "c1",
    kind: str = "llm",
) -> StepIntent:
    return StepIntent(
        step_id=step_id,
        request_id=request_id,
        chain_id=chain_id,
        kind=kind,
        model="gpt-4",
        tool_name=None,
        timeout_ms=30_000,
        metadata={},
    )


def _snapshot(
    chain_id: str = "c1",
    cost: float = 0.01,
    request_id: str = "r1",
) -> ContextSnapshot:
    node = NodeRecord(
        node_id="n1",
        parent_id=None,
        kind="llm",
        operation_name="test_op",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc),
        status="ok",
        cost_usd=cost,
        retries_used=0,
    )
    return ContextSnapshot(
        chain_id=chain_id,
        request_id=request_id,
        step_count=1,
        cost_usd_accumulated=cost,
        retries_used=0,
        aborted=False,
        abort_reason=None,
        elapsed_ms=100.0,
        nodes=[node],
        events=[],
    )


def _policy(**kwargs) -> PolicyConfig:
    defaults = dict(
        chain_id="c1",
        ceiling_usd=1.0,
        on_exceed="halt",
        issued_at=1_000_000.0,
    )
    defaults.update(kwargs)
    return PolicyConfig(**defaults)


class TestComputePolicyHash:
    def test_hash_is_sha256_hex(self) -> None:
        h = _compute_policy_hash(_policy())
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_same_policy_same_hash(self) -> None:
        p = _policy(ceiling_usd=1.0, issued_at=1_000_000.0)
        assert _compute_policy_hash(p) == _compute_policy_hash(p)

    def test_different_ceiling_different_hash(self) -> None:
        h1 = _compute_policy_hash(_policy(ceiling_usd=1.0))
        h2 = _compute_policy_hash(_policy(ceiling_usd=2.0))
        assert h1 != h2

    def test_different_chain_different_hash(self) -> None:
        h1 = _compute_policy_hash(_policy(chain_id="a"))
        h2 = _compute_policy_hash(_policy(chain_id="b"))
        assert h1 != h2

    def test_consistent_with_policy_distributor(self) -> None:
        """Hash from os._compute_policy_hash must match PolicyDistributor (single source of truth)."""
        from veronica.policy_audit import compute_policy_hash

        p = _policy(ceiling_usd=3.0, chain_id="audit-chain")
        assert _compute_policy_hash(p) == compute_policy_hash(p)


class TestPolicyHashInPayload:
    def test_step_completed_payload_has_policy_hash(self) -> None:
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        events = emitter.snapshot()
        assert len(events) == 1
        _, payload = events[0]
        assert "policy_hash" in payload
        assert len(payload["policy_hash"]) == 64

    def test_step_completed_payload_has_audit_id(self) -> None:
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        _, payload = emitter.snapshot()[0]
        assert "audit_id" in payload
        assert len(payload["audit_id"]) == 32  # UUID4 hex (no hyphens)

    def test_audit_ids_unique_across_steps(self) -> None:
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter)
        for i in range(5):
            handle = vos.before_step(_intent(step_id=f"s{i}"))
            vos.after_step(handle, _snapshot())

        events = emitter.snapshot()
        audit_ids = [p["audit_id"] for _, p in events]
        assert len(set(audit_ids)) == 5  # all unique

    def test_policy_hash_is_deterministic_for_fixed_policy(self) -> None:
        """The same PolicyConfig (same issued_at) always produces the same hash."""
        fixed_policy = _policy(issued_at=9999.0)
        h1 = _compute_policy_hash(fixed_policy)
        h2 = _compute_policy_hash(fixed_policy)
        assert h1 == h2

    def test_policy_hash_matches_distributor_output(self) -> None:
        """policy_hash in emitted event equals PolicyDistributor output for same policy."""
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        _, payload = emitter.snapshot()[0]
        emitted_hash = payload["policy_hash"]

        dist = PolicyDistributor()
        bundle = dist.distribute(handle.policy)
        assert emitted_hash == bundle.policy_hash


class TestAuditHashChainIntegrity:
    def test_full_pipeline_hash_chain_unbroken(self) -> None:
        """Create policy -> distribute -> execute -> verify hash chain."""
        from veronica.distribution import PolicyDistributor

        dist = PolicyDistributor()
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter)

        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())

        _, payload = emitter.snapshot()[0]

        # Verify: hash from distributor == hash in payload
        bundle = dist.distribute(handle.policy)
        assert payload["policy_hash"] == bundle.policy_hash

    def test_step_denied_payload_schema_version(self) -> None:
        """step_denied event still has schema_version=1."""
        from veronica.types import OrgPolicy

        org = OrgPolicy(blocked_models=frozenset({"gpt-4"}))
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter, org_policy=org)

        vos.before_step(_intent(kind="llm"))
        # Denied -- after_step not reached, but before_step emits step_denied

        events = emitter.snapshot()
        assert len(events) == 1
        event_type, payload = events[0]
        assert event_type == "step_denied"
        assert payload["schema_version"] == 1

    def test_concurrent_steps_unique_audit_ids(self) -> None:
        """Concurrent after_step calls produce unique audit_ids."""
        emitter = BufferedEmitter()
        vos = VeronicaOS(emitter=emitter)
        def run_step(i: int) -> None:
            handle = vos.before_step(_intent(step_id=f"s{i}", request_id=f"r{i}"))
            vos.after_step(handle, _snapshot())

        threads = [threading.Thread(target=run_step, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        events = emitter.snapshot()
        audit_ids = [p["audit_id"] for _, p in events]
        assert len(set(audit_ids)) == 10  # all unique under concurrency
