# Module Spec: racepower

> **Last Updated**: 2026-09-30
> **Status**: Active
> **Domain Layer**: Core Domain

## Overview

The 賽事功率 page: a port of the Palladino/Stryd "SuperPower Calculator" spreadsheet that
takes its inputs from the athlete's own activities instead of typed numbers, extended to
trail races (effort distance + a personal trail RE) and 百岳 (a walking model on personal
EP/h), with race-day weather from 中央氣象署 / Open-Meteo. The formulas, the task list and the
decisions on the workbook's ambiguities (D1–D10) are in
`docs/research/superpower-calculator.md`; this spec records what is implemented.

## Architecture

```
 Dataset ── athlete.derive() ── inputs: CP sources, W′, TTE, k, RE, EP/h, conditions, AeT
                                   │
 PredictIn ── /predict ── env.multiplier (M) ── predict.predict_run / predict_baiyue
                                   │                          │
                             weather.race_conditions ◄── CWA fileapi · Open-Meteo
```

| Module | Responsibility | Entry point |
|---|---|---|
| `env.py` | Pressure, altitude factor, dew point, Hadley heat penalty, M | `backend/engine/racepower/env.py:100` |
| `riegel.py` | Tasks 7/8/9/10/12, Riegel table lookup, ln-ln personal fit | `backend/engine/racepower/riegel.py:157` |
| `cp.py` | OLS work-vs-time CP / W′, validity checks, RWC rating | `backend/engine/racepower/cp.py:16` |
| `re.py` | RE, CVI and its adjustment, effort km, trail RE, per-activity metrics | `backend/engine/racepower/re.py:90` |
| `predict.py` | Riegel+RE solver, scenarios, road / trail / 百岳 predictions | `backend/engine/racepower/predict.py:108` |
| `weather.py` | Key storage, peaks, CWA + Open-Meteo providers, caches | `backend/engine/racepower/weather.py:370` |
| `athlete.py` | Reads the Dataset and derives every input | `backend/engine/racepower/athlete.py:270` |
| API + page | Endpoints, override logic, the HTML page | `backend/api/racepower.py:209` |
| `gpx.py` (v2) | GPX 1.0/1.1 / FIT course parsing (stdlib ElementTree), GPX writer | `backend/engine/racepower/gpx.py:57` |
| `course.py` (v2) | Distance, resample, smoothing, hysteresis gain, Douglas–Peucker, classes, merge | `backend/engine/racepower/course.py:364` |
| `grade_model.py` (v2) | Personal RE(g) / v_max(g) and walking speed v_h(g) | `backend/engine/racepower/grade_model.py:47` |
| `difficulty.py` (v2) | Sustainable power F1–F3, t_lim, effort bar | `backend/engine/racepower/difficulty.py:36` |
| `pacing.py` (v2) | Hill / ramp weights, the three solvers, W′ budget and curves | `backend/engine/racepower/pacing.py:77` |
| `hike.py` (v2) | Pandolf, multi-day fatigue, walking rows, Naismith / Langmuir | `backend/engine/racepower/hike.py:28` |
| `planner.py` (v2) | Plan orchestration, the validation gate, COROS steps | `backend/engine/racepower/planner.py:149` |
| `backtest.py` (v2) | Leave-one-out back-test, pass rule, stored flags | `backend/engine/racepower/backtest.py:383` |

## Derived inputs (`derive`)

- **Weight**: `ds.setting("weight")` (season-plan weight, else WKO5).
- **Runs**: last 365 days; CP uses the 90-day subset (`backend/engine/racepower/athlete.py:23`).
  Per-run metrics are disk-cached (`backend/engine/racepower/athlete.py:164`).
- **Implausible power** is excluded: NP > 1.5 × CP, or a 5-min best > 2 × CP
  (`backend/engine/racepower/athlete.py:73`).
- **Altitude normalisation (D2)**: before building an envelope, each run's power is scaled by
  M(activity median elevation → training reference altitude), altitude term only
  (`backend/engine/racepower/athlete.py:86`).
