# tests/test_org_policy.py
"""Tests for Phase 7: Org Policy Engine."""

from __future__ import annotations

import logging
import threading
import time
from unittest.mock import MagicMock

import pytest
from prometheus_client import CollectorRegistry

from veronica.buffered_emitter import BufferedEmitter
from veronica.metrics_subscriber import MetricsSubscriber
from veronica.os import OrgPolicyDenied, VeronicaOS
from veronica.types import (
    DesiredPolicy,
    OrgPolicy,
    PolicyConfig,
    StepIntent,
)


def _intent(
    model: str | None = "gpt-4",
    tool_name: str | None = None,
    kind: str = "llm",
) -> StepIntent:
    return StepIntent(
        step_id="s1",
        request_id="r1",
        chain_id="c1",
        kind=kind,
        model=model,
        tool_name=tool_name,
        timeout_ms=30_000,
        metadata={},
    )


def _desired(
    ceiling_usd: float = 10.0,
    timeout_ms: int = 30_000,
    priority: int = 50,
    fallback_model: str | None = None,
) -> DesiredPolicy:
    return DesiredPolicy(
        chain_id="c1",
        ceiling_usd=ceiling_usd,
        ceiling_steps=100,
        ceiling_tokens_out=50_000,
        on_exceed="halt",
        fallback_model=fallback_model,
        timeout_ms=timeout_ms,
        priority=priority,
    )


class TestOrgPolicyValidate:
    def test_blocks_model(self) -> None:
        """Blocked model returns denial reason."""
        policy = OrgPolicy(blocked_models=frozenset({"gpt-4"}))
        result = policy.validate(_intent(model="gpt-4"))
        assert result is not None
        assert "gpt-4" in result
        assert "blocked" in result

    def test_blocks_tool(self) -> None:
        """Blocked tool returns denial reason."""
        policy = OrgPolicy(blocked_tools=frozenset({"dangerous_tool"}))
        result = policy.validate(_intent(tool_name="dangerous_tool"))
        assert result is not None
        assert "dangerous_tool" in result

    def test_allows_clean_intent(self) -> None:
        """Clean intent returns None."""
        policy = OrgPolicy(blocked_models=frozenset({"gpt-4o"}))
        result = policy.validate(_intent(model="gpt-4"))
        assert result is None

    def test_casefold(self) -> None:
        """Case-insensitive model/tool matching."""
        policy = OrgPolicy(
            blocked_models=frozenset({"GPT-4"}),
            blocked_tools=frozenset({"DangerousTool"}),
        )
        assert policy.validate(_intent(model="gpt-4")) is not None
        assert policy.validate(_intent(model="Gpt-4")) is not None
        assert policy.validate(_intent(tool_name="dangeroustool")) is not None


class TestOrgPolicyClamp:
    def test_clamp_ceiling(self) -> None:
        """ceiling_usd capped to max."""
        policy = OrgPolicy(max_ceiling_usd=5.0)
        result = policy.clamp(_desired(ceiling_usd=10.0), _intent())
        assert result.ceiling_usd == 5.0
        # Other fields preserved
        assert result.ceiling_steps == 100
        assert result.on_exceed == "halt"

    def test_clamp_timeout(self) -> None:
        """timeout_ms capped to max."""
        policy = OrgPolicy(max_timeout_ms=10_000)
        result = policy.clamp(_desired(timeout_ms=30_000), _intent())
        assert result.timeout_ms == 10_000

    def test_clamp_fallback_model_blocked(self) -> None:
        """Blocked fallback_model set to None."""
        policy = OrgPolicy(blocked_models=frozenset({"gpt-4o"}))
        result = policy.clamp(_desired(fallback_model="GPT-4o"), _intent())
        assert result.fallback_model is None

    def test_clamp_no_change(self) -> None:
        """Within limits returns same instance."""
        policy = OrgPolicy(max_ceiling_usd=100.0, max_timeout_ms=60_000)
        desired = _desired(ceiling_usd=10.0, timeout_ms=30_000)
        result = policy.clamp(desired, _intent())
        assert result is desired


# ---------------------------------------------------------------------------
# Task 2: OrgPolicyDenied + StepContext._check_denial
# ---------------------------------------------------------------------------


