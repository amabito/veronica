# tests/test_file_store.py
"""Tests for veronica.file_store -- JSONL persistence with EMA computation."""
from __future__ import annotations

import json
import time

import pytest

from veronica.file_store import FileStore
from veronica.types import (
    AnalysisResult,
    CostEstimate,
    DecisionMeta,
    DesiredPolicy,
    PolicyConfig,
    StepOutcome,
)


def _outcome(
    chain_id: str = "c1",
    step_id: str = "s1",
    status: str = "ok",
    cost: float = 0.01,
    model: str = "gpt-4",
    elapsed_ms: float = 100.0,
) -> StepOutcome:
    return StepOutcome(
        step_id=step_id,
        request_id="r1",
        chain_id=chain_id,
        kind="llm",
        status=status,
        cost_usd=cost,
        tokens_in=100,
        tokens_out=50,
        elapsed_ms=elapsed_ms,
        model=model,
        events=(),
        timestamp_ms=int(time.time() * 1000),
    )


def _analysis() -> AnalysisResult:
    return AnalysisResult(signals=(), risk_level="nominal", recommendation="continue")


def _cost_est() -> CostEstimate:
    return CostEstimate(estimated_usd=0.01, confidence=0.8, model_used="gpt-4", basis="historical")


def _desired() -> DesiredPolicy:
    return DesiredPolicy(
        chain_id="c1", ceiling_usd=1.0, ceiling_steps=100,
        ceiling_tokens_out=50000, on_exceed="halt",
        fallback_model=None, timeout_ms=30000, priority=50,
    )


def _policy() -> PolicyConfig:
    return PolicyConfig(chain_id="c1", ceiling_usd=1.0, on_exceed="halt", issued_at=time.time())


def _meta() -> DecisionMeta:
    return DecisionMeta(risk_level="nominal", recommendation="continue", degraded=False, stage_time_ms={})


