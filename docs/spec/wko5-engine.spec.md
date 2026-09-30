# Module Spec: wko5-engine

> **Last Updated**: 2026-09-30
> **Status**: Active
> **Domain Layer**: Core Domain

## Overview

Reads WKO5's own binary files directly, re-derives every training metric with
algorithms verified bit-for-bit against WKO5's stored values, and evaluates
WKO5's chart expression language so any `.wko5chart` view — or a view the
athlete writes as JSON — renders from the same data.

It runs in two modes. **Parity** reproduces WKO5 exactly, so every number can
be checked against WKO5 on screen; it is the correctness proof. **Own
formulas** applies this project's mountain-sport adjustments where WKO5
(cycling-first) is weak. Bad data is detected automatically but only corrected
after the athlete approves, as a reversible overlay that never touches the
source files.

## Architecture

```
 .wko5chart ─┐                          ┌─ render JSON ─ /api/v1/wko5 ─ viewer
 views/*.json┤                          │
             ├─ view defs ─ parser ─ evaluator
 .wko4 ──────┤                  ▲        │
 .wko5athlete┤                  │        ▼
 Cache5 ─────┴─ file readers ─ dataset (metrics, TSS policy, corrections)
 FIT ────────── fit_to_channels ┘                  ▲
                                          algorithms (verified)
```

Five layers, each depending only on the ones below it:

| Layer | Responsibility | Entry point |
|---|---|---|
| File readers | Decode WKO5's tagged binary encoding; FIT → WKO5-equivalent channels | `backend/files/wko5chart_reader.py:127` |
| Algorithms | Pure functions, one metric each, verified against WKO5 | `backend/engine/algorithms/wko5_power.py` and siblings |
| Dataset | One athlete: workouts, metrics, TSS policy, caches, corrections | `backend/engine/wko5expr/dataset.py:221` |
| Expression engine | Parse and evaluate WKO5's expression language | `backend/engine/wko5expr/evaluator.py:305` |
| API + viewer | Serve views, charts (through the render cache), workout samples, config, corrections; the viewer page | `backend/api/wko5views.py:156`, `backend/static/wko5_viewer.html` |

## File formats

Every WKO5 file is `b"wko" + kind + 0x1a` followed by one tagged record
(`backend/files/wko5chart_reader.py:118`). A tag is a varint of
`field_id << 3 | wire_type`:

| Wire | Payload |
|---|---|
| 0, 1 | varint |
| 2 | 8-byte little-endian double |
| 3 | length-prefixed UTF-8 string |
| 4 | length-prefixed nested record |
| 5 | length-prefixed packed blob (sample channels) |
| 6 | 4-byte little-endian float |

| File | Holds | Reader |
|---|---|---|
| `.wko5chart` | View → dashboards → charts → series expressions | `backend/files/wko5chart_reader.py:330` `read_view` |
| `.wko4` | One activity: info, ranges with WKO5's stats, sample channels, the original FIT | `backend/files/wko4_file.py:114` `read_wko4` |
| `.wko5athlete` | Settings history, workout index with per-workout metrics, PMC snapshot | `backend/files/wko5_athlete.py:104` `read_athlete` |
| `.wko5cache` | WKO5's per-workout expression results (e.g. `meanmax(power)`) | `backend/engine/wko5expr/dataset.py:165` `load_wko5_curve_cache` |

Sample channels (`backend/files/wko4_file.py:91`) are zigzag int32 delta varints divided by a
scale, or a raw float64 array when packed field 111 = 1. `0x7fffffff` and
`DBL_MAX` mean "no data". Field 119 is the channel's value before sample 0
(the distance odometer; 0 for elapsed time).

## Verified algorithms

Each is a pure function in `backend/engine/algorithms/`, recomputed from raw
samples and compared against what WKO5 itself stored.