class TestOrgPolicyDenied:
    def test_deny_fn_not_called(self) -> None:
        """Denied step raises OrgPolicyDenied, fn is never called."""
        vos = VeronicaOS(
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4"})),
        )
        intent = _intent(model="gpt-4")
        fn = MagicMock(return_value="should_not_run")

        with pytest.raises(OrgPolicyDenied, match="blocked"):
            with vos.step(intent) as ctx:
                ctx.run(fn)

        fn.assert_not_called()

    def test_deny_after_step_still_runs(self) -> None:
        """Denied step still commits to Store via after_step (step() finally block)."""
        vos = VeronicaOS(
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4"})),
        )
        intent = _intent(model="gpt-4")

        with pytest.raises(OrgPolicyDenied):
            with vos.step(intent) as ctx:
                ctx.run(lambda: None)

        # after_step committed to store
        history = vos._store.build_history("c1")
        assert history.depth == 1


# ---------------------------------------------------------------------------
# Task 3: VeronicaOS.before_step() integration (validate + clamp + emit)
# ---------------------------------------------------------------------------


class TestOrgPolicyIntegration:
    def test_deny_emits_step_denied_event(self) -> None:
        """Denied step emits step_denied event with kind."""
        emitter = BufferedEmitter()
        events: list[tuple[str, dict]] = []
        emitter.subscribe("test", lambda et, p: events.append((et, p)))

        vos = VeronicaOS(
            emitter=emitter,
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4"})),
        )
        intent = _intent(model="gpt-4")

        with pytest.raises(OrgPolicyDenied):
            with vos.step(intent) as ctx:
                ctx.run(lambda: None)

        denied_events = [(et, p) for et, p in events if et == "step_denied"]
        assert len(denied_events) == 1
        _, payload = denied_events[0]
        assert payload["kind"] == "llm"
        assert "blocked" in payload["reason"]
        assert payload["model"] == "gpt-4"

    def test_deny_emitter_failure_no_crash(self) -> None:
        """Emitter exception during step_denied does not break before_step."""
        bad_emitter = MagicMock()
        bad_emitter.emit.side_effect = RuntimeError("emitter broken")

        vos = VeronicaOS(
            emitter=bad_emitter,
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4"})),
        )
        intent = _intent(model="gpt-4")

        # before_step still returns a StepHandle (doesn't crash)
        handle = vos.before_step(intent)
        assert handle.decision_meta.org_denial is not None

    def test_clamp_integration(self) -> None:
        """Planner output is clamped by org policy before reaching Arbiter."""
        spy_arbiter = MagicMock()
        spy_arbiter.arbitrate.return_value = {
            "c1": PolicyConfig(
                chain_id="c1",
                ceiling_usd=5.0,
                on_exceed="halt",
                issued_at=time.time(),
            ),
        }

        vos = VeronicaOS(
            arbiter=spy_arbiter,
            org_policy=OrgPolicy(max_ceiling_usd=5.0),
        )
        intent = _intent(model="gpt-4")

        vos.before_step(intent)

        # Verify arbiter was called with clamped desired
        call_args = spy_arbiter.arbitrate.call_args
        desired_list = call_args[0][0]  # first positional arg
        assert desired_list[0].ceiling_usd <= 5.0

    def test_no_org_policy_passthrough(self) -> None:
        """org_policy=None means all existing behavior unchanged."""
        vos = VeronicaOS()  # no org_policy
        intent = _intent(model="gpt-4")

        handle = vos.before_step(intent)

        assert handle.decision_meta.org_denial is None
        assert handle.policy.ceiling_usd > 0


# ---------------------------------------------------------------------------
# Task 4: MetricsSubscriber + StructuredLogSubscriber extensions
# ---------------------------------------------------------------------------


class TestOrgPolicyMetrics:
    def test_denied_total_metric(self) -> None:
        """MetricsSubscriber increments veronica_denied_total on step_denied."""
        registry = CollectorRegistry()
        subscriber = MetricsSubscriber(registry=registry)

        subscriber(
            "step_denied",
            {
                "kind": "llm",
                "reason": "model blocked",
                "model": "gpt-4",
            },
        )

        value = registry.get_sample_value(
            "veronica_denied_total",
            {"kind": "llm"},
        )
        assert value == 1.0

    def test_denied_total_unknown_kind(self) -> None:
        """Missing kind defaults to 'unknown'."""
        registry = CollectorRegistry()
        subscriber = MetricsSubscriber(registry=registry)

        subscriber("step_denied", {"reason": "blocked"})

        value = registry.get_sample_value(
            "veronica_denied_total",
            {"kind": "unknown"},
        )
        assert value == 1.0


