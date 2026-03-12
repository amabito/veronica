# Data Flow: Kernel-Control Plane Integration

This document describes the full data flow from policy creation to metric
emission in VERONICA OS, including the policy_hash chain and audit_id lifecycle.

---

## Overview

VERONICA OS sits between the application (policy decisions) and veronica-core
(enforcement). The control plane (CP) converts policy intent into kernel limits,
then collects kernel events back for audit and observability.

```
Application
    |
    | StepIntent (chain_id, kind, model, tool_name, ...)
    v
+-------------------+
| VeronicaOS        |  Control Plane
|  before_step()    |
|  - OrgPolicy      |  <-- org-level hard block (step_denied if blocked)
|  - CostModel      |
|  - Planner        |  --> DesiredPolicy
|  - Arbiter        |  --> PolicyConfig  <-- policy_hash computed here
+-------------------+
    |
    | PolicyConfig.to_exec_config()
    |          +
    | PolicyDistributor.distribute(policy)  --> PolicyBundle {exec_config, policy_hash}
    v
+-------------------+
| ExecutionContext   |  veronica-core kernel
|  wrap_llm_call()  |
|  wrap_tool_call() |
|  - limit checks   |  --> Decision (ALLOW / HALT / DEGRADE / RETRY)
|  - ShieldPipeline |  --> SafetyEvent[]
+-------------------+
    |
    | get_snapshot() --> ContextSnapshot {events, cost_usd_accumulated, ...}
    v
+-------------------+
| VeronicaOS        |  Control Plane
|  after_step()     |
|  - Collector      |  ContextSnapshot --> StepOutcome (OS type)
|  - Analyzer       |  StepOutcome --> AnalysisResult
|  - Store.commit() |
|  - EventEmitter   |  --> step_completed {policy_hash, audit_id, ...}
+-------------------+
    |
    +-------> BufferedEmitter
    |              |
    |         +----+----+
    |         |         |
    |    MetricsSubscriber   StructuredLogSubscriber
    |    PrometheusSubscriber
    |
    +-------> EventIngestor
                   |
                   v
             CPStepOutcomeStore
             (SafetyEvent --> CPStepOutcome with policy_hash + audit_id)
```

---

## Transformation Steps

### 1. StepIntent -> PolicyConfig (before_step)

`VeronicaOS.before_step()` runs the planning pipeline:

1. **OrgPolicy.validate()** -- hard block on forbidden models/tools. If denied,
   returns a `StepHandle` with `ceiling_usd=0` and emits `step_denied`.
2. **CostModel.estimate()** -- predicts cost of the next step.
3. **Planner.plan()** -- produces `DesiredPolicy` (desired limits for this chain).
4. **OrgPolicy.clamp()** -- caps DesiredPolicy to org-wide numerical limits.
5. **Arbiter.arbitrate()** -- resolves contention, returns `PolicyConfig` per chain.

The resulting `PolicyConfig` is the contract between the OS and the kernel.

### 2. PolicyConfig -> ExecutionConfig (PolicyDistributor)

`PolicyDistributor.distribute(policy)` performs:

1. **Validation** -- ceiling_usd >= 0, on_exceed in {halt, degrade, queue}.
   In strict mode, also requires ceiling_steps, ceiling_tokens_out, timeout_ms.
2. **policy_hash computation** -- SHA-256 of a stable subset of PolicyConfig fields:
   ```
   fields = {chain_id, ceiling_usd, on_exceed, issued_at,
             ceiling_steps, ceiling_tokens_out, timeout_ms}
   policy_hash = SHA-256(json.dumps(fields, sort_keys=True))
   ```
3. **Conversion** -- `policy.to_exec_config()` maps PolicyConfig -> ExecutionConfig.
4. **PolicyBundle** -- bundles {policy, exec_config, policy_hash, version, distributed_at}.

`VeronicaOS` computes the same hash internally via `_compute_policy_hash()`.
Both functions use identical field selection and serialization -- same policy
always yields the same 64-character hex digest.

### 3. ExecutionConfig -> Kernel Decision (veronica-core)

`ExecutionContext.wrap_llm_call()` / `wrap_tool_call()`:

- Checks chain-level limits (max_cost_usd, max_steps, max_retries_total, timeout_ms).
- Runs the ShieldPipeline (if configured) for pre/post-dispatch hooks.
- Runs the CircuitBreaker (if configured).
- Returns a `Decision`: ALLOW, HALT, RETRY, DEGRADE, QUARANTINE, or QUEUE.
- Appends `SafetyEvent` entries to the internal event log for any non-ALLOW decision.

### 4. ContextSnapshot -> StepOutcome (after_step)

`VeronicaOS.after_step()` runs the collection pipeline:

1. **Collector** -- converts `ContextSnapshot` to OS-level `StepOutcome`.
2. **Analyzer** -- produces `AnalysisResult` (risk_level, recommendation, signals).
3. **Store.commit()** -- persists outcome + analysis + policy + meta.
4. **EventEmitter.emit()** -- fires `step_completed` with full audit payload.

