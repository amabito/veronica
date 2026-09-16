"""Foreground CLI. This program runs autonomously only while the operator runs it."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

from . import Autopilot, Document, Stopped, initialize


def main(argv=None):
    parser = argparse.ArgumentParser(description="VERONICA delegated document autopilot")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="explicitly delegate to a NEW empty local workspace")
    init.add_argument("--workspace", type=Path, required=True)
    init.add_argument("--issuer", action="append", required=True)
    init.add_argument("--valid-days", type=int, default=30)
    for name in ("run", "watch"):
        sub = commands.add_parser(name)
        sub.add_argument("--policy", type=Path, required=True)
        if name == "watch":
            sub.add_argument("--interval", type=int, default=10)
    demo = commands.add_parser("demo", help="create synthetic documents and execute TWO real runs")
    demo.add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            print(initialize(args.workspace, tuple(args.issuer), valid_days=args.valid_days))
            return 0
        if args.command == "demo":
            policy = initialize(args.workspace, ("Example Supplier",))
            inbox = policy.parent.parent / "inbox"
            good = Document("DEMO-001", "invoice", "Example Supplier", "2026-09-16",
                            "Synthetic office supplies", "12000", "JPY")
            (inbox / "invoice.json").write_text(good.json, encoding="utf-8")
            (inbox / "duplicate.json").write_text(good.json, encoding="utf-8")
            (inbox / "needs-review.txt").write_text("Unstructured example requiring review", encoding="utf-8")
            first = Autopilot(policy).run_once()
            second = Autopilot(policy).run_once()
            print(json.dumps({"first": first, "second": second}, ensure_ascii=False, indent=2))
            assert first['counts']['completed'] == 1
            assert second['counts']['completed'] == 0
            assert second['counts']['already_completed'] == 2
            return 0
        if args.command == "watch" and not 1 <= args.interval <= 3600:
            raise ValueError("interval_outside_1_to_3600")
        runner = Autopilot(args.policy)
        while True:
            report = runner.run_once()
            print(json.dumps(report, ensure_ascii=False), flush=True)
            if args.command == "run":
                return 2 if (report['counts']['review_required'] or report['counts']['retryable']
                             or report['pending_jobs'] or report['deferred']) else 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 130
    except (Stopped, ValueError, OSError, ImportError):
        # No traceback containing customer text or paths by default.
        print(json.dumps({"status": "stopped", "reason": "check_delegation_workspace_and_dependencies"}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
