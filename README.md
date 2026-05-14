# Tennis Court Monitor (multi-venue)

Polls multiple Bangkok tennis booking systems every 5 minutes and emails you the moment a court slot opens up in your preferred time window. Runs in the background on your Mac via `launchd`, or 24/7 free on GitHub Actions. Notifications and logs are grouped by venue.

**Currently supports:**

- **Crystal Sports** (17 courts: 8 normal + 9 G) — needs a `PHPSESSID` cookie. Cookie typically lasts days.
- **Cozy Tennis** (4 courts) — public, no auth.
- **Talent Sport Academy** (2 tennis courts) — needs a JWT bearer token. **Expires ~24h, refresh daily.**

Adding a new venue means writing one fetcher function in `monitor.py` and registering it in `VENUE_HANDLERS`.

## TL;DR

```sh
# Local launchd
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.crystal-monitor.plist   # start
launchctl bootout   gui/$(id -u)/com.user.crystal-monitor                                 # stop
launchctl kickstart -k gui/$(id -u)/com.user.crystal-monitor                              # force run now

# Script (from ~/crystal-monitor/)
python3 monitor.py              # full run, emails if new matches
python3 monitor.py --show       # list currently-open slots, no email
python3 monitor.py --test-email # SMTP smoke test
python3 monitor.py --discover   # probe Crystal Sports stadiumIds 1-60
python3 monitor.py --dump       # save raw API responses to last_response.json
```

## What it does

- Checks every enabled venue's courts on the dates you've listed.
- Filters to your time window (e.g. 10:00-22:00, inclusive on both ends) and optional court whitelist.
- Sends one email per polling tick containing all newly-opened slots, grouped by venue.
- Remembers what it already notified you about so you don't get duplicate alerts.
- Auto-detects expired auth (Crystal Sports cookie or Talent Sport JWT) and emails you once to refresh it.

## How notifications work (read this if "no email" confuses you)

The monitor only emails when something **changes** since the last run. For each `(venue, date, court, time)` slot it has already alerted on, it stays quiet — even if that slot is still open hours later.

| Situation | Email? |
| --- | --- |
| Slot becomes available for the first time | ✅ yes |
| Same slot is still available 5 min later | ❌ no — already in `state.json` |
| Slot gets booked, then someone cancels and it reopens | ✅ yes |
| You add a new date to config and slots are open on it | ✅ yes |
| All requests to a venue fail (cookie / token expired) | ✅ one-time "auth expired" alert |
| Nothing changed | ❌ no email; log still records the check |

**Two consequences:**

1. **Silence ≠ broken.** No email means "no new openings", not "the monitor died". To check it's alive, look at the latest Actions run (cloud) or `tail monitor.log` (local).
2. **`--test-email` always sends.** It bypasses the "is this new?" check, so a successful test email proves SMTP works but doesn't prove the matching logic does.

**To force a fresh email of everything currently open** (useful for end-to-end testing):

- Local: `echo '{"notified": []}' > state.json && python3 monitor.py`
- GHA: edit `state.json` in the web UI → replace with `{"notified": []}` → commit → Run workflow

## Config shape (config.json — local; config.template.json — committed)

```jsonc
{
  "dates": ["2026-05-16", "2026-05-17"],
  "preferences": { "timeStart": "10:00", "timeEnd": "22:00", "courts": [] },
  "venues": [
    {
      "name": "Crystal Sports",
      "type": "crystal_sports",
      "enabled": true,
      "cookie": "PHPSESSID_VALUE",
      "locations": [{ "locId": "LOC001", "stadiumId": "1", "_name": "North-1" }, ...]
    },
    {
      "name": "Cozy Tennis",
      "type": "cozy_tennis",
      "enabled": true,
      "courts": []
    },
    {
      "name": "Talent Sport Academy",
      "type": "talent_sport",
      "enabled": true,
      "token": "JWT_VALUE",
      "siteId": "4102",
      "sportId": "287357a0-7374-445b-b9cb-35bd2ea40a35",
      "courts": []
    }
  ],
  "email": { "from": "...", "password": "...", "to": "...", "smtpHost": "smtp.gmail.com", "smtpPort": 465 }
}
```

To **disable a venue** without removing it, set its `"enabled": false`.

## Files

| File | What it is |
| --- | --- |
| `monitor.py` | Polling script. Runs on each scheduled tick. |
| `config.json` | Local settings with secrets. **Gitignored.** Read by local launchd. |
| `config.template.json` | Committed config with `REPLACE_WITH_*` placeholders. GHA renders `config.json` from this + secrets. |
| `config.example.json` | Reference template for setting up fresh. |
| `build_config.py` | Reads template + env vars (`PHPSESSID`, `TALENT_TOKEN`, `GMAIL_APP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`) → writes `config.json` at GHA runtime. |
| `com.user.crystal-monitor.plist` | macOS launchd config that runs `monitor.py` every 5 min. |
| `.github/workflows/monitor.yml` | GHA cron workflow. Builds config from secrets, runs monitor, commits state back. |
| `state.json` | Notified-slot ledger. Auto-committed by GHA after each run with changes. |
| `monitor.log` | Append-only log. Useful for `tail -f`. |
| `last_response.json` | Auto-created on `--dump`. Raw API responses for debugging. |
| `crystal-court-monitor.skill` | Reusable skill bundle for redeploying the project elsewhere. |
| `CONTEXT.md` | Project handoff doc — read to pick up state on a new machine or session. |

