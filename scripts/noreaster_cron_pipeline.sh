#!/bin/bash
#
# Nor'easter report pipeline — GENERATE step. Runs via macOS system crontab at
# 11pm ET the night BEFORE publication (Sun/Thu nights for Mon/Fri reports).
# No Hermes app required. Just Python, OpenRouter API, and git.
#
# Steps: analyst → hooper → generate 45 writer reports → fix empty tags →
#        rebuild index → commit locally (NO PUSH — the push is gated on Kent's
#        editorial review, which runs at 5:30am; push job at 6am ships only if
#        Kent's verdict file approves).
#
# Crontab entry (11pm Sun/Thu — reports go live next morning):
#   0 23 * * 0,4 /Users/spartacus/.hermes/workspace/noreaster/writers-feed/scripts/noreaster_cron_pipeline.sh >> /Users/spartacus/.hermes/workspace/noreaster/writers-feed/scripts/pipeline.log 2>&1
# Companion jobs (installed 2026-07-20): Kent auto-review at 5:30am Mon/Fri
# (kent_auto_review.sh) and gated push at 6am (noreaster_push_if_approved.sh).
#
# Mac must be awake at 11pm. Arm the wake with:
#   sudo pmset repeat wakeorpoweron MTWRFSU 22:45:00
#   sudo pmset repeat wakeorpoweron MT F 05:15:00
# (Mac Studio on AC power — wakeorpoweron works reliably.)

# Keep the Mac awake for the whole run — caffeinate -s blocks system sleep
# until this script exits. Critical because the Mac is set to sleep after 1 min
# of idle; without this it could nod off mid-generation.
if [ -z "${NOREASTER_CAFFEINATED:-}" ]; then
    export NOREASTER_CAFFEINATED=1
    exec caffeinate -s "$0" "$@"
fi

# Graceful degradation: do NOT use `set -e`. A failed analyst or Hooper run
# must NOT kill the whole pipeline — writers can still generate (thinner) and
# the site still gets an update. We only hard-fail when publishing is truly
# impossible (no python, no API key). Each step reports its own status.
set -uo pipefail

# --- Paths ---
INTEL_DIR="/Users/spartacus/nor-easter-setup/projects/noreaster-intel"
FEED_DIR="/Users/spartacus/.hermes/workspace/noreaster/writers-feed"
LOG_FILE="$FEED_DIR/scripts/pipeline.log"

# --- Date (ET) ---
export TZ="America/New_York"
TODAY=$(date '+%Y-%m-%d')
echo "=== PIPELINE START: $TODAY $(date) ==="

# Degradation tracker — recorded so the push/Kent knows what shipped thin.
DEGRADED=""

# --- Python --- (3.11 was removed in the crash; use the intel venv's 3.12)
PYTHON="/Users/spartacus/nor-easter-setup/projects/noreaster-intel/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    echo "FATAL: $PYTHON not found or not executable"
    exit 1
fi

# --- API Key ---
export OPENROUTER_API_KEY=$(grep '^OPENROUTER_API_KEY=' /Users/spartacus/.hermes/.env | cut -d'=' -f2- | tr -d '"' | tr -d "'")
if [ -z "$OPENROUTER_API_KEY" ]; then
    echo "FATAL: OPENROUTER_API_KEY not found in ~/.hermes/.env"
    exit 1
fi

# --- Step 1: Analyst (buoy data, no LLM) ---
echo "--- Step 1: Analyst ---"
cd "$INTEL_DIR"
if "$PYTHON" scripts/run_analyst_today.py "$TODAY" 2>&1; then
    echo "  Analyst: OK"
else
    echo "  [WARN] Analyst failed — writers will run on prior analyst data"
    DEGRADED="$DEGRADED analyst"
fi

# --- Step 2: Hooper (LLM intel briefing) ---
echo "--- Step 2: Hooper ---"
if "$PYTHON" scripts/run_hooper.py "$TODAY" 2>&1; then
    echo "  Hooper: OK"
else
    echo "  [WARN] Hooper failed — writers will run without today's synthesis (analyst-only)"
    DEGRADED="$DEGRADED hooper"
fi

# --- Step 3: Generate all 45 writer reports ---
echo "--- Step 3: Generate writer reports ---"
cd "$FEED_DIR"

WRITER_IDS=$("$PYTHON" -c "import json; writers=json.load(open('writers.json')).get('writers',[]); print(' '.join(w['id'] for w in writers if w['id']!='editor-in-chief'))")

SUCCESS=0
FAIL=0
FAILED_WRITERS=""

for w in $WRITER_IDS; do
    echo "  Generating: $w"
    # One retry per writer — network/LLM timeouts are the common failure.
    if "$PYTHON" scripts/generate_writer_report.py "$w" --skip-index >> "$LOG_FILE" 2>&1; then
        SUCCESS=$((SUCCESS+1))
    else
        echo "  [retry] $w failed once, retrying…"
        sleep 5
        if "$PYTHON" scripts/generate_writer_report.py "$w" --skip-index >> "$LOG_FILE" 2>&1; then
            SUCCESS=$((SUCCESS+1))
        else
            FAIL=$((FAIL+1))
            FAILED_WRITERS="$FAILED_WRITERS $w"
            echo "  [FAIL] $w (after retry) — continuing"
        fi
    fi
    sleep 2
done

