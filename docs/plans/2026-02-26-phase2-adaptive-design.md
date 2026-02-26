# VERONICA OS Phase 2: Adaptive Layer Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace all Phase 1 stub implementations with adaptive, history-aware components

**Architecture:** Protocol-injection approach (Approach A: Incremental Replacement). New implementations conform to existing Protocols -- no changes to `os.py`. VeronicaOS constructor injection swaps Phase 1 defaults for Phase 2 implementations.

**Tech Stack:** Python 3.11+, dataclasses (frozen), veronica-core ContextSnapshot

**Design review:** 3 rounds of GPT review + 8-scenario destruction test. All mitigations incorporated.

---

## Scope

| Component | Phase 1 (stub) | Phase 2 (adaptive) |
|-----------|----------------|---------------------|
| Analyzer | `RuleAnalyzer` (3 rules) | `HistoryAnalyzer` (6 patterns) |
| CostModel | `TableCostModel` (static table) | `RegressionCostModel` (EMA-based) |
| Planner | `SimplePlanner` (single drift) | `AdaptivePlanner` (error-class + cooldown) |
| Arbiter | `PassthroughArbiter` | `ProportionalArbiter` (priority-weighted) |
| Emitter | `NullEmitter` | `BufferedEmitter` (ring buffer + subscribers) |
| Store | `MemoryStore` | `FileStore` (JSONL + EMA computation) |
| Types | `HistoryView` (6 fields) | `HistoryView` (11 fields, backward-compat) |

**Invariant:** `os.py` is NOT modified. All changes are new files + `types.py` expansion.

---

## 1. HistoryView Expansion (types.py)

### Current (Phase 1)

```python
@dataclass(frozen=True)
class HistoryView:
    chain_id: str
    last_n: tuple[StepOutcome, ...]
    rolling_cost_usd: float
    failure_streak: int
    depth: int
    loop_score: float
```

### Phase 2 Target

```python
@dataclass(frozen=True)
class HistoryView:
    chain_id: str
    last_n: tuple[StepOutcome, ...]
    rolling_cost_usd: float
    failure_streak: int
    depth: int
    loop_score: float

    # Phase 2 additions (all with defaults for backward compatibility)
    success_streak: int = 0
    cost_per_step_ema: float = 0.0
    cost_per_step_ema_by_model: Mapping[str, float] = field(default_factory=dict)
    latency_ema_ms: Mapping[str, float] = field(default_factory=dict)
    budget_headroom_ratio: float = 1.0
```

**Backward compatibility:** All new fields have defaults. Phase 1 MemoryStore code continues to work without changes.

### Field semantics

| Field | Source | Description |
|-------|--------|-------------|
| `success_streak` | Store | Consecutive `status == "ok"` count (reset on non-ok) |
| `cost_per_step_ema` | Store | Chain-wide EMA of `cost_usd` (alpha=0.3) |
| `cost_per_step_ema_by_model` | Store | Per-model EMA. Key = `model` string |
| `latency_ema_ms` | Store | Per-model EMA of `elapsed_ms` |
| `budget_headroom_ratio` | Store | `remaining_budget / ceiling_usd`. 0.0-1.0 |

### EMA Update Timing Rule

- `build_history()` returns EMA values computed from **all previous commits** (baseline)
- `commit()` updates EMA with the current outcome **after** storing
- This ensures Analyzer sees the EMA **before** the current step's impact

---

## 2. HistoryAnalyzer (analyzer.py replacement)

**File:** `src/veronica/history_analyzer.py`

**Protocol:** `AnalyzerProtocol.analyze(intent, outcome, history) -> AnalysisResult`

**Pure function:** No internal state. All statistics come from `HistoryView`.

### 6 Detection Patterns

#### Pattern 1: halt_tighten
- **Trigger:** `outcome.status in ("halted", "error")`
- **Severity:** `critical` if halted, `warning` if error
- **Signal:** `Signal(kind="halt_tighten", severity=..., detail=...)`