- **Envelope**: mean-max power on a 1.05 grid with the activity that set each point
  (`backend/engine/racepower/athlete.py:99`), from WKO5's Cache5 curves.
- **CP sources**: season-plan CP test (fresh ≤ 90 days), activities fit at 180–1200 s, WKO5
  mFTP; default order fresh plan → activities → WKO5. The fit reports CP, W′, R², its points,
  validity checks and the RWC rating (`backend/engine/racepower/cp.py:82`).
- **TTE**: WKO5 model TTE, else 3000 s.
- **Personal Riegel k**: ln-ln fit on the 365-day envelope from max(TTE, 1200 s), cut where the
  envelope falls below 0.8 × its start value (deviation from the design doc, documented in
  `backend/engine/racepower/riegel.py:157`). Valid only with k in −0.25…−0.01, ≥ 5 points,
  R² ≥ 0.8 and ≥ 3 distinct activities; otherwise the table k is used.
- **RE**: road = median over flat road runs (CVI < 25, ≥ 20 min, not indoor); trail = median
  over trail runs (≥ 150 m climb, ≥ 45 min) of `(effort m / moving s) / (W/kg)` for the
  fitted_run / itra / scarf divisors (`backend/engine/racepower/re.py:64`).
- **Hiking EP/h**: per calendar day from `achievements.build_achievements` (multi-day trips
  split), ≥ 1 h moving, 3-year window, weighted ×3 for days with ≥ 600 m gain; biggest day kept
  (`backend/engine/racepower/athlete.py:184`).
- **Training conditions**: median elevation of the 90-day power runs + Open-Meteo archive
  T / RH over those activities at the median start location, cached per day; fallback
  100 m / 25 °C / 75 % (`backend/engine/racepower/athlete.py:209`).
- **AeT** via `thresholds.estimate` + `zones.training_targets`; **priors** = standard-distance
  road runs; auto prior = the fastest of the year; **events** = upcoming season-plan events with
  their matched peak.

`/inputs` is memoised for 10 min per (dataset, day, plan mtime) (`backend/api/racepower.py:63`).

## Prediction (`/predict`)

Every derived input can be overridden in `PredictIn` (`backend/api/racepower.py:179`); `used`
records each value and its source ("手動" when overridden).

- **M** = `env.multiplier(from = training conditions, to = race day)`.
- **Road**: D = true distance; RE = road median + CVI adjustment from the flat runs' CVI to the
  race CVI; k = manual > personal (if valid) > table (from the chosen or auto prior) > −0.07.
  `solve_riegel_re` iterates `P = CP·M·(t/TTE)^k`, `t = D/(RE·P/W)` to |Δt| < 0.5 s with a
  bisection fallback (D10, `backend/engine/racepower/predict.py:42`).
- **Trail**: D = effort km (km + gain/X, default X = 153 fitted_run); RE = personal trail RE;
  adds a climb-power cap (1.10 × target, heuristic) and the workbook's CVI cross-check
  (`backend/engine/racepower/predict.py:145`).
- **Both**: scenarios at ±5/10/20 % time, task 8/15 values, task 12 "CP needed for the target
  time" (divided by M so it compares with training CP), task 17 for roads ≤ 10.3 km, prior-race
  tasks 7/9/10 (D1 time-consistent form), zones from CP, extrapolation and > 3 h warnings.
- **百岳**: per-day plan (given, or split evenly), `moving_h = EP / (EP/h · M · pack_factor)`,
  pack factor `(W + 5 kg)/(W + pack)` (pack default 6 kg single-day / 12 kg multi-day), Yamamoto
  course constant → kcal, water 0.7–0.8 × kcal ml, HR cap = AeT, comparison with the biggest
  past day (`backend/engine/racepower/predict.py:175`).

## Weather

`race_conditions` (`backend/engine/racepower/weather.py:370`) tries, in order, and logs every
attempt in `tried`:
1. CWA 登山三天 hourly (F-B0053-035), then 登山一週 day/night (F-B0053-033) — whole-file
   downloads from the fileapi, compacted and cached as `cwa_<id>.json` in the app data dir (`weather.HOME`),
   refreshed when ≥ 3 h old; a stale cache is used when there is no key or the fetch fails
   (`backend/engine/racepower/weather.py:336`). Locations match by name, else nearest ≤ 5 km;
   daytime 06–18 values only.