## Prerequisites

- macOS with Python 3 (any recent macOS has it at `/usr/bin/python3`).
- A Crystal Sports member account (to get a session cookie).
- A Talent Sport Academy account (to get a JWT bearer token) — optional if you disable the Talent venue.
- A Gmail account with a 16-character **app password** at https://myaccount.google.com/apppasswords (2-Step Verification must be on).

## First-time local setup

### 1. Grab the auth credentials

| Venue | Where | What to copy |
| --- | --- | --- |
| Crystal Sports | DevTools → Application → Cookies @ `crystalsports-booking.kegroup.co.th` | Value of `PHPSESSID` |
| Talent Sport Academy | DevTools → Network → any XHR to `backend.talentsportacademy.com` → Request Headers | Value of `authorization` (with or without `Bearer ` prefix — both work) |
| Gmail SMTP | https://myaccount.google.com/apppasswords | 16-char app password |

### 2. Fill in `config.json`

```sh
cd ~/crystal-monitor
cp config.example.json config.json   # only if config.json doesn't exist
open config.json
```

Replace each `PASTE_*` placeholder with the real value. Keep `config.json` private — it's gitignored for that reason.

### 3. Verify

```sh
python3 monitor.py --test-email   # confirms SMTP works
python3 monitor.py --show         # lists currently-open slots, no email
```

### 4. Install the launchd job

```sh
cp com.user.crystal-monitor.plist ~/Library/LaunchAgents/
chmod 644 ~/Library/LaunchAgents/com.user.crystal-monitor.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.crystal-monitor.plist
```

Polls every 5 minutes whenever your Mac is awake.

## Daily operation

```sh
# Status
launchctl list | grep crystal-monitor
tail -f ~/crystal-monitor/monitor.log

# Force a run right now
launchctl kickstart -k gui/$(id -u)/com.user.crystal-monitor

# Stop / start
launchctl bootout   gui/$(id -u)/com.user.crystal-monitor
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.crystal-monitor.plist
```

## Editing settings

Everything lives in `config.json` locally (or `config.template.json` for GHA + secrets in GitHub Settings). No restart needed — the script reads it fresh on every tick.

| Want to change | Edit field |
| --- | --- |
| Dates monitored | `dates` (list of `"YYYY-MM-DD"`) |
| Time window | `preferences.timeStart` / `preferences.timeEnd` (24h `HH:MM`, inclusive) |
| Court whitelist | `preferences.courts` (`[]` = any, or `["G North-1", ...]`) |
| Email recipient | `email.to` (comma-separated for multiple) |
| Crystal Sports cookie | `venues[*].cookie` for the Crystal venue (or the `PHPSESSID` GitHub secret) |
| Talent Sport JWT | `venues[*].token` for the Talent venue (or the `TALENT_TOKEN` GitHub secret) |

### Auto-rolling dates

Instead of hardcoded `dates`, scan the next N days:

```jsonc
"daysAhead": 14,
"weekdays": ["Sat", "Sun"]
```

Remove the `dates` key (if both are present, `dates` wins).

## Deploy to GitHub Actions (free, 24/7)

The local launchd job only runs while your Mac is awake. To poll continuously even when the laptop is off, deploy to GitHub Actions. Free on a public repo, no minute cap.

### Setup

1. **Create a new GitHub repo.** Public is fine — secrets live in GitHub's secret manager, not in the repo. Public repo = unlimited Actions minutes.

2. **Push the project:**
   ```sh
   cd ~/crystal-monitor
   git init
   git add .
   git commit -m "initial: tennis court monitor"
   git branch -M main
   git remote add origin git@github.com:YOUR_USER/YOUR_REPO.git
   git push -u origin main
   ```
   `.gitignore` ensures `config.json` and logs are excluded.

3. **Add repository secrets.** Settings → Secrets and variables → Actions → New repository secret:

   | Name | Value |
   | --- | --- |
   | `PHPSESSID` | Crystal Sports cookie. Refresh when you get a "session expired" email. |
   | `TALENT_TOKEN` | Talent Sport Academy JWT (with or without `Bearer ` prefix). **Refresh ~daily.** |
   | `GMAIL_APP_PASSWORD` | Gmail 16-char app password. |
   | `EMAIL_FROM` | Sender Gmail address. |
   | `EMAIL_TO` | Recipient(s). Comma-separated for multiple. |

4. **First run.** Actions tab → **Tennis Court Monitor** → **Run workflow** → main → Run. Expand each step to verify the build succeeded and slots were found.

### Day-to-day with GHA

