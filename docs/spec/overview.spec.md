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
bars and the load projection, and can be pushed to the watch (COROS Training Hub, through the
workout-sync provider interface) by day, week or phase.
The generator follows the athlete's **課表偏好** (training-plan preferences: allowed days, per-
session time caps, counts, terrain) and one-off **不排課日期** (blackout days: a holiday or trip on
which nothing is planned), and the session dialog can convert a session to another terrain at
the **same load** from the athlete's own speed history. What the planner only *suggests* — a due
CP / AeT test, a B2B weekend, the race simulation — goes to a floating suggestion box and is
stored only once the athlete picks a day. The template follows the **主要訓練項目** (越野跑 or
路跑／馬拉松). How closely the athlete followed the plan is shown per session and on the
**課表統計** page (planned vs actual).

Volume is **moving time**, never recorded time — a multi-day 百岳 file records
the nights too (a two-day trip can hold only a few hours of walking).

## Architecture

```
 Dataset (wko5-engine) ──┬─ Status engine (status.py) ── indicators, actions, phase, goals
                         ├─ overview.summary()  ── period buckets + current-period detail
                         ├─ overview.pmc()      ── CTL / ATL / TSB per day
                         ├─ overview.week_plan() ─ target, sessions by day, projection
                         │        ▲  plan_prefs.shape() / place()  ◄── user_settings plan.prefs.*
                         │        ▲  blackouts (blocked days, hours, 休息日) ◄── user_settings plan.blackouts
                         │        ▲  primary_sport · b2b · specific_phase · steep_hill · heat_plan · reentry
                         └─ equivalence.summary() ── easy-HR speed model + LOO backtest
                                      │
                         projection.project_weeks() ── later weeks up to the horizon (same prefs)
                                      │
                         reconcile.reconcile() (+ plan_match) ⇄ plan_store (table plan_sessions)
                                      │                     │
                                      │          workout_targets (COROS provider → coros_workouts)
                                      │                     ⇄ table coros_plan_push ⇄ COROS
                                      │          compliance (planned vs actual)
                /api/v1/overview/* + /api/v1/overview/plan/* ── overview.html, schedule.html (課表),
                                                                compliance.html (課表統計)
```

| Layer | Responsibility | Entry point |
|---|---|---|
| Categories / helpers | Workout → category, moving time, effort km | `backend/engine/overview.py:72` |
| Periods | Week (Monday) / month / year buckets and totals | `backend/engine/overview.py:228` |
| PMC | Same `tl()` recurrence as the chart expressions `ctl` / `atl` / `tsb` | `backend/engine/overview.py:290` |
| Week plan | Volume target, session template, done-matching, day placement, projection | `backend/engine/overview.py:821` |
| Plan preferences | 課表偏好: shape the template (counts, caps, terrain), place on allowed / preferred days | `backend/engine/plan_prefs.py:465`, `backend/engine/plan_prefs.py:674` |
| Blackout days | 不排課日期 / 休息日: validation, blocked days, lost-day volume, move-to for stored sessions | `backend/engine/blackouts.py:77`, `backend/engine/blackouts.py:223` |
| Same-load conversion | Easy-HR time model per terrain, design km / climb for a time, LOO backtest | `backend/engine/equivalence.py:249`, `backend/engine/equivalence.py:315` |
| Multi-week projection | Rolls the week-plan rules forward to the horizon | `backend/engine/projection.py:358` |
| Reconcile | Pure rules: stored plan vs regenerated weeks vs activities | `backend/engine/reconcile.py:91` |
| Plan store | Table I/O, edits, tombstones, expired deletes, manual link, stored-plan summary | `backend/engine/plan_store.py:123` |
| Workout-sync provider | The active push target (`plan.push.provider`, default COROS; Garmin / intervals.icu are disabled stubs) | `backend/sync/workout_targets/__init__.py:18` |
| COROS push | Session → structured COROS workout, idempotent push / remove | `backend/sync/coros_workouts.py:967` |
| Compliance | Planned vs actual per session / week, the 課表統計 dashboard | `backend/engine/compliance.py:44`, `backend/engine/compliance.py:191` |
| API | Memoised Status, the endpoints, the pages | `backend/api/overview.py:42`, `backend/api/plan_sessions.py:38` |

The dataset is the chart pages' shared instance (`backend/api/wko5views.py` `_dataset()`), so the
engine config / parity mode is the same everywhere.

## Categories

`category()` (`backend/engine/overview.py:72`): 路跑 road (run, not trail; incl. treadmill),
越野跑 trail (tag `runningtrail` or type *trail running*), 登山健行 hike (tags `hiking` /
`mountaineering`), 騎車 bike, 肌力 strength, 走路 walk, 其他 other. Colours are the validated
categorical palette in fixed slot order (`backend/engine/overview.py:36`).

- **Endurance** = road, trail, hike, bike (intensity split, longest session, quality-session
  matching). **Foot** = road, trail, hike (effort km).
- **Effort km (EP)** = km + gain/100, the 健行筆記 / ITRA convention (`backend/engine/overview.py:109`).

## Periods (`summary`)

- `summary(ds, unit, anchor, n)` returns `n` consecutive buckets ending with the one containing
  `anchor`; the last one gets detail (`backend/engine/overview.py:228`).
- Bucket totals: sessions, moving s, km, climbing, descending, TSS, EP, days trained, and the
  same per category (`backend/engine/overview.py:188`).
- Detail: `elapsed_share` while the period is still running; intensity split; longest session,
  biggest climb, hardest (TSS) session; deltas vs the previous bucket and vs the mean of the
  earlier buckets ("平常"); activity list.
- **Intensity split** (`backend/engine/overview.py:211`): moving samples only
  (`speed > 1.6 km/h`, WKO5's moving threshold, or no speed channel), endurance sessions only;
  low < AeT, mid AeT–LTHR, high ≥ LTHR (`backend/engine/overview.py:57`). The per-workout
  aggregates are disk-cached by the evaluator.

## PMC

`pmc()` (`backend/engine/overview.py:290`) evaluates `ctl`, `atl`, `tsb` with the evaluator
(full history, constants from the athlete: 42 / 7) and daily TSS with the `tl()` input rule
0 ≤ x ≤ 5000 (`backend/engine/overview.py:280`). TSB is yesterday's CTL − ATL.
`project()` (`backend/engine/overview.py:306`) continues the recurrence with planned daily TSS.

The stored-plan projection is `plan_store.plan_summary()`
(`backend/engine/plan_store.py:643`): today's CTL / ATL continued with the TSS of the **stored**
active sessions on each day after today, up to the horizon, so edits change it. It replaces
`week_plan()`'s own week targets, Sunday CTL and next-Monday TSB once the stored plan has loaded
(`paintPlanLoad`, `backend/static/overview.html:696`). The overview's PMC chart itself shows only
the last 90 days (CTL / ATL lines, TSB bars in the PMC's Form% colours; `loadPmc`,
`backend/static/overview.html:903`) — no projection line (2026-10-03).

## Week plan (`week_plan`)

Inputs: the computed `Status` (phase kind, goals, indicators), the last 8 complete weeks of
moving hours / TSS, today's CTL / ATL / TSB, the 課表偏好 `prefs`, the 不排課日期, the accepted
B2B weekends, the race calculator (for the 專項期 target) and the 主要訓練項目 `sport`
(`backend/engine/overview.py:821`). `prefs=None` or the defaults run exactly the rules below.

**Volume target**
1. Base / specific: the weekly TSS that raises CTL by the phase goal (base max(2, 5 % of CTL),
   specific max(2.5, 7 %) per week, 推估 — SP-63; `load_guard.ramp_goal`,
   `backend/engine/load_guard.py:222`) — `7·(CTL₀ + Δ/(1 − (1 − 1/42)⁷))` —
   converted to hours with the athlete's TSS per hour over 6 weeks.
2. Capped at `max(1.10 × ref, ref + 0.5 h)`, ref = max(4-week mean, last week) (UA 10 %).
   Floored at the 4-week mean (hold).
3. Guards: TSB < −30 → recovery week (60 % of the 4-week mean); TSB < −20 → hold; three
   building weeks in a row → recovery week (65 % of their mean, 3:1 cycle). An accepted B2B's
   own TSB drop is exempt (`B2B.tsb_exempt`, `backend/engine/overview.py:903`).
4. Taper: 50 % of the 6-week mean (40 % in the last 7 days to the A event); event week 30 %;
   recovery 50 %; transition 65 %.
5. A custom weekly-hours preference only lowers the result (`backend/engine/overview.py:928`).
6. A break ≥ 6 days without running — a 不排課日期 range or simply no runs — gives the re-entry
   block instead (`reentry.find`, `backend/engine/overview.py:936`; Daniels; plan-auto.spec.md);
   the week after the block goes back to the pre-break volume. Open injuries add a week note
   (`injuries.week_notes`, `backend/engine/overview.py:941`).
7. 不排課日期 (`blackouts`, `backend/engine/overview.py:956`): this week's lost days scale the
   target (see 不排課日期 below).

**Sessions** (dataclass `Session`, `backend/engine/overview.py:360`; `terrain`, `distance_km`,
`climb_m` added for the preferences / conversion, `protocol` for tests, `heat`, and the
interval-library `variant_*` fields — plan-auto.spec.md §Interval library)
- Base / specific (not a recovery week): one **LSD** (the long run's label since 2026-10-03, was
  長時間輕鬆; 30 % of the week, ≥ 60 min, ≤ 1.15 × the longest of the last 28 days; specific:
  toward 70 % of the goal event's hours, ≥ 90 min, or the 專項期 race target below), terrain from
  the goal's climb density (「LSD（山路）」 with a mountain goal); 路跑 uses `road_long_session`
  (`backend/engine/overview.py:778`). Then one of (`backend/engine/overview.py:1115`):
  1. specific, Zone 5 not confirmed → the **Zone 3 ladder** (uphill versions allowed) instead
     of the 5×4′ hill set (台灣教練: Zone 3 first);
  2. specific, 路跑 → 「閾值節奏 2×15 分（平路）」 (`ROAD_SPECIFIC_Q`,
     `backend/engine/overview.py:801`); specific, trail → uphill intervals 5×4';
  3. base → the **間歇門檻**'s dose step as an interval-library variant fitted to the weekday
     cap (`_gate_session`, `backend/engine/overview.py:469`, `backend/engine/overview.py:1130`;
     see below).
  - Base **recovery week** (3:1): the gate's 「恢復週 fartlek 4×1 分」 instead of intervals
    (Palladino, `backend/engine/overview.py:1123`).
- **Tests are suggested, never planned** (2026-10-01/02): a due CP test (`testing` bad / watch,
  `extra.cp_due`, A event > 10 days away, not inside a re-entry block) and a due AeT test
  (`aet_test.due`, for a reason only) become `test_suggestions` (`backend/engine/overview.py:1406`)
  — the floating suggestion box and the 課表 context menu's 排入測試 let the athlete pick a day.
  The CP session comes from the 課表偏好 CP 測試方式 via `cp_protocols.session_for`
  (`backend/engine/cp_protocols.py:128`), read even when the other preferences are the defaults:
  `quick` 「CP 測試 20 分全力」 37 min, TSS 45 (warm-up 12 → 20′ all-out → cool-down 5);
  `standard` 「CP 測試 12 分 + 3 分」 70 min, TSS 65 (15 → 12′ → rest 30 → 3′ → 10, long bout
  first); `race` → no suggestion, the testing action 「用 5–10 K 比賽或計時跑代替 CP 測試」 goes to
  還缺什麼. The AeT test follows `plan.prefs.aet_test_protocol` (`replaces_long` for 徐國峰's 90′).
  A scheduled test carries `protocol` (Session field, `plan_sessions.protocol` column + migration,
  `reconcile.FIELDS`, `plan_store.to_dict` / `push_dict`).
- **間歇門檻 — quality gate** (`backend/engine/quality_gate.py`; design
  `docs/research/aerobic-base-readiness.md` §4–§5). The old 「連續 3 次輕鬆路跑飄移 < 5%」
  rule had no source and is gone (`STREAK_NEED` is legacy only,
  `backend/engine/workout_review.py:168`). `week_plan` reads status `i_gate`'s dict
  (`backend/engine/overview.py:1036`) and asks `week_decision`
  (`backend/engine/quality_gate.py:2097`) for this week:
  - **Method** (`plan.prefs.quality_gate`, `evaluate`, `backend/engine/quality_gate.py:978`):
    `auto` → `ua_gap` + `friel_drift` when the plan has a measured AeT row that is **valid**
    (B3, `unsourced-rules.md`: the aggregated drift estimate's SE ≤ 3 bpm and no shift > 5 bpm
    over the last 6 points — `drift_agg.aet_validity`, 推估; no fixed 16-week expiry; stale after
    a break ≥ 4 weeks) (`aet_info`, `backend/engine/quality_gate.py:245`) and LTHR is not WKO5's default
    (`lthr_info`, `backend/engine/quality_gate.py:283`), else `none`. `ua_gap`: LTHR / AeT − 1
    ≤ 10 %; `friel_drift`: one run in 8 weeks, avg HR AeT−5…AeT+3, ≥ 70 min, fair drift < 5 %
    (`friel_check`, `backend/engine/quality_gate.py:364`); `xu_drift`: flat ≥ 90-min run,
    (HR@90′ − HR@10′) / HR@10′ < 10 % (`xu_drift_of` / `xu_check`,
    `backend/engine/quality_gate.py:412`, `backend/engine/quality_gate.py:412`); `plateau`: ≥ 8
    base weeks and EF change < +2 %; `weeks`: > N base weeks (evaluated per projected Monday);
    `none`: guardrails only. States: unlocked / locked (data there, criterion not met) /
    missing. **Forced mode with missing data → `fallback`**: i_gate WATCH with the reason and
    the guardrail plan (our own choice: never a permanent lock).
  - **Guardrails** (`guard`, `backend/engine/quality_gate.py:641`), base phase, every mode:
    low-intensity time share < 75 % (or run power < 80 % CP share < 75 %) → no Zone 5, Zone 3 goes
    on with a 「輕鬆跑心率偏高」 warning note (SP-31: 75 % is the floor, the base phase's ≥ 90 % a
    target; the AeT is often estimated, climbs inflate HR) — with an untested AeT in effect a
    warning for Zone 5 too (SP-39, `guard(aet_tested=False)`); CTL ramp ≥ max(3, 10 % CTL₋₇) →
    threshold only, ≥ min(10, max(5, 15 % CTL₋₇)) → none (Friel as a share of CTL, 推估; not in the
    first 28 days of data — `backend/engine/load_guard.py:163`); last week's running-time step
    against max(the week before, 4-week mean) > 20 % → none (Nielsen 2014, Damsted 2019), 10–20 % →
    hold the dose (推估) — not the week after a 3–5-day break without a run, which gets an info note
    instead (`src: "volume"`, plan-auto.spec.md); TSB −30…−20 → hold (Friel / TrainingPeaks). Projected weeks keep only the
    intensity block (Zone 5 only).
  - **Two gates, two tracks** (SP-31): Zone 3 once its gate is open (`z3_gate`,
    `backend/engine/quality_gate.py:1217`: 4 complete weeks with ≥ 3 runs and no 7-day gap —
    sticky, a ≥ 21-day break re-locks —, the 90-min test, or a measured UA gap; all 推估 but the
    tests) and the guardrails pass; Zone 5 is an independent gate (SP-39, `quality_gate.z5_track`
    = `gate["z5_gate"]`, the flag week_decision and the card share): a measured AeT
    (`gate["z5"]`, `base_check.z5_status`: a tested AeT + a measured LTHR with the UA gap ≤ 10 %,
    or the Friel drift < 5 % near the tested AeT — the 90-min test is not an AeT test; no
    stable-weekly-volume precondition since 2026-10-03; maintenance and re-entry rules in
    plan-auto.spec.md) and the soft 「近 6 週 ≥ 2 堂 3 區」 (推估) or the Zone 5 track under way.
  - **Dose** (`Z3` / `CRUISE` / `Z5`, `z3_spec` / `z5_spec`; `dose_tracks` → `dose_step` per
    track): each track's step = its 達標 sessions in the last 8 weeks (`dose_history` rows carry
    `track`): Zone 3 2×15′ → 3×12′ → 2×20′ → 1×30′ at 88–95 % CP (`interval_library` a1–a4), then
    A3 / A4 / T+; Zone 5 5×2′ → 4×3′ → 5×3′ → 4×4′, then V3 / V4. Over 10 % of the week (5 % the
    first time) the Zone 3 rung becomes its 巡航版 T1–T3 (the old z3a–z3c), which still counts;
    Zone 3 + Zone 5 ≤ 20 % of the week (`quality_sessions`, `backend/engine/overview.py:566`).
    Weekly: `week_decision(..., n)` (`backend/engine/quality_gate.py:2097`) — 課表偏好 2 a week =
    one of each (`quality_per_week`, `backend/engine/overview.py:748`), 1 a week with both open
    alternates 1:1 (A race road ≤ 10 km) or 2:1 (`track_ratio`, `backend/engine/quality_gate.py:1324`).
    Rung details in plan-auto.spec.md.
    The step moves by the progression state machine (`interval_outcome` / `dose_step`,
    plan-auto.spec.md): 達標 forward, 邊界 / 無法判定 repeat, 未適應 rest +1 min then back one,
    first rep short = target −5 %.
  - **停訓後恢復期** (`reentry.find`, mode `reentry`): a break ≥ 6 days without running — a
    blackout range or from the runs — gives Daniels' block (week hours = the 4 weeks before the
    break × the block's %; no quality, no strides, no test inside; long-run cap; targets ×
    FVDOT). It replaces `blackouts.step_cap` (plan-auto.spec.md).
  - **AeT test** (`aet_test.due(today, kind, base_start, gate["aet_test_reason"], last)`): only
    for a reason (B3 / the Z5 lifecycle), its protocol from `plan.prefs.aet_test_protocol`
    (auto = 徐國峰 90′ on the weekend in place of the long run, UA 40′ backup); a suggestion, not
    a session (above). Gate session text keeps the COROS / trim tokens
    (`session`, `backend/engine/quality_gate.py:2289`); the detail prefix names the rule
    (`prefix`, `backend/engine/quality_gate.py:2325`). In guardrail mode `plan_prefs.shape`
    gets `quality_cap=1` (`backend/engine/overview.py:1165`).
  - 專項期: the same two-track pick; road Zone 3 = 閾值節奏 2×15′ (`ROAD_SPECIFIC_Q`), trail Zone 5 =
    爬坡間歇 5×4′, else the ladder; drift bad → none, intensity bad → no Zone 5; this week's CTL ramp
    at the block line / volume step > 20 % → none and at the watch line → threshold only, on both
    tracks (owner 2026-10-04).
  - **Why no Zone 3** (SP-31): `week_decision`'s `z3_note` (the gate with its progress, a
    guardrail's verdict, 「本週輪到 5 區」, the recovery week) is a week note (`src: z3`); the
    low-share warning is `src: intensity`, the Zone 3 / week-total caps `src: z3` / `quality_share`.
  - Returned as `quality_gate` (the gate dict + `levels`, `allowed`, `this_week`,
    `this_week_tracks`, `quality_n`, `aet_test`) for the projection
    (`backend/engine/overview.py:1460`).
- Taper: one session by the two-track pick — Zone 3 節奏 2×8′ (88–95 % CP), Zone 5 or no track
  open the short intensity 4×3'. Event week: the race.
- Strength ×2 in base / transition / recovery or when the `strength` indicator is bad / watch,
  else ×1 (not counted in the hours).
- Easy runs fill the remaining minutes in 40–60 min sessions; in base the first one carries
  8×10 s hill strides.
- Targets per session come from `zones.training_targets` (CP / LTHR / AeT, estimate-aware),
  formatted by `_targets` (`backend/engine/overview.py:435`). The easy-run cap is the
  **課表心率區間**'s Z2 top (設定 → 心率: COROS % LTHR / % HRR / % HRmax, `engine/hr_profile.py`)
  unless an AeT was measured; session texts call it 「輕鬆跑上限」, with 「（實測 AeT）」 only
  when measured (2026-10-03, `backend/engine/overview.py:1001`), and a week note says which model
  set it when it isn't LTHR.
- A season-plan threshold row applies from its own date on, never to earlier days
  (`planning.Plan.threshold_on`, `backend/engine/planning.py:291`, fixed 2026-10-01). Before the
  first row, `Dataset.setting` / `cp` / `aethr` fall back to WKO5's dated settings (runthr, the
  current mFTP snapshot, 0.89 × LTHR). Today's thresholds come from the last run moved to today
  (`zones._on_day`, `backend/engine/zones.py:188`; also `status._aet_now` and the LTHR estimate's
  CP), so a test dated after WKO5's last run still applies. Today's CP 220 / LTHR 160 / AeT 142,
  the targets and the zone bounds are unchanged. Indicators that judge each past run with its
  own-date AeT do move (2026-10-01, WKO5 source): 強度分配 mid share 26 % → 35 % (still bad);
  效率 good 「進步」 → info 「持平」; drift-eligible easy runs 1 → 6 (still too few).
  Past days change back to their values before the first threshold row: hrTSS of past runs
  uses WKO5's dated LTHR instead of the row's (365-day TSS −0.7 %, CTL today −0.6 % in the
  evaluator). `workout_review` again labels about three dozen past hard 5 km runs `test_cp` by
  power pattern against the WKO5 mFTP snapshot (≈ 80 % of CP). None of them falls in the
  28 / 42-day windows the overview reads.
