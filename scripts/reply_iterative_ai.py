#!/usr/bin/env python3
"""Cron-oriented HH chat reply worker.

The worker uses the applicant /negotiations API, revalidates the exact last
employer message immediately before sending, and treats malformed/ambiguous
message ordering as non-actionable. Humanizer Framework selects its own provider.
Failed generation leaves the chat unanswered for review.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from hh_applicant_tool.automation.reply_state import (
    EmployerContactStore,
    ManualChatQueue,
    ReplySendLog,
)
from hh_applicant_tool.automation.reply_worker import (
    HHCLI,
    ReplyWorker,
    ReplyWorkerConfig,
)
from hh_applicant_tool.communication import (
    FrameworkWriter,
)
from hh_applicant_tool.constants import CONFIG_DIR, DATABASE_FILENAME
from hh_applicant_tool.utils.config import resolve_profile_config_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safe autonomous HH chat replies")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="Send real replies")
    mode.add_argument("--dry-run", action="store_true", help="Never send replies")
    parser.add_argument("--profile", default=os.environ.get("HH_PROFILE_ID", ""))
    parser.add_argument(
        "--max-chats",
        type=int,
        default=int(os.environ.get("REPLY_CHATS", os.environ.get("CHATS", "100"))),
        help="Maximum negotiations to inspect per scan (default: 100)",
    )
    parser.add_argument(
        "--rounds", type=int, default=int(os.environ.get("REPLY_ROUNDS", "1")),
        help="How many times to rescan chats during this run (default: 1)",
    )
    parser.add_argument(
        "--round-interval", type=int,
        default=int(os.environ.get("REPLY_ROUND_INTERVAL", "60")),
        help="Seconds between scans when --rounds is greater than 1 (default: 60)",
    )
    return parser.parse_args()


def profile_dir(profile_id: str) -> Path:
    base_dir = Path(os.environ.get("CONFIG_DIR", str(CONFIG_DIR)))
    return resolve_profile_config_dir(base_dir, profile_id)


def config_path(profile_id: str) -> Path:
    return profile_dir(profile_id) / "config.json"


def main() -> int:
    args = parse_args()
    if args.max_chats <= 0:
        print("--max-chats must be a positive integer", file=sys.stderr)
        return 2
    if not 1 <= args.rounds <= 5 or args.round_interval < 0:
        print("--rounds must be 1..5 and --round-interval must be non-negative", file=sys.stderr)
        return 2

    dry_run = not args.live
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    framework_writer = None
    if not dry_run:
        framework_writer = FrameworkWriter()

    manual_queue = None
    send_log = None
    db_path = profile_dir(args.profile) / DATABASE_FILENAME
    contact_store = EmployerContactStore(db_path)
    if not dry_run:
        manual_queue = ManualChatQueue(db_path)
        send_log = ReplySendLog(db_path)

    worker = ReplyWorker(
        ReplyWorkerConfig(
            profile_id=args.profile,
            dry_run=dry_run,
            max_chats=args.max_chats,
            skip_chat_ids=tuple(
                x.strip() for x in os.environ.get("HH_REPLY_SKIP", "").split(",") if x.strip()
            ),
        ),
        hh=HHCLI(args.profile),
        manual_queue=manual_queue,
        framework_writer=framework_writer,
        send_log=send_log,
        contact_store=contact_store,
    )
    stats: dict[str, int] = {}
    for round_number in range(1, args.rounds + 1):
        if round_number > 1:
            time.sleep(args.round_interval)
        logger = logging.getLogger(__name__)
        logger.info("Reply scan %d/%d", round_number, args.rounds)
        round_stats = worker.run()
        logger.info("Reply scan %d result: %s", round_number, round_stats)
        for key, value in round_stats.items():
            stats[key] = stats.get(key, 0) + value
    stats["rounds"] = args.rounds
    stats["fallback"] = 0
    print(json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
