# src/veronica/emitter.py
"""VERONICA OS emitter -- NullEmitter (no-op)."""

from __future__ import annotations

import logging
from typing import Any, Mapping

logger = logging.getLogger(__name__)


class NullEmitter:
    """No-op event emitter. Discards all events silently."""

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        pass
