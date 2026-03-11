# tests/test_timeguard.py
"""Tests for veronica._timeguard -- stage time budget enforcement."""

from __future__ import annotations

import time

from veronica._timeguard import run_with_budget, TimeBudgetExceeded


class TestRunWithBudget:
    def test_fast_function_succeeds(self) -> None:
        result = run_with_budget(lambda: 42, budget_ms=100.0, stage_name="test")
        assert result == 42

    def test_returns_elapsed(self) -> None:
        result, elapsed = run_with_budget(
            lambda: 42,
            budget_ms=100.0,
            stage_name="test",
            return_elapsed=True,
        )
        assert result == 42
        assert elapsed >= 0.0

    def test_slow_function_raises(self) -> None:
        def slow():
            time.sleep(0.2)
            return 99

        # Budget is 10ms but function takes 200ms.
        # Note: we check AFTER completion (no thread kill).
        import pytest

        with pytest.raises(TimeBudgetExceeded):
            run_with_budget(slow, budget_ms=10.0, stage_name="slow_test")
