# src/veronica/metrics_exporter.py
"""Optional Prometheus HTTP exporter for VERONICA metrics."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 9464
_started = False


def start_metrics_server(
    port: int | None = None,
    addr: str = "0.0.0.0",
) -> bool:
    """Start Prometheus HTTP metrics server.

    Port resolution order:
    1. Explicit ``port`` argument
    2. ``VERONICA_METRICS_PORT`` environment variable
    3. Default 9464

    Returns True if server started, False if prometheus_client is
    not installed or server was already started.

    Note: The double-start guard is per-process only (module-level flag).
    In multi-process deployments (gunicorn, uvicorn with workers), each
    worker process will start its own server. Use ``VERONICA_METRICS_PORT``
    or the ``port`` argument to assign distinct ports per worker, or run a
    single dedicated metrics process.
    """
    global _started
    if _started:
        return True

    try:
        from prometheus_client import start_http_server
    except ImportError:
        logger.debug("prometheus_client not installed; metrics server disabled")
        return False

    resolved = port if port is not None else int(
        os.environ.get("VERONICA_METRICS_PORT", _DEFAULT_PORT),
    )
    start_http_server(resolved, addr=addr)
    _started = True
    logger.info("VERONICA metrics server started on %s:%d", addr, resolved)
    return True
