#!/usr/bin/env python3
"""Tennis court availability monitor — multi-venue.

Polls each configured venue's API on a schedule and emails when slots
matching your preferences open up. Currently supports:

  - "crystal_sports"  (Crystal Sports Bangkok — needs PHPSESSID cookie)
  - "cozy_tennis"     (Cozy Tennis — public, no auth)

State is persisted in state.json so you don't get duplicate alerts.
Reports and notifications are grouped by venue name.

Modes
-----
    python3 monitor.py                # one normal check
    python3 monitor.py --dump         # check + dump raw responses to last_response.json
    python3 monitor.py --discover     # probe Crystal Sports stadiumIds (Crystal-only)
    python3 monitor.py --test-email   # send a test email and exit
    python3 monitor.py --show         # print currently-available slots, no email

For 5-minute scheduling see com.user.crystal-monitor.plist.
"""

from __future__ import annotations

import argparse
import json
import smtplib
import ssl
import sys
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, date
from email.message import EmailMessage
from pathlib import Path
from urllib import request, error, parse as urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
STATE_PATH = SCRIPT_DIR / "state.json"
LOG_PATH = SCRIPT_DIR / "monitor.log"
DUMP_PATH = SCRIPT_DIR / "last_response.json"

# --- Crystal Sports endpoints ---
CRYSTAL_API_URL = (
    "https://crystalsports-booking.kegroup.co.th"
    "/api_helper.php?action=getAvailableStadiums"
)
CRYSTAL_BOOKING_URL = "https://crystalsports-booking.kegroup.co.th/booking.php"

# --- Cozy Tennis endpoints ---
COZY_API_URL = "https://schedule.cozytennis.com/wp-admin/admin-ajax.php"
COZY_PAGE_URL = "https://schedule.cozytennis.com/?pd"

# --- Talent Sport Academy endpoints ---
TALENT_API_BASE = "https://backend.talentsportacademy.com/api/v1"
TALENT_BOOKING_BASE = "https://booking.talentsportacademy.com"

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/148.0.0.0 Safari/537.36"
)


# --------- utilities ---------

def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def load_json(path: Path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def normalize_time(s) -> str:
    """Return time as zero-padded 'HH:MM'.

    Accepts: '6:00', '06:00', '06:00:00', '0600', '6', 6, None.
    Zero-padding matters because window matching does lexicographic compares.
    """
    if s is None or s == "":
        return ""
    s = str(s).strip()
    if ":" in s:
        parts = s.split(":")
        try:
            h = int(parts[0])
            m = int(parts[1]) if len(parts) > 1 else 0
            return f"{h:02d}:{m:02d}"
        except ValueError:
            return s[:5]
    if s.isdigit():
        if len(s) <= 2:
            return f"{int(s):02d}:00"
        if len(s) == 4:
            return f"{s[:2]}:{s[2:]}"
    return s[:5]


def expand_dates(cfg: dict) -> list[str]:
    """Build the list of dates to check.

    1. cfg["dates"]                — explicit list, used as-is
    2. cfg["daysAhead"] (+ weekdays filter) — generate next N days
    """
    if cfg.get("dates"):
        return list(cfg["dates"])
    days = int(cfg.get("daysAhead", 14))
    weekdays = cfg.get("weekdays")
    if weekdays:
        weekdays = {w.title() for w in weekdays}
    out = []
    today = date.today()
    for i in range(days + 1):
        d = today + timedelta(days=i)
        if weekdays and WEEKDAY_NAMES[d.weekday()] not in weekdays:
            continue
        out.append(d.isoformat())
    return out


# ========= Crystal Sports =========

def crystal_fetch_raw(cookie: str, target_date: str, stadium_id, loc_id: str) -> list:
    """POST to Crystal Sports API. Returns flat list of slot dicts (one per court×hour)."""
    payload = json.dumps({
        "date": target_date,
        "stadiumId": str(stadium_id),
        "locId": loc_id,
    }).encode("utf-8")
    req = request.Request(
        CRYSTAL_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "*/*",
            "Cookie": f"PHPSESSID={cookie}",
            "Referer": CRYSTAL_BOOKING_URL,
            "Origin": "https://crystalsports-booking.kegroup.co.th",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": UA,
        },
        method="POST",
    )
    with request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, list):
        if isinstance(data, dict) and isinstance(data.get("data"), list):
            return data["data"]
        return []
    return data


