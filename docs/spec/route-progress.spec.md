# Module Spec: route-progress

> Last Updated: 2026-10-08 · Status: implemented (feat/route-progress, feat/routes-weather-hr, fix/routes-dedup-thumbs)

## Overview

Finds repeated **segments** (climbs, descents, shared stretches) and whole
**routes** in the athlete's own GPS history, with no map data and no manual
setup — like Strava segments / Matched Runs, detected automatically. Each
traversal is an **effort** with time, VAM, heart rate, power and hrTSS share,
so progress on the same hill or loop can be read effort by effort and any two
efforts compared along the segment.

User request: 「同一條路線的進步追蹤能自動產出嗎，例如判斷歷史上有幾段重疊的路線，就把重疊的地方撈出來做出差異比對」

## Architecture

| Layer | File | Role |
|---|---|---|
| Geometry | `backend/engine/algorithms/route_match.py` | resampling, point-to-path distance, overlap ratio, discrete Fréchet, common stretches, effort search, in-order projection |
| Engine | `backend/engine/routes.py` | compact tracks, detection, effort metrics, names, on-disk store, background builder |
| API | `backend/api/routes.py` | `/api/v1/routes…`, `/api/v1/wko5/workouts/{idx}/segments` |
| Page | `backend/static/routes.html` | 路線 page: list, detail (map, trend, comparison, effort table) |
| Card | `backend/static/segments_card.js` | single-activity card in the viewer (one `<script>` tag in `wko5_viewer.html`) |
| Map layers | `backend/static/basemaps.js` | basemaps / overlays + the settings default, the tile-error hint and the route drawing (halo, line, start / finish), shared by the viewer, this page and the race calculator; layer names and hints come from the i18n `common.map.*` keys (`backend/static/basemaps.js:14-15`, SP-78) |

The existing `algorithms/routes.py` (100 m cell Jaccard, used by the
achievements page) is unchanged.

### Storage (root: `RouteStore(root)`, else `routes.HOME` when set (tests), else the `WKO5COACH_ROUTES_DIR` environment variable, else the current tenant's shared `routes/` folder — `tenancy.shared_path("routes")`, resolved per call by `routes.home()`)

| File | Content |
|---|---|
| `tracks/<file>.json` | tier A: one compact track per activity |
| `manifest.json` | `[size, mtime, ALGO_VERSION]` per workout file, and whether it has GPS |
| `index.json` | tier B: segments, routes, pending references, efforts with metrics and weather, the build's weather call counts |
| `names.json` | renames, keyed by segment / route id (see Names) |
| `weather/<lat>_<lon>_<date>.json` | one Open-Meteo archive day per 0.25° cell, every effort point in it (see Weather) |
| `activity_weather.json` | per-activity heat exposure for the heat-acclimation index (see Weather) |

`ALGO_VERSION` 3 (per-interval peaks) makes every tier-A stamp stale, so the
first build after upgrading parses every file again; kept points, climbs and
therefore segment ids are unchanged by it. The index has its own
`INDEX_VERSION` (4: route clustering, merged segments): bumping it rebuilds
the index from the cached tracks without parsing any file, ids carried over.

A second server on the same machine (e.g. a worktree) must set
`WKO5COACH_ROUTES_DIR`: two builds of different versions sharing one index
would each find the other's version and rebuild it.

### Names

Every build gives each segment and route an `auto_name` (`routes.auto_name`,
`backend/engine/routes.py:1516-1539`): the nearest 百岳 / 小百岳 from
`backend/data/baiyue.json` (`PEAKS_PATH`, `backend/engine/routes.py:77`) within 1 km
(`PEAK_NAME_RADIUS_M`, `backend/engine/routes.py:82`; `nearest_peak`, `backend/engine/routes.py:1505-1513`)
of the landmark end — a climb's top, a descent's start, a route's highest
point, a stretch's start — falling back to the other end (a route: its start),
followed by the kind, the length and the gain (爬坡 / 下坡 always, a route or a
stretch only when |gain| ≥ 50 m); without a peak, the kind, length and gain
alone (e.g. 「爬坡 1.2 km ↑180 m」). A rename in `names.json` takes precedence
wherever a name is served (`backend/api/routes.py:195`, `backend/api/routes.py:361`); renaming to
an empty string clears it and the `auto_name` returns (`backend/api/routes.py:383`).

