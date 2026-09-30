# Module Spec: route-progress

> Last Updated: 2026-09-30 · Status: implemented (feat/route-progress, feat/routes-weather-hr)

## Overview

Finds repeated **segments** (climbs, descents, shared stretches) and whole
**routes** in the athlete's own GPS history, with no map data and no manual
setup — like Strava segments / Matched Runs, detected automatically. Each
traversal is an **effort** with time, VAM, heart rate, power and hrTSS share,
so progress on the same hill or loop can be read effort by effort and any two
efforts compared along the segment.

User request: 「同一條路線的進步追蹤能自動產出嗎，例如判斷我歷史有幾段重疊的路線，就把重疊的地方撈出來做出差異比對」

## Architecture

| Layer | File | Role |
|---|---|---|
| Geometry | `backend/engine/algorithms/route_match.py` | resampling, point-to-path distance, overlap ratio, discrete Fréchet, common stretches, effort search, in-order projection |
| Engine | `backend/engine/routes.py` | compact tracks, detection, effort metrics, names, on-disk store, background builder |
| API | `backend/api/routes.py` | `/api/v1/routes…`, `/api/v1/wko5/workouts/{idx}/segments` |
| Page | `backend/static/routes.html` | 路線 page: list, detail (map, trend, comparison, effort table) |
| Card | `backend/static/segments_card.js` | single-activity card in the viewer (one `<script>` tag in `wko5_viewer.html`) |
| Map layers | `backend/static/basemaps.js` | the viewer's basemaps / overlays + the settings default, shared |

The existing `algorithms/routes.py` (100 m cell Jaccard, used by the
achievements page) is unchanged.

### Storage (the `routes/` folder in the app's home data folder; root injectable via `RouteStore(root)` or the `WKO5COACH_ROUTES_DIR` environment variable)

| File | Content |
|---|---|
| `tracks/<file>.json` | tier A: one compact track per activity |
| `manifest.json` | `[size, mtime, ALGO_VERSION]` per workout file, and whether it has GPS |
| `index.json` | tier B: segments, routes, pending references, efforts with metrics and weather, the build's weather call counts |
| `names.json` | renames, keyed by segment / route id |
| `weather/<lat>_<lon>_<date>.json` | one Open-Meteo archive day per 0.25° cell, every effort point in it (see Weather) |

`ALGO_VERSION` 3 (per-interval peaks) makes every tier-A stamp stale, so the
first build after upgrading parses every file again; kept points, climbs and
therefore segment ids are unchanged by it.

A second server on the same machine (e.g. a worktree) must set
`WKO5COACH_ROUTES_DIR`: two builds of different versions sharing one index
would each find the other's version and rebuild it.

## Compact track (tier A)

From the raw `.wko4` channels (read with `read_wko4` directly, not through
`Dataset.wko4`'s parse cache; approved data corrections applied to HR, power,
speed):

- **Kept points** are raw samples at ≥ 25 m spacing along the GPS path, plus the
  first / last valid fix and every climb / descent endpoint. (0, 0) and
  non-finite fixes are skipped. Because kept points are raw samples, anything
  measured between two of them is an exact slice of the raw data.
- **Elevation** is `_elevation` (smoothed) else `elevation`; a gap takes the
  last valid value before it (the first valid value at the very start).
- **Distance** is the device's `elapseddistance`, else cumulative GPS. Speed
  comes from the device, else from distance.
- **Cumulative sums** at each kept sample k cover raw samples 1..k with
  dt = t_k − t_(previous valid): moving seconds; Σhr·dt and Σdt over moving
  samples with HR; the same for power; Σtemperature·dt over all samples with
  temperature; Σ hrTSS rate·dt over all samples with HR.
- **Per-interval peaks** (`interval_peaks`) at each kept point k, over raw
  samples idx[k−1]+1 .. idx[k] (the slice the sums use): `mhr` max HR;
  `mp30` max trailing 30 s power ending in the interval; and for an effort that
  starts at k, `p30k` / `p30h` (where its first whole 30 s window can end, and
  the max from there to that interval's end). Power only when the file has it.
- **Moving** = speed > the sport's threshold (`wko5_time.MOVING_SPEED_KMH`,
  1 mph on foot) and the sample does not follow a gap > 60 s — the
  `achievements.moving_mask` rule.
- **Climbs** = `climbs.detect_climbs` (≥ 80 m gain, ≥ 3 %, moving mask);
  **descents** = the same on the negated elevation.

Sport families: foot (run, walk, hiking, other) and bike. Treadmill, indoor
and swim have no family and are skipped. Matching only happens within a family.