2. Open-Meteo forecast (≤ 16 days, lat/lon/elevation).
3. Open-Meteo archive climatology: the same ±7-day window in the last 5 years, temperature
   lapse-corrected to the target elevation (−6.5 °C/km).
4. Manual (`values: None`, the page's editable fields).

CWA key: `CWA_API_KEY` env var, else `weather.json` in the app data dir (`weather.KEY_PATH`); the API only returns it
masked (`backend/engine/racepower/weather.py:56`). Peaks: `backend/data/baiyue.json`
(100 百岳 + 103 小百岳: name, elevation_m, lat, lon, flags), built by
`backend/scripts/build_baiyue.py`; `achievements.load_peaks` now returns only 百岳 by default.

## API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/v1/racepower/inputs?refresh=` | the derived inputs (`backend/api/racepower.py:78`) |
| GET | `/api/v1/racepower/peaks?q=&baiyue_only=` | peaks list (`backend/api/racepower.py:83`) |
| GET | `/api/v1/racepower/weather?date=&days=&event_id=&peak=&lat=&lon=&elevation=&cwa=` | provider, values, tried, location, fetched_at (`backend/api/racepower.py:105`) |
| GET / POST | `/api/v1/racepower/weather/key` | masked key status / save (10–80 chars, no spaces) (`backend/api/racepower.py:142`) |
| POST | `/api/v1/racepower/predict` | v1, unchanged: type, used, env, result, tasks, zones, warnings (百岳 adds biggest_day) (`backend/api/racepower.py:209`) |
| POST | `/api/v1/racepower/course` | multipart `file` (.gpx/.fit) + segmentation options → `course_id` (content sha1; the Track is kept in a 20-entry LRU), totals, segments, profile ≤ 1500 points, climbs, waypoints, warnings (`backend/api/racepower.py:400`) |
| POST | `/api/v1/racepower/plan` | `PlanIn` = `PredictIn` + mode, targets, course ref (`course_id` + options, or manual), strategy, hills, acclimatisation, locks, start time, aid stations, day splits, terrain, W′ curve → summary, effort, segments, days (百岳), compare, crosscheck, v1, warnings; unknown `course_id` → 410 (`backend/api/racepower.py:571`) |
| GET | `/api/v1/racepower/grade-model` | personal RE(g) / v_max(g) / v_h(g) with bin counts (`backend/api/racepower.py:447`) |
| GET / POST | `/api/v1/racepower/backtest`, `/backtest/run` | stored back-test + run state / start a background run (`backend/api/racepower.py:576`, `backend/api/racepower.py:582`) |
| POST | `/api/v1/racepower/export/coros` | the plan as a COROS structured workout; `push: true` adds it to the library and, for a future date, the calendar (`backend/api/racepower.py:632`) |
| GET | `/api/v1/racepower/page` | `backend/static/racepower.html` (`backend/api/racepower.py:350`) |

Missing CP / road RE / trail RE / EP/h → HTTP 400 asking for a manual value.

## v2: three modes on a segmented course

Design: `docs/research/racepower-v2.md` (formulas F1–F18, verification §3A / §3C, back-test
§3B). User decisions of 2026-09-30 are applied as listed in the change history.

### Course

- **GPX / FIT** (`backend/engine/racepower/gpx.py:57`): stdlib ElementTree, GPX 1.0 / 1.1, trk
  (several trksegs concatenated) or rte, waypoint names; a DOCTYPE / ENTITY is refused before
  parsing; 20 MB cap; no `ele` → error. FIT courses via `fitdecode`.
- **Segmentation** (`backend/engine/racepower/course.py:364`): haversine distance (or the device
  distance for the athlete's own activities), 10 m resample, 5-point median + Gaussian σ 50 m,
  3 m hysteresis gain / loss, optional scaling to an official gain, Douglas–Peucker ε 10 m
  (`backend/engine/racepower/course.py:171`), classes 陡下 ≤ −15 % / 下坡 / 平 ±2 % / 上坡 / 陡上
  ≥ 15 %, merge of segments shorter than max(200 m, 1 %) (`backend/engine/racepower/course.py:233`),
  flats > 3 km split per km; walk labels 走跑皆可 ≥ 15 %, 建議快走 ≥ 28 %. Also per-km or one
  segment. Manual courses are one segment or per-km with no grade information.
- **Multi-day** (百岳): split points clicked on the profile (camp / hut waypoints pre-fill them).

### Models

- **Sustainable power** (`backend/engine/racepower/difficulty.py:36`): F1 Riegel for T ≥ TTE,
  F2 CP + W′/T up to min(1200 s, TTE/2), F3 log-linear bridge in between; t_lim by bisection.
- **Personal RE(g)** (`backend/engine/racepower/grade_model.py:47`): 100 m windows of every outdoor
  run with power (365 days, disk-cached per activity), 2 % bins, shrunk n/(n+30) towards the
  Minetti prior RE_flat·Cr(0)/Cr(g) with the 0.9 downhill floor
  (`backend/engine/racepower/grade_model.py:34`); v_max(g) = p90 speed of downhill bins. For road
  plans RE(0) is v1's CVI-adjusted road RE.
- **Walking speed** v_h(g): hiking windows (3 years, rests < 0.3 m/s dropped), shrunk towards
  Tobler; altitude relative to the median elevation of those windows.
- **Allocation** (`backend/engine/racepower/pacing.py:77`): uᵢ = h(g)·aᵢ·s(τ); hill elasticity
  up +5 % (≤ 12 %) at ≥ 8 %, down −10 % at ≤ −10 % (user-adjustable); ramp σ ±0–5 % (default 2 %,
  trail positive 3 %); downhill cap; locked segments; F16 keeps the time-weighted average.
  Solvers: power (`backend/engine/racepower/pacing.py:139`), time, auto
  (`backend/engine/racepower/pacing.py:153`). The W′ budget F11b
  (`backend/engine/racepower/pacing.py:165`) shrinks α while a stretch above CP spends > 0.75 W′.
- **W′ curve** (display only): WKO5's dfrc port (default, `backend/engine/racepower/pacing.py:246`)
  or Skiba with a labelled τ — 2012 cycling 546·e^(−0.01D)+316 or Vassallo 2020's running refit
  372·e^(−0.02D)+102 (`backend/engine/racepower/pacing.py:224`).
- **Environment per segment** (`backend/engine/racepower/env.py:157`): v1's M with each segment's
  mean elevation; acclimatised = the pressure polynomial (the workbook / GoldenCheetah
  coefficients, attributed to Péronnet 1991 there — not Bassett), unacclimatised = Wehrlin linear
  (`backend/engine/racepower/env.py:119`, default for 百岳), partial = their midpoint (推估).
  Bassett 1999's two curves (`backend/engine/racepower/env.py:133`) are a cross-check quoted above
  2800 m.
- **百岳** (`backend/engine/racepower/planner.py:368`): tᵢ = dᵢ/(λ_h·v_h·pack·Aᵢ·f_day/η); pack =
  v1 linear factor; Pandolf (`backend/engine/racepower/hike.py:28`) is implemented for G ≥ 0 only
  and not used for the times; multi-day fatigue F17 (`backend/engine/racepower/hike.py:66`) needs
  ≥ 3 trips; clock ETA = moving ÷ the personal moving ratio + aid stops.

### Modes and the validation gate (`backend/engine/racepower/planner.py:149`)

- A 目標時間 (time or pace) → power; B 目標功率 (W, %CP, or training-condition power × M;
  百岳: speed %) → time; C 通通幫我算: effort target f* (100 / 95 / 85 %, or dragged on the bar).
- Until a category (road / trail / hike) passes the back-test, the whole-race time and power are
  v1's method (Riegel F1–F3 on v1's RE or effort km × trail RE, `backend/engine/racepower/planner.py:92`;
  百岳 EP/h) and the v2 segments only distribute it (times scaled, average power kept); every
  segment target carries 推估. A passing category switches to the v2 segment sum automatically
  (flags from the stored back-test, `backend/engine/racepower/backtest.py:462`). Manual courses
  always use v1.
- In mode C the whole-race M is the effort's own time-weighted M, so f comes out at f* exactly.
- Cross-checks: v1 /predict, Stryd's race-power table as % of the athlete's own 10 km power
  (`backend/engine/racepower/planner.py:70`; the percentages are of 10 km power, never of CP; the
  page's linear formula is not used), CVI method, Naismith, Langmuir.

### Effort bar (`backend/engine/racepower/difficulty.py:85`)

f = P̄_train / P_sus,train(T). Five levels: 輕鬆 < 80 %, 穩定 80–90, 吃力 90–97, 極限 97–100,
超出 > 100 (lower bounds inclusive; exactly 100 % is 極限). Band = CP ± 3 % and k ± 0.01; the
multiple m = f^(1/k) and t_lim are shown. 百岳: speed needed ÷ usual speed against the athlete's
EP/h quantile ratios (`backend/engine/racepower/difficulty.py:133`). Badge 推估 until the A-race
check passes.

### Back-test (`backend/engine/racepower/backtest.py:383`)

Leave-one-out (`backend/engine/racepower/backtest.py:190`): each past A race, run ≥ 90 min with
power (365 days) and hiking day ≥ 1 h (3 years) is predicted with inputs derived as of the day
before and the activity excluded from every fit (`derive(exclude=…)`,
`backend/engine/racepower/athlete.py:270`), on its own GPS track with the device distance as the
ruler (`backend/engine/racepower/backtest.py:80`). Mode B with the actual power → time vs moving
time; per segment at the segment's actual power. Pass (`backend/engine/racepower/backtest.py:140`):
n ≥ 5, median |error| ≤ road 3 % / trail 6 % / hike 10 %, downhill median speed error ≤ +5 %.
Script: `python -m backend.scripts.racepower_backtest`; result stored in the app data dir
(`racepower_backtest.json`).

Result on 2026-09-30 (27 cases):

| Category | n | v2 median \|err\| | v2 signed | v1 median \|err\| | downhill bias | pass |
|---|---|---|---|---|---|---|
| 路跑 | 1 | 1.0 % | +1.0 % | 2.3 % | – | no (n < 5) |
| 越野 | 10 | 11.0 % | +5.7 % | 10.2 % | +6.7 % | no |
| 登山 | 16 | 11.9 % | −0.2 % | 29.3 % | +2.8 % | no |

Effort bar: 0 past A races → not validated; long runs median f 0.80. Nothing is validated, so
every segment target and the bar keep 推估. With raw-GPS haversine instead of device distance the
trail median was 7.4 % (the two rulers differ by up to ±27 % on these tracks). Steep climbs are
predicted 16 % (trail) / 32 % (hike) too slow; hike flats 33 % too fast.

### Page

Tabs 計算 / 準確度. Mode switch (sticky on phones), course manual / GPX (drop or file button),
segmentation options, strategy, hill elasticity, acclimatisation, display power / pace, W′ curve,
start time, aid stations. Results: tiles, draggable effort bar with band, Palladino zone strip,
ECharts profile (segment-class bands, target step line, click ↔ table row, day split taps, zoom),
segment table (editable power locks, 百岳 terrain per segment) or cards below 700 px, COROS export
(preview, then confirm to push). References stay out of the page.

### COROS export

`coros_payload` / `push_to_coros` (`backend/api/racepower.py:613`) map segments through the existing
`coros_workouts` Step / build_program: one time-based step per segment (≤ 50, shortest merged),
power ± 3 % (百岳: HR ≤ AeT), overview 「看 30 秒平均功率」. Tests use the mocked Training Hub only.

## Testing

`backend/tests/test_racepower.py`: the environment chain against the workbook's cached
defaults and the worked example (M 0.911174), CP / W′ fits and RWC bands, every Riegel task
incl. D1, table lookup edges (D5), CVI categories and adjustment, the personal-k cut, D2
altitude normalisation, road / trail / 百岳 predictions, CWA / Open-Meteo / climatology parsing
and the provider chain (no network), key storage, peaks and the build script. Not covered:
`athlete.derive` on a dataset.

`backend/tests/test_racepower_v2.py`: the V-numbered checks (V-F1 Stryd table, V-F2–F6, V-F10,
V-F11 τ values, V-F11b, V-F12 Pandolf, V-F13, V-F14 / F14b, V-F15, V-F16, V-F17, V-F18, V-DP, V-SM,
V-CL, V-HE, V-BT), T1 (auto mode = v1 `solve_riegel_re`, engine and planner), T2–T14 (modes
round trip, strategy, downhill cap, W′ budget, GPX parsing and limits, haversine, segmentation,
grade model, per-segment M), T15 API (`/predict` unchanged, `/course` → `/plan`, 410, COROS preview)
and the mocked COROS push.

## Domain Model

### Bounded Context
- **Context Name**: RacePrediction（賽事功率預估）
- **Domain Layer**: Core Domain
- **Parent Module**: N/A (consumes `wko5-engine`, `planning`, `achievements`)

### Ubiquitous Language
| Term | Definition |
|------|-----------|
| CP | critical power (W); the FTP input of the workbook |
| W′ / RWC | work capacity above CP (J); rated by the RWC bands |
| TTE | time to exhaustion at CP |
| k | Riegel exponent, P(t) = CP·(t/TTE)^k |
| personal / table k | fitted from the athlete's envelope / looked up from a prior race |
| envelope | mean-max power across runs, with the run that set each point |
| RE | running effectiveness = speed (m/s) ÷ (W/kg) |
| CVI | climb ft per mile; categories drive a ±0.01 RE adjustment |
| effort km (D_eff) | km + gain/X |
| EP, EP/h | km + gain/100, and per moving hour |
| M | environment multiplier (altitude factor + Hadley heat penalty) |
| From / To | the conditions power was measured in / race day |
| prior | a past run used for tasks 7/9/10/16 |
| task N | the workbook's task number |
| pack factor | (W + historical pack) / (W + target pack) |
| CC | Yamamoto course constant |
| provider / tried | the weather source used / every attempt made |
| mode A / B / C | target time → power, target power → time, effort target → both |
| f (effort) | race-day power in training conditions ÷ sustainable power for that duration |
| segment | a stretch of the course with one grade class |
| RE(g) | the athlete's RE per grade bin, shrunk towards the Minetti prior |
| λ_h | walking-speed multiple, 1.0 = the athlete's usual pace |
| validated category | road / trail / hike that passed the back-test; drops 推估 |

## Known gaps

- Only the altitude term is normalised per activity (per-activity T / RH are not stored).
- Heat is one value for the whole event, not per day / hour.
- Sex defaults to male while the season-plan profile is empty.
- Back-test: WKO5 mFTP / TTE are today's values at every date; no past A races yet; RE(g) is
  intensity-independent, which fails on walking-heavy "trail runs" at ~1.2 W/kg.
- CSV export and saving a GPX course onto a season-plan event are not done.

## Change History

| Date | Source | Feature SRS | Summary |
|------|--------|-------------|---------|
| 2026-09-30 | code-sync | N/A | Created from brownfield analysis — SuperPower Calculator port on the athlete's data, trail and 百岳 extensions, CWA / Open-Meteo race-day weather; D2 altitude normalisation added and the CVI cross-check source clarified during sync |
| 2026-09-30 | feature | docs/research/racepower-v2.md | v2: three modes, five-level effort bar (80/90/97/100), pacing strategies, GPX / manual courses with per-segment allocation and profile, per-segment altitude with acclimatisation switch (百岳 default unacclimatised, partial = 推估), hill elasticity +5/−10 %, aid stops in the ETA, 「看 30 秒平均功率」, COROS export (mocked push), leave-one-out back-test + 準確度 tab gating the 推估 labels (nothing validated yet); §3C applied: Skiba τ labelled, Pandolf uphill only, altitude polynomial not attributed to Bassett, Stryd percentages are of 10 km power |
