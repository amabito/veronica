# tests/test_metrics_exporter.py
"""Tests for metrics_exporter -- Prometheus HTTP server helper."""
from __future__ import annotations

import veronica.metrics_exporter as mod


class TestStartMetricsServer:
    def setup_method(self) -> None:
        """Reset module state between tests."""
        mod._started = False

    def test_returns_true_on_start(self, monkeypatch) -> None:
        """First call starts server and returns True."""
        started_with = {}

        def fake_start(port, addr=""):
            started_with["port"] = port
            started_with["addr"] = addr

        monkeypatch.setattr(
            "prometheus_client.start_http_server", fake_start,
        )
        result = mod.start_metrics_server(port=9999)
        assert result is True
        assert started_with["port"] == 9999

    def test_double_call_returns_true_without_restart(self, monkeypatch) -> None:
        """Second call returns True without starting again."""
        call_count = 0

        def fake_start(port, addr=""):
            nonlocal call_count
            call_count += 1

        monkeypatch.setattr(
            "prometheus_client.start_http_server", fake_start,
        )
        mod.start_metrics_server(port=9998)
        mod.start_metrics_server(port=9998)
        assert call_count == 1

    def test_env_var_port(self, monkeypatch) -> None:
        """VERONICA_METRICS_PORT env var overrides default."""
        started_with = {}

        def fake_start(port, addr=""):
            started_with["port"] = port

        monkeypatch.setattr(
            "prometheus_client.start_http_server", fake_start,
        )
        monkeypatch.setenv("VERONICA_METRICS_PORT", "8888")
        mod.start_metrics_server()
        assert started_with["port"] == 8888

    def test_default_port(self, monkeypatch) -> None:
        """Default port is 9464."""
        started_with = {}

        def fake_start(port, addr=""):
            started_with["port"] = port

        monkeypatch.setattr(
            "prometheus_client.start_http_server", fake_start,
        )
        monkeypatch.delenv("VERONICA_METRICS_PORT", raising=False)
        mod.start_metrics_server()
        assert started_with["port"] == 9464

    def test_import_error_returns_false(self, monkeypatch) -> None:
        """Returns False when prometheus_client is not installed."""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "prometheus_client":
                raise ImportError("no prometheus_client")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        result = mod.start_metrics_server(port=9997)
        assert result is False
