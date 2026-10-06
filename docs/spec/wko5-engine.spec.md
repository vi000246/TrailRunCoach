# Module Spec: wko5-engine

> **Last Updated**: 2026-10-04
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
| Dataset | One athlete: workouts, metrics, TSS policy, caches, corrections (`FitFolderDataset` for COROS / TP folders) | `backend/engine/wko5expr/dataset.py:314` |
| Expression engine | Parse and evaluate WKO5's expression language | `backend/engine/wko5expr/evaluator.py:508` |
| API + viewer | Serve views, charts (through the render cache), workout samples, config, corrections; the viewer page | `backend/api/wko5views.py:304`, `backend/static/wko5_viewer.html` |

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
| `.wko5athlete` | Settings history, workout index with per-workout metrics, PMC snapshot | `backend/files/wko5_athlete.py:167` `read_athlete` |
| `.wko5cache` | WKO5's per-workout expression results (e.g. `meanmax(power)`) | `backend/engine/wko5expr/dataset.py:228` `load_wko5_curve_cache` |

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
| FIT → channels | `backend/files/fit_to_channels.py:280` | channels | 1038/1061 files sample-for-sample |
| PMC (CTL/ATL/TSB) | `evaluator.py` `_tl` | athlete snapshot | matches WKO5's stored CTL / ATL / TSB |
| Power-duration model | `wko5_pdmodel.py` | — | **disassembly only, unverified** |

Two traps the verification surfaced, both reproduced deliberately:

- `_elevation` is only exact if every write is quantized to the channel's 0.1 m
  step, including the final drift-corrected output (`wko5_elevation.py`).
- WKO5's "almost equal" is effectively *exactly* equal; float residue in a
  running window sum counts as non-zero (`wko5_pace.py` `_is_zero`).

The remaining FIT mismatches (23 files) are a handheld GPS unit's clock quirks,
data-less indoor activities, and swims.

## TSS policy

`backend/engine/wko5expr/dataset.py:641` `_metrics` follows WKO5's branch
order, reconstructed from disassembly, and records which branch won
(`tss_source`: power / rtss / trainingpeaks / hrtss) plus, for a power TSS,
`ftp_used` / `ftp_source` (shown on hover in the activity list and the source
compare):

1. **Power:** `NP² × tssduration / (FTP² × 36)` when there is a power stream
   and an FTP in effect. Skipped for a file whose power is watch-estimated
   unless `power.accept_watch_power` is on (`power_tss_blocked`). The FTP
   (`tss_ftp`, `backend/engine/wko5expr/dataset.py:659`) is WKO5's rule: the
   FTP stored with the workout, else the sport's dated FTP setting. On a COROS
   / TP source a run instead divides by the CP in effect — the plan's CP test,
   else the athlete's `run_ftp_w`, else the Stryd-only PD-model mFTP as of
   that day (labelled 推估), else no power TSS
   (`backend/engine/wko5expr/fitdataset.py:996`); parity mode and the WKO5
   settings opt-in keep WKO5's rule.
2. **rTSS:** `(d/60)^1.025 × IF² / 60 × 100`, IF = threshold pace / NGP, for runs without power. The 1.025 exponent means an hour at threshold scores ~110.8, not 100; this is as disassembled and unverified against WKO5's UI.
3. **TrainingPeaks TSS** (`.wko4` info field 4038) when present, **before** WKO5's own hrTSS. WKO5 keeps TP's `tssActual` only when `tssSource == 0`, which is not stored on disk, so "use it when present" is the closest rule available from files.
4. **hrTSS** otherwise.

Moving-time hrTSS and the elevation bonus (own formulas) apply to every
workout whose `tss_source` is not power or rTSS
(`backend/engine/wko5expr/dataset.py:539` `_is_hr_sourced`), so a run with NP
but no FTP in effect is treated like any hrTSS day.

**Walks / hikes (SP-63, own formulas only):** a `walk` workout (walking,
hiking, mountaineering) without a threshold of its own scores hrTSS on the run
LTHR (`hr_lthr`, `backend/engine/wko5expr/dataset.py:576`), and always over
moving time — whatever `hr_tss_moving_only` says (`moving_hrtss_on`,
`backend/engine/wko5expr/dataset.py:568`): over recorded time a multi-day 百岳
charges the nights. Runs follow the knob as before. Only the hrTSS path falls
back — `aethr` and the low-intensity share still read the sport's own setting,
so hike time does not enter the 80/20 share. Strength always scores 0 TSS,
a dated plan LTHR or not (`NO_TSS_SPORTS`: `hr_lthr` returns None and `_metrics`
drops a file / TP TSS for it — the plan row used to reach it through
`otherthr`): resistance-training HR is not an endurance load.
Parity mode is unchanged. History changes with it (TSS is computed on the fly):
CTL rises in the weeks with hikes.

`tl()` is linear: `v += (x − v) / constant`, daily sums, inputs outside 0–5000
ignored (`backend/engine/wko5expr/evaluator.py:2429` `_tl`).

The builtins `ctl` / `atl` / `tsb` (`backend/engine/wko5expr/evaluator.py:760`) are not plain
`tl(tss, ctl/atlconstant)` since SP-68: `Evaluator.pmc`
(`backend/engine/wko5expr/evaluator.py:2443`) starts the same recurrence from
`load_guard.pmc_start` — the manual CTL / ATL at a date (user_settings `athlete.pmc_start`,
read once per evaluator), else CTL = ATL = the mean daily TSS of the first 28 days with TSS,
else 0. With years of data the start has decayed away (WKO5 parity of today's CTL / ATL / TSB
holds); in the first months, or with a manual start, they differ from WKO5. An expression's
own `tl()` is unchanged (WKO5, v = 0 before the first input). A sport-filtered evaluator or a
`sport(x)` context takes the automatic seed only. The chart render cache keys on the manual
start (`backend/engine/wko5expr/render_cache.py`).

## Modes

`backend/engine/wko5expr/config.py` — `EngineConfig`, persisted as
`engine.json` in the tenant's base folder (`config_path()`; a demo sandbox
reads its base's file and never writes it). Without a stored `parity`, the
default is parity only when a WKO5 athlete file exists (`wko5_available`); a
COROS / TP-only runner starts on own formulas.

