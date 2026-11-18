# HH upstream sync status — 2026-11-18

Source: s3rgeym/hh-applicant-tool, main at
fe7e4fe6214a8452c4e2f7aefb45cef23939599a (upstream v2.5.5).
Our base: orelkrylatiy/hh-ops main at
7ea8fbebc17e92a34ab11d66176f484d677e29bc (fork v1.8.26).

## Why upstream is not merged verbatim

GitHub compare reports no common ancestor for the current main branches.
Upstream has 52 tracked files and our fork has 276 tracked files plus
admin, SQLite, AI framework, multi-profile runtime and routing. A blind
merge would replace functionality we intentionally maintain.

## Integrated

| Upstream change | HH-OPS adaptation |
| --- | --- |
| 15 min human response grace period | Configurable and timestamp-validated |
| Immediate bot replies | Only when the payload explicitly identifies a bot |
| Two-day reminders | Opt-in, one reminder maximum per chat, durable SQLite claim |
| Skip 30-day-old chats | Configurable age limit; unknown timestamps fail closed |
| Quick reply options | Optional choice only from explicit provided options; otherwise MANUAL |
| Multiple-choice test fix | Verify candidate ID, never invent an option |
| Desktop User-Agent Chrome 156 | Refreshed and overridable through environment |
| Pipeline dependencies | Reuse fixes from our verified CI PR #15 |

## Deliberate boundaries

The original reply_chats.py accesses the private browser /chatik/api
interface, including quick-reply lookup, read/typing notifications and
other XHR-only actions. Our main worker uses the applicant /negotiations
API. It does not claim full chatik parity, and does not use that private
endpoint. The option selection feature is effective only when HH includes
options in our available payload.

Upstream config.toml, full tool.py and AI-client rewrites are not
compatible with our per-profile JSON configuration, Humanizer Framework,
Playwright/OpenCode or SQLite architecture. Python 3.12 is supported by
our Python ^3.11 range already. Version numbers do not share a release
line and should not be synchronized mechanically.

## Rollout safety

New outbound features are disabled unless explicitly enabled.
HH_AUTOMATION_MODE remains off by default, independent of these flags.
The live reply worker still validates the latest message immediately
before sending, checks for duplicates, and uses per-profile SQLite state.
A pending follow-up claim is deliberately not automatically retried
after an ambiguous send, to avoid duplicating unsolicited messages.

Example environment settings:

    HH_REPLY_HUMAN_COOLDOWN_MINUTES=15
    HH_REPLY_MAX_CHAT_AGE_DAYS=30
    HH_REPLY_FOLLOWUPS_ENABLED=off
    HH_REPLY_FOLLOWUP_AFTER_DAYS=2
    HH_REPLY_AUTO_QUICK_REPLIES=off

Supported direct CLI flags include --human-cooldown-minutes,
--max-chat-age-days, --followup-after-days, --followups,
and --auto-quick-replies.

## Remaining parity verification

Full parity requires real HH session snapshots confirming bot metadata,
chatik quick-reply semantics, read/typing event requirements, refusals
and the CAPTCHA/XHR response patterns. Without those live tests it would
be incorrect to claim every upstream web-private behaviour is supported.
Review upstream licensing obligations before commercial distribution.
