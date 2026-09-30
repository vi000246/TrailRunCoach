# Module Spec: workout-review

> **Last Updated**: 2026-09-30
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

The same measurements feed the progression decisions: the drift streak gates
base-phase intervals in `status.i_drift` and `overview.week_plan`, and the
latest CP test drives `status.i_testing` (see `overview.spec.md`).

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
 status.i_drift ◄── drift_streak      status.i_testing ◄── latest_cp_test
 overview.week_plan ◄── quality_gate · last_quality · next_quality
```

| Layer | Responsibility | Entry point |
|---|---|---|
| Pure analyses | Arrays in, numbers out; unit-tested on synthetic data | `backend/engine/workout_review.py:221` |
| Session typing | Plan order: category → test_cp → test_aet → quality → long → easy | `backend/engine/workout_review.py:477` |
| Dataset adapters | Samples, thresholds on the date, per-workout measure, classify, peers, baselines | `backend/engine/workout_review.py:611` |
| Progression hooks | Drift streak, last quality session, latest CP test | `backend/engine/workout_review.py:780` |
| Verdicts | Line builders for aerobic, interval and CP cards | `backend/engine/workout_review.py:866` |
| Review JSON | One card per section, in the shape the viewer's `draw()` renders | `backend/engine/workout_review.py:958` |
| View definition | Kind `review` with a `section` | `backend/engine/wko5expr/customviews.py:94-100` |
| API | Render branch and the review endpoint | `backend/api/wko5views.py:250`, `backend/api/wko5views.py:301` |

## Measurement (`measure`)

`_measure` (`backend/engine/workout_review.py:611`) reads the workout's channels
(`backend/engine/workout_review.py:588`) and returns one JSON dict: category,
moving / elapsed time, average HR and power, AeT / LTHR / CP in effect on the date
(`backend/engine/workout_review.py:546`), time over AeT+3, three-zone time
(< AeT, AeT–LTHR, ≥ LTHR), hard time, climb rate, drift, efforts (first 40),
interval summary, CP-test result, climbs, median HR per 100 m climbed, Stryd flag
and form drift.

- Moving = sample interval ≤ 30 s and, with a speed channel, above 1.6 km/h
  (WKO5's 1 mph) (`backend/engine/workout_review.py:64`, `backend/engine/workout_review.py:176`).
- Hard time = max(seconds with HR ≥ LTHR, seconds with 30-s power ≥ 95 % CP)
  (`backend/engine/workout_review.py:635-645`); `hard_power_s` exists only for runs
  with power and a CP.
- Memoised on disk through `Dataset.cached_series` under key `workout_review_v3`
  (`backend/engine/workout_review.py:58`, `backend/engine/workout_review.py:682`,
  `backend/engine/wko5expr/dataset.py:403`). The key holds the file and thresholds,
  not the code, so the version is bumped whenever `_measure` changes. Phase,
  classification, baselines and verdicts are recomputed on each call.

## Analyses

| Function | What | Rule / thresholds | Line |
|---|---|---|---|
| `drift_of` | Pa:HR decoupling, (r1 − r2)/r1 with r = speed/HR over the two halves of moving time after a 10-min warm-up | Refused (with a reason) when: no HR/speed; elapsed < 40 min; trail or ≥ `TRAIL_CLIMB_RATE_M_PER_KM` climbed per km; stopped > 5 % after warm-up; 30-s power CV > 15 %; mean power > 90 % CP; < 600 s usable | `backend/engine/workout_review.py:221` |
| `detect_efforts` | Work bouts in the 1-s power stream | 30-s power ≥ max(0.85 CP, 1.12 × session median) (1.15 × median with no CP), ≥ 60 s, gaps < 30 s bridged; HR drop 60 s after the HR peak | `backend/engine/workout_review.py:291` |
| `interval_summary` | Set band (median %CP), reps in band (±1 %), fade last vs first, median HR drop | Bands 閾值下 0.88–0.95, 閾值 0.95–1.01, 超閾值 1.01–1.06, VO2max 1.06–1.16, 無氧 ≥ 1.16 ×CP | `backend/engine/workout_review.py:342`, `backend/engine/workout_review.py:85` |
| `cp_test` | Best 3′ and 12′ windows; CP = (P12·720 − P3·180)/540, W′ = (P3 − CP)·180; whether the two windows are separate | — | `backend/engine/workout_review.py:359` |
| `looks_like_cp_test` | Two separate all-out efforts | 3′ ≥ 115 % and 12′ ≥ 98 % of the current CP | `backend/engine/workout_review.py:375` |
| `form_drift` | First ⅓ vs last ⅓ of moving time for ILR, LSS, kleg, GCT, cadence, VO, impact G | With a cadence channel only samples ≥ 65 strides/min (130 spm) count | `backend/engine/workout_review.py:386` |
| `downhill_share` | Steep downhill (grade < −10 %) share of time, distance and ILR·dt | — | `backend/engine/workout_review.py:411` |
| `pacing_deciles` | Moving pace, HR, power per 10 % of the distance | Needs ≥ 0.5 km | `backend/engine/workout_review.py:435` |
| `baseline` / `compare` | Median and IQR; high / low / within | No comparison under 5 samples | `backend/engine/workout_review.py:456`, `backend/engine/workout_review.py:465` |
| `baseline_for` | Same category (and session type) over the previous 8 weeks, widened to 12 when 8 has < 5 | — | `backend/engine/workout_review.py:760` |

Climbs come from `algorithms.climbs.detect_climbs`, the grade table from
`panels.workout.grade_bins`, and the durability curve from
`panels.workout.durability` (`backend/engine/workout_review.py:50`,
`backend/engine/workout_review.py:54`). kleg and impact G are evaluated through the
expression engine (`backend/engine/workout_review.py:577`, `backend/engine/workout_review.py:675-677`).

## Classification (`classify` / `session_type`)

`classify` (`backend/engine/workout_review.py:709`) returns `type`, `type_label`,
`terrain` (road / trail / hike, from `overview.category`), `phase` (from
`planning.phase_on` on the activity date), labels and date. A hike classified
easy is labelled 輕鬆健行 (`backend/engine/workout_review.py:732`).

`session_type` (`backend/engine/workout_review.py:477`), in order:

1. strength / bike / walk / other → that category.
2. `test_cp`: plan threshold record with a CP on that date
   (`backend/engine/workout_review.py:698`), a title matching `CP` or 測試, or
   `looks_like_cp_test`.
3. `test_aet`: plan AeT record on that date, or a fair drift on a road run with
   moving time ≥ 55 min.
4. `quality`: road, trail or hike whose average HR is **not** ≤ AeT+3, and either
   30-s power time ≥ 95 % CP reaches `HARD_SESSION_S` (600 s,
   `backend/engine/overview.py:65`), or hard time reaches it **and**, when a power
   stream exists, at least one detected effort.
5. `long`: moving ≥ 75 min, or ≥ 0.8 × a long-run target.
6. `easy`.

## Verdicts and cards

| Section | Card | Line |
|---|---|---|
| `summary` | Type · terrain · phase, time, HR vs AeT/LTHR, three zones; the type's verdict lines (trail / hike lines first); 建議分頁 | `backend/engine/workout_review.py:983` |
| `aerobic` | Drift, HR/speed per half, Pw:HR, time over AeT+3, same-type drift baseline | `backend/engine/workout_review.py:1015` |
| `intervals` | Per-rep table: start, duration, power, %CP, HR, max HR, 60-s drop | `backend/engine/workout_review.py:1038` |
| `climbs` | Per-climb table: gain, distance, grade, VAM, HR, HR per 100 m | `backend/engine/workout_review.py:1078` |
| `grades` | Grade bins: time, share, distance, pace, HR, power | `backend/engine/workout_review.py:1092` |
| `durability` | Moving / stopped, last-20 % durability, 補給 (no data source: 沒有補給紀錄) | `backend/engine/workout_review.py:1130` |
| `durability_curve` | Output/HR curve with a 90 % line (`points` series) | `backend/engine/workout_review.py:1147` |
| `pacing` | Pace / HR / power per 10 % distance | `backend/engine/workout_review.py:1162` |
| `form` | First ⅓ / last ⅓ / change / same-type baseline, steep-downhill share; ILR/LSS only with Stryd | `backend/engine/workout_review.py:1182` |
| `cp_test` | 3′, 12′, CP, W′ and the CP-delta verdict | `backend/engine/workout_review.py:1228` |

Verdict rules:

- Aerobic (`backend/engine/workout_review.py:866`): time over AeT+3 > 10 % on easy /
  long → 下次放慢; drift < 5 % → stable (with the streak; ≥ 3 → add a sub-threshold
  interval), but not counted when average HR > AeT+3; 5–10 % → hold intervals;
  > 10 % → aerobic base lacking. AeT test: drift < 5 % → first-half HR can be the AeT.
- Intervals (`backend/engine/workout_review.py:898`): reps in band; fade > 5 % → one
  rep fewer or more rest; median 60-s HR drop < 20 bpm → longer rest; band 閾值下/閾值
  with HR between AeT and LTHR → 屬於閾值下.
- CP (`backend/engine/workout_review.py:921`): delta vs current CP > 3 % → update.
- Trail / hike (`backend/engine/workout_review.py:1057`): HR per 100 m vs the 8-week
  median (any session type), ±5 %; last-20 % durability < 90 % → fuelling / pacing.
- Form, reference only (`backend/engine/workout_review.py:1182`): ILR change above own
  IQR and steep downhill > 30 % → mind the knees; LSS down > 2 % with GCT up > 2 % →
  fatigue.

`review()` adds `classification` and `suggested_dashboard` to every card:
dashboard 2 for `test_cp`, 3 for trail / hike terrain, otherwise `SUGGESTED`
(easy / long / test_aet → 1, quality → 2, others 0)
(`backend/engine/workout_review.py:96-98`, `backend/engine/workout_review.py:968`).
Unknown section or no samples → an `empty` card.

## Progression hooks

| Function | Used by | Rule | Line |
|---|---|---|---|
| `drift_series` / `drift_streak` | `status.i_drift` (`backend/engine/status.py:390`) | Road runs (not `runningtrail`), duration ≥ 40 min, avg HR ≤ AeT+3, last 56 days; streak = consecutive most recent fair drifts < 5 % (refused runs skipped); `streak_ok` at 3 | `backend/engine/workout_review.py:780`, `backend/engine/workout_review.py:805`, `backend/engine/workout_review.py:509` |
| `quality_gate` | `overview.week_plan` `allow_quality` (`backend/engine/overview.py:511`) | Intensity and drift not bad; in base phase (or no phase) `streak_ok` also required | `backend/engine/workout_review.py:520` |
| `last_quality` / `next_quality` | `overview.week_plan` base branch (`backend/engine/overview.py:540-543`) | Latest run classified quality in 28 days; next session 3×8 min, one rep fewer (min 2) when it faded | `backend/engine/workout_review.py:811`, `backend/engine/workout_review.py:529` |
| `latest_cp_test` | `status.i_testing` (`backend/engine/status.py:567-580`) | Latest run classified `test_cp` in 120 days; delta vs CP in effect | `backend/engine/workout_review.py:837` |

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
the viewer as panel kind `workout` (`backend/api/wko5views.py:147-151`), so it
needs a selected workout (`backend/api/wko5views.py:204`) and renders through
`review()` with the chart's title and description (`backend/api/wko5views.py:250-253`).

## API

| Method | Path | Line | Purpose |
|---|---|---|---|
| GET | `/api/v1/wko5/workouts/{i}/review` | `backend/api/wko5views.py:301` | `section` given: that card (400 if not a known section). Otherwise `{workout, classification, suggested_dashboard, sections}` with the six `SECTIONS` cards. `parity` selects the dataset mode; 404 for an unknown index |
| GET | `/api/v1/wko5/views/{view}/dashboards/{d}/charts/{c}` | `backend/api/wko5views.py:250` | A review chart renders through the same branch |

## Deviations from the design doc

What the implementation does differently from `docs/plans/done-workout-review.plan.md`:

| Topic | Design doc | Code |
|---|---|---|
| Quality: power | 達到 `HARD_SESSION_S` (`docs/plans/done-workout-review.plan.md:72`) | Power time uses **30-s** power ≥ 95 % CP, so second-by-second Stryd spikes on short rises don't make an easy run quality (`backend/engine/workout_review.py:637-645`) |
| Quality: HR-only | Same | With a power stream, time above LTHR alone isn't enough: it also needs ≥ 1 detected effort (`backend/engine/workout_review.py:484-487`, `backend/engine/workout_review.py:502`, `backend/engine/workout_review.py:729`) |
| Quality: easy HR | — | Average HR ≤ AeT+3 is never quality (`backend/engine/workout_review.py:497-499`, `backend/engine/workout_review.py:730`) |
| Quality: hikes | Terrain judged separately (`docs/plans/done-workout-review.plan.md:75`) | **User decision 2026-09-30**: hikes go through the same quality rule as road and trail — a sustained climb above threshold is a quality stimulus for 百岳 — with the same exception that average HR ≤ AeT+3 means easy (`backend/engine/workout_review.py:496-503`; tests `backend/tests/test_workout_review.py:34-35`). This replaces the earlier implementation where hikes were never quality |
| Drift floor | i_drift ≥ 40 min (`docs/plans/done-workout-review.plan.md:101`) | `drift_of` itself refuses runs under 40 min elapsed (`backend/engine/workout_review.py:60`, `backend/engine/workout_review.py:237-239`) |
| Form drift | First vs last ⅓ (`docs/plans/done-workout-review.plan.md:57`) | Only running steps (cadence ≥ 130 spm) count, so walking a steep climb doesn't read as a stiffness collapse (`backend/engine/workout_review.py:383-396`) |
| CP-test detection | 偵測到 3′ 和 12′ 兩組全力段 (`docs/plans/done-workout-review.plan.md:70`) | Separate windows with 3′ ≥ 115 % and 12′ ≥ 98 % of current CP (`backend/engine/workout_review.py:375-380`) |
| VAM by HR | 各心率下的 VAM (`docs/plans/done-workout-review.plan.md:47`) | Dropped: no per-sample VAM scatter in `views/workout.json`; VAM appears only per climb in the climbs card (`backend/engine/workout_review.py:1086`) |
| Not implemented | Optional `i_knee` (`docs/plans/done-workout-review.plan.md:111`); viewer auto-jump to `suggested_dashboard` (`docs/plans/done-workout-review.plan.md:122`) | Neither exists; `suggested_dashboard` is only returned and shown as the 建議分頁 row (`backend/engine/workout_review.py:1010-1011`) |

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
| Fair drift | A drift `drift_of` did not refuse (flat, steady, not stopped, ≥ 40 min, ≤ 90 % CP) |
| Drift streak | Consecutive most-recent fair drifts < 5 % on easy road runs; 3 unlocks base-phase intervals |
| Effort | A work bout: 30-s power over the effort threshold for ≥ 60 s |
| Band | A %CP power band (閾值下 … 無氧) of the set's median effort |
| Fade | Last rep power vs first − 1; < −5 % = faded |
| Hard time | max(time HR ≥ LTHR, time 30-s power ≥ 95 % CP) |
| Baseline | Median and IQR of the same category (and type) over the previous 8–12 weeks; needs ≥ 5 samples |
| Easy HR | Average moving HR ≤ AeT + 3 |

### Domain Events
None. The module computes on request; there are no emitters or subscribers.

## Change History

| Date | Type | Feature SRS | Summary |
|------|------|-------------|---------|
| 2026-09-30 | code-sync | N/A | Created from brownfield analysis — single-activity review cards (session type, Pa:HR drift, efforts, climbs, durability, form), 單次活動判讀 view, drift streak / CP-test hooks for status and week plan |
| 2026-09-30 | user-decision | N/A | Hikes go through the same quality rule as road and trail; average HR ≤ AeT+3 still means easy |
