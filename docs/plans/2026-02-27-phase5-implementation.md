# Phase 5: Grafana Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship a `docker compose up` experience that gives users a working VERONICA Grafana dashboard with zero manual config.

**Architecture:** Grafana JSON provisioning with docker-compose (Prometheus + Grafana). VERONICA exports metrics via opt-in `start_metrics_server()`. CI smoke test validates the full stack with polling.

**Tech Stack:** Python 3.10+, prometheus-client, Grafana 10.4, Prometheus 2.51, Docker Compose, httpx (test only)

**Design doc:** `docs/plans/2026-02-27-phase5-dashboard-design.md`

---

## Dependency Graph

```
Task 1 (metrics_exporter.py)
     |
Task 2 (metrics_exporter tests)
     |
Task 3 (deploy/ docker-compose + prometheus + grafana provisioning)
     |
Task 4 (veronica.json dashboard)
     |
Task 5 (CI smoke test)
     |
Task 6 (__init__.py export + version bump)
     |
Task 7 (final verification + tag)
```

All tasks are sequential (each depends on the previous).

---

### Task 1: metrics_exporter.py

**Files:**
- Create: `src/veronica/metrics_exporter.py`

**Step 1: Write metrics_exporter.py**

```python
# src/veronica/metrics_exporter.py
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

**Step 2: Verify import works**

Run: `cd D:/work/Projects/veronica && python -c "from veronica.metrics_exporter import start_metrics_server; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add src/veronica/metrics_exporter.py
git commit -m "feat: add metrics_exporter with start_metrics_server helper"
```

---

### Task 2: metrics_exporter Tests

**Files:**
- Create: `tests/test_metrics_exporter.py`

**Step 1: Write tests**

```python
# tests/test_metrics_exporter.py
"""Tests for metrics_exporter -- Prometheus HTTP server helper."""
from __future__ import annotations

import veronica.metrics_exporter as mod


class TestStartMetricsServer:
    def setup_method(self) -> None:
        """Reset module state between tests."""
        mod._started = False

    def test_returns_true_on_start(self, monkeypatch) -> None:
        """First call starts server and returns True."""
        started_with = {}

        def fake_start(port, addr=""):
            started_with["port"] = port
            started_with["addr"] = addr

        monkeypatch.setattr(
            "prometheus_client.start_http_server", fake_start,
        )
        result = mod.start_metrics_server(port=9999)
        assert result is True
        assert started_with["port"] == 9999

    def test_double_call_returns_true_without_restart(self, monkeypatch) -> None:
        """Second call returns True without starting again."""
        call_count = 0

        def fake_start(port, addr=""):
            nonlocal call_count
            call_count += 1

        monkeypatch.setattr(
            "prometheus_client.start_http_server", fake_start,
        )
        mod.start_metrics_server(port=9998)
        mod.start_metrics_server(port=9998)
        assert call_count == 1

    def test_env_var_port(self, monkeypatch) -> None:
        """VERONICA_METRICS_PORT env var overrides default."""
        started_with = {}

        def fake_start(port, addr=""):
            started_with["port"] = port

        monkeypatch.setattr(
            "prometheus_client.start_http_server", fake_start,
        )
        monkeypatch.setenv("VERONICA_METRICS_PORT", "8888")
        mod.start_metrics_server()
        assert started_with["port"] == 8888

    def test_default_port(self, monkeypatch) -> None:
        """Default port is 9464."""
        started_with = {}

        def fake_start(port, addr=""):
            started_with["port"] = port

        monkeypatch.setattr(
            "prometheus_client.start_http_server", fake_start,
        )
        monkeypatch.delenv("VERONICA_METRICS_PORT", raising=False)
        mod.start_metrics_server()
        assert started_with["port"] == 9464

    def test_import_error_returns_false(self, monkeypatch) -> None:
        """Returns False when prometheus_client is not installed."""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "prometheus_client":
                raise ImportError("no prometheus_client")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        result = mod.start_metrics_server(port=9997)
        assert result is False
```

**Step 2: Run tests**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_metrics_exporter.py -v`
Expected: 5 tests PASS

**Step 3: Commit**

```bash
git add tests/test_metrics_exporter.py
git commit -m "test: add metrics_exporter tests (5 tests)"
```

---

### Task 3: deploy/ Directory (docker-compose + provisioning)

**Files:**
- Create: `deploy/docker-compose.yml`
- Create: `deploy/prometheus/prometheus.yml`
- Create: `deploy/grafana/provisioning/datasources/prometheus.yml`
- Create: `deploy/grafana/provisioning/dashboards/dashboard.yml`

**Step 1: Create docker-compose.yml**

```yaml
# deploy/docker-compose.yml
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

**Step 2: Create prometheus/prometheus.yml**

```yaml
# deploy/prometheus/prometheus.yml
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

**Step 3: Create grafana provisioning files**

datasources/prometheus.yml:

```yaml
# deploy/grafana/provisioning/datasources/prometheus.yml
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

dashboards/dashboard.yml:

```yaml
# deploy/grafana/provisioning/dashboards/dashboard.yml
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

