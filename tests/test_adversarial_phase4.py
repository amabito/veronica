# tests/test_adversarial_phase4.py
"""Adversarial tests for VERONICA Phase 4 (Deploy) -- /export and /health endpoints.

Attacker mindset: each test tries to bypass auth, exhaust resources, corrupt data,
leak information, or race concurrent state. All tests MUST PASS -- the application
must handle every attack gracefully.

Categories:
  1. TestAdversarialPhase4AuthBypass   -- auth bypass on /export
  2. TestAdversarialPhase4DoS          -- denial-of-service vectors
  3. TestAdversarialPhase4DataIntegrity -- export completeness and hash stability
  4. TestAdversarialPhase4InputValidation -- event_limit edge cases and injection
  5. TestAdversarialPhase4InfoLeakage  -- no stack traces / internal paths in errors
  6. TestAdversarialPhase4StateConcurrency -- concurrent /export must not interfere
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app
from veronica.types import PolicyConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_policy(chain_id: str, ceiling_usd: float = 1.0) -> PolicyConfig:
    """Minimal valid PolicyConfig for test registration."""
    return PolicyConfig(
        chain_id=chain_id,
        ceiling_usd=ceiling_usd,
        on_exceed="halt",
        issued_at=time.time(),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Auth-disabled client with lifespan started."""
    monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
    monkeypatch.delenv("VERONICA_API_KEY", raising=False)
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def client_with_key(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Client with API key configured (auth enforced)."""
    monkeypatch.setenv("VERONICA_API_KEY", "phase4-secret-key")
    monkeypatch.delenv("VERONICA_AUTH_DISABLED", raising=False)
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Category 1: Auth Bypass on /export
# ---------------------------------------------------------------------------


class TestAdversarialPhase4AuthBypass:
    """Auth bypass attempts on /export must always fail -- never grant access."""

    def test_adversarial_auth_export_no_key_returns_401_or_503(
        self, client_with_key: TestClient
    ) -> None:
        """Request to /export without X-Veronica-Key must be denied (401 or 503)."""
        resp = client_with_key.get("/export")
        assert resp.status_code in {401, 503}, resp.text

    def test_adversarial_auth_export_wrong_key_denied(
        self, client_with_key: TestClient
    ) -> None:
        """Wrong API key on /export must always return 401, not 200."""
        resp = client_with_key.get(
            "/export", headers={"X-Veronica-Key": "totally-wrong-key"}
        )
        assert resp.status_code == 401, resp.text

    def test_adversarial_auth_export_empty_key_denied(
        self, client_with_key: TestClient
    ) -> None:
        """Empty string X-Veronica-Key must not bypass auth -- 401."""
        resp = client_with_key.get("/export", headers={"X-Veronica-Key": ""})
        assert resp.status_code == 401, resp.text

    def test_adversarial_auth_export_key_in_wrong_header_denied(
        self, client_with_key: TestClient
    ) -> None:
        """Correct key in Authorization header (not X-Veronica-Key) must not bypass auth."""
        resp = client_with_key.get(
            "/export", headers={"Authorization": "Bearer phase4-secret-key"}
        )
        assert resp.status_code == 401, resp.text

    def test_adversarial_auth_export_key_in_query_param_denied(
        self, client_with_key: TestClient
    ) -> None:
        """Key passed as query param must not bypass header-based auth -- 401."""
        resp = client_with_key.get("/export?api_key=phase4-secret-key")
        assert resp.status_code == 401, resp.text

    def test_adversarial_auth_disabled_zero_string_does_not_disable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VERONICA_AUTH_DISABLED='0' must NOT disable auth (only '1' is valid)."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "0")
        monkeypatch.setenv("VERONICA_API_KEY", "real-key")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/export")
        # Without the key header, must be denied
        assert resp.status_code in {401, 503}, resp.text

    def test_adversarial_auth_disabled_false_string_does_not_disable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VERONICA_AUTH_DISABLED='false' must NOT disable auth (only '1' disables)."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "false")
        monkeypatch.setenv("VERONICA_API_KEY", "real-key")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/export")
        assert resp.status_code in {401, 503}, resp.text

    def test_adversarial_auth_disabled_no_string_does_not_disable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VERONICA_AUTH_DISABLED='no' must NOT disable auth (only '1' disables)."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "no")
        monkeypatch.setenv("VERONICA_API_KEY", "real-key")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/export")
        assert resp.status_code in {401, 503}, resp.text

    def test_adversarial_auth_health_exempt_but_export_not(
        self, client_with_key: TestClient
    ) -> None:
        """/health is exempt; /export is NOT. Same client, different results."""
        health_resp = client_with_key.get("/health")
        export_resp = client_with_key.get("/export")
        assert health_resp.status_code == 200, health_resp.text
        assert export_resp.status_code in {401, 503}, export_resp.text


# ---------------------------------------------------------------------------
# Category 2: DoS Vectors on /export and /health
# ---------------------------------------------------------------------------


class TestAdversarialPhase4DoS:
    """Resource-exhaustion vectors must be rejected or capped gracefully."""

    def test_adversarial_dos_export_event_limit_minimum_boundary(
        self, client: TestClient
    ) -> None:
        """event_limit=1 (minimum valid) must be accepted -- boundary is valid."""
        resp = client.get("/export?event_limit=1")
        assert resp.status_code == 200, resp.text

    def test_adversarial_dos_export_event_limit_maximum_boundary(
        self, client: TestClient
    ) -> None:
        """event_limit=100000 (maximum valid) must be accepted -- boundary is valid."""
        resp = client.get("/export?event_limit=100000")
        assert resp.status_code == 200, resp.text

    def test_adversarial_dos_export_event_limit_one_above_max_rejected(
        self, client: TestClient
    ) -> None:
        """event_limit=100001 (one above MAX) must be rejected as 422."""
        resp = client.get("/export?event_limit=100001")
        assert resp.status_code == 422, resp.text

    def test_adversarial_dos_export_event_limit_zero_rejected(
        self, client: TestClient
    ) -> None:
        """event_limit=0 (below minimum 1) must be rejected as 422."""
        resp = client.get("/export?event_limit=0")
        assert resp.status_code == 422, resp.text

    def test_adversarial_dos_export_many_policies_completes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Export with 20 registered policies must complete without 500."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            for i in range(20):
                app.state.registry.register(_make_policy(f"dos-chain-{i}"))
            resp = c.get("/export")
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["policies"]) == 20

    def test_adversarial_dos_health_rapid_fire_stable(self, client: TestClient) -> None:
        """50 rapid /health calls must all return 200 without server errors."""
        statuses = [client.get("/health").status_code for _ in range(50)]
        assert all(s == 200 for s in statuses), f"Unexpected statuses: {set(statuses)}"

    def test_adversarial_dos_export_rapid_fire_stable(self, client: TestClient) -> None:
        """20 rapid /export calls must all return 200 without server errors."""
        statuses = [client.get("/export").status_code for _ in range(20)]
        assert all(s == 200 for s in statuses), f"Unexpected statuses: {set(statuses)}"


# ---------------------------------------------------------------------------
# Category 3: Data Integrity on /export
# ---------------------------------------------------------------------------


class TestAdversarialPhase4DataIntegrity:
    """Export must include ALL registered policies with stable, consistent hashes."""

    def test_adversarial_integrity_five_policies_all_exported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All 5 registered policies must appear in export (no silent truncation)."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        chain_ids = [f"integrity-chain-{i}" for i in range(5)]
        with TestClient(app, raise_server_exceptions=False) as c:
            for cid in chain_ids:
                app.state.registry.register(_make_policy(cid))
            body = c.get("/export").json()
        exported_ids = {p["chain_id"] for p in body["policies"]}
        assert exported_ids == set(chain_ids), (
            f"Missing chains: {set(chain_ids) - exported_ids}"
        )

    def test_adversarial_integrity_policy_hash_consistent_across_exports(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same policy must produce identical policy_hash on repeated exports."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            app.state.registry.register(
                _make_policy("hash-stable-chain", ceiling_usd=3.14)
            )
            body1 = c.get("/export").json()
            body2 = c.get("/export").json()
        hash1 = next(
            p["policy_hash"]
            for p in body1["policies"]
            if p["chain_id"] == "hash-stable-chain"
        )
        hash2 = next(
            p["policy_hash"]
            for p in body2["policies"]
            if p["chain_id"] == "hash-stable-chain"
        )
        assert hash1 == hash2, "policy_hash changed between two identical exports"

    def test_adversarial_integrity_policy_hash_differs_for_different_policies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two policies with different ceiling_usd must produce different hashes."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            app.state.registry.register(_make_policy("hash-a", ceiling_usd=1.0))
            app.state.registry.register(_make_policy("hash-b", ceiling_usd=9.9))
            body = c.get("/export").json()
        policies = {p["chain_id"]: p["policy_hash"] for p in body["policies"]}
        assert policies["hash-a"] != policies["hash-b"], (
            "Different policies must have different hashes"
        )

    def test_adversarial_integrity_event_collection_failure_adds_warnings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If event collection fails, export must include 'warnings' field, not crash."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()

        from veronica.ingest.event_ingestor import CPStepOutcomeStore

        class BrokenStore(CPStepOutcomeStore):
            def snapshot(self) -> list[Any]:
                raise RuntimeError("Store unavailable for test")

        with TestClient(app, raise_server_exceptions=False) as c:
            # Replace the ingestor's internal store with a broken one
            app.state.ingestor._store = BrokenStore()
            resp = c.get("/export")

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "warnings" in body, "Warnings field missing when event collection fails"
        assert any("Event collection" in w for w in body["warnings"])
        assert body["events"] == []

    def test_adversarial_integrity_export_events_empty_when_no_activity(
        self, client: TestClient
    ) -> None:
        """Fresh app with no ingested events must export events=[] (not None or missing)."""
        body = client.get("/export").json()
        assert body["events"] == []

    def test_adversarial_integrity_policy_count_stable_without_new_registrations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Policy count must not change between two exports if no registrations occur."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            app.state.registry.register(_make_policy("stable-count-chain"))
            count1 = len(c.get("/export").json()["policies"])
            count2 = len(c.get("/export").json()["policies"])
        assert count1 == count2


# ---------------------------------------------------------------------------
# Category 4: Input Validation on /export
# ---------------------------------------------------------------------------


class TestAdversarialPhase4InputValidation:
    """Malformed event_limit values must be rejected; injection must not crash."""

    def test_adversarial_input_event_limit_negative_rejected(
        self, client: TestClient
    ) -> None:
        """Negative event_limit must be rejected with 422."""
        resp = client.get("/export?event_limit=-1")
        assert resp.status_code == 422, resp.text

    def test_adversarial_input_event_limit_non_integer_rejected(
        self, client: TestClient
    ) -> None:
        """Non-integer event_limit (float string) must be rejected with 422."""
        resp = client.get("/export?event_limit=3.14")
        assert resp.status_code == 422, resp.text

    def test_adversarial_input_event_limit_string_rejected(
        self, client: TestClient
    ) -> None:
        """String event_limit must be rejected with 422."""
        resp = client.get("/export?event_limit=many")
        assert resp.status_code == 422, resp.text

    def test_adversarial_input_event_limit_extremely_large_rejected(
        self, client: TestClient
    ) -> None:
        """Extremely large event_limit (2^31) must be rejected as 422 (above max 100000)."""
        resp = client.get("/export?event_limit=2147483647")
        assert resp.status_code == 422, resp.text

    def test_adversarial_input_event_limit_sql_injection_rejected(
        self, client: TestClient
    ) -> None:
        """SQL injection in event_limit must be rejected with 422 (type validation)."""
        resp = client.get("/export?event_limit=1; DROP TABLE events; --")
        assert resp.status_code == 422, resp.text

    def test_adversarial_input_event_limit_json_object_rejected(
        self, client: TestClient
    ) -> None:
        """JSON object as event_limit must be rejected with 422."""
        resp = client.get("/export?event_limit={}")
        assert resp.status_code == 422, resp.text

    def test_adversarial_input_unknown_query_params_ignored(
        self, client: TestClient
    ) -> None:
        """Unknown query parameters must be silently ignored -- 200."""
        resp = client.get("/export?unknown_param=evil&other=garbage")
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Category 5: Information Leakage
# ---------------------------------------------------------------------------


class TestAdversarialPhase4InfoLeakage:
    """Error responses must not contain stack traces, internal paths, or class names."""

    def test_adversarial_leak_export_error_no_traceback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If /export raises internally, 500 must not contain Python traceback."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        monkeypatch.delenv("VERONICA_DEBUG", raising=False)
        app = create_app()

        def _explode(*_a: Any, **_kw: Any) -> None:
            raise RuntimeError("secret-internal-detail /home/veronica/.config")

        with TestClient(app, raise_server_exceptions=False) as c:
            # Patch list_policies on the live registry (after lifespan startup)
            app.state.registry.list_policies = _explode  # type: ignore[method-assign]
            resp = c.get("/export")

        assert resp.status_code == 500
        body_text = resp.text
        assert "Traceback" not in body_text, "Stack trace leaked in 500 response"
        assert "RuntimeError" not in body_text, "Exception class name leaked"
        assert "/home/veronica" not in body_text, "Internal path leaked"

    def test_adversarial_leak_export_error_no_internals_in_production_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In production mode (no VERONICA_DEBUG), 500 detail must be generic string."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        monkeypatch.delenv("VERONICA_DEBUG", raising=False)
        app = create_app()

        def _explode(*_a: Any, **_kw: Any) -> None:
            raise RuntimeError("sensitive-internal-info")

        with TestClient(app, raise_server_exceptions=False) as c:
            app.state.registry.list_policies = _explode  # type: ignore[method-assign]
            resp = c.get("/export")

        assert resp.status_code == 500
        assert "sensitive-internal-info" not in resp.text, (
            "Internal exception message leaked in production mode"
        )

    def test_adversarial_leak_health_no_sensitive_subsystem_internals(
        self, client: TestClient
    ) -> None:
        """/health response must not expose internal module names or file paths."""
        body = client.get("/health").json()
        body_text = str(body)
        assert "veronica.ingest" not in body_text, "Internal module path in /health"
        assert "veronica.api" not in body_text, "Internal module path in /health"
        assert "D:\\" not in body_text, "Windows path exposed in /health"
        assert "/home/" not in body_text, "Unix home path exposed in /health"

    def test_adversarial_leak_health_subsystems_only_expected_keys(
        self, client: TestClient
    ) -> None:
        """/health subsystems must expose only 'store' status string, no deep internals."""
        body = client.get("/health").json()
        subsystems = body.get("subsystems", {})
        # Each subsystem value must be a plain string status, not an object
        for key, value in subsystems.items():
            assert isinstance(value, str), (
                f"Subsystem '{key}' exposes non-string value: {value!r}"
            )
            assert value in {"ok", "unavailable", "degraded"}, (
                f"Subsystem '{key}' has unexpected status: {value!r}"
            )

    def test_adversarial_leak_401_does_not_reveal_correct_key(
        self, client_with_key: TestClient
    ) -> None:
        """401 error body must not hint at the correct API key value."""
        resp = client_with_key.get("/export", headers={"X-Veronica-Key": "wrong-guess"})
        assert resp.status_code == 401
        assert "phase4-secret-key" not in resp.text, "Correct key leaked in 401 body"

    def test_adversarial_leak_422_event_limit_no_traceback(
        self, client: TestClient
    ) -> None:
        """422 for invalid event_limit must not contain Python traceback."""
        resp = client.get("/export?event_limit=-999")
        assert resp.status_code == 422
        assert "Traceback" not in resp.text, "Stack trace in 422 validation error"
        assert "veronica.api" not in resp.text, "Internal module path in 422 response"


# ---------------------------------------------------------------------------
# Category 6: State Corruption -- Concurrent /export
# ---------------------------------------------------------------------------


class TestAdversarialPhase4StateConcurrency:
    """Concurrent /export calls must not interfere or crash."""

    def test_adversarial_concurrent_export_all_succeed(
        self, client: TestClient
    ) -> None:
        """15 concurrent /export calls must all return 200, no 500s."""
        results: list[int] = []
        errors: list[Exception] = []

        def call() -> None:
            try:
                resp = client.get("/export")
                results.append(resp.status_code)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=call) for _ in range(15)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Exceptions during concurrent export: {errors}"
        assert all(s == 200 for s in results), f"Unexpected statuses: {set(results)}"

    def test_adversarial_concurrent_export_policy_counts_consistent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Concurrent exports of the same registry must return identical policy counts."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            for i in range(7):
                app.state.registry.register(_make_policy(f"concurrent-chain-{i}"))

            counts: list[int] = []
            errors: list[Exception] = []

            def fetch_count() -> None:
                try:
                    body = c.get("/export").json()
                    counts.append(len(body["policies"]))
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=fetch_count) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert not errors, f"Exceptions during concurrent export: {errors}"
        assert all(n == 7 for n in counts), (
            f"Inconsistent policy counts across concurrent exports: {counts}"
        )

    def test_adversarial_concurrent_export_during_registration_no_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/export during concurrent policy registration must not crash (500 forbidden)."""
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        app = create_app()
        statuses: list[int] = []
        errors: list[Exception] = []

        with TestClient(app, raise_server_exceptions=False) as c:

            def register_policies() -> None:
                for i in range(10):
                    try:
                        app.state.registry.register(_make_policy(f"race-chain-{i}"))
                        time.sleep(0.005)
                    except Exception as exc:
                        errors.append(exc)

            def export_concurrently() -> None:
                for _ in range(5):
                    try:
                        resp = c.get("/export")
                        statuses.append(resp.status_code)
                        time.sleep(0.008)
                    except Exception as exc:
                        errors.append(exc)

            registrar = threading.Thread(target=register_policies)
            exporter = threading.Thread(target=export_concurrently)
            registrar.start()
            exporter.start()
            registrar.join()
            exporter.join()

        # No 500s during concurrent registration + export
        assert not errors, f"Exceptions: {errors}"
        assert all(s == 200 for s in statuses), (
            f"Unexpected status codes: {set(statuses)}"
        )

    def test_adversarial_concurrent_health_and_export_no_interference(
        self, client: TestClient
    ) -> None:
        """Concurrent /health and /export must not interfere with each other."""
        health_statuses: list[int] = []
        export_statuses: list[int] = []
        errors: list[Exception] = []

        def poll_health() -> None:
            for _ in range(10):
                try:
                    health_statuses.append(client.get("/health").status_code)
                except Exception as exc:
                    errors.append(exc)

        def poll_export() -> None:
            for _ in range(5):
                try:
                    export_statuses.append(client.get("/export").status_code)
                except Exception as exc:
                    errors.append(exc)

        t1 = threading.Thread(target=poll_health)
        t2 = threading.Thread(target=poll_export)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Exceptions: {errors}"
        assert all(s == 200 for s in health_statuses)
        assert all(s == 200 for s in export_statuses)
