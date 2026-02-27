# src/veronica/os.py
"""VERONICA OS -- the main orchestrator."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import contextmanager
from itertools import count
from typing import Any, Callable, Iterator, Mapping, TypeVar

from veronica_core.containment.execution_context import (
    ChainMetadata,
    ContextSnapshot,
    ExecutionContext,
)

from veronica._timeguard import TimeBudgetExceeded, run_with_budget
from veronica.analyzer import RuleAnalyzer
from veronica.arbiter import PassthroughArbiter
from veronica.collector import SimpleCollector
from veronica.cost_model import TableCostModel
from veronica.emitter import NullEmitter
from veronica.planner import SimplePlanner
from veronica.protocols import (
    AnalyzerProtocol,
    ArbiterProtocol,
    CollectorProtocol,
    CostModelProtocol,
    EventEmitterProtocol,
    PlannerProtocol,
    StoreProtocol,
)
from veronica.store import MemoryStore
from veronica.types import (
    AnalysisResult,
    BudgetState,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    OrgPolicy,
    PolicyConfig,
    StepHandle,
    StepIntent,
)

logger = logging.getLogger(__name__)

_DEFAULT_STAGE_BUDGETS: dict[str, float] = {
    "collector": 5.0,
    "analyzer": 20.0,
    "cost_model": 10.0,
    "planner": 30.0,
    "arbiter": 20.0,
}

_DEFAULT_REQUEST_BUDGET_USD = 100.0

_KNOWN_STAGES = frozenset({
    "collector", "analyzer", "cost_model", "planner", "arbiter",
    "store", "emit",
})

_step_counter = count(1)
_DEFAULT_TIMEOUT_MS = 30_000

T = TypeVar("T")


def _make_fallback_snapshot(intent: StepIntent, reason: str) -> ContextSnapshot:
    """Build a minimal ContextSnapshot when get_snapshot() fails.

    Defensive: even if SafetyEvent creation fails, the snapshot
    is still returned (with empty events). The snapshot itself
    must never raise.
    """
    events: list[Any] = []
    try:
        from veronica_core.shield.event import SafetyEvent
        from veronica_core.shield.hooks import Decision
        from datetime import datetime, timezone

        events = [SafetyEvent(
            event_type="snapshot_failed",
            decision=Decision.HALT,
            reason=reason,
            hook="veronica_os",
            ts=datetime.now(timezone.utc),
            metadata={"step_id": intent.step_id},
        )]
    except Exception:
        pass  # events stays empty; snapshot still valid

    return ContextSnapshot(
        chain_id=intent.chain_id,
        request_id=intent.request_id,
        step_count=0,
        cost_usd_accumulated=0.0,
        retries_used=0,
        aborted=True,
        abort_reason=reason,
        elapsed_ms=0.0,
        nodes=[],
        events=events,
    )


class OrgPolicyDenied(Exception):
    """Raised when org policy denies the step."""


class StepContext:
    """Yielded by vos.step(). Wraps ExecutionContext."""

    def __init__(self, handle: StepHandle, exec_ctx: ExecutionContext) -> None:
        self.handle = handle
        self.exec_ctx = exec_ctx

    def _check_denial(self) -> None:
        if self.handle.decision_meta.org_denial is not None:
            raise OrgPolicyDenied(self.handle.decision_meta.org_denial)

    def run(self, fn: Callable[[], T]) -> T:
        """Execute fn, dispatching by intent.kind.

        kind="tool" -> wrap_tool_call, otherwise -> wrap_llm_call.
        kind="system" is treated as llm (no wrap_system_call in veronica-core).
        """
        self._check_denial()
        if self.handle.intent.kind == "tool":
            return self.exec_ctx.wrap_tool_call(fn)
        return self.exec_ctx.wrap_llm_call(fn)

    def run_llm(self, fn: Callable[[], T]) -> T:
        self._check_denial()
        return self.exec_ctx.wrap_llm_call(fn)

    def run_tool(self, fn: Callable[[], T]) -> T:
        self._check_denial()
        return self.exec_ctx.wrap_tool_call(fn)

    @property
    def policy(self) -> PolicyConfig:
        return self.handle.policy


class VeronicaOS:
    """VERONICA Execution OS.

    Synchronous pipeline that sits above veronica-core.
    Decides what limits to set; veronica-core enforces them.
    """

    def __init__(
        self,
        collector: CollectorProtocol | None = None,
        analyzer: AnalyzerProtocol | None = None,
        cost_model: CostModelProtocol | None = None,
        planner: PlannerProtocol | None = None,
        arbiter: ArbiterProtocol | None = None,
        store: StoreProtocol | None = None,
        emitter: EventEmitterProtocol | None = None,
        stage_budgets_ms: Mapping[str, float] | None = None,
        request_budget_usd: float = _DEFAULT_REQUEST_BUDGET_USD,
        org_policy: OrgPolicy | None = None,
    ) -> None:
        self._collector = collector or SimpleCollector()
        self._analyzer = analyzer or RuleAnalyzer()
        self._cost_model = cost_model or TableCostModel()
        self._planner = planner or SimplePlanner()
        self._arbiter = arbiter or PassthroughArbiter()
        self._store = store or MemoryStore()
        self._emitter = emitter or NullEmitter()
        self._budgets = dict(stage_budgets_ms or _DEFAULT_STAGE_BUDGETS)
        self._request_budget_usd = request_budget_usd
        self._org_policy = org_policy
        self._last_analysis: AnalysisResult | None = None
        self._total_spent_usd: float = 0.0
        self._chain_spent_usd: dict[str, float] = {}
        self._lock = threading.Lock()

    @contextmanager
    def step(self, intent: StepIntent) -> Iterator[StepContext]:
        """Context manager for one execution step.

        Guarantees after_step always runs, even on exception.
        On get_snapshot() failure, a fallback ContextSnapshot is used.
        """
        handle = self.before_step(intent)
        metadata = ChainMetadata(
            request_id=intent.request_id,
            chain_id=intent.chain_id,
        )
        ctx = ExecutionContext(config=handle.policy.to_exec_config(), metadata=metadata)
        step_ctx = StepContext(handle=handle, exec_ctx=ctx)
        try:
            yield step_ctx
        finally:
            try:
                snapshot = ctx.get_snapshot()
            except Exception:
                logger.exception(
                    "[VERONICA_OS] snapshot retrieval failed; using fallback"
                )
                snapshot = _make_fallback_snapshot(
                    intent, "snapshot_retrieval_failed"
                )
            self.after_step(handle, snapshot)

    def _normalize_intent(self, intent: StepIntent) -> StepIntent:
        """Fill missing StepIntent fields with safe defaults.

        Does not mutate the original (frozen dataclass). Returns a new
        instance only if any field was empty.
        """
        changes: dict[str, Any] = {}
        if not intent.request_id:
            changes["request_id"] = uuid.uuid4().hex
        if not intent.chain_id:
            changes["chain_id"] = "default"
        if not intent.step_id:
            changes["step_id"] = f"step-{next(_step_counter)}"
        if not intent.timeout_ms:
            changes["timeout_ms"] = _DEFAULT_TIMEOUT_MS
        if not intent.metadata:
            changes["metadata"] = {}

        if not changes:
            return intent

        from dataclasses import asdict

        fields = asdict(intent)
        fields.update(changes)
        return StepIntent(**fields)

    def run_step(self, intent: StepIntent, fn: Callable[[], T]) -> T:
        """Execute one step with full OS pipeline. Convenience wrapper.

        Equivalent to::

            with vos.step(intent) as s:
                return s.run(fn)

        Empty intent fields are auto-filled (see _normalize_intent).
        """
        with self.step(self._normalize_intent(intent)) as s:
            return s.run(fn)

    def before_step(self, intent: StepIntent) -> StepHandle:
        """Pre-execution pipeline. Returns a StepHandle carrying the policy."""
        stage_times: dict[str, float] = {}
        degraded = False

        # 0. Org policy validation (hard block, pre-Planner)
        if self._org_policy is not None:
            denial = self._org_policy.validate(intent)
            if denial is not None:
                logger.warning("[VERONICA_OS] org policy denied: %s", denial)
                try:
                    self._emitter.emit("step_denied", {
                        "schema_version": 1,
                        "request_id": intent.request_id,
                        "step_id": intent.step_id,
                        "chain_id": intent.chain_id,
                        "kind": intent.kind,
                        "reason": denial,
                        "model": intent.model,
                        "tool_name": intent.tool_name,
                    })
                except Exception:
                    pass  # fire-and-forget
                policy = PolicyConfig(
                    chain_id=intent.chain_id,
                    ceiling_usd=0.0,
                    on_exceed="halt",
                    issued_at=time.time(),
                )
                return StepHandle(
                    intent=intent,
                    policy=policy,
                    desired=DesiredPolicy(
                        chain_id=intent.chain_id,
                        ceiling_usd=0.0, ceiling_steps=0,
                        ceiling_tokens_out=0, on_exceed="halt",
                        fallback_model=None,
                        timeout_ms=intent.timeout_ms, priority=0,
                    ),
                    cost=CostEstimate(
                        estimated_usd=0.0, confidence=1.0,
                        model_used=intent.model or "unknown",
                        basis="fallback",
                    ),
                    decision_meta=DecisionMeta(
                        risk_level="critical",
                        recommendation="halt",
                        degraded=True,
                        stage_time_ms={"org_policy": 0.0},
                        org_denial=denial,
                    ),
                )

        # 1. Build history
        history = self._store.build_history(intent.chain_id)

        # 2. CostModel
        try:
            cost, elapsed = run_with_budget(
                lambda: self._cost_model.estimate(
                    intent, history, self._last_analysis,
                ),
                self._budgets.get("cost_model", 10.0),
                "cost_model",
                return_elapsed=True,
            )
            stage_times["cost_model"] = elapsed
        except TimeBudgetExceeded as e:
            logger.warning("[VERONICA_OS] %s", e)
            degraded = True
            stage_times["cost_model"] = e.actual_ms
            cost = CostEstimate(
                estimated_usd=0.01, confidence=0.1,
                model_used="fallback", basis="fallback",
            )

        # 3. Budget state
        with self._lock:
            total_spent = self._total_spent_usd
            chain_spent = self._chain_spent_usd.get(intent.chain_id, 0.0)
        remaining = self._request_budget_usd - total_spent
        chain_remaining = self._request_budget_usd - chain_spent
        budget = BudgetState(
            request_remaining_usd=remaining,
            chain_remaining_usd=chain_remaining,
            window_remaining_steps=100,
        )

        # 4. Planner
        try:
            desired, elapsed = run_with_budget(
                lambda: self._planner.plan(
                    self._last_analysis, cost, budget,
                ),
                self._budgets.get("planner", 30.0),
                "planner",
                return_elapsed=True,
            )
            stage_times["planner"] = elapsed
        except TimeBudgetExceeded as e:
            logger.warning("[VERONICA_OS] %s", e)
            degraded = True
            stage_times["planner"] = e.actual_ms
            desired = DesiredPolicy(
                chain_id=intent.chain_id,
                ceiling_usd=min(1.0, remaining),
                ceiling_steps=100,
                ceiling_tokens_out=50_000,
                on_exceed="halt",
                fallback_model=None,
                timeout_ms=intent.timeout_ms,
                priority=50,
            )

        # 4.5 Org policy clamp (post-Planner numerical cap)
        if self._org_policy is not None:
            desired = self._org_policy.clamp(desired, intent)

        # Fill chain_id from intent if planner left it empty
        if not desired.chain_id:
            desired = DesiredPolicy(
                chain_id=intent.chain_id,
                ceiling_usd=desired.ceiling_usd,
                ceiling_steps=desired.ceiling_steps,
                ceiling_tokens_out=desired.ceiling_tokens_out,
                on_exceed=desired.on_exceed,
                fallback_model=desired.fallback_model,
                timeout_ms=desired.timeout_ms,
                priority=desired.priority,
            )

        # 4b. Inject arbitration context for RedisArbiter idempotency
        if hasattr(self._arbiter, "set_arbitration_context"):
            self._arbiter.set_arbitration_context(
                request_id=intent.request_id,
                step_id=intent.step_id,
            )

        # 5. Arbiter
        try:
            configs, elapsed = run_with_budget(
                lambda: self._arbiter.arbitrate([desired], remaining),
                self._budgets.get("arbiter", 20.0),
                "arbiter",
                return_elapsed=True,
            )
            stage_times["arbiter"] = elapsed
        except TimeBudgetExceeded as e:
            logger.warning("[VERONICA_OS] %s", e)
            degraded = True
            stage_times["arbiter"] = e.actual_ms
            configs = {intent.chain_id: PolicyConfig(
                chain_id=intent.chain_id,
                ceiling_usd=desired.ceiling_usd,
                on_exceed="halt",
                issued_at=time.time(),
            )}

        policy = configs.get(intent.chain_id, PolicyConfig(
            chain_id=intent.chain_id,
            ceiling_usd=1.0,
            on_exceed="halt",
            issued_at=time.time(),
        ))

        meta = DecisionMeta(
            risk_level=self._last_analysis.risk_level if self._last_analysis else "nominal",
            recommendation=self._last_analysis.recommendation if self._last_analysis else "continue",
            degraded=degraded,
            stage_time_ms=stage_times,
        )

        return StepHandle(
            intent=intent,
            policy=policy,
            desired=desired,
            cost=cost,
            decision_meta=meta,
        )

    def after_step(self, handle: StepHandle, snapshot: ContextSnapshot) -> None:
        """Post-execution pipeline. Commits to Store, emits events."""
        stage_times: dict[str, float] = {}

        # 1. Collector
        try:
            outcome, elapsed = run_with_budget(
                lambda: self._collector.collect(snapshot),
                self._budgets.get("collector", 5.0),
                "collector",
                return_elapsed=True,
            )
            stage_times["collector"] = elapsed
        except TimeBudgetExceeded as e:
            logger.warning("[VERONICA_OS] %s", e)
            stage_times["collector"] = e.actual_ms
            return  # Cannot proceed without outcome

        # 2. History
        history = self._store.build_history(outcome.chain_id)

        # 3. Analyzer
        try:
            analysis, elapsed = run_with_budget(
                lambda: self._analyzer.analyze(
                    handle.intent, outcome, history,
                ),
                self._budgets.get("analyzer", 20.0),
                "analyzer",
                return_elapsed=True,
            )
            stage_times["analyzer"] = elapsed
        except TimeBudgetExceeded as e:
            logger.warning("[VERONICA_OS] %s", e)
            stage_times["analyzer"] = e.actual_ms
            analysis = AnalysisResult(
                signals=(), risk_level="nominal", recommendation="continue",
            )

        # 4. Update state
        self._last_analysis = analysis
        with self._lock:
            self._total_spent_usd += outcome.cost_usd
            self._chain_spent_usd[outcome.chain_id] = (
                self._chain_spent_usd.get(outcome.chain_id, 0.0) + outcome.cost_usd
            )

        # 5. Store commit (atomic, timed)
        if hasattr(self._store, "set_budget_context"):
            remaining = self._request_budget_usd - self._total_spent_usd
            self._store.set_budget_context(
                ceiling_usd=self._request_budget_usd,
                remaining_usd=remaining,
            )
        t0 = time.monotonic()
        self._store.commit(
            outcome, analysis, handle.cost,
            handle.desired, handle.policy, handle.decision_meta,
        )
        stage_times["store"] = (time.monotonic() - t0) * 1000

        # 5b. Settle reservation with actual cost
        if hasattr(self._arbiter, "settle"):
            self._arbiter.settle(
                request_id=handle.intent.request_id,
                step_id=handle.intent.step_id,
                actual_cost_usd=outcome.cost_usd,
            )

        # 6. Merge before_step + after_step stage times
        all_stage_times = dict(handle.decision_meta.stage_time_ms)
        all_stage_times.update(stage_times)

        # 7. EventEmitter (fire-and-forget, timed)
        payload: dict[str, Any] = {
            "schema_version": 1,
            "request_id": outcome.request_id,
            "step_id": outcome.step_id,
            "chain_id": outcome.chain_id,
            "kind": outcome.kind,
            "status": outcome.status,
            "cost_usd": outcome.cost_usd,
            "tokens_in": outcome.tokens_in,
            "tokens_out": outcome.tokens_out,
            "elapsed_ms": outcome.elapsed_ms,
            "risk_level": analysis.risk_level,
            "recommendation": analysis.recommendation,
            "degraded": handle.decision_meta.degraded,
            "degrade_reason": self._degrade_reason(handle),
            "signals": [
                {"kind": s.kind, "severity": s.severity}
                for s in analysis.signals
            ],
            "stage_time_ms": {
                k: v for k, v in all_stage_times.items()
                if k in _KNOWN_STAGES
            },
        }
        t0 = time.monotonic()
        try:
            self._emitter.emit("step_completed", payload)
        except Exception:
            logger.debug("[VERONICA_OS] EventEmitter error (swallowed)")
        stage_times["emit"] = (time.monotonic() - t0) * 1000

    def _degrade_reason(self, handle: StepHandle) -> str | None:
        """Determine why this step was degraded."""
        if not handle.decision_meta.degraded:
            return None
        if handle.policy.fallback_model is not None:
            return "fallback_model"
        for stage, elapsed in handle.decision_meta.stage_time_ms.items():
            budget = self._budgets.get(stage, 0.0)
            if budget > 0 and elapsed > budget:
                return "time_budget"
        return "other"
