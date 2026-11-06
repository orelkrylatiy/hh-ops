"""Уникальные TG-контакты по профилям: config.json + «О себе» в резюме.

Usage:
    .venv/Scripts/python.exe scripts/set_tg_contacts.py 5457=andeevwave 0056=... [--dry-run]
    .venv/Scripts/python.exe scripts/set_tg_contacts.py --check            # показать текущие TG

Для каждого профиля:
  1. %APPDATA%/hh-applicant-tool/<p>/config.json -> contact.telegram (бакап рядом).
  2. GET /resumes/{id} -> в skills («О себе») заменить t.me/<old> на t.me/<new>
     и тем же телом сделать PUT /resumes/{id} (резюме остаётся опубликованным).
Опасные поля GET в PUT не идут (id, counters, actions, views и т.п.),
experience передаётся целиком вместе с id пунктов — иначе HH дублирует записи.
"""
from __future__ import annotations

import argparse
import json
import re
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EXE = REPO / ".venv" / "Scripts" / "hh-applicant-tool.exe"
APPDATA_HH = Path(os.environ["APPDATA"])

# Поля GET /resumes/{id}, которые HH принимает в PUT /resumes/{id} при редактировании.
EDITABLE_KEYS = (
    "first_name", "last_name", "middle_name",
    "title",
    "area",
    "salary",
    "specialization",
    "professional_roles",
    "skill_set",
    "skills",
    "experience",
    "education",
    "education_level",
    "language",
    "citizenship",
    "work_ticket",
    "travel_time",
    "commute_time",
    "relocation",
    "business_trip_readiness",
    "driver_license_types",
    "employment",
    "work_schedule",
    "working_time_intervals",
    "working_time_modes",
    "working_days",
    "portfolio",
    "website",
    "recommendation",
    "certificate",
    "resume_locale",
)


def api(profile: str, endpoint: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    cmd = [str(EXE), "--profile", profile, "api", endpoint, "-X", method]
    if payload is not None:
        cmd += ["-d", json.dumps(payload, ensure_ascii=False)]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, encoding="utf-8", errors="replace")
    out = (proc.stdout or "").strip()
    # предупреждение о версии летит в тот же поток — берём последнюю { ... } строку
    m = None
    for line in out.splitlines():
        if line.lstrip().startswith("{"):
            m = line
    if m is None:
        return proc.returncode or 1, {"_raw": out, "_stderr": (proc.stderr or "")[:500]}
    try:
        return 0, json.loads(m)
    except json.JSONDecodeError:
        return 1, {"_raw": out[:800]}


def current_tg(profile: str) -> str:
    cfg = json.loads((APPDATA_HH / "hh-applicant-tool" / profile / "config.json").read_text(encoding="utf-8"))
    return str((cfg.get("contact") or {}).get("telegram") or "").strip().rstrip("/")


def set_config_tg(profile: str, url: str, dry: bool) -> None:
    path = APPDATA_HH / "hh-applicant-tool" / profile / "config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    contact = cfg.setdefault("contact", {})
    old = str(contact.get("telegram") or "")
    if old.rstrip("/") == url:
        print(f"  config: уже {url}, скип")
        return
    if not dry:
        bak = path.with_name(f"config.json.bak-tguniq-{time.strftime('%Y%m%d-%H%M')}")
        shutil.copy2(path, bak)
        contact["telegram"] = url
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  config: {old or '—'} -> {url}")