| Task | How |
| --- | --- |
| Change dates / time / courts / enable / disable | Pencil-edit `config.template.json` in the GitHub web UI → commit. Next run picks it up. |
| Refresh Crystal Sports cookie | Settings → Secrets → `PHPSESSID` → Update. No commit. |
| Refresh Talent Sport JWT (daily) | Settings → Secrets → `TALENT_TOKEN` → Update. No commit. |
| Stop monitoring | Actions tab → workflow → kebab menu → **Disable workflow**. |
| Restart | Same menu → **Enable workflow**. |
| Run immediately | Actions tab → workflow → **Run workflow**. |
| Reset notification state | Edit `state.json` in repo → set `{"notified": []}` → commit. One fresh email next run. |

### Trade-offs vs. local launchd

| | launchd | GitHub Actions |
| --- | --- | --- |
| Cost | free | free (public repo only) |
| Runs when laptop sleeps | ❌ | ✅ |
| Cron precision | tight | drifts 1-15 min |
| Cookie / token refresh | edit local `config.json` | edit GitHub secret |
| Log inspection | `tail monitor.log` | Actions run page |

**Run both?** Possible but wasteful — you'll get duplicate emails. Stop one before the other: `launchctl bootout gui/$(id -u)/com.user.crystal-monitor` to disable local while keeping GHA active.

## Troubleshooting

### "Load failed: 5: Input/output error" when loading launchd

```sh
launchctl bootout gui/$(id -u)/com.user.crystal-monitor 2>/dev/null
chmod 644 ~/Library/LaunchAgents/com.user.crystal-monitor.plist
plutil ~/Library/LaunchAgents/com.user.crystal-monitor.plist   # should print "OK"
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.crystal-monitor.plist
```

### "Operation not permitted" reading monitor.py

macOS sandbox blocks launchd-spawned processes from reading `~/Documents`, `~/Desktop`, `~/Downloads`. Install the project at `~/crystal-monitor/` (or anywhere outside protected dirs).

### "Tennis monitor: ... auth expired" email

Cookie or JWT expired. Refresh:

- Crystal Sports: log in → DevTools → copy new `PHPSESSID` → paste into `config.json` or the `PHPSESSID` secret.
- Talent Sport: log in → DevTools → copy new `Authorization` header → paste into `config.json` or the `TALENT_TOKEN` secret.

No restart needed; the script picks up the new value on the next tick.

### Test email never arrives

- Confirm a **Gmail app password** (16 chars from https://myaccount.google.com/apppasswords), not your login password. Wrong-password symptom: `5.7.9 Application-specific password required`.
- 2-Step Verification must be on for app passwords to exist.
- Check spam folder.
- `5.5.2 Cannot Decode response` usually means an empty or whitespace-only password field.

### GHA run succeeds but no email lands

Most common cause: every match is already in `state.json` from an earlier run, so nothing is "new". If the "Run monitor" step shows slots but you see no `Email sent: N new match(es)` line, that's state, not a bug. Force a fresh email by editing `state.json` to `{"notified": []}` and triggering a run.

### GHA cron isn't firing on schedule

- First scheduled run lands 5-20 minutes after the workflow file is committed to the default branch.
- Free-tier crons drift 1-15 minutes under load.
- Workflow must be on the default branch (`main`).
- After 60 days with no commits, GitHub disables scheduled workflows. The `Commit state if changed` step keeps the repo active during normal operation; for total silence, push any trivial commit to renew.

### "No courts available" but you can see slots in the browser

- Time window is inclusive: `start <= slotTime <= end`. A slot starting at `22:00` matches a `10:00-22:00` window.
- Confirm dates are in `config.json` / `config.template.json`.
- `python3 monitor.py --show` and compare against the UI.

### See exactly what the API returned

```sh
python3 monitor.py --dump
```

Writes `last_response.json` with full raw responses for each venue × date.

## How it works (one-line summary per venue)

- **Crystal Sports**: `POST api_helper.php?action=getAvailableStadiums` per `(date, stadiumId, locId)` triple, JSON body, `PHPSESSID` cookie. Response is a flat list of slot dicts where `reservestatus: "0"` = available, `"1"` = booked.
- **Cozy Tennis**: `POST wp-admin/admin-ajax.php` with `action=tc_load_date&date=...`, no auth. Response lists *booked* events per court; availability = absence of booking in the hour grid.
- **Talent Sport Academy**: `GET api/v1/site-sports/{sportId}/court?date=...`, JWT bearer auth. Response has per-court `availableTime` map where a numeric value = price (available), a string `"CONFIRMED"` = booked.

State is keyed by `venue|date|court|time`. Default poll interval is 300 seconds (5 minutes); adjust `StartInterval` in the plist or the `cron` line in `monitor.yml`.

## Privacy / hygiene

`config.json` and any auth secrets pasted into chat should be considered sensitive. If exposed (committed to git, screenshotted publicly), **rotate immediately**:

- Gmail app password: https://myaccount.google.com/apppasswords (revoke + regenerate).
- Crystal Sports cookie: log out and back in to invalidate the old session.
- Talent Sport JWT: log out and back in.
