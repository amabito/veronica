# tests/test_rollouts.py
"""Tests for rollout pipeline endpoints (/rollouts)."""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app

_FIXED_ISSUED_AT = 1_700_000_000.0

_BASE_POLICY: dict = {
    "chain_id": "test-chain",
    "ceiling_usd": 1.0,
    "on_exceed": "halt",
    "issued_at": _FIXED_ISSUED_AT,
}


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as c:
        yield c


def _create(
    client: TestClient, policy: dict | None = None, created_by: str = "tester"
) -> dict:
    """Helper: create a rollout, assert 201, return JSON body."""
    body = {"policy_config": policy or _BASE_POLICY, "created_by": created_by}
    resp = client.post("/rollouts", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _advance_to_active(client: TestClient, rollout_id: str) -> None:
    """Helper: drive a rollout from DRAFT to ACTIVE."""
    client.post(f"/rollouts/{rollout_id}/simulate")
    client.post(f"/rollouts/{rollout_id}/approve", json={"actor": "qa"})
    client.post(f"/rollouts/{rollout_id}/promote", json={"actor": "ops"})
    client.post(f"/rollouts/{rollout_id}/activate", json={"actor": "ops"})


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateRollout:
    def test_create_returns_201(self, client: TestClient) -> None:
        resp = client.post(
            "/rollouts",
            json={"policy_config": _BASE_POLICY, "created_by": "alice"},
        )
        assert resp.status_code == 201

    def test_create_response_shape(self, client: TestClient) -> None:
        data = _create(client)
        required = {
            "id",
            "state",
            "created_by",
            "created_at",
            "updated_at",
            "history",
            "simulation_result",
        }
        assert required.issubset(data.keys())

    def test_create_initial_state_is_draft(self, client: TestClient) -> None:
        data = _create(client)
        assert data["state"] == "draft"

    def test_create_history_is_empty(self, client: TestClient) -> None:
        data = _create(client)
        assert data["history"] == []

    def test_create_simulation_result_is_null(self, client: TestClient) -> None:
        data = _create(client)
        assert data["simulation_result"] is None

    def test_create_invalid_policy_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/rollouts",
            json={"policy_config": {"chain_id": "x"}, "created_by": "alice"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestGetRollout:
    def test_get_existing_returns_200(self, client: TestClient) -> None:
        data = _create(client)
        resp = client.get(f"/rollouts/{data['id']}")
        assert resp.status_code == 200

    def test_get_nonexistent_returns_404(self, client: TestClient) -> None:
        resp = client.get("/rollouts/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_get_returns_correct_id(self, client: TestClient) -> None:
        data = _create(client)
        resp = client.get(f"/rollouts/{data['id']}")
        assert resp.json()["id"] == data["id"]


# ---------------------------------------------------------------------------
# List + Pagination + State filter
# ---------------------------------------------------------------------------


class TestListRollouts:
    def test_list_empty(self, client: TestClient) -> None:
        resp = client.get("/rollouts")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_list_returns_created_rollout(self, client: TestClient) -> None:
        _create(client)
        resp = client.get("/rollouts")
        assert resp.json()["total"] == 1

    def test_list_response_shape(self, client: TestClient) -> None:
        resp = client.get("/rollouts")
        body = resp.json()
        assert {"items", "total", "page", "per_page"}.issubset(body.keys())

    def test_pagination_per_page(self, client: TestClient) -> None:
        for _ in range(5):
            _create(client)
        resp = client.get("/rollouts?page=1&per_page=2")
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5

    def test_pagination_page2(self, client: TestClient) -> None:
        for _ in range(5):
            _create(client)
        resp = client.get("/rollouts?page=2&per_page=3")
        body = resp.json()
        assert len(body["items"]) == 2  # 5 total, page2 with per_page=3 has 2 items

    def test_state_filter_draft(self, client: TestClient) -> None:
        d = _create(client)
        client.post(f"/rollouts/{d['id']}/simulate")
        _create(client)  # remains DRAFT
        resp = client.get("/rollouts?state=draft")
        body = resp.json()
        assert body["total"] == 1
        assert all(item["state"] == "draft" for item in body["items"])

    def test_state_filter_simulated(self, client: TestClient) -> None:
        d = _create(client)
        client.post(f"/rollouts/{d['id']}/simulate")
        _create(client)  # remains DRAFT
        resp = client.get("/rollouts?state=simulated")
        assert resp.json()["total"] == 1


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------


class TestRolloutLifecycle:
    def test_draft_to_simulated(self, client: TestClient) -> None:
        data = _create(client)
        resp = client.post(f"/rollouts/{data['id']}/simulate")
        assert resp.status_code == 200
        assert resp.json()["state"] == "simulated"

    def test_simulate_stores_result(self, client: TestClient) -> None:
        data = _create(client)
        resp = client.post(f"/rollouts/{data['id']}/simulate")
        body = resp.json()
        assert body["simulation_result"] is not None
        assert "policy_hash" in body["simulation_result"]

    def test_simulate_adds_history_entry(self, client: TestClient) -> None:
        data = _create(client)
        client.post(f"/rollouts/{data['id']}/simulate")
        resp = client.get(f"/rollouts/{data['id']}")
        history = resp.json()["history"]
        assert len(history) == 1
        assert history[0]["from_state"] == "draft"
        assert history[0]["to_state"] == "simulated"

    def test_simulated_to_approved(self, client: TestClient) -> None:
        data = _create(client)
        client.post(f"/rollouts/{data['id']}/simulate")
        resp = client.post(f"/rollouts/{data['id']}/approve", json={"actor": "qa"})
        assert resp.status_code == 200
        assert resp.json()["state"] == "approved"

    def test_approved_to_promoted(self, client: TestClient) -> None:
        data = _create(client)
        client.post(f"/rollouts/{data['id']}/simulate")
        client.post(f"/rollouts/{data['id']}/approve", json={"actor": "qa"})
        resp = client.post(f"/rollouts/{data['id']}/promote", json={"actor": "ops"})
        assert resp.json()["state"] == "promoted"

    def test_promoted_to_active(self, client: TestClient) -> None:
        data = _create(client)
        _advance_to_active(client, data["id"])
        resp = client.get(f"/rollouts/{data['id']}")
        assert resp.json()["state"] == "active"

    def test_full_lifecycle_history_length(self, client: TestClient) -> None:
        data = _create(client)
        _advance_to_active(client, data["id"])
        resp = client.get(f"/rollouts/{data['id']}")
        # simulate, approve, promote, activate = 4 transitions
        assert len(resp.json()["history"]) == 4


# ---------------------------------------------------------------------------
# Revocation
# ---------------------------------------------------------------------------


class TestRevocation:
    def test_revoke_from_draft(self, client: TestClient) -> None:
        data = _create(client)
        resp = client.post(f"/rollouts/{data['id']}/revoke", json={"actor": "admin"})
        assert resp.status_code == 200
        assert resp.json()["state"] == "revoked"

    def test_revoke_from_simulated(self, client: TestClient) -> None:
        data = _create(client)
        client.post(f"/rollouts/{data['id']}/simulate")
        resp = client.post(f"/rollouts/{data['id']}/revoke", json={"actor": "admin"})
        assert resp.json()["state"] == "revoked"

    def test_revoke_from_approved(self, client: TestClient) -> None:
        data = _create(client)
        client.post(f"/rollouts/{data['id']}/simulate")
        client.post(f"/rollouts/{data['id']}/approve", json={"actor": "qa"})
        resp = client.post(f"/rollouts/{data['id']}/revoke", json={"actor": "admin"})
        assert resp.json()["state"] == "revoked"

    def test_revoke_from_promoted(self, client: TestClient) -> None:
        data = _create(client)
        client.post(f"/rollouts/{data['id']}/simulate")
        client.post(f"/rollouts/{data['id']}/approve", json={"actor": "qa"})
        client.post(f"/rollouts/{data['id']}/promote", json={"actor": "ops"})
        resp = client.post(f"/rollouts/{data['id']}/revoke", json={"actor": "admin"})
        assert resp.json()["state"] == "revoked"

    def test_revoke_from_active(self, client: TestClient) -> None:
        data = _create(client)
        _advance_to_active(client, data["id"])
        resp = client.post(f"/rollouts/{data['id']}/revoke", json={"actor": "admin"})
        assert resp.json()["state"] == "revoked"

    def test_revoked_is_terminal(self, client: TestClient) -> None:
        data = _create(client)
        client.post(f"/rollouts/{data['id']}/revoke", json={"actor": "admin"})
        resp = client.post(f"/rollouts/{data['id']}/simulate")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------


class TestInvalidTransitions:
    def test_draft_to_active_is_409(self, client: TestClient) -> None:
        data = _create(client)
        resp = client.post(f"/rollouts/{data['id']}/activate", json={"actor": "ops"})
        assert resp.status_code == 409

    def test_draft_to_approved_is_409(self, client: TestClient) -> None:
        data = _create(client)
        resp = client.post(f"/rollouts/{data['id']}/approve", json={"actor": "qa"})
        assert resp.status_code == 409

    def test_draft_to_promoted_is_409(self, client: TestClient) -> None:
        data = _create(client)
        resp = client.post(f"/rollouts/{data['id']}/promote", json={"actor": "ops"})
        assert resp.status_code == 409

    def test_active_to_draft_is_409(self, client: TestClient) -> None:
        data = _create(client)
        _advance_to_active(client, data["id"])
        resp = client.post(f"/rollouts/{data['id']}/simulate")
        assert resp.status_code == 409

    def test_active_to_approved_is_409(self, client: TestClient) -> None:
        data = _create(client)
        _advance_to_active(client, data["id"])
        resp = client.post(f"/rollouts/{data['id']}/approve", json={"actor": "qa"})
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# 404 for nonexistent rollout
# ---------------------------------------------------------------------------


_MISSING_UUID = "00000000-0000-0000-0000-000000000000"


class TestNotFound:
    def test_get_404(self, client: TestClient) -> None:
        assert client.get(f"/rollouts/{_MISSING_UUID}").status_code == 404

    def test_simulate_404(self, client: TestClient) -> None:
        assert client.post(f"/rollouts/{_MISSING_UUID}/simulate").status_code == 404

    def test_approve_404(self, client: TestClient) -> None:
        assert (
            client.post(
                f"/rollouts/{_MISSING_UUID}/approve", json={"actor": "qa"}
            ).status_code
            == 404
        )

    def test_promote_404(self, client: TestClient) -> None:
        assert (
            client.post(
                f"/rollouts/{_MISSING_UUID}/promote", json={"actor": "ops"}
            ).status_code
            == 404
        )

    def test_activate_404(self, client: TestClient) -> None:
        assert (
            client.post(
                f"/rollouts/{_MISSING_UUID}/activate", json={"actor": "ops"}
            ).status_code
            == 404
        )

    def test_revoke_404(self, client: TestClient) -> None:
        assert (
            client.post(
                f"/rollouts/{_MISSING_UUID}/revoke", json={"actor": "admin"}
            ).status_code
            == 404
        )


# ---------------------------------------------------------------------------
# Concurrent transitions
# ---------------------------------------------------------------------------


class TestConcurrentTransitions:
    def test_concurrent_revoke_is_safe(self, client: TestClient) -> None:
        """10 threads try to revoke the same DRAFT rollout -- result must be REVOKED, no crash."""
        data = _create(client)
        rollout_id = data["id"]
        status_codes: list[int] = []
        lock = threading.Lock()

        def do_revoke() -> None:
            resp = client.post(
                f"/rollouts/{rollout_id}/revoke", json={"actor": "admin"}
            )
            with lock:
                status_codes.append(resp.status_code)

        threads = [threading.Thread(target=do_revoke) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # At least one must succeed; the rest may be 409 (already revoked)
        assert 200 in status_codes
        # Final state must be revoked
        final = client.get(f"/rollouts/{rollout_id}").json()
        assert final["state"] == "revoked"

    def test_concurrent_approve_only_one_succeeds(self, client: TestClient) -> None:
        """10 threads try to approve the same SIMULATED rollout -- only 1 should succeed."""
        data = _create(client)
        rollout_id = data["id"]
        client.post(f"/rollouts/{rollout_id}/simulate")

        results: list[int] = []
        lock = threading.Lock()

        def do_approve() -> None:
            resp = client.post(f"/rollouts/{rollout_id}/approve", json={"actor": "qa"})
            with lock:
                results.append(resp.status_code)

        threads = [threading.Thread(target=do_approve) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        successes = results.count(200)
        assert successes == 1
        final = client.get(f"/rollouts/{rollout_id}").json()
        assert final["state"] == "approved"
