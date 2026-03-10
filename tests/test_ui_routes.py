# tests/test_ui_routes.py
"""Tests for the Veronica web UI static file serving."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = create_app()
    return TestClient(app, follow_redirects=True)


@pytest.fixture(scope="module")
def client_no_redirect() -> TestClient:
    app = create_app()
    return TestClient(app, follow_redirects=False)


class TestRootRedirect:
    def test_root_redirects(self, client_no_redirect: TestClient) -> None:
        """GET / must redirect (3xx) to the dashboard UI."""
        resp = client_no_redirect.get("/", headers={"X-Veronica-Key": "test"})
        assert resp.status_code in (301, 302, 307, 308)

    def test_root_eventually_serves_html(self, client: TestClient) -> None:
        """GET / following redirects must return HTML."""
        resp = client.get("/", headers={"X-Veronica-Key": "test"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")


class TestDashboardPage:
    def test_ui_index_returns_html(self, client: TestClient) -> None:
        """GET /ui/ must return 200 with HTML content."""
        resp = client.get("/ui/", headers={"X-Veronica-Key": "test"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_ui_index_contains_veronica(self, client: TestClient) -> None:
        """Dashboard HTML must mention VERONICA."""
        resp = client.get("/ui/", headers={"X-Veronica-Key": "test"})
        assert "VERONICA" in resp.text

    def test_ui_index_references_dashboard_js(self, client: TestClient) -> None:
        """Dashboard HTML must reference dashboard.js."""
        resp = client.get("/ui/", headers={"X-Veronica-Key": "test"})
        assert "dashboard.js" in resp.text

    def test_ui_index_references_stylesheet(self, client: TestClient) -> None:
        """Dashboard HTML must reference the stylesheet."""
        resp = client.get("/ui/", headers={"X-Veronica-Key": "test"})
        assert "style.css" in resp.text


class TestStaticFiles:
    def test_css_file_served(self, client: TestClient) -> None:
        """Static CSS must be served with correct content-type."""
        resp = client.get("/ui/static/css/style.css")
        assert resp.status_code == 200
        assert "css" in resp.headers.get("content-type", "")

    def test_dashboard_js_served(self, client: TestClient) -> None:
        """Static dashboard.js must be served."""
        resp = client.get("/ui/static/js/dashboard.js")
        assert resp.status_code == 200

    def test_policies_js_served(self, client: TestClient) -> None:
        """Static policies.js must be served."""
        resp = client.get("/ui/static/js/policies.js")
        assert resp.status_code == 200

    def test_missing_static_file_returns_404(self, client: TestClient) -> None:
        """Non-existent static file must return 404."""
        resp = client.get("/ui/static/js/nonexistent.js")
        assert resp.status_code == 404


class TestPoliciesPage:
    def test_policies_page_returns_html(self, client: TestClient) -> None:
        """GET /ui/policies must return 200 with HTML."""
        resp = client.get("/ui/policies", headers={"X-Veronica-Key": "test"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_policies_page_references_policies_js(self, client: TestClient) -> None:
        """Policies HTML must reference policies.js."""
        resp = client.get("/ui/policies", headers={"X-Veronica-Key": "test"})
        assert "policies.js" in resp.text
