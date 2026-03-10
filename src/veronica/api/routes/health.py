# src/veronica/api/routes/health.py
"""GET /health endpoint -- no auth required."""
from __future__ import annotations

import time

from fastapi import APIRouter

import veronica

router = APIRouter(tags=["health"])

_START_TIME = time.monotonic()


@router.get("/health", summary="Health check")
async def health() -> dict[str, object]:
    """Return API status, version, and uptime."""
    return {
        "status": "ok",
        "version": veronica.__version__,
        "kernel_version": _get_kernel_version(),
        "uptime_seconds": round(time.monotonic() - _START_TIME, 2),
    }


def _get_kernel_version() -> str:
    """Return veronica-core version, or 'unknown' on import failure."""
    try:
        import veronica_core

        return getattr(veronica_core, "__version__", "unknown")
    except ImportError:
        return "unknown"
