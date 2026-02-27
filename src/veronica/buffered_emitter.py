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

    Thread-safe. Lock protects internal state only; subscriber callbacks
    execute outside the lock. Subscribers are assumed to be few (< 50).

    Re-entrancy: Subscribers may call emit() from within their callback
    without deadlock. Infinite recursion is the caller's responsibility.
    """

    def __init__(self, maxlen: int = 1024) -> None:
        self._buffer: deque[tuple[str, Mapping[str, Any]]] = deque(maxlen=maxlen)
        self._subscribers: dict[str, Callable[[str, Mapping[str, Any]], None]] = {}
        self._fail_counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def emit(self, event_type: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            self._buffer.append((event_type, payload))
            targets = list(self._subscribers.items())

        local_resets: list[str] = []
        local_removals: list[str] = []

        for name, callback in targets:
            try:
                callback(event_type, payload)
                local_resets.append(name)
            except (SystemExit, KeyboardInterrupt):
                raise
            except Exception:
                with self._lock:
                    count = self._fail_counts.get(name, 0) + 1
                    self._fail_counts[name] = count
                    if count >= _MAX_CONSECUTIVE_FAILURES:
                        local_removals.append(name)

        if local_resets or local_removals:
            with self._lock:
                for name in local_resets:
                    if name in self._fail_counts:
                        self._fail_counts[name] = 0
                for name in local_removals:
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
        with self._lock:
            self._subscribers[name] = callback
            self._fail_counts[name] = 0

    def unsubscribe(self, name: str) -> None:
        with self._lock:
            self._subscribers.pop(name, None)
            self._fail_counts.pop(name, None)

    def drain(self, n: int) -> list[tuple[str, Mapping[str, Any]]]:
        with self._lock:
            result: list[tuple[str, Mapping[str, Any]]] = []
            for _ in range(min(n, len(self._buffer))):
                result.append(self._buffer.popleft())
        return result

    def snapshot(self) -> list[tuple[str, Mapping[str, Any]]]:
        with self._lock:
            return list(self._buffer)