| Metric | Module | WKO5 field | Result |
|---|---|---|---|
| Normalized Power, tssduration | `wko5_power.py` | 4219, 4248 | **359/359 bit-exact** |
| hrTSS, hrIF | `wko5_hr.py` | 4235, 4236 | **1030/1030** |
| Mean-max curve | `wko5_meanmax.py` | Cache5 `meanmax(power)` | **49,188/49,188 points** |
| `_elevation` (smoothed) | `wko5_elevation.py` | channel | **628/628 bit-exact** |
| Climbing, descending, elevation change | `wko5_elevation.py` | 4223, 4225, 4227 | 628/628 |
| Moving / pedalling time, distance | `wko5_time.py` | 4213, 4214, 4217 | 789/790, 680/680, 780/780 |
| NGP, rTSS duration | `wko5_pace.py` | 4230, 4249 | 582/582 within 2.2e-6, 578/582 |
| Channel min / max / avg | `backend/files/wko4_file.py:194` `range_stats` | range stats | 100% of fresh ranges |
| FIT → channels | `backend/files/fit_to_channels.py:269` | channels | 1038/1061 files sample-for-sample |
| PMC (CTL/ATL/TSB) | `evaluator.py` `_tl` | athlete snapshot | 17.62 / 10.41 / 5.91, matches WKO5 |
| Power-duration model | `wko5_pdmodel.py` | — | **disassembly only, unverified** |

Two traps the verification surfaced, both reproduced deliberately:

- `_elevation` is only exact if every write is quantized to the channel's 0.1 m
  step, including the final drift-corrected output (`wko5_elevation.py`).
- WKO5's "almost equal" is effectively *exactly* equal; float residue in a
  running window sum counts as non-zero (`wko5_pace.py` `_is_zero`).

The remaining FIT mismatches (23 files) are GPSMAP 66i clock quirks, data-less
indoor/table-tennis runs, and swims.

## TSS policy

`backend/engine/wko5expr/dataset.py:298` `_metrics` follows WKO5's branch
order, reconstructed from disassembly:

1. **Power:** `NP² × tssduration / (FTP² × 36)` when there is a power stream.
2. **rTSS:** `(d/60)^1.025 × IF² / 60 × 100`, IF = threshold pace / NGP, for runs without power. The 1.025 exponent means an hour at threshold scores ~110.8, not 100; this is as disassembled and unverified against WKO5's UI.
3. **TrainingPeaks TSS** (`.wko4` info field 4038) when present, **before** WKO5's own hrTSS. WKO5 keeps TP's `tssActual` only when `tssSource == 0`, which is not stored on disk, so "use it when present" is the closest rule available from files.
4. **hrTSS** otherwise.

`tl()` is linear: `v += (x − v) / constant`, daily sums, inputs outside 0–5000
ignored (`evaluator.py` `_tl`).

## Modes

`backend/engine/wko5expr/config.py` — `EngineConfig`, persisted as JSON at
`CONFIG_PATH` in the user data directory.

| Setting | Parity | Own formulas (`MOUNTAIN_PRESET`) | Why |
|---|---|---|---|
| Use TP's TSS | forced on | off | Independence from TrainingPeaks; a direct COROS import has no TP TSS |
| hrTSS on moving time only | off | on | WKO5 charges every recorded second; a 51 h trip with 7 h moving scored 906 |
| hrTSS zone-1 floor | off | 0.70 × LTHR | WKO5's lowest band earns 20–30 TSS/h even while asleep |
| Elevation bonus | off | 10 TSS / 1000 ft | Uphill Athlete: heart rate cannot see the muscular cost of climbing |
| Data corrections | ignored | applied | Keeps WKO5 comparisons honest |

In parity mode every custom knob is ignored (`config.py` `tp_tss`,
`moving_hr_tss`, `elevation_bonus`). The viewer exposes only the parity switch;
the individual knobs are a fixed, researched preset.

## Data corrections

`backend/engine/wko5expr/corrections.py`. Nothing is auto-applied.

1. **Detect** (`detect_spikes`): flag samples above `factor × p90` of the
   athlete's per-workout peaks (defaults 1.6 × p90). WKO5's own spike chart
   compares the maximum against the top-5 average, which breaks when several
   files are corrupted — the baseline is pulled up by the very samples being
   hunted. On this athlete it finds exactly the three outliers (1875, 1594,
   1462 W) above a smooth tail ending at 896 W.
