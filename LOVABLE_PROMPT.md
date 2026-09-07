# Lovable Build Prompt — writer roster snapshot (not live fishing reports)

**Paste this into a Lovable project only if you need the Writers / Editorial Team roster UI.**

This GitHub repo is an **archive/backup**. Live fishing reports live in Supabase `public.fishing_reports`. **As of 2026-08-27, nyangler.com and reports.nyangler.com get no new fishing reports from this repo.** The XenForo forum stays. Do **not** fetch `reports.json` as a live Noreaster feed.

The roster snapshot is hosted on GitHub at `raw.githubusercontent.com/georgescocca-dev/nyangler-writers-feed`. No backend, no API keys, no database needed for **writer names, beats, and portraits**. That is not fishing-report copy.

---

## Master Prompt

> Build the **Writers / Editorial Team** section from the **roster snapshot** in this GitHub archive — names, beats, portraits. This is **not** a live fishing-reports product and must not fetch `reports.json` for Noreaster.
>
> The data is hosted as a public JSON feed at:
>
> ```
> https://raw.githubusercontent.com/georgescocca-dev/nyangler-writers-feed/main/writers.json
> ```
>
> The feed contains **19 writers**: one Editor-in-Chief and 18 zone writers covering NY, NJ, RI, and CT inshore, surf, sound, bay, and offshore canyon fisheries.
>
> ### Pages to build
>
> 1. `/writers` — masthead grid of all 19 writers, grouped by `domain` (Editorial, Inshore, Offshore).
> 2. `/writers/[id]` — individual writer profile page using the writer's `id` as the URL slug.
> 3. A homepage hero strip surfacing the Editor-in-Chief plus 3-4 featured zone writers (rotate weekly).
>
> ### Visual direction
>
> **Style: Sports Illustrated × Atlantic — Dark Premium Editorial.**
>
> - Background: midnight navy `#0a0e14`, card surface `#11161e`, divider `#1f2733`.
> - Text: salt-white `#ecf2f8` on body, muted `#8a98a9` for secondary lines.
> - Accent: sunrise copper `#d97706` — used sparingly for kickers, hover borders, and pill badges. Never for body text.
> - Typography stack:
>   - Headlines / writer names: **Playfair Display** (700–800 weight, tight letter-spacing -0.01em to -0.02em, line-height 1.05–1.15).
>   - Body / bios / metadata: **Inter** (400–600 weight).
>   - Kickers, role labels, section counts, byline marks: **JetBrains Mono** (500 weight, ALL CAPS, letter-spacing 0.15em–0.22em). This is the SI/Atlantic "byline" voice.
> - Cards are bordered (1px solid divider), 4px radius (not pill — keeps it editorial, not consumer). Hover lifts the card 4px and shifts the border to the copper accent.
> - Portraits sit at the top of each card in a 4:5 aspect ratio, slightly desaturated (`filter: contrast(1.04) saturate(.92)`) for that editorial polish.
> - Page max-width: 1240px. Generous padding — this is a newsroom, not a marketplace.
> - Mobile-first responsive — grid collapses to a single column under 640px.
>
> Reference: think **The Atlantic's design language** with the gravitas of **SI Longform** features. Premium, serious, paid-subscription-ready. Not flashy, not marketing-y.
>
> **Pixel reference:** a complete working HTML/CSS implementation of this style — using real data from the feed — is shipped in this repo at `mockups/reference-design.html`. View it raw at:
> ```
> https://raw.githubusercontent.com/georgescocca-dev/nyangler-writers-feed/main/mockups/reference-design.html
> ```
> Match the typography, color palette, spacing, and hover behavior of that file. The mockup shows 6 of 19 writers in a grid — Lovable should render all 19, grouped by domain (see Grouping logic below).
>
> ### Card content (use these fields exactly)
>
> - `name` — big serif header
> - `role` — small caps under the name (e.g. "Zone Writer")
> - `zone.name` — beat title (e.g. "Jamaica Bay / Rockaway")
> - `bio` — 2-3 sentence paragraph, the main body of the card
> - `beat_species` — render as pill badges, max 4 visible
> - `landmarks` — render as a small "Covers:" line, max 3 visible
> - `voice` and `mood` — combine into a single italic tagline at the bottom (e.g. *Bay Local · warm-grounded-female*)
> - `portrait_url` — direct `<img src>`, no transformation needed
>
> ### Fetching the data
>
> Use this hook:
>
> ```jsx
> // src/hooks/useWriters.js
> import { useEffect, useState } from "react";
>
> const FEED_URL =
>   "https://raw.githubusercontent.com/georgescocca-dev/nyangler-writers-feed/main/writers.json";
>
> export function useWriters() {
>   const [state, setState] = useState({ writers: [], loading: true, error: null });
>
>   useEffect(() => {
>     fetch(FEED_URL, { cache: "no-store" })
>       .then((r) => {
>         if (!r.ok) throw new Error(`HTTP ${r.status}`);
>         return r.json();
>       })
>       .then((d) => setState({ writers: d.writers, loading: false, error: null }))
>       .catch((err) => setState({ writers: [], loading: false, error: err }));
>   }, []);
>
>   return state;
> }
> ```
>
> ### Routing
>
> Use React Router or your default Lovable routing layer. The detail-page route should be `/writers/:id`, and look the writer up by matching `w.id === params.id`.
>
> ### Grouping logic for the masthead
>
> Order groups exactly as follows:
>
> 1. **Editorial** (`domain === "editorial"`) — just the Editor-in-Chief, render as a feature row at the top
> 2. **Inshore Zone Writers** (`domain === "inshore"`) — sorted by `zone.name` alphabetically
> 3. **Offshore Captains** (`domain === "offshore"`) — sorted by `zone.name` alphabetically
>
> ### Tone & Voice
>
> NY Angler is the largest fishing community in the Northeast and the writers are real personas with deep beat knowledge. The page should feel like a **professional newsroom masthead** — not a marketing team page. No "meet the team!" energy. These are operators, not mascots.
>
> ### Acceptance criteria
>
> - Loads in under 2 seconds on a cold cache.
> - Looks correct on a 1440px desktop and a 390px iPhone.
> - Portrait images degrade gracefully if the URL 404s (use a fallback placeholder).
> - No hardcoded writer data anywhere in the code — everything comes from the JSON feed.
> - Detail page deep links work (sharing `/writers/jamaica-bay` opens directly to Denise Vasquez).

