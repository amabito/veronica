# tests/test_rollouts_registry_adversarial.py
"""Adversarial tests for RolloutRegistry -- direct registry-level (not HTTP).

Attacker mindset: each test targets a distinct code branch that HTTP-level
tests cannot easily reach. Categories:

  1. State transition abuse -- illegal transitions at registry level
  2. History overflow -- boundary at _MAX_HISTORY_LENGTH (1000)
  3. Simulation result boundaries -- 65536 / 65537 bytes, empty, deep-nested
  4. Concurrent operations -- race conditions on create, transition, simulate
  5. Deep copy isolation -- returned objects must not alias registry internals
  6. simulate() atomicity -- TOCTOU protection, state after partial failure
  7. Non-JSON-serializable results -- datetime, set, custom class in result
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import pytest

from veronica.rollouts.models import Rollout, RolloutState, StateTransition
from veronica.rollouts.registry import (
    InvalidTransitionError,
    RolloutRegistry,
    _MAX_HISTORY_LENGTH,
    _MAX_SIM_RESULT_SIZE,
)
from veronica.types import PolicyConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FIXED_ISSUED_AT = 1_700_000_000.0


def _make_policy(chain_id: str = "test-chain") -> PolicyConfig:
    return PolicyConfig(
        chain_id=chain_id,
        ceiling_usd=1.0,
        on_exceed="halt",
        issued_at=_FIXED_ISSUED_AT,
    )


def _make_registry() -> RolloutRegistry:
    return RolloutRegistry()


def _draft(reg: RolloutRegistry, chain_id: str = "test-chain") -> Rollout:
    """Create and return a DRAFT rollout."""
    return reg.create(policy_config=_make_policy(chain_id), created_by="tester")


def _advance_to_simulated(reg: RolloutRegistry, rollout_id: str) -> Rollout:
    return reg.simulate(rollout_id, result={"ok": True}, actor="auto")


def _advance_to_approved(reg: RolloutRegistry, rollout_id: str) -> Rollout:
    _advance_to_simulated(reg, rollout_id)
    return reg.transition(rollout_id, RolloutState.APPROVED, "qa")


def _advance_to_promoted(reg: RolloutRegistry, rollout_id: str) -> Rollout:
    _advance_to_approved(reg, rollout_id)
    return reg.transition(rollout_id, RolloutState.PROMOTED, "ops")


def _advance_to_active(reg: RolloutRegistry, rollout_id: str) -> Rollout:
    _advance_to_promoted(reg, rollout_id)
    return reg.transition(rollout_id, RolloutState.ACTIVE, "ops")


# ===========================================================================
# Category 1: State Transition Abuse
# ===========================================================================


class TestAdversarialRolloutRegistry:
    """Adversarial registry-level tests -- attacker mindset throughout."""

    # -----------------------------------------------------------------------
    # 1a. REVOKED -> * (all targets must be blocked)
    # -----------------------------------------------------------------------

    def test_revoked_to_draft_raises(self) -> None:
        """REVOKED is terminal -- transition to DRAFT must raise InvalidTransitionError."""
        reg = _make_registry()
        r = _draft(reg)
        reg.transition(r.id, RolloutState.REVOKED, "admin")

        with pytest.raises(InvalidTransitionError):
            reg.transition(r.id, RolloutState.DRAFT, "attacker")

    def test_revoked_to_simulated_raises(self) -> None:
        """REVOKED -> SIMULATED must be blocked."""
        reg = _make_registry()
        r = _draft(reg)
        reg.transition(r.id, RolloutState.REVOKED, "admin")

        with pytest.raises(InvalidTransitionError):
            reg.transition(r.id, RolloutState.SIMULATED, "attacker")

    def test_revoked_to_approved_raises(self) -> None:
        """REVOKED -> APPROVED must be blocked."""
        reg = _make_registry()
        r = _draft(reg)
        reg.transition(r.id, RolloutState.REVOKED, "admin")

        with pytest.raises(InvalidTransitionError):
            reg.transition(r.id, RolloutState.APPROVED, "attacker")

    def test_revoked_to_promoted_raises(self) -> None:
        """REVOKED -> PROMOTED must be blocked."""
        reg = _make_registry()
        r = _draft(reg)
        reg.transition(r.id, RolloutState.REVOKED, "admin")

        with pytest.raises(InvalidTransitionError):
            reg.transition(r.id, RolloutState.PROMOTED, "attacker")

    def test_revoked_to_active_raises(self) -> None:
        """REVOKED -> ACTIVE must be blocked."""
        reg = _make_registry()
        r = _draft(reg)
        reg.transition(r.id, RolloutState.REVOKED, "admin")

        with pytest.raises(InvalidTransitionError):
            reg.transition(r.id, RolloutState.ACTIVE, "attacker")

    def test_revoked_to_revoked_raises(self) -> None:
        """REVOKED -> REVOKED self-loop must also be blocked."""
        reg = _make_registry()
        r = _draft(reg)
        reg.transition(r.id, RolloutState.REVOKED, "admin")

        with pytest.raises(InvalidTransitionError):
            reg.transition(r.id, RolloutState.REVOKED, "attacker")

    def test_active_to_draft_raises(self) -> None:
        """ACTIVE -> DRAFT must be blocked (only REVOKED is allowed from ACTIVE)."""
        reg = _make_registry()
        r = _draft(reg)
        _advance_to_active(reg, r.id)

        with pytest.raises(InvalidTransitionError):
            reg.transition(r.id, RolloutState.DRAFT, "attacker")

    def test_active_to_simulated_raises(self) -> None:
        """ACTIVE -> SIMULATED must be blocked."""
        reg = _make_registry()
        r = _draft(reg)
        _advance_to_active(reg, r.id)

        with pytest.raises(InvalidTransitionError):
            reg.transition(r.id, RolloutState.SIMULATED, "attacker")

    def test_active_to_approved_raises(self) -> None:
        """ACTIVE -> APPROVED must be blocked."""
        reg = _make_registry()
        r = _draft(reg)
        _advance_to_active(reg, r.id)

        with pytest.raises(InvalidTransitionError):
            reg.transition(r.id, RolloutState.APPROVED, "attacker")

    def test_active_to_promoted_raises(self) -> None:
        """ACTIVE -> PROMOTED must be blocked."""
        reg = _make_registry()
        r = _draft(reg)
        _advance_to_active(reg, r.id)

        with pytest.raises(InvalidTransitionError):
            reg.transition(r.id, RolloutState.PROMOTED, "attacker")

    def test_draft_to_active_raises(self) -> None:
        """DRAFT -> ACTIVE (skip all intermediates) must be blocked."""
        reg = _make_registry()
        r = _draft(reg)

        with pytest.raises(InvalidTransitionError):
            reg.transition(r.id, RolloutState.ACTIVE, "attacker")

    def test_draft_to_approved_raises(self) -> None:
        """DRAFT -> APPROVED (skip SIMULATED) must be blocked."""
        reg = _make_registry()
        r = _draft(reg)

        with pytest.raises(InvalidTransitionError):
            reg.transition(r.id, RolloutState.APPROVED, "attacker")

    def test_draft_to_promoted_raises(self) -> None:
        """DRAFT -> PROMOTED must be blocked."""
        reg = _make_registry()
        r = _draft(reg)

        with pytest.raises(InvalidTransitionError):
            reg.transition(r.id, RolloutState.PROMOTED, "attacker")

    def test_transition_nonexistent_id_raises_key_error(self) -> None:
        """transition() on a missing ID must raise KeyError, not crash silently."""
        reg = _make_registry()

        with pytest.raises(KeyError):
            reg.transition("does-not-exist", RolloutState.REVOKED, "admin")

    # -----------------------------------------------------------------------
    # 2. History overflow -- exactly at limit, then one over
    # -----------------------------------------------------------------------

    def test_history_exactly_at_limit_succeeds(self) -> None:
        """The _MAX_HISTORY_LENGTH-th transition must succeed (boundary inclusive)."""
        reg = _make_registry()
        r = _draft(reg)
        rid = r.id

        # Each DRAFT->SIMULATED->DRAFT cycle = 2 history entries.
        # Fill to exactly _MAX_HISTORY_LENGTH - 2 entries (499 cycles), leaving 2 slots.
        half = (_MAX_HISTORY_LENGTH - 2) // 2  # 499 cycles = 998 entries
        for _ in range(half):
            reg.simulate(rid, result={}, actor="fill")
            reg.transition(rid, RolloutState.DRAFT, "fill")

        # 999th entry
        reg.simulate(rid, result={}, actor="fill")
        # 1000th entry -- exactly at the cap; must succeed
        reg.transition(rid, RolloutState.DRAFT, "fill")

        rollout = reg.get(rid)
        assert rollout is not None
        assert len(rollout.history) == _MAX_HISTORY_LENGTH

    def test_history_one_over_limit_raises(self) -> None:
        """The (_MAX_HISTORY_LENGTH + 1)-th transition must raise InvalidTransitionError."""
        reg = _make_registry()
        r = _draft(reg)
        rid = r.id

        # Fill to exactly _MAX_HISTORY_LENGTH entries (same as test above)
        half = (_MAX_HISTORY_LENGTH - 2) // 2
        for _ in range(half):
            reg.simulate(rid, result={}, actor="fill")
            reg.transition(rid, RolloutState.DRAFT, "fill")

        reg.simulate(rid, result={}, actor="fill")
        reg.transition(rid, RolloutState.DRAFT, "fill")
        # History is now exactly _MAX_HISTORY_LENGTH -- next must raise
        with pytest.raises(InvalidTransitionError, match="History limit"):
            reg.simulate(rid, result={}, actor="overflow")

    # -----------------------------------------------------------------------
    # 3. Simulation result boundaries
    # -----------------------------------------------------------------------

    def test_sim_result_exactly_64kb_accepted(self) -> None:
        """A simulation result serialized to exactly 65536 bytes must be accepted."""
        reg = _make_registry()
        r = _draft(reg)

        # Construct a value whose JSON representation is exactly _MAX_SIM_RESULT_SIZE bytes.
        # '{"x": "' + padding + '"}' = 8 + len(padding) + 2 bytes
        overhead = len('{"x": "') + len('"}')  # 9 chars
        padding = "a" * (_MAX_SIM_RESULT_SIZE - overhead)
        result = {"x": padding}

        import json

        assert len(json.dumps(result)) == _MAX_SIM_RESULT_SIZE

        updated = reg.set_simulation_result(r.id, result)
        assert updated.simulation_result is not None

    def test_sim_result_one_byte_over_64kb_raises(self) -> None:
        """A simulation result serialized to 65537 bytes must raise ValueError."""
        reg = _make_registry()
        r = _draft(reg)

        # One byte over the boundary
        overhead = len('{"x": "') + len('"}')
        padding = "a" * (_MAX_SIM_RESULT_SIZE - overhead + 1)
        result = {"x": padding}

        with pytest.raises(ValueError, match=str(_MAX_SIM_RESULT_SIZE)):
            reg.set_simulation_result(r.id, result)

    def test_sim_result_empty_dict_accepted(self) -> None:
        """An empty dict simulation result must be accepted (valid JSON, 2 bytes)."""
        reg = _make_registry()
        r = _draft(reg)

        updated = reg.set_simulation_result(r.id, {})
        assert updated.simulation_result == {}

    def test_sim_result_100_levels_deep_nested_accepted(self) -> None:
        """A deeply nested dict (100 levels) must be accepted if within size limit."""
        reg = _make_registry()
        r = _draft(reg)

        # Build 100-level nesting: {"a": {"a": {"a": ...}}}
        deep: dict[str, Any] = {}
        current = deep
        for _ in range(99):
            inner: dict[str, Any] = {}
            current["a"] = inner
            current = inner
        current["leaf"] = "value"

        import json

        serialized = json.dumps(deep)
        if len(serialized) <= _MAX_SIM_RESULT_SIZE:
            updated = reg.set_simulation_result(r.id, deep)
            assert updated.simulation_result is not None
        else:
            # If the deep nesting exceeds size, expect ValueError (not a crash)
            with pytest.raises(ValueError):
                reg.set_simulation_result(r.id, deep)

    def test_sim_result_on_non_draft_state_raises(self) -> None:
        """set_simulation_result on a SIMULATED rollout must raise InvalidTransitionError."""
        reg = _make_registry()
        r = _draft(reg)
        reg.simulate(r.id, result={"ok": True}, actor="auto")

        with pytest.raises(InvalidTransitionError):
            reg.set_simulation_result(r.id, {"attack": True})

    # -----------------------------------------------------------------------
    # 4. Concurrent operations
    # -----------------------------------------------------------------------

    def test_concurrent_create_does_not_corrupt_registry(self) -> None:
        """10 threads simultaneously creating rollouts must each get a unique rollout."""
        reg = _make_registry()
        created_ids: list[str] = []
        lock = threading.Lock()

        def do_create() -> None:
            rollout = reg.create(
                policy_config=_make_policy("concurrent"), created_by="racer"
            )
            with lock:
                created_ids.append(rollout.id)

        threads = [threading.Thread(target=do_create) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All IDs must be unique -- no aliasing or lost writes
        assert len(created_ids) == 10
        assert len(set(created_ids)) == 10

    def test_concurrent_transition_only_one_succeeds_from_same_state(self) -> None:
        """10 threads racing to revoke a single DRAFT rollout: exactly 1 must succeed."""
        reg = _make_registry()
        r = _draft(reg)

        successes: list[bool] = []
        lock = threading.Lock()

        def try_revoke() -> None:
            try:
                reg.transition(r.id, RolloutState.REVOKED, "racer")
                with lock:
                    successes.append(True)
            except InvalidTransitionError:
                with lock:
                    successes.append(False)

        threads = [threading.Thread(target=try_revoke) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert successes.count(True) == 1, (
            f"Exactly one revoke must succeed, got {successes.count(True)}"
        )
        final = reg.get(r.id)
        assert final is not None
        assert final.state == RolloutState.REVOKED

    def test_concurrent_simulate_only_one_succeeds(self) -> None:
        """10 threads racing to simulate the same DRAFT: exactly 1 must succeed."""
        reg = _make_registry()
        r = _draft(reg)

        successes: list[bool] = []
        lock = threading.Lock()

        def try_simulate() -> None:
            try:
                reg.simulate(r.id, result={"data": "ok"}, actor="racer")
                with lock:
                    successes.append(True)
            except InvalidTransitionError:
                with lock:
                    successes.append(False)

        threads = [threading.Thread(target=try_simulate) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert successes.count(True) == 1
        final = reg.get(r.id)
        assert final is not None
        assert final.state == RolloutState.SIMULATED

    def test_concurrent_mixed_create_and_transition_no_crash(self) -> None:
        """Simultaneous create() and transition() on registry must not crash or deadlock."""
        reg = _make_registry()
        r = _draft(reg)

        errors: list[Exception] = []
        lock = threading.Lock()

        def do_creates() -> None:
            for i in range(5):
                try:
                    reg.create(_make_policy(f"mix-{i}"), "creator")
                except Exception as exc:
                    with lock:
                        errors.append(exc)

        def do_transitions() -> None:
            for _ in range(5):
                try:
                    reg.transition(r.id, RolloutState.REVOKED, "actor")
                except (InvalidTransitionError, KeyError):
                    pass  # expected once revoked
                except Exception as exc:
                    with lock:
                        errors.append(exc)

        t1 = threading.Thread(target=do_creates)
        t2 = threading.Thread(target=do_transitions)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert errors == [], f"Unexpected errors during concurrent ops: {errors}"

    # -----------------------------------------------------------------------
    # 5. Deep copy isolation
    # -----------------------------------------------------------------------

    def test_create_returns_independent_copy(self) -> None:
        """Mutating the returned rollout from create() must not affect the registry."""
        reg = _make_registry()
        returned = _draft(reg)
        original_state = returned.state

        # Mutate the returned copy
        returned.state = RolloutState.REVOKED
        returned.history.append(
            StateTransition(
                from_state=RolloutState.DRAFT,
                to_state=RolloutState.REVOKED,
                timestamp=datetime.now(timezone.utc),
                actor="mutator",
            )
        )

        stored = reg.get(returned.id)
        assert stored is not None
        assert stored.state == original_state, "Registry copy must not be mutated"
        assert len(stored.history) == 0, "Registry history must not be mutated"

    def test_get_returns_independent_copy(self) -> None:
        """Mutating the rollout returned by get() must not affect subsequent gets."""
        reg = _make_registry()
        r = _draft(reg)

        first_get = reg.get(r.id)
        assert first_get is not None
        first_get.state = RolloutState.ACTIVE
        first_get.history.append(
            StateTransition(
                from_state=RolloutState.DRAFT,
                to_state=RolloutState.ACTIVE,
                timestamp=datetime.now(timezone.utc),
                actor="mutator",
            )
        )

        second_get = reg.get(r.id)
        assert second_get is not None
        assert second_get.state == RolloutState.DRAFT
        assert len(second_get.history) == 0

    def test_transition_returned_copy_is_isolated(self) -> None:
        """Mutating the rollout returned by transition() must not corrupt registry state."""
        reg = _make_registry()
        r = _draft(reg)
        returned = reg.transition(r.id, RolloutState.REVOKED, "admin")

        returned.state = RolloutState.DRAFT  # attempt to reset state on the copy

        stored = reg.get(r.id)
        assert stored is not None
        assert stored.state == RolloutState.REVOKED, (
            "Registry state must remain REVOKED after mutating returned copy"
        )

    def test_simulate_result_is_deep_copied(self) -> None:
        """Mutating the original result dict after simulate() must not affect registry."""
        reg = _make_registry()
        r = _draft(reg)

        mutable_result: dict[str, Any] = {"key": "original"}
        reg.simulate(r.id, result=mutable_result, actor="auto")

        # Mutate the dict after the call
        mutable_result["key"] = "corrupted"
        mutable_result["injected"] = True

        stored = reg.get(r.id)
        assert stored is not None
        assert stored.simulation_result is not None
        assert stored.simulation_result.get("key") == "original"
        assert "injected" not in stored.simulation_result

    # -----------------------------------------------------------------------
    # 6. simulate() atomicity
    # -----------------------------------------------------------------------

    def test_simulate_sets_state_to_simulated(self) -> None:
        """simulate() must transition state to SIMULATED atomically."""
        reg = _make_registry()
        r = _draft(reg)

        result = reg.simulate(r.id, result={"hash": "abc123"}, actor="sys")

        assert result.state == RolloutState.SIMULATED
        # Verify the stored version also reflects the transition
        stored = reg.get(r.id)
        assert stored is not None
        assert stored.state == RolloutState.SIMULATED

    def test_simulate_on_non_draft_raises_before_mutating(self) -> None:
        """simulate() on a non-DRAFT rollout must raise without modifying the rollout."""
        reg = _make_registry()
        r = _draft(reg)
        reg.simulate(r.id, result={"first": True}, actor="auto")
        # Rollout is now SIMULATED

        original = reg.get(r.id)
        assert original is not None

        with pytest.raises(InvalidTransitionError):
            reg.simulate(r.id, result={"second": True}, actor="attacker")

        # State and simulation_result must be unchanged
        after = reg.get(r.id)
        assert after is not None
        assert after.state == RolloutState.SIMULATED
        assert after.simulation_result == {"first": True}

    def test_simulate_oversized_result_does_not_transition_state(self) -> None:
        """If simulate() rejects the result for size, the rollout must stay DRAFT."""
        reg = _make_registry()
        r = _draft(reg)

        overhead = len('{"x": "') + len('"}')
        too_big = "a" * (_MAX_SIM_RESULT_SIZE - overhead + 1)

        with pytest.raises(ValueError):
            reg.simulate(r.id, result={"x": too_big}, actor="auto")

        stored = reg.get(r.id)
        assert stored is not None
        assert stored.state == RolloutState.DRAFT, (
            "State must remain DRAFT after failed simulate()"
        )
        assert stored.simulation_result is None, (
            "simulation_result must remain None after failed simulate()"
        )
        assert len(stored.history) == 0, (
            "History must not grow after failed simulate()"
        )

    def test_simulate_adds_exactly_one_history_entry(self) -> None:
        """simulate() must append exactly one history entry (DRAFT -> SIMULATED)."""
        reg = _make_registry()
        r = _draft(reg)

        reg.simulate(r.id, result={"ok": True}, actor="sys")

        stored = reg.get(r.id)
        assert stored is not None
        assert len(stored.history) == 1
        entry = stored.history[0]
        assert entry.from_state == RolloutState.DRAFT
        assert entry.to_state == RolloutState.SIMULATED
        assert entry.actor == "sys"

    # -----------------------------------------------------------------------
    # 7. Non-JSON-serializable results
    # -----------------------------------------------------------------------

    def test_datetime_in_sim_result_raises_value_error(self) -> None:
        """A datetime object in simulation_result is not JSON-serializable -- must raise."""
        reg = _make_registry()
        r = _draft(reg)

        with pytest.raises(ValueError, match="not JSON-serializable"):
            reg.set_simulation_result(
                r.id, {"timestamp": datetime.now(timezone.utc)}
            )

    def test_set_in_sim_result_raises_value_error(self) -> None:
        """A set in simulation_result is not JSON-serializable -- must raise."""
        reg = _make_registry()
        r = _draft(reg)

        with pytest.raises(ValueError, match="not JSON-serializable"):
            reg.set_simulation_result(r.id, {"items": {1, 2, 3}})

    def test_custom_class_in_sim_result_raises_value_error(self) -> None:
        """A custom class instance in simulation_result must raise ValueError."""

        class _Opaque:
            pass

        reg = _make_registry()
        r = _draft(reg)

        with pytest.raises(ValueError, match="not JSON-serializable"):
            reg.set_simulation_result(r.id, {"obj": _Opaque()})

    def test_simulate_with_non_serializable_result_raises_value_error(self) -> None:
        """simulate() with a non-JSON-serializable result must raise ValueError, not crash."""
        reg = _make_registry()
        r = _draft(reg)

        with pytest.raises(ValueError, match="not JSON-serializable"):
            reg.simulate(r.id, result={"when": datetime(2025, 1, 1)}, actor="auto")

        # State must be unchanged (DRAFT) since the error fired before the transition
        stored = reg.get(r.id)
        assert stored is not None
        assert stored.state == RolloutState.DRAFT

    # -----------------------------------------------------------------------
    # 8. Additional edge cases not covered elsewhere
    # -----------------------------------------------------------------------

    def test_rollback_to_draft_clears_simulation_result(self) -> None:
        """Transitioning SIMULATED -> DRAFT must clear the simulation_result field."""
        reg = _make_registry()
        r = _draft(reg)
        reg.simulate(r.id, result={"important": "data"}, actor="auto")

        # Roll back to DRAFT
        reg.transition(r.id, RolloutState.DRAFT, "rollback-actor")

        stored = reg.get(r.id)
        assert stored is not None
        assert stored.state == RolloutState.DRAFT
        assert stored.simulation_result is None, (
            "simulation_result must be cleared when rolling back to DRAFT"
        )

    def test_rollback_approved_to_draft_clears_simulation_result(self) -> None:
        """Transitioning APPROVED -> DRAFT must also clear simulation_result."""
        reg = _make_registry()
        r = _draft(reg)
        _advance_to_approved(reg, r.id)

        reg.transition(r.id, RolloutState.DRAFT, "rollback-actor")

        stored = reg.get(r.id)
        assert stored is not None
        assert stored.state == RolloutState.DRAFT
        assert stored.simulation_result is None

    def test_list_all_returns_independent_copies(self) -> None:
        """Items returned by list_all() must be independent copies -- mutation must not leak."""
        reg = _make_registry()
        _draft(reg)

        items, _ = reg.list_all()
        assert len(items) == 1
        items[0].state = RolloutState.REVOKED

        # Registry must be unaffected
        stored_items, _ = reg.list_all()
        assert all(item.state == RolloutState.DRAFT for item in stored_items)

    def test_set_simulation_result_on_missing_id_raises_key_error(self) -> None:
        """set_simulation_result on a non-existent rollout must raise KeyError."""
        reg = _make_registry()

        with pytest.raises(KeyError):
            reg.set_simulation_result("no-such-id", {"data": 1})

    def test_simulate_on_missing_id_raises_key_error(self) -> None:
        """simulate() on a non-existent rollout must raise KeyError."""
        reg = _make_registry()

        with pytest.raises(KeyError):
            reg.simulate("no-such-id", result={"data": 1}, actor="auto")

    def test_get_nonexistent_returns_none(self) -> None:
        """get() on a missing rollout must return None, not raise."""
        reg = _make_registry()
        result = reg.get("completely-missing")
        assert result is None
