# Module Spec: overview

> **Last Updated**: 2026-10-04
> **Status**: Active
> **Domain Layer**: Core Domain

## Overview

The home page (總覽). It answers four questions without splitting by sport: *how am I
doing* (the training-status indicators), *what's missing* (their prioritised
actions), *what did I do* (totals by week / month / year) and *what should I do
this week* (a day-by-day plan with a load projection). Trail, 百岳 and bike days are
usually too few to read on their own, so every total, the PMC and the
plan use all sports together; categories exist only to colour stacked bars.

The plan is **stored and editable**: the generator writes sessions into a table once, the
athlete edits, adds, moves or deletes them, and a reconcile step refreshes the rest from what
actually happened. The stored plan (not the generator's output) drives the week's progress
bars and the PMC projection, and can be pushed to COROS Training Hub by day, week or phase.
The generator follows the athlete's **課表偏好** (training-plan preferences: allowed days, per-
session time caps, counts, terrain) and one-off **不排課日期** (blackout days: a holiday or trip on
which nothing is planned), and the session dialog can convert a session to another terrain at
the **same load** from the athlete's own speed history.

Volume is **moving time**, never recorded time — a multi-day 百岳 file records
the nights too (a two-day trip can hold only a few hours of walking).

## Architecture

```
 Dataset (wko5-engine) ──┬─ Status engine (status.py) ── indicators, actions, phase, goals
                         ├─ overview.summary()  ── period buckets + current-period detail
                         ├─ overview.pmc()      ── CTL / ATL / TSB per day
                         ├─ overview.week_plan() ─ target, sessions by day, projection
                         │        ▲  plan_prefs.shape() / place()  ◄── user_settings plan.prefs.*
                         │        ▲  blackouts (blocked days, hours, step) ◄── user_settings plan.blackouts
                         └─ equivalence.summary() ── easy-HR speed model + LOO backtest
                                      │
                         projection.project_weeks() ── later weeks up to the horizon (same prefs)
                                      │
                         reconcile.reconcile() ⇄ plan_store (table plan_sessions)
                                      │                     │
                                      │          coros_workouts ⇄ table coros_plan_push ⇄ COROS
                                      │
                /api/v1/overview/* + /api/v1/overview/plan/* ── overview.html, schedule.html (課表)
```

| Layer | Responsibility | Entry point |
|---|---|---|
| Categories / helpers | Workout → category, moving time, effort km | `backend/engine/overview.py:68` |
| Periods | Week (Monday) / month / year buckets and totals | `backend/engine/overview.py:207` |
| PMC | Same `tl()` recurrence as the chart expressions `ctl` / `atl` / `tsb` | `backend/engine/overview.py:269` |
| Week plan | Volume target, session template, done-matching, day placement, projection | `backend/engine/overview.py:408` |
| Plan preferences | 課表偏好: shape the template (counts, caps, terrain), place on allowed days | `backend/engine/plan_prefs.py:314`, `backend/engine/plan_prefs.py:420` |
| Blackout days | 不排課日期: validation, blocked days, lost-day volume, the step after, move-to for stored sessions | `backend/engine/blackouts.py:65`, `backend/engine/blackouts.py:206` |
| Same-load conversion | Easy-HR time model per terrain, design km / climb for a time, LOO backtest | `backend/engine/equivalence.py:236`, `backend/engine/equivalence.py:301` |
| Multi-week projection | Rolls the week-plan rules forward to the horizon | `backend/engine/projection.py:227` |
| Reconcile | Pure rules: stored plan vs regenerated weeks vs activities | `backend/engine/reconcile.py:85` |
| Plan store | Table I/O, edits, tombstones, stored-plan summary | `backend/engine/plan_store.py:101` |
| COROS push | Session → structured COROS workout, idempotent push / remove | `backend/sync/coros_workouts.py:589` |
| API | Memoised Status, the endpoints, the pages | `backend/api/overview.py:40`, `backend/api/plan_sessions.py:33` |

The dataset is the chart pages' shared instance (`backend/api/wko5views.py` `_dataset()`), so the
engine config / parity mode is the same everywhere.

## Categories

`category()` (`backend/engine/overview.py:68`): 路跑 road (run, not trail; incl. treadmill),
越野跑 trail (tag `runningtrail` or type *trail running*), 登山健行 hike (tags `hiking` /
`mountaineering`), 騎車 bike, 肌力 strength, 走路 walk, 其他 other. Colours are the validated
categorical palette in fixed slot order (`backend/engine/overview.py:35`).

- **Endurance** = road, trail, hike, bike (intensity split, longest session, quality-session
  matching). **Foot** = road, trail, hike (effort km).
- **Effort km (EP)** = km + gain/100, the 健行筆記 / ITRA convention (`backend/engine/overview.py:104`).

## Periods (`summary`)

- `summary(ds, unit, anchor, n)` returns `n` consecutive buckets ending with the one containing
  `anchor`; the last one gets detail (`backend/engine/overview.py:207`).
- Bucket totals: sessions, moving s, km, climbing, descending, TSS, EP, days trained, and the
  same per category (`backend/engine/overview.py:167`).
- Detail: `elapsed_share` while the period is still running; intensity split; longest session,
  biggest climb, hardest (TSS) session; deltas vs the previous bucket and vs the mean of the
  earlier buckets ("平常"); activity list.
- **Intensity split** (`backend/engine/overview.py:190`): moving samples only
  (`speed > 1.6 km/h`, WKO5's moving threshold, or no speed channel), endurance sessions only;
  low < AeT, mid AeT–LTHR, high ≥ LTHR (`backend/engine/overview.py:56`). The per-workout
  aggregates are disk-cached by the evaluator.

## PMC

`pmc()` (`backend/engine/overview.py:269`) evaluates `ctl`, `atl`, `tsb` with the evaluator
(full history, constants from the athlete: 42 / 7) and daily TSS with the `tl()` input rule
0 ≤ x ≤ 5000 (`backend/engine/overview.py:258`). TSB is yesterday's CTL − ATL.
`project()` (`backend/engine/overview.py:285`) continues the recurrence with planned daily TSS.

The dashed projection on the page is `plan_store.plan_summary()`
(`backend/engine/plan_store.py:284`): today's CTL / ATL continued with the TSS of the **stored**
active sessions on each day after today, up to the horizon, so edits change the curve. It
replaces `week_plan()`'s own projection once the stored plan has loaded
(`backend/static/overview.html:306`).

## Week plan (`week_plan`)

Inputs: the computed `Status` (phase kind, goals, indicators), the last 8 complete weeks of
moving hours / TSS, today's CTL / ATL / TSB, and the 課表偏好 `prefs`
(`backend/engine/overview.py:408`). `prefs=None` or the defaults run exactly the rules below.

**Volume target**
1. Base / specific: the weekly TSS that raises CTL by the phase goal (base +3, specific +4 per
   week; `RAMP_GOAL`, `backend/engine/overview.py:309`) — `7·(CTL₀ + Δ/(1 − (1 − 1/42)⁷))` —
   converted to hours with the athlete's TSS per hour over 6 weeks.
2. Capped at `max(1.10 × ref, ref + 0.5 h)`, ref = max(4-week mean, last week) (UA 10 %).
   Floored at the 4-week mean (hold).
3. Guards: TSB < −30 → recovery week (60 % of the 4-week mean); TSB < −20 → hold; three
   building weeks in a row → recovery week (65 % of their mean, 3:1 cycle).
4. Taper: 50 % of the 6-week mean (40 % in the last 7 days to the A event); event week 30 %;
   recovery 50 %; transition 65 %.
5. A custom weekly-hours preference only lowers the result (`backend/engine/overview.py:488`).
6. 不排課日期 (`blackouts`, `backend/engine/overview.py:493`): after a week that lost days the
   target is capped at +10 % (≥ +0.5 h) of what was **actually done** that week; this week's
   lost days then scale it (see 不排課日期 below).

**Sessions** (dataclass `Session`, `backend/engine/overview.py:314`; `terrain`, `distance_km`,
`climb_m` added for the preferences / conversion)
- Base / specific (not a recovery week): one long easy session (30 % of the week, ≥ 60 min,
  ≤ 1.15 × the longest of the last 28 days; specific: toward 70 % of the goal event's hours,
  ≥ 90 min), terrain from the goal's climb density; then one of, in this order
  (`backend/engine/overview.py:568`):
  1. **CP test** when the `testing` indicator is bad / watch and the A event is > 10
     days away (independent of the quality gate), built by `cp_protocols.session_for` from the
     課表偏好 CP 測試方式 — read from `prefs` even when the other preferences are the defaults
     (`backend/engine/overview.py:555`, `backend/engine/cp_protocols.py:93`):
     `quick` 「CP 測試 20 分全力」 37 min, TSS 45 (warm-up 12 → 20′ all-out → cool-down 5);
     `standard` 「CP 測試 12 分 + 3 分」 70 min, TSS 65 (15 → 12′ → rest 30 → 3′ → 10, long
     bout first); `race` → **no session**: the quality branches below run instead and the
     testing action 「用 5–10 K 比賽或計時跑代替 CP 測試」 goes to 還缺什麼 and the week notes.
     The session carries `protocol` (Session field, `plan_sessions.protocol` column + migration,
     `reconcile.FIELDS`, `plan_store.to_dict` / `push_dict`);
  2. base → the **AeT 飄移測試** when `aet_test.due` says so and no CP test was placed
     (`backend/engine/overview.py:580`, `backend/engine/overview.py:602`; see AeT drift test below);
  3. specific → uphill intervals 5×4';
  4. base → the **間歇門檻**'s dose step (`_gate_session`, `backend/engine/overview.py:409`,
     `backend/engine/overview.py:612`; see below).
  - Base **recovery week** (3:1): the gate's 「恢復週 fartlek 4×1 分」 instead of intervals
    (Palladino, `backend/engine/overview.py:613`).
