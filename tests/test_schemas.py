# tests/test_schemas.py
"""Tests for the unified CP event schema (schemas/events.py)."""
from __future__ import annotations

import time

import pytest

from veronica.schemas.events import PolicyDecision, StepOutcome


# ---------------------------------------------------------------------------
# StepOutcome -- construction and serialization
# ---------------------------------------------------------------------------


class TestStepOutcome:
    def _make(self, **overrides: object) -> StepOutcome:
        defaults: dict = {
            "step_id": "step-1",
            "chain_id": "chain-abc",
            "operation_name": "gpt-4o",
            "decision": "allow",
            "cost_usd": 0.002,
            "tokens": 512,
            "duration_ms": 123.4,
            "policy_hash": "a" * 64,
            "audit_id": "audit-uuid-1",
            "timestamp": 1_700_000_000.0,
        }
        defaults.update(overrides)
        return StepOutcome(**defaults)  # type: ignore[arg-type]

    def test_construction_happy_path(self) -> None:
        outcome = self._make()
        assert outcome.step_id == "step-1"
        assert outcome.decision == "allow"
        assert outcome.policy_hash == "a" * 64

    def test_frozen(self) -> None:
        outcome = self._make()
        with pytest.raises(AttributeError):
            outcome.cost_usd = 99.9  # type: ignore[misc]

    def test_to_dict_roundtrip(self) -> None:
        outcome = self._make()
        d = outcome.to_dict()
        assert isinstance(d, dict)
        restored = StepOutcome.from_dict(d)
        assert restored == outcome

    def test_to_dict_all_fields_present(self) -> None:
        outcome = self._make()
        d = outcome.to_dict()
        expected_keys = {
            "step_id", "chain_id", "operation_name", "decision",
            "cost_usd", "tokens", "duration_ms", "policy_hash",
            "audit_id", "timestamp",
        }
        assert set(d.keys()) == expected_keys

    def test_from_dict_type_coercion(self) -> None:
        # Integers and strings should be coerced correctly
        d = {
            "step_id": 42,          # int -> str
            "chain_id": "c",
            "operation_name": "op",
            "decision": "halt",
            "cost_usd": "0.5",      # str -> float
            "tokens": "100",        # str -> int
            "duration_ms": 10,
            "policy_hash": "b" * 64,
            "audit_id": "x",
        }
        outcome = StepOutcome.from_dict(d)
        assert outcome.step_id == "42"
        assert outcome.cost_usd == 0.5
        assert outcome.tokens == 100

    def test_from_dict_missing_timestamp_defaults_to_current_time(self) -> None:
        before = time.time()
        d = {
            "step_id": "s", "chain_id": "c", "operation_name": "op",
            "decision": "allow", "cost_usd": 0.0, "tokens": 0,
            "duration_ms": 0.0, "policy_hash": "a" * 64, "audit_id": "u",
        }
        outcome = StepOutcome.from_dict(d)
        after = time.time()
        # Missing timestamp must default to current time, not 0.0 (which is epoch 1970)
        assert before <= outcome.timestamp <= after

    def test_default_timestamp_is_recent(self) -> None:
        before = time.time()
        outcome = StepOutcome(
            step_id="s", chain_id="c", operation_name="op",
            decision="allow", cost_usd=0.0, tokens=0,
            duration_ms=0.0, policy_hash="a" * 64, audit_id="u",
        )
        after = time.time()
        assert before <= outcome.timestamp <= after

    @pytest.mark.parametrize("decision", [
        "allow", "halt", "degrade", "retry", "quarantine", "queue",
    ])
    def test_all_decision_values_accepted(self, decision: str) -> None:
        outcome = self._make(decision=decision)  # type: ignore[arg-type]
        assert outcome.decision == decision

    def test_zero_cost_and_tokens(self) -> None:
        outcome = self._make(cost_usd=0.0, tokens=0, duration_ms=0.0)
        assert outcome.cost_usd == 0.0
        assert outcome.tokens == 0

    def test_large_token_count(self) -> None:
        outcome = self._make(tokens=10_000_000)
        assert StepOutcome.from_dict(outcome.to_dict()).tokens == 10_000_000


# ---------------------------------------------------------------------------
# PolicyDecision -- construction and serialization
# ---------------------------------------------------------------------------