#### Pattern 2: clean_loosen
- **Trigger:** `outcome.status == "ok"` AND `history.success_streak >= 3` AND `history.budget_headroom_ratio >= 0.5`
- **Severity:** `info`
- **Rationale:** Requires sustained success (3+) AND sufficient budget room. Prevents premature loosening after one good result.

#### Pattern 3: depth_guard (2-stage)
- **Soft trigger:** `history.depth >= 6` -> severity=`warning`, recommendation stays `tighten`
- **Hard trigger:** `history.depth >= 10` -> severity=`critical`, recommendation escalates to `halt`
- **Rationale:** Soft warning allows graceful adjustment; hard limit prevents runaway chains.

#### Pattern 4: cost_acceleration
- **Trigger:** `outcome.cost_usd > history.cost_per_step_ema * 2.0` AND `history.depth >= 5`
- **Guard:** `min_history=5` (insufficient data -> skip pattern)
- **Guard:** `cost_per_step_ema <= EPS` -> skip (prevent division issues)
- **Severity:** `warning`
- **Rationale:** Detects sudden cost spikes relative to historical baseline. EMA from Store, not computed in Analyzer.

#### Pattern 5: loop_detection
- **Trigger:** `history.loop_score >= 0.7`
- **Loop key:** `(kind, model_or_tool, status)` 3-tuple computed by Store
- **Severity:** `warning`

#### Pattern 6: latency_anomaly (info-only)
- **Trigger:** `outcome.elapsed_ms > latency_ema * 3.0` (3x historical average for this model)
- **Severity:** `info`
- **Note:** No recommendation change. Informational signal for monitoring.

### Signal Composition Rules

1. All matching patterns emit signals (tuple, not exclusive)
2. `risk_level` = max severity across all signals (`critical > elevated > nominal`)
3. `recommendation` = single value from highest-priority matching signal:
   - `halt` > `tighten` > `loosen` > `continue`
   - If no signal matches, recommendation = `continue`

---

## 3. RegressionCostModel (cost_model.py replacement)

**File:** `src/veronica/regression_cost_model.py`

**Protocol:** `CostModelProtocol.estimate(intent, history, last_analysis) -> CostEstimate`

**Stateless.** Reads EMA values from `HistoryView`.

### Estimation Logic

```python
def estimate(self, intent, history, last_analysis):
    model_key = intent.model or "unknown"
    ema = history.cost_per_step_ema_by_model.get(model_key)

    if ema is not None and ema > EPS:
        estimated = ema
        basis = "historical"
        confidence = _graduated_confidence(history.depth)
    else:
        # Fallback to static pricing table
        estimated = PRICING_TABLE.get(model_key, {}).get("max_usd_per_step", 0.01)
        basis = "pricing_table"
        confidence = 0.2

    return CostEstimate(
        estimated_usd=estimated,
        confidence=confidence,
        model_used=model_key,
        basis=basis,
    )
```

### Graduated Confidence

| History Depth | Confidence |
|---------------|------------|
| < 5 | 0.60 |
| 5-19 | 0.75 |
| >= 20 | 0.85 |

**Rationale:** Confidence increases with sample size but never reaches 1.0 (model behavior is inherently uncertain).

### Pricing Table

Reuses `veronica_core.cost.PRICING_TABLE` for fallback. Takes `max(input_price, output_price)` as conservative estimate.

---

## 4. AdaptivePlanner (planner.py replacement)

**File:** `src/veronica/adaptive_planner.py`

**Protocol:** `PlannerProtocol.plan(analysis, cost, budget) -> DesiredPolicy`

### Error-Class-Aware Tightening

| Status | Ceiling Adjustment |
|--------|-------------------|
| `halted` | -50% (critical: shut it down) |
| `error` | -15% (warning: caution) |
| `timeout` | -10% (mild: might be transient) |
| `ok` + `loosen` signal | +3% (conservative growth) |

**Asymmetry rationale:** Fast tighten, slow loosen prevents oscillation.

### Cooldown Mechanism

```python
# Per chain_id cooldown: no adjustment within 3 steps of last change
_cooldown: dict[str, int] = {}  # chain_id -> steps_since_last_change
COOLDOWN_STEPS = 3
```

