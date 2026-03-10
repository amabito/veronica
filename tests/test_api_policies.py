# tests/test_api_policies.py
"""Tests for GET /policies and GET /policies/{id} endpoints."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app
from veronica.types import PolicyConfig


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _make_policy(chain_id: str = "chain-a", ceiling_usd: float = 1.0) -> PolicyConfig:
    return PolicyConfig(
        chain_id=chain_id,
        ceiling_usd=ceiling_usd,
        on_exceed="halt",
        issued_at=time.time(),
        priority=50,
    )


# ---------------------------------------------------------------------------
# GET /policies
# ---------------------------------------------------------------------------


class TestListPolicies:
    def test_empty_registry_returns_empty_list(self, client):
        resp = client.get("/policies")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["page"] == 1
        assert body["per_page"] == 20

    def test_list_after_register(self, client):
        registry = client.app.state.registry
        registry.register(_make_policy("chain-1"))
        registry.register(_make_policy("chain-2"))

        resp = client.get("/policies")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        chain_ids = {item["chain_id"] for item in body["items"]}
        assert chain_ids == {"chain-1", "chain-2"}

    def test_pagination_page_1(self, client):
        registry = client.app.state.registry
        for i in range(5):
            registry.register(_make_policy(f"chain-{i}"))

        resp = client.get("/policies?page=1&per_page=3")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert len(body["items"]) == 3
        assert body["page"] == 1
        assert body["per_page"] == 3

    def test_pagination_page_2(self, client):
        registry = client.app.state.registry
        for i in range(5):
            registry.register(_make_policy(f"chain-pg-{i}"))

        resp = client.get("/policies?page=2&per_page=3")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["items"]) == 2  # 5 total, page 2 of 3

    def test_response_contains_policy_hash(self, client):
        registry = client.app.state.registry
        registry.register(_make_policy("chain-hash"))

        resp = client.get("/policies")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert "policy_hash" in item
        assert len(item["policy_hash"]) == 64  # SHA-256 hex

    def test_response_contains_version(self, client):
        registry = client.app.state.registry
        registry.register(_make_policy("chain-ver"))

        resp = client.get("/policies")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        assert "version" in item
        assert item["version"] >= 1

    def test_invalid_page_param_returns_422(self, client):
        resp = client.get("/policies?page=0")
        assert resp.status_code == 422

    def test_per_page_too_large_returns_422(self, client):
        resp = client.get("/policies?per_page=9999")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /policies/{id}
# ---------------------------------------------------------------------------


class TestGetPolicy:
    def test_get_existing_policy(self, client):
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-get"))

        resp = client.get("/policies/chain-get")
        assert resp.status_code == 200
        body = resp.json()
        assert body["chain_id"] == "chain-get"
        assert body["policy_hash"] == bundle.policy_hash
        assert body["version"] == bundle.version

    def test_get_nonexistent_returns_404(self, client):
        resp = client.get("/policies/does-not-exist")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_get_returns_full_policy_fields(self, client):
        registry = client.app.state.registry
        policy = PolicyConfig(
            chain_id="chain-full",
            ceiling_usd=5.0,
            on_exceed="degrade",
            issued_at=1000.0,
            ceiling_tokens_out=10000,
            ceiling_steps=50,
            fallback_model="gpt-4o-mini",
            timeout_ms=30000,
            priority=75,
        )
        registry.register(policy)

        resp = client.get("/policies/chain-full")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ceiling_usd"] == 5.0
        assert body["on_exceed"] == "degrade"
        assert body["ceiling_tokens_out"] == 10000
        assert body["ceiling_steps"] == 50
        assert body["fallback_model"] == "gpt-4o-mini"
        assert body["timeout_ms"] == 30000
        assert body["priority"] == 75

    def test_policy_hash_is_64_char_hex(self, client):
        registry = client.app.state.registry
        registry.register(_make_policy("chain-hex"))

        resp = client.get("/policies/chain-hex")
        assert resp.status_code == 200
        h = resp.json()["policy_hash"]
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)


# ---------------------------------------------------------------------------
# Adversarial tests
# ---------------------------------------------------------------------------


class TestAdversarialPolicies:
    def test_special_chars_in_chain_id_returns_422(self, client):
        """Chain ID with special chars must be rejected by SafeChainId validation."""
        resp = client.get("/policies/chain!@#$%25not-registered")
        assert resp.status_code == 422

    def test_very_long_chain_id_returns_422(self, client):
        """Very long chain_id must be rejected by SafeChainId max_length."""
        long_id = "x" * 2000
        resp = client.get(f"/policies/{long_id}")
        assert resp.status_code == 422

    def test_pagination_beyond_total_returns_empty(self, client):
        """Page beyond total should return empty items, not error."""
        registry = client.app.state.registry
        registry.register(_make_policy("chain-beyond"))

        resp = client.get("/policies?page=999&per_page=20")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 1  # still has data

    def test_per_page_zero_returns_422(self, client):
        resp = client.get("/policies?per_page=0")
        assert resp.status_code == 422

    def test_list_response_shape_stable(self, client):
        """PolicySummary must have exactly the expected keys."""
        registry = client.app.state.registry
        registry.register(_make_policy("chain-shape"))

        resp = client.get("/policies")
        assert resp.status_code == 200
        item = resp.json()["items"][0]
        expected_keys = {"chain_id", "on_exceed", "ceiling_usd", "priority", "issued_at", "policy_hash", "version"}
        assert set(item.keys()) == expected_keys

    def test_concurrent_register_then_list(self, client):
        """Concurrent registrations must all be visible in list."""
        import threading
        registry = client.app.state.registry
        errors = []

        def register(i: int) -> None:
            try:
                registry.register(_make_policy(f"concurrent-{i}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=register, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        resp = client.get("/policies?per_page=200")
        assert resp.status_code == 200
        registered = {item["chain_id"] for item in resp.json()["items"] if item["chain_id"].startswith("concurrent-")}
        assert len(registered) == 10
