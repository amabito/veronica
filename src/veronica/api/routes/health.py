# src/veronica/api/routes/health.py
"""GET /health endpoint -- no auth required."""

from __future__ import annotations

import time

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])

_START_TIME = time.monotonic()


def _is_immutable_config() -> bool:
    """Return True if VERONICA_IMMUTABLE_CONFIG env var is set to a truthy value."""
    import os

    return os.environ.get("VERONICA_IMMUTABLE_CONFIG", "").lower() in (
        "1",
        "true",
        "yes",
    )


@router.get("/health", summary="Health check")
async def health(request: Request) -> dict[str, object]:
    """Return API status, version, and uptime.

    Returns status="ok" when all subsystems are healthy.
    Returns status="degraded" with details when a subsystem (e.g. store) is unavailable.
    Includes stats.active_chains (distinct chain_ids with recent events in last 5 min)
    and stats.circuit_state (from veronica-core circuit breaker if available).
    """
    store_status = _check_store(request)
    kernel_compat = _check_kernel_compat()
    subsystem_ok = store_status == "ok" and kernel_compat == "ok"
    overall_status = "ok" if subsystem_ok else "degraded"

    startup_valid = getattr(getattr(request.app, "state", None), "startup_valid", True)

    return {
        "status": overall_status,
        "version": _get_cp_version(),
        "kernel_version": _get_kernel_version(),
        "uptime_seconds": round(time.monotonic() - _START_TIME, 2),
        "immutable_config": _is_immutable_config(),
        "startup_valid": startup_valid,
        "subsystems": {
            "store": store_status,
            "kernel_compat": kernel_compat,
        },
        "stats": {
            "active_chains": _count_active_chains(request),
            "circuit_state": _get_circuit_state(),
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


_KERNEL_MIN_VERSION = (3, 4, 0)


def _get_cp_version() -> str:
    """Return veronica control plane version via deferred import."""
    try:
        from veronica import __version__

        return __version__
    except Exception:
        return "unknown"


def _get_kernel_version() -> str:
    """Return veronica-core version, or 'unknown' on import failure."""
    try:
        import veronica_core

        return getattr(veronica_core, "__version__", "unknown")
    except ImportError:
        return "unknown"


def _parse_version_tuple(version_str: str) -> tuple[int, ...]:
    """Parse version string to int tuple, stripping pre-release suffixes.

    '3.4.2' -> (3, 4, 2), '3.4.0rc1' -> (3, 4, 0), '3.4' -> (3, 4, 0)
    Raises ValueError on unparseable input.
    """
    import re

    raw_parts = version_str.split(".")[:3]
    nums = []
    for part in raw_parts:
        m = re.match(r"(\d+)", part)
        if m is None:
            raise ValueError(f"Non-numeric version part: {part!r}")
        nums.append(int(m.group(1)))
    # Zero-pad to 3 elements
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


_ACTIVE_CHAIN_WINDOW_SECONDS = 300  # 5 minutes


def _count_active_chains(request: Request) -> int:
    """Count distinct chain_ids with events in the last 5 minutes."""
    try:
        ingestor = getattr(getattr(request, "app", None), "state", None)
        if ingestor is None:
            return 0
        ingestor_obj = getattr(ingestor, "ingestor", None)
        if ingestor_obj is None:
            return 0
        outcomes = ingestor_obj.store.snapshot()
        cutoff = time.time() - _ACTIVE_CHAIN_WINDOW_SECONDS
        active = {o.chain_id for o in outcomes if o.timestamp >= cutoff}
        return len(active)
    except Exception:
        return 0


def _get_circuit_state() -> str | None:
    """Return circuit breaker state string if available, else None."""
    try:
        import veronica_core

        cb = getattr(veronica_core, "circuit_breaker", None)
        if cb is None:
            return None
        state = getattr(cb, "state", None)
        if state is None:
            return None
        return str(state.value) if hasattr(state, "value") else str(state)
    except Exception:
        return None


def _check_kernel_compat() -> str:
    """Return 'ok' if kernel version is compatible, 'incompatible' otherwise."""
    version_str = _get_kernel_version()
    if version_str == "unknown":
        return "unknown"
    try:
        parts = _parse_version_tuple(version_str)
        if parts < _KERNEL_MIN_VERSION:
            return "incompatible"
        # Major version 4+ is untested
        if parts[0] >= 4:
            return "incompatible"
        return "ok"
    except (ValueError, IndexError):
        return "unknown"
