#!/usr/bin/env python3
"""Shadow-run HH chat replies: real LLM, no sending.

 Mimics scripts/reply_iterative_ai.py but never POSTs to /negotiations.
 Prints classifier decisions and the exact reply text that live mode would send.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from reply_iterative_ai import profile_dir, config_path  # noqa: E402
from hh_applicant_tool.automation.reply_fallback import (  # noqa: E402
    FallbackChatAI,
    load_reply_fallback_config,
)
from hh_applicant_tool.automation.reply_worker import (  # noqa: E402
    ACTION_REPLY,
    HHCLI,
    ReplyWorker,
    ReplyWorkerConfig,
    build_ai_client,
    load_json_config,
    sanitize_reply_text,
    reply_quality_issues,
)

logging.basicConfig(level=logging.WARNING)


def render_prompt() -> str:
    root = Path(__file__).resolve().parent.parent
    template = (root / "prompts" / "reply_employer.txt").read_text(encoding="utf-8")
    return template.replace("${HH_NAME}", os.environ.get("HH_NAME", "")).replace(
        "${HH_TELEGRAM}", os.environ.get("HH_TELEGRAM", "")
    )


def build_ai(profile: str, prompt: str) -> FallbackChatAI:
    app_config = load_json_config(config_path(profile))
    primary = build_ai_client(app_config, prompt)
    return FallbackChatAI(primary, load_reply_fallback_config(app_config))


def call_api_retry(hh: HHCLI, endpoint: str, attempts: int = 3) -> dict:
    last: Exception | None = None
    for i in range(attempts):
        try:
            return hh.call_api(endpoint)
        except Exception as exc:  # transient HH API flakes
            last = exc
            time.sleep(3 * (i + 1))
    raise last  # type: ignore[misc]


def generate(worker: ReplyWorker, ai: FallbackChatAI, decision) -> tuple[str | None, list[str]]:
    raw = ai.complete(worker._generation_prompt(decision))
    reply = sanitize_reply_text(raw.strip())
    return reply, reply_quality_issues(reply)


def replay(profile: str, chat_ids: list[str]) -> int:
    """Regenerate answers for a real chat history up to the last employer question."""
    prompt = render_prompt()
    ai = build_ai(profile, prompt)
    worker = ReplyWorker(
        ReplyWorkerConfig(profile_id=profile, dry_run=True, max_chats=1),
        hh=HHCLI(profile),
        ai=ai,
        system_prompt=prompt,
    )
    from hh_applicant_tool.automation.reply_worker import (
        APPLICANT_ROLE,
        EMPLOYER_ROLE,
        build_context,
        classify_chat,
        message_id,
        message_role,
        message_text,
        sorted_messages,
    )

    for chat_id in chat_ids:
        raw = call_api_retry(worker.hh, f"/negotiations/{chat_id}/messages").get("items", [])
        ordered = sorted_messages(raw)
        # Truncate history right after the last employer message, so the model
        # answers the same question the live bot already answered.
        last_employer = max(
            (i for i, m in enumerate(ordered) if message_role(m) == EMPLOYER_ROLE),
            default=-1,
        )
        history = ordered[: last_employer + 1]
        if not history:
            print(f"### {chat_id}: no employer messages")
            continue
        context, initiated_by_us = build_context(history)
        action, reason = classify_chat(history)
        question = message_text(history[-1])
        print(f"### chat {chat_id} | classify={action}:{reason} | initiated_by_us={initiated_by_us}")
        print(f"  employer: {question[:200]!r}")
        if action != ACTION_REPLY:
            print("  (live worker would not reply)")
            continue
        from hh_applicant_tool.automation.reply_worker import ReplyDecision

        decision = ReplyDecision(
            chat_id=chat_id,
            expected_last_message_id=message_id(history[-1]),
            context=context,
            initiated_by_us=initiated_by_us,
            vacancy_name="вакансия",
            employer_name="",
            latest_message_text=question,
        )
        try:
            reply, issues = generate(worker, ai, decision)
        except Exception as exc:
            print(f"  AI ERROR: {exc}")
            continue
        status = "OK" if not issues else f"REJECTED ({'; '.join(issues)})"
        print(f"  would send [{status}]:")
        for line in (reply or "").splitlines():
            print(f"    | {line}")
    return 0


def main() -> int:
    profile = sys.argv[1]
    if profile == "replay":
        return replay(sys.argv[2], sys.argv[3:])
    max_chats = int(sys.argv[2]) if len(sys.argv) > 2 else 15

    prompt = render_prompt()
    app_config = load_json_config(config_path(profile))
    primary = build_ai_client(app_config, prompt)
    ai = FallbackChatAI(primary, load_reply_fallback_config(app_config))

    worker = ReplyWorker(
        ReplyWorkerConfig(profile_id=profile, dry_run=True, max_chats=max_chats),
        hh=HHCLI(profile),
        ai=ai,
        system_prompt=prompt,
    )

    candidates = worker.collect_candidate_chats()
    print(f"### profile {profile}: {len(candidates)} candidate chats with employer turn")
    reply_candidates = 0
    for chat in candidates:
        decision = worker.make_decision(chat)
        if decision is None:
            continue
        if decision.action != ACTION_REPLY:
            print(
                f"[{decision.action}] {decision.chat_id} {decision.vacancy_name[:40]}"
                f" ({decision.reason})"
            )
            continue
        reply_candidates += 1
        # Same generation path as live, minus the send.
        reply = None
        try:
            raw = ai.complete(worker._generation_prompt(decision))
            reply = sanitize_reply_text(raw.strip())
            issues = reply_quality_issues(reply)
        except Exception as exc:  # OpenAIError and config errors
            print(f"[REPLY] {decision.chat_id} {decision.vacancy_name[:40]}: AI ERROR {exc}")
            continue
        status = "OK" if not issues else f"REJECTED ({'; '.join(issues)})"
        print(f"[REPLY] {decision.chat_id} {decision.vacancy_name[:40]} -> {status}")
        print(f"  employer asked: {decision.latest_message_text[:140]!r}")
        print("  would send:")
        for line in reply.splitlines():
            print(f"    | {line}")
    print(f"### profile {profile}: {reply_candidates} replies would be generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