def crystal_is_available(slot: dict) -> bool:
    """reservestatus: '0' = open, '1' = reserved."""
    return str(slot.get("reservestatus", "")).strip() == "0"


def crystal_available(venue_cfg: dict, target_date: str, prefs: dict,
                      raw_dumps: dict | None = None) -> list[dict]:
    """Returns list of available slot dicts for Crystal Sports on target_date."""
    cookie = (venue_cfg.get("cookie") or "").strip()
    window_start = normalize_time(prefs["timeStart"])
    window_end = normalize_time(prefs["timeEnd"])
    courts_filter = prefs.get("courts") or None

    available = []
    for loc in venue_cfg.get("locations", []):
        label = (f"{target_date} {venue_cfg['name']} "
                 f"{loc['locId']} stadium={loc['stadiumId']}")
        slots = crystal_fetch_raw(cookie, target_date,
                                   loc["stadiumId"], loc["locId"])
        if raw_dumps is not None:
            raw_dumps[label] = slots

        for slot in slots:
            if not isinstance(slot, dict):
                continue
            if not crystal_is_available(slot):
                continue
            court = slot.get("stadiumName") or "?"
            if courts_filter and court not in courts_filter:
                continue
            t = normalize_time(slot.get("timeName") or slot.get("timeStart"))
            if not t or not (window_start <= t <= window_end):
                continue
            available.append({
                "venue": venue_cfg["name"],
                "date": target_date,
                "court": court,
                "time": t,
                "timeEnd": normalize_time(slot.get("timeEnd")),
                "location": slot.get("locName", ""),
                "price": str(slot.get("stadiumtimePrice", ""))
                            .rstrip("0").rstrip(".") or "?",
                "bookingUrl": CRYSTAL_BOOKING_URL,
            })
    return available


def crystal_discover(cfg: dict, max_id: int = 60) -> None:
    """Probe stadiumIds across both Crystal Sports locIds."""
    cv = next((v for v in cfg.get("venues", [])
               if v.get("type") == "crystal_sports"), None)
    if not cv:
        print("No crystal_sports venue in config.")
        return
    cookie = cv.get("cookie", "")
    probe_date = (date.today() + timedelta(days=1)).isoformat()
    loc_ids = sorted({l["locId"] for l in cv.get("locations", [])}
                     | {"LOC001", "LOC002"})

    found: dict[str, list[dict]] = {loc: [] for loc in loc_ids}
    log(f"Probing stadiumIds 1..{max_id} on {probe_date} for {loc_ids}")
    for loc in loc_ids:
        for sid in range(1, max_id + 1):
            try:
                slots = crystal_fetch_raw(cookie, probe_date, sid, loc)
            except Exception as e:  # noqa: BLE001
                log(f"  loc={loc} sid={sid}: error {e}")
                continue
            if slots:
                names = sorted({s.get("stadiumName", "?") for s in slots})
                loc_names = sorted({s.get("locName", "?") for s in slots})
                print(f"  loc={loc}  stadiumId={sid:>3}  "
                      f"court={','.join(names)}  "
                      f"({','.join(loc_names)})  slots={len(slots)}")
                found[loc].append({
                    "locId": loc,
                    "stadiumId": str(sid),
                    "_name": ",".join(names),
                })
    print("\nSuggested locations block:")
    flat = [item for items in found.values() for item in items]
    print(json.dumps(flat, indent=2, ensure_ascii=False))


# ========= Cozy Tennis =========

def cozy_fetch_raw(target_date: str) -> dict:
    """POST to Cozy Tennis admin-ajax. Returns dict of court_id -> [booked_event...]."""
    payload = urlparse.urlencode({
        "action": "tc_load_date",
        "date": target_date,
    }).encode("utf-8")
    req = request.Request(
        COZY_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "*/*",
            "Origin": "https://schedule.cozytennis.com",
            "Referer": COZY_PAGE_URL,
            "User-Agent": UA,
        },
        method="POST",
    )
    with request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("success"):
        return {}
    return data.get("data", {}).get("events", {}) or {}


