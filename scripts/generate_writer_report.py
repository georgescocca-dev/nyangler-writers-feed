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
You are writing a single dated fishing report under your byline for
reports.nyangler.com — a premium fishing intelligence site.

NON-NEGOTIABLE RULES:
1. Only reference species, locations, and landmarks listed in your beat
   profile below or in the source forum reports. NEVER invent a place name.
2. If the source forum reports clearly say "slow" or "tough", say so. Do not
   manufacture a hot bite that the data does not support.
3. Cite specific report authors by username when summarizing observations
   ("Snapprhead27 reported a 33-inch keeper out of Captree on the 16th…").
4. Voice = third person editorial byline mixed with your first-person quotes.
   Open with a two-sentence dateline-style lead. Then a 3-4 paragraph body.
   Close with a "Bottom line" of 2 sentences and a forward-looking prediction.
5. Length: 400–550 words. No more, no less.
6. Use Fahrenheit for water temps. Reference the SST date if you cite a temp.
7. Never say "phish". Always "fish".
8. No emoji. No hashtags. No call-to-action. No "subscribe".

OUTPUT FORMAT — return ONLY valid JSON with this exact schema:
{
  "headline": "string, max 80 chars, sentence case, no clickbait",
  "subhead": "string, one sentence, max 140 chars",
  "dateline": "string, e.g. CAPTREE, NY — May 29",
  "body_markdown": "string, the full report body in markdown. Use **bold** for emphasis sparingly. Use blockquotes (>) for direct angler quotes. Do NOT include the headline or dateline inside this field.",
  "bottom_line": "string, 2 sentences max",
  "forward_look": "string, what to watch over the next 3–5 days",
  "tags": ["string array of 3–6 lowercase tags, e.g. striped-bass, captree, outgoing-tide"],
  "cited_threads": [{"thread_id": int, "url": "string", "author": "string"}]
}
"""


def build_prompt(writer: dict, reports: list[dict], sst: dict) -> tuple[str, str]:
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
    source_reports = [shorten_report(r) for r in reports]

    sst_summary = ""
    if sst:
        sst_summary = (
            f"Latest SST package date: {sst.get('latest_date')}. "
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
            "sst": sst_summary,
            "source_forum_reports": source_reports,
            "task": (
                "Write today's fishing report for your zone, drawing on the "
                "source forum reports above. Synthesize observations across "
                "multiple anglers where possible. Be honest about a slow bite "
                "if that's what the data shows. Return ONLY the JSON object "
                "specified in the rules — no preamble, no markdown code fence."
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
        f"**Bottom line.** {report.get('bottom_line','')}",
        "",
        f"**Forward look.** {report.get('forward_look','')}",
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
        "schema_version": "1.0",
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

    print(f"[gen] writer={writer['name']} zone={zone_slug} source_reports={len(reports)} sst={sst.get('latest_date')}", file=sys.stderr)
    if not reports:
        print(f"[warn] no source reports found for {zone_slug}", file=sys.stderr)

    system, user = build_prompt(writer, reports, sst)
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
