# src/veronica/pg_store.py
"""PostgreSQL-backed StoreProtocol implementation for VERONICA OS.

Requires the 'postgres' optional-dependency group:
    pip install veronica[postgres]
    # or
    uv add veronica[postgres]

Environment variable:
    VERONICA_DATABASE_URL -- psycopg connection string, e.g.
        postgresql://user:pass@host:5432/dbname

Tables are created automatically on first connection via CREATE TABLE IF NOT EXISTS.
Connection pooling is provided by psycopg_pool.ConnectionPool.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any

try:
    from psycopg_pool import ConnectionPool  # type: ignore[import]
except ImportError:
    ConnectionPool = None  # type: ignore[assignment,misc]

from veronica.types import (
    AnalysisResult,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    HistoryView,
    PolicyConfig,
    StepOutcome,
)

logger = logging.getLogger(__name__)

_DDL_POLICIES = """
CREATE TABLE IF NOT EXISTS veronica_policies (
    chain_id      TEXT PRIMARY KEY,
    config_json   TEXT        NOT NULL,
    version       INTEGER     NOT NULL DEFAULT 1,
    policy_hash   TEXT        NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_DDL_EVENTS = """
CREATE TABLE IF NOT EXISTS veronica_events (
    id            SERIAL      PRIMARY KEY,
    chain_id      TEXT        NOT NULL,
    step_id       TEXT        NOT NULL,
    decision      TEXT        NOT NULL,
    reason        TEXT        NOT NULL DEFAULT '',
    cost_usd      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    tokens_in     INTEGER     NOT NULL DEFAULT 0,
    tokens_out    INTEGER     NOT NULL DEFAULT 0,
    duration_ms   DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    policy_hash   TEXT        NOT NULL DEFAULT '',
    audit_id      TEXT        NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata_json TEXT        NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS veronica_events_chain_id_idx
    ON veronica_events (chain_id);
"""

_MAX_OUTCOMES_PER_CHAIN = 50_000


def _outcome_to_meta(
    outcome: StepOutcome,
    analysis: AnalysisResult,
    cost: CostEstimate,
    desired: DesiredPolicy,
    policy: PolicyConfig,
    meta: DecisionMeta,
) -> dict[str, Any]:
    """Serialize non-column data into metadata_json."""
    return {
        "kind": outcome.kind,
        "status": outcome.status,
        "model": outcome.model,
        "elapsed_ms": outcome.elapsed_ms,
        "risk_level": analysis.risk_level,
        "recommendation": analysis.recommendation,
        "estimated_usd": cost.estimated_usd,
        "confidence": cost.confidence,
        "degraded": meta.degraded,
        "org_denial": meta.org_denial,
    }


class PgStore:
    """PostgreSQL-backed StoreProtocol implementation.

    Thread-safe. Uses psycopg_pool.ConnectionPool for connection reuse.
    Tables are created automatically on first connection.

    Parameters
    ----------
    dsn         : psycopg connection string
    min_size    : minimum pool connections (default 1)
    max_size    : maximum pool connections (default 5)
    """

    def __init__(
        self,
        dsn: str,
        min_size: int = 1,
        max_size: int = 5,
    ) -> None:
        _pool_cls = ConnectionPool
        if _pool_cls is None:
            raise ImportError(
                "PgStore requires psycopg_pool. "
                "Install with: pip install veronica[postgres]"
            )

        self._pool: Any = _pool_cls(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            open=True,
        )
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create tables if they do not exist."""
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL_POLICIES)
                cur.execute(_DDL_EVENTS)
            conn.commit()

    def commit(
        self,
        outcome: StepOutcome,
        analysis: AnalysisResult,
        cost: CostEstimate,
        desired: DesiredPolicy,
        policy: PolicyConfig,
        meta: DecisionMeta,
    ) -> None:
        """Persist a completed step to the events table."""
        metadata = _outcome_to_meta(outcome, analysis, cost, desired, policy, meta)
        cost_usd = outcome.cost_usd if math.isfinite(outcome.cost_usd) else 0.0
        reason = meta.org_denial or analysis.recommendation

        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO veronica_events
                        (chain_id, step_id, decision, reason, cost_usd,
                         tokens_in, tokens_out, duration_ms, policy_hash,
                         audit_id, metadata_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        outcome.chain_id,
                        outcome.step_id,
                        analysis.recommendation,
                        reason,
                        cost_usd,
                        outcome.tokens_in,
                        outcome.tokens_out,
                        outcome.elapsed_ms,
                        meta.policy_hash,
                        meta.audit_id,
                        json.dumps(metadata),
                    ),
                )
            conn.commit()

    def build_history(self, chain_id: str, limit: int = 50) -> HistoryView:
        """Build a HistoryView from the most recent events for chain_id."""
        limit = max(1, min(limit, _MAX_OUTCOMES_PER_CHAIN))

        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT step_id, cost_usd, tokens_in, tokens_out,
                           duration_ms, metadata_json, created_at
                    FROM veronica_events
                    WHERE chain_id = %s
                    ORDER BY id DESC
                    LIMIT %s
                    """,
                    (chain_id, limit),
                )
                rows = cur.fetchall()

        # Rows come back newest-first; reverse to chronological order
        rows = list(reversed(rows))
        outcomes = []
        for row in rows:
            step_id, cost_usd_raw, tokens_in, tokens_out, dur, meta_json, created = row
            meta_data: dict[str, Any] = {}
            try:
                meta_data = json.loads(meta_json) if meta_json else {}
            except (json.JSONDecodeError, TypeError):
                pass
            cost_usd_float = float(cost_usd_raw) if cost_usd_raw is not None else 0.0
            cost_usd = cost_usd_float if math.isfinite(cost_usd_float) else 0.0
            status = meta_data.get("status", "ok")
            if status not in ("ok", "halted", "error", "timeout"):
                status = "error"
            kind = meta_data.get("kind", "system")
            if kind not in ("llm", "tool", "system"):
                kind = "system"
            outcomes.append(
                StepOutcome(
                    step_id=str(step_id),
                    request_id="",
                    chain_id=chain_id,
                    kind=kind,  # type: ignore[arg-type]
                    status=status,  # type: ignore[arg-type]
                    cost_usd=cost_usd,
                    tokens_in=int(tokens_in or 0),
                    tokens_out=int(tokens_out or 0),
                    elapsed_ms=float(dur or 0.0),
                    model=meta_data.get("model"),
                    events=(),
                    timestamp_ms=int(created.timestamp() * 1000) if created else 0,
                )
            )

        last_n = tuple(outcomes)
        rolling_cost = sum(o.cost_usd for o in last_n if math.isfinite(o.cost_usd))

        failure_streak = 0
        for o in reversed(last_n):
            if o.status != "ok":
                failure_streak += 1
            else:
                break

        return HistoryView(
            chain_id=chain_id,
            last_n=last_n,
            rolling_cost_usd=rolling_cost,
            failure_streak=failure_streak,
            depth=len(last_n),
            loop_score=0.0,
        )

    def close(self) -> None:
        """Close the connection pool. Call on application shutdown."""
        try:
            self._pool.close()
        except Exception:
            logger.exception("Error closing PgStore connection pool")
