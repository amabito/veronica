# Policy Rollout Runbook

Step-by-step procedure for rolling out a policy change to production.
State machine: DRAFT -> SIMULATED -> APPROVED -> PROMOTED -> ACTIVE (-> REVOKED on rollback).

Variables used throughout:
- `API=http://127.0.0.1:8000`
- `KEY=your-secret-key-here`
- `ROLLOUT_ID` -- UUID returned by step 1

---

## Step 1: Create Draft Rollout

```bash
curl -s -X POST "$API/rollouts" \
  -H "X-Veronica-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "created_by": "ops@example.com",
    "policy_config": {
      "chain_id": "payment-agent",
      "ceiling_usd": 2.00,
      "on_exceed": "halt",
      "ceiling_steps": 50
    }
  }' | jq '{id, state}'
```

Save the returned `id` as `ROLLOUT_ID`.

Expected state: `"draft"`

---

## Step 2: Run Simulation

```bash
curl -s -X POST "$API/rollouts/$ROLLOUT_ID/simulate" \
  -H "X-Veronica-Key: $KEY" | jq '{state, simulation_result}'
```

Expected state: `"simulated"`

Check `simulation_result.policy_hash` is present. If the call returns 422, the policy
config is invalid -- fix `ceiling_usd`, `on_exceed`, or other fields and recreate from step 1.

---

## Step 3: Approve

**Who approves:** team lead or platform eng with write access.

**What to check before approving:**
- `ceiling_usd` reflects the intended budget for the chain.
- `on_exceed` is set correctly (`halt` for hard-stop, `degrade` for fallback model, `queue` for deferral).
- `simulation_result.policy_hash` is non-null (simulation ran successfully).
- No active rollout for the same `chain_id` exists in APPROVED or PROMOTED state.

```bash
curl -s -X POST "$API/rollouts/$ROLLOUT_ID/approve" \
  -H "X-Veronica-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"actor": "lead@example.com"}' | jq '{state, history}'
```

Expected state: `"approved"`

---

## Step 4: Promote to Staging

```bash
curl -s -X POST "$API/rollouts/$ROLLOUT_ID/promote" \
  -H "X-Veronica-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"actor": "ops@example.com"}' | jq '{state}'
```

Expected state: `"promoted"`

At this point the policy is queued but not yet enforced. Run a replay against staging traffic
to validate (see incident-response.md step 4 for replay syntax).

---

## Step 5: Activate in Production

```bash
curl -s -X POST "$API/rollouts/$ROLLOUT_ID/activate" \
  -H "X-Veronica-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"actor": "ops@example.com"}' | jq '{state}'
```

Expected state: `"active"`

A 409 here means either the state transition is invalid (wrong sequence) or policy
registration failed. Check `history` for current state and correct accordingly.

---

## Step 6: Monitor for 15 Minutes

Open Grafana at http://127.0.0.1:3000 and watch these panels for the affected `chain_id`:

- `veronica_decisions_total{decision="halt"}` -- should not spike above baseline.
- `veronica_decisions_total{decision="degrade"}` -- expected if `on_exceed=degrade`.
- `veronica_cost_usd_total` -- confirm spending is within new ceiling.

Or query Prometheus directly:

```bash
curl -s "http://127.0.0.1:9090/api/v1/query" \
  --data-urlencode 'query=rate(veronica_decisions_total{decision="halt"}[5m])'
```

Alert threshold: halt rate > 2x pre-rollout baseline requires immediate investigation.

---

## Step 7: Rollback Procedure (Revoke)

If a spike is confirmed or behavior is wrong, revoke immediately:

```bash
curl -s -X POST "$API/rollouts/$ROLLOUT_ID/revoke" \
  -H "X-Veronica-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{"actor": "ops@example.com"}' | jq '{state}'
```

Expected state: `"revoked"` (terminal -- cannot be re-activated).

Then restore the previous policy by creating a new rollout from step 1 with the old config values.
To retrieve the previous policy config:

```bash
curl -s "$API/policies/payment-agent" -H "X-Veronica-Key: $KEY" | jq '.'
```
