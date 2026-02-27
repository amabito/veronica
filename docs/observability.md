# VERONICA Observability Guide

VERONICA ships three observability layers that compose independently. Use any combination.

```
VeronicaOS  ──emit()──>  BufferedEmitter  ──subscribe()──>  MetricsSubscriber   ──>  Prometheus
                                          ──subscribe()──>  StructuredLogSubscriber ──>  JSON logs
                                                           start_metrics_server()  ──>  HTTP /metrics
                                                           deploy/                 ──>  Grafana dashboard
```

---

## 1. Setup

### Minimal (metrics only)

```python
from veronica import VeronicaOS, BufferedEmitter, MetricsSubscriber
from veronica.metrics_exporter import start_metrics_server

start_metrics_server()  # HTTP :9464/metrics
emitter = BufferedEmitter()
emitter.subscribe("prometheus", MetricsSubscriber())
vos = VeronicaOS(emitter=emitter)
```

### Minimal (logs only)

```python
from veronica import VeronicaOS, BufferedEmitter, StructuredLogSubscriber

emitter = BufferedEmitter()
emitter.subscribe("json_log", StructuredLogSubscriber())
vos = VeronicaOS(emitter=emitter)
```

### Full stack (metrics + logs + dashboard)

```python
from veronica import (
    VeronicaOS, BufferedEmitter,
    MetricsSubscriber, StructuredLogSubscriber,
)
from veronica.metrics_exporter import start_metrics_server

start_metrics_server()
emitter = BufferedEmitter()
emitter.subscribe("prometheus", MetricsSubscriber())
emitter.subscribe("json_log", StructuredLogSubscriber())
vos = VeronicaOS(emitter=emitter)
```

```bash
cd deploy/ && docker compose up -d
# Grafana: http://127.0.0.1:3000
# Prometheus: http://127.0.0.1:9090
```

---

## 2. Components

### BufferedEmitter

Ring buffer (default 1024) with subscriber fan-out. Thread-safe.

| Property | Description |
|----------|-------------|
| `dropped_total` | Events lost to buffer overflow. Monitor this; if non-zero, increase `maxlen` or speed up subscribers. |

Subscribers that raise 3 consecutive exceptions are auto-unsubscribed.

### MetricsSubscriber

Translates `step_completed` events into Prometheus metrics.

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `veronica_steps_total` | Counter | status, kind, recommendation, risk_level | Steps executed |
| `veronica_step_elapsed_ms` | Histogram | kind | Step wall-clock time |
| `veronica_stage_elapsed_ms` | Histogram | stage | Per-pipeline-stage time |
| `veronica_cost_microusd_total` | Counter | -- | Cumulative cost (1 USD = 1,000,000) |
| `veronica_degrade_total` | Counter | degrade_reason | Degraded steps |

**Integer cost**: `cost_microusd_total` uses integer increments to avoid float accumulation drift. Divide by 1,000,000 for USD.

**Registry param**: Pass an isolated `CollectorRegistry` in tests to prevent double-registration errors. Production code uses the global registry by default.

**Double-registration safety**: `_get_or_create` returns existing collectors if already registered under the same name.

### StructuredLogSubscriber

Emits one JSON line per `step_completed` event to a Python logger.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `logger_name` | `"veronica.events"` | Logger name |
| `level` | `logging.INFO` | Log level |

Signals are capped at 16 entries per event (`_MAX_SIGNALS_LOG`).

**JSON formatter note**: If your root logger already uses a JSON formatter, the message field will contain a nested JSON string. Subclass and use `extra={"veronica": record}` instead.

### start_metrics_server()

Starts Prometheus HTTP server on a background thread.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `port` | `9464` | Port (overridden by `VERONICA_METRICS_PORT` env var) |
| `addr` | `"0.0.0.0"` | Bind address |

Returns `True` if started, `False` if `prometheus_client` is not installed.

**Guards**:
- ImportError: returns `False` without crashing when `prometheus_client` is absent.
- Double-start: module-level `_started` flag prevents binding the same port twice within one process.

**Multi-process note**: The double-start guard is per-process only. In gunicorn/uvicorn with multiple workers, each worker starts its own server. Assign distinct ports per worker via `VERONICA_METRICS_PORT`, or run a single dedicated metrics process.

