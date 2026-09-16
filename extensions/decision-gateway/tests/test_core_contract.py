"""MOCKED kernel contract tests. These do not establish real-kernel compatibility."""
from dataclasses import dataclass
from enum import Enum
import sys
from types import ModuleType, SimpleNamespace

import pytest

from veronica_gateway import (
    Assessment, BoundaryDenied, CoreCallBoundary, DecisionRequest, Provider, Stage,
)


@pytest.fixture
def kernel(monkeypatch):
    class Decision(Enum):
        ALLOW = "allow"
        HALT = "halt"
        RETRY = "retry"
        DEGRADE = "degrade"

    @dataclass(frozen=True)
    class WrapOptions:
        operation_name: str
        cost_estimate_hint: float
        retry_policy_override: int

    class ExecutionContext:
        def __init__(self, decision=Decision.ALLOW, invoke=True):
            self.decision = decision
            self.invoke = invoke
            self.aborted = False
            self.post_abort = False
            self.request_id = "r"
            self.options = None
            self.calls = 0
            self.callback_error = False

        def get_snapshot(self):
            return SimpleNamespace(aborted=self.aborted, request_id=self.request_id)

        def wrap_llm_call(self, fn, options):
            self.calls += 1
            self.options = options
            if self.invoke:
                try:
                    fn()
                except Exception:
                    self.callback_error = True
                    return Decision.RETRY
            self.aborted = self.post_abort
            return self.decision

    package = ModuleType("veronica_core")
    containment = ModuleType("veronica_core.containment")
    containment.ExecutionContext = ExecutionContext
    containment.WrapOptions = WrapOptions
    shield = ModuleType("veronica_core.shield")
    hooks = ModuleType("veronica_core.shield.types")
    hooks.Decision = Decision
    for name, module in {"veronica_core": package, "veronica_core.containment": containment,
                         "veronica_core.shield": shield, "veronica_core.shield.types": hooks}.items():
        monkeypatch.setitem(sys.modules, name, module)
    return ExecutionContext, Decision


def call_arguments():
    req = DecisionRequest("r", "task", ("a", "b"), "{}")
    calls = []
    def assess(request):
        calls.append(request.request_id)
        return Assessment("a", 0.95)
    return Provider("model", Stage.LIGHT, assess, cost_estimate_usd=0.02), req, calls


def test_allow_captures_answer_not_kernel_decision(kernel):
    Context, _ = kernel
    ctx = Context()
    p, req, calls = call_arguments()
    answer = CoreCallBoundary(ctx).call(p, req)
    assert answer == Assessment("a", 0.95)
    assert calls == ["r"]
    assert ctx.options.operation_name == "decision:model"
    assert ctx.options.cost_estimate_hint == 0.02
    assert ctx.options.retry_policy_override == 0


@pytest.mark.parametrize("verdict", ["HALT", "RETRY", "DEGRADE"])
@pytest.mark.parametrize("invoke", [True, False])
def test_non_allow_never_releases_answer(kernel, verdict, invoke):
    Context, Decision = kernel
    ctx = Context(getattr(Decision, verdict), invoke=invoke)
    p, req, _ = call_arguments()
    with pytest.raises(BoundaryDenied):
        CoreCallBoundary(ctx).call(p, req)


@pytest.mark.parametrize("condition", ["aborted", "mismatch", "not_called", "post_abort"])
def test_fail_closed_bindings_and_completion(kernel, condition):
    Context, _ = kernel
    ctx = Context()
    if condition == "aborted":
        ctx.aborted = True
    elif condition == "mismatch":
        ctx.request_id = "wrong"
    elif condition == "not_called":
        ctx.invoke = False
    else:
        ctx.post_abort = True
    p, req, calls = call_arguments()
    with pytest.raises(BoundaryDenied):
        CoreCallBoundary(ctx).call(p, req)
    if condition in ("aborted", "mismatch"):
        assert calls == []
        assert ctx.calls == 0


def test_fake_allow_string_cannot_authorize(kernel):
    Context, _ = kernel
    p, req, _ = call_arguments()
    with pytest.raises(BoundaryDenied):
        CoreCallBoundary(Context("allow")).call(p, req)


def test_no_context_duck_typing(kernel):
    with pytest.raises(TypeError):
        CoreCallBoundary(SimpleNamespace(wrap_llm_call=lambda *_: "allow"))


def test_provider_error_is_not_retried(kernel):
    Context, _ = kernel
    ctx = Context()
    p, req, _ = call_arguments()
    def fail(_):
        raise RuntimeError("provider failed")
    p = Provider("failure", Stage.LIGHT, fail)
    with pytest.raises(BoundaryDenied):
        CoreCallBoundary(ctx).call(p, req)
    assert ctx.calls == 1
    assert ctx.callback_error


def test_duplicate_callback_cannot_invoke_provider_twice(kernel):
    Context, Decision = kernel
    class RepeatingContext(Context):
        def wrap_llm_call(self, fn, options):
            fn()
            try:
                fn()
            except BoundaryDenied:
                return Decision.RETRY
            return Decision.ALLOW
    p, req, calls = call_arguments()
    with pytest.raises(BoundaryDenied):
        CoreCallBoundary(RepeatingContext()).call(p, req)
    assert calls == ["r"]
