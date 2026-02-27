# tests/test_org_policy.py
"""Tests for Phase 7: Org Policy Engine."""
from __future__ import annotations

import time

import pytest

from veronica.types import (
    DesiredPolicy,
    OrgPolicy,
    StepIntent,
)


def _intent(
    model: str | None = "gpt-4",
    tool_name: str | None = None,
    kind: str = "llm",
) -> StepIntent:
    return StepIntent(
        step_id="s1", request_id="r1", chain_id="c1",
        kind=kind, model=model, tool_name=tool_name,
        timeout_ms=30_000, metadata={},
    )


def _desired(
    ceiling_usd: float = 10.0,
    timeout_ms: int = 30_000,
    priority: int = 50,
    fallback_model: str | None = None,
) -> DesiredPolicy:
    return DesiredPolicy(
        chain_id="c1", ceiling_usd=ceiling_usd, ceiling_steps=100,
        ceiling_tokens_out=50_000, on_exceed="halt",
        fallback_model=fallback_model,
        timeout_ms=timeout_ms, priority=priority,
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
from unittest.mock import MagicMock

from veronica.os import OrgPolicyDenied, StepContext, VeronicaOS
from veronica.types import (
    CostEstimate,
    DecisionMeta,
    PolicyConfig,
    StepHandle,
)


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
