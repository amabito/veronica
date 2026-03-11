# tests/test_integration_phase5.py
"""Integration tests -- tenant + rollout + replay E2E flows."""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app
from veronica.schemas.events import StepOutcome


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_POLICY_BASE = {
    "chain_id": "chain-test",
    "ceiling_usd": 5.0,
    "on_exceed": "halt",
}


def _make_policy(chain_id: str = "chain-test", ceiling_usd: float = 5.0) -> dict:
    return {"chain_id": chain_id, "ceiling_usd": ceiling_usd, "on_exceed": "halt"}


def _activate_rollout(client: TestClient, policy: dict, actor: str = "ci") -> dict:
    """Helper: create and fully activate a rollout. Returns the final rollout dict."""
    r = client.post("/rollouts", json={"policy_config": policy, "created_by": actor})
    assert r.status_code == 201, r.text
    rollout_id = r.json()["id"]

    r = client.post(f"/rollouts/{rollout_id}/simulate")
    assert r.status_code == 200, r.text

    r = client.post(f"/rollouts/{rollout_id}/approve", json={"actor": actor})
    assert r.status_code == 200, r.text

    r = client.post(f"/rollouts/{rollout_id}/promote", json={"actor": actor})
    assert r.status_code == 200, r.text

    r = client.post(f"/rollouts/{rollout_id}/activate", json={"actor": actor})
    assert r.status_code == 200, r.text
    return r.json()


def _ingest_outcome(client: TestClient, chain_id: str, ts: float) -> None:
    """Directly insert a StepOutcome into the app ingestor store via TestClient app state."""
    # Access ingestor store directly through the app state (available on TestClient.app)
    store = client.app.state.ingestor.store  # type: ignore[attr-defined]
    outcome = StepOutcome(
        step_id=uuid.uuid4().hex,
        chain_id=chain_id,
        operation_name="test_op",
        decision="allow",
        cost_usd=0.01,
        tokens=10,
        duration_ms=5.0,
        policy_hash="aabbccdd" * 8,
        audit_id=uuid.uuid4().hex,
        timestamp=ts,
    )
    store.put(outcome)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
    app = create_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTenantRolloutE2EFlow:
    """Test 1: Create tenant with overrides, create rollout, activate, verify export."""

    def test_tenant_rollout_e2e_flow(self, client: TestClient) -> None:
        tenant_id = f"org-{uuid.uuid4().hex[:8]}"
        chain_id = f"chain-{uuid.uuid4().hex[:8]}"

        # Create tenant with ceiling_usd override
        r = client.post(
            "/tenants",
            json={
                "id": tenant_id,
                "name": "E2E Org",
                "policy_overrides": {"ceiling_usd": 99.0},
            },
        )
        assert r.status_code == 201, r.text
        assert r.json()["id"] == tenant_id

        # Create and activate a rollout for the chain
        policy = _make_policy(chain_id=chain_id, ceiling_usd=7.5)
        rollout = _activate_rollout(client, policy)
        assert rollout["state"] == "active"

        # /export must now contain the registered policy
        r = client.get("/export")
        assert r.status_code == 200, r.text
        export = r.json()
        chain_ids = [p["chain_id"] for p in export["policies"]]
        assert chain_id in chain_ids


class TestRolloutRevocationDoesNotRemovePolicy:
    """Test 2: Revoke an active rollout -- policy stays in registry."""

    def test_rollout_revocation_blocks_replay_of_active_policy(
        self, client: TestClient
    ) -> None:
        chain_id = f"chain-{uuid.uuid4().hex[:8]}"
        policy = _make_policy(chain_id=chain_id)

        rollout = _activate_rollout(client, policy)
        rollout_id = rollout["id"]
        assert rollout["state"] == "active"

        # Revoke the rollout
        r = client.post(f"/rollouts/{rollout_id}/revoke", json={"actor": "admin"})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "revoked"

        # Policy should still be in the registry (revocation only changes rollout state)
        r = client.get("/export")
        assert r.status_code == 200, r.text
        chain_ids = [p["chain_id"] for p in r.json()["policies"]]
        assert chain_id in chain_ids

        # Rollout is in REVOKED state
        r = client.get(f"/rollouts/{rollout_id}")
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "revoked"


