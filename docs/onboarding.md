# Your First 30 Minutes with VERONICA

A hands-on walkthrough for design partners evaluating the control plane.
All commands target `localhost:8000`. Replace `your-api-key` with the value
from your `.env` file.

---

## Prerequisites

- Docker and Docker Compose
- `curl` (or `httpie`)
- A browser

---

## Step 1: Start VERONICA (2 min)

Copy the example environment file and set required values:

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```
VERONICA_API_KEY=your-api-key
POSTGRES_PASSWORD=changeme
GRAFANA_ADMIN_PASSWORD=changeme
```

Start all services:

```bash
cd deploy/
docker compose up -d
```

Verify the API is up (no auth required on `/health`):

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "version": "0.8.0",
  "uptime_seconds": 3.1
}
```

Open the dashboard in a browser: `http://localhost:3000`
Open the interactive API docs: `http://localhost:8000/docs`

---

## Step 2: Create Your First Policy (5 min)

A policy attaches a cost ceiling to an agent chain. The three required fields
are `chain_id`, `ceiling_usd`, and `on_exceed`.

```bash
curl -X PUT http://localhost:8000/policies/research-agent \
  -H "X-Veronica-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "chain_id": "research-agent",
    "ceiling_usd": 0.50,
    "on_exceed": "halt",
    "current_version": 0
  }'
```

Field reference:

| Field | Description |
|---|---|
| `chain_id` | Unique identifier for the agent chain. Matches the kernel's chain scope. |
| `ceiling_usd` | Maximum spend in USD before enforcement triggers. |
| `on_exceed` | What happens when the ceiling is reached: `halt`, `degrade`, or `queue`. |
| `current_version` | Optimistic concurrency token. Use `0` for new policies; read from `GET` response for updates. |

Retrieve the policy to confirm it was stored:

```bash
curl http://localhost:8000/policies/research-agent \
  -H "X-Veronica-Key: your-api-key"
```

The policy appears in the Policies page of the dashboard at `http://localhost:3000`.

---

## Step 3: Simulate a Policy (5 min)

`POST /simulate` evaluates a sequence of steps against a policy without
modifying any stored state. Use it to answer "what would happen if this agent
ran these steps?"

```bash
curl -X POST http://localhost:8000/simulate \
  -H "X-Veronica-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "policy": {
      "chain_id": "research-agent",
      "ceiling_usd": 0.50,
      "on_exceed": "halt"
    },
    "steps": [
      {"kind": "llm_call", "cost_usd": 0.18, "tokens_out": 800, "elapsed_ms": 420},
      {"kind": "llm_call", "cost_usd": 0.22, "tokens_out": 950, "elapsed_ms": 510},
      {"kind": "llm_call", "cost_usd": 0.15, "tokens_out": 600, "elapsed_ms": 300}
    ]
  }'
```

Reading the response:

```json
{
  "steps_allowed": 2,
  "steps_halted": 1,
  "total_cost_usd": 0.40,
  "final_decision": "halt",
  "store_unchanged": true,
  "step_results": [
    {"step_index": 0, "decision": "allow", "cumulative_cost_usd": 0.18, "reason": "within budget"},
    {"step_index": 1, "decision": "allow", "cumulative_cost_usd": 0.40, "reason": "within budget"},
    {"step_index": 2, "decision": "halt",  "cumulative_cost_usd": 0.40, "reason": "cost ceiling exceeded: 0.55 > 0.50"}
  ]
}
```

- `steps_allowed` -- steps that would have been permitted
- `steps_halted` -- steps blocked (including unprocessed steps after the first block)
- `final_decision` -- the last enforcement outcome (`allow`, `halt`, `degrade`, or `queue`)
- `store_unchanged: true` -- confirms no persistent state was written

To see `halt` triggered on step 1, lower `ceiling_usd` to `0.10` and re-run.

---

## Step 4: Watch Live Events (5 min)

The Events page at `http://localhost:3000/events` shows decisions emitted by
the kernel in real time.

Events are written by the veronica-core engine running in your application
process. The kernel calls `emitter.emit(event)` after each enforcement
decision, and the ingestor writes those events to PostgreSQL.

To generate events without a running agent, use the E2E fixtures if present,
or POST directly to the ingest endpoint:

```bash
curl -X POST http://localhost:8000/ingest \
  -H "X-Veronica-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "chain_id": "research-agent",
    "decision": "halt",
    "cost_usd": 0.55,
    "tokens_out": 1100,
    "step_kind": "llm_call",
    "timestamp": '"$(date +%s.%N)"'
  }'
```

In the Events page, use the decision filter to show only `halt` events.
The Grafana dashboard at `http://localhost:3000` provides a time-series view
of decisions, cost, and step counts from Prometheus metrics.

