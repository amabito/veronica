# tests/test_emitter.py
"""Tests for veronica.emitter -- NullEmitter."""

from __future__ import annotations

from veronica.emitter import NullEmitter


class TestNullEmitter:
    def test_emit_noop(self) -> None:
        emitter = NullEmitter()
        emitter.emit("test_event", {"key": "value"})
        # No exception, no side effects

    def test_emit_exception_swallowed(self) -> None:
        """Even if payload is weird, no crash."""
        emitter = NullEmitter()
        emitter.emit("", {})