---

## 3. Grafana Dashboard

The `deploy/` directory provides a zero-config dashboard via Grafana JSON provisioning.

```
deploy/
  docker-compose.yml              Prometheus + Grafana (127.0.0.1 only)
  prometheus/prometheus.yml       Scrape config (host.docker.internal:9464)
  grafana/provisioning/
    datasources/prometheus.yml    Auto-configured datasource
    dashboards/dashboard.yml      File-based dashboard provider
  grafana/dashboards/
    veronica.json                 5-panel operations dashboard
```

### Panels

| # | Title | Query | Purpose |
|---|-------|-------|---------|
| 1 | Steps / sec | `sum by (status) (rate(veronica_steps_total[5m]))` | Processing rate, error spikes |
| 2 | Cost Burn Rate | `rate(veronica_cost_microusd_total[5m]) / 1000000` | Budget burn velocity (USD/sec) |
| 3 | Step Latency | `histogram_quantile(0.50/0.95/0.99, sum by (le) (rate(veronica_step_elapsed_ms_bucket[5m])))` | Latency distribution |
| 4 | Stage Breakdown P95 | `histogram_quantile(0.95, sum by (le, stage) (rate(veronica_stage_elapsed_ms_bucket[5m])))` | Slow pipeline stage |
| 5 | Degrade Rate | `sum by (degrade_reason) (rate(veronica_degrade_total[5m]))` | Degradation frequency |

**Dashboard UID**: `veronica-os` (fixed). Grafana provisioning uses this for stable updates.

### Security

- **Prometheus and Grafana bind to 127.0.0.1 only.** Do not expose to public networks.
- Anonymous viewer access is enabled for local use. Disable `GF_AUTH_ANONYMOUS_ENABLED` for external deployments.
- Default admin password is `veronica`. Change it for any non-local use.

### Linux note

`host.docker.internal` requires Docker 20.10+ with `extra_hosts: host-gateway`. If it does not resolve, replace the target in `prometheus/prometheus.yml`:

```yaml
- targets: ["172.17.0.1:9464"]  # or: ip route | grep docker0 | awk '{print $9}'
```

---

## 4. Event Schema

Every `step_completed` event carries 16 fields. See [schema-v1.md](schema-v1.md) for the full contract.

Key points:
- `schema_version: 1` enables future evolution. Subscribers MUST ignore unknown fields.
- `stage_time_ms` keys are restricted to: `collector`, `analyzer`, `cost_model`, `planner`, `arbiter`, `store`, `emit`.
- `signals` entries contain `kind` and `severity` only (no `detail`).
- `cost_usd` is float at the event level; MetricsSubscriber converts to integer microusd.

---

## 5. Operational Recipes

### Alert on error rate spike

```promql
# Fire when error rate exceeds 10% of total steps over 5 minutes
sum(rate(veronica_steps_total{status="error"}[5m]))
/ sum(rate(veronica_steps_total[5m]))
> 0.10
```

### Alert on cost burn

```promql
# Fire when burn exceeds $1/min
rate(veronica_cost_microusd_total[5m]) / 1000000 * 60 > 1.0
```

### Monitor buffer backpressure

```python
emitter = BufferedEmitter(maxlen=4096)
# Periodically check:
if emitter.dropped_total > 0:
    log.warning("Events dropped: %d", emitter.dropped_total)
```

### Extract structured logs with jq

```bash
# All error steps
python app.py 2>&1 | grep veronica.events | jq -r 'select(.status == "error")'

# Slowest stages
python app.py 2>&1 | grep veronica.events | jq '.stage_time_ms | to_entries | sort_by(-.value) | .[0]'
```

---

## 6. Dependency Matrix

| Feature | Required Package | Install |
|---------|-----------------|---------|
| MetricsSubscriber | `prometheus-client` | `pip install veronica[metrics]` |
| StructuredLogSubscriber | -- (stdlib logging) | included |
| start_metrics_server | `prometheus-client` | `pip install veronica[metrics]` |
| Dashboard | Docker, Docker Compose | `cd deploy/ && docker compose up` |

`prometheus-client` is optional. All Prometheus features degrade gracefully when absent.
