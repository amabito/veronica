# src/veronica/api/app.py
"""FastAPI application factory for the Veronica control-plane API."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from veronica.api.auth import APIKeyMiddleware
from veronica.api.policy_registry import PolicyRegistry
from veronica.distribution.policy_distributor import PolicyDistributor
from veronica.ingest.event_ingestor import EventIngestor
from veronica.store import MemoryStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize and teardown shared resources."""
    store = MemoryStore()
    distributor = PolicyDistributor()
    ingestor = EventIngestor()
    registry = PolicyRegistry(distributor)

    app.state.store = store
    app.state.distributor = distributor
    app.state.ingestor = ingestor
    app.state.registry = registry

    logger.info("Veronica API started")
    yield
    logger.info("Veronica API shutting down")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    from veronica.api.routes import events as events_router
    from veronica.api.routes import export as export_router
    from veronica.api.routes import health as health_router
    from veronica.api.routes import policies as policies_router
    from veronica.api.routes import simulate as simulate_router
    from veronica.ui.router import mount_static
    from veronica.ui.router import router as ui_router

    app = FastAPI(
        title="VERONICA Control Plane API",
        version="0.7.1",
        description=(
            "Execution OS API for LLM systems.\n\n"
            "Provides policy management, event audit, and side-effect-free simulation "
            "built on veronica-core."
        ),
        openapi_tags=[
            {"name": "health", "description": "Liveness and readiness checks"},
            {"name": "policies", "description": "Policy CRUD and versioned updates"},
            {"name": "simulate", "description": "Side-effect-free policy simulation"},
            {"name": "events", "description": "Paginated, filterable event audit log"},
            {"name": "export", "description": "Full JSON export for backup and migration"},
        ],
        lifespan=_lifespan,
    )

    app.add_middleware(APIKeyMiddleware)

    _cors_env = os.environ.get("VERONICA_CORS_ORIGINS", "")
    if _cors_env and _cors_env != "*":
        # Explicit origin list with credentials enabled
        _cors_origins: list[str] = [o.strip() for o in _cors_env.split(",") if o.strip()]
        _cors_credentials = True
    else:
        # Default or explicit wildcard -- credentials must be disabled (browser security requirement)
        _cors_origins = ["*"]
        _cors_credentials = False

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=_cors_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s", exc)
        detail = str(exc) if os.environ.get("VERONICA_DEBUG") == "1" else "Internal server error"
        return JSONResponse(status_code=500, content={"detail": detail})

    app.include_router(health_router.router)
    app.include_router(events_router.router)
    app.include_router(policies_router.router)
    app.include_router(simulate_router.router)
    app.include_router(export_router.router)
    app.include_router(ui_router)
    mount_static(app)

    return app