| Setting | Parity | Own formulas (`MOUNTAIN_PRESET`) | Why |
|---|---|---|---|
| Use TP's TSS | forced on | off | Independence from TrainingPeaks; a direct COROS import has no TP TSS |
| hrTSS on moving time only | off | on (walks / hikes: always, SP-63) | WKO5 charges every recorded second; a two-day trip with only ~7 h moving can score ~900 |
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
   hunted. On one runner's data it finds exactly the three outliers
   (1.6–2.1 × the highest genuine peak) above a smooth tail.
2. **Propose** (`GET /corrections/proposals`): returns the evidence — samples,
   peak, and the workout's peak after correction.
3. **Approve** (`POST /corrections/approve`): only the proposals sent are stored.
4. **Apply**: an overlay at `corrections.json` in the tenant's base folder
   (`corrections_path()`), applied when channels are read
   (`backend/engine/wko5expr/dataset.py:839`).
   The `.wko4` files are never modified (WKO5 rewrites them on sync, and they
   are the only copy).
5. **Undo** (`DELETE /corrections/{id}`).

## Expression engine

`backend/engine/wko5expr/parser.py` parsed 828 of the 829 expressions in the
two WKO5 views originally imported (the exception used the `in` operator,
which the grammar now accepts); those chart packs are no longer in the repo
(see Views). Value kinds in `evaluator.py`: per-workout series (`WS`), daily
series (`Daily`), sample arrays, curves (`Curve`), pairs, ranges and lists.

Semantics chosen to match WKO5:

- Aggregations over sample-level arguments are lifted per workout
  (`needs_samples` stops at nested aggregations and `athleterange`).
- `athleterange(a, b, e)` limits aggregation and output; `tl()` always
  integrates full history so CTL does not restart.
- `sum/avg/count(values, groupby)` bucket by the second argument, e.g.
  `sum(climbing, startofweek(date))`.
- `meanmax(channel)` with no duration returns the athlete envelope (best of
  each duration across workouts in range); power curves reuse WKO5's Cache5.

Known gap: `startofweek` assumes Monday (WKO5 reads a user preference). The
statistics (`stddev` / `variance` / `slr*`), `filter`, `bin` and `lookup` are
implemented; an unknown function raises `unsupported function`
(`backend/engine/wko5expr/evaluator.py:1071`).

Own functions beyond WKO5's: `drift("pace" | "power", tier)` returns the
single-activity card's heart-rate drift of a run (warm-up excluded, fairness
refusals → no point; not WKO5's stored `pahr` / `pwhr`) and `drift_avg`
(`backend/engine/wko5expr/evaluator.py:1093`).

## Views

Two kinds, one renderer:

| Source | Location | Editable | Purpose |
|---|---|---|---|
| `wko5` | the user's own exported `*.wko5chart`, searched recursively in `WKO5_VIEWS_DIR`, else the `charts.wko5_views_dir` setting; none when neither is set (`backend/api/wko5views.py:61`) | no | Parity checking |
| `custom` | `views/*.json` in the repo, then the tenant's `views/` folder (`user_views()`) | yes | The athlete's own charts |

The WKO5 chart packs that used to be bundled in the repo were removed
(commit 455f716). Outside parity mode, `views/wko5_fixes.json` patches design
mistakes in imported WKO5 charts (`chartfixes.py`; details in
[wko5-chart-units.spec.md](./wko5-chart-units.spec.md)). Every dashboard and
chart has a stable `id` (`backend/engine/wko5expr/viewids.py:41`: the bundled
views write hand-picked slugs, others get one derived from the title); the
fixes and the translations address charts by it. The bundled views are
translated by a per-locale sidecar `views/i18n/<locale>.json`, applied after
the fixes (`backend/engine/wko5expr/viewi18n.py:113`); WKO5 views and the
athlete's own JSON are not translated.

Custom views use the same shape as parsed WKO5 views (`customviews.py`); a
later file with the same `name` overrides an earlier one
(`backend/engine/wko5expr/customviews.py:266`). A chart's `kind`
(`backend/engine/wko5expr/customviews.py:86`) is `athlete` (default),
`workout`, `zones`, `targets`, `map` (the workout's GPS route, same as WKO5's map panel), `review` (needs a `section`; a single-activity
card — see [workout-review.spec.md](./workout-review.spec.md)), `activity`
(a single-activity panel named by `chart`, e.g. 心率與功率, zone times),
`periodzones` (time in zone over a period, `view` total / weekly), `z5gate`
(the 5 區開放流程 replay over the season), `climbvam` (steady-climb VAM:HR
per route) or `polecompare` (有杖 vs 沒杖 per grade bin, SP-243:
`backend/engine/panels/pole_compare.py` + `backend/static/pole_compare.js`). Two optional chart keys drive the period toggle: `period` (day /
week / month / quarter / year, the default bucket) and `min_days` (look-back
floor for that default bucket) (`backend/engine/wko5expr/customviews.py:117`).

Other optional chart keys, each validated in `_chart`:

| Key | Effect |
|---|---|
| `variants` | Several axes + series sets behind a segmented toggle; the first is the default and also stands at the top level; the viewer asks `?variant=<key>` (`backend/engine/wko5expr/variants.py:69`) |
| `window` | 近 7／14／28 天新高 toggle (`recentbests.py`, `?window=`) |
| `basis` | 配速／功率 toggle (see Drift basis toggle) |
| `zoned` | Banded chart (see Banded charts) |
| `race_refs` | `"course_constant"`: the next two target races' single-day コース定数 lines and the A race's 80–100 % band (`backend/engine/panels/race_refs.py`) |
| `drift_bars` | Drift as one verdict-coloured bar per run, with a hover line per bar (`backend/engine/panels/drift_bars.py`) |
| `sports` / `order` | 主要訓練項目: shown only in the trail or road mode, and the chart's place in its dashboard per mode; a dashboard may carry per-mode `descriptions` |
| `needs` | A condition on the athlete's own data (`customviews.NEEDS`): `"poles"` = ≥ 5 activities marked 有杖 and ≥ 5 沒杖 in the last 365 days, counting only the trail runs and hikes the chart uses (`pole_compare.counts` → `chart_rows` (`climb_vam.kind_of`) → `activity_tags.pole_counts`; no mark at all = no Dataset wait). `GET /views` adds `needs_met`; the viewer hides the chart until it is true |

The bundled custom views (regrouped in commit 4f75cfe; the old 每月・每年
dashboard was dropped in favour of the period toggle; the polarization-index,
monotony / strain and other redundant charts were dropped in 2026-10):

