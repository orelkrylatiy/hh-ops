# Selective upstream sync — 2026-11-23

Source: `s3rgeym/hh-applicant-tool` at
[`01fc921`](https://github.com/s3rgeym/hh-applicant-tool/commit/01fc9211770a1d4df95c0f554d8dedd3128250e7)
(upstream package version 2.5.8). Prior HH Ops review:
[`UPSTREAM_SYNC_2026-11-18.md`](UPSTREAM_SYNC_2026-11-18.md),
which reviewed upstream 2.5.5.

## Integrated / validated

| Upstream feature or fix | HH Ops treatment |
| --- | --- |
| Configurable WebView User-Agent | Adapted to Playwright OAuth browser context. Optional `--webview-user-agent`, `HH_WEBVIEW_USER_AGENT`, or per-profile `config.json: webview_user_agent`. |
| Web-page 401/403 authorization failures | Early actionable exception in `parse_redirect_config`, before trying to parse HH-Lux-InitialState. |
| CLI overrides for config path | Already worked through `--config-dir` / `--profile-id`; regression test added, no code replacement. |
| Alternative API `client_id`, `client_secret`, `user_agent` | Already supported by the per-profile JSON config; retained. |
| Consistent desktop User-Agent | Fixed web requests session to honor CLI `--user-agent`, then `HH_DESKTOP_USER_AGENT`, then per-profile `user_agent`, then default. The API client still explicitly provides its own headers. |

## WebView User-Agent examples

For a specific profile, use one of the following (priority: CLI > env > JSON config):

```bash
hh-applicant-tool --profile-id 0056 --webview-user-agent 'YOUR_WEBVIEW_UA' authorize
HH_WEBVIEW_USER_AGENT='YOUR_WEBVIEW_UA' hh-applicant-tool --profile-id 0056 authorize
hh-applicant-tool --profile-id 0056 config --set webview_user_agent 'YOUR_WEBVIEW_UA'
```

This setting is used when creating the Android-emulated Playwright context
for **OAuth authorization**, not for the public HH API and not to change
the desktop CAPTCHA browser. With no override, the existing `Galaxy A55`
Playwright device settings remain unchanged. Use a UA compatible with the
actual browser engine; an arbitrary UA may make authorization less reliable.

## Intentional non-changes

- **No direct merge:** GitHub reports no common ancestor between current
  `main` histories and the fork implements a different runtime.
- **No upstream TOML/config refactor:** HH Ops keeps its per-profile JSON
  configuration and SQLite state.
- **No replacement of the reply worker:** it keeps the safeguards from the
  2026-11-18 review.
- **No automatic live HH requests or sends** were made as part of the sync.

## Verification

Regression tests: `tests/test_upstream_2026_11_23.py`.
Run alongside existing compatibility tests:

```bash
pytest -q tests/test_upstream_2026_11_23.py tests/test_upstream_compat.py tests/test_http_session_security.py
```

CI tests and a live account authorization smoke test are separate steps.
OAuth/cookie persistence must be checked with a user-authorized HH account
before relying on a changed WebView UA in production.
