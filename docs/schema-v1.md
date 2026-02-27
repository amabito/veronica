# step_completed Event Schema v1

Emitted by `VeronicaOS` after each step execution via `BufferedEmitter`.

## Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `schema_version` | int | yes | Always `1`. Enables future payload evolution. |
| `request_id` | str | yes | ID of the originating request. |
| `step_id` | str | yes | ID of this step. |
| `chain_id` | str | yes | Chain this step belongs to. |
| `kind` | str | yes | Execution type: `"llm"` or `"tool"`. |
| `status` | str | yes | Outcome: `"ok"`, `"error"`, `"halted"`, or `"timeout"`. |
| `cost_usd` | float | yes | Cost of this step in USD. |
| `tokens_in` | int | yes | Input tokens consumed. |
| `tokens_out` | int | yes | Output tokens produced. |
| `elapsed_ms` | float | yes | Total step wall-clock time in milliseconds. |
| `risk_level` | str | yes | Risk assessment: `"nominal"`, `"elevated"`, or `"critical"`. |
| `recommendation` | str | yes | Planner recommendation: `"continue"`, `"pause"`, `"halt"`, or `"abort"`. |
| `degraded` | bool | yes | True if the step ran in degraded mode. |
| `degrade_reason` | str \| None | yes | Why degraded: `"time_budget"`, `"fallback_model"`, `"other"`, or `None` if not degraded. |
| `signals` | list[dict] | yes | Analysis signals. Each entry: `{"kind": str, "severity": str}`. |
| `stage_time_ms` | dict[str, float] | yes | Per-stage elapsed time in milliseconds. Keys restricted to known stages (see below). |

## Known Stages

`stage_time_ms` keys are filtered at emission to the following set:

```
collector, analyzer, cost_model, planner, arbiter, store, emit
```

Unknown stage keys are dropped before the payload is emitted.

## Compatibility

- Fields may be added in future schema versions. Subscribers MUST ignore unknown fields.
- All fields listed above are guaranteed present when `schema_version=1`.
- `signals` contains only `kind` and `severity`. The `detail` field is excluded (can be arbitrarily large).
- `stage_time_ms` keys are filtered to the known set. Unknown stages are dropped at emission.
