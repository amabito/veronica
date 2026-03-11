# tests/test_api_health.py
"""Tests for GET /health endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestHealthEndpoint:
    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_returns_json(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.headers["content-type"].startswith("application/json")

    def test_status_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.json()["status"] == "ok"

    def test_version_present(self, client: TestClient) -> None:
        resp = client.get("/health")
        data = resp.json()
        assert "version" in data
        assert data["version"] == "0.8.0"

    def test_kernel_version_present(self, client: TestClient) -> None:
        resp = client.get("/health")
        data = resp.json()
        assert "kernel_version" in data
        assert isinstance(data["kernel_version"], str)

    def test_uptime_seconds_present(self, client: TestClient) -> None:
        resp = client.get("/health")
        data = resp.json()
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], float | int)
        assert data["uptime_seconds"] >= 0

    def test_no_auth_required(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERONICA_API_KEY", "secret")
        app = create_app()
        with TestClient(app) as c:
            resp = c.get("/health")
        assert resp.status_code == 200

    def test_uptime_increases_over_calls(self, client: TestClient) -> None:
        import time

        r1 = client.get("/health").json()["uptime_seconds"]
        time.sleep(0.05)
        r2 = client.get("/health").json()["uptime_seconds"]
        assert r2 >= r1

    def test_all_required_fields_present(self, client: TestClient) -> None:
        required = {
            "status",
            "version",
            "kernel_version",
            "uptime_seconds",
            "subsystems",
        }
        data = client.get("/health").json()
        assert required.issubset(data.keys())

    def test_subsystems_store_ok(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.json()["subsystems"]["store"] == "ok"
