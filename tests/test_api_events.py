# tests/test_api_events.py
"""Tests for GET /events endpoint."""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app
from veronica.ingest.event_ingestor import CPStepOutcome


def _make_outcome(**kwargs: object) -> CPStepOutcome:
    """Build a CPStepOutcome with sensible defaults, overriding with kwargs."""
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
    """Context manager that yields (client, ingestor_store) with lifespan active."""
    app = create_app()
    with TestClient(app) as c:
        store = app.state.ingestor.store
        yield c, store


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    with _running_client() as (c, _):
        yield c


@pytest.fixture()
def populated_client() -> Generator[TestClient, None, None]:
    with _running_client() as (c, store):
        store.put(
            _make_outcome(
                step_id="s1", chain_id="chain-a", decision="allow", timestamp=1000.0
            )
        )
        store.put(
            _make_outcome(
                step_id="s2", chain_id="chain-b", decision="halt", timestamp=2000.0
            )
        )
        store.put(
            _make_outcome(
                step_id="s3", chain_id="chain-a", decision="degrade", timestamp=3000.0
            )
        )
        store.put(
            _make_outcome(
                step_id="s4", chain_id="chain-b", decision="allow", timestamp=4000.0
            )
        )
        yield c


class TestEventsEndpointBasic:
    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/events")
        assert resp.status_code == 200

    def test_empty_store_returns_empty_list(self, client: TestClient) -> None:
        resp = client.get("/events")
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_response_shape(self, client: TestClient) -> None:
        data = client.get("/events").json()
        assert "items" in data
        assert "total" in data
        assert "offset" in data
        assert "limit" in data

    def test_default_offset(self, client: TestClient) -> None:
        data = client.get("/events").json()
        assert data["offset"] == 0


class TestEventsFiltering:
    def test_filter_by_chain_id(self, populated_client: TestClient) -> None:
        resp = populated_client.get("/events", params={"chain_id": "chain-a"})
        data = resp.json()
        assert data["total"] == 2
        assert all(item["chain_id"] == "chain-a" for item in data["items"])

    def test_filter_by_decision(self, populated_client: TestClient) -> None:
        resp = populated_client.get("/events", params={"decision": "allow"})
        data = resp.json()
        assert data["total"] == 2
        assert all(item["decision"] == "allow" for item in data["items"])

    def test_filter_by_since(self, populated_client: TestClient) -> None:
        resp = populated_client.get("/events", params={"since": 2500.0})
        data = resp.json()
        assert data["total"] == 2
        assert all(item["timestamp"] >= 2500.0 for item in data["items"])

    def test_filter_by_until(self, populated_client: TestClient) -> None:
        resp = populated_client.get("/events", params={"until": 2000.0})
        data = resp.json()
        assert data["total"] == 2
        assert all(item["timestamp"] <= 2000.0 for item in data["items"])

    def test_combined_filters(self, populated_client: TestClient) -> None:
        resp = populated_client.get(
            "/events", params={"chain_id": "chain-a", "since": 2000.0}
        )
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["step_id"] == "s3"

    def test_filter_by_policy_hash(self, populated_client: TestClient) -> None:
        resp = populated_client.get("/events", params={"policy_hash": "abc123"})
        data = resp.json()
        assert data["total"] == 4
        assert all(item["policy_hash"] == "abc123" for item in data["items"])

    def test_filter_by_policy_hash_no_match(self, populated_client: TestClient) -> None:
        resp = populated_client.get("/events", params={"policy_hash": "deadbeef"})
        data = resp.json()
        assert data["total"] == 0

    def test_filter_by_multiple_decisions(self, populated_client: TestClient) -> None:
        resp = populated_client.get(
            "/events", params=[("decision", "halt"), ("decision", "degrade")]
        )
        data = resp.json()
        assert data["total"] == 2
        assert all(item["decision"] in {"halt", "degrade"} for item in data["items"])

    def test_invalid_decision_returns_400(self, populated_client: TestClient) -> None:
        resp = populated_client.get("/events", params={"decision": "invalid"})
        assert resp.status_code == 400
        assert "detail" in resp.json()

    def test_one_valid_one_invalid_decision_returns_400(
        self, populated_client: TestClient
    ) -> None:
        resp = populated_client.get(
            "/events", params=[("decision", "allow"), ("decision", "bogus")]
        )
        assert resp.status_code == 400


class TestEventsPagination:
    def test_limit_parameter(self, populated_client: TestClient) -> None:
        resp = populated_client.get("/events", params={"limit": 2})
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 4
        assert data["limit"] == 2

    def test_offset_parameter(self, populated_client: TestClient) -> None:
        resp = populated_client.get("/events", params={"offset": 2})
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["offset"] == 2

    def test_offset_beyond_total(self, populated_client: TestClient) -> None:
        resp = populated_client.get("/events", params={"offset": 100})
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 4

    def test_negative_offset_rejected(self, client: TestClient) -> None:
        resp = client.get("/events", params={"offset": -1})
        assert resp.status_code == 422

    def test_zero_limit_rejected(self, client: TestClient) -> None:
        resp = client.get("/events", params={"limit": 0})
        assert resp.status_code == 422


class TestEventsSorting:
    def test_sorted_newest_first(self, populated_client: TestClient) -> None:
        resp = populated_client.get("/events")
        items = resp.json()["items"]
        timestamps = [item["timestamp"] for item in items]
        assert timestamps == sorted(timestamps, reverse=True)


class TestEventsItemSchema:
    def test_item_has_all_fields(self, populated_client: TestClient) -> None:
        resp = populated_client.get("/events", params={"limit": 1})
        item = resp.json()["items"][0]
        required = {
            "step_id",
            "chain_id",
            "operation_name",
            "decision",
            "cost_usd",
            "tokens",
            "duration_ms",
            "policy_hash",
            "audit_id",
            "timestamp",
        }
        assert required.issubset(item.keys())


class TestEventsAuth:
    def test_requires_auth_when_key_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERONICA_API_KEY", "secret")
        app = create_app()
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/events")
        assert resp.status_code == 401

    def test_passes_with_correct_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VERONICA_API_KEY", "secret")
        app = create_app()
        with TestClient(app) as c:
            resp = c.get("/events", headers={"X-Veronica-Key": "secret"})
            assert resp.status_code == 200
