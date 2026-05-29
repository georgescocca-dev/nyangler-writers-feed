# Lovable Build Prompt — ai.nyangler.com Writers Section

**Paste this into a new Lovable project to spin up the writers section of ai.nyangler.com.**

The data is live-hosted on GitHub at `raw.githubusercontent.com/georgescocca-dev/nyangler-writers-feed`. No backend, no API keys, no database needed. Updates to the roster propagate to the live site within seconds whenever the source repo is pushed.

---

## Master Prompt

> Build the **Writers / Editorial Team** section for **ai.nyangler.com** — the AI-powered subdomain of New York Angler, the largest fishing community in the Northeast.
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
> - Dark, premium editorial style — think *Sports Illustrated* meets *Atlantic*.
> - Portrait images are large and lead each card (each portrait is roughly square, 1024px or larger).
> - Typography: serif for names and headlines, clean sans for metadata.
> - Color accents: deep ocean blues, salt-white text, a single warm accent (sunrise orange or copper) for hover/CTA.
> - Mobile-first responsive — the grid collapses to a single column under 768px.
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

When you (George) want to add a writer, retire one, refresh a portrait, or rewrite a bio, the Nor'easter system (Spartacus) updates the source roster, regenerates the feed, and pushes to GitHub. The live ai.nyangler.com site picks up the change on the next page load — no Lovable redeploy required.

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
