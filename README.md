# Tennis Court Monitor (multi-venue)

Polls multiple Bangkok tennis booking systems every 5 minutes and emails you the moment a court slot opens up in your preferred time window. Runs in the background on your Mac via `launchd`. Notifications and logs are grouped by venue.

**Currently supports:**

- **Crystal Sports** (17 courts: 8 normal + 9 G) — needs a `PHPSESSID` cookie
- **Cozy Tennis** (4 courts) — public, no auth required

Adding a new venue means writing one fetcher function in `monitor.py`.

##TL;DR
Start / stop:
- launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.crystal-monitor.plist   # start
- launchctl bootout   gui/$(id -u)/com.user.crystal-monitor                                 # stop
- python3 monitor.py              # one full run, emails if new matches
- python3 monitor.py --show       # list currently-open slots, no email
- python3 monitor.py --test-email # send a test email and exit
- python3 monitor.py --discover   # probe stadiumIds 1-60, print court mapping
- python3 monitor.py --dump       # save raw API responses to last_response.json
- launchctl kickstart -k gui/$(id -u)/com.user.crystal-monitor   # force scheduled run now

## What it does

- Checks every configured venue's courts on the dates you've listed.
- Filters to your time window (e.g. 10:00-18:00) and optional court whitelist.
- Sends one email per polling tick containing all newly-opened slots, **grouped by venue**.
- Remembers what it already notified you about, so you don't get duplicate alerts.
- Auto-detects an expired Crystal Sports cookie and emails you once to refresh it.

## Config shape (config.json)

```json
{
  "dates": ["2026-05-16", "2026-05-17"],
  "preferences": { "timeStart": "10:00", "timeEnd": "20:00", "courts": [] },
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
    }
  ],
  "email": { "from": "...", "password": "...", "to": "...", "smtpHost": "smtp.gmail.com", "smtpPort": 465 }
}
```

To **disable a venue** without removing it, set its `"enabled": false`.

## Files

| File | What it is |
| --- | --- |
| `monitor.py` | The polling script. Runs on each scheduled tick. |
| `config.json` | Your settings: cookie, dates, time window, courts, email creds. The only file you ever edit. |
| `config.example.json` | Template with placeholders if you ever need to start over. |
| `com.user.crystal-monitor.plist` | macOS launchd config that runs `monitor.py` every 5 minutes. |
| `state.json` | Auto-created. Tracks slots that have already been notified. |
| `monitor.log` | Append-only log of every run. Useful for `tail -f`. |
| `last_response.json` | Auto-created on `--dump`. Raw API responses for debugging. |
| `launchd.stdout.log` / `launchd.stderr.log` | stdout/stderr captured by launchd. |

## Prerequisites

- macOS with Python 3 (any recent macOS has it at `/usr/bin/python3`).
- A Crystal Sports member account (to get a session cookie).
- A Gmail account with a 16-character **app password** — not your regular Gmail password. Generate one at https://myaccount.google.com/apppasswords.

## Setup (first-time, in order)

### 1. Get your session cookie

1. Log into https://crystalsports-booking.kegroup.co.th/booking.php in Chrome.
2. Open DevTools (Cmd+Option+I) -> Application tab -> Cookies -> click the domain.
3. Copy the value of the `PHPSESSID` cookie.

### 2. Fill in `config.json`

Open `config.json` and set:

- `cookie` -> paste the `PHPSESSID` value
- `email.from` -> your Gmail address
- `email.password` -> your Gmail app password (spaces OK, e.g. `"abcd efgh ijkl mnop"`)
- `email.to` -> where to send notifications (usually same as `from`)
- `dates` -> list of dates to monitor in `YYYY-MM-DD` format
- `preferences.timeStart` / `timeEnd` -> your time window in 24h `HH:MM`
- `preferences.courts` -> `[]` for any court, or a whitelist like `["G North-1", "Center-2"]`

### 3. Discover all court IDs (one-time)