# ---------------------------------------------------------------------------
# Adversarial / boundary / type-variation / breaking tests
# ---------------------------------------------------------------------------


class TestOrgPolicyBoundaryValues:
    """Boundary conditions and edge values that expose off-by-one or comparison bugs."""

    def test_clamp_at_exact_boundary(self) -> None:
        """ceiling_usd == max_ceiling_usd must NOT clamp (>= vs > check)."""
        policy = OrgPolicy(max_ceiling_usd=10.0)
        desired = _desired(ceiling_usd=10.0)
        result = policy.clamp(desired, _intent())
        assert result is desired  # same instance, no change

    def test_clamp_at_exact_boundary_timeout(self) -> None:
        """timeout_ms == max_timeout_ms must NOT clamp."""
        policy = OrgPolicy(max_timeout_ms=30_000)
        desired = _desired(timeout_ms=30_000)
        result = policy.clamp(desired, _intent())
        assert result is desired

    def test_clamp_at_exact_boundary_priority(self) -> None:
        """priority == max_priority must NOT clamp."""
        policy = OrgPolicy(max_priority=50)
        desired = _desired(priority=50)
        result = policy.clamp(desired, _intent())
        assert result is desired

    def test_clamp_ceiling_zero(self) -> None:
        """max_ceiling_usd=0.0 clamps everything to zero."""
        policy = OrgPolicy(max_ceiling_usd=0.0)
        result = policy.clamp(_desired(ceiling_usd=10.0), _intent())
        assert result.ceiling_usd == 0.0

    def test_clamp_timeout_zero(self) -> None:
        """max_timeout_ms=0 clamps everything to zero."""
        policy = OrgPolicy(max_timeout_ms=0)
        result = policy.clamp(_desired(timeout_ms=30_000), _intent())
        assert result.timeout_ms == 0

    def test_clamp_priority_zero(self) -> None:
        """max_priority=0 clamps everything to zero."""
        policy = OrgPolicy(max_priority=0)
        result = policy.clamp(_desired(priority=50), _intent())
        assert result.priority == 0

    def test_clamp_negative_ceiling(self) -> None:
        """max_ceiling_usd=-1.0 must still clamp (negative is stricter than any positive)."""
        policy = OrgPolicy(max_ceiling_usd=-1.0)
        result = policy.clamp(_desired(ceiling_usd=0.0), _intent())
        # 0.0 > -1.0, so it should clamp
        assert result.ceiling_usd == -1.0

    def test_clamp_all_fields_simultaneously(self) -> None:
        """All clamp fields fire at once."""
        policy = OrgPolicy(
            max_ceiling_usd=1.0,
            max_timeout_ms=5_000,
            max_priority=10,
            blocked_models=frozenset({"fallback-model"}),
        )
        desired = _desired(
            ceiling_usd=50.0,
            timeout_ms=60_000,
            priority=99,
            fallback_model="fallback-model",
        )
        result = policy.clamp(desired, _intent())
        assert result.ceiling_usd == 1.0
        assert result.timeout_ms == 5_000
        assert result.priority == 10
        assert result.fallback_model is None
        # Untouched fields preserved
        assert result.ceiling_steps == 100
        assert result.on_exceed == "halt"
        assert result.chain_id == "c1"


class TestOrgPolicyEmptyAndNoneInputs:
    """Edge cases: None, empty strings, empty frozensets."""

    def test_validate_model_none(self) -> None:
        """model=None must NOT match any blocked model."""
        policy = OrgPolicy(blocked_models=frozenset({"gpt-4"}))
        result = policy.validate(_intent(model=None))
        assert result is None

    def test_validate_tool_name_none(self) -> None:
        """tool_name=None must NOT match any blocked tool."""
        policy = OrgPolicy(blocked_tools=frozenset({"shell"}))
        result = policy.validate(_intent(tool_name=None))
        assert result is None

    def test_validate_model_empty_string(self) -> None:
        """model='' is falsy, must NOT match blocked models."""
        policy = OrgPolicy(blocked_models=frozenset({"gpt-4", ""}))
        # Empty string intent.model is falsy -> guard `if intent.model` skips
        result = policy.validate(_intent(model=""))
        assert result is None

    def test_validate_tool_empty_string(self) -> None:
        """tool_name='' is falsy, must NOT match blocked tools."""
        policy = OrgPolicy(blocked_tools=frozenset({"shell", ""}))
        result = policy.validate(_intent(tool_name=""))
        assert result is None

    def test_empty_policy_validates_everything(self) -> None:
        """OrgPolicy with no constraints allows everything."""
        policy = OrgPolicy()
        assert policy.validate(_intent(model="gpt-4")) is None
        assert policy.validate(_intent(tool_name="shell")) is None

    def test_empty_policy_clamps_nothing(self) -> None:
        """OrgPolicy with no constraints returns same instance."""
        policy = OrgPolicy()
        desired = _desired(ceiling_usd=999.0, timeout_ms=999_999, priority=999)
        result = policy.clamp(desired, _intent())
        assert result is desired

    def test_clamp_fallback_model_none_not_blocked(self) -> None:
        """fallback_model=None must not crash even if blocked_models has entries."""
        policy = OrgPolicy(blocked_models=frozenset({"gpt-4o"}))
        desired = _desired(fallback_model=None)
        result = policy.clamp(desired, _intent())
        assert result is desired  # no change