### 5. step_completed -> Subscribers

`BufferedEmitter` dispatches to all registered subscribers:

- **MetricsSubscriber** -- increments Prometheus counters by status/kind/risk.
- **PrometheusSubscriber** -- increments CP-level counters with chain_id/policy_hash labels.
- **StructuredLogSubscriber** -- emits JSON log line.

### 6. SafetyEvent -> CPStepOutcome (EventIngestor)

`EventIngestor.ingest(event)` converts kernel `SafetyEvent` to CP `StepOutcome`:

- Extracts `policy_hash` from `event.metadata["policy_hash"]` if present,
  else derives a stable hash from `hook:event_type`.
- Extracts `audit_id` from `event.metadata["audit_id"]` if present,
  else generates a fresh UUID4.
- Maps `Decision` enum -> CP decision string (allow/halt/degrade/retry/...).
- Batches records and writes to `CPStepOutcomeStore.put_many()`.

---

## policy_hash Chain

The same SHA-256 hash flows through the entire pipeline for traceability:

```
PolicyConfig (issued by Planner)
    |
    | _compute_policy_hash(policy)  [os.py]
    | PolicyDistributor._compute_hash(policy)  [policy_distributor.py]
    |
    v
policy_hash = "a3f8c2..."  (64 hex chars)
    |
    +-- step_completed payload.policy_hash
    +-- PolicyBundle.policy_hash
    +-- SafetyEvent.metadata["policy_hash"]  (caller must inject)
    +-- CPStepOutcome.policy_hash
    +-- PrometheusSubscriber labels: chain_id + policy_hash
```

**Hash stability**: Same PolicyConfig fields -> same hash, always. Different
`issued_at` timestamps produce different hashes (issued_at is included).

---

## audit_id Lifecycle

`audit_id` is a UUID4 hex string (32 chars, no hyphens) generated per decision:

```
VeronicaOS.after_step()
    |
    | uuid.uuid4().hex  -->  audit_id  (new per step_completed event)
    v
step_completed payload.audit_id

EventIngestor.ingest(event)
    |
    | event.metadata.get("audit_id")  or  uuid.uuid4().hex
    v
CPStepOutcome.audit_id  (unique per ingested event)
```

`audit_id` is NOT shared between the `step_completed` event and the
`CPStepOutcome`. They are independent unique identifiers that can be
correlated by `step_id` + `chain_id` + `request_id`.

---

## Event Type Reference

### step_denied (emitted by before_step on OrgPolicy block)

```json
{
  "schema_version": 1,
  "request_id": "req-abc123",
  "step_id": "step-1",
  "chain_id": "my-chain",
  "kind": "tool",
  "reason": "tool 'bash_tool' is blocked by org policy",
  "model": null,
  "tool_name": "bash_tool"
}
```

### step_completed (emitted by after_step)

```json
{
  "schema_version": 1,
  "request_id": "req-abc123",
  "step_id": "step-1",
  "chain_id": "my-chain",
  "kind": "llm",
  "status": "ok",
  "cost_usd": 0.012,
  "tokens_in": 500,
  "tokens_out": 200,
  "elapsed_ms": 842.5,
  "risk_level": "nominal",
  "recommendation": "continue",
  "degraded": false,
  "degrade_reason": null,
  "signals": [
    {"kind": "high_cost_step", "severity": "warning"}
  ],
  "stage_time_ms": {
    "cost_model": 0.8,
    "planner": 2.1,
    "arbiter": 0.3,
    "store": 1.2,
    "emit": 0.1
  },
  "policy_hash": "a3f8c2d1e4b5f6a7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1",
  "audit_id": "7f3a9c2b1d4e5f6a7b8c9d0e1f2a3b4c"
}
```

### CPStepOutcome (stored by EventIngestor)

```json
{
  "step_id": "step-1",
  "chain_id": "my-chain",
  "operation_name": "gpt-4",
  "decision": "halt",
  "cost_usd": 0.0,
  "tokens": 0,
  "duration_ms": 0.0,
  "policy_hash": "a3f8c2d1e4b5f6a7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1",
  "audit_id": "2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a",
  "timestamp": 1741564800.123
}
```

### PolicyDecision (schema only, for future audit trail)

```json
{
  "decision_id": "d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6",
  "policy_id": "my-chain",
  "policy_hash": "a3f8c2d1e4b5f6a7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1",
  "rule_matched": "cost_model_timeout",
  "verdict": "DEGRADE",
  "reason": "Stage 'planner' exceeded budget: 94ms > 1ms",
  "context": {"stage": "planner", "elapsed_ms": 94.0},
  "timestamp": 1741564800.456
}
```

---

## Store Interface

`StoreProtocol` defines what the OS-level store must implement:

```python
class StoreProtocol(Protocol):
    def commit(
        self,
        outcome: StepOutcome,
        analysis: AnalysisResult,
        cost: CostEstimate,
        desired: DesiredPolicy,
        policy: PolicyConfig,
        meta: DecisionMeta,
    ) -> None: ...

    def build_history(self, chain_id: str, limit: int = 50) -> HistoryView: ...
```

