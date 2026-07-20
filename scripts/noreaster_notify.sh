#!/bin/bash
#
# Nor'easter pipeline notifier. Called by the cron jobs so the user gets a
# confirmation when the pipeline fires (or fails) — the "watchdog" ping.
#
# Usage: noreaster_notify.sh "message text"
#
# Channel: ntfy.sh push to the user's phone. Set NTFY_TOPIC in ~/.hermes/.env
#   (subscribe to that topic in the ntfy iOS/Android app). If no topic is set,
#   falls back to a macOS local notification (only useful if someone's at the
#   machine) and always logs the line to pipeline.log regardless.

MSG="$1"
TOPIC=$(grep '^NTFY_TOPIC=' /Users/spartacus/.hermes/.env 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")

echo "[notify] $MSG"

if [ -n "$TOPIC" ]; then
    curl -s -m 15 -H "Title: Nor'easter Pipeline" -d "$MSG" "https://ntfy.sh/$TOPIC" >/dev/null 2>&1 \
        && echo "[notify] sent via ntfy.sh/$TOPIC" \
        || echo "[notify] ntfy send FAILED"
else
    # No topic configured — local desktop notification as a weak fallback.
    osascript -e "display notification \"$MSG\" with title \"Nor'easter Pipeline\"" 2>/dev/null \
        || echo "[notify] no NTFY_TOPIC in ~/.hermes/.env and osascript unavailable"
fi
