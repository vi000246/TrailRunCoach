# Module Spec: overview

> **Last Updated**: 2026-10-08
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
| Categories / helpers | Workout → category, moving time, effort km | `backend/engine/overview.py:75` |
| Periods | Week (Monday) / month / year buckets and totals | `backend/engine/overview.py:228` |
| PMC | The chart expressions `ctl` / `atl` / `tsb`: the `tl()` recurrence from the shared start (SP-68) | `backend/engine/overview.py:290` |
| Week plan | Volume target, session template, done-matching, day placement, projection | `backend/engine/overview.py:1328` |
| Plan preferences | 課表偏好: shape the template (counts, caps, terrain), place on allowed / preferred days | `backend/engine/plan_prefs.py:504`, `backend/engine/plan_prefs.py:715` |
| Blackout days | 不排課日期 / 休息日: validation, blocked days, lost-day volume, move-to for stored sessions | `backend/engine/blackouts.py:77`, `backend/engine/blackouts.py:223` |
| Same-load conversion | Easy-HR time model per terrain, design km / climb for a time, LOO backtest | `backend/engine/equivalence.py:250`, `backend/engine/equivalence.py:316` |
| Multi-week projection | Rolls the week-plan rules forward to the horizon | `backend/engine/projection.py:471` |
| Reconcile | Pure rules: stored plan vs regenerated weeks vs activities | `backend/engine/reconcile.py:101` |
| Plan store | Table I/O, edits, tombstones, expired deletes, manual link, stored-plan summary | `backend/engine/plan_store.py:150` |
| Workout-sync provider | The active push target (`plan.push.provider`, default COROS; Garmin / intervals.icu are disabled stubs) | `backend/sync/workout_targets/__init__.py:18` |
| COROS push | Session → structured COROS workout, idempotent push / remove | `backend/sync/coros_workouts.py:1096` |
| Compliance | Planned vs actual per session / week, the 課表統計 dashboard | `backend/engine/compliance.py:93`, `backend/engine/compliance.py:284` |
| API | Memoised Status, the endpoints, the pages | `backend/api/overview.py:45`, `backend/api/plan_sessions.py:42` |

The dataset is the chart pages' shared instance (`backend/api/wko5views.py` `_dataset()`), so the
engine config / parity mode is the same everywhere.

### Caches, single flight and the 課表 timing (SP-362, 2026-10-08)

- **Single flight.** The Status (`_status`, `backend/api/overview.py:45`; 9–13 s cold on the NAS)
  and the stored-plan inputs (`_compute_inputs`, `backend/api/plan_sessions.py:71`; 3–4 s) keep
  their keyed caches, and a cache miss now runs **one** computation per key
  (`SingleFlight.do`, `backend/singleflight.py:33`; `backend/api/overview.py:87`,
  `backend/api/plan_sessions.py:85`): the calendar, the suggestion calls, the warm-up thread and an
  automatic plan run asking for the same key wait for that one result. An exception reaches every
  waiter and is not cached (the next call computes again); a same-thread re-entry runs inline.
  The callers are sync functions in the thread pool or the warm-up thread (never the event loop).
- **Warm-up order** (`warm_up`, `backend/api/wko5views.py:293`; at start-up and after a sync with
  new files): Dataset → Status → plan inputs; that thread then ends (so a warm-up asked for
  meanwhile is not dropped) and a low-priority thread (`_low_priority`,
  `backend/api/wko5views.py:254`) waits until an automatic plan run has ended
  (`plan_auto.busy`, `backend/engine/plan_auto.py:1104`), re-reads the Dataset, and runs the
  never-fitted calibration and the activity auto-classification; a warm-up during that wait
  makes it run once more. The inputs' flight computes exactly its caller's key
  (`_inputs_key` → `_build_inputs`, `backend/api/plan_sessions.py:161`, `:201`). After a sync the runner starts the warm-up before the automatic
  plan run and the calibration (which waits for the plan run, `backend/engine/calibrate.py:348`);
  see wko5-coros-sync.spec.md.
- **Files stamp.** `_dataset()` no longer scans the FIT folder on every call: the scan is kept
  `FILES_STAMP_TTL_S` = 5 s while the source folder and its year folders keep their mtimes, and
  imports / purges drop it (`files_changed`, `backend/engine/wko5expr/datasource.py:152-182`).
- **One `/suggestions` per page load.** The floating box and the 課表's 排入測試 ▸ share one GET
  (`AppSuggestions.load(fresh)`, `backend/static/suggestions.js:203`; `sugCount`,
  `backend/static/schedule.html:2929`); a later reload or 排入 asks again.
- **Calendar timing.** `GET /plan/calendar` (`backend/api/plan_sessions.py:2840`) logs one line per
  request to the app log (`applog.phases`, `backend/applog.py:321`):
  `calendar took 4.2 s days=42 | inputs …, lock_wait …, reconcile …, view …, extras …,
  suggestions …, other …` — WARNING from 3 s; a stale answer (below) adds `stale=sync` / `stale=day`.
  The inputs, the writer lock wait and reconcile / match / snapshot (`_sessions_body`,
  `backend/api/plan_sessions.py:579`) add their phases through a context variable (`_TIMING` /
  `_took`), so the week view's own response is unchanged.
- **Stale view while the plan is recomputed (B4, 2026-10-08).** `GET /plan/calendar` alone sets
  `_STALE_OK` (`backend/api/plan_sessions.py:60`, `:2840`). When its inputs miss the cache only because
  the data changed (a new dataset generation: a sync imported files; or the 跑後自評 stamp) or the
  day changed, and the user's own settings in the key are the same (key layout: tenant,
  generation, day, self-ratings, then the settings from `_K_SETTINGS` on — `_inputs_key`,
  `backend/api/plan_sessions.py:161`; a test on the real key guards the split), it answers the
  tenant's previous computed inputs (`_last`, kept by `_remember`, `:91`) marked `stale` and starts
  the fresh computation in the background through the same single flight (`_stale_view` /
  `_refresh`, `:106`, `:130`): once per key; a request or `plan_auto` computing the same key is
  joined. Bounded: `_last` is an LRU of `_LAST_MAX` = 64 tenants (`:61`), a demo tenant (one per
  visitor) is never kept nor served stale, and the background computations share a 2-thread pool
  (`_REFRESH_POOL`, `:63`). The response gets `stale: {reason: sync | day, age_s, since}`.
  **Display only**: no `_ensure` reconcile, no `match_only`, no 每週課表存檔, nothing saved
  (`_sessions_body`, `:579`); the phases / goal of the view come from the tenant's previous Status
  (`_status_for`, `:2604`; `status_peek`, `backend/api/overview.py:90`). Every other caller — GET
  `/sessions`, reconcile, the edits, push, `plan_auto` — gets fresh inputs (it waits for the
  flight), so a write never uses stale data. A failed background computation is not retried by the
  stale path: the next calendar load computes it in the request and reports the error.
  `GET /plan/fresh` (`:615`) = `{updating}` (computes nothing). The page (`staleWatch`,
  `backend/static/schedule.html:979`) shows a 「同步後更新中」 / 「換日更新中」 badge (`stale-badge`, the
  computed-at time in its hover) and, while stale, refuses drag / edit / the context menu
  (`editable`, `:1026`; the capture guard, `:3054`: 「課表更新中，幾秒後再編輯」), the suggestion
  box's 排入 (`:3063`), 推送 / 重新計算 / 刪除所有過期未完成 / 移除推送 (disabled in the toolbar);
  it polls `/fresh` every 3 s and reloads the calendar when done. After 5 min without a fresh
  result it stops polling, drops the edit lock (the server computes fresh inputs for any write)
  and the badge becomes 「重新整理」 (click reloads, `:1006`). A deploy / restart has no previous
  view: that first load still computes in the request; the Dataset build itself (`_dataset()`) is
  still waited for.
- **COROS push outside the writer lock (B3, 2026-10-08).** COROS takes 20–30 s on the NAS. The
  plan writer lock (`_wlock`) now covers reconcile + save only; every push to the watch runs under
  a separate push lock (`_plock`, `backend/api/plan_sessions.py:472`) and re-reads the stored plan
  once it holds it: the automatic run (`plan_auto._push_after`), SP-358's `_sync_watch`
  (`:2173`), the manual push (`:2209`), unpush (`:2242`) and the removal after deleting expired
  sessions (`_unpush_expired`, `:1973`). The page's GET `/sessions` / calendar and the user's edits
  never wait for COROS. An edit saved while a push runs wins: the push never writes sessions, it
  sends the stored rows as they were when its turn came, and the edit's own `_sync_watch` queues
  on `_plock` and re-sends the edited copy (the fingerprint skips a copy already up to date, so
  the same version is not uploaded twice). Never take `_wlock` while holding `_plock`. The
  automatic run checks inside `_wlock` that its inputs (computed before it) are still current —
  their `inputs_key` (`:258`) against `inputs_key_now` (`:149`) — so a 不排課日期 / 課表偏好 / B2B
  saved meanwhile, or newer data, is never reverted (see plan-auto.spec.md › Safety).

## Categories

`category()` (`backend/engine/overview.py:75`) is the activity's platform-neutral app type
(`sport_map.app_type`, `backend/engine/sport_map.py:292`, SP-263; workouts.spec.md): a trail run by
the app's trail / road classification (`classify.is_trail`; a run that is not trail is road whatever
the platform says), else the platform's own sport code (COROS `sportType`), the FIT sport /
sub_sport, the WKO5 names, else other — 路跑 road, 越野跑 trail, 登山健行 hike, 騎車 bike,
肌力 strength, 走路 walk, 其他 other (the same keys as `CATEGORIES`). Colours are the validated
categorical palette in fixed slot order (`backend/engine/overview.py:39`).

- **Endurance** = road, trail, hike, bike (intensity split, longest session, quality-session
  matching). **Foot** = road, trail, hike (effort km).
- **Effort km (EP)** = km + gain/100, the 健行筆記 / ITRA convention (`backend/engine/overview.py:102`).

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
  low < AeT, mid AeT–LTHR, high ≥ LTHR (`backend/engine/overview.py:60`). The per-workout
  aggregates are disk-cached by the evaluator.

## PMC

`pmc()` (`backend/engine/overview.py:290`) evaluates `ctl`, `atl`, `tsb` with the evaluator
(full history, constants from the athlete: 42 / 7) and daily TSS with the `tl()` input rule
0 ≤ x ≤ 5000 (`backend/engine/overview.py:280`). TSB is yesterday's CTL − ATL.

**LTHR prior note (SP-289).** `marks` = [{date, kind: lthr_switch, text}] — the day the LTHR prior
(0.90 × max HR, `fitdataset.lthr_prior`) gave way to a real LTHR (an estimate grid day or a plan
row); hrTSS / CTL change scale there. The overview page draws it as a dashed line with the text in
the legend. Where the prior comes from (SP-289, `hr_profile.lthr_prior`): LTHR = test → estimate
from the runner's data → watch account → 0.90 × max HR, only without a watch LTHR, from the first
run to the first real value; max HR = 設定 → watch → runs → 208 − 0.7 × age (Tanaka, 「推估（年齡公式，
個人可以差 10 bpm 以上）」). `training_targets` reads it before any run too (`lthr_prior` flag); the
easy / long runs then carry the talk test; `threshold_confidence` rates it low (`prior` / `age`), and
a new runner (no 4 good weeks in the data) is suggested the LTHR test from week 5 of the data (week 2
with ≥ 3 h a week and a race result in the questionnaire; `cold_start.lthr_test_from`).

**Start values (SP-68).** CTL / ATL start from `load_guard.pmc_start()`
(`backend/engine/load_guard.py:216`), one source order:
1. **manual** — the user's CTL / ATL at the start of a date (設定 → 閾值 → 起始 CTL／ATL;
   user_settings `athlete.pmc_start` = {date, ctl, atl}, 0–300, date ≤ today). The series
   restarts on that date; the days before it keep the automatic start. A date after today is
   ignored.
2. **auto** — SP-63's seed, now on both lines: CTL = ATL = the mean daily TSS of the first 28 days
   from the first day with TSS (days ≤ today only, so it is final after 4 weeks; 推估, Coggan
   gives CTL / ATL a starting value instead of 0).
3. **none** — no TSS yet: 0.

