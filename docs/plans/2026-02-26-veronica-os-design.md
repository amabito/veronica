# VERONICA OS -- Architecture Design

## Decision Record

- **Date**: 2026-02-26
- **Scope**: Full-layer blueprint for the VERONICA Execution OS
- **Deployment**: Python library (`pip install veronica`)
- **Target user**: LLM application developers (AG2, LangChain, CrewAI, etc.)
- **Persistence**: In-memory + file (JSON/SQLite), pluggable backend
- **Architecture**: Synchronous pipeline (A) with fire-and-forget event emission (B as side-channel)

---

## Design Principles

1. **The engine enforces. The OS decides.** veronica-core guarantees are unconditional. VERONICA extends them across agents, services, and time.
2. **Synchronous pipeline, no surprises.** The critical path is a pure, synchronous chain of Protocol calls. Each stage receives input, returns output, touches nothing else.
3. **Single writer to Store.** Only `VeronicaOS` writes to the Store -- one step, one atomic transaction. Pipeline stages are pure functions.
4. **Events are side effects.** `EventEmitter` is fire-and-forget. Exceptions are swallowed. Backpressure never propagates to the pipeline.
5. **Step is the atom.** One LLM or tool call is the smallest unit of observation and decision. Requests aggregate steps.

---

## Core Structure

```
Application (AG2, LangChain, etc.)
      |
  VeronicaOS              <-- synchronous pipeline (this library)
      |
      |  [before_step]         [after_step]
      |  Intent -> Handle       Handle + Snapshot -> commit
      |
      +-- Collector            StepOutcome from ContextSnapshot
      +-- Analyzer             intent + outcome + history -> signals
      +-- CostModel            intent + history + last_analysis -> estimate
      +-- Planner              analysis + cost + budget -> DesiredPolicy
      +-- Arbiter              desires[] -> {chain_id: PolicyConfig}
      +-- Store                atomic commit per step
      +-- [EventEmitter]       fire-and-forget, ring buffer, drop-oldest
      |
  veronica-core            <-- ExecutionContext + ShieldPipeline (enforcement)
      |
  LLM Providers
```

### Pipeline Flow

**before_step (pre-execution):**

```
1. Store.build_history(chain_id)        -> HistoryView
2. CostModel.estimate(intent, history,
                      last_analysis)    -> CostEstimate
3. Planner.plan(last_analysis, cost,
                budget_state)           -> DesiredPolicy
4. Arbiter.arbitrate(desires[],
                     remaining_usd)     -> {chain_id: PolicyConfig}
5. Return StepHandle(intent, policy, desired, cost, meta)
```

**after_step (post-execution):**

```
1. Collector.collect(snapshot)          -> StepOutcome
2. Store.build_history(chain_id)        -> HistoryView
3. Analyzer.analyze(intent, outcome,
                    history)            -> AnalysisResult
4. Update last_analysis
5. Store.commit(outcome, analysis,
                cost, desired, policy,
                decision_meta)          <- atomic
6. EventEmitter.emit(...)               <- fire-and-forget
```

### Stage Rules

| Rule | Detail |
|------|--------|
| Pure I/O | Collector, Analyzer, CostModel, Planner, Arbiter never touch Store |
| Single writer | Only VeronicaOS writes to Store (1 step = 1 txn) |
| Time budget | Each stage has a ms budget; exceeded -> DEGRADE to conservative default |
| Arbiter scope | Planner = local optimum (one chain). Arbiter = global optimum (all chains) |
| CostModel position | Before Planner. Cost estimate is Planner's input, not a parallel peer |
| EventEmitter | fire-and-forget, swallow exceptions, ring buffer + drop-oldest |
| Audit data | Goes into Store.commit (not EventEmitter) -- must not be dropped |

### Stage Time Budgets (defaults)

| Stage | Budget |
|-------|--------|
| Collector | 5 ms |
| Analyzer | 20 ms |
| CostModel | 10 ms |
| Planner | 30 ms |
| Arbiter | 20 ms |

Exceeded -> fall back to conservative/passthrough logic. Pipeline never blocks.

---

## Types

### Step Lifecycle

```python
@dataclass(frozen=True)
class StepIntent:
    """Pre-execution declaration. What the application intends to do."""
    step_id: str
    request_id: str
    chain_id: str
    kind: Literal["llm", "tool", "system"]
    model: str | None
    tool_name: str | None
    timeout_ms: int
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class StepOutcome:
    """Post-execution record. What actually happened."""
    step_id: str
    request_id: str
    chain_id: str
    kind: Literal["llm", "tool", "system"]
    status: Literal["ok", "halted", "error", "timeout"]
    cost_usd: float
    tokens_in: int
    tokens_out: int
    elapsed_ms: float
    model: str | None
    events: tuple[SafetyEvent, ...]
    timestamp_ms: int


@dataclass(frozen=True)
class StepHandle:
    """Returned by before_step. Passed to after_step.
    Carries all pre-execution decisions forward so that
    after_step can commit the full record atomically."""
    intent: StepIntent
    policy: PolicyConfig
    desired: DesiredPolicy
    cost: CostEstimate
    decision_meta: DecisionMeta
```

