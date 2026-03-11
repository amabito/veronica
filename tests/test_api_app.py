# tests/test_api_app.py
"""Tests for FastAPI application factory."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


class TestAppFactory:
    def test_create_app_returns_fastapi(self) -> None:
        from fastapi import FastAPI

        app = create_app()
        assert isinstance(app, FastAPI)

    def test_app_title(self) -> None:
        app = create_app()
        assert app.title == "VERONICA Control Plane API"

    def test_app_version(self) -> None:
        app = create_app()
        assert app.version == "0.8.0"


class TestLifespan:
    def test_state_initialized_on_startup(self) -> None:
        app = create_app()
        with TestClient(app):
            assert hasattr(app.state, "store")
            assert hasattr(app.state, "distributor")
            assert hasattr(app.state, "ingestor")

    def test_store_is_memory_store(self) -> None:
        from veronica.store import MemoryStore

        app = create_app()
        with TestClient(app):
            assert isinstance(app.state.store, MemoryStore)


class TestErrorHandlers:
    def test_http_exception_returns_json(self, client: TestClient) -> None:
        # /health exists; test a missing route instead
        resp = client.get("/nonexistent-route-xyz")
        assert resp.status_code == 404
        assert "detail" in resp.json()

    def test_cors_headers_present(self, client: TestClient) -> None:
        resp = client.get("/health", headers={"Origin": "http://example.com"})
        assert resp.headers.get("access-control-allow-origin") in (
            "*",
            "http://example.com",
        )


class TestCLIModule:
    def test_main_function_exists(self) -> None:
        from veronica.cli import main

        assert callable(main)

    def test_serve_function_exists(self) -> None:
        from veronica.cli import _serve

        assert callable(_serve)
