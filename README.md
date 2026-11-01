# Counterfactual Accessibility Lab

Ask a what-if question about Algiers in plain language. The agent turns it into a list of edits to a sandboxed copy of the city, shows you the plan, and after you approve it computes access to hospitals, clinics, pharmacies and schools for the real city and the changed one. It then explains who gains, who loses, by how much, and whether access becomes more or less equal.

The LLM plans and explains. Code computes. Every number in the explanation is checked against stored results before you see it. The real city is never modified.

![Travel time to the nearest hospital](docs/img/factual_hospital.png)

## What you can ask

- "What if a new clinic opened near Baraki?"
- "What if the main hospital in Bab El Oued closed?"
- "What if Rue Hassiba Ben Bouali were closed to traffic?"
- "What if a road connected these two pins?" (drop pins on the map first)
- "Now also close this road" (builds on the scenario you are looking at)

Five edit types: open a facility, close a facility, add a road, close a road or an area, change driving speed on a road or area (a proxy for a road upgrade or a bus corridor). Two modes: driving and walking.

## How it works

```
question
  -> interpret   LLM with two tools: search_places, nearby_facilities. Writes a plan that
                 refers to places by ref (p1, click1). It never writes coordinates.
  -> validate    code: refs exist, points inside the wilaya and within 300 m of a road,
                 removed roads exist, nothing large gets cut off. One repair round.
  -> confirm     LangGraph interrupt. You approve, edit or cancel the plan.
  -> apply       copy-on-write branch of the scenario tree
  -> compute     travel times, coverage, 2SFCA, equity for every populated H3 cell
  -> compare     per-cell change, summary deltas, communes that gain and lose
  -> narrate     LLM writes 4 to 6 sentences. Any number not in the computed facts is
                 rejected and the LLM rewrites once; after that a plain template is used.
```

The LLM is called two to three times per scenario, never inside a metric loop. Each run keeps a provenance log of every tool call, LLM call, validation result and timing (`GET /api/scenarios/{id}/provenance`).

## Data

All sources were checked before use. Counts are from `scripts/fetch_data.py` on 2026-11-01.

| Input | Source | Result |
|---|---|---|
| Study area | OSM relation 157062, Wilaya d'Alger | 770.9 km² |
| Road network | Geofabrik Algeria extract 2026-10-31, built with OSMnx's own filters | drive: 54,010 nodes, 131,721 edges. walk: 81,811 nodes, 220,680 edges |
| Facilities | OSM `amenity` and `healthcare` tags | 123 hospital points (85 after merging), 859 clinics, 757 pharmacies, 1,328 schools |
| Population | WorldPop 2025 constrained 100 m (R2025A) | 4,121,898 people in 7,240 H3 cells (res 9, 201 m edge) |
| Communes | OSM admin level 8 | 56 polygons |

Notes from checking the data:

- A name search for "Algiers Province" in Nominatim returns the wilaya office building, so the boundary is pinned by relation id and the fetch fails if the area is under 500 km².
- The public Overpass servers were overloaded or refusing requests during development, so OSM data comes from a pinned Geofabrik extract parsed locally with pyosmium. The graphs go through the same build steps as `ox.graph_from_polygon`.
- OSM maps many hospital departments as separate hospitals ("urgences pédiatriques", "consultation d'hématologie"). Hospital points within 250 m are merged into one: 123 points become 85.
- No hospital in the data has a `beds` tag, so 2SFCA uses equal capacity for every facility.
- Communes cover 743.6 of 770.9 km². About 112,000 people live in cells outside any commune polygon and are assigned to the nearest commune. Algiers has 57 communes; one is missing from OSM.

Metric work uses EPSG:32631 (WGS 84 / UTM 31N).

## Metrics

For each populated H3 cell, each facility type and each mode:

- travel time to the nearest facility
- people within 10, 15 and 30 minutes
- number of facilities within 15 minutes (cumulative opportunities)
- 2SFCA with a 15 minute catchment, per 10,000 people
- population-weighted Gini of travel time and the 90th / 10th percentile ratio
- per-cell change, people gaining and losing, communes gaining and losing most

Travel time = walk from the cell centre to a street node + network time + walk from a street node to the facility. Each cell and facility links to up to four street nodes: the nearest, plus any within 300 m. The best one is used. Links are fixed by the real city, so closing a road can only lengthen trips and adding one can only shorten them. Motorway nodes are not used for access. A cell more than 1 km from any road counts as unreachable (4,228 people for driving, 1,765 for walking).