def cozy_available(venue_cfg: dict, target_date: str, prefs: dict,
                   raw_dumps: dict | None = None) -> list[dict]:
    """Return list of available 1-hour slots for Cozy Tennis on target_date.

    The API only lists BOOKED events; availability = absence of booking.
    We synthesize hourly slots over the window and remove any whose start
    hour falls inside a booked range.
    """
    events_by_court = cozy_fetch_raw(target_date)
    if raw_dumps is not None:
        raw_dumps[f"{target_date} {venue_cfg['name']}"] = events_by_court

    window_start = normalize_time(prefs["timeStart"])
    window_end = normalize_time(prefs["timeEnd"])
    try:
        start_h = int(window_start.split(":")[0])
        end_h = int(window_end.split(":")[0])
    except (ValueError, IndexError):
        return []

    courts_filter = prefs.get("courts") or venue_cfg.get("courts") or None
    booking_url = "https://lin.ee/pM2wmeyT"  # LINE OA link from cozytennis.com

    available = []
    for court_id, booked_events in events_by_court.items():
        court_name = f"Court {court_id}"
        if courts_filter and court_name not in courts_filter and str(court_id) not in courts_filter:
            continue
        # Set of hours fully covered by a booking.
        booked_hours = set()
        for ev in booked_events or []:
            try:
                h_start = int(str(ev.get("start", "0")).split(":")[0])
                h_end = int(str(ev.get("end", "0")).split(":")[0])
            except (ValueError, IndexError):
                continue
            if h_end == 0 and h_start >= 23:
                h_end = 24  # treat "23:00-00:00" as ending at midnight
            for h in range(h_start, h_end):
                booked_hours.add(h)
        # Generate hourly slots in window, exclude booked.
        for h in range(start_h, end_h + 1):
            if h in booked_hours:
                continue
            slot_time = f"{h:02d}:00"
            if not (window_start <= slot_time <= window_end):
                continue
            end_time = f"{(h + 1) % 24:02d}:00"
            available.append({
                "venue": venue_cfg["name"],
                "date": target_date,
                "court": court_name,
                "time": slot_time,
                "timeEnd": end_time,
                "location": venue_cfg["name"],
                "price": "?",
                "bookingUrl": booking_url,
            })
    return available


# ========= Talent Sport Academy =========

def talent_fetch_raw(token: str, sport_id: str, target_date: str) -> dict:
    """GET availability for one sport on one date. Returns the JSON payload."""
    # Tolerate the user pasting the full "Bearer eyJ..." Authorization header.
    t = token.strip()
    if t.lower().startswith("bearer "):
        t = t[7:].strip()
    url = f"{TALENT_API_BASE}/site-sports/{sport_id}/court?date={target_date}"
    req = request.Request(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "Authorization": f"Bearer {t}",
            "Locale": "en",
            "Origin": TALENT_BOOKING_BASE,
            "Referer": TALENT_BOOKING_BASE + "/",
            "User-Agent": UA,
        },
        method="GET",
    )
    with request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def talent_available(venue_cfg: dict, target_date: str, prefs: dict,
                     raw_dumps: dict | None = None) -> list[dict]:
    """Return available 1-hour slots for Talent Sport Academy on target_date.

    The API returns one entry per court, with `availableTime` as a dict of
    "HH:00" -> value. Value is a NUMBER (= price, slot is available) or the
    string "CONFIRMED" (= already booked). Other strings could exist; we treat
    "any non-numeric value = unavailable" defensively.
    """
    token = (venue_cfg.get("token") or "").strip()
    sport_id = venue_cfg.get("sportId", "").strip()
    site_id = venue_cfg.get("siteId", "4102").strip()
    if not token or not sport_id:
        return []

    data = talent_fetch_raw(token, sport_id, target_date)
    if raw_dumps is not None:
        raw_dumps[f"{target_date} {venue_cfg['name']}"] = data

    status = data.get("statusResponse", {}).get("statusCode")
    if status and status != 200:
        return []

    courts = (data.get("data") or {}).get("court") or []
    courts_filter = prefs.get("courts") or venue_cfg.get("courts") or None
    window_start = normalize_time(prefs["timeStart"])
    window_end = normalize_time(prefs["timeEnd"])

    booking_url = (
        f"{TALENT_BOOKING_BASE}/en/book/site/{site_id}/court"
        f"?sportId={sport_id}&date={target_date}"
    )

    available = []
    for court in courts:
        court_name = court.get("courtName") or court.get("courtCode") or "?"
        if courts_filter and court_name not in courts_filter:
            continue
        slots = court.get("availableTime") or {}
        for raw_time, value in slots.items():
            # number = price (available); anything else (str like "CONFIRMED") = booked
            if not isinstance(value, (int, float)):
                continue
            t = normalize_time(raw_time)
            if not t or not (window_start <= t <= window_end):
                continue
            try:
                h = int(t.split(":")[0])
                end_time = f"{(h + 1) % 24:02d}:00"
            except (ValueError, IndexError):
                end_time = ""
            available.append({
                "venue": venue_cfg["name"],
                "date": target_date,
                "court": court_name,
                "time": t,
                "timeEnd": end_time,
                "location": venue_cfg["name"],
                "price": str(int(value)),
                "bookingUrl": booking_url,
            })
    return available


