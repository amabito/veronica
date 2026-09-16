"""Real-kernel integration tests. Skip explicitly when the optional kernel is absent."""
import pytest

pytest.importorskip("veronica_core", reason="optional real veronica-core is not installed")
from veronica_core.containment import ChainMetadata, ExecutionConfig, ExecutionContext
from veronica_gateway import (
    Assessment, CoreCallBoundary, DecisionGateway, DecisionRequest, MemoryAuditSink,
    Outcome, Provider, RoutingPolicy, Stage,
)


def make_context(*, max_steps=8, max_cost_usd=1.0):
    return ExecutionContext(
        config=ExecutionConfig(max_cost_usd=max_cost_usd, max_steps=max_steps,
                               max_retries_total=1, timeout_ms=0),
        metadata=ChainMetadata(request_id="r", chain_id="triage-chain"),
    )


def run(context, providers):
    return DecisionGateway(
        RoutingPolicy("triage-v1", "triage", ("a", "b")), providers,
        boundary=CoreCallBoundary(context), audit=MemoryAuditSink(),
    ).evaluate(DecisionRequest("r", "triage", ("a", "b"), "{}"))


def test_real_allow_and_reply_capture():
    with make_context() as ctx:
        result = run(ctx, (Provider("rule", Stage.RULE, lambda _: Assessment("a", 1)),))
        assert result.choice == "a"
        assert result.outcome is Outcome.REVIEW_REQUIRED
        assert ctx.get_snapshot().step_count == 1


def test_real_abort_prevents_provider_call():
    calls = []
    with make_context() as ctx:
        ctx.abort("test denial")
        result = run(ctx, (Provider("rule", Stage.RULE, lambda _: calls.append(1)),))
        assert result.outcome is Outcome.BLOCKED
        assert calls == []


def test_real_shared_step_limit_blocks_fallback():
    calls = []
    with make_context(max_steps=1) as ctx:
        result = run(ctx, (Provider("rule", Stage.RULE, lambda _: Assessment(None, 0)),
                           Provider("model", Stage.LIGHT, lambda _: calls.append(1))))
        assert result.outcome is Outcome.BLOCKED
        assert calls == []


def test_real_cost_estimate_rejects_before_dispatch():
    calls = []
    with make_context(max_cost_usd=0.01) as ctx:
        result = run(ctx, (Provider("model", Stage.LIGHT, lambda _: calls.append(1),
                                   cost_estimate_usd=0.02),))
        assert result.outcome is Outcome.BLOCKED
        assert calls == []


def test_real_provider_exception_never_advances_to_fallback():
    calls = []
    def fail(_):
        calls.append("failure")
        raise RuntimeError("expected provider failure")
    with make_context() as ctx:
        result = run(ctx, (Provider("rule", Stage.RULE, fail),
                           Provider("fallback", Stage.LIGHT, lambda _: calls.append("fallback"))))
        assert result.outcome is Outcome.BLOCKED
        assert calls == ["failure"]
