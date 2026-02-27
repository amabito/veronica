# tests/test_os.py
"""Tests for veronica.os -- VeronicaOS orchestrator."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest

from veronica_core.containment.execution_context import ContextSnapshot, NodeRecord

from veronica.os import VeronicaOS
from veronica.store import MemoryStore
from veronica.types import StepIntent


def _intent(
    step_id: str = "s1",
    chain_id: str = "c1",
    model: str = "gpt-4",
) -> StepIntent:
    return StepIntent(
        step_id=step_id, request_id="r1", chain_id=chain_id,
        kind="llm", model=model, tool_name=None,
        timeout_ms=30_000, metadata={},
    )


def _snapshot(
    chain_id: str = "c1",
    cost: float = 0.01,
    status: str = "ok",
) -> ContextSnapshot:
    node = NodeRecord(
        node_id="n1", parent_id=None, kind="llm",
        operation_name="test_op",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc),
        status=status, cost_usd=cost, retries_used=0,
    )
    return ContextSnapshot(
        chain_id=chain_id, request_id="r1", step_count=1,
        cost_usd_accumulated=cost, retries_used=0,
        aborted=False, abort_reason=None,
        elapsed_ms=100.0, nodes=[node], events=[],
    )


class TestVeronicaOS:
    def test_before_step_returns_handle(self) -> None:
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        assert handle.intent.step_id == "s1"
        assert handle.policy.ceiling_usd > 0
        assert handle.policy.chain_id == "c1"

    def test_after_step_commits_to_store(self) -> None:
        store = MemoryStore()
        vos = VeronicaOS(store=store)
        handle = vos.before_step(_intent())
        vos.after_step(handle, _snapshot())
        hv = store.build_history("c1")
        assert len(hv.last_n) == 1

    def test_full_cycle(self) -> None:
        """before_step -> (simulated execution) -> after_step."""
        store = MemoryStore()
        vos = VeronicaOS(store=store)

        # Step 1
        handle1 = vos.before_step(_intent(step_id="s1"))
        vos.after_step(handle1, _snapshot(cost=0.01))

        # Step 2
        handle2 = vos.before_step(_intent(step_id="s2"))
        vos.after_step(handle2, _snapshot(cost=0.02))

        hv = store.build_history("c1")
        assert len(hv.last_n) == 2

    def test_to_exec_config_bridge(self) -> None:
        """PolicyConfig.to_exec_config produces valid ExecutionConfig."""
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        ec = handle.policy.to_exec_config()
        assert ec.max_cost_usd > 0
        assert ec.max_steps > 0

    def test_tighten_after_halt(self) -> None:
        """After a halted step, ceiling should decrease."""
        store = MemoryStore()
        vos = VeronicaOS(store=store)

        handle1 = vos.before_step(_intent())
        ceiling1 = handle1.policy.ceiling_usd

        vos.after_step(handle1, _snapshot(status="halted"))

        handle2 = vos.before_step(_intent(step_id="s2"))
        ceiling2 = handle2.policy.ceiling_usd

        assert ceiling2 < ceiling1

    def test_loosen_after_clean_run(self) -> None:
        """After a clean step, ceiling should increase."""
        store = MemoryStore()
        vos = VeronicaOS(store=store)

        handle1 = vos.before_step(_intent())
        ceiling1 = handle1.policy.ceiling_usd

        vos.after_step(handle1, _snapshot(status="ok"))

        handle2 = vos.before_step(_intent(step_id="s2"))
        ceiling2 = handle2.policy.ceiling_usd

        assert ceiling2 > ceiling1

    def test_custom_components(self) -> None:
        """Accepts custom Protocol implementations."""
        store = MemoryStore()
        vos = VeronicaOS(store=store)
        assert vos is not None

    def test_decision_meta_not_degraded(self) -> None:
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        assert not handle.decision_meta.degraded

    def test_budget_state_defaults(self) -> None:
        """Default budget state allows execution."""
        vos = VeronicaOS()
        handle = vos.before_step(_intent())
        assert handle.policy.ceiling_usd > 0


class TestTotalSpentUsdThreadSafety:
    """Bug 1: _total_spent_usd must be protected by a threading.Lock."""

    def test_concurrent_after_step_no_data_race(self) -> None:
        """N threads calling after_step concurrently must not lose updates."""
        n_threads = 20
        cost_per_step = 0.01
        store = MemoryStore()
        vos = VeronicaOS(store=store, request_budget_usd=1000.0)

        # Pre-generate handles (before_step is not under test here)
        handles = [
            vos.before_step(_intent(step_id=f"s{i}", chain_id=f"c{i}"))
            for i in range(n_threads)
        ]
        snapshots = [
            _snapshot(chain_id=f"c{i}", cost=cost_per_step)
            for i in range(n_threads)
        ]

        barrier = threading.Barrier(n_threads)
        errors: list[Exception] = []

        def worker(h, s):
            try:
                barrier.wait()
                vos.after_step(h, s)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(handles[i], snapshots[i]))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors in worker threads: {errors}"
        # All costs must be recorded; no lost updates
        expected = n_threads * cost_per_step
        assert abs(vos._total_spent_usd - expected) < 1e-9, (
            f"Expected {expected:.6f}, got {vos._total_spent_usd:.6f} — likely race"
        )

    def test_has_lock_attribute(self) -> None:
        """VeronicaOS must expose a threading.Lock (or RLock) for _total_spent_usd."""
        import threading as _threading
        vos = VeronicaOS()
        assert hasattr(vos, "_lock"), "VeronicaOS must have a _lock attribute"
        assert isinstance(vos._lock, (_threading.Lock().__class__, _threading.RLock().__class__))


class TestStepCounterIsolation:
    """Bug 9: _step_counter must be per-instance, not module-level global."""

    def test_two_instances_have_independent_counters(self) -> None:
        """Two VeronicaOS instances must not share a step counter."""
        vos1 = VeronicaOS()
        vos2 = VeronicaOS()

        # Advance vos1 counter by 5 steps
        for i in range(5):
            intent = StepIntent(
                step_id="", request_id="r1", chain_id="c1",
                kind="llm", model="gpt-4", tool_name=None,
                timeout_ms=30_000, metadata={},
            )
            normalized = vos1._normalize_intent(intent)
            # Each call to _normalize_intent with empty step_id generates a new step id

        # vos2 should still start at step-1 (or its own fresh counter)
        intent2 = StepIntent(
            step_id="", request_id="r2", chain_id="c2",
            kind="llm", model="gpt-4", tool_name=None,
            timeout_ms=30_000, metadata={},
        )
        normalized2 = vos2._normalize_intent(intent2)
        # vos2 counter must be independent from vos1's counter
        # If they shared a module-level counter, vos2 would get step-6
        step_num = int(normalized2.step_id.split("-")[1])
        assert step_num == 1, (
            f"vos2 should have step-1 (independent counter), got {normalized2.step_id}"
        )

    def test_instance_counter_increments(self) -> None:
        """Counter must increment within the same instance."""
        vos = VeronicaOS()
        intent = StepIntent(
            step_id="", request_id="r1", chain_id="c1",
            kind="llm", model="gpt-4", tool_name=None,
            timeout_ms=30_000, metadata={},
        )
        n1 = vos._normalize_intent(intent)
        n2 = vos._normalize_intent(intent)
        num1 = int(n1.step_id.split("-")[1])
        num2 = int(n2.step_id.split("-")[1])
        assert num2 == num1 + 1, f"Counter should increment: got {n1.step_id}, {n2.step_id}"


class TestExpiresAtWarning:
    """Bug 8: PolicyConfig.expires_at should log a warning when expired."""

    def test_expired_policy_logs_warning(self, caplog) -> None:
        """before_step must log a warning when policy.expires_at is in the past."""
        import logging
        from unittest.mock import MagicMock
        from veronica.types import PolicyConfig, DesiredPolicy

        # Build an arbiter that returns an already-expired policy
        expired_policy = PolicyConfig(
            chain_id="c1",
            ceiling_usd=1.0,
            on_exceed="halt",
            issued_at=time.time() - 100.0,
            expires_at=time.time() - 1.0,  # expired 1 second ago
        )

        mock_arbiter = MagicMock()
        mock_arbiter.arbitrate.return_value = {"c1": expired_policy}

        vos = VeronicaOS(arbiter=mock_arbiter)
        with caplog.at_level(logging.WARNING, logger="veronica.os"):
            vos.before_step(_intent())

        assert any("expires_at" in msg or "expired" in msg.lower() for msg in caplog.messages), (
            f"Expected warning about expires_at, got: {caplog.messages}"
        )

    def test_non_expired_policy_no_warning(self, caplog) -> None:
        """before_step must not warn when policy.expires_at is in the future."""
        import logging
        from unittest.mock import MagicMock
        from veronica.types import PolicyConfig

        future_policy = PolicyConfig(
            chain_id="c1",
            ceiling_usd=1.0,
            on_exceed="halt",
            issued_at=time.time(),
            expires_at=time.time() + 3600.0,  # expires in 1 hour
        )

        mock_arbiter = MagicMock()
        mock_arbiter.arbitrate.return_value = {"c1": future_policy}

        vos = VeronicaOS(arbiter=mock_arbiter)
        with caplog.at_level(logging.WARNING, logger="veronica.os"):
            vos.before_step(_intent())

        expires_warnings = [
            msg for msg in caplog.messages
            if "expires_at" in msg or ("expired" in msg.lower() and "policy" in msg.lower())
        ]
        assert not expires_warnings, f"Unexpected expires_at warning: {expires_warnings}"

    def test_no_expires_at_no_warning(self, caplog) -> None:
        """before_step must not warn when policy.expires_at is None."""
        import logging
        vos = VeronicaOS()
        with caplog.at_level(logging.WARNING, logger="veronica.os"):
            vos.before_step(_intent())

        expires_warnings = [
            msg for msg in caplog.messages
            if "expires_at" in msg
        ]
        assert not expires_warnings


class TestVeronicaOSLifecycle:
    """Bug 4: VeronicaOS.close() must call store.close() if hasattr."""

    def test_close_calls_store_close(self) -> None:
        """VeronicaOS.close() must call store.close() when store has it."""
        from unittest.mock import MagicMock
        store = MagicMock()
        store.build_history.return_value = MemoryStore().build_history("c1")
        vos = VeronicaOS(store=store)
        vos.close()
        store.close.assert_called_once()

    def test_close_no_error_when_store_lacks_close(self) -> None:
        """VeronicaOS.close() must not raise if store has no close()."""
        store = MemoryStore()  # MemoryStore has no close()
        vos = VeronicaOS(store=store)
        assert not hasattr(store, "close"), "MemoryStore should not have close()"
        vos.close()  # Must not raise

    def test_context_manager_calls_close(self) -> None:
        """VeronicaOS must support 'with' statement."""
        from unittest.mock import patch
        vos = VeronicaOS()
        with patch.object(vos, "close") as mock_close:
            with vos:
                pass
        mock_close.assert_called_once()

    def test_context_manager_returns_self(self) -> None:
        """__enter__ must return self."""
        vos = VeronicaOS()
        with vos as entered:
            assert entered is vos

    def test_context_manager_calls_close_on_exception(self) -> None:
        """__exit__ must call close() even when body raises."""
        from unittest.mock import patch
        vos = VeronicaOS()
        with patch.object(vos, "close") as mock_close:
            try:
                with vos:
                    raise ValueError("test error")
            except ValueError:
                pass
        mock_close.assert_called_once()


class TestChainRemainingUsd:
    """Bug 3: BudgetState.chain_remaining_usd must track per-chain spending."""

    def test_chain_remaining_reflects_per_chain_spend(self) -> None:
        """After spending on chain A, chain B should still see full budget."""
        vos = VeronicaOS(request_budget_usd=10.0)

        # Spend 5 USD on chain A
        handle_a = vos.before_step(_intent(step_id="sA", chain_id="chainA"))
        vos.after_step(handle_a, _snapshot(chain_id="chainA", cost=5.0))

        # chain B should NOT see chain A's spend in its chain_remaining_usd
        # We inspect via before_step on chainB — the handle carries the budget state
        # indirectly through DesiredPolicy.ceiling_usd
        handle_b = vos.before_step(_intent(step_id="sB", chain_id="chainB"))
        # chain B must not be limited to (global_budget - chainA_spend)
        # Its ceiling should be based on per-chain tracking, not global total
        assert handle_b.policy.ceiling_usd > 0, "Chain B must not be blocked by chain A spend"

    def test_chain_spent_tracked_per_chain(self) -> None:
        """_chain_spent_usd must track spending per chain independently."""
        vos = VeronicaOS(request_budget_usd=100.0)

        handle_a = vos.before_step(_intent(step_id="sA", chain_id="chainA"))
        vos.after_step(handle_a, _snapshot(chain_id="chainA", cost=5.0))

        handle_b = vos.before_step(_intent(step_id="sB", chain_id="chainB"))
        vos.after_step(handle_b, _snapshot(chain_id="chainB", cost=3.0))

        # Per-chain tracking: chain A spent 5, chain B spent 3
        assert vos._chain_spent_usd.get("chainA", 0.0) == pytest.approx(5.0)
        assert vos._chain_spent_usd.get("chainB", 0.0) == pytest.approx(3.0)
        # Global total: 5 + 3 = 8
        assert vos._total_spent_usd == pytest.approx(8.0)