2. **Propose** (`GET /corrections/proposals`): returns the evidence — samples,
   peak, and the workout's peak after correction.
3. **Approve** (`POST /corrections/approve`): only the proposals sent are stored.
4. **Apply**: an overlay at `CORRECTIONS_PATH` in the user data directory,
   applied when channels are read (`backend/engine/wko5expr/dataset.py:461`).
   The `.wko4` files are never modified (WKO5 rewrites them on sync, and they
   are the only copy).
5. **Undo** (`DELETE /corrections/{id}`).

## Expression engine

`backend/engine/wko5expr/parser.py` parses 828 of the 829 expressions in the
athlete's two views (the exception uses the `in` operator). Value kinds in
`evaluator.py`: per-workout series (`WS`), daily series (`Daily`), sample
arrays, curves (`Curve`), pairs, ranges and lists.

Semantics chosen to match WKO5:

- Aggregations over sample-level arguments are lifted per workout
  (`needs_samples` stops at nested aggregations and `athleterange`).
- `athleterange(a, b, e)` limits aggregation and output; `tl()` always
  integrates full history so CTL does not restart.
- `sum/avg/count(values, groupby)` bucket by the second argument, e.g.
  `sum(climbing, startofweek(date))`.
- `meanmax(channel)` with no duration returns the athlete envelope (best of
  each duration across workouts in range); power curves reuse WKO5's Cache5.

Known gaps: `startofweek` assumes Monday (WKO5 reads a user preference);
`bin`, `lookup` levels, `stddev`, `slr*`, `filter` family are not implemented.

## Views

Two kinds, one renderer:

| Source | Location | Editable | Purpose |
|---|---|---|---|
| `wko5` | `*.wko5chart` anywhere under the repo | no | Parity checking |
| `custom` | `views/*.json` in the repo, then `USER_VIEWS` in the user data directory | yes | The athlete's own charts |

Custom views use the same shape as parsed WKO5 views (`customviews.py`); a
later file with the same `name` overrides an earlier one
(`backend/engine/wko5expr/customviews.py:133`). A chart's `kind` is `athlete`
(default), `workout`, `zones`, `targets` or `review`
(`backend/engine/wko5expr/customviews.py:75`); `review` needs a `section`
and renders a single-activity card — see
[workout-review.spec.md](./workout-review.spec.md). Two optional chart keys
drive the period toggle: `period` (day / week / month / quarter / year, the
default bucket) and `min_days` (look-back floor for that default bucket)
(`backend/engine/wko5expr/customviews.py:84`).

The bundled custom views (regrouped in commit 4f75cfe; the old 每月・每年
dashboard was dropped in favour of the period toggle):

| File | View | Dashboards |
|---|---|---|
| `views/training.json` | 我的訓練 | 負荷 PMC (PMC, 每日 TSS, CTL 每週增加); 訓練量 (每週移動時間 stacked by category, 本週 − 上週, 每週跑量 and 每週努力距離 EP stacked 路跑 / 越野跑 / 登山健行, 每週爬升／下降 in one chart, 肌力訓練日曆 as a day calendar); 強度 (HR distribution, low-intensity share, Palladino power zones); 能力 (EF, Pa:HR, VAM, 每公里爬升, per-session moving time, durability) |
| `views/periodization.json` | 周期化訓練 | ① 轉換期, ② 基礎期, ③ 專項期, ④ 減量期, 區間與課表強度 (zone / target tables last) |
| `views/workout.json` | 單次活動判讀 | 本次重點, 有氧／心率飄移, 間歇, 爬坡與地形, 配速與耐久, 跑姿與膝蓋負荷（參考） — see [workout-review.spec.md](./workout-review.spec.md) |

Most season charts in 訓練量 / 強度 and the phase dashboards carry a `period`
key; no bundled chart sets `min_days` at present.

