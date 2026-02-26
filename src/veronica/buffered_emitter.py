# src/veronica/buffered_emitter.py
"""VERONICA OS emitter -- BufferedEmitter with ring buffer and subscribers."""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Callable, Mapping

logger = logging.getLogger(__name__)

_MAX_CONSECUTIVE_FAILURES = 3


class BufferedEmitter:
    """Phase 2 event emitter with ring buffer and subscriber management.

    Events are stored in a bounded deque. Subscribers receive synchronous
    callbacks. After 3 consecutive failures, a subscriber is auto-removed.
    """

    def __init__(self, maxlen: int = 1024) -> None:
        self._buffer: deque[tuple[str, Mapping[str, Any]]] = deque(maxlen=maxlen)
        self._subscribers: dict[str, Callable[[str, Mapping[str, Any]], None]] = {}
        self._fail_counts: dict[str, int] = {}

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        assert threading.current_thread() is threading.main_thread(), (
            "BufferedEmitter.emit() must be called from the main thread"
        )
        self._buffer.append((event_type, payload))
        for name in list(self._subscribers):
            callback = self._subscribers.get(name)
            if callback is None:
                continue
            try:
                callback(event_type, payload)
                self._fail_counts[name] = 0
            except Exception:
                count = self._fail_counts.get(name, 0) + 1
                self._fail_counts[name] = count
                if count >= _MAX_CONSECUTIVE_FAILURES:
                    self._subscribers.pop(name, None)
                    self._fail_counts.pop(name, None)
                    logger.warning(
                        "Auto-unsubscribed '%s' after %d consecutive failures",
                        name,
                        _MAX_CONSECUTIVE_FAILURES,
                    )

    def subscribe(
        self,
        name: str,
        callback: Callable[[str, Mapping[str, Any]], None],
    ) -> None:
        self._subscribers[name] = callback
        self._fail_counts[name] = 0

    def unsubscribe(self, name: str) -> None:
        self._subscribers.pop(name, None)
        self._fail_counts.pop(name, None)

    def drain(self, n: int) -> list[tuple[str, Mapping[str, Any]]]:
        result: list[tuple[str, Mapping[str, Any]]] = []
        for _ in range(min(n, len(self._buffer))):
            result.append(self._buffer.popleft())
        return result

    def snapshot(self) -> list[tuple[str, Mapping[str, Any]]]:
        return list(self._buffer)
