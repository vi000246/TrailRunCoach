# Module Spec: racepower

> **Last Updated**: 2026-10-01
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
| API + page | Endpoints, override logic, the HTML page | `backend/api/racepower.py:211` |
| `gpx.py` (v2) | GPX 1.0/1.1 / FIT course parsing (stdlib ElementTree), GPX writer | `backend/engine/racepower/gpx.py:57` |
| `course.py` (v2) | Distance, resample, smoothing, hysteresis gain, Douglas–Peucker, classes, merge | `backend/engine/racepower/course.py:364` |
| `grade_model.py` (v2) | Personal RE(g) / v_max(g) and walking speed v_h(g) | `backend/engine/racepower/grade_model.py:47` |
| `difficulty.py` (v2) | Sustainable power F1–F3, t_lim, effort bar | `backend/engine/racepower/difficulty.py:36` |
| `pacing.py` (v2) | Hill / ramp weights, the three solvers, W′ budget and curves | `backend/engine/racepower/pacing.py:77` |
| `trailhr.py` | Trail HR pace model (effort km vs HR / LTHR, durability, race HR level; 推估) | `backend/engine/racepower/trailhr.py` |
| `activity_tags.py` | Activity type / effort tags (auto + user override) used to pick capacity samples | `backend/engine/activity_tags.py` |
| `hike.py` (v2) | Pandolf, multi-day fatigue, walking rows, Naismith / Langmuir | `backend/engine/racepower/hike.py:28` |
| `planner.py` (v2) | Plan orchestration, the validation gate, per-segment heat fixed point, COROS steps | `backend/engine/racepower/planner.py:258` |
| `csvplan.py` | Plan → CSV text (header block + one row per segment); formatting only | `backend/engine/racepower/csvplan.py:146` |
| `backtest.py` (v2) | Two leave-one-out back-tests (capacity, terrain), pass rule, stored flags | `backend/engine/racepower/backtest.py:269` |
| `intensity.py` (v2) | Per-activity HR / power stats, the easy / steady / race classifier, INTENSITY constant | `backend/engine/racepower/intensity.py:169` |
| `cptest.py` (v2) | 3′/12′ tests in the synced FIT files, non-maximal bout detection, single-bout CP, FIT mean-max curves | `backend/engine/racepower/cptest.py:49` |
| `hikehr.py` (v2) | HR-filtered steep hike windows: VAM, personal altitude factor, multi-day fatigue | `backend/engine/racepower/hikehr.py:54` |
| `maximal.py` (v2) | Capacity samples: plan-event matching, self-paced maximal road rule, race-like trail rule, observed HRmax | `backend/engine/racepower/maximal.py:118` |
| `hrcap.py` (v2) | HR-based capacity: per-run steady points, OLS power on HR → P at LTHR, training-intensity distribution | `backend/engine/racepower/hrcap.py:100` |

## Derived inputs (`derive`)

- **Weight**: `ds.setting("weight")` (season-plan weight, else WKO5).
- **Runs**: last 365 days; CP uses the 90-day subset (`backend/engine/racepower/athlete.py:23`).
  Per-run metrics are disk-cached (`backend/engine/racepower/athlete.py:164`).
- **Implausible power** is excluded: NP > 1.5 × CP, or a 5-min best > 2 × CP
  (`backend/engine/racepower/athlete.py:73`).
- **Power source** (2026-10-01, `backend/engine/power_source.py`; workouts.spec.md): only Stryd
  power (Form Power / Air Power / LSS developer fields, or a Stryd device) feeds the power-based
  models; watch-estimated power is 「手錶推估功率（未採用）」 unless `power.accept_watch_power`.
  Gated in `athlete.py` (`power_ok`, `power_runs`, `model_stats`): the envelopes (`_curve`), the
  per-run metrics (`run_metrics` → RE, the moving-power lower-bound points, priors, training
  conditions), the PD refit including the synced FIT curves (`cptest.curves(accept_watch=…)`), the
  CP-test scan, the grade RE samples (`grade_samples(power_only=True)`; the walking-capacity
  windows pass False), the HR–power capacity, the CP floor, the road monotonicity check
  (`longer_p` skipped, not failed, on a watch run) and the power side of the intensity class.
  HR / pace paths use every run. One exception, 推估: `cp_as_of` (it only locates the Friel window
  of the LTHR estimate) uses the usable power when the 90-day window has any, else every power
  (the pre-Stryd history). `derive()["power_source"]` = counts, the unused watch runs, the setting;
  the page lists them under the CP detail and in the inputs note.
- **Bad activity files** (2026-10-01, `backend/engine/bad_activity.py`; workouts.spec.md): a run
  recorded in a car / on a bike, or with impossible power, is not in `ds.workouts`, so it is no run,
  envelope point, capacity sample or back-test case; the synced FIT files read beside the dataset
  (`cptest.curves` / `scan` → `_usable`) drop it too (`cptest.bad_files`, cached in
  `racepower_bad_activity.json`). The TP 2025-12-14 file (43 km/h, 899 W) is excluded by the
  average-speed rule whatever `power.accept_watch_power` says. The user's 「這筆是正常的」 brings it back.
- **Altitude normalisation (D2)**: before building an envelope, each run's power is scaled by
  M(activity median elevation → training reference altitude), altitude term only
  (`backend/engine/racepower/athlete.py:86`).
- **Envelope**: mean-max power on a 1.05 grid with the activity that set each point
  (`backend/engine/racepower/athlete.py:99`), from WKO5's Cache5 curves.
- **CP sources** (`backend/engine/racepower/athlete.py:528`):
  - `pdmodel` (default): WKO5's PD model port (`algorithms/wko5_pdmodel.py`) refitted on the raw
    90-day mean-max of the runs plus the synced running FIT files not yet in WKO5
    (`backend/engine/racepower/athlete.py:453`, `cptest.curves`), on WKO5's own duration grid.
    On WKO5's data alone it gives mFTP 175.5 W / TTE 1895 s against the stored snapshot's
    175.6 W / 1897 s. With today's COROS file it gives 191.5 W. The research script got 196.1 W
    (a different sampling of the FIT curve). The source is two-anchored: F1 is anchored at mFTP
    with the fit's TTE. F2 (≤ 20 min) uses the pair (cp2, W′) from a fresh plan CP test, else a
    detected FIT test, else the model's mFTP + FRC.
  - `plan` (the season-plan test, fresh ≤ 90 days), `cptest` (a detected 3′/12′ test, shown as a
    suggestion and never written to the plan), `wko5` (the snapshot), and `activities` (the
    180–1200 s envelope fit, with its 14-day check).
  - **Lower bound** (`backend/engine/racepower/difficulty.py:162`): every 365-day envelope point
    and every run's moving-time average power (both altitude-normalised), with t ≥ 20 min (t ≥ TTE
    for a two-anchor source), requires p_sus(t) ≥ the power held. The binding
    point, cp_min and a message 「模型 CP 低於你實際撐過的功率（…）：CP 至少 ≥ X W，請重測」 are
    exposed. A PD model below its bound becomes the `lower_bound` source (anchor raised to
    cp_min). Without a PD fit the order is plan → cptest → WKO5 → activities, each only when it
    covers the bound. `/predict` re-checks the bound for the k it actually uses
    (`enforce_lower_bound`, `backend/engine/racepower/athlete.py:435`).
