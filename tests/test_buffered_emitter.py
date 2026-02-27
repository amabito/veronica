# tests/test_buffered_emitter.py
"""Tests for veronica.buffered_emitter -- ring buffer event emitter."""
from __future__ import annotations

import threading
import time

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

    def test_dropped_total_counts_overflow(self) -> None:
        """emit 5 events into maxlen=2 buffer -- dropped_total == 3."""
        emitter = BufferedEmitter(maxlen=2)
        for i in range(5):
            emitter.emit("e", {"i": i})
        assert emitter.dropped_total == 3


class TestBufferedEmitterThreadSafety:
    def test_concurrent_emit(self) -> None:
        """10 threads emit simultaneously -- no data corruption."""
        emitter = BufferedEmitter()
        barrier = threading.Barrier(10)

        def worker(idx: int) -> None:
            barrier.wait()
            emitter.emit("e", {"idx": idx})

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        events = emitter.snapshot()
        assert len(events) == 10
        indices = sorted(e[1]["idx"] for e in events)
        assert indices == list(range(10))

    def test_reentrant_emit_from_subscriber(self) -> None:
        """Subscriber calling emit() inside callback -- no deadlock."""
        emitter = BufferedEmitter()
        inner_called = threading.Event()

        def reentrant_callback(et: str, p: dict) -> None:
            if et == "outer":
                emitter.emit("inner", {"from": "callback"})
                inner_called.set()

        emitter.subscribe("reentrant", reentrant_callback)
        emitter.emit("outer", {})

        assert inner_called.is_set()
        events = emitter.snapshot()
        types = [e[0] for e in events]
        assert "outer" in types
        assert "inner" in types

    def test_slow_subscriber_does_not_block_others(self) -> None:
        """Slow subscriber doesn't prevent other subscribers from being called."""
        emitter = BufferedEmitter()
        fast_received = threading.Event()

        def slow_callback(et: str, p: dict) -> None:
            time.sleep(0.1)

        def fast_callback(et: str, p: dict) -> None:
            fast_received.set()

        emitter.subscribe("slow", slow_callback)
        emitter.subscribe("fast", fast_callback)
        emitter.emit("test", {})

        assert fast_received.is_set()

    def test_subscribe_during_emit(self) -> None:
        """subscribe/unsubscribe from another thread during emit -- no crash."""
        emitter = BufferedEmitter()
        results: list[bool] = []

        def slow_callback(et: str, p: dict) -> None:
            time.sleep(0.05)

        emitter.subscribe("slow", slow_callback)

        def subscribe_thread() -> None:
            time.sleep(0.01)  # mid-emit
            emitter.subscribe("late", lambda et, p: None)
            results.append(True)

        t = threading.Thread(target=subscribe_thread)
        t.start()
        emitter.emit("test", {})
        t.join()

        assert results == [True]

    def test_fatal_exception_propagates(self) -> None:
        """SystemExit/KeyboardInterrupt from subscriber propagates."""
        emitter = BufferedEmitter()
        emitter.subscribe("fatal", lambda et, p: (_ for _ in ()).throw(KeyboardInterrupt))
        with pytest.raises(KeyboardInterrupt):
            emitter.emit("test", {})
