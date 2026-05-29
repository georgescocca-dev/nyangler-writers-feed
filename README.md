# NY Angler — AI Writers Feed

Public, versioned data feed for the **reports.nyangler.com** editorial roster.

- **Source of truth:** `writers.json` in this repo
- **Live URL (raw):** https://raw.githubusercontent.com/georgescocca-dev/nyangler-writers-feed/main/writers.json
- **Portraits:** `portraits/<writer-id>.png` — referenced by `portrait_url` in each record

Updated by the Nor'easter editorial system. Any change to the roster here propagates to reports.nyangler.com automatically.

---

## Schema (writers.json)

```json
{
  "schema_version": "1.0",
  "site": "reports.nyangler.com",
  "publisher": "Nor'easter / NY Angler",
  "feed_url": "https://raw.githubusercontent.com/.../writers.json",
  "total_writers": 19,
  "writers": [
    {
      "id": "jamaica-bay",                    // stable slug, use as React key
      "name": "Denise Dee Vasquez",           // display name, quotes stripped
      "full_name": "Denise \"Dee\" Vasquez",  // raw with nickname quotes
      "first_name": "Denise",
      "last_name": "Vasquez",
      "nickname": "Dee",                       // null if no nickname
      "role": "Zone Writer",                   // or "Editor-in-Chief"
      "domain": "inshore",                     // editorial | inshore | offshore | surf | sound | bay
      "zone": {
        "slug": "jamaica-bay",
        "name": "Jamaica Bay / Rockaway"
      },
      "coverage_area": "Jamaica Bay, Rockaway Inlet, ...",
      "beat_species": ["striped bass", "bluefish", "fluke", ...],
      "landmarks": ["Marine Parkway Bridge", "Breezy Point", ...],
      "voice": "Bay Local",
      "mood": "warm-grounded-female",
      "style_tags": ["multi-platform", "access-points", "fall-blitz"],
      "portrait_url": "https://raw.githubusercontent.com/.../portraits/jamaica-bay.png",
      "status": "active"
    }
  ]
}
```

### Field notes

- `id` is the canonical key. Use it for React `key=` props, URL slugs (`/writers/jamaica-bay`), and image filenames.
- `domain` is the highest-level grouping — useful for filter tabs (Inshore / Offshore / Bay / Sound / Surf / Editorial).
- `zone.slug` is the lower-level grouping — useful for a coverage map.
- `voice` and `mood` describe editorial tone; show them as small chips on the writer card.
- `style_tags` are short keywords that work well as pill badges.
- `portrait_url` is a stable raw.githubusercontent.com URL — fine to use directly in `<img src>`.

---

## How Lovable should consume this

In your Lovable React project, fetch the feed once on page load and render from it. No backend needed.

```jsx
// src/hooks/useWriters.js
import { useEffect, useState } from "react";

const FEED_URL =
  "https://raw.githubusercontent.com/georgescocca-dev/nyangler-writers-feed/main/writers.json";

export function useWriters() {
  const [data, setData] = useState({ writers: [], loading: true, error: null });

  useEffect(() => {
    fetch(FEED_URL, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => setData({ writers: d.writers, loading: false, error: null }))
      .catch((err) => setData({ writers: [], loading: false, error: err }));
  }, []);

  return data;
}
```

```jsx
// src/pages/Writers.jsx
import { useWriters } from "../hooks/useWriters";

export default function Writers() {
  const { writers, loading } = useWriters();
  if (loading) return <p>Loading roster…</p>;

  return (
    <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
      {writers.map((w) => (
        <article key={w.id} className="rounded-xl bg-slate-900 p-5 text-white">
          <img
            src={w.portrait_url}
            alt={w.name}
            className="w-full h-72 object-cover rounded-lg mb-4"
          />
          <h3 className="text-xl font-bold">{w.name}</h3>
          <p className="text-sm text-slate-400">{w.role} · {w.zone.name}</p>
          <p className="mt-3 text-slate-300">{w.coverage_area}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            {w.beat_species.slice(0, 4).map((s) => (
              <span key={s} className="text-xs px-2 py-1 rounded bg-slate-800">
                {s}
              </span>
            ))}
          </div>
        </article>
      ))}
    </section>
  );
}
```

---

## Suggested page layouts

1. **Masthead grid** — all 19 writers in a responsive 3-up grid, grouped by `domain`.
2. **Meet your zone writer** — interactive coastal map where each zone slug pin opens the matching writer card.
3. **Editor-in-Chief feature** — pull the writer where `role === "Editor-in-Chief"` and feature on the about page with a tagline pulled from `voice` + `mood`.

---

## How updates flow

```
config/writers_roster.json (Nor'easter system)
        │
        │  scripts/build-feed.py  (regenerates writers.json + copies portraits)
        ▼
nyangler-writers-feed (this repo, main branch)
        │
        │  git push → raw.githubusercontent.com
        ▼
reports.nyangler.com (Lovable site fetches at runtime)
```

When the writers change (new persona, updated portrait, retired beat), run `scripts/build-feed.py` and push. Live within ~10 seconds via the GitHub raw CDN.

---

## License & private data

This repo intentionally **excludes** internal scaffolding from the source roster:
- `system_prompt` — internal AI instruction set, never shipped
- `portrait_prompt` — image-gen prompt history, internal
- `model` — which LLM backs the persona, internal

Only public-facing fields ship here. If you need the internal fields for editorial review, see the private `nor-easter-setup` repo.