class TestFileStore:
    def test_commit_and_build_history(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        store.commit(_outcome(), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        hv = store.build_history("c1")
        assert hv.chain_id == "c1"
        assert len(hv.last_n) == 1
        assert hv.depth == 1

    def test_success_streak(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        for i in range(5):
            store.commit(
                _outcome(step_id=f"s{i}"), _analysis(), _cost_est(),
                _desired(), _policy(), _meta(),
            )
        hv = store.build_history("c1")
        assert hv.success_streak == 5
        assert hv.failure_streak == 0

    def test_failure_resets_success_streak(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        store.commit(_outcome(step_id="s1"), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        store.commit(_outcome(step_id="s2"), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        store.commit(
            _outcome(step_id="s3", status="error"), _analysis(), _cost_est(),
            _desired(), _policy(), _meta(),
        )
        hv = store.build_history("c1")
        assert hv.success_streak == 0
        assert hv.failure_streak == 1

    def test_cost_ema_single_model(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        costs = [0.10, 0.10, 0.10]
        for i, c in enumerate(costs):
            store.commit(
                _outcome(step_id=f"s{i}", cost=c), _analysis(), _cost_est(),
                _desired(), _policy(), _meta(),
            )
        hv = store.build_history("c1")
        assert hv.cost_per_step_ema > 0
        assert "gpt-4" in hv.cost_per_step_ema_by_model
        assert hv.cost_per_step_ema_by_model["gpt-4"] > 0

    def test_latency_ema(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        store.commit(
            _outcome(elapsed_ms=200.0), _analysis(), _cost_est(),
            _desired(), _policy(), _meta(),
        )
        hv = store.build_history("c1")
        assert "gpt-4" in hv.latency_ema_ms
        assert hv.latency_ema_ms["gpt-4"] == pytest.approx(200.0)

    def test_multiple_chains_independent(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        store.commit(
            _outcome(chain_id="c1", step_id="s1"), _analysis(), _cost_est(),
            _desired(), _policy(), _meta(),
        )
        store.commit(
            _outcome(chain_id="c2", step_id="s2"), _analysis(), _cost_est(),
            DesiredPolicy(chain_id="c2", ceiling_usd=1.0, ceiling_steps=100,
                          ceiling_tokens_out=50000, on_exceed="halt",
                          fallback_model=None, timeout_ms=30000, priority=50),
            PolicyConfig(chain_id="c2", ceiling_usd=1.0, on_exceed="halt", issued_at=time.time()),
            _meta(),
        )
        hv1 = store.build_history("c1")
        hv2 = store.build_history("c2")
        assert hv1.depth == 1
        assert hv2.depth == 1

    def test_jsonl_persisted(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        store.commit(_outcome(), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        jsonl_path = tmp_path / "c1.jsonl"
        assert jsonl_path.exists()
        lines = jsonl_path.read_text().strip().split("\n")
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["outcome"]["step_id"] == "s1"

    def test_stats_flush(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path), flush_interval=2)
        store.commit(_outcome(step_id="s1"), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        store.commit(_outcome(step_id="s2"), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        stats_path = tmp_path / "c1_stats.json"
        assert stats_path.exists()

    def test_reload_from_disk(self, tmp_path) -> None:
        store1 = FileStore(data_dir=str(tmp_path))
        for i in range(3):
            store1.commit(
                _outcome(step_id=f"s{i}"), _analysis(), _cost_est(),
                _desired(), _policy(), _meta(),
            )
        store1.close()
        # New store instance loads from disk
        store2 = FileStore(data_dir=str(tmp_path))
        hv = store2.build_history("c1")
        assert hv.depth == 3
        assert hv.cost_per_step_ema > 0

    def test_corrupt_line_skipped(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        store.commit(_outcome(step_id="s1"), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        # Corrupt the JSONL file
        jsonl_path = tmp_path / "c1.jsonl"
        with open(jsonl_path, "a") as f:
            f.write("{corrupt\n")
        store2 = FileStore(data_dir=str(tmp_path))
        hv = store2.build_history("c1")
        assert hv.depth == 1  # corrupt line skipped

    def test_protocol_compatible(self, tmp_path) -> None:
        from veronica.protocols import StoreProtocol

        store = FileStore(data_dir=str(tmp_path))
        assert isinstance(store, StoreProtocol)

    def test_empty_chain_returns_defaults(self, tmp_path) -> None:
        store = FileStore(data_dir=str(tmp_path))
        hv = store.build_history("nonexistent")
        assert hv.depth == 0
        assert hv.success_streak == 0
        assert hv.cost_per_step_ema == 0.0
        assert hv.budget_headroom_ratio == 1.0

    def test_headroom_basic_injection(self, tmp_path) -> None:
        """set_budget_context -> commit -> build_history returns correct headroom."""
        store = FileStore(data_dir=str(tmp_path))
        store.set_budget_context(10.0, 7.0)
        store.commit(_outcome(), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        hv = store.build_history("c1")
        assert hv.budget_headroom_ratio == pytest.approx(0.7)

    def test_headroom_zero_ceiling(self, tmp_path) -> None:
        """Zero ceiling does not cause division by zero."""
        store = FileStore(data_dir=str(tmp_path))
        store.set_budget_context(0.0, 0.0)
        store.commit(_outcome(), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        hv = store.build_history("c1")
        assert hv.budget_headroom_ratio == 1.0  # default preserved

    def test_headroom_no_context_set(self, tmp_path) -> None:
        """Without set_budget_context, headroom stays at default 1.0."""
        store = FileStore(data_dir=str(tmp_path))
        store.commit(_outcome(), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        hv = store.build_history("c1")
        assert hv.budget_headroom_ratio == 1.0

    def test_headroom_cleared_after_commit(self, tmp_path) -> None:
        """Budget context is consumed by commit -- not reused on next commit."""
        store = FileStore(data_dir=str(tmp_path))
        store.set_budget_context(10.0, 7.0)
        store.commit(_outcome(step_id="s1"), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        # Second commit without set_budget_context
        store.commit(_outcome(step_id="s2"), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        hv = store.build_history("c1")
        # headroom should still be 0.7 (last successful update), not re-updated
        assert hv.budget_headroom_ratio == pytest.approx(0.7)

    def test_headroom_persists_across_reload(self, tmp_path) -> None:
        """Headroom survives stats flush + reload."""
        store = FileStore(data_dir=str(tmp_path))
        store.set_budget_context(10.0, 5.0)
        store.commit(_outcome(), _analysis(), _cost_est(), _desired(), _policy(), _meta())
        store.close()
        store2 = FileStore(data_dir=str(tmp_path))
        hv = store2.build_history("c1")
        assert hv.budget_headroom_ratio == pytest.approx(0.5)


class TestFileStoreRotation:
    def test_rotation_trigger(self, tmp_path) -> None:
        """Rotation occurs when commits_since_rotate >= max_lines."""
        store = FileStore(data_dir=str(tmp_path), max_lines=5)
        for i in range(6):
            store.commit(
                _outcome(step_id=f"s{i}"), _analysis(), _cost_est(),
                _desired(), _policy(), _meta(),
            )
        active = tmp_path / "c1.jsonl"
        rotated = tmp_path / "c1.1.jsonl"
        assert active.exists()
        assert rotated.exists()
        # Active has 1 line (post-rotation commit)
        assert len(active.read_text().strip().split("\n")) == 1
        # Rotated has 5 lines
        assert len(rotated.read_text().strip().split("\n")) == 5

    def test_post_rotation_history_no_coldstart(self, tmp_path) -> None:
        """build_history reads both active and rotated files."""
        store = FileStore(data_dir=str(tmp_path), max_lines=5)
        for i in range(6):
            store.commit(
                _outcome(step_id=f"s{i}"), _analysis(), _cost_est(),
                _desired(), _policy(), _meta(),
            )
        hv = store.build_history("c1")
        assert hv.depth == 6  # all 6 entries visible

    def test_double_rotation(self, tmp_path) -> None:
        """Second rotation overwrites .1 file (1 generation only)."""
        store = FileStore(data_dir=str(tmp_path), max_lines=5)
        for i in range(12):
            store.commit(
                _outcome(step_id=f"s{i}"), _analysis(), _cost_est(),
                _desired(), _policy(), _meta(),
            )
        active = tmp_path / "c1.jsonl"
        rotated = tmp_path / "c1.1.jsonl"
        # Active: 2 lines (10 rotated, 11th + 12th in active)
        assert len(active.read_text().strip().split("\n")) == 2
        # Rotated: 5 lines (second generation, overwrote first)
        assert len(rotated.read_text().strip().split("\n")) == 5
        # Total visible via history
        hv = store.build_history("c1")
        assert hv.depth == 7  # 5 from .1 + 2 from active

    def test_rotation_failure_resilience(self, tmp_path, monkeypatch) -> None:
        """Rename failure logs warning and continues -- next commit retries."""
        store = FileStore(data_dir=str(tmp_path), max_lines=5)
        for i in range(4):
            store.commit(
                _outcome(step_id=f"s{i}"), _analysis(), _cost_est(),
                _desired(), _policy(), _meta(),
            )

        # Make 5th commit trigger rotation, but sabotage rename
        from pathlib import Path
        original_rename = Path.rename
        def failing_rename(self_path, target):
            raise OSError("simulated Windows lock")
        monkeypatch.setattr(Path, "rename", failing_rename)

        # Should not raise -- rotation failure is non-fatal
        store.commit(
            _outcome(step_id="s4"), _analysis(), _cost_est(),
            _desired(), _policy(), _meta(),
        )

        monkeypatch.setattr(Path, "rename", original_rename)
        # Next commit retries rotation
        store.commit(
            _outcome(step_id="s5"), _analysis(), _cost_est(),
            _desired(), _policy(), _meta(),
        )
        rotated = tmp_path / "c1.1.jsonl"
        assert rotated.exists()

    def test_rotation_stats_persist(self, tmp_path) -> None:
        """commits_since_rotate persists through close + reload."""
        store = FileStore(data_dir=str(tmp_path), max_lines=10)
        for i in range(3):
            store.commit(
                _outcome(step_id=f"s{i}"), _analysis(), _cost_est(),
                _desired(), _policy(), _meta(),
            )
        store.close()
        store2 = FileStore(data_dir=str(tmp_path), max_lines=10)
        # Continue committing -- rotation should happen at commit 10, not 13
        for i in range(3, 10):
            store2.commit(
                _outcome(step_id=f"s{i}"), _analysis(), _cost_est(),
                _desired(), _policy(), _meta(),
            )
        rotated = tmp_path / "c1.1.jsonl"
        assert rotated.exists()