class TestOrgPolicyAdversarial:
    """'How do I break this?' -- adversarial attack patterns."""

    def test_partial_model_name_not_blocked(self) -> None:
        """'gpt-4' blocked must NOT block 'gpt-4-turbo' (exact match, not substring)."""
        policy = OrgPolicy(blocked_models=frozenset({"gpt-4"}))
        assert policy.validate(_intent(model="gpt-4-turbo")) is None

    def test_partial_tool_name_not_blocked(self) -> None:
        """'shell' blocked must NOT block 'shell_exec' (exact match, not substring)."""
        policy = OrgPolicy(blocked_tools=frozenset({"shell"}))
        assert policy.validate(_intent(tool_name="shell_exec")) is None

    def test_unicode_model_name_casefold(self) -> None:
        """Unicode casefold edge: German sharp S (SS <-> ss)."""
        # 'Stra\u00dfe'.casefold() == 'strasse'
        policy = OrgPolicy(blocked_models=frozenset({"Stra\u00dfe-model"}))
        # casefold("Stra\u00dfe-model") == "strasse-model"
        result = policy.validate(_intent(model="strasse-model"))
        assert result is not None  # blocked via casefold

    def test_validate_both_model_and_tool_blocked(self) -> None:
        """Both model AND tool blocked -- model check fires first."""
        policy = OrgPolicy(
            blocked_models=frozenset({"gpt-4"}),
            blocked_tools=frozenset({"shell"}),
        )
        result = policy.validate(_intent(model="gpt-4", tool_name="shell"))
        assert result is not None
        assert "model" in result  # model check is first in code

    def test_validate_tool_blocked_model_ok(self) -> None:
        """Model is OK but tool is blocked -- tool denial fires."""
        policy = OrgPolicy(
            blocked_models=frozenset({"gpt-4o"}),
            blocked_tools=frozenset({"shell"}),
        )
        result = policy.validate(_intent(model="gpt-4", tool_name="shell"))
        assert result is not None
        assert "tool" in result

    def test_frozen_immutability_after_clamp(self) -> None:
        """Clamping must not mutate the original DesiredPolicy."""
        policy = OrgPolicy(max_ceiling_usd=1.0)
        original = _desired(ceiling_usd=50.0)
        result = policy.clamp(original, _intent())
        # Original is untouched (frozen dataclass guarantees this)
        assert original.ceiling_usd == 50.0
        assert result.ceiling_usd == 1.0
        assert result is not original

    def test_frozen_org_policy_immutability(self) -> None:
        """OrgPolicy itself is frozen -- cannot mutate after creation."""
        policy = OrgPolicy(max_ceiling_usd=5.0)
        with pytest.raises(AttributeError):
            policy.max_ceiling_usd = 999.0  # type: ignore[misc]

    def test_cached_casefold_sets_consistency(self) -> None:
        """Internal _blocked_models_cf matches blocked_models via casefold."""
        models = frozenset({"GPT-4", "Claude-3.5-Sonnet", "LLAMA-70B"})
        policy = OrgPolicy(blocked_models=models)
        expected = frozenset(m.casefold() for m in models)
        assert policy._blocked_models_cf == expected
        assert policy._blocked_tools_cf == frozenset()

    def test_org_policy_equality_ignores_cached_fields(self) -> None:
        """Two OrgPolicies with same inputs are equal (compare=False on cached fields)."""
        p1 = OrgPolicy(blocked_models=frozenset({"GPT-4"}))
        p2 = OrgPolicy(blocked_models=frozenset({"GPT-4"}))
        assert p1 == p2


