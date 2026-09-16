"""Gateway tests use a transparent TEST-ONLY boundary, not production enforcement."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, asdict, replace
import json

import pytest

from veronica_gateway import (
    Assessment, AuditUnavailable, BoundaryDenied, DecisionGateway, DecisionRequest,
    MemoryAuditSink, Outcome, Provider, RoutingPolicy, Stage,
)


class TestBoundary:
    __test__ = False

    def __init__(self, denied=()):
        self.denied = denied
        self.calls = []

    def call(self, provider, request):
        self.calls.append(provider.provider_id)
        if provider.provider_id in self.denied:
            raise BoundaryDenied("do-not-leak")
        return provider.assess(request)


def request(**kwargs):
    return DecisionRequest(**dict(request_id="req-1", task="triage",
                                  choices=("billing", "technical"), context_json="{}") | kwargs)


def policy(**kwargs):
    return RoutingPolicy(**dict(policy_id="triage-v1", task="triage",
                                choices=("billing", "technical")) | kwargs)


def provider(name="rules", stage=Stage.RULE, answer=None, **kwargs):
    assessment = answer if answer is not None else Assessment("billing", 1.0)
    return Provider(name, stage, lambda _: assessment, **kwargs)


def gateway(providers=None, *, pol=None, boundary=None, audit=None):
    return DecisionGateway(pol or policy(), providers or (provider(),),
                           boundary=boundary or TestBoundary(),
                           audit=audit if audit is not None else MemoryAuditSink())


def test_rule_is_advisory_and_human_review_is_default():
    result = gateway().evaluate(request())
    assert result.outcome is Outcome.REVIEW_REQUIRED
    assert result.choice == "billing"
    assert result.reason == "human_required"
    assert result.authorization_granted is False
    with pytest.raises(FrozenInstanceError):
        result.choice = "technical"


def test_recommendation_still_cannot_authorize_an_action():
    result = gateway(pol=policy(require_human=False)).evaluate(request())
    assert result.outcome is Outcome.RECOMMENDATION
    assert result.authorization_granted is False


def test_rule_short_circuits_paid_models():
    boundary = TestBoundary()
    result = gateway((provider(), provider("light", Stage.LIGHT)), boundary=boundary).evaluate(request())
    assert result.attempts == 1
    assert boundary.calls == ["rules"]


def test_full_cascade_and_input_snapshots():
    def rule(req):
        req.context()["message"] = "tampered"
        return Assessment(None, 0)

    def frontier(req):
        assert req.context()["message"] == "original"
        return Assessment("technical", 0.97)

    providers = (Provider("rules", Stage.RULE, rule),
                 provider("light", Stage.LIGHT, Assessment("billing", 0.3)),
                 Provider("frontier", Stage.FRONTIER, frontier))
    boundary = TestBoundary()
    result = gateway(providers, boundary=boundary).evaluate(
        request(context_json='{"message":"original"}'))
    assert boundary.calls == ["rules", "light", "frontier"]
    assert result.choice == "technical"
    assert result.attempts == 3


def test_threshold_equality_is_accepted():
    result = gateway((provider(answer=Assessment("billing", 0.9)),)).evaluate(request())
    assert result.choice == "billing"


def test_no_confident_result_has_no_choice():
    result = gateway((provider(answer=Assessment(None, 0)),)).evaluate(request())
    assert result.reason == "no_qualified_answer"
    assert result.outcome is Outcome.REVIEW_REQUIRED
    assert result.choice is None


def test_attempt_limit_never_calls_next_provider():
    boundary = TestBoundary()
    result = gateway((provider(answer=Assessment(None, 0)), provider("light", Stage.LIGHT)),
                     pol=policy(max_attempts=1), boundary=boundary).evaluate(request())
    assert result.reason == "attempt_limit"
    assert result.choice is None
    assert boundary.calls == ["rules"]


@pytest.mark.parametrize("overrides", [{"task": "other"}, {"choices": ("technical", "billing")},
                                       {"choices": ("yes", "no")}])
def test_request_cannot_change_trusted_policy(overrides):
    boundary = TestBoundary()
    result = gateway(boundary=boundary).evaluate(request(**overrides))
    assert result.reason == "task_or_schema_mismatch"
    assert result.outcome is Outcome.BLOCKED
    assert boundary.calls == []


def test_external_is_opt_in_and_skips_do_not_consume_attempts():
    boundary = TestBoundary()
    providers = (provider("remote", Stage.LIGHT, external=True),
                 provider("local", Stage.FRONTIER))
    result = gateway(providers, pol=policy(max_attempts=1), boundary=boundary).evaluate(request())
    assert boundary.calls == ["local"]
    assert result.attempts == 1
    boundary.calls.clear()
    gateway(providers, pol=policy(allow_external=True), boundary=boundary).evaluate(request())
    assert boundary.calls == ["remote"]


def test_only_disallowed_external_providers_go_to_human():
    boundary = TestBoundary()
    result = gateway((provider("remote", Stage.LIGHT, external=True),),
                     boundary=boundary).evaluate(request())
    assert result.outcome is Outcome.REVIEW_REQUIRED
    assert result.attempts == 0
    assert boundary.calls == []


@pytest.mark.parametrize("denied", ["rules", "light"])
def test_denial_never_uses_frontier_as_bypass(denied):
    providers = (provider(answer=Assessment(None, 0)),
                 provider("light", Stage.LIGHT, Assessment(None, 0)),
                 provider("frontier", Stage.FRONTIER))
    boundary = TestBoundary((denied,))
    result = gateway(providers, boundary=boundary).evaluate(request())
    assert result.outcome is Outcome.BLOCKED
    assert result.reason == "boundary_denied"
    assert "frontier" not in boundary.calls
    assert result.choice is None


def test_provider_exception_is_redacted_and_does_not_fallback():
    def fail(_):
        raise RuntimeError("SECRET-PROVIDER-TOKEN")
    boundary = TestBoundary()
    sink = MemoryAuditSink()
    result = gateway((Provider("rules", Stage.RULE, fail), provider("next", Stage.LIGHT)),
                     boundary=boundary, audit=sink).evaluate(request())
    assert result.reason == "boundary_or_provider_error"
    assert boundary.calls == ["rules"]
    assert "SECRET" not in repr(result) + repr(sink.events)


@pytest.mark.parametrize("answer", [None, {}, "billing", True, Assessment("outside", 1)])
def test_invalid_response_stops_cascade(answer):
    boundary = TestBoundary()
    result = gateway((Provider("rules", Stage.RULE, lambda _: answer),
                      provider("next", Stage.LIGHT)), boundary=boundary).evaluate(request())
    assert result.outcome is Outcome.BLOCKED
    assert boundary.calls == ["rules"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 2, True, "1"])
def test_tampered_frozen_assessment_is_revalidated(value):
    bad = Assessment("billing", 1)
    object.__setattr__(bad, "confidence", value)
    result = gateway((provider(answer=bad),)).evaluate(request())
    assert result.reason == "invalid_assessment"
    assert result.choice is None


@pytest.mark.parametrize("failure_at, expected_calls", [(0, 0), (1, 0), (2, 1), (3, 1)])
def test_audit_failure_before_or_after_inference_releases_no_result(failure_at, expected_calls):
    class BrokenAudit:
        writes = 0
        def write(self, event):
            if self.writes == failure_at:
                raise RuntimeError("SECRET-AUDIT-CREDENTIAL")
            self.writes += 1
    boundary = TestBoundary()
    with pytest.raises(AuditUnavailable, match="no result released") as error:
        gateway(boundary=boundary, audit=BrokenAudit()).evaluate(request())
    assert len(boundary.calls) == expected_calls
    assert "SECRET" not in str(error.value)


def test_audit_has_no_raw_payload_answer_or_confidence():
    sink = MemoryAuditSink()
    req = request(context_json='{"message":"VERY-SENSITIVE-TEXT"}')
    result = gateway(audit=sink).evaluate(req)
    events = sink.events
    serialized = json.dumps([asdict(e) for e in events])
    assert "VERY-SENSITIVE-TEXT" not in serialized + repr(req)
    assert "billing" not in serialized
    assert [e.sequence for e in events] == list(range(len(events)))
    assert {e.config_digest for e in events} == {result.config_digest}
    assert {e.request_digest for e in events} == {req.fingerprint}
    assert len({e.evaluation_id for e in events}) == 1


def test_config_digest_covers_routing_and_provider_metadata():
    base = gateway().evaluate(request()).config_digest
    variants = [gateway(pol=policy(min_confidence=0.8)),
                gateway(pol=policy(allow_external=True)),
                gateway(pol=policy(require_human=False)),
                gateway((provider(implementation_id="v2"),)),
                gateway((provider("light", Stage.LIGHT, cost_estimate_usd=0.01),))]
    assert all(item.evaluate(request()).config_digest != base for item in variants)
    assert gateway().evaluate(request()).config_digest == base


def test_parallel_requests_have_separate_receipts_and_no_state_leak():
    sink = MemoryAuditSink()
    gw = gateway(audit=sink)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda n: gw.evaluate(request(request_id=f"r-{n}")), range(40)))
    assert len({r.evaluation_id for r in results}) == 40
    assert all(r.attempts == 1 and not r.authorization_granted for r in results)
    for result in results:
        events = [e for e in sink.events if e.evaluation_id == result.evaluation_id]
        assert [e.sequence for e in events] == [0, 1, 2, 3]
        assert all(e.request_id == result.request_id for e in events)


def test_repeated_request_id_is_not_claimed_to_be_idempotent():
    gw = gateway()
    a, b = gw.evaluate(request()), gw.evaluate(request())
    assert a.evaluation_id != b.evaluation_id


def test_memory_audit_overflow_fails_closed_instead_of_dropping_events():
    sink = MemoryAuditSink(max_events=1)
    boundary = TestBoundary()
    with pytest.raises(AuditUnavailable):
        gateway(boundary=boundary, audit=sink).evaluate(request())
    assert boundary.calls == []
    assert len(sink.events) == 1


def test_cancellation_is_not_swallowed():
    def interrupt(_):
        raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        gateway((Provider("rules", Stage.RULE, interrupt),)).evaluate(request())


@pytest.mark.parametrize("identifier", ["", "with space", "\n", "x" * 129, "日本語", None, True, 1])
def test_bad_request_identifiers(identifier):
    with pytest.raises(ValueError):
        request(request_id=identifier)


@pytest.mark.parametrize("choices", [[], ["a", "b"], ("a",), ("a", "a"), ("a", None),
                                     tuple(f"v{i}" for i in range(33))])
def test_bad_choice_schema(choices):
    with pytest.raises(ValueError):
        request(choices=choices)


@pytest.mark.parametrize("text", ["", "null", "[]", "1", '{"a":NaN}', '{"a":Infinity}',
                                  '{"a":1e999}', '{"a":1,"a":2}', '{"nested":{"a":1,"a":2}}',
                                  '{"x":' + '[' * 18 + '0' + ']' * 18 + '}',
                                  '{"x":"' + 'a' * 65536 + '"}', "\ud800", {},
                                  '{"x":"' + 'あ' * 12000 + '"}'])
def test_bad_contexts(text):
    with pytest.raises(ValueError):
        request(context_json=text)


def test_canonical_input_and_copy_isolation():
    a = request(context_json=' {"b": 2,"a": [1]} ')
    b = request(context_json='{"a":[1],"b":2}')
    assert a.fingerprint == b.fingerprint
    value = a.context()
    value["a"].append(2)
    assert a.context() == {"a": [1], "b": 2}


@pytest.mark.parametrize("value", [True, "1", None, -0.1, 1.01, float("nan"), float("inf"), 10**400])
def test_confidence_validation(value):
    with pytest.raises(ValueError):
        Assessment("billing", value)


def test_abstention_requires_zero_confidence():
    with pytest.raises(ValueError):
        Assessment(None, 0.9)


@pytest.mark.parametrize("kwargs", [{"max_attempts": 0}, {"max_attempts": 9}, {"max_attempts": True},
                                    {"allow_external": "yes"}, {"require_human": 0},
                                    {"min_confidence": float("nan")}])
def test_bad_policy(kwargs):
    with pytest.raises(ValueError):
        policy(**kwargs)


@pytest.mark.parametrize("kwargs", [{"external": True}, {"external": "no"},
                                    {"cost_estimate_usd": 0.1}, {"cost_estimate_usd": -1},
                                    {"cost_estimate_usd": float("nan")},
                                    {"implementation_id": ""}, {"stage": "rule"}])
def test_bad_provider(kwargs):
    values = dict(provider_id="rules", stage=Stage.RULE, assess=lambda _: Assessment(None, 0))
    values.update(kwargs)
    with pytest.raises(ValueError):
        Provider(**values)


@pytest.mark.parametrize("providers", [(), [], (provider(), provider()),
                                       (provider("f", Stage.FRONTIER), provider()),
                                       ("not-a-provider",)])
def test_bad_provider_registry(providers):
    with pytest.raises(ValueError):
        DecisionGateway(policy(), providers, boundary=TestBoundary(), audit=MemoryAuditSink())


def test_mandatory_boundary_and_audit():
    with pytest.raises(ValueError):
        DecisionGateway(policy(), (provider(),), boundary=None, audit=MemoryAuditSink())
    with pytest.raises(ValueError):
        DecisionGateway(policy(), (provider(),), boundary=TestBoundary(), audit=None)


def test_invalid_top_level_types():
    with pytest.raises(ValueError):
        DecisionGateway({}, (provider(),), boundary=TestBoundary(), audit=MemoryAuditSink())
    with pytest.raises(ValueError):
        gateway().evaluate({})
    with pytest.raises(ValueError):
        MemoryAuditSink(max_events=True)


def test_valid_finite_cost_estimate_changes_config():
    p = provider("model", Stage.LIGHT, cost_estimate_usd=0.1)
    assert p.descriptor()["cost_estimate_usd"] == 0.1
    assert replace(p, cost_estimate_usd=0.2).descriptor() != p.descriptor()
