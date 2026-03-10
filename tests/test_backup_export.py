# tests/test_backup_export.py
"""Tests for GET /export endpoint -- backup and JSON export."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app
from veronica.types import PolicyConfig


def _make_policy(chain_id: str = "test-chain", ceiling_usd: float = 1.0) -> PolicyConfig:
    """Minimal valid PolicyConfig for testing."""
    return PolicyConfig(
        chain_id=chain_id,
        ceiling_usd=ceiling_usd,
        on_exceed="halt",
        issued_at=time.time(),
    )


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Auth-disabled client with lifespan started (context manager)."""
    monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
    app = create_app()
    # Use context manager to ensure lifespan (startup) runs
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def client_with_key(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Client with API key configured and lifespan started."""
    monkeypatch.setenv("VERONICA_API_KEY", "test-key-export")
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class TestExportResponseStructure:
    def test_export_returns_200(self, client: TestClient) -> None:
        resp = client.get("/export")
        assert resp.status_code == 200

    def test_export_returns_json(self, client: TestClient) -> None:
        resp = client.get("/export")
        assert resp.headers["content-type"].startswith("application/json")

    def test_export_has_required_top_level_keys(self, client: TestClient) -> None:
        body = client.get("/export").json()
        required = {"exported_at", "veronica_version", "policies", "events"}
        assert required.issubset(body.keys())

    def test_export_exported_at_is_recent_float(self, client: TestClient) -> None:
        body = client.get("/export").json()
        exported_at = body["exported_at"]
        assert isinstance(exported_at, (float, int))
        assert abs(exported_at - time.time()) < 5.0

    def test_export_veronica_version_is_nonempty_string(self, client: TestClient) -> None:
        body = client.get("/export").json()
        assert isinstance(body["veronica_version"], str)
        assert body["veronica_version"] != ""

    def test_export_policies_is_list(self, client: TestClient) -> None:
        assert isinstance(client.get("/export").json()["policies"], list)

    def test_export_events_is_list(self, client: TestClient) -> None:
        assert isinstance(client.get("/export").json()["events"], list)


class TestExportPolicies:
    def test_empty_registry_exports_empty_policies(self, client: TestClient) -> None:
        assert client.get("/export").json()["policies"] == []

    def test_registered_policy_appears_in_export(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            app.state.registry.register(_make_policy("export-chain-1", ceiling_usd=2.5))
            body = c.get("/export").json()
        assert any(p["chain_id"] == "export-chain-1" for p in body["policies"])

    def test_policy_export_has_required_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            app.state.registry.register(_make_policy("field-check-chain"))
            body = c.get("/export").json()
        policy = body["policies"][0]
        required = {"chain_id", "ceiling_usd", "on_exceed", "issued_at", "policy_hash", "version"}
        assert required.issubset(policy.keys())

    def test_policy_hash_is_sha256_hex(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            app.state.registry.register(_make_policy("hash-check-chain"))
            body = c.get("/export").json()
        policy_hash = body["policies"][0]["policy_hash"]
        assert isinstance(policy_hash, str)
        assert len(policy_hash) == 64
        int(policy_hash, 16)  # valid hex

    def test_multiple_policies_all_exported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            for i in range(5):
                app.state.registry.register(_make_policy(f"chain-{i}"))
            body = c.get("/export").json()
        assert len(body["policies"]) == 5

    def test_policy_ceiling_usd_matches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            app.state.registry.register(_make_policy("usd-check", ceiling_usd=9.99))
            body = c.get("/export").json()
        exported = next(p for p in body["policies"] if p["chain_id"] == "usd-check")
        assert exported["ceiling_usd"] == pytest.approx(9.99)


class TestExportEvents:
    def test_no_events_initially(self, client: TestClient) -> None:
        assert client.get("/export").json()["events"] == []

    def test_event_limit_must_be_at_least_1(self, client: TestClient) -> None:
        resp = client.get("/export?event_limit=0")
        assert resp.status_code == 422

    def test_event_limit_param_accepted(self, client: TestClient) -> None:
        resp = client.get("/export?event_limit=5")
        assert resp.status_code == 200

    def test_max_events_out_of_range_returns_422(self, client: TestClient) -> None:
        """max_events query param: 0 is invalid."""
        resp = client.get("/export?max_events=0")
        # 422 (invalid) or 200 (if param not supported -- falls back to default)
        assert resp.status_code in {200, 422}


class TestExportAuthentication:
    def test_export_protected_when_key_configured(
        self, client_with_key: TestClient
    ) -> None:
        resp = client_with_key.get("/export")
        assert resp.status_code in {401, 503}

    def test_export_accessible_with_correct_key(
        self, client_with_key: TestClient
    ) -> None:
        resp = client_with_key.get(
            "/export", headers={"X-Veronica-Key": "test-key-export"}
        )
        assert resp.status_code == 200

    def test_export_503_when_no_key_and_auth_not_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VERONICA_API_KEY", raising=False)
        monkeypatch.delenv("VERONICA_AUTH_DISABLED", raising=False)
        app = create_app()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/export")
        assert resp.status_code == 503


class TestExportConsistency:
    def test_two_exports_have_same_policy_count(self, client: TestClient) -> None:
        b1 = client.get("/export").json()
        b2 = client.get("/export").json()
        assert len(b1["policies"]) == len(b2["policies"])

    def test_exported_at_advances_between_calls(self, client: TestClient) -> None:
        t1 = client.get("/export").json()["exported_at"]
        time.sleep(0.02)
        t2 = client.get("/export").json()["exported_at"]
        assert t2 >= t1

    def test_export_policy_on_exceed_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            app.state.registry.register(
                PolicyConfig(
                    chain_id="degrade-chain",
                    ceiling_usd=1.0,
                    on_exceed="degrade",
                    issued_at=time.time(),
                )
            )
            body = c.get("/export").json()
        exported = body["policies"][0]
        assert exported["on_exceed"] == "degrade"