| File | View | Dashboards |
|---|---|---|
| `views/training.json` | 我的訓練 | 負荷 PMC (PMC with TSB bars coloured by Form% zone, 每日 TSS with a TSS / % CTL variant, TSS 合計, Ramp rate, Form% and 負荷比 as `zoned` charts, then 有氧／無氧刺激 TIS per activity and the Chronic / Acute TIS load — see below); 訓練量 (每週移動時間 stacked by category, one weekly volume chart with 跑量 / 爬升下降 / EP variants, 每次長跑距離, 每週下坡衝擊負荷, 肌力訓練日曆 as a day calendar, コース定数 with race reference lines); 強度 (periodzones total + weekly, 每週馬拉松配速時間); 能力 (power curve with the PD-model line, EF, 輕鬆路跑的心率飄移 as drift bars, 長跑配速, 上坡腳程, steady-climb VAM:HR (`climbvam`), 下坡腳程, 有杖 vs 沒杖 (`polecompare`, `needs: "poles"`), 每公里爬升, per-session moving time, durability) |
| `views/periodization.json` | 周期化訓練 | ① 轉換期, ② 基礎期 (incl. drift bars and the `z5gate` 5 區開放流程), ③ 專項期, ④ 減量期, 區間與課表強度 (zone / target tables last) |
| `views/workout.json` | 單次活動判讀 | 本次重點, 有氧／心率飄移, 間歇, 爬坡與地形, 配速與耐久, 跑姿與膝蓋負荷（參考） — see [workout-review.spec.md](./workout-review.spec.md) |

Most season charts in 訓練量 / 強度 and the phase dashboards carry a `period`
key; no bundled chart sets `min_days` at present.

**TIS charts** (我的訓練 › 負荷 PMC, appended after 負荷比 so the earlier charts
keep their dashboard / chart indexes; `views/training.json:111-131`):
有氧／無氧刺激 TIS（每次活動） plots the built-ins `tisaerobic` / `tisanaerobic`
(WKO5's own expressions, `backend/engine/wko5expr/evaluator.py:235-260`;
formulas.md §6.10) as one dot per activity on a 0–10 axis, and 有氧／無氧刺激的長期與短期負荷
plots `tl((tis…), ctlconstant)` / `tl((tis…), atlconstant)` — WKO5's Chronic /
Acute TIS Load — as a PMC-like pair per energy system. Both need a power
channel (`_builtin_workout`, `backend/engine/wko5expr/evaluator.py:879`), so
使用功率 off hides them (`tisaerobic` / `tisanaerobic` are power identifiers,
`backend/engine/wko5expr/power_use.py:30-36`). Tests: `backend/tests/test_tis_charts.py`;
parity with WKO5's cached per-workout scores (aerobic 359/361 equal, anaerobic
345/361; 15 of the 16 anaerobic misses are one level off, one is two): `backend/tests/realdata/test_real_wko5_tis.py`.

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
(`backend/api/wko5views.py:449` `_apply_period`): the `period` query parameter is honoured only
for custom views and unlocked charts; the floor and bucket alignment apply to
custom views only. The response adds `x_period`, `period_default`,
`period_toggle`, `buckets` and `range_note`
(`backend/api/wko5views.py:470`).

## Drift basis toggle (配速／功率)

`backend/engine/wko5expr/basis.py` lets a drift chart switch between Pa:HR
(speed / HR) and Pw:HR (power / HR). Same pattern as the 近 7／14／28 天 window
(`recentbests.py`): a chart-level spec, a server rewrite, a per-chart viewer
toggle.

- **Spec** (`backend/engine/wko5expr/customviews.py:139-150`): `"basis":
  {"default": "pace", "choices": ["pace", "power"], "power_note": "…"}`. Series
  carry `"basis": "pace"` or `"power"` (`SERIES_DEFAULTS`,
  `backend/engine/wko5expr/customviews.py:59`); untagged series are drawn in
  both modes. A tagged series on a chart without a basis spec is an error.
- **Rewrite** (`backend/engine/wko5expr/basis.py:53`): keep the series of the
  chosen basis, rewrite the title and description (Pa:HR → Pw:HR, 速度／心率 →
  功率／心率 …, `backend/engine/wko5expr/basis.py:32`), append `power_note` in
  power mode, and set `basis_chosen` for review cards. Tagged series rather
  than an expression swap, because the two EF expressions differ by more than
  a token (`*1000/60` is a speed-only unit factor) and 每公里心率與速度 has no
  speed / power token to swap.
- **No power** (`backend/engine/wko5expr/basis.py:71`): power mode on a
  workout without a power channel drops the power series and says 這次沒有功率 —
  as the chart's `empty` message when only reference lines would remain,
  otherwise as `basis_note` above the chart. Season charts need nothing: a run
  without power has no `pwhr` and `drift("power")` is NaN for it, so it has no
  point.
- **API** (`backend/api/wko5views.py:376-378`): custom views, every chart kind
  (athlete, workout, review); `?basis=` is a query parameter, so it is part of
  the render-cache key. The response adds `basis`, `basis_default`,
  `basis_choices`, `basis_labels` and `basis_toggle`. Review cards receive the
  basis through `_render` (`backend/api/wko5views.py:517`).
- **Charts using it**: 輕鬆路跑的心率飄移 and 耐久度 in 我的訓練 › 能力
  (`views/training.json:302`, `views/training.json:374`), 長時間輕鬆跑的心率飄移
  and 耐久度 in 周期化訓練 (`views/periodization.json:67`,
  `views/periodization.json:194`), and the 有氧／心率飄移 dashboard of
  單次活動判讀: 飄移判讀 (`views/workout.json:35`). 滾動有氧效率 EF and 每公里心率與速度 were
  dropped in 2026-10 (covered by 耐久曲線 and 每 10% 距離的配速與心率).
  The two 心率飄移 season charts plot the card's `drift(basis, "all")` as
  verdict-coloured bars (`drift_bars`: < 5 % / 5–10 % / > 10 %, one 5 % line);
  the 耐久度 charts plot WKO5's stored `pahr` / `pwhr`
  (`backend/engine/wko5expr/dataset.py:686-686`).
- **Trail caveat**: the trail drift charts' `power_note` says Pw:HR is only a
  reference off-road because Stryd power is validated only up to about 8 %
  grade (user-supplied figure; not checked against a Stryd source here).
