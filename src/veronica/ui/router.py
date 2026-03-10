# src/veronica/ui/router.py
"""FastAPI router for serving the Veronica web UI static files."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static"

router = APIRouter(tags=["ui"])


def mount_static(app: object) -> None:
    """Mount static files on the FastAPI app (call after app creation)."""
    app.mount("/ui/static", StaticFiles(directory=str(STATIC_DIR)), name="ui_static")  # type: ignore[attr-defined]


@router.get("/", include_in_schema=False)
async def root_redirect() -> RedirectResponse:
    """Redirect root to the dashboard UI."""
    return RedirectResponse(url="/ui/")


@router.get("/ui/", include_in_schema=False)
async def dashboard_index() -> FileResponse:
    """Serve the dashboard HTML."""
    return FileResponse(str(STATIC_DIR / "index.html"))


@router.get("/ui/events", include_in_schema=False)
async def events_page() -> FileResponse:
    """Serve the event log HTML."""
    return FileResponse(str(STATIC_DIR / "events.html"))


@router.get("/ui/incident", include_in_schema=False)
async def incident_page() -> FileResponse:
    """Serve the incident detail HTML."""
    return FileResponse(str(STATIC_DIR / "incident.html"))


@router.get("/ui/policies", include_in_schema=False)
async def policies_page() -> FileResponse:
    """Serve the policy editor HTML."""
    return FileResponse(str(STATIC_DIR / "policies.html"))


@router.get("/ui/replay", include_in_schema=False)
async def replay_page() -> FileResponse:
    """Serve the incident replay HTML."""
    return FileResponse(str(STATIC_DIR / "replay.html"))


@router.get("/ui/rollouts", include_in_schema=False)
async def rollouts_page() -> FileResponse:
    """Serve the rollout pipeline HTML."""
    return FileResponse(str(STATIC_DIR / "rollouts.html"))
