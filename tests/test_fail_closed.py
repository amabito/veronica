# tests/test_fail_closed.py
"""Fail-closed startup tests for VERONICA control-plane API.

Covers:
1. Auth fail-closed: no API key + no VERONICA_AUTH_DISABLED -> 503 on protected routes
2. Health degraded: /health returns status="degraded" when store is unavailable
3. Clear error messages for missing config
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client_no_auth(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Client with no API key and no VERONICA_AUTH_DISABLED."""
    monkeypatch.delenv("VERONICA_API_KEY", raising=False)
    monkeypatch.delenv("VERONICA_AUTH_DISABLED", raising=False)
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


def _make_client_with_key(monkeypatch: pytest.MonkeyPatch, key: str = "secure-key") -> TestClient:
    """Client with a configured API key."""
    monkeypatch.setenv("VERONICA_API_KEY", key)
    app = create_app()
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# 1. Auth fail-closed: 503 when no key and auth not explicitly disabled
# ---------------------------------------------------------------------------


class TestAuthFailClosed:
    """No VERONICA_API_KEY + no VERONICA_AUTH_DISABLED -> 503 (fail-closed)."""

    def test_policies_returns_503_when_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _make_client_no_auth(monkeypatch)
        resp = client.get("/policies")
        assert resp.status_code == 503

    def test_events_returns_503_when_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _make_client_no_auth(monkeypatch)
        resp = client.get("/events")
        assert resp.status_code == 503

    def test_simulate_returns_503_when_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _make_client_no_auth(monkeypatch)
        resp = client.post("/simulate")
        assert resp.status_code == 503

    def test_503_has_detail_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client_no_auth(monkeypatch)
        resp = client.get("/policies")
        assert resp.status_code == 503
        body = resp.json()
        assert "detail" in body

    def test_503_detail_mentions_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Error message must guide the operator -- 'API key not configured'."""
        client = _make_client_no_auth(monkeypatch)
        resp = client.get("/policies")
        assert resp.status_code == 503
        detail = resp.json().get("detail", "")
        assert "key" in detail.lower() or "configured" in detail.lower(), (
            f"503 detail should mention 'key' or 'configured', got: {detail!r}"
        )

    def test_health_still_accessible_when_no_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Health check is always exempt from auth -- operators need it for liveness."""
        client = _make_client_no_auth(monkeypatch)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_auth_disabled_bypasses_503(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """VERONICA_AUTH_DISABLED=1 must opt out of fail-closed (explicit operator choice)."""
        monkeypatch.delenv("VERONICA_API_KEY", raising=False)
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_wrong_value_for_auth_disabled_does_not_bypass(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VERONICA_AUTH_DISABLED=yes/true/0 must NOT bypass fail-closed -- only '1' counts."""
        for bad_value in ("yes", "true", "True", "0", "false"):
            monkeypatch.delenv("VERONICA_API_KEY", raising=False)
            monkeypatch.setenv("VERONICA_AUTH_DISABLED", bad_value)
            app = create_app()
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/policies")
            assert resp.status_code == 503, (
                f"Expected 503 with VERONICA_AUTH_DISABLED={bad_value!r}, got {resp.status_code}"
            )


# ---------------------------------------------------------------------------
# 2. Health degraded when Store unavailable
# ---------------------------------------------------------------------------


class TestHealthDegradedWhenStoreUnavailable:
    """GET /health returns status='degraded' when the store raises or is missing."""

    def _make_degraded_client(self) -> TestClient:
        """App with a broken store that raises on build_history."""

        class BrokenStore:
            def build_history(self, chain_id: str, limit: int = 50) -> None:
                raise RuntimeError("Store backend unavailable")

        app = create_app()

        # Override the store after lifespan startup
        @app.on_event("startup")
        async def _inject_broken_store() -> None:
            app.state.store = BrokenStore()

        return TestClient(app, raise_server_exceptions=False)

    def _make_missing_store_client(self) -> TestClient:
        """App with state.store explicitly set to None."""
        app = create_app()

        @app.on_event("startup")
        async def _remove_store() -> None:
            app.state.store = None

        return TestClient(app, raise_server_exceptions=False)

    def test_healthy_store_returns_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Normal app with MemoryStore must return status='ok'."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_broken_store_returns_degraded(self) -> None:
        client = self._make_degraded_client()
        resp = client.get("/health")
        assert resp.status_code == 200, "Health must always return 200 (never 5xx itself)"
        assert resp.json()["status"] == "degraded"

    def test_missing_store_returns_degraded(self) -> None:
        client = self._make_missing_store_client()
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "degraded"

    def test_degraded_response_includes_subsystems_store_unavailable(self) -> None:
        client = self._make_degraded_client()
        resp = client.get("/health")
        body = resp.json()
        assert "subsystems" in body
        assert body["subsystems"]["store"] == "unavailable"

    def test_healthy_response_includes_subsystems_store_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/health")
        body = resp.json()
        assert "subsystems" in body
        assert body["subsystems"]["store"] == "ok"

    def test_degraded_still_returns_version(self) -> None:
        """Even in degraded mode the version fields must be present -- used by monitoring."""
        client = self._make_degraded_client()
        body = client.get("/health").json()
        assert "version" in body
        assert "kernel_version" in body
        assert "uptime_seconds" in body


# ---------------------------------------------------------------------------
# 3. Invalid PolicyConfig at registration -- validation errors, not silent accept
# ---------------------------------------------------------------------------


class TestInvalidPolicyConfigRejected:
    """Invalid PolicyConfig must be rejected with a clear error -- not silently accepted."""

    def _make_client(self, monkeypatch: pytest.MonkeyPatch) -> TestClient:
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        return TestClient(app, raise_server_exceptions=False)

    def test_negative_ceiling_usd_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """ceiling_usd < 0 must raise PolicyValidationError, not be registered."""
        from veronica.distribution.policy_distributor import PolicyValidationError
        from veronica.types import PolicyConfig
        import time

        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as _:
            registry = app.state.registry
            bad_policy = PolicyConfig(
                chain_id="bad-chain",
                ceiling_usd=-1.0,
                on_exceed="halt",
                issued_at=time.time(),
            )
            with pytest.raises(PolicyValidationError):
                registry.register(bad_policy)

    def test_invalid_on_exceed_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """on_exceed value outside allowed set must be rejected."""
        from veronica.distribution.policy_distributor import PolicyValidationError
        from veronica.types import PolicyConfig
        import time

        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as _:
            registry = app.state.registry
            bad_policy = PolicyConfig(
                chain_id="bad-exceed",
                ceiling_usd=1.0,
                on_exceed="explode",  # type: ignore[arg-type]
                issued_at=time.time(),
            )
            with pytest.raises(PolicyValidationError):
                registry.register(bad_policy)

    def test_nan_ceiling_usd_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """NaN ceiling_usd must be rejected as non-finite."""
        from veronica.distribution.policy_distributor import PolicyValidationError
        from veronica.types import PolicyConfig
        import math
        import time

        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as _:
            registry = app.state.registry
            bad_policy = PolicyConfig(
                chain_id="nan-chain",
                ceiling_usd=math.nan,
                on_exceed="halt",
                issued_at=time.time(),
            )
            with pytest.raises(PolicyValidationError):
                registry.register(bad_policy)

    def test_valid_policy_is_accepted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sanity check: a valid PolicyConfig must be registered without error."""
        from veronica.types import PolicyConfig
        import time

        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as _:
            registry = app.state.registry
            good_policy = PolicyConfig(
                chain_id="good-chain",
                ceiling_usd=1.0,
                on_exceed="halt",
                issued_at=time.time(),
            )
            bundle = registry.register(good_policy)
            assert bundle.policy.chain_id == "good-chain"

    def test_validation_error_message_is_informative(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PolicyValidationError message must name the offending field."""
        from veronica.distribution.policy_distributor import PolicyValidationError
        from veronica.types import PolicyConfig
        import time

        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as _:
            registry = app.state.registry
            bad_policy = PolicyConfig(
                chain_id="msg-chain",
                ceiling_usd=-99.0,
                on_exceed="halt",
                issued_at=time.time(),
            )
            with pytest.raises(PolicyValidationError, match=r"ceiling_usd"):
                registry.register(bad_policy)


# ---------------------------------------------------------------------------
# 4. Clear error messages for missing config
# ---------------------------------------------------------------------------


class TestMissingConfigErrorMessages:
    """Error messages must be actionable for operators."""

    def test_503_detail_is_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client_no_auth(monkeypatch)
        resp = client.get("/policies")
        assert resp.status_code == 503
        assert isinstance(resp.json().get("detail"), str)

    def test_503_detail_is_not_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _make_client_no_auth(monkeypatch)
        resp = client.get("/policies")
        assert resp.status_code == 503
        assert resp.json()["detail"].strip() != ""

    def test_401_detail_mentions_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """401 when a key is configured but wrong -- message must guide operator."""
        client = _make_client_with_key(monkeypatch)
        resp = client.get("/policies", headers={"X-Veronica-Key": "wrong"})
        assert resp.status_code == 401
        detail = resp.json().get("detail", "")
        assert "key" in detail.lower() or "invalid" in detail.lower(), (
            f"401 detail should mention 'key' or 'invalid', got: {detail!r}"
        )

    def test_health_error_message_does_not_expose_internals(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Health endpoint must not leak stack traces or internal paths."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()

        class BrokenStore:
            def build_history(self, chain_id: str, limit: int = 50) -> None:
                raise RuntimeError("SECRET_DB_PASSWORD in connection string")

        @app.on_event("startup")
        async def _inject() -> None:
            app.state.store = BrokenStore()

        client = TestClient(app, raise_server_exceptions=False)
        body = client.get("/health").json()
        # subsystem status is "unavailable", not the raw exception message
        assert body["subsystems"]["store"] == "unavailable"
        assert "SECRET_DB_PASSWORD" not in str(body)
