# src/veronica/metrics/__init__.py
"""VERONICA OS metrics -- Prometheus subscriber with CP-level labels."""
from veronica.metrics.prometheus_subscriber import PrometheusSubscriber

__all__ = ["PrometheusSubscriber"]
