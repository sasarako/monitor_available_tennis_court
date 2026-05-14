# Project Context — Tennis Court Monitor

> Handoff document. Read this when picking up the project from another machine or a new Claude session. Skip to "Current state" if you just want to know what's deployed; skip to "Decisions and gotchas" if you want to know *why* things are the way they are.

## 1. What it does

Polls multiple Bangkok tennis booking sites on a schedule and emails the user (Sirisub, `sasara.1412@gmail.com`) whenever a previously-unavailable court slot opens up within a preferred time window. Two deployment paths exist side-by-side:

- **Local launchd** on Sirisub's Mac (`~/crystal-monitor/`)
- **GitHub Actions** at `https://github.com/sasarako/monitor_available_tennis_court` (public repo, free 24/7)

Both share the same `monitor.py` and config shape; only the wrapper differs (launchd plist vs. GHA workflow + secrets).

## 2. Architecture

### Venues supported

| Venue | Type key | Auth | API |
| --- | --- | --- | --- |
| Crystal Sports | `crystal_sports` | `PHPSESSID` cookie | `POST /api_helper.php?action=getAvailableStadiums` (JSON body with `date`, `stadiumId`, `locId`) |
| Cozy Tennis | `cozy_tennis` | none (public) | `POST /wp-admin/admin-ajax.php` (form body `action=tc_load_date&date=YYYY-MM-DD`) |
| Talent Sport Academy | `talent_sport` | JWT bearer (`TALENT_TOKEN`, ~24h lifetime) | `GET backend.talentsportacademy.com/api/v1/site-sports/{sportId}/court?date=YYYY-MM-DD` |

Adding a new venue = write `fetch_<name>_raw()` + `<name>_available()`, register in `VENUE_HANDLERS`.

### Slot model

`monitor.py` normalizes every venue's response into a list of dicts with these keys:

```python
{
  "venue": "Crystal Sports",
  "date": "2026-05-16",
  "court": "G North-1",
  "time": "10:00",      # HH:MM zero-padded
  "timeEnd": "11:00",
  "location": "Crystal Sports G",
  "price": "500",        # or "?" if unknown
  "bookingUrl": "https://...",
}
```

### Notification dedup

`state.json` stores keys `venue|date|court|time` for everything ever notified. Each run filters out matches already in state. Emails are sent only for the delta. If a slot gets booked then reopens, it's a fresh notification.

### Time window matching

`prefs.timeStart <= slot.time <= prefs.timeEnd` — **inclusive on both ends**. Comparison is lexicographic on zero-padded `HH:MM`, so `"6:00"` is auto-padded to `"06:00"` before compare (this gotcha bit us once — see Decisions § "Zero-pad time").

## 3. File inventory

In `~/crystal-monitor/` (and mirrored in the GitHub repo, minus `config.json` / logs):

| File | Purpose |
| --- | --- |
| `monitor.py` | The script. CLI: `--show`, `--dump`, `--discover`, `--test-email`, no args = real run. |
| `config.json` | Local secrets-laden config. **Gitignored.** Read by local launchd. |
| `config.template.json` | Sanitized config committed to repo. Has `REPLACE_WITH_*` placeholders for all four secrets. |
| `config.example.json` | Reference template for someone setting up fresh. |
| `build_config.py` | Reads template + env vars (`PHPSESSID`, `TALENT_TOKEN`, `GMAIL_APP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO`) → writes `config.json` at GHA runtime. Fails loudly if any placeholder remains. |
| `state.json` | Notified-slot ledger. Committed by GHA workflow after each run. |
| `com.user.crystal-monitor.plist` | macOS launchd job (5-min cron). Hardcodes `/Users/sirisub.am/crystal-monitor/`. |
| `.github/workflows/monitor.yml` | GHA cron workflow. Runs `*/5 * * * *` UTC, builds config from secrets, runs monitor, commits state back. |
| `.gitignore` | Excludes `config.json`, logs, `last_response.json`, `.claude/`. |
| `crystal-court-monitor.skill` | Bundled reusable skill for redeploying the whole thing on another Mac. Contains every file above except `config.json` / state. |
| `README.md` | User-facing docs: setup, daily ops, troubleshooting, GHA deployment guide. |
| `CONTEXT.md` | This file. |

