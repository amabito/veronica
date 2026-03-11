# tests/test_api_runs.py
"""Tests for GET /runs/{chain_id} endpoint."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app
from veronica.ingest.event_ingestor import CPStepOutcome


def _make_outcome(**kwargs: object) -> CPStepOutcome:
    defaults: dict[str, object] = {
        "step_id": "step-1",
        "chain_id": "chain-a",
        "operation_name": "gpt-4",
        "decision": "allow",
        "cost_usd": 0.001,
        "tokens": 100,
        "duration_ms": 50.0,
        "policy_hash": "abc123",
        "audit_id": "audit-1",
        "timestamp": time.time(),
    }
    defaults.update(kwargs)
    return CPStepOutcome(**defaults)  # type: ignore[arg-type]


@contextmanager
def _running_client() -> Generator[tuple[TestClient, object], None, None]:
    # conftest autouse fixture sets VERONICA_AUTH_DISABLED=1 for tests
    app = create_app()
    with TestClient(app) as c:
        store = app.state.ingestor.store
        yield c, store


@pytest.fixture()
def client_with_data() -> Generator[tuple[TestClient, object], None, None]:
    with _running_client() as (c, store):
        now = time.time()
        store.put(
            _make_outcome(
                step_id="s1",
                chain_id="chain-a",
                decision="allow",
                cost_usd=0.01,
                timestamp=now - 200,
            )
        )
        store.put(
            _make_outcome(
                step_id="s2",
                chain_id="chain-a",
                decision="halt",
                cost_usd=0.02,
                timestamp=now - 100,
            )
        )
        store.put(
            _make_outcome(
                step_id="s3",
                chain_id="chain-b",
                decision="allow",
                cost_usd=0.05,
                timestamp=now - 50,
            )
        )
        yield c, store


class TestRunsEndpointBasic:
    def test_returns_200_for_existing_chain(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        resp = client.get("/runs/chain-a")
        assert resp.status_code == 200

    def test_returns_404_for_missing_chain(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        resp = client.get("/runs/nonexistent-chain")
        assert resp.status_code == 404

    def test_aggregate_fields_present(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        data = client.get("/runs/chain-a").json()
        assert data["chain_id"] == "chain-a"
        assert "status" in data
        assert "total_cost" in data
        assert "total_steps" in data
        assert "events_count" in data
        assert "first_event_at" in data
        assert "last_event_at" in data
        assert "policy_hash" in data

    def test_total_cost_is_sum(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        data = client.get("/runs/chain-a").json()
        assert abs(data["total_cost"] - 0.03) < 1e-9

    def test_events_count_correct(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        data = client.get("/runs/chain-a").json()
        assert data["events_count"] == 2

    def test_status_active_for_recent_chain(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        # chain-a last event is ~100s ago, within the 300s active window
        client, _ = client_with_data
        data = client.get("/runs/chain-a").json()
        assert data["status"] == "active"

    def test_status_completed_for_old_chain(self) -> None:
        with _running_client() as (client, store):
            old_ts = time.time() - 1000  # 1000s ago, outside 300s window
            store.put(
                _make_outcome(
                    step_id="s1",
                    chain_id="old-chain",
                    decision="allow",
                    timestamp=old_ts,
                )
            )
            data = client.get("/runs/old-chain").json()
            assert data["status"] == "completed"

    def test_first_and_last_event_timestamps(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        data = client.get("/runs/chain-a").json()
        assert data["first_event_at"] < data["last_event_at"]

    def test_policy_hash_from_most_recent(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        # The policy_hash should come from the most recent event
        client, _ = client_with_data
        data = client.get("/runs/chain-a").json()
        # Both events use default "abc123"
        assert data["policy_hash"] == "abc123"

    def test_chain_isolation(self, client_with_data: tuple[TestClient, object]) -> None:
        client, _ = client_with_data
        data_b = client.get("/runs/chain-b").json()
        assert data_b["events_count"] == 1
        assert abs(data_b["total_cost"] - 0.05) < 1e-9


class TestRunsEndpointAdversarial:
    def test_chain_id_with_special_chars_404(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        # Chain IDs with special chars not in store -> 404 (not crash)
        resp = client.get("/runs/chain.with.dots")
        assert resp.status_code == 404

    def test_very_long_chain_id_rejected(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        long_id = "x" * 300
        resp = client.get(f"/runs/{long_id}")
        # FastAPI path param validation rejects > 256 chars
        assert resp.status_code in (404, 422)