def put_resume_tg(profile: str, old_url: str, new_url: str, dry: bool) -> bool | None:
    """Меняет t.me-ссылку в «О себе» и сохраняет резюме PUT-ом.
    None = менять было нечего (норма), True = заменил, False = ошибка PUT."""
    _, mine = api(profile, "/resumes/mine")
    items = mine.get("items") or []
    if not items:
        print("  resume: нет резюме, скип (только config)")
        return None
    rid = items[0]["id"]
    _, res = api(profile, f"/resumes/{rid}")

    skills = res.get("skills") or ""
    if old_url not in skills:
        print("  resume: старой ссылки в «О себе» нет, скип (норма)")
        return None

    new_skills = skills.replace(old_url, new_url)
    print(f"  resume: {rid[:8]} «О себе»: t.me/{old_url.rsplit('/', 1)[-1]} -> t.me/{new_url.rsplit('/', 1)[-1]}")
    if dry:
        return True

    body = {}
    for key in EDITABLE_KEYS:
        if key not in res or res[key] in (None, [], {}, ""):
            continue
        val = res[key]
        if key in ("specialization", "professional_roles", "citizenship", "work_ticket", "driver_license_types"):
            val = [{"id": x["id"]} for x in val if isinstance(x, dict) and x.get("id")]
        elif key == "skill_set":
            val = [x["name"] if isinstance(x, dict) else x for x in val]
        elif key == "area":
            val = {"id": val["id"]}
        elif key == "education_level" and isinstance(val, dict):
            val = {"id": val["id"]}
        elif key == "education" and isinstance(val, dict):
            level = val.get("level")
            val = {"level": {"id": level["id"]} if isinstance(level, dict) else level}
            if val.get("extra") is None:
                val.pop("extra", None)
        elif key == "relocation" and isinstance(val, dict):
            t = val.get("type")
            val = {"type": {"id": t["id"]} if isinstance(t, dict) else t}
            if val.get("area"):
                a = val["area"]
                val["area"] = {"id": a["id"]} if isinstance(a, dict) else a
            else:
                val.pop("area", None)
        elif key in ("travel_time", "commute_time", "business_trip_readiness", "employment") and isinstance(val, dict):
            val = {"id": val["id"]}
        elif key == "language":
            val = [{"id": x["id"], "level": {"id": x["level"]["id"]}} for x in val]
        elif key == "experience":
            fixed = []
            for it in val:
                item = {k: v for k, v in it.items()
                        if k in ("id", "start", "end", "company", "company_url", "company_specialization",
                                 "company_industry", "position", "description", "industries", "area")}
                if isinstance(item.get("industries"), list):
                    item["industries"] = [{"id": x["id"]} for x in item["industries"] if isinstance(x, dict) and x.get("id")]
                if isinstance(item.get("area"), dict):
                    item["area"] = {"id": item["area"]["id"]}
                fixed.append(item)
            val = fixed
        elif key == "certificate" and isinstance(val, list):
            val = [{"title": x.get("title"), "type": {"id": (x.get("type") or {}).get("id")},
                    "achieved_at": x.get("achieved_at"), "url": x.get("url")} for x in val]
        elif key == "working_days":
            val = [{"id": x["id"]} for x in val]
        body[key] = val

    body["skills"] = new_skills

    code, resp = api(profile, f"/resumes/{rid}", method="PUT", payload=body)
    if code != 0 or resp.get("errors") or resp.get("bad_arguments") or "_raw" in resp:
        print(f"  resume: PUT ОШИБКА: {json.dumps(resp, ensure_ascii=False)[:600]}")
        return False
    # verify
    _, res2 = api(profile, f"/resumes/{rid}")
    ok = new_url in (res2.get("skills") or "") and res2.get("status", {}).get("id") == "published"
    print(f"  resume: PUT ok, проверка: {'ссылка заменена, статус ' + str((res2.get('status') or {}).get('name')) if ok else 'НЕ ПОДТВЕРЖДЕНО'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pairs", nargs="*", help="профиль=handle, напр. 5457=andeevwave")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-put", action="store_true",
                    help="даже при совпадении old==new прогнать PUT (холостое самотестирование)")
    ap.add_argument("--check", action="store_true", help="показать текущие TG по всем профилям")
    args = ap.parse_args()

    profiles = [p.name for p in sorted(APPDATA_HH.glob("hh-applicant-tool/*")) if p.is_dir() and p.name.isdigit()]

    if args.check or not args.pairs:
        for p in profiles:
            resume_mark = ""
            try:
                resume_mark = f" (резюме: {current_tg(p) and 'есть' or '?'})"
            except Exception:
                pass
            print(f"{p}: {current_tg(p) or '—'}{resume_mark}")
        return 0

    rc = 0
    for pair in args.pairs:
        profile, handle = pair.split("=", 1)
        handle = handle.lstrip("@")
        new_url = f"https://t.me/{handle}"
        old_url = current_tg(profile)
        print(f"[{profile}] {old_url or '—'} -> {new_url}")
        if old_url == new_url and not args.force_put:
            print("  совпадает, скип целиком")
            continue
        if old_url == new_url and args.force_put:
            put_resume_tg(profile, old_url, new_url, args.dry_run)
            continue
        set_config_tg(profile, new_url, args.dry_run)
        if old_url and put_resume_tg(profile, old_url, new_url, args.dry_run) is False:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
