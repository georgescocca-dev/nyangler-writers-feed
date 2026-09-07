#!/usr/bin/env python3
"""
Zone-writer column generator (GitHub archive copy).

Produces a single, dated fishing report in a writer's voice by synthesizing:
  • Persona from writers_roster.json (voice, mood, beat, system prompt)
  • Optional stale local forum jsonl (not source of truth; do not scrape nyangler.com)
  • Fresh-from-the-Fleet harvest (Supabase public.fishing_reports, when configured)
  • Latest SST package (water temps, dates)
  • Optional environmental signals (moon phase, wind, tide-window)

Writes archive files in this repo (not a live reports.nyangler.com delivery):
  • <out_dir>/reports/<id>.json  (zone-writer column; reports.json is an archive index)
  • <out_dir>/reports/<id>.md    (human-readable preview)

Live fishing reports live in Supabase public.fishing_reports. As of 2026-08-27,
nyangler.com / reports.nyangler.com get no new fishing reports from this repo.

Usage:
  python3 scripts/generate_writer_report.py mariana-reyes [--dry-run] [--intel-only]
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
import fleet_harvest  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
HERMES_HOME = Path(
    os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))
).expanduser()
WORKSPACE = Path(
    os.environ.get("NOREASTER_WORKSPACE", str(HERMES_HOME / "workspace"))
).expanduser()
# The active roster and intel live in the canonical Nor'easter tree.  The
# retired projects/ mirror drifts from the multi-state roster.
NOREASTER = Path(
    os.environ.get(
        "NOREASTER_INTEL_DIR",
        str(WORKSPACE / "noreaster" / "intel"),
    )
).expanduser()
SRC_ROSTER = NOREASTER / "config" / "writers_roster.json"
SRC_REPORTS = NOREASTER / "data" / "raw" / "nyangler"
FEED_REPO = Path(
    os.environ.get(
        "NOREASTER_FEED_REPO",
        str(Path(__file__).resolve().parent.parent),
    )
).expanduser()
REPORTS_DIR = FEED_REPO / "reports"
REPORTS_INDEX = FEED_REPO / "reports.json"
TEASERS_INDEX = FEED_REPO / "teasers.json"

# Per-writer voice profiles — gives each writer a distinct opening style,
# headline approach, and voice directives to prevent all reports from
# sounding the same.
VOICE_PROFILES_FILE = FEED_REPO / "scripts" / "writer_voice_profiles.json"
FIELD_GUIDE_DIR = NOREASTER / "data" / "regional_memory" / "field_guides"


def load_voice_profile(writer_id: str) -> dict | None:
    """Load the voice profile for a specific writer, or None if not found."""
    if not VOICE_PROFILES_FILE.exists():
        return None
    try:
        data = json.loads(VOICE_PROFILES_FILE.read_text(encoding="utf-8"))
        return data.get("writers", {}).get(writer_id)
    except (json.JSONDecodeError, OSError):
        return None


def load_regional_field_guide(writer_id: str, *, as_of: str | None = None) -> dict:
    """Load only current, verified facts from an editor's durable field guide."""
    guide_path = FIELD_GUIDE_DIR / f"{writer_id}.json"
    if not guide_path.exists():
        return {}
    try:
        guide = json.loads(guide_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    as_of = as_of or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    facts = [
        fact
        for fact in guide.get("facts", [])
        if fact.get("status") == "verified"
        and (not fact.get("expires_at") or fact["expires_at"] > as_of)
    ]
    guide["facts"] = facts
    guide["fact_count"] = len(facts)
    return guide

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
    # MA expansion zones
    "cape-cod-canal": "cape-cod-canal",
    "boston-harbor-north-shore": "boston-harbor-north-shore",
    "cape-cod-bay": "cape-cod-bay",
    "buzzards-bay-vineyard": "buzzards-bay-vineyard",
    "nantucket-sound": "nantucket-sound",
    "ma-offshore-stellwagen": "ma-offshore-stellwagen",
    # RI/NH/ME expansion zones
    "narragansett-bay": "narragansett-bay",
    "point-judith-block-island": "point-judith-block-island",
    "ri-south-shore": "ri-south-shore",
    "nh-coast": "nh-coast",
    "nh-offshore": "nh-offshore",
    "southern-maine": "southern-maine",
    "casco-bay": "casco-bay",
    "midcoast-maine": "midcoast-maine",
    # CT + NJ expansion zones (each has its own bucket)
    "western-ct-sound": "western-ct-sound",
    "central-ct-sound": "central-ct-sound",
    "lower-ct-river": "lower-ct-river",
    "eastern-ct-sound": "eastern-ct-sound",
    "thames-river-new-london": "thames-river-new-london",
    "fishers-island-sound-stonington": "fishers-island-sound-stonington",
    "ct-offshore": "ct-offshore",
    "raritan-bay-sandy-hook": "raritan-bay-sandy-hook",
    "northern-nj-shore": "northern-nj-shore",
    "barnegat-bay": "barnegat-bay",
    "long-beach-island": "long-beach-island",
    "south-jersey-shore": "south-jersey-shore",
    "cape-may-delaware-bay": "cape-may-delaware-bay",
    "nj-offshore": "nj-offshore",
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


def load_recent_reports(zone_slug: str, writer_id: str | None = None, limit: int = 30) -> list[dict]:
    """Load optional stale local forum jsonl for a zone.

    This is leftover local archive, not the live product and not a reason to
    scrape nyangler.com. Fleet harvest from public.fishing_reports is the
    live table; jsonl is fallback background only.

    If writer_id is provided, only returns reports dated AFTER the writer's
    most recent generated report — so we only feed new intel each cycle.
    Falls back to last `limit` reports if no prior report exists.
    """
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

    # If we have a writer_id, find their most recent report and only keep
    # forum posts dated after that report.
    if writer_id:
        cutoff_date = None
        writer_reports = sorted(
            REPORTS_DIR.glob(f"*{writer_id}*.json"), reverse=True
        )
        if writer_reports:
            fname = writer_reports[0].name
            if fname.startswith("20"):
                try:
                    last_date_str = fname[:10]  # e.g. "2026-06-24"
                    cutoff_date = last_date_str
                except (ValueError, IndexError):
                    pass

        if cutoff_date:
            reports = [r for r in reports if r.get("date", "") > cutoff_date]

    reports.sort(key=lambda r: r.get("date", ""), reverse=True)
    return reports[:limit]


ANALYST_DIR = NOREASTER / "data" / "analysis"
YOUTUBE_WEEKLY_DIR = NOREASTER / "data" / "knowledge" / "youtube_weekly_reports"
YOUTUBE_KNOWLEDGE = NOREASTER / "data" / "knowledge" / "fishing_knowledge.jsonl"


# ---------------------------------------------------------------------------
# Hooper intel briefing (LLM synthesis: zone/species analysis + predictions)
# ---------------------------------------------------------------------------
# Hooper's daily briefing is the richest intel we produce — regional reads,
# per-zone analysis, species breakdowns, and a predictive_outlook with dated
# calls ("white marlin within 1-2 weeks", "bonito by late July"). The writers
# consume it in two ways:
#   1. Today's briefing -> zone + species context for their beat.
#   2. The last few briefings -> "we called it" candidates. When a prior
#      prediction has visibly come true in today's intel, the writer works the
#      confirmation into the report. Cheap credibility — remind readers we
#      know this water.
HOOPER_SETUP_DIR = Path(
    os.environ.get(
        "NOREASTER_SETUP_ANALYSIS_DIR",
        str(
            Path.home()
            / "nor-easter-setup"
            / "projects"
            / "noreaster-intel"
            / "data"
            / "analysis"
        ),
    )
).expanduser()


def _hooper_score(path: Path) -> int:
    """Score a hooper file by usefulness. More zones = better; the newer
    46-zone format (species_analysis as list) beats the older dict format."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return -1
    za = data.get("zone_analysis", {})
    zone_count = len(za) if isinstance(za, dict) else 0
    newer_format = 1 if isinstance(data.get("species_analysis"), list) else 0
    return zone_count * 10 + newer_format


def _hooper_files() -> list[Path]:
    """Best hooper_*.json per date across all intel trees, newest first.

    The intel trees drift (see P24): the same date can hold DIFFERENT briefings
    in different trees. We score every candidate and keep the richest per date
    so writers always get the most complete briefing available."""
    by_date: dict[str, Path] = {}
    best_score: dict[str, int] = {}
    candidates = list(ANALYST_DIR.glob("hooper_*.json")) + list(
        HOOPER_SETUP_DIR.glob("hooper_*.json")
    )
    for f in candidates:
        date = f.stem.replace("hooper_", "")[:10]
        s = _hooper_score(f)
        if date not in by_date or s > best_score[date]:
            by_date[date] = f
            best_score[date] = s
    return [by_date[d] for d in sorted(by_date, reverse=True)]


def _species_entries(species_analysis) -> list[tuple[str, str]]:
    """Normalize species_analysis to [(label, text)] regardless of format.

    Two formats exist: dict {"striped_bass": "text..."} (older 18-zone Hooper)
    and list [{"species": "Fluke", "status": "...", "details": "..."}] (newer
    46-zone Hooper — richer, preferred)."""
    out: list[tuple[str, str]] = []
    if isinstance(species_analysis, dict):
        for k, v in species_analysis.items():
            out.append((str(k), str(v)))
    elif isinstance(species_analysis, list):
        for item in species_analysis:
            if not isinstance(item, dict):
                continue
            label = item.get("species") or item.get("name") or ""
            text = item.get("details") or item.get("analysis") or item.get("summary") or ""
            status = item.get("status")
            if status and text:
                text = f"[{status}] {text}"
            elif status and not text:
                text = str(status)
            if label:
                out.append((str(label), str(text)))
    return out


def load_hooper_briefing(
    zone_slug: str,
    beat_species: list[str],
    include_species_analysis: bool = True,
) -> dict:
    """Load the LATEST Hooper briefing, filtered to this writer's beat.

    Returns today's zone_analysis entry, the species_analysis entries matching
    their beat species, and the predictive_outlook. Empty dict if none."""
    files = _hooper_files()
    if not files:
        return {}
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    out: dict = {"briefing_date": data.get("date") or files[0].stem.replace("hooper_", "")}
    structure_evidence = data.get("offshore_structure_evidence", [])
    if isinstance(structure_evidence, list):
        route_index = {
            str(item.get("location_name", "")).strip(): str(item.get("report_zone", ""))
            for item in structure_evidence
            if isinstance(item, dict)
            and str(item.get("location_name", "")).strip()
            and str(item.get("report_zone", "")).strip()
        }
        if route_index:
            # Kept out of the writer prompt; used only by deterministic acceptance.
            out["offshore_structure_route_index"] = route_index
    zone_analysis = data.get("zone_analysis", {}) or {}
    if isinstance(zone_analysis, dict) and zone_slug in zone_analysis:
        out["zone_analysis"] = zone_analysis[zone_slug]
        evidence = data.get("named_lead_evidence", {})
        zone_text = str(zone_analysis[zone_slug]).lower()
        if isinstance(evidence, dict):
            zone_evidence = {}
            for name, records in evidence.items():
                if str(name).lower() not in zone_text or not isinstance(records, list):
                    continue
                matching_records = [
                    record
                    for record in records
                    if isinstance(record, dict)
                    and record.get("preferred_report_zone") == zone_slug
                ]
                if matching_records:
                    zone_evidence[name] = matching_records
            if zone_evidence:
                out["named_lead_evidence"] = zone_evidence
        if isinstance(structure_evidence, list):
            matching_structures = [
                item
                for item in structure_evidence
                if isinstance(item, dict)
                and item.get("report_zone") == zone_slug
                and str(item.get("location_name", "")).lower() in zone_text
            ]
            if matching_structures:
                out["offshore_structure_evidence"] = matching_structures

    if include_species_analysis:
        beat_norm = {_norm_species(s) for s in beat_species}
        matched = {}
        for label, text in _species_entries(data.get("species_analysis")):
            nl = _norm_species(label)
            if nl in beat_norm or any(nl in b or b in nl for b in beat_norm if b):
                matched[label] = text
        if matched:
            out["species_analysis"] = matched

    pred = data.get("predictive_outlook")
    if pred:
        out["predictive_outlook"] = pred
    return out


def _norm_species(s: str) -> str:
    """Normalize a species label for fuzzy matching (striped bass == striped_bass)."""
    return re.sub(r"[_\-\s]+", " ", str(s).lower()).strip()


# Prediction-signal phrases. If a PRIOR briefing used this language about a
# zone/species and TODAY's intel shows it happening, that's a "called it."
_PREDICTION_SIGNALS = re.compile(
    r"\b(expect|should start|should see|will|building|watch for|by late|"
    r"next (week|few|couple)|coming week|within|keep an eye|set to|"
    r"about to|poised to|on the (verge|cusp))\b",
    re.IGNORECASE,
)


def load_hooper_called_it(
    zone_slug: str,
    beat_species: list[str],
    lookback: int = 3,
    include_species_analysis: bool = True,
) -> list[dict]:
    """Scan PRIOR Hooper briefings for dated predictions about this beat.

    Returns up to `lookback` candidates (newest first): {date, text, about}.
    The writer (via the prompt) decides whether today's intel confirms it; we
    do NOT auto-assert a confirmation — that would risk claiming credit for a
    call that didn't pan out."""
    files = _hooper_files()[1:]  # skip today — we want PRIOR calls
    beat_norm = {_norm_species(s) for s in beat_species}
    candidates: list[dict] = []

    for f in files[:lookback]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        date = data.get("date") or f.stem.replace("hooper_", "")

        texts: list[tuple[str, str]] = []  # (about, text)
        zone_analysis = data.get("zone_analysis", {}) or {}
        if isinstance(zone_analysis, dict) and zone_slug in zone_analysis:
            texts.append((f"your zone ({zone_slug})", zone_analysis[zone_slug]))
        if include_species_analysis:
            for label, sp_text in _species_entries(data.get("species_analysis")):
                if _norm_species(label) in beat_norm:
                    texts.append((_norm_species(label), sp_text))

        for about, text in texts:
            for sent in re.split(r"(?<=[.!?])\s+", str(text)):
                sent = sent.strip()
                if 20 < len(sent) < 320 and _PREDICTION_SIGNALS.search(sent):
                    candidates.append({"date": date, "about": about, "text": sent})

    seen: set[str] = set()
    out: list[dict] = []
    for c in candidates:
        key = c["text"][:60].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
        if len(out) >= 3:
            break
    return out


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
You are the assigned beat reporter for this zone on reports.nyangler.com.
You are a fisherman who knows the block, not a character who went fishing
this week. Write as the person at the dock, in the tackle shop, or on the
radio — the one who already talked to the fleet and can tell readers what
happened and what the next few days look like.

Open with analysis: what the week did, why, and what is setting up. Weather,
tides, bait, wind, water color, moon, and where the bite is moving all
belong in that rundown. Do not open with a trip you took.

HARD RULES (break these and we pull the column):

1. NO FIRST-PERSON FISHING. Never write as if you were on the boat, in
   the surf, or on a drift this week. Banned: "I was out," "I fished,"
   "I drifted," "I dropped a jig," "I had two rods bent," "we left the
   dock," "I landed," "I hooked," "my rod." Dock talk, shop talk, and
   radio chatter are allowed ("a mate at Captree said," "the fleet is
   working the east wall"). Advice is allowed ("if you only have Sunday,
   start the flood at the inlet").

2. NO FORUM NAMES. No usernames, no "according to X." The intel you get
   from forum posts is background — you synthesize it into your own voice.
   Never reveal the source.

3. NO DATA-GAP COMPLAINTS. If a buoy is offline or SST is stale, work
   around it. Never say "buoys went dark" or "data is stale" or
   "running blind." Use whatever conditions you have. Skip what you don't.

4. NO INVENTED CATCHES. Only reference species in your beat profile and
   landmarks in your zone. Don't fabricate water temps — if you have a
   number, use it; if you don't, describe conditions qualitatively (warm,
   cold, dirty, clean, ripping, slack).

5. NEVER say "phish." Always "fish."

6. NO UNVERIFIED LOCAL BUSINESS FACTS. Do not invent bait-shop hours,
   breakfast stops, fuel docks, or parking rules. If the verified field
   guide does not contain the fact, omit it.

7. FLEET HARVEST IS BACKGROUND INTEL, NOT YOUR CATCH AND NOT YOUR
   COLUMN. Boat reports in the intel blob (often Facebook dock posts)
   are what the fleet said this week. Synthesize them into your own
   analysis. Do not cite Facebook URLs. Do not paste the post. Do not
   write as that captain or boat. You may say the fleet or a named
   boat out of a port reported X only when that fact is in the intel.
   Do not invent species or places that are not in the intel.

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

ANTI-SAMENESS RULES — CRITICAL. You are one of 45+ writers. If your
report sounds like the others, it fails. These rules are here to
force your report to sound like YOU and nobody else:

A. NEVER open with "Last week the [water body] did what it does in
   [month]..." or any variation. This is the #1 most overused opening
   on this site. Do not use it. Do not use "Last week the bay/sound/
   river/canyon did..." as your first sentence. Find YOUR opening.

B. NEVER open with "We're rolling into a new moon..." or "We're heading
   into a new moon..." — five other writers already used that opening
   this week. Find your own way in.

C. NEVER use "lights up" or "lights up the [spot]" in a headline. It
   has been used to death. Find a different verb.

D. NEVER use "stack the [structure]" in a headline (e.g. "yellowfin
   stack the 100-fathom line"). Find a different image.

E. NEVER use "New moon springs" as a headline prefix. It appears in
   20+ headlines. Just don't.

F. OPEN WITH THE WEEK, NOT A TRIP. First paragraph is the rundown:
   what happened, what the water is doing, and what the next few days
   look like. Personality still matters — you can open with dock talk,
   a number, bait, birds, or a seasonal marker — but not with you
   leaving the slip. Your voice profile may color the opening. It may
   not override this reporter rule.

G. HEADLINE DIVERSITY: Your headline should be specific and punchy but
   must NOT follow the formula "[Conditions] [verb] [species] at
   [spot]." Mix it up. Use a quote, a number, a question, an
   observation, a single vivid image. Your voice profile tells you
   your headline style. Follow it.

H. CONDITIONS STILL MATTER — but weave them INTO the report, not
   necessarily as the first 200 words. Mention tides, wind, water
   temps, moon phase where they're relevant to what's being caught
   and why. Don't front-load them unless your voice profile says to.

HONESTY — THIS IS CRITICAL:

Fishing is not always good. Real anglers get skunked. Real anglers
have tough sessions, tough weeks, tough conditions. Your readers are
anglers — they know when they're being sold a bill of goods.

- If the bite is genuinely on, say so — but don't oversell it. "Solid
  bass action" is fine. "The greatest fishing of all time" is not.
- If the bite is mixed or spotty, SAY SO. "The bass are here but scattered
   — the productive window is first light and the last of the ebb" is a
   better report than pretending it was easy.
- If the bite is slow, SAY SO. "Tough week on the Sound. The wind's been
   out of the southwest for five days straight, churned the water into
   milo, and the bass have lockjaw. Here's the better window..." is a
   real report. Anglers respect honesty. They don't respect cheerleading.
- Never give the impression that a reader is guaranteed to catch fish.
  "Worth a shot if you can get out before the front pushes through" is
  honest. "Get out there NOW, it's going off!" is not.
- A report that says "the fishing was slow but here's what I learned"
  is more valuable than one that says "epic bite, get out there!"

PREDICTIONS AND THE LOOK-AHEAD — HOW TO FRAME UNCERTAINTY:

You're a reporter who fishes, not a psychic and not a trip log.
Predictions should sound like an educated call — confident in the
reasoning, honest about the uncertainty, without ever saying
"nothing is guaranteed" or "no guarantees" or any variation of that
disclaimer. Anglers know fishing is unpredictable. They don't need
you to remind them. They need the conditions and where the time
is worth spending.

Frame predictions through one of these natural angler approaches:

- CONDITIONAL: "IF the wind lays down Saturday night, the drift at
  the lighthouse should set up perfect for the early flood. That's
  where I'd start." The IF does the hedging for you.

- ODDS-BASED: "The fleet connected on two of three weather windows
  this week. Those are good numbers for late June, but the tide
  still has to line up."

- SCENARIO: "Best case, the eddy holds through Tuesday and the tuna
  stay stacked on the 100-fathom line. If it slides east like it did
  last month, I'd shift to the flats."

- EXPERIENCE-BASED: "I've seen this pattern before — neap tides in
  late June, the bass slide off the rips and settle into the deeper
  eddies. Doesn't mean it's guaranteed, but that's where I'd put my
  time."

- WHAT TO DO: "If there is only one day this weekend, the Sunday
  morning flood at the inlet is the window. The moon's right, the
  tide's right, and there are enough fish around to make the trip.
  Keep a Plan B."

NEVER use these phrases — they're the verbal equivalent of a
disclaimer and they kill credibility:

- "nothing is guaranteed" / "no guarantees"
- "as always, fishing is unpredictable"
- "but that's fishing" / "that's why they call it fishing"
- "results may vary"
- "I can't promise anything"

Instead, let the conditions do the hedging. Name what needs to
happen for the prediction to play out.

WHAT MAKES A GREAT REPORT:

• CATCHES AND TACTICS — get into what's being caught: species, sizes,
  where, on what (specific baits, lures, rigs, presentations), at what
  depth, on which tide. "Fluke to 6 pounds on white Gulp and
  chartreuse bucktails in 30 feet off the Robert Moses bridge, outgoing
  water" — that's what readers want.

• BE SPECIFIC ABOUT BAIT AND TECHNIQUE. Don't say "soft plastics are
  working." Say "5-inch white Gulp Swimming Mullets on 3/4-oz
  bucktails, dragged slow on the drift." Don't say "live bait." Say
  "peanut bunker on a fishfinder rig, fished tight to the pilings on
  the outgoing."

• EXPLAIN THE WHY. You're not just a reporter — you understand fish
  behavior. Why are the bass hitting here and not there? Connect the
  dots between conditions, bait, and fish.

• YOUR VOICE, YOUR PERSONALITY. You have a distinct way of talking.
  Use it. The opening is still analysis, not a trip diary.

• THE LOOK-AHEAD. End with what the next few days look like and why.

STRUCTURE: Open with the week's analysis and the near-term outlook.
Then move into catches and tactics. Weave weather, tides, bait, and
structure through the column. Voice-profile color is allowed after
that reporter lead, not instead of it.

LENGTH: 800–1100 words. If your report is under 800 words, you are
leaving out analysis the reader needs.

OUTPUT FORMAT — return ONLY valid JSON with this exact schema:

{
  "headline": "string, max 90 chars. Punchy, specific, says what happened. Do NOT use 'lights up' or 'stack the' or 'new moon springs' — these are banned. Find your own verb and image.",
  "subhead": "string, one sentence, max 160 chars. The hook that makes you read the whole thing.",
  "dateline": "string, e.g. 'CAPTREE, NY — June 12'",
  "body_markdown": "string. 800-1100 words. Open with the week's analysis and the next few days. Then catches and tactics. Weather, tides, bait, and structure belong in the rundown. NO first-person fishing trip. NO H2 or H3 headings, NO bullet lists, NO blockquotes. Paragraphs only.",
  "tags": ["3–6 lowercase hyphen-tags: species, technique, location focused. e.g. fluke, bucktail, captree-drift, outgoing-tide, bunker"],
  "offshore_locations_used": ["Every named offshore wreck, lump, ledge, bank, shoal, hole, tower, canyon, or local spot mentioned anywhere in this report. Use exact supplied wording. Empty array when none."]
}
"""

OFFSHORE_SCOPE_DIRECTIVE = """
OFFSHORE COVERAGE CONTRACT:
- Your offshore beat begins 10 nautical miles from shore and continues outward; it is not limited to the canyon edge.
- Retain evidence-backed tuna bites on wrecks, lumps, ledges, banks, shoals, and canyons. A 10-12-mile tuna bite belongs in scope.
- Fold each qualifying signal into the closest applicable existing area report instead of isolating all tuna coverage under a canyon label. Use the exact nearby structure and plain language: for example, "tuna are inside at the Virginia Wreck" or San Diego when supplied evidence supports that statement.
- A supplied, verified nearby structure is part of your offshore beat even if it is not listed in your static landmarks. This exception applies only to evidence-backed offshore signals routed by Hooper; it never authorizes an invented place or catch.
- Priority New York and New York Bight leads are Virginia Wreck, San Diego, Bacardi, the Mud Hole, Texas Tower, and the Tails. Use one only when supplied evidence verifies the structure name or alias, position, report date, tuna species, and source.
- Never invent coordinates, catches, or a tuna report to fill the expanded scope. If the evidence has no qualifying signal, leave it out.
- List every named offshore place used in `offshore_locations_used`. Omitting the list or a used place fails publication; listing a place without routed source evidence also fails publication.
"""

OFFSHORE_NAMED_LEADS = (
    "Virginia Wreck",
    "San Diego",
    "Bacardi",
    "Mud Hole",
    "Texas Tower",
    "The Tails",
)

OFFSHORE_STRUCTURE_NAME_RE = re.compile(
    r"\b((?:(?:The|the)\s+)?(?:[A-Z][A-Za-z0-9'’.-]*\s+){1,4}"
    r"(?:Wreck|Lump|Ledge|Bank|Shoal|Hole|Tower|Canyon))\b"
)


def extract_named_offshore_structures(text: str) -> list[str]:
    return list(
        dict.fromkeys(
            match.group(1).strip() for match in OFFSHORE_STRUCTURE_NAME_RE.finditer(text)
        )
    )


def load_latest_analyst(writer_id: str | None = None) -> dict:
    """Load analyst buoy/tide data covering the full period since the last published report.

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
    """Extract the parts of the analyst buoy/tide output most relevant to this writer's beat.

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


def build_prompt(writer: dict, reports: list[dict], analyst: dict, youtube_intel: list[dict] = None, hooper: dict = None, called_it: list[dict] = None, fleet_harvest: list[dict] = None) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt)."""
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    hooper = hooper or {}
    named_lead_evidence = hooper.get("named_lead_evidence", {})
    if not isinstance(named_lead_evidence, dict):
        named_lead_evidence = {}
    unsupported_named_leads = {
        name
        for name in OFFSHORE_NAMED_LEADS
        if not any(
            isinstance(item, dict) and item.get("date") and item.get("source")
            for item in named_lead_evidence.get(name, [])
        )
    }

    def redact_unsupported_named_leads(value: object) -> object:
        if isinstance(value, str):
            result = value
            for name in unsupported_named_leads:
                result = re.sub(re.escape(name), "", result, flags=re.IGNORECASE)
            result = re.sub(r"\s*/\s*(?=$|[,;)])", "", result)
            return re.sub(r" {2,}", " ", result).strip(" /,;-\t")
        if isinstance(value, list):
            return [
                cleaned
                for item in value
                if (cleaned := redact_unsupported_named_leads(item))
            ]
        return value

    beat = {
        "id": writer["id"],
        "name": writer["name"],
        "role": writer["role"],
        "zone": redact_unsupported_named_leads(writer.get("zone_name")),
        "area": writer.get("area"),
        "beat_species": writer.get("beat_species", []),
        "landmarks": redact_unsupported_named_leads(writer.get("landmarks", [])),
        "voice": writer.get("voice"),
        "mood": writer.get("mood"),
        "style_tags": writer.get("style_tags", []),
    }
    background_reports = [shorten_report(r) for r in reports]

    # Hooper's briefing: today's zone/species synthesis (background — never
    # cited by name) plus the predictive outlook.
    hooper_context = {}
    if hooper.get("zone_analysis"):
        hooper_context["zone_read"] = hooper["zone_analysis"]
    if hooper.get("species_analysis"):
        hooper_context["species_read"] = hooper["species_analysis"]
    if hooper.get("predictive_outlook"):
        hooper_context["predictive_outlook"] = hooper["predictive_outlook"]
    if hooper.get("named_lead_evidence"):
        hooper_context["named_lead_evidence"] = hooper["named_lead_evidence"]
    if hooper.get("offshore_structure_evidence"):
        hooper_context["offshore_structure_evidence"] = hooper["offshore_structure_evidence"]

    # "We called it" candidates — prior Hooper predictions about this beat.
    called_it = called_it or []
    called_it_block = ""
    if called_it:
        lines = [
            "",
            "WE CALLED IT — credibility opportunity (use ONLY if today's intel confirms it):",
            "Our own recent analysis made these calls about your beat:",
        ]
        for c in called_it:
            lines.append(f"  • On {c['date']} ({c['about']}): \"{c['text']}\"")
        lines += [
            "If what's happening NOW in your zone confirms one of these calls,",
            "say so — naturally, in YOUR voice. One short line is plenty:",
            "\"We flagged this last week — said the [X] would [Y], and here it is.\"",
            "Do NOT force it. Do NOT claim a confirmation that isn't in your intel.",
            "Do NOT mention Hooper, 'the analyst', or 'our analysis' by name —",
            "it is simply what WE saw coming. If nothing confirmed, skip entirely.",
        ]
        called_it_block = "\n".join(lines)

    # Load per-writer voice profile and build the voice directive block
    voice_profile = load_voice_profile(writer["id"])
    voice_block = ""
    if voice_profile:
        voice_lines = [
            f"\nYOUR VOICE PROFILE (follow these directives — they make you unique):",
            f"  Opening style: {voice_profile.get('opening_style', 'your own choice')}",
            f"  How to open: {voice_profile.get('opening_directive', 'Open however feels right for you this week.')}",
            f"  Headline style: {voice_profile.get('headline_style', 'Punchy and specific.')}",
            f"  Voice focus: {voice_profile.get('voice_focus', 'Be yourself.')}",
        ]
        banned = voice_profile.get("banned_for_this_writer", [])
        if banned:
            voice_lines.append(f"  Phrases banned for YOU specifically: {', '.join(banned)}")
        voice_lines.append(
            "  These directives color YOUR voice. They do not authorize a first-person fishing trip."
        )
        voice_block = "\n".join(voice_lines)

    system = (
        writer.get("system_prompt", "")
        + "\n\n---\n"
        + EDITORIAL_RULES.strip()
    )
    if unsupported_named_leads and writer.get("domain") == "offshore":
        for name in unsupported_named_leads:
            system = re.sub(re.escape(name), "", system, flags=re.IGNORECASE)
        system += (
            "\n\nDo not introduce any named offshore lead that lacks a dated source "
            "record in the supplied Hooper evidence. Canonical landmarks in this "
            "writer's beat profile may be used as geographic context, but they do "
            "not prove a current catch on their own."
        )
    offshore_scope = writer.get("domain") == "offshore"
    if offshore_scope:
        system += "\n\n---\n" + OFFSHORE_SCOPE_DIRECTIVE.strip()
        permitted_locations = [
            str(item).strip()
            for item in beat.get("landmarks", [])
            if str(item).strip()
        ]
        zone_name = str(beat.get("zone", "")).strip()
        if zone_name:
            permitted_locations.append(zone_name.split("(", 1)[0].split("/", 1)[-1].strip())
            parenthetical = re.search(r"\(([^)]+)\)", zone_name)
            if parenthetical:
                for name in parenthetical.group(1).split(","):
                    clean_name = name.strip()
                    if clean_name:
                        permitted_locations.extend([clean_name, f"{clean_name} Canyon"])
        for item in hooper.get("offshore_structure_evidence", []) or []:
            if (
                isinstance(item, dict)
                and item.get("report_zone") == writer.get("id")
                and item.get("location_name")
            ):
                permitted_locations.append(str(item["location_name"]).strip())
        for name, records in named_lead_evidence.items():
            if any(
                isinstance(item, dict)
                and item.get("preferred_report_zone") == writer.get("id")
                and item.get("date")
                and item.get("source")
                for item in records
            ):
                permitted_locations.append(str(name).strip())
        permitted_locations = list(dict.fromkeys(filter(None, permitted_locations)))
        system += (
            "\n\nNAMED OFFSHORE LOCATION ALLOWLIST FOR THIS REPORT:\n- "
            + ("\n- ".join(permitted_locations) if permitted_locations else "No named location is currently allowed")
            + "\nDo not mention or disclose any other named offshore location. "
            "A familiar neighboring canyon or structure is still prohibited unless it appears above."
        )
    field_guide = load_regional_field_guide(writer["id"])
    system += (
        "\n\n---\nUse the verified regional field guide as factual local context. "
        "Do not invent missing local details. Never treat your own prior articles "
        "as evidence; article-derived leads require independent verification before "
        "they can appear in this field guide."
    )
    if voice_block:
        system += "\n\n---\n" + voice_block
    if unsupported_named_leads and offshore_scope:
        for name in unsupported_named_leads:
            system = re.sub(re.escape(name), "", system, flags=re.IGNORECASE)

    user = json.dumps(
        {
            "today": today,
            "beat_profile": beat,
            "offshore_coverage_scope": (
                {
                    "minimum_distance_from_shore_nm": 10,
                    "direction": "outward",
                    "structure_types": [
                        "wreck", "lump", "ledge", "bank", "shoal", "canyon"
                    ],
                }
                if offshore_scope
                else None
            ),
            "verified_regional_field_guide": field_guide,
            "primary_intel_buoy_tide_conditions": analyst,
            "hooper_synthesis_background_DO_NOT_CITE": hooper_context,
            "youtube_intel_DO_NOT_CITE": youtube_intel or [],
            "background_forum_chatter_DO_NOT_CITE": background_reports,
            "fleet_harvest_DO_NOT_CITE": fleet_harvest or [],
            "task": (
                "Write this week's fishing report for your zone as a beat "
                "reporter. Open with analysis of what happened and what the "
                "next few days look like — weather, tides, bait, wind, and "
                "where the bite is moving. Do not write a first-person trip. "
                "Dock talk and shop talk are allowed. Then move into what's "
                "being CAUGHT — species, sizes, tactics, baits, specific "
                "spots. Be honest about the bite quality — if it's slow or "
                "mixed, say so. Anglers respect honesty, not hype. Use the "
                "buoy/tide conditions and the Hooper synthesis to explain WHY "
                "conditions are producing. The forum chatter, YouTube intel, "
                "fleet harvest, and Hooper synthesis are BACKGROUND ONLY — "
                "synthesize them into your voice, never cite users, video "
                "channels, Hooper, Facebook URLs, or the buoy data source. "
                "Do not copy a fleet post as the finished column and do not "
                "write in a boat's voice."
                + called_it_block +
                " Write it like YOUR column — your voice, your personality, "
                "your way of reading the water — but as a reporter, not as "
                "someone who just got off the boat. Be specific on baits and "
                "rigs. Return ONLY the JSON object specified — no preamble, "
                "no markdown code fence."
            ),
        },
        indent=2,
        ensure_ascii=False,
    )
    return system, user


def retryable_http_status(status: int) -> bool:
    return status in {404, 408, 409, 425, 429} or status >= 500


def call_openrouter(system: str, user: str, model: str) -> str:
    """Call OpenRouter via `requests` (handles chunked reads better than urllib).

    Retries up to 5 times with exponential backoff on transient errors
    (timeouts, dropped connections, IncompleteRead, 5xx). 4xx errors raise
    immediately — they're deterministic.
    """
    import requests as _req

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
        "max_tokens": 8000,
        "response_format": {"type": "json_object"},
        "plugins": [{"id": "response-healing"}],
        "provider": {"require_parameters": True},
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://reports.nyangler.com",
        "X-Title": "reports.nyangler.com writer generator",
    }

    MAX_ATTEMPTS = 5
    last_err: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            resp = _req.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=(30, 180),  # (connect, read) — connect fast, read slow
            )
            resp.raise_for_status()
            data = resp.json()
            break
        except (
            _req.exceptions.Timeout,
            _req.exceptions.ConnectionError,
            _req.exceptions.ChunkedEncodingError,
        ) as e:
            # Transient: timeout, dropped connection, chunked stream died
            last_err = e
            if attempt < MAX_ATTEMPTS - 1:
                wait = 15 * (2 ** attempt)  # 15s, 30s, 60s, 120s
                print(
                    f"  [llm] read failed ({type(e).__name__}), "
                    f"retry {attempt+1}/{MAX_ATTEMPTS-1} in {wait}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
        except _req.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            # OpenRouter can return a transient 404 when no compatible provider
            # route is available at that instant. Retry routing/rate-limit/server
            # failures; other 4xx responses are deterministic.
            if retryable_http_status(status) and attempt < MAX_ATTEMPTS - 1:
                last_err = e
                wait = 15 * (2 ** attempt)
                print(
                    f"  [llm] retryable HTTP {status} from upstream, "
                    f"retry {attempt+1}/{MAX_ATTEMPTS-1} in {wait}s",
                    file=sys.stderr,
                )
                time.sleep(wait)
            else:
                raise
    else:
        raise SystemExit(f"LLM call failed after {MAX_ATTEMPTS} attempts: {last_err}")

    choice = data["choices"][0]
    content = choice["message"].get("content")
    if content is None:
        raise SystemExit(
            f"Model returned null content (finish_reason={choice.get('finish_reason')}). "
            "May need higher max_tokens or the model returned only reasoning."
        )
    return content


def escape_json_string_controls(raw: str) -> str:
    """Escape literal control characters only while inside JSON strings.

    Some otherwise-valid model responses contain real newlines or tabs inside
    `body_markdown` instead of the required JSON escape sequences. Repair that
    narrow defect without changing structural whitespace outside strings.
    """
    output: list[str] = []
    in_string = False
    escaped = False
    replacements = {
        "\b": "\\b",
        "\f": "\\f",
        "\n": "\\n",
        "\r": "\\r",
        "\t": "\\t",
    }
    for char in raw:
        if in_string:
            if escaped:
                output.append(char)
                escaped = False
                continue
            if char == "\\":
                output.append(char)
                escaped = True
                continue
            if char == '"':
                output.append(char)
                in_string = False
                continue
            if ord(char) < 0x20:
                output.append(replacements.get(char, f"\\u{ord(char):04x}"))
                continue
            output.append(char)
            continue

        output.append(char)
        if char == '"':
            in_string = True
    return "".join(output)


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
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        if "control character" not in exc.msg.lower():
            raise
        return json.loads(escape_json_string_controls(raw))


FIRST_PERSON_FISHING_RE = re.compile(
    r"\b(?:I was out|I fished|I drifted|I dropped|I landed|I hooked|"
    r"I had (?:two|a couple|three|four) rods? bent|we left the dock|"
    r"my rod|I wet a line|I ran the boat|I poled)\b",
    re.IGNORECASE,
)


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
    # Anti-sameness phrases — these make every writer sound identical
    "did what it does",
    "did what it always does",
    "did what overheated",
    "did what shallow water does",
    "did what bay water does",
    "did what it does every",
    "did what it usually does",
]


