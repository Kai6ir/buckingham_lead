"""Headless runner: run the bot in the terminal without the dashboard.

  python -m leadgen.cli run            # live run (needs SERPAPI_KEY in .env)
  python -m leadgen.cli run --demo     # offline synthetic run
  python -m leadgen.cli run --target 50
"""

from __future__ import annotations

import argparse
import dataclasses

from .config import Settings
from .controller import BotController


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="leadgen")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="run the lead bot once")
    run.add_argument("--demo", action="store_true", help="use synthetic data (no API calls)")
    run.add_argument("--target", type=int, help="override run.target_leads")
    run.add_argument("--config", help="path to a config YAML (default: config.yaml)")
    args = parser.parse_args(argv)

    settings = Settings.load(args.config)
    if args.target:
        settings = dataclasses.replace(settings, target_leads=args.target)
    ctrl = BotController(settings)
    mode = "demo" if args.demo else "live"
    ok, msg = ctrl.start(mode)
    if not ok:
        print(f"Could not start: {msg}")
        return 1
    if msg:
        print(f"Note: {msg}")
    ctrl.wait()
    snap = ctrl.snapshot()
    print(f"\nStatus: {snap['status']}  |  counts: {snap['counts']}")
    if snap.get("error"):
        print(f"Error: {snap['error']}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