If cooldown is active, return previous ceiling unchanged.

### Minimum Ceiling Guard

```python
ceiling = max(ceiling, cost.estimated_usd * 1.5)
```

Ensures ceiling is always at least 1.5x the estimated cost. Prevents cost model from starving execution.

### Double Clamp (Destruction Test Scenario 7 mitigation)

```python
# Clamp 1: Planner bounds
ceiling = max(min_ceiling, min(max_ceiling, ceiling))
# Clamp 2: Budget constraint
ceiling = min(ceiling, budget.chain_remaining_usd)
```

Applied at Planner level AND again at Arbiter level. Prevents any path from exceeding budget.

---

## 5. ProportionalArbiter (arbiter.py replacement)

**File:** `src/veronica/proportional_arbiter.py`

**Protocol:** `ArbiterProtocol.arbitrate(desires, budget_remaining_usd) -> Mapping[str, PolicyConfig]`

### Priority-Weighted Allocation

```
weight_i = priority_i                    # NOT priority * ceiling (prevents bias)
share_i = weight_i / sum(weights)
allocation_i = share_i * budget_remaining_usd
```

**Rule:** `priority <= 0` is excluded entirely (no allocation, no PolicyConfig emitted).

### Conditional Minimum Allocation (Destruction Test Scenario 7 + Final Fix)

```python
MIN_ALLOCATION_USD = 0.01

eligible_count = len([d for d in desires if d.priority > 0])

# ONLY apply floor when budget can cover all minimums
if budget_remaining_usd >= eligible_count * MIN_ALLOCATION_USD:
    for chain_id in allocations:
        allocations[chain_id] = max(allocations[chain_id], MIN_ALLOCATION_USD)
```

**Rationale:** Without the conditional, `min_allocation` could create budget out of thin air when remaining budget < N * min. The conditional ensures total allocations never exceed remaining budget.

### 2-Pass Surplus Redistribution

1. **Pass 1:** Allocate proportionally. If any chain's `allocation > desired.ceiling_usd`, cap to ceiling. Collect surplus.
2. **Pass 2:** Redistribute surplus proportionally among uncapped chains.

### Double Clamp (Arbiter level)

```python
# Final safety: no single allocation exceeds remaining budget
allocation = min(allocation, budget_remaining_usd)
# Total allocations cannot exceed budget
assert sum(allocations.values()) <= budget_remaining_usd + EPS
```

---

## 6. BufferedEmitter (emitter.py replacement)

**File:** `src/veronica/buffered_emitter.py`

**Protocol:** `EventEmitterProtocol.emit(event_type, payload) -> None`

### Ring Buffer

```python
from collections import deque

class BufferedEmitter:
    def __init__(self, maxlen: int = 1024):
        self._buffer: deque[tuple[str, Mapping[str, Any]]] = deque(maxlen=maxlen)
        self._subscribers: dict[str, Callable] = {}
        self._fail_counts: dict[str, int] = {}
```

### Subscriber Management

- `subscribe(name, callback)` -- register a callback
- `unsubscribe(name)` -- remove a callback
- `drain(n)` -- return and remove up to N oldest events
- `snapshot()` -- return copy of all buffered events (non-destructive)

### Subscriber Timeout + Auto-Unsubscribe

```python
SUBSCRIBER_TIMEOUT_MS = 10
MAX_CONSECUTIVE_FAILURES = 3

def emit(self, event_type, payload):
    self._buffer.append((event_type, payload))
    for name, callback in list(self._subscribers.items()):
        try:
            # Synchronous call with soft timeout tracking
            callback(event_type, payload)
            self._fail_counts[name] = 0
        except Exception:
            self._fail_counts[name] = self._fail_counts.get(name, 0) + 1
            if self._fail_counts[name] >= MAX_CONSECUTIVE_FAILURES:
                self._subscribers.pop(name, None)
                self._fail_counts.pop(name, None)
                logger.warning("Auto-unsubscribed %s after %d failures", name, MAX_CONSECUTIVE_FAILURES)
```

