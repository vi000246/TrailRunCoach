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
| `backtest.py` (v2) | Two leave-one-out back-tests (capacity, terrain), pass rule, stored flags | `backend/engine/racepower/backtest.py:269` |
| `intensity.py` (v2) | Per-activity HR / power stats, the easy / steady / race classifier, INTENSITY constant | `backend/engine/racepower/intensity.py:169` |
| `cptest.py` (v2) | 3′/12′ tests in the synced FIT files, non-maximal bout detection, single-bout CP, FIT mean-max curves | `backend/engine/racepower/cptest.py:49` |
| `hikehr.py` (v2) | HR-filtered steep hike windows: VAM, personal altitude factor, multi-day fatigue | `backend/engine/racepower/hikehr.py:54` |

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
  ≥ 1.3 × the other laps' median power (自組). The mean-max is taken inside the lap. A bout is
  non-maximal when the 3′ power is not above the 12′ power (the workbook's "falling" check) or
  its peak HR is ≥ 10 bpm below the other bout's (自組; 2026-09-30: 146 vs 171 bpm). With one
  maximal bout, CP = P − W′/t, with the W′ prior 13.1 ± 4.0 kJ for men and 6.4 ± 2.2 kJ for
  women (Ruiz-Alias et al. 2025, amateur Stryd 9/3) and the range at ± 1 SD. For 2026-09-30:
  12′ 220.9 W → CP 202.7 W (197.1–208.3). `workout_review.cp_test` now takes non-overlapping
  windows (the 3′ is ≥ 10 min away from the 12′), and falls back to the same single-bout estimate.
  A separate 3′ at ≥ 98 % CP still marks the session test_cp.
- **TTE**: that of the default source (PD refit), else the WKO5 snapshot, else 3000 s.
- **Personal Riegel k**: the same ln-ln fit, but only on the envelope of the HR race-like runs,
  which need ≥ 3 activities. Otherwise k = −0.07 (≈ Stryd's table). The table prior
  (`auto_prior`) is only a race-like standard-distance run, never a training run. The back-test
  looks the table up at the case's own distance (trail: effort distance).
- **RE**: road = median over road runs ≥ 20 min with CVI < 51, each adjusted to flat with the
  workbook's CVI adjustment (+0.01 per category), so it rests on 140 runs instead of the few
  under 25 ft/mi (`road_cvi` is then 0). Trail = median over trail runs (≥ 150 m climb, ≥ 45 min)
  of `(effort m / moving s) / (W/kg)` for the fitted_run / itra / scarf divisors
  (`backend/engine/racepower/re.py:64`).
- **Hiking EP/h**: per calendar day from `achievements.build_achievements`, the same rules as
  before, but only over hikes the user opted in as solo (`racepower_solo_hikes.json`,
  `GET/POST /solo-hikes`, `backend/engine/racepower/athlete.py:197`). 「百岳多為跟團，速度不代表個人能力，不列入目標時間推算」:
  without solo days the 百岳 v1 time uses Tobler's EP/h on the course (`hike.tobler_eph`,
  two-segment approximation, 推估). The group days are still listed, marked 跟團，不計.
- **Intensity class** of every run (`backend/engine/racepower/athlete.py:413`) uses the thresholds
  as of that run's date (`thresholds_as_of`, `backend/engine/racepower/athlete.py:325`). LTHR /
  AeT come from a plan test dated on or before that day, else `thresholds.estimate` on the runs
  before it, else WKO5. AeT falls back to 0.89 × LTHR. CP comes from a dated plan test, else
  the lower bound from the earlier runs (demotion only).
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
| GET | `/api/v1/racepower/grade-model` | gait-aware RE(g) (run / walk bins, walk share, technicality) / v_max(g) / v_h(g), the HR hike-window summary and its basis (`backend/api/racepower.py:477`) |
| GET / POST | `/api/v1/racepower/solo-hikes` | the opted-in solo hikes (`{"files": [.wko4 names]}`); only these calibrate EP/h, the walking model, the hike back-test and the 登山 conversion (`backend/api/racepower.py:488`) |
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
  - Per bin, the athlete's majority gait picks the curve (自組).
  - Trail technicality factor (自組): on flats and descents (g ≤ +2 %), the median actual ÷
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
    compared with Wehrlin's −6.3 %/1000 m (自組, needs ≥ 30 windows over ≥ 800 m);
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
  (flags from the stored version-2 back-test, `backtest.flags`). Manual courses
  always use v1.
- In mode C the whole-race M is the effort's own time-weighted M, so f comes out at f* exactly.
- Cross-checks: v1 /predict, Stryd's race-power table as % of the athlete's own 10 km power
  (`backend/engine/racepower/planner.py:70`; the percentages are of 10 km power, never of CP; the
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
| race_min_f | 0.90 | the effort bar's 吃力 cut (自組), P_sus by F1 with k −0.07, TTE 3000 s |
| majority | 0.5 | 自組 |

Rule (our composite, 自組):
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

### Back-tests (`backend/engine/racepower/backtest.py:269`)

**Cases.** Outdoor runs ≥ 20 min with power in the last 365 days, season-plan races, the maximal
bouts of detected CP tests, and opted-in solo hikes. No group hike is ever a case.

**Time travel** (`derive(strict_as_of=True)`). Each case uses the inputs as of the day before,
with the case itself excluded:
- no WKO5 snapshot value; the PD model is refitted on the mean-max up to that day and raised to
  its lower bound;
- only thresholds and tests dated before the case;
- intensity classes with each activity's own-date thresholds.

Remaining leak: `thresholds.estimate` filters its runs with `ds.cp`. That is the plan CP applied
backwards by `threshold_on`, or WKO5's current mFTP (see Known gaps).

1. **比賽預測回測 (capacity)**: race-like runs, plan races and CP-test bouts. The as-of CP / W′ /
   TTE / k give P_sus(T) against the actual power (f), and mode C (f* = 1) on the activity's own
   course gives a time against the actual time. The table k uses the case's own distance, and
   the CP is re-raised to the bound for that k. The lower-bound test runs on every run and
   fails when the actual power > P_sus(T).
   - Pass: n ≥ 5, median |time error| ≤ road 3 % / trail 6 %, and no lower-bound violation.
   - With < 5 capacity cases the tab says 「沒有全力比賽或測試紀錄，無法驗證能力模型；請做 3'/12' CP 測試或報名一場 B 級比賽」.
2. **地形模型回測 (terrain)**: mode B with the actual power, then time and per-segment speed.
   This tests only the RE(g) physics.
   - Stratified by class × grade bin (±2 %, 8 % Stryd range, 15 % walk label).
   - Trail is split into running and walking-heavy groups (≥ 50 % of the time < 130 spm, 自組).
   - Compared: the per-class RE(g) fit (LOO) against the pooled fit, and gait against no gait.

A category is validated (drops 推估, v2 segment sum) when capacity passes AND the race-like
terrain rows have a median |err| ≤ the threshold and a downhill bias ≤ +5 %. The effort bar
needs ≥ 5 race-like / test efforts with median f 0.97–1.03. Script:
`python -m backend.scripts.racepower_backtest`; result `racepower_backtest.json` (version 2).

Result on 2026-09-30, after the plan CP 204 W test row was applied: 177 cases. Runs by class:
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
and the mocked COROS push. V-BT now covers the capacity / terrain summaries, validated flags and
the version-2 store.

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
- Back-test:
  - no past A races and no solo hikes yet;
  - the race-like runs are short hard training runs, not maximal, so the capacity model cannot
    pass until there are ≥ 5 tests or races;
  - the one remaining leak: `thresholds.estimate` filters runs with `ds.cp`, and
    `planning.threshold_on` applies the plan CP (204 W, dated 2026-09-30) to every earlier date.
    Since that row exists, many past LTHR estimates fail and fall back to WKO5's untouched 160;
    before it they read 152–156;
  - an LTHR of 152–156 looks low next to the 12′ test (HR 155 → 171), so many daily runs read
    HR-high and are demoted only by the power check.
- The PD refit gives 191.5 W against the research script's 196.1 W with the same FIT file (curve
  sampling). Route-specific technicality: `GaitRE.route_tech` overrides the per-class factor
when set, but nothing fills it from the routes module yet.
- CSV export and saving a GPX course onto a season-plan event are not done.

## Change History

| Date | Source | Feature SRS | Summary |
|------|--------|-------------|---------|
| 2026-09-30 | code-sync | N/A | Created from brownfield analysis — SuperPower Calculator port on the athlete's data, trail and 百岳 extensions, CWA / Open-Meteo race-day weather; D2 altitude normalisation added and the CVI cross-check source clarified during sync |
| 2026-09-30 | feature | docs/research/racepower-v2.md | v2: three modes, five-level effort bar (80/90/97/100), pacing strategies, GPX / manual courses with per-segment allocation and profile, per-segment altitude with acclimatisation switch (百岳 default unacclimatised, partial = 推估), hill elasticity +5/−10 %, aid stops in the ETA, 「看 30 秒平均功率」, COROS export (mocked push), leave-one-out back-test + 準確度 tab gating the 推估 labels (nothing validated yet); §3C applied: Skiba τ labelled, Pandolf uphill only, altitude polynomial not attributed to Bassett, Stryd percentages are of 10 km power |
| 2026-09-30 | feature | user feedback + capacity review + docs/research/cp-test-protocols.md | Back-test v2: HR / power intensity classes (Seiler / Friel / Palladino constants, own-date thresholds); two back-tests (比賽預測 capacity on race-like + CP-test bouts with the lower-bound test on every run; 地形模型 by class × grade bin, trail running vs walking-heavy); group hikes out of every target-time calibration (solo opt-in list, Tobler EP/h fallback, equivalence 登山 → EP 推估), HR-filtered steep hike windows kept for VAM / altitude factor / fatigue / walking bins; capacity: PD-model refit (incl. synced FIT) as the mFTP/TTE anchor + CP-test pair for F2, CP lower bound (k-consistent in /predict), detected CP tests as suggestions (non-maximal bout → single bout with Ruiz-Alias W′ prior), `workout_review.cp_test` non-overlapping windows; k only from race-like priors at the case distance; road RE CVI-adjusted over the year; gait-aware RE(g) + trail technicality; effort band from the CP spread; HR-first note on steep trail courses |