**Step 4: Verify directory structure**

Run: `ls -R deploy/`
Expected:
```
deploy/docker-compose.yml
deploy/prometheus/prometheus.yml
deploy/grafana/provisioning/datasources/prometheus.yml
deploy/grafana/provisioning/dashboards/dashboard.yml
```

**Step 5: Commit**

```bash
git add deploy/
git commit -m "feat: add docker-compose with Prometheus + Grafana provisioning"
```

---

### Task 4: Grafana Dashboard JSON

**Files:**
- Create: `deploy/grafana/dashboards/veronica.json`

**Step 1: Write the dashboard JSON**

The JSON must contain exactly 5 panels with correct PromQL. The full JSON is large (~300 lines), so here is the structure. Implement each panel with these exact queries:

Panel 1 - "Steps / sec":
- Type: timeseries
- Query: `sum by (status) (rate(veronica_steps_total[5m]))`
- Legend: `{{status}}`
- Stack: true

Panel 2 - "Cost Burn Rate":
- Type: timeseries
- Query: `rate(veronica_cost_microusd_total[5m]) / 1000000`
- Unit: `currencyUSD`
- Legend: `USD/sec`

Panel 3 - "Step Latency":
- Type: timeseries
- Queries (3):
  - `histogram_quantile(0.50, sum by (le) (rate(veronica_step_elapsed_ms_bucket[5m])))`  legend: `p50`
  - `histogram_quantile(0.95, sum by (le) (rate(veronica_step_elapsed_ms_bucket[5m])))`  legend: `p95`
  - `histogram_quantile(0.99, sum by (le) (rate(veronica_step_elapsed_ms_bucket[5m])))`  legend: `p99`
- Unit: `ms`

Panel 4 - "Stage Breakdown P95":
- Type: timeseries
- Query: `histogram_quantile(0.95, sum by (le, stage) (rate(veronica_stage_elapsed_ms_bucket[5m])))`
- Legend: `{{stage}}`
- Unit: `ms`

Panel 5 - "Degrade Rate":
- Type: timeseries
- Query: `sum by (degrade_reason) (rate(veronica_degrade_total[5m]))`
- Legend: `{{degrade_reason}}`

Dashboard metadata:
- title: "VERONICA OS"
- uid: "veronica-os"
- refresh: "10s"
- time: last 1 hour
- timezone: browser

Use a gridPos layout: 2 columns top row (panels 1-2), 2 columns middle (panels 3-4), 1 full-width bottom (panel 5). Each panel height 8, width 12 (top/middle) or 24 (bottom).

**Step 2: Validate JSON syntax**

Run: `cd D:/work/Projects/veronica && python -c "import json; json.load(open('deploy/grafana/dashboards/veronica.json')); print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add deploy/grafana/dashboards/veronica.json
git commit -m "feat: add 5-panel VERONICA Grafana dashboard"
```

---

### Task 5: CI Smoke Test

**Files:**
- Create: `tests/test_dashboard_smoke.py`
- Modify: `pyproject.toml:38-43` (add httpx to dev deps)

**Step 1: Add httpx to dev dependencies**

In `pyproject.toml`, add `"httpx>=0.27"` to the `dev` optional dependencies:

```toml
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "fakeredis[lua]>=2.0",
    "prometheus-client>=0.20",
    "httpx>=0.27",
]
```

**Step 2: Write smoke test**

```python
# tests/test_dashboard_smoke.py
"""CI smoke test for Grafana dashboard provisioning.

Requires Docker. Skipped automatically when Docker is not available.
Run explicitly: pytest tests/test_dashboard_smoke.py -v
"""
from __future__ import annotations

import shutil
import subprocess
import time

import pytest

_DEPLOY_DIR = "deploy"


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _compose_available() -> bool:
    if not _docker_available():
        return False
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


requires_docker = pytest.mark.skipif(
    not _compose_available(),
    reason="Docker Compose not available",
)


def _poll(url, predicate, timeout=30, interval=1):
    """Poll URL until predicate(response) is True."""
    import httpx

    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(url, timeout=5)
            if resp.status_code == 200 and predicate(resp):
                return resp
        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            last_error = e
        time.sleep(interval)
    pytest.fail(f"Timed out polling {url}: {last_error}")


@pytest.fixture(scope="module")
def compose_up():
    """Start docker-compose stack, tear down after tests."""
    subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=_DEPLOY_DIR,
        check=True,
        capture_output=True,
    )
    yield
    subprocess.run(
        ["docker", "compose", "down", "-v"],
        cwd=_DEPLOY_DIR,
        check=True,
        capture_output=True,
    )


@requires_docker
class TestDashboardSmoke:
    def test_grafana_health(self, compose_up) -> None:
        """Grafana /api/health returns 200 with database ok."""
        resp = _poll(
            "http://127.0.0.1:3000/api/health",
            lambda r: r.json().get("database") == "ok",
        )
        assert resp.status_code == 200

    def test_dashboard_provisioned(self, compose_up) -> None:
        """VERONICA dashboard is auto-loaded via provisioning."""
        resp = _poll(
            "http://127.0.0.1:3000/api/search?query=veronica",
            lambda r: any(
                "veronica" in d.get("title", "").lower()
                for d in r.json()
            ),
        )
        dashboards = resp.json()
        titles = [d["title"] for d in dashboards]
        assert any("veronica" in t.lower() for t in titles)

    def test_prometheus_scrape_target(self, compose_up) -> None:
        """Prometheus has veronica scrape target registered."""
        resp = _poll(
            "http://127.0.0.1:9090/api/v1/targets",
            lambda r: "veronica" in [
                t["labels"].get("job", "")
                for t in r.json().get("data", {}).get("activeTargets", [])
            ],
        )
        targets = resp.json()["data"]["activeTargets"]
        jobs = [t["labels"]["job"] for t in targets]
        assert "veronica" in jobs
```

