# tests/test_api_auth.py
"""Tests for API key authentication middleware."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app
from veronica.api.auth import _constant_time_compare, _is_exempt


@pytest.fixture()
def client_with_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("VERONICA_API_KEY", "test-secret-key")
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def client_no_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VERONICA_API_KEY", raising=False)
    monkeypatch.delenv("VERONICA_AUTH_DISABLED", raising=False)
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def client_auth_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VERONICA_API_KEY", raising=False)
    monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestExemptPaths:
    def test_health_exempt(self) -> None:
        assert _is_exempt("/health") is True

    def test_docs_exempt(self) -> None:
        assert _is_exempt("/docs") is True

    def test_openapi_exempt(self) -> None:
        assert _is_exempt("/openapi.json") is True

    def test_redoc_exempt(self) -> None:
        assert _is_exempt("/redoc") is True

    def test_other_not_exempt(self) -> None:
        assert _is_exempt("/policies") is False
        assert _is_exempt("/events") is False


class TestTimingSafeCompare:
    def test_matching_keys(self) -> None:
        assert _constant_time_compare("abc", "abc") is True

    def test_different_keys(self) -> None:
        assert _constant_time_compare("abc", "xyz") is False

    def test_empty_vs_nonempty(self) -> None:
        assert _constant_time_compare("", "secret") is False

    def test_both_empty(self) -> None:
        assert _constant_time_compare("", "") is True


class TestAuthMiddlewareWithKey:
    def test_health_passes_without_key(self, client_with_key: TestClient) -> None:
        resp = client_with_key.get("/health")
        assert resp.status_code == 200

    def test_protected_route_without_key_returns_401(
        self, client_with_key: TestClient
    ) -> None:
        resp = client_with_key.get("/policies")
        assert resp.status_code == 401

    def test_protected_route_with_wrong_key_returns_401(
        self, client_with_key: TestClient
    ) -> None:
        resp = client_with_key.get("/policies", headers={"X-Veronica-Key": "wrong"})
        assert resp.status_code == 401

    def test_protected_route_with_correct_key_passes(
        self, client_with_key: TestClient
    ) -> None:
        # /policies not yet implemented, so 404 is expected (not 401)
        resp = client_with_key.get(
            "/policies", headers={"X-Veronica-Key": "test-secret-key"}
        )
        assert resp.status_code != 401

    def test_401_response_has_detail_key(self, client_with_key: TestClient) -> None:
        resp = client_with_key.get("/events")
        assert resp.status_code == 401
        assert "detail" in resp.json()


class TestAuthMiddlewareWithoutKey:
    def test_exempt_path_passes_when_no_env_key(
        self, client_no_key: TestClient
    ) -> None:
        # Exempt paths (/health) pass through even without VERONICA_API_KEY
        resp = client_no_key.get("/health")
        assert resp.status_code == 200

    def test_protected_route_returns_503_when_key_not_configured(
        self, client_no_key: TestClient
    ) -> None:
        # Without VERONICA_API_KEY and without VERONICA_AUTH_DISABLED=1, return 503
        resp = client_no_key.get("/policies")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "API key not configured"


class TestAuthMiddlewareAuthDisabled:
    def test_all_requests_pass_when_auth_disabled(
        self, client_auth_disabled: TestClient
    ) -> None:
        # With VERONICA_AUTH_DISABLED=1, auth is explicitly disabled
        resp = client_auth_disabled.get("/health")
        assert resp.status_code == 200

    def test_protected_route_passes_when_auth_disabled(
        self, client_auth_disabled: TestClient
    ) -> None:
        # /policies not implemented yet, 404 is expected (not 401 or 503)
        resp = client_auth_disabled.get("/policies")
        assert resp.status_code not in {401, 503}


class TestAdversarialAuth:
    def test_empty_key_header_returns_401(self, client_with_key: TestClient) -> None:
        resp = client_with_key.get("/events", headers={"X-Veronica-Key": ""})
        assert resp.status_code == 401

    def test_whitespace_key_returns_401(self, client_with_key: TestClient) -> None:
        resp = client_with_key.get("/events", headers={"X-Veronica-Key": "   "})
        assert resp.status_code == 401

    def test_null_bytes_in_key_returns_401(self, client_with_key: TestClient) -> None:
        resp = client_with_key.get(
            "/events", headers={"X-Veronica-Key": "test\x00secret"}
        )
        assert resp.status_code == 401

    def test_header_name_case_insensitive(self, client_with_key: TestClient) -> None:
        # HTTP headers are case-insensitive by spec
        resp = client_with_key.get(
            "/events", headers={"x-veronica-key": "test-secret-key"}
        )
        assert resp.status_code != 401