- **Default stays pace.** Uphill Athlete's AeT drift test is a pace test
  ("TrainingPeaks' Pa:HR does this automatically",
  `docs/research/uphill-athlete-mountain-metrics.md:128-135`); UA says running
  power suits runnable terrain while HR stays the practical tool for steep
  hiking (`docs/research/coaching-dashboards-mountain.md:70`); the 5 % cut is
  Friel's convention adopted by UA (`docs/research/coaching-dashboards-mountain.md:194-195`).
  UA's EF allows "pace or power" (`docs/research/coaching-dashboards-mountain.md:74-78`),
  but nothing found specifies Pw:HR for runners, so the overview's 心率飄移
  indicator and the base-phase streak stay on Pa:HR.

## Render cache

`backend/engine/wko5expr/render_cache.py`, used by the chart endpoint
(`backend/api/wko5views.py:446`).

- **Key** (`backend/engine/wko5expr/render_cache.py:145`): sha1 of the chart
  definition (after fixes, translation, variant, basis and period rewrite), the
  request (view, dashboard, chart, begin/end after the floor, parity, the data
  source, every other query parameter, the workout's file, the variant), the
  data fingerprint, the code signature and, outside zh-TW, the request locale.
  Some chart kinds add hidden inputs to the parameters: `z5gate` the 課表偏好
  stamp and the stored test sessions; `zones` / `targets` / `activity` /
  `periodzones` the HR-profile stamp; `race_refs` the events' stored GPX;
  `climbvam` the route index / names / weather files; `polecompare` the pole
  marks (`activity_tags.pole_marks_stamp`) and today's date
  (`backend/api/wko5views.py:385-414`). Nothing is invalidated explicitly;
  changed inputs miss.
- **Data fingerprint** (`backend/engine/wko5expr/render_cache.py:89`): the
  `.wko5athlete` stamps, plan and corrections file stamps, engine config,
  workout list hash, today, the chart data source with its FIT-folder stamp
  (`backend/engine/wko5expr/render_cache.py:104`), the stored CP-test sessions
  and done interval sessions (the review cards judge a run against its matched
  session), and the per-activity weather file.
- **Code signature** (`backend/engine/wko5expr/render_cache.py:70`):
  `CACHE_VERSION` plus size/mtime of every `*.py` in `wko5expr/`,
  `algorithms/`, `backend/engine/`, `backend/engine/panels/`, `backend/files/`
  and of `backend/api/wko5views.py` (`_ENGINE_GLOBS`,
  `backend/engine/wko5expr/render_cache.py:57`), taken once at import so a
  process that has not reloaded never stores old-code results under a new
  signature.
- **Storage**: in-memory LRU of 400 entries plus JSON files under the
  tenant's shared `cache/render/` (`backend/engine/wko5expr/render_cache.py:39`;
  demo sandboxes share their base's cache), pruned to 300 MB
  least-recently-used every 50 writes (`backend/engine/wko5expr/render_cache.py:201`).
- **Concurrency** (`backend/engine/wko5expr/render_cache.py:243`): identical
  in-flight requests are coalesced; at most 2 renders run at once so other
  endpoints keep threadpool time. Errors are raised to every waiter and not
  cached.

## FIT dataset build: cache, single flight, progress (2026-10-01)

A COROS / TP `FitFolderDataset` reads every per-file result from the
persistent FIT cache (`backend/engine/wko5expr/fitcache.py`); a restart with
unchanged files and unchanged code reads no FIT file at all.

- **Storage**: `<data dir>/cache/fit/<sha1(folder)>/` (`WKO5COACH_FIT_CACHE`
  overrides the root): `index.json` (one entry per file, valid for its
  size + `mtime_ns`), `ch/*.npz` (the parsed channels, float64, NaN = no
  data), `series_*.json` (`cached_series`, up to 4 threshold variants per
  file), `estimate.json` (the as-of LTHR estimates), `pd_mftp.json` (the
  as-of PD refits behind `cp_as_of`).
- **Parsing**: only new / changed files; ≥ 12 of them go to a spawn process
  pool (≤ 8 workers, `WKO5COACH_FIT_WORKERS`), so the web server's event loop
  keeps the GIL. Channels load lazily, at most 256 files in memory
  (`WKO5COACH_FIT_OPEN`).
- **Invalidation map** — each derived field has its own version (a constant
  plus a hash of the source of the code computing it):

  | Cached | Recomputed when |
  |---|---|
  | parsed channels, start, sport, sub_sport, Stryd device | the file's size / mtime; `fit_to_channels.py`, `power_source.fit_stryd_device`, the fitdecode version |
  | power source | the parse; `power_source.classify` |
  | bad-file features | the parse; `bad_activity.features` and its constants; the file's approved power corrections (separate key) |
  | workout fields (duration, moving, distance, climbing, NP, work, NGP, VAM 4224) | the parse; `workout_fields` (where VAM is computed) / `_rolling` / `_smooth` / `minetti.py` / the moving-speed table; the sport group |
  | hike / trail tags (`hiking`, `mountaineering`, `runningtrail`) | not cached: derived on every build from the sport type (`TYPE_TAGS`, the DB classification) |
  | the charts' Stryd-only CP fit of a grid day (`_estimate_cp`) | the PD-refit entry of that day (same window key, kind `stryd`) plus the source of `_estimate_cp` and `CP_FIT_MIN_RUNS`; the whole grid is also in the estimate memo |
  | hrTSS / hrIF, moving-time hrTSS | the parse; `wko5_hr.py`; the LTHR (and moving speed) — separate keys |
  | `cached_series` (thresholds, race power, workout review, evaluator aggregates, mean-max) | the file stamp, its corrections, the thresholds in effect (as on the WKO5 Dataset) |
  | as-of PD refit of a day | the day's 90-day window: runs (stamp, power source / use, power corrections, NP), synced FITs `cptest.curves` adds, watch-power / bad-file settings and overrides, the code of athlete / cptest / PD model / mean-max / power_source |
  | as-of LTHR estimates | any workout (stamp, sport, tags, power source), exclusions, thresholds / weight before the estimate, plan, corrections, engine config, today, the code of thresholds / athlete / cp / PD model / mean-max |
  | the Dataset object (in memory) | `source_stamp` (files, DB classification / settings, watch power, bad-file setting / overrides), engine config, plan threshold edits |

  Not cached: the bad-file verdict itself (`judge` / `decide` read the
  features, the weight and the user's overrides, cheap).
- **Single flight** (`backend/api/wko5views.py` `_dataset`): one lock per
  (config, source, stamp); concurrent chart requests wait for one build.
- **Progress**: `backend/engine/wko5expr/buildstate.py`; `GET
  /api/v1/wko5/dataset/status` (async, answered on the event loop) returns
  `state` idle / building / ready / error, `phase` (scan, parse 解析 FIT,
  assemble 整理活動, estimate 估算門檻, finish), `n_done` / `n_total`,
  `message`. `shell.js` shows it under the nav and in the page's loading
  placeholders.
- **Measured** (2026-10-01, 808 COROS files, a copy of the data dir, this
  PC): before 468 s per build (FIT parsing 390 s, as-of estimates 66 s, of
  which 670 PD refits 64 s), on every restart. After: cold cache 116–146 s
  (parsing in 8 processes ~60 s, estimates 42 s); restart with unchanged
  data 0.4–1.0 s build, ~2 s from process start to a served overview
  status; one changed file 1.0 s. Event-loop latency during a cold build
  with six page requests waiting: typically 3–6 ms, worst 240 ms. The
  datasets are identical to the old build's (all 802 workouts' metrics,
  power sources, exclusions, estimated settings).
- **Relocatable** (2026-10-03): the cache folder is found by the FIT
  folder's place inside the app home (`fitcache.resolve_home`: the new key,
  else the old absolute-path key, else a folder whose `home.json` / index
  names the same `app:fit/<source>`), so a copied or moved app data folder
  reads no FIT again. Each entry keeps the file's sha1: a file whose mtime
  changed but whose bytes did not keeps its parse, and `stamp_of` /
  `stamp_s` answer the cached stamp so the per-file memos (series, estimate,
  PD refits, the race-power file caches) stay valid too.