## 4. Current state (as of last session)

### Local
- Project lives at `~/crystal-monitor/` (moved from `~/Documents/claude/notify_available_tennis_court/` to escape macOS sandbox blocking launchd reads).
- launchd job loaded as `com.user.crystal-monitor`, runs every 300s.
- `config.json` currently has **placeholders** for cookie, JWT, password, email.from, email.to — Sirisub needs to paste real values back for the local job to work.

### GitHub Actions
- Repo: `https://github.com/sasarako/monitor_available_tennis_court` (public).
- Default branch: `main`.
- Workflow: `Tennis Court Monitor`, scheduled `*/5 * * * *` + manual `workflow_dispatch`.
- Required secrets (set in `Settings → Secrets and variables → Actions`):
  - `PHPSESSID` — Crystal Sports session cookie
  - `TALENT_TOKEN` — Talent Sport Academy JWT (Authorization header value, with or without `Bearer ` prefix)
  - `GMAIL_APP_PASSWORD` — 16-char Gmail app password
  - `EMAIL_FROM` — sender address (e.g., `sasara.1412@gmail.com`)
  - `EMAIL_TO` — recipient address(es). Comma-separated for multiple.
- `build_config.py` enforces all five; missing any will fail the workflow with `ERROR: secrets not provided: ...`.
- Workflow commits `state.json` back to the repo on each run that finds changes.

### Config in use
- Dates: `2026-05-16, 2026-05-17, 2026-05-20, 2026-05-23, 2026-05-24` (set on `config.template.json` in repo)
- Window: `10:00 – 22:00`
- All 17 Crystal Sports courts + all 4 Cozy Tennis courts + 2 Talent Sport tennis courts enabled.

## 5. Decisions and gotchas (lessons learned during dev)

### Don't put the project in `~/Documents/`
macOS sandbox blocks launchd-spawned processes from reading user-Documents/Desktop/Downloads. Symptom: `Operation not permitted` in `launchd.stderr.log`. Fix: install in `~/<project>/` instead.

### Crystal Sports `reservestatus`: `"1"` = booked, `"0"` = available
The field name is misleading — `1` means "is reserved".

### Cozy Tennis returns only BOOKED slots
The API lists booked time ranges per court. Availability = absence of booking. The parser synthesizes hourly slots over the window and subtracts booked hours.

### Talent Sport `availableTime` values
A **number** = price → slot is available. A **string** (typically `"CONFIRMED"`) → slot is booked. The fetcher tolerates a `Bearer ` prefix in the token (strip it).

### Zero-pad time before comparing
`"6:00" < "10:00"` is False as raw strings because `'6' > '1'`. `normalize_time()` always zero-pads before window matching.

### Inclusive end of window
`timeStart <= slot <= timeEnd`. User intuition is that `22:00` includes the 22:00 slot.

### State key includes venue
After multi-venue refactor, key is `venue|date|court|time` (4 parts). Old 3-part keys were migrated by prefixing `Crystal Sports|` once.

### Test-email bypasses state
`--test-email` always sends, ignoring `state.json`. Useful for end-to-end SMTP check; misleading as a proof the matching logic works.

### "GHA succeeded but no email" is usually not a bug
The script only emails *new* matches. If state already has everything currently open, no email. To force, reset `state.json` to `{"notified": []}` and trigger a run.

### Gmail SMTP needs an app password
NOT the regular login password. Symptom of wrong password: `5.7.9 Application-specific password required`. Symptom of empty/whitespace password: `5.5.2 Cannot Decode response`. App password lives at https://myaccount.google.com/apppasswords; 2-Step Verification must be on.

### Don't write secrets to files for the user
Rule we've followed: if a user pastes a password or cookie or JWT in chat, **do not** write it into `config.json` or anywhere else on their behalf. Tell them to paste it themselves.

### macOS `launchctl load` is dead, use `bootstrap` / `bootout`
`load` may return `Load failed: 5: Input/output error` on modern macOS. Correct verbs:
- Start: `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<label>.plist`
- Stop:  `launchctl bootout   gui/$(id -u)/<label>`
- Force now: `launchctl kickstart -k gui/$(id -u)/<label>`

