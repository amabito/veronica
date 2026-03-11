# tests/test_api_incidents.py
"""Tests for GET /incidents/{chain_id}/{step_id} endpoint."""

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
                timestamp=now - 300,
                audit_id="audit-1",
            )
        )
        store.put(
            _make_outcome(
                step_id="s2",
                chain_id="chain-a",
                decision="halt",
                timestamp=now - 200,
                audit_id="audit-2",
            )
        )
        store.put(
            _make_outcome(
                step_id="s3",
                chain_id="chain-a",
                decision="degrade",
                timestamp=now - 100,
                audit_id="audit-3",
            )
        )
        store.put(
            _make_outcome(
                step_id="s4",
                chain_id="chain-b",
                decision="allow",
                timestamp=now - 50,
                audit_id="audit-4",
            )
        )
        yield c, store


class TestIncidentsEndpointBasic:
    def test_returns_200_for_existing_step(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        resp = client.get("/incidents/chain-a/s2")
        assert resp.status_code == 200

    def test_returns_404_for_missing_chain(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        resp = client.get("/incidents/nope/s1")
        assert resp.status_code == 404

    def test_returns_404_for_missing_step(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        resp = client.get("/incidents/chain-a/nonexistent")
        assert resp.status_code == 404

    def test_response_fields_present(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        data = client.get("/incidents/chain-a/s2").json()
        assert data["incident_id"] == "chain-a:s2"
        assert data["chain_id"] == "chain-a"
        assert data["step_id"] == "s2"
        assert data["decision"] == "halt"
        assert "timeline" in data
        assert "root_cause" in data
        assert "audit_id" in data
        assert "policy_hash" in data

    def test_timeline_contains_all_chain_events(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        data = client.get("/incidents/chain-a/s2").json()
        # chain-a has 3 events
        assert len(data["timeline"]) == 3

    def test_timeline_sorted_ascending(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        data = client.get("/incidents/chain-a/s2").json()
        timestamps = [e["timestamp"] for e in data["timeline"]]
        assert timestamps == sorted(timestamps)

    def test_root_cause_is_first_halt(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        data = client.get("/incidents/chain-a/s3").json()
        assert data["root_cause"] is not None
        assert data["root_cause"]["step_id"] == "s2"  # halt comes before degrade
        assert data["root_cause"]["decision"] == "halt"

    def test_root_cause_none_for_allow_only_chain(self) -> None:
        with _running_client() as (client, store):
            store.put(
                _make_outcome(step_id="a1", chain_id="clean-chain", decision="allow")
            )
            data = client.get("/incidents/clean-chain/a1").json()
            assert data["root_cause"] is None

    def test_timeline_isolated_per_chain(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        data = client.get("/incidents/chain-b/s4").json()
        assert len(data["timeline"]) == 1
        assert data["timeline"][0]["step_id"] == "s4"

    def test_reason_code_is_none(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        data = client.get("/incidents/chain-a/s1").json()
        assert data["reason_code"] is None


class TestIncidentsEndpointAdversarial:
    def test_step_from_different_chain_not_found(
        self, client_with_data: tuple[TestClient, object]
    ) -> None:
        client, _ = client_with_data
        # s4 exists but belongs to chain-b, not chain-a
        resp = client.get("/incidents/chain-a/s4")
        assert resp.status_code == 404

    def test_concurrent_reads_stable(self) -> None:
        import threading

        with _running_client() as (client, store):
            store.put(
                _make_outcome(step_id="t1", chain_id="race-chain", decision="halt")
            )
            results = []

            def read():
                r = client.get("/incidents/race-chain/t1")
                results.append(r.status_code)

            threads = [threading.Thread(target=read) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert all(s == 200 for s in results)