**Sport identification trap.** Tags are FIT sport + subsport concatenated:
trail runs carry both `running` and `runningtrail`, road cycling carries
`cycling` and `cyclingroad`. So `hastag("running")` matches trail runs too;
road running is `sport="run" and !hastag("runningtrail")`.

## Period-total charts

`backend/engine/wko5expr/periods.py` re-buckets charts like
`sum(x, startofweek(date))` into 日／週／月／季／年.

- **Detect** (`backend/engine/wko5expr/periods.py:50`): the chart's `period`
  key, else the single bucket call in its expressions — `trunc` /
  `startofweek` / `startofmonth` / `startofquarter` / `startofyear(date)` as the
  group-by argument, or WKO5's named form `sum(tss, "week")`. Mixed buckets →
  not a period chart.
- **Lock** (`backend/engine/wko5expr/periods.py:62`): a chart with `shift(`
  (week-over-week) or `tl(…)*7` (weekly reference lines) keeps its default
  bucket.
- **Rewrite** (`backend/engine/wko5expr/periods.py:89`): only the group-by
  position is swapped (an `if(trunc(date) <= …)` stays); the title and legend
  names follow (display details in
  [wko5-chart-units.spec.md](./wko5-chart-units.spec.md)).
- **Look-back floor** (`backend/engine/wko5expr/periods.py:101`): month 365,
  quarter 730, year 1825 days; the chart's `min_days` applies only to its
  default bucket. Begin is also moved to a bucket start so the first bucket is
  whole (`backend/engine/wko5expr/periods.py:108`).
- **Buckets** (`backend/engine/wko5expr/periods.py:131`): every bucket start in
  the range (capped at 5000), so empty buckets still get an x category.

The chart endpoint applies it for `athlete` charts
(`backend/api/wko5views.py:227`): the `period` query parameter is honoured only
for custom views and unlocked charts; the floor and bucket alignment apply to
custom views only. The response adds `x_period`, `period_default`,
`period_toggle`, `buckets` and `range_note`
(`backend/api/wko5views.py:248`).

## Render cache

`backend/engine/wko5expr/render_cache.py`, used by the chart endpoint
(`backend/api/wko5views.py:219`).

