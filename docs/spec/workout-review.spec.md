# Module Spec: workout-review

> **Last Updated**: 2026-10-02
> **Status**: Active
> **Domain Layer**: Core Domain

## Overview

Single-activity review: the 判讀卡 (verdict cards) that lead each dashboard of
the 單次活動判讀 view. For one workout it decides what kind of session it was
(easy / 中強度 / long / 高強度長跑 / Z5 間歇 / Z3 閾值 / CP test / AeT test, or strength / bike / walk / other),
on what terrain and in which training phase, then measures what one chart
expression cannot — Pa:HR drift of a steady run, work bouts, climbs, durability,
pacing by distance, and form drift — and writes at most three verdict lines per
card, compared against the athlete's own 8–12-week baseline.

The same measurements feed the progression decisions: the drift series is
informational in `status.i_drift` (the base-phase interval gate is
`engine/quality_gate.py`, see `overview.spec.md`), the measurements feed the
gate's Friel / 徐國峰 methods and dose count, and the latest CP and AeT tests
drive `status.i_testing`.

Design doc: `docs/plans/done-workout-review.plan.md:1`. The verdicts are coaching
heuristics; the knee / form card always says 參考 (`backend/engine/workout_review.py:35-37`).

## Architecture

```
 views/workout.json ── customviews (kind "review" + section)
          │
          ▼
 /api/v1/wko5 render ── _panel_kind: review → "workout" ── _render → workout_review.review(ds, w, section)
 /api/v1/wko5/workouts/{i}/review ─────────────────────────────────┘
                                                                     │
          measure (disk-memoised) ── classify ── baseline_for ── section builders (_summary … _cp)
                 │
     drift_of · detect_efforts · cp_test · detect_climbs · form_drift · downhill_share · pacing_deciles
                 │
 status.i_drift ◄── drift_series      status.i_testing ◄── latest_cp_test · aet_test.latest_aet_test
 quality_gate (friel_check · xu_check · dose_history) ◄── measure · classify · _samples
 season drift charts ── evaluator drift("pace" | "power") ── measure (heat_band on read)
```

| Layer | Responsibility | Entry point |
|---|---|---|
| Pure analyses | Arrays in, numbers out; unit-tested on synthetic data | `backend/engine/workout_review.py:224` |
| Session typing | Plan order: category → test_cp → test_aet → quality → long → easy | `backend/engine/workout_review.py:481` |
| Dataset adapters | Samples, thresholds on the date, per-workout measure, classify, peers, baselines | `backend/engine/workout_review.py:615` |
| Progression hooks | Drift series (informational), latest CP test; the AeT test lives in `engine/aet_test.py` | `backend/engine/workout_review.py:955` |
| Verdicts | Line builders for aerobic, interval and CP cards | `backend/engine/workout_review.py:877` |
| Review JSON | One card per section, in the shape the viewer's `draw()` renders | `backend/engine/workout_review.py:969` |
| View definition | Kind `review` with a `section` | `backend/engine/wko5expr/customviews.py:94-100` |
| API | Render branch and the review endpoint | `backend/api/wko5views.py:253`, `backend/api/wko5views.py:304` |

## Measurement (`measure`)

`_measure` (`backend/engine/workout_review.py:615`) reads the workout's channels
(`backend/engine/workout_review.py:592`) and returns one JSON dict: category,
moving / elapsed time, average HR and power, AeT / LTHR / CP in effect on the date
(`backend/engine/workout_review.py:550`), time over AeT+3, three-zone time
(< AeT, AeT–LTHR, ≥ LTHR), hard time, climb rate, drift, efforts (first 40),
interval summary, CP-test result, climbs, median HR per 100 m climbed, Stryd flag
and form drift.

