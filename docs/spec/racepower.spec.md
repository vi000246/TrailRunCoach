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
| API + page | Endpoints, override logic, the HTML page | `backend/api/racepower.py:199` |

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

`/inputs` is memoised for 10 min per (dataset, day, plan mtime) (`backend/api/racepower.py:53`).

## Prediction (`/predict`)

Every derived input can be overridden in `PredictIn` (`backend/api/racepower.py:169`); `used`
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
| GET | `/api/v1/racepower/inputs?refresh=` | the derived inputs (`backend/api/racepower.py:68`) |
| GET | `/api/v1/racepower/peaks?q=&baiyue_only=` | peaks list (`backend/api/racepower.py:73`) |
| GET | `/api/v1/racepower/weather?date=&days=&event_id=&peak=&lat=&lon=&elevation=&cwa=` | provider, values, tried, location, fetched_at (`backend/api/racepower.py:95`) |
| GET / POST | `/api/v1/racepower/weather/key` | masked key status / save (10–80 chars, no spaces) (`backend/api/racepower.py:132`) |
| POST | `/api/v1/racepower/predict` | type, used, env, result, tasks, zones, warnings (百岳 adds biggest_day) (`backend/api/racepower.py:199`) |
| GET | `/api/v1/racepower/page` | `backend/static/racepower.html` (`backend/api/racepower.py:340`) |

Missing CP / road RE / trail RE / EP/h → HTTP 400 asking for a manual value.

## Testing

`backend/tests/test_racepower.py`: the environment chain against the workbook's cached
defaults and the worked example (M 0.911174), CP / W′ fits and RWC bands, every Riegel task
incl. D1, table lookup edges (D5), CVI categories and adjustment, the personal-k cut, D2
altitude normalisation, road / trail / 百岳 predictions, CWA / Open-Meteo / climatology parsing
and the provider chain (no network), key storage, peaks and the build script. Not covered:
`athlete.derive` on a dataset and the API layer.

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

## Known gaps

- Only the altitude term is normalised per activity (per-activity T / RH are not stored).
- Environment is one value for the whole event, not per day.
- Sex defaults to male while the season-plan profile is empty.

## Change History

| Date | Source | Feature SRS | Summary |
|------|--------|-------------|---------|
| 2026-09-30 | code-sync | N/A | Created from brownfield analysis — SuperPower Calculator port on the athlete's data, trail and 百岳 extensions, CWA / Open-Meteo race-day weather; D2 altitude normalisation added and the CVI cross-check source clarified during sync |
