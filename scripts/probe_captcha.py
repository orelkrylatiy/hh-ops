"""Inspect a saved HH challenge without submitting any vacancy applications."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hh_applicant_tool.main import BaseNamespace, HHApplicantTool
from hh_applicant_tool.operations.apply_vacancies import (
    _captcha_browser_url,
    _effective_captcha_proxy,
    _playwright_cookies,
)


async def inspect(profile: str, callback: str, fresh: bool) -> None:
    from playwright.async_api import async_playwright

    tool = HHApplicantTool()
    tool._assign_args(
        tool._parser.parse_args(
            ["--profile", profile, "--no-auto-auth", "whoami"], namespace=BaseNamespace()
        )
    )
    log = Path(f"logs/profiles/{profile}-apply.log").read_text(encoding="utf-8")
    challenge = re.findall(r"https://hh.ru/account/captcha\?state=\S+", log)[-1]
    if fresh:
        from hh_applicant_tool.api.errors import ApiError, CaptchaRequired

        try:
            # Both identifiers are intentionally invalid: this cannot create
            # an application, even if HH allows the request past its guard.
            tool.api_client.post(
                "/negotiations",
                {
                    "vacancy_id": "0",
                    "resume_id": "captcha-diagnostic-invalid-resume",
                },
            )
        except CaptchaRequired as exc:
            challenge = exc.captcha_url
        except ApiError as exc:
            print(
                f"Invalid application probe returned {type(exc).__name__}; no fresh CAPTCHA issued."
            )
            return
        else:
            print("No fresh CAPTCHA issued by invalid-identifier probe.")
            return
    proxy_url = _effective_captcha_proxy(tool.session, challenge)
    kwargs = {"headless": True}
    if proxy_url:
        p = urlsplit(proxy_url)
        kwargs["proxy"] = {"server": f"{p.scheme}://{p.hostname}:{p.port}"}
        if p.username:
            kwargs["proxy"].update(username=unquote(p.username), password=unquote(p.password or ""))
    events = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**kwargs)
        try:
            context = await browser.new_context()
            await context.add_cookies(_playwright_cookies(tool.session.cookies))
            page = await context.new_page()
            page.on(
                "response",
                lambda r: events.append(
                    {
                        "method": r.request.method,
                        "path": urlsplit(r.url).path,
                        "host": urlsplit(r.url).hostname,
                        "status": r.status,
                    }
                ),
            )
            target = _captcha_browser_url(challenge) if callback == "applicant" else challenge
            if callback == "web":
                target += "&backurl=https%3A%2F%2Fhh.ru%2F"
            response = await page.goto(target, timeout=60000, wait_until="domcontentloaded")
            print(
                json.dumps(
                    {
                        "navigation_status": response.status if response else None,
                        "page_path": urlsplit(page.url).path,
                        "body": (await page.inner_text("body"))[:500],
                        "forms": await page.locator("form").evaluate_all(
                            "els => els.map(e => ({method:e.method,action:new URL(e.action).pathname}))"
                        ),
                        "controls": await page.locator("input,button").evaluate_all(
                            "els => els.map(e => ({tag:e.tagName,type:e.type,qa:e.getAttribute('data-qa'),name:e.name,text:e.innerText}))"
                        ),
                        "network": [e for e in events if e["host"] == "hh.ru"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--callback", choices=["applicant", "original", "web"], default="applicant")
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()
    asyncio.run(inspect(args.profile, args.callback, args.fresh))
