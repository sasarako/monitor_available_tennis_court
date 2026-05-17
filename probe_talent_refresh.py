#!/usr/bin/env python3
"""Probe the Talent Sport backend to find a refresh-token endpoint.

Uses a deliberately invalid JWT as bait. Endpoints that exist should respond
400/401 with a JSON error; endpoints that don't exist should return 404.
Anything else (200, 500, weird body) is interesting and worth a closer look.

Run:  python3 probe_talent_refresh.py
"""
import json
import urllib.error
import urllib.request

BASE = "https://backend.talentsportacademy.com/api/v1"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
FAKE = "eyJhbGciOiJIUzI1NiJ9.eyJpZCI6ImZha2UifQ.fake"  # not a real token

PATHS = [
    "/auth/refresh",
    "/auth/refresh-token",
    "/auth/refreshToken",
    "/auth/token/refresh",
    "/auth/renew",
    "/auth/token",
    "/token/refresh",
    "/refresh",
    "/refresh-token",
    "/oauth/token",
    "/oauth/refresh",
]

# Try a couple of payload shapes and with/without Authorization header.
BODY_SHAPES = [
    {"refreshToken": FAKE},
    {"token": FAKE},
]

HDR = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://booking.talentsportacademy.com",
    "Referer": "https://booking.talentsportacademy.com/",
    "User-Agent": UA,
    "Locale": "en",
}


def hit(path, body, with_auth):
    url = BASE + path
    headers = dict(HDR)
    if with_auth:
        headers["Authorization"] = f"Bearer {FAKE}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                  headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read()[:200].decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"")[:200].decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return "ERR", repr(e)[:200]


print(f"{'path':<26} {'body':<14} {'auth':<5} {'status':<7} body-snippet")
print("-" * 110)
for path in PATHS:
    for body in BODY_SHAPES:
        for with_auth in (False, True):
            st, snip = hit(path, body, with_auth)
            snip = snip.replace("\n", " ")[:60]
            label = "Y" if with_auth else "N"
            body_key = next(iter(body))
            print(f"{path:<26} {body_key:<14} {label:<5} {str(st):<7} {snip}")
