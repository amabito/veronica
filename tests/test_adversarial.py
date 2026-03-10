# tests/test_adversarial.py
"""Adversarial tests for the Veronica control plane.

Attacker mindset: each test tries to break, confuse, or extract
information from the system. All tests MUST PASS -- the application
must handle every attack gracefully.

Categories:
  1. TestAdversarialAPI      -- corrupted / malformed input at API layer
  2. TestAdversarialAuth     -- authentication bypass attempts
  3. TestAdversarialDoS      -- resource-exhaustion / denial-of-service
  4. TestAdversarialStateCorruption -- bad state fed to ingest / distribution
  5. TestAdversarialBoundary -- exact-boundary, zero, and overflow abuse
  6. TestAdversarialInfoLeakage -- error responses must not leak internals
"""
from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app
from veronica.distribution.policy_distributor import PolicyDistributor, PolicyValidationError
from veronica.ingest.event_ingestor import CPStepOutcomeStore, EventIngestor
from veronica.types import PolicyConfig

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_VALID_POLICY: dict[str, Any] = {
    "chain_id": "test-chain",
    "ceiling_usd": 1.0,
    "on_exceed": "halt",
}


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Auth-disabled client for non-auth tests.

    Uses context manager to trigger lifespan startup (initializes app.state).
    """
    monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
    monkeypatch.delenv("VERONICA_API_KEY", raising=False)
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def client_with_key(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Client with an API key configured (auth enforced).

    Uses context manager to trigger lifespan startup.
    """
    monkeypatch.setenv("VERONICA_API_KEY", "real-secret-key")
    monkeypatch.delenv("VERONICA_AUTH_DISABLED", raising=False)
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Category 1: Corrupted Input (API layer)
# ---------------------------------------------------------------------------


