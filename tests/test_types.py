# tests/test_types.py
"""Tests for veronica.types -- all frozen dataclasses."""

from __future__ import annotations

import time

import pytest

from veronica.types import (
    AnalysisResult,
    BudgetState,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    HistoryView,
    PolicyConfig,
    Signal,
    StepHandle,
    StepIntent,
    StepOutcome,
)


class TestStepIntent:
    def test_construction(self) -> None:
        intent = StepIntent(
            step_id="s1",
            request_id="r1",
            chain_id="c1",
            kind="llm",
            model="gpt-4",
            tool_name=None,
            timeout_ms=30_000,
            metadata={},
        )
        assert intent.step_id == "s1"
        assert intent.kind == "llm"

    def test_frozen(self) -> None:
        intent = StepIntent(
            step_id="s1",
            request_id="r1",
            chain_id="c1",
            kind="llm",
            model=None,
            tool_name=None,
            timeout_ms=0,
            metadata={},
        )
        with pytest.raises(AttributeError):
            intent.step_id = "s2"  # type: ignore[misc]


class TestStepOutcome:
    def test_construction(self) -> None:
        outcome = StepOutcome(
            step_id="s1",
            request_id="r1",
            chain_id="c1",
            kind="llm",
            status="ok",
            cost_usd=0.005,
            tokens_in=100,
            tokens_out=50,
            elapsed_ms=123.4,
            model="gpt-4",
            events=(),
            timestamp_ms=int(time.time() * 1000),
        )
        assert outcome.status == "ok"
        assert outcome.cost_usd == 0.005


class TestPolicyConfig:
    def test_minimal(self) -> None:
        pc = PolicyConfig(
            chain_id="c1",
            ceiling_usd=1.0,
            on_exceed="halt",
            issued_at=time.time(),
        )
        assert pc.ceiling_usd == 1.0
        assert pc.ceiling_tokens_out is None

    def test_to_exec_config(self) -> None:
        pc = PolicyConfig(
            chain_id="c1",
            ceiling_usd=2.50,
            ceiling_steps=30,
            ceiling_tokens_out=50_000,
            on_exceed="halt",
            timeout_ms=30_000,
            issued_at=time.time(),
        )
        ec = pc.to_exec_config()
        assert ec.max_cost_usd == 2.50
        assert ec.max_steps == 30
        assert ec.timeout_ms == 30_000

    def test_to_exec_config_defaults(self) -> None:
        """None fields get safe defaults."""
        pc = PolicyConfig(
            chain_id="c1",
            ceiling_usd=1.0,
            on_exceed="halt",
            issued_at=time.time(),
        )
        ec = pc.to_exec_config()
        assert ec.max_steps > 0
        assert ec.max_retries_total > 0

    def test_frozen(self) -> None:
        pc = PolicyConfig(
            chain_id="c1",
            ceiling_usd=1.0,
            on_exceed="halt",
            issued_at=time.time(),
        )
        with pytest.raises(AttributeError):
            pc.ceiling_usd = 99.0  # type: ignore[misc]


class TestHistoryView:
    def test_empty_history(self) -> None:
        hv = HistoryView(
            chain_id="c1",
            last_n=(),
            rolling_cost_usd=0.0,
            failure_streak=0,
            depth=0,
            loop_score=0.0,
        )
        assert len(hv.last_n) == 0
        assert hv.failure_streak == 0

    def test_phase2_fields_have_defaults(self) -> None:
        """Phase 2 fields must all have defaults for backward compatibility."""
        hv = HistoryView(
            chain_id="c1",
            last_n=(),
            rolling_cost_usd=0.0,
            failure_streak=0,
            depth=0,
            loop_score=0.0,
        )
        assert hv.success_streak == 0
        assert hv.cost_per_step_ema == 0.0
        assert hv.cost_per_step_ema_by_model == {}
        assert hv.latency_ema_ms == {}
        assert hv.budget_headroom_ratio == 1.0

    def test_phase2_fields_can_be_set(self) -> None:
        """Phase 2 fields can be explicitly provided."""
        hv = HistoryView(
            chain_id="c1",
            last_n=(),
            rolling_cost_usd=0.0,
            failure_streak=0,
            depth=10,
            loop_score=0.0,
            success_streak=5,
            cost_per_step_ema=0.05,
            cost_per_step_ema_by_model={"gpt-4": 0.03},
            latency_ema_ms={"gpt-4": 150.0},
            budget_headroom_ratio=0.7,
        )
        assert hv.success_streak == 5
        assert hv.cost_per_step_ema == 0.05
        assert hv.cost_per_step_ema_by_model["gpt-4"] == 0.03
        assert hv.latency_ema_ms["gpt-4"] == 150.0
        assert hv.budget_headroom_ratio == 0.7


class TestAnalysisResult:
    def test_nominal(self) -> None:
        ar = AnalysisResult(
            signals=(),
            risk_level="nominal",
            recommendation="continue",
        )
        assert ar.risk_level == "nominal"


class TestSignal:
    def test_construction(self) -> None:
        sig = Signal(kind="cost_acceleration", severity="warning", detail="2x spike")
        assert sig.severity == "warning"


class TestCostEstimate:
    def test_construction(self) -> None:
        ce = CostEstimate(
            estimated_usd=0.01,
            confidence=0.8,
            model_used="gpt-4",
            basis="pricing_table",
        )
        assert ce.confidence == 0.8


class TestBudgetState:
    def test_construction(self) -> None:
        bs = BudgetState(
            request_remaining_usd=5.0,
            chain_remaining_usd=2.0,
            window_remaining_steps=10,
        )
        assert bs.chain_remaining_usd == 2.0


class TestDesiredPolicy:
    def test_construction(self) -> None:
        dp = DesiredPolicy(
            chain_id="c1",
            ceiling_usd=1.0,
            ceiling_steps=10,
            ceiling_tokens_out=5000,
            on_exceed="halt",
            fallback_model=None,
            timeout_ms=30_000,
            priority=50,
        )
        assert dp.priority == 50


class TestDecisionMeta:
    def test_construction(self) -> None:
        dm = DecisionMeta(
            risk_level="nominal",
            recommendation="continue",
            degraded=False,
            stage_time_ms={"collector": 1.2},
        )
        assert not dm.degraded


class TestStepHandle:
    def test_construction(self) -> None:
        intent = StepIntent(
            step_id="s1",
            request_id="r1",
            chain_id="c1",
            kind="llm",
            model="gpt-4",
            tool_name=None,
            timeout_ms=30_000,
            metadata={},
        )
        pc = PolicyConfig(
            chain_id="c1",
            ceiling_usd=1.0,
            on_exceed="halt",
            issued_at=time.time(),
        )
        dp = DesiredPolicy(
            chain_id="c1",
            ceiling_usd=1.0,
            ceiling_steps=10,
            ceiling_tokens_out=5000,
            on_exceed="halt",
            fallback_model=None,
            timeout_ms=30_000,
            priority=50,
        )
        ce = CostEstimate(
            estimated_usd=0.01,
            confidence=0.8,
            model_used="gpt-4",
            basis="pricing_table",
        )
        dm = DecisionMeta(
            risk_level="nominal",
            recommendation="continue",
            degraded=False,
            stage_time_ms={},
        )
        handle = StepHandle(
            intent=intent,
            policy=pc,
            desired=dp,
            cost=ce,
            decision_meta=dm,
        )
        assert handle.intent.step_id == "s1"
        assert handle.policy.ceiling_usd == 1.0