- Moving = sample interval ≤ 30 s and, with a speed channel, above 1.6 km/h
  (WKO5's 1 mph) (`backend/engine/workout_review.py:67`, `backend/engine/workout_review.py:179`).
- Hard time = max(seconds with HR ≥ LTHR, seconds with 30-s power ≥ 95 % CP)
  (`backend/engine/workout_review.py:641-653`). HR time leaves out recording gaps
  (sample interval > 30 s), so a pause between two samples above LTHR is not counted.
  `hard_power_s` exists for runs **and hikes** (`QUALITY_CATEGORIES`,
  `backend/engine/workout_review.py:61`) with power and a CP; efforts are detected
  for the same sessions (`backend/engine/workout_review.py:661`).
- Memoised on disk through `Dataset.cached_series` under key `workout_review_v11`
  (v7: Pw:HR halves and `cp_bouts`; v8: drift_of's 40 min after the warm-up, fast finish,
  one Pa/Pw window, `watch_temp_c`; v9: the two drift tiers, `ref_ok` / `pw_ref_ok` / `tier`;
  v10: the adaptive start, `warmup_s` / `start_shift`; v11: drift v2 — `end_s`, `tail`,
  `idle_s`, `vi`, `cv30`, `cv30_w1`, `walks`, `walk_max_s`, `halves_diff`, `drift_se`,
  `pw_drift_se`, `noisy`, `ramps`; `drift_of` also gets `dist` / `elev`) (`backend/engine/workout_review.py:71`,
  `backend/engine/workout_review.py:907`, `backend/engine/wko5expr/dataset.py:403`). The key
  holds the file and thresholds, not the code, so the version is bumped whenever `_measure`
  changes. Phase, classification, baselines and verdicts are recomputed on each call.
- The drift's temperature band is **not** cached: `measure()` re-applies `heat_band` on every
  read with `activity_temp`, because the route_weather archive is not part of the cache stamp
  and a routes build can fill it later. `_measure` stores the watch's raw mean temperature
  over the drift window as `watch_temp_c`; the wrist bias is subtracted on read. Key
  `workout_review_v18` since the session classifier (`stim`, 2026-10-02; v17 = the heat bands).

## Heat bands (2026-10-02, user-approved)

`drift_of` no longer refuses a run above 25 °C — in Taiwan most of the year is above it.
Every drift result carries `temp_band` (`temp_band()`: ≤ 25 °C `cool`「< 25 °C」, ≤ 28 °C
`warm`「25–28 °C」, else `hot`「> 28 °C」, `none`「溫度不明」 without a temperature) and `heat`
(warm or hot). Air temperature, not Hadley: the drift sources give °C and the watch path
has no humidity of its own.

| Rule | Source / status |
|---|---|
| 25 °C (`DRIFT_HEAT_C`) | 台灣教練 < 25 °C; Lafrenz 2008 (35 °C HR +11 % vs 22 °C +2 %, DOI 10.1249/MSS.0b013e3181666ed7) |
| 28 °C (`DRIFT_HOT_C`) | **推估**: Beiter 2025 (Physiol Rep, DOI 10.14814/phy2.70305) 28.7 vs 19.2 °C, HR +16 bpm — the hot condition sits just above 28; no source gives a cut-off |
| Temperature source | `activity_temp`: Open-Meteo archive by file, else the only archive row of that date (a COROS / TP dataset's files are not the WKO5 names the archive is keyed by — `zone_events.weather_of`'s rule), else the watch minus the wrist bias `WATCH_BIAS_C` 3.7 °C (`watch_air`; 72 paired route efforts on one runner's data, 推估, lower confidence) |
| Compare within a band | `status.i_drift` (the band of the latest fair run when it has ≥ 2, else the band with most; `drift_agg.pick_band`), `drift_agg.rolling` / `drift_avg()` (the season charts: one 6-run mean per band), the aerobic card's 同類課表基準. `aet_points` (the AeT aggregate) regresses across runs, so it reads the cool band, `none` and — heat-adjusted — the warm band (next row); the hot band stays out |
| AeT heat covariate (feat/aet-heat-covariate) | A warm run's first-half HR is moved to 25 °C before `aet_aggregate`: hr1′ = hr1 − β·(T − 25); cool / `none` runs are not moved (the AeT test is run < 25 °C and `aet_test_reason` "moved" compares with it — Hadley 120 as the reference would put every estimate ~7 bpm under a cool-day test). The drift is not adjusted. β (bpm/°C) = `drift_agg.heat_beta`: default **1.0 bpm/°C** (Jenkins 2023, Exp Physiol, doi 10.1113/EP090969: cycling 70 % VO2peak at 18/27/36 °C, 「1 bpm/°C」; for running 推估) shrunk toward the athlete's own OLS hr1 = a + b·P1 (else v1) + β·T over 365 d of road runs with a drift tier and a temperature (≥ 10 runs, temperature SD ≥ 2 °C), w = n/(n+20), clipped to 0–2 (all 推估). No weather → no adjustment. The validity reason (the ? text) ends 「含 N 次 25–28 °C 的跑步：前半心率先扣掉熱的影響 … 推估」 |
| Gates | Friel / 徐國峰 90 min / AeT test still apply in every band. Heat inflates the drift, so **a pass in heat unlocks** (conservative) and says 「熱環境，結果可能偏高；熱天通過仍算數」; a fail in heat stays a fail, 「…可能是熱造成的」. UA's 「at」 band in heat may apply its AeT: the true drift is lower, so the first-half HR can only be on the low side. `i_drift` never turns BAD in a heat band |
| No heat-adjusted drift | Skipped: the measured β 0.224 bpm／Hadley (`heat.HR_BETA`) is a between-run HR level shift at a given power, not the within-run rate at which HR climbs; a drift is the HR change from half 1 to half 2 at roughly one ambient temperature, so β·ΔHadley between halves is ≈ 0 and would not remove the heat effect (core temperature, skin blood flow, fluid loss). No source gives a per-°C drift correction (`unsourced-rules.md` §B6: 逐度劑量反應未找到來源) |
| Display | The aerobic card's header chip 「🌡 25–28 °C」 (`res.chip` {text, tip, heat, band}; the viewer's `draw()` puts it in the h2 with the why on hover), the 溫度 row 「來源 N °C · 🌡 …」, a verdict line 「🌡 …：熱環境，結果可能偏高」 in heat; the overview indicator's text ends 「· 🌡 …」 |
| Session text | 「氣溫 25 °C 以下時開始（熱會讓心率偏高、飄移失真）」 stays as advice (`aet_test.HEAT_TEXT`) |

## Analyses

| Function | What | Rule / thresholds | Line |
|---|---|---|---|
| `drift_of` | Pa:HR decoupling, (r1 − r2)/r1 with r = speed/HR over the two halves of the moving time after a 10-min warm-up; Pw:HR the same with power (`pw_drift`, `p1`/`p2`, `pw_hr1`/`pw_hr2`), through the shared `_halves_drift` (`backend/engine/workout_review.py:243`). **One window for both bases**: moving samples after the warm-up with HR, speed and power valid; when power covers < 95 % of the Pa:HR window (推估), Pa:HR keeps the whole window and Pw:HR is refused (「功率只涵蓋 N%」). Returns `measured_s`, `finish`, `temp_c` / `temp_src`. **Two tiers** (one window, so one tier for both bases): 嚴格 / test = `ok` / `pw_ok`, ≥ 40 min after the warm-up; 參考 / reference = `ref_ok` / `pw_ref_ok`, every other check passed and 30–40 min after the warm-up (`DRIFT_REF_MIN_S` 1800, **推估**: no source gives 30 min; Coyle & González-Alonso 2001, Exerc Sport Sci Rev, DOI 10.1097/00003677-200104000-00009, show cardiovascular drift starting after ~10–20 min, and UA's 40 min is for a formal AeT test) — `drift` / `hr1` … filled, `ok` False, `reason` = the strict refusal. `tier` "test" / "ref" / None (`drift_tier`) | Strict refusal (with a reason) when: no HR/speed; **< 40 min of moving time after the warm-up** (`DRIFT_MIN_S`; UA's 40–60 min is the test after the warm-up, https://uphillathlete.com/aerobic-training/heart-rate-drift/; 30–40 min → the reference tier, < 30 min refused in both); trail or ≥ `TRAIL_CLIMB_RATE_M_PER_KM` climbed per km; stopped > 5 % of the window (v11: the window ends at the return-leg cool-down / trailing idle); a run-walk, VI > 1.04, halves power (pace) difference > 5 % (v11, see drift v2 below — the old 30-s power CV > 15 % is information only); mean power > 90 % CP; **fast finish**: the last 10 % of the measured time > 5 % above the rest, power or pace (推估, `docs/research/aerobic-base-readiness.md:513`); < 600 s usable. Heat is **not** a refusal since 2026-10-02 (see Heat bands). A refusal applies to both bases **and both tiers** (only the length differs); a fair run without power gets `pw_reason` 這次沒有功率 | `backend/engine/workout_review.py:290`, `backend/engine/workout_review.py:76-82` |
| `steady_start` | drift_of's adaptive warm-up (**推估**): start = max(10 min, end of the last stop — a sample at ≤ 1.6 km/h, a recording gap is not a stop — that begins in the first 20 min + 60 s); kept at 10 min when the later start would drop below a tier (40 / 30 min) the fixed start reaches (`fallback`, the stops then count toward the 5 % rule). Every drift_of check and the halves run from that start (power CV sliced by grid time). `warmup_s`, `start_shift` {stops, stopped_s, last_stop_s, shifted_s, fallback}; the aerobic card prints 「（前 m:ss 不算）」 and an 「已排除」 row (`excluded_text`, hover `START_TIP`). Ramps and strides are **not** masked (user decision 2026-10-01: HR after a ramp may not have recovered). Data: `docs/research/drift-steady-window-data.md` — on 8 weeks of one runner's data it changes no tier | `DRIFT_EARLY_S` 1200, `DRIFT_SETTLE_S` 60 (both 推估, 未找到來源) | `backend/engine/workout_review.py` |
| drift v2 (`docs/research/drift-algorithm.md`, user-approved 2026-10-01) | Window = [start, end): **`trailing_idle`** cuts trailing non-moving time ≥ 2 min (the watch not stopped; `idle_s`, not counted toward the 5 % stop rule); **`steady_end`** takes the return-leg city section as the cool-down: the last cluster of stops (≤ 1.6 km/h) that start ≤ 12 min before the end, ≤ 6 min apart and after minute 20 → excluded from the cluster's first stop (`tail` {stops, first_stop_s, excluded_s}, `end_s`; no tier protection). The stop rule runs on the window only. **Stability gate on the window's moving samples** (1-s grid): `walk_breaks` (30-s speed < 75 % of the window's median moving speed for ≥ 60 s; the longest ≥ 180 s = a run-walk, refused); **VI = NP30/AP ≤ 1.04** (`power_vi`, Coggan's NP; replaces the unsourced 30-s CV > 15 %, which stays as `cv30` — the old way, every power sample after the start — and `cv30_w1` on the window); > 90 % CP on the window; **\|P2 − P1\|/P1 ≤ 5 %** over the halves (`halves_diff`, pace without power); then the fast finish. Ramps are **not** excluded (user decision): `ramp_contrast` gives ramps (\|15-s grade\| ≥ 3 % for ≥ 10 s) per half, climb per half and the ramp-free halves drift (ramps + 120 s after left out) — display only. **Precision**: `drift_regression` — HR = a + b·P_filt(τ = 60 s) + c·t on the window, drift_eq = c·T/2 ÷ HR₂, SE(c) × the AR(1) correction √((n−k)/(n_eff−k)) → `drift_se` / `pw_drift_se`; `noisy` = SE > 5 pp. Single runs are ±4–6 pp (DRIFT §1.3) | 2 min, 12 / 6 / 20 min, 75 % / 60 s / 180 s, VI 1.04, 5 %, 3 % / 10 s / 120 s, SE 5 pp: **推估** (calibrated on one runner's runs, DRIFT §4, §7). τ 60 s: the max-likelihood on those runs, inside Hunt 2015 / 2019, Wang & Hunt 2021's 55–70 s (peer-reviewed); τ is used only for the SE. Excluding the cool-down: UA, Friel (coach) | `backend/engine/workout_review.py` |
| Multi-run aggregate (`backend/engine/drift_agg.py`) | `aggregate`: the last 6 drifts (both tiers), inverse-variance weighted (w = 1/SE², SE floor 0.5 pp, default 5 pp without one), SE = max(√(1/Σw), weighted SD/√n). `rolling` for the season charts (8 weeks, **same temperature band only**, `band` on each). `aet_points` / `aet_validity`: drift points (first-half HR, Pw:HR else Pa:HR, SE) of road runs in 180 days, cool / warm (heat-adjusted HR) / no temperature (`AET_BANDS`) → `threshold_estimate.aet_aggregate` (weighted regression, crossing 5 %, delta-method SE scaled by the reduced χ²); valid = SE ≤ 3 bpm and the last 6 points' mean horizontal offset ≤ 5 bpm (`unsourced-rules.md` §B3) | 6 runs: Ikari 2026 (SportRxiv preprint) and `AET_MIN_RUNS`; weights, floors, SE rule: 推估 | `backend/engine/drift_agg.py`, `backend/engine/algorithms/threshold_estimate.py` |
| `fast_finish` | Time-weighted mean of the last 10 % of the mask's time vs the rest − 1 | > `DRIFT_FAST_FINISH` 0.05 refuses (推估) | `backend/engine/workout_review.py:264` |
| `heat_band` (alias `heat_gate`) | Tags a drift result with `temp_c` / `temp_src` / `temp_band` / `heat`; never refuses (2026-10-02). Idempotent | Bands: see Heat bands | `backend/engine/workout_review.py` |
| `activity_temp` | (°C, source): the route_weather archive's air temperature for the file (`activity_weather.json`, moving-weighted, `backend/engine/route_weather.py:300`), else the only archive row of that date — the air is what the 25 °C rule (台灣教練) means, a wrist sensor is warmed by the body (`docs/research/aerobic-base-readiness.md:515`) — else the watch's `watch_temp_c` minus 3.7 °C (`watch_air`); a dataset may carry `activity_temps` (tests; `conftest` points `routes.HOME` at a temp folder so the date fallback never reads the real archive) | Archive read cached on the file's mtime | `backend/engine/workout_review.py` |
| `basis_drift` | (drift, reason) of a `drift_of` result for pace or power; strict by default, `ref=True` also returns a reference-tier value | Gates and thresholds (`quality_gate.friel_check` / `xu_check`, `classify`'s steady AeT test, the AeT-test bands) read strict; display (`_aerobic`, `aerobic_lines` except on `test_aet`, `drift_series(ref=True)` → `i_drift`, `drift(basis, "ref")`) opts in, labelled 「參考（暖身後 30–40 分，未達 UA 測試標準）」 (`REF_LABEL`, hover `REF_TIP`) | `backend/engine/workout_review.py:390` |
| `detect_efforts` | Work bouts in the 1-s power stream | 30-s power ≥ max(0.85 CP, 1.12 × session median) (1.15 × median with no CP), ≥ 60 s, gaps < 30 s bridged; HR drop 60 s after the HR peak, skipped only when the next bout that is itself an effort (≥ 60 s) starts within those 60 s (`backend/engine/workout_review.py:336`) | `backend/engine/workout_review.py:294` |
| `interval_summary` | Set band (median %CP), reps in band (±1 %), fade last vs first, median HR drop | Bands 閾值下 0.88–0.95, 閾值 0.95–1.01, 超閾值 1.01–1.06, VO2max 1.06–1.16, 無氧 ≥ 1.16 ×CP | `backend/engine/workout_review.py:346`, `backend/engine/workout_review.py:88` |
| `cp_test` | Best 12′ window, then the best 3′ window ≥ 10 min away (never overlapping); two-point CP, or the single-bout fallback when P3 ≤ P12 | — | `backend/engine/workout_review.py:369` |
| `looks_like_cp_test` | Two separate all-out efforts | 3′ ≥ 115 % and 12′ ≥ 98 % of the current CP (or a separate 3′ ≥ 98 % on the single-bout fallback) | `backend/engine/workout_review.py:407` |
| `cp_protocols.measure_bouts` | Per-protocol bouts, memoised as `cp_bouts`: non-overlapping 12′ / 3′ (+ gap, order), best 20′, best race-like 15–70 min window, best 3′; each bout's HR peak (+15 s lag) and last-minute power | See "CP-test protocols" below | `backend/engine/cp_protocols.py:167` |
| `form_drift` | First ⅓ vs last ⅓ of moving time for ILR, LSS, kleg, GCT, cadence, VO, impact G | With a cadence channel only samples ≥ 65 strides/min (130 spm) count | `backend/engine/workout_review.py:390` |
| `downhill_share` | Steep downhill (grade < −10 %) share of time, distance and ILR·dt | — | `backend/engine/workout_review.py:415` |
| `pacing_deciles` | Moving pace, HR, power per 10 % of the distance | Needs ≥ 0.5 km | `backend/engine/workout_review.py:439` |
| `baseline` / `compare` | Median and IQR; high / low / within | No comparison under 5 samples | `backend/engine/workout_review.py:460`, `backend/engine/workout_review.py:469` |
| `baseline_for` | Same category (and session type) over the previous 8 weeks, widened to 12 when 8 has < 5 | — | `backend/engine/workout_review.py:769` |

Climbs come from `algorithms.climbs.detect_climbs`, the grade table from
`panels.workout.grade_bins`, and the durability curve from
`panels.workout.durability` (`backend/engine/workout_review.py:50`,
`backend/engine/workout_review.py:54`). kleg and impact G are evaluated through the
expression engine (`backend/engine/workout_review.py:581`, `backend/engine/workout_review.py:684-686`).

## Classification (`classify` / `session_type`)

`classify` (`backend/engine/workout_review.py:718`) returns `type`, `type_label`,
`terrain` (road / trail / hike, from `overview.category`), `phase` (from
`planning.phase_on` on the activity date), labels and date. A hike classified
easy is labelled 輕鬆健行 (`backend/engine/workout_review.py:741`).

`session_type` (`backend/engine/workout_review.py:629`), in order:

1. strength / bike / walk / other → that category.
1b. **the plan's AeT test** (`plan_aet`): `scheduled_aet_test`
   (`backend/engine/workout_review.py:967`) — a stored test session that is the AeT
   test (`aet_test.is_aet_session`: protocol or kind `aet`, gen_key / id `test_aet`
   for rows stored before the protocol field, or an AeT title,
   `backend/engine/aet_test.py:297`) in state done whose `done_by.index` is this
   activity and `done_by.date` its day — the same match as the CP test's done_by.
   `scheduled_test` skips AeT sessions, so a done AeT test is never read as a CP
   test (before, any done kind-`test` row matched and the activity became `test_cp`).
   `plan_store.test_sessions` returns `gen_key` for this (`backend/engine/plan_store.py:367`);
   generated AeT sessions now carry `protocol: "aet"` (`backend/engine/aet_test.py:283`,
   kept by `projection._bq`).
2. `test_cp`: plan threshold record with a CP on that date, a title matching
   `CP` or 測試, or a detected test (`cp_detected`). `classify` decides the
   detection in this order (`backend/engine/workout_review.py:821`):
   1. **the plan first** (`scheduled_test`, `backend/engine/workout_review.py:773`):
      a stored kind `test` session in state done whose `done_by.index` is this
      activity and `done_by.date` its day; else an active / missed test session
      the same day **and** a ≥ 3-min bout ≥ 1.05 × the CP in effect
      (`backend/engine/workout_review.py:753`). The sessions come from
      `plan_store.test_sessions` (read-only sqlite, cached on the DB mtime,
      `backend/engine/plan_store.py:364`); a dataset may carry its own list.
   2. a 5–10 K race or TT (protocol `race`): 15–90 min moving and a race / TT
      title or a plan race event that day of 4–11 km (`backend/engine/workout_review.py:794`).
   3. the power pattern — `looks_like_cp_test` (standard), else a 20′ window
      ≥ 1.03 × CP whose HR reached LTHR (quick, 推估,
      `backend/engine/workout_review.py:811`) — is **no longer a label on its
      own** (2026-10-01: it labelled ~35 hard 5 km training runs as CP tests
      against WKO5's mFTP snapshot). Unmarked, it only sets
      `cp_hint` (the summary card's 「CP 測試？」 row, `CP_HINT`); on a test marked
      by the plan, a threshold row or the title it picks the protocol.
   `classify` returns `protocol` (the session's, else the method of a
   threshold row that day, the title, the pattern) and `test_match`
   (done_by / same_day / race / threshold / title / pattern).
3. `test_aet` fallbacks, after the plan's session: a title with 「AeT」 (checked before
   the CP test's 「測試」 title rule unless the plan / a race marked a CP test), a plan AeT record on
   that date, or a fair (strict-tier) drift on a road run with moving time ≥ 55 min. The
   planned test is 80 min (15′ + 60′ + 5′) or, under a weekday cap < 80 min, UA's 50-min
   minimum (10′ + 40′): it is matched by the plan's done_by, its 「AeT」 title
  or the AeT row; the untitled steady fallback stays at 55 min so ordinary
  41–52′ road runs are not taken for tests (and never offer 「套用這次的 AeT」). `classify`
   returns `test_match` done_by / title / threshold / steady for a `test_aet`
   (`backend/engine/workout_review.py:1029`, `MATCH_LABEL` `backend/engine/workout_review.py:994`).
   **User mark** (2026-10-01): a run the user tagged activity type 測試 (`engine/activity_tags.py`,
   the 活動資訊 card) that no other rule made a test becomes `test_aet` when the title says AeT,
   else `test_cp`, with `test_match` "user" (accepted by the race-power back-test's
   `wko5_cp_tests` like a plan / title mark). Other tags do not demote an auto test.
4. **the stimulus** (2026-10-02, `session_class`; design and sources
   `docs/research/vo2max-session-detection.md`; per-run numbers in `measure()["stim"]`
   from `engine/session_stimulus.py`, the verdict in `classify()["stim"]`):
   - `quality` / `stimulus "z5"` 「Z5 間歇」: road or trail, equivalent T@VO2max ≥ 4 min.
     T_p = VO2 bouts on power-trusted samples (road; trail −3…8 % grade; never hikes):
     10-s power ≥ 1.03 CP extended over raw seconds ≥ 1.03 CP, mean ≥ 1.06 CP for ≥ 2 min
     (minus 60 s, the day's first 90 s) or 1.03–1.06 CP for ≥ 5 min (minus 180 s), 5 s slack.
     T_h = wrist HR ≥ 0.93 × HRpeak in runs ≥ 60 s where power is not trusted, moving, not
     downhill, not cadence-locked (HR within 3 bpm of the cadence **and** following it:
     r ≥ 0.8 with ≥ 1.5 spm of cadence SD, 60-s windows); counted ÷ 1.6. HRpeak = the plan's
     最大心率 (`mhr`), else the 3rd-highest per-run 60-s peak of road / trail runs in 365 days
     (`hr_peak`, memoised per dataset). Long runs too (a race is Z5).
     ≥ 4 min of **power** evidence is Z5 even when the average HR stayed ≤ AeT+3.
   - Zone 3 time: power-trusted 30-s power ≥ 0.88 CP in runs ≥ 150 s, plus HR-only samples
     ≥ 0.95 LTHR minus the first 3 min of each run (≥ 150 s left). ≥ 10 min and avg HR not
     ≤ AeT+3: moving ≥ 75 min → `hard_long` 「高強度長跑」 (hikes 「高強度長天」: a hard
     day, not an interval session), else `quality` / `stimulus "z3"` 「Z3 閾值」.
   - Hikes (百岳) never get Z5 automatically (the 「當作間歇判讀」 mark still works).
5. `long`: moving ≥ 75 min, or ≥ 0.8 × a long-run target.
6. `easy` — with `moderate: true` and the label 「中強度跑」 (hikes 「中強度健行」) when the
   average HR is above AeT+3 (informational; the drift is still judged).

`classify` also returns `stimulus`, `moderate`, `stim` (equivalent / power / HR seconds,
the bouts, Zone 3 seconds, HR threshold and HRpeak source, `easy_hr`) and `icon` (dashicons
name: z5 / z3 / long / intensity / easy / test / strength, `type_icon`). The old rule (≥ 10 min
HR ≥ LTHR or 30-s power ≥ 95 % CP) is gone: on 12 months of one runner's data it made 100 of 188
runs 「品質課（間歇）」 (backtest in the research doc §5).

## Verdicts and cards

| Section | Card | Line |
|---|---|---|
| `summary` | Type · terrain · phase, time, HR vs AeT/LTHR, three zones; the type's verdict lines (trail / hike lines first); 建議分頁 | `backend/engine/workout_review.py:994` |
| `aerobic` | Header chip 「🌡 < 25 °C／25–28 °C／> 28 °C／溫度不明」 (`res.chip`, hover `HEAT_TIP`); the 溫度 row names source, °C and band; the baseline is the same band only. Drift on the chosen basis (Pa:HR with HR/speed per half, or Pw:HR with HR/power per half; 這次沒有功率 without power) — a reference-tier value gets 「（參考）」 and a 飄移等級 row 「參考（暖身後 30–40 分，未達 UA 測試標準）」 with the why as hover (`_row(…, tip)` → `data.tip`, the viewer's row `title`), a strict one 「嚴格（暖身後 ≥ 40 分，UA 測試標準）」 — time over AeT+3, same-type drift baseline on that basis (both tiers). v11: the drift reads 「x% ±y pp」 (hover `SE_TIP`: single runs are ±4–6 pp, judge the 6-run mean), 「精度」 when SE > 5 pp (「這次很吵…只當多次平均的一個點」), 「已排除」 adds 「回程市區段 m:ss（當緩和：從 m:ss 起、停等 N 次）」 and 「結尾靜止 m:ss（錶沒按停，已裁掉）」, 「穩定度」 (VI, the longest slow stretch, the halves difference, the old 30-s CV 只供參考), 「坡道」 (ramps per half, climb, 去坡道 value, 只當對照) | `backend/engine/workout_review.py:1109` |
| `intervals` | Per-rep table: start, duration, power, %CP, HR, max HR, 60-s drop | `backend/engine/workout_review.py:1049` |
| `climbs` | Per-climb table: gain, distance, grade, VAM, HR, HR per 100 m | `backend/engine/workout_review.py:1089` |
| `grades` | Grade bins: time, share, distance, pace, HR, power | `backend/engine/workout_review.py:1103` |
| `durability` | Moving / stopped, last-20 % durability, 補給 (no data source: 沒有補給紀錄) | `backend/engine/workout_review.py:1141` |
| `durability_curve` | Output/HR curve with a 90 % line (`points` series) | `backend/engine/workout_review.py:1158` |
| `pacing` | Pace / HR / power per 10 % distance | `backend/engine/workout_review.py:1173` |
| `form` | First ⅓ / last ⅓ / change / same-type baseline, steep-downhill share; ILR/LSS only with Stryd | `backend/engine/workout_review.py:1193` |
| `cp_test` | Protocol (and how it was matched), each bout (power, start, HR peak), CP with range and method, W′ (only when measured), quality, every check (✓ / ✗, 推估 labelled), the verdict lines; `action` = the 「套用這次的 CP」 button when not yet applied and not 不採用 | `backend/engine/workout_review.py:1427` |

Verdict rules:

- Aerobic (`aerobic_lines`, `backend/engine/workout_review.py:1066`), informational: time
  over AeT+3 > 10 % on easy / long → 下次放慢; drift < 5 % → 有氧基礎穩 (不是輕鬆跑 when
  average HR > AeT+3); 5–10 % → 後段心率往上跑; > 10 % → 有氧基礎不足或跑太快. The old
  streak lines (「連續 N 次」, 「可以加一次閾值下間歇」) are gone — no source; the gate is
  `engine/quality_gate.py`. Same bands on Pw:HR in power mode. A reference-tier drift
  reads the same bands, named 「飄移（參考）」, plus the 參考 label line; on `test_aet` the
  bands are strict only (they suggest a threshold).
- AeT test (`_aet_test_lines`, `backend/engine/workout_review.py:1285`, on the summary and
  aerobic cards): `aet_test.analyze` (`backend/engine/aet_test.py:69`) cuts the warm-up
  (`warm_for`: 15′ for 「AeT 飄移測試 60 分」, 10′ for 「… 40 分」, else 15′ when the run is
  ≥ 55′, 推估) and the cool-down (trailing 60-s output < 85 % of the block's median, the mean
  taken over existing samples so a test stopped right at 40′ isn't trimmed), analyses up to
  60′ after the warm-up, needs ≥ 40 min after it (UA, like `drift_of`; 30 s slack for lost
  samples, 推估), refuses
  stops > 5 %, 30-s power CV > 15 %, hills and a fast finish (last 10 % > 5 % above the rest,
  自訂). Heat is a band since 2026-10-02 (`_tag_heat`: the archive's air temperature when
  present, else the watch's over the block minus the wrist bias); in heat `lines` adds
  「熱環境，結果可能偏高」 — a pass / 「at」 counts (熱天通過仍算數), a fail may be the heat. Pw:HR over the halves
  (Pa:HR without power), UA's bands (`lines`, `backend/engine/aet_test.py:162`): < 3.5 % →
  still below AeT, +5 bpm next time; 3.5–5 % → first-half HR is the AeT; > 5 % → −5 bpm.
  In band "at" and not applied, the card's `action` is 「套用這次的 AeT（N bpm）」 → POST
  `/api/v1/plan/thresholds/apply-estimate` `{aethr, date, note}` (the viewer's `drawAction`,
  `backend/static/wko5_viewer.html:1022`). Without samples the three bands run on
  `drift_of`'s drift.

### Basis (配速／功率)

The 飄移判讀 card has the chart-level `basis` toggle
(`views/workout.json:46`; mechanism in
[wko5-engine.spec.md](./wko5-engine.spec.md) "Drift basis toggle").
`review(ds, w, section, basis)` (`backend/engine/workout_review.py:1051`) puts the
basis on the card; only `_aerobic` reads it. The summary card, `drift_series` /
`drift_streak`, `status.i_drift` and `overview.week_plan` stay on Pa:HR, the
documented default: Uphill Athlete's AeT drift test is a pace test
(`docs/research/uphill-athlete-mountain-metrics.md:128-135`) and running power
is for runnable terrain (`docs/research/coaching-dashboards-mountain.md:70`).

Pw:HR here replaces the earlier row that came from
`threshold_estimate.steady_drift`: that one used a 1-s grid over elapsed time and
a 45-minute floor, and was empty on every fair run checked. `steady_drift` still feeds the AeT estimate (`backend/engine/thresholds.py:34`).

**Card vs season chart.** WKO5's stored `pahr` / `pwhr` split the whole
recording at half its length with the warm-up and stops kept
(`docs/wko5-internals/workout-metrics.md:18`); the card leaves out the first 10
minutes and uses moving-time halves (1.2–9.4 percentage points lower on
three real runs with the warm-up-excluded definition). The season drift
charts now plot **the card's number**: the evaluator function `drift("pace" |
"power")` (`backend/engine/wko5expr/evaluator.py:993`) reads `measure()`'s drift
through `basis_drift` — NaN (no point) for a refused run, a non-run, or power mode
without power; runs shorter than 50 min on the clock are skipped before measuring.
`drift(basis, tier)`: "test" (default, strict), "ref" (only the 參考 tier, runs ≥ 40 min
on the clock), "all". Both charts carry a second series per basis, 「參考 Pa:HR / Pw:HR
（暖身後 30–40 分，未達 UA 測試標準）」, markers only (`line_style` none, lighter colour),
and the description explains the tier (推估, Coyle & González-Alonso 2001).
Switched: 我的訓練 能力「心率飄移 Pa:HR（暖身後 ≥ 40 分鐘的路跑，30–40 分為參考）」
(`views/training.json:247`, the 越野跑 series removed: drift_of refuses every trail
run) and 周期化訓練 ②「長時間輕鬆跑的心率飄移」 (`views/periodization.json:53`); the
basis toggle is unchanged (series tagged pace / power), and the descriptions say
which definition is used and that it differs from WKO5's stored Pa:HR. **Not
switched**: the two 耐久度 charts (trail runs > 90 min) stay on stored `pahr` /
`pwhr` — under the card's definition they would never draw a point — and say so;
WKO5's own imported charts (Athlete's views) are untouched. The render-cache
fingerprint includes the archive file (`backend/engine/wko5expr/render_cache.py:119`).
`backend/tests/test_drift_basis.py` recomputes both definitions with plain
loops: the card matches `plain_card` (v8) to 1e-9 on the one fair real run,
the three older runs are refused by the strict tier (v9: the reference
tier matches `plain_card(floor=1800)` on those without a fast finish), both season charts equal the
card for 3 real runs, and the stored values match the whole-run definition to
5e-5 (`pahr` is stored to 4 decimals) and 1e-9 (`pwhr`).
- Intervals (`backend/engine/workout_review.py:909`): reps in band; fade > 5 % → one
  rep fewer or more rest; median 60-s HR drop < 20 bpm → longer rest; band 閾值下/閾值
  with HR between AeT and LTHR → 屬於閾值下.
- CP (`backend/engine/workout_review.py:1110`): the headline by method, the first
  failed check, and the delta vs the previous result **of the same method**
  (`cp_protocols.reference`) > 3 % → update; 已套用 once a row of that day has a CP.
  The summary card of a `test_cp` also carries the apply `action`
  (`backend/engine/workout_review.py:1169`).
- Trail / hike (`backend/engine/workout_review.py:1068`): HR per 100 m vs the 8-week
  median (any session type), ±5 %; last-20 % durability < 90 % → fuelling / pacing.
- Form, reference only (`backend/engine/workout_review.py:1193`): ILR change above own
  IQR and steep downhill > 30 % → mind the knees; LSS down > 2 % with GCT up > 2 % →
  fatigue.

`review()` adds `classification` and `suggested_dashboard` to every card:
dashboard 2 for `test_cp`, 3 for trail / hike terrain, otherwise `SUGGESTED`
(easy / long / hard_long / test_aet → 1, quality → 2, others 0)
(`backend/engine/workout_review.py:99-101`, `backend/engine/workout_review.py:979`).
Unknown section or no samples → an `empty` card.

## Progression hooks

| Function | Used by | Rule | Line |
|---|---|---|---|
| `drift_series` | `status.i_drift` (`backend/engine/status.py:451`, `ref=True`), informational | Road runs (not `runningtrail`), duration ≥ 40 min, avg HR ≤ AeT+3, last 56 days; each point has `tier` and `band` (temperature band; `i_drift` compares within one); strict by default (`drift_streak`), `ref=True` keeps reference-tier drifts; runs drift_of refuses (< 30 min after the warm-up, fast finish, …) are kept with drift None | `backend/engine/workout_review.py:1137` |
| `drift_streak` / `STREAK_NEED` | legacy only (the removed 「連續 3 次」 rule) | consecutive most recent fair drifts < 5 % | `backend/engine/workout_review.py:980`, `backend/engine/workout_review.py:70` |
| `quality_gate` | thin wrapper over `quality_gate.week_decision`; a legacy bool / None gate = no method | outside base: intensity and drift not bad | `backend/engine/workout_review.py:594` |
| `measure` / `classify` / `_samples` | `quality_gate.friel_check`, `xu_check`, `dose_history` (`backend/engine/quality_gate.py:186`, `backend/engine/quality_gate.py:232`, `backend/engine/quality_gate.py:303`) | Friel: avg HR AeT−5…AeT+3, ≥ 70 min, fair drift; 徐國峰: fair ≥ 90-min run, HR@10′ vs HR@90′ (every temperature band counts; a pass in heat unlocks, a fail in heat says 「可能是熱造成的」 — `quality_gate.heat_suffix`); dose: `quality` class or ≥ 4 short reps at ≥ 95 % CP | — |
| `latest_aet_test` | `status.i_testing` (`backend/engine/status.py:660`) | Latest run classified `test_aet` in 120 days: `analyze` result, `aethr_suggest` (band "at" only), `apply_body` with the test date, `applied` once a plan AeT row is dated on / after it | `backend/engine/aet_test.py:187`, `backend/engine/aet_test.py:215`, `backend/engine/aet_test.py:223` |
| `cp_eval` / `latest_cp_test` | `status.i_testing` (`backend/engine/status.py:580`) | Latest run classified `test_cp` in 120 days, by date; its protocol's result, `ref` / `delta` vs the previous result of the same method, `apply` payload | `backend/engine/workout_review.py:976`, `backend/engine/workout_review.py:996` |

## CP-test protocols (`engine/cp_protocols.py`)

Design: `docs/research/cp-test-protocols.md:316`. The user picks the protocol
in 課表偏好 (`plan.prefs.cp_test_protocol`, default `quick`, user
decision 2026-09-30); the session side is in `overview.spec.md`.

| Protocol | Result | Method | Checks (fail → 參考 unless noted) |
|---|---|---|---|
| `standard` 12′ + 30′ + 3′ | CP = (P12·720 − P3·180)/540, W′ = (P3 − CP)·180 | `2pt` | model: P3 > P12 and W′ within the prior ± 2 SD; 3′ HR peak ≥ 12′ HR peak − 8 bpm (推估); 12′ last minute ≤ 1.08 × its average (推估); ≥ 25 min between bouts |
| `standard`, model or 3′ HR fails | CP = P12 − W′prior/720, range ± 1 SD of the prior | `1pt_prior`, always 參考 | as above; 12′ HR peak < LTHR − 5 → 不採用 (推估) |
| `quick` 20′ all-out | CP = 0.95 × P20 (Ñancupil-Andrade 2024); cross-check P20 − W′prior/1200 within 3 % | `tt20` | pacing (推估); HR peak < LTHR − 5 → 不採用 (推估) |
| `race` 5–10 K | CP = P · (T/1800)^0.07 (Riegel k −0.07 at 30 min, 外插), 15–70 min only; the window with the highest converted CP | `race` | HR vs LTHR |

- W′ prior: Ruiz-Alias 2025, men 13.1 ± 4.0 kJ, women 6.4 ± 2.2 kJ (by `plan.profile.sex`)
  (`backend/engine/cp_protocols.py:43`). Thresholds labelled 推估 are ours
  (`backend/engine/cp_protocols.py:54-57`); failed checks say「（推估門檻）」.
- `reference` (`backend/engine/cp_protocols.py:349`): the latest plan threshold
  before the test day with the same `cp_method` (none = legacy 3′/12′ = `2pt`);
  else the latest CP row converted between the two-point and the 30-min
  definition (× 1.05, 外插); else the CP in effect. So rotating quick and
  standard doesn't keep flagging 要更新.
- `apply_payload` (`backend/engine/cp_protocols.py:377`): `{date = test day, cp, wprime
  (2pt only), cp_method, activity_index, note, label}`; None for 不採用. 參考 labels
  the button「套用這次的 CP {cp} W（參考）」 and applies the point estimate.
- The synthetic 範例跑者 test (`backend/tests/fixtures/make_cp_test_fixture.py`: 3′ 234 W < 12′ 238 W,
  3′ HR peak 152 vs 177, 16 min apart) → `1pt_prior`, CP ≈ 220 W (214–225), 參考
  (`backend/tests/test_cp_protocols.py:286`).

## View (`views/workout.json`)

View 單次活動判讀, six dashboards in `SECTIONS` order, each led by a review card
(`views/workout.json:2-5`): 本次重點 (summary card, HR + power with AeT / LTHR /
95 % CP lines, three-zone time, cp_test card), 有氧／心率飄移 (aerobic card, HR vs
AeT, rolling EF, HR per km), 間歇 (intervals card, 30-s power with 88 / 95 / 101 % CP
lines), 爬坡與地形 (climbs and grades cards, grade vs HR and grade vs power
scatters), 配速與耐久 (durability, durability_curve, pacing cards), 跑姿與膝蓋負荷（參考）
(form card, ILR vs grade and impact G vs grade scatters) (`views/workout.json:80-130`).

### Activity charts (kind `activity`, `backend/engine/panels/activity_charts.py`)

Chart kind `activity` with `chart` ∈ `hrpower`, `hrzones`, `powerzones`, `hrtrend`; the API
reports it as panel kind `workout` and renders `activity_charts.render` (JSON `kind` `act_*`,
drawn by the viewer's `drawActivity`).

| Chart | Where | What |
|---|---|---|
| `hrpower` 心率與功率（拖曳選一段看統計） | 本次重點 (replaces the dual-axis HR + power chart) | HR (top) and 30-s power (bottom), two stacked panels on one time axis — two units never share a y-scale; AeT / LTHR / CP dashed. Each plotted point carries running totals on a 1-s grid, so a brushed range gets exact stats at plot resolution (`range_stats`, mirrored by `ACT.stats`): elapsed / moving time, distance, moving pace, avg / max HR, avg power (zeros included), NP (whole-run 30-s rolling power), Pw:HR = avg P ÷ avg HR, EF = NP ÷ avg HR, the range's Pw:HR halves (WKO5's definition) |
| `hrtrend` 心率變化與趨勢 | 有氧／心率飄移 | WKO5 「Heart Rate Variation and Trend」 + 「Heart Rate Format」 (`docs/wko5-views/workout-view.json`): 1-min HR, `slr(heartrate)` trend (x = elapsed s), avg ± 1 `stddev` (n − 1) band, CV = SD / avg (< .33 穩定, < .66 混合), slope `slrm` ±0.0005 bpm/s = 持平, PWHR (PAHR without power) on halves of the recording. avg is deltatime-weighted like the evaluator; the test checks avg / stddev / slrm / slrb against the expression engine |
| `hrzones` / `powerzones` 心率／功率區間時間 | 有氧／心率飄移 | Seconds per zone (1-s grid, value > 0) under every model, the viewer picks one (`localStorage` `wko5viewer.zmodel`); unavailable models are listed disabled with the reason. HR: Friel 7 (default), WKO5 Classic, Seiler 3 (AeT / LTHR), 徐國峰 RQ %HRR (needs a resting HR: unavailable); no %HRmax model (removed 2026-10-01, zones-and-thresholds.md §2.1 — a remembered `hrmax5` falls back to the default). Power: Palladino 10 (the default, Palladino % CP, §3.2) and Palladino 3 zones only — Stryd 5 / Coggan were removed, and WKO5 iLevels (kept only as a cross-check) were removed 2026-10-02; a remembered choice falls back to the default. Tables in `zones.py` (`STRYD_ZONES`, `RQ_HRR_ZONES` added) and the evaluator's `LEVEL_TABLES["classicpower"]`; the first zone takes everything below it |

Daniels' %HRmax ranges overlap (E 65–79, M 80–90, T 88–92), so they are not offered as zones.
Tests: `backend/tests/test_activity_charts.py` (synthetic).

`customviews` accepts kind `review` and requires `section` in
`SECTIONS + EXTRA_SECTIONS` (`backend/engine/wko5expr/customviews.py:75`,
`backend/engine/wko5expr/customviews.py:94-100`). The API reports a review card to
the viewer as panel kind `workout` (`backend/api/wko5views.py:148-152`), so it
needs a selected workout (`backend/api/wko5views.py:205`) and renders through
`review()` with the chart's title and description (`backend/api/wko5views.py:253-256`).

## API

| Method | Path | Line | Purpose |
|---|---|---|---|
| GET | `/api/v1/wko5/workouts/{i}/review` | `backend/api/wko5views.py:321` | `basis` pace (default) or power, 400 otherwise. `section` given: that card (400 if not a known section). Otherwise `{workout, classification, suggested_dashboard, sections}` with the six `SECTIONS` cards. `parity` selects the dataset mode; 404 for an unknown index |
| GET | `/api/v1/wko5/views/{view}/dashboards/{d}/charts/{c}` | `backend/api/wko5views.py:253` | A review chart renders through the same branch |
| POST | `/api/v1/plan/thresholds/apply-cp` | `backend/api/plan.py:315` | 「套用這次的 CP」: the card's `action.body`; writes / merges the test day's threshold row (cp, wprime, cp_method, note). 400 for a future date, unknown method, W′ without `2pt`, CP outside 50–700 W |

The viewer draws a card's `action` as a button (`drawAction`,
`backend/static/wko5_viewer.html:995`): confirm, POST, then 已套用. The stored
test sessions are part of the render-cache fingerprint
(`backend/engine/wko5expr/render_cache.py:109`).

## Deviations from the design doc

What the implementation does differently from `docs/plans/done-workout-review.plan.md`:

| Topic | Design doc | Code |
|---|---|---|
| Quality: power | 達到 `HARD_SESSION_S` (`docs/plans/done-workout-review.plan.md:72`) | Power time uses **30-s** power ≥ 95 % CP, so second-by-second Stryd spikes on short rises don't make an easy run quality (`backend/engine/workout_review.py:644-653`) |
| Quality: HR-only | Same | With a power stream, time above LTHR alone isn't enough: it also needs ≥ 1 detected effort (`backend/engine/workout_review.py:488-491`, `backend/engine/workout_review.py:506`, `backend/engine/workout_review.py:738`) |
| Quality: easy HR | — | Average HR ≤ AeT+3 is never quality (`backend/engine/workout_review.py:501-503`, `backend/engine/workout_review.py:739`) |
| Quality: hikes | Terrain judged separately (`docs/plans/done-workout-review.plan.md:75`) | **User decision 2026-09-30**: hikes go through the same quality rule as road and trail — HR ≥ LTHR **or** 30-s power ≥ 95 % CP, with efforts detected for hikes too — a sustained climb above threshold is a quality stimulus for 百岳 — with the same exception that average HR ≤ AeT+3 means easy (`backend/engine/workout_review.py:500-507`, `backend/engine/workout_review.py:644-653`; tests `backend/tests/test_workout_review.py:34-35`, `backend/tests/test_workout_review.py:325`). This replaces the earlier implementation where hikes were never quality, and the one where hikes could only reach it through HR |
| Drift floor | i_drift ≥ 40 min (`docs/plans/done-workout-review.plan.md:101`) | `drift_of` itself refuses runs with < 40 min of moving time **after** the 10-min warm-up (UA; `docs/research/aerobic-base-readiness.md:519`) (`backend/engine/workout_review.py:76`, `backend/engine/workout_review.py:290`). On the real data (269 runs ≥ 40 min) this refuses 37 of the 38 runs v7 accepted — the steady road runs in that data are 41–52 min, 30–40 min of moving time after the warm-up; only one (41.1 min) passes. `status.i_drift` changed on 25 of 106 weekly snapshots over two years (17 levels, all to NA); today it is NA both ways; Friel / 徐國峰 gates and session types unchanged |
| Drift tiers | User decision 2026-10-01 | 嚴格 / test (≥ 40 min after the warm-up) for gates and thresholds; 參考 / reference (30–40 min, `DRIFT_REF_MIN_S`, 推估 — Coyle & González-Alonso 2001) for display, labelled 「參考（暖身後 30–40 分，未達 UA 測試標準）」. On the real data (164 road runs ≥ 40 min on the clock, read-only, 2026-10-01): 1 test, 32 ref, 131 refused (72 power CV > 15 %, 28 > 90 % CP, 20 stops, 4 hills, 4 fast finish, 3 < 30 min after the warm-up); of the i_drift easy set 8 ref, 0 test. `i_drift` in the last 56 days stays NA: those easy runs fail the power-CV check, not the length. The indicator's BAD level needs ≥ 2 strict runs (it feeds the base-phase guardrail) |
| Drift heat / fast finish | Doc §6.2 suggests both for `drift_of` | Fast finish implemented (5 % / last 10 % from the doc, 推估). Heat: a band since 2026-10-02, not a refusal (see Heat bands). Before, the > 25 °C refusal matched the archive by WKO5 file only, so on the COROS dataset it found a temperature for 5 of 161 road runs and refused none of the 32 drift values; with the date fallback 26 of those 32 are above 25 °C and would have been refused |
| Season drift charts | — | 能力 心率飄移 and periodization ② plot `drift()` (the card); the 耐久度 charts stay on WKO5's stored Pa:HR because drift_of refuses every trail run (requested switch not done for those two, see "Card vs season chart") |
| Form drift | First vs last ⅓ (`docs/plans/done-workout-review.plan.md:57`) | Only running steps (cadence ≥ 130 spm) count, so walking a steep climb doesn't read as a stiffness collapse (`backend/engine/workout_review.py:387-400`) |
| CP-test detection | 偵測到 3′ 和 12′ 兩組全力段 (`docs/plans/done-workout-review.plan.md:70`) | The plan's test session (done_by) first, then a race / TT, then the power pattern per protocol (`backend/engine/workout_review.py:821`) |
| CP-test windows | Laps within ± 10 % of the target (`docs/research/cp-test-protocols.md:408`) | Laps are not in the dataset channels: always non-overlapping mean-max windows (`backend/engine/cp_protocols.py:167`) |
| Envelope lower bound | CP ≥ 90-day MMP floor (`docs/research/cp-test-protocols.md:435`) | Not implemented |
| VAM by HR | 各心率下的 VAM (`docs/plans/done-workout-review.plan.md:47`) | Dropped: no per-sample VAM scatter in `views/workout.json`; VAM appears only per climb in the climbs card (`backend/engine/workout_review.py:1097`) |
| Not implemented | Optional `i_knee` (`docs/plans/done-workout-review.plan.md:111`); viewer auto-jump to `suggested_dashboard` (`docs/plans/done-workout-review.plan.md:122`) | Neither exists; `suggested_dashboard` is only returned and shown as the 建議分頁 row (`backend/engine/workout_review.py:1021-1022`) |

## Testing

`backend/tests/test_workout_review.py` (synthetic, not golden):

| Area | Tests |
|---|---|
| Session type table, incl. hike and easy-HR cases | `backend/tests/test_workout_review.py:40` |
| Efforts, fade, easy run has none | `backend/tests/test_workout_review.py:61`, `backend/tests/test_workout_review.py:74`, `backend/tests/test_workout_review.py:83` |
| CP formula and detection | `backend/tests/test_workout_review.py:89` |
| Drift sign, warm-up, refusals | `backend/tests/test_workout_review.py:104`, `backend/tests/test_workout_review.py:114`, `backend/tests/test_workout_review.py:125`, `backend/tests/test_workout_review.py:131` |
| Aerobic lines, baselines, streak, gate, next quality | `backend/tests/test_workout_review.py:148`, `backend/tests/test_workout_review.py:166`, `backend/tests/test_workout_review.py:190`, `backend/tests/test_workout_review.py:203`, `backend/tests/test_workout_review.py:207` |
| Fake-dataset streak and review cards | `backend/tests/test_workout_review.py:234`, `backend/tests/test_workout_review.py:248`, `backend/tests/test_workout_review.py:264` |
| View parsing | `backend/tests/test_workout_review.py:282`, `backend/tests/test_workout_review.py:293` |
| Hike power quality, gap-free hard HR, HR drop past a short surge, last_quality over hikes by date | `backend/tests/test_workout_review.py:325`, `backend/tests/test_workout_review.py:340`, `backend/tests/test_workout_review.py:354`, `backend/tests/test_workout_review.py:364` |
| Pw:HR halves and refusals, no-power text, power-mode verdicts and card | `backend/tests/test_drift_basis.py:47`, `backend/tests/test_drift_basis.py:68`, `backend/tests/test_drift_basis.py:84`, `backend/tests/test_drift_basis.py:114`, `backend/tests/test_drift_basis.py:127` |
| Drift v8: 40 min counted after a synthetic warm-up (and after a stop), hot run refused with the source named, archive before watch in `measure`, the archive file read, fast finish on pace and on power refused (+3 % kept), Pa/Pw on one window and the coverage refusal | `backend/tests/test_workout_review.py:157`, `backend/tests/test_workout_review.py:179`, `backend/tests/test_workout_review.py:196`, `backend/tests/test_workout_review.py:213`, `backend/tests/test_workout_review.py:234`, `backend/tests/test_workout_review.py:252` |
| Golden: the fair real run against `plain_card`, the 3 older runs refused, stored pahr / pwhr against the whole-run recomputation, both season charts equal the card on 3 real runs; chart definitions | `backend/tests/test_drift_basis.py:391`, `backend/tests/test_drift_basis.py:422`, `backend/tests/test_drift_basis.py:435`, `backend/tests/test_drift_basis.py:226` |
| AeT test from the plan: done_by on protocol aet / legacy gen_key / custom title (and not CP), wrong index / state / day, fallbacks title → plan row → ≥ 55′ steady, a short planned test found and refused, `protocol: "aet"` on the session | `backend/tests/test_quality_gate.py:464`, `backend/tests/test_quality_gate.py:479`, `backend/tests/test_quality_gate.py:491`, `backend/tests/test_quality_gate.py:507`, `backend/tests/test_quality_gate.py:516` |
| CP-test detection: done_by, wrong index / day, same day, pattern standard (no overlap) / quick, race | `backend/tests/test_cp_protocols.py:146`, `backend/tests/test_cp_protocols.py:156`, `backend/tests/test_cp_protocols.py:164`, `backend/tests/test_cp_protocols.py:174`, `backend/tests/test_cp_protocols.py:187`, `backend/tests/test_cp_protocols.py:195` |
| CP analysis per protocol, the synthetic 3′/12′ file (and its generator), same-method comparison, card button, apply-cp API | `backend/tests/test_cp_protocols.py:228`, `backend/tests/test_cp_protocols.py:281`, `backend/tests/test_cp_protocols.py:286`, `backend/tests/test_cp_protocols.py:312`, `backend/tests/test_cp_protocols.py:327`, `backend/tests/test_cp_protocols.py:360`, `backend/tests/test_cp_protocols.py:388` |
| Informational aerobic lines (no streak), UA three bands on test_aet, the gate wrapper | `backend/tests/test_workout_review.py:148`, `backend/tests/test_workout_review.py:212` |
| Drift tiers: 35′ → ref, 45′ → test, 25′ → refused, hot refused in both, other refusals on ref, gates (Friel / 徐國峰 / steady AeT test) ignore ref, i_drift shows ref but never BAD on it, card label + hover, AeT bands strict | `backend/tests/test_drift_tiers.py` |
| AeT test length / day: standard 80′ vs UA's 50′ under a weekday cap (detail says why, COROS steps), both lengths analysed strict, `warm_for`, weekday placement ≥ 2 days from the long run in projection / week_plan / PP.place, weekend only for the 80′ test, titled 50′ test marked done | `backend/tests/test_aet_weekday.py` |
| CP-test pattern alone → `cp_hint`, not test_cp; a titled test still takes the pattern's protocol | `backend/tests/test_cp_protocols.py:174`, `backend/tests/test_cp_protocols.py:187` |
| AeT test: analysis bands, refusals (short / hot / fast finish / hills), `latest_aet_test`, 「AeT」 title → test_aet, the card's apply action, COROS steps, apply on a temp plan | `backend/tests/test_quality_gate.py:400`, `backend/tests/test_quality_gate.py:410`, `backend/tests/test_quality_gate.py:431`, `backend/tests/test_quality_gate.py:451`, `backend/tests/test_quality_gate.py:480` |

## Domain Model

### Bounded Context
- **Context Name**: Workout Review（單次活動判讀）
- **Domain Layer**: Core Domain
- **Parent Module**: N/A (feeds `overview.spec.md` progression; rendered by the wko5-engine viewer)

### Ubiquitous Language
| Term | Definition |
|------|-----------|
| 判讀卡 (review card) | A chart of kind `review`: rows, tables or a curve plus ≤ 3 verdict lines for one section |
| Section | One of `summary`, `aerobic`, `intervals`, `climbs`, `durability`, `form` (dashboards) or `grades`, `pacing`, `durability_curve`, `cp_test` (extra cards) |
| Session type | easy (moderate flag) / long / hard_long / quality (stimulus z5 / z3) / test_cp / test_aet, or strength / bike / walk / other |
| Terrain | road / trail / hike, from the workout category |
| Phase | The plan phase on the activity date (base, specific, taper, …) |
| Pa:HR drift | (r1 − r2)/r1, r = speed/HR, halves of moving time after 10 min; positive = HR drifted up |
| Pw:HR drift | The same with r = power/HR, same fairness rules and halves |
| Basis | Which drift the aerobic card shows: pace (Pa:HR, default) or power (Pw:HR) |
| Fair drift | A drift `drift_of` did not refuse (flat, steady, not stopped, ≥ 40 min after the warm-up, ≤ 90 % CP, no fast finish) — the 嚴格 / test tier; any temperature band |
| Temperature band | `temp_band`: < 25 °C / 25–28 °C / > 28 °C / 溫度不明 (28 推估); drifts are compared only within one |
| Reference drift (參考) | Fair in every way but only 30–40 min after the warm-up (`tier` "ref"); shown, labelled, never a gate or threshold |
| Drift streak | Legacy only: consecutive most-recent fair drifts < 5 % (the removed, unsourced 3-run interval rule) |
| AeT drift test | Standard 15′ warm-up + 60′ fixed power + 5′, or (weekday cap < 80 min) UA's minimum 10′ + 40′; treadmill or flat; UA bands < 3.5 % / 3.5–5 % / > 5 % on the block after the warm-up |
| Effort | A work bout: 30-s power over the effort threshold for ≥ 60 s |
| Band | A %CP power band (閾值下 … 無氧) of the set's median effort |
| Fade | Last rep power vs first − 1; < −5 % = faded |
| Hard time | max(time HR ≥ LTHR outside recording gaps, time 30-s power ≥ 95 % CP) |
| Baseline | Median and IQR of the same category (and type) over the previous 8–12 weeks; needs ≥ 5 samples |
| Easy HR | Average moving HR ≤ AeT + 3 |
| CP-test protocol | quick (20′ all-out) / standard (12′ + 30′ + 3′) / race (5–10 K instead) |
| CP method | How a CP was measured: 2pt / 1pt_prior / tt20 / race; stored on the plan threshold as `cp_method` |
| Quality (CP) | 可信 / 參考 / 不採用 from the protocol's checks |

### Domain Events
None. The module computes on request; there are no emitters or subscribers.

## Change History

| Date | Type | Feature SRS | Summary |
|------|------|-------------|---------|
| 2026-09-30 | code-sync | N/A | Created from brownfield analysis — single-activity review cards (session type, Pa:HR drift, efforts, climbs, durability, form), 單次活動判讀 view, drift streak / CP-test hooks for status and week plan |
| 2026-09-30 | user-decision | N/A | Hikes go through the same quality rule as road and trail; average HR ≤ AeT+3 still means easy |
| 2026-10-01 | feature | user request (activity tags) | The user's activity type 測試 marks a test (`test_match` "user") |
| 2026-10-01 | feature | N/A | 間歇門檻 replaces the drift streak (aerobic lines informational, streak legacy only; `quality_gate` wraps `engine/quality_gate.py`); AeT drift test: 「AeT」 title → test_aet, `aet_test.analyze` (post-warm-up block, UA bands, fast-finish / heat / 40-min-after-warm-up checks kept out of `drift_of`), `latest_aet_test`, 「套用這次的 AeT」 card action through `drawAction` |
| 2026-09-30 | bugfix | N/A | Hikes get power quality and efforts like runs; hard HR time excludes recording gaps; HR-drop check skips short surges; last_quality covers hikes, sorted by date; measure cache `workout_review_v4`; docstring points at the done plan; refreshed anchors |
| 2026-09-30 | feat/drift-basis | N/A | Pw:HR from `drift_of` itself (same rules and halves); aerobic card follows the 配速／功率 toggle, 這次沒有功率 without power; streak and overview stay on Pa:HR; measure cache `workout_review_v6`; golden recomputation test on 3 real runs |
| 2026-10-01 | feature | N/A | CP-test protocols (quick default / standard / race): detection from the plan's done_by first, per-protocol analysis with quality checks (推估 labelled), single-bout W′ prior 參考, same-method comparison, 「套用這次的 CP」 button + `POST /api/v1/plan/thresholds/apply-cp`; measure cache `workout_review_v6` |
| 2026-10-01 | merge | N/A | feat/drift-basis + feat/cp-test-protocol merged; measure cache `workout_review_v7` |
| 2026-10-01 | fix/drift-aet-todos | N/A | `drift_of`: 40 min counted after the warm-up, fast finish and > 25 °C refused (archive air temperature first, else the watch; heat applied on read), Pa:HR / Pw:HR on one window; measure cache `workout_review_v8`. AeT test recognised from the plan's done AeT session (done_by) before title / plan row / steady run; `scheduled_test` no longer takes AeT sessions. Season drift charts (能力, periodization ②) plot the card's drift through `drift()`; 耐久度 charts stay on WKO5's stored values |
| 2026-10-01 | feat/drift-two-tier | N/A | Two drift tiers (user decision): strict `ok` unchanged for gates / AeT test / thresholds; `ref_ok` / `tier` "ref" at 30–40 min after the warm-up (`DRIFT_REF_MIN_S`, 推估) for display — card label + hover, i_drift (never BAD on ref), season charts' 參考 series via `drift(basis, "ref")`; hot runs refused in both; measure cache `workout_review_v9`. AeT test analysis: warm-up by protocol (`warm_for`), window up to 60′, end-of-recording trim fixed, 30-s slack. CP-test power pattern alone → `cp_hint`, not a test |
| 2026-10-01 | feat/drift-v2-planning | docs/research/drift-algorithm.md | drift v2 (user-approved): trailing idle cut, return-leg city tail as the cool-down (12 / 6 min, 推估, calibrated), stop rule on the window; VI ≤ 1.04 + walk rule + halves power ≤ 5 % on the window's moving samples (30-s CV kept as information); ramps stay in with a ramp-free comparison and per-half counts; SE per run (τ 60 s regression) shown as ±pp; 6-run inverse-variance aggregate (`drift_agg`) for the overview indicator and the season charts (`drift_avg()`); AeT aggregate with SE and shift (B3); measure cache `workout_review_v11` |
| 2026-10-01 | feat/workout-hr-power-charts | user request | Chart kind `activity`: stacked HR / power with brushed-range stats (replaces the dual-axis chart on 本次重點), time in HR / power zones with a remembered model picker (iLevels, Palladino, Stryd, Coggan, Friel, Classic, Seiler 3, %HRmax, RQ), WKO5 Heart Rate Variation and Trend |
| 2026-10-01 | fix/drift-steady-window | N/A | `drift_of` adaptive start (`steady_start`, 推估): 60 s after the last stop in the first 20 min, never below a tier the fixed 10 min reaches; recorded (`warmup_s`, `start_shift`) and shown (「前 m:ss 不算」, 「已排除」 row); ramps / strides not masked, 15 % CV unchanged (no source found — `docs/research/drift-steady-window-data.md` §6); measure cache `workout_review_v10` |
| 2026-10-01 | feat/interval-library | docs/research/interval-prescription.md | 間歇判讀 (engine/interval_eval.py): reps from pushed-step laps else the planned band, hit rate, which-rep outcome, TIZ vs the chosen variant's plan, fade / Sdec, W′ by WKO5 dFRC + Skiba (Vassallo τ), HR at matched power vs 3–5 same-class sessions; verdict 達到／部分達到／未達到 (TIZ 85 % / 60 %, 推估) also drives dose_step. Sections interval_verdict / interval_reps / interval_power / interval_battery / interval_tiz / interval_hr in views/workout.json 間歇, hidden on non-interval activities |
| 2026-10-02 | feat/session-classifier | user-approved | Session classifier (`engine/session_stimulus.py`, docs/research/vo2max-session-detection.md): Z5 間歇 = equivalent T@VO2max ≥ 4 min (power bouts ≥ 1.06 CP 2′ / 1.03 CP 5′ minus on-kinetics; wrist HR ≥ 0.93 HRpeak ÷ 1.6 where power isn't trusted), Z3 閾值 = Zone 3 ≥ 10′, 高強度長跑／長天 (`hard_long`, Z3 ≥ 10′ and ≥ 75′: hard day, not an interval), 中強度跑 (informational); hikes never auto-Z5; power evidence beats the AeT+3 rule; cadence lock must follow cadence; HRpeak = plan 最大心率 or the 3rd-highest 60-s peak in 365 d; type card icon + ? sources, 「VO2max 刺激」／「閾值刺激」 cards; `last_quality` no longer pre-filters on hard_s; measure cache `workout_review_v18` |
| 2026-10-02 | feat/heat-bands | user-approved | Heat bands: no > 25 °C refusal; `temp_band` < 25 / 25–28 (28 推估) / > 28 °C / 溫度不明 on every drift; archive by date as a fallback, watch minus the 3.7 °C wrist bias; within-band comparison in i_drift, rolling / `drift_avg(…, band)` (one season-chart line per band), the card's baseline; AeT aggregate cool band only; gates count heat runs (pass unlocks, fail 「可能是熱造成的」); card chip `res.chip`; no heat-adjusted drift (β is between runs); measure cache `workout_review_v17` |
| 2026-10-02 | feat/aet-heat-covariate | unsourced-rules.md §B6 | AeT aggregate takes the warm band with hr1 − β·(T − 25); β = Jenkins 2023 1.0 bpm/°C shrunk toward the runner's own fit (`drift_agg.heat_beta`, n/(n+20), 0–2); cool / no-temperature runs unmoved, hot out, drift unadjusted; the validity reason says the warm runs were heat-adjusted. Real data: 180-d points 4 → 14, still 「需要測試」 (slope ≤ 0) |
| 2026-10-02 | feat/aet-heat-covariate | owner-approved (temporary) | AeT lower bound when the regression finds no crossing (`AetAggregate.code` flat / slope / range_hi): `threshold_estimate.aet_lower_bound` — reference grade or better, SE ≤ 5 pp, each SE × 2 (GC validation), X = highest first-half HR ≤ LTHR − 3, ≥ 6 runs ≤ X and ≥ 3 within 5 bpm, the top 6's weighted mean + 2·SE < 5 %; any run ≤ X with drift − 2·SE ≥ 5 % drops it (all 推估). Valid, value X, se None: shift / moved never fire on it; zones still from the plan. Shown 「AeT ≥ X bpm（下限，推估）」 in the gate's why with the temporary-rule text; `zone_events` aet_bound: one AeT test every 8 weeks, priority low (box only, the testing indicator unchanged), id per cycle. Real data: the bound never held in 53 weeks (≤ 5 runs with SE ≤ 5 pp below LTHR − 3) — 53/53 still 「需要測試」 |
| 2026-10-02 | chore/drop-ilevels-encryption | owner decision | WKO5 iLevels removed from the time-in-zone charts / zone APIs (`activity_charts.ilevels_for` / `ILEVEL_*` gone, `period_zones.POWER_IDS` = Palladino 10 / 3); a remembered 「ilevels」 falls back to Palladino. The evaluator's `levelto` / `ilevels` (WKO5 expression language) stays |
