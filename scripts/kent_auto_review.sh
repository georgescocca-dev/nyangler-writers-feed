#!/bin/bash
#
# Kent AUTO-REVIEW — headless editorial gate. Runs via macOS system crontab at
# 5:30am ET Mon/Fri, after the 11pm generation and before the 6am push.
#
# This is the RULE-BASED gate (not the full Kent LLM review — the app is closed
# at 5:30am, so a Hermes-cron review can't run). It applies the hard editorial
# rules mechanically and writes the verdict file the 6am push reads:
#
#   APPROVE only if ALL of:
#     - staged reports exist for today (ahead of origin/main)
#     - zone coverage >= 30 of 45 zones (missing majority = hold)
#     - scan_reports.py finds ZERO hard rejects (phish, data-gap mentions,
#       placeholders, banned openers/headlines, missing headline, short, etc.)
#     - today's Hooper briefing exists (reports built on fresh intel)
#
#   Otherwise: write status="rejected" (which HOLDS the push) + ping the user.
#
# The full Kent LLM review still runs when the app opens (8:30am) as a
# post-publication audit — it can pull or fix content that the rule gate let
# through. No content ships without SOME gate; the rule gate errs toward
# holding when unsure.
#
# Crontab entry (5:30am Mon/Fri):
#   30 5 * * 1,5 /Users/spartacus/.hermes/workspace/noreaster/writers-feed/scripts/kent_auto_review.sh >> /Users/spartacus/.hermes/workspace/noreaster/writers-feed/scripts/pipeline.log 2>&1
#
# Mac must be awake: sudo pmset repeat wakeorpoweron MT F 05:15:00

set -uo pipefail

FEED_DIR="/Users/spartacus/.hermes/workspace/noreaster/writers-feed"
APPROVALS_DIR="/Users/spartacus/.hermes/workspace/noreaster/reports/approvals"
SCANNER="/Users/spartacus/.hermes/profiles/kent/skills/productivity/noreaster-editorial-review/scripts/scan_reports.py"
HOOPER_DIR="/Users/spartacus/.hermes/workspace/noreaster/intel/data/analysis"
LOG_FILE="$FEED_DIR/scripts/pipeline.log"
PYTHON="/Users/spartacus/nor-easter-setup/projects/noreaster-intel/.venv/bin/python3"
[ -x "$PYTHON" ] || PYTHON="python3"

export TZ="America/New_York"
TODAY=$(date '+%Y-%m-%d')
VERDICT="$APPROVALS_DIR/approved_${TODAY}.json"
mkdir -p "$APPROVALS_DIR"

echo "=== KENT AUTO-REVIEW: $TODAY $(date) ==="

cd "$FEED_DIR"

# Nothing staged? Nothing to gate.
STAGED=$(git log origin/main..HEAD --oneline 2>/dev/null | wc -l | tr -d ' ')
if [ "$STAGED" -eq 0 ]; then
    echo "  No staged commits ahead of origin — nothing to review."
    exit 0
fi

# Run the full check in Python (zone coverage + scanner + hooper freshness),
# write the verdict file. Exit code tells us which way it went.
"$PYTHON" - "$TODAY" "$VERDICT" "$FEED_DIR" "$SCANNER" "$HOOPER_DIR" << 'PYEOF'
import json, os, subprocess, sys
from datetime import datetime, timezone

today, verdict_path, feed_dir, scanner, hooper_dir = sys.argv[1:6]
reports_dir = os.path.join(feed_dir, "reports")

problems = []

# --- 1. staged reports exist ---
files = sorted(f for f in os.listdir(reports_dir)
               if f.startswith(f"{today}-") and f.endswith(".json"))
report_count = len(files)
if report_count == 0:
    problems.append("no staged reports for today")

# --- 2. zone coverage (expect ~45; hold under 30) ---
writers = json.load(open(os.path.join(feed_dir, "writers.json")))["writers"]
expected = [w["id"] for w in writers if w["id"] != "editor-in-chief"]
have = set()
for f in files:
    base = f[:-5]  # strip .json
    for wid in expected:
        if base.startswith(f"{today}-{wid}-"):
            have.add(wid)
missing = [w for w in expected if w not in have]
coverage = len(have)
print(f"  Coverage: {coverage}/{len(expected)} zones, {report_count} report files")
if coverage < 30:
    problems.append(f"low zone coverage ({coverage}/{len(expected)})")

# --- 3. hard-reject scanner ---
scan = subprocess.run(
    [sys.executable, scanner, today, reports_dir],
    capture_output=True, text=True)
scan_out = (scan.stdout or "") + (scan.stderr or "")
print(scan_out)
if scan.returncode != 0:
    problems.append("scanner found hard rejects (see log)")

# --- 4. today's Hooper briefing exists ---
hooper = os.path.join(hooper_dir, f"hooper_{today}.json")
if not os.path.exists(hooper):
    problems.append("no Hooper briefing for today (stale intel)")

status = "approved" if not problems else "rejected"
verdict = {
    "date": today,
    "status": status,
    "reviewed_at": datetime.now(timezone.utc).isoformat(),
    "reports_reviewed": report_count,
    "zone_coverage": f"{coverage}/{len(expected)}",
    "hooper_analysis": f"hooper_{today}.json" if os.path.exists(hooper) else "MISSING",
    "review_type": "auto-rule-gate (headless)",
    "notes": ("Auto-gate passed: coverage OK, no hard rejects, fresh Hooper."
              if not problems else
              "Auto-gate HELD: " + "; ".join(problems) +
              ". Full Kent review required before manual push."),
}
if missing and coverage < len(expected):
    verdict["missing_zones"] = missing

with open(verdict_path, "w") as fh:
    json.dump(verdict, fh, indent=2)

print(f"  VERDICT: {status.upper()} -> {verdict_path}")
sys.exit(0 if status == "approved" else 1)
PYEOF
RC=$?

# --- Watchdog ping: tell the user which way the gate went ---
bash "$FEED_DIR/scripts/noreaster_notify.sh" \
    "Kent gate $TODAY: $([ $RC -eq 0 ] && echo APPROVED || echo HELD) — verdict written, push job $( [ $RC -eq 0 ] && echo 'will fire at 6am' || echo 'will NOT push' )" \
    2>/dev/null || echo "  (notify script not available)"

echo "=== AUTO-REVIEW COMPLETE: $TODAY $(date) ==="
exit $RC