- **Where the rows are edited** (2026-10-04, SP-46): the settings page's 「閾值測試紀錄（LTHR／AeT／CP）」
  section (`backend/static/settings.html:192`, between 心率 and 資料同步) is the one editor of
  `plan.thresholds`: the dated table (LTHR / AeT / CP / note), the LTHR / AeT 自動估算 cards with
  套用, the WKO5 settings line, 怎麼測 and the Palladino power-zone table. It reads
  `GET /api/v1/plan/thresholds` and saves with the whole-table `PUT` as before. 最大心率 is edited
  only in 設定 → 心率 (`hr-profile`): the table has no max-HR column, hides rows that hold only
  `mhr` / `rhr` but sends them back unchanged, and 移除 on a row that also holds them clears only its
  LTHR / AeT / CP; a 心率 save reloads the table so a stale copy never overwrites it. The 賽事周期
  page (`backend/static/plan.html:159`) shows a read-only line — what is in effect, the latest test
  date and count — with 「到設定修改」 (`/api/v1/wko5/settings#thresholds`); the status action for a
  default LTHR points to 設定 too (`backend/engine/status.py:939`). No data-model change.
- With active preferences the template is then shaped by `plan_prefs.shape()`
  (`backend/engine/overview.py:1166`; see 課表偏好 below).

**Session decorators** (2026-10-02/03; each a small hook module, also run per projected week):
- **主要訓練項目** (`engine/primary_sport.py`, setting `athlete.primary_sport` auto / trail / road;
  auto = the next A event's type, else trail + hike ≥ 25 % of 12 weeks' foot time, 推估): 路跑 =
  no B2B, no steep-hill walk, no mountain long run / uphill interval versions; the 專項期 LSD
  carries a marathon-pace segment (Pfitzinger / Daniels; 40 % of the run within 20–75 min, 推估;
  the A road race's goal pace when ≥ 30 km, else threshold pace × 1.04–1.08), and base strides
  are 「加速跑 6×20 秒」 (`ROAD_STRIDES`, `backend/engine/overview.py:816`).
- **專項期** (`engine/specific_phase.py`): the LSD follows the next A race's コース定數 (the race
  calculator's single-day target; a 推估 share per week from week 10 to 3 before the race, still
  ≤ +15 % over the 4-week longest); the race GPX's longest climb becomes one 長爬坡反覆 easy run;
  a race simulation 4–3 weeks out is a suggestion (`race_sim_suggestion`).
- **B2B 連續長天** (`engine/b2b.py`): a due B2B weekend is only a suggestion (課表偏好 `b2b`
  off = never); an accepted one is stored as the user's two sessions (`plan.b2b.accepted`) and
  the rest of the week is planned around it (day 2 out of the easy minutes, 4 easy days after).
- **陡坡健走（模擬負重）** (`engine/steep_hill.py`): before a 百岳 / multi-day trip, one weekday
  easy run of a 專項期 week becomes a 40–50 min steep walk at the Pandolf grade that costs what
  the pack would (no pack in training).
- **熱適應課** (`engine/heat_plan.py`): below.

**Done-matching** (the generated week; stored sessions are matched by `plan_match`, reconcile
rule 1): strength ← a strength workout; long (by id, so a long day of kind `hike` too,
`backend/engine/overview.py:1205`) ← an endurance session ≥ 80 % of the planned minutes; the AeT
test ← a road run ≥ 55 min (`backend/engine/overview.py:1210`); quality / test ← a session with
≥ 10 min at ≥ LTHR or ≥ 0.95 CP run power, or 60 % of the planned work for short reps
(`hard_need`, `backend/engine/quality_gate.py:2347`, `backend/engine/overview.py:1221`); a Zone 3
library variant ← its own time at ≥ 85 % CP (it never reaches 95 % CP);
a planned **Zone 5** session (library class Z5, or rung `z5*`; `quality_gate.is_z5_variant`)
← only a run classified 「Z5 間歇」 (`workout_review.classify` stimulus `z5`, owner 2026-10-02);
easy ← any other endurance session. Week activities and `done_by` rows carry `session`
(`overview.session_of`: type, label, stimulus, dashicon), shown on the 本週 tiles / 課表 chips.
**Done hard days** (Z5 / Z3 / 高強度長跑 / CP test, planned or not; `workout_review.HARD_TYPES`)
keep the remaining interval 48 h away (`plan_prefs.place(hard_done=…)` and the no-prefs path, `backend/engine/overview.py:1260`).

**Placement**: remaining days from today (tomorrow when something is already logged today)
to Sunday. The long session goes on the athlete's usual long-day weekday (mode over 12 weeks,
`backend/engine/overview.py:410`) or the last free day; quality ≥ 2 days from the long one;
easy on the next free days; strength on easy or free days, never the day before the long one.
Sessions that don't fit are reported as a note, not squeezed in. Active preferences place
with `plan_prefs.place()` instead (`backend/engine/overview.py:1266`). Blocked days are removed
from the candidate days first (`backend/engine/overview.py:1246`); when they leave a quality /
test session only a day next to the long one, it is dropped rather than stacked
(`backend/engine/overview.py:1311`). An accepted B2B keeps its own two days and the rest moves
around them (`B2B.place`, fixed).

**Output**: target / done / remaining (hours, TSS), the reasons (`why`), the rules cited,
8-week history, load now and at Sunday (CTL, ATL, next-Monday TSB, weekly ramp), the daily
projection, sessions with day / done state, thresholds and their sources, notes (data /
testing to-dos from the indicators; preference notes tagged `src: prefs`), the per-category
TSS / h (`tss_per_category`), the preferences applied and the week's lost days
(`blackout_days`), and the 主要訓練項目, the easy-cap label / HR model, the `test_suggestions`,
the re-entry block, the B2B state and suggestion, the steep-walk and 專項期 info and the race
simulation suggestion (`backend/engine/overview.py:1429`). Blackout notes are tagged
`src: blackout`.

## 課表偏好 — training-plan preferences (`plan_prefs.py`)

Stored as `user_settings` keys `plan.prefs.*` with per-key validation
(`backend/settings/repository.py:99`, `backend/settings/repository.py:352`) and cross-field
rules in `check()` (`backend/engine/plan_prefs.py:275`: one session type per preferred weekday,
runs ≤ allowed days, quality < runs, long cap ≥ weekday cap, valid gate / AeT / B2B values).
Read synchronously by `load()` (`backend/engine/plan_prefs.py:303`), which drops stored
preferred-weekday overlaps from before that rule (`drop_overlaps`; the first type keeps the day).
`Prefs()` (`backend/engine/plan_prefs.py:120`) is **inactive** (`active`,
`backend/engine/plan_prefs.py:169`) and every caller keeps its original code path, so the
defaults reproduce today's plan exactly.

