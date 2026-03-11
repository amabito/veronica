# tests/test_pg_store.py
"""Tests for PgStore -- uses unittest.mock to avoid a real PostgreSQL connection.

Covers:
- Store protocol compliance (commit + build_history)
- Connection failure handling (graceful ImportError fallback)
- Schema validation (DDL sent on init)
- build_history ordering, failure_streak, cost aggregation
"""

from __future__ import annotations

import json
import math
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from veronica.types import (
    AnalysisResult,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    PolicyConfig,
    Signal,
    StepOutcome,
)


# ---------------------------------------------------------------------------
# Helpers: domain objects
# ---------------------------------------------------------------------------


def _make_outcome(
    chain_id: str = "chain-1",
    step_id: str = "step-1",
    status: str = "ok",
    cost_usd: float = 0.01,
    tokens_in: int = 100,
    tokens_out: int = 50,
) -> StepOutcome:
    return StepOutcome(
        step_id=step_id,
        request_id="req-1",
        chain_id=chain_id,
        kind="llm",
        status=status,  # type: ignore[arg-type]
        cost_usd=cost_usd,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        elapsed_ms=123.4,
        model="gpt-5",
        events=(),
        timestamp_ms=1700000000000,
    )


def _make_analysis(risk: str = "nominal", rec: str = "continue") -> AnalysisResult:
    return AnalysisResult(
        signals=(Signal(kind="ok", severity="info", detail=""),),
        risk_level=risk,  # type: ignore[arg-type]
        recommendation=rec,  # type: ignore[arg-type]
    )


def _make_cost() -> CostEstimate:
    return CostEstimate(
        estimated_usd=0.01, confidence=0.9, model_used="gpt-5", basis="pricing_table"
    )


def _make_desired(chain_id: str = "chain-1") -> DesiredPolicy:
    return DesiredPolicy(
        chain_id=chain_id,
        ceiling_usd=1.0,
        ceiling_steps=100,
        ceiling_tokens_out=10000,
        on_exceed="halt",
        fallback_model=None,
        timeout_ms=30000,
        priority=50,
    )


def _make_policy(chain_id: str = "chain-1") -> PolicyConfig:
    return PolicyConfig(
        chain_id=chain_id,
        ceiling_usd=1.0,
        on_exceed="halt",
        issued_at=1700000000.0,
    )


def _make_meta() -> DecisionMeta:
    return DecisionMeta(
        risk_level="nominal",
        recommendation="continue",
        degraded=False,
        stage_time_ms={},
        policy_hash="a" * 64,
        audit_id="audit-abc",
    )


def _make_row(
    step_id: str = "step-1",
    cost_usd: float = 0.01,
    tokens_in: int = 10,
    tokens_out: int = 5,
    dur: float = 100.0,
    meta: dict | None = None,
    status: str = "ok",
    kind: str = "llm",
) -> tuple:
    m = meta or {"status": status, "kind": kind}
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    return (step_id, cost_usd, tokens_in, tokens_out, dur, json.dumps(m), ts)


# ---------------------------------------------------------------------------
# Mock pool factory
# ---------------------------------------------------------------------------


class _MockPool:
    """Minimal mock that mirrors psycopg_pool.ConnectionPool context manager API.

    Keeps a single cursor shared across all connections so tests can assert
    on cursor.execute call counts without complex mock navigation.
    """

    def __init__(self, rows: list[Any] | None = None) -> None:
        self.cursor = MagicMock()
        self.cursor.fetchall.return_value = rows or []
        self._closed = False

    @contextmanager
    def connection(self):  # type: ignore[return]
        conn = _MockConn(self.cursor)
        yield conn

    def close(self) -> None:
        self._closed = True


class _MockConn:
    """Mock psycopg connection supporting both conn.execute() and conn.cursor() APIs."""

    def __init__(self, cursor: MagicMock) -> None:
        self._cursor = cursor

    def commit(self) -> None:
        pass

    def execute(self, *args: Any, **kwargs: Any) -> None:
        self._cursor.execute(*args, **kwargs)

    def fetchall(self) -> list:
        return self._cursor.fetchall()

    @contextmanager
    def cursor(self):  # type: ignore[return]
        yield self._cursor


