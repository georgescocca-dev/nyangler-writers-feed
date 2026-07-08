#!/usr/bin/env python3
"""
Build the public writers.json feed from the internal Nor'easter roster.

Source: /Users/spartacus/.hermes/workspace/noreaster/intel/config/writers_roster.json
Output: writers.json (in the repo root) + portraits/<id>.png

Strips internal-only fields (system_prompt, portrait_prompt, model) so they
never leak through the public feed.

Run this whenever writers_roster.json changes, then `git add` / commit / push.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

SRC_ROSTER = Path("/Users/spartacus/.hermes/workspace/noreaster/intel/config/writers_roster.json")
SRC_PORTRAITS = Path("/Users/spartacus/.hermes/workspace/noreaster/intel/assets/writer-portraits")
OUT_ROOT = Path(__file__).resolve().parent.parent      # repo root
OUT_JSON = OUT_ROOT / "writers.json"
OUT_PORTRAITS = OUT_ROOT / "portraits"

PUBLIC_BASE = "https://raw.githubusercontent.com/georgescocca-dev/nyangler-writers-feed/main"

# Fields stripped from the public feed (internal scaffolding)
INTERNAL_FIELDS = {"system_prompt", "portrait_prompt", "model"}


def split_name(full: str) -> dict:
    """Decompose a name like 'Denise "Dee" Vasquez' into structured parts."""
    clean = re.sub(r'\s*"[^"]*"\s*', " ", full).strip()
    parts = clean.split()
    nickname_m = re.search(r'"([^"]+)"', full)
    return {
        "full": full,
        "first": parts[0] if parts else full,
        "last": parts[-1] if len(parts) > 1 else "",
        "nickname": nickname_m.group(1) if nickname_m else None,
        "display": full.replace('"', "").replace("  ", " ").strip(),
    }


def build_public_record(w: dict) -> dict:
    name = split_name(w["name"])
    return {
        "id": w["id"],
        "name": name["display"],
        "full_name": name["full"],
        "first_name": name["first"],
        "last_name": name["last"],
        "nickname": name["nickname"],
        "role": w["role"],
        "domain": w.get("domain"),
        "zone": {
            "slug": w["zone_slug"],
            "name": w["zone_name"],
        },
        "coverage_area": w.get("area"),
        "beat_species": w.get("beat_species", []),
        "landmarks": w.get("landmarks", []),
        "voice": w.get("voice"),
        "mood": w.get("mood"),
        "style_tags": w.get("style_tags", []),
        "bio": w.get("bio") or "",
        "portrait_url": f"{PUBLIC_BASE}/portraits/{w['id']}.png",
        "status": w.get("status", "active"),
    }


def main() -> int:
    if not SRC_ROSTER.exists():
        print(f"ERROR: roster not found at {SRC_ROSTER}", file=sys.stderr)
        return 1
    if not SRC_PORTRAITS.exists():
        print(f"ERROR: portraits dir not found at {SRC_PORTRAITS}", file=sys.stderr)
        return 1

    roster = json.loads(SRC_ROSTER.read_text(encoding="utf-8"))
    writers = roster.get("writers", [])
    if not writers:
        print("ERROR: roster has no writers", file=sys.stderr)
        return 1

    # Fresh portraits/ dir (drops removed writers, refreshes existing)
    if OUT_PORTRAITS.exists():
        shutil.rmtree(OUT_PORTRAITS)
    OUT_PORTRAITS.mkdir(parents=True)

    public = []
    missing_portraits = []
    for w in writers:
        public.append(build_public_record(w))
        src = SRC_PORTRAITS / f"{w['id']}.png"
        if src.exists():
            shutil.copy(src, OUT_PORTRAITS / f"{w['id']}.png")
        else:
            missing_portraits.append(w["id"])

    manifest = {
        "schema_version": "1.0",
        "site": "reports.nyangler.com",
        "publisher": "Nor'easter / NY Angler",
        "description": (
            "Editorial roster: 1 editor-in-chief + 18 zone writers covering "
            "NY/NJ inshore, surf, sound, bay, and offshore canyons."
        ),
        "feed_url": f"{PUBLIC_BASE}/writers.json",
        "total_writers": len(public),
        "writers": public,
    }
    OUT_JSON.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    print(f"✓ Wrote {OUT_JSON} ({len(public)} writers, {OUT_JSON.stat().st_size:,} bytes)")
    print(f"✓ Copied {len(list(OUT_PORTRAITS.glob('*.png')))} portraits")
    if missing_portraits:
        print(f"⚠ Missing portraits for: {missing_portraits}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