## Compact track (tier A)

From the raw `.wko4` channels (read with `read_wko4` directly, not through
`Dataset.wko4`'s parse cache; approved data corrections applied to HR, power,
speed). Without a WKO5 folder (`datasource.wko5_available` false: a COROS /
TrainingPeaks-only runner) the source is the synced FITs of the charts'
Dataset instead: `read_track(…, parsed=ds.wko4(idx))` takes the already
parsed file, which carries the same GPS channels:

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

### Routes (`cluster_routes`, every build, over all tracks)

Until 2026-10-01 an activity joined the route whose reference (its oldest
run) it matched: mutual overlap ≥ 0.8, start **and** end within 200 m, same
direction. On the real data that listed one loop as several routes: a loop
started at another point, run the other way round, or a first run with its own
car-park spur all started new routes, and a run over part of a route became a
route of its own (43 listed routes; the 143-run loop was 7 of them).

Now, start- and direction-free:

1. **Length share** (`route_match.cover_share`): the share of track a's length
   inside the 30 m buffer of track b's polyline, on a resampled every 25 m
   (`resample_even`; point-to-segment distance). One way, so containment shows.
   Pairs are bounded first: the share of a's 25 m points inside b's *halo*
   (b's 100 m cells and their 8 neighbours; a point within 30 m of b is within
   42.5 m of one of b's 25 m points, so inside it) is an upper bound, and a pair
   whose bound is < 0.8 either way is never measured. Results are cached by
   file pair in the builder, so an incremental build measures only its new
   tracks' pairs.
2. **Same route**: both shares ≥ 0.8 (the old mutual-overlap threshold).
3. **Clusters**, leader style (lazy greedy): the track with the most
   unassigned same-route neighbours (ties: higher summed share, older, file)
   takes them all; every member is within the threshold of its leader, so a
   chain of slowly drifting runs cannot grow into one route.
4. **Canonical path** = the maximal common part: the reference's kept points
   from the first to the last 25 m point that more than half of the members
   pass within 30 m (`ROUTE_COMMON` 0.5, 推估). Spurs of single runs trim away.
   The path stored in the index and drawn on the map is thinned evenly to at
   most 400 points (`ROUTE_MAX_POINTS`, `backend/engine/routes.py:851`, `:997`).
   Clusters whose canonical paths are the same route (both shares ≥ 0.8) are
   merged into the larger, until none is (≤ 4 passes).
5. **Reference / id**: the previous index's reference when it is in the
   cluster (the id and the comparison axis stay), the largest old route first;
   other old listed routes whose reference landed in the cluster become its
   `aliases`. Otherwise the leader, id `r` + sha1(route|file|0|raw end)[:10].
6. **Direction** per member (`direction_shares`, start-free): for each
   member point within 30 m of the canonical path, its heading against every
   canonical segment within 30 m — forward when one is within 60° (cos ≥ 0.5),
   reverse when one is within 60° of the opposite. ≥ 0.8 forward = `same`
   (an out-and-back, whose legs lie on each other, is `same`), else ≥ 0.8
   reverse = `reversed`, else `mixed`. Reversed runs stay in the route; ranks
   are per direction (the climbs differ) and the comparison needs one.
7. **Parts** (`_link_parts`): a cluster lying ≥ 0.8 (`ROUTE_PART`, 推估)
   inside a longer repeated route's canonical path, that route not ≥ 0.8 inside
   it, and at most 0.9 × its length (`ROUTE_PART_LEN`, 推估: two variants of one
   length sharing 80 % are siblings) gets `parent`. A single run there is that
   route's `partials` entry (not a route); several are a sub-route, listed under
   the parent and counted apart (`counts.sub_route`).

Single runs that are no part of anything stay in `pending_routes`.

### What is listed (`_finish`)

- Segments / routes done by ≥ 2 distinct activities.
- A segment ≥ 80 % inside a longer segment done by exactly the same activities
  is dropped (a climb / descent only by one of its kind; a stretch by any).
- The other way round: a longer segment whose activities are ≥ 80 %
  (`CHAIN_PIECE_ACTS`, 推估) among those of a shorter one of its kind lying
  ≥ 80 % on it, done by more activities, is dropped — the same hill or path
  with its ends detected a little differently is listed once, as the part
  more activities do.
- A stretch lying ≥ 0.8 (`STRETCH_ON_ROUTE`, 推估) inside a repeated route's
  canonical path with ≥ 0.8 (`STRETCH_ROUTE_ACTS`, 推估) of its activities being
  that route's runs (members, partials, sub-route runs) is folded into the
  route (`route.stretches`; its id is an alias of the route).
- Stretch suppression: climbs / descents are kept; stretches in order of
  activities × length (ties by id), each dropped if ≥ 50 % of it lies on an
  already kept segment. One busy path otherwise yields many overlapping pieces.
  A folded stretch takes its place in that order and suppresses as before,
  without being listed.
- **Chains** (`_merge_chains`): two listed stretches sharing ≥ 0.6 of their
  activities (Jaccard, `CHAIN_JACCARD`, 推估) whose efforts on the oldest
  activity doing both overlap or are ≤ 250 m apart (`CHAIN_GAP_M`, 推估) are
  links of one corridor. Per chain the host is the activity doing most links;
  their efforts on it merged where they touch give one stretch (`derived`,
  `merged_from`), matched against the chain's activities; links with ≥ 80 % of
  their activities on it are dropped. Kept only when it replaces ≥ 2 links.
  Derived stretches are rebuilt by every build and never loaded as
  references, so incremental and full builds agree.
- Dropped and single-activity segments stay in `index.json` as `pending`, so an
  incremental build sees the same references as a full one.

### Incremental builds and stability

`Builder.build` parses only new / changed files (tier A stamps), drops efforts
of removed / changed files, and runs `detect` with the old tracks as already
processed — the same segments as a full build over the same tracks, and the
same route memberships (tested). Existing segment ids and references never
change; a route keeps its id while its reference is in it. `POST /rebuild {"full": true}`
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
| `hrtss`, `hrtss_share` | WKO5 hrTSS (`wko5_hr.hr_tss`, verified 1030/1030 against WKO5) on the slice ÷ the same over the whole activity. LTHR is `Dataset.sport_setting("thr")` of the engine config in use — outside parity mode that is the plan's own test value (which can differ from WKO5's), so the totals differ from WKO5's stored hrTSS while the share uses one LTHR on both sides |
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
`temperature_2m, relative_humidity_2m, dew_point_2m, precipitation`
(`HOURLY`, `backend/engine/route_weather.py:46`), `timezone=auto`), shown
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
  for the new points, a rebuild none. Calls go 3 at a time (`WORKERS`, well
  under Open-Meteo's 600 calls / min) and at most 2,000 per build (`MAX_CALLS`,
  `backend/engine/route_weather.py:53`, `backend/engine/route_weather.py:55`,
  `backend/engine/route_weather.py:207-212`); the rest are counted as skipped
  and wait for the next build.
