# Tenant Onboarding Runbook

Procedure for onboarding a new team to the VERONICA control plane.

Tenant hierarchy: org (root) -> team (child). Policy overrides cascade down --
a team inherits the org's overrides and may add narrower constraints.

Variables used throughout:
- `API=http://127.0.0.1:8000`
- `KEY=your-secret-key-here`

---

## Step 1: Create Org Tenant (if new org)

Skip this step if the org already exists. Org tenants have no `parent_id`.

```bash
curl -s -X POST "$API/tenants" \
  -H "X-Veronica-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "acme",
    "name": "Acme Corp",
    "policy_overrides": {
      "ceiling_usd": 50.0,
      "on_exceed": "halt"
    }
  }' | jq '{id, name, parent_id, policy_overrides}'
```

`id` must match `^[a-zA-Z0-9_-]+$` (max 128 chars). Returns 422 if the id already exists.

---

## Step 2: Create Team Tenant Under Org

```bash
curl -s -X POST "$API/tenants" \
  -H "X-Veronica-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "acme-payments",
    "name": "Acme Payments Team",
    "parent_id": "acme",
    "policy_overrides": {}
  }' | jq '{id, name, parent_id}'
```

A 422 response with `"Tenant registration failed"` means either `parent_id` does not exist
or `id` is already taken. Verify the org exists first:

```bash
curl -s "$API/tenants/acme" -H "X-Veronica-Key: $KEY" | jq '{id, name}'
```

---

## Step 3: Set Team-Level Policy Overrides

Apply tighter constraints for the team without affecting the parent org.
Only known keys are accepted (ceiling_usd, on_exceed, ceiling_steps, ceiling_tokens_out,
fallback_model, timeout_ms, rate_window_seconds, rate_ceiling_calls, priority, deadline_ts,
expires_at, planner_version).

```bash
curl -s -X PUT "$API/tenants/acme-payments" \
  -H "X-Veronica-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "policy_overrides": {
      "ceiling_usd": 2.00,
      "on_exceed": "degrade",
      "fallback_model": "gpt-3.5-turbo",
      "ceiling_steps": 30
    }
  }' | jq '{id, policy_overrides}'
```

A 422 with `"Unknown policy_overrides keys"` means an unrecognized key was supplied.
Maximum 20 keys per tenant.

---

## Step 4: Verify Effective Policy

The effective policy merges overrides from root to leaf. Confirm the resolved values
match expectations before any agents use this tenant.

```bash
curl -s "$API/tenants/acme-payments/effective-policy" \
  -H "X-Veronica-Key: $KEY" | jq '{
    tenant_id,
    ceiling_usd,
    on_exceed,
    fallback_model,
    ceiling_steps
  }'
```

Verify:
- `ceiling_usd` is the team's override (2.00), not the org's (50.0).
- `on_exceed` and `fallback_model` match what was set in step 3.

---

## Step 5: Test with Simulation

Create a draft rollout scoped to the team's first agent chain and run the simulation
to validate the policy compiles and distributes without errors.

```bash
curl -s -X POST "$API/rollouts" \
  -H "X-Veronica-Key: $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "created_by": "ops@example.com",
    "policy_config": {
      "chain_id": "acme-payments-checkout",
      "ceiling_usd": 2.00,
      "on_exceed": "degrade",
      "ceiling_steps": 30
    }
  }' | jq '.id' | xargs -I{} \
  curl -s -X POST "$API/rollouts/{}/simulate" \
    -H "X-Veronica-Key: $KEY" | jq '{state, simulation_result}'
```

Expected state: `"simulated"` with a non-null `policy_hash`. A 422 indicates an invalid
policy config -- check `ceiling_usd >= 0` and `on_exceed` in `{halt, degrade, queue}`.

---

## Step 6: Monitor First 24 Hours

After the onboarded team's agents start sending events, check:

- No unexpected halts: `GET /incidents/{chain_id}/{step_id}` on any halt decision.
- Cost tracking within ceiling: watch `veronica_cost_usd_total{chain_id="acme-payments-checkout"}` in Grafana.
- Effective policy inheritance is stable: re-run step 4 after any org-level override changes.

If the team reports unexpected halts in the first 24 h, run a replay against the incident
window (see incident-response.md steps 4-5) before modifying overrides.
