# Hot Leads In HH Chats

## Definition

A **hot lead** is not just a positive employer reply. In this project the term
means a chat turn that satisfies both conditions:

1. the latest employer message is very likely written by a real recruiter or
   hiring manager rather than an HH system notification, recruiter bot,
   questionnaire or button flow;
2. the message contains a concrete transition to live human contact, such as an
   interview, call, meeting, personal recruiter contact, scheduling question or
   meeting link.

Examples that should normally be hot:

- "Давайте созвонимся завтра. Вам удобно в 15:00?"
- "Приглашаю вас на интервью. Когда сможете поговорить?"
- "Вот мой Telegram: @recruiter, напишите мне."
- a real recruiter sends a Zoom/Meet/Teams link and proposes a time.

Examples that are **not** hot by themselves:

- "Спасибо за отклик, мы рассмотрим резюме."
- "Расскажите про опыт с React."
- salary/work-format screening without a call/interview proposal;
- a test assignment without a live-contact next step;
- recruiter-bot questions;
- "Ваши ответы отправлены работодателю. Если заинтересует, он позвонит.";
- repeated questionnaire questions or explicit button/UI flows.

When evidence is ambiguous, classify as not hot. The goal is a high-precision
notification channel, not maximum recall.

## Pipeline

Hot-lead detection is a side channel next to the existing reply classifier. It
does **not** change the reply action.

```text
latest employer turn
        |
        +--> existing chat classifier --> REPLY_TEXT / IGNORE / MANUAL
        |
        +--> deterministic hot prefilter
                 |
                 +-- no concrete next step / explicit automation --> stop
                 |
                 +-- candidate
                        |
                        +--> LLM hot-lead classifier
                               |
                               +-- hot=false / uncertain --> persist, no alert
                               |
                               +-- hot=true
                                      |
                                      +--> persist/deduplicate in SQLite
                                      +--> Telegram alert if configured
```

The deterministic prefilter exists to keep obvious junk, automated flows and
ordinary screening questions away from the LLM. The LLM is deliberately
fail-closed and confirms hot only when:

- `hot=true`;
- confidence is at least `HOT_LEAD_MIN_CONFIDENCE` (default `0.85`);
- `human_likelihood == "high"`.

A failure in hot-lead classification or Telegram delivery must never block an
otherwise valid HH reply.

## Persistence And Deduplication

Evaluated candidates are stored in the profile SQLite database in
`hot_lead_events`, keyed by `(chat_id, message_id)`.

This has two purposes:

- the same employer turn is not sent through the LLM every hourly run;
- a confirmed hot lead is notified at most once after a successful Telegram
  delivery.

If Telegram delivery fails, the row remains `notified=0`. Pending
notifications are retried at the start of a later live reply pass even if the
chat has already moved on.

Use:

```bash
hh-applicant-tool --profile-id PROFILE hot-leads
hh-applicant-tool --profile-id PROFILE --json hot-leads
```

to inspect confirmed hot leads locally.

## Telegram

Configure in the runtime `.env`:

```text
HOT_LEADS_ENABLED=1
HOT_LEAD_MIN_CONFIDENCE=0.85
HOT_LEAD_TELEGRAM_BOT_TOKEN=<secret>
HOT_LEAD_TELEGRAM_CHAT_ID=<target chat id>
HOT_LEAD_TELEGRAM_TIMEOUT=10
```

Both Telegram values are required for delivery. Detection and SQLite
persistence still work if Telegram is not configured.

The bot token is a secret. Do not put it in Git, logs, ops snapshots, generated
Markdown or `/tmp/hh-runtime.env`.

A Telegram alert contains:

- HH profile/account;
- vacancy;
- employer;
- confidence;
- short classifier reason;
- suggested next step;
- the triggering employer message.

## Dry-run Semantics

Dry-run keeps the existing no-write contract:

- deterministic hot-lead prefilter runs;
- no hot-lead LLM call;
- no SQLite hot-lead writes;
- no Telegram request.

The run reports only `hot_candidates` for messages that passed the cheap
prefilter.

## Agent Guidance

Agents working in this repository must preserve these invariants:

- **hot lead is orthogonal to reply action**; do not turn hot leads into a new
  auto-reply action without an explicit product decision;
- optimize for precision: false Telegram alarms are more harmful than missing a
  weak/ambiguous lead;
- do not classify automated questionnaires as human interest merely because
  they contain words such as "интервью" or "позвонит";
- never infer that a sender is human solely from the HH employer role;
- keep notification failures isolated from HH reply failures;
- keep event-level message text out of privacy-safe `ops/*.json`; only
  aggregate counters/table counts may be published there;
- do not log Telegram bot tokens or include them in exception messages.