## Detection

Thresholds (`route_match.py`): step 25 m, on-path tolerance 30 m, endpoint
tolerance 60 m, overlap ≥ 0.8, effort length within 0.7–1.4 × the reference.

### Methods

- **Overlap ratio** — fraction of one polyline's points within 30 m of the
  other polyline (point-to-segment distance). Mutual overlap = the minimum of
  both directions, so a short path does not match a long one containing it.
- **Discrete Fréchet distance** — Eiter & Mannila, *Computing Discrete Fréchet
  Distance*, Tech. Report CD-TR 94/64, TU Wien, 1994:
  `c(i,j) = max(d(p_i,q_j), min(c(i−1,j), c(i−1,j−1), c(i,j−1)))`, result
  `c(n−1,m−1)`. Curves over 150 points are thinned evenly first. Stored per
  effort as `frechet_m` (match quality; shown as 「偏 N m」 when > 60 m). It is
  **not** the accept gate: one GPS spike inflates it on a perfect match.
- **Effort search** (`find_efforts`) — every run of track points within 60 m
  of the reference start gives its closest point as a start; the first later
  point closest to the reference end whose path length is 0.7–1.4 × the
  reference and whose mutual overlap with the reference is ≥ 0.8 ends the
  effort. Several traversals in one activity are all kept (repeats).
  Direction is inherent: a reverse traversal meets the end first.
- **Common stretches** (`common_runs`) — walk track a, pairing each point with
  a point of b within 30 m just ahead of the previous pairing (b's index
  increases: same direction); ≤ 2 unpaired points are tolerated; stretches
  ≥ 500 m are kept.
- **In-order projection** (`along_residual`) — each point projects onto the
  next 12 reference segments ahead of the previous projection; off-path points
  may jump further ahead only by ≤ 1.5 × the distance the track itself covered
  + 100 m. Used for the route direction test and the comparison axis.

### Order (`routes.detect`)

Tracks are processed oldest first. For each track:

1. match it against every known segment whose start is within ~1 km of it;
2. each of its climbs / descents not already covered by one of its efforts
   (interval overlap ≥ 0.8 along the track: shared length ÷ longer range)
   becomes a new segment (min 200 m);
3. the parts of it not covered by any effort (≥ 500 m, padded 10 points ≈
   250 m) are compared with every processed track sharing ≥ 5 100 m cells;
   each common stretch is placed on the **older** track of the pair and becomes
   a new segment unless an effort on that track covers it.

A new segment is matched at once against every processed track. The oldest
traversal is the reference; the id is `s` + sha1(kind|file|raw start|raw end)[:10].

**Routes**: an activity joins the route whose reference it matches best —
mutual overlap ≥ 0.8, start and end within 200 m, and ≥ 80 % of its points
within 60 m of their in-order projection (same direction). Otherwise it starts
a route; id `r` + sha1(route|file|0|raw end)[:10].

### What is listed (`_finish`)

- Segments / routes done by ≥ 2 distinct activities.
- A segment ≥ 80 % inside a longer segment done by exactly the same activities
  is dropped (a climb / descent only by one of its kind; a stretch by any).
- A stretch whose activities are a route's members and whose path is that
  route is dropped.
- Stretch suppression: climbs / descents are kept; stretches in order of
  activities × length (ties by id), each dropped if ≥ 50 % of it lies on an
  already kept segment. One busy path otherwise yields many overlapping pieces.
- Dropped and single-activity segments stay in `index.json` as `pending`, so an
  incremental build sees the same references as a full one.

### Incremental builds and stability

`Builder.build` parses only new / changed files (tier A stamps), drops efforts
of removed / changed files, and runs `detect` with the old tracks as already
processed — the same result as a full build over the same tracks (tested).
Existing ids and references never change. `POST /rebuild {"full": true}`
recomputes everything; a new segment that is the same path as an old one
(same kind, ends within 60 m, mutual overlap ≥ 0.8) takes the old id, so
renames and links survive (`carry_over`).

## Metrics (per effort, `effort_metrics`)

All slices of raw samples between the effort's first and last kept point;
matches `climbs._measure` on the same sample range (tested).

