#!/usr/bin/env python3
"""
Editor-in-Chief Verification Script
Runs after the batch report generator to verify all expected zones shipped.
If any zone is missing, it auto-generates the missing report immediately.

Used by the daily report script as a post-run check.
Can also be run standalone: python3 scripts/editor_verify.py [date    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]
"""
import json
import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime

# All expected writer zones (must match writers.json + batch script)
EXPECTED_ZONES = [
    "western-sound",
    "central-sound",
    "eastern-sound",
    "jamaica-bay",
    "jones-inlet",
    "fire-island",
    "moriches",
    "shinnecock",
    "montauk",
    "peconic",
    "north-fork-sound-shore",
    "nj-shore",
    "block-island",
    "hudson-canyon",
    "wilmington-canyon",
    "washington-canyon",
    "south-canyons",
    "east-canyons",
    # CT expansion (July 2026)
    "western-ct-sound",
    "central-ct-sound",
    "lower-ct-river",
    "eastern-ct-sound",
    "thames-river-new-london",
    "fishers-island-sound-stonington",
    "ct-offshore",
    # NJ expansion (July 2026)
    "raritan-bay-sandy-hook",
    "northern-nj-shore",
    "barnegat-bay",
    "long-beach-island",
    "south-jersey-shore",
    "cape-may-delaware-bay",
    "nj-offshore",
    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]

FEED_REPO = Path(__file__).resolve().parent.parent
REPORTS_DIR = FEED_REPO / "reports"


def check_date(date_str: str) -> dict:
    """Check which zones have reports for the given date."""
    found = {}
    missing = [    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]

    for zone in EXPECTED_ZONES:
        pattern = f"{date_str}-{zone}-*.json"
        matches = list(REPORTS_DIR.glob(pattern))
        if matches:
            # Read the report to get headline
            try:
                data = json.loads(matches[0    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
].read_text())
                found[zone    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] = {
                    "file": matches[0    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
].name,
                    "headline": data.get("headline", "?"),
                    "writer": data.get("writer_name", "?"),
                }
            except Exception:
                found[zone    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] = {"file": matches[0    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
].name, "headline": "ERROR READING", "writer": "?"}
        else:
            missing.append(zone)

    return {
        "date": date_str,
        "expected": len(EXPECTED_ZONES),
        "found": len(found),
        "missing": missing,
        "reports": found,
    }


def generate_missing(date_str: str, missing: list) -> dict:
    """Auto-generate any missing zone reports."""
    results = {"generated": [    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
], "failed": [    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]}
    for zone in missing:
        print(f"[editor    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] AUTO-GENERATING missing report: {zone}")
        try:
            proc = subprocess.run(
                ["python3", "scripts/generate_writer_report.py", zone    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(FEED_REPO),
            )
            if proc.returncode == 0:
                # Extract headline from output
                headline = ""
                for line in proc.stdout.splitlines():
                    if '"headline"' in line:
                        headline = line.split('"headline": "')[1    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
].rstrip('"') if '"' in line else ""
                        break
                results["generated"    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
].append({"zone": zone, "headline": headline})
                print(f"  ✅ {zone}: {headline}")
            else:
                results["failed"    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
].append({"zone": zone, "error": proc.stderr[:200    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]})
                print(f"  ❌ {zone}: {proc.stderr[:200    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]}")
        except subprocess.TimeoutExpired:
            results["failed"    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
].append({"zone": zone, "error": "timeout"})
            print(f"  ❌ {zone}: timeout")
        except Exception as e:
            results["failed"    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
].append({"zone": zone, "error": str(e)})
            print(f"  ❌ {zone}: {e}")
    return results


def main():
    # Get date from arg or default to today
    if len(sys.argv) > 1:
        date_str = sys.argv[1    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]
    else:
        date_str = datetime.now().strftime("%Y-%m-%d")

    print(f"[editor    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] Verifying reports for {date_str}...")
    print(f"[editor    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] Expected zones: {len(EXPECTED_ZONES)}")

    result = check_date(date_str)

    print(f"[editor    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] Found: {result['found'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]}/{result['expected'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]}")

    if result["missing"    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]:
        print(f"[editor    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] MISSING: {', '.join(result['missing'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
])}")
        print(f"[editor    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] Auto-generating {len(result['missing'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
])} missing reports...")
        gen = generate_missing(date_str, result["missing"    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
])

        if gen["generated"    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]:
            print(f"[editor    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] ✅ Generated {len(gen['generated'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
])} missing reports")
        if gen["failed"    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]:
            print(f"[editor    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] ❌ Failed to generate {len(gen['failed'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
])} reports:")
            for f in gen["failed"    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]:
                print(f"  - {f['zone'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]}: {f['error'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]}")

        # Re-check after generation
        result = check_date(date_str)
        print(f"[editor    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] Post-generation: {result['found'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]}/{result['expected'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]}")

        if result["missing"    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]:
            print(f"[editor    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] ⚠️ STILL MISSING after auto-gen: {', '.join(result['missing'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
])}")
            print(f"[editor    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] These need manual intervention.")
    else:
        print(f"[editor    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
] ✅ All {result['expected'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]} zones present!")

    # Print summary table
    print(f"\n{'='*80}")
    print(f"EDITOR'S REPORT — {date_str}")
    print(f"{'='*80}")
    for zone in EXPECTED_ZONES:
        if zone in result["reports"    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]:
            r = result["reports"    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
][zone    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]
            print(f"  ✅ {zone:25s} {r['writer'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]:30s} {r['headline'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
][:50    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]}")
        else:
            print(f"  ❌ {zone:25s} MISSING")
    print(f"{'='*80}")
    print(f"Total: {result['found'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]}/{result['expected'    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]}")

    # Exit non-zero if still missing
    if result["missing"    "narragansett-bay",
    "point-judith-block-island",
    "ri-south-shore",
    "nh-coast",
    "nh-offshore",
    "southern-maine",
    "casco-bay",
    "midcoast-maine",
]:
        sys.exit(1)


if __name__ == "__main__":
    main()