| Setting | Key | Values (default) |
|---|---|---|
| 可練日 | `plan.prefs.days` | 7 bools Mon..Sun, unchecked = rest day (`null` = every day) |
| 長跑日 | `plan.prefs.long_day` | any weekday `mon`…`sun` / `auto` = the athlete's most frequent long day over 12 weeks (`auto`) |
| 偏好的星期 | `plan.prefs.pref_days`, `plan.prefs.pref_keep` | `{quality \| aet_test \| cp_test \| strides: [first, second]}` (`{}` = 自動, the planner picks); one type per weekday (the long run's is 長跑日). Breaks of the default rules (`day_conflicts`, `backend/engine/plan_prefs.py:605`: 48 h from the long run, the day after it, not an allowed day, the long-day cap, two Zone 5 days < 2 apart, AeT test on a weekend / next to a hard day) are shown live (`POST /prefs/conflicts`) and after saving; 照我的偏好 stores the code in `pref_keep` and the planner then keeps the day, else it moves the session with a note |
| 單次時間上限（平日） | `plan.prefs.cap_weekday` | 20–300 min (`null` = none) |
| 長跑日上限 | `plan.prefs.cap_long` | 20–600 min (`null` = 同平日) |
| 上限模式 | `plan.prefs.cap_mode` | `soft` 盡量不超過 / `hard` 絕對不超過 (`soft`) |
| 每週跑步次數 | `plan.prefs.runs_per_week` | 3–7 (`null` = auto) |
| 每週品質課 | `plan.prefs.quality_per_week` | 0–2 (`null` = auto, ≤ 1) |
| 每週肌力 | `plan.prefs.strength_per_week`, `plan.prefs.strength_days` | 0–3 (`null` = auto); weekdays 0–6, `[]` = with easy runs |
| 每週時數 | `plan.prefs.weekly_hours` | 1–40 h cap (`null` = CTL ramp rules) |
| 地形偏好 | `plan.prefs.terrain_easy` / `_long` / `_quality` | easy `road`/`trail`/`any`; long `road`/`trail`/`auto` (a stored `hike` reads as `trail` — 登山 is not a workout type; kind `hike` is labelled 越野跑); quality `flat`/`hill`/`any` |
| 目標依據 | `plan.prefs.target_basis` | `auto` (by session type: HR for easy / long / trail days, power for intervals and 3–8 % hill repeats) / `hr` / `power` (`auto`; `engine/target_policy.py`). The legacy 間歇目標 `plan.prefs.interval_target` = `hr` reads as `hr` |
| 間歇門檻 | `plan.prefs.quality_gate`, `plan.prefs.quality_gate_weeks` | `auto` / `ua_gap` / `friel_drift` / `xu_drift` / `plateau` / `weeks` / `none` (`auto`); weeks 2–16 (8). **Not part of `active`** (`GATE_FIELDS`, `backend/engine/plan_prefs.py:87`): read by status `i_gate`. Panel: a chip per mode, each with a `?` whose fixed-position popup (ported from the viewer's `.qtip`, appended inside the open dialog so the modal top layer and its scroll box never hide it) gives the source, the exact criterion, what to do and whether it runs on your data now (`GET /prefs` `gate_options` + `GET /prefs/gate`; `backend/static/schedule.html:761`, `backend/static/schedule.html:1958`, `backend/static/schedule.html:1995`) |
| CP 測試方式 | `plan.prefs.cp_test_protocol` | `quick` 約 37 分 / `standard` 約 70 分 / `race` 不另外排 (`quick`). **Not part of `active`**: it only changes the test session (`NOT_SHAPING`, `backend/engine/plan_prefs.py:95`). Panel: three chips, the details behind `?` (`backend/static/schedule.html:770`, `backend/static/schedule.html:1972`) |
| AeT 測試方式 | `plan.prefs.aet_test_protocol` | `auto` (徐國峰 90′ on the weekend LSD, UA 40′ backup) / `xu90` / `ua60` / `ua40` / `evoke60` / `friel` (`auto`). **Not part of `active`**. Protocols in plan-auto.spec.md |
| AeT 飄移測試 | `plan.prefs.aet_test_days` | `weekday` / `any` (`weekday`: weekends are often trail days). **Not part of `active`** (`NOT_SHAPING`): every placement path reads it (`aet_test.test_days` / `pick_day`): weekday = Mon–Fri in Tue-first order, ≥ 2 days from the long run and other hard days where possible, never the day after the long run unless nothing else; the 80′ standard test may fall back to a weekend day that isn't the long run's, the 50′ short one never; `any` = the interval rule. The test's **length** follows `cap_weekday` (`aet_test.variant_for`): no cap or ≥ 80 → 15′ + 60′ + 5′; < 80 → UA's minimum 10′ + 40′ (never shorter, exempt below 50). Panel: `#pf-aet` chips + AeT 排在 |
| 間歇暖身／緩和 | `plan.prefs.warmup_commute_min`, `plan.prefs.cooldown_min` | 0–30 (10) / 0–20 (5) min: the interval's easy warm-up run and cool-down (`engine/interval_library.py`). **Not part of `active`** |
| 建議 B2B | `plan.prefs.b2b` | `true` / `false` (`true`): whether a due B2B weekend is suggested at all. **Not part of `active`** |
| 熱適應 | `plan.prefs.heat`, `plan.prefs.heat_method` | `auto` / `off` (`auto`); `run` / `overdress` / `bath` / `sauna` / `mixed` (`run`). **Not part of `active`** (`NOT_SHAPING`): they only add heat sessions before a hot A/B race (`engine/heat_plan.py`). Panel: switch + select with the current S and the rules (`#pf-heat`) |

**Panel** (⚙ 課表偏好, redesigned 2026-10-02, `backend/static/schedule.html:680`): sections 每週時間
(可練日, runs, weekly hours, caps, 超過上限時) / 偏好的星期 (one row per type: type + weekday chips +
delete, ＋ 新增; a weekday given to another row is disabled) / 課表內容／目標 / 自動調整 (the
`plan.auto.*` settings, saved with the dialog — plan-auto.spec.md) / 進階 (collapsed: 間歇門檻,
warm-up / cool-down, CP / AeT test, B2B, 熱適應). One control per line, explanations behind `?`.

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

**Application order** (`shape()`, `backend/engine/plan_prefs.py:465`, then `place()`,
`backend/engine/plan_prefs.py:674`), in `week_plan` and every projected week:
1. The target hours are computed as before (CTL ramp, ≤ 10 % step, 3:1); `weekly_hours` only
   lowers them.
2. Quality count: 0 removes quality and the CP test (with a note); 2 → `week_plan` already
   planned one Zone 3 + one Zone 5 (`quality` / `quality2`) when both tracks are open; with one
   track it duplicates that session as `quality2` — only when the caller's gate allows quality
   at all, not in the 間歇門檻's guardrail mode (`Ctx.quality_cap`,
   `backend/engine/plan_prefs.py:387`), and not when the two would pass 20 % of the week (a note). Quality terrain
   adds （平路）/（坡道） and rewrites the detail (a library variant carries its own terrain); the
   目標依據 rewrites the target text (`target_policy`, `backend/engine/plan_prefs.py:503`).
3. **Caps**: the long session is capped at the long-day cap (同平日 = weekday cap). A quality
   session over the weekday cap is shortened — warm-up 15 → 10, cool-down 10 → 5 min, then one
   rep fewer (never below 2) — with title / detail rewritten so the COROS step builder still
   parses it (`trim_quality`, `backend/engine/plan_prefs.py:334`); a library variant is already
   fitted to the cap and left alone. The **CP test is exempt**
   (its protocol is fixed) with the protocol's note `note_test(protocol)`
   (`backend/engine/plan_prefs.py:110`); the 37-min quick test rarely hits a cap.
4. **Distribution**: the remaining minutes go to easy runs. Count = runs − (long + hard) when
   set, else the original count raised to ⌈minutes / cap⌉ so every run fits the cap; never
   more than the allowed days. When the target still does not fit (target > count × cap):
   - **hard**: the long day takes what fits under its cap, the rest is dropped with
     「受限於你的偏好，本週少 X 小時；想補量可以多排一天或放寬長跑日上限」
     (`NOTE_HARD`, `backend/engine/plan_prefs.py:105`);
   - **soft**: the excess goes on the long day (beyond its cap; with no long session, on one
     easy run placed on the long weekday), weekday sessions stay within the cap, note
     `NOTE_SOFT` (`backend/engine/plan_prefs.py:106`).
5. **Terrain**: trail easy runs are 輕鬆越野跑 with an **HR-only target ≤ 輕鬆跑上限** (pace and
   power are unreliable on trail) and the athlete's trail TSS / h; the long day becomes
   「LSD（路跑）」 / 「LSD（山路越野）」 (HR-only) (`_terrain_long`, `backend/engine/plan_prefs.py:399`).
   The time-based 登山 long day is gone (2026-10-02): a stored 登山 reads as 越野.
6. **Placement**: main sessions only on allowed days, one per day; the long session on the
   chosen long day (else the last allowed day left, like week_plan); quality ≥ 2 days from the
   long one, from each other and from hard days already done (Tue-first order), on its
   偏好的星期 when that keeps the rules or the athlete kept the conflict (else a `watch` note);
   the CP test and strides on theirs; strength on the chosen weekdays, else on easy-run /
   allowed days, never the day before the long session.

The projection passes the same preferences (`week_sessions`, `backend/engine/projection.py:104`),
rolls its history on the minutes actually planned, and returns each week's preference notes. The
課表 page shows the notes of the weeks in view (`_plan_notes`, `backend/api/plan_sessions.py:2130`).
User-edited and custom sessions are never overwritten: preferences only change the
generator's output and reconcile rule 3 keeps edited sessions.

**Saving** (`PUT /plan/prefs`, `backend/api/plan_sessions.py:1771`) validates the whole set,
writes every key and commits; when a 偏好的星期 breaks a default rule the dialog first shows the
conflicts once (照我的偏好), then the page opens the existing reconcile preview
(`backend/static/schedule.html:2319`). Cancelling keeps the preferences
saved and the plan unchanged until the next reconcile. The generator inputs are memoised with
the preference stamp in the key (`backend/api/plan_sessions.py:67`), so a saved change
regenerates immediately. COROS pushes read the stored sessions, so they follow too; an HR
target becomes an HR work step (`_work_hr`, `backend/sync/coros_workouts.py:182`).

## 不排課日期 — blackout days (`blackouts.py`)

One-off date ranges (a long weekend, a trip) on which **nothing is planned**. Separate from the
recurring 可練日 preference: that one says which weekdays are for training, this one blocks
specific dates. Docstring with the rules: `backend/engine/blackouts.py:1`.

**Storage**: `user_settings` key `plan.blackouts` (default `[]`,
`backend/settings/repository.py:142`), a list of `{id, start, end, label[, kind]}`. Validation
(`validate`, `backend/engine/blackouts.py:77`, called from `backend/settings/repository.py:304`):
ISO dates, start ≤ end, ≤ 62 days per range, ≤ 60 ranges, label ≤ 30 chars, unique ids, **no
overlaps**. Ranges may be in the past (kept as a record; only days from today on change the
plan). Read synchronously by `load()` (`backend/engine/blackouts.py:136`); bad data reads as none.

**休息日** (2026-10-02): `kind: "rest"` is a one-day range the athlete sets from the 課表 calendar's
context menu (`POST` / `DELETE /plan/rest-days`, `backend/api/plan_sessions.py:1876`,
`backend/api/plan_sessions.py:1896`; label 休息日, never a past day). Nothing is planned there, but
it is **not a lost day**: the week keeps its volume on its other days (`lost_days`,
`backend/engine/blackouts.py:178`). The athlete's own sessions on that day move to another day of
the week (decision `move`).

**Planning rules** (`week_plan`, `project_weeks`, `reconcile`):
1. **Never on a blocked day**: blocked days are removed from the candidate days before placement
   (`week_plan` both paths, `plan_prefs.place()`, `projection._place`,
   `backend/engine/projection.py:253`), so the existing placers keep their rules — long first
   (it gets the last free day before easy runs do), quality ≥ 2 days from the long and from each
   other, strength not the day before the long — and their 「排不進去」 drop path.
2. **Volume**: target hours × (allowed days not blocked ÷ allowed days) (`lost_days` / `factor`,
   `backend/engine/blackouts.py:188`, `backend/engine/blackouts.py:188`); allowed = the 可練日
   preference, else all 7. A past blocked day with a workout is not lost, nor is a 休息日. With preferences, the
   run slots shrink by the lost days too.
3. **Week note** (`week_note`, `backend/engine/blackouts.py:193`):
   「9/30–10/4 不排課（連假出遊），本週少 X 小時」, plus 「剩下的日子排不下 N 堂課」 when sessions
   were dropped.
4. **The step after** (2026-10-01, detraining.md §6.6): the old `step_cap` (the week after a
   blocked week capped at `max(1.10 × done, done + 0.5 h)` — 0.5 h after a fully blocked week,
   far slower than Daniels) is no longer applied. A blackout ≥ 6 days with no run inside gets
   the re-entry block (`engine/reentry.py`, Daniels table 9.2: 50 % / 75 % … of the 4 weeks
   before, mode `reentry`, no quality inside); a shorter one is Daniels' category 1 — back to
   100 %, and the projection doesn't let that week lower the base it ramps from.
5. **Stored sessions** on a blocked day from today on (reconcile rule 6,
   `_clear_blackouts`, `backend/engine/reconcile.py:276`): unedited auto sessions follow the
   regenerated week (the change says 「在不排課日期內（label），移到 m/d」,
   `backend/engine/reconcile.py:260`), or, in a week that wasn't regenerated, move to the
   nearest free day (`move_to`, `backend/engine/blackouts.py:223`: same week, ≥ today, allowed
   weekday, not blocked, no other main session, a hard session never next to another hard day;
   ties go earlier; long first) or are dropped. **Edited / custom sessions are never changed
   without the user**: they come back as a `conflict` change with `move_to`, and only
   `decisions[uid]` = `move` / `delete` changes them (an edited auto session deleted leaves a
   tombstone). Adding or moving a session onto a blocked day is a 400
   (`_not_blocked`, `backend/engine/plan_store.py:322`).
6. **COROS**: through the normal reconcile + push. Regenerated sessions are re-sent on their new
   day or removed as stale; an edited session still on a blocked day (no decision yet) is not
   pushed and its pushed copy is removed (`_on_blocked`, `backend/api/plan_sessions.py:1658`,
   used at `backend/api/plan_sessions.py:1684`). The push preview counts them
   (`blackout_to_remove`).

**Flow** (`backend/api/plan_sessions.py:1815`): `POST /plan/blackouts/preview` regenerates with
the *candidate* list and returns the reconcile preview without saving anything (the generator
inputs are memoised with the blackout stamp in the key, `backend/api/plan_sessions.py:67`, and
`_compute_inputs` takes the candidate list); `PUT /plan/blackouts` saves the list, then
reconciles with the user's `decisions`. `POST /plan/reconcile` also takes `{decisions}`.

**Page** (`backend/static/schedule.html`): blocked days get a faint 45° hatch (the provisional
week hatch is 135°, so both read when stacked) and a small 🏖 label chip on the first day of the
range in each week row (every blocked day in the phone agenda); no ＋, no add-on-click, no drop
target (`boOf`, `backend/static/schedule.html:992`). Ranges are set by dragging across days
(mouse / pen, 8 px threshold, never starting on a chip or button; `backend/static/schedule.html:2440`),
by clicking a day and Shift-clicking another, or from ⋯ → 「🏖 設定不排課日期」
(`backend/static/schedule.html:566`), which also works on the phone. The dialog
(`backend/static/schedule.html:802`, `openBo` `backend/static/schedule.html:2350`) takes start,
end and an optional label, checks overlap / length client-side, and shows how many sessions sit
in the range; clicking the chip edits or removes the range. Creating, editing and removing all
go through the reconcile preview first (`applyBlackouts`, `backend/static/schedule.html:2387`),
where each conflicting edited session gets 移到… / 刪除 / 先留著 (`changesHtml`,
`backend/static/schedule.html:1203`). Blackout notes show above the calendar with a 🏖 mark.

## Same-load conversion (`equivalence.py`)

When a session's terrain changes in the dialog (路跑 ⇄ 越野跑), the page designs a session of
the **same time**, hence the same TSS: the sessions it applies to are easy (≤ AeT), and at the
same intensity TSS grows with time at the same rate (hrTSS = h × IF² × 100). Only time has to
be predicted from distance and climb, per terrain, for this athlete.

**Model** (docstring `backend/engine/equivalence.py:1`; `fit`, `backend/engine/equivalence.py:249`):
- Samples: the last 26 weeks; easy = avg HR ≤ AeT + 3 (the review card's easy-run tolerance), ≥ 20
  min, ≥ 1 km (`samples_from`, `backend/engine/equivalence.py:334`). Group hikes are paced by the
  group, so only hikes the athlete marked solo (`racepower.athlete.solo_hikes`) are hike samples.
- Naismith's additive form (1892) with Langmuir's grade-dependent descent correction
  (*Mountaincraft and Leadership*, 1984: −10 min per 300 m on 5–12° descents, +10 min on
  steeper; `descent_hours`, `backend/engine/equivalence.py:142`):
  `h = km / v_flat + gain / VAM + descent`.
  v_flat = median speed of easy flat road runs (else the speed at AeT from a speed ~ HR line
  over all flat road runs; `backend/engine/equivalence.py:186`); trail VAM by least squares
  on the residual (`backend/engine/equivalence.py:215`); hike flat walking speed and VAM
  fitted together (`backend/engine/equivalence.py:228`).
- Fewer than 5 samples on a terrain → effort distance EP = km + gain/100 (ITRA / 健行筆記,
  `algorithms/effort.py` SIMPLE_FORMULAS["itra"], measured there at 6.9 % vs integrated
  Minetti on one runner's data) at the athlete's median EP speed on that terrain.
- With ≥ 5 samples both forms are fitted and the one with the lower **inner** leave-one-out
  error is used (`method="auto"`).
- `design()` solves km (and climb = km × m/km) for a time at a climb density
  (`backend/engine/equivalence.py:170`); road uses EP at v_flat.
- `TODO(racepower-v2)` (`backend/engine/equivalence.py:64`): a validated grade-cost model from
  `backend/engine/racepower/` can be passed as `fit(..., grade_cost=f)`; nothing imports it yet.

**Validation** (`backtest`, `backend/engine/equivalence.py:315`): leave-one-out on the athlete's
own easy trail and hike activities — refit without the activity (including the method choice),
predict its moving time, compare. Result on one runner's data (2026-09-30, 26 weeks):

| Terrain | n | Method chosen | MAPE | Bias | Naismith / Langmuir alone | EP alone |
|---|---|---|---|---|---|---|
| 越野 trail | 9 | EP | 5.6 % | −1.0 % | 11.3 % | 5.6 % |
| 登山 hike | 3 | EP (< 5 samples) | 21.5 % | +1.7 % | — | 21.5 % |

Flat easy road speed from 7 runs. Trail samples span 55–111 m/km; the page warns outside
that range. A terrain whose MAPE is above 15 % (`ESTIMATE_MAPE`) or that cannot be backtested is
labelled **推估** — on that data hike is 推估, trail is 依你的紀錄.

**Dialog** (`backend/static/schedule.html:626`; the 「地形與同負荷換算」 fold is collapsed by default
since 2026-10-03, the 結構 editor above it open): terrain 路跑 / 越野跑 (kind `hike` is 越野跑;
choosing 路跑 on it goes back to a road kind), a
爬升比例 slider 0–150 m/km, a 套用目標賽事 button (the goal's climb per km), distance / climb /
TSS fields, a lock (鎖時間 / 鎖 TSS / 鎖距離) and 完全自由調整; editing any unlocked field
recomputes the rest with the same formula in JS (`eqH`, `backend/static/schedule.html:1397`;
`eqRecalc`, `backend/static/schedule.html:1436`). An inline grade readout shows the average grade
(on trail, climbing sections ≈ 2 × the average, 推估); for a 陡坡健走 the grade is given, so
distance and climb fill each other. It shows the live TSS and its difference vs
the original (「比原本多 15 %」), warns above the preference cap (`capFor`,
`backend/static/schedule.html:1385`), when the typed distance / climb would take a different
time, and outside the data range; a trail / hike target becomes HR-only ≤ AeT. The session is
saved with `terrain`, `distance_km`, `climb_m`.

## Multi-week projection (`projection.py`)

`project_weeks(cur, phases, until, ctlconstant, atlconstant, prefs, blackouts, events, heat_acts,
b2b_accepted)` (`backend/engine/projection.py:358`) starts
from this week's `week_plan()` output and rolls the same rules forward week by week, never
more than `MAX_WEEKS` = 8 ahead (`backend/engine/projection.py:36`):

- Hours per week (`week_hours`, `backend/engine/projection.py:77`): base / specific use the CTL
  ramp goal capped at +10 % (≥ +0.5 h) of max(4-week mean, last week), with a 65 % recovery
  week after 3 build weeks; taper 40–50 % of the 6-week mean; event 30 %; recovery 50 %;
  transition 65 % of the 4-week mean. A weekly-hours preference caps it
  (`backend/engine/projection.py:439`).
- Sessions (`week_sessions`, `backend/engine/projection.py:104`): the same template (long, the
  week's intervals, strength, easy fill) placed by `_place` (`backend/engine/projection.py:253`), or
  shaped and placed by the preferences. Base / 專項期 / 減量期 intervals come from the 間歇門檻
  per week through the same two-track pick as `week_plan` (`overview.quality_sessions`,
  `backend/engine/projection.py:482`; `_bq`, `backend/engine/projection.py:241`), a base recovery
  week gets the fartlek (`backend/engine/projection.py:179`). Tests are never projected: a due
  AeT test only moves the cadence's "last" date (`backend/engine/projection.py:478`). The 主要訓練項目,
  B2B, 專項期, 陡坡健走 and 熱適應 hooks run per week too.
- Whether a projected week gets a quality session is decided per week, for that week's phase,
  mode and Monday (`allow_quality` → `quality_gate.week_decision`,
  `backend/engine/projection.py:326`): the method state from this week (`weeks` mode and the
  Zone 3 gate's consistency streak re-evaluated per Monday), only the intensity guardrail
  carried forward (Zone 5 only), and each track's step (`{"z3", "z5", "met"}`) advanced once per
  projected interval of that track (this week's own intervals count).
  A CP-test week no longer carries into later weeks; a `cur` without the new gate — or with
  the old `{levels, streak_ok}` shape — becomes a no-method gate (intensity bad keeps Zone 5
  out) (`_gate_inputs`, `backend/engine/projection.py:304`).
- CTL / ATL roll forward with the athlete's constants (`ds.athlete.ctlconstant` /
  `atlconstant`, `backend/engine/projection.py:552`); a session `_place` left without a day is
  kept out of the date filter.
- Each projected week carries `mode`, hours, TSS, CTL start / end, `why`, `provisional`
  (true beyond next week) and, with preferences, `notes`.

The horizon is the current phase end, at least two weeks out, capped at `MAX_WEEKS`
(`backend/api/plan_sessions.py:80`).

## Stored plan (`plan_store.py`, `reconcile.py`)

**Table** `plan_sessions` (`backend/db/models.py:157`): one row per planned session — `uid`,
`week_start`, `gen_key` (the generator's id: long / quality / easy1 …; none for custom), `day`,
`kind`, `title`, `minutes`, `target`, `detail`, `source`, `tss`, `origin` (auto / custom),
`edited`, `provisional`, `state` (active / done / missed / deleted / superseded), `done_by`
(JSON activity row), `note`, and `terrain` / `distance_km` / `climb_m`, `protocol`, the
interval-library `variant_*` columns, `target_basis` (目標用 hr / power, None = 自動), `steps`
(the structure saved in the 課表 editor, JSON) and `ext_key` / `ext_sig` (a session written from
outside the generator — the 賽事計算機's 「匯出至課表」, `racecalc:<event id>` — and the fingerprint
of what it wrote) (`backend/db/models.py:184`, added by `_migrate_schema`,
`backend/db/database.py:101`). `updated_at` moves only when a row's content changes.

**Kinds** (`backend/engine/plan_store.py:21`): easy 輕鬆跑, long **LSD** (was 長時間, 2026-10-03;
the old auto titles are mapped at read time by `display_title`, `backend/engine/plan_store.py:41`,
the key stays `long`), quality 強度課, test 測試, hike **越野跑** (登山 is not a workout type),
strength 肌力, heat_passive 被動熱適應, notice 課表待確認 (a reminder, never load or compliance),
race 比賽 (the generator's race-day row, minutes 0, or the 賽事計算機's export; never added by hand,
and no other kind can be changed into it). **Terrains** road / trail / hike
(`backend/engine/plan_store.py:32`).

**Reconcile rules** (`reconcile()`, `backend/engine/reconcile.py:91`; documented at
`backend/engine/reconcile.py:12`):
1. Active (and previously missed) sessions up to today that match an activity become **done**
   (`plan_match.assign`, `backend/engine/reconcile.py:114`: same day + the planned sport first,
   one activity per session; long / quality / test also by the generator's week-wide match —
   details in plan-auto.spec.md); the rest on past days become **missed**, but only up to the day
   the synced data covers (`covered`), so a late sync can turn a missed session back into done.
   An activity the athlete unlinked is never auto-matched again.
2. Per generated week, unedited auto sessions from today on are replaced by the regenerated
   ones: same `gen_key` → changed, gone → removed, new → added. Terrain, distance and climb
   are regenerated fields (`FIELDS`, `backend/engine/reconcile.py:48`).
3. Edited and custom sessions are kept. An edited long / quality / test is **superseded** when
   the regenerated week is a rest week (recovery / taper / event / transition) that no longer
   has it (`backend/engine/reconcile.py:151`). Deleted auto sessions stay deleted: their
   tombstone blocks the `gen_key` for that week. A kept race row (the calculator's export) blocks
   the generator's own `race` of that week (`backend/engine/reconcile.py:126`), so the week never
   has two races and the export is never moved.
4. An auto session on the same day as a kept edited / custom session moves to a free day of
   that week, or is dropped (`_resolve_collisions`, `backend/engine/reconcile.py:341`).
5. Unedited auto sessions past the horizon are removed.
6. 不排課日期: nothing active stays on a blocked day from today on; edited / custom sessions
   there are a `conflict` until the user decides (see 不排課日期 above; documented at
   `backend/engine/reconcile.py:30`).

Each change is returned as `{action, uid, day, title, kind, minutes, origin, edited, reason?,
before?, conflict?}` (`conflict` = `{label, move_to, day, choice}`; `_change`,
`backend/engine/reconcile.py:80`) and grouped by day for the preview
(`backend/engine/reconcile.py:375`).

**Coverage** (`_covered`, `backend/api/plan_sessions.py:182`): the later of the latest activity
day and the day before the latest successful COROS / generic sync.

**Automatic reconcile** (`_ensure`, `backend/api/plan_sessions.py:233`): on the first visit of
a week, or while an earlier week still has active sessions, the plan is reconciled and saved
before anything else is returned — unless a 課表待確認 proposal is waiting (plan-auto.spec.md).
`GET /sessions` also matches newly synced runs right away (`match_only`).

**Edits** (`_clean`, `backend/engine/plan_store.py:248`; `edit`, `backend/engine/plan_store.py:328`):
editable fields are day, kind, title, minutes, target, detail, terrain, distance_km, climb_m,
target_basis, steps. A day must be ISO and not in the past; kind and terrain must be known (not
`notice`); minutes 0–1440; distance 0–500 km, climb 0–20000 m; an optional TSS estimate 0–2000;
`steps` is normalised by `workout_steps` (a bad structure is a 400); title not blank. An edit
marks the session `edited` and non-provisional; hand-editing a library variant's text keeps the
variant marked `swap = user`. Moving an auto session to another week leaves a tombstone in the
old week and turns the session into a custom one. Only active sessions can be edited.

**Add** (`backend/engine/plan_store.py:372`): a custom session needs a day; defaults kind easy,
45 min, a title per kind (a new test follows the CP 測試方式); `notice` and `race` can't be added.
**Delete** (`backend/engine/plan_store.py:455`): an auto session becomes a tombstone
(`state = deleted`), a custom one is removed; deleting either day of an accepted B2B cancels it.

**External sessions — 賽事計算機「匯出至課表」** (2026-10-04, SP-43; `upsert_external`,
`backend/engine/plan_store.py:498`): one row per `ext_key`, written as the user's own (`edited`).
The row is this key's active row (updated), else its deleted / superseded row (restored), else the
generator's own race row of that week (`gen_key` race, claimed), else a new custom row; a moved race
date moves the row. An identical export is `unchanged` (no write, `updated_at` kept). `ext_sig`
fingerprints day / kind / title / minutes / target / detail / steps (`ext_signature`,
`backend/engine/plan_store.py:488`), so a later edit on the 課表 page shows as `user_edited` before an
overwrite. The race's planned TSS counts in the week (`plan_summary`). See racepower.spec.md,
Watch export.

**Expired sessions** (2026-10-02): a past session that was never done (missed, or still open on a
past day; `is_expired_open`, `backend/engine/plan_store.py:410`) can be deleted alone or all at
once (「刪除所有過期未完成」, `delete_expired`, `backend/engine/plan_store.py:436`). It becomes a
tombstone of any origin with note `user_deleted_expired` and no `gen_key`, so reconcile,
plan_match and the auto-replan's 復原 never bring it back; a pushed copy comes off the watch
through the workout-sync provider (`_unpush_expired`, `backend/api/plan_sessions.py:1487`), or
on the next push when the login has expired.

**Manual link** (`link` / `unlink`, `backend/engine/plan_store.py:617`,
`backend/engine/plan_store.py:604`): the athlete pairs a session with an activity of the same
week (± 1 day; the calendar's `link_options`) or undoes it; an unlinked activity is stored in
`plan.match.unlinked` (by start time) and becomes an unplanned run.

**Stored-plan summary** (`plan_summary`, `backend/engine/plan_store.py:643`): the week's
target hours (active + done sessions, strength excluded) and TSS (`session_tss`: a done session
counts its activity's actual TSS), plus the CTL / ATL projection described under PMC, ending
CTL / ATL and next-Monday TSB.

**Compliance** (`engine/compliance.py`; TrainingPeaks / intervals.icu): a done session's
actual ÷ planned TSS and duration; the worse deviation sets the colour — green ≤ 20 %, yellow
≤ 50 %, red beyond or the wrong kind of activity (`COMPLIANCE`, `backend/engine/compliance.py:21`);
planned vs actual (`plan_match.compare`) flags 「沒照課表」 (≠). Shown on the 本週 tiles, the 課表
chips and week rows, and the 課表統計 page.

**Concurrency**: plan writes are serialized by one asyncio lock per event loop
(`_wlock`, `backend/api/plan_sessions.py:221`), so two tabs or a preview racing a push cannot
generate the same week twice.

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

### 課表訂閱 — calendar feed (`calendar_feed.py`, 2026-10-04, SP-54)

The stored plan as an iCalendar (RFC 5545) subscription that Google Calendar (「從網址新增」) and
the iPhone calendar (「新增訂閱的行事曆」, then its widget shows today's session) read without
logging in (`build`, `backend/engine/calendar_feed.py:189`; served by `feed`,
`backend/api/calendar_feed.py:122`).

- **Address**: `/share/calendar/<token>.ics`. Under `/share/` because the tunnel's Basic-auth
  proxy lets only that prefix through (like the race-power share links). The token
  (`secrets.token_urlsafe(24)`) is stored with the origin the settings page was opened at in the
  setting `plan.calendar` = `{token, origin}` (None = off; `backend/settings/repository.py:176`,
  validated by `validate_setting`, `backend/engine/calendar_feed.py:67`). Compared in constant
  time (`token_ok`, `backend/engine/calendar_feed.py:74`); a wrong, old or missing token is a
  **404**, never a 401. 「重設網址」 makes a new token (the old address is a 404 at once);
  「停用」 clears it.
- **Events**: one all-day VEVENT per stored session from 14 days ago to 56 days ahead
  (`in_window`, `backend/engine/calendar_feed.py:180`), states active / done / missed; deleted /
  superseded rows and kind `notice` (課表待確認) are left out. Sessions have a day, no time, so
  every event is `DTSTART;VALUE=DATE` + the next day, `TRANSP:TRANSPARENT`. Rest days and
  不排課日期 have no session and get no event; an edited / custom session the user kept on a
  blocked day is shown as stored. SUMMARY = the title, 「✓ 」 when done, 「✗ 」 when missed
  (`summary`, `backend/engine/calendar_feed.py:133`). DESCRIPTION = planned minutes / TSS, the
  actual minutes / TSS when done, distance / climb, the target text, the saved structure
  (`workout_steps.structure_text`), the detail and 「在課表打開這堂課」 (`description`,
  `backend/engine/calendar_feed.py:148`); `URL` = the same link: the 課表 page's existing deep
  link `?day=&uid=` that opens the session's dialog (`backend/static/schedule.html:2722`).
  Calendar properties: `X-WR-CALNAME`, `REFRESH-INTERVAL` / `X-PUBLISHED-TTL` PT1H (hints;
  Google ignores them). Lines are CRLF, folded at 75 octets without splitting a UTF-8 character,
  TEXT escaped (`fold`, `escape`, `backend/engine/calendar_feed.py:94`).
- **Edits follow**: UID = `<uid>@trailruncoach`. A session's `uid` lives as long as the session:
  an edit or a move to another day / week keeps it (`edit`, `backend/engine/plan_store.py:328`),
  and reconcile keeps it when it regenerates the same `gen_key` in a week; a newly generated
  session (a new `gen_key`, or a week generated again after its rows were removed) gets a new
  uid, and the old one simply leaves the feed. `LAST-MODIFIED` = the row's `updated_at`, which
  `_fill` (`backend/engine/plan_store.py:111`) now moves only when the row's content changed
  (save() rewrites every row) and always by ≥ 1 s; `SEQUENCE` = its whole seconds since
  2026-01-01 (`sequence`, `backend/engine/calendar_feed.py:125`), so every change raises it.
  Deleted sessions (one, 「刪除所有過期未完成」, reconcile removals, auto-plan replacements)
  disappear from the feed.
- **Read-only and cheap**: the feed never reconciles; it shows the weeks the app has stored so
  far. Links use `WKO5COACH_PUBLIC_URL`, else the stored origin, else the request's address
  (X-Forwarded-Proto / -Host only from a trusted proxy; `public_base`,
  `backend/api/calendar_feed.py:70`).
- **Demo**: neither router is mounted in the demo instance (`owner_only`, `backend/main.py:142`):
  no feed of the demo athlete and no way to the owner's.
- **Refresh latency**: the iPhone fetches as often as 設定 › 行事曆 › 帳號 › 擷取新資料 allows
  (every 15 min ⇒ about 5–15 min); Google Calendar refreshes subscribed URLs on its own schedule
  (typically several hours, up to about a day) and can't be forced. The settings page says so
  (`backend/static/settings.html:362`).

## COROS push (`coros_workouts.py`)

Pushes stored sessions to COROS Training Hub as structured, scheduled workouts through the
unofficial Training Hub API (same host and token as the COROS sync client; endpoints listed at
`backend/sync/coros_workouts.py:6`). Since 2026-10-02 the API talks to the **active workout-sync
provider** (`WT.active`, setting `plan.push.provider`; the COROS provider delegates to
`coros_workouts`, Garmin / intervals.icu are disabled stubs); `coros_plan_push.provider` records
which one. The response keeps the `coros` field names.

- **Scope** (`_range`, `backend/api/plan_sessions.py:247`): `day` = that day; `week` = the
  Monday–Sunday week of `day`, from today on; `phase` = today to the phase end, capped at
  `MAX_WEEKS`. `day` defaults to today; the plan's "today" is never earlier than the real date
  (`backend/api/plan_sessions.py:229`). A `week` entirely before today is a 400 for preview,
  push and unpush instead of an empty range (`backend/api/plan_sessions.py:264`); a past `day`
  scope is not guarded.
- **Every push reconciles first** and applies the result, then pushes the active sessions in
  range (`backend/api/plan_sessions.py:1668`).
- **Session → steps** (`session_steps`, `backend/sync/coros_workouts.py:400`): a structure the
  athlete saved in the 課表 editor (`steps`, `engine/workout_steps.py`) wins over the text; long /
  hike / easy are one time step at HR ≤ the easy cap; a 路跑 專項期 LSD with a marathon-pace segment
  is easy / MP / easy, the MP step a pace target (COROS `intensityType` 3, s/km: the goal pace,
  else threshold pace × 1.04–1.08) or an HR band without threshold pace; an easy session whose
  title has `N×S 秒` gets a strides repeat when ≥ 10 min remain; quality and test sessions get
  their own step builders — an HR basis gives HR work steps (`_work_hr`,
  `backend/sync/coros_workouts.py:182`). Strength, rest, heat_passive and a race without steps
  (the generator's 比賽) are not pushed (skipped, with a reason); a race with steps (the 賽事計算機's
  「匯出至課表」) is pushed from them (`backend/sync/coros_workouts.py:404`); a 課表待確認 notice is one 1-minute step. Done, unplaced and past-day
  sessions are not pushed (`session_workout`, `backend/sync/coros_workouts.py:562`). The push
  preview lists sessions whose % / zone pace steps have no threshold pace (`pace_notes`,
  `backend/api/plan_sessions.py:1646`).
- **Program** (`build_program`, `backend/sync/coros_workouts.py:508`): run sport; HR targets as
  absolute bpm with the LTHR zone scheme; names `TRC <title> <m>/<d>`, ≤ 30 chars
  (`workout_name`, `backend/sync/coros_workouts.py:557`).
- **Idempotency** (`_push_one`, `backend/sync/coros_workouts.py:908`): each push is recorded in
  `coros_plan_push` (`backend/db/models.py:130`) with the COROS program / plan / schedule ids
  and a SHA-256 fingerprint of day + payload. Same fingerprint → left alone; changed → the old
  COROS entry is removed and a new one created; an entry already executed on the watch is kept
  as done. The stored-plan push keys rows by session `uid` (`session_key`,
  `backend/db/models.py:142`).
- **Clean-up** (`push_sessions`, `backend/sync/coros_workouts.py:967`): pushed sessions that
  left the plan (deleted / superseded / regenerated away) are removed unless on a past day;
  missed sessions and expired ones the athlete deleted are removed from the calendar
  (`plan_store.off_watch`). Only entries recorded in `coros_plan_push` are
  ever deleted (`_remove_row`, `backend/sync/coros_workouts.py:1006`). A pushed exported race also
  takes off the workout the calculator's retired 「匯出到 COROS」 pushed under the same key
  (`racecalc:<event id>`, not in `all_rows`; `_old_calc_keys`, `backend/api/plan_sessions.py:1652`);
  the preview counts it as `calc_to_replace`.
- **Unpush** (`DELETE /push-coros`, `backend/api/plan_sessions.py:1697`) removes every recorded
  entry whose day falls in the range (`remove_keys`, `backend/sync/coros_workouts.py:991`).
- **Status per session** (`status_of`, `backend/sync/coros_workouts.py:796`): done / skipped /
  not_pushed / pushed / outdated / failed; sessions no longer active but still recorded show
  `pushed_<state>` (`backend/api/plan_sessions.py:282`).
- The old week-keyed helpers (`push_week` / `remove_week` / `week_status`, keys
  `<week start>/<session id>`) were only used by tests and are gone; the tests drive
  `push_sessions` / `remove_keys` / `status_of` directly.
- Pushes and removals are serialized by a module-level lock
  (`backend/sync/coros_workouts.py:79`). An expired COROS login returns 401
  `COROS_AUTH_REQUIRED` (`SYNC_AUTH_REQUIRED` for another provider) with a hint to log in again
  on the settings page (`_auth`, `backend/api/plan_sessions.py:1662`).

## Page (`backend/static/overview.html`)

- Title 訓練總覽, heading and nav entry 總覽 (`backend/static/overview.html:6`,
  `backend/static/overview.html:244`).
- **Dashboard layout** (2026-10-02, reordered 2026-10-03): an in-page jump nav 狀況 / 紀錄 / 指標 /
  本週 / 待辦 (`backend/static/overview.html:245`); a pending 課表待確認 proposal on top
  (`autoplan.js`; the change log lives on 課表); **KPI tiles** — CTL and TSB (value, 90-day
  sparkline, one-word status), this week's volume (done / target ring and TSS, from the stored
  plan once loaded) and the countdown to the next A race (`renderKpis`,
  `backend/static/overview.html:408`); the **90-day PMC**; 做了什麼 (period totals, the tables
  behind 詳細數字); 指標 as compact cards (value, sparkline, one-line verdict; why / action /
  source behind 詳細, `loadStatus`, `backend/static/overview.html:443`); **本週** as seven
  day-cards — one icon + colour per session type (輕鬆, 3 區, 5 區, LSD, B2B, 負重, 測試, 肌力 …),
  ✓ and the compliance colour when done, activities outside the plan with their session class,
  a click opens the 課表 page on that day (`dayHtml` / `renderPlan`,
  `backend/static/overview.html:676`, `backend/static/overview.html:676`), the week notes and a 詳細
  with the bars, the reasons, CTL → Sunday, next-Monday TSB and the thresholds line (incl. the
  輕鬆跑上限 and its `?`) (`loadWeek`, `backend/static/overview.html:709`); the bottom row holds the
  5 區 card (full width), 待辦與警示 and, left of it, the B2B card (shown only before a multi-day /
  ≥ 6 h A event or once B2B weekends exist; `GET /overview/b2b`). Due tests, B2B weekends and zone
  retests are in the shared floating suggestion box (`static/suggestions.js`); its
  `suggestions:changed` event reloads the week (`backend/static/overview.html:941`). No sideways
  scroll at phone width (long alert titles wrap).
- **Glossary hovers**: the terms `AeT` and `CP 測試` inside engine text (actions, indicator
  verdict / why / action, week reasons, notes, the thresholds line) get a hover / tap
  explanation of what they are and how to test them (`backend/static/overview.html:327`).
- **Sources removed from the plan**: the to-do list no longer prints sources, and the 依據 block
  of the week plan is gone — `w.rules` still carries them, noted in a code comment
  (`backend/static/overview.html:723`). Indicator cards keep their 來源 inside 詳細
  (`backend/static/overview.html:461`).
- The day list (`GET /plan/calendar` for this week: sessions, TSS estimates, compliance;
  `loadPlan`, `backend/static/overview.html:842`), the week's progress-bar targets, the Sunday CTL
  and next-Monday TSB come from the stored plan (`backend/static/overview.html:696`).
- **3 區／5 區解鎖流程** card (`#z5card`, renamed by SP-39, `backend/static/overview.html:301`; full
  width of the bottom row because it decides whether the week's interval is Zone 3 or Zone 5):
  the pill 「5 區：已解鎖／AeT 已通過／未確認／暫停／恢復期／不設門檻」 (the Zone 5 track; its
  `z5_gate.text` on hover), then the flow (`z5_card.flow` = `quality_gate.z5_flow`, drawn by
  `static/z5flow.js`): **two independent, parallel tracks**, side by side from 700 px (container
  query), stacked on a phone — 3 區 (3 區解鎖: consistency / 90-min test / UA gap, any one → 3 區階梯
  A1–A4) and 5 區 (5 區解鎖: 實測 AeT 二選一 — AeT＋LTHR gap ≤ 10 % or Friel < 5 % — plus 「近 6 週
  做過 ≥ 2 堂 3 區」, re-entry / 維持 items → 5 區階梯 V1–V4). Each track: a header with 已解鎖 /
  未解鎖, its own 「你現在在這裡：<stage> · 下一步：…」 (+「也可以」; the full 「還缺：…」 `next`
  behind ? on the 5 區 track) and stage cards: badge (✓ / number), tag word (已完成 / 現在 /
  未解鎖), checklist ☑ / ☐ with one short 「→ 下一步」 line, 「完成後：」 what it unlocks; sources
  behind ?. **安排課表**: an unticked session / test (the current ladder rung, the 90-min / AeT /
  Friel / LTHR test, the soft Zone 3 line) shows a 「安排課表」 link (`item.action.href`) to the 課表
  page — `?add=<variant or template key>[&proto=]` / `?test=aet&proto=<protocol>` — which opens
  the new-session dialog with that session preselected; the user picks the day and saves through
  `POST /sessions` (the 90-min test takes that day's long run, as 排入測試 does — plan-auto.spec.md).
  Presentation only: every flag comes from `z5_card`. 「歷程 →」 opens the 基礎期
  panel.
- **5 區開放流程 panel** (viewer, 周期化訓練 → ② 基礎期, custom view `kind: "z5gate"`,
  `wko5views.z5gate_panel`): the same stage flow as the card (`z5.progress.flow` =
  `wko5views.z5_progress` = `z5_card` on the status gate) — no chart, no time axis — and a
  collapsed 歷程 list (`quality_gate.z5_history`, range capped at a year: every state change and
  event in order, the description and the sources). The render cache key adds the preference
  and test-session stamps.
- Plan editing, drag-to-move, reconcile preview and COROS push by day / week / phase live on
  the 課表 page (`backend/static/schedule.html`: session dialog `openDlg`
  `backend/static/schedule.html:1827`, reconcile `backend/static/schedule.html:1222`, push
  `backend/static/schedule.html:1242`, unpush `backend/static/schedule.html:1273`), which also has
  the ⚙ 課表偏好 panel (`backend/static/schedule.html:680`, `openPrefs`
  `backend/static/schedule.html:2296`), its client-side checks (`pfError`,
  `backend/static/schedule.html:2091`) and the preference notes above the calendar
  (`backend/static/schedule.html:1058`). Since 2026-10-02/03 the 課表 page also has:
  - **日曆 ｜ 課表統計** mode cards (`backend/static/schedule.html:520`);
  - a **context menu** (right-click, long-press on touch; `ctxItems`,
    `backend/static/schedule.html:2529`): on a session 編輯 / 移到… / 刪除 (an expired one too);
    on a free day 新增 / 排入測試 ▸ (the suggested tests with their templates and day rules) /
    設為休息日, on a 休息日 取消休息日;
  - ⋯ → 「刪除所有過期未完成」 (`backend/static/schedule.html:567`);
  - on a done session, planned vs actual (compliance, 「沒照課表」 ≠) and a manual link / unlink to
    an activity (`backend/static/schedule.html:1766`);
  - the session dialog's 結構 (step editor, `engine/workout_steps.py`) open by default and the
    地形與同負荷換算 fold collapsed; the push preview's pace-target warnings.
- **課表統計** page (`backend/static/compliance.html`, a tab of 課表; `GET /plan/compliance` →
  `compliance.dashboard`, `backend/engine/compliance.py:191`): KPI tiles, the current phase's
  progress, per-kind breakdown, planned vs actual TSS / hours and completion rate per 天／週／月,
  the streak (weeks with ≥ 80 % of the due sessions done, 推估), a compact session list, a
  per-phase period filter and a zoned 負荷比 ATL ÷ CTL chart (hover 「值 · 區間（範圍）」, a dashed
  line at 1).
- **課表 toolbar wording** (2026-10-04): 「抓活動／匯入」 = 資料來源 → here, 「推送」 = 課表 → 手錶.
  The push split button reads 推送到手錶 (was 「同步到 COROS」). Left of it, ⟳ 從 {COROS｜TrainingPeaks}
  抓活動 (`#pull-btn`, `backend/static/schedule.html:548`; `pull` `backend/static/schedule.html:1318`)
  runs the same manual sync as 設定 › 立即同步 for the 資料來源 in use only (`GET /api/v1/sync/primary`,
  then the SSE start endpoint through the shared `backend/static/syncrun.js:22`). Not logged in /
  login expired / source switched off → a 到設定頁 link instead (`renderPull`,
  `backend/static/schedule.html:1304`; when COROS is the source the push side's login link covers
  it); hidden in the demo. Progress (已檢查 n · 新下載 m) and the result show in `#sync-msg`; 409
  `SYNC_BUSY` is a hint, not an error. When it ends the calendar reloads (new activities pair:
  ✓／未完成) and, if anything was downloaded, reloads once more ~5 s later together with the
  自動調整 box (`window.autoPlanRefresh`) for the background `plan_auto.after_sync`.
- **Calendar status glyphs** (2026-10-04): 完成 = the chip itself (✓ before the title + compliance
  colour, ≠, 未完成, ● activity chips). 推送狀態 = a small watch at the chip's top right, only on
  active sessions today or later (`SYNC` / `SY_SVG`, `backend/static/schedule.html:895`): 已推送
  neutral grey outline, 需更新 yellow, 失敗 red, 未推送 dashed; labels are provider-neutral
  (手錶). ✓ is never used for push. The legend has two titled groups, 完成 and 手錶
  (`renderLegend`, `backend/static/schedule.html:1106`). 已推送 stays visible (subtle).
- **強度課的家族** (2026-10-04, SP-32; `docs/research/coach-schools-zones-periodization.md` R1). One
  classifier, `workout_templates.family_of` (`backend/engine/workout_templates.py:617`, rule in
  `classify`, `backend/engine/workout_templates.py:593`), splits a structure's `work` steps by
  intensity first, then the median rep length and the median rest between reps:
  **有氧間歇** (≤ 101 % CP, ≤ 102 % LTHR, or a pace not faster than T; sub 長 tempo = reps ≥ 15′ or
  one continuous block, 巡航間歇 = shorter reps, reps < 6′ included, 推估), **VO2max 間歇** (above
  threshold, reps 2–5′; reps < 2′ with a rest < 2× the rep = sub 短間歇, e.g. 30/30, 30/15),
  **速度** (reps ≤ 2′ with a rest ≥ 2× the rep, power > 116 % CP, or untargeted short sprints).
  Above threshold with reps > 5′ is 巡航（超閾值）. Distance reps use T pace (4:48/km without one,
  推估). Used by: the editor's 插入範本 強度課 tabs (有氧間歇 ／ VO2max 間歇 ／ 速度, labels and
  tips through `_()`, `cats`, `backend/engine/workout_templates.py:511`; groups in
  `workout_steps.templates`, `backend/engine/workout_steps.py:1490`, for the published templates
  and the interval ladder's rows alike — Palladino 4×2:40 now files as VO2max, 4×4:30 @ 98–104 %
  as 巡航); the 推薦 block (`template_recs._score`, `backend/engine/template_recs.py:124`: Zone 5
  closed → no VO2max / 速度 template; 基礎期 favours 有氧間歇, 強化期／專項期 巡航間歇; the
  「同一類」 bonus by `_rung_family`, `backend/engine/template_recs.py:204`); and the plan's
  強度課: `session_family` (`backend/engine/workout_templates.py:654`, the stored steps, else the
  derived ones) gives every session read through `_view` (`backend/api/plan_sessions.py:269`) a
  computed `quality_family` `{id, sub, label, sub_label, text}` (None for other kinds; never
  stored, no DB change). The calendar chip shows the family label before the minutes, the full
  text in its tooltip / aria-label, and the session dialog's sub-line adds it (`chipHtml`,
  `backend/static/schedule.html:929`). The scheduler's ladders and gates are unchanged.
- **訓練目的** (2026-10-04, SP-32): every built-in template has a one-line `purpose`
  (`PURPOSE`, `backend/engine/workout_templates.py:123`, from the report's Finding 7 with the
  coaches it cites, msgids through `_()`); the interval ladder's rows take the purpose of their
  family. The 插入範本 menu shows it under each row's title, and a 強度課 row also shows its sub
  (長 tempo ／ 巡航間歇 ／ 短間歇) as a small tag (`menuHtml`, `backend/static/workout_editor.js:771`).
- **速度 tab add-ons** (2026-10-04, SP-32 follow-up): strides (快步跑 4×20″) and short hill sprints
  (上坡衝刺 8×10″, UA 陡坡衝刺 8×10″) are 速度 by `family_of`, but the menu only gave family tabs to
  the `quality` category, so only Daniels R showed there. `workout_steps.templates`
  (`backend/engine/workout_steps.py:1490`) now also lists them in 強度課 › 速度 (group
  「加速跑與短坡衝刺」, family 速度, their own purpose); they stay under 輕鬆跑 / 越野跑 too.
- **越野跑 in three kinds** (2026-10-04, SP-62, the user's decision). 插入範本 › 越野跑 gets sub-tabs
  (`TRAIL_TYPES`, `backend/engine/workout_templates.py:497`; labels / tips through `_()` with en):
  **結構化爬升** (stairs, steady grades — 登山王, Koop uphill tempo, 陡坡健走, 長爬坡有氧, 陡坡衝刺:
  the HR / power bands keep floor and cap; SP-61 cancelled), **技術地形** (time + climb + RPE, no
  HR / power target) and **下坡技術／離心** (time + descent; `downhill_ecc` no longer targets HR).
  A template says its kind (`Template.trail`); a structure of your own by `trail_type_of`
  (`backend/engine/workout_templates.py:666`: an RPE work step with only a descent → 下坡, any
  other RPE work step → 技術地形, else 結構化爬升). Two technical templates: 技術地形 60′（低 RPE
  3–4, 爬升 300 m） and 90′（RPE 6–7, 爬升 600 m）, `backend/engine/workout_templates.py:422`
  (structure 推估, source / purpose as the others). The step model has a new target type **`rpe`**
  `{lo, hi (Borg CR-10 1–10), up?, down? (m)}` (`_norm_target`, `backend/engine/workout_steps.py:537`):
  it resolves to RPE with the climb, the CR-10 word and a **reference HR as text only**
  (`rpe_hint`, `backend/engine/workout_steps.py:781`: ≤ 4 under the easy cap, 5–6 up to 95 %
  LTHR, ≥ 7 from 95 % LTHR, 推估); its ≈ % CP sizes only the chart and the TSS estimate.
  **Push**: no intensity — the step keeps its time / distance / 直到按下計圈 end and its name
  carries 「RPE 6–7 · 爬升 600 m」 (`rpe_name`, `backend/engine/workout_steps.py:1247`); the watch
  preview lists that limit. **Load / PMC stay the watch's record** (no RPE correction).
  **Easy or quality by RPE**: `rpe_role` (`backend/engine/workout_steps.py:1131`) — a work step
  reaching RPE 7 (很累) = 強度課, else 輕鬆課; POST /steps/check returns it (`rpe_role`), the menu
  tags each row 算強度課 / 算輕鬆課, the editor adds an info line, and the session dialog switches
  the session's type to match when the structure changes (`rpeKind`,
  `backend/static/schedule.html:1652`: 輕鬆跑 / LSD / 越野跑 → 強度課 at ≥ 7, back otherwise), so
  the existing 48 h spacing and hard-day rules apply through the kind.
- **End conditions follow the push target; 「負荷」 (SP-38, 2026-10-04)**. Each workout provider
  declares its step end conditions and their names (`Capabilities.end_conditions` / `end_labels` /
  `load_unit`, `backend/sync/workout_targets/base.py:55`): COROS 時間／距離／直到按下計圈／負荷 (TL),
  Garmin 時間／直到按下 Lap 鍵, intervals.icu 時間. `/steps/derive` returns the active one
  (`context.provider`, read from `plan.push.provider`, `backend/api/plan_sessions.py:1153`) and the
  editor builds the 時長類型 dropdown from it (`endOpts`, `backend/static/workout_editor.js:443`); a
  stored type the provider lacks stays listed as 「（… 不支援）」. 「按圈」 is now 「直到按下計圈」
  everywhere (editor, chart legend, watch preview, issues, race-calculator export switch and hint,
  template step notes). **`load`** = `{"type": "load", "value": TSS}` (1–500), **main-set (work)
  steps only** (`normalize`, `backend/engine/workout_steps.py:666`). Its time is estimated
  TSS ÷ (IF² × 100) h at the step's ≈ % CP (`load_if`, `backend/engine/workout_steps.py:1006`; 推估),
  so the chart, the total and TSS 估 include it; the ladder counts such a main set by the same
  formula at the band's middle (`load_work_s`, `backend/engine/workout_steps.py:1253` — see
  plan-auto.spec.md). The editor shows the provider's conversion next to
  the TSS: COROS 「≈ N TL（推估 ±E）」 (`load_tl` → `engine/coros_tl.py`, refit per athlete after
  each sync — wko5-coros-sync.spec.md). **Push**: COROS gets its training-load end condition,
  `targetType 6`, `targetValue` = the TL (integer; read back from a Training Hub workout with a
  「TL 100」 end condition), the step's intensity target unchanged (`COROS_TARGET_TYPE_LOAD`,
  `backend/sync/coros_workouts.py:67`); with that constant unset, and on every provider without a
  load end condition, the step goes as the estimated time (COROS: 「負荷 X TSS（約 Y TL）」 in the
  name) and the editor lists an issue. The fingerprint is the payload, so a TL refit marks only
  sessions whose load step's sent TL moved by ≥ 3 TL as 需更新 (`TL_RESEND_MIN`, 推估; a smaller
  move keeps the TL last pushed, read from the closed-loop record — `sent_tl`,
  `backend/engine/workout_steps.py:1020`). Pushed load steps are recorded
  (`load_records`, `backend/engine/workout_steps.py:1030`) for the closed-loop correction.
  `workout_templates.session_role` (`backend/engine/workout_templates.py:689`) gives the same
  answer for a stored session. The editor's target menu adds 「RPE＋爬升」 with RPE / 爬升 / 下降
  fields (`tgHtml`, `backend/static/workout_editor.js:435`); the static demo's JS port follows.
  The 推薦 block: 基礎期 favours the low-RPE technical session, 專項期 the race-like one
  (`TRAIL_SPECIFIC`, `backend/engine/template_recs.py:43`). Not done: week_plan does not generate
  技術地形 sessions itself, and a user's own quality-kind session is not counted into the
  generator's 20 % interval budget.
- **範本 page and 我的範本** (2026-10-04, SP-36, the user's answers). 課表's third mode card
  **範本** (`backend/static/templates.html`, `GET /plan/templates/page`,
  `backend/api/plan_sessions.py:2289`; also on 課表統計) lists the user's own templates and the
  built-in library (filter 全部／我的／內建, by category, by name); the chosen one opens in the
  same step editor (`WorkoutEditor.load`, `backend/static/workout_editor.js:295`) with its name,
  categories, 目標用 (自動／心率／功率) and note. Built-in rows are read-only with their source;
  「複製成我的範本」 copies one (`backend/api/plan_sessions.py:1265`). Storage: tables
  `workout_templates_user` / `workout_template_cats` (`backend/db/models.py:345`,
  `backend/db/models.py:369`; created by create_all, and on demand in a DB made before them,
  `_ensure`, `backend/engine/user_templates.py:92`) through `engine/user_templates.py`:
  `clean` (`backend/engine/user_templates.py:174`: name 1–40, categories from the built-in ids
  easy / quality / test / trail and the user's own `c<id>`, several per template, steps through
  `workout_steps.normalize`, 目標用 hr / power / 自動) and custom categories add / rename / delete
  (`backend/engine/user_templates.py:121`; deleting one takes it off its templates). **Targets are
  stored as written**: % CP / % LTHR / zones / 自動 / RPE / 不設目標 and 「直到按下計圈」 resolve
  with the day's thresholds when used; absolute W / bpm / pace stay. 「自由模式」 = 直到按下計圈 +
  no target (or RPE＋爬升) — the existing step model, nothing new; the 「長間歇、自由模式」
  uphill template pushes as an open-ended COROS group with no intensity. In the editor's
  插入範本, `workout_steps.templates(user=…)` (`backend/engine/workout_steps.py:1490`) puts
  「我的範本」 first in every category a template is in (`groups`,
  `backend/engine/user_templates.py:487`: 強度課 under its `family_of` sub-tab — none → every
  sub-tab —, 越野跑 under its `trail_type_of` kind) and adds the custom categories as tabs; rows are
  tagged 我的 / ▲ GPX. Applying one in the session dialog also sets the session's `target_basis`
  (`backend/static/schedule.html:1640`, saved with it, `backend/static/schedule.html:1899`).
  **儲存成範本** in the editor (`saveForm`, `backend/static/workout_editor.js:915`): name +
  categories, the current structure; `POST /sessions/{uid}/save-as-template`
  (`backend/api/plan_sessions.py:1347`: the body's steps, else the stored, else derived; the
  session's 目標用) or `POST /steps/templates/user` for an unsaved session. **Route GPX**: a
  template may carry a training-route GPX / FIT (upload, replace, remove, download); parsed with the
  race calculator's reader and builder (`parse_profile`, `backend/engine/user_templates.py:309`:
  `racepower/gpx.parse` + `course.build_course`, no parser of its own), the file gzipped per tenant
  (`<HOME>/template_gpx/<id>.gz`, a demo sandbox's private dir), the profile cached in the row. A
  structure made from it keeps the template id (`tpl`, kept by `normalize`,
  `backend/engine/workout_steps.py:599`), so the session's chart shows it too; POST /steps/check
  then returns `elev` (`backend/api/plan_sessions.py:1188`): `route_elevation`
  (`backend/engine/user_templates.py:398`) walks the run order — each step covers its effort
  distance (km + climb ÷ 100 on the route's own climb) at the athlete's speed for its intensity
  (`speed_kmh`, scaled to the trail EP speed; untargeted rests walk; a distance step covers its km;
  a lap-button step without an estimate its 90 s chart width) — and maps the profile onto the
  chart's time axis (推估; a route longer than the workout is drawn up to where it ends, the legend
  says so). The editor draws it behind the bars as a light area + thin line with its own m scale
  (`elev`, `backend/static/workout_editor.js:559`), in the chart viewer's neutral elevation colour.
  Demo: the routes are sandbox writes (`backend/tenancy_mw.py:49`), each visitor's own; the static
  demo shows the page read-only (the write controls locked, `backend/demo/export_static.py:106`).
  i18n: page namespace `templates`, editor strings `common.workout.*`, server messages via `_()`,
  zh-TW + en.

## Status engine change

- `Status.weekly_hours()` sums moving time (fallback recorded time) instead of recorded
  time (`backend/engine/status.py:181`), so the volume indicators aren't inflated by multi-day
  trips.
- **`i_drift`** (`backend/engine/status.py:479`) is **informational**: the same per-run drift as
  the single-activity review (`workout_review.drift_series`, `backend/engine/workout_review.py:2209`):
  road runs, ≥ 40 min, avg HR ≤ AeT+3, hilly / stopped / unsteady runs refused. It reads
  `drift_series(ref=True)`: the 參考 tier (30–40 min after the warm-up, 推估) counts for the
  median, `why` names how many, and `extra` carries `{fair, median, ref, test, ref_label,
  ref_tip}` (the wording: Plain words below).
  > 10 % → bad (輕鬆跑太快) **only on ≥ 2 strict runs whose own median is ≥ 10 %** (the level
  feeds the base-phase guardrail), otherwise info; the text says 「飄移是 AeT 測試用的，
  不是間歇門檻」. Source Friel (< 5 %) and 徐國峰 (90′ < 10 %), not Uphill Athlete.
  **drift v2**: one run is ±4–6 pp, so the number shown and judged is the **inverse-variance
  mean ± SE of the last 6 fair runs** (`drift_agg.aggregate`, `extra.agg`; the ± SE only behind `?`);
  the median stays in `why` / `extra.median`, the single runs in the spark. The BAD level still
  needs ≥ 2 strict runs whose median is ≥ 10 % (strict tier only, as before).
  **Plain words** (2026-10-02): the card's text is the verdict word + % (「穩定 · 3.2%」,
  `workout_review.drift_plain`); ± SE, Pa:HR and the tiers are off the surface — `why` says it is
  the average of the last N easy road runs (「其中 N 次比較短，只當參考」 for the 參考 tier), the
  method (mean ± SE, median, the rules) is in `extra.tip` behind `?`, and `driftTier` shows a
  「只當參考」 badge with 「N 次暖身後不到 40 分鐘」.
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
  once the block ends (WKO5 seminar notes). `i_fitness` (`backend/engine/status.py:290`): CTL
  ramp at `load_guard`'s block line bad, watch line watch (SP-63: relative lines, seeded guardrail
  CTL, 起算期 info in the first 28 days with `ramp_week = None`; `extra` adds `ramp_base`,
  `ramp_level`, `ramp_lines`, `ramp_startup`). `i_volume` (`backend/engine/status.py:376`): the
  headline stays all-sport moving hours; the step is running time against max(the week before,
  4-week mean) — > 20 % bad (Nielsen 2014 / Damsted 2019), 10–20 % watch (推估), good with the reason
  when the week before had a 3–5-day break without a run (`load_guard.short_break`); `extra` adds
  `run_last_week`, `run_base`, `step_exempt`.
- **`i_gate`** hover adds the Zone 5 track (「Zone 5：已解鎖（…）／未解鎖（AeT 已通過，還差 3 區：…）」)
  or the AeT state (「Zone 5：未確認／已確認／暫停（原因）／恢復期」) and 「建議測試：…」; a locked method reads 「5 區未開」 and
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
- **`i_gate`** 「間歇門檻」 (`backend/engine/status.py:563`): `quality_gate.evaluate` +
  `indicator` (`backend/engine/quality_gate.py:2382`) with the status' 課表偏好 (`Status(prefs=…)`;
  the API's status cache keys on `prefs.stamp()`, `backend/api/overview.py:53`). Second in
  `PHASE_PRIORITY["base"]` (`backend/engine/status.py:1012`), so its WATCH action lands in 還缺什麼.
  Texts per the design doc §4.6: auto without AeT → info 「沒有 AeT 實測：照 80/20 原則每週 1
  次間歇（第 N 步：…）」; a blocking guardrail → watch with its number (e.g. 「本週不排間歇：低強度只有
  68%（< 75%）」); ua_gap locked → 「AeT 142 / LTHR 165：差距 16%（> 10%，有氧不足）」; unlocked →
  good 「差距 9% ≤ 10%：可以加 Zone 3」; forced + missing → watch 「沒有實測 AeT，差距法算不出來：先照
  護欄排（自訂…）」, action 「先做 AeT 飄移測試，或把間歇門檻改回自動」. `why` names the mode and
  the AeT source (「AeT 146（活動資料估算）」 / 「（{date} 飄移測試）」). `extra` is the gate dict incl.
  `options` (per mode usable + why, `backend/engine/quality_gate.py:1128`).
- `PHASE_GOAL["base"]` no longer says 飄移 < 5 %; `PHASE_FOCUS["base"]` cites UA for the easy long
  run and Palladino for the 8–15 s hill sprints (`backend/engine/status.py:1022`).
- `i_data`'s action for a missing AeT is 「排一次 AeT 飄移測試（平日，10 分暖身＋40 分固定功率，跑步機或平路）；
  測了可以改用有氧基礎門檻」 (`backend/engine/status.py:941`).
- **`i_testing`** (`backend/engine/status.py:733`) — a CP row older than 42 days → watch, 90 →
  bad (`backend/engine/status.py:62`); LTHR is event-driven (an applied estimate is said as one,
  not judged by age) and AeT goes by reason (B3); event-driven retests (`zone_events`: HR shift
  at the same power, a ≥ 4-week break, the first cool spell) are **suggestions only** — they turn
  the card watch (「建議測」) and go to the floating box, never into the plan; 10–21 days before
  the A event is named the right time; < 10 days → 「賽前 10 天內不要測，賽後再測」, watch. The
  action names the 課表偏好 protocol (`_cp_protocol`, `backend/engine/status.py:721`); `race` →
  「用 5–10 K 比賽或計時跑代替 CP 測試」. It also reads the latest CP test in the data
  (`workout_review.latest_cp_test`, 120 days, `backend/engine/workout_review.py:2296`), whose
  `delta` is against the **previous result of the same method** (`cp_protocols.reference`;
  across methods converted two-point ≈ 1.05 × a 30-min CP, 外插), so rotating quick / standard
  doesn't keep flagging. Not applied (no CP row dated on / after the test), an `apply` payload
  (not 不採用) and |delta| > `CP_DELTA` 3 % → at least watch, 要更新, action 「套用這次的 CP」.
  `extra.cp_test` carries method, quality, `ref`, `apply`, `applied`; the 總覽 測試 card draws
  the apply button from it (`applyCpBtn`, `backend/static/overview.html:538`), POSTing
  `/api/v1/plan/thresholds/apply-cp` (see `workout-review.spec.md`).
  `extra.cp_due` (CP missing / > 42 days, `backend/engine/status.py:899`) decides the CP-test
  suggestion in `week_plan` (the CP test measures CP only). The latest AeT drift test
  (`aet_test.latest_aet_test`) → `extra.aet_test` (`backend/engine/status.py:831`): band "at" and
  not applied → watch 「{date} 的 AeT 測試：飄移 4.2%，AeT = 146 bpm（目前 142）」, action 「套用這次的
  AeT（146 bpm）」, and the 總覽 測試 card's button (`aetApply`, `backend/static/overview.html:494`)
  POSTs `extra.aet_test.apply` to `/api/v1/plan/thresholds/apply-estimate` with the test `date`;
  band below / above → 「下次起始心率 +5／−5 bpm 再測一次」; a 徐國峰 90′ / Friel result is a base
  check with no AeT to apply. The old 「AeT 已經 N 週沒測」 age rule is gone (B3: a reason, not a
  date).
  **Threshold confidence (SP-64)**: `i_testing` also runs `threshold_confidence.check`
  (`backend/engine/threshold_confidence.py:889`, wired at `backend/engine/status.py:865`): LTHR /
  max HR / resting HR confidence (high / medium / low) from 8 LTHR signals (source, estimate
  premise, easy cap ≥ LTHR and % HRmax / % HRR, long efforts above LTHR, 40–60 min races < 95 %,
  the CP-band cross-check, events, age) and the max-HR plausibility check (highest HR held 120 s,
  spikes / cadence lock filtered, `hrmax_check`, `backend/engine/threshold_confidence.py:434`);
  `diagnose` (`backend/engine/threshold_confidence.py:488`) names the likely wrong value. Its
  suggestion (`thr_check:<tests>`, tests `hrmax` / `tt30`, `links` = 「安排課表」 deep links,
  `schedule_link`, `backend/engine/threshold_confidence.py:619`; `wait_cool`, `earliest` after
  the A race in taper / race week, `test_conditions`, `backend/engine/threshold_confidence.py:530`)
  joins `test_suggestions` (a low source alone → `priority: low`); the why gets the diagnosis and
  「LTHR 可信度低」; `extra.thr_check` carries the confidences, signals, diagnosis, the latest
  LTHR 30-min / max-HR test results and `warn`. The 總覽 測試 card shows them with the links
  (`thrCheck`, `backend/static/overview.html:520`); the floating box renders `links` for the
  tests it doesn't schedule (`zone_rows`, `backend/engine/suggestions.py:105`). Nothing is applied
  automatically: 設定 shows the results / candidate with 「套用」 (`renderThrCheck`,
  `backend/static/settings.html:706`). HR-target templates and sessions get a warning badge in
  the editor from `thresholds.thr_warn` (`_thr_warn`, `backend/api/plan_sessions.py:118`).
- **AeT drift test** (`backend/engine/aet_test.py`): `due` (`backend/engine/aet_test.py:481`) —
  base phase, a reason (`quality_gate.aet_test_reason`: no data for ~6 weeks, the aggregate's SE
  too large, a shift, the estimate moved) and no test in the last 28 days (推估); no fixed
  cadence. A due test is a **suggestion** (`week_plan.test_suggestions`), never a planned
  session. The session (`aet_test.session(th, hr0, p0, cap_weekday, protocol)`,
  `backend/engine/aet_test.py:527`) is kind `test`, protocol per `plan.prefs.aet_test_protocol`
  (plan-auto.spec.md); the UA versions: target 「固定功率 P W（±3%）；心率從 HR 附近開始」 (start HR
  = the estimate's aethr, else 0.89 × LTHR − 5; P = 0.75 × CP, both our choice), and their
  length follows the 課表偏好 weekday cap (`variant_for`, `backend/engine/aet_test.py:498`): no cap / ≥ 80 →
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
  `coros-sync` (`_aet_test_steps`, `backend/sync/coros_workouts.py:306`): 10 / 40 (no
  cool-down step) or 15 / 60 / 5.
- Inline source names were removed from engine text (e.g. the ramp verdict, phase focus).

## API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/v1/overview/status` | `Status.to_dict()`: today, phase, goals, headline, indicators, actions, counts (`backend/api/overview.py:80`) |
| GET | `/api/v1/overview/summary?unit=week\|month\|year&anchor=&n=` | buckets + current detail; n capped 104 / 60 / 12 (`backend/api/overview.py:87`) |
| GET | `/api/v1/overview/pmc?begin=&end=` | daily tss / ctl / atl / tsb; default the last 180 days (the page asks for 90) (`backend/api/overview.py:97`) |
| GET | `/api/v1/overview/weekplan` | the generated week plan, with the stored 課表偏好, 不排課日期, accepted B2B weekends and the race calculator (`backend/api/overview.py:108`) |
| GET | `/api/v1/overview/z5` | the 「5 區（最大攝氧量間歇）開放流程」 card: `quality_gate.z5_card` on the status gate (the object the week plan decides with) + `history_href`, the viewer link of the first `z5gate` panel (`backend/api/overview.py:121`) |
| GET | `/api/v1/overview/b2b` | the B2B card (`engine/b2b.card`): target event, this week's B2B state, planned B2B weekends, each done B2B's day-2-vs-day-1 reading and the trend (`backend/api/overview.py:133`) |
| GET | `/api/v1/overview/page` | `backend/static/overview.html` (`backend/api/overview.py:176`) |
| GET | `/api/v1/overview/plan/sessions?start=&end=` | reconcile-if-needed, match new runs, then stored sessions (not deleted / superseded) with push status, week meta, projected weeks and `summary` (`backend/api/plan_sessions.py:294`) |
| POST | `/api/v1/overview/plan/sessions` | add a custom session; 400 on a bad field (`backend/api/plan_sessions.py:369`) |
| PATCH | `/api/v1/overview/plan/sessions/{uid}` | edit day / kind / minutes / title / target / detail / terrain / distance_km / climb_m / target_basis / steps; 400 on a bad field (`backend/api/plan_sessions.py:404`) |
| DELETE | `/api/v1/overview/plan/sessions/{uid}` | tombstone (auto) or remove (custom); an expired one is tombstoned and taken off the watch; a B2B day cancels the pair; 404 when unknown (`backend/api/plan_sessions.py:1527`) |
| POST | `/api/v1/overview/plan/sessions/expired/delete` | `{uids?}` → tombstone those (or all) expired open sessions, remove pushed copies; 400 for a uid that isn't one (`backend/api/plan_sessions.py:1508`) |
| POST / DELETE | `/api/v1/overview/plan/sessions/{uid}/link` | `{index}` → pair the session with an activity / undo (the activity then stays unplanned) (`backend/api/plan_sessions.py:1550`, `backend/api/plan_sessions.py:1570`) |
| GET | `/api/v1/overview/plan/reconcile` | preview: changes and changes by day (`backend/api/plan_sessions.py:1588`) |
| POST | `/api/v1/overview/plan/reconcile` | apply the same; optional body `{decisions}` for sessions on a 不排課日期 (`backend/api/plan_sessions.py:1605`) |
| GET | `/api/v1/overview/plan/push-coros/preview?scope=day\|week\|phase&day=` | sessions in range with push status, counts to send / unchanged / skipped, missed to remove, `pace_notes`, pending changes; 400 for a past week (`backend/api/plan_sessions.py:1622`) |
| POST | `/api/v1/overview/plan/push-coros?scope=&day=` | reconcile, push the range through the active provider, clean up; 401 `COROS_AUTH_REQUIRED`; 400 for a past week (`backend/api/plan_sessions.py:1668`) |
| DELETE | `/api/v1/overview/plan/push-coros?scope=&day=` | remove what was pushed in the range; 400 for a past week (`backend/api/plan_sessions.py:1697`) |
| GET | `/api/v1/overview/plan/prefs` | `{prefs, defaults, active, day_conflicts, pref_dropped, gate_options, aet_options}` — `gate_options` = the 間歇門檻 hover texts (`quality_gate.option_texts`, `backend/engine/quality_gate.py:2509`) (`backend/api/plan_sessions.py:1752`) |
| GET | `/api/v1/overview/plan/prefs/gate` | per mode `{usable, why}` on the athlete's data, plus the active mode / state / verdict (status `i_gate`, `backend/api/plan_sessions.py:1740`) |
| POST | `/api/v1/overview/plan/prefs/conflicts` | an unsaved preference set → `{day_conflicts, overlaps}`; nothing stored (`backend/api/plan_sessions.py:1760`) |
| GET | `/api/v1/plan/thresholds` | 設定 › 閾值測試紀錄 (SP-46): `{today, thresholds, effective_thresholds, power_zones, wko5_settings}` — the same rows and effective values as `GET /api/v1/plan`, without the rest of the season plan (`backend/api/plan.py:285`); saved with `PUT /api/v1/plan/thresholds` (whole table, `backend/api/plan.py:301`) |
| POST | `/api/v1/plan/thresholds/apply-estimate` | now takes an optional `date` (the test day; not in the future) so 「套用這次的 AeT」 dates the row on the test; SP-64: also `mhr` + `mhr_method` (`planning.MHR_METHODS`; a max-HR test = test, the sustained-peak candidate = estimate) and `lthr_method: friel30` for a 30-min test (`backend/api/plan.py:637`) |
| GET | `/api/v1/plan/threshold-check` | SP-64: `threshold_confidence.check` — LTHR / max / resting HR confidence and signals, diagnosis, suggestions, latest test results with `apply` bodies; nothing saved (`backend/api/plan.py:612`) |
| PUT | `/api/v1/overview/plan/prefs` | the whole preference set (Prefs field names, missing = default); 400 on a bad / unknown value or a cross-field rule (`backend/api/plan_sessions.py:1771`) |
| GET | `/api/v1/overview/plan/blackouts` | `{blackouts}` — the stored 不排課日期 (`backend/api/plan_sessions.py:1808`) |
| POST | `/api/v1/overview/plan/blackouts/preview` | `{blackouts}` → the reconcile preview with that list; nothing saved; 400 on a bad range (`backend/api/plan_sessions.py:1815`) |
| PUT | `/api/v1/overview/plan/blackouts` | `{blackouts, decisions?}` → save, then reconcile applying `decisions` `{uid: move \| delete}`; 400 on a bad range / decision (`backend/api/plan_sessions.py:1826`) |
| POST / DELETE | `/api/v1/overview/plan/rest-days`, `/rest-days/{day}` | `{day}` → add a 休息日 (400 for a past or already blocked day) / remove it (404 when not one); both reconcile (`backend/api/plan_sessions.py:1876`, `backend/api/plan_sessions.py:1896`) |
| GET | `/api/v1/overview/plan/equivalence` | the time model, LOO backtest per terrain, 推估 flags, sources; memoised per dataset / day / AeT (`backend/api/plan_sessions.py:1958`, `backend/api/plan_sessions.py:1947`) |
| POST | `/api/v1/overview/plan/equivalence/design` | `{mode, minutes, climb_per_km}` → km, climb, 推估 flag (`backend/api/plan_sessions.py:1963`) |
| GET | `/api/v1/overview/plan/calendar?start=&end=` | the 課表 page payload (≤ 120 days): sessions with `tss_est`, planned vs actual `vs`, `compliance`, `link_options`, a 強度課's computed `quality_family`; `week_rows`, `prefs`, `goal_climb_per_km`, `plan_notes`, `test_suggestions`, `test_templates`, `expired_open`, provider state (`backend/api/plan_sessions.py:2192`, `backend/api/plan_sessions.py:2130`) |
| GET | `/api/v1/overview/plan/schedule/page` | `backend/static/schedule.html` (`backend/api/plan_sessions.py:2231`) |
| GET | `/api/v1/plan/calendar` | 課表訂閱: `{enabled, path, url, window}` of the feed address (`backend/api/calendar_feed.py:88`) |
| POST / DELETE | `/api/v1/plan/calendar/token` | `{origin?}` → a new secret address (the old one is a 404 from now on) / turn the feed off (`backend/api/calendar_feed.py:93`, `backend/api/calendar_feed.py:104`) |
| GET / HEAD | `/share/calendar/<token>.ics` | public, token-only: the stored plan as `text/calendar` (see 課表訂閱 above); 404 for any other token; not in the demo (`backend/api/calendar_feed.py:122`) |
| GET | `/api/v1/overview/plan/compliance?start=&end=` | the 課表統計 dashboard (≤ 371 days): due sessions with status and %, totals, weeks and days planned vs actual, streak, per kind, the current phase's progress, `plan_phases` (`backend/api/plan_sessions.py:2012`) |
| GET | `/api/v1/overview/plan/compliance/page` | `backend/static/compliance.html` (`backend/api/plan_sessions.py:2283`) |
| GET | `/api/v1/overview/plan/templates/page` | `backend/static/templates.html`, the 範本 tab (`backend/api/plan_sessions.py:2289`) |
| GET / POST | `/api/v1/overview/plan/steps/templates/user` | `{templates (each with its menu row), cats (built-in + custom), limits}` / create `{name, cats, steps, target_basis?, note?}` → 400 `{errors}` (`backend/api/plan_sessions.py:1241`) |
| POST | `/api/v1/overview/plan/steps/templates/user/copy` | `{key}` of a built-in 插入範本 row → a template of the user's (`copied_from`); 404 for an unknown key (`backend/api/plan_sessions.py:1265`) |
| PATCH / DELETE | `/api/v1/overview/plan/steps/templates/user/{id}` | any of name / cats / steps / target_basis / note; delete (and its GPX file); 404 when unknown |
| POST / DELETE / GET | `/api/v1/overview/plan/steps/templates/user/{id}/gpx` (`/gpx/file`) | multipart upload or replace (400 on a file that isn't a course) / remove / the stored file (`backend/api/plan_sessions.py:1292`) |
| POST / PATCH / DELETE | `/api/v1/overview/plan/steps/templates/cats`, `/cats/{cid}` | `{label}` → a custom category / rename / delete (taken off its templates) |
| POST | `/api/v1/overview/plan/sessions/{uid}/save-as-template` | `{name, cats, steps?, target_basis?}` → a template from the session's structure (`backend/api/plan_sessions.py:1347`) |
| GET | `/` | always redirects to the overview page (`backend/main.py:207`; the React SPA was removed 2026-10-04 — old SPA paths such as `/activities`, `/achievements`, `/sync`, `/config` redirect to their static pages, `backend/main.py:33`); in demo mode to `/demo` |

The same router also serves the suggestion box (`/suggestions`, `/suggestions/accept`,
`/suggestions/dismiss`, `/test-suggestions*`), the test templates / options and the 課表
editor's step and variant endpoints (`/steps/*`, `/steps-preview`, `/variants`,
`/sessions/{uid}/coros-preview`); they are not specified here (see plan-auto.spec.md for the
interval library and suggestions).

`Status` is memoised per (tenant, dataset, day, `plan.json` mtime, the 課表偏好 stamp, stored
test sessions, the 課表心率區間 stamp), LRU per tenant (`backend/api/overview.py:53`); the
plan endpoints memoise their generator inputs on the dataset / day / plan key plus the
preference, blackout, auto-replan, accepted-B2B, 主要訓練項目 and HR-profile stamps
(`backend/api/plan_sessions.py:67`). Bad scope or day → 400 (`backend/api/plan_sessions.py:247`).

## Testing

- Tests run on synthetic data only; the comparisons on the athlete's own data (golden
  `week_plan`, the equivalence backtest, blackouts and preferences on real weeks) moved to the
  opt-in `backend/tests/realdata/` suite (`WKO5COACH_REALDATA=1`).
- `backend/tests/test_overview.py`: category mapping, period arithmetic and labels, the
  projection matching the `tl()` recurrence and TSB = yesterday's CTL − ATL, and the ramp
  formula reaching exactly +3 CTL in 7 days.
- `backend/tests/test_plan_store.py`: projection ramp / cap / horizon, every reconcile rule
  (regeneration, tombstones, missed and late-sync done, coverage, collisions, supersede,
  horizon), persistence and edits, API initialisation, push scopes and idempotency, missed
  removal, concurrent first loads, unpush, and the stored-plan summary moving bars and projection.
- `backend/tests/test_plan_prefs.py`: defaults change nothing (projection and `week_plan`);
  50-min cap within cap and volume kept; hard cap note; soft cap excess on the
  long day (and on an easy run when there is no long); long-day cap first; CP test exempt;
  quality trimming; run counts 3–7; rest days; long day and quality spacing; quality 0 / 2 and
  the drift gate; strength count and days; weekly-hours cap; trail HR-only target and rate;
  a stored 登山 long terrain read as 越野; quality terrain and HR target → COROS HR steps; validation and cross-field
  checks; the prefs API; reconcile keeps an edited session when prefs change.
- `backend/tests/test_blackouts.py`: range validation; placement never on a blocked day (with
  and without preferences, 50-min cap, rest days, strength days); the long run keeps the last
  free day and easy runs are dropped; hard days stay apart; `move_to` (nearest, hard spacing,
  today, allowed weekdays); regenerated sessions say why; move vs drop in weeks not regenerated;
  edited sessions as a conflict until move / delete; the factor and note text; projection weeks
  (hours × kept share, note, earlier weeks untouched, the ≤ 10 % step after, the step after a
  short current week); the 50-min cap with allowed weekdays; the API (preview saves nothing,
  PUT applies decisions, add onto a blocked day refused); a mocked COROS removal of pushed
  sessions on blocked days.
- `backend/tests/test_equivalence.py`: Langmuir descent, recovery of known Naismith parameters,
  the LOO harness (exact on noise-free data, error with noise, nested method choice), EP
  fallback, hike two-parameter fit, flat speed regression, design as the inverse, the
  grade-cost hook and the API.
- `backend/tests/test_coros_workouts.py`: step building, program payload, push / replace /
  remove against a fake Training Hub.
- `backend/tests/test_quality_gate.py`: the gate prefs (round trip, validation, not `active`);
  every mode with and without a measured AeT (auto → none / ua_gap + friel, stale AeT, default
  LTHR, forced ua_gap / friel / xu / plateau / weeks / none); forced mode with missing data
  (watch, fallback, never locked); guardrails (intensity, power share, relative ramp lines, step 10 / 20 %,
  TSB); the dose table, hold, fade and the recovery fartlek; dose sessions through the COROS
  step builder; 1-minute rep counting and the dose history; `Status.i_gate` + `week_plan` on a
  fake dataset; the projection's per-week `weeks` unlock and dose advance; the AeT analysis
  bands and refusals (short, hot, fast finish, hills); `latest_aet_test`, the review card's
  apply action; the AeT session's COROS steps and payload (nothing sent); the due cadence; the
  apply flow on a **temp** plan (the real plan.json untouched).
- `backend/tests/test_compliance_dashboard.py`: expired-session delete (single / all, never
  restored, pushed copies removed via a mocked provider), the 休息日 action and the 課表統計
  dashboard; `backend/tests/test_schedule_calendar.py`: the calendar payload and page;
  `backend/tests/test_plan_match.py`: matching, planned vs actual and manual link / unlink;
  `backend/tests/test_lsd_label.py`: the LSD label and legacy titles;
  `backend/tests/test_workout_targets.py`: the provider registry, COROS delegation, stubs;
  `backend/tests/test_b2b.py`, `test_specific_phase.py`, `test_steep_hill.py`,
  `test_primary_sport.py`, `test_hr_profile.py`: the session decorators and the easy cap.

## Domain Model

### Bounded Context
- **Context Name**: TrainingOverview（訓練總覽）
- **Domain Layer**: Core Domain
- **Parent Module**: N/A (consumes `wko5-engine`, the Status engine and `workout_review`; pushes to COROS through the `coros-sync` client)

### Ubiquitous Language
| Term | Definition |
|------|-----------|
| 移動時間 (moving time) | WKO5 `movingduration`; the only volume unit here |
| LSD | the long easy run (kind `long`; label since 2026-10-03, was 長時間) |
| 輕鬆跑上限 (easy cap) | the easy-run HR ceiling: the 課表心率區間's Z2 top, or a measured AeT |
| 主要訓練項目 | trail (越野跑) or road (路跑／馬拉松); shapes the template |
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
| guardrails | low-intensity ≥ 75 %, CTL ramp under max(3, 10 % CTL₋₇) (to min(10, max(5, 15 %)) sub-threshold only), running-time step ≤ 10 % of max(last week, 4-week mean) (≤ 20 % holds), 3:1 fartlek, TSB, 48 h spacing |
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
| 休息日 | a one-day blackout of kind `rest` set from the calendar; the week keeps its volume |
| 偏好的星期 | preferred weekdays per session type (`pref_days`); 照我的偏好 keeps a conflicting one |
| suggestion | something the planner proposes but never schedules (CP / AeT test, B2B, race simulation, retest); stored only when the athlete picks a day |
| B2B | 連續兩天長天: two consecutive long days, suggested before a long / multi-day A event |
| expired session | a past session never done (missed or still open); deletable as a tombstone |
| link | the athlete's manual pairing of a session with an activity (unlink = unplanned run) |
| compliance | actual ÷ planned TSS / duration of a done session: green ≤ 20 %, yellow ≤ 50 %, red beyond or wrong type |
| 課表統計 | the planned-vs-actual dashboard page (was 課表達成率) |
| provider | the workout-sync target the plan is pushed to (COROS today) |

### Domain Events
None as explicit events. State transitions of a stored session (active → done / missed /
deleted / superseded) are returned as reconcile `changes` (`backend/engine/reconcile.py:114`).

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
| 2026-10-04 | code-sync | N/A | Synced ~140 commits: dashboard 總覽 (KPI tiles, 90-day PMC without projection, day-cards, Z5 card renamed, 待辦 at the bottom, B2B card); tests / B2B / race sim are suggestions; 主要訓練項目, 專項期, B2B, 陡坡健走, 輕鬆跑上限 (課表心率區間) in week_plan; LSD label, kind hike = 越野跑, 登山 long terrain dropped; prefs redesign (偏好的星期, 目標依據, warm-up / cool-down, B2B switch); 休息日, expired-session delete, manual link, compliance + 課表統計 page, context menu; push via the workout-sync provider, MP / pace steps; i_drift plain words, i_testing event-driven; AeT test by reason (no cadence, not projected); new API rows; all file:line pointers refreshed |
| 2026-10-04 | feat/sp-34-35-schedule | SP-34, SP-35 | 課表: ⟳ 從 COROS 抓活動 button (資料來源 only, shared `syncrun.js`, reload + one re-poll for 自動調整); push button renamed 推送到手錶; push status drawn as a watch (neutral when up to date, coloured only for 需更新／失敗), ✓ reserved for 完成, legend split into 完成 / 手錶 groups |
| 2026-10-04 | feat/sp-54-ics-feed | SP-54 | 課表訂閱: `/share/calendar/<token>.ics` ICS feed of the stored plan (all-day events, ✓ / ✗, steps + deep link, −14 / +56 days), `plan.calendar` token with 重設 / 停用 and 404 on mismatch, owner only (not mounted in the demo); `plan_sessions.updated_at` moves only on a real change (LAST-MODIFIED / SEQUENCE); settings page section with Google / iPhone steps (zh-TW + en) |
| 2026-10-04 | feature | SP-31 | Week plan / projection: two interval tracks (Zone 3 A1–A4 + 巡航版 fallback, Zone 5 V1–V4) with the Zone 3 gate (4 weeks ≥ 3 runs, no 7-day gap; 90-min test; UA gap; ≥ 21-day break re-locks); low-intensity share blocks Zone 5 only (warning note, 底線 75% / 目標 90% labels); Zone 3 ≤ 10 % and Zone 3 + Zone 5 ≤ 20 % of the week; 2 a week = one of each; 專項期 / 減量期 two-track sessions; z3 / intensity / quality_share notes |
| 2026-10-04 | feat/sp-32-interval-families | SP-32, docs/research/coach-schools-zones-periodization.md R1 / Finding 7 | 強度課 families: one classifier (`workout_templates.family_of`: 有氧間歇 長 tempo／巡航, VO2max 間歇, 速度 — intensity first, then rep length) replaces the 三區／四區／五區 %CP tabs of 插入範本 (published templates and ladder rows; Palladino 4×2:40 → VO2max), drives the 推薦 block's Zone 5 / phase rules, and adds a computed `quality_family` to plan sessions (calendar chip, dialog); 「無氧間歇」 named 「VO2max 間歇」; templates get a one-line `purpose` (訓練目的) shown in the menu; family labels / tips / purposes through `_()` with en |
| 2026-10-04 | feat/sp-46-thresholds-settings | SP-46 | 閾值測試紀錄 (LTHR / AeT / CP) edited on the settings page (table, 自動估算 cards, power zones; zh-TW + en), `GET /api/v1/plan/thresholds`; 賽事周期 page shows a read-only summary + link; 最大心率 only in 設定 → 心率 (no max-HR column; mhr / rhr rows kept on save); status / chart hints point to 設定. Same `plan.thresholds` data, no model change |
| 2026-10-04 | feat/sp-43-calc-export | SP-43 | Stored plan: kind `race` in `KINDS` (not added by hand), `ext_key` / `ext_sig` columns and `plan_store.upsert_external` for the 賽事計算機's 「匯出至課表」 (one row per event, claims the generator's 比賽 row, restore / move, `user_edited` by fingerprint, `updated_at` kept on an identical export); reconcile: a kept race blocks the generator's race of that week; push: a race with steps is pushed, the old `racecalc:` watch workout is removed on that push (`calc_to_replace` in the preview); 課表 dialog keeps kind 比賽 |
| 2026-10-04 | feat/sp-62-trail-types | SP-32 | 速度 tab of 插入範本 also lists strides and short hill sprints (they were 速度 by `family_of` but filed under 輕鬆跑 / 越野跑) |
| 2026-10-04 | feat/sp-62-trail-types | SP-62 | 越野跑 templates in three kinds (結構化爬升 / 技術地形 / 下坡技術／離心; sub-tabs, `trail_type_of`); target type `rpe` (CR-10 + 爬升 / 下降, reference HR as text, pushed with no target and the RPE in the step name); two 技術地形 templates, `downhill_ecc` by RPE + descent; `rpe_role` (RPE ≥ 7 = 強度課) in /steps/check, the menu and the session dialog (type follows) |
| 2026-10-04 | feature | SP-39 | 3 區／5 區 independent gates: Zone 5 needs a measured AeT (tested AeT + measured LTHR ≤ 10 % or Friel) + the soft 「近 6 週 ≥ 2 堂 3 區」 (`z5_track`, shared by week_decision and the card); low-intensity share blocks Zone 5 only with a tested AeT; the card renamed 3 區／5 區解鎖流程 and redrawn as two parallel tracks with their own 「下一步」; 「安排課表」 links into the 課表 dialog (`schedule.html?add=` / `?test=`, `WorkoutEditor.applyKey`) |
| 2026-10-04 | feature | SP-64 | Threshold confidence (`threshold_confidence.py`): 8 LTHR signals + max-HR plausibility (120-s sustained peak, spike / cadence-lock filter), diagnosis of the wrong value, `thr_check` test suggestions (max-HR / LTHR test) with 「安排課表」 links and test conditions, `extra.thr_check` on the 測試 card, `GET /plan/threshold-check`, 「套用」 on 設定 (`mhr_method`), HR-target warning badge in the editor, `maxhr_hill` test template |
| 2026-10-04 | feature | SP-63 | Relative CTL ramp lines (`load_guard`), startup seed / 28-day skip, running-time volume step vs max(last week, 4-week mean), weekly CTL goal max(2, 5 %) / max(2.5, 7 %) in `week_plan` and the projection |
| 2026-10-04 | feature | SP-31 follow-ups | 專項期 applies this week's CTL-ramp / volume-step guardrails to both tracks; 2 a week with only Zone 3 open = rung + a different 巡航版; the weekday-cap 巡航版 counts as the Zone 3 rung |
| 2026-10-04 | sp-36-template-manager | SP-36 | 範本 page (third tab of 課表): the user's own templates (`workout_templates_user`, `engine/user_templates.py`) with several categories (built-in + custom, add / rename / delete), 目標用, relative targets resolved when used, CRUD + 複製成我的範本 + 儲存成範本 (`/sessions/{uid}/save-as-template`); 「我的範本」 in 插入範本 by category / family / trail kind, custom tabs; a training-route GPX per template (race calculator's parser), its elevation behind the step chart on the time axis by estimated speed (`elev`, `tpl` in the steps); demo sandbox writes, static demo read-only; zh-TW + en |
| 2026-10-04 | sp-38-load-step | SP-38 | Step end conditions from the provider's capabilities (`end_conditions` / `end_labels` / `load_unit`); new 「負荷」 end condition (TSS, main-set only; COROS targetType 6 with the converted TL, else estimated time); 「按圈」 → 「直到按下計圈」 on the race-calculator export and template notes too |
| 2026-10-04 | feature | SP-38 follow-up | 「負荷」 main sets count on the ladder (`load_work_s`) |
| 2026-10-04 | feature | SP-38 follow-up | A TL refit re-pushes a load step only when its TL moves by ≥ 3 (`TL_RESEND_MIN`); smaller moves keep the TL last sent |
| 2026-10-04 | feature | SP-63 follow-up | `i_volume` / `guard`: no volume-step verdict the week after a 3–5-day break without a run (`step_exempt` / `step_note`); `week_plan` adds an info note (`src: "volume"`) |
| 2026-10-04 | feature | SP-39 follow-up | 「安排課表」 for the 90-min test: `POST /sessions` replaces that day's long run like 排入測試 |