### Analysis and Planning

```python
@dataclass(frozen=True)
class HistoryView:
    """Lightweight history slice. Statistics, not raw logs."""
    chain_id: str
    last_n: tuple[StepOutcome, ...]   # capped (default 50)
    rolling_cost_usd: float
    failure_streak: int
    depth: int
    loop_score: float


@dataclass(frozen=True)
class Signal:
    """One detected pattern."""
    kind: str           # "cost_acceleration", "repeated_failure",
                        # "depth_anomaly", "intent_deviation", "loop", ...
    severity: Literal["info", "warning", "critical"]
    detail: str


@dataclass(frozen=True)
class AnalysisResult:
    """Analyzer output. Detected patterns from intent vs outcome."""
    signals: tuple[Signal, ...]
    risk_level: Literal["nominal", "elevated", "critical"]
    recommendation: Literal["continue", "tighten", "loosen", "halt"]


@dataclass(frozen=True)
class CostEstimate:
    """CostModel output. Predicted cost of the next step."""
    estimated_usd: float
    confidence: float           # 0.0 - 1.0
    model_used: str
    basis: Literal["historical", "pricing_table", "fallback"]


@dataclass(frozen=True)
class BudgetState:
    """Current budget position. Planner's input."""
    request_remaining_usd: float
    chain_remaining_usd: float
    window_remaining_steps: int


@dataclass(frozen=True)
class DesiredPolicy:
    """Planner output. One chain's desired limits (local optimum)."""
    chain_id: str
    ceiling_usd: float
    ceiling_steps: int
    ceiling_tokens_out: int
    on_exceed: Literal["halt", "degrade", "queue"]
    fallback_model: str | None
    timeout_ms: int
    priority: int


@dataclass(frozen=True)
class DecisionMeta:
    """Audit record. Why this PolicyConfig was chosen."""
    risk_level: str
    recommendation: str
    degraded: bool              # True if any stage hit its time budget
    stage_time_ms: Mapping[str, float]
```

---

## Protocols

Each protocol is a single-method interface. Default implementations are provided.

```python
class CollectorProtocol(Protocol):
    def collect(self, snapshot: ContextSnapshot) -> StepOutcome: ...


class AnalyzerProtocol(Protocol):
    def analyze(self, intent: StepIntent, outcome: StepOutcome,
                history: HistoryView) -> AnalysisResult: ...


class CostModelProtocol(Protocol):
    def estimate(self, intent: StepIntent, history: HistoryView,
                 last_analysis: AnalysisResult | None) -> CostEstimate: ...


class PlannerProtocol(Protocol):
    def plan(self, analysis: AnalysisResult | None,
             cost: CostEstimate,
             budget: BudgetState) -> DesiredPolicy: ...


class ArbiterProtocol(Protocol):
    def arbitrate(self, desires: Sequence[DesiredPolicy],
                  budget_remaining_usd: float) -> Mapping[str, PolicyConfig]: ...


class StoreProtocol(Protocol):
    def commit(self, outcome: StepOutcome, analysis: AnalysisResult,
               cost: CostEstimate, desired: DesiredPolicy,
               policy: PolicyConfig, meta: DecisionMeta) -> None: ...

    def build_history(self, chain_id: str, limit: int = 50) -> HistoryView: ...


class EventEmitterProtocol(Protocol):
    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None: ...
```

### Dependency Graph (strictly unidirectional)

```
StepIntent + HistoryView
    -> CostModel -> CostEstimate
        -> Planner -> DesiredPolicy  (+ BudgetState)
            -> Arbiter -> PolicyConfig  (global optimum)
                -> veronica-core (execution)
                    -> Collector -> StepOutcome
                        -> Analyzer -> AnalysisResult  (+ StepIntent)
                            -> Store.commit (atomic)
                            -> EventEmitter (fire-and-forget)
```

No stage references another stage. No stage reads from Store. VeronicaOS orchestrates all data flow.

---

## VeronicaOS Entry Point

```python
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
    ) -> None:
        # None -> default implementations:
        #   SimpleCollector, RuleAnalyzer, TableCostModel,
        #   SimplePlanner, PassthroughArbiter, FileStore, NullEmitter

    def before_step(self, intent: StepIntent) -> StepHandle:
        """Pre-execution pipeline. Returns a handle carrying the policy."""
        ...

    def after_step(self, handle: StepHandle,
                   snapshot: ContextSnapshot) -> None:
        """Post-execution pipeline. Commits to Store, emits events."""
        ...
```

