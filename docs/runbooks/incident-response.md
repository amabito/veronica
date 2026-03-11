# Incident Response Runbook

Procedure for investigating HALT/DEGRADE spikes in the VERONICA control plane.

Variables used throughout:
- `API=http://127.0.0.1:8000`
- `KEY=your-secret-key-here`

---

## Decision Tree

```
Anomaly detected
  |
  +-- decision = "halt"
  |     Policy ceiling_usd or ceiling_steps breached.
  |     Budget increase or chain decomposition required.
  |
  +-- decision = "degrade"
  |     Ceiling breached with on_exceed=degrade.
  |     Fallback model is active. Check response quality.
  |     May self-recover if cost resets per window.
  |
  +-- decision = "circuit_open"
        Circuit breaker tripped (error rate threshold exceeded).
        Backend/model reliability issue, not policy.
        Investigate upstream model errors first.
```

---

## Step 1: Check Dashboard for Anomaly

Open Grafana at http://127.0.0.1:3000. Look for:
- Sudden rise in `veronica_decisions_total{decision="halt"}`.
- Elevated `veronica_decisions_total{decision="degrade"}`.

Or query Prometheus:

```bash
curl -s "http://127.0.0.1:9090/api/v1/query" \
  --data-urlencode 'query=topk(5, rate(veronica_decisions_total{decision="halt"}[5m]))'
```

Note the `chain_id` label from the top result.

---

## Step 2: Identify Affected chain_id

List recent events for a specific chain:

```bash
curl -s "$API/events?chain_id=payment-agent&limit=20" \
  -H "X-Veronica-Key: $KEY" | jq '[.items[] | {step_id, decision, cost_usd, timestamp}]'
```

Scan `decision` values for `halt` or `degrade` entries. Note the `step_id` of the first bad event.

---

## Step 3: Get Incident Detail

```bash
curl -s "$API/incidents/payment-agent/step-abc123" \
  -H "X-Veronica-Key: $KEY" | jq '{decision, reason_code, policy_hash, root_cause, timeline: (.timeline | length)}'
```

Key fields:
- `root_cause.step_id` -- the first halt/degrade step in the chain timeline.
- `root_cause.timestamp` -- when enforcement first triggered.
- `policy_hash` -- identifies which policy version was active.
- `timeline` -- full event sequence for the chain; shows cost accumulation pattern.

A null `reason_code` is expected (not yet populated in schema v1).

---

## Step 4: Replay with Current Policy to Confirm

Confirm the issue is reproducible under the live policy.
Use unix timestamps: `from_timestamp` = incident start - 300s, `to_timestamp` = now.

```bash
curl -s -X POST "$API/replay" \
  -H "X-Veronica-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "chain_id": "payment-agent",
    "from_timestamp": 1741600000,
    "to_timestamp": 1741603600,
    "override_policy": null
  }' | jq '{event_count, changed_count, summary}'
```

`changed_count=0` here confirms the current policy would reproduce the same decisions.

---

## Step 5: Replay with Relaxed Policy to Test Fix

Test whether raising the ceiling resolves the halts without removing enforcement entirely:

```bash
curl -s -X POST "$API/replay" \
  -H "X-Veronica-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "chain_id": "payment-agent",
    "from_timestamp": 1741600000,
    "to_timestamp": 1741603600,
    "override_policy": {
      "chain_id": "payment-agent",
      "ceiling_usd": 5.00,
      "on_exceed": "halt"
    }
  }' | jq '{event_count, changed_count, summary}'
```

If `changed_count` drops to 0, the new ceiling would have prevented the incident.
If `changed_count` stays high, the problem is not the ceiling -- investigate `ceiling_steps`
or check for a model cost regression.

---

## Step 6: Create Rollout with Fix

Once the replay confirms the fix, create a rollout. Follow policy-rollout.md from step 1.
Use the validated `ceiling_usd` value from step 5.

Expedited approval is allowed for P1 incidents: approver may combine steps 3 and 4
(approve immediately after simulate) if the replay in step 5 is conclusive.

---

## Step 7: Post-Incident Actions

After the fix rollout reaches ACTIVE state:

1. Verify halt rate returns to baseline in Grafana (15 min observation window).
2. Update the policy documentation or cost model if a model price change caused the incident.
3. File a post-incident note with:
   - Affected `chain_id` and time window.
   - Root cause (budget exhaustion / step limit / model cost regression).
   - Fix applied (new `ceiling_usd`, `ceiling_steps`, or `on_exceed` value).
   - Replay results (event_count, changed_count before and after fix).
   - Rollout ID of the fix.

```bash
# Confirm rollout is active and record for post-incident note
curl -s "$API/rollouts/$ROLLOUT_ID" \
  -H "X-Veronica-Key: $KEY" | jq '{id, state, updated_at}'
```