- **Race-power file caches** (`racepower/cptest.py`): the folder listing is
  reused while no folder changed; a cold folder is read once in the process
  pool (`_prefetch`, also filling the bad-file / power-source caches), and
  files the FIT dataset cache already parsed take their power source and
  bad-file features from it. Before, a new user's first build re-parsed
  every FIT file 2–3 times single-threaded, one 90-day window at a time.
- **Series writes** inside the as-of estimates are batched
  (`dataset.batched_flush`, at most every 20 s): each estimate() rewrote the
  MB-sized series files.
- **Measured** (2026-10-03, a fresh user: user data + FIT folders only, 803
  COROS activities, 4 workers): first build 1680 s → 377 s; GET
  /activities/auto blocked 147 s → answers in 0.03 s (computing n/N, the
  background job done in 83 s), a restart 0.1 s from disk; the data dir
  copied elsewhere: build 0.7 s (no re-parse), with all mtimes reset 0.8–15 s.
- **Warm-up**: the app's lifespan and a sync that downloaded files start a
  background build of the active source (and the overview status);
  `WKO5COACH_NO_WARMUP=1` disables. The AnyIO thread limit is 200
  (`WKO5COACH_THREADS`) so requests waiting on a build don't starve the rest.

## Viewer

`backend/static/wko5_viewer.html`, served at `/api/v1/wko5/viewer`.

