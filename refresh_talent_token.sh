#!/usr/bin/env bash
# Refresh the TALENT_TOKEN GitHub Secret from whatever's currently on your
# clipboard (a fresh Talent Sport JWT). Strips any leading "Bearer " and
# surrounding whitespace. Run from anywhere:
#
#     ~/crystal-monitor/refresh_talent_token.sh
#
# Prereqs (one-time):
#   1. Install gh CLI:   brew install gh
#   2. Auth gh:          gh auth login   (pick GitHub.com -> HTTPS -> browser)
#
# Daily flow:
#   1. Log into https://booking.talentsportacademy.com
#   2. DevTools -> Application -> Local Storage -> copy the JWT (or grab from
#      the Authorization header of any XHR after login)
#   3. ./refresh_talent_token.sh
#
# The next GitHub Actions tick (within 5 min) will pick up the new token.

set -euo pipefail

REPO="sasarako/monitor_available_tennis_court"
SECRET_NAME="TALENT_TOKEN"

# --- read clipboard ---
if command -v pbpaste >/dev/null 2>&1; then
    RAW=$(pbpaste)
elif command -v xclip >/dev/null 2>&1; then
    RAW=$(xclip -selection clipboard -o)
elif command -v wl-paste >/dev/null 2>&1; then
    RAW=$(wl-paste)
else
    echo "ERROR: no clipboard tool found (need pbpaste / xclip / wl-paste)." >&2
    exit 1
fi

# Strip leading/trailing whitespace and any "Bearer " prefix (case-insensitive).
TOKEN=$(printf '%s' "$RAW" | tr -d '[:space:]' | sed -E 's/^[Bb][Ee][Aa][Rr][Ee][Rr]//')

if [ -z "$TOKEN" ]; then
    echo "ERROR: clipboard is empty." >&2
    exit 1
fi

# Sanity check: JWTs have exactly two dots.
DOTS=$(printf '%s' "$TOKEN" | tr -cd '.' | wc -c | tr -d '[:space:]')
if [ "$DOTS" -ne 2 ]; then
    echo "ERROR: clipboard doesn't look like a JWT (got $DOTS dots, expected 2)." >&2
    echo "Contents start: ${TOKEN:0:40}..." >&2
    exit 1
fi

# --- check gh ---
if ! command -v gh >/dev/null 2>&1; then
    echo "ERROR: gh CLI not installed.  brew install gh  (then gh auth login)." >&2
    exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
    echo "ERROR: gh not authenticated. Run:  gh auth login" >&2
    exit 1
fi

# --- decode exp claim so we can show how long the new token lasts ---
PAYLOAD=$(printf '%s' "$TOKEN" | cut -d. -f2)
# base64url -> base64 (pad with =) and decode
PAD=$((4 - ${#PAYLOAD} % 4))
if [ "$PAD" -lt 4 ]; then PAYLOAD="${PAYLOAD}$(printf '=%.0s' $(seq 1 $PAD))"; fi
DECODED=$(printf '%s' "$PAYLOAD" | tr '_-' '/+' | base64 -d 2>/dev/null || true)
EXP=$(printf '%s' "$DECODED" | sed -nE 's/.*"exp"[[:space:]]*:[[:space:]]*([0-9]+).*/\1/p')

echo "Token: ${TOKEN:0:40}...${TOKEN: -20}"
if [ -n "$EXP" ]; then
    if date -r "$EXP" '+%Y-%m-%d %H:%M:%S %Z' >/dev/null 2>&1; then
        EXP_HUMAN=$(date -r "$EXP" '+%Y-%m-%d %H:%M:%S %Z')   # BSD / macOS
    else
        EXP_HUMAN=$(date -d "@$EXP" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || echo "$EXP")
    fi
    NOW=$(date +%s)
    HOURS_LEFT=$(( (EXP - NOW) / 3600 ))
    echo "Expires: $EXP_HUMAN  (~${HOURS_LEFT}h from now)"
fi

echo "Updating ${SECRET_NAME} on ${REPO}..."
printf '%s' "$TOKEN" | gh secret set "$SECRET_NAME" --repo "$REPO" --body -

echo "Done. The next cron tick (within 5 min) will use the new token."