**Rationale:** Slow/broken subscribers must not block the pipeline. Auto-unsubscribe after 3 consecutive failures prevents repeated retry overhead.

### Thread Safety Note

Phase 2 is single-threaded (synchronous pipeline). Thread safety is deferred to Phase 3.
Debug assertion: `assert threading.current_thread() is threading.main_thread()` in emit() for early detection.

---

## 7. FileStore (store.py supplement)

**File:** `src/veronica/file_store.py`

**Protocol:** `StoreProtocol.commit(...) -> None` + `build_history(chain_id, limit) -> HistoryView`

### Storage Format

- One JSONL file per `chain_id`: `{data_dir}/{chain_id}.jsonl`
- One stats file per `chain_id`: `{data_dir}/{chain_id}_stats.json`
- Each JSONL line = one commit record (outcome + analysis + cost + desired + policy + meta)

### EMA Computation (Store-side)

```python
EMA_ALPHA = 0.3

class _ChainStats:
    cost_ema: float = 0.0
    cost_ema_by_model: dict[str, float] = {}
    latency_ema_by_model: dict[str, float] = {}
    success_streak: int = 0
    failure_streak: int = 0
    total_commits: int = 0
```

**EMA key:** `(chain_id, model)` -- scoped to chain AND model.
**Phase 3 design note:** Provider separation (e.g., `openai/gpt-4` vs `azure/gpt-4`) deferred. Current key is model string only.

### EMA Update (in commit())

```python
def commit(self, outcome, analysis, cost, desired, policy, meta):
    # 1. Append JSONL
    self._append_jsonl(outcome.chain_id, record)

    # 2. Update EMA
    stats = self._chain_stats[outcome.chain_id]
    stats.cost_ema = _ema(stats.cost_ema, outcome.cost_usd, EMA_ALPHA)

    model_key = outcome.model or "unknown"
    prev = stats.cost_ema_by_model.get(model_key, outcome.cost_usd)
    stats.cost_ema_by_model[model_key] = _ema(prev, outcome.cost_usd, EMA_ALPHA)

    prev_lat = stats.latency_ema_by_model.get(model_key, outcome.elapsed_ms)
    stats.latency_ema_by_model[model_key] = _ema(prev_lat, outcome.elapsed_ms, EMA_ALPHA)

    # 3. Update streaks
    if outcome.status == "ok":
        stats.success_streak += 1
        stats.failure_streak = 0
    else:
        stats.failure_streak += 1
        stats.success_streak = 0

    stats.total_commits += 1

    # 4. Flush stats periodically
    if stats.total_commits % self._flush_interval == 0:
        self._flush_stats(outcome.chain_id)
```

### build_history() Returns Baseline

```python
def build_history(self, chain_id, limit=50):
    stats = self._chain_stats.get(chain_id, _ChainStats())
    outcomes = self._load_recent_outcomes(chain_id, limit)

    return HistoryView(
        chain_id=chain_id,
        last_n=tuple(outcomes),
        rolling_cost_usd=sum(o.cost_usd for o in outcomes),
        failure_streak=stats.failure_streak,
        success_streak=stats.success_streak,
        depth=len(outcomes),
        loop_score=self._compute_loop_score(outcomes),
        cost_per_step_ema=stats.cost_ema,
        cost_per_step_ema_by_model=dict(stats.cost_ema_by_model),
        latency_ema_ms=dict(stats.latency_ema_by_model),
        budget_headroom_ratio=self._compute_headroom(chain_id),
    )
```

### Stats Flush Strategy

- Flush to `{chain_id}_stats.json` every `N` commits (default N=10)
- Also flush on explicit `close()` call
- On startup, load stats from `_stats.json` if exists

### JSONL Rotation

- `max_lines_per_file` (default 10,000)
- When exceeded, rename to `{chain_id}.1.jsonl` (archive), create fresh file
- `build_history()` only reads from active file (recent data sufficient for EMA)

### Corrupt Tail Handling

