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
TEASERS_INDEX = FEED_REPO / "teasers.json"

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
    # Offshore canyon captains — map to offshore bucket for forum intel
    "hudson-canyon": "offshore",
    "wilmington-canyon": "offshore",
    "washington-canyon": "offshore",
    "south-canyons": "offshore",
    "east-canyons": "offshore",
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
You are writing YOUR fishing report — your voice, your beat, your way of
seeing the water. You are a real angler who fishes this zone every week.
You talk to captains, you talk to guys at the dock, you watch the bait,
you read the water. This is YOUR column for reports.nyangler.com.

Write it like you'd tell your best fishing buddy what's happening on
your water this week — except polished enough for print.

HARD RULES (break these and we pull the column):

1. NO FORUM NAMES. No usernames, no "according to X." The intel you get
   from forum posts is background — you synthesize it into your own voice.
   Never reveal the source.

2. NO DATA-GAP COMPLAINTS. If a buoy is offline or SST is stale, work
   around it. Real anglers don't write about what instruments they can't
   read — they write about what they saw on the water. Use whatever data
   you have. Skip what you don't. Never say "buoys went dark" or "data
   is stale" or "running blind." Just fish.

3. NO INVENTED CATCHES. Only reference species in your beat profile and
   landmarks in your zone. Don't fabricate water temps — if you have a
   number, use it; if you don't, describe conditions qualitatively (warm,
   cold, dirty, clean, ripping, slack).

4. NEVER say "phish." Always "fish."

BANNED PHRASES — never use any of these. They are clichés that make
every writer sound the same:

- "on fire" / "absolutely on fire" / "lit up"
- "finally woke up" / "finally showing"
- "firing on all cylinders"
- "responding exactly how they should" / "responding like they should"
- "and I'm telling you" / "and I mean really" / "and I mean *firing*"
- "the kind of fishing that makes you forget about..."
- "everything we've been waiting for"
- "the kind of [fish] that makes you remember why..."
- "officially here" / "officially summer" / "officially arrived"
- "the water's telling a story" (find a different way to say it)

If you catch yourself using any of these phrases, stop and rewrite.

HONESTY — THIS IS CRITICAL:

Fishing is not always good. Real anglers get skunked. Real anglers
have tough sessions, tough weeks, tough conditions. Your readers are
anglers — they know when they're being sold a bill of goods.

- If the bite is genuinely on, say so — but don't oversell it. "Solid
  bass action" is fine. "The greatest fishing of all time" is not.
- If the bite is mixed or spotty, SAY SO. "The bass are here but scattered
  — I covered twenty miles of beach to put three keepers in the truck"
  is a better report than pretending it was easy.
- If the bite is slow, SAY SO. "Tough week on the Sound. The wind's been
  out of the southwest for five days straight, churned the water into
  milo, and the bass have lockjaw. Here's what I'd do differently..." is
  a real report. Anglers respect honesty. They don't respect cheerleading.
- Never give the impression that a reader is guaranteed to catch fish.
  "Worth a shot if you can get out before the front pushes through" is
  honest. "Get out there NOW, it's going off!" is not.
- A report that says "the fishing was slow but here's what I learned"
  is more valuable than one that says "epic bite, get out there!"

THE CONDITIONS PRELUDE — REQUIRED:

Before you get into what's being caught, open with 1-2 paragraphs of
conditions analysis. This is where you show your expertise. Cover:

- WHAT HAPPENED LAST WEEK: How did the tides set up? What did the wind
  do? Did water temps move? Was there a moon phase that mattered? Did a
  front push through? Did a storm roll the water?
- WHAT'S COMING THIS WEEK: Moon phase (new, full, quarter, waxing,
  waning — and what it means for tides and feeding). Tide heights and
  timing. Wind forecast. Any weather systems approaching. Water temp
  trend (warming, cooling, stable).