def _make_pg_store(rows: list[Any] | None = None):
    """Return a (PgStore, MockPool) backed by an in-process mock (no DB needed)."""
    from veronica.pg_store import PgStore

    pool = _MockPool(rows)
    store = PgStore.__new__(PgStore)
    store._pool = pool
    store._ensure_schema()
    return store, pool


# ---------------------------------------------------------------------------
# Tests: ImportError / missing psycopg_pool
# ---------------------------------------------------------------------------


class TestPgStoreImportError:
    def test_raises_import_error_when_psycopg_pool_missing(self) -> None:
        """PgStore must raise ImportError if psycopg_pool is not installed."""
        import veronica.pg_store as pg_mod

        original = pg_mod.ConnectionPool
        try:
            pg_mod.ConnectionPool = None  # type: ignore[assignment]
            from veronica.pg_store import PgStore

            with pytest.raises(ImportError, match="psycopg_pool"):
                PgStore(dsn="postgresql://localhost/test")
        finally:
            pg_mod.ConnectionPool = original


# ---------------------------------------------------------------------------
# Tests: Schema creation
# ---------------------------------------------------------------------------


class TestPgStoreSchema:
    def test_ensure_schema_executes_create_table(self) -> None:
        """_ensure_schema must execute DDL for both tables."""
        store, pool = _make_pg_store()
        cur = pool.cursor
        # At least 2 execute calls (policies DDL + events DDL)
        assert cur.execute.call_count >= 2
        calls = [str(c) for c in cur.execute.call_args_list]
        assert any("CREATE TABLE" in c for c in calls)


# ---------------------------------------------------------------------------
# Tests: commit
# ---------------------------------------------------------------------------


class TestPgStoreCommit:
    def test_commit_inserts_row(self) -> None:
        """commit() must INSERT a row into veronica_events."""
        store, pool = _make_pg_store()
        pool.cursor.execute.reset_mock()

        store.commit(
            _make_outcome(),
            _make_analysis(),
            _make_cost(),
            _make_desired(),
            _make_policy(),
            _make_meta(),
        )

        calls = [str(c) for c in pool.cursor.execute.call_args_list]
        assert any("INSERT" in c for c in calls)

    def test_commit_stores_chain_id_and_step_id(self) -> None:
        """commit() INSERT must include chain_id and step_id in params."""
        store, pool = _make_pg_store()
        pool.cursor.execute.reset_mock()

        store.commit(
            _make_outcome(chain_id="test-chain", step_id="test-step"),
            _make_analysis(),
            _make_cost(),
            _make_desired(),
            _make_policy(),
            _make_meta(),
        )

        found = False
        for call in pool.cursor.execute.call_args_list:
            args = call[0]
            if len(args) >= 2 and "INSERT" in str(args[0]):
                params = args[1]
                assert "test-chain" in params
                assert "test-step" in params
                found = True
                break
        assert found, "No INSERT call found"

    def test_commit_sanitizes_nan_cost_to_zero(self) -> None:
        """commit() must replace non-finite cost_usd with 0.0."""
        store, pool = _make_pg_store()
        pool.cursor.execute.reset_mock()

        store.commit(
            _make_outcome(cost_usd=float("nan")),
            _make_analysis(),
            _make_cost(),
            _make_desired(),
            _make_policy(),
            _make_meta(),
        )

        for call in pool.cursor.execute.call_args_list:
            args = call[0]
            if len(args) >= 2 and "INSERT" in str(args[0]):
                params = args[1]
                # index 4: chain_id(0), step_id(1), decision(2), reason(3), cost_usd(4)
                cost_idx = 4
                assert math.isfinite(params[cost_idx])
                assert params[cost_idx] == 0.0
                break


# ---------------------------------------------------------------------------
# Tests: build_history
# ---------------------------------------------------------------------------


