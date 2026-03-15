#!/usr/bin/env python3
"""Run chat JSON outbox retry worker."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from env_loader import load_workspace_env
from server.services.conversation.chat_json_outbox_worker import ChatJsonOutboxWorker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run chat JSON outbox worker")
    parser.add_argument("--once", action="store_true", help="run one cycle then exit")
    parser.add_argument(
        "--max-loops",
        type=int,
        default=0,
        help="max loops in run-forever mode (0 means infinite)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="python logging level (default: INFO)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_workspace_env(override_existing=False)

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    worker = ChatJsonOutboxWorker()

    if args.once:
        summary = worker.run_once()
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    max_loops = int(args.max_loops or 0)
    summary = worker.run_forever(max_loops=max_loops if max_loops > 0 else None)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
