# Architecture: Execution OS Control Plane

## Overview

VERONICA is the **Execution OS** for LLM systems.
[veronica-core](https://github.com/amabito/veronica-core) is the **Runtime Containment Kernel** it is built on.

## Stack

```
Application
     |
veronica-core --------[policy config]-------- VERONICA (Control Plane)
     |                                              |
LLM Providers                              Org Policy / Dashboard /
                                           Shared State / Audit / Alerts
```

**veronica-core is on the critical path. VERONICA is on the management path.**

The Control Plane manages policy and state asynchronously.
It does not sit between the application and LLM calls.
This preserves veronica-core's latency guarantees regardless of VERONICA availability.

---

## OS Analogy

| OS Layer | VERONICA |
|---|---|
| Kernel | veronica-core |
| Control Plane | VERONICA (this repository) |
| User Space | Application |
| Hardware abstraction | LLM Providers |

---

## Layer Responsibilities

### veronica-core (Kernel)

Enforces bounded execution. Runs local. No cloud required.
See [veronica-core architecture](https://github.com/amabito/veronica-core/blob/main/docs/architecture.md).

### VERONICA (Control Plane)

Manages policy across agents, services, and organizations.

| Component | Role |
|---|---|
| Planner | Execution strategy — decides what limits to set |
| Budget allocation | Distributes budget across competing agents |
| Cost prediction | Estimates spend before LLM calls are made |
| Arbitration | Resolves contention under shared resource constraints |
| Org policy engine | Organization-wide containment rules |
| Shared circuit state | Cross-service breaker coordination |
| Dashboard | Visibility into execution health |
| Audit / Compliance | Policy enforcement at scale |

---

## Design Principles

**The kernel enforces. The OS decides.**

veronica-core's guarantees are unconditional. They hold whether VERONICA is present or not.
VERONICA extends those guarantees across agents, services, and organizations.

**VERONICA does not replace veronica-core.**

Every VERONICA deployment requires veronica-core at the enforcement boundary.
VERONICA adds coordination and governance. It does not add enforcement.

**Planner scope boundary.**

The Planner decides *what limits to set* — ceiling, timeout, escalation policy.
The Planner does not decide *what the agent does* — routing, model selection, prompt construction.
Crossing this boundary turns the Planner into an orchestrator. That is a different product.

---

## Feedback Loop

```
veronica-core --[SafetyEvents]--> VERONICA Planner --[PolicyConfig]--> veronica-core
```

The kernel never modifies its behavior based on SafetyEvents mid-execution.
Adaptation always flows via a new PolicyConfig from the Planner.

See [PolicyConfig specification](policy-config.md).

---

## Status

Early stage. Implementation begins when `PlannerProtocol` is defined in veronica-core (planned v1.0).

Tracking: [veronica-core roadmap](https://github.com/amabito/veronica-core#roadmap)