- **CP tests** (`backend/engine/racepower/cptest.py:49`): laps of 150–210 s and 660–780 s, each
  ≥ 1.3 × the other laps' median power (推估). The mean-max is taken inside the lap. A bout is
  non-maximal when the 3′ power is not above the 12′ power (the workbook's "falling" check) or
  its peak HR is ≥ 10 bpm below the other bout's (推估; 2026-09-30: 146 vs 171 bpm). With one
  maximal bout, CP = P − W′/t, with the W′ prior 13.1 ± 4.0 kJ for men and 6.4 ± 2.2 kJ for
  women (Ruiz-Alias et al. 2025, amateur Stryd 9/3) and the range at ± 1 SD. For 2026-09-30:
  12′ 220.9 W → CP 202.7 W (197.1–208.3). `workout_review.cp_test` now takes non-overlapping
  windows (the 3′ is ≥ 10 min away from the 12′), and falls back to the same single-bout estimate.
  A separate 3′ at ≥ 98 % CP still marks the session test_cp.
- **TTE**: that of the default source (PD refit), else the WKO5 snapshot, else 3000 s.
- **Personal Riegel k**: the same ln-ln fit, but only on the envelope of the capacity samples
  (plan races, self-paced maximal road efforts, race-like trail efforts; `maximal.py`), which
  need ≥ 3 activities. Otherwise k = −0.07 (≈ Stryd's table). The table prior (`auto_prior`) is
  only a capacity sample at a standard distance, never a training run or an HR "race" class
  run. The back-test looks the table up at the case's own distance (trail: effort distance).
- **RE**: road = median over road runs ≥ 20 min with CVI < 51, each adjusted to flat with the
  workbook's CVI adjustment (+0.01 per category), so it rests on 140 runs instead of the few
  under 25 ft/mi (`road_cvi` is then 0). Trail = median over trail runs (≥ 150 m climb, ≥ 45 min)
  of `(effort m / moving s) / (W/kg)` for the fitted_run / itra / scarf divisors
  (`backend/engine/racepower/re.py:64`).
- **Hiking EP/h**: per calendar day from `achievements.build_achievements`, the same rules as
  before, but only over hikes the user opted in as solo (`racepower_solo_hikes.json`,
  `GET/POST /solo-hikes`, `backend/engine/racepower/athlete.py:197`). 「百岳多為跟團，速度不代表個人能力，不列入目標時間推算」:
  without solo days the 百岳 v1 time uses the walking-capacity model's EP/h on the course
  (`capacity.course_eph`, the same two-segment split as `hike.tobler_eph`, at the v1 reference
  pack; 推估). Tobler's EP/h is only used when no capacity model can be fitted. The group days are
  still listed, marked 跟團，不計.
- **Intensity class** of every run (`backend/engine/racepower/athlete.py:547`) uses the thresholds
  as of that run's date (`thresholds_as_of`, `backend/engine/racepower/athlete.py:359`). LTHR /
  AeT come from a plan test dated on or before that day, else `thresholds.estimate` on the runs
  before it, else the dataset's own dated setting: WKO5's on the WKO5 source; on a COROS / TP
  source the as-of estimates the FIT dataset made at load (`backend/engine/wko5expr/fitdataset.py`,
  never WKO5 unless `charts.fit_settings_from_wko5` is on), else 未設定. AeT falls back to
  0.89 × LTHR. In that estimate each run is measured
  against `cp_as_of` its own date (`backend/engine/racepower/athlete.py:324`): a plan CP row on
  or before the date, else WKO5's PD model refitted on the 90-day mean-max up to the date, else
  the last valid refit of the 30 days before (推估). It never uses a later CP or today's WKO5
  snapshot (`thresholds.estimate(cp_of=…)`, `backend/engine/thresholds.py:50`). CP for the class
  comes from a dated plan test, else the lower bound from the earlier runs (demotion only). A
  plan race is the one activity matched to a past season-plan event
  (`plan_race_runs`, `backend/engine/racepower/athlete.py:426`), not every run that day. Each
  class also carries `capacity`: whether the run is a capacity sample (see Back-tests).
- **Thresholds on past dates**: `planning.Plan.threshold_on` (`backend/engine/planning.py:205`)
  returns None before a row's date (fixed 2026-10-01). Before, the earliest row applied
  backwards, so the 2026-09-30 row (CP 204, LTHR 155) leaked into every earlier date.
  `Dataset.setting` / `cp` / `aethr` then fall back to WKO5's dated settings (runthr 160, the
  current mFTP snapshot 175.6 W, 0.89 × LTHR). Today's values are unchanged.
- **Training conditions**: median elevation of the 90-day power runs + Open-Meteo archive
  T / RH over those activities at the median start location, cached per day; fallback
  100 m / 25 °C / 75 % (`backend/engine/racepower/athlete.py:209`).
- **AeT** via `thresholds.estimate` + `zones.training_targets`; **priors** = standard-distance
  road runs; auto prior = the fastest of the year; **events** = upcoming season-plan events with
  their matched peak.

`/inputs` is memoised for 10 min per (dataset, day, plan mtime) (`backend/api/racepower.py:64`).

## Prediction (`/predict`)

Every derived input can be overridden in `PredictIn` (`backend/api/racepower.py:180`); `used`
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

`race_conditions` (`backend/engine/racepower/weather.py:444`) tries, in order, and logs every
attempt in `tried`:
1. CWA 登山三天 hourly (F-B0053-035), then 登山一週 day/night (F-B0053-033) — whole-file
   downloads from the fileapi, compacted and cached as `cwa_<id>.json` in the app data dir (`weather.HOME`),
   refreshed when ≥ 3 h old; a stale cache is used when there is no key or the fetch fails
   (`backend/engine/racepower/weather.py:410`). Locations match by name, else nearest ≤ 5 km;
   daytime 06–18 values only.
2. Open-Meteo forecast (≤ 16 days, lat/lon/elevation). It requests one extra day when the horizon
   allows, for races that run past midnight.
3. Open-Meteo archive climatology: the same ±7-day window in the last 5 years, temperature
   lapse-corrected to the target elevation (−6.5 °C/km).
4. Manual (`values: None`, the page's editable fields).

The response also carries `hourly`: `[{t, temp_c, rh_pct, dew_c}]`, with `t` as naive local time
(UTC+8, `YYYY-MM-DDTHH:MM`). It covers the event days plus the day after. Only the CWA 3-day
product and the Open-Meteo forecast provide it; the weekly blocks, climatology and manual give
`null`. `hourly_at` (`backend/engine/racepower/weather.py:355`) interpolates temperature and dew
point linearly between the bracketing rows and rebuilds RH from them. It takes the edge row up to
1.5 h past either end, and returns nothing beyond that.

CWA key: `CWA_API_KEY` env var, else `weather.json` in the app data dir (`weather.KEY_PATH`); the API only returns it
masked (`backend/engine/racepower/weather.py:56`). Peaks: `backend/data/baiyue.json`
(100 百岳 + 103 小百岳: name, elevation_m, lat, lon, flags), built by
`backend/scripts/build_baiyue.py`; `achievements.load_peaks` now returns only 百岳 by default.

## API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/v1/racepower/inputs?refresh=` | the derived inputs (`backend/api/racepower.py:80`) |
| GET | `/api/v1/racepower/peaks?q=&baiyue_only=` | peaks list (`backend/api/racepower.py:85`) |
| GET | `/api/v1/racepower/weather?date=&days=&event_id=&peak=&lat=&lon=&elevation=&cwa=` | provider, values, hourly, tried, location, fetched_at (`backend/api/racepower.py:107`) |
| GET / POST | `/api/v1/racepower/weather/key` | masked key status / save (10–80 chars, no spaces) (`backend/api/racepower.py:144`) |
| POST | `/api/v1/racepower/predict` | v1, unchanged: type, used, env, result, tasks, zones, warnings (百岳 adds biggest_day) (`backend/api/racepower.py:211`) |
| POST | `/api/v1/racepower/course` | multipart `file` (.gpx/.fit) + segmentation options → `course_id` (content sha1; the Track is kept in a 20-entry LRU), totals, segments, profile ≤ 1500 points, climbs, waypoints, warnings (`backend/api/racepower.py:424`) |
| POST | `/api/v1/racepower/plan` | `PlanIn` (`backend/api/racepower.py:535`) = `PredictIn` + mode, targets, course ref (`course_id` + options, or manual), strategy, hills, acclimatisation, locks, start time, aid stations, day splits, terrain, W′ curve, `hourly` (the /weather rows) and `hourly_heat` (default true) → summary (incl. `heat`, `strategy`), effort, segments (incl. temp_c / dew_c / rh_pct / heat_pct / heat_clock / heat_src), heat_profile, days (百岳), compare, crosscheck, v1, course_name, warnings; unknown `course_id` → 410 (`backend/api/racepower.py:645`) |
| GET | `/api/v1/racepower/grade-model` | gait-aware RE(g) (run / walk bins, walk share, technicality) / v_max(g) / v_h(g), the HR hike-window summary and its basis (`backend/api/racepower.py:479`) |
| GET / POST | `/api/v1/racepower/solo-hikes` | the opted-in solo hikes (`{"files": [.wko4 names]}`); only these calibrate EP/h, the walking model, the hike back-test and the 登山 conversion (`backend/api/racepower.py:490`) |
| GET / POST | `/api/v1/racepower/backtest`, `/backtest/run` | stored back-test + run state / start a background run (`backend/api/racepower.py:650`, `backend/api/racepower.py:656`) |
| POST | `/api/v1/racepower/export/csv` | the /plan output as CSV, UTF-8 with BOM; same body as /plan plus `name`; `Content-Disposition` (RFC 5987) and a percent-encoded `X-Filename` (`backend/api/racepower.py:706`) |
| POST | `/api/v1/racepower/export/coros` | the plan as a COROS structured workout; `push: true` adds it to the library and, for a future date, the calendar (`backend/api/racepower.py:729`) |
| GET | `/api/v1/racepower/page` | `backend/static/racepower.html` (`backend/api/racepower.py:374`) |

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

- **Sustainable power** (`backend/engine/racepower/difficulty.py:36`):
  - F1 is Riegel for T ≥ TTE, anchored at `cp`. For a two-anchor source that is mFTP with the fit's
    TTE, the power at TTE as F1 defines it (docs/research/cp-test-protocols.md §1B.3).
  - F2 is cp2 + W′/T up to min(1200 s, TTE/2), on the CP test's pair.
  - F3 is the log-linear bridge in between.
  - t_lim is solved by bisection.
- **Personal RE(g), gait-aware** (`backend/engine/racepower/grade_model.py:150`):
  - The input is 100 m windows of every outdoor run with power (365 days, disk-cached per
    activity; each window carries HR and its running share, the share of time at cadence
    ≥ 65 strides/min = 130 spm, workout_review.RUN_CADENCE).
  - Running windows fit `run`, 2 % bins, shrunk n/(n+30) towards Minetti's running prior
    RE_flat·Cr(0)/Cr(g) with the 0.9 downhill floor.
  - Walked windows fit `walk`, towards Minetti's walking prior RE_flat·Cr(0)/Cw(g) (same paper;
    that Stryd follows walking cost is 待驗證).
  - Per bin, the athlete's majority gait picks the curve (推估).
  - Trail technicality factor (推估): on flats and descents (g ≤ +2 %), the median actual ÷
    predicted RE over the athlete's own trail running windows. It is computed per intensity class
    when there are ≥ 30 windows, bounded 0.6–1.2, and applied only to trail plans (race class).
  - v_max(g) is the p90 speed of the downhill running bins. For road plans RE(0) is v1's
    CVI-adjusted road RE.
  - A per-class RE(g) replaces the pooled one only when the stored back-test shows the class
    errors clearly differ AND the race class's own fit is better (`backtest.class_model_flag`).
    On 2026-09-30 it does not.
- **Walking speed** v_h(g), shrunk towards Tobler: solo hikes' windows plus the HR-filtered steep
  windows of every hike (`hikehr`). Altitude is relative to the median elevation of those windows.
- **HR-filtered hike windows** (`backend/engine/racepower/hikehr.py:54`, user-specified filter):
  moving > 1.5 km/h, HR ≥ AeT (as of the trip date), ≥ 3 consecutive 100 m windows, grade ≥ 10 %.
  Windows above 2000 m/h (the vertical-kilometre record) are dropped as elevation noise. They
  are used for:
  - VAM per HR band × grade band (定義);
  - the personal altitude factor: ln VAM on elevation with grade × HR-cell fixed effects,
    compared with Wehrlin's −6.3 %/1000 m (推估, needs ≥ 30 windows over ≥ 800 m);
  - multi-day fatigue: HR at the same VAM, day n − day 1 (F17, no external source);
  - the walking model's steep bins.
  Group-paced total times and EP/h stay out.
- **Allocation** (`backend/engine/racepower/pacing.py:77`): uᵢ = h(g)·aᵢ·s(τ); hill elasticity
  up +5 % (≤ 12 %) at ≥ 8 %, down −10 % at ≤ −10 % (user-adjustable); ramp σ ±0–5 % (default 2 %,
  trail positive 3 %); downhill cap; locked segments; F16 keeps the time-weighted average.
  Solvers: power (`backend/engine/racepower/pacing.py:139`), time, auto
  (`backend/engine/racepower/pacing.py:153`). The W′ budget F11b
  (`backend/engine/racepower/pacing.py:165`) shrinks α while a stretch above CP spends > 0.75 W′.
- **W′ curve** (display only): WKO5's dfrc port (default, `backend/engine/racepower/pacing.py:246`)
  or Skiba with Vassallo 2020's running-refit τ 372·e^(−0.02D)+102
  (`backend/engine/racepower/pacing.py:224`). The 2012 cycling τ option was removed from the page
  (user, 2026-10-01); an old `wbal: "skiba"` request is mapped to the WKO5 curve.
- **Environment per segment** (`backend/engine/racepower/env.py:157`): v1's M with each segment's
  mean elevation; acclimatised = the pressure polynomial (the workbook / GoldenCheetah
  coefficients, attributed to Péronnet 1991 there — not Bassett), unacclimatised = Wehrlin linear
  (`backend/engine/racepower/env.py:119`, default for 百岳), partial = their midpoint (推估).
  Bassett 1999's two curves (`backend/engine/racepower/env.py:133`) are a cross-check quoted above
  2800 m.
- **Per-segment, time-of-day heat** (road / trail, `backend/engine/racepower/planner.py:177`; 推估,
  labelled 推估):
  - `segment_factors(…, heat=[(temp, rh)…])` (`backend/engine/racepower/env.py:157`) replaces
    H_to with Hadley's penalty at each segment's own conditions. The altitude term keeps the To
    temperature, so a heat list equal to To gives exactly the single-heat Mᵢ.
  - Each segment's clock is date + start time + the time before it + aid stops up to its start +
    half its own time. The forecast at that clock comes from `hourly_at` (linear between hours).
  - The clocks depend on the times and the times on Mᵢ, so everything that uses Mᵢ is re-solved
    (M̄, the v1 whole-race time, the allocation, the W′ budget, the mode C M fixed point, the
    scaling). It stops when the largest change in cumulative time is < 1 s, after at most 8
    passes; pass 0 is the single-heat plan. In practice 2–3 passes.
  - Falls back to the single To value with a warning 「熱修正用單一溫度 X °C / 濕度 Y %（reason）」
    when there are no hourly rows (date beyond the forecast, offline, weather not fetched), no
    date / start time, or no row near the race. Segments whose clock is past the rows use the
    single value, with a count warning. `hourly_heat: false` turns it off without a warning.
  - `summary.heat` = {mode hourly | single, passes, converged, delta_s, outside, reason, badge}.
    `heat_profile` places each forecast hour at the km the plan reaches then, for the chart.
  - Forecast temperatures are used as given at the forecast point, not lapse-corrected per
    segment. 百岳 keeps one heat value: racepower-v2.md §8 limits Hadley to running segments.
- **百岳** (`planner.plan_hike`): the walking-capacity model below. The old HikeSpeed path
  (tᵢ = dᵢ/(λ_h·v_h·pack·Aᵢ·f_day/η), `planner._plan_hike_v1`) only runs when no capacity model
  can be fitted.

### 百岳 walking capacity (`backend/engine/racepower/capacity.py`)

Design: `docs/research/baiyue-from-running.md`. User decisions 2026-09-30: pack 9 kg on day 1
(2026-10-01: single-day trips without a recorded pack also default to 9 kg), −0.7 kg/day food (推估, labelled), 3-day
trips, 跟團 as the default trip kind, altitude width from the literature, solo detection as a
suggestion.

- **Windows** (caches v3: `racepower_v3_grade_hr_kt`, `racepower_v3_hike_hr_t`): every 100 m window
  carries k, cumulative moving seconds t and the HR read 60 s later (`grade_model.windows(hr_lag_s)`,
  推估). Trail walk windows: trail runs, running share < 0.5, grade ≥ 10 %, ≥ 3 consecutive, first
  window dropped, 300 m centred grade, VAM ≤ 2000 m/h. 百岳 windows: `hikehr`'s HR ≥ AeT windows,
  re-cut the same way. Pack per trip: `racepower_hike_meta.json` (`GET/POST /hike-meta`); none
  recorded → 9 kg (預設背負, multi-day and single-day). Solo trips are no longer excluded.
- **B1** Ė_AeT = W(1.5 + 3.6·v_run,AeT); v_run,AeT = median flat (|g| ≤ 2 %) road running windows
  with HR within AeT ± 3 bpm (as-of AeT), 90 days; falls back to ± 5 bpm, then 365 days (推估).
- **B2** v₀ = Pandolf⁻¹(Ė; W, L, 100g, η) by bisection; **B3** pack ratio Pandolf⁻¹(L)/Pandolf⁻¹(L₀)
  uphill, (W+L₀)/(W+L) downhill (B3', 推估).
- **B4** ln v = ln v₀ + δ(g) + α·Δz + γ·h: δ(g) = δ̄ + n/(n+30)·(bin mean − δ̄) — a personal level δ̄
  over every window plus each 2 % bin's shrunk deviation (推估; shrinking bins straight to 0 pulled
  the held-out trail segments +7.7 % slow). β (HR band) fitted at the run level inside grade-bin
  cells, fixed at 0 unless |β/SE| ≥ 2. γ fitted on the trail windows (activity × bin cells), shrunk
  to 0 with τ_γ 0.03/h. f_day = 1.0 (β unreliable), warned.
- **B7 altitude**: α = the athlete's trip-fixed-effect slope (cluster-bootstrap SE by trip) shrunk
  to Wehrlin −6.3 %/1000 m by precision weighting. τ = √(1.02² + 1.25²) = 1.61 points: Wehrlin's 8
  athletes span 4.6–7.5 (range ÷ 2.847 = 1.02; the n = 8 range-to-SD step is ours) plus half the
  Wehrlin–Coffman gap (VO2max vs fixed-HR speed, 1.25; 推估). A(z) = exp(b/100·max(0, z−300)/1000);
  acclimatised × Bassett acclimatised ÷ unacclimatised (推估). Partial acclimatisation is treated as
  unacclimatised, warned.
- **Flat / descent** (§3.4): v_flat = min(median walked flat speed of trail runs × p(L), Pandolf⁻¹
  at g); uphill never faster than flat; v_down = personal walked descent windows shrunk to
  c_cap·Tobler, × (W+2)/(W+L) ÷ η, capped at c_cap·Tobler(g), c_cap = p75 of speed ÷ Tobler over
  walked trail and hike windows ≤ −10 %.
- **B8** per segment v = cap.v(g, L_day, η, z, h, n) × Hᵢ; Hᵢ = 1 − scale·Hadley(Tᵢ, RH)/100 with
  Tᵢ = T₀ − 0.0065·(zᵢ − z₀) (`env.segment_temp`, `env.heat_term`); z₀ = `heat_ref_alt_m`, else the
  race-day altitude, else the training altitude when no race-day temperature was given; scale =
  1 − a·S. 能力上限 band = exp(p75 − mean of the residuals) (推估, ≤ 3 h).
- **Group time** = EP ÷ past group days' EP/h (p25 / p50 / p75, ≥ 3 days); clock time uses the group
  or solo moving ratio by trip kind; `summary.main` = group (跟團, default) or capacity.
- **Band** (§3.8): σ² = σ_LOO² + σ_pack² + σ_alt² + σ_time² + σ_day² (time-weighted per component,
  independence 推估); σ_LOO = the stored back-test's segment log-error SD; shares are shown.
- **Solo suggestion** (`classify_day`, §2.6, 推估): own ≥ 60 %, limited ≤ 15 % (stops per km not
  computed yet); applies only after ≥ 5 manually marked days per class with ≥ 80 % agreement
  (`classifier_validation`); the 登山紀錄 table shows the suggestion and a confirm button.
- **Gate** `validated["hike_capacity"]` (`backtest.capacity_backtest`, `store_capacity`; script
  `python -m backend.scripts.baiyue_capacity_backtest`): A (trail walk segments ≥ 300 m,
  leave-one-activity-out) and B (百岳 segments, leave-one-trip-out) each need median |time err|
  ≤ 10 %, |bias| ≤ 5 %, n ≥ 30 segments and ≥ 10 activities / ≥ 5 trips.

Result 2026-10-01 (isolated copy of the data):

| Test | n | median \|err\| | bias | p10…p90 | pass |
|---|---|---|---|---|---|
| A trail walk, LOO activity | 50 segments / 30 activities | 7.5 % | −3.9 % | −11.8…+22.5 % | yes |
| B 百岳 HR, LOO trip | 16 segments / 8 trips | 16.4 % | +4.7 % | −13.3…+50.1 % | no (n, error) |
| B-high z ≥ 2500 m | 4 / 1 trip | 18.9 % | +18.9 % | −5.6…+22.8 % | no (bias) |
| B-high: personal α −11.0 / shrunk / Wehrlin | 4 | 38.8 / 18.9 / 19.0 % | | | shrunk not worst |
| C prior order (A + B) | 66 | full 7.7 % ≤ prior 21.9 % ≤ Tobler 51.1 % | | | yes |
| D whole group days: capacity ÷ actual moving | 16 days | 56 % in 0.60–1.00 | | | no (< 80 %) |

`hike_capacity` is not validated: every 百岳 capacity time is 推估. Diagnostics (§2.4): 88 百岳
windows after re-cutting (157 before), 10 trips, elevation p10 / p50 / p90 223 / 775 / 3356 m, 28
windows ≥ 2500 m from 2 trips. Altitude slope: grade × HR cells −11.0 %/1000 m (SE 8.9); trip fixed
effects +31.8 (SE 124); day 1 only +146 (SE 147); first 2 h +54 (SE 117) — no within-trip
leverage, so α_post = −6.29 (personal weight 0.02 %). β = 0.0004 ± 0.0008 per bpm → 0. γ = +0.011
± 0.081 /h → +0.001. v_run,AeT 2.03 m/s (227 windows, AeT 142) → Ė 8.8 W/kg; level δ̄ +26.5 %;
c_cap 1.35; σ_LOO 0.18. D: the model is slower than the group on short steep days and steep
segments (≥ +15 %: capacity 2.7 vs actual 4.1 km/h), so group-day times sit near or below it.

### Heat acclimation (`backend/engine/heat.py`, `heat_data.py`)

Design: `docs/research/heat-acclimation.md`.
- Per-activity exposure: `route_weather.fill_activities` (Open-Meteo archive at each GPS activity's
  mean point, same batching and cache as the efforts; `activity_weather.json` next to the route
  index; the app's routes builder runs it). hot_min = moving minutes per archive hour × weight
  (Hadley ≥ 150 → 1, 130–150 linear, 推估).
- S: dose = min(1, hot_min/60) (+1 for a ticked heat_passive session); S += 0.214·dose·(1 − S),
  else × (1 − 0.025) (Pandolf 1998 calibration, Daanen 2018); projection with three sets (a 1.0 /
  2.3 %, 0.75 / 2.5 %, 0.35 / "1 day lost per 2 days off"). Levels 0.75 / 0.35 (推估).
- M: `env.multiplier` / `segment_factors(heat_s=(S_from, S_to), a)` scale each side's Hadley
  penalty by (1 − a·S); S_from = mean S over the 90-day training window; None = v1 bit for bit.
- Calculator: `#heat-accl` (自動 / 未適應 / 部分 0.5 / 已適應 0.9 / 自訂), `heat_acclimatisation` in
  /plan, `GET /heat-status?date=`; road / trail show 熱影響 raw → after, the range and the
  finish-time range; segments carry `heat_eff_pct`. 百岳 applies H per segment via the lapse rate.
- HRC (`heat.hr_cost`, `heat_data.steady_segments`) is an observation only.

Heat back-test (`python -m backend.scripts.heat_backtest`, route efforts with weather, isolated
copy, 2026-10-01): 271 running route efforts ≥ 20 min with power; 611 activities with exposure.
HR ~ route FE + power + moving min + time of day + β·(Hadley − 120): β = 0.224 ± 0.036 bpm per
Hadley unit. By season: early summer (May–Jun) 0.149 ± 0.054, late summer (Aug–Sep) 0.260 ±
0.045, so late − early = +0.111 ± 0.070 (the wrong sign for acclimation); summer 0.219 ± 0.039 vs
winter 0.304 ± 0.126 (−0.085 ± 0.132, not significant). β·(1 − a_hr·S): best a_hr = 0 (ΔAIC 0).
The athlete's data do not support the acclimation effect; S, H_eff and the heat sessions stay 推估
(mean S: winter 0.24, late summer 0.87).

### Modes and the validation gate (`backend/engine/racepower/planner.py:258`)

- A 目標時間 (time or pace) → power; B 目標功率 (W, %CP, or training-condition power × M;
  百岳: speed %) → time; C 通通幫我算: effort target f* (100 / 95 / 85 %, or dragged on the bar).
- Until a category (road / trail / hike) passes the back-test, the whole-race time and power are
  v1's method (Riegel F1–F3 on v1's RE or effort km × trail RE, `backend/engine/racepower/planner.py:95`;
  百岳 EP/h) and the v2 segments only distribute it (times scaled, average power kept); every
  segment target carries 推估. A passing category switches to the v2 segment sum automatically
  (flags from the stored version-2 back-test, `backtest.flags`). Manual courses
  always use v1.
- In mode C the whole-race M is the effort's own time-weighted M, so f comes out at f* exactly.
- Cross-checks: v1 /predict, Stryd's race-power table as % of the athlete's own 10 km power
  (`backend/engine/racepower/planner.py:73`; the percentages are of 10 km power, never of CP; the
  page's linear formula is not used), CVI method, Naismith, Langmuir.

### Effort bar (`backend/engine/racepower/difficulty.py:85`)

f = P̄_train / P_sus,train(T). Five levels: 輕鬆 < 80 %, 穩定 80–90, 吃力 90–97, 極限 97–100,
超出 > 100 (lower bounds inclusive; exactly 100 % is 極限). Band = the CPs the data supports
(every CP source and the lower bound, `cp.spread`) and k ± 0.01 (CP ± 3 % only without a
spread). When the lower bound exceeds the CP used, `inconsistent` is set and the bar shows the
lower-bound warning instead of a confident 超出. The multiple m = f^(1/k) and t_lim are shown.
百岳: speed needed ÷ usual speed against the athlete's EP/h quantile ratios
(`backend/engine/racepower/difficulty.py:133`). Badge 推估 until ≥ 5 race-like / test efforts
have a median f in 0.97–1.03.

Trail plans: when > 30 % of the distance is steeper than ±8 % (Stryd's validated range, van
Rassel 2026), the plan says HR first (cap LTHR, long races near AeT) and power second (推估),
and returns `summary.hr_first`. Walked grades are noted per segment.

### Intensity classes (`backend/engine/racepower/intensity.py:169`)

User feedback 2026-09-30: 「回測要搭配心率吧，如果我是 zone2 區間，感覺會不準」. Every threshold lives in
the one constant `INTENSITY`:

| Constant | Value | Source |
|---|---|---|
| zones | low < AeT, moderate AeT–LTHR, high ≥ LTHR | Seiler & Kjerland 2006 three zones (status.SRC_SEILER) |
| race_frac_lthr | 0.95 | Friel HR Zone 4 (SubThreshold) lower bound, `zones.FRIEL_HR` |
| aet_frac_lthr | 0.89 | AeT fallback = top of Friel Z2 (`dataset.aethr`) |
| easy_tol_bpm | 3 | "easy" = avg HR ≤ AeT + 3 (`workout_review.AET_MARGIN`) |
| power_low / power_high | 0.80 / 0.95 × CP | Palladino three zones (`zones.PALLADINO_3ZONE`) |
| drift_easy | 0.05 | Pw:HR decoupling < 5 % = aerobic (Uphill Athlete, `status.DRIFT_GOOD`) |
| run_cadence | 65 strides/min | 130 spm walk/run (`workout_review.RUN_CADENCE`) |
| race_min_f | 0.90 | the effort bar's 吃力 cut (推估), P_sus by F1 with k −0.07, TTE 3000 s |
| majority | 0.5 | 推估 |

Rule (our composite, 推估):
- **race**: a season-plan race; or ≥ 50 % of the moving time at ≥ 0.95·LTHR, provided the power
  reaches 90 % of the sustainable power for that duration. HR high with power below that is a
  conflict and is classed steady. It guards against an LTHR that is set too low: on this athlete
  LTHR 152–156 would call 98 daily 45-min runs races. The last race rule is moving power
  ≥ 0.95·CP when CP is a real test.
- **easy**: ≥ 50 % of the time < AeT and avg HR < AeT + 3. When the average sits within ±3 bpm of
  AeT, Pw:HR drift > 5 % demotes the run to steady.
- **steady**: everything else. Without HR, the Palladino power zones decide.

With CP known only as the lower bound from the earlier runs, power only demotes. Verified by
an independent numpy recomputation on 3 real activities (easy 2026-01-25 越野 5.3 km, race
2026-01-30 路跑 5.1 km, steady 2026-02-01 路跑 5.1 km; shares and average HR within 1 %,
`test_racepower_backtest2.py`).

### Back-tests (`backend/engine/racepower/backtest.py:675`)

**Cases.** Outdoor runs ≥ 20 min with power in the last 365 days, season-plan races, the maximal
bouts of detected CP tests, and opted-in solo hikes. No group hike is ever a case.

**Time travel** (`derive(strict_as_of=True)`). Each case uses the inputs as of the day before,
with the case itself excluded:
- no WKO5 snapshot value; the PD model is refitted on the mean-max up to that day and raised to
  its lower bound;
- only thresholds and tests dated before the case (`threshold_on` no longer applies a row
  backwards, and the LTHR estimate measures every run against `cp_as_of` its own date);
- intensity classes with each activity's own-date thresholds.

**Capacity samples are gated on EFFORT, not on "race"** (2026-10-01, second revision;
`capacity_samples` in `backend/engine/racepower/athlete.py`, tags in
`backend/engine/activity_tags.py`). The user: what matters is whether the effort was maximal; they
often race by feel, and on trail power is a poor effort signal. A run is a sample when its
effective effort (the user's mark, else the auto rule) is 全力 and its activity type is not 測試:
- a user effort mark always wins: ≠ 全力 always excludes, 全力 always includes;
- auto, road: the self-paced maximal road rules below (item 3);
- auto, trail: `activity_tags.effort_hr` — moving HR ≥ 0.90 × own-date LTHR (Friel Z3 lower
  bound), ≥ 2/3 of the HR time above AeT (推估), and long rests (stops ≥ 5 min, a recording gap
  counts) ≤ 10 % of the elapsed time (推估). Same HR with more long rests is 有拼但有休息, not 全力.
  An auto trail sample also needs ≥ 10 km and ≥ 90 min moving (item 4). The rest cut comes from
  this athlete's data: on the 7 diary trail races 22–48 % of the elapsed time is "not moving" by
  the speed rule (aid stations, queues, GPS speed dropouts on steep climbs), but only 0–5 % is in
  stops ≥ 5 min; the hard mountain days with real breaks (2026-07-27, 2025-11-02, 2024-07-27,
  2024-08-18) have 11–17 %;
- a plan race is activity type 比賽 and is still matched as below, but it is no longer a sample by
  itself.

**Window.** Auto-detected samples: the last 365 days (as before — older auto samples would add
efforts the user never confirmed, from a different fitness). Runs the user marked 比賽 or 全力:
any date (`user_marked`). Each case is still predicted as of the day before with own-date
thresholds and the case excluded, so an older case is no leak; it tests the model of that time.
This brings in the diary races of 2024–2025.

The rules the auto effort uses:
1. **plan race**: a past season-plan event of any priority (路跑賽 → non-trail run, 越野賽 → trail
   run), matched by date and distance. The watch km must be within ±25 % (else no match;
   `km_ok` flags ±10 %; 推估). When several runs fall on that day, the nearest in distance is
   taken; without a distance, the longest (`match_events`, `backend/engine/racepower/maximal.py:169`).
   Since the effort revision this sets activity type 比賽 only.
2. **CP test bout**: the maximal bouts of a FIT-detected 3′/12′ test (cptest). Also a
   `workout_review` test_cp, but only when the plan, title or race says so
   (`wko5_cp_tests`, `backend/engine/racepower/backtest.py:593`). The power pattern alone gave 43
   "test" bouts from ~35 hard 5 km runs, judged against WKO5's mFTP snapshot of 175.6 W.
3. **self-paced maximal road effort**, all of:
   - distance within ±10 % of 5K / 10K / HM / M (the user's rule);
   - last-quarter HR ≥ 1.00 × LTHR for 5K / 10K, ≥ 0.95 for HM, ≥ 0.90 for M. The anchor is Friel's
     LTHR, the average HR of the last 20 min of a solo 30-min TT; the 0.95 / 0.90 are the
     Friel Z4 / Z3 lower bounds. The mapping is 推估;
   - 5K / 10K only: the 30-s peak HR ≥ observed HRmax − 10 bpm. Near-maximal HR is a criterion
     of a maximal effort (Howley, Bassett & Welch 1995, MSSE 27:1292–1301). The tolerance and
     the observed HRmax (median of the top-5 per-run peaks held ≥ 120 s in the 365 days) are
     推估;
   - an even or negative split: second-half speed ≥ 0.98 × first half (Abbiss & Laursen 2008;
     the tolerance is 推估);
   - power–duration monotonicity: the moving power is not below the best moving power of any
     earlier road run (365 days) ≥ 1.5 × as long. A mean-max curve is non-increasing by
     definition; the 1.5 × is 推估. This rule is needed because heat alone pushes this
     athlete's HR to "maximal" on summer 5 km runs: 150–158 W at a 30-s peak of 181–183 bpm,
     against 184 W held for 141 min in the half marathon.
4. **race-like trail effort** (the user's correction: their trail races are > 10 km, not standard
   distances, and always slow down in the second half, so there is no distance bucket and no
   split rule). It needs ≥ 10 km and ≥ 90 min moving, plus either:
   - average HR ≥ 0.90 × LTHR (Friel Z3) and ≥ 2/3 of the HR time above AeT (Seiler boundary;
     2/3 is 推估), or
   - a race word in the title or tags (賽 / race / 馬拉松 / marathon), with only the duration
     rule. Since the effort revision these HR checks are reported only; the auto trail effort is
     `effort_hr` above, and a race word only sets activity type 比賽.

   The power side is reported, not used to select: f, and the grade-adjusted demand through
   mode C on the own course.
5. **AeT test** (title AeT or a plan aethr row that day): a submaximal anchor. Only the HR
   model's power at its HR is checked (`aet_check`). There were none in 2026.

No rule uses the model's own P_sus, because selecting samples with the model under test would be
circular.

**Trail HR pace model** (推估, all 推估; `backend/engine/racepower/trailhr.py`, `trail_hr_model`
in `backend/engine/racepower/athlete.py`). Trail capacity from the power envelope was +46 % power
/ −35 % time off, so for trail races the planner's whole-race time (mode auto) comes from HR and
terrain; the power-based time is kept only as `crosscheck.power_envelope`, and
`summary.total_method = "trail_hr"`.
- Per trail run ≥ 45 min moving with HR (365 days before the as-of date): effort km
  E = km + gain / 153 (`SIMPLE_FORMULAS["fitted_run"]`), moving time T, x = moving HR ÷ own-date
  LTHR, v = E / T.
- Durability: `panels.workout.durability` on the moving-time axis with effort-km speed as the
  output, on runs ≥ 2 h; the decline per hour after 1 h is the run's δ; personal δ = the median,
  clamped to 0–0.15 /h. Mean multiplier over T: D̄ = 1 − δ (T − 1)² / (2T).
- v₀(x) = a + b·x by OLS on v / D̄ (≥ 6 runs, b > 0), else proportional.
- Race HR level x*: median x of earlier trail races (type 比賽) and 全力 runs ≥ 90 min; none → 0.90.
  The planner's effort target f gives x = f·x*. The time is divided by the course M (heat /
  altitude, 推估).
- The back-test reports per trail case "given HR" (the case's own moving HR), the same without
  durability, and "race level" (x* from earlier races), plus `trail_hr.race_rows` per race and
  `validated["trail_hr"]` (races n ≥ 5 and median |race-level error| ≤ 6 %). No power is needed:
  trail runs without power (2024-09-21) are cases for this model only.

Back-test 2026-10-01 (WKO5 source, read-only, the seed applied to a scratch DB copy):

| | before | after, auto only | after, with the user's marks |
|---|---|---|---|
| road capacity n / median \|time err\| | 2 / 13.0 % | 2 / 13.0 % (2025-10-18 still passes the road rules) | 1 / 6.5 % |
| trail capacity (power envelope) n / power / time | 2 / +46 % / −35 % (2025-11-02, 2026-07-27) | 0 (both now 有拼但有休息) | 4 / +37 % / 40.6 % \|err\| |
| trail HR model, 7 diary races, race level | – | – | 7.6 % \|err\|, bias −0.9 % (no durability 5.5 %) |
| trail HR model, 7 races, given HR | – | – | 9.9 % (no durability 8.8 %) |
| trail HR model, all trail cases, given HR | – | n 35: 6.9 % | n 41: 7.3 % |
| terrain mode B, trail | 10.3 % | 10.3 % | 10.3 % |

Durability does not improve the races (δ hits the 0.15 /h clamp; 7.6 % with it vs 5.5 % without),
so it stays 推估. 2025-07-26 is the worst race (−27 % at race level): its x = 1.11 sits on the
WKO5 default LTHR of 160.

**HR-based capacity** (推估; `hrcap.py`, `hr_capacity` at `backend/engine/racepower/athlete.py:1279`).
- **Points**: per outdoor road run of the 90 days, the flat (|g| ≤ 2 %) running windows 10–60 min
  in, with the 60-s-lagged HR, give one time-weighted (HR, P) point. Runs with Pw:HR drift > 5 %
  are dropped (Uphill Athlete).
- **Fit**: OLS of P on HR across the runs (runs are the unit), P_LTHR = a + b · LTHR, with a
  bootstrap 10–90 %. This extrapolates the individual submaximal HR–work-rate line, the
  Åstrand & Ryhming 1954 principle (J Appl Physiol 7:218–221). Lamberts et al. 2011 (Br J Sports
  Med 45:797–804, LSCT) found that the power at a fixed submaximal HR tracks performance
  (r 0.80–0.94). Reading P at LTHR as a threshold is 推估.
- **Valid** only with ≥ 8 runs, an HR span ≥ 15 bpm, a positive slope and R² ≥ 0.5 (推估).
- **Anchors**: P_LTHR is used as the F1 anchor at TTE 1800 s (Friel's 30-min TT, `tt30`) and at
  the as-of TTE (`tte`), single anchor, with the Ruiz-Alias W′ prior.
- **Combined**: max(power envelope, HR `tte`) when there are < 3 capacity samples in the year
  before and the fit is valid; else the envelope alone.
- **Intensity distribution**: each road run's moving power ÷ P_LTHR, and ÷ the CP in effect,
  in Palladino's three zones.
- **Uncertainty**: heat and drift raise HR at a given power, HR lags power, and the
  extrapolation distance (LTHR − the highest run HR) is reported.

1. **比賽預測回測 (capacity)**: the capacity samples. The as-of CP / W′ / TTE / k give P_sus(T)
   against the actual power (f), and mode C (f* = 1) on the activity's own course gives a time
   against the actual time. Each case also gets the HR variants and the combined rule
   (`_hr_eval`, `backend/engine/racepower/backtest.py:484`). The table k uses the case's own
   distance, and the CP is re-raised to the bound for that k. The lower-bound test runs on
   every run and fails when the actual power > P_sus(T).
   - Pass: n ≥ 5, median |time error| ≤ road 3 % / trail 6 %, and no lower-bound violation.
   - With < 5 capacity cases the tab says 「沒有全力比賽或測試紀錄，無法驗證能力模型；請做 3'/12' CP 測試或報名一場 B 級比賽」.
2. **地形模型回測 (terrain)**: mode B with the actual power, then time and per-segment speed.
   This tests only the RE(g) physics.
   - Stratified by class × grade bin (±2 %, 8 % Stryd range, 15 % walk label).
   - Trail is split into running and walking-heavy groups (≥ 50 % of the time < 130 spm, 推估).
   - Compared: the per-class RE(g) fit (LOO) against the pooled fit, and gait against no gait.

A category is validated (drops 推估, v2 segment sum) when capacity passes AND the race-like
terrain rows have a median |err| ≤ the threshold and a downhill bias ≤ +5 %. The effort bar
needs ≥ 5 race-like / test efforts with median f 0.97–1.03. Script:
`python -m backend.scripts.racepower_backtest`; result `racepower_backtest.json` (version 2).

**Result on 2026-10-01** (WKO5 source; stricter samples, the threshold fix, HR capacity): 176
cases. Runs by HR class: easy 18, steady 143, race-like 14 (before: 20 / 141 / 14). Capacity
samples in the 365 days: 1 plan race, 1 self-paced maximal 5K, 2 race-like trail efforts, 1 CP
bout (the 9/30 12′), 0 AeT tests. The 13 hard 5 km training runs are no longer samples.

| Capacity (before → after) | n | median \|time err\| | time bias | power err bias | median f |
|---|---|---|---|---|---|
| 路跑 before | 14 | 16.5 % | −16.5 % (too fast) | +13.3 % | 0.88 |
| 路跑 after | 2 | 13.0 % | −2.5 % | +2.4 % | 0.99 |
| 越野 before | 0 | – | – | – | – |
| 越野 after | 2 | 35.0 % | −35.0 % (too fast) | +46.2 % | 0.68 |
| effort bar before / after | 15 / 5 | | | | 0.88 / 0.89 |

Per case, as of the day before (power envelope | HR `tte` | HR `tt30`; P_sus vs actual and mode C time):

| Case | actual | envelope | HR tte | HR tt30 |
|---|---|---|---|---|
| 2025-12-21 半馬 (plan A), 141 min | 184.3 W | 170.2 W (−7.6 %), time +10.5 % | 153.0 W (−17.0 %), +24.3 % | 145.4 W (−21.1 %), +31.6 % |
| 2026-09-30 CP 12′ | 220.9 W | 213.1 W (−3.5 %; before 212.9, −3.6 %) | 180.2 W (−18.4 %) | 180.2 W (−18.4 %) |
| 2025-10-18 路跑 5.0 km, 41 min (5K rule) | 161.0 W | 181.2 W (+12.5 %), −15.5 % | 163.6 W (+1.6 %), −6.5 % | 160.1 W (−0.6 %), −4.1 % |
| 2025-11-02 越野 14.4 km / 1432 m, 232 min | 120.4 W | 176.9 W, −37.3 % | 141.8 W, −20.4 % | 141.5 W, −20.2 % |
| 2026-07-27 越野 11.7 km / 990 m, 154 min | 125.7 W | 182.9 W, −32.7 % | 144.8 W, −13.5 % | 143.5 W, −12.7 % |

The HR fit is not valid on any date: today it has 24 runs, an HR span of 20 bpm, slope 0.18 W/bpm
and R² 0.01, so P_LTHR 162 W (160–164) is just the mean training power. The road runs are all
held at about the same power, and the HR differences between them come from heat, drift and
fatigue. With an invalid fit the combined rule never applies, so the combined columns equal the
power envelope. On the two cases that matter, HR is worse: the half marathon is −17 % against
−7.6 %, and the 12′ bout −18 % against −3.5 %. HR stays 推估 and is not used for predictions.

Training intensity (road runs, 90 days): against the CP 204 W, the median is 76 %, 90th
percentile 81 %. 86 % of the moving time is < 80 % CP, 14 % is at 80–95 %, none ≥ 95 %. That
supports the user's suspicion: there are few hard efforts, and the envelope cannot show the
capacity the 12′ test did. Against the invalid HR P_LTHR it reads 95 %, which is meaningless.

Lower bound: still 1 of 175 runs. The 2025-12-21 half (141 min at 184 W) is above the as-of
P_sus of 170 W (CP 189, raised to its bound). Nothing is validated. The terrain back-test is
unchanged within 0.1 point (路跑 139 runs 3.7 %, 越野 36 runs 10.3 %, downhill bias +13.2 %).

**Before (2026-09-30)**, after the plan CP 204 W test row was applied: 177 cases. Runs by class:
easy 20, steady 142, race-like 14. Plus one CP-test bout. Hikes: 0 solo.

比賽預測回測（能力）:

| Category | n | median \|time err\| | bias | 10–90 % | power err (bias) | median f | pass |
|---|---|---|---|---|---|---|---|
| 路跑 | 14 | 16.5 % | −16.5 % | −21.2…−9.0 % | +13.3 % | 0.88 | no |
| 越野 | 0 | – | – | – | – | – | no (n < 5) |
| CP test 12′ (2026-09-30, as of 09-29) | 1 | – | – | – | P_sus 212.9 vs 220.9 W (−3.6 %) | – | – |

The lower bound is violated by 1 of 176 runs: 2025-12-21 路跑 20.5 km, 141 min at 184 W, against
the as-of model's 170 W. The model before that day had no run to show it. Effort bar: n 15,
median f 0.88, not validated. The road race-like runs are short hard training runs (≈ 5 km at
HR ≥ 0.95 LTHR), not maximal. The model's P_sus is 13 % above what they held, so the mode C
times are 16.5 % fast. Capacity cannot be validated until there are ≥ 5 tests or races.

地形模型回測（給實際功率）:

| Group | n | median \|err\| | bias | 10–90 % | v1 \|err\| | downhill bias |
|---|---|---|---|---|---|---|
| 路跑 | 140 | 3.7 % | −3.6 % | −5.0…−2.3 % | 2.4 % | – |
| 越野 | 36 | 10.3 % | −9.2 % | −15.9…+10.6 % | 9.7 % | +13.2 % |
| 越野 跑為主 | 3 | 9.2 % | −9.2 % | −15.6…−7.3 % | | |
| 越野 走為主 | 33 | 10.4 % | −9.2 % | −14.5…+13.0 % | | |
| 輕鬆 | 20 | 12.4 % | −10.1 % | −17.3…+16.1 % | segments 14.0 % | |
| 穩定 | 142 | 3.7 % | −3.6 % | −6.1…−2.2 % | segments 4.0 % | |
| 比賽強度 | 14 | 4.0 % | −4.0 % | −5.9…−1.7 % | segments 2.1 % | |

Segment speed bias by class × grade (n). Positive means the model is too fast:

| Class | ≤ −15 % | −15…−8 % | −8…−2 % | ±2 % | +2…+8 % | +8…+15 % | ≥ +15 % |
|---|---|---|---|---|---|---|---|
| 輕鬆 | +20.1 (38) | +7.0 (18) | +20.5 (16) | +6.3 (25) | +14.1 (20) | +11.7 (20) | +1.9 (37) |
| 穩定 | +22.5 (34) | +8.1 (26) | +8.4 (16) | +2.6 (650) | +7.8 (17) | +5.3 (24) | −1.0 (36) |
| 比賽強度 | – | – | – | +1.7 (89) | – | – | – |
| 全部 | +21.8 (72) | +7.9 (44) | +14.0 (32) | +2.6 (764) | +9.2 (37) | +7.2 (44) | −0.8 (73) |

Findings:
- The grade model does not fail only at Zone 2. Descents are too fast in both the easy and the
  steady classes (+20 / +23 % at ≤ −15 %), while flats hold at 2–6 % in every class. Easy runs
  are worse on every grade.
- The class spread is 11.9 points, so the errors clearly differ by class. But no class's own
  LOO fit beats the pooled fit (easy 19.9 vs 14.0 %, steady 4.0 vs 4.0 %, race 2.5 vs 2.1 %),
  so the pooled model stays.
- Gait split time |err|: 3.8 % → 3.9 % (no gain on these runs).
- HR hike windows: 157. The personal altitude factor is −9.9 %/1000 m (n 153) against Wehrlin
  −6.3 %. Fatigue on day 2: −1.2 bpm (2 trips).
- Nothing is validated.

Capacity before / after on the same data:
- 21.1 km road: before (activities CP 174, WKO5 TTE, k −0.10, flat RE 0.908) 3:00:00 at 146 W.
  After: PD mFTP 191.5 raised to the bound (204.8 at k −0.07, 214.3 at the table k −0.10), TTE
  1884, plan CP 204 + W′ 13.1 kJ, RE 0.871. That gives 2:29:48 at 183 W (k −0.10) or 2:29:36 at
  184 W (k −0.07).
- The 2025-12-21 run (2.36 h, 184.2 W moving): f 1.23 → 1.00.
- The bound includes each run's moving-time average (altitude-normalised) beside the elapsed
  mean-max envelope. The elapsed mean-max of that run was only 178.8 W.

**COROS vs TP (2026-10-01, read-only).** The 2025-12-21 half's envelope was 180.4 W on COROS and
152.9 W on TP (time +3.8 % vs +24.0 %). Settings were equal (weight 66.3, k −0.07, Riegel invalid
on both) and the matched runs' mean-max curves identical. The whole difference is one TP-only file,
`tp_2025_12_14_3477204875.fit`: 17 min, 12.3 km (≈ 43 km/h), mean 899 W / max 1462 W, COROS-recorded,
no Stryd fields, not in the COROS folder (推定: deleted on COROS). Inside the 90-day window it made
the PD refit invalid → the 3–20 min `activities` fit (W′ 367 kJ) raised to the lower bound, CP 164.5
with TTE 3000 → P_sus 152.9 W. `implausible()` could not drop it (no reference CP before the first
plan CP in strict mode). Excluding only that file on the old code gives TP = COROS exactly
(pdmodel 200.5 W, TTE 1882, P_sus 180.4 W). It also shifted TP's LTHR estimates (cp_as_of) for
~90 days, hence small trail-HR differences. Other set differences (TP lacks 10 COROS Stryd runs of
2025-03/04; 3 short TP-only runs) changed no envelope point the half used. The watch-power rule
removes the file; after the change TP gives 180.4 W / +3.8 % too. Back-tests after the change
(TP): road capacity n 1, |time err| 3.8 % (before 24.0 %); trail capacity n 2 (the 2024 races are
watch power → HR model only), 23.8 % (before 4 at 36.1 %); 2025-07-26 trail CP 262.9 → 191.5 W;
trail HR model and the CP test unchanged (12′: P_sus 213.0 vs 220.9 W).

### Page

Tabs 計算 / 準確度. Mode switch (sticky on phones), course manual / GPX (drop or file button),
segmentation options, strategy, hill elasticity, acclimatisation, display power / pace, W′ curve,
start time, aid stations. Results: tiles, draggable effort bar with band, Palladino zone strip,
ECharts profile (segment-class bands, target step line, click ↔ table row, day split taps, zoom),
segment table (editable power locks, 百岳 terrain per segment) or cards below 700 px, COROS export
(preview, then confirm to push), 「匯出 CSV」. The 熱 row toggles the per-segment heat (推估 badge;
on by default). The hourly rows go with the plan only when the race-day temperature / humidity
are the fetched values, not ones the user typed. With hourly heat, the profile gets an °C axis with
the forecast temperature (labelled with the hour) and dew point at the km reached each hour. The
table has a 熱 column (temperature used and the Hadley penalty; the tooltip shows dew point and the
hour). References stay out of the page.

### CSV export

`POST /export/csv` runs `make_plan` and formats it with `csvplan.plan_csv`, so no maths is
duplicated. The body is UTF-8 with a BOM, CRLF rows. The file is named
`賽事功率_<course>_<date>.csv`. The course is the page's event name, else the GPX track name, else
type + km. The date is the race date, else the day computed.
- Header block (`key, value[, source]`): course, race date, type, km, gain, loss, segments, top
  elevation, mode (with the effort target), total method + 推估, finish time and ETA, average
  power and %CP, pace, effort, mean M, CP / CP2 / W′ / TTE / k / RE / weight (百岳: EP/h, pack,
  AeT) with their sources, strategy and amount, hill elasticity (set and used),
  acclimatisation, heat mode, start time, aid stations, time computed.
- One row per segment: 段, 天, start / end km, distance, gain / loss, grade, class, target W, %CP,
  zone, pace, km/h, segment time, cumulative time, ETA (with aid stops), M (百岳: A), temperature,
  dew point, heat %, heat hour, notes, badge. A total row closes the table.

### COROS export

`coros_payload` / `push_to_coros` (`backend/api/racepower.py:666`, `backend/api/racepower.py:686`) map segments through the existing
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
and the mocked COROS push. V-BT now covers the capacity / terrain summaries, validated flags and
the version-2 store.

`backend/tests/test_racepower_export.py` covers:
- the per-segment heat list in `segment_factors`; `hourly_at` interpolation and edges; hourly rows
  from Open-Meteo, and none from climatology;
- a synthetic forecast (cool, then 32 °C / 70 %) giving the expected Mᵢ per segment from an
  independent interpolation, convergence in ≤ 8 passes, self-consistency of the fixed point, and
  a later start picking warmer hours;
- a flat forecast equal to To giving the single-M result (manual and GPX courses, auto and
  time modes);
- the fallbacks: no hourly, no start time, switched off, another day, and a forecast ending
  mid-race;
- the CSV: BOM, filename, header block, one row per /plan segment with matching power / M /
  temperature / ETA, totals, GPX and 百岳 files.

`backend/tests/test_racepower_backtest2.py` covers:
- the INTENSITY constants against `zones`, and the classifier (easy / steady / race, the HR-vs-power
  conflict, the drift tie-break, power only, walk share);
- the lower-bound algebra, the two-anchor p_sus (F2 on the test pair, F1 at mFTP, continuity,
  t_lim round trip), and the effort band from the spread;
- CP-test detection on the 2026-09-30 laps (non-maximal 3′, single bout with the Ruiz-Alias
  prior), the two-point case, and non-overlapping `workout_review.cp_test` windows;
- the hike window filter (consecutive rule, flats, AeT, VAM cap), recovery of a known −6.3 %/km
  altitude factor, and the fatigue HR shift;
- gait bins, the trail technicality factor, `tobler_eph`, and the k table at the case distance;
- golden tests: an independent recomputation of the class, shares and average HR on 3 real
  activities; group hikes are absent from the 登山 conversion fit.

`backend/tests/test_racepower_capacity_samples.py` covers (temporary plans only):
- `threshold_on` in the past, on the day and for a future row; `Dataset.setting` / `cp` /
  `aethr` falling back to WKO5 before the row and using it from that day on; a test dated after
  the last run still applying today through `zones._on_day`;
- the standard-distance ±10 % buckets, the 30-s peak HR from the histogram and the robust
  observed HRmax;
- every road rule alone, including HM / M without the HRmax rule and the monotonicity check on
  a hot 5 km;
- the trail rules, including that no split rule exists and a race word in the title;
- event matching: a warm-up jog that day, the wrong kind, no distance, too far off;
- HR capacity: the window filters, the OLS against `numpy.polyfit`, P_LTHR, the bootstrap
  range, and the n / span / R² validity; the Palladino-zone shares;
- `hr_psus` against F1 / F2 by hand, the combined rule (only when few samples and a valid fit),
  and the HR columns of the summary.

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
- Heat per segment and hour is only for road / trail plans with an hourly forecast (CWA 3-day or
  Open-Meteo ≤ 16 days); 百岳 and farther dates use one value. The forecast temperature is not
  lapse-corrected to each segment's elevation.
- Sex defaults to male while the season-plan profile is empty.
- Back-test:
  - one past A race, no solo hikes; 5 capacity samples in the year (≥ 5 per category are needed),
    so capacity cannot pass yet;
  - 2025-10-18 路跑 5.0 km still passes every road rule (a weekday training run by the user's
    account); only the user's mark (`backend/scripts/seed_activity_tags.py`) removes it. The
    trail runs 2025-11-02 and 2026-07-27 are now auto 有拼但有休息; 2026-07-27's long-rest share
    (11 %) is close to the 10 % cut;
  - `validated["trail_hr"]` is stored but `flags()` does not return it, so the planner's trail
    HR total keeps its 推估 badge even after a pass; the durability δ sits on its 0.15 /h clamp;
  - the chosen data source may have no trail history: the planner's trail HR model then falls
    back to the WKO5 dataset. Since 2026-10-01 COROS trail runs are trail in the FIT dataset (the
    app DB's `trail_classification`, `backend/engine/wko5expr/fitdataset.py`); before that the
    COROS back-test had trail n = 0 because COROS FITs carry no trail sub_sport;
  - many 2024–2025 dates use WKO5's default LTHR of 160 (not set), so x = HR / LTHR is
    unreliable there; 2024-04-13's power is partial (NP 81), so its power-envelope error (+122 %)
    is meaningless — the HR model still works on it;
  - on trail, P_sus overstates race power by ~46 %: walking-heavy courses at 120–126 W. Mode C
    on trail needs a walking-aware demand, not the road power curve;
  - the HR-based capacity is not identifiable on this athlete's data (R² 0.01). It needs runs
    at clearly different powers, e.g. a progressive run or a submaximal step test;
  - an LTHR of 152–156 looks low next to the 12′ test (HR 155 → 171), so many daily runs read
    HR-high and are demoted only by the power check;
  - `Dataset.cp` before the first plan CP row is WKO5's current mFTP snapshot (WKO5 has no
    dated mFTP). The back-test does not use it (it uses `cp_as_of`), but `workout_review`'s
    power-pattern test_cp rule labels ~35 past hard 5 km runs as CP tests against it.
- The PD refit gives 191.5 W against the research script's 196.1 W with the same FIT file (curve
  sampling). Route-specific technicality: `GaitRE.route_tech` overrides the per-class factor
when set, but nothing fills it from the routes module yet.
- Saving a GPX course onto a season-plan event is not done.

## Change History

| Date | Source | Feature SRS | Summary |
|------|--------|-------------|---------|
| 2026-09-30 | code-sync | N/A | Created from brownfield analysis — SuperPower Calculator port on the athlete's data, trail and 百岳 extensions, CWA / Open-Meteo race-day weather; D2 altitude normalisation added and the CVI cross-check source clarified during sync |
| 2026-09-30 | feature | docs/research/racepower-v2.md | v2: three modes, five-level effort bar (80/90/97/100), pacing strategies, GPX / manual courses with per-segment allocation and profile, per-segment altitude with acclimatisation switch (百岳 default unacclimatised, partial = 推估), hill elasticity +5/−10 %, aid stops in the ETA, 「看 30 秒平均功率」, COROS export (mocked push), leave-one-out back-test + 準確度 tab gating the 推估 labels (nothing validated yet); §3C applied: Skiba τ labelled, Pandolf uphill only, altitude polynomial not attributed to Bassett, Stryd percentages are of 10 km power |
| 2026-09-30 | feature | user feedback + capacity review + docs/research/cp-test-protocols.md | Back-test v2: HR / power intensity classes (Seiler / Friel / Palladino constants, own-date thresholds); two back-tests (比賽預測 capacity on race-like + CP-test bouts with the lower-bound test on every run; 地形模型 by class × grade bin, trail running vs walking-heavy); group hikes out of every target-time calibration (solo opt-in list, Tobler EP/h fallback, equivalence 登山 → EP 推估), HR-filtered steep hike windows kept for VAM / altitude factor / fatigue / walking bins; capacity: PD-model refit (incl. synced FIT) as the mFTP/TTE anchor + CP-test pair for F2, CP lower bound (k-consistent in /predict), detected CP tests as suggestions (non-maximal bout → single bout with Ruiz-Alias W′ prior), `workout_review.cp_test` non-overlapping windows; k only from race-like priors at the case distance; road RE CVI-adjusted over the year; gait-aware RE(g) + trail technicality; effort band from the CP spread; HR-first note on steep trail courses |
| 2026-10-01 | feature | docs/research/baiyue-from-running.md, docs/research/heat-acclimation.md | 百岳 walking capacity (capacity.py B1–B8, window caches v3, pack per trip, group vs capacity time, band, solo suggestion, `validated["hike_capacity"]` back-test — not passed) replaces the Tobler fallback; heat acclimation S, H_eff on both sides of M, `#heat-accl`, per-activity exposure, heat back-test (does not support acclimation), Event.heat, /heat-status, /hike-meta |
| 2026-10-01 | bugfix | user request | Capacity back-test: `threshold_on` never applies a row backwards; the LTHR estimate uses `cp_as_of`, so no later CP; capacity samples (maximal.py) replace the HR race class: plan events matched by date + kind + distance, CP bouts, self-paced maximal road (distance ±10 %, last-quarter HR, HRmax, split, monotonicity) and race-like trail (≥ 10 km, ≥ 90 min, HR; no split rule); personal k / table prior from the samples only; HR-based capacity (hrcap.py, 推估, invalid on this data: R² 0.01) with tt30 / tte anchors and a combined second lower bound; training-intensity distribution; script `--source` / `--out` |
| 2026-10-01 | feature | user request (activity tags) | Capacity samples gated on effort (activity_tags: user mark wins; road = road_maximal, trail = HR on moving time + long rests ≥ 5 min ≤ 10 %), plan races only set type 比賽; user-marked races / 全力 over the full history (auto 365 d); trail HR pace model (trailhr.py, effort km vs HR / LTHR, durability, race HR level; 推估) as the planner's trail total, power as cross-check; back-test `trail_hr`, no-power trail cases, `--tags-db`; seed script |
| 2026-10-01 | bugfix | docs/research/unsourced-rules.md §0.10 step 0 | COROS / TP back-test prerequisites: the FIT dataset reads trail / road from the app DB (overrides, duplicates), takes thresholds / weight from plan → athlete_settings → as-of estimates (no WKO5 by default), and the activity-tag seed matches COROS / TP races by start time |
| 2026-10-01 | bugfix | user request (COROS vs TP back-test) | Cause of the COROS / TP difference (one TP-only junk watch-power file); power models use Stryd power only by default (`power.accept_watch_power`), watch-power runs are no-power back-test cases, `power_source` in derive / back-test rows; cp_as_of prefers usable power (推估) |
| 2026-10-01 | feature | user request (bad activity files) | Bad activity files (car / bike speed, impossible power) are no run, envelope point, capacity sample or back-test case (not in `ds.workouts`); the synced FIT curves / CP-test scan drop them (`cptest.bad_files`) |
| 2026-09-30 | feature | user request | CSV export (`POST /export/csv`, `csvplan.py`, UTF-8 BOM, header block + one row per segment, 「匯出 CSV」 button); per-segment, time-of-day heat (road / trail): /weather returns hourly rows, the plan maps each segment's ETA to the forecast hour and applies Hadley there (推估), iterating to max |Δ cumulative time| < 1 s; falls back to the single value with a warning; °C axis on the profile, 熱 column in the table |