class TestMultipleTenantsWithRollouts:
    """Test 3: Org and team tenants with independent rollout lifecycles."""

    def test_multiple_tenants_with_rollouts(self, client: TestClient) -> None:
        suffix = uuid.uuid4().hex[:6]
        org_id = f"org-{suffix}"
        team_id = f"team-{suffix}"
        chain_org = f"chain-org-{suffix}"
        chain_team = f"chain-team-{suffix}"

        # Create org tenant
        r = client.post("/tenants", json={"id": org_id, "name": "Org"})
        assert r.status_code == 201, r.text

        # Create team tenant under org
        r = client.post(
            "/tenants",
            json={"id": team_id, "name": "Team", "parent_id": org_id},
        )
        assert r.status_code == 201, r.text
        assert r.json()["parent_id"] == org_id

        # Create rollouts for both, advance to different states
        org_rollout = _activate_rollout(
            client, _make_policy(chain_id=chain_org), actor="org-admin"
        )
        assert org_rollout["state"] == "active"

        team_rollout = _activate_rollout(
            client, _make_policy(chain_id=chain_team), actor="team-admin"
        )
        assert team_rollout["state"] == "active"

        # Both policies in export
        r = client.get("/export")
        assert r.status_code == 200, r.text
        chain_ids = [p["chain_id"] for p in r.json()["policies"]]
        assert chain_org in chain_ids
        assert chain_team in chain_ids


class TestReplayWithEventsAfterRolloutActivation:
    """Test 4: Activate rollout, ingest events, replay returns non-empty result."""

    def test_replay_with_events_after_rollout_activation(
        self, client: TestClient
    ) -> None:
        chain_id = f"chain-{uuid.uuid4().hex[:8]}"

        # Activate a rollout first
        _activate_rollout(client, _make_policy(chain_id=chain_id))

        # Ingest events for the chain
        base_ts = time.time() - 60.0
        _ingest_outcome(client, chain_id, base_ts)
        _ingest_outcome(client, chain_id, base_ts + 1.0)

        # Replay within the time window
        r = client.post(
            "/replay",
            json={
                "chain_id": chain_id,
                "from_timestamp": base_ts - 1.0,
                "to_timestamp": base_ts + 10.0,
            },
        )
        assert r.status_code == 200, r.text
        result = r.json()
        assert result["chain_id"] == chain_id
        assert result["event_count"] >= 2


class TestTenantDeletionBlockedWithChildren:
    """Test 5: Delete parent tenant that has children -- expect 409."""

    def test_tenant_deletion_blocked_with_children(self, client: TestClient) -> None:
        suffix = uuid.uuid4().hex[:6]
        parent_id = f"parent-{suffix}"
        child_id = f"child-{suffix}"

        r = client.post("/tenants", json={"id": parent_id, "name": "Parent"})
        assert r.status_code == 201, r.text

        r = client.post(
            "/tenants",
            json={"id": child_id, "name": "Child", "parent_id": parent_id},
        )
        assert r.status_code == 201, r.text

        # Deleting parent should be blocked
        r = client.delete(f"/tenants/{parent_id}")
        assert r.status_code == 409, r.text

        # Child still exists
        r = client.get(f"/tenants/{child_id}")
        assert r.status_code == 200, r.text


class TestRolloutActivateRegistersPolicyInExport:
    """Test 6: Activating a rollout registers the policy in /export."""

    def test_rollout_activate_registers_policy_in_export(
        self, client: TestClient
    ) -> None:
        chain_id = f"chain-{uuid.uuid4().hex[:8]}"
        policy = _make_policy(chain_id=chain_id, ceiling_usd=42.0)

        # Before activation, chain not in export
        r = client.get("/export")
        assert r.status_code == 200, r.text
        chain_ids_before = {p["chain_id"] for p in r.json()["policies"]}
        assert chain_id not in chain_ids_before

        _activate_rollout(client, policy)

        # After activation, chain IS in export
        r = client.get("/export")
        assert r.status_code == 200, r.text
        matching = [p for p in r.json()["policies"] if p["chain_id"] == chain_id]
        assert len(matching) >= 1
        assert matching[0]["ceiling_usd"] == 42.0


class TestConcurrentRolloutsForSameChain:
    """Test 7: Two rollouts for the same chain_id can coexist independently."""

    def test_concurrent_rollouts_for_same_chain(self, client: TestClient) -> None:
        chain_id = f"chain-{uuid.uuid4().hex[:8]}"
        policy = _make_policy(chain_id=chain_id)

        # Create two rollouts for the same chain
        r1 = client.post(
            "/rollouts", json={"policy_config": policy, "created_by": "user1"}
        )
        assert r1.status_code == 201, r1.text
        r2 = client.post(
            "/rollouts", json={"policy_config": policy, "created_by": "user2"}
        )
        assert r2.status_code == 201, r2.text

        id1 = r1.json()["id"]
        id2 = r2.json()["id"]
        assert id1 != id2

        # Activate the first rollout
        rollout1 = _activate_rollout(client, policy)
        assert rollout1["state"] == "active"

        # The second rollout is still independently in DRAFT
        r = client.get(f"/rollouts/{id2}")
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "draft"

        # Activating the second one independently also succeeds
        r = client.post(f"/rollouts/{id2}/simulate")
        assert r.status_code == 200, r.text
        r = client.post(f"/rollouts/{id2}/approve", json={"actor": "ci"})
        assert r.status_code == 200, r.text
        r = client.post(f"/rollouts/{id2}/promote", json={"actor": "ci"})
        assert r.status_code == 200, r.text
        r = client.post(f"/rollouts/{id2}/activate", json={"actor": "ci"})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "active"


