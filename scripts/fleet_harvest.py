#!/usr/bin/env python3
"""Load Fresh-from-the-Fleet harvest (public.fishing_reports) as writer intel.

This is source material for the zone-writer prompt — not a second reports.json
feed. Facebook URLs stay out of the prompt blob. Rows that cannot be mapped
onto a single writer beat are skipped rather than broadcast.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

DEFAULT_SUPABASE_URL = "https://bcdlbzyvbpolxdpdthls.supabase.co"
FLEET_TABLE = "fishing_reports"
FLEET_WINDOW_DAYS = 7
FLEET_FETCH_LIMIT = 200
READY_STATUS = "ready"
SKIP_STATUSES = frozenset(
    {
        "held",
        "hold",
        "stale",
        "stale-for-reporters",
        "stale_for_reporters",
        "draft",
        "pending",
        "rejected",
    }
)
# Regional desks / buckets that would otherwise fan out to many writers.
BROADCAST_SLUGS = frozenset(
    {
        "offshore",
        "inshore",
        "all",
        "all-zones",
        "northeast",
        "mid-atlantic",
        "new-england",
        "unknown",
        "unclassified",
        "other",
        "general",
        "fleet",
        "facebook",
        "south-shore",
        "north-shore",
        "the-sound",
        "long-island",
        "new-jersey",
        "new-york",
    }
)
FACEBOOK_URL_RE = re.compile(r"https?://(?:www\.)?(?:facebook|fb)\.com\S*", re.IGNORECASE)
PHOTO_KEYS = frozenset(
    {
        "photo",
        "photos",
        "image",
        "images",
        "image_url",
        "photo_url",
        "thumbnail",
        "media",
        "attachments",
        "media_url",
        "picture",
        "pictures",
    }
)

# Writer ids / zone slugs that the generator already knows. Area values that
# match these (or the aliases below) are the primary wiring.
KNOWN_WRITER_IDS = frozenset(
    {
        "fire-island",
        "jamaica-bay",
        "western-sound",
        "central-sound",
        "eastern-sound",
        "jones-inlet",
        "moriches",
        "shinnecock",
        "montauk",
        "peconic",
        "block-island",
        "nj-shore",
        "cape-cod-canal",
        "boston-harbor-north-shore",
        "cape-cod-bay",
        "buzzards-bay-vineyard",
        "nantucket-sound",
        "ma-offshore-stellwagen",
        "narragansett-bay",
        "point-judith-block-island",
        "ri-south-shore",
        "nh-coast",
        "nh-offshore",
        "southern-maine",
        "casco-bay",
        "midcoast-maine",
        "western-ct-sound",
        "central-ct-sound",
        "lower-ct-river",
        "eastern-ct-sound",
        "thames-river-new-london",
        "fishers-island-sound-stonington",
        "ct-offshore",
        "raritan-bay-sandy-hook",
        "northern-nj-shore",
        "barnegat-bay",
        "long-beach-island",
        "south-jersey-shore",
        "cape-may-delaware-bay",
        "nj-offshore",
        "hudson-canyon",
        "wilmington-canyon",
        "washington-canyon",
        "south-canyons",
        "east-canyons",
        "north-fork-sound-shore",
    }
)

# Harvest area / port / desk strings that are not already writer ids.
# Values are writer ids. Ambiguous names are omitted on purpose.
AREA_ALIASES: dict[str, str] = {
    "debs-jones": "jones-inlet",
    "debs": "jones-inlet",
    "jones": "jones-inlet",
    "jones-beach": "jones-inlet",
    "freeport": "jones-inlet",
    "wantagh": "jones-inlet",
    "hempstead-bay": "jones-inlet",
    "south-oyster-bay": "jones-inlet",
    "captree": "fire-island",
    "great-south-bay": "fire-island",
    "gsb": "fire-island",
    "robert-moses": "fire-island",
    "patchogue": "fire-island",
    "sayville": "fire-island",
    "babylon": "fire-island",
    "rockaway": "jamaica-bay",
    "rockaway-inlet": "jamaica-bay",
    "canarsie": "jamaica-bay",
    "breezy-point": "jamaica-bay",
    "cupsogue": "moriches",
    "east-moriches": "moriches",
    "westhampton": "moriches",
    "hampton-bays": "shinnecock",
    "ponquogue": "shinnecock",
    "greenport": "peconic",
    "sag-harbor": "peconic",
    "shelter-island": "peconic",
    "orient": "peconic",
    "north-fork": "north-fork-sound-shore",
    "mattituck": "north-fork-sound-shore",
    "sandy-hook": "raritan-bay-sandy-hook",
    "raritan-bay": "raritan-bay-sandy-hook",
    "highlands": "raritan-bay-sandy-hook",
    "sea-bright": "northern-nj-shore",
    "belmar": "northern-nj-shore",
    "manasquan": "northern-nj-shore",
    "shark-river": "northern-nj-shore",
    "barnegat": "barnegat-bay",
    "barnegat-inlet": "barnegat-bay",
    "island-beach": "barnegat-bay",
    "toms-river": "barnegat-bay",
    "lbi": "long-beach-island",
    "barnegat-light": "long-beach-island",
    "little-egg": "long-beach-island",
    "atlantic-city": "south-jersey-shore",
    "ocean-city": "south-jersey-shore",
    "great-egg": "south-jersey-shore",
    "cape-may": "cape-may-delaware-bay",
    "delaware-bay": "cape-may-delaware-bay",
    "wildwood": "cape-may-delaware-bay",
    "lewes": "cape-may-delaware-bay",
    "mud-hole": "nj-offshore",
    "klondike": "nj-offshore",
    "niantic": "eastern-ct-sound",
    "new-london": "thames-river-new-london",
    "stonington": "fishers-island-sound-stonington",
    "watch-hill": "fishers-island-sound-stonington",
    "the-race": "ct-offshore",
    "plum-gut": "ct-offshore",
    "galilee": "point-judith-block-island",
    "point-judith": "point-judith-block-island",
    "newport": "ri-south-shore",
    "stellwagen": "ma-offshore-stellwagen",
    "stellwagen-bank": "ma-offshore-stellwagen",
    "gloucester": "boston-harbor-north-shore",
    "cape-ann": "boston-harbor-north-shore",
    "woods-hole": "buzzards-bay-vineyard",
    "marthas-vineyard": "buzzards-bay-vineyard",
    "cuttyhunk": "buzzards-bay-vineyard",
    "provincetown": "cape-cod-bay",
    "race-point": "cape-cod-bay",
    "hudson": "hudson-canyon",
    "wilmington": "wilmington-canyon",
    "washington": "washington-canyon",
    "hydrographer": "east-canyons",
    "veatch": "east-canyons",
    "poor-mans": "south-canyons",
    "poor-man's": "south-canyons",
}


def _norm_slug(value: object) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("&", " and ")
    text = text.replace("/", " ")
    text = re.sub(r"[’']", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _load_dotenv_value(name: str) -> str:
    env_path = Path.home() / ".hermes" / ".env"
    if not env_path.exists():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def supabase_credentials() -> tuple[str, str]:
    """Return (url, service_role) from env or ~/.hermes/.env. Empty if missing."""
    url = (
        os.environ.get("SUPABASE_URL", "").strip()
        or _load_dotenv_value("SUPABASE_URL")
    )
    role = (
        os.environ.get("SUPABASE_SERVICE_ROLE", "").strip()
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        or os.environ.get("SERVICE_ROLE", "").strip()
        or _load_dotenv_value("SUPABASE_SERVICE_ROLE")
        or _load_dotenv_value("SUPABASE_SERVICE_ROLE_KEY")
        or _load_dotenv_value("SERVICE_ROLE")
    )
    return url, role


def credentials_missing_reason() -> str | None:
    url, role = supabase_credentials()
    missing = []
    if not url:
        missing.append("SUPABASE_URL")
    if not role:
        missing.append("SUPABASE_SERVICE_ROLE")
    if missing:
        return " / ".join(missing)
    return None


def map_location_token(token: object) -> str | None:
    """Map one area/port/desk token onto a writer id, or None if unclassified."""
    slug = _norm_slug(token)
    if not slug or slug in BROADCAST_SLUGS:
        return None
    if slug in KNOWN_WRITER_IDS:
        return slug
    if slug in AREA_ALIASES:
        return AREA_ALIASES[slug]
    return None


def resolve_writer_ids(row: dict) -> set[str]:
    """Return the writer beat(s) this harvest row belongs to.

    Area is the primary pin. Port and desk fill in only when they resolve to
    the same beat, or when area is empty. Conflicting pins yield no writers
    (skip) rather than stuffing the row into the wrong column.
    """
    mapped: list[tuple[str, str]] = []
    for field in ("area", "port", "desk"):
        writer_id = map_location_token(row.get(field))
        if writer_id:
            mapped.append((field, writer_id))

    if not mapped:
        return set()

    unique = {writer_id for _, writer_id in mapped}
    if len(unique) == 1:
        return unique

    area_mapped = {writer_id for field, writer_id in mapped if field == "area"}
    if len(area_mapped) == 1:
        # Area is the beat assignment. A conflicting port/desk means this
        # row is not safe for the area writer either — skip it.
        others = unique - area_mapped
        if others:
            return set()
        return area_mapped
    return set()


def row_belongs_to_writer(row: dict, writer_id: str) -> bool:
    resolved = resolve_writer_ids(row)
    if not resolved:
        return False
    return writer_id in resolved


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def row_timestamp(row: dict) -> datetime | None:
    for key in (
        "posted_at",
        "harvested_at",
        "report_date",
        "created_at",
        "updated_at",
        "date",
    ):
        parsed = _parse_timestamp(row.get(key))
        if parsed:
            return parsed
    return None


def is_ready_row(row: dict, *, now: datetime | None = None) -> bool:
    status = _norm_slug(row.get("status"))
    if status != READY_STATUS:
        return False
    now = now or datetime.now(timezone.utc)
    ts = row_timestamp(row)
    if ts is None:
        # Undated ready rows still count — reporters already dropped held
        # and stale-for-reporters before this status.
        return True
    return ts >= now - timedelta(days=FLEET_WINDOW_DAYS)


def _first_text(row: dict, keys: Iterable[str], max_chars: int | None = None) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = " ".join(str(item) for item in value if item)
        text = re.sub(r"\s+", " ", str(value)).strip()
        if not text:
            continue
        text = FACEBOOK_URL_RE.sub("", text).strip()
        if max_chars and len(text) > max_chars:
            text = text[:max_chars].rsplit(" ", 1)[0] + "…"
        if text:
            return text
    return ""


def format_fleet_intel(row: dict, max_chars: int = 700) -> dict[str, Any]:
    """Shape a harvest row for the writer prompt. Text only. No Facebook URLs."""
    headline = _first_text(
        row,
        ("headline", "title", "subject", "boat_headline"),
        max_chars=160,
    )
    body = _first_text(
        row,
        (
            "body",
            "body_text",
            "report_text",
            "caption",
            "text",
            "summary",
            "content",
            "notes",
        ),
        max_chars=max_chars,
    )
    boat = _first_text(
        row,
        ("boat", "boat_name", "vessel", "source_name", "page_name", "author"),
        max_chars=80,
    )
    species = row.get("species")
    if isinstance(species, str):
        species_list = [s.strip() for s in re.split(r"[,/;]", species) if s.strip()]
    elif isinstance(species, list):
        species_list = [str(s).strip() for s in species if str(s).strip()]
    else:
        species_list = []
    ts = row_timestamp(row)
    intel = {
        "source_kind": "fleet_harvest",
        "date": ts.date().isoformat() if ts else None,
        "area": _first_text(row, ("area",), max_chars=80) or None,
        "port": _first_text(row, ("port",), max_chars=80) or None,
        "desk": _first_text(row, ("desk",), max_chars=80) or None,
        "boat": boat or None,
        "headline": headline or None,
        "text": body or None,
        "species": species_list,
    }
    # Never pass photo payloads or Facebook permalinks through as if they
    # were the writer's own catch or a citeable URL.
    return {key: value for key, value in intel.items() if value not in (None, "", [])}


def fleet_headlines(rows: list[dict]) -> list[str]:
    headlines: list[str] = []
    for row in rows:
        headline = row.get("headline") or row.get("title") or row.get("boat")
        if headline:
            headlines.append(str(headline))
    return headlines


def _rest_get(url: str, service_role: str, params: dict[str, str]) -> list[dict]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url.rstrip('/')}/rest/v1/{FLEET_TABLE}?{query}",
        headers={
            "apikey": service_role,
            "Authorization": f"Bearer {service_role}",
            "Accept": "application/json",
            "Prefer": "count=none",
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=20) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list):
        return []
    return [row for row in payload if isinstance(row, dict)]


def fetch_ready_fishing_reports(
    *,
    now: datetime | None = None,
    url: str | None = None,
    service_role: str | None = None,
) -> list[dict]:
    """Fetch status=ready harvest rows. Returns [] on any failure."""
    if url is None or service_role is None:
        url, service_role = supabase_credentials()
    if not url or not service_role:
        return []
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=FLEET_WINDOW_DAYS)).isoformat().replace("+00:00", "Z")
    try:
        rows = _rest_get(
            url,
            service_role,
            {
                "status": "eq.ready",
                "select": "*",
                "order": "created_at.desc",
                "limit": str(FLEET_FETCH_LIMIT),
            },
        )
    except Exception as exc:  # noqa: BLE001 — cron must not crash
        print(
            f"[warn] fleet harvest fetch failed ({type(exc).__name__}: {exc}) "
            "— falling back to jsonl forum intel",
            file=sys.stderr,
        )
        return []

    ready = [row for row in rows if is_ready_row(row, now=now)]
    # Drop photo-only payloads; keep rows that still have text after stripping.
    usable: list[dict] = []
    for row in ready:
        text_row = {
            key: value
            for key, value in row.items()
            if _norm_slug(key) not in PHOTO_KEYS
        }
        formatted = format_fleet_intel(text_row)
        if formatted.get("headline") or formatted.get("text"):
            usable.append(text_row)
    # Prefer rows inside the window even if created_at filter was not applied
    # server-side (some harvest rows date on posted_at).
    _ = cutoff  # documented for operators; filtering is in is_ready_row
    return usable


def load_fleet_harvest_for_writer(
    writer_id: str,
    *,
    limit: int = 30,
    now: datetime | None = None,
    rows: list[dict] | None = None,
) -> list[dict]:
    """Ready harvest rows that map onto this writer's beat, newest first."""
    if rows is None:
        if credentials_missing_reason():
            return []
        rows = fetch_ready_fishing_reports(now=now)
    matched: list[dict] = []
    for row in rows:
        if not is_ready_row(row, now=now):
            continue
        if not row_belongs_to_writer(row, writer_id):
            continue
        formatted = format_fleet_intel(row)
        if not (formatted.get("headline") or formatted.get("text")):
            continue
        matched.append(formatted)
    matched.sort(key=lambda r: r.get("date") or "", reverse=True)
    return matched[:limit]


def log_credential_miss() -> None:
    reason = credentials_missing_reason()
    if reason:
        print(
            f"[gen] fleet_harvest=skip (no {reason}) — using jsonl forum intel "
            f"(expected {DEFAULT_SUPABASE_URL})",
            file=sys.stderr,
        )
