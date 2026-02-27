# Phase 5: Grafana Dashboard -- Provisioning-First

**Goal:** Ship a `docker compose up` experience that gives users a working VERONICA dashboard with zero manual config.

**Approach:** Grafana JSON provisioning + docker-compose (Prometheus + Grafana). VERONICA exports metrics via opt-in `start_metrics_server()`. CI smoke test validates the full stack.

**Scope:**
1. `metrics_exporter.py` -- Prometheus HTTP server helper (opt-in, ImportError-safe, double-start guard)
2. `deploy/` directory -- docker-compose.yml, Prometheus config, Grafana provisioning, dashboard JSON
3. 5-panel Grafana dashboard (operations-focused, not UI-focused)
4. CI smoke test (docker-based, polling-aware)

**Protocol changes:** None.
**os.py pipeline structure:** Unchanged.

---

## 1. Metrics Exporter

### src/veronica/metrics_exporter.py

```python
"""Optional Prometheus HTTP exporter for VERONICA metrics."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 9464
_started = False


def start_metrics_server(
    port: int | None = None,
    addr: str = "0.0.0.0",
) -> bool:
    """Start Prometheus HTTP metrics server.

    Port resolution order:
    1. Explicit ``port`` argument
    2. ``VERONICA_METRICS_PORT`` environment variable
    3. Default 9464

    Returns True if server started, False if prometheus_client is
    not installed or server was already started.
    """
    global _started
    if _started:
        return True

    try:
        from prometheus_client import start_http_server
    except ImportError:
        logger.debug("prometheus_client not installed; metrics server disabled")
        return False

    resolved = port if port is not None else int(
        os.environ.get("VERONICA_METRICS_PORT", _DEFAULT_PORT),
    )
    start_http_server(resolved, addr=addr)
    _started = True
    logger.info("VERONICA metrics server started on %s:%d", addr, resolved)
    return True
```

### Safety

- **ImportError guard**: `prometheus_client` absent -> returns False, no crash.
- **Double-start guard**: Module-level `_started` flag. Second call returns True immediately.
- **Return value**: `bool` for testability (started vs disabled).
- **Opt-in only**: Does nothing unless explicitly called.

### User quickstart (3 lines)

```python
from veronica import VeronicaOS, BufferedEmitter, MetricsSubscriber
from veronica.metrics_exporter import start_metrics_server

start_metrics_server()  # :9464/metrics
emitter = BufferedEmitter()
emitter.subscribe("prometheus", MetricsSubscriber())
vos = VeronicaOS(emitter=emitter)
```

---

## 2. docker-compose + Provisioning

### deploy/docker-compose.yml

```yaml
services:
  prometheus:
    image: prom/prometheus:v2.51.0
    volumes:
      - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports:
      - "127.0.0.1:9090:9090"
    extra_hosts:
      - "host.docker.internal:host-gateway"

  grafana:
    image: grafana/grafana:10.4.0
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=veronica
      - GF_PATHS_PROVISIONING=/etc/grafana/provisioning
      - GF_AUTH_ANONYMOUS_ENABLED=true
      - GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
      - ./grafana/dashboards:/var/lib/grafana/dashboards:ro
    ports:
      - "127.0.0.1:3000:3000"
    depends_on:
      - prometheus
```

### deploy/prometheus/prometheus.yml

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: "veronica"
    static_configs:
      # Requires Docker 20.10+ (extra_hosts: host-gateway).
      # If host.docker.internal does not resolve on your Linux:
      #   Replace with your host IP, e.g. "172.17.0.1:9464"
      #   Or run: ip route | grep docker0 | awk '{print $9}'
      - targets: ["host.docker.internal:9464"]
```

### deploy/grafana/provisioning/datasources/prometheus.yml

```yaml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

### deploy/grafana/provisioning/dashboards/dashboard.yml

```yaml
apiVersion: 1
providers:
  - name: veronica
    orgId: 1
    type: file
    disableDeletion: true
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards
      foldersFromFilesStructure: false
```

### Provisioning path safety

- `GF_PATHS_PROVISIONING=/etc/grafana/provisioning` explicit in compose.
- `options.path: /var/lib/grafana/dashboards` explicit in dashboard.yml.
- JSON mounted to that exact path via compose volume.

### Network safety

- Both `prometheus` and `grafana` bind to `127.0.0.1` only.
- Anonymous Viewer enabled (local-only, not exposed to public network).

---

## 3. Dashboard Panels (5 panels)

### Panel 1: Steps / sec (status)

```promql
sum by (status) (rate(veronica_steps_total[5m]))
```

