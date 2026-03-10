# tests/test_replay.py
"""Tests for POST /replay endpoint and ReplayEngine."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from veronica.api.app import create_app
from veronica.ingest.event_ingestor import CPStepOutcomeStore
from veronica.replay.engine import ReplayEngine, _build_override_policy
from veronica.replay.models import DecisionDiff, ReplayRequest, ReplayResult
from veronica.distribution.policy_distributor import PolicyDistributor
from veronica.schemas.events import StepOutcome

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = time.time()
_PAST = _NOW - 3600.0


def _make_outcome(
    step_id: str,
    chain_id: str = "chain-a",
    decision: str = "allow",
    cost_usd: float = 0.01,
    tokens: int = 100,
    ts: float | None = None,
) -> StepOutcome:
    return StepOutcome(
        step_id=step_id,
        chain_id=chain_id,
        operation_name="test-op",
        decision=decision,
        cost_usd=cost_usd,
        tokens=tokens,
        duration_ms=10.0,
        policy_hash="abc123",
        audit_id="audit-" + step_id,
        timestamp=ts if ts is not None else _NOW - 100.0,
    )


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def populated_client() -> TestClient:
    """Client with pre-populated event store."""
    app = create_app()
    with TestClient(app) as c:
        store = app.state.ingestor.store
        store.put_many([
            _make_outcome("step-1", chain_id="chain-a", decision="allow", cost_usd=0.01, ts=_NOW - 200.0),
            _make_outcome("step-2", chain_id="chain-a", decision="allow", cost_usd=0.01, ts=_NOW - 100.0),
            _make_outcome("step-3", chain_id="chain-a", decision="halt", cost_usd=0.05, ts=_NOW - 50.0),
            _make_outcome("step-x", chain_id="chain-b", decision="allow", cost_usd=0.01, ts=_NOW - 100.0),
        ])
        yield c


# ---------------------------------------------------------------------------
# Unit: ReplayEngine
# ---------------------------------------------------------------------------


class TestReplayEngineNoOverride:
    def _engine(self, outcomes: list[StepOutcome]) -> ReplayEngine:
        store = CPStepOutcomeStore()
        store.put_many(outcomes)
        return ReplayEngine(store=store, distributor=PolicyDistributor())

    def test_no_override_decisions_unchanged(self) -> None:
        outcomes = [_make_outcome("s1", decision="allow"), _make_outcome("s2", decision="halt")]
        engine = self._engine(outcomes)
        req = ReplayRequest(chain_id="chain-a", from_timestamp=_NOW - 300.0, to_timestamp=_NOW)
        result = engine.replay(req)
        assert result.event_count == 2
        assert result.changed_count == 0
        for diff in result.diffs:
            assert diff.changed is False
            assert diff.original_decision == diff.replayed_decision

    def test_empty_range_returns_empty_result(self) -> None:
        outcomes = [_make_outcome("s1", ts=_NOW - 1000.0)]
        engine = self._engine(outcomes)
        req = ReplayRequest(chain_id="chain-a", from_timestamp=_NOW - 10.0, to_timestamp=_NOW)
        result = engine.replay(req)
        assert result.event_count == 0
        assert result.diffs == []
        assert result.changed_count == 0

    def test_chain_not_found_returns_empty(self) -> None:
        outcomes = [_make_outcome("s1", chain_id="other")]
        engine = self._engine(outcomes)
        req = ReplayRequest(chain_id="chain-a", from_timestamp=_NOW - 300.0, to_timestamp=_NOW)
        result = engine.replay(req)
        assert result.event_count == 0
        assert "No events found" in result.summary

    def test_store_not_mutated_after_replay(self) -> None:
        outcomes = [_make_outcome("s1")]
        store = CPStepOutcomeStore()
        store.put_many(outcomes)
        engine = ReplayEngine(store=store, distributor=PolicyDistributor())
        req = ReplayRequest(chain_id="chain-a", from_timestamp=_NOW - 300.0, to_timestamp=_NOW)
        engine.replay(req)
        # Store must still have exactly the original record
        assert len(store.snapshot()) == 1

    def test_result_fields_present(self) -> None:
        outcomes = [_make_outcome("s1")]
        engine = self._engine(outcomes)
        req = ReplayRequest(chain_id="chain-a", from_timestamp=_NOW - 300.0, to_timestamp=_NOW)
        result = engine.replay(req)
        assert isinstance(result, ReplayResult)
        assert result.chain_id == "chain-a"
        assert result.event_count == 1
        assert len(result.diffs) == 1
        assert isinstance(result.diffs[0], DecisionDiff)


class TestReplayEngineWithOverride:
    def _engine(self, outcomes: list[StepOutcome]) -> ReplayEngine:
        store = CPStepOutcomeStore()
        store.put_many(outcomes)
        return ReplayEngine(store=store, distributor=PolicyDistributor())

    def _req(self, override: dict) -> ReplayRequest:
        return ReplayRequest(
            chain_id="chain-a",
            from_timestamp=_NOW - 300.0,
            to_timestamp=_NOW,
            override_policy=override,
        )

    def test_stricter_ceiling_halts_expensive_step(self) -> None:
        # Original: allow at 0.01 each. Override: ceiling_usd=0.005 -> halt
        outcomes = [_make_outcome("s1", decision="allow", cost_usd=0.01)]
        engine = self._engine(outcomes)
        req = self._req({"chain_id": "chain-a", "ceiling_usd": 0.005, "on_exceed": "halt"})
        result = engine.replay(req)
        assert result.changed_count == 1
        assert result.diffs[0].replayed_decision == "halt"
        assert result.diffs[0].original_decision == "allow"
        assert result.diffs[0].changed is True

    def test_looser_ceiling_allows_originally_halted(self) -> None:
        # Original: halt. Override: ceiling_usd=100 -> allow
        outcomes = [_make_outcome("s1", decision="halt", cost_usd=0.01)]
        engine = self._engine(outcomes)
        req = self._req({"chain_id": "chain-a", "ceiling_usd": 100.0, "on_exceed": "halt"})
        result = engine.replay(req)
        assert result.changed_count == 1
        assert result.diffs[0].replayed_decision == "allow"
        assert result.diffs[0].original_decision == "halt"

    def test_cumulative_cost_triggers_halt_mid_chain(self) -> None:
        # Two steps each 0.01. Override ceiling=0.015 -> step-2 halts
        outcomes = [
            _make_outcome("s1", decision="allow", cost_usd=0.01, ts=_NOW - 200.0),
            _make_outcome("s2", decision="allow", cost_usd=0.01, ts=_NOW - 100.0),
        ]
        engine = self._engine(outcomes)
        req = self._req({"chain_id": "chain-a", "ceiling_usd": 0.015, "on_exceed": "halt"})
        result = engine.replay(req)
        assert result.diffs[0].replayed_decision == "allow"
        assert result.diffs[1].replayed_decision == "halt"
        assert result.changed_count == 1

    def test_step_ceiling_enforced(self) -> None:
        # Three steps, override ceiling_steps=2
        outcomes = [
            _make_outcome(f"s{i}", decision="allow", cost_usd=0.001, ts=_NOW - (300 - i * 10))
            for i in range(3)
        ]
        engine = self._engine(outcomes)
        req = self._req({
            "chain_id": "chain-a", "ceiling_usd": 100.0,
            "ceiling_steps": 2, "on_exceed": "halt",
        })
        result = engine.replay(req)
        assert result.diffs[0].replayed_decision == "allow"
        assert result.diffs[1].replayed_decision == "allow"
        assert result.diffs[2].replayed_decision == "halt"

    def test_on_exceed_degrade_propagates(self) -> None:
        outcomes = [_make_outcome("s1", decision="allow", cost_usd=0.01)]
        engine = self._engine(outcomes)
        req = self._req({"chain_id": "chain-a", "ceiling_usd": 0.005, "on_exceed": "degrade"})
        result = engine.replay(req)
        assert result.diffs[0].replayed_decision == "degrade"

    def test_summary_mentions_chain_id(self) -> None:
        outcomes = [_make_outcome("s1")]
        engine = self._engine(outcomes)
        req = self._req({"chain_id": "chain-a", "ceiling_usd": 100.0, "on_exceed": "halt"})
        result = engine.replay(req)
        assert "chain-a" in result.summary

    def test_invalid_override_policy_raises_value_error(self) -> None:
        outcomes = [_make_outcome("s1")]
        engine = self._engine(outcomes)
        req = ReplayRequest(
            chain_id="chain-a",
            from_timestamp=_NOW - 300.0,
            to_timestamp=_NOW,
            override_policy={"ceiling_usd": 1.0},  # missing chain_id
        )
        with pytest.raises(ValueError, match="Invalid override_policy"):
            engine.replay(req)

    def test_override_missing_ceiling_usd_raises(self) -> None:
        with pytest.raises(ValueError, match="ceiling_usd"):
            _build_override_policy({"chain_id": "x", "on_exceed": "halt"})


# ---------------------------------------------------------------------------
# API: POST /replay
# ---------------------------------------------------------------------------


class TestReplayAPIBasic:
    def test_empty_store_returns_200_with_empty_result(self, client: TestClient) -> None:
        resp = client.post("/replay", json={
            "chain_id": "chain-x",
            "from_timestamp": _NOW - 100.0,
            "to_timestamp": _NOW,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["event_count"] == 0
        assert data["diffs"] == []
        assert data["changed_count"] == 0

    def test_response_shape(self, client: TestClient) -> None:
        resp = client.post("/replay", json={
            "chain_id": "chain-x",
            "from_timestamp": _NOW - 100.0,
            "to_timestamp": _NOW,
        })
        data = resp.json()
        required = {"chain_id", "event_count", "diffs", "changed_count", "summary", "store_unchanged"}
        assert required.issubset(data.keys())

    def test_store_unchanged_is_true(self, client: TestClient) -> None:
        resp = client.post("/replay", json={
            "chain_id": "chain-x",
            "from_timestamp": _NOW - 100.0,
            "to_timestamp": _NOW,
        })
        assert resp.json()["store_unchanged"] is True

    def test_populated_chain_returns_events(self, populated_client: TestClient) -> None:
        resp = populated_client.post("/replay", json={
            "chain_id": "chain-a",
            "from_timestamp": _NOW - 300.0,
            "to_timestamp": _NOW,
        })
        assert resp.status_code == 200
        assert resp.json()["event_count"] == 3

    def test_chain_isolation(self, populated_client: TestClient) -> None:
        resp = populated_client.post("/replay", json={
            "chain_id": "chain-b",
            "from_timestamp": _NOW - 300.0,
            "to_timestamp": _NOW,
        })
        assert resp.json()["event_count"] == 1

    def test_time_range_filtering(self, populated_client: TestClient) -> None:
        # Only step-3 (ts=_NOW-50) is within this range
        resp = populated_client.post("/replay", json={
            "chain_id": "chain-a",
            "from_timestamp": _NOW - 80.0,
            "to_timestamp": _NOW,
        })
        assert resp.json()["event_count"] == 1

    def test_no_override_all_unchanged(self, populated_client: TestClient) -> None:
        resp = populated_client.post("/replay", json={
            "chain_id": "chain-a",
            "from_timestamp": _NOW - 300.0,
            "to_timestamp": _NOW,
        })
        data = resp.json()
        assert data["changed_count"] == 0
        for d in data["diffs"]:
            assert d["changed"] is False

    def test_override_stricter_changes_decisions(self, populated_client: TestClient) -> None:
        # ceiling_usd=0.005 -> first step (0.01) already exceeds
        resp = populated_client.post("/replay", json={
            "chain_id": "chain-a",
            "from_timestamp": _NOW - 300.0,
            "to_timestamp": _NOW,
            "override_policy": {"chain_id": "chain-a", "ceiling_usd": 0.005, "on_exceed": "halt"},
        })
        data = resp.json()
        assert data["changed_count"] > 0

    def test_override_looser_ceiling_no_changes_if_all_allowed(self, populated_client: TestClient) -> None:
        # Very large ceiling: all steps already within budget -> no changes for "allow" steps
        resp = populated_client.post("/replay", json={
            "chain_id": "chain-a",
            "from_timestamp": _NOW - 300.0,
            "to_timestamp": _NOW,
            "override_policy": {"chain_id": "chain-a", "ceiling_usd": 9999.0, "on_exceed": "halt"},
        })
        data = resp.json()
        # Original "halt" step would now be "allow" -> changed
        assert data["changed_count"] >= 1


class TestReplayAPIValidation:
    def test_missing_chain_id_returns_422(self, client: TestClient) -> None:
        resp = client.post("/replay", json={
            "from_timestamp": _NOW - 100.0,
            "to_timestamp": _NOW,
        })
        assert resp.status_code == 422

    def test_from_equals_to_returns_422(self, client: TestClient) -> None:
        resp = client.post("/replay", json={
            "chain_id": "c",
            "from_timestamp": _NOW,
            "to_timestamp": _NOW,
        })
        assert resp.status_code == 422

    def test_from_after_to_returns_422(self, client: TestClient) -> None:
        resp = client.post("/replay", json={
            "chain_id": "c",
            "from_timestamp": _NOW,
            "to_timestamp": _NOW - 1.0,
        })
        assert resp.status_code == 422

    def test_negative_from_timestamp_returns_422(self, client: TestClient) -> None:
        resp = client.post("/replay", json={
            "chain_id": "c",
            "from_timestamp": -1.0,
            "to_timestamp": _NOW,
        })
        assert resp.status_code == 422

    def test_invalid_override_missing_ceiling_usd_returns_422(self, client: TestClient) -> None:
        resp = client.post("/replay", json={
            "chain_id": "c",
            "from_timestamp": _NOW - 100.0,
            "to_timestamp": _NOW,
            "override_policy": {"chain_id": "c", "on_exceed": "halt"},
        })
        assert resp.status_code == 422

    def test_invalid_override_bad_on_exceed_returns_422(self, client: TestClient) -> None:
        resp = client.post("/replay", json={
            "chain_id": "c",
            "from_timestamp": _NOW - 100.0,
            "to_timestamp": _NOW,
            "override_policy": {"chain_id": "c", "ceiling_usd": 1.0, "on_exceed": "explode"},
        })
        assert resp.status_code == 422

    def test_empty_chain_id_returns_422(self, client: TestClient) -> None:
        resp = client.post("/replay", json={
            "chain_id": "",
            "from_timestamp": _NOW - 100.0,
            "to_timestamp": _NOW,
        })
        assert resp.status_code == 422


class TestReplayAPISideEffects:
    def test_store_not_modified_after_replay(self) -> None:
        app = create_app()
        with TestClient(app) as c:
            store = app.state.ingestor.store
            store.put_many([_make_outcome("s1")])
            before = len(store.snapshot())
            c.post("/replay", json={
                "chain_id": "chain-a",
                "from_timestamp": _NOW - 300.0,
                "to_timestamp": _NOW,
            })
            after = len(store.snapshot())
            assert before == after

    def test_multiple_replays_independent(self) -> None:
        app = create_app()
        with TestClient(app) as c:
            store = app.state.ingestor.store
            store.put_many([_make_outcome("s1", cost_usd=0.10)])

            r1 = c.post("/replay", json={
                "chain_id": "chain-a",
                "from_timestamp": _NOW - 300.0,
                "to_timestamp": _NOW,
                "override_policy": {"chain_id": "chain-a", "ceiling_usd": 0.05, "on_exceed": "halt"},
            })
            r2 = c.post("/replay", json={
                "chain_id": "chain-a",
                "from_timestamp": _NOW - 300.0,
                "to_timestamp": _NOW,
                "override_policy": {"chain_id": "chain-a", "ceiling_usd": 100.0, "on_exceed": "halt"},
            })

            assert r1.json()["diffs"][0]["replayed_decision"] == "halt"
            assert r2.json()["diffs"][0]["replayed_decision"] == "allow"