### Application Usage

```python
from veronica import VeronicaOS
from veronica.types import StepIntent
from veronica_core.containment import ExecutionContext

os = VeronicaOS()  # all defaults

# 1. Declare intent
intent = StepIntent(
    step_id="s-001",
    request_id="r-abc",
    chain_id="c-1",
    kind="llm",
    model="gpt-4",
    tool_name=None,
    timeout_ms=30_000,
    metadata={},
)

# 2. Get policy
handle = os.before_step(intent)

# 3. Execute under veronica-core containment
ctx = ExecutionContext(config=handle.policy.to_exec_config())
result = ctx.wrap_llm_call(call_gpt4, WrapOptions())

# 4. Report outcome
os.after_step(handle, ctx.get_snapshot())
```

---

## Default Implementations

| Protocol | Default | Behavior |
|----------|---------|----------|
| Collector | `SimpleCollector` | Maps ContextSnapshot fields to StepOutcome 1:1 |
| Analyzer | `RuleAnalyzer` | 3 rules: halt tightening (-10%), clean run loosening (+5%), depth guard (>=8) |
| CostModel | `TableCostModel` | Static pricing table (model -> $/1K tokens). Fallback: $0.01 |
| Planner | `SimplePlanner` | Rule-based ceiling from analysis + cost + budget |
| Arbiter | `PassthroughArbiter` | Single-chain passthrough. Multi-chain: proportional split |
| Store | `FileStore` | JSON lines file. One file per request_id. Atomic append |
| EventEmitter | `NullEmitter` | No-op. Ring buffer implementation available as `BufferedEmitter` |

---

## Package Structure

```
veronica/
    __init__.py             # VeronicaOS re-export
    os.py                   # VeronicaOS class
    types.py                # All frozen dataclasses
    protocols.py            # All Protocol definitions
    collector.py            # SimpleCollector
    analyzer.py             # RuleAnalyzer
    cost_model.py           # TableCostModel
    planner.py              # SimplePlanner
    arbiter.py              # PassthroughArbiter
    store.py                # FileStore, MemoryStore
    emitter.py              # NullEmitter, BufferedEmitter
    _timeguard.py           # Stage time budget enforcement
```

No sub-packages. Flat structure. One module per concern.

---

## Relationship to veronica-core

| Concern | veronica-core | VERONICA OS |
|---------|---------------|-------------|
| Path | Critical (every LLM call) | Management (between calls) |
| Timing | Synchronous, in-band | Synchronous, between steps |
| Guarantees | Unconditional enforcement | Best-effort planning |
| Failure mode | HALT (safe) | DEGRADE to conservative defaults |
| Data flow | ExecutionContext -> ContextSnapshot | ContextSnapshot -> PolicyConfig -> ExecutionConfig |
| State | Per-chain (ExecutionContext owns) | Cross-chain (Store owns history) |

**Bridge**: `PolicyConfig.to_exec_config()` is the only coupling point. The OS produces a PolicyConfig; veronica-core consumes an ExecutionConfig. The conversion is a pure function owned by the OS library.

---

## Phase Roadmap

### Phase 1: Foundation

- VeronicaOS with all 7 default implementations
- StepHandle flow (before_step / after_step)
- FileStore with JSON lines
- 3-rule RuleAnalyzer (from existing planner.md)
- TableCostModel with static pricing
- SimplePlanner (rule-based)
- PassthroughArbiter
- NullEmitter

### Phase 2: Adaptive

- HistoryAnalyzer (pattern detection from historical data)
- RegressionCostModel (learns from StepOutcome history)
- AdaptivePlanner (feedback-driven ceiling adjustment)
- ProportionalArbiter (multi-chain budget splitting)
- BufferedEmitter with ring buffer

### Phase 3: Multi-context coordination

- Cross-service Arbiter (distributed budget via Redis)
- Organization-level policy engine
- Dashboard subscriber (EventEmitter -> WebSocket)
- Compliance/audit subscriber
- Session-level aggregation in Store

---

## Open Questions (deferred)

1. **PolicyConfig schema finalization** -- current docs/policy-config.md fields vs what Planner actually needs. Resolve during Phase 1 implementation.
2. **Store rotation/compaction** -- JSON lines grow unbounded. Add rotation in Phase 2.
3. **Multi-process Arbiter** -- single-process `PassthroughArbiter` is Phase 1. Cross-process needs IPC (Phase 3).
4. **Async variant** -- synchronous-only for now. Async wrappers are Phase 3 if demand exists.