- **Key** (`backend/engine/wko5expr/render_cache.py:113`): sha1 of the chart
  definition (after fixes and period rewrite), the request (view, dashboard,
  chart, begin/end after the floor, parity, the data source, every other query
  parameter, the workout's file), the data fingerprint and the code signature.
  Nothing is invalidated explicitly; changed inputs miss.
- **Data fingerprint** (`backend/engine/wko5expr/render_cache.py:81`): the
  `.wko5athlete` stamps, plan and corrections file stamps, engine config,
  workout list hash, today, and the chart data source with its FIT-folder stamp
  (`backend/engine/wko5expr/render_cache.py:94`).
- **Code signature** (`backend/engine/wko5expr/render_cache.py:62`):
  `CACHE_VERSION` plus size/mtime of every `*.py` in `wko5expr/`,
  `algorithms/`, `backend/engine/`, `backend/engine/panels/`, `backend/files/`
  and of `backend/api/wko5views.py` (`_ENGINE_GLOBS`,
  `backend/engine/wko5expr/render_cache.py:49`), taken once at import so a
  process that has not reloaded never stores old-code results under a new
  signature.
- **Storage**: in-memory LRU of 400 entries plus JSON files under the user
  data directory's `cache/render/`, pruned to 300 MB least-recently-used every
  50 writes (`backend/engine/wko5expr/render_cache.py:177`).
- **Concurrency** (`backend/engine/wko5expr/render_cache.py:203`): identical
  in-flight requests are coalesced; at most 2 renders run at once so other
  endpoints keep threadpool time. Errors are raised to every waiter and not
  cached.

## Viewer

`backend/static/wko5_viewer.html`, served at `/api/v1/wko5/viewer`.

- **Data-source chip** (`backend/static/wko5_viewer.html:251`): `#source-chip` +
  `sourcechip.js` in the header switch `charts.data_source` (WKO5 folder / COROS /
  TrainingPeaks) and reload; the chart, overview and race-power datasets all
  follow it (`_dataset`, `backend/api/wko5views.py:78`).
- **Chart directory** (`backend/static/wko5_viewer.html:488`): custom views are
  one flat list of dashboard tabs with no view level, ordered by
  `CUSTOM_ORDER` = 我的訓練, 周期化訓練, then the rest
  (`backend/static/wko5_viewer.html:388`); imported WKO5 views stay grouped per
  view with a 匯入 tag and collapsible headers.
- **Deep link** (`backend/static/wko5_viewer.html:427`): `?view=<name>&dash=<index
  or title>`, plus `&chart=<index>` to load that chart first and open it
  enlarged. The query string is then cleared.
- **Period toggle** (`backend/static/wko5_viewer.html:744`): when the response
  says `period_toggle`, the card header gets 日／週／月／季／年; the choice is
  remembered per chart in local storage (`wko5viewer.period`) and re-fetches
  that card only. A chart with a `calendar` series (a day calendar such as
  肌力訓練日曆, `backend/static/wko5_viewer.html:1234`) never gets the toggle.
- **Enlarge** (`backend/static/wko5_viewer.html:782`): 「⤢ 放大」 opens a
  `<dialog>` redrawn from the card's JSON (no refetch) with the full legend and
  a zoom slider; it pushes a history entry with `&chart=`, so Back, Esc, the
  close button or a backdrop click closes it
  (`backend/static/wko5_viewer.html:809`). A period toggle inside the overlay
  re-renders both card and overlay.
- **No 數值與公式 table.** The per-series debug table (status, points, last
  value, unit, expression) is no longer drawn; the same data stays in the chart
  JSON (`backend/static/wko5_viewer.html:908`).
- **Stack total.** A stacked chart's tooltip adds a 合計 row over the
  categories currently shown in the legend (hidden ones drop out); skipped for
  percent shares (`backend/static/wko5_viewer.html:1520`). Its unit, like every
  tooltip row's, comes from the source series of the ECharts series
  (`srcOf[seriesIndex]`, `backend/static/wko5_viewer.html:1399`).
- **Route map** (`backend/static/wko5_viewer.html:1087`): WKO5's map panel
  (`PKMapPanelConfig`, `backend/api/wko5views.py:148`) is drawn with Leaflet
  from the workout samples; the panel JSON (`render_map`,
  `backend/engine/wko5expr/render.py:372`) only says which workout and whether it
  has GPS (`empty`), no track of its own. Basemaps 魯地圖 (default), Google 地形, NLSC 電子地圖,
  正射影像, OSM; overlays 等高線, Google 道路, NLSC 道路
  (`backend/static/wko5_viewer.html:1041`). The defaults come from the settings
  keys `charts.map.basemap` / `charts.map.overlays`
  (`backend/settings/repository.py:53`), read as `map_basemap` / `map_overlays`
  from `GET /api/v1/sync/settings` (`backend/api/sync.py:215`,
  `backend/static/wko5_viewer.html:410`; storage side in
  [wko5-coros-sync.spec.md](./wko5-coros-sync.spec.md)); a per-browser switch is kept only
  while that default is unchanged (`backend/static/wko5_viewer.html:1065`).
  The route is coloured by 心率 / 功率 / 坡度 / 單色 (5–95th percentile ramp,
  12 bins) with start / end markers.
- **Tile-error hint** (`backend/static/wko5_viewer.html:1115`): if the active
  basemap has 3 tile errors and no tile loaded, a hint offers up to three other
  basemaps (not Google 地形) as buttons.
- **Synced hover** (`backend/static/wko5_viewer.html:986`): the map and every
  workout chart whose x is elapsed time or a distance unit join one hover
  group per workout; hovering any member shows the same sample index on all of
  them (tooltip on charts, a marker with a time / distance / HR / power /
  elevation / grade readout on the map), one flush per animation frame. Hovering
  within 24 px of the route drives the charts
  (`backend/static/wko5_viewer.html:1211`). Every workout series that joins the
  synced hover (time or distance x) skips `lttb` sampling so every chart's
  tooltip lands on the same point (`backend/static/wko5_viewer.html:1453`).
- **Samples** (`backend/api/wko5views.py:414`): per-sample `t`, `d` (km),
  `lat` / `lng`, `elev`, `hr`, `power`, `grade` (%), downsampled with the same
  step as the workout charts (`MAX_POINTS` 3000, `backend/engine/wko5expr/render.py:55`),
  so chart x maps exactly to a sample index; NaN and (0, 0) GPS become null. The
  viewer keeps the last 4 workouts' samples (`backend/static/wko5_viewer.html:952`).

## Mountain metrics (own formulas)

Not in WKO5; grounded in `docs/research/`.

| Module | What | Basis |
|---|---|---|
| `minetti.py` | Energy cost of running/walking by gradient; grade-adjusted speed with a downhill floor | Minetti et al. 2002, R² 0.999, ±45% |
| `effort.py` | Equivalent flat distance by integrating Minetti over the elevation stream | Same |
| `trail.py` `compute_hr_drift` | Aerobic decoupling Pa:HR | TrainingPeaks / Uphill Athlete convention |
| `chart_metrics.py` | Reference implementations of the competitor-derived charts in `views/training.json` / `views/workout.json`: Form% zones, ATL/CTL ratio, Foster monotony/strain, Treff PI, コース定数, ITRA km-effort/category, up/downhill m/h, downhill impact load (own composite; `DOWNHILL_EXPR` is shared with the 總覽 `descent` card) | Friel; Gabbett 2016 via Runalyze; Foster 1998; Treff 2019; 山本正嘉; ITRA; Gottschall & Kram 2005 + Keller 1996 — sources, formulas and check results in `docs/research/competitor-charts.md` §7, tested in `test_chart_metrics.py` |

WKO5's ACSM grade factor `(0.19v + 0.9vg)/0.19` under-counts steep running
against Minetti by 22% at +20% grade, 31% at +30%, and goes negative below
about −21%.

Against integrated Minetti on 430 of this athlete's activities, Scarf's
`km + gain/126` is the best summary formula (4.0% mean error); ITRA's
`gain/100` over-counts by ~7%; the Swiss descent term makes it worse (22%).
Least-squares fit: running `gain/153`, hiking `gain/111`. This says which
formula best approximates a lab model, not which is physiologically true.

## API

All under `/api/v1/wko5` (`backend/api/wko5views.py`).

| Method | Path | Line | Purpose |
|---|---|---|---|
| GET | `/views` | 156 | Both view kinds, with source; map panels report kind `map`, review cards kind `workout` |
| GET | `/views/dirs` | 170 | Where custom view files live |
| GET | `/views/{view}/dashboards/{d}/charts/{c}` | 189 | Render one chart through the render cache (`parity`, `begin`, `end`, `sports`, `workout`, `period`); the dataset follows `charts.data_source` |
| GET | `/workouts` | 277 | RHE activity list, with TSS source |
| GET | `/workouts/{i}/review` | 304 | Single-activity review cards — see [workout-review.spec.md](./workout-review.spec.md) |
| GET | `/sports` | 323 | Sport groups and counts |
| GET | `/athlete` | 332 | Settings history, WKO5's PMC snapshot |
| GET / PUT | `/config` | 351, 358 | Engine config |
| GET | `/corrections` | 371 | Applied corrections |
| GET | `/corrections/proposals` | 377 | Detect only — changes nothing |
| POST | `/corrections/approve` | 392 | Apply the proposals sent |
| DELETE | `/corrections/{id}` | 401 | Undo one |
| GET | `/workouts/{idx}/samples` | 414 | Downsampled per-sample arrays for the route map and synced hover |
| GET | `/viewer` | 463 | The viewer page |
| GET | `/settings` | 468 | The settings page |

The WKO5 athlete folder is `WKO5_ATHLETE_DIR` (or `WKO5COACH_ATHLETE_DIR`), else the first
folder holding a `*.wko5athlete` under the home directory's `WKO5` or
`Projects/TrailRunCoach/WKO5` (`default_roots`, `athlete_dir`, `backend/settings/paths.py:45`); the chart, achievements and plan APIs share it.

## Testing

`pytest.ini` registers a `golden` marker.

| Kind | Run | What it proves |
|---|---|---|
| Synthetic | `pytest -m "not golden"` (~45 s) | Each rule in isolation, on hand-built data |
| Golden | `pytest -m golden` (~5 min) | Parity with the athlete's real WKO5 data |

Golden tests skip automatically when `WKO5_ATHLETE_DIR` is absent. The
end-to-end golden test (`backend/tests/test_wko5_pipeline_golden.py`) goes
FIT → channels → NP/hrTSS → TSS → CTL and checks each against WKO5, including
the athlete-bar snapshot.

`backend/tests/test_periods.py` covers period detection, rewrite, legend
renaming, locks, floors and bucket lists (including `_apply_period`);
`backend/tests/test_render_cache.py` covers key changes (chart, request, data,
code), disk persistence, error non-caching, size eviction, coalescing and the
concurrency cap.

## Domain Model

### Bounded Context
- **Context Name**: WKO5 Engine
- **Domain Layer**: Core Domain
- **Parent Module**: N/A

### Ubiquitous Language
| Term | Definition |
|------|-----------|
| Parity mode | Reproduce WKO5 exactly; the correctness proof |
| Own formulas | This project's mountain-sport adjustments to WKO5 |
| Golden test | A test that compares against the athlete's real WKO5 data |
| Channel | A per-sample data stream in a `.wko4` (heartrate, `_elevation`, `@form_power`...) |
| Range | A span of a workout with WKO5-computed stats ("Entire Workout", "Peak 0:05:00 Speed", laps) |
| TSS source | Which branch produced a workout's TSS: power, rTSS, TrainingPeaks, hrTSS |
| Correction | An approved, reversible overlay blanking bad samples |
| Proposal | A detected, not-yet-approved correction |
| Custom view | A view the athlete defines as JSON |
| Equivalent flat distance | Flat distance that would cost the same energy (Minetti) |
| RHE | WKO5's right-hand explorer: the date range and sport filter |
| Period chart | A chart totalling by a date bucket; the viewer can re-bucket it (日／週／月／季／年) |
| Period lock | A period chart tied to a week scale that keeps its default bucket |
| Look-back floor | Minimum days a period chart shows for its bucket (`min_days`) |
| Render cache key | sha1 of chart + request + data fingerprint + code signature |
| Data fingerprint | Stamp of every data input a chart depends on |
| Samples | One workout's downsampled per-sample arrays, shared by the map and hover |
| Synced hover | All time/distance charts and the map of one workout showing the same sample |
| Basemap / overlay | The map's switchable tile layers; defaults from settings |

## Change History

| Date | Source | SRS | Change |
|------|--------|-----|--------|
| 2026-09-29 | code-sync | N/A | Created from brownfield analysis — WKO5 file readers, verified metric algorithms, expression engine, parity/own-formula modes, approved data corrections, custom views |
| 2026-09-30 | code-sync | N/A | Period toggle (periods.py, `period` / `min_days`), render cache, viewer (flat custom tabs, &chart= deep link, enlarge overlay, 數值與公式 table removed, Leaflet route map with basemaps / overlays / tile-error hint, samples endpoint and synced hover), regrouped custom views, refreshed API table |
| 2026-09-30 | bugfix | N/A | Chart dataset follows `charts.data_source` (header chip); code signature also covers panels/, files/ and api/wko5views.py; render_map drops the unused track; tooltip units from the drawn series; no lttb on distance-x hover charts; WKO5 folder from env or found under the home directory's WKO5 folder; refreshed anchors |
| 2026-09-30 | feat/competitor-charts | N/A | `chart_metrics.py` reference implementations + charts (Form% bands, ATL/CTL, monotony/strain, PI, downhill impact load and 7:28 ratio, up/downhill m/h, コース定数 / ITRA); 總覽 `descent` indicator |
