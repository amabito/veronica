# tests/test_buffered_emitter.py
"""Tests for veronica.buffered_emitter -- ring buffer event emitter."""
from __future__ import annotations

import pytest

from veronica.buffered_emitter import BufferedEmitter


class TestBufferedEmitter:
    def test_emit_stores_event(self) -> None:
        emitter = BufferedEmitter()
        emitter.emit("step_completed", {"step_id": "s1"})
        events = emitter.snapshot()
        assert len(events) == 1
        assert events[0] == ("step_completed", {"step_id": "s1"})

    def test_ring_buffer_maxlen(self) -> None:
        emitter = BufferedEmitter(maxlen=3)
        for i in range(5):
            emitter.emit("e", {"i": i})
        events = emitter.snapshot()
        assert len(events) == 3
        assert events[0][1]["i"] == 2  # oldest kept

    def test_drain_removes_events(self) -> None:
        emitter = BufferedEmitter()
        emitter.emit("a", {})
        emitter.emit("b", {})
        emitter.emit("c", {})
        drained = emitter.drain(2)
        assert len(drained) == 2
        assert drained[0][0] == "a"
        assert drained[1][0] == "b"
        remaining = emitter.snapshot()
        assert len(remaining) == 1

    def test_subscriber_receives_events(self) -> None:
        emitter = BufferedEmitter()
        received: list[tuple[str, dict]] = []
        emitter.subscribe("test_sub", lambda et, p: received.append((et, p)))
        emitter.emit("x", {"val": 1})
        assert len(received) == 1
        assert received[0] == ("x", {"val": 1})

    def test_unsubscribe(self) -> None:
        emitter = BufferedEmitter()
        received: list = []
        emitter.subscribe("test_sub", lambda et, p: received.append(1))
        emitter.unsubscribe("test_sub")
        emitter.emit("x", {})
        assert len(received) == 0

    def test_auto_unsubscribe_after_3_failures(self) -> None:
        emitter = BufferedEmitter()

        def bad_callback(et: str, p: dict) -> None:
            raise RuntimeError("boom")

        emitter.subscribe("bad", bad_callback)
        for _ in range(3):
            emitter.emit("e", {})
        # After 3 failures, subscriber should be gone
        assert "bad" not in emitter._subscribers

    def test_snapshot_is_nondestructive(self) -> None:
        emitter = BufferedEmitter()
        emitter.emit("a", {})
        snap1 = emitter.snapshot()
        snap2 = emitter.snapshot()
        assert snap1 == snap2
        assert len(emitter.snapshot()) == 1

    def test_emit_protocol_compatible(self) -> None:
        """Satisfies EventEmitterProtocol."""
        from veronica.protocols import EventEmitterProtocol

        emitter = BufferedEmitter()
        assert isinstance(emitter, EventEmitterProtocol)
