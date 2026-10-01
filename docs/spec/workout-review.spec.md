# Module Spec: workout-review

> **Last Updated**: 2026-10-01
> **Status**: Active
> **Domain Layer**: Core Domain

## Overview

Single-activity review: the 判讀卡 (verdict cards) that lead each dashboard of
the 單次活動判讀 view. For one workout it decides what kind of session it was
(easy / long / quality / CP test / AeT test, or strength / bike / walk / other),
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
 season drift charts ── evaluator drift("pace" | "power") ── measure (heat_gate on read)
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
- Memoised on disk through `Dataset.cached_series` under key `workout_review_v9`
  (v7: Pw:HR halves and `cp_bouts`; v8: drift_of's 40 min after the warm-up, fast finish,
  one Pa/Pw window, `watch_temp_c`; v9: the two drift tiers, `ref_ok` / `pw_ref_ok` / `tier`) (`backend/engine/workout_review.py:71`,
  `backend/engine/workout_review.py:907`, `backend/engine/wko5expr/dataset.py:403`). The key
  holds the file and thresholds, not the code, so the version is bumped whenever `_measure`
  changes. Phase, classification, baselines and verdicts are recomputed on each call.
- The drift's heat rule is **not** cached: `measure()` re-applies `heat_gate` on every read
  with `activity_temp` (`backend/engine/workout_review.py:890`), because the route_weather
  archive is not part of the cache stamp and a routes build can fill it later. `_measure`
  stores the watch's mean temperature over the drift window as `watch_temp_c`
  (`backend/engine/workout_review.py:850`).

## Analyses