| Field | Definition |
|---|---|
| `elapsed_s` | t(end) − t(start) |
| `moving_s` | moving seconds (rule above) |
| `moving_share` | moving ÷ elapsed; < 0.5 is flagged ⚠ (slow steep climbing falls under 1 mph and is not "moving", so moving-time VAM overstates) |
| `dist_km` | device distance difference |
| `gain_m` | net elevation change end − start |
| `pace_min_km` | moving time ÷ distance |
| `vam` | gain ÷ moving time × 3600 (climbs.py), up segments only |
| `descent_rate` | −gain ÷ moving time × 3600, down segments only |
| `avg_hr`, `avg_power` | time-weighted over moving samples |
| `hr_per_100m` | Σhr·dt ÷ 60 ÷ gain × 100 (climbs.py) |
| `hr_vam`, `power_vam` | avg HR (W) ÷ VAM × 1000, up segments only |
| `hrtss`, `hrtss_share` | WKO5 hrTSS (`wko5_hr.hr_tss`, verified 1030/1030 against WKO5) on the slice ÷ the same over the whole activity. LTHR is `Dataset.sport_setting("thr")` of the engine config in use — outside parity mode that is the plan's own test value (e.g. 155 vs WKO5's 160), so the totals differ from WKO5's stored hrTSS while the share uses one LTHR on both sides |
| `max_hr` | max HR over raw samples idx[i0]+1 .. idx[i1], all samples with HR (moving or not) = max of the kept points' `mhr` over i0+1 .. i1 — exact, not a sampled max |
| `max_p30` | max 30 s power over windows lying wholly in the effort: for each end sample e the window starts after s = the latest sample with t ≤ t_e − 30 s, and counts when s ≥ idx[i0] and power covers ≥ 80 % of it; mean = Σp·dt ÷ Σdt over samples with power. = max(`p30h`[i0], `mp30`[p30k[i0]+1 .. i1]) |
| `raw_i0`, `raw_i1` | the effort's first / last raw sample index (for independent checks) |
| `temp_c` | watch temperature sensor, time-weighted — shown as 錶溫, secondary to `wx` |
| `wx` | historical weather (next section), or null |
| `phase` | plan phase on the effort's date (`planning.phases`), at serve time |
| `overlap`, `frechet_m` | match quality |

Ranking: segments by elapsed time (Strava's segment convention), routes by
moving time. Route efforts are whole activities: no VAM (a loop's net gain is
~0); `climbing_m` is WKO5's climbing.

### Not done, and why

- **Power TSS share** — power TSS uses NP (30 s rolling, 4th power), which is not
  additive over a slice; only hrTSS is.

## Weather (`backend/engine/route_weather.py`)

Air temperature, humidity and dew point for every effort from the Open-Meteo
historical archive (`archive-api.open-meteo.com/v1/archive`, hourly
`temperature_2m, relative_humidity_2m, dew_point_2m`, `timezone=auto`), shown
with the attribution *Weather data by Open-Meteo.com (CC BY 4.0)*. It reuses
`racepower/weather.py` (client `_http_get`, `OM_ARCHIVE`, `activities_conditions`,
`ATTRIBUTION`) and `racepower/env.py` (`dew_point`, `heat_penalty_pct`)
without changing them.

- **Where / when**: the effort's point = the mean position of its kept points
  to 0.01° (~1 km) and their mean elevation to 10 m, passed as `elevation` so
  Open-Meteo downscales T and dew point to the effort's height (DEM height when
  the file has no elevation); the window = activity start (local clock) +
  t(i0) .. + t(i1).
- **Batching**: efforts are grouped by (local day, 0.25° cell of the point).
  Each group is one archive call for that single day carrying all of the
  group's points (comma-separated coordinates and elevations; the API returns
  one result per point; > 50 points split into more calls). Every day within
  30 min of the window is needed (an effort near midnight takes two). The
  cache holds one file per (cell, day) with its points; a full build makes one
  call per (day, cell) not yet cached, a new point in a cached group one call
  for the new points, a rebuild none.
- **Why the effort's own point, not the cell centre**: the first version asked
  for the cell centre and corrected T by −6.5 °C/km to the effort's elevation.
  On 小油坑 → 七星山主峰 (997 m) that gave 24.3 °C where the archive at the
  climb itself (downscaled to 970 m) says 21.7 °C — the cell centre's model
  cell is the warm basin. Asking for each point in the same call costs no
  extra calls.
- **Values**: T and RH = the mean of the hourly rows within ±30 min of the
  window (`activities_conditions`); dew point = Magnus of that T and RH
  (`env.dew_point`, the input `heat_penalty_pct` uses). `archive_elev_m` is the
  elevation the archive answered for.
