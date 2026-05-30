#!/usr/bin/env python3
"""
Batch-generate today's fishing reports for every inshore zone writer.

Skips:
  • editor-in-chief (Maggie Holloway — she edits, she doesn't file beats)
  • the 5 offshore canyon captains (will be tuned separately for offshore
    intel before we ship those)

Runs sequentially with a short cooldown between calls to be polite to
the OpenRouter API. Failures are logged but don't abort the batch.

Usage:
  python3 scripts/run_all_inshore.py [--cooldown SECONDS] [--dry-run]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
GEN_SCRIPT = SCRIPT_DIR / "generate_writer_report.py"

# Inshore roster (canonical writer ids). Mariana already shipped yesterday's
# test; she gets refreshed in this batch so the whole slate is one day.
INSHORE_WRITERS = [
    "western-sound",
    "central-sound",
    "eastern-sound",
    "north-fork-sound-shore",
    "peconic",
    "montauk",
    "block-island",
    "shinnecock",
    "moriches",
    "fire-island",
    "jones-inlet",
    "jamaica-bay",
    "nj-shore",
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cooldown", type=float, default=4.0,
                   help="Seconds to wait between writer calls (default 4)")
    p.add_argument("--dry-run", action="store_true",
                   help="Pass --dry-run through to the generator")
    p.add_argument("--only", nargs="*", help="Only run these writer ids")
    args = p.parse_args()

    targets = args.only if args.only else INSHORE_WRITERS
    results: list[tuple[str, str, str]] = []  # (writer_id, status, headline_or_err)

    print(f"[batch] running {len(targets)} writers; cooldown={args.cooldown}s; dry_run={args.dry_run}")
    for i, wid in enumerate(targets, 1):
        t0 = time.time()
        print(f"\n[batch] ({i}/{len(targets)}) {wid} starting…", flush=True)
        cmd = ["python3", str(GEN_SCRIPT), wid]
        if args.dry_run:
            cmd.append("--dry-run")
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            elapsed = time.time() - t0
            if proc.returncode != 0:
                msg = (proc.stderr or proc.stdout or "").strip().splitlines()[-1] if (proc.stderr or proc.stdout) else "unknown error"
                results.append((wid, "FAIL", msg))
                print(f"[batch] ({i}/{len(targets)}) {wid} FAIL ({elapsed:.1f}s): {msg}")
            else:
                # The generator prints a JSON object {id, headline} on stdout in non-dry mode
                last_line = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
                headline = last_line if last_line else "(no headline returned)"
                results.append((wid, "OK", headline))
                print(f"[batch] ({i}/{len(targets)}) {wid} OK ({elapsed:.1f}s)")
                if proc.stderr:
                    # Show the generator's progress line for visibility
                    for line in proc.stderr.strip().splitlines()[-3:]:
                        print(f"        {line}")
        except subprocess.TimeoutExpired:
            results.append((wid, "TIMEOUT", "300s exceeded"))
            print(f"[batch] ({i}/{len(targets)}) {wid} TIMEOUT after 300s")
        except Exception as e:
            results.append((wid, "FAIL", str(e)))
            print(f"[batch] ({i}/{len(targets)}) {wid} FAIL: {e}")

        if i < len(targets):
            time.sleep(args.cooldown)

    # Summary
    print("\n" + "=" * 72)
    print("BATCH SUMMARY")
    print("=" * 72)
    ok = [r for r in results if r[1] == "OK"]
    bad = [r for r in results if r[1] != "OK"]
    print(f"OK:   {len(ok)} / {len(results)}")
    print(f"FAIL: {len(bad)}")
    for wid, status, info in results:
        marker = "✓" if status == "OK" else "✗"
        print(f"  {marker} {wid:28s} {status:8s} {info[:120]}")
    return 0 if not bad else 2


if __name__ == "__main__":
    raise SystemExit(main())
