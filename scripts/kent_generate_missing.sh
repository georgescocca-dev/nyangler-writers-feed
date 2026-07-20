#!/bin/bash
# Kent's manual generation script for missing 2026-07-20 reports
# Generates only the 29 missing writers (CT, NJ, RI, MA, NH, ME, North Fork)
# Existing 16 reports are left untouched.

export OPENROUTER_API_KEY=$(grep '^OPENROUTER_API_KEY=' ~/.hermes/.env | cut -d'=' -f2- | tr -d '"' | tr -d "'")

if [ -z "$OPENROUTER_API_KEY" ]; then
    echo "[ERROR] OPENROUTER_API_KEY not found in ~/.hermes/.env"
    exit 1
fi

PYTHON="/Users/spartacus/nor-easter-setup/projects/noreaster-intel/.venv/bin/python3"
FEED_DIR="$HOME/.hermes/workspace/noreaster/writers-feed"
cd "$FEED_DIR" || { echo "[ERROR] Cannot cd to $FEED_DIR"; exit 1; }

MISSING_IDS="north-fork-sound-shore western-ct-sound central-ct-sound lower-ct-river eastern-ct-sound thames-river-new-london fishers-island-sound-stonington ct-offshore raritan-bay-sandy-hook northern-nj-shore barnegat-bay long-beach-island south-jersey-shore cape-may-delaware-bay nj-offshore narragansett-bay point-judith-block-island ri-south-shore nh-coast nh-offshore southern-maine casco-bay midcoast-maine cape-cod-canal boston-harbor-north-shore cape-cod-bay buzzards-bay-vineyard nantucket-sound ma-offshore-stellwagen"

TOTAL=0
SUCCESS=0
FAIL=0
FAILED_WRITERS=""

for w in $MISSING_IDS; do
    TOTAL=$((TOTAL + 1))
    echo "[gen] starting writer=$w ($TOTAL/29)"
    "$PYTHON" scripts/generate_writer_report.py "$w" 2>&1
    rc=$?
    if [ $rc -eq 0 ]; then
        SUCCESS=$((SUCCESS + 1))
        echo "[gen] OK writer=$w"
    else
        # Retry once (per P12 LLM flakiness)
        echo "[gen] RETRY writer=$w (exit=$rc)"
        "$PYTHON" scripts/generate_writer_report.py "$w" 2>&1
        rc2=$?
        if [ $rc2 -eq 0 ]; then
            SUCCESS=$((SUCCESS + 1))
            echo "[gen] OK writer=$w (retry)"
        else
            FAIL=$((FAIL + 1))
            FAILED_WRITERS="$FAILED_WRITERS $w"
            echo "[gen] FAIL writer=$w (exit=$rc2)"
        fi
    fi
    sleep 2
done

echo ""
echo "=== GENERATION COMPLETE ==="
echo "Total: $TOTAL, Success: $SUCCESS, Fail: $FAIL"
if [ -n "$FAILED_WRITERS" ]; then
    echo "Failed writers:$FAILED_WRITERS"
fi

# Rebuild index
echo "[index] rebuilding reports.json + teasers.json"
"$PYTHON" -c "
import json, glob, os
reports = []
for f in sorted(glob.glob('reports/*.json'), key=os.path.getmtime, reverse=True):
    try:
        with open(f) as fh:
            r = json.load(fh)
        r['_file'] = os.path.basename(f)
        reports.append(r)
    except:
        pass
with open('reports.json', 'w') as fh:
    json.dump({'reports': reports}, fh, indent=2)
teasers = [{'id': r.get('id',''), 'headline': r.get('headline',''), 'zone': r.get('zone',{}).get('name',''), 'date': r.get('date','')} for r in reports]
with open('teasers.json', 'w') as fh:
    json.dump({'teasers': teasers}, fh, indent=2)
print(f'[index] wrote reports.json ({len(reports)} reports) + teasers.json ({len(teasers)} teasers)')
"

# Commit
git add -A
git commit -m "Kent manual generation: $SUCCESS/29 missing reports for 2026-07-20"
echo "[done] committed"
