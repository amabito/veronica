# src/veronica/metrics_subscriber.py
"""VERONICA OS observability -- Prometheus metrics subscriber."""
from __future__ import annotations

from typing import Any, Mapping

_KNOWN_STAGES = frozenset({
    "collector", "analyzer", "cost_model", "planner", "arbiter",
    "store", "emit",
})

_MICRO = 1_000_000


class MetricsSubscriber:
    """Prometheus metrics collector. Subscribe to BufferedEmitter.

    Usage::

        emitter = BufferedEmitter()
        metrics = MetricsSubscriber()
        emitter.subscribe("prometheus", metrics)

    All payload values are accessed defensively via .get() with
    try/except guards. Missing or malformed values are silently skipped.

    Args:
        prefix: Metric name prefix (default: "veronica").
        registry: Prometheus CollectorRegistry to register metrics into.
            Defaults to the global REGISTRY. Pass an isolated
            CollectorRegistry in tests to avoid double-registration errors.
    """

    def __init__(
        self,
        prefix: str = "veronica",
        registry: "CollectorRegistry | None" = None,
    ) -> None:
        from prometheus_client import CollectorRegistry, Counter, Histogram, REGISTRY

        registry = registry or REGISTRY

        self.steps_total = self._get_or_create(
            registry, Counter,
            f"{prefix}_steps_total",
            "Total steps executed",
            ["status", "kind", "recommendation", "risk_level"],
        )
        self.step_elapsed = self._get_or_create(
            registry, Histogram,
            f"{prefix}_step_elapsed_ms",
            "Step elapsed time in ms",
            ["kind"],
            buckets=[10, 50, 100, 500, 1000, 5000, 10000],
        )
        self.stage_elapsed = self._get_or_create(
            registry, Histogram,
            f"{prefix}_stage_elapsed_ms",
            "Pipeline stage elapsed time in ms",
            ["stage"],
            buckets=[1, 5, 10, 20, 50, 100, 250, 500, 1000, 5000],
        )
        self.cost_total = self._get_or_create(
            registry, Counter,
            f"{prefix}_cost_microusd_total",
            "Total cost in microusd (1 USD = 1,000,000)",
        )
        self.degrade_total = self._get_or_create(
            registry, Counter,
            f"{prefix}_degrade_total",
            "Total degraded steps",
            ["degrade_reason"],
        )

    @staticmethod
    def _get_or_create(registry, cls, name, documentation, labelnames=(), **kwargs):
        """Return existing collector if already registered, else create it."""
        existing = registry._names_to_collectors.get(name)
        if existing is not None:
            return existing
        return cls(name, documentation, labelnames, registry=registry, **kwargs)

    def __call__(
        self, event_type: str, payload: Mapping[str, Any],
    ) -> None:
        """Callback for BufferedEmitter.subscribe()."""
        if event_type != "step_completed":
            return

        self.steps_total.labels(
            status=payload.get("status", "unknown"),
            kind=payload.get("kind", "unknown"),
            recommendation=payload.get("recommendation", "unknown"),
            risk_level=payload.get("risk_level", "unknown"),
        ).inc()

        try:
            self.step_elapsed.labels(
                kind=payload.get("kind", "unknown"),
            ).observe(float(payload["elapsed_ms"]))
        except (KeyError, TypeError, ValueError):
            pass

        for stage, ms in payload.get("stage_time_ms", {}).items():
            if stage not in _KNOWN_STAGES:
                continue
            try:
                self.stage_elapsed.labels(stage=stage).observe(float(ms))
            except (TypeError, ValueError):
                pass

        try:
            self.cost_total.inc(round(float(payload["cost_usd"]) * _MICRO))
        except (KeyError, TypeError, ValueError):
            pass

        if payload.get("degraded"):
            reason = payload.get("degrade_reason", "other")
            if reason:
                self.degrade_total.labels(degrade_reason=reason).inc()