Speeds: driving uses OSMnx speed imputation (posted `maxspeed`, else the mean for that road class, e.g. 30.9 km/h for residential). Walking is 4.5 km/h everywhere.

## Validation

`uv run pytest` runs 23 tests.

- **Hand-built network** (`backend/tests/toy.py`): a line of nodes 1 km apart where every time can be worked out by hand. One test per edit type, plus scenario chaining, validation messages, the factual city never changing, copy-on-write, re-linking cut-off cells, 2SFCA values and Gini on known inputs.
- **Real data properties**: adding a hospital never slows any cell; removing one never speeds any cell; adding a road never slows any cell; closing every motorway never speeds any cell.
- **Cross-check**: travel times for 300 random cells per mode match NetworkX Dijkstra on the same graph exactly.
- **Agent**: with a scripted model, a full run through the interrupt, a repair round, cancel, a plan that cannot be repaired, and the number check rejecting an invented figure.
- **API**: ask, confirm, metrics, compare, edit geometry, isochrone and scenario tree over HTTP.

## Routing benchmark

`scripts/benchmark.py`, nearest hospital for every node and every hospital's 15 minute catchment, Intel i7 13th gen laptop, no GPU:

| Engine | drive build | drive nearest | drive catchments | matches NetworkX |
|---|---|---|---|---|
| scipy csgraph | 0.03 s | 0.02 s | 0.67 s | exactly |
| igraph | 0.18 s | 0.09 s | | exactly |
| NetworkX | 0.70 s | 0.33 s | | reference |
| pandana | 204 s | 1.44 s | 1.76 s | within 0.05 s |

Every counterfactual changes the graph, so build time matters most. pandana's contraction hierarchy takes over three minutes to build for the drive graph. The lab uses scipy.

## Runtime

Measured on the same laptop after caching:

| Step | Time |
|---|---|
| Server start (load city, cached baseline) | 6 s |
| Factual baseline, all 8 mode and type pairs | 22 s, once, cached to disk |
| Facility edit (one type recomputed) | about 7 s |
| Road edit for cars (four types recomputed) | about 18 s |
| LLM planning | 2 to 5 s, more when the provider is busy |
| LLM narrative | about 1 s |

Results are cached per network state and facility set, so only what an edit touches is recomputed.

## Limitations

- **OSM completeness.** Facilities are what volunteers mapped. Some clinics and pharmacies are missing; some points are departments, not facilities. Names are often missing (342 of 757 pharmacies have one).
- **No traffic.** Driving times are free flow. Real peak-hour times in Algiers are much longer, so driving access is optimistic.
- **No capacity data.** 2SFCA treats every facility of a type as equal. A university hospital and a small polyclinic count the same.
- **Speed assumptions.** Imputed speeds by road class, one walking speed for everyone, no slopes, no crossings.
- **A bus line is only a speed change.** There are no stops, headways or waiting times.
- **Population** is a modelled 2025 estimate, not a census.
- **Nearest facility only** for travel time. People do not always go to the nearest one.

## Run it

```bash
uv sync
uv run python scripts/fetch_data.py        # about 6 minutes, downloads 370 MB
uv run python scripts/precompute_factual.py
cp .env.example .env                       # add a model key
uv run uvicorn backend.api.app:app --port 8000
cd frontend && npm install && npm run dev  # http://localhost:3000
```

`LAB_MODEL` takes any LangChain `init_chat_model` string, for example `google_genai:gemini-3.1-flash-lite`. Install the matching provider package. Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` to trace runs.

One question from the command line:

```bash
uv run python scripts/run_scenario.py "What if a new clinic opened near Baraki?"
```

## Layout

```
backend/
  agent/     LangGraph graph, plan schema, prompts, number check, fact sheet
  network/   loading, edits, scenario tree, validation
  metrics/   travel times, 2SFCA, equity, comparison
  geocode/   local name search, rate-limited Nominatim
  api/       FastAPI with server-sent events
  tests/
frontend/    Next.js, MapLibre, deck.gl
scripts/     fetch_data, precompute_factual, benchmark, run_scenario
data/        README only; everything else is generated
```

Data: OpenStreetMap contributors (ODbL), WorldPop (CC BY 4.0). Basemap: CARTO.