**Step 3: Run tests (non-Docker tests only to verify no import errors)**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/test_dashboard_smoke.py -v --co`
Expected: 3 tests collected (may be skipped if no Docker)

**Step 4: Commit**

```bash
git add tests/test_dashboard_smoke.py pyproject.toml
git commit -m "test: add CI smoke test for Grafana dashboard provisioning"
```

---

### Task 6: Export + Version Bump

**Files:**
- Modify: `src/veronica/__init__.py:13,44`
- Modify: `pyproject.toml:7`

**Step 1: Add import to __init__.py**

Add after line 13 (`from veronica.structured_log_subscriber import StructuredLogSubscriber`):

```python
from veronica.metrics_exporter import start_metrics_server
```

**Step 2: Add to __all__**

Add after `"StructuredLogSubscriber"` in `__all__`:

```python
    # Phase 5 components
    "start_metrics_server",
```

**Step 3: Bump version to 0.5.0**

In `src/veronica/__init__.py`, change `__version__ = "0.4.0"` to `__version__ = "0.5.0"`.

In `pyproject.toml`, change `version = "0.4.0"` to `version = "0.5.0"`.

**Step 4: Verify**

Run: `cd D:/work/Projects/veronica && python -c "import veronica; print(veronica.__version__, hasattr(veronica, 'start_metrics_server'))"`
Expected: `0.5.0 True`

**Step 5: Commit**

```bash
git add src/veronica/__init__.py pyproject.toml
git commit -m "chore: export start_metrics_server, bump version to 0.5.0"
```

---

### Task 7: Final Verification + Tag

**Files:** None (verification only)

**Step 1: Run full test suite**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/ -v --tb=short -k "not smoke"`
Expected: All tests PASS (smoke tests skipped unless Docker available)

**Step 2: Run with coverage**

Run: `cd D:/work/Projects/veronica && python -m pytest tests/ --cov=veronica --cov-report=term-missing -k "not smoke"`
Expected: Coverage >= 80%

**Step 3: Verify backward compatibility**

Run:
```bash
cd D:/work/Projects/veronica && python -c "
from veronica import VeronicaOS, start_metrics_server
from veronica.types import StepIntent
vos = VeronicaOS()
intent = StepIntent(step_id='s1', request_id='r1', chain_id='c1', kind='llm', model='gpt-4', tool_name=None, timeout_ms=30000, metadata={})
handle = vos.before_step(intent)
print('Default OS works:', handle.policy.ceiling_usd > 0)
print('start_metrics_server importable:', callable(start_metrics_server))
"
```
Expected: `Default OS works: True` and `start_metrics_server importable: True`

**Step 4: Verify deploy/ structure**

Run: `ls -R deploy/`
Expected:
```
deploy/docker-compose.yml
deploy/prometheus/prometheus.yml
deploy/grafana/provisioning/datasources/prometheus.yml
deploy/grafana/provisioning/dashboards/dashboard.yml
deploy/grafana/dashboards/veronica.json
```

**Step 5: Tag**

```bash
git tag v0.5.0
```

Note: Do NOT push. J.A.R.V.I.S. will handle push after review.

---

## Summary

| Task | Description | Files | Tests |
|------|-------------|-------|-------|
| 1 | metrics_exporter.py | `metrics_exporter.py` (new) | -- |
| 2 | metrics_exporter tests | `test_metrics_exporter.py` (new) | 5 |
| 3 | deploy/ directory | `deploy/` (4 new files) | -- |
| 4 | Dashboard JSON | `veronica.json` (new) | -- |
| 5 | CI smoke test | `test_dashboard_smoke.py` (new), `pyproject.toml` (mod) | 3 |
| 6 | Export + version | `__init__.py`, `pyproject.toml` (mod) | -- |
| 7 | Final verification + tag | -- | full suite |

**Total new tests:** 8 (5 exporter + 3 smoke)
**Total new source files:** 1 (`metrics_exporter.py`)
**Total new deploy files:** 5 (compose, prometheus, grafana x3)
**Protocol changes:** None
