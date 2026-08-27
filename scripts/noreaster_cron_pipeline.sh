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

# --- Python --- (3.11 was removed in the crash; use the intel venv's 3.12,
# falling back to the hermes-agent venv, then system python)
PYTHON="/Users/spartacus/nor-easter-setup/projects/noreaster-intel/.venv/bin/python3"
if [ ! -x "$PYTHON" ]; then
    PYTHON="/Users/spartacus/.hermes/hermes-agent/venv/bin/python3"
    echo "  [WARN] old intel venv missing — falling back to hermes venv: $PYTHON"
fi
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

# Optional Fresh-from-the-Fleet harvest. Missing vars are not fatal —
# generate_writer_report.py falls back to jsonl forum intel and logs a miss.
_SB_URL=$(grep '^SUPABASE_URL=' /Users/spartacus/.hermes/.env 2>/dev/null | cut -d'=' -f2- | tr -d '"' | tr -d "'")
_SB_ROLE=$(grep -E '^(SUPABASE_SERVICE_ROLE|SUPABASE_SERVICE_ROLE_KEY|SERVICE_ROLE)=' /Users/spartacus/.hermes/.env 2>/dev/null | head -1 | cut -d'=' -f2- | tr -d '"' | tr -d "'")
if [ -n "$_SB_URL" ]; then
    export SUPABASE_URL="$_SB_URL"
fi
if [ -n "$_SB_ROLE" ]; then
    export SUPABASE_SERVICE_ROLE="$_SB_ROLE"
fi
if [ -z "${SUPABASE_URL:-}" ] || [ -z "${SUPABASE_SERVICE_ROLE:-}" ]; then
    echo "  [WARN] SUPABASE_URL / SERVICE_ROLE unset — writers will skip fleet harvest"
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

# --- Step 3: Generate all 45 writer reports (parallel, 5 workers) ---
echo "--- Step 3: Generate writer reports (parallel) ---"
cd "$FEED_DIR"

WRITER_IDS=$("$PYTHON" -c "import json; writers=json.load(open('writers.json')).get('writers',[]); print(' '.join(w['id'] for w in writers if w['id']!='editor-in-chief'))")

# Write the parallel generation script
PARALLEL_GEN=$(mktemp /tmp/noreaster_parallel_gen.XXXXXX.py)
cat > "$PARALLEL_GEN" << 'PYEOF'
"""Parallel writer generation — runs N writers concurrently via ThreadPoolExecutor."""
import json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed

PYTHON = sys.argv[1]
FEED_DIR = sys.argv[2]
WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 5

writer_ids = sys.argv[4].split()

def generate_one(writer_id: str) -> tuple[str, bool, str]:
    """Generate one writer report. Returns (writer_id, success, detail)."""
    cmd = [PYTHON, "scripts/generate_writer_report.py", writer_id, "--skip-index"]
    env = os.environ.copy()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            timeout=600,  # 10 min max per writer
            cwd=FEED_DIR,
            env=env,
        )
        if result.returncode == 0:
            return (writer_id, True, "OK")
        else:
            # Extract last meaningful line from stderr
            err_lines = [l for l in result.stderr.strip().split('\n') if l.strip()]
            detail = err_lines[-1] if err_lines else f"exit code {result.returncode}"
            return (writer_id, False, detail[:120])
    except subprocess.TimeoutExpired:
        return (writer_id, False, "timeout after 600s")
    except Exception as e:
        return (writer_id, False, f"{type(e).__name__}: {e}")

# --- Pass 1: parallel generation ---
print(f"  [pass-1] Generating {len(writer_ids)} reports with {WORKERS} workers")
t0 = time.time()
succeeded = []
failed = []

with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    futures = {pool.submit(generate_one, w): w for w in writer_ids}
    for future in as_completed(futures):
        wid, ok, detail = future.result()
        if ok:
            succeeded.append(wid)
            print(f"    ✓ {wid}")
        else:
            failed.append(wid)
            print(f"    ✗ {wid}: {detail}")

elapsed = time.time() - t0
print(f"  [pass-1] Done in {elapsed:.0f}s: {len(succeeded)} OK, {len(failed)} failed")

# --- Pass 2: catch-up for failures (after 2 min cooling off) ---
if failed:
    print(f"  [pass-2] Cooling off 120s, then retrying {len(failed)} failures...")
    time.sleep(120)
    still_failed = []
    with ThreadPoolExecutor(max_workers=3) as pool:  # fewer workers for retries
        futures = {pool.submit(generate_one, w): w for w in failed}
        for future in as_completed(futures):
            wid, ok, detail = future.result()
            if ok:
                succeeded.append(wid)
                failed.remove(wid)
                print(f"    ✓ {wid} (catch-up)")
            else:
                still_failed.append(wid)
                print(f"    ✗ {wid}: {detail} (catch-up failed)")
    failed = still_failed

total = len(succeeded) + len(failed)
print(f"\n  TOTAL: {len(succeeded)}/{total} succeeded, {len(failed)} failed")
if failed:
    print(f"  FAILED: {' '.join(failed)}")

# Write results for the shell script to read
result = {"succeeded": succeeded, "failed": failed, "total": total}
with open(os.environ.get("GEN_RESULT_FILE", "/tmp/noreaster_gen_result.json"), "w") as f:
    json.dump(result, f)

sys.exit(0 if succeeded else 1)
PYEOF

export GEN_RESULT_FILE=$(mktemp /tmp/noreaster_gen_result.XXXXXX.json)

if "$PYTHON" "$PARALLEL_GEN" "$PYTHON" "$FEED_DIR" 5 "$WRITER_IDS" 2>&1; then
    GEN_OK=true
else
    GEN_OK=false
fi

# Read results
if [ -f "$GEN_RESULT_FILE" ]; then
    SUCCESS=$("$PYTHON" -c "import json; r=json.load(open('$GEN_RESULT_FILE')); print(len(r['succeeded']))")
    FAIL=$("$PYTHON" -c "import json; r=json.load(open('$GEN_RESULT_FILE')); print(len(r['failed']))")
    FAILED_WRITERS=$("$PYTHON" -c "import json; r=json.load(open('$GEN_RESULT_FILE')); print(' '.join(r['failed']))")
else
    echo "  [WARN] No result file found — assuming worst case"
    SUCCESS=0
    FAIL=0
    FAILED_WRITERS=""
fi

rm -f "$PARALLEL_GEN" "$GEN_RESULT_FILE"

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
