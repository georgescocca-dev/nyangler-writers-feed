#!/usr/bin/env python3
"""
Mariana Reyes — Fire Island / Great South Bay Report Generator

Produces a single, dated fishing report in Mariana's voice by synthesizing:
  • Her persona from writers_roster.json (voice, mood, beat, system prompt)
  • Recent forum reports for her zone (raw nyangler scrape)
  • Latest SST package (water temps, dates)
  • Optional environmental signals (moon phase, wind, tide-window)

Writes:
  • <out_dir>/reports/<id>.json  (machine-readable, published to GitHub feed)
  • <out_dir>/reports/<id>.md    (human-readable preview)

Designed to be safe and deterministic-ish — passes ALL the source intel to the
LLM and constrains the output schema, so the writer can never invent species
or locations that aren't in her beat.

Usage:
  python3 scripts/generate_writer_report.py mariana-reyes [--dry-run] [--push]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
WORKSPACE = Path("/Users/spartacus/.hermes/workspace")
NOREASTER = WORKSPACE / "projects" / "noreaster-intel"
SRC_ROSTER = NOREASTER / "config" / "writers_roster.json"
SRC_REPORTS = NOREASTER / "data" / "raw" / "nyangler"
SST_LATEST = NOREASTER / "sst-pipeline" / "output" / "latest.json"
FEED_REPO = WORKSPACE / "projects" / "nyangler-writers-feed"
REPORTS_DIR = FEED_REPO / "reports"
REPORTS_INDEX = FEED_REPO / "reports.json"

# Map writer id -> zone slug used in raw/nyangler/<zone>/ data path
WRITER_TO_ZONE_BUCKET = {
    "fire-island": "fire-island",
    "jamaica-bay": "jamaica-bay",
    "western-sound": "western-sound",
    "central-sound": "central-sound",
    "eastern-sound": "eastern-sound",
    "jones-inlet": "jones-inlet",
    "moriches": "moriches",
    "shinnecock": "shinnecock",
    "montauk": "montauk",
    "peconic": "peconic",
    "block-island": "block-island",
    "nj-shore": "nj-shore",
}


# ---------------------------------------------------------------------------
# Source intel gathering
# ---------------------------------------------------------------------------
def load_writer(writer_id: str) -> dict:
    roster = json.loads(SRC_ROSTER.read_text(encoding="utf-8"))
    for w in roster["writers"]:
        if w["id"] == writer_id:
            return w
    raise SystemExit(f"writer not found: {writer_id}")


def load_recent_reports(zone_slug: str, limit: int = 30) -> list[dict]:
    bucket_dir = SRC_REPORTS / zone_slug
    if not bucket_dir.exists():
        return []
    reports: list[dict] = []
    for f in sorted(bucket_dir.glob("reports_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                reports.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    reports.sort(key=lambda r: r.get("date", ""), reverse=True)
    return reports[:limit]


def load_sst() -> dict:
    try:
        return json.loads(SST_LATEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def shorten_report(r: dict, max_chars: int = 700) -> dict:
    """Trim a single raw report to fit in the LLM context cleanly."""
    text = r.get("text", "") or ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return {
        "date": r.get("date"),
        "author": r.get("author"),
        "title": r.get("title", "")[:120],
        "text": text,
        "thread_id": r.get("thread_id"),
        "url": f"https://nyangler.com/threads/{r.get('thread_id')}",
    }


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------
EDITORIAL_RULES = """
You are filing a serious, oceanographically-grounded fishing report under
your own byline for reports.nyangler.com. Think of yourself as a veteran
beat reporter at a regional publication — closer to *Anglers Journal* or
*On The Water* than a forum digest.

NON-NEGOTIABLE RULES:

1. FACTUAL DEPTH, NOT FORUM RECAP.
   Do NOT name forum posters, do NOT quote them, do NOT cite them with
   "according to X" phrasing. The forum reports you receive are background
   intelligence — synthesize them silently. The reader does not care what
   Snapprhead27 said. They care what the water is doing.

2. LEAD WITH THE OCEAN.
   Open the report with the physical state of your beat — water temperature
   with buoy station name and the actual reading in Fahrenheit, sea state,
   wind, the thermal structure between inside and outside water, where the
   bait is. This is the dateline of your reporting. Not a fish story.

3. SECOND PARAGRAPH = WHAT IT MEANS FOR YOUR BEAT.
   Translate the oceanographic state into specific predictions for your
   zone: which structure is firing, which tide stage, which species class
   (slot vs overslot vs short), which technique matches the water.

4. THIRD AND FOURTH PARAGRAPHS = ON-THE-GROUND INTELLIGENCE.
   Synthesize forum signal and any specific zone evidence into observations.
   Phrase as a beat reporter: "The Captree night bite has shifted away from
   clams toward soft plastics in the last week" — not "user X said he caught
   3 keepers on plastics."

5. FIFTH PARAGRAPH = THE LOOK-AHEAD.
   Specific. What changes in the next 3–5 days. Wind shifts, moon phase,
   temperature thresholds, bait migration. Tie it to mechanism, not vibes.

