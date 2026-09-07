#!/bin/bash
#
# Nor'easter PUSH step — runs via macOS system crontab at 6am ET Mon/Fri.
# Pushes staged archive files to GitHub ONLY if Kent's editorial verdict approves.
#
# Gate: reads Kent's verdict file. If it approves today's staged reports, push.
# If it's missing or rejected, DO NOT push — hold and log (Kent/user alerted).
# This is the editorial gate that keeps unreviewed content out of the GitHub
# archive. It is not a live reports.nyangler.com delivery step.
#
# Crontab entry (6am Mon/Fri — after Kent's 5:30am review):
#   0 6 * * 1,5 /Users/spartacus/.hermes/workspace/noreaster/writers-feed/scripts/noreaster_push_if_approved.sh >> /Users/spartacus/.hermes/workspace/noreaster/writers-feed/scripts/pipeline.log 2>&1

set -uo pipefail

FEED_DIR="/Users/spartacus/.hermes/workspace/noreaster/writers-feed"
APPROVALS_DIR="/Users/spartacus/.hermes/workspace/noreaster/reports/approvals"
export TZ="America/New_York"
TODAY=$(date '+%Y-%m-%d')
VERDICT="$APPROVALS_DIR/approved_${TODAY}.json"

echo "=== PUSH GATE: $TODAY $(date) ==="

cd "$FEED_DIR"

# Nothing staged to push? Then nothing to do.
if [ -z "$(git log origin/main..HEAD --oneline 2>/dev/null)" ]; then
    echo "  No staged commits ahead of origin — nothing to push."
    exit 0
fi

# Kent's verdict must exist.
if [ ! -f "$VERDICT" ]; then
    echo "  HELD: no Kent verdict file at $VERDICT"
    echo "  Reports staged but NOT pushed — Kent review did not run/complete."
    echo "  ACTION NEEDED: run Kent editorial review, then push manually."
    bash "$FEED_DIR/scripts/noreaster_notify.sh" "Nor'easter $TODAY: push HELD — no Kent verdict. Reports staged, not on GitHub archive." 2>/dev/null || true
    exit 1
fi

# Verdict must say approved.
STATUS=$(python3 -c "import json,sys; print(json.load(open('$VERDICT')).get('status',''))" 2>/dev/null || echo "")
if [ "$STATUS" != "approved" ]; then
    echo "  HELD: Kent verdict status is '$STATUS' (not approved)."
    echo "  NOT pushing. See $VERDICT for details."
    COUNT=$(ls "$FEED_DIR/reports/${TODAY}-"*.json 2>/dev/null | wc -l | tr -d ' ')
    bash "$FEED_DIR/scripts/noreaster_notify.sh" "Nor'easter $TODAY: push HELD — Kent gate rejected ($COUNT reports staged). Check verdict." 2>/dev/null || true
    exit 1
fi

# Approved — push.
echo "  Kent verdict: APPROVED. Pushing staged reports…"
git push origin main 2>&1
COUNT=$(ls "$FEED_DIR/reports/${TODAY}-"*.json 2>/dev/null | wc -l | tr -d ' ')
bash "$FEED_DIR/scripts/noreaster_notify.sh" "Nor'easter $TODAY: $COUNT reports PUSHED to GitHub archive (not reports.nyangler.com). Kent gate approved." 2>/dev/null || true
echo "=== PUSH COMPLETE: $TODAY $(date) ==="