class TestOrgPolicyDeniedPathCoverage:
    """Verify _check_denial fires on ALL StepContext entry points."""

    def test_run_llm_raises_on_denial(self) -> None:
        """run_llm() must raise OrgPolicyDenied, not just run()."""
        vos = VeronicaOS(
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4"})),
        )
        fn = MagicMock(return_value="nope")
        with pytest.raises(OrgPolicyDenied, match="blocked"):
            with vos.step(_intent(model="gpt-4")) as ctx:
                ctx.run_llm(fn)
        fn.assert_not_called()

    def test_run_tool_raises_on_denial(self) -> None:
        """run_tool() must raise OrgPolicyDenied, not just run()."""
        vos = VeronicaOS(
            org_policy=OrgPolicy(blocked_tools=frozenset({"shell"})),
        )
        fn = MagicMock(return_value="nope")
        with pytest.raises(OrgPolicyDenied, match="blocked"):
            with vos.step(_intent(tool_name="shell", kind="tool")) as ctx:
                ctx.run_tool(fn)
        fn.assert_not_called()

    def test_deny_handle_has_zero_budget(self) -> None:
        """Denied StepHandle must have ceiling_usd=0, ceiling_steps=0, priority=0."""
        vos = VeronicaOS(
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4"})),
        )
        handle = vos.before_step(_intent(model="gpt-4"))
        assert handle.policy.ceiling_usd == 0.0
        assert handle.desired.ceiling_usd == 0.0
        assert handle.desired.ceiling_steps == 0
        assert handle.desired.ceiling_tokens_out == 0
        assert handle.desired.priority == 0
        assert handle.desired.fallback_model is None

    def test_deny_handle_cost_estimate_uses_intent_model(self) -> None:
        """Denied StepHandle cost.model_used must be intent.model, not 'denied'."""
        vos = VeronicaOS(
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4"})),
        )
        handle = vos.before_step(_intent(model="gpt-4"))
        assert handle.cost.model_used == "gpt-4"

    def test_deny_handle_cost_estimate_model_none(self) -> None:
        """When intent.model is None, cost.model_used falls back to 'unknown'."""
        vos = VeronicaOS(
            org_policy=OrgPolicy(blocked_tools=frozenset({"shell"})),
        )
        handle = vos.before_step(_intent(model=None, tool_name="shell"))
        assert handle.cost.model_used == "unknown"

    def test_deny_stage_time_has_org_policy(self) -> None:
        """Denied handle.decision_meta.stage_time_ms must include 'org_policy'."""
        vos = VeronicaOS(
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4"})),
        )
        handle = vos.before_step(_intent(model="gpt-4"))
        assert "org_policy" in handle.decision_meta.stage_time_ms
        assert handle.decision_meta.stage_time_ms["org_policy"] == 0.0

    def test_deny_decision_meta_fields(self) -> None:
        """Denied handle has risk_level='critical', recommendation='halt', degraded=True."""
        vos = VeronicaOS(
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4"})),
        )
        handle = vos.before_step(_intent(model="gpt-4"))
        assert handle.decision_meta.risk_level == "critical"
        assert handle.decision_meta.recommendation == "halt"
        assert handle.decision_meta.degraded is True
        assert handle.decision_meta.org_denial is not None


