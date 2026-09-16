"""Sequential advisory routing outside the kernel and existing control-plane Planner."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import Lock
from typing import Protocol
from uuid import uuid4

from .contracts import (
    Assessment, DecisionRequest, GatewayResult, Outcome, Provider, RoutingPolicy,
    Stage, digest,
)


class BoundaryDenied(RuntimeError):
    """The enforcement boundary did not allow this inference call to complete."""


class AuditUnavailable(RuntimeError):
    """Audit failed. Do not use a result or retry the paid operation automatically."""


class CallBoundary(Protocol):
    def call(self, provider: Provider, request: DecisionRequest) -> Assessment: ...


@dataclass(frozen=True)
class AuditEvent:
    evaluation_id: str
    sequence: int
    request_id: str
    request_digest: str
    config_digest: str
    event: str
    reason: str
    provider_id: str | None = None
    stage: str | None = None
    outcome: str | None = None
    cost_estimate_usd: float | None = None


class AuditSink(Protocol):
    def write(self, event: AuditEvent) -> None:
        """Acknowledge storage or raise. A best-effort/drop-on-error sink is unsuitable."""
        ...


class MemoryAuditSink:
    """Bounded, thread-safe DEMO sink. Neither durable nor tamper resistant."""
    def __init__(self, max_events: int = 1000) -> None:
        if type(max_events) is not int or max_events < 1:
            raise ValueError("max_events must be a positive integer")
        self._max_events = max_events
        self._events: list[AuditEvent] = []
        self._lock = Lock()

    def write(self, event: AuditEvent) -> None:
        with self._lock:
            if len(self._events) >= self._max_events:
                raise AuditUnavailable("demo audit capacity reached")
            self._events.append(event)

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        with self._lock:
            return tuple(self._events)


class DecisionGateway:
    """One immutable task policy, ordered providers, and a mandatory call boundary.

    Only valid low-confidence answers or explicit abstentions advance the cascade.
    Boundary denials, exceptions, malformed answers and audit failures NEVER trigger
    a second provider. Every returned value is advisory, including rule answers.
    """
    def __init__(self, policy: RoutingPolicy, providers: tuple[Provider, ...], *,
                 boundary: CallBoundary, audit: AuditSink) -> None:
        if type(policy) is not RoutingPolicy:
            raise ValueError("expected RoutingPolicy")
        if type(providers) is not tuple or not 1 <= len(providers) <= 8:
            raise ValueError("providers must be an immutable tuple of 1..8 entries")
        if any(type(p) is not Provider for p in providers):
            raise ValueError("invalid provider")
        if len({p.provider_id for p in providers}) != len(providers):
            raise ValueError("duplicate provider id")
        order = {Stage.RULE: 0, Stage.LIGHT: 1, Stage.FRONTIER: 2}
        ranks = [order[p.stage] for p in providers]
        if ranks != sorted(ranks):
            raise ValueError("providers must be ordered rule -> light -> frontier")
        if not callable(getattr(boundary, "call", None)):
            raise ValueError("an enforcement boundary is required")
        if not callable(getattr(audit, "write", None)):
            raise ValueError("an acknowledged audit sink is required")
        self._policy = policy
        self._providers = providers
        self._boundary = boundary
        self._audit = audit
        self._config_digest = digest({"schema": 1, "policy": asdict(policy),
                                      "providers": [p.descriptor() for p in providers]})

    def evaluate(self, request: DecisionRequest) -> GatewayResult:
        if type(request) is not DecisionRequest:
            raise ValueError("expected DecisionRequest")
        evaluation_id = uuid4().hex
        sequence = 0
        attempts = 0

        def emit(event: str, reason: str, provider: Provider | None = None,
                 outcome: Outcome | None = None) -> None:
            nonlocal sequence
            record = AuditEvent(
                evaluation_id, sequence, request.request_id, request.fingerprint,
                self._config_digest, event, reason,
                None if provider is None else provider.provider_id,
                None if provider is None else provider.stage.value,
                None if outcome is None else outcome.value,
                None if provider is None else provider.cost_estimate_usd,
            )
            try:
                self._audit.write(record)
            except Exception:
                # Never leak provider/payload/exception text into caller-visible errors.
                raise AuditUnavailable("gateway audit unavailable; no result released") from None
            sequence += 1

        def finish(outcome: Outcome, reason: str,
                   assessment: Assessment | None = None) -> GatewayResult:
            emit("decision_finished", reason, outcome=outcome)
            return GatewayResult(
                evaluation_id, request.request_id, outcome,
                None if assessment is None else assessment.choice,
                None if assessment is None else assessment.confidence,
                reason, attempts, self._config_digest,
            )

        emit("decision_started", "start")
        if request.task != self._policy.task or request.choices != self._policy.choices:
            return finish(Outcome.BLOCKED, "task_or_schema_mismatch")

        for provider in self._providers:
            if provider.external and not self._policy.allow_external:
                emit("provider_skipped", "external_disallowed", provider)
                continue
            if attempts >= self._policy.max_attempts:
                return finish(Outcome.REVIEW_REQUIRED, "attempt_limit")
            # This acknowledgement precedes every provider call, including free rules.
            emit("attempt_started", "dispatch_requested", provider)
            attempts += 1
            try:
                assessment = self._boundary.call(provider, request)
            except BoundaryDenied:
                return finish(Outcome.BLOCKED, "boundary_denied")
            except Exception:
                return finish(Outcome.BLOCKED, "boundary_or_provider_error")

            if type(assessment) is not Assessment:
                return finish(Outcome.BLOCKED, "invalid_assessment")
            # Revalidate at the untrusted plugin boundary, including values altered
            # through object.__setattr__ despite the frozen dataclass.
            try:
                assessment = Assessment(assessment.choice, assessment.confidence)
            except (TypeError, ValueError):
                return finish(Outcome.BLOCKED, "invalid_assessment")
            if assessment.choice is not None and assessment.choice not in request.choices:
                return finish(Outcome.BLOCKED, "choice_outside_schema")
            accepted = (assessment.choice is not None
                        and assessment.confidence >= self._policy.min_confidence)
            emit("attempt_finished", "accepted" if accepted else "abstain_or_low_confidence",
                 provider)
            if accepted:
                outcome = (Outcome.REVIEW_REQUIRED if self._policy.require_human
                           else Outcome.RECOMMENDATION)
                return finish(outcome, "human_required" if self._policy.require_human
                              else "advisory_only", assessment)

        return finish(Outcome.REVIEW_REQUIRED, "no_qualified_answer")