The default config already has all 17 courts mapped (8 normal at LOC001, 9 G courts at LOC002). If Crystal Sports ever changes their layout, re-run:

```
cd ~/Documents/claude/notify_available_tennis_court
python3 monitor.py --discover
```

It probes stadium IDs 1-60 across both locations and prints the live mapping.

### 4. Verify the setup

```
python3 monitor.py --test-email   # confirms Gmail SMTP works
python3 monitor.py --show         # prints currently-open slots (no email)
python3 monitor.py                # one full run with email if new matches
```

### 5. Schedule it

```
cp com.user.crystal-monitor.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.crystal-monitor.plist
```

It now polls every 5 minutes for as long as your Mac is awake. Auto-resumes after reboot.

## Daily operation

### Start monitoring
```
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.crystal-monitor.plist
```

### Stop monitoring
```
launchctl bootout gui/$(id -u)/com.user.crystal-monitor
```

### Check if running
```
launchctl list | grep crystal-monitor
```

### Watch live activity
```
tail -f ~/Documents/claude/notify_available_tennis_court/monitor.log
```

### Force a run right now
```
launchctl kickstart -k gui/$(id -u)/com.user.crystal-monitor
```

Or manually:
```
cd ~/Documents/claude/notify_available_tennis_court
python3 monitor.py
```

## Editing settings

Everything lives in `config.json`. No restart needed — the script reads it on every run.

| Want to change | Edit field |
| --- | --- |
| Dates monitored | `dates` (list of `"YYYY-MM-DD"`) |
| Time window | `preferences.timeStart` / `preferences.timeEnd` (24h `HH:MM`) |
| Court whitelist | `preferences.courts` (`[]` = any, or `["G North-1", ...]`) |
| Where notifications go | `email.to` |
| Session cookie (when it expires) | `cookie` |

### Auto-rolling dates

Instead of hardcoded `dates`, you can scan the next N days:

```json
"daysAhead": 14,
"weekdays": ["Sat", "Sun"]
```

(Remove the `dates` key. If both are present, `dates` wins.)

## Troubleshooting

### "Load failed: 5: Input/output error" when loading launchd

Use the modern bootstrap syntax:
```
launchctl unload ~/Library/LaunchAgents/com.user.crystal-monitor.plist 2>/dev/null
launchctl bootout gui/$(id -u)/com.user.crystal-monitor 2>/dev/null
chmod 644 ~/Library/LaunchAgents/com.user.crystal-monitor.plist
plutil ~/Library/LaunchAgents/com.user.crystal-monitor.plist   # should print "OK"
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.crystal-monitor.plist
```

### "Tennis monitor: session expired" email

Your `PHPSESSID` cookie expired. Log into the booking site again, copy the new cookie from DevTools, paste into `config.json`. The launchd job picks it up on the next 5-minute tick — no restart needed.

### Test email never arrives

- Confirm you're using a **Gmail app password**, not your account password.
- Check spam folder.
- Check `monitor.log` for the actual SMTP error.

### "No courts available" but you can see slots in the browser

- Confirm time window. The script matches `start <= slotTime <= end` (inclusive on both ends). A slot starting at `18:00` matches a `10:00-18:00` window.
- Confirm dates. The script only checks dates in `config.json`.
- Run `python3 monitor.py --show` and compare against the UI.

### Want to see what the API is actually returning

```
python3 monitor.py --dump
```

Writes `last_response.json` with the full raw response.

## Deploy to GitHub Actions (free, 24/7)

The local launchd job only runs while your Mac is awake. To poll continuously even when the laptop is off, deploy to GitHub Actions. Free on a public repo, no minute cap.

**Bundled files for this path:**

| File | What it does |
| --- | --- |
| `.github/workflows/monitor.yml` | Cron every 5 min; renders config from secrets, runs `monitor.py`, commits `state.json` back. |
| `config.template.json` | Committed config without secrets. Has `REPLACE_WITH_PHPSESSID` and `REPLACE_WITH_GMAIL_APP_PASSWORD` placeholders. |
| `build_config.py` | At runtime, reads template + env vars, writes `config.json`. |
| `.gitignore` | Keeps `config.json`, logs, and dumps out of the repo. |