class TestOrgPolicyStepDeniedEvent:
    """Verify step_denied event payload completeness and edge cases."""

    def test_step_denied_payload_completeness(self) -> None:
        """step_denied event must contain all required fields."""
        emitter = BufferedEmitter()
        events: list[tuple[str, dict]] = []
        emitter.subscribe("test", lambda et, p: events.append((et, p)))

        vos = VeronicaOS(
            emitter=emitter,
            org_policy=OrgPolicy(blocked_tools=frozenset({"shell"})),
        )
        intent = _intent(model="gpt-4", tool_name="shell", kind="tool")

        with pytest.raises(OrgPolicyDenied):
            with vos.step(intent) as ctx:
                ctx.run(lambda: None)

        denied = [p for et, p in events if et == "step_denied"]
        assert len(denied) == 1
        payload = denied[0]
        # All required fields present
        assert payload["schema_version"] == 1
        assert payload["request_id"] == "r1"
        assert payload["step_id"] == "s1"
        assert payload["chain_id"] == "c1"
        assert payload["kind"] == "tool"
        assert "shell" in payload["reason"]
        assert payload["model"] == "gpt-4"
        assert payload["tool_name"] == "shell"

    def test_step_denied_not_emitted_for_clean_intent(self) -> None:
        """No step_denied event when intent is clean."""
        emitter = BufferedEmitter()
        events: list[tuple[str, dict]] = []
        emitter.subscribe("test", lambda et, p: events.append((et, p)))

        vos = VeronicaOS(
            emitter=emitter,
            org_policy=OrgPolicy(blocked_models=frozenset({"gpt-4o"})),
        )
        # gpt-4 is NOT blocked (gpt-4o is)
        with vos.step(_intent(model="gpt-4")) as ctx:
            ctx.run(lambda: "ok")

        denied = [p for et, p in events if et == "step_denied"]
        assert len(denied) == 0


class TestOrgPolicyStructuredLog:
    """Verify StructuredLogSubscriber handles step_denied correctly."""

    def test_step_denied_logged_as_json(self) -> None:
        """step_denied event is logged as parseable JSON with all fields."""
        import json as json_mod
        from veronica.structured_log_subscriber import StructuredLogSubscriber

        log_records: list[str] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                log_records.append(record.getMessage())

        subscriber = StructuredLogSubscriber()
        handler = CapturingHandler()
        logger = logging.getLogger("veronica.events")
        logger.addHandler(handler)
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            subscriber(
                "step_denied",
                {
                    "schema_version": 1,
                    "request_id": "r1",
                    "step_id": "s1",
                    "chain_id": "c1",
                    "kind": "llm",
                    "reason": "model blocked",
                    "model": "gpt-4",
                    "tool_name": None,
                },
            )
            assert len(log_records) == 1
            record = json_mod.loads(log_records[0])
            assert record["event"] == "step_denied"
            assert record["kind"] == "llm"
            assert record["reason"] == "model blocked"
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)

    def test_step_denied_ignores_unknown_event(self) -> None:
        """Unknown event types are silently ignored."""
        from veronica.structured_log_subscriber import StructuredLogSubscriber

        log_records: list[str] = []

        class CapturingHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                log_records.append(record.getMessage())

        subscriber = StructuredLogSubscriber()
        handler = CapturingHandler()
        logging.getLogger("veronica.events").addHandler(handler)
        try:
            subscriber("unknown_event", {"foo": "bar"})
            assert len(log_records) == 0
        finally:
            logging.getLogger("veronica.events").removeHandler(handler)


class TestOrgPolicyConcurrency:
    """Thread-safety tests for frozen OrgPolicy."""

    def test_concurrent_validate_no_race(self) -> None:
        """OrgPolicy.validate() is safe under concurrent access (frozen + cached sets)."""
        policy = OrgPolicy(
            blocked_models=frozenset({f"model-{i}" for i in range(100)}),
            blocked_tools=frozenset({f"tool-{i}" for i in range(100)}),
        )
        errors: list[Exception] = []
        results: list[str | None] = []

        def validate_worker(model: str) -> None:
            try:
                r = policy.validate(_intent(model=model))
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(50):
            # Half blocked, half clean
            model = f"model-{i}" if i < 25 else f"clean-{i}"
            t = threading.Thread(target=validate_worker, args=(model,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(errors) == 0, f"Concurrent errors: {errors}"
        assert len(results) == 50
        blocked = [r for r in results if r is not None]
        clean = [r for r in results if r is None]
        assert len(blocked) == 25
        assert len(clean) == 25

    def test_concurrent_clamp_no_race(self) -> None:
        """OrgPolicy.clamp() is safe under concurrent access."""
        policy = OrgPolicy(max_ceiling_usd=5.0, max_timeout_ms=10_000)
        errors: list[Exception] = []
        results: list[DesiredPolicy] = []

        def clamp_worker(ceiling: float) -> None:
            try:
                r = policy.clamp(
                    _desired(ceiling_usd=ceiling, timeout_ms=60_000),
                    _intent(),
                )
                results.append(r)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=clamp_worker, args=(float(i),)) for i in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert len(errors) == 0, f"Concurrent errors: {errors}"
        assert len(results) == 50
        for r in results:
            assert r.ceiling_usd <= 5.0
            assert r.timeout_ms <= 10_000
