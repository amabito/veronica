# [Organization] -- VERONICA Case Study

## Overview

- **Team size**: (e.g., 5 engineers, 3 AI agents in production)
- **Use case**: (e.g., customer support automation, code review pipeline)
- **Deployment date**:
- **VERONICA version**: (e.g., v0.7.0)
- **Integration method**: direct / adapter / MCP

---

## Before VERONICA

- **Cost controls**: (e.g., manual token limits, ad-hoc per-script caps, none)
- **Incident detection time (MTTD)**: (e.g., "discovered next billing cycle", "~2 hours")
- **Policy change cycle time**: (e.g., "redeploy required, ~1 day")
- **Runaway incident examples**: (optional -- describe any past overspend events)

---

## Implementation

### Tenant and chain structure

(Describe how chain IDs map to agents/workloads. Example: one chain per agent role,
one chain per customer tier, shared chains for batch tasks.)

### Key policies configured

```json
{
  "chain_id": "example-agent",
  "ceiling_usd": 1.00,
  "on_exceed": "halt",
  "step_limit": 50
}
```

(List the 2-3 most important policies and why those limits were chosen.)

### Integration path

(How veronica-core is wired: `ShieldPipeline`, `BudgetEnforcer`, Redis for distributed
budget, or via the HTTP control-plane API. Which adapters are in use.)

### Rollout sequence

1. (e.g., deployed in simulation mode for 1 week)
2. (e.g., enabled DEGRADE before HALT)
3. (e.g., tightened ceilings after observing p95 spend)

---

## Results

| Metric | Before | After |
|--------|--------|-------|
| Monthly LLM cost | | |
| Cost reduction | -- | |
| MTTD (runaway detection) | | |
| Policy change cycle time | | |
| HALT/DEGRADE false positive rate | -- | |

(Fill in observed numbers. Estimates are acceptable if exact figures are unavailable.)

---

## Policy Evolution

- **Initial policy**: (what was configured on day 1 and why)
- **Adjustments made**: (what changed after observing real traffic, and what triggered each change)
- **Current steady-state**: (final configuration, rationale for current limits)

---

## Lessons Learned

- (e.g., start ceilings 3x above expected p95 spend, tighten after a week of data)
- (e.g., DEGRADE is more useful than HALT for interactive agents; HALT for batch)
- (e.g., per-tenant chain IDs simplify audit more than per-model chains)

---

## Recommendations for Others

- (concrete advice based on your deployment experience)
- (what to configure first, what to defer)
- (common mistakes to avoid)
