# src/veronica/file_store.py
"""VERONICA OS file store -- JSONL persistence with EMA computation."""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

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

_EMA_ALPHA = 0.3
_EPS = 1e-12
_MAX_CHAINS = 10_000
_SAFE_CHAIN_RE = re.compile(r"^[a-zA-Z0-9_:@.\-]{1,256}$")


def _ema(prev: float, current: float, alpha: float = _EMA_ALPHA) -> float:
    if prev < _EPS:
        return current
    return alpha * current + (1 - alpha) * prev


class _ChainStats:
    __slots__ = (
        "cost_ema",
        "cost_ema_by_model",
        "latency_ema_by_model",
        "success_streak",
        "failure_streak",
        "total_commits",
        "budget_headroom_ratio",
        "commits_since_rotate",
    )

    def __init__(self) -> None:
        self.cost_ema: float = 0.0
        self.cost_ema_by_model: dict[str, float] = {}
        self.latency_ema_by_model: dict[str, float] = {}
        self.success_streak: int = 0
        self.failure_streak: int = 0
        self.total_commits: int = 0
        self.budget_headroom_ratio: float = 1.0
        self.commits_since_rotate: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "cost_ema": self.cost_ema,
            "cost_ema_by_model": dict(self.cost_ema_by_model),
            "latency_ema_by_model": dict(self.latency_ema_by_model),
            "success_streak": self.success_streak,
            "failure_streak": self.failure_streak,
            "total_commits": self.total_commits,
            "budget_headroom_ratio": self.budget_headroom_ratio,
            "commits_since_rotate": self.commits_since_rotate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> _ChainStats:
        s = cls()
        s.cost_ema = float(data.get("cost_ema", 0.0))
        s.cost_ema_by_model = {str(k): float(v) for k, v in dict(data.get("cost_ema_by_model", {})).items()}
        s.latency_ema_by_model = {str(k): float(v) for k, v in dict(data.get("latency_ema_by_model", {})).items()}
        s.success_streak = int(data.get("success_streak", 0))
        s.failure_streak = int(data.get("failure_streak", 0))
        s.total_commits = int(data.get("total_commits", 0))
        s.budget_headroom_ratio = float(data.get("budget_headroom_ratio", 1.0))
        s.commits_since_rotate = int(data.get("commits_since_rotate", 0))
        return s