- **Mode cards** (`backend/static/wko5_viewer.html:383`): the page opens on
  two big cards, 趨勢 (season charts) and 單次活動 (one workout's charts).
- **Data-source chip** (`backend/static/wko5_viewer.html:393`): `#source-chip` +
  `sourcechip.js` switch `charts.data_source` between the 資料來源 (the one
  synced source in use, COROS or TrainingPeaks) and the WKO5 folder
  (cross-check), then reload; hidden in the demo. The chart, overview and
  race-power datasets all follow it (`_dataset`, `backend/api/wko5views.py:133`;
  source resolution in [wko5-coros-sync.spec.md](./wko5-coros-sync.spec.md)).
- **Chart directory** (`backend/static/wko5_viewer.html:657`): custom views are
  one flat list of dashboard tabs with no view level, ordered by
  `CUSTOM_ORDER` = 我的訓練, 周期化訓練, then the rest
  (`backend/static/wko5_viewer.html:539`); imported WKO5 views stay grouped per
  view with a 匯入 tag and collapsible headers. Charts marked `power` are hidden
  when 使用功率 is off, and charts whose `sports` excludes the 主要訓練項目 are
  hidden (`backend/static/wko5_viewer.html:547`).
- **Deep link** (`backend/static/wko5_viewer.html:590`): `?view=<name>&dash=<index
  or title>`, plus `&chart=<index>` to load that chart first and open it
  enlarged; `?workout=<index>&label=<name>` (from the 課表 page) opens that
  activity. The query string is then cleared.
- **Period toggle** (`backend/static/wko5_viewer.html:1102`): when the response
  says `period_toggle`, the card header gets 日／週／月／季／年; the choice is
  remembered per chart in local storage (`wko5viewer.period`,
  `backend/static/wko5_viewer.html:1011`) and re-fetches that card only. A chart
  with a `calendar` series (a day calendar such as 肌力訓練日曆,
  `backend/static/wko5_viewer.html:2053`) never gets the toggle.
- **Basis toggle** (`backend/static/wko5_viewer.html:1020`,
  `backend/static/wko5_viewer.html:1142`): when the response says `basis_toggle`,
  the header gets 配速／功率, remembered per chart in `wko5viewer.basis` and sent
  as `&basis=` for athlete and workout cards (`backend/static/wko5_viewer.html:1086`);
  with 使用功率 off a `power_basis` chart is locked to pace
  (`backend/static/wko5_viewer.html:1140`); `basis_note` is drawn above the
  chart (`backend/static/wko5_viewer.html:1173`).
- **Variant toggle** (`backend/static/wko5_viewer.html:1045`): a chart with
  `variant_choices` gets a segmented control, remembered per chart in
  `wko5viewer.variant` and sent as `&variant=`.
- **Enlarge** (`backend/static/wko5_viewer.html:1196` `openZoom`): 「⤢ 放大」 opens a
  `<dialog>` redrawn from the card's JSON (no refetch) with the full legend and
  a zoom slider; it pushes a history entry with `&chart=`, so Back, Esc, the
  close button or a backdrop click closes it
  (`backend/static/wko5_viewer.html:1225`). A period toggle inside the overlay
  re-renders both card and overlay.
- **No 數值與公式 table.** The per-series debug table (status, points, last
  value, unit, expression) is no longer drawn; the same data stays in the chart
  JSON.
- **Stack total.** A stacked chart's tooltip adds a 合計 row over the
  categories currently shown in the legend (hidden ones drop out); skipped for
  percent shares (`backend/static/wko5_viewer.html:2415`). Its unit, like every
  tooltip row's, comes from the source series of the ECharts series
  (`srcOf[seriesIndex]`, `backend/static/wko5_viewer.html:2253`).
- **Route map** (`backend/static/wko5_viewer.html:1953`): WKO5's map panel
  (`PKMapPanelConfig`, `backend/api/wko5views.py:292`) or a custom view's `kind: "map"` chart
  is drawn with Leaflet from the workout samples; the panel JSON (`render_map`,
  `backend/engine/wko5expr/render.py:445`) only says which workout and whether it
  has GPS (`empty`, shown as 「沒有 GPS 資料」), no track of its own. The bundled
  單次活動判讀 view has it on its first page (`route-map`, `views/workout.json`). Basemaps
  魯地圖, Google 地形, NLSC 電子地圖, 正射影像, OSM; overlays 等高線, Google 道路, NLSC 道路
  come from the shared `backend/static/basemaps.js:15` (also the routes page and the race
  calculator's course map). The defaults come from the settings keys `charts.map.basemap` /
  `charts.map.overlays` (`backend/settings/repository.py:100`; basemap unset = by 地區: tw 魯地圖, intl OSM), read as `map_basemap` /
  `map_overlays` from `GET /api/v1/sync/settings` (`backend/api/sync.py:203`,
  `backend/static/wko5_viewer.html:571`; storage side in
  [wko5-coros-sync.spec.md](./wko5-coros-sync.spec.md)); a per-browser switch is kept only
  while that default is unchanged (`backend/static/basemaps.js:52`; the viewer keeps its
  route colour in the same entry, `backend/static/wko5_viewer.html:1941`).
  The route is coloured by 心率 / 功率 / 坡度 / 單色 (5–95th percentile ramp,
  12 bins) with start / end markers (`MapLayers.track`, `backend/static/basemaps.js:111`).
- **Tile-error hint** (`backend/static/basemaps.js:66`): if the active
  basemap has 3 tile errors and no tile loaded, a hint offers up to three other
  basemaps (not Google 地形) as buttons.
- **Synced hover** (`backend/static/wko5_viewer.html:1883`): the map and every
  workout chart whose x is elapsed time or a distance unit join one hover
  group per workout; hovering any member shows the same sample index on all of
  them (tooltip on charts, a marker with a time / distance / HR / power /
  elevation / grade readout on the map), one flush per animation frame. Hovering
  within 24 px of the route drives the charts
  (`backend/static/wko5_viewer.html:2042`). Every workout series that joins the
  synced hover (time or distance x) skips `lttb` sampling so every chart's
  tooltip lands on the same point (`backend/static/wko5_viewer.html:2313`).
- **Samples** (`backend/api/wko5views.py:1157`): per-sample `t`, `d` (km),
  `lat` / `lng`, `elev`, `hr`, `power`, `grade` (%), downsampled with the same
  step as the workout charts (`MAX_POINTS` 3000, `backend/engine/wko5expr/render.py:55`),
  so chart x maps exactly to a sample index; NaN and (0, 0) GPS become null. The
  viewer keeps the last 4 workouts' samples (`backend/static/wko5_viewer.html:1847`).

## Mountain metrics (own formulas)

Not in WKO5; grounded in `docs/research/`.

| Module | What | Basis |
|---|---|---|
| `minetti.py` | Energy cost of running/walking by gradient; grade-adjusted speed with a downhill floor | Minetti et al. 2002, R² 0.999, ±45% |
| `effort.py` | Equivalent flat distance by integrating Minetti over the elevation stream | Same |
| `trail.py` `compute_hr_drift` | Aerobic decoupling Pa:HR | TrainingPeaks / Uphill Athlete convention |
| `chart_metrics.py` | Reference implementations of the competitor-derived charts in `views/training.json` / `views/workout.json`: Form% zones, ATL/CTL ratio, Treff PI (its chart was dropped in 2026-10), コース定数, ITRA km-effort/category, up/downhill m/h, downhill impact load (own composite; `DOWNHILL_EXPR` is shared with the 總覽 `descent` card) and, beside it, the weekly steep-downhill cadence lines (`downhill_cadence_expr`: < −8 %, moving, time-weighted, strides/min × 2 = spm, no point under 10 min; SP-237) | Friel; Gabbett 2016 via Runalyze; Treff 2019; 山本正嘉; ITRA; Gottschall & Kram 2005 + Keller 1996; Van Hooren 2024 (cadence) — sources, formulas and check results in `docs/research/competitor-charts.md` §7, tested in `test_chart_metrics.py` |

WKO5's ACSM grade factor `(0.19v + 0.9vg)/0.19` under-counts steep running
against Minetti by 22% at +20% grade, 31% at +30%, and goes negative below
about −21%.

Against integrated Minetti on 430 activities of one runner, Scarf's
`km + gain/126` is the best summary formula (4.0% mean error); ITRA's
`gain/100` over-counts by ~7%; the Swiss descent term makes it worse (22%).
Least-squares fit: running `gain/153`, hiking `gain/111`. This says which
formula best approximates a lab model, not which is physiologically true.

## API

All under `/api/v1/wko5` (`backend/api/wko5views.py`).

| Method | Path | Line | Purpose |
|---|---|---|---|
| GET | `/views` | 304 | Both view kinds, with source and ids; map panels report kind `map`, review / activity cards kind `workout`; each chart carries `power` / `power_basis` (使用功率), and `zoned` / `sports` / `order` when set |
| GET | `/views/dirs` | 329 | Where custom view files live, and the WKO5 views folder |
| GET | `/dataset/status` | 214 | Build progress of the active source's Dataset (state, phase, n_done / n_total, message) |
| GET | `/views/{view}/dashboards/{d}/charts/{c}` | 350 | Render one chart through the render cache (`parity`, `begin`, `end`, `sports`, `workout`, `period`, `window`, `basis`, `variant`, plus panel parameters such as `zsys` / `zkind` / `route`); the dataset follows `charts.data_source` |
| GET | `/workouts` | 565 | RHE activity list, with TSS source and the FTP of a power TSS |
| GET / PUT | `/exclusions` | 626, 646 | Bad activity files left out / kept, and the per-activity override — see [workouts.spec.md](./workouts.spec.md) |
| GET | `/workouts/{i}/review` | 667 | Single-activity review cards — see [workout-review.spec.md](./workout-review.spec.md) |
| GET / PATCH | `/workouts/{i}/activity` | 759, 784 | One activity's type / effort tags (auto with reasons, user overrides, note) |
| GET | `/workouts/{i}/pain` | 770 | The activity's 疼痛 mark (404 in the demo) |
| GET | `/activities` | 868 | Every activity of the current source with stored user values, terrain and power source (the 活動列表) |
| GET | `/activities/auto` | 922 | Auto activity type / effort of every activity, computed once per Dataset in the background; never waits |
| GET | `/activities/stats` | 952 | Average HR / power per activity for the list columns |
| PATCH | `/activities` | 1000 | Key-based bulk edit (also reaches excluded files) |
| GET | `/activities/page` | 1043 | The 活動列表 page |
| GET | `/sports` | 1048 | Sport groups and counts |
| GET | `/athlete` | 1057 | Settings history, WKO5's PMC snapshot |
| GET | `/primary-sport` | 1072 | 主要訓練項目: setting, sport in effect and suggestion |
| GET / PUT | `/config` | 1089, 1096 | Engine config |
| GET | `/corrections` | 1109 | Applied corrections |
| GET | `/corrections/proposals` | 1115 | Detect only — changes nothing |
| POST | `/corrections/approve` | 1130 | Apply the proposals sent |
| DELETE | `/corrections/{id}` | 1139 | Undo one |
| GET | `/workouts/{idx}/samples` | 1152 | Downsampled per-sample arrays for the route map and synced hover |
| GET | `/viewer` | 1201 | The viewer page |
| GET | `/settings` | 1206 | The settings page (404 in the demo) |

The WKO5 athlete folder is `WKO5_ATHLETE_DIR` (or `WKO5COACH_ATHLETE_DIR`), else the first
folder holding a `*.wko5athlete` under the home directory's `WKO5` (`default_roots`,
`athlete_dir`, `backend/settings/paths.py:53`); in the demo it is always an
empty folder (`no_wko5_dir`). The chart, achievements, race-power and plan APIs share it.

## Testing

| Kind | Run | What it proves |
|---|---|---|
| Synthetic | `pytest backend/tests` | Each rule in isolation, on hand-built data and small frozen fixtures (`backend/tests/fixtures/`) |
| Golden | `WKO5COACH_REALDATA=1 pytest backend/tests/realdata` (~5 min) | Parity with a real WKO5 athlete folder |

The default run never reads the real WKO5 folder or the app data folder:
`backend/tests/_guard.py` points home at a temp folder and fails any test that
opens, lists or writes a path under either. The golden tests (marker `golden`) live in
`backend/tests/realdata/` (README there) and skip when no athlete folder is
found. The end-to-end golden test (`backend/tests/realdata/test_real_wko5_pipeline.py`) goes
FIT → channels → NP/hrTSS → TSS → CTL and checks each against WKO5, including
the athlete-bar snapshot.

`backend/tests/test_periods.py` covers period detection, rewrite, legend
renaming, locks, floors and bucket lists (including `_apply_period`);
`backend/tests/test_render_cache.py` covers key changes (chart, request, data,
code), disk persistence, error non-caching, size eviction, coalescing and the
concurrency cap.

`backend/tests/test_drift_basis.py` covers the basis spec and rewrite, the
no-power note, the bundled drift charts (`backend/tests/test_drift_basis.py:159`,
`backend/tests/test_drift_basis.py:175`, `backend/tests/test_drift_basis.py:182`,
`backend/tests/test_drift_basis.py:199`) and that the season drift charts plot
`drift()` while the 耐久度 charts keep the stored `pahr`
(`backend/tests/test_drift_basis.py:223`); golden, that the season charts plot
the card's drift for each basis
(`backend/tests/realdata/test_real_drift_basis.py:220`).
`backend/tests/test_chart_variants.py` covers chart variants,
`backend/tests/test_power_use.py` the 使用功率 classification and
`backend/tests/test_run_ftp_tss.py` the run FTP for power TSS on a COROS / TP
source (synthetic FITs).

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
| Golden test | A test that compares against a real WKO5 athlete folder |
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
| Basis | Whether a drift chart uses speed (Pa:HR, the default) or power (Pw:HR) against HR |
| Variant | One of several axes + series sets of a chart, picked by a segmented toggle (`?variant=`) |
| Chart id | A stable slug per dashboard / chart that fixes and translations address instead of the title |
| Chart data source | The folder a Dataset is built from: the synced 資料來源 (COROS or TrainingPeaks) or the WKO5 folder |
| 使用功率 | Setting that hides power-only charts and locks 配速／功率 toggles to pace; models unchanged |
| Drift bars | Season drift as one verdict-coloured bar per run, from the single-activity card's `drift()` |

## Change History

| Date | Source | SRS | Change |
|------|--------|-----|--------|
| 2026-10-06 | feature | SP-243 | 能力 › 有杖 vs 沒杖 (`kind: "polecompare"`, after 下坡腳程, trail mode): per grade bin (≤ −15, −15…−8, −8…−3, ≥ +15 %) the median of the activities' measured downhill vertical speed / cadence / impact G / ILR and steep-climb VAM ÷ HR, 有杖 vs 沒杖, every moving step (hike rest floor), ≥ 2 min per bin (推估), n shown, n < 3 not drawn, caveat on the card; chart key `needs` (`"poles"`) + `needs_met` on `GET /views`; `GET /activities` → `pole_compare` counts (activity editor: 「再標 N 次」); the mark still feeds no model |
| 2026-10-04 | feature | SP-68 | Builtins `ctl` / `atl` / `tsb` start from `load_guard.pmc_start` (manual at a date → first-28-day mean → 0); `tl()` unchanged; render cache keys on the manual start |
| 2026-10-04 | feature | SP-63 | Strength scores 0 TSS in own-formula mode even with a dated plan LTHR (`NO_TSS_SPORTS`); parity unchanged |
| 2026-10-04 | bugfix | SP-52 | `hr_tss_zone1_floor` removed from `EngineConfig` and `MOUNTAIN_PRESET` (nothing read it; moving-time hrTSS already drops the camp / sleep hours); an old `engine.json` with the key still loads |
| 2026-10-04 | feature | SP-63 | Walks / hikes without their own LTHR score hrTSS on the run LTHR over moving time (own formulas only; strength stays 0; parity unchanged) |
| 2026-10-04 | feature | SP-41 | Custom views accept `kind: "map"`; 單次活動判讀's first page has the route map; the viewer's basemap list, layer switch, tile-error hint, route drawing and nearest-point lookup moved to the shared `basemaps.js` (`MapLayers`) |
| 2026-09-29 | code-sync | N/A | Created from brownfield analysis — WKO5 file readers, verified metric algorithms, expression engine, parity/own-formula modes, approved data corrections, custom views |
| 2026-09-30 | code-sync | N/A | Period toggle (periods.py, `period` / `min_days`), render cache, viewer (flat custom tabs, &chart= deep link, enlarge overlay, 數值與公式 table removed, Leaflet route map with basemaps / overlays / tile-error hint, samples endpoint and synced hover), regrouped custom views, refreshed API table |
| 2026-09-30 | bugfix | N/A | Chart dataset follows `charts.data_source` (header chip); code signature also covers panels/, files/ and api/wko5views.py; render_map drops the unused track; tooltip units from the drawn series; no lttb on distance-x hover charts; WKO5 folder from env or found under the home directory's WKO5 folder; refreshed anchors |
| 2026-09-30 | feat/competitor-charts | N/A | `chart_metrics.py` reference implementations + charts (Form% bands, ATL/CTL, monotony/strain, PI, downhill impact load and 7:28 ratio, up/downhill m/h, コース定数 / ITRA); 總覽 `descent` indicator |
| 2026-10-01 | perf/dataset-load | user request (site frozen during a COROS build) | Persistent per-file FIT cache with per-field versions, lazy channels, disk `cached_series` / as-of estimates / PD refits for `FitFolderDataset`; process-pool parsing; single-flight `_dataset`; `GET /dataset/status` + shell.js progress; warm-up at startup and after a sync |
| 2026-09-30 | feat/drift-basis | N/A | 配速／功率 basis toggle (`basis.py`, chart `basis` spec, tagged series, `?basis=`, viewer control, 這次沒有功率) on the drift charts; rolling EF skips the first 10 min |
| 2026-10-04 | code-sync | N/A | Run FTP for power TSS on COROS / TP + `tss_source` / watch-power block; per-tenant engine.json / corrections / views / render cache, parity default by WKO5 presence; WKO5 chart packs no longer bundled (WKO5_VIEWS_DIR); chart ids, view i18n sidecar, variants, new chart kinds / keys (z5gate, activity, periodzones, climbvam, race_refs, drift_bars, sports / order); drift bars from `drift()`; stats / bin / lookup / filter implemented; 使用功率 auto; viewer mode cards / variant toggle; new activity endpoints; dropped monotony / PI charts and iLevels; all anchors refreshed |
| 2026-10-04 | SP-45 | N/A | 我的訓練 › 負荷 PMC gains the aerobic / anaerobic TIS charts (per activity + Chronic / Acute TIS load); TIS built-ins count as power for 使用功率; real-data TIS golden test |
| 2026-10-06 | feature | SP-237 | 訓練量 →「每週下坡衝擊負荷」gains a right axis (`steps/min`, spm) with two dashed lines: trail-run and hike cadence on steep downhills (`chart_metrics.downhill_cadence_expr`: < −8 % grade, > 1.6 km/h, sample gap ≤ 30 s, Σ cadence·dt ÷ Σ dt per week × 2; < 10 min in the week = no point), in their bars' colours; bars unchanged, still `"sports": ["trail"]`; help + en legend updated |
| 2026-10-06 | fix | SP-243 follow-up | The 5 + 5 有杖 / 沒杖 marks count only the trail runs and hikes the chart uses (user decision): `pole_compare.counts` / `chart_rows` / `used` (same `climb_vam.kind_of` as the panel) feed `needs_met`, the panel and `GET /activities` → `pole_compare`; each activity gets `pole_chart` (the editor's live recount skips the others); hint / empty / help text say so (zh-TW + en). 365-day window, 2 min per bin and VAM ÷ HR unchanged |


## Banded charts and the 使用功率 setting (2026-10)

- **`zoned`** (`backend/engine/wko5expr/customviews.py:151`): a chart option
  `{"line": "<series name>"}`, optionally with `"ref": {"y": …, "label": …}`
  (a thin dashed reference line, e.g. 負荷比's 「1 = 跟平常一樣」). The viewer
  (`zonedSetup`, `backend/static/wko5_viewer.html:2133`) turns the chart's
  `{lo:hi}` band series into shaded bands with solid edges and the band's
  short name in the right margin, draws reference lines solid,
  colours the named line by the band it is in (ECharts piecewise visualMap,
  the band hue pulled 20 % toward the text ink, a surface halo) and labels the
  latest value with its band's first word (「1.12 正常」); the hover reads
  「值 · 區間（範圍）」. No legend box. Used by
  狀況 Form%, 負荷比 and 減量期 新鮮度 Form%. The Form% zones (過度疲勞 / 有效訓練 /
  持平 / 比賽狀態 / 休息過久) also colour the PMC TSB bars, so a day has one colour
  in both charts.
- **使用功率** (`charts.power.enabled`, default auto — on with Stryd, watch
  power only when `power.accept_watch_power` is on, off without a power meter
  (`backend/engine/athlete_profile.py:97`); `use_power` in
  `GET/PUT /api/v1/sync/settings`): `backend/engine/wko5expr/power_use.py`
  marks each chart in `GET /views` with `power` (every data series reads power
  / CP, or a power-only panel: watt zone tables, power zones, CP test, W′ and
  interval cards) and `power_basis` (a 配速／功率 toggle). With the setting off
  the viewer hides `power` charts (and pages left empty), locks `power_basis`
  charts to pace, shows HR zones only in periodzones and the HR panel only in
  心率與功率; the overview drops the CP threshold and 套用 CP button, the
  activity page the 功率來源 field. Models and calculations are unchanged.