- **間歇門檻 — quality gate** (`backend/engine/quality_gate.py`; design
  `docs/research/aerobic-base-readiness.md` §4–§5). The old 「連續 3 次輕鬆路跑飄移 < 5%」
  rule had no source and is gone (`STREAK_NEED` is legacy only,
  `backend/engine/workout_review.py:70`). `week_plan` reads status `i_gate`'s dict
  (`backend/engine/overview.py:563`) and asks `week_decision`
  (`backend/engine/quality_gate.py:665`) for this week:
  - **Method** (`plan.prefs.quality_gate`, `evaluate`, `backend/engine/quality_gate.py:490`):
    `auto` → `ua_gap` + `friel_drift` when the plan has a measured AeT row that is **valid**
    (B3, `unsourced-rules.md`: the aggregated drift estimate's SE ≤ 3 bpm and no shift > 5 bpm
    over the last 6 points — `drift_agg.aet_validity`, 推估; no fixed 16-week expiry; stale after
    a break ≥ 4 weeks) (`aet_info`, `backend/engine/quality_gate.py:141`) and LTHR is not WKO5's default
    (`lthr_info`, `backend/engine/quality_gate.py:153`), else `none`. `ua_gap`: LTHR / AeT − 1
    ≤ 10 %; `friel_drift`: one run in 8 weeks, avg HR AeT−5…AeT+3, ≥ 70 min, fair drift < 5 %
    (`friel_check`, `backend/engine/quality_gate.py:186`); `xu_drift`: flat ≥ 90-min run,
    (HR@90′ − HR@10′) / HR@10′ < 10 % (`xu_drift_of` / `xu_check`,
    `backend/engine/quality_gate.py:213`, `backend/engine/quality_gate.py:232`); `plateau`: ≥ 8
    base weeks and EF change < +2 %; `weeks`: > N base weeks (evaluated per projected Monday);
    `none`: guardrails only. States: unlocked / locked (data there, criterion not met) /
    missing. **Forced mode with missing data → `fallback`**: i_gate WATCH with the reason and
    the guardrail plan (our own choice: never a permanent lock).
  - **Guardrails** (`guard`, `backend/engine/quality_gate.py:350`), base phase, every mode:
    low-intensity time share < 75 % (or run power < 80 % CP share < 75 %) → none; CTL ramp ≥ 5
    → threshold only, ≥ 8 → none (Friel, coach); last week's step > 20 % → none (Nielsen 2014,
    Damsted 2019), 10–20 % → hold the dose (推估); TSB −30…−20 → hold (Friel / TrainingPeaks).
    Projected weeks keep only the intensity block.
  - **Two gates** (台灣教練): Zone 3 whenever the guardrails pass — a locked
    method no longer stops it; Zone 5 only while the base is confirmed (`gate["z5"]`,
    `base_check.z5_status`: one of three tests done and passed — the 90-min test, a measured AeT
    passing the UA gap, or the Friel drift near a measured AeT;
    maintenance and re-entry rules in plan-auto.spec.md).
  - **Dose** (`Z3` / `Z5` / `LADDER`, `dose_spec(step, z5_open)`; `dose_step`): step = 達標
    sessions in the last 8 weeks (`dose_history`, counting ≥ 4 short reps at ≥ 95 % CP with
    `count_reps`): 閾值 3×8′ → 4×8′ → 3×10′, then (Zone 5 open) 5×2′ → 4×3′ → 5×3′ → 4×4′,
    then 4×4′ / 3×10′ alternating; Zone 5 closed → the top Zone 3 rungs and the step waits.
    The step moves by the progression state machine (`interval_outcome` / `dose_step`,
    plan-auto.spec.md): 達標 forward, 邊界 / 無法判定 repeat, 未適應 rest +1 min then back one,
    first rep short = target −5 %.
  - **停訓後恢復期** (`reentry.find`, mode `reentry`): a break ≥ 6 days without running — a
    blackout range or from the runs — gives Daniels' block (week hours = the 4 weeks before the
    break × the block's %; no quality, no strides, no test inside; long-run cap; targets ×
    FVDOT). It replaces `blackouts.step_cap` (plan-auto.spec.md).
  - **AeT test** (`aet_test.due(today, kind, base_start, gate["aet_test_reason"], last)`): only
    for a reason (B3 / the Z5 lifecycle), its protocol from `plan.prefs.aet_test_protocol`
    (auto = 徐國峰 90′ on the weekend in place of the long run, UA 40′ backup). Session text keeps the COROS / trim tokens
    (`session`, `backend/engine/quality_gate.py:715`); the detail prefix names the rule
    (`prefix`, `backend/engine/quality_gate.py:747`). In guardrail mode `plan_prefs.shape`
    gets `quality_cap=1` (`backend/engine/overview.py:641`).
  - Outside base: intensity and drift not bad (unchanged).
  - Returned as `quality_gate` (the gate dict + `levels`, `allowed`, `this_week`,
    `aet_test`) for the projection (`backend/engine/overview.py:791`).
- Taper: one short intensity 4×3'. Event week: the race.
- Strength ×2 in base / transition / recovery or when the `strength` indicator is bad / watch,
  else ×1 (not counted in the hours).
- Easy runs fill the remaining minutes in 40–60 min sessions; in base the first one carries
  8×10 s hill strides.
- Targets per session come from `zones.training_targets` (CP / LTHR / AeT, estimate-aware),
  formatted by `_targets` (`backend/engine/overview.py:374`).
- A season-plan threshold row applies from its own date on, never to earlier days
  (`planning.Plan.threshold_on`, `backend/engine/planning.py:205`, fixed 2026-10-01). Before the
  first row, `Dataset.setting` / `cp` / `aethr` fall back to WKO5's dated settings (runthr, the
  current mFTP snapshot, 0.89 × LTHR). Today's thresholds come from the last run moved to today
  (`zones._on_day`, `backend/engine/zones.py:113`; also `status._aet_now` and the LTHR estimate's
  CP), so a test dated after WKO5's last run still applies. Today's CP 220 / LTHR 160 / AeT 142,
  the targets and the zone bounds are unchanged. Indicators that judge each past run with its
  own-date AeT do move (2026-10-01, WKO5 source): 強度分配 mid share 26 % → 35 % (still bad);
  效率 good 「進步」 → info 「持平」; drift-eligible easy runs 1 → 6 (still too few).
  Past days change back to their values before the first threshold row: hrTSS of past runs
  uses WKO5's dated LTHR instead of the row's (365-day TSS −0.7 %, CTL today −0.6 % in the
  evaluator). `workout_review` again labels about three dozen past hard 5 km runs `test_cp` by
  power pattern against the WKO5 mFTP snapshot (≈ 80 % of CP). None of them falls in the
  28 / 42-day windows the overview reads.
- With active preferences the template is then shaped by `plan_prefs.shape()`
  (`backend/engine/overview.py:619`; see 課表偏好 below).

**Done-matching**: strength ← a strength workout; long (by id, so a 登山 long day of kind
`hike` too, `backend/engine/overview.py:641`) ← an endurance session ≥ 80 % of the planned
minutes; the AeT test ← a road run ≥ 55 min (`backend/engine/overview.py:666`); quality / test
← a session with ≥ 10 min at ≥ LTHR or ≥ 0.95 CP run power, or 60 % of the planned work for
short reps (`hard_need`, `backend/engine/quality_gate.py:769`, `backend/engine/overview.py:669`);
a planned **Zone 5** session (library class Z5, or rung `z5*`; `quality_gate.is_z5_variant`)
← only a run classified 「Z5 間歇」 (`workout_review.classify` stimulus `z5`, owner 2026-10-02);
easy ← any other endurance session. Week activities and `done_by` rows carry `session`
(`overview.session_of`: type, label, stimulus, dashicon), shown on the 本週 tiles / 課表 chips.
**Done hard days** (Z5 / Z3 / 高強度長跑 / CP test, planned or not; `workout_review.HARD_TYPES`)
keep the remaining interval 48 h away (`plan_prefs.place(hard_done=…)` and the no-prefs path).

**Placement**: remaining days from today (tomorrow when something is already logged today)
to Sunday. The long session goes on the athlete's usual long-day weekday (mode over 12 weeks,
`backend/engine/overview.py:349`) or the last free day; quality ≥ 2 days from the long one;
easy on the next free days; strength on easy or free days, never the day before the long one.
Sessions that don't fit are reported as a note, not squeezed in. Active preferences place
with `plan_prefs.place()` instead (`backend/engine/overview.py:669`). Blocked days are removed
from the candidate days first (`backend/engine/overview.py:656`); when they leave a quality /
test session only a day next to the long one, it is dropped rather than stacked
(`backend/engine/overview.py:691`).

**Output**: target / done / remaining (hours, TSS), the reasons (`why`), the rules cited,
8-week history, load now and at Sunday (CTL, ATL, next-Monday TSB, weekly ramp), the daily
projection, sessions with day / done state, thresholds and their sources, notes (data /
testing to-dos from the indicators; preference notes tagged `src: prefs`), the per-category
TSS / h (`tss_per_category`), the preferences applied and the week's lost days
(`blackout_days`) (`backend/engine/overview.py:743`). Blackout notes are tagged `src: blackout`.

## 課表偏好 — training-plan preferences (`plan_prefs.py`)

Stored as `user_settings` keys `plan.prefs.*` with per-key validation
(`backend/settings/repository.py:57`, `backend/settings/repository.py:176`) and cross-field
rules in `check()` (`backend/engine/plan_prefs.py:144`: runs ≤ allowed days, quality < runs,
long cap ≥ weekday cap). Read synchronously by `load()` (`backend/engine/plan_prefs.py:155`).
`Prefs()` (`backend/engine/plan_prefs.py:75`) is **inactive** (`active`,
`backend/engine/plan_prefs.py:92`) and every caller keeps its original code path, so the
defaults reproduce today's plan exactly.

