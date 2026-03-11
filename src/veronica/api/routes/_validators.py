# src/veronica/api/routes/_validators.py
"""Shared validation constants for route modules."""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

CHAIN_ID_RE = r"^[a-zA-Z0-9_:@.\-]+$"
SafeChainId = Annotated[
    str, Path(min_length=1, max_length=256, pattern=CHAIN_ID_RE)
]