The start lives in the evaluator builtins (`Evaluator.pmc`,
`backend/engine/wko5expr/evaluator.py:2511`), so this PMC, the PMC charts, `status`
(體能 CTL ramp, 狀況 TSB), `week_plan`'s TSB guards (below), `b2b`, the projection and
`plan_store.plan_summary` read the same numbers; the guardrail ramp (`load_guard.guard_ramp`) reads
the same CTL series. The response carries `start` {source, date, ctl, atl}. Where they still
differ on purpose: an expression's own `tl(tss, ctlconstant)` stays WKO5's (0 before the first
input); a sport-filtered evaluator / `sport(x)` PMC uses the automatic seed, not the manual
value (that one is the whole athlete's); the injury-exposure model keeps its own run-TSS PMC
from 0 (`backend/engine/injury_exposure.py:295`). `GET / PUT /api/v1/plan/pmc-start`
(`backend/api/plan.py:729`) serve the settings card: the start in effect, the automatic seed,
today's CTL / ATL / TSB. The status and chart render caches key on the manual value.
`project()` (`backend/engine/overview.py:329`) continues the recurrence with planned daily TSS.

The stored-plan projection is `plan_store.plan_summary()`
(`backend/engine/plan_store.py:702`): today's CTL / ATL continued with the TSS of the **stored**
active sessions on each day after today, up to the horizon, so edits change it. It replaces
`week_plan()`'s own week targets, Sunday CTL and next-Monday TSB once the stored plan has loaded
(`paintPlanLoad`, `backend/static/overview.html:698`). The overview's PMC chart itself shows only
the last 90 days (CTL / ATL lines, TSB bars in the PMC's Form% colours; `loadPmc`,
`backend/static/overview.html:907`) — no projection line (2026-10-03).

## Week plan (`week_plan`)

Inputs: the computed `Status` (phase kind, goals, indicators), the last 8 complete weeks of
moving hours / TSS, today's CTL / ATL / TSB, the 課表偏好 `prefs`, the 不排課日期, the accepted
B2B weekends, the race calculator (for the 專項期 target) and the 主要訓練項目 `sport`
(`backend/engine/overview.py:1361`). `prefs=None` or the defaults run exactly the rules below.

**Volume target**
1. Base / specific: the weekly TSS that raises CTL by the phase goal (base max(2, 5 % of CTL),
   specific max(2.5, 7 %) per week, 推估 — SP-63; `load_guard.ramp_goal`,
   `backend/engine/load_guard.py:363`) — `7·(CTL₀ + Δ/(1 − (1 − 1/42)⁷))` —
   converted to hours with the athlete's TSS per hour over 6 weeks.
2. Capped at `max(1.10 × ref, ref + 0.5 h)`, ref = max(4-week mean, last week) (10 % 推估: a systematic
   review found no evidence for the 10 % rule — periodization-cross-sport.md [169]) over
   normal weeks only (SP-73, owner 2026-10-05): a week touching a 減量期 / race week / post-race
   恢復期 / 轉換期 is left out and the most recent normal weeks before it count instead
   (`_normal_weeks` → `load_guard.normal_weeks`, up to 26 weeks back; returned as
   `target.ref_weeks` for the projection). So the first base week after a 轉換期 can climb back
   toward the pre-race level as fast as the CTL goal asks (≤ +10 % of it). Floored at the 4-week
   mean (hold).
3. Guards: TSB < −30 → recovery week (60 % of the 4-week mean); TSB < −20 → hold (TSB from the
   started PMC above — SP-63 Q3: a new user's first weeks no longer read a CTL still filling up
   from 0 as a false TSB < −30, `backend/engine/overview.py:1411`); base: three
   building weeks in a row → recovery week (65 % of their mean, 3:1 cycle). 專項期 (SP-97): no 3:1
   from the history — 賽前第 5、3 週 are the recovery weeks (`specific_phase.EASY_WEEKS`, FRAC's low
   points; not right after another light week), so one never takes the week 4 long day. Both:
   after 6 weeks without a recovery week the next is one (Koop; a 專項期 week waits for the
   countdown week right after it; `recovery_reason`, `weeks_since_recovery` — a week ≤ 80 % of the
   3 before it, or touching a 減量期 / race / 恢復期 / 轉換期, counts). An accepted B2B's
   own TSB drop is exempt (`B2B.tsb_exempt`, `backend/engine/overview.py:1456`).
4. Taper: 50 % of the 6-week mean (40 % in the last 7 days to the A event); event week 30 %;
   recovery 50 %. **Transition** (SP-73, `backend/engine/overview.py:1499`): 50 % of the race's
   pre-race level (`TRANSITION_SHARE`, 推估 — the recovery share; Friel 「for fun rather than
   fitness」): the mean of the 4 complete weeks before its taper (`planning.pre_race_mondays`,
   `backend/engine/planning.py:1082`; `transition_hours`, `backend/engine/overview.py:462`), not
   of the last 4 weeks (they hold the taper, race and recovery and would shrink the phase week
   after week); no race known (a manual 轉換期) → the old 65 % of the 4-week mean. Easy runs
   only, each ≤ 60 min (`TRANSITION_RUN_MAX`, Canova's 4 weeks of easy running ≤ 1 h; enforced
   after the 課表偏好 shaping too, `cap_transition_runs`, `backend/engine/overview.py:492`),
   strength ×2, no long run, interval or CP-test suggestion; a week note says so and
   that cross-training may replace an easy run (`src: transition`). From the phase's 2nd week
   (`transition_week`) the week's first easy run ends with 「＋加速跑 4×15 秒」 (~5K pace, after
   20–30′ easy; `TRANSITION_STRIDES`, SP-103: Jay Johnson 3–5 × 15 s from week 2, 教練級; the
   cyclists' one-sprint-session-a-week trials, 推估 for running), with a note; never in week 1 or
   the 恢復期. A 課表偏好 越野 easy run keeps these strides (not the base hill sprints).
5. A custom weekly-hours preference only lowers the result (`backend/engine/overview.py:1513`).
6. A break ≥ 6 days without running — a 不排課日期 range or simply no runs — gives the re-entry
   block instead (`reentry.find`, `backend/engine/overview.py:1391`; Daniels; plan-auto.spec.md);
   the week after the block goes back to the pre-break volume. Open injuries add a week note
   (`injuries.week_notes`, `backend/engine/overview.py:1527`).
7. 不排課日期 (`blackouts`, `backend/engine/overview.py:1546`): this week's lost days scale the
   target (see 不排課日期 below).
8. **冷啟動** (SP-288, `engine/cold_start.py`; docs/research/cold-start.md §4.2): the week the data
   starts (no run / hike in the 28 days before this Monday, no re-entry block with a previous volume)
   = the 跑步經驗問卷's runs × minutes as entered (not discounted; 「能連續跑 30 分鐘」 → ≥ 1.5 h),
   else 1.5 h, 3 easy runs, no long run, with a week note (`src: cold_start`, also on the 課表 page);
   「還不能連續跑 30 分鐘」 → the same default and 「app 的自動排課要等你能連續跑 30 分鐘之後才準」
   (no run-walk sessions). The 4 weeks from the data's start (the ramp) take max(start level, actual)
   as the base and step by the volume cap itself (+10 %, at least +0.5 h, 3:1), ≥ 3 runs a day apart
   (more when the questionnaire reported more), no intervals; a long run only when longer than the
   other runs. No CP-test suggestion in those 4 weeks (week plan, its testing note, the 基線測試 and
   zone-retest box rows; owner 2026-10-06) — the base phase's hill strides stay, the AeT test keeps
   its rule. The projection reads `cold_start` and applies the same. A runner with history:
   `cold_start` None, nothing changes. Any week < 120 min: long run ≤ 40 % of it, no 60-min floor.
9. **資料等級** (SP-291, `engine/data_level.py`; docs/research/cold-start.md §4.1): ONE function
   (`data_level.level(ds, today)`) gives `{level, week, since, need, runs, weeks_ok, survey}`, read
   on this week's Monday; the week plan (via `cold_start.week_context`), the status page
   (`Status.i_level`) and the race feasibility read it — no module decides 「not enough data」 on
   its own. 0 沒有資料 = no run / hike in the 28 days before this Monday; 2 正常 = the Zone 3 gate's
   consistency path (`quality_gate.z3_consistency` with `z3_rule`, the 進階設定 values — default
   4 complete weeks with ≥ 3 sessions each, no 7-day gap, a ≥ 21-day break starts over) on the
   run AND hike days; 1 累積中 = in between. `week` = the week of the current stretch of data
   (`data_start`). `survey` (the questionnaire's start level stands in, i.e. `cold_start` is set):
   level 0, and level 1 up to week `need` (the rule's weeks) — level 2 or later weeks never use
   the questionnaire (a level-1 runner past week `need` is planned on their own records, 推估).
   The cold / ramp week's note is the level's one line: 「你的資料還在累積（第 n 週／4）：週量依你填
   的問卷，心率區間是推估」 (「週量從預設的每週 1.5 小時起算」 without the questionnaire's volume after
   the cold week; 「，心率區間是推估」 only with the SP-289 LTHR prior); the cold week without the
   questionnaire's volume keeps its 「還沒填跑步經驗」 / 「還不能連續跑 30 分鐘」 note. `data_level`
   (with its `label`) is in the week plan's output and in `Status.to_dict()`.

**Sessions** (dataclass `Session`, `backend/engine/overview.py:603`; `terrain`, `distance_km`,
`climb_m` added for the preferences / conversion, `protocol` for tests, `heat`, and the
interval-library `variant_*` fields — plan-auto.spec.md §Interval library)
- Base / specific (not a recovery week): one **LSD** (the long run's label since 2026-10-03, was
  長時間輕鬆; 30 % of the week, ≥ 60 min, ≤ 1.15 × the longest of the last 28 days; specific:
  toward 70 % of the goal event's hours, ≥ 90 min, or the 專項期 race target below), terrain from
  the goal's climb density (「LSD（山路）」 with a mountain goal); 路跑 uses `road_long_session`
  (`backend/engine/overview.py:1217`). Then one of (`backend/engine/overview.py:1628`):
  1. specific, Zone 5 not confirmed → the **Zone 3 ladder** (台灣教練: Zone 3 first);
  2. specific (SP-75) → the two ladders as in the base phase, 越野 the rung's uphill version, 路跑
     flat; the 後段's race ratio (plan-auto.spec.md);
  3. base → the **間歇門檻**'s dose step as an interval-library variant fitted to the weekday
     cap (`_gate_session`, `backend/engine/overview.py:859`, `backend/engine/overview.py:1647`;
     see below).
  - **Recovery week** (base and 專項期, SP-97): a shorter long run (`recovery_long_minutes`: 65 %
    of the usual, ≥ 45 min; 專項期 ≤ FRAC's share; easy, no MP segment) and the gate's
    「恢復週 fartlek 4×1 分」 instead of intervals (Palladino; the Norwegian coaches keep the
    sessions and intensity, shorten each). The projection doesn't lower the next long-run base.
- **Tests are suggested, never planned** (2026-10-01/02): a due CP test (`testing` bad / watch,
  `extra.cp_due`, A event > 10 days away, not inside a re-entry block) and a due AeT test
  (`aet_test.due`, for a reason only) become `test_suggestions` (`backend/engine/overview.py:2269`)
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
  `backend/engine/workout_review.py:177`). `week_plan` reads status `i_gate`'s dict
  (`backend/engine/overview.py:1643`) and asks `week_decision`
  (`backend/engine/quality_gate.py:2509`) for this week:
  - **Method** (`plan.prefs.quality_gate`, `evaluate`, `backend/engine/quality_gate.py:1132`):
    `auto` → `ua_gap` + `friel_drift` when the plan has a measured AeT row that is **valid**
    (B3, `unsourced-rules.md`: the aggregated drift estimate's SE ≤ 3 bpm and no shift > 5 bpm
    over the last 6 points — `drift_agg.aet_validity`, 推估; no fixed 16-week expiry; stale after
    a break ≥ 4 weeks) (`aet_info`, `backend/engine/quality_gate.py:262`) and LTHR is not WKO5's default
    (`lthr_info`, `backend/engine/quality_gate.py:300`), else `none`. `ua_gap`: LTHR / AeT − 1
    ≤ 10 %; `friel_drift`: one run in 8 weeks, avg HR AeT−5…AeT+3, ≥ 70 min, fair drift < 5 %
    (`friel_check`, `backend/engine/quality_gate.py:389`); `xu_drift`: flat ≥ 90-min run,
    (HR@90′ − HR@10′) / HR@10′ < 10 % (`xu_drift_of` / `xu_check`,
    `backend/engine/quality_gate.py:439`, `backend/engine/quality_gate.py:436`); `plateau`: ≥ 8
    base weeks and EF change < +2 %; `weeks`: > N base weeks (evaluated per projected Monday);
    `none`: guardrails only. States: unlocked / locked (data there, criterion not met) /
    missing. **Forced mode with missing data → `fallback`**: i_gate WATCH with the reason and
    the guardrail plan (our own choice: never a permanent lock).
  - **Guardrails** (`guard`, `backend/engine/quality_gate.py:675`), base phase, every mode:
    low-intensity time share < 75 % (or run power < 80 % CP share < 75 %) → no Zone 5, Zone 3 goes
    on with a 「輕鬆跑心率偏高」 warning note (SP-31: 75 % is the floor, the base phase's ≥ 90 % a
    target; the AeT is often estimated, climbs inflate HR) — with an untested AeT in effect a
    warning for Zone 5 too (SP-39, `guard(aet_tested=False)`); CTL ramp ≥ max(3, 10 % CTL₋₇) →
    threshold only, ≥ min(10, max(5, 15 % CTL₋₇)) → none (Friel as a share of CTL, 推估; not in the
    first 28 days of data — `backend/engine/load_guard.py:216`); last week's running-time step
    against max(the week before, 4-week mean) of normal weeks (no 減量期 / race / 恢復期 / 轉換期 week,
    SP-73) > 20 % → none (Nielsen 2014, Damsted 2019), 10–20 % →
    hold the dose (推估) — not the week after a 3–5-day break without a run, which gets an info note
    instead (`src: "volume"`, plan-auto.spec.md); TSB −30…−20 → hold (Friel / TrainingPeaks). Projected weeks keep only the
    intensity block (Zone 5 only).
  - **Two gates, two tracks** (SP-31): Zone 3 once its gate is open (`z3_gate`,
    `backend/engine/quality_gate.py:1489`: 4 complete weeks with ≥ 3 runs and no 7-day gap —
    sticky, a ≥ 21-day break re-locks; post-race 恢復期 / 轉換期 days are no gap, SP-73 —, the 90-min test, or a measured UA gap; all 推估 but the
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
    Zone 3 + Zone 5 ≤ 20 % of the week (`quality_sessions`, `backend/engine/overview.py:957`).
    Weekly: `week_decision(..., n)` (`backend/engine/quality_gate.py:2502`) — 課表偏好 2 a week =
    one of each (`quality_per_week`, `backend/engine/overview.py:1165`), 1 a week with both open
    alternates 1:1 (A race road ≤ 10 km) or 2:1 (`track_ratio`, `backend/engine/quality_gate.py:1649`).
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
    (`session`, `backend/engine/quality_gate.py:2734`); the detail prefix names the rule
    (`prefix`, `backend/engine/quality_gate.py:2770`). In guardrail mode `plan_prefs.shape`
    gets `quality_cap=1` (`backend/engine/overview.py:1854`).
  - 專項期: the same two-track pick on the ladders (SP-75: trail = the uphill version; 前段 / 後段 ratios,
    plan-auto.spec.md); drift bad → none, intensity bad → no Zone 5; this week's CTL ramp
    at the block line / volume step > 20 % → none and at the watch line → threshold only, on both
    tracks (owner 2026-10-04).
  - **Why no Zone 3** (SP-31): `week_decision`'s `z3_note` (the gate with its progress, a
    guardrail's verdict, 「本週輪到 5 區」, the recovery week) is a week note (`src: z3`); the
    low-share warning is `src: intensity`, the Zone 3 / week-total caps `src: z3` / `quality_share`.
  - Returned as `quality_gate` (the gate dict + `levels`, `allowed`, `this_week`,
    `this_week_tracks`, `quality_n`, `aet_test`) for the projection
    (`backend/engine/overview.py:2330`).
- Taper: one session by the two-track pick — Zone 3 有氧間歇（巡航）2×8′ (88–95 % CP), Zone 5 or no track
  open 有氧間歇（巡航）4×3′ (98–102 % CP, intensity unchanged; renamed from 「短強度 4×3 分」 2026-10-05 —
  its short reps read as 有氧間歇・巡航 to `family_of`; a stored old title is mapped on read). Event week: the race.
- Strength ×2 in base / transition / recovery or when the `strength` indicator is bad / watch,
  else ×1 (not counted in the hours). **No strength in the 14 days before an A event**
  (`STRENGTH_STOP_DAYS`: the 減量期 and the race itself; SP-86, Bompa & Buzzichelli
  《Periodization Training for Sports》 p.184 — long endurance events can stop strength 2 weeks
  before the main race — and p.327 — none in the taper's second week). After the placement,
  `drop_strength_before_a` (`backend/engine/overview.py:2708`) removes every not-done strength
  session on a day from `strength_stops` (`backend/engine/overview.py:2392`: each A event's first
  day − 14 to its last), 課表偏好 每週肌力 / 肌力日 included, with a week note (`src: strength`);
  a week partly inside keeps the strength days before the window. B / C events are unchanged
  (the book's 「主要比賽」; applying it to them would be 推估). week_plan returns the windows as
  `strength_stop` and the projection applies the same rule per week. Removing a stored strength
  session is a reduction, so auto-adjust does it without asking (plan-auto.spec.md).
- Easy runs fill the remaining minutes in 40–60 min sessions; in base the first one carries
  8×10 s hill strides.
- Targets per session come from `zones.training_targets` (CP / LTHR / AeT, estimate-aware),
  formatted by `_targets` (`backend/engine/overview.py:825`). The easy-run cap is the
  **課表心率區間**'s Z2 top (設定 → 心率: COROS % LTHR / % HRR / % HRmax, `engine/hr_profile.py`)
  unless an AeT was measured; session texts call it 「輕鬆跑上限」, with 「（實測 AeT）」 only
  when measured (2026-10-03, `backend/engine/overview.py:1469`), and a week note says which model
  set it when it isn't LTHR.
- A season-plan threshold row applies from its own date on, never to earlier days
  (`planning.Plan.threshold_on`, `backend/engine/planning.py:779`, fixed 2026-10-01). Before the
  first row, `Dataset.setting` / `cp` / `aethr` fall back to WKO5's dated settings (runthr, the
  current mFTP snapshot, 0.89 × LTHR). Today's thresholds come from the last run moved to today
  (`zones._on_day`, `backend/engine/zones.py:190`; also `status._aet_now` and the LTHR estimate's
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
  section (`backend/static/settings.html:241`, between 心率 and 資料同步) is the one editor of
  `plan.thresholds`: the dated table (LTHR / AeT / CP / note), the LTHR / AeT 自動估算 cards with
  套用, the WKO5 settings line, 怎麼測 and the Palladino power-zone table. It reads
  `GET /api/v1/plan/thresholds` and saves with the whole-table `PUT` as before. 最大心率 is edited
  only in 設定 → 心率 (`hr-profile`): the table has no max-HR column, hides rows that hold only
  `mhr` / `rhr` but sends them back unchanged, and 移除 on a row that also holds them clears only its
  LTHR / AeT / CP; a 心率 save reloads the table so a stale copy never overwrites it. The 賽事周期
  page (`backend/static/plan.html:190`) shows a read-only line — what is in effect, the latest test
  date and count — with 「到設定修改」 (`/api/v1/wko5/settings#thresholds`); the status action for a
  default LTHR points to 設定 too (`backend/engine/status.py:1071`). No data-model change.
- With active preferences the template is then shaped by `plan_prefs.shape()`
  (`backend/engine/overview.py:1855`; see 課表偏好 below).

**Session decorators** (2026-10-02/03; each a small hook module, also run per projected week):
- **主要訓練項目** (`engine/primary_sport.py`, setting `athlete.primary_sport` auto / trail / road;
  auto = the future A races decide (only 越野賽 / 百岳 → trail, only 路跑賽 → road, both → the
  harder race by `planning.event_size` tier, then predicted hours, then EP; tie / unknown size →
  trail), with no future A race the B races the same way, else trail + hike ≥ 25 % of 12 weeks'
  foot time, 推估; C races never count — SP-245): 路跑 =
  no B2B, no steep-hill walk, no mountain long run / uphill interval versions; the 專項期 LSD
  carries a marathon-pace segment (Pfitzinger / Daniels; SP-75 `MP_PLAN`: 20 / 25 / 30 % of the run at
  賽前第 10 / 9 / 8 週, 35 % at 6, 40 % at 4, all easy at 7 / 5 / 3 and before a road race ≤ 10 km; 40 %
  without a known race; within 20–75 min, 推估;
  the A road race's goal pace when ≥ 30 km, else threshold pace × 1.04–1.08), and base strides
  are 「加速跑 6×20 秒」 (`ROAD_STRIDES`, `backend/engine/overview.py:1266`).
- **專項期** (`engine/specific_phase.py`): the LSD follows the next A race's コース定數 (the race
  calculator's single-day target; a 推估 share per week from week 10 to 3 before the race, still
  ≤ +15 % over the 4-week longest); the race GPX's longest climb becomes one 長爬坡反覆 easy run,
  which says how the race climbs it (SP-227, `race_gait`: runwalk.gait on the climb's grade × the
  race day's climbing speed, 「比賽時這段每小時約 N m、G%：用快走／走跑皆可／用跑的」; the old
  「用比賽的走／跑方式」 without a race time); a race simulation 4–3 weeks out is a suggestion
  (`race_sim_suggestion`).
- **爬坡走路提示** (SP-298, `engine/walk_hint.py`; run-walk-threshold.md §5.4 #2): a planned hill easy
  run / long run (kind easy / long, terrain trail / hike or a 山路 / 越野 title; never road / flat)
  gets `walk_hint` in the 課表 API view (`api/plan_sessions._view`, shown after the detail on the 課表
  calendar and the overview tooltip) — 「提示：照你輕鬆心率的爬升速度（約 N m/h），坡度超過約 X% 用走的
  比較省（預設值｜依你的跑走紀錄校正）；規則仍是心率上限」. N = the median VAM of the last 90 days' trail-run
  sustained climbs (`climb_vam`, ≥ 8 %, ≥ 8 min) with average HR ≤ the plan's easy cap, ≥ 3 climbs
  (both 推估), else no hint; X = `runwalk.walk_grade(N, shift)` — the smallest grade ≥ 3 % whose gait at
  that rate is walk (the SP-226 PTS line; SP-228's shift only when personal). Display only: the stored
  detail, the HR-cap rule and the pushed workout are unchanged; cached per dataset day and cap
  (`_walk_hint`).
- **B2B 連續長天** (`engine/b2b.py`): a due B2B weekend is only a suggestion (課表偏好 `b2b`
  off = never); an accepted one is stored as the user's two sessions (`plan.b2b.accepted`) and
  the rest of the week is planned around it (day 2 out of the easy minutes, 4 easy days after).
- **陡坡健走（模擬負重）** (`engine/steep_hill.py`): before a 百岳 / multi-day trip, one weekday
  easy run of a 專項期 week becomes a 40–50 min steep walk at the Pandolf grade that costs what
  the pack would (no pack in training).
- **熱適應課** (`engine/heat_plan.py`): below.
- **下坡課** (SP-99, `engine/downhill.py`; after placement, just before the 技術地形課, in week_plan
  and per projected week): 專項期 賽前第 9、6、3 週 (`DOWNHILL_WEEKS`: ≤ 3 weeks apart — one
  downhill run protects 3–6 weeks, not 9, controlled trials — and the last 14–21 days out, 推估)
  before an A race that is not road and descends ≥ 20 m/km (推估; GPX, else descent = climb);
  主要訓練項目 路跑: none. One easy run becomes SP-62's 下坡離心 (−10～−15 %, RPE 3–5, warm-up /
  cool-down 10′, the template's steps; 25′ downhill, the first one of the phase 15′) on a day ≥ 14
  days before the race, not on / the day before a hard day, not in the easy days after a B2B; the
  first keeps the 2 days after it easy. The weekday cap shrinks the downhill part (≥ 10′); the
  other easy runs give the extra minutes. The 技術地形 session keeps ≥ 2 days from it
  (`technical.HARD_IDS`). A week note (`src: downhill`) says when / why or that no day fit.
- **技術地形課** (SP-74, `engine/technical.py`; applied last, after placement, the climb / steep
  walk and heat hooks, `backend/engine/overview.py:2132`, and the same per projected week,
  `backend/engine/projection.py:780`): 主要訓練項目 越野跑 only (路跑: none), base / 專項期, not
  in a recovery / re-entry week (`week_context`, `backend/engine/technical.py:70`).
  - **基礎期**: every other week (an even ISO week number, `base_week`; 推估) the week's LSD
    becomes 「技術地形 N′（低 RPE 3–4）」 of the same minutes on the same day — it stays the `long`
    session (an easy one, so the long-run rules hold) with the structure warm-up 10′ + RPE 3–4
    work (+ ≈ 5 m climb per minute, the template's 300 m / 60′) + 5′, no HR / power target. Not
    in a B2B week, when the long run is done or carries the heat session.
  - **專項期**: one session a week out of a placed easy run (id `tech`, kind `hike` 越野跑),
    near the race's terrain. RPE 6–7 (a quality session by `workout_steps.rpe_role`) when an easy
    run's day is ≥ 2 days from the long run, the B2B days, the race-climb repeats, every quality /
    test session and the hard runs done this week, and the week's budget has ≥ 30′ left
    (`SPEC_WORK_MIN`, 推估): work = min(90′ (the template), 20 % of the week − the intervals'
    time in zone (`budget_room`, `backend/engine/technical.py:214`; the technical work is not
    put in the Zone 3 ≤ 10 % bucket — HR stays low on technical trail, SP-62; 推估), the day's
    cap − 25′); weekend days first; the other easy runs give the extra minutes (≥ 20′ each).
    Otherwise the same session at RPE 4–5 (an easy one) on that easy run's time. A week note
    (`src: technical`) says which and why (budget left, no spaced day, the day cap).
  - **The user's own** (SP-74 follow-up): a stored 技術地形 session the user added (custom) or an
    auto one they edited, kind easy / long / hike, active or done, whose structure makes it a
    quality session (`workout_templates.session_role`: an RPE work step ≥ 7) counts like the
    generated one. `plan_store.user_rpe_rows` (`backend/engine/plan_store.py:870`, read-only) →
    `user_quality` (`backend/engine/technical.py:136`) per week; its RPE ≥ 7 work
    (`user_work_min`, `backend/engine/technical.py:113`: timed / estimated steps, a step with no
    time → the whole session, 推估) is `reserved` in `quality_sessions`
    (`backend/engine/overview.py:957`): the intervals get the 20 % minus it — shortened to a 縮量版,
    or left out with a note (`src: quality_share`) when even its floor doesn't fit — and the 專項期
    gets no generated 技術地形 session that week. A week note (`src: technical`) names the
    session(s) and what is left. Same in week_plan and per projected week (a projected week
    whose intervals all went gets no fallback interval); the plan inputs' cache key includes the
    rows (`technical.user_stamp`). Not covered: a user's own interval (kind quality) is still not
    counted, and the generator's intervals don't keep 48 h from the user's session (reconcile
    only moves them off its day).
  - The generated session carries its `steps` (`Session.steps`, `backend/engine/overview.py:635`)
    so reconcile stores the structure on the auto row and the push sends time + RPE + climb.
    Load stays the watch's (SP-62).

**Done-matching** (the generated week; stored sessions are matched by `plan_match`, reconcile
rule 1): strength ← a strength workout; long (by id, so a long day of kind `hike` too,
`backend/engine/overview.py:1917`) ← an endurance session ≥ 80 % of the planned minutes; the AeT
test ← a road run ≥ 55 min (`backend/engine/overview.py:1923`); quality / test ← a session with
≥ 10 min at ≥ LTHR or ≥ 0.95 CP run power, or 60 % of the planned work for short reps
(`hard_need`, `backend/engine/quality_gate.py:2792`, `backend/engine/overview.py:1621`); a Zone 3
library variant ← its own time at ≥ 85 % CP (it never reaches 95 % CP);
a planned **Zone 5** session (library class Z5, or rung `z5*`; `quality_gate.is_z5_variant`)
← only a run classified 「Z5 間歇」 (`workout_review.classify` stimulus `z5`, owner 2026-10-02);
easy / hike (the 技術地形 session, `backend/engine/overview.py:1948`) ← any other endurance session. Week activities and `done_by` rows carry `session`
(`overview.session_of`: type, label, stimulus, dashicon), shown on the 本週 tiles / 課表 chips.
**Done hard days** (Z5 / Z3 / 高強度長跑 / CP test, planned or not; `workout_review.HARD_TYPES`)
keep the remaining interval 48 h away (`plan_prefs.place(hard_done=…)` and the no-prefs path, `backend/engine/overview.py:1973`).

**Placement**: remaining days from today (tomorrow when something is already logged today)
to Sunday. The long session goes on the athlete's usual long-day weekday (mode over 12 weeks,
`backend/engine/overview.py:800`) or the last free day; quality ≥ 2 days from the long one;
easy on the days that put the rest days where they belong (below); strength on easy days first.
Sessions that don't fit are reported as a note, not squeezed in. Active preferences place
with `plan_prefs.place()` instead (`backend/engine/overview.py:1979`). Blocked days are removed
from the candidate days first (`backend/engine/overview.py:1959`); when they leave a quality /
test session only a day next to the long one, it is dropped rather than stacked
(`backend/engine/overview.py:2028`). An accepted B2B keeps its own two days and the rest moves
around them (`B2B.place`, fixed).

**Rest days** (SP-82, `engine/rest_days.py`; rest-day-placement.md, the owner's decisions §4.4):
the run count still follows the week's minutes (easy runs ~50′, `easy_count`; 課表偏好 每週跑步次數
when set), so a week can leave 可練日 free on purpose — before SP-82 those free days were simply
what the easy runs (earliest free day first) left over, piled up late in the week. Now, in all three
placement paths (no prefs, `plan_prefs.place`, `projection._place`), the easy runs take the
combination of days with the smallest penalty (ties → earliest): a run the day after the long run
(a Sunday long run → this Monday too) 8, the day before it while the week has a rest day 4, the day
after an interval in a ≤ 4-run week 2, each training day past 3 in a row / rest day past 2 in a row
1 (weights and streak limits 推估); the 休息日偏好 (pref_days `rest`, first / second choice) 32 / 16,
above every rule. Auto mode runs ≤ 6 days (`AUTO_MAX_RUNS`, `auto_easy_cap`, `shape`'s room; 推估 —
Bompa / UA / Koop keep ≥ 1 full rest day); 每週跑步次數 7 is honoured. Strength goes on an easy-run
day first, a free day only when none fits (a 休息日偏好 day last), never the interval's day or the
day before the long run. The 課表 calendar shows 「休息」 on an empty planned day (week view /
agenda): dragged onto another day it calls `POST /rest-days/move {from, to}` — that day's active
sessions move to `from` as user moves, with a warning when a moved hard session ends up < 2 days
from another (`rest_days.swap_warnings`).

**Session TSS** (`_tss_per_hour`): a session's planned TSS = its minutes × a TSS / h rate. The
long run and the other sessions take the median TSS per moving hour of the athlete's last 180
days in that category (road / trail / hike / strength, ≥ 3 activities, else
`TSS_PER_HOUR_DEFAULT`); the quality sessions and tests carry their own fixed rates. The **easy
run** (SP-302) has its own rate `easy`: the median over the last 180 days' **genuinely easy**
runs only — the session classifier says 輕鬆跑 and not 中強度跑, or the average HR is within the
easy line (AeT + 3, `workout_review.classify` `stim.easy_hr`); a Zone 3 / Zone 5 / 高強度長跑 /
test never counts (`overview._genuinely_easy`). Road runs first; with < 3 of them, road + trail.
With < 3 in all: 推估 IF 0.80 (`EASY_EST_IF`), rate = IF² × 100 = 64 (TrainingPeaks' TSS
formula; owner 2026-10-06 — the easy cap's IF, cap HR ÷ LTHR, gave the owner 81.6, more than the
all-runs median); the week gets an info note (`src: easy_tss`) naming the rate and its basis. The
projection (`projection.week_sessions`) prices easy runs with the same `easy` rate with or without
課表偏好 (before, its default path used the all-runs `tss_per_hour`). `easy_trail` (課表偏好
輕鬆跑地形 = 越野) is the same over trail runs, else `easy`. The output's `easy_tss` = {n,
estimated, rate, source}; `plan_prefs._easy` and the editor's estimate
(`plan_sessions.tss_rates`, kind easy → `easy`, a plan from before → `road`) read the same
rates. Before, the easy run took the road median of every run — intervals and 中強度跑 too
(one runner: 79 TSS / h ≈ IF 0.89, near tempo; easy runs only: 70) — so a run kept under the
cap looked short of its plan and rule D's TSS + 20 % never fired (plan-auto.spec.md).

**Rules added after the 2026-10-04 code-sync** (synced 2026-10-08; the adjust side of the same
tickets is in plan-auto.spec.md):

- **減量期 by the race** (SP-96): the next A race's taper touching this week reads the pre-taper
  level — mean runs, climb and hours of the 4 complete weeks before the taper's week
  (`backend/engine/overview.py:690`, `backend/engine/overview.py:1380`). The run count is kept and
  each run is shorter (`backend/engine/overview.py:1835`); the last long run is ≤ 90 min easy and
  ≥ 7 days out, 14 when sore (`backend/engine/overview.py:1796`); quality by the days to the race,
  no hard downhill / climbing (`backend/engine/overview.py:2147`). The taper length follows the race
  (7 days before a 2–3 day 百岳, up to 21 by 課表偏好 `taper_days`, default 14,
  `backend/engine/plan_prefs.py:184`) and applies to A races only — B / C events keep their sessions
  (`backend/engine/overview.py:2385`).
- **Two A races close together** (SP-90): the 恢復期 / 專項期 / 減量期 between them are cut short and
  the week says so (`backend/engine/overview.py:1870`); the projection reads the phases the same way
  (`backend/engine/projection.py:81`).
- **中間訓練 and B / C races** (SP-95): a 專項期 week between two A races < 12 weeks apart is capped at
  a share of the first race's pre-taper level (`inter_cap`, `backend/engine/overview.py:544`, used at
  `backend/engine/overview.py:1505`; projection `backend/engine/projection.py:623`); a base / 專項期
  week holding a B race is scaled by `B_WEEK_SHARE` (`post_race.b_week_factor`,
  `backend/engine/post_race.py:204`, `backend/engine/overview.py:1509`).
- **B race notes** (SP-280): too many B races, a long one just before an A race, or a B race inside
  (or longer than) the next A race's 減量期 get a week note; a 百岳 B race is left out
  (`post_race.b_hints`, `backend/engine/post_race.py:383`, `backend/engine/overview.py:2164`).
- **恢復期 / 回量期** (SP-98): post-race hours are a share of the level before the race, not of the
  taper weeks (`recovery_hours` `backend/engine/overview.py:524`, `rebuild_hours`
  `backend/engine/overview.py:534`, used at `backend/engine/overview.py:1489`).
- **轉換期 after an ultra** (SP-109): up to 6 weeks after a 超馬級+ A race, else ≤ 4
  (`backend/engine/planning.py:529`).
- **Multi-day 百岳** (SP-114): ME (ADS ≤ 10 %) instead of the uphill VO2max set, else general strength
  (`backend/engine/overview.py:1887`; projection `backend/engine/projection.py:730`); an old multi-day
  event without per-day numbers gets a split hint (`backend/engine/overview.py:1708`).
- **Walking sessions' uphill cap** (SP-115): 75 % HRmax, never below the easy-run cap
  (`hr_profile.walk_cap_for`, `backend/engine/overview.py:1614`); 攻頂日模擬 and ME climb at it
  (`backend/engine/overview.py:1894`).
- **生病** (SP-117): a cold = Z1 recovery runs only; a fever = no run until a day after the symptoms,
  then a recovery-pace first run; applied last, it wins (`injuries.illness_rule`,
  `backend/engine/overview.py:2191`, `backend/engine/overview.py:2412`).
- **肌力課依期別** (SP-119) and **肌力動作** (SP-191): before a 越野賽 / 百岳 A race the strength session
  goes AA → 最大肌力 → 維持 (`strength_plan`, `backend/engine/overview.py:1821`), with the moves the
  athlete picked per type and the equipment they lack (`strength_moves`, 課表偏好, not part of
  `active`; `backend/engine/plan_prefs.py:188`, `backend/engine/overview.py:1824`).
- **平衡／腳踝** (SP-120): a block at the end of the strength session(s) before a 越野賽 / 百岳 A race
  (`balance_plan`, `backend/engine/overview.py:2217`).
- **傷病** (SP-270 – SP-273): an open 膝前痛／髂脛束／跟腱 event takes the avoided session types off its
  days — flat easy runs, no strides, a flat long run (`injuries.condition_rule`,
  `backend/engine/overview.py:2165`, `backend/engine/overview.py:2471`); the 疼痛燈號 of the last
  marked run — yellow cuts the rest of the week, red takes the runs out, in the projected weeks too
  (`injuries.light`, `backend/engine/overview.py:2174`, `backend/engine/overview.py:2544`,
  `backend/engine/projection.py:816`); after red the walk-run stages take the runs
  (`walkrun_apply`, `backend/engine/overview.py:2184`, `backend/engine/overview.py:2581`), skipped by
  a run marked 沒痛 (SP-273).
- **賽前碳水負荷** (SP-285): the week holding the A race's last 1–2 days gets a text note
  (`backend/engine/overview.py:2203`, `backend/engine/overview.py:2843`).

**Output**: target / done / remaining (hours, TSS), the reasons (`why`), the rules cited,
8-week history, load now and at Sunday (CTL, ATL, next-Monday TSB, weekly ramp), the daily
projection, sessions with day / done state, thresholds and their sources, notes (data /
testing to-dos from the indicators; preference notes tagged `src: prefs`), the per-category
TSS / h (`tss_per_category`), the preferences applied and the week's lost days
(`blackout_days`), and the 主要訓練項目, the easy-cap label / HR model, the `test_suggestions`,
the re-entry block, the B2B state and suggestion, the steep-walk and 專項期 info and the race
simulation suggestion (`backend/engine/overview.py:2214`). Blackout notes are tagged
`src: blackout`.

## 課表偏好 — training-plan preferences (`plan_prefs.py`)

Stored as `user_settings` keys `plan.prefs.*` with per-key validation
(`backend/settings/repository.py:128`, `backend/settings/repository.py:378`) and cross-field
rules in `check()` (`backend/engine/plan_prefs.py:304`: one session type per preferred weekday,
runs ≤ allowed days, quality < runs, long cap ≥ weekday cap, valid gate / AeT / B2B values).
Read synchronously by `load()` (`backend/engine/plan_prefs.py:338`), which drops stored
preferred-weekday overlaps from before that rule (`drop_overlaps`; the first type keeps the day).
`Prefs()` (`backend/engine/plan_prefs.py:132`) is **inactive** (`active`,
`backend/engine/plan_prefs.py:192`) and every caller keeps its original code path, so the
defaults reproduce today's plan exactly.

| Setting | Key | Values (default) |
|---|---|---|
| 可練日 | `plan.prefs.days` | 7 bools Mon..Sun, unchecked = rest day (`null` = every day) |
| 長跑日 | `plan.prefs.long_day` | any weekday `mon`…`sun` / `auto` = the athlete's most frequent long day over 12 weeks (`auto`) |
| 偏好的星期 | `plan.prefs.pref_days`, `plan.prefs.pref_keep` | `{quality \| aet_test \| cp_test \| strides: [first, second]}` (`{}` = 自動, the planner picks); one type per weekday (the long run's is 長跑日). Breaks of the default rules (`day_conflicts`, `backend/engine/plan_prefs.py:646`: 48 h from the long run, the day after it, not an allowed day, the long-day cap, two Zone 5 days < 2 apart, AeT test on a weekend / next to a hard day) are shown live (`POST /prefs/conflicts`) and after saving; 照我的偏好 stores the code in `pref_keep` and the planner then keeps the day, else it moves the session with a note |
| 單次時間上限（平日） | `plan.prefs.cap_weekday` | 20–300 min (`null` = none) |
| 長跑日上限 | `plan.prefs.cap_long` | 20–600 min (`null` = 同平日) |
| 上限模式 | `plan.prefs.cap_mode` | `soft` 盡量不超過 / `hard` 絕對不超過 (`soft`) |
| 每週跑步次數 | `plan.prefs.runs_per_week` | 3–7 (`null` = auto) |
| 每週品質課 | `plan.prefs.quality_per_week` | 0–2 (`null` = auto, ≤ 1) |
| 每週肌力 | `plan.prefs.strength_per_week`, `plan.prefs.strength_days` | 0–3 (`null` = auto); weekdays 0–6, `[]` = with easy runs |
| 每週時數 | `plan.prefs.weekly_hours` | 1–40 h cap (`null` = CTL ramp rules) |
| 地形偏好 | `plan.prefs.terrain_easy` / `_long` / `_quality` | easy `road`/`trail`/`any`; long `road`/`trail`/`auto` (a stored `hike` reads as `trail` — 登山 is not a workout type; kind `hike` is labelled 越野跑); quality `flat`/`hill`/`any` |
| 目標依據 | `plan.prefs.target_basis` | `auto` (by session type: HR for easy / long / trail days, power for intervals and 3–8 % hill repeats) / `hr` / `power` (`auto`; `engine/target_policy.py`). The legacy 間歇目標 `plan.prefs.interval_target` = `hr` reads as `hr` |
| 間歇門檻 | `plan.prefs.quality_gate`, `plan.prefs.quality_gate_weeks` | `auto` / `ua_gap` / `friel_drift` / `xu_drift` / `plateau` / `weeks` / `none` (`auto`); weeks 2–16 (8). **Not part of `active`** (`GATE_FIELDS`, `backend/engine/plan_prefs.py:91`): read by status `i_gate`. Panel: a chip per mode, each with a `?` whose fixed-position popup (ported from the viewer's `.qtip`, appended inside the open dialog so the modal top layer and its scroll box never hide it) gives the source, the exact criterion, what to do and whether it runs on your data now (`GET /prefs` `gate_options` + `GET /prefs/gate`; `backend/static/schedule.html:802`, `backend/static/schedule.html:2120`, `backend/static/schedule.html:2102`) |
| CP 測試方式 | `plan.prefs.cp_test_protocol` | `quick` 約 37 分 / `standard` 約 70 分 / `race` 不另外排 (`quick`). **Not part of `active`**: it only changes the test session (`NOT_SHAPING`, `backend/engine/plan_prefs.py:104`). Panel: three chips, the details behind `?` (`backend/static/schedule.html:811`, `backend/static/schedule.html:2106`) |
| AeT 測試方式 | `plan.prefs.aet_test_protocol` | `auto` (徐國峰 90′ on the weekend LSD, UA 40′ backup) / `xu90` / `ua60` / `ua40` / `evoke60` / `friel` (`auto`). **Not part of `active`**. Protocols in plan-auto.spec.md |
| AeT 飄移測試 | `plan.prefs.aet_test_days` | `weekday` / `any` (`weekday`: weekends are often trail days). **Not part of `active`** (`NOT_SHAPING`): every placement path reads it (`aet_test.test_days` / `pick_day`): weekday = Mon–Fri in Tue-first order, ≥ 2 days from the long run and other hard days where possible, never the day after the long run unless nothing else; the 80′ standard test may fall back to a weekend day that isn't the long run's, the 50′ short one never; `any` = the interval rule. The test's **length** follows `cap_weekday` (`aet_test.variant_for`): no cap or ≥ 80 → 15′ + 60′ + 5′; < 80 → UA's minimum 10′ + 40′ (never shorter, exempt below 50). Panel: `#pf-aet` chips + AeT 排在 |
| 間歇暖身／緩和 | `plan.prefs.warmup_commute_min`, `plan.prefs.cooldown_min` | 0–30 (10) / 0–20 (5) min: the interval's easy warm-up run and cool-down (`engine/interval_library.py`). **Not part of `active`** |
| 建議 B2B | `plan.prefs.b2b` | `true` / `false` (`true`): whether a due B2B weekend is suggested at all. **Not part of `active`** |
| A 賽事後轉換期 | `plan.prefs.transition_weeks` | 0–4 weeks, 0 = off (3; `backend/settings/repository.py:167`). **Not part of `active`** (`NOT_SHAPING`, `backend/engine/plan_prefs.py:105`): it changes the season's phases (`planning.auto_phases`, `backend/engine/planning.py:799`: after each A race's recovery; Friel 3–4 weeks, Canova 4 — 3 is the low end, 推估), read by `planning.phases` (`transition_weeks_setting`, `backend/engine/planning.py:993`) so status, the week plan, the projection, the phase labels and the automatic run agree. Manual phases win (nothing is added). The next A race's backward-planned 專項期 wins: the 轉換期 ends the day before it with a phase `note` 「轉換期縮短為 N 天…」, or is skipped (< 7 days, `TRANSITION_MIN_DAYS`, 推估) with the note on the recovery phase; the note is a week note. Panel: select `#pf-trw` (`backend/static/schedule.html:823`) |
| 熱適應 | `plan.prefs.heat`, `plan.prefs.heat_method` | `auto` / `off` (`auto`); `run` / `overdress` / `bath` / `sauna` / `mixed` (`run`). **Not part of `active`** (`NOT_SHAPING`): they only add heat sessions before a hot A/B race (`engine/heat_plan.py`). Panel: switch + select with the current S and the rules (`#pf-heat`) |

**Panel** (⚙ 課表偏好, redesigned 2026-10-02, `backend/static/schedule.html:711`): sections 每週時間
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

**Application order** (`shape()`, `backend/engine/plan_prefs.py:504`, then `place()`,
`backend/engine/plan_prefs.py:715`), in `week_plan` and every projected week:
1. The target hours are computed as before (CTL ramp, ≤ 10 % step, 3:1); `weekly_hours` only
   lowers them.
2. Quality count: 0 removes quality and the CP test (with a note); 2 → `week_plan` already
   planned one Zone 3 + one Zone 5 (`quality` / `quality2`) when both tracks are open; with one
   track it duplicates that session as `quality2` — only when the caller's gate allows quality
   at all, not in the 間歇門檻's guardrail mode (`Ctx.quality_cap`,
   `backend/engine/plan_prefs.py:422`), and not when the two would pass 20 % of the week (a note). Quality terrain
   adds （平路）/（坡道） and rewrites the detail (a library variant carries its own terrain); the
   目標依據 rewrites the target text (`target_policy`, `backend/engine/plan_prefs.py:544`).
3. **Caps**: the long session is capped at the long-day cap (同平日 = weekday cap). A quality
   session over the weekday cap is shortened — warm-up 15 → 10, cool-down 10 → 5 min, then one
   rep fewer (never below 2) — with title / detail rewritten so the COROS step builder still
   parses it (`trim_quality`, `backend/engine/plan_prefs.py:369`); a library variant is already
   fitted to the cap and left alone. The **CP test is exempt**
   (its protocol is fixed) with the protocol's note `note_test(protocol)`
   (`backend/engine/plan_prefs.py:122`); the 37-min quick test rarely hits a cap.
4. **Distribution**: the remaining minutes go to easy runs. Count = runs − (long + hard) when
   set, else the original count raised to ⌈minutes / cap⌉ so every run fits the cap; never
   more than the allowed days. When the target still does not fit (target > count × cap):
   - **hard**: the long day takes what fits under its cap, the rest is dropped with
     「受限於你的偏好，本週少 X 小時；想補量可以多排一天或放寬長跑日上限」
     (`NOTE_HARD`, `backend/engine/plan_prefs.py:117`);
   - **soft**: the excess goes on the long day (beyond its cap; with no long session, on one
     easy run placed on the long weekday), weekday sessions stay within the cap, note
     `NOTE_SOFT` (`backend/engine/plan_prefs.py:118`).
5. **Terrain**: trail easy runs are 輕鬆越野跑 with an **HR-only target ≤ 輕鬆跑上限** (pace and
   power are unreliable on trail) and the athlete's trail TSS / h; the long day becomes
   「LSD（路跑）」 / 「LSD（山路越野）」 (HR-only) (`_terrain_long`, `backend/engine/plan_prefs.py:434`).
   The time-based 登山 long day is gone (2026-10-02): a stored 登山 reads as 越野.
6. **Placement**: main sessions only on allowed days, one per day; the long session on the
   chosen long day (else the last allowed day left, like week_plan); quality ≥ 2 days from the
   long one, from each other and from hard days already done (Tue-first order), on its
   偏好的星期 when that keeps the rules or the athlete kept the conflict (else a `watch` note);
   the CP test and strides on theirs; strength on the chosen weekdays, else on easy-run /
   allowed days, never the day before the long session.

The projection passes the same preferences (`week_sessions`, `backend/engine/projection.py:155`),
rolls its history on the minutes actually planned, and returns each week's preference notes. The
課表 page shows the notes of the weeks in view (`_plan_notes`, `backend/api/plan_sessions.py:2611`).
User-edited and custom sessions are never overwritten: preferences only change the
generator's output and reconcile rule 3 keeps edited sessions.

**Saving** (`PUT /plan/prefs`, `backend/api/plan_sessions.py:2153`) validates the whole set,
writes every key and commits; when a 偏好的星期 breaks a default rule the dialog first shows the
conflicts once (照我的偏好), then the page opens the existing reconcile preview
(`backend/static/schedule.html:2490`). Cancelling keeps the preferences
saved and the plan unchanged until the next reconcile. The generator inputs are memoised with
the preference stamp in the key (`backend/api/plan_sessions.py:84`), so a saved change
regenerates immediately. COROS pushes read the stored sessions, so they follow too; an HR
target becomes an HR work step (`_work_hr`, `backend/sync/coros_workouts.py:202`).

## 不排課日期 — blackout days (`blackouts.py`)

One-off date ranges (a long weekend, a trip) on which **nothing is planned**. Separate from the
recurring 可練日 preference: that one says which weekdays are for training, this one blocks
specific dates. Docstring with the rules: `backend/engine/blackouts.py:1`.

**Storage**: `user_settings` key `plan.blackouts` (default `[]`,
`backend/settings/repository.py:178`), a list of `{id, start, end, label[, kind]}`. Validation
(`validate`, `backend/engine/blackouts.py:77`, called from `backend/settings/repository.py:381`):
ISO dates, start ≤ end, ≤ 62 days per range, ≤ 60 ranges, label ≤ 30 chars, unique ids, **no
overlaps**. Ranges may be in the past (kept as a record; only days from today on change the
plan). Read synchronously by `load()` (`backend/engine/blackouts.py:136`); bad data reads as none.

**休息日** (2026-10-02): `kind: "rest"` is a one-day range the athlete sets from the 課表 calendar's
context menu (`POST` / `DELETE /plan/rest-days`, `backend/api/plan_sessions.py:2259`,
`backend/api/plan_sessions.py:2280`; label 休息日, never a past day). Nothing is planned there, but
it is **not a lost day**: the week keeps its volume on its other days (`lost_days`,
`backend/engine/blackouts.py:178`). The athlete's own sessions on that day move to another day of
the week (decision `move`).

**Planning rules** (`week_plan`, `project_weeks`, `reconcile`):
1. **Never on a blocked day**: blocked days are removed from the candidate days before placement
   (`week_plan` both paths, `plan_prefs.place()`, `projection._place`,
   `backend/engine/projection.py:337`), so the existing placers keep their rules — long first
   (it gets the last free day before easy runs do), quality ≥ 2 days from the long and from each
   other, strength not the day before the long — and their 「排不進去」 drop path.
2. **Volume**: target hours × (allowed days not blocked ÷ allowed days) (`lost_days` / `factor`,
   `backend/engine/blackouts.py:178`, `backend/engine/blackouts.py:188`); allowed = the 可練日
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
   `_clear_blackouts`, `backend/engine/reconcile.py:286`): unedited auto sessions follow the
   regenerated week (the change says 「在不排課日期內（label），移到 m/d」,
   `backend/engine/reconcile.py:270`), or, in a week that wasn't regenerated, move to the
   nearest free day (`move_to`, `backend/engine/blackouts.py:223`: same week, ≥ today, allowed
   weekday, not blocked, no other main session, a hard session never next to another hard day;
   ties go earlier; long first) or are dropped. **Edited / custom sessions are never changed
   without the user**: they come back as a `conflict` change with `move_to`, and only
   `decisions[uid]` = `move` / `delete` changes them (an edited auto session deleted leaves a
   tombstone). Adding or moving a session onto a blocked day is a 400
   (`_not_blocked`, `backend/engine/plan_store.py:343`).
6. **COROS**: through the normal reconcile + push. Regenerated sessions are re-sent on their new
   day or removed as stale; an edited session still on a blocked day (no decision yet) is not
   pushed and its pushed copy is removed (`_on_blocked`, `backend/api/plan_sessions.py:2000`,
   used at `backend/api/plan_sessions.py:1979`). The push preview counts them
   (`blackout_to_remove`). Since SP-358 this happens when the 不排課日期 / 休息日 is saved, not
   only at the next push: `PUT /blackouts` and the 休息日 endpoints sync the sessions reconcile
   changed (`_sync_watch`, `backend/api/plan_sessions.py:2024`) and return `coros`.

**Flow** (`backend/api/plan_sessions.py:2187`): `POST /plan/blackouts/preview` regenerates with
the *candidate* list and returns the reconcile preview without saving anything (the generator
inputs are memoised with the blackout stamp in the key, `backend/api/plan_sessions.py:84`, and
`_compute_inputs` takes the candidate list); `PUT /plan/blackouts` saves the list, then
reconciles with the user's `decisions`. `POST /plan/reconcile` also takes `{decisions}`.

**Page** (`backend/static/schedule.html`): blocked days get a faint 45° hatch (the provisional
week hatch is 135°, so both read when stacked) and a small 🏖 label chip on the first day of the
range in each week row (every blocked day in the phone agenda); no ＋, no add-on-click, no drop
target (`boOf`, `backend/static/schedule.html:1061`). Ranges are set by dragging across days
(mouse / pen, 8 px threshold, never starting on a chip or button; `backend/static/schedule.html:2636`),
by clicking a day and Shift-clicking another, or from ⋯ → 「🏖 設定不排課日期」
(`backend/static/schedule.html:594`), which also works on the phone. The dialog
(`backend/static/schedule.html:847`, `openBo` `backend/static/schedule.html:2512`) takes start,
end and an optional label, checks overlap / length client-side, and shows how many sessions sit
in the range; clicking the chip edits or removes the range. Creating, editing and removing all
go through the reconcile preview first (`applyBlackouts`, `backend/static/schedule.html:2549`),
where each conflicting edited session gets 移到… / 刪除 / 先留著 (`changesHtml`,
`backend/static/schedule.html:1280`). Blackout notes show above the calendar with a 🏖 mark.

## Same-load conversion (`equivalence.py`)

When a session's terrain changes in the dialog (路跑 ⇄ 越野跑), the page designs a session of
the **same time**, hence the same TSS: the sessions it applies to are easy (≤ AeT), and at the
same intensity TSS grows with time at the same rate (hrTSS = h × IF² × 100). Only time has to
be predicted from distance and climb, per terrain, for this athlete.

**Model** (docstring `backend/engine/equivalence.py:1`; `fit`, `backend/engine/equivalence.py:250`):
- Samples: the last 26 weeks; easy = avg HR ≤ AeT + 3 (the review card's easy-run tolerance), ≥ 20
  min, ≥ 1 km (`samples_from`, `backend/engine/equivalence.py:335`). Group hikes are paced by the
  group, so only hikes the athlete marked solo (`racepower.athlete.solo_hikes`) are hike samples.
- Naismith's additive form (1892) with Langmuir's grade-dependent descent correction
  (*Mountaincraft and Leadership*, 1984: −10 min per 300 m on 5–12° descents, +10 min on
  steeper; `descent_hours`, `backend/engine/equivalence.py:143`):
  `h = km / v_flat + gain / VAM + descent`.
  v_flat = median speed of easy flat road runs (else the speed at AeT from a speed ~ HR line
  over all flat road runs; `backend/engine/equivalence.py:187`); trail VAM by least squares
  on the residual (`backend/engine/equivalence.py:216`); hike flat walking speed and VAM
  fitted together (`backend/engine/equivalence.py:229`).
- Fewer than 5 samples on a terrain → effort distance EP = km + gain/100 (ITRA / 健行筆記,
  `algorithms/effort.py` SIMPLE_FORMULAS["itra"], measured there at 6.9 % vs integrated
  Minetti on one runner's data) at the athlete's median EP speed on that terrain.
- With ≥ 5 samples both forms are fitted and the one with the lower **inner** leave-one-out
  error is used (`method="auto"`).
- `design()` solves km (and climb = km × m/km) for a time at a climb density
  (`backend/engine/equivalence.py:171`); road uses EP at v_flat.
- `TODO(racepower-v2)` (`backend/engine/equivalence.py:64`): a validated grade-cost model from
  `backend/engine/racepower/` can be passed as `fit(..., grade_cost=f)`; nothing imports it yet.

**Validation** (`backtest`, `backend/engine/equivalence.py:316`): leave-one-out on the athlete's
own easy trail and hike activities — refit without the activity (including the method choice),
predict its moving time, compare. Result on one runner's data (2026-09-30, 26 weeks):

| Terrain | n | Method chosen | MAPE | Bias | Naismith / Langmuir alone | EP alone |
|---|---|---|---|---|---|---|
| 越野 trail | 9 | EP | 5.6 % | −1.0 % | 11.3 % | 5.6 % |
| 登山 hike | 3 | EP (< 5 samples) | 21.5 % | +1.7 % | — | 21.5 % |

Flat easy road speed from 7 runs. Trail samples span 55–111 m/km; the page warns outside
that range. A terrain whose MAPE is above 15 % (`ESTIMATE_MAPE`) or that cannot be backtested is
labelled **推估** — on that data hike is 推估, trail is 依你的紀錄.

**Dialog** (`backend/static/schedule.html:625`; the 「地形與同負荷換算」 fold is collapsed by default
since 2026-10-03, the 結構 editor above it open): terrain 路跑 / 越野跑 (kind `hike` is 越野跑;
choosing 路跑 on it goes back to a road kind), a
爬升比例 slider 0–150 m/km, a 套用目標賽事 button (the goal's climb per km), distance / climb /
TSS fields, a lock (鎖時間 / 鎖 TSS / 鎖距離) and 完全自由調整; editing any unlocked field
recomputes the rest with the same formula in JS (`eqH`, `backend/static/schedule.html:1494`;
`eqRecalc`, `backend/static/schedule.html:1533`). An inline grade readout shows the average grade
(on trail, climbing sections ≈ 2 × the average, 推估); for a 陡坡健走 the grade is given, so
distance and climb fill each other. It shows the live TSS and its difference vs
the original (「比原本多 15 %」), warns above the preference cap (`capFor`,
`backend/static/schedule.html:1482`), when the typed distance / climb would take a different
time, and outside the data range; a trail / hike target becomes HR-only ≤ AeT. The session is
saved with `terrain`, `distance_km`, `climb_m`.

## Multi-week projection (`projection.py`)

`project_weeks(cur, phases, until, ctlconstant, atlconstant, prefs, blackouts, events, heat_acts,
b2b_accepted)` (`backend/engine/projection.py:437`) starts
from this week's `week_plan()` output and rolls the same rules forward week by week, never
more than `MAX_WEEKS` = 8 ahead (`backend/engine/projection.py:43`):

- Hours per week (`week_hours`, `backend/engine/projection.py:100`): base / specific use the CTL
  ramp goal capped at +10 % (≥ +0.5 h) of max(4-week mean, last week) over normal weeks (`cap_ref`:
  `target.ref_weeks`, this week and each projected week outside a 減量期 / race / 恢復期 / 轉換期,
  `_skip_week`; SP-73), with a 65 % recovery
  week after 3 build weeks; taper 40–50 % of the 6-week mean; event 30 %; recovery 50 %;
  transition 50 % of the pre-race level, the same number as week_plan (`O.transition_ref` over
  the history, this week and the projected weeks by Monday, `backend/engine/projection.py:609`;
  week_plan's `transition_ref` when its weeks are past), easy runs ≤ 60 min. A weekly-hours preference caps it
  (`backend/engine/projection.py:550`).
- Sessions (`week_sessions`, `backend/engine/projection.py:155`): the same template (long, the
  week's intervals, strength, easy fill) placed by `_place` (`backend/engine/projection.py:337`), or
  shaped and placed by the preferences. Base / 專項期 / 減量期 intervals come from the 間歇門檻
  per week through the same two-track pick as `week_plan` (`overview.quality_sessions`,
  `backend/engine/projection.py:676`; `_bq`, `backend/engine/projection.py:325`), a base recovery
  week gets the fartlek (`backend/engine/projection.py:239`). Tests are never projected: a due
  AeT test only moves the cadence's "last" date (`backend/engine/projection.py:674`). The 主要訓練項目,
  B2B, 專項期, 陡坡健走 and 熱適應 hooks run per week too.
- Whether a projected week gets a quality session is decided per week, for that week's phase,
  mode and Monday (`allow_quality` → `quality_gate.week_decision`,
  `backend/engine/projection.py:423`): the method state from this week (`weeks` mode and the
  Zone 3 gate's consistency streak re-evaluated per Monday), only the intensity guardrail
  carried forward (Zone 5 only), and each track's step (`{"z3", "z5", "met"}`) advanced once per
  projected interval of that track (this week's own intervals count).
  A CP-test week no longer carries into later weeks; a `cur` without the new gate — or with
  the old `{levels, streak_ok}` shape — becomes a no-method gate (intensity bad keeps Zone 5
  out) (`_gate_inputs`, `backend/engine/projection.py:401`).
- CTL / ATL roll forward with the athlete's constants (`ds.athlete.ctlconstant` /
  `atlconstant`, `backend/engine/projection.py:863`); a session `_place` left without a day is
  kept out of the date filter.
- Each projected week carries `mode`, hours, TSS, CTL start / end, `why`, `provisional`
  (true beyond next week) and, with preferences, `notes`.

The horizon (`horizon_of`, `backend/api/plan_sessions.py:160`) is the current phase end, at
least two weeks out (the end of next week), capped at `MAX_WEEKS`; no phase = the cap. When the
current phase ends within 14 days (`NEXT_PHASE_DAYS`, end − today ≤ 14) it is the **next**
phase's end instead (the first phase starting after the current one; none = the rule above),
still within the floor and the cap (SP-327). Before, the horizon dropped to the two-week floor on
a phase's last days (or when a new / moved race cut the current phase short), rule 5 removed the
later weeks and they came back the next day as new rows with new uids — the calendar feed and
COROS saw them deleted and created again. With fixed phases the horizon never moves back from
one day to the next, so the later rows keep their uids across a boundary.

## Stored plan (`plan_store.py`, `reconcile.py`)

**Table** `plan_sessions` (`backend/db/models.py:140`): one row per planned session — `uid`,
`week_start`, `gen_key` (the generator's id: long / quality / easy1 …; none for custom), `day`,
`kind`, `title`, `minutes`, `target`, `detail`, `source`, `tss`, `origin` (auto / custom),
`edited`, `provisional`, `state` (active / done / missed / deleted / superseded), `done_by`
(JSON activity row), `note`, and `terrain` / `distance_km` / `climb_m`, `protocol`, the
interval-library `variant_*` columns, `target_basis` (目標用 hr / power, None = 自動), `steps`
(the structure saved in the 課表 editor, JSON), `ext_key` / `ext_sig` (a session written from
outside the generator — the 賽事計算機's 「匯出至課表」, `racecalc:<event id>` — and the fingerprint
of what it wrote) and `family` (a 強度課's aerobic / vo2max / speed picked in the editor, None =
read from the steps; SP-79, `backend/db/models.py:192`, `backend/db/database.py:163`)
(`backend/db/models.py:167`, added by `_migrate_schema`, `backend/db/database.py:114`). `updated_at` moves only when a row's content changes.

**Kinds** (`backend/engine/plan_store.py:21`): easy 輕鬆跑, long **LSD** (was 長時間, 2026-10-03;
the old auto titles are mapped at read time by `display_title`, `backend/engine/plan_store.py:46`,
the key stays `long`), quality 強度課 (shown as its family — 有氧間歇 ／ VO2max 間歇 ／ 速度, SP-79;
`FAMILIES`, `backend/engine/plan_store.py:41`), test 測試, hike **越野跑** (登山 is not a workout type),
strength 肌力, heat_passive 被動熱適應, notice 課表待確認 (a reminder, never load or compliance),
race 比賽 (the generator's race-day row, minutes 0, or the 賽事計算機's export; never added by hand,
and no other kind can be changed into it). **Terrains** road / trail / hike
(`backend/engine/plan_store.py:32`).

**Reconcile rules** (`reconcile()`, `backend/engine/reconcile.py:101`; documented at
`backend/engine/reconcile.py:12`):
1. Active (and previously missed) sessions up to today that match an activity become **done**
   (`plan_match.assign`, `backend/engine/reconcile.py:124`: same day + the planned sport first,
   one activity per session; long / quality / test also by the generator's week-wide match —
   details in plan-auto.spec.md); the rest on past days become **missed**, but only up to the day
   the synced data covers (`covered`), so a late sync can turn a missed session back into done.
   An activity the athlete unlinked is never auto-matched again.
2. Per generated week, unedited auto sessions from today on are replaced by the regenerated
   ones: same `gen_key` → changed, gone → removed, new → added. Terrain, distance and climb
   are regenerated fields (`FIELDS`, `backend/engine/reconcile.py:48`).
3. Edited and custom sessions are kept. An edited long / quality / test is **superseded** when
   the regenerated week is a rest week (recovery / taper / event / transition) that no longer
   has it (`backend/engine/reconcile.py:161`). Deleted auto sessions stay deleted: their
   tombstone blocks the `gen_key` for that week. A kept race row (the calculator's export) blocks
   the generator's own `race` of that week (`backend/engine/reconcile.py:136`), so the week never
   has two races and the export is never moved.
4. An auto session on the same day as a kept edited / custom session moves to a free day of
   that week, or is dropped (`_resolve_collisions`, `backend/engine/reconcile.py:351`).
5. Unedited auto sessions past the horizon are removed.
6. 不排課日期: nothing active stays on a blocked day from today on; edited / custom sessions
   there are a `conflict` until the user decides (see 不排課日期 above; documented at
   `backend/engine/reconcile.py:30`).

Each change is returned as `{action, uid, day, title, kind, minutes, origin, edited, reason?,
before?, conflict?}` (`conflict` = `{label, move_to, day, choice}`; `_change`,
`backend/engine/reconcile.py:90`) and grouped by day for the preview
(`backend/engine/reconcile.py:385`).

**Coverage** (`_covered`, `backend/api/plan_sessions.py:306`): the later of the latest activity
day and the day before the latest successful COROS / generic sync.

**Automatic reconcile** (`_ensure`, `backend/api/plan_sessions.py:357`): on the first visit of
a week, or while an earlier week still has active sessions, the plan is reconciled and saved
before anything else is returned — unless a 課表待確認 proposal is waiting (plan-auto.spec.md).
`GET /sessions` also matches newly synced runs right away (`match_only`).

**Edits** (`_clean`, `backend/engine/plan_store.py:265`; `edit`, `backend/engine/plan_store.py:350`):
editable fields are day, kind, title, minutes, target, detail, terrain, distance_km, climb_m,
target_basis, steps. A day must be ISO and not in the past; kind and terrain must be known (not
`notice`); minutes 0–1440; distance 0–500 km, climb 0–20000 m; an optional TSS estimate 0–2000;
`steps` is normalised by `workout_steps` (a bad structure is a 400); title not blank. An edit
marks the session `edited` and non-provisional; hand-editing a library variant's text keeps the
variant marked `swap = user`. Moving an auto session to another week leaves a tombstone in the
old week and turns the session into a custom one. Only active sessions can be edited. An edit,
a move and a delete sync the watch right away (see COROS push › The watch follows the user's
changes, SP-358).

**Add** (`backend/engine/plan_store.py:430`): a custom session needs a day; defaults kind easy,
45 min, a title per kind (a new test follows the CP 測試方式); `notice` and `race` can't be added.
**Delete** (`backend/engine/plan_store.py:515`): an auto session becomes a tombstone
(`state = deleted`), a custom one is removed; deleting either day of an accepted B2B cancels it.

**External sessions — 賽事計算機「匯出至課表」** (2026-10-04, SP-43; `upsert_external`,
`backend/engine/plan_store.py:557`): one row per `ext_key`, written as the user's own (`edited`).
The row is this key's active row (updated), else its deleted / superseded row (restored), else the
generator's own race row of that week (`gen_key` race, claimed), else a new custom row; a moved race
date moves the row. An identical export is `unchanged` (no write, `updated_at` kept). `ext_sig`
fingerprints day / kind / title / minutes / target / detail / steps (`ext_signature`,
`backend/engine/plan_store.py:547`), so a later edit on the 課表 page shows as `user_edited` before an
overwrite. The race's planned TSS counts in the week (`plan_summary`). See racepower.spec.md,
Watch export.

**Expired sessions** (2026-10-02): a past session that was never done (missed, or still open on a
past day; `is_expired_open`, `backend/engine/plan_store.py:470`) can be deleted alone or all at
once (「刪除所有過期未完成」, `delete_expired`, `backend/engine/plan_store.py:496`). It becomes a
tombstone of any origin with note `user_deleted_expired` and no `gen_key`, so reconcile,
plan_match and the auto-replan's 復原 never bring it back; a pushed copy comes off the watch
through the workout-sync provider (`_unpush_expired`, `backend/api/plan_sessions.py:1825`), or
on the next push when the login has expired.

**Manual link** (`link` / `unlink`, `backend/engine/plan_store.py:677`,
`backend/engine/plan_store.py:663`): the athlete pairs a session with an activity of the same
week (± 1 day; the calendar's `link_options`) or undoes it; an unlinked activity is stored in
`plan.match.unlinked` (by start time) and becomes an unplanned run.

**Stored-plan summary** (`plan_summary`, `backend/engine/plan_store.py:703`): the week's
target hours (active + done sessions, strength excluded) and TSS (`session_tss`: a done session
counts its activity's actual TSS), plus the CTL / ATL projection described under PMC, ending
CTL / ATL and next-Monday TSB.

**Compliance** (`engine/compliance.py`; TrainingPeaks / intervals.icu): a done session's
actual ÷ planned TSS and duration; the worse deviation sets the colour — green ≤ 20 %, yellow
≤ 50 %, red beyond or the wrong kind of activity (`COMPLIANCE`, `backend/engine/compliance.py:31`);
planned vs actual (`plan_match.compare`, `backend/engine/plan_match.py:318`) flags 「沒照課表」 (≠).
The intensity is graded, not pass / fail (SP-216; TrainingPeaks' compliance colours applied to the
quality dose, 推估; `backend/engine/plan_match.py:60-64`): a planned hard session at ≥ 80 % of the
dose it needs is done, 50–80 % is 強度不足, < 50 % ran easy (≠); a planned easy run with a quality
dose ≥ 150 % ran hard (≠), below that 偏強 — 強度不足 / 偏強 count as ◐ 部分, not ≠
(`backend/engine/compliance.py:130`).
SP-370 (owner 2026-10-08; intervals.icu / TrainingPeaks rate completion by load / time, the
combination 推估): when time **and** TSS are both measured and both within ±20 %
(`compliance.time_tss_level`, `backend/engine/compliance.py:67-76` — None when either is missing:
no TSS, or a planned TSS of 0 with no estimate, keeps ≠), an intensity reversal (easy / LSD run as intensity, quality run
easy) or another **foot** sport (`FOOT` = road / trail / hike / walk, `compliance.foot_swap`,
`backend/engine/compliance.py:61`) is ◐ 部分 (yellow, `vs.short` + `vs.soft` intensity / sport,
label 跑成強度課 / 跑成輕鬆 / 項目不同, reason 「時間和負荷都對，但…」); ≠ stays when time or TSS is
also > 20 % off, or the sport is not on foot (bike …). A walking session (`target_policy.is_walk`:
陡坡健走 — stored as kind easy —, 登山 / 健行 / 百岳, ME 負重爬坡) takes a hike / walk / trail run as its
own sport (`compliance.accepted`, `WALK_OK`, `backend/engine/compliance.py:39-53`), read-side, so
stored rows from before follow without a migration. Shown on the 本週 tiles, the 課表
chips and week rows, the workout review 「課表」 card and the 課表統計 page — all through
`session_compliance` + `with_plan_check` + `status_of`, on the same planned TSS: the calendar's
`est_tss` (stored TSS, else minutes × `tss_rates`); the workout review card gets it from
`api/plan_sessions.session_est_tss` (`backend/api/plan_sessions.py:2510`; rates from the dataset's
TSS / h and `plan_store.rate_rows`, only computed for a row without a TSS).

**Concurrency**: plan writes are serialized by one asyncio lock per event loop
(`_wlock`, `backend/api/plan_sessions.py:454`), so two tabs or a preview racing a push cannot
generate the same week twice. Pushes to the watch take a separate push lock (`_plock`,
`backend/api/plan_sessions.py:472`), never the writer lock (SP-362 B3, see Caches above).

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
  setting `plan.calendar` = `{token, origin}` (None = off; `backend/settings/repository.py:218`,
  validated by `validate_setting`, `backend/engine/calendar_feed.py:67`). Compared in constant
  time (`token_ok`, `backend/engine/calendar_feed.py:74`); a wrong, old or missing token is a
  **404**, never a 401. 「重設網址」 makes a new token (the old address is a 404 at once);
  「停用」 clears it.
- **Events**: one all-day VEVENT per stored session from 14 days ago to 56 days ahead
  (`in_window`, `backend/engine/calendar_feed.py:180`), states active / done / missed; deleted /
  superseded rows and kind `notice` (課表待確認) are left out. Sessions have a day, no time, so
  every event is `DTSTART;VALUE=DATE` + the next day, `TRANSP:TRANSPARENT`. Rest days and
  不排課日期 have no session and get no event; an edited / custom session the user kept on a
  blocked day is shown as stored. SUMMARY = the title (read through `display_title`: a 強度課's
  family name, SP-79), 「✓ 」 when done, 「✗ 」 when missed
  (`summary`, `backend/engine/calendar_feed.py:133`). DESCRIPTION = planned minutes / TSS, the
  actual minutes / TSS when done, distance / climb, the target text, the saved structure
  (`workout_steps.structure_text`), the detail and 「在課表打開這堂課」 (`description`,
  `backend/engine/calendar_feed.py:148`); `URL` = the same link: the 課表 page's existing deep
  link `?day=&uid=` that opens the session's dialog (`backend/static/schedule.html:3053`).
  Calendar properties: `X-WR-CALNAME`, `REFRESH-INTERVAL` / `X-PUBLISHED-TTL` PT1H (hints;
  Google ignores them). Lines are CRLF, folded at 75 octets without splitting a UTF-8 character,
  TEXT escaped (`fold`, `escape`, `backend/engine/calendar_feed.py:94`).
- **Edits follow**: UID = `<uid>@trailruncoach`. A session's `uid` lives as long as the session:
  an edit or a move to another day / week keeps it (`edit`, `backend/engine/plan_store.py:350`),
  and reconcile keeps it when it regenerates the same `gen_key` in a week; a newly generated
  session (a new `gen_key`, or a week generated again after its rows were removed) gets a new
  uid, and the old one simply leaves the feed. `LAST-MODIFIED` = the row's `updated_at`, which
  `_fill` (`backend/engine/plan_store.py:125`) now moves only when the row's content changed
  (save() rewrites every row) and always by ≥ 1 s; `SEQUENCE` = its whole seconds since
  2026-01-01 (`sequence`, `backend/engine/calendar_feed.py:125`), so every change raises it.
  Deleted sessions (one, 「刪除所有過期未完成」, reconcile removals, auto-plan replacements)
  disappear from the feed.
- **Read-only and cheap**: the feed never reconciles; it shows the weeks the app has stored so
  far. Links use `WKO5COACH_PUBLIC_URL`, else the stored origin, else the request's address
  (X-Forwarded-Proto / -Host only from a trusted proxy; `public_base`,
  `backend/api/calendar_feed.py:70`).
- **Demo**: neither router is mounted in the demo instance (`owner_only`, `backend/main.py:151`):
  no feed of the demo athlete and no way to the owner's.
- **Refresh latency**: the iPhone fetches as often as 設定 › 行事曆 › 帳號 › 擷取新資料 allows
  (every 15 min ⇒ about 5–15 min); Google Calendar refreshes subscribed URLs on its own schedule
  (typically several hours, up to about a day) and can't be forced. The settings page says so
  (`backend/static/settings.html:448`).

## COROS push (`coros_workouts.py`)

Pushes stored sessions to COROS Training Hub as structured, scheduled workouts through the
unofficial Training Hub API (same host and token as the COROS sync client; endpoints listed at
`backend/sync/coros_workouts.py:6`). Since 2026-10-02 the API talks to the **active workout-sync
provider** (`WT.active`, setting `plan.push.provider`; the COROS provider delegates to
`coros_workouts`, Garmin / intervals.icu are disabled stubs); `coros_plan_push.provider` records
which one. The response keeps the `coros` field names.

- **Scope** (`_range`, `backend/api/plan_sessions.py:371`): `day` = that day; `week` = the
  Monday–Sunday week of `day`, from today on; `phase` (整個周期) = today to the phase end, capped at
  `MAX_WEEKS` (`phase_push_end`, `backend/api/plan_sessions.py:183`) — the current phase only, also
  on its last days when the horizon already reaches into the next phase (SP-327); the automatic
  push (`plan.auto.push_days` from today) is independent of both. `day` defaults to today; the plan's "today" is never earlier than the real date
  (`backend/api/plan_sessions.py:371`). A `week` entirely before today is a 400 for preview,
  push and unpush instead of an empty range (`backend/api/plan_sessions.py:388`); a past `day`
  scope is not guarded.
- **Every push reconciles first** and applies the result, then pushes the active sessions in
  range (`backend/api/plan_sessions.py:2065`).
- **Session → steps** (`session_steps`, `backend/sync/coros_workouts.py:438`): a structure the
  athlete saved in the 課表 editor (`steps`, `engine/workout_steps.py`) wins over the text; long /
  hike / easy are one time step at HR ≤ the easy cap; a 路跑 專項期 LSD with a marathon-pace segment
  is easy / MP / easy, the MP step a pace target (COROS `intensityType` 3, s/km: the goal pace,
  else threshold pace × 1.04–1.08) or an HR band without threshold pace; an easy session whose
  title has `N×S 秒` gets a strides repeat when ≥ 10 min remain; quality and test sessions get
  their own step builders — an HR basis gives HR work steps (`_work_hr`,
  `backend/sync/coros_workouts.py:202`). Strength, rest, heat_passive and a race without steps
  (the generator's 比賽) are not pushed (skipped, with a reason); a race with steps (the 賽事計算機's
  「匯出至課表」) is pushed from them (`backend/sync/coros_workouts.py:443`); a 課表待確認 notice is one 1-minute step. Done, unplaced and past-day
  sessions are not pushed (`session_workout`, `backend/sync/coros_workouts.py:632`). The push
  preview lists sessions whose % / zone pace steps have no threshold pace (`pace_notes`,
  `backend/api/plan_sessions.py:1988`).
- **Program** (`build_program`, `backend/sync/coros_workouts.py:567`): run sport; HR targets as
  absolute bpm with the LTHR zone scheme; names `TRC <title> <m>/<d>`, ≤ 30 chars
  (`workout_name`, `backend/sync/coros_workouts.py:627`). The title is the stored one read through
  `display_title`, so a 強度課 stored before SP-79 is pushed as 「TRC 有氧間歇（巡航）3×8 分 10/6」,
  not 「TRC 閾值 3×8 分 …」. The name is part of the payload and so of the fingerprint: a session
  already on the watch under its old name shows 需更新 once and is re-pushed with the new name on
  the next push (the only change in its payload; decided 2026-10-04, SP-79 — keeping the old name
  would leave the watch and the 課表 disagreeing forever).
- **Idempotency** (`_push_one`, `backend/sync/coros_workouts.py:1034`): each push is recorded in
  `coros_plan_push` (`backend/db/models.py:113`) with the COROS program / plan / schedule ids
  and a SHA-256 fingerprint of day + payload. Same fingerprint → left alone; changed → the old
  COROS entry is removed and a new one created; an entry already executed on the watch is kept
  as done. The stored-plan push keys rows by session `uid` (`session_key`,
  `backend/db/models.py:125`).
- **Clean-up** (`push_sessions`, `backend/sync/coros_workouts.py:1096`): pushed sessions that
  left the plan (deleted / superseded / regenerated away) are removed unless on a past day;
  missed sessions and expired ones the athlete deleted are removed from the calendar
  (`plan_store.off_watch`). Only entries recorded in `coros_plan_push` are
  ever deleted (`_remove_row`, `backend/sync/coros_workouts.py:1135`). A pushed exported race also
  takes off the workout the calculator's retired 「匯出到 COROS」 pushed under the same key
  (`racecalc:<event id>`, not in `all_rows`; `_old_calc_keys`, `backend/api/plan_sessions.py:1994`);
  the preview counts it as `calc_to_replace`.
- **Moved sessions** (SP-358, 2026-10-08): a push of a range also re-sends the sessions whose pushed
  copy sits in that range but which moved out of it (`copies_in` / `_with_copies`,
  `backend/api/plan_sessions.py:2012`; preview and push alike), so the copy comes off the old day.
  Before, a session dragged to next week stayed on the watch on its old day after a 「推送本週」.
  `_remove_remote` (`backend/sync/coros_workouts.py:863`) looks the entry up once more after the
  calendar delete, after a short pause (`RECHECK_DELAY_S` 1.5 s). One COROS answered 0000 for but
  still lists is a **reminder, not a failure** (owner, SP-358 review — to be revisited after a real
  drag shows whether COROS just lags): the row is removed / re-sent as usual, nothing is retried,
  the app log gets a warning, and the result carries `check_day` (`push_window` →
  `check_days`); the page adds 「；COROS 回應已刪除，但 m/d 的行事曆上可能還在，請到 COROS App
  確認」 (`watch.check`, also after a manual push), in the warning colour.
- **The watch follows the user's changes** (SP-358, `_sync_watch`,
  `backend/api/plan_sessions.py:2024`): after a drag / 移到… / edit (`PATCH /sessions/{uid}`), a
  delete, a 不排課日期 save (`PUT /blackouts`), a 休息日 set / undo / move and a swap (SP-359), the
  affected sessions are synced at once through `plan_auto.push_window(only=…)`: a pushed copy is
  re-sent on its new day (the old entry removed first) or removed when the session left the plan
  or sits on a blocked day; with 自動推送 on (`plan.auto.enabled` + `plan.auto.push`) a changed
  session that lands in the push window is pushed too; with it off only copies already on the
  watch follow. **Only the sessions the change touched** are synced (owner, SP-358 review): the
  stale / blocked / missed clean-up is limited to them too (`push_window`,
  `backend/engine/plan_auto.py:493`), so other leftover copies wait for the automatic run or the
  manual push, and their problems never show here. Nothing is called when nothing on the watch is
  affected. The response carries
  `coros` (`status` ok / unchanged / none / partial / failed, `sent`, `removed`, `error`,
  `check_days`); the page appends 「，COROS 手錶已同步」 or warns 「；COROS 手錶沒跟著更新：…」
  (`watchNote`, `backend/static/schedule.html:2825`), and a failure of a touched session adds a change-log row (trigger `edit`,
  status `failed`, 「課表已改，但COROS手錶沒有跟著更新」 with the push error). Root cause of SP-358:
  these endpoints only wrote the store; the watch changed only at the next manual push or the
  automatic run, which runs only after a sync with a new activity — so the old day's workout
  stayed on the watch.
- **Unpush** (`DELETE /push-coros`, `backend/api/plan_sessions.py:2088`) removes every recorded
  entry whose day falls in the range (`remove_keys`, `backend/sync/coros_workouts.py:1120`).
- **Status per session** (`status_of`, `backend/sync/coros_workouts.py:903`): done / skipped /
  not_pushed / pushed / outdated / failed; sessions no longer active but still recorded show
  `pushed_<state>` (`backend/api/plan_sessions.py:417`).
- The old week-keyed helpers (`push_week` / `remove_week` / `week_status`, keys
  `<week start>/<session id>`) were only used by tests and are gone; the tests drive
  `push_sessions` / `remove_keys` / `status_of` directly.
- Pushes and removals are serialized by a module-level lock
  (`backend/sync/coros_workouts.py:87`). An expired COROS login returns 401
  `COROS_AUTH_REQUIRED` (`SYNC_AUTH_REQUIRED` for another provider) with a hint to log in again
  on the settings page (`_auth`, `backend/api/plan_sessions.py:2052`).

## Page (`backend/static/overview.html`)

- Title 訓練總覽, heading and nav entry 總覽 (`backend/static/overview.html:6`,
  `backend/static/overview.html:244`).
- **Dashboard layout** (2026-10-02, reordered 2026-10-03): an in-page jump nav 狀況 / 紀錄 / 指標 /
  本週 / 待辦 (`backend/static/overview.html:245`); a pending 課表待確認 proposal on top
  (`autoplan.js`; the change log lives on 課表); **KPI tiles** — CTL and TSB (value, 90-day
  sparkline, one-word status), this week's volume (done / target ring and TSS, from the stored
  plan once loaded) and the countdown to the next A race (`renderKpis`,
  `backend/static/overview.html:408`); the **PMC** over the last 7 / 42 / 90 days (default 42,
  remembered per browser; SP-122, `backend/static/overview.html:899`); 做了什麼 (period totals, the tables
  behind 詳細數字); 指標 as compact cards (value, sparkline, one-line verdict; why / action /
  source behind 詳細, `loadStatus`, `backend/static/overview.html:443`); **本週** as seven
  day-cards — one icon + colour per session type (輕鬆, 3 區, 5 區, LSD, B2B, 負重, 測試, 肌力 …),
  ✓ and the compliance colour when done, activities outside the plan with their session class,
  a click opens the 課表 page on that day (`dayHtml` / `renderPlan`,
  `backend/static/overview.html:666`, `backend/static/overview.html:678`), the week notes and a 詳細
  with the bars, the reasons, CTL → Sunday, next-Monday TSB and the thresholds line (incl. the
  輕鬆跑上限 and its `?`) (`loadWeek`, `backend/static/overview.html:711`); the bottom row holds the
  5 區 card (full width), 待辦與警示 and, left of it, the B2B card (shown only before a multi-day /
  ≥ 6 h A event or once B2B weekends exist; `GET /overview/b2b`). Due tests, B2B weekends and zone
  retests are in the shared floating suggestion box (`static/suggestions.js`); its
  `suggestions:changed` event reloads the week (`backend/static/overview.html:967`). No sideways
  scroll at phone width (long alert titles wrap).
- **Glossary hovers**: the terms `AeT` and `CP 測試` inside engine text (actions, indicator
  verdict / why / action, week reasons, notes, the thresholds line) get a hover / tap
  explanation of what they are and how to test them (`backend/static/overview.html:327`).
- **Sources removed from the plan**: the to-do list no longer prints sources, and the 依據 block
  of the week plan is gone — `w.rules` still carries them, noted in a code comment
  (`backend/static/overview.html:725`). Indicator cards keep their 來源 inside 詳細
  (`backend/static/overview.html:461`).
- The day list (`GET /plan/calendar` for this week: sessions, TSS estimates, compliance;
  `loadPlan`, `backend/static/overview.html:844`), the week's progress-bar targets, the Sunday CTL
  and next-Monday TSB come from the stored plan (`backend/static/overview.html:698`).
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
  `backend/static/schedule.html:1927`, reconcile `backend/static/schedule.html:1294`, push
  `backend/static/schedule.html:1319`, unpush `backend/static/schedule.html:1352`), which also has
  the ⚙ 課表偏好 panel (`backend/static/schedule.html:711`, `openPrefs`
  `backend/static/schedule.html:2457`), its client-side checks (`pfError`,
  `backend/static/schedule.html:2249`) and the preference notes above the calendar
  (`backend/static/schedule.html:1130`). Since 2026-10-02/03 the 課表 page also has:
  - **日曆 ｜ 課表統計** mode cards (`backend/static/schedule.html:548`);
  - a **context menu** (right-click, long-press on touch; `ctxItems`,
    `backend/static/schedule.html:2727`): on a session 編輯 / 移到… / 刪除 (an expired one too);
    on a free day 新增 / 排入測試 ▸ (the suggested tests with their templates and day rules) /
    設為休息日, on a 休息日 取消休息日;
  - **交換課表** (SP-359, 2026-10-08): on a session the context menu has 交換… (「再點另一堂課」,
    `backend/static/schedule.html:2735`); the page then waits for the second session (hint 「點另一堂課，
    和「…」交換日期（Esc 取消）」, the first chip outlined; paging the weeks is allowed, a click
    elsewhere or Esc cancels, a session that can't swap says so). Dragging a session onto another
    session swaps them too (that chip lights up; dropping on an empty part of a day still moves,
    `swapTarget`, `backend/static/schedule.html:2675`). Swappable = what a drag may move: active,
    from today on, not on a 不排課日期, not a 課表待確認 (`swappable`,
    `backend/static/schedule.html:2839`), two different days; done sessions are never swappable.
    Both go through `POST /sessions/swap {a, b}` (`swap_sessions`,
    `backend/api/plan_sessions.py:589`) → `plan_store.swap` (`backend/engine/plan_store.py:359`):
    the two days trade in one commit, each move the same user edit as a drag (`_edit_row`: edited,
    non-provisional; an auto session moved to another week leaves a tombstone and becomes custom),
    so reconcile / auto-adjust never undo it; a refused second move rolls the first back. An accepted
    B2B day follows, hard sessions that end up < 2 days apart get the 休息日 move's warning
    (`rest_days.swap_warnings`), and the watch follows on both days at once (`_sync_watch`, SP-358:
    each pushed copy is re-sent on its new day, the old entries removed). The legend has 「⇄ 交換」
    with the how-to behind its ?;
  - ⋯ → 「刪除所有過期未完成」 (`backend/static/schedule.html:595`);
  - on a done session, planned vs actual (compliance, 「沒照課表」 ≠) and a manual link / unlink to
    an activity (`backend/static/schedule.html:1860`);
  - the session dialog's 結構 (step editor, `engine/workout_steps.py`) open by default and the
    地形與同負荷換算 fold collapsed; the push preview's pace-target warnings;
  - 「課前要吃」 on a 強度課 / ≥ 2 h long run (SP-286, `engine/session_fuel.py`): app text only, never
    pushed to the watch (`fuel_note`, `backend/api/plan_sessions.py:398`,
    `backend/static/schedule.html:1955`) — the owner decided 2026-10-07 to remove it (Open Questions);
  - a done run's post-run self-rating 「自評：Hard（COROS）」 (SP-231, worded by the server:
    `self_rating`, `backend/api/plan_sessions.py:222`; `backend/static/schedule.html:1008`);
  - 記錄睡在高處 on today or a past day, shown as a small mark on the day (SP-259, `high_nights`,
    `backend/api/plan_sessions.py:2729`; `backend/static/schedule.html:2720`), and the 高度適應提醒
    for events ≥ 3,000 m (their GPX) 1–28 days away (SP-100 / SP-258,
    `backend/api/plan_sessions.py:974`). 15–28 days out it is 「安排適應週末」 (`_plan_row`,
    `backend/engine/altitude.py:354`): the weekends of the 14 days before the start, the weekend
    whose Sunday is the departure day included (`weekends`, `backend/engine/altitude.py:327`); no
    place named (owner 2026-10-07: 松雪樓 dropped too) and 行前一晚 2,500 m only in its ? help; its
    id is `altitude_plan:<event>:<start>` (`backend/engine/suggestions.py:295`), so ✕ closes it for
    that trip for good — `prune` keeps the close until the trip starts, even on a day the row is
    not computed (`backend/engine/suggestions.py:353`); the 1–14 day check is another row.
- **課表統計** page (`backend/static/compliance.html`, a tab of 課表; `GET /plan/compliance` →
  `compliance.dashboard`, `backend/engine/compliance.py:284`): KPI tiles, the current phase's
  progress, per-kind breakdown (強度課 split into 有氧間歇 ／ VO2max 間歇 ／ 速度 by `quality_family`,
  then one with no interval work as 強度課 — `FAMILY_ORDER`, `backend/engine/compliance.py:197`;
  each row's own colour, planned → actual TSS on hover, `renderKinds`,
  `backend/static/compliance.html:564`; the session list names the family), planned vs actual TSS / hours and completion rate per 天／週／月,
  the streak (weeks with ≥ 80 % of the due sessions done, 推估), a compact session list, a
  per-phase period filter and a zoned 負荷比 ATL ÷ CTL chart (hover 「值 · 區間（範圍）」, a dashed
  line at 1). 每週存檔 (SP-71, `engine/plan_history.py`; `backend/static/compliance.html:649`): one
  row per stored week, the plan as it began and how it went; `GET /sessions` takes this week's
  snapshot / last week's result when due (`backend/api/plan_sessions.py:458`). The 基線測試 rows
  (SP-71) are in plan-auto.spec.md.
- **課表 toolbar wording** (2026-10-04): 「抓活動／匯入」 = 資料來源 → here, 「推送」 = 課表 → 手錶.
  The push split button reads 推送到手錶 (was 「同步到 COROS」). Left of it, ⟳ 從 {COROS｜TrainingPeaks}
  抓活動 (`#pull-btn`, `backend/static/schedule.html:576`; `pull` `backend/static/schedule.html:1397`)
  runs the same manual sync as 設定 › 立即同步 for the 資料來源 in use only (`GET /api/v1/sync/primary`,
  then the SSE start endpoint through the shared `backend/static/syncrun.js:22`). Not logged in /
  login expired / source switched off → a 到設定頁 link instead (`renderPull`,
  `backend/static/schedule.html:1383`; when COROS is the source the push side's login link covers
  it); hidden in the demo. Progress (已檢查 n · 新下載 m) and the result show in `#sync-msg`; 409
  `SYNC_BUSY` is a hint, not an error. When it ends the calendar reloads (new activities pair:
  ✓／未完成) and, if anything was downloaded, reloads once more ~5 s later together with the
  自動調整 box (`window.autoPlanRefresh`) for the background `plan_auto.after_sync`.
- **Calendar status glyphs** (2026-10-04): 完成 = the chip itself (✓ before the title + compliance
  colour, ≠, 未完成, ● activity chips). 推送狀態 = a small watch at the chip's top right, only on
  active sessions today or later (`SYNC` / `SY_SVG`, `backend/static/schedule.html:952`): 已推送
  neutral grey outline, 需更新 yellow, 失敗 red, 未推送 dashed; labels are provider-neutral
  (手錶). ✓ is never used for push. The legend has two titled groups, 完成 and 手錶
  (`renderLegend`, `backend/static/schedule.html:1179`). 已推送 stays visible (subtle).
- **強度課的家族** (2026-10-04, SP-32; `docs/research/coach-schools-zones-periodization.md` R1). One
  classifier, `workout_templates.family_of` (`backend/engine/workout_templates.py:618`, rule in
  `classify`, `backend/engine/workout_templates.py:594`), splits a structure's `work` steps by
  intensity first, then the median rep length and the median rest between reps:
  **有氧間歇** (≤ 101 % CP, ≤ 102 % LTHR, or a pace not faster than T; sub 長 tempo = reps ≥ 15′ or
  one continuous block, 巡航間歇 = shorter reps, reps < 6′ included, 推估), **VO2max 間歇** (above
  threshold, reps 2–5′; reps < 2′ with a rest < 2× the rep = sub 短間歇, e.g. 30/30, 30/15),
  **速度** (reps ≤ 2′ with a rest ≥ 2× the rep, power > 116 % CP, or untargeted short sprints).
  Above threshold with reps > 5′ is 巡航（超閾值）. Distance reps use T pace (4:48/km without one,
  推估). Used by: the editor's 插入範本 強度課 tabs (有氧間歇 ／ VO2max 間歇 ／ 速度, labels and
  tips through `_()`, `cats`, `backend/engine/workout_templates.py:512`; groups in
  `workout_steps.templates`, `backend/engine/workout_steps.py:1735`, for the published templates
  and the interval ladder's rows alike — Palladino 4×2:40 now files as VO2max, 4×4:30 @ 98–104 %
  as 巡航); the 推薦 block (`template_recs._score`, `backend/engine/template_recs.py:134`: Zone 5
  closed → no VO2max / 速度 template; 基礎期 favours 有氧間歇, 強化期／專項期 巡航間歇; the
  「同一類」 bonus by `_rung_family`, `backend/engine/template_recs.py:236`); and the plan's
  強度課: `session_family` (`backend/engine/workout_templates.py:667`, the stored steps, else the
  derived ones) gives every session read through `_view` (`backend/api/plan_sessions.py:393`) a
  computed `quality_family` `{id, sub, label, sub_label, text}` (None for other kinds). The
  calendar chip shows the family label before the minutes, the full text in its tooltip /
  aria-label, and the session dialog's sub-line adds it (`chipHtml`,
  `backend/static/schedule.html:994`). The scheduler's ladders and gates are unchanged.
- **The three families in the main UI** (2026-10-04, SP-79, the user's decision: 有氧間歇 ／
  VO2max 間歇 ／ 速度, never 「無氧間歇」). Storage stays kind `quality`; the sub-type is the
  `family` column, written only when the athlete picks one (`_clean` validates it, a kind other
  than quality clears it). `session_family` (`backend/engine/workout_templates.py:667`) returns the
  stored family (keeping the derived sub when the steps agree), else `steps_family`
  (`backend/engine/workout_templates.py:655`, the steps / derived structure) — so an old session
  without one still shows its family. `_view` (`backend/api/plan_sessions.py:393`) adds
  `steps_family`; `POST /steps/check` returns `family` (what the edited steps read as) for a
  強度課 (`steps_check`, `backend/api/plan_sessions.py:1497`); `/calendar` adds `family_titles`
  (`FAMILY_TITLES`, `backend/engine/plan_store.py:43`).
  - 課表 editor: the 類型 radios offer 有氧間歇 ／ VO2max 間歇 ／ 速度 in place of 強度課 (each radio
    kind quality + `data-fam`; `setKind`, `backend/static/schedule.html:1431`). A new 強度課 saves
    its family; an existing one only when the athlete clicks another. While the title is still a
    default one, picking a family swaps it (`FAMILY_TITLES`). When the steps read as another family a
    hint says so — 「步驟看起來比較像…；類型照你選的…存」 — and never blocks (`famHint`,
    `backend/static/schedule.html:1438`).
  - 課表日曆: chips and the legend per family (`renderLegend`, `backend/static/schedule.html:1179`):
    有氧間歇 keeps the 強度課 orange (`--s2`), VO2max 間歇 `--qf2` (red #d32f2f / dark #f0605d),
    速度 `--qf3` (purple #8e24aa / #c27ad6) — all ≥ 3:1 against the cell in both themes; a 強度課
    with no interval work stays orange.
  - 課表統計: below.
  - Titles: the generator names the family (「有氧間歇 2×15 分」「有氧間歇（巡航）3×8 分」
    「VO2max 間歇 4×3 分」; plan-auto.spec.md), stored older titles are read in today's words
    (`display_title` → `interval_library.renamed`), so the watch and the calendar feed get the same
    names.
- **訓練目的** (2026-10-04, SP-32): every built-in template has a one-line `purpose`
  (`PURPOSE`, `backend/engine/workout_templates.py:123`, from the report's Finding 7 with the
  coaches it cites, msgids through `_()`); the interval ladder's rows take the purpose of their
  family. The 插入範本 menu shows it under each row's title, and a 強度課 row also shows its sub
  (長 tempo ／ 巡航間歇 ／ 短間歇) as a small tag (`menuHtml`, `backend/static/workout_editor.js:885`).
- **速度 tab add-ons** (2026-10-04, SP-32 follow-up): strides (快步跑 4×20″) and short hill sprints
  (上坡衝刺 8×10″, UA 陡坡衝刺 8×10″) are 速度 by `family_of`, but the menu only gave family tabs to
  the `quality` category, so only Daniels R showed there. `workout_steps.templates`
  (`backend/engine/workout_steps.py:1736`) now also lists them in 強度課 › 速度 (group
  「加速跑與短坡衝刺」, family 速度, their own purpose); they stay under 輕鬆跑 / 越野跑 too.
- **越野跑 in three kinds** (2026-10-04, SP-62, the user's decision). 插入範本 › 越野跑 gets sub-tabs
  (`TRAIL_TYPES`, `backend/engine/workout_templates.py:498`; labels / tips through `_()` with en):
  **結構化爬升** (stairs, steady grades — 登山王, Koop uphill tempo, 陡坡健走, 長爬坡有氧, 陡坡衝刺:
  the HR / power bands keep floor and cap; SP-61 cancelled), **技術地形** (time + climb + RPE, no
  HR / power target) and **下坡技術／離心** (time + descent; `downhill_ecc` no longer targets HR).
  A template says its kind (`Template.trail`); a structure of your own by `trail_type_of`
  (`backend/engine/workout_templates.py:684`: an RPE work step with only a descent → 下坡, any
  other RPE work step → 技術地形, else 結構化爬升). Two technical templates: 技術地形 60′（低 RPE
  3–4, 爬升 300 m） and 90′（RPE 6–7, 爬升 600 m）, `backend/engine/workout_templates.py:423`
  (structure 推估, source / purpose as the others). The step model has a new target type **`rpe`**
  `{lo, hi (Borg CR-10 1–10), up?, down? (m)}` (`_norm_target`, `backend/engine/workout_steps.py:578`):
  it resolves to RPE with the climb, the CR-10 word and a **reference HR as text only**
  (`rpe_hint`, `backend/engine/workout_steps.py:888`: ≤ 4 under the easy cap, 5–6 up to 95 %
  LTHR, ≥ 7 from 95 % LTHR, 推估); its ≈ % CP sizes only the chart and the TSS estimate.
  **Push**: no intensity — the step keeps its time / distance / 直到按下計圈 end and its name
  carries 「RPE 6–7 · 爬升 600 m」 (`rpe_name`, `backend/engine/workout_steps.py:1500`); the watch
  preview lists that limit. **Load / PMC stay the watch's record** (no RPE correction).
  **Easy or quality by RPE**: `rpe_role` (`backend/engine/workout_steps.py:1293`) — a work step
  reaching RPE 7 (很累) = 強度課, else 輕鬆課; POST /steps/check returns it (`rpe_role`), the menu
  tags each row 算強度課 / 算輕鬆課, the editor adds an info line, and the session dialog switches
  the session's type to match when the structure changes (`rpeKind`,
  `backend/static/schedule.html:1749`: 輕鬆跑 / LSD / 越野跑 → 強度課 at ≥ 7, back otherwise), so
  the existing 48 h spacing and hard-day rules apply through the kind.
- **End conditions follow the push target; 「負荷」 (SP-38, 2026-10-04)**. Each workout provider
  declares its step end conditions and their names (`Capabilities.end_conditions` / `end_labels` /
  `load_unit`, `backend/sync/workout_targets/base.py:59`): COROS 時間／距離／直到按下計圈／負荷 (TL),
  Garmin 時間／直到按下 Lap 鍵, intervals.icu 時間. `/steps/derive` returns the active one
  (`context.provider`, read from `plan.push.provider`, `backend/api/plan_sessions.py:1441`) and the
  editor builds the 時長類型 dropdown from it (`endOpts`, `backend/static/workout_editor.js:495`); a
  stored type the provider lacks stays listed as 「（… 不支援）」. 「按圈」 is now 「直到按下計圈」
  everywhere (editor, chart legend, watch preview, issues, race-calculator export switch and hint,
  template step notes). **`load`** = `{"type": "load", "value": TSS}` (1–500), **main-set (work)
  steps only** (`normalize`, `backend/engine/workout_steps.py:640`). Its time is estimated
  TSS ÷ (IF² × 100) h at the step's ≈ % CP (`load_if`, `backend/engine/workout_steps.py:1097`; 推估),
  so the chart, the total and TSS 估 include it; the ladder counts such a main set by the same
  formula at the band's middle (`load_work_s`, `backend/engine/workout_steps.py:1414` — see
  plan-auto.spec.md). The editor shows the provider's conversion next to
  the TSS: COROS 「≈ N TL（推估 ±E）」 (`load_tl` → `engine/coros_tl.py`, refit per athlete after
  each sync — wko5-coros-sync.spec.md). **Push**: COROS gets its training-load end condition,
  `targetType 6`, `targetValue` = the TL (integer; read back from a Training Hub workout with a
  「TL 100」 end condition), the step's intensity target unchanged (`COROS_TARGET_TYPE_LOAD`,
  `backend/sync/coros_workouts.py:70`); with that constant unset, and on every provider without a
  load end condition, the step goes as the estimated time (COROS: 「負荷 X TSS（約 Y TL）」 in the
  name) and the editor lists an issue. The fingerprint is the payload, so a TL refit marks only
  sessions whose load step's sent TL moved by ≥ 3 TL as 需更新 (`TL_RESEND_MIN`, 推估; a smaller
  move keeps the TL last pushed, read from the closed-loop record — `sent_tl`,
  `backend/engine/workout_steps.py:1117`). Pushed load steps are recorded
  (`load_records`, `backend/engine/workout_steps.py:1127`) for the closed-loop correction.
  `workout_templates.session_role` (`backend/engine/workout_templates.py:707`) gives the same
  answer for a stored session. The editor's target menu adds 「RPE＋爬升」 with RPE / 爬升 / 下降
  fields (`tgHtml`, `backend/static/workout_editor.js:522`); the static demo's JS port follows.
  The 推薦 block: 基礎期 favours the low-RPE technical session, 專項期 the race-like one
  (`TRAIL_SPECIFIC`, `backend/engine/template_recs.py:49`). Not done: week_plan does not generate
  技術地形 sessions itself, and a user's own quality-kind session is not counted into the
  generator's 20 % interval budget.
- **「負荷」 entered by RPE (SP-57, 2026-10-05, the user's answers)**. Next to a load step's TSS
  the editor has a 「負荷怎麼填」 choice TSS／RPE（感覺）(`loadIn`, `backend/static/workout_editor.js:506`;
  the pure field update `loadDur`, `backend/static/workout_editor.js:221`): by RPE the user picks one
  of five levels 輕鬆／稍累／累／很累／極限 = Borg CR-10 2／4／5／7／10 (Foster's anchors: easy,
  somewhat hard, hard, very hard, maximal; `LEVELS`, `backend/engine/rpe_load.py:57`) and the
  minutes. Stored as `{"type": "load", "value": TSS, "rpe": easy|moderate|hard|very_hard|max,
  "min": minutes}` (1–360 min); `normalize` sets `value` = factor × CR-10 × minutes — Foster session
  RPE × the athlete's TSS-per-AU factor (default 0.30, 推估; refit per athlete after each sync on
  the watch-recorded RPE vs the activity's TSS — wko5-coros-sync.spec.md) — and refuses a level it
  doesn't know, minutes out of range or a result > 500 TSS (`_norm_rpe_load`,
  `backend/engine/workout_steps.py:737`). **RPE + minutes, not a TSS/h rate × the step's time**: a
  load step's time is itself estimated from its TSS, so a rate would be circular; the minutes are
  what session RPE is. Such a step is timed by its minutes and converted (TL, closed loop) at the IF
  they imply, √(TSS ÷ (100 × h)) clamped to 0.4–1.3 (`load_if`,
  `backend/engine/workout_steps.py:1097`); the rest — `value` in TSS, the COROS TL end condition,
  the fingerprint, the closed-loop record — is the typed load step's. Because `normalize` runs with
  the factor in effect, a refit moves the TSS; the push keeps the TL last sent while it moves < 3 TL
  (`TL_RESEND_MIN`). The editor shows 「≈ N TSS ≈ M TL（推估 ±E）」 and, in its tooltip, the factor and
  whether it is the default or calibrated (`view` adds `load.rpe` {level, min, factor, fitted,
  err_pct}, `backend/engine/workout_steps.py:1676`); `/steps/derive` context `rpe_load` lists the
  levels and the factor (`_rpe_load_ctx`, `backend/api/plan_sessions.py:1448`). **Planning only**
  (the user, 2026-10-04): the PMC, the overview and every guardrail keep the watch's recorded load —
  RPE never corrects an activity's TSS. The static demo's JS port follows (default factor).
  (`TRAIL_SPECIFIC`, `backend/engine/template_recs.py:49`). week_plan generates 技術地形 sessions
  itself (SP-74) and counts the user's own RPE ≥ 7 ones into the 20 % budget (above); a user's own
  interval (kind quality) is still not counted.
- **範本 page and 我的範本** (2026-10-04, SP-36, the user's answers). 課表's third mode card
  **範本** (`backend/static/templates.html`, `GET /plan/templates/page`,
  `backend/api/plan_sessions.py:2791`; also on 課表統計) lists the user's own templates and the
  built-in library (filter 全部／我的／內建, by category, by name); the chosen one opens in the
  same step editor (`WorkoutEditor.load`, `backend/static/workout_editor.js:352`) with its name,
  categories, 目標用 (自動／心率／功率) and note. Built-in rows are read-only with their source;
  「複製成我的範本」 copies one (`backend/api/plan_sessions.py:1582`). Storage: tables
  `workout_templates_user` / `workout_template_cats` (`backend/db/models.py:364`,
  `backend/db/models.py:388`; created by create_all, and on demand in a DB made before them,
  `_ensure`, `backend/engine/user_templates.py:95`) through `engine/user_templates.py`:
  `clean` (`backend/engine/user_templates.py:177`: name 1–40, categories from the built-in ids
  easy / quality / test / trail and the user's own `c<id>`, several per template, steps through
  `workout_steps.normalize`, 目標用 hr / power / 自動) and custom categories add / rename / delete
  (`backend/engine/user_templates.py:124`; deleting one takes it off its templates). **Targets are
  stored as written**: % CP / % LTHR / zones / 自動 / RPE / 不設目標 and 「直到按下計圈」 resolve
  with the day's thresholds when used; absolute W / bpm / pace stay. 「自由模式」 = 直到按下計圈 +
  no target (or RPE＋爬升) — the existing step model, nothing new; the 「長間歇、自由模式」
  uphill template pushes as an open-ended COROS group with no intensity. In the editor's
  插入範本, `workout_steps.templates(user=…)` (`backend/engine/workout_steps.py:1736`) puts
  「我的範本」 first in every category a template is in (`groups`,
  `backend/engine/user_templates.py:514`: 強度課 under its `family_of` sub-tab — none → every
  sub-tab —, 越野跑 under its `trail_type_of` kind) and adds the custom categories as tabs; rows are
  tagged 我的 / ▲ GPX. Applying one in the session dialog also sets the session's `target_basis`
  (`backend/static/schedule.html:1701`, saved with it, `backend/static/schedule.html:2001`).
  **儲存成範本** in the editor (`saveForm`, `backend/static/workout_editor.js:1039`): name +
  categories, the current structure; `POST /sessions/{uid}/save-as-template`
  (`backend/api/plan_sessions.py:1664`: the body's steps, else the stored, else derived; the
  session's 目標用) or `POST /steps/templates/user` for an unsaved session. **Route GPX**: a
  template may carry a training-route GPX / FIT (upload, replace, remove, download); parsed with the
  race calculator's reader and builder (`parse_profile`, `backend/engine/user_templates.py:313`:
  `racepower/gpx.parse` + `course.build_course`, no parser of its own), the file gzipped per tenant
  (`<HOME>/template_gpx/<id>.gz`, a demo sandbox's private dir), the profile cached in the row. A
  structure made from it keeps the template id (`tpl`, kept by `normalize`,
  `backend/engine/workout_steps.py:640`) and, once saved as a session, **its own copy of the
  profile** (`route` {km, z, route_km, gain_m, name}: `route_copy`,
  `backend/engine/user_templates.py:385`, the ≤ 400-point downsample; validated by `_norm_route`,
  `backend/engine/workout_steps.py:755`, malformed copies dropped; a template never stores one).
  The copy is embedded on save (`_with_route`, `backend/api/plan_sessions.py:1785`, from
  POST / PATCH /sessions; a re-save with the same `tpl` keeps the stored copy, so the session's
  profile doesn't follow later changes to the template's GPX), and deleting the template or its
  GPX leaves it on the session (2026-10-04 follow-up, the user's decision). POST /steps/check
  returns `elev` (`backend/api/plan_sessions.py:1518`, the copy first, else the template's):
  **with a GPX the step chart switches to a distance axis** — real km along the route, as the race
  calculator's course profile (2026-10-04 follow-up, the user's decision; without a GPX the time
  axis stays). `route_elevation` (`backend/engine/user_templates.py:422`) walks the run order and
  gives each step's start km (`x`, one per `view` order row plus the end): a distance step covers
  its own km of the route; a time / 直到按下計圈 / 負荷 / RPE step covers its effort distance
  (km + climb ÷ 100 on the route's own climb) at the athlete's speed for its intensity
  (`speed_kmh`, scaled to the trail EP speed; untargeted rests walk; a lap-button step without an
  estimate its 90 s chart width), turned back into km through the route's climb; past the route's
  end 1 : 1 (推估). The profile (`d` km, `z` m) is drawn up to where the workout ends (a route
  longer than the workout: the legend says so). The editor (`chart`,
  `backend/static/workout_editor.js:604`) places the bars by `x` with km ticks and the step's km
  range in the tooltip, the elevation behind them as a light area + thin line with its own m scale
  (`elev`, `backend/static/workout_editor.js:659`), in the chart viewer's neutral elevation colour.
  **我的範本 in 推薦** (2026-10-04 follow-up, the user's decision): GET /steps/templates/recs
  (`backend/api/plan_sessions.py:1700`) ranks the user's templates with the built-ins by the same
  `_score` rules (family, Zone 5, phase, the ladder rung's 同一類 bonus, time, terrain, type); the
  key-listed trail phase rules take a user row by its 越野跑 kind (`_trail_key`,
  `backend/engine/template_recs.py:219`: 結構化爬升 → the long climb, 下坡 → 下坡離心 incl. its
  taper exclusion, 技術地形 hard / easy by `rpe_role`); a pick carries `mine` and the menu tags it
  我的; custom category tabs get 推薦 too.
  **主課強度 filter** (2026-10-05, SP-84): after the category, both lists filter by the main
  set's target type. One helper, `workout_steps.target_types` (`backend/engine/workout_steps.py:1340`):
  the targets of the work steps (inside repeats too; none → the 「其他」 steps, e.g. strides; none
  → every step), each type a mixed main set uses, in `TARGET_TYPE_IDS` order
  (`backend/engine/workout_steps.py:1307`): % CP 功率區間 (pct and Palladino zones) / 絕對功率 /
  % LTHR 心率 / 心率區間 (≤ AeT, Friel, 課表心率區間) / 絕對心率 / 配速 / RPE / 自動（依課表類型）/
  不設目標 (incl. a 自動 open step) / 負荷 (a 「負荷」 end condition on the main set). A 「自動」 band
  takes the template's 目標用 (library `basis`, user `target_basis`: power → % CP, hr → % LTHR),
  else stays 自動 — so the interval ladder's variants are 自動 (the session's 目標用 decides); an
  自動 easy step is 心率區間 unless a power band under 目標用 power. Every row of
  GET /steps/templates (`backend/engine/workout_steps.py:1818`) and GET /steps/templates/user
  (`row`, `backend/engine/user_templates.py:499`) carries `target_types`; both responses carry
  the type list `target_types` [{id, label}] (translated labels, `backend/api/plan_sessions.py:1563`). The UIs
  show only the types the listed rows use (by category and source; one type → no chips) plus
  全部強度: the 範本 page under its category chips (`rows`, `backend/static/templates.html:229`),
  插入範本 under its category tabs, over the 推薦 block and the rows (`menuHtml`,
  `backend/static/workout_editor.js:892`). The choice is kept per viewer in localStorage
  (`templates.target_type`, shared by both, try/catch; `backend/static/templates.html:197`,
  `backend/static/workout_editor.js:258`); a kept type the current category doesn't use shows 全部.
  Demo: the routes are sandbox writes (`backend/tenancy_mw.py:51`), each visitor's own; the static
  demo shows the page read-only (the write controls locked, `backend/demo/export_static.py:108`).
  i18n: page namespace `templates`, editor strings `common.workout.*`, server messages via `_()`,
  zh-TW + en.

## Status engine change

- **`i_level`** (SP-291, `backend/engine/status.py`): the 資料等級 card — `data_level.level` (the
  same level the week plan and the race feasibility read), shown only at level 0 / 1 (info): text
  「等級 n：累積中」, verdict = the plan page's line (without the HR clause), why = the rule's numbers
  and 「現在 k/4 週」, action = fill in the 跑步經驗問卷 while it would stand in and isn't answered.
  Nothing at level 2. The `/status` cache key also carries `experience.stamp()` (the answers word
  the line).
- `Status.weekly_hours()` sums moving time (fallback recorded time) instead of recorded
  time (`backend/engine/status.py:185`), so the volume indicators aren't inflated by multi-day
  trips.
- **`i_drift`** (`backend/engine/status.py:574`) is **informational**: the same per-run drift as
  the single-activity review (`workout_review.drift_series`, `backend/engine/workout_review.py:2462`):
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
  once the block ends (WKO5 seminar notes). `i_fitness` (`backend/engine/status.py:316`): CTL
  ramp at `load_guard`'s block line bad, watch line watch (SP-63: relative lines; SP-68: on the
  PMC's own started CTL, so the CTL shown and the guardrail's agree; 起算期 info in the first 28
  days of an automatic start, the first 7 after a manual one, with `ramp_week = None`; `extra`
  adds `ramp_base`, `ramp_level`, `ramp_lines`, `ramp_startup`, `pmc_start` = the source). `i_volume` (`backend/engine/status.py:435`): the
  headline stays all-sport moving hours; the step is running time against max(the week before,
  4-week mean) of normal weeks (`load_guard.skip_mondays` / `normal_weeks`: a week touching a
  減量期 / race / 恢復期 / 轉換期 is left out, the why says 「不含減量期／比賽週／賽後恢復期／轉換期的週」;
  SP-73) — > 20 % bad (Nielsen 2014 / Damsted 2019), 10–20 % watch (推估), good with the reason
  when the week before had a 3–5-day break without a run, ≥ 3 of its days unplanned (not on the user's
  不排課日期 / 休息日 / unticked 可練日; `load_guard.short_break`, `Status(blackouts=, prefs=)`); `extra` adds
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
- **`i_gate`** 「間歇門檻」 (`backend/engine/status.py:658`): `quality_gate.evaluate` +
  `indicator` (`backend/engine/quality_gate.py:2827`) with the status' 課表偏好 (`Status(prefs=…)`;
  the API's status cache keys on `prefs.stamp()`, `backend/api/overview.py:62`). Second in
  `PHASE_PRIORITY["base"]` (`backend/engine/status.py:1147`), so its WATCH action lands in 還缺什麼.
  Texts per the design doc §4.6: auto without AeT → info 「沒有 AeT 實測：照 80/20 原則每週 1
  次間歇（第 N 步：…）」; a blocking guardrail → watch with its number (e.g. 「本週不排間歇：低強度只有
  68%（< 75%）」); ua_gap locked → 「AeT 142 / LTHR 165：差距 16%（> 10%，有氧不足）」; unlocked →
  good 「差距 9% ≤ 10%：可以加 Zone 3」; forced + missing → watch 「沒有實測 AeT，差距法算不出來：先照
  護欄排（自訂…）」, action 「先做 AeT 飄移測試，或把間歇門檻改回自動」. `why` names the mode and
  the AeT source (「AeT 146（活動資料估算）」 / 「（{date} 飄移測試）」). `extra` is the gate dict incl.
  `options` (per mode usable + why, `backend/engine/quality_gate.py:2433`).
- `PHASE_GOAL["base"]` no longer says 飄移 < 5 %; `PHASE_FOCUS["base"]` cites UA for the easy long
  run and Palladino for the 8–15 s hill sprints (`backend/engine/status.py:1158`).
- `i_data`'s action for a missing AeT is 「排一次 AeT 飄移測試（平日，10 分暖身＋40 分固定功率，跑步機或平路）；
  測了可以改用有氧基礎門檻」 (`backend/engine/status.py:1027`).
- **`i_testing`** (`backend/engine/status.py:864`) — a CP row older than 42 days → watch, 90 →
  bad (`backend/engine/status.py:64`); LTHR is event-driven (an applied estimate is said as one,
  not judged by age) and AeT goes by reason (B3); event-driven retests (`zone_events`: HR shift
  at the same power, a ≥ 4-week break, the first cool spell) are **suggestions only** — they turn
  the card watch (「建議測」) and go to the floating box, never into the plan; 10–21 days before
  the A event is named the right time; < 10 days → 「賽前 10 天內不要測，賽後再測」, watch. The
  action names the 課表偏好 protocol (`_cp_protocol`, `backend/engine/status.py:852`); `race` →
  「用 5–10 K 比賽或計時跑代替 CP 測試」. It also reads the latest CP test in the data
  (`workout_review.latest_cp_test`, 120 days, `backend/engine/workout_review.py:2549`), whose
  `delta` is against the **previous result of the same method** (`cp_protocols.reference`;
  across methods converted two-point ≈ 1.05 × a 30-min CP, 外插), so rotating quick / standard
  doesn't keep flagging. Not applied (no CP row dated on / after the test), an `apply` payload
  (not 不採用) and |delta| > `CP_DELTA` 3 % → at least watch, 要更新, action 「套用這次的 CP」.
  `extra.cp_test` carries method, quality, `ref`, `apply`, `applied`; the 總覽 測試 card draws
  the apply button from it (`applyCpBtn`, `backend/static/overview.html:538`), POSTing
  `/api/v1/plan/thresholds/apply-cp` (see `workout-review.spec.md`).
  `extra.cp_due` (CP missing / > 42 days, `backend/engine/status.py:902`) decides the CP-test
  suggestion in `week_plan` (the CP test measures CP only). The latest AeT drift test
  (`aet_test.latest_aet_test`) → `extra.aet_test` (`backend/engine/status.py:962`): band "at" and
  not applied → watch 「{date} 的 AeT 測試：飄移 4.2%，AeT = 146 bpm（目前 142）」, action 「套用這次的
  AeT（146 bpm）」, and the 總覽 測試 card's button (`aetApply`, `backend/static/overview.html:494`)
  POSTs `extra.aet_test.apply` to `/api/v1/plan/thresholds/apply-estimate` with the test `date`;
  band below / above → 「下次起始心率 +5／−5 bpm 再測一次」; a 徐國峰 90′ / Friel result is a base
  check with no AeT to apply. The old 「AeT 已經 N 週沒測」 age rule is gone (B3: a reason, not a
  date).
  **Threshold confidence (SP-64)**: `i_testing` also runs `threshold_confidence.check`
  (`backend/engine/threshold_confidence.py:907`, wired at `backend/engine/status.py:1001`): LTHR /
  max HR / resting HR confidence (high / medium / low) from 8 LTHR signals (source, estimate
  premise, easy cap ≥ LTHR and % HRmax / % HRR, long efforts above LTHR, 40–60 min races < 95 %,
  the CP-band cross-check, events, age) and the max-HR plausibility check (highest HR held 120 s,
  spikes / cadence lock filtered, `hrmax_check`, `backend/engine/threshold_confidence.py:383`);
  `diagnose` (`backend/engine/threshold_confidence.py:437`) names the likely wrong value. Its
  suggestion (`thr_check:<tests>`, tests `hrmax` / `tt30`, `links` = 「安排課表」 deep links,
  `schedule_link`, `backend/engine/threshold_confidence.py:571`; `wait_cool`, `earliest` after
  the A race in taper / race week, `test_conditions`, `backend/engine/threshold_confidence.py:479`)
  joins `test_suggestions` (a low source alone → `priority: low`); the why gets the diagnosis and
  「LTHR 可信度低」; `extra.thr_check` carries the confidences, signals, diagnosis, the latest
  LTHR 30-min / max-HR test results and `warn`. The 總覽 測試 card shows them with the links
  (`thrCheck`, `backend/static/overview.html:520`); the floating box renders `links` for the
  tests it doesn't schedule (`zone_rows`, `backend/engine/suggestions.py:162`). Nothing is applied
  automatically: 設定 shows the results / candidate with 「套用」 (`renderThrCheck`,
  `backend/static/settings.html:886`). HR-target templates and sessions get a warning badge in
  the editor from `thresholds.thr_warn` (`_thr_warn`, `backend/api/plan_sessions.py:204`).
  SP-274 / SP-277: per session `POST /steps/check` returns `thr_warn` from
  `threshold_confidence.session_warn` — never on a test session (`is_test_session`: kind test or a
  test title: 90-minute / UA / Evoke / Friel 30′ / max HR), and only when a step's HR target is
  worked out from LTHR (`lthr_step`: zones, % LTHR, an interval band on HR, the easy-run cap
  unless it is a measured AeT; not a typed bpm range). The template rows use the same rule
  client-side (`usesLthr`; no badge in the 測試 category). `lthr_warn`: the source alone →
  「LTHR 是估算的；輕鬆跑可以改用講話測試的配速，做一次 30 分鐘測試後這個提醒會消失」; other reasons
  keep the why and add the way out. A manual LTHR stays 中 (no warning) with the small
  「手動輸入，沒有驗證」 (editor and 設定's LTHR card).
- **AeT drift test** (`backend/engine/aet_test.py`): `due` (`backend/engine/aet_test.py:515`) —
  base phase, a reason (`quality_gate.aet_test_reason`: no data for ~6 weeks, the aggregate's SE
  too large, a shift, the estimate moved) and no test in the last 28 days (推估); no fixed
  cadence. A due test is a **suggestion** (`week_plan.test_suggestions`), never a planned
  session. The session (`aet_test.session(th, hr0, p0, cap_weekday, protocol)`,
  `backend/engine/aet_test.py:561`) is kind `test`, protocol per `plan.prefs.aet_test_protocol`
  (plan-auto.spec.md); the UA versions: target 「固定功率 P W（±3%）；心率從 HR 附近開始」 (start HR
  = the estimate's aethr, else 0.89 × LTHR − 5; P = 0.75 × CP, both our choice), and their
  length follows the 課表偏好 weekday cap (`variant_for`, `backend/engine/aet_test.py:532`): no cap / ≥ 80 →
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
  `coros-sync` (`_aet_test_steps`, `backend/sync/coros_workouts.py:338`): 10 / 40 (no
  cool-down step) or 15 / 60 / 5.
- Inline source names were removed from engine text (e.g. the ramp verdict, phase focus).

## API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/v1/overview/status` | `Status.to_dict()`: today, phase, goals, headline, indicators, actions, counts, `data_level` (SP-291) (`backend/api/overview.py:99`) |
| GET | `/api/v1/overview/summary?unit=week\|month\|year&anchor=&n=` | buckets + current detail; n capped 104 / 60 / 12 (`backend/api/overview.py:106`) |
| GET | `/api/v1/overview/pmc?begin=&end=` | daily tss / ctl / atl / tsb; default the last 180 days (the page asks for 90) (`backend/api/overview.py:116`) |
| GET | `/api/v1/overview/weekplan` | the generated week plan, with the stored 課表偏好, 不排課日期, accepted B2B weekends and the race calculator (`backend/api/overview.py:127`) |
| GET | `/api/v1/overview/z5` | the 「5 區（最大攝氧量間歇）開放流程」 card: `quality_gate.z5_card` on the status gate (the object the week plan decides with) + `history_href`, the viewer link of the first `z5gate` panel (`backend/api/overview.py:140`) |
| GET | `/api/v1/overview/b2b` | the B2B card (`engine/b2b.card`): target event, this week's B2B state, planned B2B weekends, each done B2B's day-2-vs-day-1 reading and the trend (`backend/api/overview.py:152`) |
| GET | `/api/v1/overview/feasibility?event_id=` | 「賽事完備程度」 for the upcoming A / B races or one event (`engine/race_feasibility.py`; SP-105 moved it from 總覽 to the season-plan page, SP-292 added 資料等級 0 / 1); advice only (`backend/api/overview.py:180`) |
| GET | `/api/v1/overview/page` | `backend/static/overview.html` (`backend/api/overview.py:226`) |
| GET | `/api/v1/overview/plan/sessions?start=&end=` | reconcile-if-needed, match new runs, then stored sessions (not deleted / superseded) with push status, week meta, projected weeks and `summary` (`backend/api/plan_sessions.py:444`) |
| POST | `/api/v1/overview/plan/sessions` | add a custom session; 400 on a bad field (`backend/api/plan_sessions.py:527`) |
| PATCH | `/api/v1/overview/plan/sessions/{uid}` | edit day / kind / minutes / title / target / detail / terrain / distance_km / climb_m / target_basis / steps; 400 on a bad field (`backend/api/plan_sessions.py:565`) |
| DELETE | `/api/v1/overview/plan/sessions/{uid}` | tombstone (auto) or remove (custom); an expired one is tombstoned and taken off the watch; a B2B day cancels the pair; 404 when unknown (`backend/api/plan_sessions.py:1865`) |
| POST | `/api/v1/overview/plan/sessions/swap` | `{a, b}` → the two sessions trade days (SP-359; both the user's own move), `{sessions, warnings, coros}`; 400 when either is done / past / missing / a notice, on the same day, or a day is blocked (`backend/api/plan_sessions.py:589`) |
| POST | `/api/v1/overview/plan/sessions/expired/delete` | `{uids?}` → tombstone those (or all) expired open sessions, remove pushed copies; 400 for a uid that isn't one (`backend/api/plan_sessions.py:1846`) |
| POST / DELETE | `/api/v1/overview/plan/sessions/{uid}/link` | `{index}` → pair the session with an activity / undo (the activity then stays unplanned) (`backend/api/plan_sessions.py:1891`, `backend/api/plan_sessions.py:1873`) |
| GET | `/api/v1/overview/plan/reconcile` | preview: changes and changes by day (`backend/api/plan_sessions.py:1929`) |
| POST | `/api/v1/overview/plan/reconcile` | apply the same; optional body `{decisions}` for sessions on a 不排課日期 (`backend/api/plan_sessions.py:1946`) |
| GET | `/api/v1/overview/plan/push-coros/preview?scope=day\|week\|phase&day=` | sessions in range with push status, counts to send / unchanged / skipped, missed to remove, `pace_notes`, pending changes; 400 for a past week (`backend/api/plan_sessions.py:1963`) |
| POST | `/api/v1/overview/plan/push-coros?scope=&day=` | reconcile, push the range through the active provider, clean up; 401 `COROS_AUTH_REQUIRED`; 400 for a past week (`backend/api/plan_sessions.py:2058`) |
| DELETE | `/api/v1/overview/plan/push-coros?scope=&day=` | remove what was pushed in the range; 400 for a past week (`backend/api/plan_sessions.py:2088`) |
| GET | `/api/v1/overview/plan/prefs` | `{prefs, defaults, active, day_conflicts, pref_dropped, gate_options, aet_options}` — `gate_options` = the 間歇門檻 hover texts (`quality_gate.option_texts`, `backend/engine/quality_gate.py:2967`) (`backend/api/plan_sessions.py:2146`) |
| GET | `/api/v1/overview/plan/prefs/gate` | per mode `{usable, why}` on the athlete's data, plus the active mode / state / verdict (status `i_gate`, `backend/api/plan_sessions.py:2136`) |
| POST | `/api/v1/overview/plan/prefs/conflicts` | an unsaved preference set → `{day_conflicts, overlaps}`; nothing stored (`backend/api/plan_sessions.py:2154`) |
| GET | `/api/v1/plan/thresholds` | 設定 › 閾值測試紀錄 (SP-46): `{today, thresholds, effective_thresholds, power_zones, wko5_settings}` — the same rows and effective values as `GET /api/v1/plan`, without the rest of the season plan (`backend/api/plan.py:319`); saved with `PUT /api/v1/plan/thresholds` (whole table, `backend/api/plan.py:335`) |
| POST | `/api/v1/plan/thresholds/apply-estimate` | now takes an optional `date` (the test day; not in the future) so 「套用這次的 AeT」 dates the row on the test; SP-64: also `mhr` + `mhr_method` (`planning.MHR_METHODS`; a max-HR test = test, the sustained-peak candidate = estimate) and `lthr_method: friel30` for a 30-min test (`backend/api/plan.py:879`) |
| GET | `/api/v1/plan/threshold-check` | SP-64: `threshold_confidence.check` — LTHR / max / resting HR confidence and signals, diagnosis, suggestions, latest test results with `apply` bodies; nothing saved (`backend/api/plan.py:854`) |
| PUT | `/api/v1/overview/plan/prefs` | the whole preference set (Prefs field names, missing = default); 400 on a bad / unknown value or a cross-field rule (`backend/api/plan_sessions.py:2165`) |
| GET | `/api/v1/overview/plan/blackouts` | `{blackouts}` — the stored 不排課日期 (`backend/api/plan_sessions.py:2202`) |
| POST | `/api/v1/overview/plan/blackouts/preview` | `{blackouts}` → the reconcile preview with that list; nothing saved; 400 on a bad range (`backend/api/plan_sessions.py:2209`) |
| PUT | `/api/v1/overview/plan/blackouts` | `{blackouts, decisions?}` → save, then reconcile applying `decisions` `{uid: move \| delete}`; 400 on a bad range / decision (`backend/api/plan_sessions.py:2220`) |
| POST / DELETE | `/api/v1/overview/plan/rest-days`, `/rest-days/{day}` | `{day}` → add a 休息日 (400 for a past or already blocked day) / remove it (404 when not one); both reconcile (`backend/api/plan_sessions.py:2272`, `backend/api/plan_sessions.py:2256`) |
| GET | `/api/v1/overview/plan/equivalence` | the time model, LOO backtest per terrain, 推估 flags, sources; memoised per dataset / day / AeT (`backend/api/plan_sessions.py:2414`, `backend/api/plan_sessions.py:2368`) |
| POST | `/api/v1/overview/plan/equivalence/design` | `{mode, minutes, climb_per_km}` → km, climb, 推估 flag (`backend/api/plan_sessions.py:2419`) |
| GET | `/api/v1/overview/plan/calendar?start=&end=` | the 課表 page payload (≤ 120 days): sessions with `tss_est`, planned vs actual `vs`, `compliance`, `link_options`, a 強度課's `quality_family` and `steps_family`, `family_titles`; `week_rows`, `prefs`, `goal_climb_per_km`, `plan_notes`, `test_suggestions`, `test_templates`, `expired_open`, provider state; SP-362 B4: `stale: {reason, age_s, since}` when answered from the previous inputs while the fresh ones are computed (display only) (`backend/api/plan_sessions.py:2840`, `backend/api/plan_sessions.py:2870`) |
| GET | `/api/v1/overview/plan/fresh` | SP-362 B4: `{updating}` — the fresh plan behind a stale calendar is still being computed in the background; computes nothing (`backend/api/plan_sessions.py:615`) |
| GET | `/api/v1/overview/plan/schedule/page` | `backend/static/schedule.html` (`backend/api/plan_sessions.py:2745`) |
| GET | `/api/v1/plan/calendar` | 課表訂閱: `{enabled, path, url, window}` of the feed address (`backend/api/calendar_feed.py:88`) |
| POST / DELETE | `/api/v1/plan/calendar/token` | `{origin?}` → a new secret address (the old one is a 404 from now on) / turn the feed off (`backend/api/calendar_feed.py:93`, `backend/api/calendar_feed.py:104`) |
| GET / HEAD | `/share/calendar/<token>.ics` | public, token-only: the stored plan as `text/calendar` (see 課表訂閱 above); 404 for any other token; not in the demo (`backend/api/calendar_feed.py:122`) |
| GET | `/api/v1/overview/plan/compliance?start=&end=` | the 課表統計 dashboard (≤ 371 days): due sessions with status and %, totals, weeks and days planned vs actual, streak, per kind, the current phase's progress, `plan_phases` (`backend/api/plan_sessions.py:2468`; route `backend/api/plan_sessions.py:2762`) |
| GET | `/api/v1/overview/plan/compliance/page` | `backend/static/compliance.html` (`backend/api/plan_sessions.py:2797`) |
| GET | `/api/v1/overview/plan/history?limit=` | 每週存檔 (SP-71): the stored weeks, newest first — what each week planned when it began (frozen) and, once over, how it went; plus how many activities are marked 比賽 (`backend/api/plan_sessions.py:2810`) |
| GET | `/api/v1/overview/plan/templates/page` | `backend/static/templates.html`, the 範本 tab (`backend/api/plan_sessions.py:2819`) |
| GET / POST | `/api/v1/overview/plan/steps/templates/user` | `{templates (each with its menu row), cats (built-in + custom), limits}` / create `{name, cats, steps, target_basis?, note?}` → 400 `{errors}` (`backend/api/plan_sessions.py:1557`) |
| POST | `/api/v1/overview/plan/steps/templates/user/copy` | `{key}` of a built-in 插入範本 row → a template of the user's (`copied_from`); 404 for an unknown key (`backend/api/plan_sessions.py:1582`) |
| PATCH / DELETE | `/api/v1/overview/plan/steps/templates/user/{id}` | any of name / cats / steps / target_basis / note; delete (and its GPX file); 404 when unknown |
| POST / DELETE / GET | `/api/v1/overview/plan/steps/templates/user/{id}/gpx` (`/gpx/file`) | multipart upload or replace (400 on a file that isn't a course) / remove / the stored file (`backend/api/plan_sessions.py:1609`) |
| POST / PATCH / DELETE | `/api/v1/overview/plan/steps/templates/cats`, `/cats/{cid}` | `{label}` → a custom category / rename / delete (taken off its templates) |
| POST | `/api/v1/overview/plan/sessions/{uid}/save-as-template` | `{name, cats, steps?, target_basis?}` → a template from the session's structure (`backend/api/plan_sessions.py:1664`) |
| GET | `/` | always redirects to the overview page (`backend/main.py:229`; the React SPA was removed 2026-10-04 — old SPA paths such as `/activities`, `/achievements`, `/sync`, `/config` redirect to their static pages, `backend/main.py:36`); in demo mode to `/demo` |

The same router also serves the suggestion box (`/suggestions`, `/suggestions/accept`,
`/suggestions/dismiss`, `/test-suggestions*`), the test templates / options and the 課表
editor's step and variant endpoints (`/steps/*`, `/steps-preview`, `/variants`,
`/sessions/{uid}/coros-preview`); they are not specified here (see plan-auto.spec.md for the
interval library and suggestions).

`Status` is memoised per (tenant, dataset, day, `plan.json` mtime, the 課表偏好 stamp, stored
test sessions, the 課表心率區間 stamp), LRU per tenant (`backend/api/overview.py:78`); the
plan endpoints memoise their generator inputs on the dataset / day / plan key plus the
preference, blackout, auto-replan, accepted-B2B, 主要訓練項目 and HR-profile stamps
(`backend/api/plan_sessions.py:84`). Bad scope or day → 400 (`backend/api/plan_sessions.py:371`).

## Debug API for AI agents (`api/debug.py`, `debug_auth.py`, SP-371)

A read-only HTTPS view of the stored plan, a day, an activity, the thresholds, the sync and the
athlete's settings, for an AI agent debugging production without access to the host. The full
field reference and curl examples are **`docs/debug-api.md`**; this section is the contract.

**Auth** (`backend/debug_auth.py`): a yield dependency `gate` (`backend/debug_auth.py:384`) on every
`/api/v1/debug/*` route —
1. 404 (FastAPI's own `{"detail": "Not Found"}`) in the demo, without `TRC_DEBUG_PIN` (≥ 6
   characters) or with `debug.api.enabled` off (`backend/settings/repository.py:238`);
2. the token from `X-TRC-Debug-Token` (so `Authorization` stays free for the owner's password
   proxy, the chosen deployment) or `Authorization: Bearer` (`_token`, `backend/debug_auth.py:351`) —
   cookies are never read; its SHA-256 is looked up in `debug_tokens` (`backend/db/models.py:429`) of
   the current tenant and must belong to its `tenant_id`, not be revoked or expired → else 401
   (`TOKEN_MISSING` / `INVALID` / `REVOKED` / `EXPIRED`), counted per source IP (`FAIL_BUCKET` 10 /
   10 min, then that IP's *failed* attempts get 429 `BLOCKED` for 10 min) and aggregated per hour ×
   IP × code in `debug_auth_failures` (`backend/db/models.py:468`, `record_failure`
   `backend/debug_auth.py:274`; newest 500 rows). The token is checked first: a valid token is never
   refused by the IP block (behind Docker / cloudflared every caller may share one IP);
3. a valid token: `TOKEN_BUCKET` 60 / min and `TENANT_BUCKET` 120 / min (`backend/debug_auth.py:72`)
   → 429 `RATE_LIMITED`; `?gps=1` needs `read:gps` → 403 `SCOPE_GPS`; `CALL_GATE` (2 concurrent) → 503
   `BUSY`. A 429 / 503 stores nothing (in-memory counters for the settings page);
4. `need(*scopes)` / `need_any` (`backend/debug_auth.py:435`) → 403 `SCOPE`.
Every authenticated call writes a `debug_audit` row (`backend/db/models.py:449`, `audit`
`backend/debug_auth.py:260`; the query masked by `applog.mask_url`; newest 1000 kept); a token's
first use from an IP is flagged (`new_ip`). Tokens are made by `create_token`
(`backend/debug_auth.py:197`) after `check_pin` (`backend/debug_auth.py:121`: SHA-256 both sides +
`hmac.compare_digest`; 5 wrong in a row lock the PIN for 15 minutes, server-wide, in memory; wrong
PINs and locks are logged, never the PIN); scopes `read:activity`, `read:plan`, `read:sync`
(default), `read:gps`, `export:config`; 1 / 7 / 30 / 90 days (default 30); at most 20 active.

**Read-only**: the plan views never call `sessions()` (writer lock, reconcile, week snapshots).
`plan_view` (`backend/api/debug.py:203`) loads the stored rows, applies `plan_store.match_only` in
memory and decorates them with the calendar's own `_decorate` (`plan_match.compare` +
`compliance.with_plan_check(session_compliance)`), so `/debug/day` equals what `/plan/calendar`
shows after its load; at most 120 days per view (`/activity` by label pairs per activity day beyond
that, `_activity_pairs` `backend/api/debug.py:300`); `_would_change` (`backend/api/debug.py:220`) runs
`reconcile_with_adapt` on copies. Only the three debug tables are written; no COROS / TP call.
Every answer goes through `finish` (`backend/api/debug.py:153`): `meta` (git sha, schema version,
caller, dataset generation, cache versions, elapsed ms), `jsonable`, then `scrub`
(`backend/engine/debug_view.py:117`): GPS keys unless `?gps=1` (and coordinates inside strings);
credential / account-identifier keys matched on whole name segments (`secret_key`,
`backend/engine/debug_view.py:99`); strings that look like a debug token, a Fernet blob, a JWT or a
20+ character opaque key blanked, every other string through `applog.redact` (`_clean_text`,
`backend/engine/debug_view.py:104`). Input is strict and bounded (`parse_day`
`backend/api/debug.py:137`: YYYY-MM-DD in 1970–2100; `id` 1–2⁶², `every` 1–600, `lines` 1–2000);
`AuditedRoute` (`backend/api/debug.py:78`) answers a validation error with 400 `BAD_REQUEST` and
any other exception with an audited JSON 500 `{code: INTERNAL, error: <type>}`. With `streams`,
`/activity` returns at most 3 activities and at most 5000 points per stream (`every` raised,
`_streams` `backend/engine/debug_view.py:317`); with only `read:activity` its `plan` part is the
matched session's uid and level, the session row needs `read:plan`.

| Method | Path | Scope | Returns |
|---|---|---|---|
| GET | `/api/v1/debug/activity?date= \| id= \| label=[&streams=&every=&gps=1]` | read:activity (+ read:plan, read:gps) | per activity: basic, tags (auto + override), metrics, the thresholds used with their source, `workout_review` classify + measure, the plan pairing with its why, optional streams (`backend/api/debug.py:321`, `backend/engine/debug_view.py:254`) |
| GET | `/api/v1/debug/plan?from=&to=` | read:plan | stored sessions (with tombstones and push state), the generator's weeks, `would_change`, auto settings / pending / change log, gates and notes, thresholds (`backend/api/debug.py:380`) |
| GET | `/api/v1/debug/day?date=` | read:plan + read:activity | each session with its activity, `vs`, `compliance`, `status`, `why`; unmatched activities with their why (`backend/api/debug.py:459`) |
| GET | `/api/v1/debug/thresholds?date=` (default: the plan's today) | read:plan or read:activity | in effect + history + source of CP / LTHR / AeT / max HR / resting HR; dataset settings and as-of estimates; what the generator used (`backend/api/debug.py:483`, `backend/engine/debug_view.py:389`) |
| GET | `/api/v1/debug/sync?lines=` | read:sync | `sync.*` results, `sync_state` times / cursor (no token column is selected), failures (`error_count`, `error`), the redacted app-log tail (`backend/api/debug.py:515`) |
| GET | `/api/v1/debug/export/config` | export:config | `schema_version`, settings by block — an allow-list of key prefixes, no path-like value, no run results (`exportable`, `backend/engine/debug_view.py:515`) — plan thresholds / weights / profile / events (+ GPX flag), injuries, activity overrides (`backend/api/debug.py:548`, `backend/engine/debug_view.py:524`) |
| GET / PUT | `/api/v1/settings/debug-api` | web session | state (enabled, PIN configured / locked / wrong-PIN counts, tokens, last 100 calls, aggregated failures, refused counts) / `{enabled}` (400 `NO_PIN` without the PIN) (`backend/api/debug.py:588`) |
| POST | `/api/v1/settings/debug-api/tokens` | web session | `{pin, name, scopes, days}` → the token once; 403 `PIN_WRONG`, 429 `PIN_LOCKED`, 400 `NO_PIN` (`backend/api/debug.py:607`) |
| DELETE / POST | `/api/v1/settings/debug-api/tokens/{id}` / `…/tokens/revoke-all` | web session | revoke one / all (`backend/api/debug.py:627`) |

The settings writes need a same-origin request (`same_origin`, `backend/api/debug.py:101`:
`Sec-Fetch-Site`, else `Origin` = Host) and a JSON `Content-Type` (FastAPI ≥ 0.142 strict content
type, `requirements.txt`). Both routers are owner-only (`backend/main.py:152`) and out of the OpenAPI
schema: the demo never mounts them. The settings block is `#debugapi` on 設定 › 進階
(`backend/static/settings.html`, logic `backend/static/debug_api.js`, strings `settings.debug.*`).
Data registry: `debug_tokens` SECRET (`token_hash`), `debug_audit` and `debug_auth_failures` DERIVED
(`backend/data_registry.py:127`).

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
- `backend/tests/test_plan_horizon.py` (SP-327): the horizon with 0 / 3 / 14 / 15 / 20 days left in
  the phase, no next phase, the floor / cap, never moving back day to day; 整個周期 stays the
  current phase; the same uids the day before and after a boundary (also after a race cuts the
  current phase short, and through the real projection).
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
- The week-plan rules added after 2026-10-04: `backend/tests/test_taper_rules.py` (SP-96),
  `test_post_race.py` (SP-98 恢復期 / 回量期), `test_baiyue_multiday.py` (SP-114),
  `test_illness.py` (SP-117), `test_injuries.py`, `test_injury_walkrun.py` (SP-272),
  `test_strength_plan.py` (SP-119), `test_strength_moves.py` (SP-191), `test_balance_plan.py`
  (SP-120), `test_carb_hints.py` (SP-285 – SP-287); the 課表 page's `test_altitude_nights.py`
  (SP-259) and `test_plan_history.py` (SP-71 每週存檔).

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
| 資料等級 (data level) | 0 沒有資料 / 1 累積中 / 2 正常 (= the Zone 3 consistency rule), `data_level.level`; the plan, the race feasibility and the status page read the same one (SP-291) |
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
| horizon | the last day planned: phase end (the next phase's when it ends within 14 days), ≥ 2 weeks, ≤ 8 weeks |
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
deleted / superseded) are returned as reconcile `changes` (`backend/engine/reconcile.py:124`).

## Decisions Log

The owner's calls behind rules above (the ticket holds the discussion).

| Decision | Choice | Alternatives | Rationale |
|---|---|---|---|
| Strength before an A race | No strength in the 14 days before (減量期 + race week) | none 7 days out, ≤ 1 short session 8–14 days out (the research's proposal) | owner 2026-10-05 (SP-86) |
| A phase ending soon | When the current phase ends within 14 days, plan straight into the next one (still ≤ 8 weeks); 「整個周期」 push = the current phase only | shrink to two weeks | owner 2026-10-07 (SP-327) |
| Easy-run planned TSS | Genuinely easy runs only; 越野輕鬆跑 its own rate; < 3 → 推估 IF 0.80 | the all-runs median; the easy cap's IF | owner 2026-10-06 (SP-302) |
| Manual targets | No 110 % LTHR check on typed HR targets, no 40–200 % CP check on typed power | keep the checks | owner 2026-10-04 (SP-33) |
| 平衡／腳踝 | A block at the end of the strength session | its own session | owner 2026-10-05 (SP-120) |
| 疼痛燈號 | Red keeps the projected weeks free of runs too; a run marked 沒痛 after red goes straight back to green (no walk-run) | this week only; always walk-run first | owner 2026-10-06 (SP-271, SP-273) |
| Carb-loading note | Grams when the settings have a body weight | g/kg only | owner 2026-10-06 (SP-285) |
| Load source | The PMC follows the watch's load; RPE only sets planned targets | RPE-corrected PMC | owner 2026-10-04 (SP-62, SP-57) |
| 沒照課表 | Time and TSS both within ±20 % → an intensity reversal or another foot sport is ◐ 部分; a walking session accepts hike / walk / trail run | always ≠ | owner 2026-10-08 (SP-370) |

## Open Questions

Open tickets that touch this module. Not implemented unless the line says otherwise.

- [ ] Show the stored plan's future TSS / CTL on the charts (SP-219, 決策, Todo) — not decided yet
- [ ] Mark which sessions the system changed, which were left alone, which the user edited (SP-318, Todo) — not implemented
- [ ] Week plan generation fails with a manual 專項期 and no A race: `KeyError` at `backend/engine/quality_gate.py:2606` (SP-352, Bug, Todo) — not fixed
- [ ] The 課表 page loads slowly, also after switching the 課表心率區間 in 設定 and while a sync runs (SP-361 Bug Todo, SP-362 In Progress) — not fixed
- [ ] iLevel / Stryd power zones in 設定 as the 課表's default (SP-363, Todo) — not implemented
- [ ] A warm-up time in 課表偏好 added before every session and template (SP-364, Todo) — not implemented
- [ ] RPE load converted per level (TSS definition IF² × 100 / h as the default, fitted per level once there is data), decided 2026-10-07 (SP-57, Todo) — the code still uses one factor (`DEFAULT_FACTOR`, `backend/engine/rpe_load.py:67`)
- [ ] A 強度課's 「自動」 target counted as % CP in the template filter, decided 2026-10-07 (SP-84, Todo) — `target_types` still leaves it "auto" without a basis (`backend/engine/workout_steps.py:1341`)
- [ ] Remove 「課前要吃」 entirely, decided 2026-10-07 (SP-286, Todo) — still in the code (`backend/api/plan_sessions.py:398`)
- [ ] 專項期前後段: a long trail race's late half keeps one Zone 5 session every 3 weeks, and the other four answers of 2026-10-07 (SP-353, Todo) — not implemented

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
| 2026-10-04 | feature | SP-74 | 技術地形課 in week_plan and the projection for 越野跑 athletes (`engine/technical.py`): 基礎期 every other week's LSD → 技術地形 RPE 3–4 of the same time; 專項期 one a week from an easy run, RPE 6–7 (quality: 48 h spacing, ≤ 20 % budget with the intervals, work 30–90′) or RPE 4–5 when there is no room / spaced day; week notes; generated `steps`; road athletes none |
| 2026-10-04 | feature | SP-73 | 轉換期 after each A race's recovery (`planning.auto_phases`; 課表偏好 `transition_weeks` 0–4, default 3): shortened / skipped before the next A race's 專項期 with a phase note, manual phases win; volume 50 % of the 4 weeks before the taper (week_plan and projection agree), easy runs ≤ 60 min (Canova), no CP-test suggestion |
| 2026-10-04 | feature | SP-31 follow-ups | 專項期 applies this week's CTL-ramp / volume-step guardrails to both tracks; 2 a week with only Zone 3 open = rung + a different 巡航版; the weekday-cap 巡航版 counts as the Zone 3 rung |
| 2026-10-04 | sp-36-template-manager | SP-36 | 範本 page (third tab of 課表): the user's own templates (`workout_templates_user`, `engine/user_templates.py`) with several categories (built-in + custom, add / rename / delete), 目標用, relative targets resolved when used, CRUD + 複製成我的範本 + 儲存成範本 (`/sessions/{uid}/save-as-template`); 「我的範本」 in 插入範本 by category / family / trail kind, custom tabs; a training-route GPX per template (race calculator's parser), its elevation behind the step chart on the time axis by estimated speed (`elev`, `tpl` in the steps); demo sandbox writes, static demo read-only; zh-TW + en |
| 2026-10-04 | sp-38-load-step | SP-38 | Step end conditions from the provider's capabilities (`end_conditions` / `end_labels` / `load_unit`); new 「負荷」 end condition (TSS, main-set only; COROS targetType 6 with the converted TL, else estimated time); 「按圈」 → 「直到按下計圈」 on the race-calculator export and template notes too |
| 2026-10-04 | sp-36-gpx-followup | SP-36 | Follow-ups: a GPX template's / session's step chart on the route's distance axis (`elev.x` per step, distance steps by their km, others by estimated speed); sessions keep their own compact copy of the route profile (`route` in the steps) that outlives the template or its GPX; 我的範本 ranked into the 插入範本 推薦 block by the same rules (trail phase rules by kind), tagged 我的 |
| 2026-10-04 | feature | SP-68 | One start for the PMC and the guardrails: manual CTL / ATL at a date → first-28-day mean (CTL and ATL) → 0, in the evaluator builtins; week_plan's TSB < −30 / −20 read it (SP-63 Q3); `pmc()` returns `start`; 起始 CTL／ATL card and `/plan/pmc-start` |
| 2026-10-04 | feature | SP-38 follow-up | 「負荷」 main sets count on the ladder (`load_work_s`) |
| 2026-10-04 | feature | SP-38 follow-up | A TL refit re-pushes a load step only when its TL moves by ≥ 3 (`TL_RESEND_MIN`); smaller moves keep the TL last sent |
| 2026-10-04 | feature | SP-63 follow-up | `i_volume` / `guard`: no volume-step verdict the week after a 3–5-day break without a run (`step_exempt` / `step_note`); `week_plan` adds an info note (`src: "volume"`) |
| 2026-10-04 | feature | SP-39 follow-up | 「安排課表」 for the 90-min test: `POST /sessions` replaces that day's long run like 排入測試 |
| 2026-10-04 | change | SP-63 follow-up | `i_volume`: the short-break exemption counts unplanned days only (the user's 不排課日期 / 休息日 don't count; `Status(blackouts=)`) |
| 2026-10-04 | change | SP-73 follow-up | Zone 3 gate / re-entry block: 轉換期 days are not a running break (no block, no 7-day gap, no 21-day re-lock; plan-auto.spec.md) |
| 2026-10-04 | change | SP-39 follow-up | Zone 5 UA path: no LTHR age limit; `threshold_confidence.lthr_evidence` (evidence since the LTHR date) feeds `quality_gate.lthr_invalid` (plan-auto.spec.md) |
| 2026-10-04 | sp-79-quality-families | SP-79 | 強度課's three families in the main UI: `plan_sessions.family` (picked in the 課表 editor's 類型 — 有氧間歇／VO2max 間歇／速度 — else read from the steps), a mismatch hint, per-family chip colours and legend, 課表統計 by family; generated titles name the family and older stored titles are mapped on read (`display_title` → `interval_library.renamed`), so the watch names and the calendar feed SUMMARY follow (a pushed session with an old name is re-pushed once) |
| 2026-10-05 | sp-84-template-filter | SP-84 | 範本 page and 插入範本 filter by 主課強度 (the main set's target type: `workout_steps.target_types`, `target_types` on every template row and the type list in both template APIs), after the category; kept per viewer in localStorage |
| 2026-10-05 | sp-57-rpe-load | SP-57 | 「負荷」 by RPE: five levels (CR-10 2/4/5/7/10) + minutes → TSS by Foster session RPE × a per-athlete factor (`engine/rpe_load.py`, shrunk to 0.30), step timed by its minutes; planning targets only, the PMC stays on the watch's load |
| 2026-10-05 | change | SP-79 follow-up | Taper short session renamed 「短強度 4×3 分」 → 「有氧間歇（巡航）4×3 分」 (98–102 % CP unchanged); `interval_library.renamed` maps the stored old title (no reconcile change; the 「N×M 分」 parsers read both) |
| 2026-10-05 | change | SP-63 follow-up | `i_volume`: weekdays not ticked as 可練日 (課表偏好) count as planned rest for the short-break exemption, like 不排課日期 / 休息日 |
| 2026-10-05 | change | SP-73 follow-up | Zone 3 gate / re-entry block: the A race's 恢復期 days are not a running break either, like the 轉換期 (`planning.post_race_days`; plan-auto.spec.md) |
| 2026-10-05 | change | SP-73 follow-up | Volume step and the planner's +10 % cap (`week_plan`, projection `week_hours(cap_ref=)`) read normal weeks only: weeks touching a 減量期 / race / post-race 恢復期 / 轉換期 are skipped for the most recent normal ones (`load_guard.normal_weeks`, `target.ref_weeks`); owner 2026-10-05 |
| 2026-10-05 | feature | SP-86 | No strength in the 14 days before an A event (減量期 + race week; Bompa & Buzzichelli p.184 / p.327): `strength_stops` / `drop_strength_before_a` in week_plan and the projection (`strength_stop`), preferences included, a week note; B / C events unchanged |
| 2026-10-05 | feature | SP-74 follow-up | The user's own 技術地形 session (custom or edited, RPE ≥ 7 by `session_role`) counts in the week's 20 % like the generated one: `plan_store.user_rpe_rows` → `technical.user_quality`, `quality_sessions(reserved=)` shortens / leaves out the intervals (note), no generated 技術地形 that 專項期 week, a week note; week_plan and projection alike |
| 2026-10-05 | feature | SP-75 | 專項期 sessions climb the ladders (trail uphill versions; no fixed 2×15′ / 5×4′), 前段 / 後段 ratios, the road MP segment grows every other week (`MP_PLAN`, `mp_race` / `mp_week`) |
| 2026-10-06 | change | SP-245 | 主要訓練項目 自動: A races first — only trail (越野賽 / 百岳) → trail, only road → road, both → the harder race (`event_size` tier → predicted hours → EP; tie / unknown size → trail; `primary_sport._harder`); with no future A race the B races the same way; else the 12-week share; C / past / 其他 races never count; the reason names the race (and the race it was compared with); only the 自動 setting is affected |
| 2026-10-06 | feat/cold-start-sp288-290 | docs/research/cold-start.md | SP-288 冷啟動排課: the first week without history = the questionnaire as entered or 1.5 h / 3 easy runs / no long run (no more 0.5 h next to a 60-min long run); 4-week ramp with the start level as the base floor, +10 % / +0.5 h steps, ≥ 3 runs a day apart, no intervals; long run ≤ 40 % of a week < 120 min; projection consistent; runners with history unchanged |
| 2026-10-06 | feat/cold-start-sp288-290 | docs/research/cold-start.md | SP-289 心率先驗: max HR falls back to 208 − 0.7 × age, LTHR to 0.90 × max HR (only without a test / estimate / watch LTHR; before the first real value), both 推估 and low confidence; easy runs get HR numbers + the talk test; PMC `marks` on the day a real LTHR replaces it; LTHR test suggested from week 5 for new runners (week 2 with ≥ 3 h + a race); unsourced-rules §0.5.5 rewritten |
| 2026-10-06 | change | SP-302 | The easy run's planned TSS / h (`easy`, `easy_trail`) from genuinely easy runs only (classifier 輕鬆跑 not 中強度跑, or average HR ≤ AeT + 3; no Zone 3 / Zone 5 / test); < 3 → 推估 from the easy cap's IF (cap ÷ LTHR)² × 100 with a note; long run / quality rates unchanged; `easy_tss` in the output; `plan_prefs._easy` and `plan_sessions.tss_rates` read it |
| 2026-10-06 | change | SP-302 decision | < 3 genuinely easy runs → 推估 IF 0.80 (64 TSS / h) instead of the easy cap's IF; the projection's default path (no 課表偏好) prices easy runs with the same `easy` rate as week_plan |
| 2026-10-07 | feat/sp291-293-data-level | docs/research/cold-start.md §4.1 | SP-291 資料等級: `data_level.level` (0 no run / hike in 28 days, 2 = the Zone 3 consistency rule on run + hike days, 1 between; week of the data; `survey`) read by the week plan (cold_start), the status page (`i_level` card, `data_level` in `/status`) and the race feasibility; the ramp lasts the rule's weeks; the cold / ramp note is the level's one line (「你的資料還在累積（第 n 週／4）：週量依你填的問卷，心率區間是推估」); plan / status cache keys carry `experience.stamp()` |
| 2026-10-08 | fix/sp370-compliance-looser | SP-370 follow-up (owner 2026-10-08) | The ◐ softening needs time **and** TSS both measured (`compliance.time_tss_level` None when either ratio is missing, `backend/engine/compliance.py:67-76`; used by `session_compliance` `:107-112` and `plan_match.compare` `backend/engine/plan_match.py:334`); the workout review 「課表」 card grades on the calendar's planned-TSS estimate (`api/plan_sessions.session_est_tss` `backend/api/plan_sessions.py:2510`, `plan_store.rate_rows` `backend/engine/plan_store.py:884`, `workout_review._plan_card` `backend/engine/workout_review.py:3036-3042`) |
| 2026-10-08 | fix/sp370-compliance-looser | SP-370 (owner decision 2026-10-08) | 沒照課表 less strict: a walking session (`target_policy.is_walk`) accepts hike / walk / trail run (`compliance.accepted` / `sport_ok`, `backend/engine/compliance.py:44-58`; `plan_match.sport_ok` / `can_match` follow, `backend/engine/plan_match.py:75-90`); with time and TSS both within ±20 % an intensity reversal or another foot sport is ◐ 部分 with its reason, not ≠ (`compliance.session_compliance` `backend/engine/compliance.py:93-120`, `with_plan_check` / `soft_label` `:122-154`, `status_of` `:195`; `plan_match.compare(s, planned_tss)` `backend/engine/plan_match.py:318-370`; `api/plan_sessions._decorate` passes the page's TSS estimate, `backend/api/plan_sessions.py:2668`); a non-foot sport stays ≠; four-level texts in `static/i18n/*/schedule.json` `vs.levels_tip`, `compliance.json` `kpi.ok_tip`, `overview.json` `week.short_aria`; 推估 |
| 2026-10-07 | feat/sp291-293-data-level | docs/research/cold-start.md §4.4 | SP-292 賽事可行性 for 資料等級 0 / 1 (`GET /overview/feasibility`, `race_feasibility` module doc): base hours = max(questionnaire as the plan reads it, actual), km / climb actual only; no actual distance → UA weekly / climb 「還不知道」, Koop's hours still judged; `data_source` tag 「依你填的資料」／「資料還少」 on the card; level 0 at most tight (cutoff / 跨級 over → tight with the reason, no 「先不跑」／「低一級」 advice; 「late」 unchanged); ≥ 42.195 km with a self-reported week < 3 h → `optimistic_note` (Vickers & Vertosick 2016); level 2 unchanged |
| 2026-10-08 | fix/sp358-359-schedule-delete-swap | SP-358 | 刪除／移動課表沒同步到手錶: a drag / edit / delete / 不排課日期 / 休息日 only wrote the store, the watch changed only at the next manual push or after a sync with a new activity, and a range push never touched a pushed session that had moved out of the range — so the old day kept its workout. Now these endpoints sync the affected sessions at once (`_sync_watch` → `plan_auto.push_window(only=…)`, response `coros`, page note / warning, a `failed` change-log row on error); range pushes and the automatic window also re-send copies whose session moved out (`copies_in`); a COROS calendar delete is verified (`_remove_remote`); removal failures count as push failures |
| 2026-10-08 | fix/sp358-359-schedule-delete-swap | SP-359 | 交換課表: context menu 交換… then click the other session (Esc cancels), or drop a session onto another; `POST /sessions/swap` → `plan_store.swap` trades the two days in one commit as the user's own moves (like a drag; reconcile never undoes them), done / past / blocked / notice refused, B2B follows, hard-day spacing warnings, the watch synced on both days (SP-358); legend 「⇄ 交換」 with a ? tip; zh-TW + en |
| 2026-10-08 | fix/sp358-359-schedule-delete-swap | SP-358 review | An edit syncs only the sessions it touched (the stale / blocked / missed clean-up in `push_window(only=…)` limited to them; other copies wait for the run / manual push and never show as 失敗). A COROS calendar delete still listed after a 1.5 s pause is a reminder (`check_day` / `check_days`, 「…可能還在，請到 COROS App 確認」, app log), not a failure, no retry. Fixed the 課表 chip class glued as `st-donecp-green` (done chips lost the ✓ and compliance tint) |
| 2026-10-08 | perf/sp362-batch1 | SP-362 | 課表頁載入效能第一批: single flight for `_status` / `_compute_inputs` (one computation per key, exceptions to every waiter, not cached; `backend/singleflight.py`); warm-up order Dataset → Status → plan inputs → (after plan_auto) calibration / auto-classification, plan inputs warmed at start-up too; the FIT-folder stamp kept 5 s while the folders keep their mtimes (`files_changed` on import / purge); one `/suggestions` GET per 課表 page load (shared by the floating box and 排入測試 ▸); `GET /plan/calendar` logs its phases (inputs, lock_wait, reconcile, view, extras, suggestions) to the app log (`applog.phases`) |
| 2026-10-08 | perf/sp362-batch2-plan | SP-362 B3 / B4 | 課表頁載入效能第二批（課表側）. B3: every COROS push runs under a new push lock (`_plock`, `backend/api/plan_sessions.py:449`) instead of the writer lock and re-reads the stored plan first — `plan_auto.run` (inputs before the lock, `_push_after`, `backend/engine/plan_auto.py:682`, `:802`), `_sync_watch` (`:2150`), manual push / unpush (`:2186`, `:2219`), `_unpush_expired` (`:1950`); GET `/sessions` / calendar and edits no longer wait for COROS; an edit made during a push wins and its own watch sync re-sends it. B4: `GET /plan/calendar` answers the previous inputs marked `stale` {reason sync / day, age_s, since} after new data or a new day (same user settings), recomputes in the background through the single flight (`_stale_view` / `_refresh`, `backend/api/plan_sessions.py:94`, `:116`), display only (no reconcile / match / snapshot / write, `_sessions_body`, `:556`; previous Status via `status_peek`, `backend/api/overview.py:90`); `GET /plan/fresh` `{updating}` (`:591`); the 課表 page's 「同步後更新中」 / 「換日更新中」 badge, editing disabled while stale, poll + reload (`backend/static/schedule.html:977`, `:3039`); inputs key reordered (data parts before settings, `_inputs_key`, `:141`) |
| 2026-10-08 | perf/sp362-batch2-plan | SP-362 batch-2 review | `_build_inputs` records `inputs_key` (`backend/api/plan_sessions.py:258`) and `inputs_key_now` (`:149`) lets `plan_auto.run` re-check it inside `_wlock` (H1); `_last` is an LRU of 64 tenants, demo tenants never kept / served stale, refreshes in a 2-worker pool (`:61`, `:63`, `:91`, `:106`; M3); the 課表 page drops the edit lock and offers 「重新整理」 after 5 min of polling, and also refuses 刪除所有過期未完成 / 移除推送 / the suggestion box's 排入 while stale (`backend/static/schedule.html:979`, `:1006`, `:3063`; L5); anchors of the B3 / B4 bullets re-pointed |
| 2026-10-08 | feat/sp371-debug-api | SP-371, docs/debug-api.md | Debug API for AI agents: read-only `/api/v1/debug/{activity,plan,day,thresholds,sync,export/config}` (`backend/api/debug.py:321`–`:548`), Bearer-token auth with the server PIN `TRC_DEBUG_PIN`, hashed tokens, scopes, expiry, revoke, tenant binding, per-token / per-IP limits and the `debug_audit` log (`backend/debug_auth.py:384`); `/debug/day` = the calendar's `_decorate` on an in-memory `match_only` (no `sessions()`, no writes, no COROS call); 設定 › 進階 Debug API block (`backend/static/debug_api.js`); tables `debug_tokens` / `debug_audit` (`backend/db/models.py:429`), setting `debug.api.enabled` |
| 2026-10-08 | feat/sp371-debug-api | SP-371 security review | Debug API hardening: token also in `X-TRC-Debug-Token` next to the password proxy's `Authorization` (`backend/debug_auth.py:351`); token checked before the IP block, which only refuses failed attempts; failures aggregated in `debug_auth_failures` (`backend/db/models.py:468`), 429 / 503 never stored; per-tenant bucket and a 2-call gate; `scrub` on name segments + JWT / opaque / `applog.redact` / coordinates in strings, export an allow-list (`backend/engine/debug_view.py:99`–`:524`); `read:gps` scope, `read:plan` for the activity's session row; strict bounded input, `AuditedRoute` 400 / audited 500 (`backend/api/debug.py:78`); same-origin settings writes; PIN failures logged and counted; routes out of OpenAPI; CLI header / https / no redirect / env-only token |
| 2026-10-08 | code-sync（SP-90, SP-95, SP-96, SP-98, SP-109, SP-114, SP-115, SP-117, SP-119, SP-120, SP-191, SP-216, SP-263, SP-270, SP-271, SP-272, SP-273, SP-280, SP-285, SP-71, SP-100, SP-105, SP-122, SP-231, SP-258, SP-259, SP-286） | N/A | Re-anchored the whole spec: each anchor moved once from the commit that wrote its line (~410 of 510), then the ones written stale or still off checked by hand against the symbol, the route decorator or the code text (≈ 120 fixed, incl. the whole API table). New: the week-plan rules added after 2026-10-04 (taper by race, two A races, 中間訓練 / B races, B-race notes, 恢復期 / 回量期, ultra 轉換期, multi-day 百岳, the walking cap, illness, strength by phase / moves, 平衡／腳踝, injuries, carb note), Categories = the platform-neutral app type, compliance intensity grading, the 課表 page's fuel / self-rating / altitude lines, the 7 / 42 / 90-day PMC, 每週存檔 and its API row, the feasibility API row, the feature test files; Decisions Log (9) and Open Questions (11) |
| 2026-10-08 | SP-258 follow-up | owner decision 2026-10-07 (ticket SP-258) | 安排適應週末 (15–28 days): no place named (松雪樓 gone, no other names), 行前一晚 only in the ? help, the weekend of a Sunday departure counts, ✕ closes it for that trip for good (`altitude_plan:<event>:<start>` id held by `prune` until the start); the 1–14 day check unchanged; zh-TW + en. Tests `test_altitude.py::test_sp258_*` |
