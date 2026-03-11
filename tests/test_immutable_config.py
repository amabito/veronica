# tests/test_immutable_config.py
"""Tests for VERONICA_IMMUTABLE_CONFIG mode -- PUT /policies/{id} guard."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app
from veronica.types import PolicyConfig


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _make_policy(chain_id: str = "chain-a", ceiling_usd: float = 1.0) -> PolicyConfig:
    return PolicyConfig(
        chain_id=chain_id,
        ceiling_usd=ceiling_usd,
        on_exceed="halt",
        issued_at=time.time(),
        priority=50,
    )


def _register_policy(client: TestClient, policy: PolicyConfig) -> dict:
    """Register a policy via internal registry (requires app state)."""
    # Use the ASGI app's state to register directly
    app = client.app
    registry = app.state.registry
    bundle = registry.register(policy)
    return bundle


class TestImmutableConfigMode:
    def test_update_allowed_without_immutable_flag(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VERONICA_IMMUTABLE_CONFIG", raising=False)
        policy = _make_policy()
        _register_policy(client, policy)

        resp = client.put(
            "/policies/chain-a",
            json={"current_version": 1, "ceiling_usd": 2.0},
        )
        assert resp.status_code in (200, 409), (
            f"Expected 200 or 409, got {resp.status_code}"
        )
        if resp.status_code == 409:
            detail = resp.json().get("detail", "").lower()
            assert "immutable" not in detail, (
                "409 in non-immutable mode must not mention 'immutable'"
            )

    def test_update_blocked_when_immutable_1(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_IMMUTABLE_CONFIG", "1")
        policy = _make_policy()
        _register_policy(client, policy)

        resp = client.put(
            "/policies/chain-a",
            json={"current_version": 1, "ceiling_usd": 2.0},
        )
        assert resp.status_code == 403
        assert "immutable config mode" in resp.json()["detail"].lower()

    def test_update_blocked_when_immutable_true(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_IMMUTABLE_CONFIG", "true")
        policy = _make_policy()
        _register_policy(client, policy)

        resp = client.put(
            "/policies/chain-a",
            json={"current_version": 1, "ceiling_usd": 2.0},
        )
        assert resp.status_code == 403

    def test_update_blocked_when_immutable_yes(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_IMMUTABLE_CONFIG", "yes")
        policy = _make_policy()
        _register_policy(client, policy)

        resp = client.put(
            "/policies/chain-a",
            json={"current_version": 1, "ceiling_usd": 2.0},
        )
        assert resp.status_code == 403

    def test_update_allowed_when_immutable_false(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_IMMUTABLE_CONFIG", "false")
        policy = _make_policy()
        _register_policy(client, policy)

        resp = client.put(
            "/policies/chain-a",
            json={"current_version": 1, "ceiling_usd": 2.0},
        )
        assert resp.status_code in (200, 409), (
            f"Expected 200 or 409, got {resp.status_code}"
        )

    def test_update_allowed_when_immutable_0(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_IMMUTABLE_CONFIG", "0")
        policy = _make_policy()
        _register_policy(client, policy)

        resp = client.put(
            "/policies/chain-a",
            json={"current_version": 1, "ceiling_usd": 2.0},
        )
        assert resp.status_code in (200, 409), (
            f"Expected 200 or 409, got {resp.status_code}"
        )

    def test_health_reports_immutable_config_true(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_IMMUTABLE_CONFIG", "1")
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["immutable_config"] is True

    def test_health_reports_immutable_config_false(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VERONICA_IMMUTABLE_CONFIG", raising=False)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["immutable_config"] is False

    def test_get_policy_still_works_in_immutable_mode(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_IMMUTABLE_CONFIG", "1")
        policy = _make_policy()
        _register_policy(client, policy)

        resp = client.get("/policies/chain-a")
        assert resp.status_code == 200

    def test_list_policies_still_works_in_immutable_mode(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_IMMUTABLE_CONFIG", "1")
        resp = client.get("/policies")
        assert resp.status_code == 200
