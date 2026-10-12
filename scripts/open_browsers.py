"""Open separate visible HH browsers using each profile's saved cookies."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import async_playwright


def load_cookies(path: Path) -> list[dict]:
    jar = MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    return [
        {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path or "/",
            "expires": float(cookie.expires) if cookie.expires and cookie.expires > 0 else -1,
            "secure": bool(cookie.secure),
        }
        for cookie in jar
        if cookie.domain.lstrip(".") == "hh.ru" or cookie.domain.endswith(".hh.ru")
    ]


async def run(args: argparse.Namespace) -> int:
    profiles = args.profiles or sorted(
        path.name
        for path in args.config_dir.iterdir()
        if path.is_dir() and (path / "cookies.txt").is_file()
    )
    if not profiles:
        raise ValueError("No profiles with cookies.txt found")
    cookies = {}
    for profile in dict.fromkeys(profiles):
        if (
            not profile
            or any(
                char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                for char in profile
            )
            or profile in {".", ".."}
        ):
            raise ValueError("Invalid profile ID")
        cookies[profile] = load_cookies(args.config_dir / profile / "cookies.txt")

    async with async_playwright() as pw:
        browsers = []
        results = []

        async def open_profile(profile: str) -> None:
            browser = await pw.chromium.launch(headless=False)
            browsers.append(browser)
            context = await browser.new_context(viewport=None)
            await context.add_cookies(cookies[profile])
            await context.add_init_script(
                """(() => {
                    const prefix = PREFIX;
                    const update = () => {
                        if (!document.title.startsWith(prefix))
                            document.title = prefix + document.title;
                    };
                    document.addEventListener('DOMContentLoaded', () => {
                        update();
                        new MutationObserver(update).observe(document.head, {
                            childList: true, subtree: true, characterData: true
                        });
                    });
                })();""".replace("PREFIX", json.dumps(f"[HH {profile}] "))
            )
            page = await context.new_page()
            result = {"profile": profile, "opened": True}
            try:
                await page.goto("https://hh.ru/", wait_until="domcontentloaded", timeout=60000)
                result["path"] = urlsplit(page.url).path
                result["title_labeled"] = (await page.title()).startswith(f"[HH {profile}]")
            except Exception as exc:
                result["navigation_error"] = type(exc).__name__
            results.append(result)
            print(json.dumps(result), flush=True)

        try:
            outcomes = await asyncio.gather(
                *(open_profile(profile) for profile in cookies), return_exceptions=True
            )
            failed = sum(isinstance(outcome, BaseException) for outcome in outcomes)
            for profile, outcome in zip(cookies, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    print(
                        json.dumps(
                            {"profile": profile, "opened": False, "error": type(outcome).__name__}
                        ),
                        flush=True,
                    )
            print(f"Ready: {len(results)} browsers; failed: {failed}", flush=True)
            while any(browser.is_connected() for browser in browsers):
                await asyncio.sleep(1)
            return int(failed > 0)
        finally:
            await asyncio.gather(*(browser.close() for browser in browsers), return_exceptions=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profiles", nargs="*", help="Profile IDs; defaults to all saved profiles")
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path(
            os.environ.get(
                "CONFIG_DIR",
                str(
                    Path(os.environ.get("APPDATA", str(Path.home() / ".config")))
                    / "hh-applicant-tool"
                ),
            )
        ),
    )
    try:
        return asyncio.run(run(parser.parse_args()))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