class FileStore:
    """Phase 2 JSONL-based store with EMA computation.

    One JSONL file per chain_id. Stats (EMA, streaks) kept in memory
    and flushed to {chain_id}_stats.json every flush_interval commits.
    """

    def __init__(
        self,
        data_dir: str,
        flush_interval: int = 10,
        max_lines: int = 10_000,
    ) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._flush_interval = flush_interval
        self._max_lines = max_lines
        self._chain_stats: dict[str, _ChainStats] = {}
        self._budget_ceiling: float | None = None
        self._budget_remaining: float | None = None
        self._lock = threading.Lock()
        self._load_existing_stats()

    @staticmethod
    def _validate_chain_id(chain_id: str) -> None:
        """Validate chain_id to prevent path traversal and other attacks."""
        if not _SAFE_CHAIN_RE.match(chain_id):
            raise ValueError(f"Invalid chain_id: must match {_SAFE_CHAIN_RE.pattern!r}")

    def set_budget_context(self, ceiling_usd: float, remaining_usd: float) -> None:
        """Inject budget context for the next commit. Consumed on commit."""
        with self._lock:
            self._budget_ceiling = ceiling_usd
            self._budget_remaining = remaining_usd

    def _load_existing_stats(self) -> None:
        for stats_path in self._data_dir.glob("*_stats.json"):
            chain_id = stats_path.stem.removesuffix("_stats")
            if not _SAFE_CHAIN_RE.match(chain_id):
                logger.warning("Skipping stats file with invalid chain_id: %s", stats_path.name)
                continue
            try:
                data = json.loads(stats_path.read_text())
                self._chain_stats[chain_id] = _ChainStats.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                logger.warning("Corrupt stats file %s, starting fresh", stats_path)

    def commit(
        self,
        outcome: StepOutcome,
        analysis: AnalysisResult,
        cost: CostEstimate,
        desired: DesiredPolicy,
        policy: PolicyConfig,
        meta: DecisionMeta,
    ) -> None:
        chain_id = outcome.chain_id
        self._validate_chain_id(chain_id)

        with self._lock:
            # Reject new chains beyond the cap
            if chain_id not in self._chain_stats and len(self._chain_stats) >= _MAX_CHAINS:
                raise ValueError(
                    f"Chain limit ({_MAX_CHAINS}) reached; cannot create chain '{chain_id}'"
                )

            # 1. Append JSONL
            record = {
                "outcome": {
                    "step_id": outcome.step_id,
                    "request_id": outcome.request_id,
                    "chain_id": outcome.chain_id,
                    "kind": outcome.kind,
                    "status": outcome.status,
                    "cost_usd": outcome.cost_usd,
                    "tokens_in": outcome.tokens_in,
                    "tokens_out": outcome.tokens_out,
                    "elapsed_ms": outcome.elapsed_ms,
                    "model": outcome.model,
                    "timestamp_ms": outcome.timestamp_ms,
                },
                "analysis": {
                    "risk_level": analysis.risk_level,
                    "recommendation": analysis.recommendation,
                },
            }
            jsonl_path = self._data_dir / f"{chain_id}.jsonl"
            with open(jsonl_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")

            # 2. Update stats
            stats = self._chain_stats.setdefault(chain_id, _ChainStats())
            stats.cost_ema = _ema(stats.cost_ema, outcome.cost_usd)

            model_key = outcome.model or "unknown"
            prev_cost = stats.cost_ema_by_model.get(model_key, 0.0)
            stats.cost_ema_by_model[model_key] = _ema(prev_cost, outcome.cost_usd)

            prev_lat = stats.latency_ema_by_model.get(model_key, 0.0)
            stats.latency_ema_by_model[model_key] = _ema(prev_lat, outcome.elapsed_ms)

            # 3. Streaks
            if outcome.status == "ok":
                stats.success_streak += 1
                stats.failure_streak = 0
            else:
                stats.failure_streak += 1
                stats.success_streak = 0

            stats.total_commits += 1

            # Consume budget context
            ceiling = self._budget_ceiling
            remaining = self._budget_remaining
            self._budget_ceiling = None
            self._budget_remaining = None
            if ceiling is not None and remaining is not None and ceiling > _EPS:
                stats.budget_headroom_ratio = remaining / ceiling

            # 4. Periodic flush
            if stats.total_commits % self._flush_interval == 0:
                self._flush_stats(chain_id)

            # 5. Rotation check (Rule C: only after successful commit)
            stats.commits_since_rotate += 1
            if stats.commits_since_rotate >= self._max_lines:
                if self._rotate(chain_id):
                    stats.commits_since_rotate = 0
                # else: rotation failed (Rule D), will retry next commit

    _MAX_HISTORY_LIMIT = 50_000

    def build_history(self, chain_id: str, limit: int = 50) -> HistoryView:
        limit = max(1, min(limit, self._MAX_HISTORY_LIMIT))
        stats = self._chain_stats.get(chain_id, _ChainStats())
        outcomes = self._load_recent_outcomes(chain_id, limit)
        last_n = tuple(outcomes)

        rolling_cost = sum(o.cost_usd for o in last_n)
        depth = len(last_n)

        return HistoryView(
            chain_id=chain_id,
            last_n=last_n,
            rolling_cost_usd=rolling_cost,
            failure_streak=stats.failure_streak,
            depth=depth,
            loop_score=self._compute_loop_score(last_n),
            success_streak=stats.success_streak,
            cost_per_step_ema=stats.cost_ema,
            cost_per_step_ema_by_model=dict(stats.cost_ema_by_model),
            latency_ema_ms=dict(stats.latency_ema_by_model),
            budget_headroom_ratio=stats.budget_headroom_ratio,
        )

    def close(self) -> None:
        with self._lock:
            snapshots = {cid: s.to_dict() for cid, s in self._chain_stats.items()}
        for chain_id, data in snapshots.items():
            stats_path = self._data_dir / f"{chain_id}_stats.json"
            try:
                stats_path.write_text(json.dumps(data), encoding="utf-8")
            except OSError:
                logger.warning("Failed to flush stats for %s on close", chain_id)

    def _flush_stats(self, chain_id: str) -> None:
        stats = self._chain_stats.get(chain_id)
        if stats is None:
            return
        stats_path = self._data_dir / f"{chain_id}_stats.json"
        stats_path.write_text(json.dumps(stats.to_dict()), encoding="utf-8")

    def _rotate(self, chain_id: str) -> bool:
        """Rotate JSONL: active -> .1 (single generation).

        Returns True on success. On failure (e.g., Windows file lock),
        logs warning and returns False -- next commit will retry (Rule D).
        """
        active = self._data_dir / f"{chain_id}.jsonl"
        rotated = self._data_dir / f"{chain_id}.1.jsonl"
        try:
            if rotated.exists():
                rotated.unlink()  # Windows: must delete before rename
            if active.exists():
                active.rename(rotated)
            return True
        except OSError:
            logger.warning("JSONL rotation failed for %s, will retry", chain_id)
            return False

    def _load_recent_outcomes(self, chain_id: str, limit: int) -> list[StepOutcome]:
        active_path = self._data_dir / f"{chain_id}.jsonl"
        rotated_path = self._data_dir / f"{chain_id}.1.jsonl"

        outcomes = self._read_jsonl_outcomes(active_path)
        if len(outcomes) < limit and rotated_path.exists():
            older = self._read_jsonl_outcomes(rotated_path)
            outcomes = older + outcomes  # older first, then current

        return outcomes[-limit:]

    def _read_jsonl_outcomes(self, path: Path) -> list[StepOutcome]:
        if not path.exists():
            return []
        records: list[StepOutcome] = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Corrupt JSONL line in %s, skipping", path)
                    continue
                o = rec.get("outcome")
                if o is None:
                    logger.warning("JSONL line missing 'outcome' key in %s, skipping", path)
                    continue
                try:
                    records.append(StepOutcome(
                        step_id=o["step_id"],
                        request_id=o["request_id"],
                        chain_id=o["chain_id"],
                        kind=o["kind"],
                        status=o["status"],
                        cost_usd=o["cost_usd"],
                        tokens_in=o["tokens_in"],
                        tokens_out=o["tokens_out"],
                        elapsed_ms=o["elapsed_ms"],
                        model=o.get("model"),
                        events=(),
                        timestamp_ms=o["timestamp_ms"],
                    ))
                except (KeyError, TypeError, ValueError):
                    logger.warning("Corrupt outcome record in %s, skipping", path)
                    continue
        return records

    @staticmethod
    def _compute_loop_score(outcomes: tuple[StepOutcome, ...] | list[StepOutcome]) -> float:
        if len(outcomes) < 3:
            return 0.0
        keys = [(o.kind, o.model or o.kind, o.status) for o in outcomes[-10:]]
        from collections import Counter

        counts = Counter(keys)
        if not counts:
            return 0.0
        most_common_count = counts.most_common(1)[0][1]
        return most_common_count / len(keys)
