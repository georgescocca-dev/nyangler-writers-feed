#!/usr/bin/env python3
"""Dump public.fishing_reports into this GitHub repo as an archive/backup.

Live product is Supabase. This script copies every row (no status filter),
strips Facebook JPEGs, and merges/dedups into archive/fishing_reports.jsonl.
Missing credentials or a failed fetch exit 0 and do not wipe an existing dump.

This is not a live reports.nyangler.com delivery step.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import fleet_harvest


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Archive/backup dump of public.fishing_reports. "
            "Not a live Noreaster feed."
        )
    )
    parser.add_argument(
        "--dest",
        type=Path,
        default=fleet_harvest.DEFAULT_ARCHIVE_PATH,
        help="JSONL path (default: archive/fishing_reports.jsonl)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print counts; do not write the dump file.",
    )
    args = parser.parse_args()
    try:
        if args.dry_run:
            reason = fleet_harvest.credentials_missing_reason()
            if reason:
                print(
                    f"[archive] dry-run skip (no {reason})",
                    file=sys.stderr,
                )
                return 0
            rows = fleet_harvest.fetch_all_fishing_reports()
            print(
                f"[archive] dry-run fetched={len(rows)} dest={args.dest}",
                file=sys.stderr,
            )
            return 0
        fleet_harvest.dump_fishing_reports_archive(args.dest)
    except Exception as exc:  # noqa: BLE001 — cron must not crash
        print(
            f"[warn] fishing_reports archive dump failed "
            f"({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