Built-in implementations:

| Class | Storage | Use Case |
|-------|---------|----------|
| `MemoryStore` | In-process dict | Tests, single-request |
| `FileStore` | JSON files on disk | Single-process, persistent |
| `RedisArbiter` | Redis | Multi-process, distributed |

`CPStepOutcomeStore` (separate from StoreProtocol) stores CP-level records:

```python
class CPStepOutcomeStore:
    def put(self, outcome: CPStepOutcome) -> None: ...
    def put_many(self, outcomes: Sequence[CPStepOutcome]) -> None: ...
    def snapshot(self) -> list[CPStepOutcome]: ...
```

---

## Prometheus Metric Reference

### MetricsSubscriber (legacy, event-level labels)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `veronica_steps_total` | Counter | status, kind, recommendation, risk_level | Total steps |
| `veronica_step_elapsed_ms` | Histogram | kind | Step elapsed time |
| `veronica_stage_elapsed_ms` | Histogram | stage | Pipeline stage time |
| `veronica_cost_microusd_total` | Counter | -- | Total cost (1 USD = 1,000,000) |
| `veronica_degrade_total` | Counter | degrade_reason | Degraded steps |
| `veronica_denied_total` | Counter | kind | Denied steps |

### PrometheusSubscriber (CP-aware, policy_hash labels)

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `veronica_step_completed_total` | Counter | chain_id, policy_hash, decision_type | Completed steps |
| `veronica_step_denied_total` | Counter | chain_id, policy_hash, decision_type | Denied steps |
| `veronica_halt_total` | Counter | chain_id, policy_hash | Halted steps |
| `veronica_degrade_total` | Counter | chain_id, policy_hash | Degraded steps |
| `veronica_step_duration_seconds` | Histogram | chain_id | Step duration (seconds) |
| `veronica_active_chains` | Gauge | -- | Distinct chains seen |

Use `PrometheusSubscriber` when you need per-policy audit queries such as:
```promql
veronica_halt_total{policy_hash="a3f8c2..."}
```

---

## Troubleshooting

### policy_hash mismatch between distributor and OS payload

**Symptom**: `PolicyDistributor` returns a different hash than the `policy_hash`
in the `step_completed` payload.

**Cause**: The `issued_at` field is part of the hash. If `PolicyConfig` is created
twice (once in the distributor, once inside the OS pipeline), `issued_at=time.time()`
will differ.

**Fix**: Pass the `issued_at` value explicitly:
```python
now = time.time()
policy = PolicyConfig(chain_id="c1", ceiling_usd=1.0, on_exceed="halt", issued_at=now)
bundle = distributor.distribute(policy)
# Use bundle.policy_hash -- it will match the OS-emitted hash for this exact policy object.
```

### audit_id not present in CPStepOutcome

**Symptom**: `CPStepOutcome.audit_id` does not match any `audit_id` in `step_completed`.

**Cause**: These are independent. `step_completed.audit_id` is generated by `VeronicaOS`.
`CPStepOutcome.audit_id` is generated by `EventIngestor` (or taken from
`SafetyEvent.metadata["audit_id"]` if provided by the caller).

**Fix**: To correlate them, pass the same `audit_id` in `SafetyEvent.metadata`:
```python
halt_event = SafetyEvent(
    ...,
    metadata={
        "audit_id": step_completed_payload["audit_id"],
        "policy_hash": step_completed_payload["policy_hash"],
    },
)
```

### Prometheus double-registration error

**Symptom**: `ValueError: Duplicated timeseries in CollectorRegistry` when
instantiating `PrometheusSubscriber` twice.

**Fix**: Both classes use `_get_or_create()` to deduplicate by name. If you see
this error, two instances are using different `CollectorRegistry` objects.
Pass the same registry explicitly:
```python
reg = CollectorRegistry()
sub1 = PrometheusSubscriber(prefix="veronica", registry=reg)
sub2 = PrometheusSubscriber(prefix="veronica", registry=reg)  # reuses metrics
```

### EventIngestor drops events silently

**Symptom**: `EventIngestor.ingest()` does not raise, but records are missing.

**Cause**: Conversion errors are caught and logged, incrementing `error_total`.

**Fix**: Check `ingestor.error_total` after ingesting:
```python
ingestor.ingest(event)
if ingestor.error_total > 0:
    logger.warning("EventIngestor: %d conversion errors", ingestor.error_total)
```

Common conversion failure: `event.metadata` is `None` or missing required keys.
Verify that `step_id` and `chain_id` are in `SafetyEvent.metadata`.

### Degraded steps not showing in metrics

**Symptom**: `veronica_degrade_total` is not incremented despite `degraded=True` in payload.

**Cause**: `PrometheusSubscriber` only increments `degrade_total` when
`payload["degraded"] is True` (bool, not truthy). Check the emitter payload type.

**Cause 2**: The subscriber is registered after the event was emitted. Subscribers
are called synchronously -- register before calling `vos.after_step()`.