# ========= Venue dispatch =========

VENUE_HANDLERS = {
    "crystal_sports": crystal_available,
    "cozy_tennis":    cozy_available,
    "talent_sport":   talent_available,
}


def get_venues(cfg: dict) -> list[dict]:
    """Return list of enabled venue configs.

    Backward-compat: if cfg has no 'venues' key but has top-level 'cookie' and
    'locations', synthesize a single Crystal Sports venue from them.
    """
    if cfg.get("venues"):
        return [v for v in cfg["venues"] if v.get("enabled", True)]
    if cfg.get("locations"):
        return [{
            "name": "Crystal Sports",
            "type": "crystal_sports",
            "enabled": True,
            "cookie": cfg.get("cookie", ""),
            "locations": cfg["locations"],
        }]
    return []


def is_cookie_error(venue: dict, err: Exception) -> bool:
    """Heuristic: crystal_sports HTTPError 401/403 suggests cookie expired."""
    if venue.get("type") != "crystal_sports":
        return False
    if isinstance(err, error.HTTPError) and err.code in (401, 403):
        return True
    return False


# ========= Email =========

def send_email(cfg_email: dict, subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["From"] = cfg_email["from"]
    msg["To"] = cfg_email["to"]
    msg["Subject"] = subject
    msg.set_content(body)

    host = cfg_email.get("smtpHost", "smtp.gmail.com")
    port = int(cfg_email.get("smtpPort", 465))
    password = "".join(cfg_email["password"].split())  # gmail app passwords show spaces
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, context=ctx, timeout=30) as server:
        server.login(cfg_email["from"], password)
        server.send_message(msg)


def format_email_body(new_by_venue: dict) -> str:
    lines = []
    for venue_name in sorted(new_by_venue):
        matches = new_by_venue[venue_name]
        if not matches:
            continue
        lines.append(f"━━━ {venue_name} — {len(matches)} slot(s) ━━━")
        booking = matches[0].get("bookingUrl", "")
        for m in matches:
            price = f"฿{m['price']}" if m["price"] != "?" else ""
            loc = f" ({m['location']})" if m.get("location") and m["location"] != venue_name else ""
            lines.append(f"  {m['date']}  {m['court']:18s}  "
                         f"{m['time']}-{m['timeEnd']}  {price}{loc}")
        if booking:
            lines.append(f"  Book: {booking}")
        lines.append("")
    return "\n".join(lines).rstrip()


# ========= Main run =========

def collect_matches(cfg: dict, raw_dumps: dict | None = None
                    ) -> tuple[dict, dict, dict]:
    """Run all venue handlers across all dates. Returns:
      by_venue: {venue_name: [match dicts]}
      attempts_by_venue, failures_by_venue: counts per venue (used for cookie alert)
    """
    venues = get_venues(cfg)
    dates = expand_dates(cfg)
    prefs = cfg["preferences"]

    by_venue: dict[str, list[dict]] = defaultdict(list)
    attempts_by_venue: dict[str, int] = defaultdict(int)
    failures_by_venue: dict[str, int] = defaultdict(int)
    errors: list[tuple[dict, str]] = []

    for venue in venues:
        handler = VENUE_HANDLERS.get(venue["type"])
        if not handler:
            log(f"Unknown venue type '{venue['type']}' — skipping.")
            continue
        for target_date in dates:
            attempts_by_venue[venue["name"]] += 1
            try:
                matches = handler(venue, target_date, prefs, raw_dumps)
            except Exception as e:  # noqa: BLE001
                failures_by_venue[venue["name"]] += 1
                errors.append((venue, f"{target_date}: {e}"))
                log(f"{venue['name']} fetch error on {target_date}: {e}")
                continue
            by_venue[venue["name"]].extend(matches)

    return by_venue, attempts_by_venue, failures_by_venue, errors  # type: ignore[return-value]