### GHA cron drift is real
Free tier can delay scheduled runs 5–15 minutes. Don't promise 5-min precision. Public repo = unlimited Actions minutes; private = 2000 min/month cap. We went public.

### 60-day inactivity rule
GitHub disables scheduled workflows in repos with no commits for 60 days. The `Commit state if changed` step keeps the repo alive as long as state changes happen.

### Talent Sport JWT expires ~daily
24-hour token lifetime. User manually refreshes the `TALENT_TOKEN` secret (cloud) or the `token` field in `config.json` (local) by re-logging in to Talent Sport and copying the new `Authorization` header. One-time "auth expired" email is sent if all Talent requests fail in a run.

## 6. Open items / known issues

- **Cookie / JWT refresh ergonomics.** Crystal Sports cookie lasts days; Talent Sport JWT lasts ~24h. No auto-refresh yet. Could add a refresh-token or login flow for Talent if daily manual refresh gets annoying.
- **No heartbeat email.** Could add a daily digest workflow that runs without state dedup.
- **No alerts when a slot disappears.** Only opens trigger emails.
- **BCC for multi-recipient.** `EMAIL_TO` supports comma-separated, but everyone sees each other in `To:`. BCC variant exists in chat history if needed.

## 7. Common commands cheatsheet

### Local launchd
```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.crystal-monitor.plist   # start
launchctl bootout   gui/$(id -u)/com.user.crystal-monitor                                 # stop
launchctl list | grep crystal-monitor                                                      # status
launchctl kickstart -k gui/$(id -u)/com.user.crystal-monitor                              # run now
tail -f ~/crystal-monitor/monitor.log                                                      # watch
```

### Script (run from `~/crystal-monitor/`)
```sh
python3 monitor.py                # one full run
python3 monitor.py --show         # list current slots, no email
python3 monitor.py --test-email   # SMTP smoke test
python3 monitor.py --discover     # probe Crystal Sports stadiumIds
python3 monitor.py --dump         # save raw API responses
```

### GitHub Actions
- Trigger now: Actions tab → Tennis Court Monitor → **Run workflow**
- Disable / re-enable: same menu → kebab `⋮`
- Edit dates / window: pencil-edit `config.template.json` in web UI → commit
- Refresh Crystal cookie: Settings → Secrets → `PHPSESSID` → Update
- Refresh Talent JWT (daily): Settings → Secrets → `TALENT_TOKEN` → Update
- Force re-notify: pencil-edit `state.json` → set to `{"notified": []}` → commit → Run workflow

## 8. How to redeploy / set up on a new machine

```sh
git clone git@github.com:sasarako/monitor_available_tennis_court.git ~/crystal-monitor
cd ~/crystal-monitor
cp config.example.json config.json
# Edit config.json: paste cookie, JWT, password, email.from, email.to
python3 monitor.py --test-email     # confirm SMTP
python3 monitor.py --show           # confirm slots fetch
cp com.user.crystal-monitor.plist ~/Library/LaunchAgents/
# Edit plist if username differs from sirisub.am
chmod 644 ~/Library/LaunchAgents/com.user.crystal-monitor.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.crystal-monitor.plist
```

The cloud (GHA) deployment doesn't need anything — it's already running independently.

## 9. Continuing the project with a fresh Claude session

If you're a new Claude instance reading this on Sirisub's other device, here's what's likely useful to know:

- The user typically gives concise prompts and expects you to do the implementation. Bias toward making the change and showing the result, not asking permission for small edits.
- Files in `~/crystal-monitor/` are the source of truth for local; the GitHub repo `sasarako/monitor_available_tennis_court` is the source of truth for cloud.
- Treat `PHPSESSID`, `TALENT_TOKEN`, and `GMAIL_APP_PASSWORD` as sensitive — don't write them into files even if pasted in chat; redirect the user to paste them themselves.
- Skill bundle (`crystal-court-monitor.skill`) is the canonical reusable artifact. If you update `monitor.py` or any config, **re-run the skill packager** so the bundled assets stay current:
  ```sh
  python3 -m scripts.package_skill <skill-dir> <output-dir>
  ```
- This project's TodoList accumulates over time. Mark new items as you go.
