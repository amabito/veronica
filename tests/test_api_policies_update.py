# tests/test_api_policies_update.py
"""Tests for PUT /policies/{id} endpoint."""
from __future__ import annotations

import threading
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
# PUT /policies/{id} -- happy path
# ---------------------------------------------------------------------------


class TestUpdatePolicy:
    def test_update_ceiling_usd(self, client):
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-upd"))
        version = bundle.version

        resp = client.put(
            "/policies/chain-upd",
            json={"current_version": version, "ceiling_usd": 9.99},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ceiling_usd"] == 9.99
        assert body["version"] == version + 1

    def test_update_on_exceed(self, client):
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-exceed"))

        resp = client.put(
            "/policies/chain-exceed",
            json={"current_version": bundle.version, "on_exceed": "degrade"},
        )
        assert resp.status_code == 200
        assert resp.json()["on_exceed"] == "degrade"

    def test_update_priority(self, client):
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-pri"))

        resp = client.put(
            "/policies/chain-pri",
            json={"current_version": bundle.version, "priority": 99},
        )
        assert resp.status_code == 200
        assert resp.json()["priority"] == 99

    def test_update_recomputes_policy_hash(self, client):
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-hash-update"))
        original_hash = bundle.policy_hash

        resp = client.put(
            "/policies/chain-hash-update",
            json={"current_version": bundle.version, "ceiling_usd": 42.0},
        )
        assert resp.status_code == 200
        assert resp.json()["policy_hash"] != original_hash

    def test_update_increments_version(self, client):
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-ver-inc"))
        original_version = bundle.version

        resp = client.put(
            "/policies/chain-ver-inc",
            json={"current_version": bundle.version, "ceiling_usd": 2.0},
        )
        assert resp.status_code == 200
        assert resp.json()["version"] == original_version + 1

    def test_update_multiple_fields(self, client):
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-multi"))

        resp = client.put(
            "/policies/chain-multi",
            json={
                "current_version": bundle.version,
                "ceiling_usd": 5.0,
                "on_exceed": "queue",
                "priority": 80,
                "ceiling_steps": 200,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ceiling_usd"] == 5.0
        assert body["on_exceed"] == "queue"
        assert body["priority"] == 80
        assert body["ceiling_steps"] == 200

    def test_updated_policy_visible_via_get(self, client):
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-visible"))

        client.put(
            "/policies/chain-visible",
            json={"current_version": bundle.version, "ceiling_usd": 7.0},
        )

        resp = client.get("/policies/chain-visible")
        assert resp.status_code == 200
        assert resp.json()["ceiling_usd"] == 7.0


# ---------------------------------------------------------------------------
# PUT /policies/{id} -- error cases
# ---------------------------------------------------------------------------


class TestUpdatePolicyErrors:
    def test_404_for_nonexistent_policy(self, client):
        resp = client.put(
            "/policies/does-not-exist",
            json={"current_version": 1, "ceiling_usd": 1.0},
        )
        assert resp.status_code == 404

    def test_409_for_stale_version(self, client):
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-stale"))

        resp = client.put(
            "/policies/chain-stale",
            json={"current_version": bundle.version - 1, "ceiling_usd": 1.0},
        )
        assert resp.status_code == 409

    def test_409_detail_mentions_stale(self, client):
        registry = client.app.state.registry
        registry.register(_make_policy("chain-stale-detail"))

        resp = client.put(
            "/policies/chain-stale-detail",
            json={"current_version": 9999, "ceiling_usd": 1.0},
        )
        assert resp.status_code == 409
        assert "stale" in resp.json()["detail"].lower()

    def test_422_for_invalid_ceiling_usd(self, client):
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-invalid"))

        resp = client.put(
            "/policies/chain-invalid",
            json={"current_version": bundle.version, "ceiling_usd": -1.0},
        )
        assert resp.status_code == 422

    def test_422_for_invalid_on_exceed(self, client):
        """Invalid on_exceed value must be rejected by Pydantic (422)."""
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-bad-exceed"))

        resp = client.put(
            "/policies/chain-bad-exceed",
            json={"current_version": bundle.version, "on_exceed": "explode"},
        )
        assert resp.status_code == 422

    def test_missing_current_version_returns_422(self, client):
        registry = client.app.state.registry
        registry.register(_make_policy("chain-no-ver"))

        resp = client.put(
            "/policies/chain-no-ver",
            json={"ceiling_usd": 1.0},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Adversarial tests
# ---------------------------------------------------------------------------


class TestAdversarialUpdate:
    def test_concurrent_updates_only_one_wins_with_version(self, client):
        """Only the update with the matching version should succeed; others get 409."""
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-concurrent-upd"))
        version = bundle.version

        results: list[int] = []
        lock = threading.Lock()

        def do_update(ceiling: float) -> None:
            resp = client.put(
                "/policies/chain-concurrent-upd",
                json={"current_version": version, "ceiling_usd": ceiling},
            )
            with lock:
                results.append(resp.status_code)

        threads = [threading.Thread(target=do_update, args=(float(i),)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        success = results.count(200)
        conflict = results.count(409)
        assert success == 1
        assert conflict == 4

    def test_update_with_zero_ceiling_usd(self, client):
        """ceiling_usd=0 is valid (free tier policy)."""
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-zero-usd"))

        resp = client.put(
            "/policies/chain-zero-usd",
            json={"current_version": bundle.version, "ceiling_usd": 0.0},
        )
        assert resp.status_code == 200
        assert resp.json()["ceiling_usd"] == 0.0

    def test_sequential_updates_each_increment_version(self, client):
        """Sequential updates should each increment version by 1."""
        registry = client.app.state.registry
        bundle = registry.register(_make_policy("chain-sequential"))
        base_version = bundle.version

        for i in range(3):
            resp = client.get("/policies/chain-sequential")
            current_ver = resp.json()["version"]
            resp = client.put(
                "/policies/chain-sequential",
                json={"current_version": current_ver, "priority": i + 1},
            )
            assert resp.status_code == 200
            assert resp.json()["version"] == base_version + i + 1

    def test_update_with_none_optional_fields_does_not_clear_them(self, client):
        """Providing only some fields must not null out unmentioned fields."""
        registry = client.app.state.registry
        policy = PolicyConfig(
            chain_id="chain-partial",
            ceiling_usd=1.0,
            on_exceed="halt",
            issued_at=time.time(),
            ceiling_steps=100,
            fallback_model="gpt-4o-mini",
        )
        bundle = registry.register(policy)

        # Update only ceiling_usd; other optional fields must survive
        resp = client.put(
            "/policies/chain-partial",
            json={"current_version": bundle.version, "ceiling_usd": 2.0},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ceiling_steps"] == 100
        assert body["fallback_model"] == "gpt-4o-mini"

    def test_update_nonexistent_does_not_create(self, client):
        """PUT on a non-existent chain_id must not create a new policy."""
        resp = client.put(
            "/policies/ghost-chain",
            json={"current_version": 1, "ceiling_usd": 1.0},
        )
        assert resp.status_code == 404

        # Confirm it's still not in the registry
        resp = client.get("/policies/ghost-chain")
        assert resp.status_code == 404
