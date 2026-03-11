# veronica

**LLM governance control plane.**

Policy authoring, simulation, rollout pipelines, tenant hierarchy, incident replay,
and audit dashboards -- built on [veronica-core](https://github.com/amabito/veronica-core).

---

## What This Is

veronica is the control plane for [veronica-core](https://github.com/amabito/veronica-core).

veronica-core is the deterministic enforcement kernel -- cost ceilings, step limits,
circuit breakers, distributed budget. It runs local, has no dependencies, and its
guarantees are unconditional.

This repository is the management layer on top: author policies, test them in simulation,
roll them out through approval gates, organize tenants, and audit what happened.

```
Your Application
       |
  veronica-core    -- enforcement kernel (pip install veronica-core)
       |
  veronica-cp      -- control plane (pip install veronica-cp)
       |
  LLM Providers
```

---

## Features

- **Policy authoring** -- create and version cost/step/token policies per chain via HTTP API
- **Simulation** -- dry-run steps against a policy before deploying (`POST /simulate`)
- **Rollout pipeline** -- DRAFT -> SIMULATED -> APPROVED -> PROMOTED -> ACTIVE -> REVOKED
- **Tenant hierarchy** -- org -> team -> chain with policy inheritance and narrowing overrides
- **Incident replay** -- re-evaluate recorded events under alternative policies
- **Audit dashboard** -- event log, decision timeline, cost tracking (Grafana + built-in UI)
- **HMAC-signed bundles** -- immutable policy distribution with integrity verification
- **PostgreSQL backend** -- persistent event store (in-memory default for dev)
- **Prometheus metrics** -- `veronica_decisions_total`, `veronica_cost_usd_total`, etc.

---

## Install

```bash
pip install veronica-cp
```

Optional extras:

```bash
pip install veronica-cp[postgres]   # PostgreSQL event store
pip install veronica-cp[redis]      # distributed budget (Redis Arbiter)
pip install veronica-cp[metrics]    # Prometheus metrics exporter
```

> **`pip install veronica` is a different, unrelated package on PyPI.**
> This project's PyPI distribution is `veronica-cp`. The Python import remains `import veronica`.
> Do not install both `veronica` (PyPI) and `veronica-cp` in the same environment --
> they share the `veronica` import namespace and will conflict.

---

## Quickstart

### HTTP API (primary interface)

```bash
cp .env.example .env   # set VERONICA_API_KEY
cd deploy/ && docker compose up -d
```

| Service    | URL                           |
|------------|-------------------------------|
| API + Docs | http://127.0.0.1:8000/docs    |
| Dashboard  | http://127.0.0.1:8000/ui      |
| Grafana    | http://127.0.0.1:3000         |
| Prometheus | http://127.0.0.1:9090         |
| Health     | http://127.0.0.1:8000/health  |

```bash
# Create a policy
curl -X PUT http://localhost:8000/policies/my-agent \
  -H "X-Veronica-Key: $VERONICA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"chain_id":"my-agent","ceiling_usd":1.0,"on_exceed":"halt","current_version":0}'

# Simulate enforcement
curl -X POST http://localhost:8000/simulate \
  -H "X-Veronica-Key: $VERONICA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"policy":{"chain_id":"my-agent","ceiling_usd":1.0,"on_exceed":"halt"},
       "steps":[{"kind":"llm","cost_usd":0.30,"tokens_out":500,"elapsed_ms":200}]}'
```

### Embedded Python (for kernel-level integration)

```bash
pip install veronica-cp[metrics]
```

```python
from veronica import VeronicaOS, BufferedEmitter, MetricsSubscriber
from veronica.metrics_exporter import start_metrics_server

start_metrics_server()  # :9464/metrics
emitter = BufferedEmitter()
emitter.subscribe("prometheus", MetricsSubscriber())
vos = VeronicaOS(emitter=emitter)
```

---

## Architecture

```
veronica-core (kernel)       veronica (control plane)
-------------------------    --------------------------------
ExecutionContext              Policy authoring (PUT /policies)
ShieldPipeline               Simulation (POST /simulate)
BudgetEnforcer               Rollout pipeline (/rollouts)
CircuitBreaker               Tenant hierarchy (/tenants)
AdaptiveBudgetHook           Incident replay (POST /replay)
Distributed budget (Redis)   Event ingest + audit dashboard
OTel export                  Prometheus + Grafana
```

The kernel enforces. The control plane manages.

veronica-core's guarantees are unconditional -- they do not depend on the control plane.
The control plane extends those guarantees with policy lifecycle, visibility, and
organizational structure.

---

## Status

**veronica-core**: [v3.4.3](https://github.com/amabito/veronica-core) -- stable, 4837 tests.

**veronica (this repo)**: v0.8.0 -- 1197 tests.

| Version | Milestone |
|---------|-----------|
| v0.8.0  | Control plane GA: HTTP API, dashboard, deploy stack, tenant hierarchy, rollout pipeline, incident replay, design partner docs |
| v0.7.0  | Org policy engine: validate/clamp, `step_denied` metric |
| v0.6.0  | LLM integration adapter: `step()` context manager |
| v0.5.0  | Grafana dashboard: metrics exporter, docker-compose |

Single-org deployment. No federation, no cross-org, no multi-tenant SaaS.

---

## Docs

- [Onboarding guide](docs/onboarding.md) -- your first 30 minutes
- [PolicyConfig specification](docs/policy-config.md)
- [Deployment guide](docs/deploy.md)
- [Deployment checklist](docs/deployment-checklist.md)
- [Key management](docs/key-management.md)
- [Architecture](docs/architecture.md)
- [Observability](docs/observability.md)
- Runbooks: [policy rollout](docs/runbooks/policy-rollout.md), [incident response](docs/runbooks/incident-response.md), [tenant onboarding](docs/runbooks/tenant-onboarding.md)
- [CHANGELOG](CHANGELOG.md)

---

## Roadmap

Near-term (control plane hardening):
- External design partner deployment
- TriMemory kernel integration
- Multi-agent workload validation

Future (not started):
- Federation / cross-org policy coordination
- Multi-tenant SaaS mode

---

## License

MIT