**Setup steps:**

1. **Create a new GitHub repo.** Public is fine — secrets live in GitHub's secret manager, not in the repo. Public repo = unlimited Actions minutes.

2. **Push this project to the repo:**
   ```
   cd ~/crystal-monitor
   git init
   git add .
   git commit -m "initial: tennis court monitor"
   git branch -M main
   git remote add origin git@github.com:YOUR_USER/YOUR_REPO.git
   git push -u origin main
   ```
   `.gitignore` ensures `config.json` (with secrets) is excluded.

3. **Add repository secrets.** Repo → Settings → **Secrets and variables** → **Actions** → New repository secret. Add:

   | Name | Value |
   | --- | --- |
   | `PHPSESSID` | Your Crystal Sports cookie value (refresh when you get a "session expired" email) |
   | `GMAIL_APP_PASSWORD` | Your Gmail app password (the 16-char one) |
   | `EMAIL_FROM` *(optional)* | Override sender, defaults to value in `config.template.json` |
   | `EMAIL_TO` *(optional)* | Override recipient, defaults to value in `config.template.json` |

4. **Enable Actions.** Repo → Actions tab. If GitHub asks "I understand my workflows, go ahead", click it. The workflow auto-runs on the next 5-min cron boundary, or click **Run workflow** for an immediate trigger.

5. **Verify the first run.** Actions tab → click the latest run → expand each step. You should see:
   - `Render config from secrets` → "Injected PHPSESSID into 1 crystal_sports venue(s)" + "Wrote config.json"
   - `Run monitor` → log lines for each venue's slot count
   - `Commit state if changed` → either a new state commit, or "No state change"

**Day-to-day operation:**

| Task | How |
| --- | --- |
| Change dates / time window / courts | Edit `config.template.json` in the GitHub web UI (pencil icon) → Commit. Next run picks it up. |
| Refresh expired cookie | Settings → Secrets → edit `PHPSESSID` → paste new value → Save. No commit needed. |
| Stop monitoring | Actions tab → workflow → kebab menu → **Disable workflow**. |
| Restart | Same menu → **Enable workflow**. |
| Run immediately | Actions tab → workflow → **Run workflow**. |
| Reset notification state | Edit `state.json` in repo → set `"notified": []` → commit. You'll get one fresh email next run. |

**Trade-offs vs. local launchd:**

| | launchd | GitHub Actions |
| --- | --- | --- |
| Cost | free | free (public repo) |
| Runs when laptop sleeps | ❌ | ✅ |
| Cron precision | tight | drifts 1-5 min |
| Cookie refresh | edit local `config.json` | edit GitHub secret |
| Log inspection | `tail monitor.log` | Actions run page |

**Run both at the same time?** Possible but wasteful — you'll get duplicate emails until one of them dedupes via `state.json`. Pick one.

## How it works

`monitor.py` POSTs to `https://crystalsports-booking.kegroup.co.th/api_helper.php?action=getAvailableStadiums` once per (date, court) pair, using your `PHPSESSID` cookie for auth. The response is a flat list of slot dicts, each with `reservestatus` (`"0"` = available, `"1"` = booked). The script filters by `reservestatus`, time window, and optional court whitelist, then emails any new matches it hasn't already notified about.

`state.json` tracks notified slots by `date|court|time` key so you only get one email per slot per opening. If a slot gets booked and reopens, you'll get a fresh email.

Default poll interval is 300 seconds (5 minutes) — adjust `StartInterval` in the plist if you want faster/slower.

## Privacy / hygiene

`config.json` contains your session cookie and Gmail app password. Don't commit it to git. If you ever share or screenshot it, **rotate the app password** at https://myaccount.google.com/apppasswords (revoke the old, generate a new one).
