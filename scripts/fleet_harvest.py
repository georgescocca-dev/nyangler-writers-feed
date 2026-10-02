#!/usr/bin/env python3
"""Fleet harvest helpers for the GitHub archive of public.fishing_reports.

Live product is Supabase public.fishing_reports. This repo is an archive/backup.
Writer intel still uses status=ready rows from the last 7 days. The archive dump
backs up every row (no status filter), strips Facebook JPEGs, and dedups.

Facebook post permalinks may stay in the dump. Photo payloads do not. Rows that
cannot be mapped onto a single writer beat are skipped for intel, not for backup.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
import hashlib
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
IMAGE_URL_RE = re.compile(
    r"https?://\S+\.(?:jpe?g|png|gif|webp|heic)(?:\?\S*)?"
    r"|https?://(?:scontent|static)\S*fbcdn\.net\S*"
    r"|https?://\S*facebook\.com/\S+\.(?:jpe?g|png|gif|webp)",
    re.IGNORECASE,
)
ZONE_WRITER_DESK = "zone-writer"
ZONE_WRITER_SOURCE = "zone-writer-archive"
FACT_TEXT_MAX = 240
ARCHIVE_PAGE_SIZE = 1000
ARCHIVE_MAX_ROWS = 100_000
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE_PATH = REPO_ROOT / "archive" / "fishing_reports.jsonl"

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


def _rest_post(url: str, service_role: str, payload: dict) -> None:
    request = urllib.request.Request(
        f"{url.rstrip('/')}/rest/v1/{FLEET_TABLE}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "apikey": service_role,
            "Authorization": f"Bearer {service_role}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as resp:
        resp.read()


def _is_photo_key(key: str) -> bool:
    slug = _norm_slug(key)
    if slug in PHOTO_KEYS:
        return True
    if slug in {"source-url", "url", "permalink", "link", "post-url"}:
        return False
    return any(token in slug for token in ("photo", "image", "thumb", "media", "picture", "jpeg", "jpg"))


def _is_image_value(value: object) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip().lower()
    if not text:
        return False
    if "fbcdn.net" in text or "scontent" in text:
        return True
    if any(ext in text for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic")):
        if text.startswith("http") or text.startswith("data:image"):
            return True
    return bool(IMAGE_URL_RE.search(value))


def strip_media_fields(value: object) -> object:
    """Drop Facebook JPEGs / photo payloads. Keep text and post permalinks."""
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key, item in value.items():
            if _is_photo_key(str(key)):
                continue
            cleaned = strip_media_fields(item)
            if _is_image_value(cleaned):
                continue
            out[str(key)] = cleaned
        return out
    if isinstance(value, list):
        return [
            strip_media_fields(item)
            for item in value
            if not _is_image_value(item)
        ]
    if isinstance(value, str):
        if _is_image_value(value):
            return ""
        return IMAGE_URL_RE.sub("", value).strip()
    return value


def archive_row_key(row: dict) -> str:
    if row.get("id") not in (None, ""):
        return f"id:{row['id']}"
    blob = "|".join(
        [
            str(row.get("source_url") or row.get("url") or ""),
            str(row.get("created_at") or row.get("posted_at") or row.get("harvested_at") or ""),
            str(row.get("headline") or row.get("title") or ""),
            str(row.get("boat") or row.get("author") or ""),
            str(row.get("area") or ""),
            str(row.get("text") or row.get("body") or "")[:80],
        ]
    )
    return "hash:" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:20]


def load_archive_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def write_archive_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(row, ensure_ascii=False, sort_keys=True)
        for row in rows
    ]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def merge_archive_rows(existing: list[dict], incoming: list[dict]) -> list[dict]:
    """Dedup by id (or content hash). Incoming table rows win when newer."""
    by_key: dict[str, dict] = {}
    for row in existing + incoming:
        if not isinstance(row, dict):
            continue
        cleaned = strip_media_fields(row)
        if not isinstance(cleaned, dict) or not cleaned:
            continue
        key = archive_row_key(cleaned)
        prev = by_key.get(key)
        if prev is None:
            by_key[key] = cleaned
            continue
        prev_ts = row_timestamp(prev)
        new_ts = row_timestamp(cleaned)
        if prev_ts and new_ts and new_ts < prev_ts:
            continue
        by_key[key] = cleaned
    merged = list(by_key.values())
    merged.sort(
        key=lambda row: (
            row_timestamp(row) or datetime.min.replace(tzinfo=timezone.utc)
        ).isoformat(),
        reverse=True,
    )
    return merged


def fetch_all_fishing_reports(
    *,
    url: str | None = None,
    service_role: str | None = None,
) -> list[dict]:
    """Backup every fishing_reports row. No status filter. Returns [] on failure."""
    if url is None or service_role is None:
        url, service_role = supabase_credentials()
    if not url or not service_role:
        return []
    collected: list[dict] = []
    offset = 0
    order_params = [
        {"select": "*", "order": "created_at.desc"},
        {"select": "*", "order": "id.desc"},
        {"select": "*"},
    ]
    base_params = order_params[0]
    for candidate in order_params:
        try:
            _rest_get(
                url,
                service_role,
                {**candidate, "limit": "1"},
            )
            base_params = candidate
            break
        except Exception:
            continue
    try:
        while offset < ARCHIVE_MAX_ROWS:
            batch = _rest_get(
                url,
                service_role,
                {
                    **base_params,
                    "limit": str(ARCHIVE_PAGE_SIZE),
                    "offset": str(offset),
                },
            )
            if not batch:
                break
            collected.extend(batch)
            if len(batch) < ARCHIVE_PAGE_SIZE:
                break
            offset += ARCHIVE_PAGE_SIZE
    except Exception as exc:  # noqa: BLE001 — cron must not crash
        print(
            f"[warn] fishing_reports archive fetch failed "
            f"({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        return []
    return collected


def dump_fishing_reports_archive(
    dest: Path,
    *,
    rows: list[dict] | None = None,
) -> dict[str, Any]:
    """Merge table rows into dest jsonl. Never deletes an existing dump on miss."""
    existing = load_archive_jsonl(dest)
    if rows is None:
        reason = credentials_missing_reason()
        if reason:
            print(
                f"[archive] skip (no {reason}) — keeping existing dump at {dest}",
                file=sys.stderr,
            )
            return {
                "ok": False,
                "reason": reason,
                "kept_existing": len(existing),
                "path": str(dest),
            }
        rows = fetch_all_fishing_reports()
        if not rows and not existing:
            print(
                "[warn] fishing_reports archive fetch returned 0 rows",
                file=sys.stderr,
            )
    merged = merge_archive_rows(existing, rows or [])
    write_archive_jsonl(dest, merged)
    print(
        f"[archive] fishing_reports jsonl={dest} rows={len(merged)} "
        f"(incoming={len(rows or [])} existing={len(existing)})",
        file=sys.stderr,
    )
    return {
        "ok": True,
        "rows": len(merged),
        "incoming": len(rows or []),
        "existing": len(existing),
        "path": str(dest),
    }


def zone_writer_fact_payload(writer: dict, report: dict, date: str) -> dict:
    """Short ticker fact. Not a boat post and not the 800-word column."""
    writer_id = str(writer.get("id") or writer.get("zone_slug") or "").strip()
    headline = str(report.get("headline") or "").strip()[:160]
    text = str(report.get("subhead") or "").strip()
    if not text:
        body = str(report.get("body_markdown") or "")
        text = re.split(r"\n\s*\n", body, maxsplit=1)[0]
        text = re.sub(r"\s+", " ", text).strip()
    text = text[:FACT_TEXT_MAX]
    payload = {
        "status": READY_STATUS,
        "area": writer_id,
        "desk": ZONE_WRITER_DESK,
        "headline": headline,
        "text": text,
        "source": ZONE_WRITER_SOURCE,
        "author": ZONE_WRITER_DESK,
        "report_date": date,
        "source_url": f"archive://zone-writer/{writer_id}/{date}",
    }
    return {key: value for key, value in payload.items() if value not in (None, "")}


def upsert_zone_writer_fact(
    writer: dict,
    report: dict,
    date: str,
    *,
    url: str | None = None,
    service_role: str | None = None,
) -> bool:
    """Insert a short zone-writer fact. Never impersonates a boat. Never raises."""
    if url is None or service_role is None:
        url, service_role = supabase_credentials()
    if not url or not service_role:
        print(
            "[gen] zone-writer fact skip (no SUPABASE_URL / SERVICE_ROLE)",
            file=sys.stderr,
        )
        return False
    payload = zone_writer_fact_payload(writer, report, date)
    if "body_markdown" in payload or payload.get("desk") != ZONE_WRITER_DESK:
        return False
    if payload.get("boat") or payload.get("vessel"):
        return False
    attempts = [
        payload,
        {
            key: payload[key]
            for key in ("status", "area", "desk", "headline", "text")
            if key in payload
        },
    ]
    last_err: Exception | None = None
    for body in attempts:
        try:
            _rest_post(url, service_role, body)
            print(
                f"[gen] zone-writer fact upserted area={payload.get('area')} "
                f"date={date}",
                file=sys.stderr,
            )
            return True
        except Exception as exc:  # noqa: BLE001 — cron must not crash
            last_err = exc
    print(
        f"[warn] zone-writer fact upsert failed "
        f"({type(last_err).__name__}: {last_err})",
        file=sys.stderr,
    )
    return False


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
