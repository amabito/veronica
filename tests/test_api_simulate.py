# tests/test_api_simulate.py
"""Tests for POST /simulate endpoint."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app

_BASE_POLICY: dict = {
    "chain_id": "test-chain",
    "ceiling_usd": 1.0,
    "on_exceed": "halt",
    "issued_at": time.time(),
}

_CHEAP_STEP = {"kind": "llm", "cost_usd": 0.01, "tokens_out": 100, "elapsed_ms": 50.0}
_EXPENSIVE_STEP = {"kind": "llm", "cost_usd": 5.0, "tokens_out": 1000, "elapsed_ms": 200.0}


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as c:
        yield c


class TestSimulateBasic:
    def test_empty_steps_returns_200(self, client: TestClient) -> None:
        resp = client.post("/simulate", json={"policy": _BASE_POLICY, "steps": []})
        assert resp.status_code == 200

    def test_response_shape(self, client: TestClient) -> None:
        resp = client.post("/simulate", json={"policy": _BASE_POLICY, "steps": []})
        data = resp.json()
        required = {
            "policy_hash", "total_steps", "steps_allowed", "steps_halted",
            "total_cost_usd", "final_decision", "step_results", "store_unchanged",
        }
        assert required.issubset(data.keys())

    def test_store_unchanged_is_true(self, client: TestClient) -> None:
        resp = client.post("/simulate", json={"policy": _BASE_POLICY, "steps": [_CHEAP_STEP]})
        assert resp.json()["store_unchanged"] is True

    def test_policy_hash_present(self, client: TestClient) -> None:
        resp = client.post("/simulate", json={"policy": _BASE_POLICY, "steps": []})
        assert resp.json()["policy_hash"] != ""

    def test_single_cheap_step_allowed(self, client: TestClient) -> None:
        resp = client.post("/simulate", json={"policy": _BASE_POLICY, "steps": [_CHEAP_STEP]})
        data = resp.json()
        assert data["final_decision"] == "allow"
        assert data["steps_allowed"] == 1
        assert data["steps_halted"] == 0


class TestSimulateBudgetEnforcement:
    def test_over_ceiling_triggers_on_exceed(self, client: TestClient) -> None:
        policy = {**_BASE_POLICY, "ceiling_usd": 0.005, "on_exceed": "halt"}
        resp = client.post("/simulate", json={"policy": policy, "steps": [_CHEAP_STEP]})
        data = resp.json()
        assert data["final_decision"] == "halt"
        assert data["steps_halted"] == 1

    def test_on_exceed_degrade(self, client: TestClient) -> None:
        policy = {**_BASE_POLICY, "ceiling_usd": 0.005, "on_exceed": "degrade"}
        resp = client.post("/simulate", json={"policy": policy, "steps": [_CHEAP_STEP]})
        data = resp.json()
        assert data["final_decision"] == "degrade"

    def test_on_exceed_queue(self, client: TestClient) -> None:
        policy = {**_BASE_POLICY, "ceiling_usd": 0.005, "on_exceed": "queue"}
        resp = client.post("/simulate", json={"policy": policy, "steps": [_CHEAP_STEP]})
        assert resp.json()["final_decision"] == "queue"

    def test_stops_at_first_halt(self, client: TestClient) -> None:
        # ceiling_usd=0.005 -- first step (0.01) halts, second never runs
        policy = {**_BASE_POLICY, "ceiling_usd": 0.005, "on_exceed": "halt"}
        steps = [_CHEAP_STEP, _CHEAP_STEP]
        resp = client.post("/simulate", json={"policy": policy, "steps": steps})
        data = resp.json()
        assert data["total_steps"] == 1  # only 1 evaluated before halt

    def test_cumulative_cost_tracked(self, client: TestClient) -> None:
        policy = {**_BASE_POLICY, "ceiling_usd": 10.0}
        steps = [_CHEAP_STEP, _CHEAP_STEP, _CHEAP_STEP]
        resp = client.post("/simulate", json={"policy": policy, "steps": steps})
        data = resp.json()
        assert abs(data["total_cost_usd"] - 0.03) < 1e-9

    def test_token_ceiling_enforced(self, client: TestClient) -> None:
        policy = {**_BASE_POLICY, "ceiling_usd": 100.0, "ceiling_tokens_out": 50}
        step = {**_CHEAP_STEP, "tokens_out": 100}
        resp = client.post("/simulate", json={"policy": policy, "steps": [step]})
        data = resp.json()
        assert data["final_decision"] == "halt"

    def test_step_ceiling_enforced(self, client: TestClient) -> None:
        policy = {**_BASE_POLICY, "ceiling_usd": 100.0, "ceiling_steps": 1}
        steps = [_CHEAP_STEP, _CHEAP_STEP]
        resp = client.post("/simulate", json={"policy": policy, "steps": steps})
        data = resp.json()
        # First step passes (step count = 1 = ceiling), second is blocked
        assert data["total_steps"] <= 2


class TestSimulateStepResults:
    def test_step_results_count(self, client: TestClient) -> None:
        policy = {**_BASE_POLICY, "ceiling_usd": 10.0}
        resp = client.post(
            "/simulate",
            json={"policy": policy, "steps": [_CHEAP_STEP, _CHEAP_STEP]},
        )
        data = resp.json()
        assert len(data["step_results"]) == 2

    def test_step_result_shape(self, client: TestClient) -> None:
        resp = client.post("/simulate", json={"policy": _BASE_POLICY, "steps": [_CHEAP_STEP]})
        step = resp.json()["step_results"][0]
        required = {
            "step_index", "kind", "cost_usd", "tokens_out", "elapsed_ms",
            "cumulative_cost_usd", "decision", "reason",
        }
        assert required.issubset(step.keys())

    def test_cumulative_cost_per_step(self, client: TestClient) -> None:
        policy = {**_BASE_POLICY, "ceiling_usd": 10.0}
        resp = client.post(
            "/simulate",
            json={"policy": policy, "steps": [_CHEAP_STEP, _CHEAP_STEP]},
        )
        results = resp.json()["step_results"]
        assert abs(results[0]["cumulative_cost_usd"] - 0.01) < 1e-9
        assert abs(results[1]["cumulative_cost_usd"] - 0.02) < 1e-9


class TestSimulateValidation:
    def test_missing_chain_id_returns_422(self, client: TestClient) -> None:
        policy = {"ceiling_usd": 1.0, "on_exceed": "halt"}
        resp = client.post("/simulate", json={"policy": policy, "steps": []})
        assert resp.status_code == 422

    def test_missing_ceiling_usd_returns_422(self, client: TestClient) -> None:
        policy = {"chain_id": "x", "on_exceed": "halt"}
        resp = client.post("/simulate", json={"policy": policy, "steps": []})
        assert resp.status_code == 422

    def test_invalid_on_exceed_returns_422(self, client: TestClient) -> None:
        policy = {**_BASE_POLICY, "on_exceed": "invalid"}
        resp = client.post("/simulate", json={"policy": policy, "steps": []})
        assert resp.status_code == 422

    def test_negative_ceiling_usd_returns_422(self, client: TestClient) -> None:
        policy = {**_BASE_POLICY, "ceiling_usd": -1.0}
        resp = client.post("/simulate", json={"policy": policy, "steps": []})
        assert resp.status_code == 422

    def test_empty_body_returns_422(self, client: TestClient) -> None:
        resp = client.post("/simulate", json={})
        assert resp.status_code == 422


class TestSimulateSideEffects:
    def test_store_not_modified(self, client: TestClient) -> None:
        """Simulation must not write to the event store."""
        app = create_app()
        with TestClient(app) as c:
            before = len(app.state.ingestor._store)
            c.post("/simulate", json={"policy": _BASE_POLICY, "steps": [_CHEAP_STEP]})
            after = len(app.state.ingestor._store)
            assert before == after

    def test_multiple_simulations_independent(self, client: TestClient) -> None:
        """Two simulations with different policies must not interfere."""
        policy_a = {**_BASE_POLICY, "chain_id": "chain-a", "ceiling_usd": 0.005}
        policy_b = {**_BASE_POLICY, "chain_id": "chain-b", "ceiling_usd": 10.0}

        r_a = client.post("/simulate", json={"policy": policy_a, "steps": [_CHEAP_STEP]})
        r_b = client.post("/simulate", json={"policy": policy_b, "steps": [_CHEAP_STEP]})

        assert r_a.json()["final_decision"] == "halt"
        assert r_b.json()["final_decision"] == "allow"


class TestSimulateAdversarial:
    def test_zero_ceiling_halts_immediately(self, client: TestClient) -> None:
        policy = {**_BASE_POLICY, "ceiling_usd": 0.0}
        resp = client.post("/simulate", json={"policy": policy, "steps": [_CHEAP_STEP]})
        assert resp.json()["final_decision"] == "halt"

    def test_very_large_cost_step(self, client: TestClient) -> None:
        policy = {**_BASE_POLICY, "ceiling_usd": 1e12}
        step = {"kind": "llm", "cost_usd": 1e11, "tokens_out": 0, "elapsed_ms": 0.0}
        resp = client.post("/simulate", json={"policy": policy, "steps": [step]})
        assert resp.status_code == 200

    def test_many_steps_all_allowed(self, client: TestClient) -> None:
        policy = {**_BASE_POLICY, "ceiling_usd": 1000.0}
        step = {"kind": "llm", "cost_usd": 0.0, "tokens_out": 0, "elapsed_ms": 0.0}
        steps = [step] * 100
        resp = client.post("/simulate", json={"policy": policy, "steps": steps})
        data = resp.json()
        assert data["final_decision"] == "allow"
        assert data["steps_allowed"] == 100