- THE WHY: Connect these conditions to what the fish are doing or will
  do. "Last quarter moon on Friday means neap tides this weekend — the
  current slows at the rips, which lets the bass settle into the eddies
  and feed at slack instead of holding in the current lane. That changes
  where I'd fish and when." That's the kind of analysis that separates a
  real angler from a reporter.

Use the tide_moon, bite_windows, emerging_patterns, and key_predictions
data from your analyst package. That's your source for moon phases, tide
heights, thermal patterns, and what the oceanography is doing. Weave it
into YOUR voice — don't just list data points. Interpret them the way
an experienced angler would.

This prelude is NOT optional. Every report opens with conditions and
the look-ahead, THEN moves into what's being caught.

WHAT MAKES A GREAT REPORT:

• CATCHES AND TACTICS — after the prelude, get into what's being
  caught: species, sizes, where, on what (specific baits, lures, rigs,
  presentations), at what depth, on which tide. "Fluke to 6 pounds on
  white Gulp and chartreuse bucktails in 30 feet off the Robert Moses
  bridge, outgoing water" — that's what readers want.

• BE SPECIFIC ABOUT BAIT AND TECHNIQUE. Don't say "soft plastics are
  working." Say "5-inch white Gulp Swimming Mullets on 3/4-oz bucktails,
  dragged slow on the drift." Don't say "live bait." Say "peanut bunker
  on a fishfinder rig, fished tight to the pilings on the outgoing."

• EXPLAIN THE WHY. You're not just a reporter — you understand fish
  behavior. Why are the bass hitting here and not there? Because the
  thermocline set up at 45 feet and pushed bait against the shelf.
  Because the new moon spring tides are flushing bunker out of the bay.
  Because the eddy off the canyon wall is holding 68-degree water while
  everything around it is 62. Connect the dots between conditions, bait,
  and fish.

• YOUR VOICE, YOUR PERSONALITY. You have a distinct way of talking.
  Use it. If you're a night-fishing specialist, your report should feel
  different from the guy who runs a center-console out of Montauk. Your
  cadence, your vocabulary, your obsessions — let them show. Your
  opening line should sound like YOU, not like a fishing report template.

• THE LOOK-AHEAD. End with what you expect in the coming week and why.
  "Full moon Friday means big tides — I'm watching the inlet drain at
  sunset for the first real run of weakfish."

PREDICTIONS AND THE LOOK-AHEAD — HOW TO FRAME UNCERTAINTY:

You're a fisherman, not a psychic. Your predictions should sound like
an experienced angler making an educated call — confident in the
reasoning, honest about the uncertainty, without ever saying
"nothing is guaranteed" or "no guarantees" or any variation of that
disclaimer. Anglers know fishing is unpredictable. They don't need
you to remind them. They need you to tell them what the conditions
suggest and where they'd put their time.

Frame predictions through one of these natural angler approaches:

- CONDITIONAL: "IF the wind lays down Saturday night, the drift at
  the lighthouse should set up perfect for the early flood. That's
  where I'd start." The IF does the hedging for you — it says "this
  depends on something I can't control" without saying "no guarantees."

- ODDS-BASED: "Three trips out there this week, connected on two of
  them. Those are good numbers for late June, but it's not a sure
  thing — you still need the right tide and a little luck." Real
  numbers, real context, honest framing.

- SCENARIO: "Best case, the eddy holds through Tuesday and the tuna
  stay stacked on the 100-fathom line. If it slides east like it did
  last month, I'd shift to the flats. Either way, the water's warm
  enough that something's going to eat." Acknowledges multiple
  outcomes without hedging.

- EXPERIENCE-BASED: "I've seen this pattern before — neap tides in
  late June, the bass slide off the rips and settle into the deeper
  eddies. Doesn't mean it's guaranteed, but that's where I'd put my
  time." Your track record IS the credibility. No disclaimer needed.

- WHAT YOU'D DO: "If I had one day this weekend, I'd fish the
  Sunday morning flood at the inlet. The moon's right, the tide's
  right, and there are enough fish around to make it worth the trip.
  But I'd have a Plan B — the back bay fluke drift if the bass
  don't show." Showing you have a Plan B says more about uncertainty
  than any disclaimer.