- **Heat**: 熱 = Hadley sum = T °F + dew point °F (`env.heat_penalty_pct`'s x);
  `heat_pct` = its pace penalty; `hot` when the sum > 150 (≥ 4.5 % slower, Hadley's 151–160 band; > 130
  flagged 75 % of the real efforts, Taiwan's ordinary summer evening). The
  table flags hot efforts; the trend chart colours points by T (diverging blue
  ↔ gray ↔ red around 18 °C, toggle 依氣溫上色, remembered) and rings hot ones.
- **Degrading**: the index is saved before the weather phase, so a slow or
  failed fetch never holds back the efforts. A failed call is not cached; after
  5 consecutive failures the rest are skipped (counted); an empty day within 10
  days of today (the archive lags) is not cached either. A later build with no
  file changes retries what is missing. `idx.weather` = {needed, calls,
  cache_hits, failed, skipped, empty, recent, efforts, with_weather, errors,
  attribution}; the page shows the counts under the table.
- `Builder(store, weather_get=None)` has no weather (tests); the API passes the
  archive client unless `WKO5COACH_ROUTES_WEATHER=0`.

## Comparison (`GET /{id}/compare?a=&b=`)

Each effort's kept points are projected in order onto the reference path
(`along`); elapsed time, interval-average HR and power, and elevation are
interpolated every 25 m of reference distance (up to 60 m — 200 m for routes —
past an effort's own first / last projection it holds its end value: the
endpoint tolerance of the match).
Pace = Δt over the trailing 100 m. `gap_s = t_b − t_a` at the same distance
(positive: B behind); at the end it equals the elapsed difference (tested).

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/routes` | `kind` (segment/route/climb/descent/stretch), `direction` (up/down/flat), `sport`, `limit`; rows sorted by effort count then recency; `status`, `counts`, `sports` |
| GET | `/api/v1/routes/status` | build progress `{state, phase, done, total, …}` |
| POST | `/api/v1/routes/rebuild` | `{"full": false}`; returns at once, builds in a thread |
| GET | `/api/v1/routes/{id}` | detail with efforts (+ `workout` index, `phase`) |
| PATCH | `/api/v1/routes/{id}` | `{"name": ""}` clears the rename |
| GET | `/api/v1/routes/{id}/compare?a=&b=` | effort ids from the detail |
| GET | `/api/v1/routes/page` | the page |
| GET | `/api/v1/wko5/workouts/{idx}/segments` | segments / routes this activity matched, rank, Δ best |

No request waits for a build: the first request (and any request a minute
after the last check, when files changed) starts one in a daemon thread; every
endpoint answers from the last saved index. First full build on the real data
(1,098 workouts, 828 eligible, 611 with GPS): ~2.5 min; incremental with no
new file: none; full recompute from cached tracks: ~40 s.

## Page

- List: kind badge, name, length, gain, effort count, best / last time and
  date, sparkline of the last 20 times (up = faster; green = best). Filters:
  全部/路段/路線, 上坡/下坡/平路, sport. Remembered per browser.
- Detail: rename (click the title); Leaflet map with the settings-page basemap
  default and the viewer's layers (`basemaps.js`); trend (time / VAM / pace over
  date, below it HR ÷ VAM for climbs, avg HR otherwise; click a point to make
  it B); comparison (pace / HR / power / elevation vs distance, gap chart,
  hover moves A's and B's markers on the map — B's marker is where B was at
  A's elapsed time); effort table (newest 20 + A/B/best, 「顯示全部」),
  date links open the activity in the viewer (via the viewer's saved state).
- Phone (< 900 px): list and detail are separate views with a back button.
- Single-activity card in the viewer: segments matched with 「第 N 快 / M 次」
  and Δ best, linking to `/api/v1/routes/page#<id>`.

## Testing

`backend/tests/test_route_progress.py` (synthetic tracks): Fréchet against
hand-computed values; overlap one-way vs mutual; point-to-segment distance;
resampling; invalid fixes; efforts in the same direction only; repeats in one
activity; GPS offset vs a different trail; common stretches and direction;
in-order projection vs a reversed loop; metrics equal to `climbs._measure` and
`wko5_hr.hr_tss`; descents; climb + descent clustering; endpoint jitter
clustering; route direction; flat stretches; containment pruning; incremental
== full with stable ids; incremental reference on the older track; the API
compare gap and rename; max HR and max 30 s power equal a brute force over
raw samples on 43 effort ranges (with a stop, a surge, a power dropout and an
HR spike); per-effort weather and max HR served by the API equal the plain
recomputation of `backend/scripts/verify_route_weather_hr.py` (direct archive
query written out again, raw-sample max) with the network mocked, including an
effort past midnight; one call per (day, cell) and none on a full rebuild from
the cache; an offline build finishes, stops after the failure limit and a
later no-change build fills the gaps; a new point in a cached day costs one
call for that point only; the Hadley sum and hot flag; after a version bump
the old index's ids are carried over (renames kept); detection gives the same
result under three hash seeds (a guard only: the synthetic set does not show
the old seed dependence, which was found and checked on the real tracks, below).

Real-data verification (2026-09-30): per-effort metrics of 小油坑 → 七星山主峰
(13 efforts) recomputed from raw samples by an independent plain-loop script
agreed on all fields for 13/13; the same plain-loop whole-activity hrTSS at
WKO5's LTHR equals WKO5's stored hrTSS on 13/13; a route compare (122-effort
loop) ends with gap = the elapsed difference; 14 segments / routes drawn with every matched
effort's own path (≈ 720 efforts) showed no effort off the reference path; for
5 climbs every activity passing within 60 m of the start and later the end was
matched (0 missed).

Real-data verification of weather and peaks (2026-09-30, v3, built into a
separate `WKO5COACH_ROUTES_DIR`): the first build asked for 512 (day, cell)
groups (1,141 effort points) in 512 archive calls (0 failed, ~6 min with the
track parse) and gave weather to 1,654 / 1,654 efforts; a full rebuild after
that made 0 calls (512 / 512 from the cache). For
小油坑 → 七星山主峰 (13 efforts) `backend/scripts/verify_route_weather_hr.py`
recomputed max HR from the raw `.wko4` samples: 13/13 equal; max 30 s power by
brute force: 12/12 equal (the first run had 11/12 — a double rounding of the
stored peaks, fixed); the 2026-08-22 effort's weather by a direct single-point
archive query: T 21.7 °C, RH 91 %, dew 20.1 °C, Hadley 139, equal to the API;
querying the unrounded raw-sample mean point instead gives the same T (Δ 0.00 °C).
Monthly mean effort temperature runs 15.6 °C (Jan) to 27.9 °C (Jul / Aug); the
watch sensor reads 1.8 °C above the archive on average (409 efforts with both).
46 % of efforts are hot (Hadley > 150). Across the version bump 186 / 187 ids
and the one rename survived.

While checking, two full builds from identical tracks gave different stretches
(1,473 vs 1,505 efforts): `detect` iterated sets of file names, whose order
follows Python's per-process hash seed. The candidate loops are now sorted
(by activity start, then file); two builds with different `PYTHONHASHSEED`
are identical.

## Domain Model

### Bounded Context
- **Context Name**: Route Progress（路線進步）
- **Domain Layer**: Supporting Domain
- **Parent Module**: N/A (reads the WKO5 dataset of `wko5-engine.spec.md`; linked from the workout viewer)

### Ubiquitous Language
| Term | Definition |
|---|---|
| Track (compact track) | An activity's raw samples at ≥ 25 m spacing with cumulative moving time, HR, power, temperature and hrTSS sums |
| Segment | A repeated path: a climb, a descent, or a stretch ≥ 500 m, defined by its reference effort |
| Climb / descent | A segment seeded by `detect_climbs` on the elevation / negated elevation; direction is part of its identity |
| Stretch | A segment seeded by a same-direction common run of two activities |
| Route | A set of whole activities overlapping each other ≥ 80 %, same start / end (200 m) and direction |
| Reference | The oldest traversal, whose path defines the segment or route; never replaced incrementally |
| Effort | One traversal of a segment (or one member activity of a route) with its metrics |
| Overlap ratio | Share of one path's points within 30 m of the other; mutual = min of both ways |
| Fréchet distance | Discrete Fréchet (Eiter & Mannila 1994) between an effort and its reference, match quality |
| Pending | A segment kept as a reference but not listed (one activity, or redundant) |
| VAM | Net gain ÷ moving time × 3600 (climbs.py) |
| HR ÷ VAM | Average moving HR ÷ VAM × 1000: heart rate paid per unit of climbing speed |
| hrTSS share | Segment hrTSS ÷ activity hrTSS, WKO5 hrTSS formula |

### Domain Events
None. Builds are triggered by requests (or `POST /rebuild`); there are no emitters or subscribers.

## Change History

| Date | Type | Feature SRS | Summary |
|------|------|-------------|---------|
| 2026-09-30 | feature | — | Initial: automatic segments / routes, effort metrics, 路線 page, viewer card |
| 2026-09-30 | feature | — | Per-effort historical weather (Open-Meteo archive, batched per day × 0.25° cell, cached), Hadley heat flag, trend coloured by temperature; max HR and max 30 s power per effort (ALGO_VERSION 3); deterministic detection; ids carried over across a version bump; independent verification script |
