from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING

from ..automation.hot_lead_state import HotLeadStore
from ..main import BaseNamespace, BaseOperation

if TYPE_CHECKING:
    from ..main import HHApplicantTool


class Namespace(BaseNamespace):
    limit: int


class Operation(BaseOperation):
    """Показать последние подтверждённые hot leads для профиля."""

    __aliases__ = ["hot-interviews"]

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--limit", type=int, default=100)

    def run(self, tool: HHApplicantTool, args: Namespace) -> None:
        store = HotLeadStore(tool.db_path)
        payload = store.recent_hot(max(args.limit, 1))

        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return

        if not payload:
            print("Hot lead queue is empty.")
            return

        for item in payload:
            status = "notified" if item.get("notified") else "pending-notification"
            print(
                f"[{status}] {item.get('vacancy_name') or 'vacancy'} "
                f"({item.get('employer_name') or 'employer'})"
            )
            print(
                f"  confidence={float(item.get('confidence') or 0):.0%} "
                f"human={item.get('human_likelihood') or 'unknown'}"
            )
            print(f"  reason: {item.get('reason') or ''}")
            if item.get("next_step"):
                print(f"  next: {item['next_step']}")
            print(f"  message: {item.get('message_text') or ''}")
