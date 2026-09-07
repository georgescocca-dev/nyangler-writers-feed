# fishing_reports archive

This directory is a **GitHub backup** of the live Supabase table `public.fishing_reports`.

- **Live product:** Supabase `public.fishing_reports`
- **This file:** `archive/fishing_reports.jsonl` — every fleet row that cron could export (no status filter), deduped, Facebook JPEGs stripped
- **Not served to sites.** nyangler.com and reports.nyangler.com do not fetch this dump. As of 2026-08-27 those sites get no new fishing reports from this repo. The XenForo forum stays.

Cron (`scripts/archive_fishing_reports.py`) merges a fresh export into the jsonl. Missing `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE` leaves the existing dump alone.

Do not treat an empty or stale jsonl as a live feed. The first successful export on a machine with credentials fills it.
