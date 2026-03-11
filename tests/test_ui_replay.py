from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("VERONICA_AUTH_DISABLED", "1")

from veronica.api.app import create_app  # noqa: E402


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app, follow_redirects=True) as c:
        yield c


def test_ui_replay_returns_html(client) -> None:
    """GET /ui/replay must return 200 with HTML content."""
    resp = client.get("/ui/replay")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_ui_replay_contains_form(client) -> None:
    """replay.html must include the replay form elements."""
    resp = client.get("/ui/replay")
    body = resp.text
    assert "inp-chain" in body
    assert "inp-from" in body
    assert "inp-to" in body
    assert "inp-policy" in body
    assert "btn-run" in body


def test_ui_replay_contains_nav_links(client) -> None:
    """replay.html nav must include Rollouts and Replay links."""
    resp = client.get("/ui/replay")
    body = resp.text
    assert "/ui/rollouts" in body
    assert "/ui/replay" in body


def test_ui_replay_loads_js(client) -> None:
    """replay.html must reference replay.js."""
    resp = client.get("/ui/replay")
    assert "replay.js" in resp.text


def test_ui_replay_js_served(client) -> None:
    """replay.js static file must be served with 200."""
    resp = client.get("/ui/static/js/replay.js")
    assert resp.status_code == 200
