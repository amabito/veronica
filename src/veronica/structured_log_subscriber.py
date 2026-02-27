# src/veronica/structured_log_subscriber.py
"""VERONICA OS observability -- structured JSON log subscriber."""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping

_MAX_SIGNALS_LOG = 16


class StructuredLogSubscriber:
    """JSON structured log emitter. Subscribe to BufferedEmitter.

    Usage::

        emitter = BufferedEmitter()
        emitter.subscribe("structured_log", StructuredLogSubscriber())

    Note: When using a JSON log formatter on the root logger,
    the message field will contain a JSON string, causing double-encoding.
    In that case, subclass and override to use the ``extra`` approach::

        self._logger.log(level, "veronica_event", extra={"veronica": record})
    """

    def __init__(
        self,
        logger_name: str = "veronica.events",
        level: int = logging.INFO,
    ) -> None:
        self._logger = logging.getLogger(logger_name)
        self._level = level

    def __call__(
        self, event_type: str, payload: Mapping[str, Any],
    ) -> None:
        """Callback for BufferedEmitter.subscribe()."""
        if event_type != "step_completed":
            return

        signals = payload.get("signals", [])

        record = {
            "event": event_type,
            "schema_version": payload.get("schema_version", 0),
            "request_id": payload.get("request_id"),
            "step_id": payload.get("step_id"),
            "chain_id": payload.get("chain_id"),
            "kind": payload.get("kind"),
            "status": payload.get("status"),
            "cost_usd": payload.get("cost_usd"),
            "tokens_in": payload.get("tokens_in"),
            "tokens_out": payload.get("tokens_out"),
            "elapsed_ms": payload.get("elapsed_ms"),
            "risk_level": payload.get("risk_level"),
            "recommendation": payload.get("recommendation"),
            "degraded": payload.get("degraded"),
            "degrade_reason": payload.get("degrade_reason"),
            "signals": signals[:_MAX_SIGNALS_LOG],
            "stage_time_ms": payload.get("stage_time_ms", {}),
        }

        self._logger.log(self._level, json.dumps(record, default=str))