echo "  Generation: $SUCCESS success, $FAIL fail"
if [ -n "$FAILED_WRITERS" ]; then
    echo "  FAILED:$FAILED_WRITERS"
    DEGRADED="$DEGRADED writers($FAIL)"
fi

# Ship whatever we have — even a partial set is better than no update. Only
# abort the push if literally nothing generated.
if [ "$SUCCESS" -eq 0 ]; then
    echo "FATAL: 0 reports generated — not pushing an empty update"
    exit 1
fi

# --- Step 4: Fix empty tags ---
echo "--- Step 4: Fix empty tags ---"
"$PYTHON" << 'TAGFIX'
import os, re, json

reports_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)) if '__file__' in dir() else ".", "reports")
if not os.path.isdir(reports_dir):
    reports_dir = "/Users/spartacus/.hermes/workspace/noreaster/writers-feed/reports"

today = os.popen('TZ=America/New_York date "+%Y-%m-%d"').read().strip()

files = sorted([f for f in os.listdir(reports_dir) if f.startswith(f"{today}-") and f.endswith(".md")])
fixed = 0

for f in files:
    path = os.path.join(reports_dir, f)
    with open(path) as fh:
        text = fh.read()
    if not re.search(r'Tags:\s*$', text, re.MULTILINE):
        continue
    body = text.lower()
    tags = []
    species_map = {
        "striper": "striped bass", "bass": "striped bass", "bluefish": "bluefish",
        "fluke": "fluke", "sea bass": "black sea bass", "seabass": "black sea bass",
        "porgy": "porgies", "scup": "scup", "tautog": "tautog", "blackfish": "tautog",
        "cod": "cod", "haddock": "haddock", "pollock": "pollock",
        "weakfish": "weakfish", "bonito": "bonito", "albie": "false albacore",
        "false albacore": "false albacore", "yellowfin": "yellowfin tuna",
        "bigeye": "bigeye tuna", "bluefin": "bluefin tuna", "wahoo": "wahoo",
        "mahi": "mahi-mahi", "mako": "mako shark", "thresher": "thresher shark",
        "blowfish": "blowfish", "kingfish": "kingfish", "triggerfish": "triggerfish",
        "mackerel": "mackerel", "squid": "squid", "bunker": "bunker",
        "sand eel": "sand eels",
    }
    for key, tag in species_map.items():
        if key in body and tag not in tags:
            tags.append(tag)
    tactics_map = {
        "bucktail": "bucktails", "jig": "jigs", "jigging": "jigs",
        "popper": "poppers", "plug": "plugs", "swim bait": "swimbaits",
        "live bait": "live bait", "chunk": "chunking", "troll": "trolling",
        "wire line": "wire line", "three-way": "three-way rig",
        "topwater": "topwater", "soft plastic": "soft plastics",
        "gulp": "gulp",
    }
    for key, tag in tactics_map.items():
        if key in body and tag not in tags:
            tags.append(tag)
    loc_map = {"inlet": "inlets", "canyon": "canyons", "rip": "rips",
        "ledge": "ledges", "reef": "reefs", "rockpile": "rockpiles",
        "wreck": "wrecks", "surf": "surf", "jetty": "jetties"}
    for key, tag in loc_map.items():
        if key in body and tag not in tags:
            tags.append(tag)
    if "ebb" in body: tags.append("ebb tide")
    if "flood" in body: tags.append("flood tide")
    if "slack" in body: tags.append("slack tide")
    tags = tags[:8]
    if not tags: tags = ["fishing report"]
    tag_str = ", ".join(tags)
    new_text = re.sub(r'Tags:\s*$', f'Tags: {tag_str}', text, flags=re.MULTILINE)
    with open(path, "w") as fh:
        fh.write(new_text)
    json_path = path.replace(".md", ".json")
    if os.path.exists(json_path):
        with open(json_path) as fh:
            jdata = json.load(fh)
        jdata["tags"] = tags
        with open(json_path, "w") as fh:
            json.dump(jdata, fh, indent=2, ensure_ascii=False)
    fixed += 1
print(f"  Fixed {fixed} empty tag lines")
TAGFIX

# --- Step 5: Rebuild index, commit and push ---
echo "--- Step 5: Rebuild index, commit and push ---"
cd "$FEED_DIR"
# Writers ran with --skip-index (fast); rebuild reports.json/teasers.json once here.
"$PYTHON" -c "
import sys; sys.path.insert(0, 'scripts')
import importlib.util as u
spec = u.spec_from_file_location('g', 'scripts/generate_writer_report.py')
m = u.module_from_spec(spec); spec.loader.exec_module(m)
idx = m.rebuild_reports_index()
print(f'  Index rebuilt: {idx[\"total_reports\"]} reports')
" 2>&1

DEG_NOTE=""
if [ -n "$DEGRADED" ]; then
    DEG_NOTE=" [DEGRADED:$DEGRADED]"
    echo "  Note: pipeline ran degraded —$DEGRADED"
fi

git add -A
git commit -m "Auto-pipeline (staged): $TODAY — $SUCCESS reports generated, tags fixed$DEG_NOTE" 2>&1

# Do NOT push here. Push is gated on Kent's editorial review (5:30am) and runs
# as a separate 6am job (noreaster_push_if_approved.sh) that checks Kent's
# verdict file first. This keeps unreviewed content off the live site.
echo "  Staged locally — awaiting Kent review before push"

echo "=== GENERATE COMPLETE: $TODAY $(date) — $SUCCESS reports staged$DEG_NOTE ==="
