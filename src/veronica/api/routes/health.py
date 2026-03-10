# src/veronica/api/routes/health.py
"""GET /health endpoint -- no auth required."""
from __future__ import annotations

import time

from fastapi import APIRouter, Request

import veronica

router = APIRouter(tags=["health"])

_START_TIME = time.monotonic()


@router.get("/health", summary="Health check")
async def health(request: Request) -> dict[str, object]:
    """Return API status, version, and uptime.

    Returns status="ok" when all subsystems are healthy.
    Returns status="degraded" with details when a subsystem (e.g. store) is unavailable.
    """
    store_status = _check_store(request)
    overall_status = "ok" if store_status == "ok" else "degraded"

    return {
        "status": overall_status,
        "version": veronica.__version__,
        "kernel_version": _get_kernel_version(),
        "uptime_seconds": round(time.monotonic() - _START_TIME, 2),
        "subsystems": {
            "store": store_status,
        },
    }


def _check_store(request: Request) -> str:
    """Return 'ok' if store is accessible, 'unavailable' otherwise."""
    store = getattr(getattr(request, "app", None), "state", None)
    if store is None:
        return "unavailable"
    store_obj = getattr(store, "store", None)
    if store_obj is None:
        return "unavailable"
    # Probe: check that the store object exists and has the expected interface.
    # Do NOT call build_history to avoid polluting audit logs with probe entries.
    try:
        if callable(getattr(store_obj, "build_history", None)):
            return "ok"
        return "unavailable"
    except Exception:
        return "unavailable"


def _get_kernel_version() -> str:
    """Return veronica-core version, or 'unknown' on import failure."""
    try:
        import veronica_core

        return getattr(veronica_core, "__version__", "unknown")
    except ImportError:
        return "unknown"
