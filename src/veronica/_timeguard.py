# src/veronica/_timeguard.py
"""Stage time budget enforcement.

Runs a callable and checks if it exceeded its budget AFTER completion.
Does NOT kill threads -- just detects overruns for DEGRADE logic.
"""
from __future__ import annotations

import time
from typing import Any, Callable, TypeVar, overload

T = TypeVar("T")


class TimeBudgetExceeded(Exception):
    """Raised when a pipeline stage exceeds its time budget."""

    def __init__(self, stage_name: str, budget_ms: float, actual_ms: float) -> None:
        self.stage_name = stage_name
        self.budget_ms = budget_ms
        self.actual_ms = actual_ms
        super().__init__(
            f"Stage '{stage_name}' exceeded budget: "
            f"{actual_ms:.1f}ms > {budget_ms:.1f}ms"
        )


@overload
def run_with_budget(
    fn: Callable[[], T],
    budget_ms: float,
    stage_name: str,
    *,
    return_elapsed: bool = ...,
) -> T: ...


@overload
def run_with_budget(
    fn: Callable[[], T],
    budget_ms: float,
    stage_name: str,
    *,
    return_elapsed: bool,
) -> tuple[T, float]: ...


def run_with_budget(
    fn: Callable[[], Any],
    budget_ms: float,
    stage_name: str,
    *,
    return_elapsed: bool = False,
) -> Any:
    start = time.monotonic()
    result = fn()
    elapsed_ms = (time.monotonic() - start) * 1000.0

    if elapsed_ms > budget_ms:
        raise TimeBudgetExceeded(stage_name, budget_ms, elapsed_ms)

    if return_elapsed:
        return result, elapsed_ms
    return result
