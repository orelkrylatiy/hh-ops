from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

SCRIPT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_ROOT / "src"))

from hh_applicant_tool.constants import CONFIG_DIR  # noqa: E402
from hh_applicant_tool.utils.config import Config, resolve_profile_config_dir  # noqa: E402
from hh_applicant_tool.utils.resume_aliases import get_resume_aliases  # noqa: E402


def _positive_int(value: Any, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _load_lanes(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("lane config must be a JSON object")
    raw_lanes = payload.get("lanes")
    if not isinstance(raw_lanes, list) or not raw_lanes:
        raise ValueError("lane config must contain a non-empty 'lanes' array")

    lanes: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, raw_lane in enumerate(raw_lanes):
        if not isinstance(raw_lane, dict):
            raise ValueError(f"lanes[{index}] must be an object")
        lane = dict(raw_lane)
        name = str(lane.get("name") or "").strip()
        if not name:
            raise ValueError(f"lanes[{index}].name is required")
        if name in names:
            raise ValueError(f"duplicate lane name: {name}")
        names.add(name)

        if lane.get("ai_filter") not in (None, "light", "heavy"):
            raise ValueError(f"lane {name}: ai_filter must be light or heavy")
        for key in ("limit", "pages", "per_page", "timeout"):
            if key in lane:
                _positive_int(lane[key], f"lane {name}.{key}")
        lanes.append(lane)
    return lanes


def _profile_aliases(profile: str) -> dict[str, str]:
    base_dir = Path(os.environ.get("CONFIG_DIR", str(CONFIG_DIR)))
    config_path = resolve_profile_config_dir(base_dir, profile) / "config.json"
    if not config_path.exists():
        return {}
    return get_resume_aliases(Config(config_path))


def _resolve_project_path(project_root: Path, value: str) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else project_root / path)


def _lane_args(
    lane: dict[str, Any],
    *,
    aliases: dict[str, str],
    project_root: Path,
) -> list[str] | None:
    name = str(lane["name"])
    if lane.get("enabled") is False:
        return None

    args: list[str] = []
    resume_alias = str(lane.get("resume_alias") or "").strip()
    if resume_alias:
        if resume_alias in aliases:
            args += ["--resume-alias", resume_alias]
        elif resume_alias == "primary" and lane.get("allow_infer_primary"):
            args += ["--resume-alias", resume_alias]
        elif lane.get("skip_if_alias_missing"):
            print(
                f"Lane {name}: skip, resume alias {resume_alias!r} is not configured",
                flush=True,
            )
            return None
        else:
            raise ValueError(f"lane {name}: resume alias {resume_alias!r} is not configured")

    string_flags = {
        "search": "--search",
        "ai_filter": "--ai-filter",
        "system_prompt": "--system-prompt",
        "hard_filter_file": "--hard-filter-file",
    }
    for key, flag in string_flags.items():
        value = lane.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            raise ValueError(f"lane {name}.{key} must not be empty")
        if key in {"system_prompt", "hard_filter_file"}:
            text = _resolve_project_path(project_root, text)
        args += [flag, text]

    int_flags = {
        "limit": "--limit",
        "pages": "--pages",
        "per_page": "--per-page",
        "timeout": "--timeout",
    }
    for key, flag in int_flags.items():
        if key in lane:
            args += [flag, str(_positive_int(lane[key], f"lane {name}.{key}"))]

    return args


def _run(command: list[str]) -> int:
    completed = subprocess.run(command, check=False)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", required=True)
    args, passthrough = parser.parse_known_args(argv)

    script_dir = Path(__file__).resolve().parent
    project_root = Path(os.environ.get("PROJECT_ROOT", script_dir.parent)).resolve()
    apply_script = Path(os.environ.get("APPLY_SCRIPT", str(script_dir / "apply.sh"))).resolve()
    lanes_dir = Path(
        os.environ.get(
            "APPLY_LANES_DIR",
            str(project_root / "rules" / "apply-lanes"),
        )
    )
    lane_file = lanes_dir / f"{args.profile}.json"

    if not lane_file.exists():
        return _run(["bash", str(apply_script), "--profile", args.profile, *passthrough])

    try:
        lanes = _load_lanes(lane_file)
        aliases = _profile_aliases(args.profile)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Invalid apply lane config for {args.profile}: {exc}", file=sys.stderr)
        return 2

    failures: list[str] = []
    executed = 0
    for lane in lanes:
        name = str(lane["name"])
        try:
            lane_args = _lane_args(
                lane,
                aliases=aliases,
                project_root=project_root,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            failures.append(name)
            continue
        if lane_args is None:
            continue

        executed += 1
        print(f"=== apply lane profile={args.profile} lane={name} ===", flush=True)
        code = _run(
            [
                "bash",
                str(apply_script),
                "--profile",
                args.profile,
                *passthrough,
                *lane_args,
            ]
        )
        if code != 0:
            failures.append(name)

    if executed == 0:
        print(f"No enabled apply lanes executed for profile {args.profile}")
    if failures:
        print(
            "Failed apply lanes: " + ", ".join(failures),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
