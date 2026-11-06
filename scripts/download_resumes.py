"""Download full resume JSON + PDF for every HH profile.

Usage: .venv/Scripts/python.exe scripts/download_resumes.py [PROFILE ...]
Saves to resumes/downloaded/<profile>/resume.json and <original pdf name>.
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote

import requests

APPDATA = Path.home() / "AppData" / "Roaming" / "hh-applicant-tool"
OUT_ROOT = Path(__file__).resolve().parent.parent / "resumes" / "downloaded"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

PROFILES = ["0056", "0555", "0639", "1206", "1558", "5457", "8755"]


def get(session, url, token):
    r = session.get(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": UA,
        "hh-user-agent": "hh-applicant-tool/1.8.26",
    }, timeout=30)
    r.raise_for_status()
    return r


def main():
    wanted = sys.argv[1:] or PROFILES
    ok, failed = [], []
    for profile in wanted:
        cfg_path = APPDATA / profile / "config.json"
        try:
            token = json.loads(cfg_path.read_text(encoding="utf-8"))["token"]["access_token"]
        except Exception as e:
            failed.append((profile, f"config: {e}"))
            continue

        out_dir = OUT_ROOT / profile
        out_dir.mkdir(parents=True, exist_ok=True)
        s = requests.Session()

        try:
            mine = get(s, "https://api.hh.ru/resumes/mine", token).json()
            items = mine.get("items") or []
            if not items:
                failed.append((profile, "no resumes in /resumes/mine"))
                continue
            ids = [it["id"] for it in items]

            for rid in ids:
                full = get(s, f"https://api.hh.ru/resumes/{rid}", token)
                data = full.json()
                (out_dir / "resume.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                title = data.get("title", "?")

                pdf_url = data.get("download", {}).get("pdf", {}).get("url")
                pdf_name = "resume.pdf"
                if pdf_url:
                    m = re.search(r"/download/([^/?]+)", pdf_url)
                    if m:
                        pdf_name = unquote(m.group(1))
                    pdf = get(s, pdf_url, token)
                    (out_dir / pdf_name).write_bytes(pdf.content)

                print(f"[{profile}] {title} ({rid}) -> {out_dir / pdf_name}")
            ok.append(profile)
        except Exception as e:
            failed.append((profile, str(e)[:200]))

    print(f"\nOK: {len(ok)}/{len(wanted)}")
    for p, err in failed:
        print(f"FAIL [{p}]: {err}")


if __name__ == "__main__":
    main()