NEVER use these phrases — they're the verbal equivalent of a
disclaimer and they kill credibility:

- "nothing is guaranteed" / "no guarantees"
- "as always, fishing is unpredictable"
- "but that's fishing" / "that's why they call it fishing"
- "results may vary"
- "I can't promise anything"

Instead, let the conditions do the hedging. Name what needs to
happen for the prediction to play out. That's how real anglers talk.

STRUCTURE: Open with the conditions prelude (1-2 paragraphs), then
move into catches and tactics in whatever structure fits the week. Some
weeks the prelude is the whole story (tough conditions, slow bite, here's
why). Some weeks it's a quick setup before the catches take over. Match
the energy to the week — but the prelude is always there.

LENGTH: 800–1100 words. The conditions prelude is roughly 200 words on
its own, then 600+ for the catches and look-ahead. If your report is
under 800 words, you are leaving out analysis the reader needs.

OUTPUT FORMAT — return ONLY valid JSON with this exact schema:

{
  "headline": "string, max 90 chars. Punchy, specific, says what happened. e.g. 'Doormat fluke crash the Captree drift as bay water hits 66' or 'Bunker blitz fires the Rye rocks at sunset'",
  "subhead": "string, one sentence, max 160 chars. The hook that makes you read the whole thing.",
  "dateline": "string, e.g. 'CAPTREE, NY — June 12'",
  "body_markdown": "string. 800-1100 words. Opens with 1-2 paragraphs of conditions and moon/tide/weather analysis (the prelude), then flows into catches and tactics. NO H2 or H3 headings, NO bullet lists, NO blockquotes. Just your voice in paragraphs.",
  "tags": ["3–6 lowercase hyphen-tags: species, technique, location focused. e.g. fluke, bucktail, captree-drift, outgoing-tide, bunker"]
}
"""


ANALYST_DIR = NOREASTER / "data" / "analysis"
YOUTUBE_WEEKLY_DIR = NOREASTER / "data" / "knowledge" / "youtube_weekly_reports"
YOUTUBE_KNOWLEDGE = NOREASTER / "data" / "knowledge" / "fishing_knowledge.jsonl"


def load_latest_analyst(writer_id: str | None = None) -> dict:
    """Load Dr. Fish analyst data covering the full period since the last published report.

    If writer_id is provided, finds the date of that writer's most recent report
    and loads ALL analyst files from that date forward (inclusive of the day after
    the last report, so we don't re-feed data the last report already used).

    If no writer_id or no prior report exists, loads the latest analyst file only
    (same as original behavior).

    Multiple analyst files are merged: the latest file is the base (it has the
    most recent buoy readings), and earlier files' regional_outlook entries are
    prepended so the writer sees the full progression of conditions.
    """
    if not ANALYST_DIR.exists():
        return {}

    all_files = sorted(ANALYST_DIR.glob("analyst_*.json"), reverse=True)
    if not all_files:
        return {}

    # Determine the cutoff date: day after the writer's last published report
    cutoff_date = None
    if writer_id:
        # Find the writer's most recent report file
        writer_reports = sorted(
            REPORTS_DIR.glob(f"*{writer_id}*.json"), reverse=True
        )
        if writer_reports:
            # Extract date from filename: YYYY-MM-DD-writer-id-...
            fname = writer_reports[0].name
            if fname.startswith("20"):
                try:
                    last_date_str = fname[:10]  # e.g. "2026-06-24"
                    last_date = datetime.strptime(last_date_str, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    # Day after the last report — we want NEW intel
                    from datetime import timedelta
                    cutoff_date = last_date + timedelta(days=1)
                except (ValueError, IndexError):
                    pass

    if cutoff_date is None:
        # No prior report or no writer_id — just load latest
        try:
            return json.loads(all_files[0].read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    # Load all analyst files from cutoff_date forward
    # Analyst files: analyst_YYYY-MM-DD.json and analyst_YYYY-MM-DD_afternoon.json etc.
    relevant_files = []
    for f in all_files:
        # Extract date from filename
        # Pattern: analyst_2026-06-24.json or analyst_2026-06-24_afternoon.json
        parts = f.stem.replace("analyst_", "").split("_")[0]  # e.g. "2026-06-24"
        try:
            file_date = datetime.strptime(parts, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if file_date >= cutoff_date:
            relevant_files.append(f)

    if not relevant_files:
        # Fallback to latest
        try:
            return json.loads(all_files[0].read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    if len(relevant_files) == 1:
        try:
            return json.loads(relevant_files[0].read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    # Multiple files: merge them.
    # Latest file is the base (most recent buoy data).
    # Earlier files' regional_outlook entries are prepended as "prior_conditions".
    relevant_files = sorted(relevant_files)  # oldest first now
    merged = None
    prior_outlooks = []

    for f in relevant_files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if merged is None:
            merged = data
        else:
            # Collect regional outlook from earlier days
            ro = data.get("regional_outlook")
            if ro:
                prior_outlooks.append({
                    "date": data.get("date"),
                    "headline": data.get("headline", "")[:200],
                    "regional_outlook": ro,
                })

    if merged is None:
        return {}

    if prior_outlooks:
        merged["prior_analyst_runs_since_last_report"] = prior_outlooks

    return merged


def filter_analyst_for_zone(analyst: dict, zone_slug: str, writer: dict) -> dict:
    """Extract the parts of the Dr. Fish analyst output most relevant to this writer's beat.

    We strip noise (source_health, methodology) and keep the science:
    live buoy conditions, seasonal context, pattern detection, and the
    regional outlook entry whose region name overlaps the writer's zone.
    """
    if not analyst:
        return {}
    zone_slug = writer.get("id", "")
    zone_name = (writer.get("zone_name") or "").lower()

    out: dict = {
        "date": analyst.get("date"),
        "buoy_readings": analyst.get("buoy_readings", {}),
        "offline_buoys": analyst.get("offline_buoys", []),
        "yesterday_deltas": analyst.get("yesterday_deltas", {}),
        "wind_pattern": analyst.get("wind_pattern", {}),
        "thermal_structure": analyst.get("thermal_structure", {}),
        "wave_state": analyst.get("wave_state", {}),
        "tide_moon": analyst.get("tide_moon", {}),
        "bite_windows": analyst.get("bite_windows", []),
        "emerging_patterns": analyst.get("emerging_patterns", []),
        "key_predictions": analyst.get("key_predictions", []),
    }
    # regional_outlook is now a dict keyed by zone slug
    ro = analyst.get("regional_outlook", {})
    if isinstance(ro, dict):
        # Try exact zone slug match first
        if zone_slug in ro:
            out["regional_outlook_for_beat"] = ro[zone_slug]
        else:
            # Include all entries so the writer can frame the macro
            out["regional_outlook_for_beat"] = ro
    elif isinstance(ro, list):
        # Legacy format: list of dicts with "region" key
        out["regional_outlook_for_beat"] = ro
    return out


def load_recent_youtube_intel(zone_slug: str, days: int = 14) -> list[dict]:
    """Load YouTube-derived fishing intel from the last `days` days.

    Pulls from two sources:
    1. Structured weekly report JSONs (youtube_weekly_reports/)
    2. Knowledge entries from fishing_knowledge.jsonl (filtered by bucket match)

    Only returns entries with timestamps within the window. This ensures
    writers are working from CURRENT intel, not stale historical data.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    results: list[dict] = []

    # Source 1: Structured weekly reports
    if YOUTUBE_WEEKLY_DIR.exists():
        for f in sorted(YOUTUBE_WEEKLY_DIR.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            # Check if the report is within our window
            collected = data.get("collected_at", "")
            if collected:
                try:
                    col_dt = datetime.fromisoformat(collected.replace("Z", "+00:00"))
                    if col_dt < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass
            # Extract notable catches and hotspots relevant to this zone
            zone_key = zone_slug.replace("-", "_")
            hotspots = data.get("hotspots_by_region", {})
            zone_hotspot = hotspots.get(zone_key) or hotspots.get(zone_slug)
            if zone_hotspot:
                results.append({
                    "source": "YouTube weekly report",
                    "report_date": data.get("report_date"),
                    "collected_at": collected,
                    "type": "regional_hotspot",
                    "zone": zone_key,
                    "status": zone_hotspot.get("status"),
                    "species": zone_hotspot.get("species", []),
                    "notes": zone_hotspot.get("notes", ""),
                })
            # Also include notable catches that mention this zone's landmarks
            zone_terms = zone_slug.replace("-", " ").lower()
            for catch in data.get("notable_catches", []):
                location = (catch.get("location", "") + " " + catch.get("angler", "")).lower()
                if any(term in location for term in zone_terms.split()):
                    results.append({
                        "source": "YouTube weekly report",
                        "report_date": data.get("report_date"),
                        "collected_at": collected,
                        "type": "notable_catch",
                        **catch,
                    })

    # Source 2: Knowledge entries from fishing_knowledge.jsonl
    if YOUTUBE_KNOWLEDGE.exists():
        zone_terms = zone_slug.replace("-", " ").lower()
        for line in YOUTUBE_KNOWLEDGE.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            # Check timestamp freshness
            ts = entry.get("timestamp", "")
            if ts:
                try:
                    entry_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    if entry_dt < cutoff:
                        continue
                except (ValueError, TypeError):
                    continue
            else:
                continue  # Skip entries without timestamps
            # Filter by zone relevance
            bucket = (entry.get("bucket", "") or "").lower()
            region = (entry.get("region", "") or "").lower()
            if zone_terms in bucket or zone_terms in region or zone_slug in bucket:
                results.append({
                    "source": entry.get("source", "YouTube"),
                    "video_title": entry.get("video_title", ""),
                    "timestamp": ts,
                    "category": entry.get("category", ""),
                    "species": entry.get("species", []),
                    "knowledge": entry.get("knowledge", ""),
                    "tags": entry.get("tags", []),
                    "bucket": entry.get("bucket", ""),
                })

    return results[:50]  # Cap to keep prompt size reasonable


def build_prompt(writer: dict, reports: list[dict], sst: dict, analyst: dict, youtube_intel: list[dict] = None) -> tuple[str, str]:
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
            "youtube_intel_DO_NOT_CITE": youtube_intel or [],
            "background_forum_chatter_DO_NOT_CITE": background_reports,
            "task": (
                "Write this week's fishing report for your zone. "
                "OPEN with 1-2 paragraphs of conditions analysis — what "
                "happened last week (tides, wind, water temps, moon), "
                "what's coming this week (moon phase, tide heights, "
                "weather systems, temp trends), and WHY it matters to "
                "the fishing. Use the tide_moon, bite_windows, "
                "emerging_patterns, and key_predictions from your "
                "analyst data. Then move into what's being CAUGHT — "
                "species, sizes, tactics, baits, specific spots. "
                "Be honest about the bite quality — if it's slow or "
                "mixed, say so. Anglers respect honesty, not hype. "
                "Use the Dr. Fish analyst data to explain WHY "
                "conditions are producing. The forum chatter and "
                "YouTube intel are BACKGROUND ONLY — synthesize it "
                "into your voice, never cite users or video channels. "
                "Write it like YOUR column — your voice, your "
                "personality, your way of reading the water. Be "
                "specific on baits and rigs. Return ONLY the JSON "
                "object specified — no preamble, no markdown code "
                "fence. Remember: open with conditions/moon/tide "
                "analysis, then catches, then look-ahead."
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
        "max_tokens": 8000,
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
    choice = data["choices"][0]
    content = choice["message"].get("content")
    if content is None:
        raise SystemExit(
            f"Model returned null content (finish_reason={choice.get('finish_reason')}). "
            "May need higher max_tokens or the model returned only reasoning."
        )
    return content


def parse_llm_json(raw: str) -> dict:
    """Strip code fences and find the JSON object in the response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n", "", raw)
        raw = re.sub(r"\n```$", "", raw)
    # Some models (Sonnet 5) sometimes prepend prose before the JSON.
    # Find the first { and try to parse from there.
    json_start = raw.find("{")
    if json_start > 0:
        raw = raw[json_start:]
    # Also handle trailing text after the JSON
    # Find the last } and trim after it
    json_end = raw.rfind("}")
    if json_end > 0 and json_end < len(raw) - 1:
        raw = raw[:json_end + 1]
    return json.loads(raw)


# Phrases the LLM uses despite being told not to. We strip them post-hoc
# rather than re-rolling the generation (which costs another API call).
BANNED_PHRASES = [
    "absolutely on fire",
    "on fire",
    "lit up",
    "finally woke up",
    "finally showing",
    "firing on all cylinders",
    "responding exactly how they should",
    "responding like they should",
    "and I'm telling you",
    "and I mean really",
    "everything we've been waiting for",
    "officially here",
    "officially summer",
    "officially arrived",
    "the water's telling a story",
    # Disclaimer phrases — kill credibility
    "nothing is guaranteed",
    "no guarantees",
    "as always, fishing is unpredictable",
    "that's why they call it fishing",
    "but that's fishing",
    "results may vary",
    "i can't promise anything",
]


def scrub_report(report: dict) -> dict:
    """Post-process LLM output: strip banned phrases, ensure required fields."""
    body = report.get("body_markdown", "")
    for phrase in BANNED_PHRASES:
        # Case-insensitive replace, preserving surrounding context
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        body = pattern.sub("", body)
    # Clean up any double spaces left by removals
    body = re.sub(r"  +", " ", body)
    body = re.sub(r"\n +", "\n", body)
    report["body_markdown"] = body.strip()

    # Ensure tags exists (LLM occasionally drops it)
    if not report.get("tags"):
        report["tags"] = []

    return report


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


def generate_teasers(reports: list[dict]) -> list[str]:
    """Generate AI search box teasers grounded in actual report content."""
    teasers = []
    species_zones: dict[str, list[str]] = {}
    temps: list[tuple[str, str]] = []
    tactics: set[str] = set()

    for r in reports:
        zone = r.get("zone", {}).get("name", "")
        headline = r.get("headline", "")
        body = r.get("body_markdown", "")
        tags = r.get("tags", [])
        full_text = f"{headline} {body}".lower()

        # Water temps from headlines
        for m in re.findall(r"(\d+(?:\.\d+)?)\s*(?:°F|degrees)", headline):
            temps.append((zone, m))

        # Species mentions
        for sp in ["bass", "fluke", "weakfish", "porgy", "squid", "bunker",
                    "blackfish", "tog", "bluefish", "striper"]:
            if sp in full_text or sp in " ".join(tags).lower():
                species_zones.setdefault(sp, []).append(zone)

        # Tactics from tags
        for tag in tags:
            for tactic in ["bucktail", "soft-plastics", "live-eels",
                           "live-bunker", "clam-belly", "chunk-bait"]:
                if tactic in tag:
                    tactics.add(tactic.replace("-", " "))

    # Helper: pick a short, clean zone name for display
    def short_zone(z: str) -> str:
        return z.split("/")[0].strip()

    # Prefer specific/recognizable zone names over generic ones
    GENERIC_ZONES = {"new jersey shore", "north shore", "south shore"}

    def best_zone(zone_list: list[str]) -> str:
        """Pick the most specific zone from a list."""
        for z in zone_list:
            if short_zone(z).lower() not in GENERIC_ZONES:
                return short_zone(z)
        return short_zone(zone_list[0])

    # Build teasers from what's actually in the feed
    if temps:
        # Pick a temp from a recognizable zone
        named = [(z, t) for z, t in temps if short_zone(z).lower() not in GENERIC_ZONES]
        tz, _ = (named or temps)[0]
        teasers.append(f"What's the water temp at {short_zone(tz)}?")

    if "bass" in species_zones or "striper" in species_zones:
        teasers.append("Where are the slot bass biting on the outgoing tide?")

    if "fluke" in species_zones:
        teasers.append(f"Best spots for fluke in {best_zone(species_zones['fluke'])} this week?")

    if "weakfish" in species_zones:
        teasers.append(f"Any weakfish showing at {best_zone(species_zones['weakfish'])}?")

    if "bunker" in species_zones:
        teasers.append(f"Is the bunker bite on at {best_zone(species_zones['bunker'])}?")

    if "bluefish" in species_zones:
        teasers.append(f"Are bluefish crashing bait at {best_zone(species_zones['bluefish'])}?")

    if "porgy" in species_zones:
        teasers.append(f"Where are the porgies stacking up?")

    teasers.append("Where's the thermal break stacking fish right now?")

    if "bucktail" in tactics:
        teasers.append("Who's catching on bucktails this week?")
    if "live eels" in tactics:
        teasers.append("Where are live eels producing trophy bass?")

    teasers.append("What's the best tide window for the South Shore bays?")

    # Cap at 10 to keep the scrolling box tight
    return teasers[:10]


def rebuild_teasers(reports: list[dict]) -> None:
    """Regenerate teasers.json from current reports."""
    teasers = generate_teasers(reports)
    payload = {"teasers": teasers}
    TEASERS_INDEX.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"[gen] teasers.json updated with {len(teasers)} questions", file=sys.stderr)


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
    rebuild_teasers(items)
    return index


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description="Generate a fishing report for a writer.")
    p.add_argument("writer_id", help="e.g. fire-island, jamaica-bay")
    p.add_argument("--model", default="anthropic/claude-sonnet-5",
                   help="OpenRouter model id (default: anthropic/claude-sonnet-5)")
    p.add_argument("--limit", type=int, default=30, help="Max source reports to include (default 30)")
    p.add_argument("--dry-run", action="store_true", help="Print the report; do not write files.")
    args = p.parse_args()

    writer = load_writer(args.writer_id)
    zone_slug = WRITER_TO_ZONE_BUCKET.get(writer["id"]) or writer.get("zone_slug") or writer["id"]
    reports = load_recent_reports(zone_slug, limit=args.limit)
    sst = load_sst()
    raw_analyst = load_latest_analyst(writer_id=args.writer_id)
    analyst = filter_analyst_for_zone(raw_analyst, zone_slug, writer)
    youtube_intel = load_recent_youtube_intel(zone_slug, days=14)

    print(
        f"[gen] writer={writer['name']} zone={zone_slug} "
        f"forum_bg={len(reports)} sst={sst.get('latest_date')} "
        f"analyst={'yes' if analyst else 'NO'} "
        f"analyst_runs={len(raw_analyst.get('prior_analyst_runs_since_last_report', [])) + 1 if raw_analyst else 0} "
        f"regional_match={len(analyst.get('regional_outlook_for_beat', []))} "
        f"youtube_intel={len(youtube_intel)}",
        file=sys.stderr,
    )
    if not analyst:
        print(
            "[warn] no Dr. Fish analyst data available — report will be thinner than ideal",
            file=sys.stderr,
        )

    system, user = build_prompt(writer, reports, sst, analyst, youtube_intel)
    print(f"[gen] calling {args.model} (prompt={len(system)+len(user):,} chars)", file=sys.stderr)
    raw = call_openrouter(system, user, args.model)
    try:
        report = parse_llm_json(raw)
    except json.JSONDecodeError as e:
        print(f"[error] model returned non-JSON: {e}\n---\n{raw[:1200]}", file=sys.stderr)
        return 1

    report = scrub_report(report)

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
