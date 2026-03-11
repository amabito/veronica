from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app, follow_redirects=True) as c:
        yield c


def test_ui_rollouts_returns_html(client) -> None:
    """GET /ui/rollouts must return 200 with HTML content."""
    resp = client.get("/ui/rollouts")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_ui_rollouts_contains_table(client) -> None:
    """rollouts.html must include the rollout list table."""
    resp = client.get("/ui/rollouts")
    body = resp.text
    assert "rollouts-tbody" in body
    assert "filter-state" in body


def test_ui_rollouts_contains_create_form(client) -> None:
    """rollouts.html must include create rollout form."""
    resp = client.get("/ui/rollouts")
    body = resp.text
    assert "nr-created-by" in body
    assert "nr-ceiling-usd" in body
    assert "btn-create-submit" in body


def test_ui_rollouts_contains_nav_links(client) -> None:
    """rollouts.html nav must include Rollouts (active) and Replay links."""
    resp = client.get("/ui/rollouts")
    body = resp.text
    assert "/ui/rollouts" in body
    assert "/ui/replay" in body


def test_ui_rollouts_loads_js(client) -> None:
    """rollouts.html must reference rollouts.js."""
    resp = client.get("/ui/rollouts")
    assert "rollouts.js" in resp.text


def test_ui_rollouts_js_served(client) -> None:
    """rollouts.js static file must be served with 200."""
    resp = client.get("/ui/static/js/rollouts.js")
    assert resp.status_code == 200


def test_ui_rollouts_contains_state_badges(client) -> None:
    """rollouts.html must define badge classes for all 6 states."""
    resp = client.get("/ui/rollouts")
    body = resp.text
    for state in ("draft", "simulated", "approved", "promoted", "active", "revoked"):
        assert f"badge-{state}" in body
