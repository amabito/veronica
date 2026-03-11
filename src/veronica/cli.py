# src/veronica/cli.py
"""CLI entry point -- `veronica serve` command."""

from __future__ import annotations

import argparse
import sys


def _valid_port(value: str) -> int:
    """Argparse type validator for TCP port numbers (1-65535)."""
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid port: {value!r}")
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"Port must be 1-65535, got {port}")
    return port


def _serve(host: str, port: int, reload: bool) -> None:
    """Start the Uvicorn server."""
    try:
        import uvicorn
    except ImportError:
        print(
            "ERROR: uvicorn is required. Run: uv add uvicorn[standard]", file=sys.stderr
        )
        sys.exit(1)

    uvicorn.run(
        "veronica.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        reload=reload,
    )


def main() -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="veronica",
        description="Veronica Execution OS -- CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Start the Veronica API server")
    serve_parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)"
    )
    serve_parser.add_argument(
        "--port", type=_valid_port, default=8000, help="Bind port (default: 8000)"
    )
    serve_parser.add_argument(
        "--reload", action="store_true", help="Enable auto-reload (dev mode)"
    )

    args = parser.parse_args()

    if args.command == "serve":
        _serve(host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
