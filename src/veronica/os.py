# src/veronica/os.py
"""VERONICA OS -- the main orchestrator."""
from __future__ import annotations

import logging
import time
from typing import Any, Mapping

from veronica_core.containment.execution_context import ContextSnapshot

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
        self._last_analysis: AnalysisResult | None = None
        self._total_spent_usd: float = 0.0

    def before_step(self, intent: StepIntent) -> StepHandle:
        """Pre-execution pipeline. Returns a StepHandle carrying the policy."""
        stage_times: dict[str, float] = {}
        degraded = False

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
        remaining = self._request_budget_usd - self._total_spent_usd
        budget = BudgetState(
            request_remaining_usd=remaining,
            chain_remaining_usd=remaining,
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
        self._total_spent_usd += outcome.cost_usd

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