---

## After Lovable generates the project

1. Verify the writers grid renders all 19.
2. Click into 3 different writer pages — check that the portraits load and the bio reads cleanly.
3. Inspect the network tab — there should be exactly one fetch to `raw.githubusercontent.com/.../writers.json`.

## Updating the roster going forward

When you (George) want to add a writer, retire one, refresh a portrait, or rewrite a bio, the Nor'easter system (Spartacus) updates the source roster, regenerates the snapshot, and pushes to GitHub. That updates the **roster archive**. It does **not** ship new fishing reports to reports.nyangler.com.

## Feed sample

A single writer record in the feed looks like:

```json
{
  "id": "jamaica-bay",
  "name": "Denise Dee Vasquez",
  "full_name": "Denise \"Dee\" Vasquez",
  "first_name": "Denise",
  "last_name": "Vasquez",
  "nickname": "Dee",
  "role": "Zone Writer",
  "domain": "inshore",
  "zone": { "slug": "jamaica-bay", "name": "Jamaica Bay / Rockaway" },
  "coverage_area": "Jamaica Bay, Rockaway Inlet, Rockaway Beach, Marine Parkway Bridge area",
  "beat_species": ["striped bass", "bluefish", "fluke", "porgies", "blackfish", "weakfish"],
  "landmarks": ["Marine Parkway Bridge", "Breezy Point", "Rockaway Beach", "Cross Bay Bridge", "Canarsie Pier"],
  "voice": "Bay Local",
  "mood": "warm-grounded-female",
  "style_tags": ["multi-platform", "access-points", "fall-blitz"],
  "bio": "Denise Vasquez grew up fishing the bay, the inlet, and the surf from Breezy Point to Rockaway. She knows the Marine Parkway Bridge bass on the outgoing, the May fluke turn-on inside the bay, and the kind of fall Rockaway blitz most anglers only ever see in dreams. She fishes shore, boat, and kayak — and she knows every access point.",
  "portrait_url": "https://raw.githubusercontent.com/georgescocca-dev/nyangler-writers-feed/main/portraits/jamaica-bay.png",
  "status": "active"
}
```