| Function | What | Rule / thresholds | Line |
|---|---|---|---|
| `drift_of` | Pa:HR decoupling, (r1 − r2)/r1 with r = speed/HR over the two halves of the moving time after a 10-min warm-up; Pw:HR the same with power (`pw_drift`, `p1`/`p2`, `pw_hr1`/`pw_hr2`), through the shared `_halves_drift` (`backend/engine/workout_review.py:243`). **One window for both bases**: moving samples after the warm-up with HR, speed and power valid; when power covers < 95 % of the Pa:HR window (自組), Pa:HR keeps the whole window and Pw:HR is refused (「功率只涵蓋 N%」). Returns `measured_s`, `finish`, `temp_c` / `temp_src`. **Two tiers** (one window, so one tier for both bases): 嚴格 / test = `ok` / `pw_ok`, ≥ 40 min after the warm-up; 參考 / reference = `ref_ok` / `pw_ref_ok`, every other check passed and 30–40 min after the warm-up (`DRIFT_REF_MIN_S` 1800, **自組**: no source gives 30 min; Coyle & González-Alonso 2001, Exerc Sport Sci Rev, DOI 10.1097/00003677-200104000-00009, show cardiovascular drift starting after ~10–20 min, and UA's 40 min is for a formal AeT test) — `drift` / `hr1` … filled, `ok` False, `reason` = the strict refusal. `tier` "test" / "ref" / None (`drift_tier`) | Strict refusal (with a reason) when: no HR/speed; **< 40 min of moving time after the warm-up** (`DRIFT_MIN_S`; UA's 40–60 min is the test after the warm-up, https://uphillathlete.com/aerobic-training/heart-rate-drift/; 30–40 min → the reference tier, < 30 min refused in both); trail or ≥ `TRAIL_CLIMB_RATE_M_PER_KM` climbed per km; stopped > 5 % after warm-up; 30-s power CV > 15 %; mean power > 90 % CP; **fast finish**: the last 10 % of the measured time > 5 % above the rest, power or pace (自組, `docs/research/aerobic-base-readiness.md:513`); **> 25 °C** (`heat_gate`; 徐國峰's < 25 °C, Lafrenz 2008 DOI 10.1249/MSS.0b013e3181666ed7; applying it to daily runs is 自組); < 600 s usable. A refusal applies to both bases **and both tiers** (only the length differs); a fair run without power gets `pw_reason` 這次沒有功率 | `backend/engine/workout_review.py:290`, `backend/engine/workout_review.py:76-82` |
| `fast_finish` | Time-weighted mean of the last 10 % of the mask's time vs the rest − 1 | > `DRIFT_FAST_FINISH` 0.05 refuses (自組) | `backend/engine/workout_review.py:264` |
| `heat_gate` | A fair drift (either tier) with `temp_c` > 25 °C becomes refused in both tiers (`tier` None); the reason names the source (路線天氣（Open-Meteo 檔案） / 手錶溫度). Idempotent | — | `backend/engine/workout_review.py:277` |
| `activity_temp` | (°C, source): the route_weather archive's air temperature for the file (`activity_weather.json`, moving-weighted, `backend/engine/route_weather.py:300`) when present — the air is what 徐國峰's rule means, a wrist sensor is warmed by the body (`docs/research/aerobic-base-readiness.md:515`) — else the watch's `watch_temp_c`; a dataset may carry `activity_temps` (tests) | Archive read cached on the file's mtime | `backend/engine/workout_review.py:890`, `backend/engine/workout_review.py:870` |
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
   `plan_store.test_sessions` returns `gen_key` for this (`backend/engine/plan_store.py:323`);
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
      `backend/engine/plan_store.py:320`); a dataset may carry its own list.
   2. a 5–10 K race or TT (protocol `race`): 15–90 min moving and a race / TT
      title or a plan race event that day of 4–11 km (`backend/engine/workout_review.py:794`).
   3. the power pattern — `looks_like_cp_test` (standard), else a 20′ window
      ≥ 1.03 × CP whose HR reached LTHR (quick, 自組,
      `backend/engine/workout_review.py:811`) — is **no longer a label on its
      own** (2026-10-01: it labelled ~35 hard 5 km runs, 2025-10…2026-07, as CP
      tests against the day's mFTP 175.6 W). Unmarked, it only sets
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
  or the AeT row; the untitled steady fallback stays at 55 min so the athlete's ordinary
  41–52′ road runs are not taken for tests (and never offer 「套用這次的 AeT」). `classify`
   returns `test_match` done_by / title / threshold / steady for a `test_aet`
   (`backend/engine/workout_review.py:1029`, `MATCH_LABEL` `backend/engine/workout_review.py:994`).
   **User mark** (2026-10-01): a run the user tagged activity type 測試 (`engine/activity_tags.py`,
   the 活動資訊 card) that no other rule made a test becomes `test_aet` when the title says AeT,
   else `test_cp`, with `test_match` "user" (accepted by the race-power back-test's
   `wko5_cp_tests` like a plan / title mark). Other tags do not demote an auto test.
4. `quality`: road, trail or hike whose average HR is **not** ≤ AeT+3, and either
   30-s power time ≥ 95 % CP reaches `HARD_SESSION_S` (600 s,
   `backend/engine/overview.py:65`), or hard time reaches it **and**, when a power
   stream exists, at least one detected effort. Hikes use both paths like runs.
5. `long`: moving ≥ 75 min, or ≥ 0.8 × a long-run target.
6. `easy`.

## Verdicts and cards

| Section | Card | Line |
|---|---|---|
| `summary` | Type · terrain · phase, time, HR vs AeT/LTHR, three zones; the type's verdict lines (trail / hike lines first); 建議分頁 | `backend/engine/workout_review.py:994` |
| `aerobic` | Drift on the chosen basis (Pa:HR with HR/speed per half, or Pw:HR with HR/power per half; 這次沒有功率 without power) — a reference-tier value gets 「（參考）」 and a 飄移等級 row 「參考（暖身後 30–40 分，未達 UA 測試標準）」 with the why as hover (`_row(…, tip)` → `data.tip`, the viewer's row `title`), a strict one 「嚴格（暖身後 ≥ 40 分，UA 測試標準）」 — time over AeT+3, same-type drift baseline on that basis (both tiers) | `backend/engine/workout_review.py:1109` |
| `intervals` | Per-rep table: start, duration, power, %CP, HR, max HR, 60-s drop | `backend/engine/workout_review.py:1049` |
| `climbs` | Per-climb table: gain, distance, grade, VAM, HR, HR per 100 m | `backend/engine/workout_review.py:1089` |
| `grades` | Grade bins: time, share, distance, pace, HR, power | `backend/engine/workout_review.py:1103` |
| `durability` | Moving / stopped, last-20 % durability, 補給 (no data source: 沒有補給紀錄) | `backend/engine/workout_review.py:1141` |
| `durability_curve` | Output/HR curve with a 90 % line (`points` series) | `backend/engine/workout_review.py:1158` |
| `pacing` | Pace / HR / power per 10 % distance | `backend/engine/workout_review.py:1173` |
| `form` | First ⅓ / last ⅓ / change / same-type baseline, steep-downhill share; ILR/LSS only with Stryd | `backend/engine/workout_review.py:1193` |
| `cp_test` | Protocol (and how it was matched), each bout (power, start, HR peak), CP with range and method, W′ (only when measured), quality, every check (✓ / ✗, 自組 labelled), the verdict lines; `action` = the 「套用這次的 CP」 button when not yet applied and not 不採用 | `backend/engine/workout_review.py:1427` |

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
  ≥ 55′, 自組) and the cool-down (trailing 60-s output < 85 % of the block's median, the mean
  taken over existing samples so a test stopped right at 40′ isn't trimmed), analyses up to
  60′ after the warm-up, needs ≥ 40 min after it (UA, like `drift_of`; 30 s slack for lost
  samples, 自組), refuses
  stops > 5 %, 30-s power CV > 15 %, hills, a fast finish (last 10 % > 5 % above the rest,
  自訂) and a mean temperature > 25 °C (自訂; the archive's air temperature when present,
  else the watch's over the block; the reason names the source). Pw:HR over the halves
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
a 45-minute floor, and was empty on every fair run checked (e.g. 2026-09-15,
2026-08-28). `steady_drift` still feeds the AeT estimate (`backend/engine/thresholds.py:34`).

**Card vs season chart.** WKO5's stored `pahr` / `pwhr` split the whole
recording at half its length with the warm-up and stops kept
(`docs/wko5-internals/workout-metrics.md:18`); the card leaves out the first 10
minutes and uses moving-time halves (1.2–9.4 percentage points lower on
2026-09-15, 08-28, 08-27 with the warm-up-excluded definition). The season drift
charts now plot **the card's number**: the evaluator function `drift("pace" |
"power")` (`backend/engine/wko5expr/evaluator.py:993`) reads `measure()`'s drift
through `basis_drift` — NaN (no point) for a refused run, a non-run, or power mode
without power; runs shorter than 50 min on the clock are skipped before measuring.
`drift(basis, tier)`: "test" (default, strict), "ref" (only the 參考 tier, runs ≥ 40 min
on the clock), "all". Both charts carry a second series per basis, 「參考 Pa:HR / Pw:HR
（暖身後 30–40 分，未達 UA 測試標準）」, markers only (`line_style` none, lighter colour),
and the description explains the tier (自組, Coyle & González-Alonso 2001).
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
loops: the card matches `plain_card` (v8) to 1e-9 on the one fair real run
(2025-06-30), the three older runs are refused by the strict tier (v9: the reference
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
(easy / long / test_aet → 1, quality → 2, others 0)
(`backend/engine/workout_review.py:99-101`, `backend/engine/workout_review.py:979`).
Unknown section or no samples → an `empty` card.

## Progression hooks

| Function | Used by | Rule | Line |
|---|---|---|---|
| `drift_series` | `status.i_drift` (`backend/engine/status.py:451`, `ref=True`), informational | Road runs (not `runningtrail`), duration ≥ 40 min, avg HR ≤ AeT+3, last 56 days; each point has `tier`; strict by default (`drift_streak`), `ref=True` keeps reference-tier drifts; runs drift_of refuses (< 30 min after the warm-up, fast finish, > 25 °C, …) are kept with drift None | `backend/engine/workout_review.py:1137` |
| `drift_streak` / `STREAK_NEED` | legacy only (the removed 「連續 3 次」 rule) | consecutive most recent fair drifts < 5 % | `backend/engine/workout_review.py:980`, `backend/engine/workout_review.py:70` |
| `quality_gate` | thin wrapper over `quality_gate.week_decision`; a legacy bool / None gate = no method | outside base: intensity and drift not bad | `backend/engine/workout_review.py:594` |
| `measure` / `classify` / `_samples` | `quality_gate.friel_check`, `xu_check`, `dose_history` (`backend/engine/quality_gate.py:182`, `backend/engine/quality_gate.py:228`, `backend/engine/quality_gate.py:281`) | Friel: avg HR AeT−5…AeT+3, ≥ 70 min, fair drift; 徐國峰: fair ≥ 90-min run, HR@10′ vs HR@90′ (its > 25 °C check is now drift_of's heat rule, `backend/engine/quality_gate.py:228`); dose: `quality` class or ≥ 4 short reps at ≥ 95 % CP | — |
| `latest_aet_test` | `status.i_testing` (`backend/engine/status.py:660`) | Latest run classified `test_aet` in 120 days: `analyze` result, `aethr_suggest` (band "at" only), `apply_body` with the test date, `applied` once a plan AeT row is dated on / after it | `backend/engine/aet_test.py:187`, `backend/engine/aet_test.py:215`, `backend/engine/aet_test.py:223` |
| `cp_eval` / `latest_cp_test` | `status.i_testing` (`backend/engine/status.py:580`) | Latest run classified `test_cp` in 120 days, by date; its protocol's result, `ref` / `delta` vs the previous result of the same method, `apply` payload | `backend/engine/workout_review.py:976`, `backend/engine/workout_review.py:996` |

## CP-test protocols (`engine/cp_protocols.py`)

Design: `docs/research/cp-test-protocols.md:316`. The athlete picks the protocol
in 課表偏好 (`plan.prefs.cp_test_protocol`, default `quick`, the athlete's
decision 2026-09-30); the session side is in `overview.spec.md`.

| Protocol | Result | Method | Checks (fail → 參考 unless noted) |
|---|---|---|---|
| `standard` 12′ + 30′ + 3′ | CP = (P12·720 − P3·180)/540, W′ = (P3 − CP)·180 | `2pt` | model: P3 > P12 and W′ within the prior ± 2 SD; 3′ HR peak ≥ 12′ HR peak − 8 bpm (自組); 12′ last minute ≤ 1.08 × its average (自組); ≥ 25 min between bouts |
| `standard`, model or 3′ HR fails | CP = P12 − W′prior/720, range ± 1 SD of the prior | `1pt_prior`, always 參考 | as above; 12′ HR peak < LTHR − 5 → 不採用 (自組) |
| `quick` 20′ all-out | CP = 0.95 × P20 (Ñancupil-Andrade 2024); cross-check P20 − W′prior/1200 within 3 % | `tt20` | pacing (自組); HR peak < LTHR − 5 → 不採用 (自組) |
| `race` 5–10 K | CP = P · (T/1800)^0.07 (Riegel k −0.07 at 30 min, 外插), 15–70 min only; the window with the highest converted CP | `race` | HR vs LTHR |

- W′ prior: Ruiz-Alias 2025, men 13.1 ± 4.0 kJ, women 6.4 ± 2.2 kJ (by `plan.profile.sex`)
  (`backend/engine/cp_protocols.py:43`). Thresholds labelled 自組 are ours
  (`backend/engine/cp_protocols.py:54-57`); failed checks say「（自組門檻）」.
- `reference` (`backend/engine/cp_protocols.py:349`): the latest plan threshold
  before the test day with the same `cp_method` (none = legacy 3′/12′ = `2pt`);
  else the latest CP row converted between the two-point and the 30-min
  definition (× 1.05, 外插); else the CP in effect. So rotating quick and
  standard doesn't keep flagging 要更新.
- `apply_payload` (`backend/engine/cp_protocols.py:377`): `{date = test day, cp, wprime
  (2pt only), cp_method, activity_index, note, label}`; None for 不採用. 參考 labels
  the button「套用這次的 CP {cp} W（參考）」 and applies the point estimate.
- The athlete's 2026-09-30 test (3′ 218 W < 12′ 222 W, 3′ HR peak ~147–149 vs 171,
  16 min apart) → `1pt_prior`, CP ≈ 204 W (198–209), 參考
  (`backend/tests/test_cp_protocols.py:264`).

## View (`views/workout.json`)

View 單次活動判讀, six dashboards in `SECTIONS` order, each led by a review card
(`views/workout.json:2-5`): 本次重點 (summary card, HR + power with AeT / LTHR /
95 % CP lines, three-zone time, cp_test card), 有氧／心率飄移 (aerobic card, HR vs
AeT, rolling EF, HR per km), 間歇 (intervals card, 30-s power with 88 / 95 / 101 % CP
lines), 爬坡與地形 (climbs and grades cards, grade vs HR and grade vs power
scatters), 配速與耐久 (durability, durability_curve, pacing cards), 跑姿與膝蓋負荷（參考）
(form card, ILR vs grade and impact G vs grade scatters) (`views/workout.json:80-130`).

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
| Drift floor | i_drift ≥ 40 min (`docs/plans/done-workout-review.plan.md:101`) | `drift_of` itself refuses runs with < 40 min of moving time **after** the 10-min warm-up (UA; `docs/research/aerobic-base-readiness.md:519`) (`backend/engine/workout_review.py:76`, `backend/engine/workout_review.py:290`). On the real data (269 runs ≥ 40 min) this refuses 37 of the 38 runs v7 accepted — the athlete's steady road runs are 41–52 min, 30–40 min of moving time after the warm-up; only 2025-06-30 (41.1 min) passes. `status.i_drift` changed on 25 of 106 weekly snapshots over two years (17 levels, all to NA); today it is NA both ways; Friel / 徐國峰 gates and session types unchanged |
| Drift tiers | User decision 2026-10-01 | 嚴格 / test (≥ 40 min after the warm-up) for gates and thresholds; 參考 / reference (30–40 min, `DRIFT_REF_MIN_S`, 自組 — Coyle & González-Alonso 2001) for display, labelled 「參考（暖身後 30–40 分，未達 UA 測試標準）」. On the real data (164 road runs ≥ 40 min on the clock, read-only, 2026-10-01): 1 test, 32 ref, 131 refused (72 power CV > 15 %, 28 > 90 % CP, 20 stops, 4 hills, 4 fast finish, 3 < 30 min after the warm-up); of the i_drift easy set 8 ref, 0 test. `i_drift` in the last 56 days stays NA: those easy runs fail the power-CV check, not the length. The indicator's BAD level needs ≥ 2 strict runs (it feeds the base-phase guardrail) |
| Drift heat / fast finish | Doc §6.2 suggests both for `drift_of` | Both implemented, numbers 自組 (25 °C from 徐國峰, 5 % / last 10 % from the doc). No current file has a watch temperature (48 older runs do) and `activity_weather.json` has not been built yet, so the heat rule refuses nothing today; the 4 runs over +5 % at the finish were already refused by the floor |
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
| Golden: the fair real run (2025-06-30) against `plain_card`, the 3 older runs refused, stored pahr / pwhr against the whole-run recomputation, both season charts equal the card on 3 real runs; chart definitions | `backend/tests/test_drift_basis.py:391`, `backend/tests/test_drift_basis.py:422`, `backend/tests/test_drift_basis.py:435`, `backend/tests/test_drift_basis.py:226` |
| AeT test from the plan: done_by on protocol aet / legacy gen_key / custom title (and not CP), wrong index / state / day, fallbacks title → plan row → ≥ 55′ steady, a short planned test found and refused, `protocol: "aet"` on the session | `backend/tests/test_quality_gate.py:414`, `backend/tests/test_quality_gate.py:429`, `backend/tests/test_quality_gate.py:441`, `backend/tests/test_quality_gate.py:457`, `backend/tests/test_quality_gate.py:466` |
| CP-test detection: done_by, wrong index / day, same day, pattern standard (no overlap) / quick, race | `backend/tests/test_cp_protocols.py:146`, `backend/tests/test_cp_protocols.py:156`, `backend/tests/test_cp_protocols.py:164`, `backend/tests/test_cp_protocols.py:174`, `backend/tests/test_cp_protocols.py:187`, `backend/tests/test_cp_protocols.py:195` |
| CP analysis per protocol, the real 2026-09-30 file, same-method comparison, card button, apply-cp API | `backend/tests/test_cp_protocols.py:215`, `backend/tests/test_cp_protocols.py:264`, `backend/tests/test_cp_protocols.py:293`, `backend/tests/test_cp_protocols.py:308`, `backend/tests/test_cp_protocols.py:341`, `backend/tests/test_cp_protocols.py:369` |
| Informational aerobic lines (no streak), UA three bands on test_aet, the gate wrapper | `backend/tests/test_workout_review.py:148`, `backend/tests/test_workout_review.py:212` |
| Drift tiers: 35′ → ref, 45′ → test, 25′ → refused, hot refused in both, other refusals on ref, gates (Friel / 徐國峰 / steady AeT test) ignore ref, i_drift shows ref but never BAD on it, card label + hover, AeT bands strict | `backend/tests/test_drift_tiers.py` |
| AeT test length / day: standard 80′ vs UA's 50′ under a weekday cap (detail says why, COROS steps), both lengths analysed strict, `warm_for`, weekday placement ≥ 2 days from the long run in projection / week_plan / PP.place, weekend only for the 80′ test, titled 50′ test marked done | `backend/tests/test_aet_weekday.py` |
| CP-test pattern alone → `cp_hint`, not test_cp; a titled test still takes the pattern's protocol | `backend/tests/test_cp_protocols.py:174`, `backend/tests/test_cp_protocols.py:187` |
| AeT test: analysis bands, refusals (short / hot / fast finish / hills), `latest_aet_test`, 「AeT」 title → test_aet, the card's apply action, COROS steps, apply on a temp plan | `backend/tests/test_quality_gate.py:350`, `backend/tests/test_quality_gate.py:360`, `backend/tests/test_quality_gate.py:381`, `backend/tests/test_quality_gate.py:401`, `backend/tests/test_quality_gate.py:430` |

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
| Session type | easy / long / quality / test_cp / test_aet, or strength / bike / walk / other |
| Terrain | road / trail / hike, from the workout category |
| Phase | The plan phase on the activity date (base, specific, taper, …) |
| Pa:HR drift | (r1 − r2)/r1, r = speed/HR, halves of moving time after 10 min; positive = HR drifted up |
| Pw:HR drift | The same with r = power/HR, same fairness rules and halves |
| Basis | Which drift the aerobic card shows: pace (Pa:HR, default) or power (Pw:HR) |
| Fair drift | A drift `drift_of` did not refuse (flat, steady, not stopped, ≥ 40 min after the warm-up, ≤ 90 % CP, no fast finish, ≤ 25 °C when a temperature is known) — the 嚴格 / test tier |
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
| 2026-10-01 | feature | N/A | CP-test protocols (quick default / standard / race): detection from the plan's done_by first, per-protocol analysis with quality checks (自組 labelled), single-bout W′ prior 參考, same-method comparison, 「套用這次的 CP」 button + `POST /api/v1/plan/thresholds/apply-cp`; measure cache `workout_review_v6` |
| 2026-10-01 | merge | N/A | feat/drift-basis + feat/cp-test-protocol merged; measure cache `workout_review_v7` |
| 2026-10-01 | fix/drift-aet-todos | N/A | `drift_of`: 40 min counted after the warm-up, fast finish and > 25 °C refused (archive air temperature first, else the watch; heat applied on read), Pa:HR / Pw:HR on one window; measure cache `workout_review_v8`. AeT test recognised from the plan's done AeT session (done_by) before title / plan row / steady run; `scheduled_test` no longer takes AeT sessions. Season drift charts (能力, periodization ②) plot the card's drift through `drift()`; 耐久度 charts stay on WKO5's stored values |
| 2026-10-01 | feat/drift-two-tier | N/A | Two drift tiers (user decision): strict `ok` unchanged for gates / AeT test / thresholds; `ref_ok` / `tier` "ref" at 30–40 min after the warm-up (`DRIFT_REF_MIN_S`, 自組) for display — card label + hover, i_drift (never BAD on ref), season charts' 參考 series via `drift(basis, "ref")`; hot runs refused in both; measure cache `workout_review_v9`. AeT test analysis: warm-up by protocol (`warm_for`), window up to 60′, end-of-recording trim fixed, 30-s slack. CP-test power pattern alone → `cp_hint`, not a test |
