#!/usr/bin/env python3
"""Verify that every active Nor'easter writer has exactly one staged report.

The writer roster is the source of truth. This deliberately does not maintain a
separate hard-coded zone list: state expansion should require a roster change,
not a second deployment change in the editorial gate.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

FEED_REPO = Path(
    os.environ.get(
        "NOREASTER_FEED_REPO",
        str(Path(__file__).resolve().parent.parent),
    )
).expanduser()
REPORTS_DIR = FEED_REPO / "reports"
HERMES_HOME = Path(
    os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
).expanduser()
NOREASTER_INTEL_DIR = Path(
    os.environ.get(
        "NOREASTER_INTEL_DIR",
        str(HERMES_HOME / "workspace" / "noreaster" / "intel"),
    )
).expanduser()
ROSTER_PATH = NOREASTER_INTEL_DIR / "config" / "writers_roster.json"
EDITOR_IDS = {"editor-in-chief"}


def active_writer_ids() -> list[str]:
    """Return all active report writers from the canonical multi-state roster."""
    roster = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    return [
        writer["id"]
        for writer in roster.get("writers", [])
        if writer.get("status") == "active"
        and writer.get("id") not in EDITOR_IDS
        and writer.get("domain") != "editorial"
    ]


def reports_for_date(date_str: str) -> list[dict]:
    reports = []
    for path in REPORTS_DIR.glob(f"{date_str}-*.json"):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            reports.append({"_path": path.name, "_invalid": str(exc)})
            continue
        report["_path"] = path.name
        reports.append(report)
    return reports


def verify(date_str: str) -> dict:
    expected = active_writer_ids()
    reports = reports_for_date(date_str)
    by_writer = Counter(report.get("writer_id") for report in reports if not report.get("_invalid"))
    invalid = [report["_path"] for report in reports if report.get("_invalid")]
    missing = [writer_id for writer_id in expected if by_writer[writer_id] == 0]
    duplicates = {
        writer_id: count
        for writer_id, count in by_writer.items()
        if writer_id in expected and count > 1
    }
    unexpected = sorted(
        writer_id for writer_id in by_writer
        if writer_id and writer_id not in expected
    )
    return {
        "date": date_str,
        "expected_writers": expected,
        "expected_count": len(expected),
        "report_count": len(reports),
        "present_count": sum(by_writer[writer_id] == 1 for writer_id in expected),
        "missing": missing,
        "duplicates": duplicates,
        "unexpected": unexpected,
        "invalid": invalid,
        "ok": not (missing or duplicates or unexpected or invalid),
    }


def main() -> int:
    date_str = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y-%m-%d")
    result = verify(date_str)
    print(f"[editor] {date_str}: {result['present_count']}/{result['expected_count']} active writer reports")
    print(f"[editor] staged files: {result['report_count']}")
    if result["missing"]:
        print("[editor] MISSING: " + ", ".join(result["missing"]))
    if result["duplicates"]:
        print("[editor] DUPLICATES: " + ", ".join(
            f"{writer_id}={count}" for writer_id, count in sorted(result["duplicates"].items())
        ))
    if result["unexpected"]:
        print("[editor] UNEXPECTED: " + ", ".join(result["unexpected"]))
    if result["invalid"]:
        print("[editor] INVALID JSON: " + ", ".join(result["invalid"]))
    if result["ok"]:
        print("[editor] PASS: exactly one report is staged for every active writer.")
        return 0
    print("[editor] FAIL: hold for correction; no reports were auto-generated.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