def show_available(cfg: dict, dump: bool = False) -> int:
    """Read-only: fetch and print available slots, no email, no state changes."""
    raw_dumps: dict[str, object] = {}
    by_venue, _, _, _ = collect_matches(cfg, raw_dumps)

    prefs = cfg["preferences"]
    window = f"{normalize_time(prefs['timeStart'])}-{normalize_time(prefs['timeEnd'])}"
    total = sum(len(v) for v in by_venue.values())

    if dump:
        save_json(DUMP_PATH, raw_dumps)
        log(f"Dumped raw responses -> {DUMP_PATH}")

    if total == 0:
        print(f"\nThere is no court available in {window} on "
              f"{', '.join(expand_dates(cfg))} right now.\n")
        return 0

    for venue in get_venues(cfg):
        matches = sorted(by_venue.get(venue["name"], []),
                         key=lambda m: (m["date"], m["time"], m["court"]))
        if not matches:
            print(f"━━━ {venue['name']} — no slots in {window} ━━━\n")
            continue
        print(f"━━━ {venue['name']} — {len(matches)} slot(s) in {window} ━━━")
        for m in matches:
            price = f"฿{m['price']:<6s}" if m["price"] != "?" else " " * 7
            print(f"  {m['date']}  {m['court']:18s}  "
                  f"{m['time']}-{m['timeEnd']}  {price}"
                  f"({m['location']})")
        print()
    return 0