- **Why the effort's own point, not the cell centre**: the first version asked
  for the cell centre and corrected T by −6.5 °C/km to the effort's elevation.
  On a climb to a ~1,000 m summit that gave a value ~2.6 °C warmer than the
  archive at the climb itself (downscaled to its height) — the cell centre's
  model cell was a warm basin. Asking for each point in the same call costs no
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
- **Per-activity heat exposure** (`activity_weather=True`, the app's builder):
  after the effort weather (skipped when that pass had failures),
  `route_weather.fill_activities` asks for one point per GPS activity (its mean
  position and elevation, same batching and cache) and writes
  `activity_weather.json`: moving minutes, `hot_min` (moving minutes ×
  `heat.minute_weight` of each hour's Hadley sum), moving-weighted T / RH /
  Hadley and the max. Read by the heat-acclimation index (`racepower.spec.md`)
  and the workout review's activity temperature. A no-change build fills it
  when the file is missing.
- **Rain during an activity** (SP-299): `precipitation` rides in the same
  archive call (`HOURLY`), so it costs no extra call; each activity row also
  has `start` and `rain_mm` = the sum of the hourly rows (Open-Meteo: the
  preceding hour's total) whose hour overlaps the activity's first → last
  sample, `None` when an overlapping hour has no value — a build never refetches
  a day cached before precipitation was asked, it just has no rain; the one-time
  backfill below fetches those of the last 12 months once. The rain is
  looked up by the activity's file, else by its start within ±3 min under
  another source's file name (`rain_by_activity`, an `activity_key.ByStartDict`,
  `backend/engine/route_weather.py:428-438`). The 活動編輯
  page (`GET /activities` → `rain_mm`, `rain_hint_mm`) shows 「這次活動期間下過雨
  （N mm），要標成濕路嗎？」 with a 「標成濕」 button while the 路況 is 未標 and
  `rain_mm` ≥ `activity_tags.RAIN_HINT_MM` (1 mm, 推估), and only on 越野跑 / 登山健行
  (`rain_kind`, workouts.spec.md › 路況); it never marks anything itself.
- **Rain backfill** (SP-299 follow-up, owner 2026-10-07; `backend/engine/rain_backfill.py:44`):
  once, the activities of `activity_weather.json` from the last 365 days whose rain is unknown
  get their archive days asked again — the same point / (cell, day) grouping as the build
  (`activity_point`, `backend/engine/route_weather.py:398`) through the same `Fetcher` and cache,
  but a cached point without precipitation counts as missing (`refetch` + `has_rain`,
  `backend/engine/route_weather.py:149`, `backend/engine/route_weather.py:214`), one call at a
  time with `PACE_S` = 1 s between calls (`backend/engine/route_weather.py:233`). Each call
  replaces its cache entries as it lands, then only `rain_mm` is written into the rows still
  without it (re-read just before the write). Idempotent: a day once fetched with precipitation
  is never asked again, so an interrupted run resumes where it stopped. Started by the
  scheduler loop 15 min after the app starts (`backend/sync/scheduler.py:123-131`,
  `api/rain_backfill.tick`, `backend/api/rain_backfill.py:72`) while no routes build runs;
  marks itself done in the setting `weather.rain_backfill` (`backend/settings/repository.py:75`);
  a failed / cut-short run is retried 6 h later and given up after 5 attempts
  (`next_state`, `backend/api/rain_backfill.py:56`); no `activity_weather.json` yet = waiting.
  Never in tests (`WKO5COACH_NO_RAIN_BACKFILL`, conftest), the demo, or with
  `WKO5COACH_ROUTES_WEATHER=0`.

## Comparison (`GET /{id}/compare?a=&b=`)

Each effort's kept points are projected in order onto the reference path
(`along`); elapsed time, interval-average HR and power, and elevation are
interpolated every 25 m of reference distance (up to 60 m — 200 m for routes —
past an effort's own first / last projection it holds its end value: the
endpoint tolerance of the match).
Pace = Δt over the trailing 100 m. `gap_s = t_b − t_a` at the same distance
(positive: B behind); at the end it equals the elapsed difference (tested).
Routes: both runs must have the same direction (a reversed pair walks the
reference backwards) and start within 200 m of the reference's start; else
400 with the reason (a loop started elsewhere has no common distance axis).

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/routes` | `kind` (segment/route/climb/descent/stretch), `direction` (up/down/flat), `sport`, `limit`; rows sorted by effort count then recency, sub-routes right under their parent (`depth`); each row has `thumb` (below), `n_reversed`, `n_partials`, `parent`; `status`, `counts` (sub-routes as `sub_route`), `sports` |
| GET | `/api/v1/routes/status` | build progress `{state, phase, done, total, …}` |
| POST | `/api/v1/routes/rebuild` | `{"full": false}`; returns at once, builds in a thread |
| GET | `/api/v1/routes/{id}` | detail with efforts (+ `workout` index — by the effort's file, else the current source's activity with the nearest start (`activity_key.ByStartDict`, ±3 min), `phase`, `dir`), `parent`, `sub_routes`, `partials`; an id merged away (route alias, folded stretch, chain link) answers with the item holding it now |
| PATCH | `/api/v1/routes/{id}` | `{"name": ""}` clears the rename |
| GET | `/api/v1/routes/{id}/compare?a=&b=` | effort ids from the detail |
| GET | `/api/v1/routes/page` | the page (`render_page("routes")`, localized) |
| GET | `/api/v1/wko5/workouts/{idx}/segments` | segments / routes this activity matched, rank, Δ best; the activity's file in the route index is found by start when the current source names it otherwise |

No request waits for a build: the first request (and any request a minute
after the last check, when files changed) starts one in a daemon thread; every
endpoint answers from the last saved index. First full build on the real data
(1,098 workouts, 828 eligible, 611 with GPS): ~2.5 min; incremental with no
new file: none; full recompute from cached tracks: ~40 s.

## Page

- List: path thumbnail, kind badge, name, length, gain, effort count, best /
  last time and date, sparkline of the last 20 times (up = faster; green =
  best). Filters: 全部/路段/路線, 上坡/下坡/平路, sport. Remembered per browser.
  Sub-routes indented under their parent (⊂).
- **Thumbnail** (`route_match.thumbnail`, server side, 64 × 40): local
  equirectangular about the path's mid latitude (x = R·Δλ·cos φm, y = R·Δφ —
  on a few km the shape Leaflet's Web Mercator shows), one scale for both axes
  (the longer side fills the box, the other centred), y flipped (north up),
  Douglas–Peucker (Cartographica 10(2), 1973) at 0.35 px so the line stays
  within a third of a pixel of every point; green dot = start. Until
  2026-10-01 the list had **no** shape at all: the only graphic on a row was
  the time sparkline (the last 20 times, inverted so up = faster), which looks
  like a small track but is a time series — the "thumbnail that does not match
  the GPX".
- Detail: rename (click the title); Leaflet map with the settings-page basemap
  default and the viewer's layers, the route drawn by `MapLayers.track` (`basemaps.js`, SP-41); trend (time / VAM / pace over
  date, below it HR ÷ VAM for climbs, avg HR otherwise; click a point to make
  it B); comparison (pace / HR / power / elevation vs distance, gap chart,
  hover moves A's and B's markers on the map — B's marker is where B was at
  A's elapsed time); effort table (newest 20 + A/B/best, 「顯示全部」),
  date links open the activity in the viewer (via the viewer's saved state).
  Under the title: parent route, sub-routes, runs over only a part (dates
  link to the viewer), reversed-run count, merged pieces; reversed / mixed
  runs carry a 反向 / 混合 tag in the table, rank among their own direction,
  and the default A / B pair is of the main direction.
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
Route clustering and dedup (2026-10-01): a reversed loop joins the route as
`reversed`; a loop started at another corner, an out-and-back walked from the
far end, and runs with their own car-park / cool-down spurs are one route,
whose canonical path is the shared 6 km loop; a run over half the loop is a
partial, two such runs a sub-route; old route ids carry over and a merged one
becomes an alias; start-free direction shares; a stretch that is a route is
folded into it; a longer climb whose activities all do a more-done inner one
is dropped; a chain of two stretches merges into its 3 km common part
(derived, not a reference); incremental and full builds give the same route
memberships; the API rows carry thumbnails, ranks are per direction, a merged
id opens its route and compare refuses runs of different direction.
Thumbnails: an L (1 km east, 1 km north) at 24 °N has equal legs on screen
(raw degrees would make the east leg 1.095 ×), east right and north up, the
box filled by one scale; a 3 × 1 km loop stays 3 : 1 and every point lies
within 0.5 px of the simplified line.

Real-data verification (2026-09-30): per-effort metrics of one repeated mountain
climb (13 efforts) recomputed from raw samples by an independent plain-loop script
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
the same climb (13 efforts) `backend/scripts/verify_route_weather_hr.py`
recomputed max HR from the raw `.wko4` samples: 13/13 equal; max 30 s power by
brute force: 12/12 equal (the first run had 11/12 — a double rounding of the
stored peaks, fixed); one effort's weather by a direct single-point archive
query: T, RH, dew point and Hadley sum equal to the API;
querying the unrounded raw-sample mean point instead gives the same T (Δ 0.00 °C).
Monthly mean effort temperature spans ~12 °C between winter and summer; the
watch sensor reads 1.8 °C above the archive on average (409 efforts with both).
46 % of efforts are hot (Hadley > 150). Across the version bump 186 / 187 ids
and the one rename survived.

While checking, two full builds from identical tracks gave different stretches
(1,473 vs 1,505 efforts): `detect` iterated sets of file names, whose order
follows Python's per-process hash seed. The candidate loops are now sorted
(by activity start, then file); two builds with different `PYTHONHASHSEED`
are identical.

Real-data check of the dedup (2026-10-01, a copy of the 612 cached tracks,
the live store untouched): listed items 187 → 145 — routes 43 → 33 (24
top-level + 9 sub-routes), stretches 77 → 54, climbs 32 → 29, descents
35 → 29. The 143-run 5.3 km loop had been 7 routes (114 + 12 + 11 + 3 + 2 + 2
+ 2 runs, the 3.8 / 4.5 / 4.6 km "routes" being the same loop with other
start points or spurs); the 4.2 km loop 4 (122 + 3 + 3 + 2); the 4.7 km route
3 (13 + 12 + 11). Its six busiest stretches (0.6–3.4 km, 111–156 activities)
were pieces of those two loops and are now folded into them. 13 stretches
folded into routes, 5 merged in 4 chains, the rest dropped as longer variants
or overlaps. Route clustering on all 612 tracks: ~25 s (19 k pairs
measured after the halo bound), cached per pair for incremental builds; a full
detect ~35 s. Thumbnails of 6 items (4 routes, a climb, a merged stretch)
drawn next to their raw kept points in Web Mercator: same shape, aspect and
orientation.

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
| Route | Whole activities each lying ≥ 80 % (length) inside the leader's 30 m buffer and it inside theirs — any start point, any direction |
| Canonical path | A route's maximal common part: the reference's stretch more than half of the runs pass |
| Partial / sub-route | One run / several runs lying ≥ 80 % inside a longer repeated route (≤ 0.9 × its length) — linked to it, not a route of its own |
| Derived stretch | The common part of a chain of listed stretches, rebuilt each build |
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

## Open Questions

- [ ] Link with map.yichlin (pull / push GPX, race-day weather, pace → its itinerary); which direction first is not decided（SP-47，Backlog）——尚未實作

## Change History

| Date | Type | Feature SRS | Summary |
|------|------|-------------|---------|
| 2026-09-30 | feature | — | Initial: automatic segments / routes, effort metrics, 路線 page, viewer card |
| 2026-09-30 | feature | — | Per-effort historical weather (Open-Meteo archive, batched per day × 0.25° cell, cached), Hadley heat flag, trend coloured by temperature; max HR and max 30 s power per effort (ALGO_VERSION 3); deterministic detection; ids carried over across a version bump; independent verification script |
| 2026-10-01 | bugfix | — | One route per path: clustering by length share (start / direction free), canonical common part, partials / sub-routes, reversed runs ranked apart; stretches folded into routes, longer variants dropped, chains merged; real path thumbnails (the row graphic was a time sparkline); INDEX_VERSION 4 |
| 2026-10-04 | code-sync | N/A | Store root per tenant (`routes.home()`); tracks from synced FITs without a WKO5 folder; effort → workout index matched by start across sources; per-activity heat exposure (`activity_weather.json`) documented; page via `render_page` |
| 2026-10-04 | feature | SP-41 | The route on the detail map is drawn by the shared `MapLayers.track` (`basemaps.js`), the same as the activity map and the race calculator's course map |
| 2026-10-06 | feature | SP-299 | Activity weather also stores the rain during the activity (`precipitation` in the same call and cache); 活動編輯 hints 「要標成濕路嗎？」 at ≥ 1 mm (推估) while 路況 is 未標 |
| 2026-10-08 | code-sync（SP-78, SP-41） | N/A | Documented auto names (nearest 百岳 within 1 km, renames take precedence), the weather call cap (2,000 per build, 3 at a time), the canonical path thinned to 400 points, rain matched by file or start ±3 min, map layer strings via i18n `common.map.*`; `precipitation` added to the hourly list; Open Questions from SP-299 / SP-47 |
| 2026-10-08 | SP-299 follow-up | owner decision 2026-10-07 (ticket SP-299) | The rain hint only on 越野跑 / 登山健行 (`activity_tags.rain_kind`); the one-time rain backfill of the last 12 months (`engine/rain_backfill.py`, `api/rain_backfill.py`, setting `weather.rain_backfill`): `Fetcher` gains `refetch` / `workers` / `pace_s`, `activity_point` shared with `fill_activities`. Tests `test_rain_backfill.py` (HTTP faked) |
