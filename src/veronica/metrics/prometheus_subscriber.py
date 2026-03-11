# src/veronica/metrics/prometheus_subscriber.py
"""Prometheus metrics subscriber for VERONICA OS.

Exports CP-level metrics with chain_id labels for traceability.
policy_hash is intentionally excluded from labels to prevent cardinality
explosion (64-char hex with near-infinite unique values). It is available
in log messages and events only.

Requires the optional [metrics] extra:
  pip install veronica-cp[metrics]

Usage::

    from veronica.metrics import PrometheusSubscriber
    from veronica.buffered_emitter import BufferedEmitter

    emitter = BufferedEmitter()
    prom = PrometheusSubscriber()
    emitter.subscribe("prometheus_cp", prom)

All payload values are accessed defensively. Missing or malformed
values are silently skipped -- the subscriber never raises.

Metrics
-------
veronica_step_completed_total   Counter  chain_id, decision_type
veronica_step_denied_total      Counter  chain_id, decision_type
veronica_halt_total             Counter  chain_id
veronica_degrade_total          Counter  chain_id
veronica_step_duration_seconds  Histogram chain_id
veronica_active_chains          Gauge    (no labels)
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from prometheus_client import CollectorRegistry

_LABEL_UNKNOWN = "unknown"
_LABEL_OVERFLOW = "overflow"
_DURATION_BUCKETS = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
)
_MS_TO_S = 1_000.0
MAX_LABEL_LENGTH = 64
_CHAIN_ID_MAX_LEN = 32
_MAX_CHAIN_IDS = 1000
_VALID_DECISIONS = frozenset(
    {
        "allow",
        "halt",
        "halted",
        "degrade",
        "deny",
        "denied",
        "retry",
        "quarantine",
        "queue",
        "error",
        "ok",
        "unknown",
    }
)


def _safe_get(
    payload: Mapping[str, Any], key: str, default: str = _LABEL_UNKNOWN
) -> str:
    val = payload.get(key)
    if val is None or val == "":
        return default
    return str(val)


def _truncate(val: str, max_len: int) -> str:
    """Truncate label value to at most max_len characters."""
    return val[:max_len]


def _normalize_decision(value: str) -> str:
    """Map decision_type to allowlist; unknown values become 'unknown'."""
    return value if value in _VALID_DECISIONS else _LABEL_UNKNOWN


class PrometheusSubscriber:
    """CP-aware Prometheus metrics collector.

    Unlike the existing MetricsSubscriber, this class uses CP-level labels
    (chain_id, policy_hash, decision_type) for policy traceability.

    Args:
        prefix: Metric name prefix (default: "veronica").
        registry: Prometheus CollectorRegistry. Defaults to the global REGISTRY.
            Pass an isolated registry in tests to avoid double-registration errors.
    """

    def __init__(
        self,
        prefix: str = "veronica",
        registry: "CollectorRegistry | None" = None,
    ) -> None:
        from prometheus_client import Counter, Gauge, Histogram, REGISTRY

        registry = registry or REGISTRY

        self.step_completed_total = self._get_or_create(
            registry,
            Counter,
            f"{prefix}_step_completed_total",
            "Total completed steps",
            ["chain_id", "decision_type"],
        )
        self.step_denied_total = self._get_or_create(
            registry,
            Counter,
            f"{prefix}_step_denied_total",
            "Total denied steps",
            ["chain_id", "decision_type"],
        )
        self.halt_total = self._get_or_create(
            registry,
            Counter,
            f"{prefix}_halt_total",
            "Total halted steps",
            ["chain_id"],
        )
        self.degrade_total = self._get_or_create(
            registry,
            Counter,
            f"{prefix}_degrade_total",
            "Total degraded steps",
            ["chain_id"],
        )
        self.step_duration_seconds = self._get_or_create(
            registry,
            Histogram,
            f"{prefix}_step_duration_seconds",
            "Step duration in seconds",
            ["chain_id"],
            buckets=_DURATION_BUCKETS,
        )
        self.active_chains = self._get_or_create(
            registry,
            Gauge,
            f"{prefix}_active_chains",
            "Number of distinct chains seen in this process",
        )

        # Track distinct chain_ids for active_chains gauge
        self._seen_chains: set[str] = set()
        self._lock = threading.Lock()

    @staticmethod
    def _get_or_create(registry, cls, name, documentation, labelnames=(), **kwargs):
        """Return existing collector if already registered, else create it."""
        existing = registry._names_to_collectors.get(name)
        if existing is not None:
            return existing
        return cls(name, documentation, labelnames, registry=registry, **kwargs)

    def __call__(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Callback for BufferedEmitter.subscribe()."""
        try:
            if event_type == "step_completed":
                self._handle_step_completed(payload)
            elif event_type == "step_denied":
                self._handle_step_denied(payload)
        except Exception:
            pass  # Never raise from subscriber callbacks

    def _track_chain(self, chain_id: str) -> str:
        """Update active_chains gauge when a new chain_id is observed.

        Returns the sanitized chain_id to use as a metric label. If the
        number of distinct chain_ids exceeds _MAX_CHAIN_IDS, new ones are
        mapped to 'overflow' to prevent cardinality explosion.
        """
        if chain_id == _LABEL_UNKNOWN:
            return chain_id
        with self._lock:
            if chain_id in self._seen_chains:
                return chain_id
            if len(self._seen_chains) >= _MAX_CHAIN_IDS:
                return _LABEL_OVERFLOW
            self._seen_chains.add(chain_id)
            self.active_chains.inc()
            return chain_id

    def _handle_step_completed(self, payload: Mapping[str, Any]) -> None:
        raw_chain_id = _truncate(_safe_get(payload, "chain_id"), _CHAIN_ID_MAX_LEN)
        status = _safe_get(payload, "status")
        decision_type = _normalize_decision(status)  # normalized against allowlist

        chain_id = self._track_chain(raw_chain_id)

        self.step_completed_total.labels(
            chain_id=chain_id,
            decision_type=decision_type,
        ).inc()

        if status == "halted":
            self.halt_total.labels(chain_id=chain_id).inc()

        if payload.get("degraded"):
            self.degrade_total.labels(chain_id=chain_id).inc()

        try:
            elapsed_ms = float(payload["elapsed_ms"])
            self.step_duration_seconds.labels(chain_id=chain_id).observe(
                elapsed_ms / _MS_TO_S
            )
        except (KeyError, TypeError, ValueError):
            pass

    def _handle_step_denied(self, payload: Mapping[str, Any]) -> None:
        raw_chain_id = _truncate(_safe_get(payload, "chain_id"), _CHAIN_ID_MAX_LEN)
        # Use "status" (not "reason") to avoid unbounded cardinality from free-text reason strings
        decision_type = _normalize_decision(_safe_get(payload, "status"))

        chain_id = self._track_chain(raw_chain_id)

        self.step_denied_total.labels(
            chain_id=chain_id,
            decision_type=decision_type,
        ).inc()
