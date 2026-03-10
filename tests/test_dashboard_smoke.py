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
        # Check compose CLI exists
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True, timeout=10,
        )
        if result.returncode != 0:
            return False
        # Check Docker daemon is running
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


requires_docker = pytest.mark.skipif(
    not _compose_available(),
    reason="Docker Compose not available",
)
docker_marker = pytest.mark.docker


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
@docker_marker
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