Type: Time series (stacked). Purpose: Processing rate, error/halted spike detection.

### Panel 2: Cost Burn Rate

```promql
rate(veronica_cost_microusd_total[5m]) / 1000000
```

Type: Time series. Unit: USD/sec. Purpose: Budget burn velocity.

### Panel 3: Step Latency P50/P95/P99

```promql
histogram_quantile(0.50, sum by (le) (rate(veronica_step_elapsed_ms_bucket[5m])))
histogram_quantile(0.95, sum by (le) (rate(veronica_step_elapsed_ms_bucket[5m])))
histogram_quantile(0.99, sum by (le) (rate(veronica_step_elapsed_ms_bucket[5m])))
```

Type: Time series. Legend: p50 / p95 / p99. Purpose: Latency distribution.

### Panel 4: Stage Breakdown P95

```promql
histogram_quantile(0.95, sum by (le, stage) (rate(veronica_stage_elapsed_ms_bucket[5m])))
```

Type: Time series. Legend: {{stage}}. Purpose: Which pipeline stage is slow.

### Panel 5: Degrade Rate (reason)

```promql
sum by (degrade_reason) (rate(veronica_degrade_total[5m]))
```

Type: Time series. Legend: {{degrade_reason}}. Purpose: Degradation frequency by cause.

### Design principles

- **Operations-focused**: 5 panels for anomaly detection, not UI polish.
- **No high-cardinality queries**: All queries use `sum by` to aggregate labels.
- **Correct histogram_quantile**: Always `sum by (le, ...)` before quantile computation.

---

## 4. CI Smoke Test

### Polling helper

```python
def _poll(url, predicate, timeout=30, interval=1):
    """Poll URL until predicate(response) is True."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=5)
            if resp.status_code == 200 and predicate(resp):
                return resp
        except httpx.ConnectError:
            pass
        time.sleep(interval)
    pytest.fail(f"Timed out polling {url}")
```

### Tests

```python
@pytest.mark.skipif(not docker_available(), reason="Docker not available")
class TestDashboardSmoke:

    def test_grafana_health(self, compose_up):
        _poll(
            "http://127.0.0.1:3000/api/health",
            lambda r: r.json().get("database") == "ok",
        )

    def test_dashboard_provisioned(self, compose_up):
        resp = _poll(
            "http://127.0.0.1:3000/api/search?query=veronica",
            lambda r: any(
                "veronica" in d.get("title", "").lower()
                for d in r.json()
            ),
        )
        assert resp is not None

    def test_prometheus_scrape_target(self, compose_up):
        resp = _poll(
            "http://127.0.0.1:9090/api/v1/targets",
            lambda r: "veronica" in [
                t["labels"]["job"]
                for t in r.json()["data"]["activeTargets"]
            ],
        )
        assert resp is not None
```

### Fixture

```python
@pytest.fixture(scope="module")
def compose_up():
    subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd="deploy/",
        check=True,
    )
    yield
    subprocess.run(
        ["docker", "compose", "down", "-v"],
        cwd="deploy/",
        check=True,
    )
```

### Polling rationale

- `/api/health` returns 200 before provisioning completes.
- `/api/search` may return empty list during provisioning.
- Prometheus targets may be empty during initial scrape.
- All tests poll with 1s interval, 30s timeout.

---

## 5. pyproject.toml Changes

```toml
[project.optional-dependencies]
metrics = [
    "prometheus-client>=0.20",
]
```

No new dependencies. `httpx` for CI tests only (already in dev or added to dev deps if needed).

---

## 6. __init__.py Export

```python
from veronica.metrics_exporter import start_metrics_server
```

Add `"start_metrics_server"` to `__all__`.

---

## Files Summary

| File | Change |
|------|--------|
| `src/veronica/metrics_exporter.py` | New: start_metrics_server() |
| `src/veronica/__init__.py` | Export start_metrics_server |
| `deploy/docker-compose.yml` | New: Prometheus + Grafana |
| `deploy/prometheus/prometheus.yml` | New: scrape config |
| `deploy/grafana/provisioning/datasources/prometheus.yml` | New: datasource |
| `deploy/grafana/provisioning/dashboards/dashboard.yml` | New: dashboard provider |
| `deploy/grafana/dashboards/veronica.json` | New: 5-panel dashboard |
| `tests/test_metrics_exporter.py` | New: exporter tests |
| `tests/test_dashboard_smoke.py` | New: CI smoke tests |

**Protocol changes:** None.
**os.py pipeline structure:** Unchanged.