class TestPolicyDecision:
    def _make(self, **overrides: object) -> PolicyDecision:
        defaults: dict = {
            "decision_id": "dec-uuid-1",
            "policy_id": "chain-abc",
            "policy_hash": "c" * 64,
            "rule_matched": "budget_ceiling",
            "verdict": "ALLOW",
            "reason": "within budget",
            "context": {"ceiling_usd": 1.0},
            "timestamp": 1_700_000_000.0,
        }
        defaults.update(overrides)
        return PolicyDecision(**defaults)  # type: ignore[arg-type]

    def test_construction_happy_path(self) -> None:
        pd = self._make()
        assert pd.verdict == "ALLOW"
        assert pd.policy_hash == "c" * 64

    def test_frozen(self) -> None:
        pd = self._make()
        with pytest.raises(AttributeError):
            pd.verdict = "HALT"  # type: ignore[misc]

    def test_to_dict_roundtrip(self) -> None:
        pd = self._make(context={"x": 1, "y": "two"})
        d = pd.to_dict()
        restored = PolicyDecision.from_dict(d)
        assert restored == pd

    def test_to_dict_all_fields_present(self) -> None:
        pd = self._make()
        d = pd.to_dict()
        expected_keys = {
            "decision_id", "policy_id", "policy_hash", "rule_matched",
            "verdict", "reason", "context", "timestamp",
        }
        assert set(d.keys()) == expected_keys

    def test_context_is_copied_in_to_dict(self) -> None:
        ctx = {"key": "val"}
        pd = self._make(context=ctx)
        d = pd.to_dict()
        d["context"]["injected"] = True
        # Original must not be mutated
        assert "injected" not in ctx

    def test_empty_context_allowed(self) -> None:
        pd = self._make(context={})
        assert pd.to_dict()["context"] == {}

    def test_from_dict_missing_context_defaults_to_empty(self) -> None:
        d = {
            "decision_id": "d", "policy_id": "p", "policy_hash": "c" * 64,
            "rule_matched": "r", "verdict": "HALT", "reason": "over limit",
        }
        pd = PolicyDecision.from_dict(d)
        assert pd.context == {}

    def test_from_dict_missing_timestamp_defaults_to_current_time(self) -> None:
        before = time.time()
        d = {
            "decision_id": "d", "policy_id": "p", "policy_hash": "c" * 64,
            "rule_matched": "r", "verdict": "DEGRADE", "reason": "r",
            "context": {},
        }
        pd = PolicyDecision.from_dict(d)
        after = time.time()
        # Missing timestamp must default to current time, not 0.0 (which is epoch 1970)
        assert before <= pd.timestamp <= after

    def test_default_timestamp_is_recent(self) -> None:
        before = time.time()
        pd = PolicyDecision(
            decision_id="d", policy_id="p", policy_hash="c" * 64,
            rule_matched="r", verdict="ALLOW", reason="ok", context={},
        )
        after = time.time()
        assert before <= pd.timestamp <= after

    @pytest.mark.parametrize("verdict", ["ALLOW", "HALT", "DEGRADE"])
    def test_all_verdict_values_accepted(self, verdict: str) -> None:
        pd = self._make(verdict=verdict)  # type: ignore[arg-type]
        assert pd.verdict == verdict

    def test_complex_context_survives_roundtrip(self) -> None:
        ctx = {"nested": {"a": 1}, "list_val": [1, 2, 3], "num": 3.14}
        pd = self._make(context=ctx)
        restored = PolicyDecision.from_dict(pd.to_dict())
        assert dict(restored.context) == dict(pd.context)


# ---------------------------------------------------------------------------
# Adversarial tests
# ---------------------------------------------------------------------------


class TestStepOutcomeAdversarial:
    def test_corrupted_decision_still_stores(self) -> None:
        # CP schema is a pure dataclass -- no validation at this layer.
        # Enforcement is the kernel's job. Verify it stores without raising.
        outcome = StepOutcome(
            step_id="s", chain_id="c", operation_name="op",
            decision="garbage_value",  # type: ignore[arg-type]
            cost_usd=0.0, tokens=0, duration_ms=0.0,
            policy_hash="a" * 64, audit_id="u",
        )
        d = outcome.to_dict()
        assert d["decision"] == "garbage_value"

    def test_negative_cost_roundtrips(self) -> None:
        outcome = StepOutcome(
            step_id="s", chain_id="c", operation_name="op",
            decision="allow", cost_usd=-1.5, tokens=0,
            duration_ms=0.0, policy_hash="a" * 64, audit_id="u",
        )
        assert StepOutcome.from_dict(outcome.to_dict()).cost_usd == -1.5

    def test_from_dict_with_extra_keys_ignored(self) -> None:
        d = {
            "step_id": "s", "chain_id": "c", "operation_name": "op",
            "decision": "allow", "cost_usd": 0.0, "tokens": 0,
            "duration_ms": 0.0, "policy_hash": "a" * 64, "audit_id": "u",
            "unknown_future_field": "ignored",
        }
        # Extra keys must not crash from_dict (forward compat)
        outcome = StepOutcome.from_dict(d)
        assert outcome.step_id == "s"


class TestPolicyDecisionAdversarial:
    def test_empty_reason_allowed(self) -> None:
        pd = PolicyDecision(
            decision_id="d", policy_id="p", policy_hash="c" * 64,
            rule_matched="r", verdict="ALLOW", reason="", context={},
        )
        assert pd.reason == ""

    def test_empty_policy_hash_allowed(self) -> None:
        # Schema layer does not validate hash format -- that is the hasher's job.
        pd = PolicyDecision(
            decision_id="d", policy_id="p", policy_hash="",
            rule_matched="r", verdict="ALLOW", reason="ok", context={},
        )
        assert PolicyDecision.from_dict(pd.to_dict()).policy_hash == ""

    def test_very_large_context_roundtrips(self) -> None:
        ctx = {f"k{i}": i for i in range(1000)}
        pd = PolicyDecision(
            decision_id="d", policy_id="p", policy_hash="c" * 64,
            rule_matched="r", verdict="DEGRADE", reason="r", context=ctx,
        )
        restored = PolicyDecision.from_dict(pd.to_dict())
        assert len(restored.context) == 1000
