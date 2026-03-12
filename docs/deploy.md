# Deployment Guide

This document covers deploying the VERONICA control-plane API with its full service stack
(PostgreSQL, Prometheus, Grafana). It assumes you are deploying on a single host for local or
internal use. Production hardening notes are marked where applicable.

---

## Prerequisites

- Python 3.10 or later
- Docker and Docker Compose
- `git` (to clone the repository)

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/amabito/veronica.git
cd veronica
pip install -e ".[metrics]"
```

### 2. Configure environment

Copy the example environment file and set required values:

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```bash
VERONICA_API_KEY=your-secret-key-here
```

See [Configuration](#configuration) for all available variables.

### 3. Start all services

```bash
cd deploy/
docker compose up -d
```

This starts four services: veronica (API), postgres (event store), prometheus, and grafana.
All services bind to `127.0.0.1` only.

Verify the API is running:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{
  "status": "ok",
  "version": "0.8.0",
  "kernel_version": "...",
  "uptime_seconds": 0.12
}
```

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env` and adjust as needed.

### Authentication

| Variable | Required | Default | Description |
|---|---|---|---|
| `VERONICA_API_KEY` | Yes (production) | -- | API key for `X-Veronica-Key` header |
| `VERONICA_AUTH_DISABLED` | No | -- | Set to `1` to disable auth (development only) |

See [Key Management](key-management.md) for how to generate and rotate keys.

### Server

| Variable | Required | Default | Description |
|---|---|---|---|
| `VERONICA_HOST` | No | `127.0.0.1` | Bind address |
| `VERONICA_PORT` | No | `8000` | Bind port |

### CORS

| Variable | Required | Default | Description |
|---|---|---|---|
| `VERONICA_CORS_ORIGINS` | No | `*` | Comma-separated allowed origins, or `*` |

When `VERONICA_CORS_ORIGINS` is set to a specific origin list (not `*`), credentials are enabled.
When using `*`, credentials are disabled (browser security requirement).

### Redis (optional)

| Variable | Required | Default | Description |
|---|---|---|---|
| `VERONICA_REDIS_URL` | No | -- | Redis connection URL, e.g. `redis://localhost:6379/0` |

Without Redis, the in-process memory store is used. This is suitable for single-instance deployments
where distributed budget enforcement across processes is not needed.

### Debug

| Variable | Required | Default | Description |
|---|---|---|---|
| `VERONICA_DEBUG` | No | -- | Set to `1` to include exception details in 500 responses |

Do not set `VERONICA_DEBUG=1` in production.

---

## Accessing Services

After `docker compose up -d`, the following services are available:

| Service | URL | Notes |
|---|---|---|
| VERONICA API | http://127.0.0.1:8000 | Requires `X-Veronica-Key` header |
| API docs (Swagger) | http://127.0.0.1:8000/docs | No auth required |
| Metrics endpoint | http://127.0.0.1:9464/metrics | Prometheus scrape target |
| Prometheus | http://127.0.0.1:9090 | Query interface |
| Grafana | http://127.0.0.1:3000 | Dashboard (anonymous viewer enabled) |
| PostgreSQL | localhost:5432 | Event store (internal use) |

Grafana default admin credentials: `admin` / `veronica`. Change immediately if exposed beyond localhost.

---

## Defining Your First Policy

Once the API is running, create a policy via `PUT /policies/{chain_id}`:

```bash
curl -X PUT http://127.0.0.1:8000/policies/my-agent \
  -H "X-Veronica-Key: your-secret-key-here" \
  -H "Content-Type: application/json" \
  -d '{
    "ceiling_usd": 1.00,
    "on_exceed": "halt",
    "current_version": 0
  }'
```

Retrieve it:

```bash
curl http://127.0.0.1:8000/policies/my-agent \
  -H "X-Veronica-Key: your-secret-key-here"
```

List all policies:

```bash
curl "http://127.0.0.1:8000/policies?page=1&per_page=20" \
  -H "X-Veronica-Key: your-secret-key-here"
```

See the [PolicyConfig specification](policy-config.md) for all available fields.

---

## Running with Custom Host and Port

The `veronica serve` command accepts `--host` and `--port` flags:

```bash
veronica serve --host 0.0.0.0 --port 9000
```

For production deployments, run behind a reverse proxy (nginx, Caddy) rather than exposing
Uvicorn directly.

## Running in Reload Mode (Development)

```bash
veronica serve --reload
```

Auto-reloads on source file changes. Do not use in production.

---

## Production Checklist

- [ ] `VERONICA_API_KEY` is set to a securely generated value (see [Key Management](key-management.md))
- [ ] `VERONICA_AUTH_DISABLED` is not set (or explicitly set to `0`)
- [ ] `VERONICA_DEBUG` is not set
- [ ] Grafana `GF_AUTH_ANONYMOUS_ENABLED` is disabled if Grafana is exposed externally
- [ ] Grafana admin password changed from default (`veronica`)
- [ ] PostgreSQL credentials changed from defaults before any network exposure
- [ ] API server is behind a reverse proxy with TLS
- [ ] `VERONICA_CORS_ORIGINS` is set to explicit origins (not `*`) if the API is browser-accessible

---

## Troubleshooting

### `503 API key not configured`

The `VERONICA_API_KEY` environment variable is not set, and `VERONICA_AUTH_DISABLED=1` is not present.
Set `VERONICA_API_KEY` in your `.env` file.

### `401 Invalid or missing API key`

The `X-Veronica-Key` header is missing or the value does not match `VERONICA_API_KEY`.
Check that the key in your request matches the configured value exactly.

### API server starts but Prometheus shows no metrics

Verify the metrics server is running:

```bash
curl http://127.0.0.1:9464/metrics
```

If the endpoint is unreachable, verify you installed the `metrics` extra (`pip install -e ".[metrics]"`)
and that `start_metrics_server()` is called in your application setup.

### Docker Compose fails to start

Check that ports `8000` (veronica), `5432` (postgres), `9090` (prometheus), and `3000` (grafana)
are not in use:

```bash
# Linux / macOS
lsof -i :8000 -i :5432 -i :9090 -i :3000

# Windows
netstat -ano | findstr "8000\|5432\|9090\|3000"
```

### `409 Conflict` on policy update

The `current_version` in your request does not match the server's stored version (optimistic
concurrency). Fetch the current policy with `GET /policies/{chain_id}`, read its `version` field,
and retry with that value as `current_version`.
