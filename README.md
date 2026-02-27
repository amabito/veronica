# VERONICA

**The Execution OS for LLM systems.**

[veronica-core](https://github.com/amabito/veronica-core) is the containment engine.
VERONICA is the Execution OS built around it.

---

## What This Is

LLM execution systems need two distinct layers:

**Engine** ([veronica-core](https://github.com/amabito/veronica-core)): deterministic enforcement of cost, step, and retry limits. Runs local. No dependencies. Guarantees are unconditional.

**OS** (this repository): everything above the engine. Policy planning, budget allocation across agents, cost prediction, organizational governance, and cloud coordination.

```
Application
     |
veronica-core   -- local containment (OSS engine)
     |
VERONICA        -- Execution OS (Planner / Cloud / org policy)
     |
LLM Providers
```

---

## Quickstart: Metrics + Dashboard

```bash
pip install veronica[metrics]
```

```python
from veronica import VeronicaOS, BufferedEmitter, MetricsSubscriber
from veronica.metrics_exporter import start_metrics_server

start_metrics_server()  # :9464/metrics
emitter = BufferedEmitter()
emitter.subscribe("prometheus", MetricsSubscriber())
vos = VeronicaOS(emitter=emitter)
```

```bash
cd deploy/ && docker compose up -d
```

| Service    | URL                        |
|------------|----------------------------|
| Metrics    | http://127.0.0.1:9464/metrics |
| Prometheus | http://127.0.0.1:9090      |
| Grafana    | http://127.0.0.1:3000      |

**Grafana and Prometheus bind to 127.0.0.1 only. Do NOT expose to public networks.** Anonymous viewer access is enabled for local use; if you deploy externally, disable `GF_AUTH_ANONYMOUS_ENABLED` and set a strong admin password.

---

## Layers

### veronica-core (Engine)
- `ExecutionContext` — bounded execution scope
- `ShieldPipeline` — pre-call enforcement hooks
- `BudgetEnforcer` — cost ceiling per chain
- `CircuitBreaker` — failure isolation
- `AdaptiveBudgetHook` — feedback-based ceiling adjustment
- Distributed budget (Redis), OTel export, multi-agent containment

Single library. MIT. `pip install veronica-core`. No cloud required.

### VERONICA (OS)
Coordination and governance layer built on veronica-core:

- **Planner** — decides what limits to set per agent and workload
- **Budget allocation** — distributes budget across competing agents
- **Cost prediction** — estimates spend before LLM calls are made
- **Arbitration** — resolves contention under shared resource constraints
- **Org policy engine** — organization-wide containment rules
- **Dashboard and alerts** — visibility into execution health
- **Compliance layer** — audit trail and policy enforcement at scale

---

## Design Principle

The engine enforces. The OS decides.

veronica-core's guarantees are unconditional — they do not depend on VERONICA being present.
VERONICA extends those guarantees across agents, services, and organizations.

A probabilistic or adaptive layer must never sit inside the enforcement boundary.

---

## Status

veronica-core is [v1.0](https://github.com/amabito/veronica-core/releases/tag/v1.0.0). Engine is stable.

| v0.5.0 | Phase 5 | Grafana Dashboard: metrics exporter, docker-compose provisioning, 5-panel dashboard |

**Current:** v0.5.0 -- 201 tests, 93% coverage. Protocol interfaces stable since v0.1.0.

---

## Docs

- [PolicyConfig specification](docs/policy-config.md) — the Planner/Executor contract

---

## License

MIT
