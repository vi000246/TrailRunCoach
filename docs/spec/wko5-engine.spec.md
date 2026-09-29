# Module Spec: wko5-engine

> **Last Updated**: 2026-09-29
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
| API + viewer | Serve views, charts, config, corrections | `backend/api/wko5views.py:86` |

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
later file with the same `name` overrides an earlier one. The viewer groups
the dropdown into 「我的圖表」 and 「WKO5（對照用）」.

`views/training.json` is the designed set: 總覽 plus 跑步 / 越野跑 / 百岳 / 騎車,
41 series, grounded in `docs/research/coaching-dashboards-mountain.md`.

**Sport identification trap.** Tags are FIT sport + subsport concatenated:
trail runs carry both `running` and `runningtrail`, road cycling carries
`cycling` and `cyclingroad`. So `hastag("running")` matches trail runs too;
road running is `sport="run" and !hastag("runningtrail")`.

## Mountain metrics (own formulas)

Not in WKO5; grounded in `docs/research/`.

| Module | What | Basis |
|---|---|---|
| `minetti.py` | Energy cost of running/walking by gradient; grade-adjusted speed with a downhill floor | Minetti et al. 2002, R² 0.999, ±45% |
| `effort.py` | Equivalent flat distance by integrating Minetti over the elevation stream | Same |
| `trail.py` `compute_hr_drift` | Aerobic decoupling Pa:HR | TrainingPeaks / Uphill Athlete convention |

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
| GET | `/views` | 86 | Both view kinds, with source |
| GET | `/views/dirs` | 100 | Where custom view files live |
| GET | `/views/{view}/dashboards/{d}/charts/{c}` | 119 | Render one chart (`parity`, `begin`, `end`, `sports`, `workout`) |
| GET | `/workouts` | 139 | RHE activity list, with TSS source |
| GET | `/sports` | 166 | Sport groups and counts |
| GET | `/athlete` | 175 | Settings history, WKO5's PMC snapshot |
| GET / PUT | `/config` | 194, 201 | Engine config |
| GET | `/corrections` | 214 | Applied corrections |
| GET | `/corrections/proposals` | 220 | Detect only — changes nothing |
| POST | `/corrections/approve` | 235 | Apply the proposals sent |
| DELETE | `/corrections/{id}` | 244 | Undo one |
| GET | `/viewer` | 253 | The verification viewer page |

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

## Change History

| Date | Source | SRS | Change |
|------|--------|-----|--------|
| 2026-09-29 | code-sync | N/A | Created from brownfield analysis — WKO5 file readers, verified metric algorithms, expression engine, parity/own-formula modes, approved data corrections, custom views |