def normalize_tag(value: object) -> str:
    """Return a lowercase hyphen tag, or an empty string."""
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")


def scrub_report(report: dict, writer: dict | None = None) -> dict:
    """Post-process LLM output: strip banned phrases, ensure required fields."""
    body = report.get("body_markdown", "")

    # Fix double-escaped newlines: model sometimes emits literal "\n" (backslash-n
    # as two chars) instead of actual newlines inside the JSON string. json.loads
    # preserves them as literal text, which renders as visible "\n" on the sites.
    # Convert any run of escaped newline sequences into real newlines.
    body = re.sub(r"(?:\\n)+", "\n\n", body)
    # Same for escaped tabs, just in case
    body = body.replace("\\t", "  ")

    for phrase in BANNED_PHRASES:
        # Case-insensitive replace, preserving surrounding context
        pattern = re.compile(re.escape(phrase), re.IGNORECASE)
        body = pattern.sub("", body)
    # Clean up any double spaces left by removals
    body = re.sub(r"  +", " ", body)
    body = re.sub(r"\n +", "\n", body)
    # Collapse 3+ consecutive newlines to a standard paragraph break
    body = re.sub(r"\n{3,}", "\n\n", body)
    report["body_markdown"] = body.strip()

    # Models occasionally omit tags even when the JSON schema is otherwise
    # valid. Keep this deterministic and grounded in the assigned beat.
    raw_tags = report.get("tags")
    tags = raw_tags if isinstance(raw_tags, list) else []
    candidates = [
        *tags,
        *((writer or {}).get("beat_species", []) or []),
        (writer or {}).get("zone_slug"),
        (writer or {}).get("id"),
    ]
    normalized: list[str] = []
    for candidate in candidates:
        tag = normalize_tag(candidate)
        if tag and tag not in normalized:
            normalized.append(tag)
        if len(normalized) == 6:
            break
    report["tags"] = normalized

    return report


