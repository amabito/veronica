# src/veronica/api/auth.py
"""API key authentication middleware for the Veronica control-plane API.

Uses X-Veronica-Key header. Paths /health and /docs/* are exempt.
Key is loaded from VERONICA_API_KEY env var. If unset:
  - VERONICA_AUTH_DISABLED=1 env var must be explicitly set to allow
    unauthenticated access (warning logged once).
  - Otherwise, returns 503 "API key not configured".
"""

from __future__ import annotations

import hmac
import logging
import os
import threading

from fastapi import Request
from fastapi.security import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

_HEADER_NAME = "X-Veronica-Key"
_ENV_VAR = "VERONICA_API_KEY"
_ENV_AUTH_DISABLED = "VERONICA_AUTH_DISABLED"

# Paths that bypass auth -- exact matches only, plus prefix matches
_EXEMPT_EXACT = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})
_EXEMPT_PREFIXES = ("/docs/", "/ui/")  # swagger assets + UI pages

_api_key_header = APIKeyHeader(name=_HEADER_NAME, auto_error=False)

_warn_once_lock = threading.Lock()
_warn_once_done = False


def _get_configured_key() -> str | None:
    """Return the configured API key, or None if not set."""
    return os.environ.get(_ENV_VAR)


def _is_auth_disabled() -> bool:
    """Return True if VERONICA_AUTH_DISABLED=1 is explicitly set."""
    return os.environ.get(_ENV_AUTH_DISABLED) == "1"


def _is_exempt(path: str) -> bool:
    """Return True if the request path is exempt from auth.

    Trailing slashes are stripped before matching (except for root '/').
    """
    normalized = path.rstrip("/") or "/"
    if normalized in _EXEMPT_EXACT:
        return True
    return any(normalized.startswith(prefix) for prefix in _EXEMPT_PREFIXES)


def _constant_time_compare(a: str, b: str) -> bool:
    """Timing-safe string comparison."""
    return hmac.compare_digest(a.encode(), b.encode())


class APIKeyMiddleware(BaseHTTPMiddleware):
    """Middleware that enforces X-Veronica-Key on non-exempt routes."""

    async def dispatch(self, request: Request, call_next: object) -> Response:
        global _warn_once_done  # noqa: PLW0603

        if _is_exempt(request.url.path):
            return await call_next(request)  # type: ignore[arg-type]

        configured_key = _get_configured_key()

        if configured_key is None:
            if _is_auth_disabled():
                with _warn_once_lock:
                    if not _warn_once_done:
                        logger.warning(
                            "%s is not set and %s=1 -- API auth is DISABLED. "
                            "Set %s in production.",
                            _ENV_VAR,
                            _ENV_AUTH_DISABLED,
                            _ENV_VAR,
                        )
                        _warn_once_done = True
                return await call_next(request)  # type: ignore[arg-type]
            return JSONResponse(
                status_code=503,
                content={"detail": "API key not configured"},
            )

        provided = request.headers.get(_HEADER_NAME, "")
        if not provided or not _constant_time_compare(provided, configured_key):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )

        return await call_next(request)  # type: ignore[arg-type]