class TestPgStoreBuildHistory:
    def test_build_history_empty_chain(self) -> None:
        """build_history with no rows returns empty HistoryView."""
        store, _ = _make_pg_store(rows=[])
        view = store.build_history("chain-x", limit=10)
        assert view.chain_id == "chain-x"
        assert len(view.last_n) == 0
        assert view.rolling_cost_usd == 0.0
        assert view.failure_streak == 0
        assert view.depth == 0

    def test_build_history_returns_chronological_order(self) -> None:
        """build_history must return rows oldest-first (DB returns newest-first)."""
        rows = [
            _make_row("step-3", cost_usd=0.03),
            _make_row("step-2", cost_usd=0.02),
            _make_row("step-1", cost_usd=0.01),
        ]
        store, pool = _make_pg_store(rows=rows)

        view = store.build_history("chain-1", limit=10)
        step_ids = [o.step_id for o in view.last_n]
        assert step_ids == ["step-1", "step-2", "step-3"]

    def test_build_history_rolling_cost(self) -> None:
        """rolling_cost_usd must sum cost_usd across all rows."""
        rows = [
            _make_row("s1", cost_usd=0.10),
            _make_row("s2", cost_usd=0.20),
            _make_row("s3", cost_usd=0.30),
        ]
        store, _ = _make_pg_store(rows=rows)
        view = store.build_history("chain-1")
        assert abs(view.rolling_cost_usd - 0.60) < 1e-9

    def test_build_history_failure_streak(self) -> None:
        """failure_streak counts consecutive trailing non-ok statuses (newest-first from DB)."""
        # DB returns newest-first: reversed = ok, halted, error (oldest to newest)
        rows = [
            _make_row("s3", status="error"),
            _make_row("s2", status="halted"),
            _make_row("s1", status="ok"),
        ]
        store, _ = _make_pg_store(rows=rows)
        view = store.build_history("chain-1")
        # last 2 in chronological order are halted, error -> streak 2
        assert view.failure_streak == 2

    def test_build_history_no_failure_streak_on_all_ok(self) -> None:
        """failure_streak is 0 when all steps are ok."""
        rows = [_make_row("s1", status="ok"), _make_row("s2", status="ok")]
        store, _ = _make_pg_store(rows=rows)
        view = store.build_history("chain-1")
        assert view.failure_streak == 0

    def test_build_history_invalid_status_coerces_to_error(self) -> None:
        """Unknown status values must coerce to 'error'."""
        rows = [_make_row("s1", meta={"status": "UNKNOWN", "kind": "llm"})]
        store, _ = _make_pg_store(rows=rows)
        view = store.build_history("chain-1")
        assert view.last_n[0].status == "error"

    def test_build_history_invalid_kind_coerces_to_system(self) -> None:
        """Unknown kind values must coerce to 'system'."""
        rows = [_make_row("s1", meta={"status": "ok", "kind": "INVALID"})]
        store, _ = _make_pg_store(rows=rows)
        view = store.build_history("chain-1")
        assert view.last_n[0].kind == "system"

    def test_build_history_nan_cost_excluded_from_rolling(self) -> None:
        """NaN cost_usd must not corrupt rolling_cost_usd."""
        rows = [
            _make_row("s1", cost_usd=0.10),
            _make_row("s2", cost_usd=float("nan")),
        ]
        store, _ = _make_pg_store(rows=rows)
        view = store.build_history("chain-1")
        assert math.isfinite(view.rolling_cost_usd)


# ---------------------------------------------------------------------------
# Tests: close()
# ---------------------------------------------------------------------------


class TestPgStoreClose:
    def test_close_marks_pool_closed(self) -> None:
        """close() must call pool.close()."""
        store, pool = _make_pg_store()
        store.close()
        assert pool._closed is True

    def test_close_swallows_exceptions(self) -> None:
        """close() must not raise even if pool.close() throws."""
        store, pool = _make_pg_store()

        original_close = pool.close

        def bad_close() -> None:
            raise RuntimeError("pool already dead")

        pool.close = bad_close
        store.close()  # must not propagate
        pool.close = original_close


# ---------------------------------------------------------------------------
# Tests: Store protocol compliance
# ---------------------------------------------------------------------------


class TestPgStoreProtocol:
    def test_implements_store_protocol(self) -> None:
        """PgStore must satisfy StoreProtocol (runtime_checkable)."""
        from veronica.protocols import StoreProtocol

        store, _ = _make_pg_store()
        assert isinstance(store, StoreProtocol)