| Setting | Key | Values (default) |
|---|---|---|
| 可練日 | `plan.prefs.days` | 7 bools Mon..Sun, unchecked = rest day (`null` = every day) |
| 長跑日 | `plan.prefs.long_day` | `sat` / `sun` / `auto` = the athlete's most frequent long day over 12 weeks (`auto`) |
| 單次時間上限（平日） | `plan.prefs.cap_weekday` | 20–300 min (`null` = none) |
| 長跑日上限 | `plan.prefs.cap_long` | 20–600 min (`null` = 同平日) |
| 上限模式 | `plan.prefs.cap_mode` | `soft` 盡量不超過 / `hard` 絕對不超過 (`soft`) |
| 每週跑步次數 | `plan.prefs.runs_per_week` | 3–7 (`null` = auto) |
| 每週品質課 | `plan.prefs.quality_per_week` | 0–2 (`null` = auto, ≤ 1) |
| 每週肌力 | `plan.prefs.strength_per_week`, `plan.prefs.strength_days` | 0–3 (`null` = auto); weekdays 0–6, `[]` = with easy runs |
| 每週時數 | `plan.prefs.weekly_hours` | 1–40 h cap (`null` = CTL ramp rules) |
| 地形偏好 | `plan.prefs.terrain_easy` / `_long` / `_quality` | easy `road`/`trail`/`any`; long `road`/`trail`/`hike`/`auto`; quality `flat`/`hill`/`any` |
| 間歇目標 | `plan.prefs.interval_target` | `power` / `hr` (`power`) |
| 間歇門檻 | `plan.prefs.quality_gate`, `plan.prefs.quality_gate_weeks` | `auto` / `ua_gap` / `friel_drift` / `xu_drift` / `plateau` / `weeks` / `none` (`auto`); weeks 2–16 (8). **Not part of `active`** (`GATE_FIELDS`, `backend/engine/plan_prefs.py:72`): read by status `i_gate`. Panel: a chip per mode, each with a `?` whose fixed-position popup (ported from the viewer's `.qtip`, appended inside the open dialog so the modal top layer and its scroll box never hide it) gives the source, the exact criterion, what to do and whether it runs on your data now (`GET /prefs` `gate_options` + `GET /prefs/gate`; `backend/static/schedule.html:496`, `backend/static/schedule.html:1281`, `backend/static/schedule.html:1299`) |
| CP 測試方式 | `plan.prefs.cp_test_protocol` | `quick` 約 37 分 / `standard` 約 70 分 / `race` 不另外排 (`quick`). **Not part of `active`**: it only changes the test session (`backend/engine/plan_prefs.py:103`). Panel: three radio options with a time / accuracy line (`backend/static/schedule.html:492`) |
| AeT 飄移測試 | `plan.prefs.aet_test_days` | `weekday` / `any` (`weekday`: weekends are often trail days). **Not part of `active`** (`NOT_SHAPING`): every placement path reads it (`aet_test.test_days` / `pick_day`): weekday = Mon–Fri in Tue-first order, ≥ 2 days from the long run and other hard days where possible, never the day after the long run unless nothing else; the 80′ standard test may fall back to a weekend day that isn't the long run's, the 50′ short one never; `any` = the interval rule. The test's **length** follows `cap_weekday` (`aet_test.variant_for`): no cap or ≥ 80 → 15′ + 60′ + 5′; < 80 → UA's minimum 10′ + 40′ (never shorter, exempt below 50). Panel: `#pf-aet` radios |
| 熱適應 | `plan.prefs.heat`, `plan.prefs.heat_method` | `auto` / `off` (`auto`); `run` / `overdress` / `bath` / `sauna` / `mixed` (`run`). **Not part of `active`** (`NOT_SHAPING`): they only add heat sessions before a hot A/B race (`engine/heat_plan.py`). Panel: radio + select with the current S and the rules (`#pf-heat`) |

**熱適應課** (`engine/heat_plan.py`, heat-acclimation.md §5.4, 推估 from §3.4; applied after
placement in `week_plan` and per projected week in `project_weeks(events, heat_acts)`, which
carries the planned heat days into the next week's S): only when `heat` ≠ off, an A/B event within
30 days is hot (`Event.heat` hot, or auto → `heat_data.event_is_hot`: 百岳 cool, else the median
Hadley of the athlete's activities ±15 days in earlier years > 150), and the projected race-day S
(centre) < 0.75; never within 2 days of the race or in a recovery week. Induction race − 21 → − 8:
every placed easy run and the long run (a hot long run is the exposure; cap rule 1); maintenance
race − 7 → − 3 every 4 days. An easy heat run becomes 「熱適應輕鬆跑」 ≥ 60 min, HR ≤ AeT, TSS scaled;
over the weekday cap it is exempt with `NOTE_HEAT` (like the CP test); with `cap_mode` hard (cap <
60) or method bath / sauna it stays ≤ 40 min and a `kind="heat_passive"` session (TSS 0, same day)
is added. Fewer than 5 induction days in the week → `NOTE_SHORT`. `week_plan.heat` = {active,
event, s_now, s_race_before / after / band, days, sessions}. heat_passive: never a main day
(`blackouts.move_to`, `reconcile.SIDE_KINDS`), never matched to an activity (the user ticks it;
a ticked one is a full dose), never pushed (`coros_workouts.session_steps` → 「被動熱適應不推」),
rate 0 TSS/h. COROS: an easy heat run (flag or 熱適應 in the title) is warm-up 10 / main / walk
cool-down 5 min, HR ≤ AeT, with the safety text in the description. 課表 page: a 熱 tag on heat
chips, `heat_passive` in the legend, no push button for it.

**Application order** (`shape()`, `backend/engine/plan_prefs.py:314`, then `place()`,
`backend/engine/plan_prefs.py:420`), in `week_plan` and every projected week:
1. The target hours are computed as before (CTL ramp, ≤ 10 % step, 3:1); `weekly_hours` only
   lowers them.
2. Quality count: 0 removes quality and the CP test (with a note); 2 duplicates this week's
   quality session as `quality2` — only when the caller's gate allows quality at all, and not
   in the 間歇門檻's guardrail mode (`Ctx.quality_cap`, `backend/engine/plan_prefs.py:366`). Quality terrain
   adds （平路）/（坡道） and rewrites the detail; HR target keeps only the 心率 part.
3. **Caps**: the long session is capped at the long-day cap (同平日 = weekday cap). A quality
   session over the weekday cap is shortened — warm-up 15 → 10, cool-down 10 → 5 min, then one
   rep fewer (never below 2) — with title / detail rewritten so the COROS step builder still
   parses it (`trim_quality`, `backend/engine/plan_prefs.py:184`). The **CP test is exempt**
   (its protocol is fixed) with the protocol's note `note_test(protocol)`
   (`backend/engine/plan_prefs.py:76`); the 37-min quick test rarely hits a cap.
4. **Distribution**: the remaining minutes go to easy runs. Count = runs − (long + hard) when
   set, else the original count raised to ⌈minutes / cap⌉ so every run fits the cap; never
   more than the allowed days. When the target still does not fit (target > count × cap):
   - **hard**: the long day takes what fits under its cap, the rest is dropped with
     「受限於你的偏好，本週少 X 小時；想補量可以多排一天或放寬長跑日上限」
     (`NOTE_HARD`, `backend/engine/plan_prefs.py:69`);
   - **soft**: the excess goes on the long day (beyond its cap; with no long session, on one
     easy run placed on the long weekday), weekday sessions stay within the cap, note
     `NOTE_SOFT` (`backend/engine/plan_prefs.py:70`).
5. **Terrain**: trail easy runs are 輕鬆越野跑 with an **HR-only target ≤ AeT** (pace and power
   are unreliable on trail) and the athlete's trail TSS / h; the long day becomes 路跑 /
   山路越野 (HR-only) / **登山** (kind `hike`, time-based: 「以時間為主：走滿 N 分鐘，不看配速」).
6. **Placement**: main sessions only on allowed days, one per day; the long session on the
   chosen long day (else the last allowed day left, like week_plan); quality ≥ 2 days from the
   long one and from each other; strength on the chosen weekdays, else on easy-run / allowed
   days, never the day before the long session.

The projection passes the same preferences (`backend/engine/projection.py:160`), rolls its
history on the minutes actually planned, and returns each week's preference notes. The 課表
page shows the notes of the weeks in view (`_plan_notes`, `backend/api/plan_sessions.py:625`).
User-edited and custom sessions are never overwritten: preferences only change the
generator's output and reconcile rule 3 keeps edited sessions.

**Saving** (`PUT /plan/prefs`, `backend/api/plan_sessions.py:385`) validates the whole set,
writes every key and commits; the page then opens the existing reconcile preview
(「依新的課表偏好重排」, `backend/static/schedule.html:1326`). Cancelling keeps the preferences
saved and the plan unchanged until the next reconcile. The generator inputs are memoised with
the preference stamp in the key (`backend/api/plan_sessions.py:57`), so a saved change
regenerates immediately. COROS pushes read the stored sessions, so they follow too; an HR
interval target becomes an HR work step (`backend/sync/coros_workouts.py:164`).

## 不排課日期 — blackout days (`blackouts.py`)

One-off date ranges (a long weekend, a trip) on which **nothing is planned**. Separate from the
recurring 可練日 preference: that one says which weekdays are for training, this one blocks
specific dates. Docstring with the rules: `backend/engine/blackouts.py:1`.

**Storage**: `user_settings` key `plan.blackouts` (default `[]`,
`backend/settings/repository.py:73`), a list of `{id, start, end, label}`. Validation
(`validate`, `backend/engine/blackouts.py:65`, called from `backend/settings/repository.py:171`):
ISO dates, start ≤ end, ≤ 62 days per range, ≤ 60 ranges, label ≤ 30 chars, unique ids, **no
overlaps**. Ranges may be in the past (kept as a record; only days from today on change the
plan). Read synchronously by `load()` (`backend/engine/blackouts.py:120`); bad data reads as none.

**Planning rules** (`week_plan`, `project_weeks`, `reconcile`):
1. **Never on a blocked day**: blocked days are removed from the candidate days before placement
   (`week_plan` both paths, `plan_prefs.place()`, `projection._place`,
   `backend/engine/projection.py:167`), so the existing placers keep their rules — long first
   (it gets the last free day before easy runs do), quality ≥ 2 days from the long and from each
   other, strength not the day before the long — and their 「排不進去」 drop path.
2. **Volume**: target hours × (allowed days not blocked ÷ allowed days) (`lost_days` / `factor`,
   `backend/engine/blackouts.py:162`, `backend/engine/blackouts.py:171`); allowed = the 可練日
   preference, else all 7. A past blocked day with a workout is not lost. With preferences, the
   run slots shrink by the lost days too.
3. **Week note** (`week_note`, `backend/engine/blackouts.py:176`):
   「9/30–10/4 不排課（連假出遊），本週少 X 小時」, plus 「剩下的日子排不下 N 堂課」 when sessions
   were dropped.
4. **The step after** (2026-10-01, detraining.md §6.6): the old `step_cap` (the week after a
   blocked week capped at `max(1.10 × done, done + 0.5 h)` — 0.5 h after a fully blocked week,
   far slower than Daniels) is no longer applied. A blackout ≥ 6 days with no run inside gets
   the re-entry block (`engine/reentry.py`, Daniels table 9.2: 50 % / 75 % … of the 4 weeks
   before, mode `reentry`, no quality inside); a shorter one is Daniels' category 1 — back to
   100 %, and the projection doesn't let that week lower the base it ramps from.
5. **Stored sessions** on a blocked day from today on (reconcile rule 6,
   `_clear_blackouts`, `backend/engine/reconcile.py:213`): unedited auto sessions follow the
   regenerated week (the change says 「在不排課日期內（label），移到 m/d」,
   `backend/engine/reconcile.py:267`), or, in a week that wasn't regenerated, move to the
   nearest free day (`move_to`, `backend/engine/blackouts.py:206`: same week, ≥ today, allowed
   weekday, not blocked, no other main session, a hard session never next to another hard day;
   ties go earlier; long first) or are dropped. **Edited / custom sessions are never changed
   without the user**: they come back as a `conflict` change with `move_to`, and only
   `decisions[uid]` = `move` / `delete` changes them (an edited auto session deleted leaves a
   tombstone). Adding or moving a session onto a blocked day is a 400
   (`backend/engine/plan_store.py:214`).
6. **COROS**: through the normal reconcile + push. Regenerated sessions are re-sent on their new
   day or removed as stale; an edited session still on a blocked day (no decision yet) is not
   pushed and its pushed copy is removed (`_on_blocked`, `backend/api/plan_sessions.py:316`,
   used at `backend/api/plan_sessions.py:324`). The push preview counts them
   (`blackout_to_remove`).

**Flow** (`backend/api/plan_sessions.py:414`): `POST /plan/blackouts/preview` regenerates with
the *candidate* list and returns the reconcile preview without saving anything (the generator
inputs are memoised with the blackout stamp in the key, `backend/api/plan_sessions.py:57`, and
`_compute_inputs` takes the candidate list); `PUT /plan/blackouts` saves the list, then
reconciles with the user's `decisions`. `POST /plan/reconcile` also takes `{decisions}`.

**Page** (`backend/static/schedule.html`): blocked days get a faint 45° hatch (the provisional
week hatch is 135°, so both read when stacked) and a small 🏖 label chip on the first day of the
range in each week row (every blocked day in the phone agenda); no ＋, no add-on-click, no drop
target (`boOf`, `backend/static/schedule.html:666`). Ranges are set by dragging across days
(mouse / pen, 8 px threshold, never starting on a chip or button; `backend/static/schedule.html:1439`),
by clicking a day and Shift-clicking another, or from ⋯ → 「🏖 設定不排課日期」
(`backend/static/schedule.html:389`), which also works on the phone. The dialog
(`backend/static/schedule.html:497`, `openBo` `backend/static/schedule.html:1340`) takes start,
end and an optional label, checks overlap / length client-side, and shows how many sessions sit
in the range; clicking the chip edits or removes the range. Creating, editing and removing all
go through the reconcile preview first (`applyBlackouts`, `backend/static/schedule.html:1377`),
where each conflicting edited session gets 移到… / 刪除 / 先留著 (`changesHtml`,
`backend/static/schedule.html:845`). Blackout notes show above the calendar with a 🏖 mark.

## Same-load conversion (`equivalence.py`)

When a session's terrain changes in the dialog (路跑 → 越野 / 登山), the page designs a session of
the **same time**, hence the same TSS: the sessions it applies to are easy (≤ AeT), and at the
same intensity TSS grows with time at the same rate (hrTSS = h × IF² × 100). Only time has to
be predicted from distance and climb, per terrain, for this athlete.

**Model** (docstring `backend/engine/equivalence.py:1`; `fit`, `backend/engine/equivalence.py:236`):
- Samples: the last 26 weeks; easy = avg HR ≤ AeT + 3 (the review card's easy-run tolerance), ≥ 20
  min, ≥ 1 km (`samples_from`, `backend/engine/equivalence.py:320`).
- Naismith's additive form (1892) with Langmuir's grade-dependent descent correction
  (*Mountaincraft and Leadership*, 1984: −10 min per 300 m on 5–12° descents, +10 min on
  steeper; `descent_hours`, `backend/engine/equivalence.py:134`):
  `h = km / v_flat + gain / VAM + descent`.
  v_flat = median speed of easy flat road runs (else the speed at AeT from a speed ~ HR line
  over all flat road runs; `backend/engine/equivalence.py:178`); trail VAM by least squares
  on the residual (`backend/engine/equivalence.py:202`); hike flat walking speed and VAM
  fitted together (`backend/engine/equivalence.py:215`).
- Fewer than 5 samples on a terrain → effort distance EP = km + gain/100 (ITRA / 健行筆記,
  `algorithms/effort.py` SIMPLE_FORMULAS["itra"], measured there at 6.9 % vs integrated
  Minetti on one runner's data) at the athlete's median EP speed on that terrain.
- With ≥ 5 samples both forms are fitted and the one with the lower **inner** leave-one-out
  error is used (`method="auto"`).
- `design()` solves km (and climb = km × m/km) for a time at a climb density
  (`backend/engine/equivalence.py:162`); road uses EP at v_flat.
- `TODO(racepower-v2)` (`backend/engine/equivalence.py:58`): a validated grade-cost model from
  `backend/engine/racepower/` can be passed as `fit(..., grade_cost=f)`; nothing imports it yet.

**Validation** (`backtest`, `backend/engine/equivalence.py:301`): leave-one-out on the athlete's
own easy trail and hike activities — refit without the activity (including the method choice),
predict its moving time, compare. Result on one runner's data (2026-09-30, 26 weeks):

| Terrain | n | Method chosen | MAPE | Bias | Naismith / Langmuir alone | EP alone |
|---|---|---|---|---|---|---|
| 越野 trail | 9 | EP | 5.6 % | −1.0 % | 11.3 % | 5.6 % |
| 登山 hike | 3 | EP (< 5 samples) | 21.5 % | +1.7 % | — | 21.5 % |

Flat easy road speed from 7 runs. Trail samples span 55–111 m/km; the page warns outside
that range. A terrain whose MAPE is above 15 % (`ESTIMATE_MAPE`) or that cannot be backtested is
labelled **推估** — on that data hike is 推估, trail is 依你的紀錄.

**Dialog** (`backend/static/schedule.html:419`): terrain 路跑 / 越野 / 登山 (登山 ⇄ kind hike), a
爬升比例 slider 0–150 m/km, a 套用目標賽事 button (the goal's climb per km), distance / climb /
TSS fields, a lock (鎖時間 / 鎖 TSS / 鎖距離) and 完全自由調整; editing any unlocked field
recomputes the rest with the same formula in JS (`eqH`, `backend/static/schedule.html:987`;
`eqRecalc`, `backend/static/schedule.html:1008`). It shows the live TSS and its difference vs
the original (「比原本多 15 %」), warns above the preference cap (`capFor`,
`backend/static/schedule.html:975`), when the typed distance / climb would take a different
time, and outside the data range; a trail / hike target becomes HR-only ≤ AeT. The session is
saved with `terrain`, `distance_km`, `climb_m`.

## Multi-week projection (`projection.py`)

`project_weeks(cur, phases, until, ctlconstant, atlconstant, prefs)` (`backend/engine/projection.py:227`) starts
from this week's `week_plan()` output and rolls the same rules forward week by week, never
more than `MAX_WEEKS` = 8 ahead (`backend/engine/projection.py:30`):

- Hours per week (`week_hours`, `backend/engine/projection.py:71`): base / specific use the CTL
  ramp goal capped at +10 % (≥ +0.5 h) of max(4-week mean, last week), with a 65 % recovery
  week after 3 build weeks; taper 40–50 % of the 6-week mean; event 30 %; recovery 50 %;
  transition 65 % of the 4-week mean. A weekly-hours preference caps it
  (`backend/engine/projection.py:269`).
- Sessions (`week_sessions`, `backend/engine/projection.py:98`): the same template (long, one
  quality, strength, easy fill) placed by `_place` (`backend/engine/projection.py:167`), or
  shaped and placed by the preferences. Base sessions come from the 間歇門檻 per week
  (`_bq`, `backend/engine/projection.py:172`), a base recovery week gets the fartlek
  (`backend/engine/projection.py:137`), and the AeT test is projected on its cadence
  (`backend/engine/projection.py:310`).
- Whether a projected week gets a quality session is decided per week, for that week's phase,
  mode and Monday (`allow_quality` → `quality_gate.week_decision`,
  `backend/engine/projection.py:233`): the method state from this week (`weeks` mode
  re-evaluated per Monday), only the intensity guardrail carried forward, and the dose step
  advanced once per projected interval week (this week's own interval counts as a step).
  A CP-test week no longer carries into later weeks; a `cur` without the new gate — or with
  the old `{levels, streak_ok}` shape — becomes a no-method gate (intensity bad blocks)
  (`_gate_inputs`, `backend/engine/projection.py:211`).
- CTL / ATL roll forward with the athlete's constants (`ds.athlete.ctlconstant` /
  `atlconstant`, `backend/engine/projection.py:302`); a session `_place` left without a day is
  kept out of the date filter.
- Each projected week carries `mode`, hours, TSS, CTL start / end, `why`, `provisional`
  (true beyond next week) and, with preferences, `notes`.

The horizon is the current phase end, at least two weeks out, capped at `MAX_WEEKS`
(`backend/api/plan_sessions.py:67`).

## Stored plan (`plan_store.py`, `reconcile.py`)

**Table** `plan_sessions` (`backend/db/models.py:145`): one row per planned session — `uid`,
`week_start`, `gen_key` (the generator's id: long / quality / easy1 …; none for custom), `day`,
`kind`, `title`, `minutes`, `target`, `detail`, `source`, `tss`, `origin` (auto / custom),
`edited`, `provisional`, `state` (active / done / missed / deleted / superseded), `done_by`
(JSON activity row), `note`, and `terrain` / `distance_km` / `climb_m`
(`backend/db/models.py:172`, added by `_migrate_schema`, `backend/db/database.py:43`).

**Kinds** (`backend/engine/plan_store.py:19`): easy 輕鬆跑, long 長時間, quality 強度課, test 測試,
hike 健行／登山, strength 肌力. **Terrains** road / trail / hike (`backend/engine/plan_store.py:26`).

**Reconcile rules** (`reconcile()`, `backend/engine/reconcile.py:85`; documented at
`backend/engine/reconcile.py:12`):
1. Active (and previously missed) sessions up to today that match an activity — the
   generator's own done-match for auto sessions, else same day + same kind of activity — become
   **done**; the rest on past days become **missed**, but only up to the day the synced data
   covers (`covered`), so a late sync can turn a missed session back into done
   (`backend/engine/reconcile.py:126`).
2. Per generated week, unedited auto sessions from today on are replaced by the regenerated
   ones: same `gen_key` → changed, gone → removed, new → added. Terrain, distance and climb
   are regenerated fields (`FIELDS`, `backend/engine/reconcile.py:44`).
3. Edited and custom sessions are kept. An edited long / quality / test is **superseded** when
   the regenerated week is a rest week (recovery / taper / event / transition) that no longer
   has it (`backend/engine/reconcile.py:156`). Deleted auto sessions stay deleted: their
   tombstone blocks the `gen_key` for that week.
4. An auto session on the same day as a kept edited / custom session moves to a free day of
   that week, or is dropped (`backend/engine/reconcile.py:278`).
5. Unedited auto sessions past the horizon are removed.
6. 不排課日期: nothing active stays on a blocked day from today on; edited / custom sessions
   there are a `conflict` until the user decides (see 不排課日期 above; documented at
   `backend/engine/reconcile.py:26`).

Each change is returned as `{action, uid, day, title, kind, minutes, origin, edited, reason?,
before?, conflict?}` (`conflict` = `{label, move_to, day, choice}`) and grouped by day for the preview (`backend/engine/reconcile.py:312`).

**Coverage** (`_covered`, `backend/api/plan_sessions.py:91`): the later of the latest activity
day and the day before the latest successful COROS / generic sync.

**Automatic reconcile** (`_ensure`, `backend/api/plan_sessions.py:156`): on the first visit of
a week, or while an earlier week still has active sessions, the plan is reconciled and saved
before anything else is returned.

**Edits** (`backend/engine/plan_store.py:160`, `backend/engine/plan_store.py:220`): editable
fields are day, kind, title, minutes, target, detail, terrain, distance_km, climb_m. A day must
be ISO and not in the past; kind and terrain must be known; minutes 0–1440; distance 0–500 km,
climb 0–20000 m (`backend/engine/plan_store.py:183`); title not blank. An edit marks the session
`edited` and non-provisional. Moving an auto session to another week leaves a tombstone in the
old week and turns the session into a custom one. Only active sessions can be edited.

**Add** (`backend/engine/plan_store.py:249`): a custom session needs a day; defaults kind easy,
45 min, a title per kind. **Delete** (`backend/engine/plan_store.py:268`): an auto session
becomes a tombstone (`state = deleted`), a custom one is removed.

**Stored-plan summary** (`plan_summary`, `backend/engine/plan_store.py:284`): the week's
target hours (active + done sessions, strength excluded) and TSS, plus the CTL / ATL
projection described under PMC, ending CTL / ATL and next-Monday TSB.

**Concurrency**: plan writes are serialized by one asyncio lock per event loop
(`backend/api/plan_sessions.py:144`), so two tabs or a preview racing a push cannot generate
the same week twice.

**Adaptation and automation**: every reconcile path goes through
`plan_store.reconcile_with_adapt`. It applies `engine/adapt.py` to the generator's current
week before reconcile:
- missed easy runs are not made up
- missed quality and long sessions move within the 48-h spacing or are cancelled
- an easy run done too hard stays done and the rest of the week adapts
- the fatigue guard

Done sessions count their actual TSS in the bars (`plan_store.session_tss`). After a sync
the plan runs on its own and pushes the next 7 days, and big changes wait for approval. A
change log with 復原 and the kind `notice` (課表待確認) are part of it. See
`docs/spec/plan-auto.spec.md`.

## COROS push (`coros_workouts.py`)

Pushes stored sessions to COROS Training Hub as structured, scheduled workouts through the
unofficial Training Hub API (same host and token as the COROS sync client; endpoints listed at
`backend/sync/coros_workouts.py:6`).

- **Scope** (`_range`, `backend/api/plan_sessions.py:164`): `day` = that day; `week` = the
  Monday–Sunday week of `day`, from today on; `phase` = today to the phase end, capped at
  `MAX_WEEKS`. `day` defaults to today; the plan's "today" is never earlier than the real date
  (`backend/api/plan_sessions.py:152`). A `week` entirely before today is a 400 for preview,
  push and unpush instead of an empty range (`backend/api/plan_sessions.py:178`); a past `day`
  scope is not guarded.
- **Every push reconciles first** and applies the result, then pushes the active sessions in
  range (`backend/api/plan_sessions.py:324`).
- **Session → steps** (`session_steps`, `backend/sync/coros_workouts.py:185`): long / hike /
  easy are one time step at HR ≤ AeT; an easy session whose title has `N×S 秒` gets a strides
  repeat when ≥ 10 min remain; quality and test sessions get their own step builders — a
  quality session whose target carries only a 心率 range (間歇目標 = 心率) gets HR work steps
  (`backend/sync/coros_workouts.py:164`). Strength, race and rest are not pushed (skipped, with
  a reason). Done, unplaced and past-day sessions are not pushed
  (`backend/sync/coros_workouts.py:302`).
- **Program** (`build_program`, `backend/sync/coros_workouts.py:248`): run sport; HR targets as
  absolute bpm with the LTHR zone scheme; names `TRC <title> <m>/<d>`, ≤ 30 chars
  (`backend/sync/coros_workouts.py:297`).
- **Idempotency** (`_push_one`, `backend/sync/coros_workouts.py:530`): each push is recorded in
  `coros_plan_push` (`backend/db/models.py:123`) with the COROS program / plan / schedule ids
  and a SHA-256 fingerprint of day + payload. Same fingerprint → left alone; changed → the old
  COROS entry is removed and a new one created; an entry already executed on the watch is kept
  as done. The stored-plan push keys rows by session `uid` (`session_key`,
  `backend/db/models.py:130`).
- **Clean-up** (`push_sessions`, `backend/sync/coros_workouts.py:589`): pushed sessions that
  left the plan (deleted / superseded / regenerated away) are removed unless on a past day;
  missed sessions are removed from the calendar. Only entries recorded in `coros_plan_push` are
  ever deleted (`_remove_row`, `backend/sync/coros_workouts.py:628`).
- **Unpush** (`DELETE /push-coros`, `backend/api/plan_sessions.py:349`) removes every recorded
  entry whose day falls in the range (`remove_keys`, `backend/sync/coros_workouts.py:613`).
- **Status per session** (`status_of`, `backend/sync/coros_workouts.py:487`): done / skipped /
  not_pushed / pushed / outdated / failed; sessions no longer active but still recorded show
  `pushed_<state>` (`backend/api/plan_sessions.py:186`).
- The old week-keyed helpers (`push_week` / `remove_week` / `week_status`, keys
  `<week start>/<session id>`) were only used by tests and are gone; the tests drive
  `push_sessions` / `remove_keys` / `status_of` directly.
- Pushes and removals are serialized by a module-level lock
  (`backend/sync/coros_workouts.py:74`). An expired COROS login returns 401
  `COROS_AUTH_REQUIRED` with a hint to log in again on the settings page
  (`backend/api/plan_sessions.py:320`).

## Page (`backend/static/overview.html`)

- Title 訓練總覽, heading and nav entry 總覽 (`backend/static/overview.html:6`,
  `backend/static/overview.html:188`).
- **Glossary hovers**: the terms `AeT` and `CP 測試` inside engine text (actions, indicator
  verdict / why / action, week reasons, notes, the thresholds line) get a hover / tap
  explanation of what they are and how to test them (`backend/static/overview.html:288`).
- **Sources removed from the plan**: the to-do list no longer prints sources, and the 依據 block
  of the week plan is gone — `w.rules` still carries them, noted in a code comment
  (`backend/static/overview.html:406`). Indicator cards keep a collapsible 來源
  (`backend/static/overview.html:357`).
- The day list, the week's progress-bar targets, the Sunday CTL, next-Monday TSB and the PMC
  projection come from the stored plan (`backend/static/overview.html:306`).
- **5 區（最大攝氧量間歇）狀態** card (`#z5card`, left column under 還缺什麼, beside 本週該做什麼
  because it decides whether the week's interval is Zone 3 or Zone 5): the state pill (icon + text:
  未確認 / 已確認（日期、路徑）/ 暫停（原因）/ 恢復期 / 不設門檻), then the quest-style stage
  flow (`z5_card.flow` = `quality_gate.z5_flow`, drawn by `static/z5flow.js`; full width of the
  cards row): one 「你現在在這裡：<stage> · 下一步：…」 line (+「同時可以做」 for the parallel
  stage; the full 「還缺：…」 `next` text behind ?) and five stage cards — 有氧基礎 → 3 區階梯
  (the Z3 rungs) → 有氧基礎確認 (週量穩定 + 三選一 tests; the 2/3 維持 line once confirmed;
  can run alongside the Z3 ladder) → 5 區解鎖 → 5 區階梯 (the Z5 rungs). Each card: badge (✓ /
  number), tag word (已完成 / 現在 / 可同時做 / 未解鎖), checklist ☑ / ☐ with one short 「→ 下一步」
  line, 「完成後：」 what it unlocks; sources behind ?. Vertical in a narrow box (done / locked
  stages fold to their header, tap to open), horizontal from 860 px (container query).
  Presentation only: every flag comes from `z5_card`. 「歷程 →」 opens the 基礎期 panel.
- **5 區開放流程 panel** (viewer, 周期化訓練 → ② 基礎期, custom view `kind: "z5gate"`,
  `wko5views.z5gate_panel`): the same stage flow as the card (`z5.progress.flow` =
  `wko5views.z5_progress` = `z5_card` on the status gate) — no chart, no time axis — and a
  collapsed 歷程 list (`quality_gate.z5_history`, range capped at a year: every state change and
  event in order, the description and the sources). The render cache key adds the preference
  and test-session stamps.
- Plan editing, drag-to-move, reconcile preview and COROS push by day / week / phase live on
  the 課表 page (`backend/static/schedule.html`: session dialog `backend/static/schedule.html:1152`,
  reconcile `backend/static/schedule.html:864`, push `backend/static/schedule.html:884`, unpush
  `backend/static/schedule.html:914`), which also has the ⚙ 課表偏好 panel
  (`backend/static/schedule.html:460`, `openPrefs` `backend/static/schedule.html:1308`), its
  client-side checks (`backend/static/schedule.html:1279`) and the preference notes above the
  calendar (`backend/static/schedule.html:730`).
- **課表 toolbar wording** (2026-10-04): 「抓活動／匯入」 = 資料來源 → here, 「推送」 = 課表 → 手錶.
  The push split button reads 推送到手錶 (was 「同步到 COROS」). Left of it, ⟳ 從 {COROS｜TrainingPeaks}
  抓活動 (`#pull-btn`, `backend/static/schedule.html:545`; `pull` `backend/static/schedule.html:1312`)
  runs the same manual sync as 設定 › 立即同步 for the 資料來源 in use only (`GET /api/v1/sync/primary`,
  then the SSE start endpoint through the shared `backend/static/syncrun.js:22`). Not logged in /
  login expired / source switched off → a 到設定頁 link instead (`renderPull`,
  `backend/static/schedule.html:1298`; when COROS is the source the push side's login link covers
  it); hidden in the demo. Progress (已檢查 n · 新下載 m) and the result show in `#sync-msg`; 409
  `SYNC_BUSY` is a hint, not an error. When it ends the calendar reloads (new activities pair:
  ✓／未完成) and, if anything was downloaded, reloads once more ~5 s later together with the
  自動調整 box (`window.autoPlanRefresh`) for the background `plan_auto.after_sync`.
- **Calendar status glyphs** (2026-10-04): 完成 = the chip itself (✓ before the title + compliance
  colour, ≠, 未完成, ● activity chips). 推送狀態 = a small watch at the chip's top right, only on
  active sessions today or later (`SYNC` / `SY_SVG`, `backend/static/schedule.html:888`): 已推送
  neutral grey outline, 需更新 yellow, 失敗 red, 未推送 dashed; labels are provider-neutral
  (手錶). ✓ is never used for push. The legend has two titled groups, 完成 and 手錶
  (`renderLegend`, `backend/static/schedule.html:1100`). 已推送 stays visible (subtle).

## Status engine change

- `Status.weekly_hours()` sums moving time (fallback recorded time) instead of recorded
  time (`backend/engine/status.py:168`), so the volume indicators aren't inflated by multi-day
  trips.
- **`i_drift`** (`backend/engine/status.py:392`) is **informational**: the same per-run drift as
  the single-activity review (`workout_review.drift_series`, `backend/engine/workout_review.py:955`):
  road runs, ≥ 40 min, avg HR ≤ AeT+3, hilly / stopped / unsteady runs refused. It reads
  `drift_series(ref=True)`: the 參考 tier (30–40 min after the warm-up, 推估) counts for the
  median, the text gets 「（參考）」/「（含參考）」, `why` names how many, and `extra` is
  `{fair, median, ref, test, ref_label, ref_tip}` — overview.html's `driftTier` shows
  「參考 N 次是參考（暖身後 30–40 分，未達 UA 測試標準）」 with the why as a `.tip` hover.
  > 10 % → bad (輕鬆跑太快) **only on ≥ 2 strict runs whose own median is ≥ 10 %** (the level
  feeds the base-phase guardrail), otherwise info; the text says 「飄移是 AeT 測試用的，
  不是間歇門檻」. Source Friel (< 5 %) and 徐國峰 (90′ < 10 %), not Uphill Athlete.
  **drift v2**: one run is ±4–6 pp, so the number shown and judged is the **inverse-variance
  mean ± SE of the last 6 fair runs** (`drift_agg.aggregate`; text 「3.1% ±1.8」, `extra.agg`);
  the median stays in `why` / `extra.median`, the single runs in the spark. The BAD level still
  needs ≥ 2 strict runs whose median is ≥ 10 % (strict tier only, as before).
  **Heat bands** (2026-10-02, `workout-review.spec.md` §Heat bands): runs above 25 °C are no
  longer refused; each point carries its temperature band (< 25 / 25–28 / > 28 °C, 28 推估 /
  溫度不明) and the indicator **compares only within one band** — the band of the latest fair run
  when it has ≥ 2 runs in 8 weeks, else the band with the most (`drift_agg.pick_band`). The text
  ends 「· 🌡 25–28 °C」; `extra` adds `band`, `band_label`, `chip`, `heat`, `bands` (every band's
  `{n, agg, label}`) and `band_tip`; `why` lists the other bands' counts and, in heat,
  「熱環境，結果可能偏高」. In a heat band (> 25 °C) the level never turns BAD — heat inflates the
  drift (Lafrenz 2008), so > 10 % there reads 「…不當警示…涼一點的日子再看」. The Friel / 徐國峰
  gate methods count heat runs: a pass unlocks (conservative), a fail says 「可能是熱造成的」.
- **`i_testing`**: AeT age no longer sets the level (B3); 「建議 AeT 測試：…」 comes from
  `gate["aet_test_reason"]`; after a break ≥ ~8 weeks (re-entry `cp_retest`) the CP test is due
  once the block ends (WKO5 seminar notes). `i_fitness`: CTL ramp ≥ 8 bad, 5–8 watch (Friel).
  `i_volume`: > 20 % bad (Nielsen 2014 / Damsted 2019), 10–20 % watch (推估).
- **`i_gate`** hover adds the Zone 5 state (「Zone 5：未確認／已確認（日期、路徑）／暫停（原因）／
  恢復期」, 台灣教練: 3 區先、5 區後) and 「建議測試：…」; a locked method reads 「5 區未開」 and
  still names this week's Zone 3 session.
- **`i_heat`** 「熱適應」 (`Status.i_heat`; design `docs/research/heat-acclimation.md` §5.3): the
  heat-acclimation index S (`engine/heat.py`) from the per-activity exposure
  (`heat_data.exposures`, route_weather `activity_weather.json`) plus ticked heat_passive sessions
  (`heat_data.completed_passive_dates`). Text 「72 %（部分）」; level acclimatised ≥ 0.75 good,
  partial ≥ 0.35 watch, else info; bad only when a hot A/B race is within 30 days
  (`heat_plan.hot_race`) and its projected S < 0.75, with the action to start the heat block at
  race − 21 days. `spark` = S over 120 days; `extra` = s_race, doses (bars), HRC trend
  (`heat.hr_cost` on steady flat stretches, 「觀測不支持模型」 when S rises and HRC does not fall),
  a, badge 推估. Without exposure data the verdict asks for a weather-enabled routes build.
- **`i_gate`** 「間歇門檻」 (`backend/engine/status.py:419`): `quality_gate.evaluate` +
  `indicator` (`backend/engine/quality_gate.py:783`) with the status' 課表偏好 (`Status(prefs=…)`;
  the API's status cache keys on `prefs.stamp()`, `backend/api/overview.py:49`). Second in
  `PHASE_PRIORITY["base"]` (`backend/engine/status.py:793`), so its WATCH action lands in 還缺什麼.
  Texts per the design doc §4.6: auto without AeT → info 「沒有 AeT 實測：照 80/20 原則每週 1
  次間歇（第 N 步：…）」; a blocking guardrail → watch with its number (e.g. 「本週不排間歇：低強度只有
  68%（< 75%）」); ua_gap locked → 「AeT 142 / LTHR 165：差距 16%（> 10%，有氧不足）」; unlocked →
  good 「差距 9% ≤ 10%：可以加 Zone 3」; forced + missing → watch 「沒有實測 AeT，差距法算不出來：先照
  護欄排（自訂…）」, action 「先做 AeT 飄移測試，或把間歇門檻改回自動」. `why` names the mode and
  the AeT source (「AeT 146（活動資料估算）」 / 「（{date} 飄移測試）」). `extra` is the gate dict incl.
  `options` (per mode usable + why, `backend/engine/quality_gate.py:624`).
- `PHASE_GOAL["base"]` no longer says 飄移 < 5 %; `PHASE_FOCUS["base"]` cites UA for the easy long
  run and Palladino for the 8–15 s hill sprints (`backend/engine/status.py:804`).
- `i_data`'s action for a missing AeT is 「排一次 AeT 飄移測試（平日，10 分暖身＋40 分固定功率，跑步機或平路）；
  測了可以改用有氧基礎門檻」 (`backend/engine/status.py:721`).
- **`i_testing`** (`backend/engine/status.py:580`) — timing rules unchanged: a CP / LTHR / AeT
  row older than 42 days → watch, 90 → bad (`backend/engine/status.py:51`); 10–21 days before
  the A event is named the right time; < 10 days → 「賽前 10 天內不要測，賽後再測」, watch. The
  action names the 課表偏好 protocol (`_cp_protocol`, `backend/engine/status.py:568`); `race` →
  「用 5–10 K 比賽或計時跑代替 CP 測試」. It also reads the latest CP test in the data
  (`workout_review.latest_cp_test`, 120 days, `backend/engine/workout_review.py:996`), whose
  `delta` is against the **previous result of the same method** (`cp_protocols.reference`;
  across methods converted two-point ≈ 1.05 × a 30-min CP, 外插), so rotating quick / standard
  doesn't keep flagging. Not applied (no CP row dated on / after the test), an `apply` payload
  (not 不採用) and |delta| > `CP_DELTA` 3 % → at least watch, 要更新, action 「套用這次的 CP」.
  `extra.cp_test` carries method, quality, `ref`, `apply`, `applied`; the 總覽 測試 card draws
  the apply button from it (`applyCpBtn`, `backend/static/overview.html:291`), POSTing
  `/api/v1/plan/thresholds/apply-cp` (see `workout-review.spec.md`).
  `extra.cp_due` (CP missing / > 42 days, `backend/engine/status.py:612`) decides the CP-test
  session in `week_plan` (the CP test measures CP only). The latest AeT drift test
  (`aet_test.latest_aet_test`) → `extra.aet_test` (`backend/engine/status.py:660`): band "at" and
  not applied → watch 「{date} 的 AeT 測試：飄移 4.2%，AeT = 146 bpm（目前 142）」, action 「套用這次的
  AeT（146 bpm）」, and the 總覽 測試 card's button (`aetApply`, `backend/static/overview.html:298`)
  POSTs `extra.aet_test.apply` to `/api/v1/plan/thresholds/apply-estimate` with the test `date`;
  band below / above → 「下次起始心率 +5／−5 bpm 再測一次」. A plan AeT older than 16 weeks under
  `auto` → watch 「AeT 已經 N 週沒測，門檻改用不設門檻模式」, action 「重測 AeT」
  (`backend/engine/status.py:675`).
- **AeT drift test** (`backend/engine/aet_test.py`): `due` (`backend/engine/aet_test.py:243`) — base
  phase, no plan AeT or one > 6 weeks old, no test in 4 weeks, base week 2, 7, 12… (every 5
  weeks, our choice); the session (`aet_test.session(th, hr0, p0, cap_weekday)`) is kind `test`,
  id `test_aet`, target 「固定功率 P W（±3%）；心率從 HR 附近開始」 (start HR = the estimate's
  aethr, else 0.89 × LTHR − 5; P = 0.75 × CP, both our choice), in place of the week's
  interval, and its length follows the 課表偏好 weekday cap (`variant_for`): no cap / ≥ 80 →
  「AeT 飄移測試 60 分」 80 min (暖身 15 + 測試 60 + 緩和 5); < 80 → 「AeT 飄移測試 40 分」 50 min,
  UA's minimum (暖身 10 到開始流汗 + 測試 40, 緩和可省略; "If you only have 40 minutes, do
  that."). The detail starts with which and why (「平日上限 50 分 → 用 UA 最短 40 分版本」 /
  「沒有平日時間上限 → 標準版 80 分」), then 冷氣房跑步機 2–3%＋電扇（首選），或清晨平路環線，不要山路;
  中途不停; 「氣溫 25 °C 以下時開始（熱會讓心率偏高、飄移失真）」 as advice (`aet_test.HEAT_TEXT`);
  記下溫度 (heat bands, 2026-10-02: the analysis no longer refuses > 25 °C — a pass in heat
  counts, 「熱環境，結果可能偏高」, a fail may be the heat); Evoke's early abort 「主課第 10 分鐘心率已經比起始高 10 下還在升 →
  起始太高，停掉改天降 5 bpm 再測」. Placed by `aet_test.pick_day` (weekday first, see 課表偏好
  AeT 飄移測試). Never the CP-test week. Marked done by a ≥ 48-min road run titled AeT, or an
  untitled one ≥ 55 min (so ordinary 41–52′ easy runs aren't the test). COROS steps in
  `coros-sync` (`_aet_test_steps`, `backend/sync/coros_workouts.py:200`): 10 / 40 (no
  cool-down step) or 15 / 60 / 5.
- Inline source names were removed from engine text (e.g. the ramp verdict, phase focus).

## API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/v1/overview/status` | `Status.to_dict()`: today, phase, goals, headline, indicators, actions, counts (`backend/api/overview.py:62`) |
| GET | `/api/v1/overview/summary?unit=week\|month\|year&anchor=&n=` | buckets + current detail; n capped 104 / 60 / 12 (`backend/api/overview.py:69`) |
| GET | `/api/v1/overview/pmc?begin=&end=` | daily tss / ctl / atl / tsb; default the last 180 days (`backend/api/overview.py:79`) |
| GET | `/api/v1/overview/weekplan` | the generated week plan, with the stored 課表偏好 (`backend/api/overview.py:90`) |
| GET | `/api/v1/overview/z5` | the 「5 區（最大攝氧量間歇）狀態」 card: `quality_gate.z5_card` on the status gate (the object the week plan decides with) + `history_href`, the viewer link of the first `z5gate` panel (`backend/api/overview.py`) |
| GET | `/api/v1/overview/page` | `backend/static/overview.html` (`backend/api/overview.py:100`) |
| GET | `/api/v1/overview/plan/sessions?start=&end=` | reconcile-if-needed, then stored sessions (not deleted / superseded) with COROS status, week meta, projected weeks and `summary` (`backend/api/plan_sessions.py:203`) |
| POST | `/api/v1/overview/plan/sessions` | add a custom session; 400 on a bad field (`backend/api/plan_sessions.py:231`) |
| PATCH | `/api/v1/overview/plan/sessions/{uid}` | edit day / kind / minutes / title / target / detail / terrain / distance_km / climb_m; 400 on a bad field (`backend/api/plan_sessions.py:241`) |
| DELETE | `/api/v1/overview/plan/sessions/{uid}` | tombstone (auto) or remove (custom); 404 when unknown (`backend/api/plan_sessions.py:251`) |
| GET | `/api/v1/overview/plan/reconcile` | preview: changes and changes by day (`backend/api/plan_sessions.py:260`) |
| POST | `/api/v1/overview/plan/reconcile` | apply the same; optional body `{decisions}` for sessions on a 不排課日期 (`backend/api/plan_sessions.py:277`) |
| GET | `/api/v1/overview/plan/push-coros/preview?scope=day\|week\|phase&day=` | sessions in range with COROS status, counts to send / unchanged / skipped, missed to remove, pending changes; 400 for a past week (`backend/api/plan_sessions.py:294`) |
| POST | `/api/v1/overview/plan/push-coros?scope=&day=` | reconcile, push the range, clean up; 401 `COROS_AUTH_REQUIRED`; 400 for a past week (`backend/api/plan_sessions.py:324`) |
| DELETE | `/api/v1/overview/plan/push-coros?scope=&day=` | remove what was pushed in the range; 400 for a past week (`backend/api/plan_sessions.py:349`) |
| GET | `/api/v1/overview/plan/prefs` | `{prefs, defaults, active, gate_options}` — `gate_options` = the 間歇門檻 hover texts (`quality_gate.option_texts`, `backend/engine/quality_gate.py:866`) (`backend/api/plan_sessions.py:375`) |
| GET | `/api/v1/overview/plan/prefs/gate` | per mode `{usable, why}` on the athlete's data, plus the active mode / state / verdict (status `i_gate`, `backend/api/plan_sessions.py:382`) |
| POST | `/api/v1/plan/thresholds/apply-estimate` | now takes an optional `date` (the test day; not in the future) so 「套用這次的 AeT」 dates the row on the test (`backend/api/plan.py:282`, `backend/api/plan.py:292`) |
| PUT | `/api/v1/overview/plan/prefs` | the whole preference set (Prefs field names, missing = default); 400 on a bad / unknown value or a cross-field rule (`backend/api/plan_sessions.py:385`) |
| GET | `/api/v1/overview/plan/blackouts` | `{blackouts}` — the stored 不排課日期 (`backend/api/plan_sessions.py:422`) |
| POST | `/api/v1/overview/plan/blackouts/preview` | `{blackouts}` → the reconcile preview with that list; nothing saved; 400 on a bad range (`backend/api/plan_sessions.py:429`) |
| PUT | `/api/v1/overview/plan/blackouts` | `{blackouts, decisions?}` → save, then reconcile applying `decisions` `{uid: move \| delete}`; 400 on a bad range / decision (`backend/api/plan_sessions.py:440`) |
| GET | `/api/v1/overview/plan/equivalence` | the time model, LOO backtest per terrain, 推估 flags, sources; memoised per dataset / day / AeT (`backend/api/plan_sessions.py:481`, `backend/api/plan_sessions.py:463`) |
| POST | `/api/v1/overview/plan/equivalence/design` | `{mode, minutes, climb_per_km}` → km, climb, 推估 flag (`backend/api/plan_sessions.py:486`) |
| GET | `/api/v1/overview/plan/calendar?start=&end=` | the 課表 page payload, now with `prefs`, `goal_climb_per_km` (`backend/api/plan_sessions.py:532`) and `plan_notes` (`backend/api/plan_sessions.py:643`) |
| GET | `/` | redirects to the overview page when no `frontend/dist` build exists (`backend/main.py:81`) |

`Status` is memoised per (dataset, day, `plan.json` mtime, CP-test protocol, stored test
sessions) (`backend/api/overview.py:40`); the
plan endpoints memoise their generator inputs on the same key plus the preference and blackout stamps
(`backend/api/plan_sessions.py:57`). Bad scope or day → 400 (`backend/api/plan_sessions.py:164`).

## Testing

- `backend/tests/test_overview.py`: category mapping, period arithmetic and labels, the
  projection matching the `tl()` recurrence and TSB = yesterday's CTL − ATL, and the ramp
  formula reaching exactly +3 CTL in 7 days.
- `backend/tests/test_plan_store.py`: projection ramp / cap / horizon, every reconcile rule
  (regeneration, tombstones, missed and late-sync done, coverage, collisions, supersede,
  horizon), persistence and edits, API initialisation, push scopes and idempotency, missed
  removal, concurrent first loads, unpush, and the stored-plan summary moving bars and projection.
- `backend/tests/test_plan_prefs.py`: defaults change nothing (projection and, golden,
  `week_plan`); 50-min cap within cap and volume kept; hard cap note; soft cap excess on the
  long day (and on an easy run when there is no long); long-day cap first; CP test exempt;
  quality trimming; run counts 3–7; rest days; long day and quality spacing; quality 0 / 2 and
  the drift gate; strength count and days; weekly-hours cap; trail HR-only target and rate;
  time-based hike; quality terrain and HR target → COROS HR steps; validation and cross-field
  checks; the prefs API; reconcile keeps an edited session when prefs change.
- `backend/tests/test_blackouts.py`: range validation; placement never on a blocked day (with
  and without preferences, 50-min cap, rest days, strength days); the long run keeps the last
  free day and easy runs are dropped; hard days stay apart; `move_to` (nearest, hard spacing,
  today, allowed weekdays); regenerated sessions say why; move vs drop in weeks not regenerated;
  edited sessions as a conflict until move / delete; the factor and note text; projection weeks
  (hours × kept share, note, earlier weeks untouched, the ≤ 10 % step after, the step after a
  short current week); the 50-min cap with allowed weekdays; the API (preview saves nothing,
  PUT applies decisions, add onto a blocked day refused); a mocked COROS removal of pushed
  sessions on blocked days; and (golden) `week_plan` on the athlete's data.
- `backend/tests/test_equivalence.py`: Langmuir descent, recovery of known Naismith parameters,
  the LOO harness (exact on noise-free data, error with noise, nested method choice), EP
  fallback, hike two-parameter fit, flat speed regression, design as the inverse, the
  grade-cost hook, the API, and (golden) the backtest on the athlete's own activities.
- `backend/tests/test_coros_workouts.py`: step building, program payload, push / replace /
  remove against a fake Training Hub.
- `backend/tests/test_quality_gate.py`: the gate prefs (round trip, validation, not `active`);
  every mode with and without a measured AeT (auto → none / ua_gap + friel, stale AeT, default
  LTHR, forced ua_gap / friel / xu / plateau / weeks / none); forced mode with missing data
  (watch, fallback, never locked); guardrails (intensity, power share, ramp 5 / 7, step 10 / 20 %,
  TSB); the dose table, hold, fade and the recovery fartlek; dose sessions through the COROS
  step builder; 1-minute rep counting and the dose history; `Status.i_gate` + `week_plan` on a
  fake dataset; the projection's per-week `weeks` unlock and dose advance; the AeT analysis
  bands and refusals (short, hot, fast finish, hills); `latest_aet_test`, the review card's
  apply action; the AeT session's COROS steps and payload (nothing sent); the due cadence; the
  apply flow on a **temp** plan (the real plan.json untouched).

## Domain Model

### Bounded Context
- **Context Name**: TrainingOverview（訓練總覽）
- **Domain Layer**: Core Domain
- **Parent Module**: N/A (consumes `wko5-engine`, the Status engine and `workout_review`; pushes to COROS through the `coros-sync` client)

### Ubiquitous Language
| Term | Definition |
|------|-----------|
| 移動時間 (moving time) | WKO5 `movingduration`; the only volume unit here |
| category | Colour group of a workout (路跑 / 越野跑 / 登山健行 / 騎車 / 肌力 / 走路 / 其他) |
| endurance session | road, trail, hike or bike workout |
| EP / effort km | km + gain/100 |
| period / bucket | a Monday-based week, a calendar month or year |
| 平常 (typical) | mean of the earlier buckets shown |
| intensity split | moving HR time low < AeT, mid AeT–LTHR, high ≥ LTHR |
| PMC | CTL (42-day), ATL (7-day), TSB (yesterday's CTL − ATL) over all sports |
| ramp | CTL change per week |
| recovery week | reduced week triggered by fatigue (TSB) or three building weeks |
| quality session | ≥ 10 min at ≥ LTHR or ≥ 0.95 CP |
| 間歇門檻 / quality gate | base phase: the method (`plan.prefs.quality_gate`) unlocks, locks or is missing data (→ guardrails); guardrails decide this week; outside base: intensity and drift not bad |
| guardrails | low-intensity ≥ 75 %, CTL ramp < 5 (5–7 sub-threshold only), volume step ≤ 10 % (≤ 20 % holds), 3:1 fartlek, TSB, 48 h spacing |
| dose step | the next row of the 6-week table (5×1′ … 4×4′, then 3×8′ / 4×8′), one per interval session done in 8 weeks |
| drift streak | legacy only: the removed, unsourced 「連續 3 次 < 5%」 rule |
| 閾值下 N×8 | base-phase sub-threshold interval (88–95 % CP), 3×8 first, one rep fewer after a faded one |
| projection | the tl() recurrence continued with planned daily TSS; on the page, from the stored plan |
| stored plan | the `plan_sessions` table — the source of truth once generated |
| auto / custom session | generated by the planner vs added by the athlete |
| edited | a session the athlete changed; regeneration never overwrites it |
| tombstone | a deleted auto session kept as `state = deleted` so its `gen_key` is not regenerated |
| superseded | an edited hard session dropped because its week became a rest week |
| missed | an active past session with no matching activity, within the synced-data coverage |
| reconcile | refresh the stored plan from activities and a regenerated plan (rules 1–6) |
| horizon | the last day planned: phase end, ≥ 2 weeks, ≤ 8 weeks |
| provisional | a projected week beyond next week; recalculated as weeks arrive |
| push scope | day, week or phase range sent to COROS |
| fingerprint | SHA-256 of day + COROS payload; unchanged → not re-pushed |
| 課表偏好 | the athlete's plan preferences (`plan.prefs.*`); defaults = the original planner |
| 可練日 / rest day | a weekday allowed for runs; unchecked = rest day |
| cap (soft / hard) | per-session time limit; soft moves excess to the long day, hard drops it with a note |
| same-load conversion | redesigning a session on other terrain at the same time and so the same TSS |
| 爬升比例 | climb density, m of gain per km |
| 推估 | a conversion whose terrain backtests above 15 % MAPE or has too few samples |
| 不排課日期 / blackout | a one-off date range with nothing planned (`plan.blackouts`); not the weekly 可練日 |
| lost day | a blocked day that was available for training (allowed weekday, no workout logged) |
| conflict | an edited / custom session on a blocked day, waiting for the user's move / delete |

### Domain Events
None as explicit events. State transitions of a stored session (active → done / missed /
deleted / superseded) are returned as reconcile `changes` (`backend/engine/reconcile.py:74`).

## Change History

| Date | Source | Feature SRS | Summary |
|------|--------|-------------|---------|
| 2026-09-30 | code-sync | N/A | Created from brownfield analysis — all-sport home page: status, week/month/year totals, combined PMC with projection, rule-based weekly plan |
| 2026-09-30 | code-sync | N/A | Stored editable plan (plan_store / reconcile / projection, /plan/* endpoints), COROS push by day/week/phase via coros_plan_push, bars + PMC projection from the stored plan, 總覽 page with AeT/CP glossary and sources removed, drift-streak quality gate and CP-test delta in status |
| 2026-09-30 | bugfix | N/A | Spec-sync fixes: projection quality gate per projected week (week_plan returns `quality_gate`), athlete ATL constant, sessions without a day; past-week push scope is a 400; test-only push_week / remove_week / week_status removed; last_quality includes hikes |
| 2026-09-30 | feature | N/A | 課表偏好 (plan_prefs.py, `plan.prefs.*`, /plan/prefs, ⚙ panel + reconcile preview, notes on the 課表 page) applied in week_plan / projection / COROS HR intervals; same-load terrain conversion (equivalence.py, /plan/equivalence, dialog slider / locks) with LOO backtest; plan_sessions terrain / distance_km / climb_m; stale overview.html anchors refreshed |
| 2026-10-01 | feature | N/A | 間歇門檻 (quality_gate.py, `plan.prefs.quality_gate` / `_weeks`; design docs/research/aerobic-base-readiness.md): 7 modes, guardrails, the 6-week dose table, recovery-week fartlek, forced-mode fallback (自訂), i_gate / informational i_drift, per-week projection; AeT drift test (aet_test.py: due cadence, session, COROS steps, UA bands, 「套用這次的 AeT」 on the review card and 測試 card, apply-estimate `date`); prefs chips with fixed-position `?` hover; the 「連續 3 次」 rule and UA misattributions removed |
| 2026-10-01 | feature | N/A | CP 測試方式 (`plan.prefs.cp_test_protocol`, quick default / standard / race; cp_protocols.py): per-protocol test session with `protocol` (column + migration, reconcile field), race = a 還缺什麼 note instead of a session, protocol-specific cap note and COROS steps (all-out bouts open), same-method comparison in i_testing, 測試 card apply button |
| 2026-10-01 | feature | docs/research/heat-acclimation.md | 熱適應: `i_heat` (S, doses, HRC, race-day S), 熱適應課 in week_plan / project_weeks (heat_plan.py: induction / maintenance, ≥ 60 min cap exemption `NOTE_HEAT`, hard cap → 40 min + bath, methods), `heat_passive` (side kind, TSS 0, never pushed, ticked = a dose), `plan.prefs.heat` / `heat_method` (not shaping), COROS heat-run steps, Event.heat, 課表 page 熱 tag + prefs block, 總覽 heat card |
| 2026-10-01 | bugfix | N/A | Thresholds never apply backwards: `Plan.threshold_on` returns None before a row's date (the first CP 220 / LTHR 160 row had leaked into every earlier date); past days use WKO5's dated settings; today's values unchanged |
| 2026-09-30 | feature | N/A | 不排課日期 (blackouts.py, `plan.blackouts`, /plan/blackouts + preview): never placed on a blocked day, hours × kept share with a week note, ≤ 10 % step from what was actually done after it, reconcile rule 6 with move / delete decisions for edited sessions, pushed copies on blocked days removed from COROS; 課表 page hatch + label chip, drag / Shift-click / ⋯ menu, preview before applying; shifted anchors refreshed |
| 2026-10-01 | feat/auto-replan | N/A | Adaptive plan: `adapt.py` (missed easy / quality / long, easy run too hard, fatigue guard) applied before reconcile on every path. The interval progression state machine (`interval_outcome` / `dose_step`) replaces the 5 % fade rule. Actual TSS for done sessions. Kind `notice`. Automatic run after sync with hold / approve / reject / 復原 and the `plan_change_log` table. Details in plan-auto.spec.md |
| 2026-10-01 | feat/drift-v2-planning | docs/research/drift-algorithm.md, unsourced-rules.md, detraining.md | `i_drift` = 6-run mean ± SE; season drift charts add 「6 次平均」 ± SE (`drift_avg()`); guardrail sources (ramp 5/8 Friel, volume step Nielsen/Damsted, TSB Friel/TP); AeT valid by the aggregate (B3) and the test by reason; ladder Z3 → Z5 with the Zone 5 lifecycle (base_check); AeT test protocols (`plan.prefs.aet_test_protocol`, 徐國峰 90′ standard on the weekend, UA 40′ backup); re-entry block after breaks ≥ 6 days replaces `blackouts.step_cap`; easy targets from the recent EF (推估) |
| 2026-10-01 | feat/z5-gate-viz | N/A | Zone 5 opening process: `quality_gate.z5_history` replays the lifecycle day by day through the same `_z5` evaluate() uses (`ua_gap_method`, `_break_on` shared; `base_check.replay_memo` per-run memo inside a replay only); `z5_card` + GET `/overview/z5` card on 總覽; `z5gate` panel in 基礎期; auto-mode hover text corrected to 2/3 × 3 weeks and re-entry ≥ 6 days |
| 2026-10-01 | feat/z5-unlock-simplify | docs/research/aerobic-base-readiness.md | Zone 5 opens on one of three tests (90-min test, UA gap, Friel drift); the old `xu_signals` mode removed (stored value reads as auto); the late long-run check no longer pauses Zone 5 (kept as the post-break drift check); `z5_card` gives one 三選一 step, `steps` and the 「還缺什麼」 `next` line, shared with the 基礎期 chart (`z5_progress`); the chart redrawn as tracker + simple history |
| 2026-10-02 | feat/z5-flow-ui | owner request | 5 區開放流程 redrawn as a quest-style stage flow (`quality_gate.z5_flow`, `z5_card.flow`; `static/z5flow.js` shared by the 總覽 card and the 基礎期 panel): 有氧基礎 → 3 區階梯 → 有氧基礎確認 → 5 區解鎖 → 5 區階梯 with checklists and a 「你現在在這裡，下一步」 line; the 3-step tracker, the state band / marker / weekly-bar chart removed; presentation only (no gate logic change); UI chrome in `common.z5flow.*`, flow strings wrapped in `_()` |
| 2026-10-01 | feat/drift-two-tier | N/A | `i_drift` shows the drift's 參考 tier (30–40 min after the warm-up, 推估), labelled with a hover, BAD only on strict runs; AeT test length by `cap_weekday` (80′ standard, or UA's 50′ minimum under a cap < 80) with the reason in the detail, new detail text (treadmill + fan, note the temperature, Evoke early abort), placed on a weekday by `aet_test.pick_day` in all three placement paths (`plan.prefs.aet_test_days` weekday / any), done only by a titled ≥ 48′ or untitled ≥ 55′ road run |
| 2026-10-02 | feat/heat-bands | user-approved | `i_drift` compares within one temperature band (< 25 / 25–28 / > 28 °C / 溫度不明; `drift_agg.pick_band`), chip 「· 🌡 …」 in the text, `extra.bands`; never BAD in a heat band; Friel / 徐國峰 gate methods count heat runs (pass unlocks, fail 「可能是熱造成的」); AeT test text keeps 「氣溫 25 °C 以下時開始」 as advice |
| 2026-10-02 | feat/session-classifier | docs/research/vo2max-session-detection.md, user-approved | Weekly Zone 5 slot ticked only by a 「Z5 間歇」 run; done hard days (Z5 / Z3 / 高強度長跑 / CP test) keep the next interval 48 h away; activity rows carry `session` (label + dashicon). Power zones are Palladino everywhere: interval_library classes 3A 88–95 / 3B 95–101 / Z4 101–106 / Z5 ≥ 106 % CP by band middle (was Z5 ≥ 102 %), the editor's 5 區 time ≥ 106 %, time-in-zone charts / zone APIs Palladino 10 zones (Coggan / Stryd sets removed; iLevels kept as the WKO5 cross-check) |
| 2026-10-04 | feat/sp-34-35-schedule | SP-34, SP-35 | 課表: ⟳ 從 COROS 抓活動 button (資料來源 only, shared `syncrun.js`, reload + one re-poll for 自動調整); push button renamed 推送到手錶; push status drawn as a watch (neutral when up to date, coloured only for 需更新／失敗), ✓ reserved for 完成, legend split into 完成 / 手錶 groups |
