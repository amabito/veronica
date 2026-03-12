# VERONICA

![PyPI](https://img.shields.io/pypi/v/veronica-cp?label=PyPI&cacheSeconds=60)
![CI](https://img.shields.io/badge/tests-1200%20passing-brightgreen)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-blue)

**LLM governance control plane.**

LLM agent systems fail silently -- budget overruns, runaway loops, policy violations surface
as production incidents, not compile errors. VERONICA lets you author enforcement policies,
test them in simulation, roll them out through approval gates, and audit what happened --
before the cost spike hits your dashboard.

Built on [veronica-core](https://github.com/amabito/veronica-core) (the enforcement kernel).
The kernel enforces inline. The control plane manages from the side.

---

## What This Is

VERONICA is the management layer for [veronica-core](https://github.com/amabito/veronica-core).

veronica-core is the deterministic enforcement kernel -- cost ceilings, step limits,
circuit breakers, distributed budget. It runs in your application's process, has no
dependencies, and its guarantees are unconditional.

This repository is the control plane: author policies, test them in simulation,
roll them out through approval gates, organize tenants, and audit decisions.

```
Your Application ──► veronica-core ──► LLM Providers
                     (enforcement)
                          ▲
                          │ policy sync / event ingest
                          │
                      VERONICA
                    (control plane)
```

veronica-core works without VERONICA. VERONICA makes it manageable.

**This is not** a federation platform, a multi-tenant SaaS, or an observability-only tool.
Single-org deployment. The kernel does the enforcement; the control plane does the governance.

---

## Features

### Policy lifecycle

- **Policy authoring** -- create and version cost/step/token policies per chain via HTTP API
- **Simulation** -- dry-run steps against a policy before deploying (`POST /simulate`)
- **Rollout pipeline** -- DRAFT -> SIMULATED -> APPROVED -> PROMOTED -> ACTIVE -> REVOKED
- **HMAC-signed bundles** -- policy distribution with shared-secret integrity checks (not public-key; internal use)

### Operations and audit

- **Tenant hierarchy** -- org -> team -> chain with policy inheritance and narrowing overrides
- **Incident replay** -- re-evaluate recorded events under alternative policies
- **Audit dashboard** -- event log, decision timeline, cost tracking (Grafana + built-in UI)

### Platform

- **PostgreSQL backend** -- persistent event store (in-memory default for dev)
- **Prometheus metrics** -- `veronica_decisions_total`, `veronica_cost_usd_total`, etc.
- **Docker Compose** -- single-command deployment with Grafana and Prometheus

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

> **Note:** The PyPI package is `veronica-cp` (the `veronica` name belongs to an unrelated project).
> The Python import remains `import veronica`. Do not install both in the same environment.

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

### Connecting veronica-core to the control plane

veronica-core enforces locally. VERONICA collects events and distributes policies.
Minimal wiring:

```python
from veronica_core import ExecutionContext
from veronica import VeronicaOS, BufferedEmitter, MetricsSubscriber
from veronica.metrics_exporter import start_metrics_server

# 1. Start the metrics endpoint
start_metrics_server()  # :9464/metrics

# 2. Wire the emitter (events flow to VERONICA)
emitter = BufferedEmitter()
emitter.subscribe("prometheus", MetricsSubscriber())

# 3. Create the OS layer (bridges kernel <-> control plane)
vos = VeronicaOS(emitter=emitter)

# 4. Use veronica-core as usual -- the kernel enforces,
#    VERONICA observes and manages
with vos.step(chain_id="my-agent", kind="llm") as ctx:
    result = call_your_llm(prompt="...")
    ctx.record_cost(result.usage.total_cost)
```

The kernel runs inline in your process. VERONICA runs as a separate service.
If VERONICA is down, veronica-core keeps enforcing with last-known policy.

---

## Architecture

```
veronica-core (kernel)       VERONICA (control plane)
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
VERONICA extends those guarantees with policy lifecycle, visibility, and
organizational structure.

---

## Status

**veronica-core**: [v3.7.1](https://github.com/amabito/veronica-core) -- stable, 5766 tests.

**VERONICA (this repo)**: v0.8.1 -- 1200 tests.

| Version | Milestone |
|---------|-----------|
| v0.8.1  | PyPI initial release as `veronica-cp` |
| v0.8.0  | Initial public release: HTTP API, dashboard, deploy stack, tenant hierarchy, rollout pipeline, incident replay |
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