class TestEffectivePolicyReflectsTenantOverrides:
    """Test 8: Effective policy endpoint reflects tenant ceiling_usd override."""

    def test_effective_policy_reflects_tenant_overrides(
        self, client: TestClient
    ) -> None:
        tenant_id = f"tenant-{uuid.uuid4().hex[:8]}"

        r = client.post(
            "/tenants",
            json={
                "id": tenant_id,
                "name": "Override Tenant",
                "policy_overrides": {"ceiling_usd": 777.0},
            },
        )
        assert r.status_code == 201, r.text

        r = client.get(f"/tenants/{tenant_id}/effective-policy")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["tenant_id"] == tenant_id
        assert data["ceiling_usd"] == 777.0


class TestRolloutSimulateStoresResult:
    """Test 9: Simulate endpoint stores simulation_result on the rollout."""

    def test_rollout_simulate_stores_result(self, client: TestClient) -> None:
        chain_id = f"chain-{uuid.uuid4().hex[:8]}"
        policy = _make_policy(chain_id=chain_id)

        r = client.post("/rollouts", json={"policy_config": policy, "created_by": "ci"})
        assert r.status_code == 201, r.text
        rollout_id = r.json()["id"]
        assert r.json()["simulation_result"] is None

        r = client.post(f"/rollouts/{rollout_id}/simulate")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["state"] == "simulated"
        assert data["simulation_result"] is not None

        sim = data["simulation_result"]
        assert "policy_hash" in sim
        assert "distributed_at" in sim
        assert "version" in sim

        # GET confirms persistence
        r = client.get(f"/rollouts/{rollout_id}")
        assert r.status_code == 200, r.text
        assert r.json()["simulation_result"] is not None


class TestFullLifecycleWithReplay:
    """Test 10: Full flow -- create tenant, rollout lifecycle, then replay chain events."""

    def test_full_lifecycle_with_replay(self, client: TestClient) -> None:
        suffix = uuid.uuid4().hex[:8]
        tenant_id = f"tenant-{suffix}"
        chain_id = f"chain-{suffix}"

        # Step 1: create tenant
        r = client.post(
            "/tenants",
            json={"id": tenant_id, "name": "Full Lifecycle Tenant"},
        )
        assert r.status_code == 201, r.text

        # Step 2: create rollout in DRAFT
        policy = _make_policy(chain_id=chain_id, ceiling_usd=20.0)
        r = client.post(
            "/rollouts", json={"policy_config": policy, "created_by": tenant_id}
        )
        assert r.status_code == 201, r.text
        rollout_id = r.json()["id"]
        assert r.json()["state"] == "draft"

        # Step 3: simulate
        r = client.post(f"/rollouts/{rollout_id}/simulate")
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "simulated"
        assert r.json()["simulation_result"] is not None

        # Step 4: approve
        r = client.post(f"/rollouts/{rollout_id}/approve", json={"actor": "approver"})
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "approved"

        # Step 5: promote
        r = client.post(
            f"/rollouts/{rollout_id}/promote", json={"actor": "release-bot"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "promoted"

        # Step 6: activate -- registers policy
        r = client.post(
            f"/rollouts/{rollout_id}/activate", json={"actor": "release-bot"}
        )
        assert r.status_code == 200, r.text
        assert r.json()["state"] == "active"

        # Verify policy in export
        r = client.get("/export")
        assert r.status_code == 200, r.text
        chain_ids = [p["chain_id"] for p in r.json()["policies"]]
        assert chain_id in chain_ids

        # Step 7: ingest events for the chain
        base_ts = time.time() - 30.0
        _ingest_outcome(client, chain_id, base_ts)
        _ingest_outcome(client, chain_id, base_ts + 5.0)

        # Step 8: replay
        r = client.post(
            "/replay",
            json={
                "chain_id": chain_id,
                "from_timestamp": base_ts - 5.0,
                "to_timestamp": base_ts + 30.0,
            },
        )
        assert r.status_code == 200, r.text
        replay_result = r.json()
        assert replay_result["chain_id"] == chain_id
        assert replay_result["event_count"] >= 2
        assert isinstance(replay_result["diffs"], list)
        assert isinstance(replay_result["summary"], str)
