# tests/test_protocols.py
"""Tests for veronica.protocols -- structural subtyping checks."""
from __future__ import annotations

from veronica.protocols import (
    AnalyzerProtocol,
    ArbiterProtocol,
    CollectorProtocol,
    CostModelProtocol,
    EventEmitterProtocol,
    PlannerProtocol,
    StoreProtocol,
)


class _FakeCollector:
    def collect(self, snapshot):
        return None


class _FakeAnalyzer:
    def analyze(self, intent, outcome, history):
        return None


class _FakeCostModel:
    def estimate(self, intent, history, last_analysis):
        return None


class _FakePlanner:
    def plan(self, analysis, cost, budget):
        return None


class _FakeArbiter:
    def arbitrate(self, desires, budget_remaining_usd):
        return {}


class _FakeStore:
    def commit(self, outcome, analysis, cost, desired, policy, meta):
        pass

    def build_history(self, chain_id, limit=50):
        return None


class _FakeEmitter:
    def emit(self, event_type, payload):
        pass


def test_collector_structural() -> None:
    assert isinstance(_FakeCollector(), CollectorProtocol)


def test_analyzer_structural() -> None:
    assert isinstance(_FakeAnalyzer(), AnalyzerProtocol)


def test_cost_model_structural() -> None:
    assert isinstance(_FakeCostModel(), CostModelProtocol)


def test_planner_structural() -> None:
    assert isinstance(_FakePlanner(), PlannerProtocol)


def test_arbiter_structural() -> None:
    assert isinstance(_FakeArbiter(), ArbiterProtocol)


def test_store_structural() -> None:
    assert isinstance(_FakeStore(), StoreProtocol)


def test_emitter_structural() -> None:
    assert isinstance(_FakeEmitter(), EventEmitterProtocol)
