#!/usr/bin/env python3
"""Render config.json from config.template.json + environment secrets.

Used by the GitHub Actions workflow so that the cookie and Gmail app password
live in GitHub Secrets, never in the committed repo.

Environment variables consumed:
  PHPSESSID          -> injected into every crystal_sports venue's "cookie"
  GMAIL_APP_PASSWORD -> injected into email.password
  EMAIL_FROM         -> optional override for email.from
  EMAIL_TO           -> optional override for email.to

If a variable is unset, the corresponding template value is kept as-is.
Fails loudly if PHPSESSID or GMAIL_APP_PASSWORD ends up unset AND the template
still has placeholder values like 'REPLACE_WITH_...'.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "config.template.json"
OUTPUT = HERE / "config.json"


def env(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


def main() -> int:
    if not TEMPLATE.exists():
        print(f"Missing {TEMPLATE}", file=sys.stderr)
        return 2

    cfg = json.loads(TEMPLATE.read_text(encoding="utf-8"))

    cookie = env("PHPSESSID")
    if cookie:
        n = 0
        for v in cfg.get("venues", []):
            if v.get("type") == "crystal_sports":
                v["cookie"] = cookie
                n += 1
        print(f"Injected PHPSESSID into {n} crystal_sports venue(s).")

    talent_token = env("TALENT_TOKEN")
    if talent_token:
        # The user may copy the Authorization header verbatim, including the
        # "Bearer " prefix — strip it so the JWT alone ends up in config.
        if talent_token.lower().startswith("bearer "):
            talent_token = talent_token[7:].strip()
        n = 0
        for v in cfg.get("venues", []):
            if v.get("type") == "talent_sport":
                v["token"] = talent_token
                n += 1
        print(f"Injected TALENT_TOKEN into {n} talent_sport venue(s).")

    if env("GMAIL_APP_PASSWORD"):
        cfg.setdefault("email", {})["password"] = env("GMAIL_APP_PASSWORD")
        print("Injected GMAIL_APP_PASSWORD into email.password.")

    if env("EMAIL_FROM"):
        cfg.setdefault("email", {})["from"] = env("EMAIL_FROM")
    if env("EMAIL_TO"):
        cfg.setdefault("email", {})["to"] = env("EMAIL_TO")

    # Sanity check — fail loudly if the resulting config still has placeholders.
    blob = json.dumps(cfg)
    missing = []
    if "REPLACE_WITH_PHPSESSID" in blob:
        missing.append("PHPSESSID")
    if "REPLACE_WITH_TALENT_TOKEN" in blob:
        if any(v.get("type") == "talent_sport" and v.get("enabled", True)
               for v in cfg.get("venues", [])):
            missing.append("TALENT_TOKEN")
    if "REPLACE_WITH_GMAIL_APP_PASSWORD" in blob:
        missing.append("GMAIL_APP_PASSWORD")
    if "REPLACE_WITH_EMAIL_FROM" in blob:
        missing.append("EMAIL_FROM")
    if "REPLACE_WITH_EMAIL_TO" in blob:
        missing.append("EMAIL_TO")
    if missing:
        print(f"ERROR: secrets not provided: {', '.join(missing)}", file=sys.stderr)
        return 1

    OUTPUT.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