6. CLOSE.
   One paragraph. Authoritative. No "bottom line" headers, no bullet points,
   no "subscribe", no exclamation, no hashtags, no emoji.

7. NEVER INVENT.
   • Only species in your beat profile.
   • Only landmarks in your beat profile or explicitly in the oceanographic
     analyst output.
   • Only water-temperature figures that appear in the analyst data — if
     the analyst is silent on a station, do not fabricate a number.
   • If the analyst flags a data gap (e.g. no SST today, buoy offline),
     acknowledge it briefly and reason around it.

8. VOICE.
   Third-person beat-reporter authority, with first-person sparingly — only
   when you are stating a personal judgment ("I'd be watching the Robert
   Moses bridges first thing on the outgoing"). No "as a longtime…" filler.
   No autobiographical asides. The reader knows who you are.

9. LENGTH: 550–800 words. Density over filler.

10. NEVER say "phish". Always "fish".

OUTPUT FORMAT — return ONLY valid JSON with this exact schema:

{
  "headline": "string, max 90 chars, sentence case, declarative not clickbait. e.g. 'Estuary thermal engine drives Fire Island bite as offshore stays sloppy'",
  "subhead": "string, one factual sentence, max 160 chars. Should name the controlling oceanographic variable.",
  "dateline": "string, e.g. 'CAPTREE, NY — May 30'",
  "body_markdown": "string. 550–800 words of plain markdown — NO H2 or H3 headings, NO bullet lists, NO blockquotes. Just paragraphs. The body must FOLLOW the five-paragraph structure described in rules 2–6.",
  "tags": ["3–6 lowercase hyphen-tags, mechanism-focused: estuary-thermal, outgoing-tide, slot-class, soft-plastics, etc."]
}
"""


ANALYST_DIR = NOREASTER / "data" / "analysis"


def load_latest_analyst() -> dict:
    """Load the most recent Dr. Fish analyst JSON if available."""
    if not ANALYST_DIR.exists():
        return {}
    files = sorted(ANALYST_DIR.glob("analyst_*.json"), reverse=True)
    if not files:
        return {}
    try:
        return json.loads(files[0].read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def filter_analyst_for_zone(analyst: dict, zone_slug: str, writer: dict) -> dict:
    """Extract the parts of the Dr. Fish analyst output most relevant to this writer's beat.

    We strip noise (source_health, methodology) and keep the science:
    live buoy conditions, seasonal context, pattern detection, and the
    regional outlook entry whose region name overlaps the writer's zone.
    """
    if not analyst:
        return {}
    landmarks = [s.lower() for s in writer.get("landmarks", [])]
    zone_name = (writer.get("zone_name") or "").lower()
    zone_words = set(filter(None, re.split(r"[/\s,]+", zone_name)))

    out: dict = {
        "run_date": analyst.get("run_date"),
        "live_conditions": analyst.get("live_conditions", {}),
        "seasonal_context": analyst.get("seasonal_context", {}),
        "pattern_detection": analyst.get("pattern_detection", {}),
        "data_gaps": analyst.get("data_gaps", []),
        "headline": (analyst.get("analyst_summary") or {}).get("headline"),
    }
    # Pull only the regional_outlook entries whose region matches our zone
    relevant = []
    for region in analyst.get("regional_outlook", []) or []:
        rname = (region.get("region") or "").lower()
        if any(w and w in rname for w in zone_words) or any(
            lm in rname for lm in landmarks
        ):
            relevant.append(region)
    # If nothing matched, include all so the writer can frame the macro
    if not relevant:
        relevant = analyst.get("regional_outlook", []) or []
    out["regional_outlook_for_beat"] = relevant
    return out


def build_prompt(writer: dict, reports: list[dict], sst: dict, analyst: dict) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt)."""
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    beat = {
        "id": writer["id"],
        "name": writer["name"],
        "role": writer["role"],
        "zone": writer.get("zone_name"),
        "area": writer.get("area"),
        "beat_species": writer.get("beat_species", []),
        "landmarks": writer.get("landmarks", []),
        "voice": writer.get("voice"),
        "mood": writer.get("mood"),
        "style_tags": writer.get("style_tags", []),
    }
    background_reports = [shorten_report(r) for r in reports]
    sst_summary = ""
    if sst:
        sst_summary = (
            f"Latest published SST package date: {sst.get('latest_date')}. "
            f"Available dates in catalog: {', '.join(sst.get('available_dates', []))}."
        )

    system = (
        writer.get("system_prompt", "")
        + "\n\n---\n"
        + EDITORIAL_RULES.strip()
    )

    user = json.dumps(
        {
            "today": today,
            "beat_profile": beat,
            "primary_intel_dr_fish_oceanographic_analyst": analyst,
            "sst_pipeline_status": sst_summary,
            "background_forum_chatter_DO_NOT_CITE": background_reports,
            "task": (
                "Write today's fishing report for your zone. "
                "Lead with the OCEANOGRAPHIC state from the Dr. Fish analyst "
                "data above — that is your primary source. Buoy readings, "
                "water temperatures with station names, sea state, thermal "
                "structure. The forum chatter is BACKGROUND ONLY — use it to "
                "confirm what the ocean data implies, but do NOT cite users "
                "or quote them. Reason from physics to fish. "
                "Return ONLY the JSON object specified — no preamble, no "
                "markdown code fence."
            ),
        },
        indent=2,
        ensure_ascii=False,
    )
    return system, user


def call_openrouter(system: str, user: str, model: str) -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        # Lazy-load from .env
        env_path = Path.home() / ".hermes" / ".env"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("OPENROUTER_API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not api_key:
        raise SystemExit("OPENROUTER_API_KEY not set in env or ~/.hermes/.env")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.4,
        "max_tokens": 2200,
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://reports.nyangler.com",
            "X-Title": "reports.nyangler.com writer generator",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def parse_llm_json(raw: str) -> dict:
    """Strip code fences if the model wraps the response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n", "", raw)
        raw = re.sub(r"\n```$", "", raw)
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------
def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:60]


def emit_report(writer: dict, report: dict, today: datetime) -> dict:
    report_id = f"{today.strftime('%Y-%m-%d')}-{writer['id']}-{slugify(report['headline'])}"
    publication = {
        "id": report_id,
        "writer_id": writer["id"],
        "writer_name": writer["name"],
        "writer_role": writer["role"],
        "writer_portrait_url": f"https://raw.githubusercontent.com/georgescocca-dev/nyangler-writers-feed/main/portraits/{writer['id']}.png",
        "zone": {
            "slug": writer.get("zone_slug"),
            "name": writer.get("zone_name"),
        },
        "published_at": today.isoformat(),
        "date": today.strftime("%Y-%m-%d"),
        **report,
    }
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_json = REPORTS_DIR / f"{report_id}.json"
    out_json.write_text(
        json.dumps(publication, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    out_md = REPORTS_DIR / f"{report_id}.md"
    md = [
        f"# {report['headline']}",
        f"*{report.get('subhead','')}*",
        "",
        f"**{report.get('dateline','')}** — _by {writer['name']}, {writer['role']}_",
        "",
        report.get("body_markdown", ""),
        "",
        "---",
        "",
        "Tags: " + ", ".join(report.get("tags", [])),
    ]
    out_md.write_text("\n".join(md) + "\n", encoding="utf-8")

    return publication


def rebuild_reports_index() -> dict:
    """Scan reports/*.json and rebuild the public reports.json index."""
    items = []
    for f in sorted(REPORTS_DIR.glob("*.json")):
        try:
            items.append(json.loads(f.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    # Newest first
    items.sort(key=lambda x: x.get("published_at", ""), reverse=True)
    PUBLIC_BASE = "https://raw.githubusercontent.com/georgescocca-dev/nyangler-writers-feed/main"
    index = {
        "schema_version": "2.0",
        "feed_url": f"{PUBLIC_BASE}/reports.json",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_reports": len(items),
        "reports": items,
    }
    REPORTS_INDEX.write_text(
        json.dumps(index, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return index


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description="Generate a fishing report for a writer.")
    p.add_argument("writer_id", help="e.g. fire-island, jamaica-bay")
    p.add_argument("--model", default="anthropic/claude-opus-4.7",
                   help="OpenRouter model id (default: anthropic/claude-opus-4.7)")
    p.add_argument("--limit", type=int, default=30, help="Max source reports to include (default 30)")
    p.add_argument("--dry-run", action="store_true", help="Print the report; do not write files.")
    args = p.parse_args()

    writer = load_writer(args.writer_id)
    zone_slug = WRITER_TO_ZONE_BUCKET.get(writer["id"]) or writer.get("zone_slug") or writer["id"]
    reports = load_recent_reports(zone_slug, limit=args.limit)
    sst = load_sst()
    raw_analyst = load_latest_analyst()
    analyst = filter_analyst_for_zone(raw_analyst, zone_slug, writer)

    print(
        f"[gen] writer={writer['name']} zone={zone_slug} "
        f"forum_bg={len(reports)} sst={sst.get('latest_date')} "
        f"analyst={'yes' if analyst else 'NO'} "
        f"regional_match={len(analyst.get('regional_outlook_for_beat', []))}",
        file=sys.stderr,
    )
    if not analyst:
        print(
            "[warn] no Dr. Fish analyst data available — report will be thinner than ideal",
            file=sys.stderr,
        )

    system, user = build_prompt(writer, reports, sst, analyst)
    print(f"[gen] calling {args.model} (prompt={len(system)+len(user):,} chars)", file=sys.stderr)
    raw = call_openrouter(system, user, args.model)
    try:
        report = parse_llm_json(raw)
    except json.JSONDecodeError as e:
        print(f"[error] model returned non-JSON: {e}\n---\n{raw[:1200]}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    today = datetime.now(timezone.utc)
    pub = emit_report(writer, report, today)
    index = rebuild_reports_index()
    print(f"[gen] wrote {pub['id']}", file=sys.stderr)
    print(f"[gen] index now has {index['total_reports']} reports", file=sys.stderr)
    print(json.dumps({"id": pub["id"], "headline": report["headline"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