---

## Step 5: Rollout Pipeline (5 min)

Rollouts are the change-management layer for policy updates. A policy goes
through `DRAFT -> SIMULATED -> APPROVED -> PROMOTED -> ACTIVE` before it
is enforced. Any step can be revoked.

**Create a rollout in DRAFT:**

```bash
curl -X POST http://localhost:8000/rollouts \
  -H "X-Veronica-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "policy_config": {
      "chain_id": "research-agent",
      "ceiling_usd": 0.75,
      "on_exceed": "halt"
    },
    "created_by": "alice@example.com"
  }'
```

Note the `id` field in the response (a UUID). Use it in subsequent steps as `{id}`.

**Simulate the rollout** (validates the policy against the distributor):

```bash
curl -X POST http://localhost:8000/rollouts/{id}/simulate \
  -H "X-Veronica-Key: your-api-key"
```

State transitions to `SIMULATED`. The response includes `simulation_result`
with the `policy_hash` and distribution metadata.

**Approve and promote:**

```bash
curl -X POST http://localhost:8000/rollouts/{id}/approve \
  -H "X-Veronica-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"actor": "alice@example.com"}'

curl -X POST http://localhost:8000/rollouts/{id}/promote \
  -H "X-Veronica-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"actor": "alice@example.com"}'
```

**Activate** (registers the policy and transitions to `ACTIVE`):

```bash
curl -X POST http://localhost:8000/rollouts/{id}/activate \
  -H "X-Veronica-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"actor": "alice@example.com"}'
```

The policy is now live. The `history` array in each response shows the full
audit trail of state transitions with actor and timestamp.

---

## Step 6: Tenant Hierarchy (5 min)

Tenants model organizational structure. A child tenant inherits its parent's
policy and can narrow -- but not widen -- individual fields via `policy_overrides`.

**Create an org-level tenant:**

```bash
curl -X POST http://localhost:8000/tenants \
  -H "X-Veronica-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "acme-corp",
    "name": "Acme Corp",
    "policy_overrides": {
      "ceiling_usd": 10.0,
      "on_exceed": "halt"
    }
  }'
```

**Create a team under the org:**

```bash
curl -X POST http://localhost:8000/tenants \
  -H "X-Veronica-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "acme-research",
    "name": "Acme Research Team",
    "parent_id": "acme-corp",
    "policy_overrides": {
      "ceiling_usd": 2.0
    }
  }'
```

**View the effective policy** (parent fields merged with child overrides):

```bash
curl http://localhost:8000/tenants/acme-research/effective-policy \
  -H "X-Veronica-Key: your-api-key"
```

The response shows the resolved `ceiling_usd` of `2.0` (the team's tighter
limit) while inheriting `on_exceed: "halt"` from the parent. Overrides are
merged top-down; child values take precedence.

---

## Step 7: Incident Replay (3 min)

Replay lets you re-evaluate recorded chain events against a different policy.
Use it to answer "would a stricter ceiling have caught this earlier?"

Find a HALT event from the Events page or the event list endpoint, and note
its `chain_id` and approximate timestamp range.

```bash
curl -X POST http://localhost:8000/replay \
  -H "X-Veronica-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "chain_id": "research-agent",
    "from_timestamp": 1740000000,
    "to_timestamp": 1740003600,
    "override_policy": {
      "chain_id": "research-agent",
      "ceiling_usd": 0.25,
      "on_exceed": "halt"
    }
  }'
```

Response:

```json
{
  "chain_id": "research-agent",
  "event_count": 3,
  "changed_count": 1,
  "summary": "1 of 3 decisions changed under override policy",
  "store_unchanged": true,
  "diffs": [
    {"step_id": "step-0", "original_decision": "allow", "replayed_decision": "allow", "changed": false},
    {"step_id": "step-1", "original_decision": "allow", "replayed_decision": "halt",  "changed": true},
    {"step_id": "step-2", "original_decision": "halt",  "replayed_decision": "halt",  "changed": false}
  ]
}
```

`changed: true` on step-1 shows the override policy would have halted one
step earlier. `store_unchanged: true` confirms this is read-only.

---

## Next Steps

- **Production deployment** -- see [deploy.md](deploy.md) for reverse proxy setup,
  Redis configuration for distributed budget enforcement, and the production checklist.
- **Key management** -- see [key-management.md](key-management.md) for key rotation
  and revocation procedures.
- **API reference** -- full schema at `http://localhost:8000/docs` (Swagger UI,
  no auth required).
- **Policy fields** -- see [policy-config.md](policy-config.md) for the complete
  `PolicyConfig` specification including `ceiling_tokens_out`, `ceiling_steps`,
  `timeout_ms`, and rate limiting fields.