def run(dump: bool = False) -> int:
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    state = load_json(STATE_PATH, {"notified": []})
    notified = set(state.get("notified", []))

    venues = get_venues(cfg)
    if not venues:
        log("No venues configured.")
        return 2

    dates = expand_dates(cfg)
    prefs = cfg["preferences"]

    by_venue: dict[str, list[dict]] = defaultdict(list)        # all available now
    new_by_venue: dict[str, list[dict]] = defaultdict(list)    # not yet notified
    raw_dumps: dict[str, object] = {}
    fetch_errors: list[tuple[dict, str]] = []                  # (venue, msg)
    attempts_by_venue: dict[str, int] = defaultdict(int)
    failures_by_venue: dict[str, int] = defaultdict(int)

    for venue in venues:
        handler = VENUE_HANDLERS.get(venue["type"])
        if not handler:
            log(f"Unknown venue type '{venue['type']}' — skipping.")
            continue
        for target_date in dates:
            attempts_by_venue[venue["name"]] += 1
            try:
                matches = handler(venue, target_date, prefs, raw_dumps)
            except Exception as e:  # noqa: BLE001
                failures_by_venue[venue["name"]] += 1
                fetch_errors.append((venue, f"{target_date}: {e}"))
                log(f"{venue['name']} fetch error on {target_date}: {e}")
                continue
            for m in matches:
                by_venue[venue["name"]].append(m)
                key = f"{m['venue']}|{m['date']}|{m['court']}|{m['time']}"
                if key not in notified:
                    new_by_venue[venue["name"]].append(m)
                    notified.add(key)

    if dump or cfg.get("debug"):
        save_json(DUMP_PATH, raw_dumps)
        log(f"Dumped raw responses -> {DUMP_PATH}")

    # --- per-venue summary (one log line each) ---
    window = f"{normalize_time(prefs['timeStart'])}-{normalize_time(prefs['timeEnd'])}"
    total_available = sum(len(v) for v in by_venue.values())
    total_new = sum(len(v) for v in new_by_venue.values())

    if total_available == 0:
        log(f"No courts available in {window} window across "
            f"{len(venues)} venue(s) x {len(dates)} date(s).")
        print(f"\nThere is no court available in {window} on "
              f"{', '.join(dates)} right now.\n")
    else:
        for venue in venues:
            matches = sorted(by_venue.get(venue["name"], []),
                             key=lambda m: (m["date"], m["time"], m["court"]))
            if not matches:
                log(f"{venue['name']}: 0 slot(s) available in {window}.")
                continue
            preview = ", ".join(f"{m['court']} {m['date']} {m['time']}"
                                 for m in matches[:6])
            more = f" (+{len(matches) - 6} more)" if len(matches) > 6 else ""
            log(f"{venue['name']}: {len(matches)} slot(s) available in {window}: "
                f"{preview}{more}")

        # Detailed per-slot list to terminal (stdout only, not the log file)
        print()
        for venue in venues:
            matches = sorted(by_venue.get(venue["name"], []),
                             key=lambda m: (m["date"], m["time"], m["court"]))
            if not matches:
                continue
            print(f"━━━ {venue['name']} — {len(matches)} slot(s) in {window} ━━━")
            new_keys = {(n["date"], n["court"], n["time"])
                        for n in new_by_venue.get(venue["name"], [])}
            for m in matches:
                tag = " [NEW]" if (m["date"], m["court"], m["time"]) in new_keys else ""
                price = f"฿{m['price']:<6s}" if m["price"] != "?" else " " * 7
                print(f"  {m['date']}  {m['court']:18s}  "
                      f"{m['time']}-{m['timeEnd']}  {price}"
                      f"({m['location']}){tag}")
            print()

    # --- email only NEW matches, grouped by venue ---
    if total_new:
        subject = f"Tennis: {total_new} slot(s) opened across {len(new_by_venue)} venue(s)"
        body = format_email_body(new_by_venue)
        try:
            send_email(cfg["email"], subject, body)
            log(f"Email sent: {total_new} new match(es) "
                f"({', '.join(f'{k}={len(v)}' for k, v in new_by_venue.items())}).")
            state["notified"] = sorted(notified)[-3000:]
            save_json(STATE_PATH, state)
        except Exception as e:  # noqa: BLE001
            log(f"Email send failed: {e}")
            log(traceback.format_exc())
            return 1

    # --- auth-expired alert (Crystal Sports cookie / Talent Sport JWT) ---
    AUTH_VENUE_TYPES = {"crystal_sports", "talent_sport"}
    SECRET_FIELD = {
        "crystal_sports": "PHPSESSID cookie",
        "talent_sport":   "TALENT_TOKEN (JWT)",
    }
    for venue in venues:
        if venue["type"] not in AUTH_VENUE_TYPES:
            continue
        attempts = attempts_by_venue.get(venue["name"], 0)
        failures = failures_by_venue.get(venue["name"], 0)
        flag = f"__auth_expired__|{venue['name']}"
        if attempts and failures == attempts and not cfg.get("suppressErrorEmail"):
            secret = SECRET_FIELD.get(venue["type"], "auth credential")
            log(f"{venue['name']}: all requests failed — {secret} may have expired.")
            if flag not in notified:
                try:
                    errs = [m for v, m in fetch_errors if v["name"] == venue["name"]]
                    send_email(
                        cfg["email"],
                        f"Tennis monitor: {venue['name']} auth expired",
                        f"All requests to {venue['name']} failed. The {secret} "
                        f"has likely expired — refresh it and update "
                        f"config.json (local) or the secret in GitHub Settings "
                        f"(cloud).\n\n" + "\n".join(errs[:20]),
                    )
                    notified.add(flag)
                    state["notified"] = sorted(notified)[-3000:]
                    save_json(STATE_PATH, state)
                except Exception:  # noqa: BLE001
                    pass
        elif failures < attempts and flag in notified:
            notified.discard(flag)
            state["notified"] = sorted(notified)[-3000:]
            save_json(STATE_PATH, state)

    return 0


# ========= CLI =========

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dump", action="store_true",
                        help="Save raw API responses to last_response.json")
    parser.add_argument("--discover", action="store_true",
                        help="Probe Crystal Sports stadiumIds.")
    parser.add_argument("--show", action="store_true",
                        help="Print available slots without sending email.")
    parser.add_argument("--test-email", action="store_true",
                        help="Send a test email and exit.")
    args = parser.parse_args()

    if not CONFIG_PATH.exists():
        log(f"Config missing: copy config.example.json -> {CONFIG_PATH}")
        return 2
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    if args.test_email:
        try:
            send_email(cfg["email"], "Tennis monitor: test email",
                       "If you see this, SMTP is working.\n")
            log("Test email sent.")
            return 0
        except Exception as e:  # noqa: BLE001
            log(f"Test email failed: {e}")
            return 1

    if args.discover:
        crystal_discover(cfg)
        return 0

    if args.show:
        return show_available(cfg, dump=args.dump)

    try:
        return run(dump=args.dump)
    except Exception as e:  # noqa: BLE001
        log(f"Fatal: {e}")
        log(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