```python
def _load_jsonl(self, path):
    records = []
    for i, line in enumerate(open(path)):
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            logger.warning("Corrupt line %d in %s, skipping", i, path)
            continue  # Skip corrupt line, don't abort
    return records
```

---

## 8. Destruction Test Results (8 Scenarios)

All scenarios tested during design review. Mitigations incorporated above.

| # | Scenario | Mitigation | Location |
|---|----------|------------|----------|
| 1 | Cold start (EMA=0) | `ema <= EPS` guard, pricing table fallback | CostModel, Analyzer |
| 2 | Oscillation (tighten/loosen cycle) | Asymmetric factors + 3-step cooldown | Planner |
| 3 | Priority=0 chain | `priority <= 0` excluded from allocation | Arbiter |
| 4 | Store crash mid-commit | Append-only JSONL (partial line = corrupt tail) | FileStore |
| 5 | Slow subscriber blocks pipeline | Auto-unsubscribe after 3 failures | BufferedEmitter |
| 6 | Concurrent emit() calls | Phase 2 = single-threaded; debug assert added | BufferedEmitter |
| 7 | Ceiling lower bound hole | Double clamp (Planner + Arbiter) | Both |
| 8 | History contamination (model change) | EMA key = (chain_id, model). Provider separation Phase 3 | FileStore |

---

## 9. Integration Strategy

### Constructor Injection (no os.py changes)

```python
# Phase 1 (current default)
vos = VeronicaOS()  # Uses RuleAnalyzer, TableCostModel, SimplePlanner, ...

# Phase 2 (new implementations)
vos = VeronicaOS(
    analyzer=HistoryAnalyzer(),
    cost_model=RegressionCostModel(),
    planner=AdaptivePlanner(),
    arbiter=ProportionalArbiter(),
    emitter=BufferedEmitter(),
    store=FileStore(data_dir="./veronica_data"),
)
```

### Backward Compatibility

- Phase 1 implementations remain available (not deleted)
- HistoryView new fields all have defaults
- MemoryStore continues to produce valid HistoryView (new fields = defaults)
- Existing tests pass without modification

### Testing Strategy

- Each component gets its own test file: `tests/test_history_analyzer.py`, etc.
- Integration test: full Phase 2 pipeline (all 6 new components)
- Property-based tests for Arbiter (total allocation <= budget)
- Destruction test scenarios as explicit test cases

---

## 10. File Inventory

### New files (create)

| File | Lines (est.) | Description |
|------|-------------|-------------|
| `src/veronica/history_analyzer.py` | ~120 | 6-pattern analyzer |
| `src/veronica/regression_cost_model.py` | ~60 | EMA-based cost model |
| `src/veronica/adaptive_planner.py` | ~100 | Error-class + cooldown planner |
| `src/veronica/proportional_arbiter.py` | ~90 | Priority-weighted arbiter |
| `src/veronica/buffered_emitter.py` | ~80 | Ring buffer + subscribers |
| `src/veronica/file_store.py` | ~200 | JSONL persistence + EMA |
| `tests/test_history_analyzer.py` | ~150 | Analyzer tests |
| `tests/test_regression_cost_model.py` | ~80 | CostModel tests |
| `tests/test_adaptive_planner.py` | ~120 | Planner tests |
| `tests/test_proportional_arbiter.py` | ~120 | Arbiter tests |
| `tests/test_buffered_emitter.py` | ~80 | Emitter tests |
| `tests/test_file_store.py` | ~150 | FileStore tests |
| `tests/test_phase2_integration.py` | ~100 | Full pipeline integration |

### Modified files

| File | Change |
|------|--------|
| `src/veronica/types.py` | Add 5 fields to HistoryView (with defaults) |
| `src/veronica/__init__.py` | Export new classes |

### Unchanged files

`os.py`, `protocols.py`, `collector.py`, `_timeguard.py`, all Phase 1 implementations.

---

## Appendix: EMA Formula

```
EMA_new = alpha * current_value + (1 - alpha) * EMA_old

alpha = 0.3 (default)
```

For first observation: `EMA = current_value` (no history).
