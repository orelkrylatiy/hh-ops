#!/usr/bin/env python3
"""Validate the OpenAI-compatible provider used by scheduled workers."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from hh_applicant_tool.ai.openai import ChatOpenAI, OpenAIError
from hh_applicant_tool.constants import CONFIG_DIR
from hh_applicant_tool.utils.config import resolve_profile_config_dir


def config_path(profile_id: str = "") -> Path:
    base_dir = Path(os.environ.get("CONFIG_DIR", str(CONFIG_DIR)))
    return resolve_profile_config_dir(base_dir, profile_id) / "config.json"


def load_config(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as file:
        payload = json.load(file)
    if not isinstance(payload, dict):
        raise ValueError("config.json must contain a JSON object")
    return payload


def section_candidates(purpose: str) -> tuple[str, ...]:
    if purpose == "reply":
        return ("openai_reply", "openai_cover_letter")
    if purpose == "cover-letter":
        return ("openai_cover_letter",)
    raise ValueError(f"unknown AI purpose: {purpose}")


def select_provider(
    config: dict[str, Any],
    purpose: str,
) -> tuple[str, dict[str, Any]]:
    for section in section_candidates(purpose):
        provider = config.get(section)
        if isinstance(provider, dict) and provider:
            return section, provider
    expected = " or ".join(section_candidates(purpose))
    raise ValueError(f"missing AI section: {expected}")


def validate_provider(section: str, provider: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    api_key = str(provider.get("api_key") or "").strip()
    base_url = str(provider.get("base_url") or "").strip()
    model = str(provider.get("model") or "").strip()

    if not api_key:
        errors.append(f"{section}.api_key is required")
    elif api_key.endswith("xxx") or api_key in {"sk-proj-xxx", "replace-me"}:
        errors.append(f"{section}.api_key still looks like a placeholder")
    if not base_url:
        errors.append(f"{section}.base_url is required")
    elif not base_url.startswith(("http://", "https://")):
        errors.append(f"{section}.base_url must be an http(s) URL")
    if not model:
        errors.append(f"{section}.model is required")
    return errors


def probe_provider(provider: dict[str, Any]) -> None:
    client = ChatOpenAI(
        api_key=str(provider["api_key"]),
        base_url=str(provider["base_url"]),
        model=str(provider["model"]),
        system_prompt="Connection check. Reply with OK only.",
        temperature=0.0,
        max_completion_tokens=10,
        timeout=float(provider.get("timeout", 30.0)),
        max_retries=1,
        rate_limit=0,
    )
    response = client.complete("OK?").strip()
    if not response:
        raise OpenAIError("provider returned an empty response")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--purpose",
        choices=("cover-letter", "reply"),
        default="cover-letter",
    )
    parser.add_argument("--profile", default=os.environ.get("HH_PROFILE_ID", ""))
    parser.add_argument(
        "--probe",
        action="store_true",
        help="Perform one real model request after static validation",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    path = config_path(args.profile)
    try:
        config = load_config(path)
        section, provider = select_provider(config, args.purpose)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"AI config error: {exc}", file=sys.stderr)
        return 1

    errors = validate_provider(section, provider)
    if errors:
        for error in errors:
            print(f"AI config error: {error}", file=sys.stderr)
        return 1

    print(f"AI config OK: {section} / {provider['model']} / {provider['base_url']}")
    if args.purpose == "reply" and section == "openai_cover_letter":
        print("Reply provider fallback: openai_cover_letter")

    if args.probe:
        try:
            probe_provider(provider)
        except (OpenAIError, OSError, ValueError) as exc:
            print(f"AI probe failed: {exc}", file=sys.stderr)
            return 1
        print("AI probe OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