def report_quality_errors(
    report: dict,
    hooper: dict | None = None,
    writer: dict | None = None,
) -> list[str]:
    """Reject structurally valid JSON that is not publication-ready."""
    errors: list[str] = []
    headline = report.get("headline")
    subhead = report.get("subhead")
    body = report.get("body_markdown")
    tags = report.get("tags")
    if not isinstance(headline, str) or not 20 <= len(headline.strip()) <= 140:
        errors.append("headline length")
    if not isinstance(subhead, str) or len(subhead.strip()) < 40:
        errors.append("subhead too short")
    if not isinstance(body, str) or len(body.strip()) < 900:
        errors.append("body too short")
    if not isinstance(tags, list) or len(tags) < 3:
        errors.append("too few tags")
    full_text = " ".join(
        str(report.get(key, "")) for key in ("headline", "subhead", "body_markdown")
    )
    if FIRST_PERSON_FISHING_RE.search(full_text):
        errors.append("first-person fishing")
    full_text = full_text.lower()
    is_offshore_writer = (writer or {}).get("domain") == "offshore"
    evidence = (hooper or {}).get("named_lead_evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
    if is_offshore_writer:
        for name in OFFSHORE_NAMED_LEADS:
            if name.lower() not in full_text:
                continue
            records = evidence.get(name, [])
            has_source_evidence = (
                isinstance(records, list)
                and any(
                    isinstance(item, dict) and item.get("date") and item.get("source")
                    for item in records
                )
            )
            if not has_source_evidence:
                errors.append(f"unsupported named lead: {name}")
                continue
            writer_id = (writer or {}).get("id")
            if writer_id and not any(
                isinstance(item, dict)
                and item.get("preferred_report_zone") == writer_id
                for item in records
            ):
                errors.append(f"wrong-zone named lead: {name}")
    structure_evidence = (hooper or {}).get("offshore_structure_evidence", [])
    if not isinstance(structure_evidence, list):
        structure_evidence = []
    writer_id = (writer or {}).get("id")
    if is_offshore_writer:
        for item in structure_evidence:
            if not isinstance(item, dict):
                continue
            name = str(item.get("location_name", "")).strip()
            if not name or name.lower() not in full_text:
                continue
            has_source_evidence = bool(item.get("date") and item.get("source"))
            if not has_source_evidence:
                errors.append(f"unsupported offshore structure: {name}")
            elif writer_id and item.get("report_zone") != writer_id:
                errors.append(f"wrong-zone offshore structure: {name}")
    route_index = (hooper or {}).get("offshore_structure_route_index", {})
    if not isinstance(route_index, dict):
        route_index = {}
    else:
        route_index = dict(route_index)
    if is_offshore_writer and writer_id:
        # Standing writer landmarks are valid geographic context for that beat.
        # They are not current-catch evidence; priority leads still require the
        # dated Hooper evidence checked above.
        canonical_names = list((writer or {}).get("landmarks", []) or [])
        zone_name = str((writer or {}).get("zone_name", ""))
        if zone_name:
            canonical_names.append(zone_name.split("(", 1)[0].split("/", 1)[-1].strip())
            parenthetical = re.search(r"\(([^)]+)\)", zone_name)
            if parenthetical:
                for name in parenthetical.group(1).split(","):
                    clean_name = name.strip()
                    if clean_name:
                        canonical_names.extend([clean_name, f"{clean_name} Canyon"])
        named_leads_folded = {lead.casefold() for lead in OFFSHORE_NAMED_LEADS}
        for name in canonical_names:
            clean_name = str(name).strip()
            if clean_name and clean_name.casefold() not in named_leads_folded:
                route_index.setdefault(clean_name, writer_id)
    route_index_folded = {str(name).casefold(): (str(name), zone) for name, zone in route_index.items()}
    disclosed = report.get("offshore_locations_used")
    if is_offshore_writer and not isinstance(disclosed, list):
        errors.append("missing offshore location disclosure")
        disclosed = []
    elif not isinstance(disclosed, list):
        disclosed = []
    disclosed_folded = {str(name).strip().casefold() for name in disclosed if str(name).strip()}
    if is_offshore_writer:
        for name in disclosed:
            clean_name = str(name).strip()
            if not clean_name:
                continue
            route = route_index_folded.get(clean_name.casefold())
            if not route:
                errors.append(f"unsupported offshore structure: {clean_name}")
            elif writer_id and route[1] != writer_id:
                errors.append(f"wrong-zone offshore structure: {route[0]}")
        for folded_name, (name, zone) in route_index_folded.items():
            if folded_name in full_text and folded_name not in disclosed_folded:
                errors.append(f"undisclosed offshore structure: {name}")
            if folded_name in full_text and writer_id and zone != writer_id:
                marker = f"wrong-zone offshore structure: {name}"
                if marker not in errors:
                    errors.append(marker)
        for name in extract_named_offshore_structures(str(report.get("body_markdown", ""))):
            folded_name = name.casefold()
            if folded_name not in disclosed_folded:
                marker = f"undisclosed offshore structure: {name}"
                if marker not in errors:
                    errors.append(marker)
            route = route_index_folded.get(folded_name)
            if not route:
                marker = f"unsupported offshore structure: {name}"
                if marker not in errors:
                    errors.append(marker)
            elif writer_id and route[1] != writer_id:
                marker = f"wrong-zone offshore structure: {route[0]}"
                if marker not in errors:
                    errors.append(marker)
    return errors


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
    # A retry replaces the prior staged report for this writer/date.  Leaving
    # both files made Kent receive multiple conflicting reports for one zone.
    date_prefix = f"{today.strftime('%Y-%m-%d')}-{writer['id']}-"
    for stale in REPORTS_DIR.glob(f"{date_prefix}*"):
        stale.unlink()
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

    # Also write to the unified fishing intel database
    import hashlib as _hl
    UNIFIED_DB = NOREASTER / "data" / "fishing_intel.jsonl"
    uid = _hl.sha1(f"{today.isoformat()}{writer.get('id','')}{report.get('headline','')[:50]}".encode()).hexdigest()[:16]
    unified_entry = {
        "id": uid,
        "timestamp": today.isoformat(),
        "date": today.strftime("%Y-%m-%d"),
        "source_type": "writer_report",
        "zone": writer.get("zone_slug", ""),
        "region": writer.get("zone_name", ""),
        "species": [],  # extracted later by catch marker script
        "techniques": [],
        "conditions": [],
        "summary": report.get("headline", ""),
        "detail": report.get("body_markdown", ""),
        "lat": None,
        "lon": None,
        "bucket": writer.get("zone_slug", ""),
    }
    if os.environ.get("NOREASTER_WRITE_UNIFIED_DB", "1") != "0":
        with open(UNIFIED_DB, "a") as f:
            f.write(json.dumps(unified_entry, ensure_ascii=False) + "\n")

    return publication


def report_datetime() -> datetime:
    """Return the requested report date or the current UTC timestamp.

    The batch wrapper sets NOREASTER_REPORT_DATE for retries and recovery runs.
    Honor it here so a correction replaces that dated report instead of
    silently creating a new-day article.
    """
    requested = os.environ.get("NOREASTER_REPORT_DATE", "").strip()
    if not requested:
        return datetime.now(timezone.utc)
    try:
        parsed = datetime.strptime(requested, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("NOREASTER_REPORT_DATE must use YYYY-MM-DD") from exc
    return parsed.replace(tzinfo=timezone.utc)


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
    """Scan reports/*.json and rebuild the archive reports.json index.

    reports.json is a GitHub backup of zone-writer columns, not a live
    Noreaster feed and not what sites should fetch for fishing reports.
    """
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
def load_fleet_harvest(writer_id: str, limit: int = 30) -> list[dict]:
    """Load this week's ready fleet harvest for one writer. Never raises."""
    if fleet_harvest.credentials_missing_reason():
        fleet_harvest.log_credential_miss()
        return []
    try:
        return fleet_harvest.load_fleet_harvest_for_writer(writer_id, limit=limit)
    except Exception as exc:  # noqa: BLE001 — cron must not crash
        print(
            f"[warn] fleet harvest unavailable ({type(exc).__name__}: {exc}) "
            "— using jsonl forum intel",
            file=sys.stderr,
        )
        return []


def log_fleet_sample(rows: list[dict]) -> None:
    headlines = [
        str(row.get("headline") or row.get("text") or "")[:90]
        for row in rows[:5]
        if row.get("headline") or row.get("text")
    ]
    if headlines:
        print(f"[gen] fleet_sample={headlines}", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description="Generate a fishing report for a writer.")
    p.add_argument("writer_id", help="e.g. fire-island, jamaica-bay")
    p.add_argument("--model", default="anthropic/claude-sonnet-5",
                   help="OpenRouter model id (default: anthropic/claude-sonnet-5)")
    p.add_argument("--limit", type=int, default=30, help="Max source reports to include (default 30)")
    p.add_argument("--dry-run", action="store_true", help="Print the report; do not write files.")
    p.add_argument(
        "--intel-only",
        action="store_true",
        help="Assemble intel and print fleet headlines in the prompt blob; do not call the LLM.",
    )
    p.add_argument("--skip-index", action="store_true",
                   help="Skip reports.json/teasers rebuild; intended for batch generation.")
    args = p.parse_args()

    writer = load_writer(args.writer_id)
    zone_slug = WRITER_TO_ZONE_BUCKET.get(writer["id"]) or writer.get("zone_slug") or writer["id"]
    reports = load_recent_reports(zone_slug, writer_id=args.writer_id, limit=args.limit)
    fleet = load_fleet_harvest(writer["id"], limit=args.limit)
    raw_analyst = load_latest_analyst(writer_id=args.writer_id)
    analyst = filter_analyst_for_zone(raw_analyst, zone_slug, writer)
    youtube_intel = load_recent_youtube_intel(zone_slug, days=14)

    # Hooper: today's synthesis for this beat + prior "called it" candidates.
    # Use the writer's OWN id (not the forum zone bucket) — canyon captains map
    # to the generic "offshore" bucket for forum intel, but Hooper has dedicated
    # per-zone entries keyed by their real id.
    beat_species = writer.get("beat_species", []) or []
    hooper_key = writer["id"]
    include_species_analysis = writer.get("domain") != "offshore"
    hooper = load_hooper_briefing(
        hooper_key,
        beat_species,
        include_species_analysis=include_species_analysis,
    )
    called_it = load_hooper_called_it(
        hooper_key,
        beat_species,
        include_species_analysis=include_species_analysis,
    )

    print(
        f"[gen] writer={writer['name']} zone={zone_slug} "
        f"forum_bg={len(reports)} "
        f"fleet_harvest={len(fleet)} "
        f"analyst={'yes' if analyst else 'NO'} "
        f"analyst_runs={len(raw_analyst.get('prior_analyst_runs_since_last_report', [])) + 1 if raw_analyst else 0} "
        f"regional_match={len(analyst.get('regional_outlook_for_beat', []))} "
        f"hooper={'yes' if hooper else 'NO'} "
        f"called_it={len(called_it)} "
        f"youtube_intel={len(youtube_intel)}",
        file=sys.stderr,
    )
    log_fleet_sample(fleet)
    if not analyst:
        print(
            "[warn] no analyst buoy/tide data available — report will be thinner than ideal",
            file=sys.stderr,
        )

    system, user = build_prompt(
        writer,
        reports,
        analyst,
        youtube_intel,
        hooper=hooper,
        called_it=called_it,
        fleet_harvest=fleet,
    )
    if args.intel_only:
        payload = json.loads(user)
        fleet_blob = payload.get("fleet_harvest_DO_NOT_CITE") or []
        summary = {
            "writer_id": writer["id"],
            "zone_slug": zone_slug,
            "forum_bg": len(reports),
            "fleet_harvest": len(fleet_blob),
            "fleet_headlines": [
                row.get("headline") or row.get("text")
                for row in fleet_blob
                if row.get("headline") or row.get("text")
            ],
            "prompt_contains_fleet": bool(fleet_blob)
            and "fleet_harvest_DO_NOT_CITE" in user,
            "facebook_urls_in_prompt": bool(
                fleet_harvest.FACEBOOK_URL_RE.search(user)
            ),
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        return 0

    print(f"[gen] calling {args.model} (prompt={len(system)+len(user):,} chars)", file=sys.stderr)

    # Retry the LLM call up to 3 times on invalid or incomplete output.
    report: dict | None = None
    for json_attempt in range(3):
        raw = call_openrouter(system, user, args.model)
        try:
            candidate = scrub_report(parse_llm_json(raw), writer)
        except json.JSONDecodeError as e:
            print(f"[error] model returned non-JSON (attempt {json_attempt+1}/3): {e}\n---\n{raw[:400]}", file=sys.stderr)
        else:
            quality_errors = report_quality_errors(
                candidate,
                hooper=hooper,
                writer=writer,
            )
            if not quality_errors:
                report = candidate
                break
            print(
                f"[error] model output failed quality gate "
                f"(attempt {json_attempt+1}/3): {', '.join(quality_errors)}",
                file=sys.stderr,
            )
        if json_attempt < 2:
            time.sleep(5)

    if report is None:
        return 1

    if args.dry_run:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    today = report_datetime()
    pub = emit_report(writer, report, today)
    try:
        fleet_harvest.upsert_zone_writer_fact(
            writer, report, today.strftime("%Y-%m-%d")
        )
    except Exception as exc:  # noqa: BLE001 — archive fact is optional
        print(
            f"[warn] zone-writer fact upsert skipped "
            f"({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
    index = None if args.skip_index else rebuild_reports_index()
    print(f"[gen] wrote {pub['id']}", file=sys.stderr)
    if index:
        print(f"[gen] index now has {index['total_reports']} reports", file=sys.stderr)
    print(json.dumps({"id": pub["id"], "headline": report["headline"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