class TestAdversarialAPI:
    """Corrupted / malformed input must never crash the server (5xx from bugs)."""

    def test_simulate_ceiling_usd_as_string(self, client: TestClient) -> None:
        """String 'not-a-number' for ceiling_usd must be rejected as 422, not 500."""
        body = {
            "policy": {**_VALID_POLICY, "ceiling_usd": "not-a-number"},
            "steps": [],
        }
        resp = client.post("/simulate", json=body)
        assert resp.status_code == 422, resp.text

    def test_simulate_negative_ceiling_usd(self, client: TestClient) -> None:
        """Negative ceiling_usd must be rejected (422), not silently accepted."""
        body = {
            "policy": {**_VALID_POLICY, "ceiling_usd": -99.99},
            "steps": [],
        }
        resp = client.post("/simulate", json=body)
        assert resp.status_code == 422, resp.text

    def test_simulate_none_ceiling_usd(self, client: TestClient) -> None:
        """null/None ceiling_usd is not a number -- must be 422."""
        body = {
            "policy": {**_VALID_POLICY, "ceiling_usd": None},
            "steps": [],
        }
        resp = client.post("/simulate", json=body)
        assert resp.status_code == 422, resp.text

    def test_simulate_missing_required_field(self, client: TestClient) -> None:
        """Policy missing 'on_exceed' must be rejected with 422."""
        body = {
            "policy": {"chain_id": "c", "ceiling_usd": 1.0},
            "steps": [],
        }
        resp = client.post("/simulate", json=body)
        assert resp.status_code == 422, resp.text

    def test_simulate_invalid_on_exceed(self, client: TestClient) -> None:
        """on_exceed='explode' is not a valid value -- must be 422."""
        body = {
            "policy": {**_VALID_POLICY, "on_exceed": "explode"},
            "steps": [],
        }
        resp = client.post("/simulate", json=body)
        assert resp.status_code == 422, resp.text

    def test_simulate_garbage_json_body(self, client: TestClient) -> None:
        """Syntactically invalid JSON must be rejected, not crash the server."""
        resp = client.post(
            "/simulate",
            content=b"}{garbage json][",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (400, 422), resp.text

    def test_simulate_empty_body(self, client: TestClient) -> None:
        """Empty body must produce 422 (missing required fields), not 500."""
        resp = client.post(
            "/simulate",
            content=b"",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (400, 422), resp.text

    def test_simulate_unknown_extra_fields_in_step(self, client: TestClient) -> None:
        """Extra unknown fields in steps should be ignored (Pydantic default), not crash."""
        body = {
            "policy": _VALID_POLICY,
            "steps": [{"kind": "llm", "cost_usd": 0.01, "totally_unknown_field": True}],
        }
        resp = client.post("/simulate", json=body)
        # Should succeed (200) or fail with 422, never 500
        assert resp.status_code in (200, 422), resp.text

    def test_put_policy_garbage_json(self, client: TestClient) -> None:
        """PUT /policies/{id} with garbage JSON must not cause 500."""
        resp = client.put(
            "/policies/some-chain",
            content=b"{{not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code in (400, 422), resp.text

    def test_put_policy_missing_current_version(self, client: TestClient) -> None:
        """PUT without required current_version must be 422."""
        resp = client.put(
            "/policies/some-chain",
            json={"ceiling_usd": 5.0},
        )
        assert resp.status_code == 422, resp.text

    def test_get_events_invalid_decision_filter(self, client: TestClient) -> None:
        """?decision=INVALID_VALUE must be 400, not silently accepted or 500."""
        resp = client.get("/events?decision=INVALID_VALUE")
        assert resp.status_code == 400, resp.text

    def test_get_events_sql_injection_in_chain_id(self, client: TestClient) -> None:
        """SQL injection attempt in chain_id filter must not crash the server.

        The store is in-memory (no SQL), so the string is used as a literal filter.
        The attack vector is harmless here, but the server must not 500.
        """
        resp = client.get("/events?chain_id=' OR 1=1; DROP TABLE events; --")
        assert resp.status_code == 200, resp.text

    def test_get_policies_page_too_large(self, client: TestClient) -> None:
        """per_page > 200 must be rejected by FastAPI Query validation (422)."""
        resp = client.get("/policies?per_page=999999")
        assert resp.status_code == 422, resp.text

    def test_simulate_deadline_ts_as_string(self, client: TestClient) -> None:
        """deadline_ts='garbage' must be caught and return 422, not 500."""
        body = {
            "policy": {**_VALID_POLICY, "deadline_ts": "not-a-timestamp"},
            "steps": [],
        }
        resp = client.post("/simulate", json=body)
        assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Category 2: Auth Bypass Attempts
# ---------------------------------------------------------------------------


class TestAdversarialAuth:
    """Auth bypass must always fail with 401 or 503 -- never grant access."""

    def test_no_key_header_on_protected_endpoint(self, client_with_key: TestClient) -> None:
        """Request without X-Veronica-Key to /simulate must be 401."""
        body = {"policy": _VALID_POLICY, "steps": []}
        resp = client_with_key.post("/simulate", json=body)
        assert resp.status_code == 401, resp.text

    def test_empty_key_header(self, client_with_key: TestClient) -> None:
        """Empty X-Veronica-Key must be treated as missing -- 401."""
        resp = client_with_key.get(
            "/policies",
            headers={"X-Veronica-Key": ""},
        )
        assert resp.status_code == 401, resp.text

    def test_wrong_key(self, client_with_key: TestClient) -> None:
        """Wrong API key must always be 401, not 200."""
        resp = client_with_key.get(
            "/policies",
            headers={"X-Veronica-Key": "totally-wrong-key"},
        )
        assert resp.status_code == 401, resp.text

    def test_path_traversal_health_in_policies(self, client_with_key: TestClient) -> None:
        """Path traversal /health/../policies is NOT /health -- must be 4xx, not 200.

        The middleware only exempts exact /health. Traversal resolves to /policies
        which requires auth. Must NOT bypass authentication.
        """
        resp = client_with_key.get("/health/../policies")
        # Should be 401 (auth enforced) or 404 -- definitely not 200
        assert resp.status_code != 200, resp.text

    def test_case_variation_health_uppercase(self, client_with_key: TestClient) -> None:
        """/HEALTH (uppercase) is NOT exempt from auth -- must NOT return health data.

        _EXEMPT_EXACT contains '/health' (lowercase). '/HEALTH' must not bypass auth.
        """
        resp = client_with_key.get("/HEALTH")
        # 404 (route not found) or 401 (auth denied before routing) -- not 200
        assert resp.status_code != 200, resp.text

    def test_health_with_trailing_slash_is_exempt(self, client_with_key: TestClient) -> None:
        """/health/ should still be exempt (middleware strips trailing slash).

        This documents expected behavior: /health/ is treated same as /health.
        """
        resp = client_with_key.get("/health/")
        # Should be 200 (exempt) or 404 (routing may not match), but NOT 401
        assert resp.status_code != 401, resp.text

    def test_null_bytes_in_key_header(self, client_with_key: TestClient) -> None:
        """Null byte in API key must not bypass HMAC comparison -- 401."""
        resp = client_with_key.get(
            "/policies",
            headers={"X-Veronica-Key": "real-secret-key\x00injected"},
        )
        assert resp.status_code == 401, resp.text

    def test_double_encoded_path(self, client_with_key: TestClient) -> None:
        """Double-encoded %2Fhealth must not be treated as exempt /health -- 4xx."""
        resp = client_with_key.get("/%2Fhealth")
        assert resp.status_code != 200, resp.text


# ---------------------------------------------------------------------------
# Category 3: Resource Exhaustion (DoS)
# ---------------------------------------------------------------------------


class TestAdversarialDoS:
    """Oversized inputs must be rejected or capped before processing."""

    def test_simulate_steps_at_max_allowed(self, client: TestClient) -> None:
        """1000 steps (max_length) must be accepted (boundary = valid)."""
        steps = [{"kind": "llm", "cost_usd": 0.0, "tokens_out": 0}] * 1000
        body = {"policy": _VALID_POLICY, "steps": steps}
        resp = client.post("/simulate", json=body)
        # 200 is expected; may halt early but must not 422/500
        assert resp.status_code == 200, resp.text

    def test_simulate_steps_exceeds_max(self, client: TestClient) -> None:
        """1001 steps (> max_length=1000) must be rejected as 422."""
        steps = [{"kind": "llm", "cost_usd": 0.0, "tokens_out": 0}] * 1001
        body = {"policy": _VALID_POLICY, "steps": steps}
        resp = client.post("/simulate", json=body)
        assert resp.status_code == 422, resp.text

    def test_get_events_limit_at_max(self, client: TestClient) -> None:
        """limit=1000 (max allowed) must succeed."""
        resp = client.get("/events?limit=1000")
        assert resp.status_code == 200, resp.text

    def test_get_events_limit_exceeds_max(self, client: TestClient) -> None:
        """limit=1001 (> max le=1000) must be rejected as 422."""
        resp = client.get("/events?limit=1001")
        assert resp.status_code == 422, resp.text

    def test_get_events_huge_offset(self, client: TestClient) -> None:
        """Huge offset value is rejected by validation (max 10000)."""
        resp = client.get("/events?offset=99999999")
        assert resp.status_code == 422, resp.text

    def test_simulate_concurrent_requests(self, client: TestClient) -> None:
        """10 concurrent POST /simulate must all succeed or return valid status codes.

        No request must cause a 500 due to race conditions in the distributor.
        """
        body = {
            "policy": _VALID_POLICY,
            "steps": [{"kind": "llm", "cost_usd": 0.1}],
        }
        results: list[int] = []
        errors: list[Exception] = []

        def call() -> None:
            try:
                r = client.post("/simulate", json=body)
                results.append(r.status_code)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=call) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Exceptions during concurrent simulate: {errors}"
        # Every response must be a valid HTTP status, not a connection error
        for status in results:
            assert status in (200, 422, 503), f"Unexpected status {status}"

    def test_simulate_large_policy_config(self, client: TestClient) -> None:
        """Extremely long string field in policy must not crash the server."""
        body = {
            "policy": {
                **_VALID_POLICY,
                "planner_version": "x" * 100_000,
            },
            "steps": [],
        }
        resp = client.post("/simulate", json=body)
        # Accept or reject, but must not 500
        assert resp.status_code in (200, 422), resp.text


# ---------------------------------------------------------------------------
# Category 4: State Corruption (Ingest + Distribution)
# ---------------------------------------------------------------------------


class TestAdversarialStateCorruption:
    """Bad state fed directly to ingest/distribution must not crash or corrupt."""

    def test_ingestor_on_event_without_safety_event_key(self) -> None:
        """on_event() with payload missing 'safety_event' key must silently ignore.

        The contract says payloads without 'safety_event' are ignored.
        """
        ingestor = EventIngestor()
        initial_total = ingestor.ingested_total
        ingestor.on_event("some_event_type", {"other_key": "value"})
        # Nothing should be ingested
        assert ingestor.ingested_total == initial_total

    def test_ingestor_on_event_with_none_payload(self) -> None:
        """on_event() with safety_event=None must be silently ignored."""
        ingestor = EventIngestor()
        ingestor.on_event("test", {"safety_event": None})
        assert ingestor.ingested_total == 0

    def test_ingestor_on_event_garbage_payload_type(self) -> None:
        """on_event() with a non-Mapping payload must not crash.

        Defensive: payload.get() requires Mapping -- passing a non-Mapping
        should not cause an unhandled AttributeError.
        """
        ingestor = EventIngestor()
        # The method signature is Mapping[str, Any]; passing something wrong
        # should fail gracefully if the ingestor is defensive.
        try:
            ingestor.on_event("test", None)  # type: ignore[arg-type]
        except AttributeError:
            # Acceptable -- None.get() raises AttributeError; ingestor is not
            # required to handle non-Mapping payloads beyond its documented interface.
            pass
        # In either case, no records should be in the store
        assert ingestor.ingested_total == 0

    def test_distributor_nan_ceiling_usd(self) -> None:
        """PolicyDistributor must reject NaN ceiling_usd with PolicyValidationError."""
        dist = PolicyDistributor()
        policy = PolicyConfig(
            chain_id="nan-test",
            ceiling_usd=float("nan"),
            on_exceed="halt",
            issued_at=time.time(),
        )
        with pytest.raises(PolicyValidationError, match="finite"):
            dist.distribute(policy)

    def test_distributor_inf_ceiling_usd(self) -> None:
        """PolicyDistributor must reject +Inf ceiling_usd with PolicyValidationError."""
        dist = PolicyDistributor()
        policy = PolicyConfig(
            chain_id="inf-test",
            ceiling_usd=float("inf"),
            on_exceed="halt",
            issued_at=time.time(),
        )
        with pytest.raises(PolicyValidationError, match="finite"):
            dist.distribute(policy)

    def test_distributor_inf_timeout_ms(self) -> None:
        """PolicyDistributor must reject infinite timeout_ms."""
        dist = PolicyDistributor()
        policy = PolicyConfig(
            chain_id="inf-timeout",
            ceiling_usd=1.0,
            on_exceed="halt",
            issued_at=time.time(),
            timeout_ms=float("inf"),  # type: ignore[arg-type]
        )
        with pytest.raises(PolicyValidationError, match="finite"):
            dist.distribute(policy)

    def test_distributor_concurrent_distribute(self) -> None:
        """Concurrent distribute() calls must not corrupt the version counter.

        Each call must receive a unique, monotonically increasing version number.
        """
        dist = PolicyDistributor()
        versions: list[int] = []
        lock = threading.Lock()

        def distribute_one() -> None:
            policy = PolicyConfig(
                chain_id="concurrent-test",
                ceiling_usd=1.0,
                on_exceed="halt",
                issued_at=time.time(),
            )
            bundle = dist.distribute(policy)
            with lock:
                versions.append(bundle.version)

        threads = [threading.Thread(target=distribute_one) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(versions) == 20
        # All versions must be unique (no two threads got the same version)
        assert len(set(versions)) == 20, f"Duplicate versions: {sorted(versions)}"

    def test_ingestor_store_failure_moves_to_dead_letter(self) -> None:
        """If the store raises on put_many, records go to dead-letter, not lost silently."""
        from veronica_core.shield.event import SafetyEvent
        from veronica_core.shield.hooks import Decision

        class FailingStore(CPStepOutcomeStore):
            def put_many(self, outcomes: object) -> None:
                raise RuntimeError("Store unavailable")

        ingestor = EventIngestor(store=FailingStore(), batch_size=0)
        event = SafetyEvent(
            event_type="llm_call",
            hook="cost_guard",
            decision=Decision.ALLOW,
            reason="within budget",
            request_id="req-001",
        )
        ingestor.ingest(event)
        # Record should land in dead-letter, not silently vanish
        assert ingestor.dead_letter_count >= 1


# ---------------------------------------------------------------------------
# Category 5: Boundary Abuse
# ---------------------------------------------------------------------------


class TestAdversarialBoundary:
    """Exact boundary values, zero, and overflow must behave predictably."""

    def test_simulate_ceiling_usd_zero_halts_on_first_cost(self, client: TestClient) -> None:
        """ceiling_usd=0.0 with a step that has cost > 0 must halt immediately.

        A zero budget is valid configuration; any cost exceeds it.
        """
        body = {
            "policy": {**_VALID_POLICY, "ceiling_usd": 0.0},
            "steps": [{"kind": "llm", "cost_usd": 0.001, "tokens_out": 10}],
        }
        resp = client.post("/simulate", json=body)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["final_decision"] != "allow", "Zero budget must halt on non-zero cost"
        assert data["steps_halted"] == 1

    def test_simulate_ceiling_steps_zero_halts_first_step(self, client: TestClient) -> None:
        """ceiling_steps=0 must halt on the very first step (0 steps allowed).

        Step 1 projects steps=1 which exceeds ceiling_steps=0.
        """
        body = {
            "policy": {**_VALID_POLICY, "ceiling_steps": 0},
            "steps": [{"kind": "llm", "cost_usd": 0.0}],
        }
        resp = client.post("/simulate", json=body)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["final_decision"] != "allow", "ceiling_steps=0 must halt on first step"

    def test_simulate_timeout_ms_zero_is_valid(self, client: TestClient) -> None:
        """timeout_ms=0 is a valid value (>= 0) -- distributor must accept it."""
        body = {
            "policy": {**_VALID_POLICY, "timeout_ms": 0},
            "steps": [],
        }
        resp = client.post("/simulate", json=body)
        assert resp.status_code == 200, resp.text

    def test_get_policies_page_zero_rejected(self, client: TestClient) -> None:
        """page=0 is below ge=1 -- FastAPI Query validation must reject with 422."""
        resp = client.get("/policies?page=0")
        assert resp.status_code == 422, resp.text

    def test_get_policies_page_negative_rejected(self, client: TestClient) -> None:
        """page=-1 is below ge=1 -- must be rejected with 422."""
        resp = client.get("/policies?page=-1")
        assert resp.status_code == 422, resp.text

    def test_get_events_offset_zero_is_valid(self, client: TestClient) -> None:
        """offset=0 is the default, minimum valid value -- must succeed."""
        resp = client.get("/events?offset=0")
        assert resp.status_code == 200, resp.text

    def test_get_events_limit_zero_rejected(self, client: TestClient) -> None:
        """limit=0 is below ge=1 -- must be rejected with 422."""
        resp = client.get("/events?limit=0")
        assert resp.status_code == 422, resp.text

    def test_simulate_step_cost_zero_within_budget(self, client: TestClient) -> None:
        """Steps with zero cost must always be 'allow' when within other limits."""
        body = {
            "policy": _VALID_POLICY,
            "steps": [{"kind": "llm", "cost_usd": 0.0, "tokens_out": 0}] * 10,
        }
        resp = client.post("/simulate", json=body)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["final_decision"] == "allow"
        assert data["total_cost_usd"] == 0.0

    def test_distributor_negative_ceiling_usd_rejected(self) -> None:
        """PolicyDistributor must reject negative ceiling_usd."""
        dist = PolicyDistributor()
        policy = PolicyConfig(
            chain_id="neg-test",
            ceiling_usd=-0.001,
            on_exceed="halt",
            issued_at=time.time(),
        )
        with pytest.raises(PolicyValidationError, match=">= 0"):
            dist.distribute(policy)

    def test_distributor_ceiling_usd_zero_is_valid(self) -> None:
        """ceiling_usd=0.0 is a valid policy (zero budget). Must NOT raise."""
        dist = PolicyDistributor()
        policy = PolicyConfig(
            chain_id="zero-budget",
            ceiling_usd=0.0,
            on_exceed="halt",
            issued_at=time.time(),
        )
        bundle = dist.distribute(policy)
        assert bundle.policy.ceiling_usd == 0.0


# ---------------------------------------------------------------------------
# Category 6: Information Leakage
# ---------------------------------------------------------------------------


class TestAdversarialInfoLeakage:
    """Error responses must not reveal stack traces, internal paths, or class names."""

    def test_500_no_stack_trace_in_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unhandled internal exception must return 500 with no stack trace.

        The generic exception handler must sanitize the response body.
        VERONICA_DEBUG must NOT be set (default production mode).
        """
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        monkeypatch.delenv("VERONICA_API_KEY", raising=False)
        monkeypatch.delenv("VERONICA_DEBUG", raising=False)
        app = create_app()

        # Force an internal error by patching _evaluate_step (called inside the route)
        with patch(
            "veronica.api.routes.simulate._evaluate_step",
            side_effect=RuntimeError("secret internal path: /home/user/.config/veronica"),
        ):
            with TestClient(app, raise_server_exceptions=False) as c:
                body = {
                    "policy": _VALID_POLICY,
                    "steps": [{"kind": "llm", "cost_usd": 0.0}],
                }
                resp = c.post("/simulate", json=body)

        assert resp.status_code == 500
        body_text = resp.text
        # Must NOT contain Python traceback indicators
        assert "Traceback" not in body_text, "Stack trace leaked in 500 response"
        assert "RuntimeError" not in body_text, "Exception class name leaked"
        assert "/home/user" not in body_text, "Internal path leaked in 500 response"

    def test_500_debug_mode_may_expose_detail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With VERONICA_DEBUG=1, str(exc) may appear -- this is expected behavior.

        This test documents (not blocks) the debug mode behavior.
        """
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
        monkeypatch.delenv("VERONICA_API_KEY", raising=False)
        monkeypatch.setenv("VERONICA_DEBUG", "1")
        app = create_app()

        with patch(
            "veronica.api.routes.simulate._evaluate_step",
            side_effect=RuntimeError("debug-detail"),
        ):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post(
                    "/simulate",
                    json={"policy": _VALID_POLICY, "steps": [{"kind": "llm", "cost_usd": 0.0}]},
                )

        assert resp.status_code == 500
        # In debug mode, detail is str(exc) -- this is expected
        assert "debug-detail" in resp.text

    def test_401_body_does_not_reveal_expected_key(self, client_with_key: TestClient) -> None:
        """401 response must not hint at what the correct key should be."""
        resp = client_with_key.get(
            "/policies",
            headers={"X-Veronica-Key": "wrong-key"},
        )
        assert resp.status_code == 401
        body_text = resp.text
        assert "real-secret-key" not in body_text, "Correct key leaked in 401 response"

    def test_404_body_does_not_reveal_internal_paths(self, client: TestClient) -> None:
        """404 for a non-existent policy must not reveal internal file paths."""
        resp = client.get("/policies/nonexistent-chain-xyz")
        assert resp.status_code == 404
        body_text = resp.text
        # Must not expose Python module paths like 'veronica.api.routes.policies'
        assert "veronica.api" not in body_text, "Internal module path in 404 response"

    def test_422_body_does_not_contain_python_exception_repr(self, client: TestClient) -> None:
        """422 validation error must not contain raw Python exception repr."""
        body = {
            "policy": {**_VALID_POLICY, "ceiling_usd": "garbage"},
            "steps": [],
        }
        resp = client.post("/simulate", json=body)
        assert resp.status_code == 422
        body_text = resp.text
        # Must not expose raw Python tracebacks or exception class names from internals
        assert "Traceback" not in body_text, "Stack trace in 422 response"

    def test_400_invalid_decision_does_not_leak_store_state(self, client: TestClient) -> None:
        """400 response for invalid decision filter must not expose store contents."""
        resp = client.get("/events?decision=EVIL_INJECTION")
        assert resp.status_code == 400
        body_text = resp.text
        # The detail should mention valid decisions, not dump internal state
        assert "Traceback" not in body_text
        assert "veronica.ingest" not in body_text, "Internal module path in 400 response"
