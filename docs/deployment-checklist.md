# Deployment Checklist

Pre-production checklist for VERONICA control-plane deployments.
Complete all items before routing live traffic.

---

## 1. Infrastructure

- [ ] Docker and Docker Compose installed on the target host
- [ ] Ports 8000 (API), 5432 (PostgreSQL), 9090 (Prometheus), 3000 (Grafana), 9464 (metrics) are available
- [ ] `docker compose up -d` completes without errors (`cd deploy/`)
- [ ] `GET /health` returns `{"status": "ok"}` with the expected version
- [ ] PostgreSQL data directory is on a persistent volume (not the container ephemeral layer)
- [ ] Redis deployed if distributed budget enforcement across processes is required
  (`VERONICA_REDIS_URL` set and reachable from the API container)
- [ ] Host has sufficient memory for PostgreSQL + Prometheus retention (minimum 2 GB recommended)

---

## 2. Security

- [ ] `VERONICA_API_KEY` set to a securely generated value (see `docs/key-management.md`)
- [ ] `VERONICA_AUTH_DISABLED` is unset (or explicitly `0`) -- never `1` in production
- [ ] `VERONICA_DEBUG` is unset -- never `1` in production
- [ ] `VERONICA_CORS_ORIGINS` set to an explicit origin list, not `*`, if the API is browser-accessible
- [ ] API server is behind a reverse proxy (nginx, Caddy) with TLS termination
- [ ] Grafana admin password changed from default (`veronica`) -- `GF_SECURITY_ADMIN_PASSWORD`
- [ ] `GF_AUTH_ANONYMOUS_ENABLED` disabled if Grafana is reachable beyond localhost
- [ ] PostgreSQL credentials (`POSTGRES_PASSWORD`, `POSTGRES_USER`) changed from defaults
- [ ] `.env` file excluded from version control (`.gitignore` entry present)
- [ ] API binds to `127.0.0.1` by default; confirm `VERONICA_HOST` is not set to `0.0.0.0`
  unless a reverse proxy is in front

---

## 3. Observability

- [ ] `pip install veronica-cp[metrics]` installed (metrics extra)
- [ ] `GET http://127.0.0.1:9464/metrics` returns Prometheus-formatted output
- [ ] Prometheus scraping `9464/metrics` -- verify in Prometheus Targets UI (`/targets`)
- [ ] Grafana dashboard loads and shows data (open `http://127.0.0.1:3000`)
- [ ] Alerting rules configured for: cost ceiling breaches, HALT events, API error rate
- [ ] Log retention policy confirmed (Docker logging driver or external collector configured)
- [ ] `step_denied` metric baseline established before go-live (should be near zero initially)

---

## 4. Policy Configuration

- [ ] At least one policy defined via `PUT /policies/{chain_id}` before routing traffic
- [ ] Initial `ceiling_usd` set to at least 3x observed p95 spend (conservative start)
- [ ] `on_exceed` set to `degrade` for interactive agents; `halt` for batch/autonomous agents
- [ ] `step_limit` configured where unbounded agent loops are a risk
- [ ] Policy simulation run against representative historical traffic (if available)
- [ ] `GET /policies` returns all expected chain policies with correct versions
- [ ] Policy version conflict behavior tested: confirm `409 Conflict` on stale `current_version`
- [ ] Gradual rollout plan documented: simulation mode -> degrade -> halt

---

## 5. Integration

- [ ] veronica-core kernel connected to the control-plane API
- [ ] `ShieldPipeline` and `BudgetEnforcer` initialized with the expected chain IDs
- [ ] Event flow verified: a test LLM call produces an event visible in Grafana
- [ ] HALT path tested end-to-end: trigger a ceiling breach and confirm the agent stops
- [ ] DEGRADE path tested if `on_exceed: degrade` is in use
- [ ] Redis budget synchronization tested under concurrent load (if Redis is configured)
- [ ] Adapter compatibility confirmed for all LLM providers in use (OpenAI, Anthropic, etc.)

---

## 6. Backup

- [ ] PostgreSQL backup schedule configured (daily minimum for production)
- [ ] Backup includes policy table and event store
- [ ] Restore procedure tested on a non-production host at least once
- [ ] Policy export via `GET /policies` scripted and stored alongside infrastructure backups
- [ ] Recovery time objective (RTO) documented and acceptable to stakeholders

---

## 7. Go-live

- [ ] Smoke test: create a policy, make a test LLM call, verify spend recorded in Grafana
- [ ] On-call rotation established for HALT/DEGRADE alerts
- [ ] Escalation path documented: who to contact if the API is unreachable
- [ ] Rollback plan documented: steps to revert to previous policy versions
- [ ] Design partner contact confirmed for first 48 hours post-launch monitoring
- [ ] `VERONICA_DEBUG` confirmed absent from production environment one final time
