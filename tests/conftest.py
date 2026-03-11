# tests/conftest.py
"""Shared fixtures for VERONICA tests."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _disable_auth_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable API key auth for all tests unless VERONICA_API_KEY is already set.

    Tests that exercise auth directly (test_api_auth.py) manage their own
    environment via monkeypatch and will override this fixture.
    """
    if not os.environ.get("VERONICA_API_KEY"):
        monkeypatch.setenv("VERONICA_AUTH_DISABLED", "1")
