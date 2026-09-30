# Module Spec: overview

> **Last Updated**: 2026-09-30
> **Status**: Active
> **Domain Layer**: Core Domain

## Overview

The home page (總覽). It answers four questions without splitting by sport: *how am I
doing* (the training-status indicators), *what's missing* (their prioritised
actions), *what did I do* (totals by week / month / year) and *what should I do
this week* (a day-by-day plan with a load projection). The athlete's trail, 百岳
and bike days are too few to read on their own, so every total, the PMC and the
plan use all sports together; categories exist only to colour stacked bars.

The plan is **stored and editable**: the generator writes sessions into a table once, the
athlete edits, adds, moves or deletes them, and a reconcile step refreshes the rest from what
actually happened. The stored plan (not the generator's output) drives the week's progress
bars and the PMC projection, and can be pushed to COROS Training Hub by day, week or phase.

Volume is **moving time**, never recorded time — a multi-day 百岳 file records
the nights too (one 51 h trip held about 7 h of walking).

## Architecture

```
 Dataset (wko5-engine) ──┬─ Status engine (status.py) ── indicators, actions, phase, goals
                         ├─ overview.summary()  ── period buckets + current-period detail
                         ├─ overview.pmc()      ── CTL / ATL / TSB per day
                         └─ overview.week_plan() ─ target, sessions by day, projection
                                      │
                         projection.project_weeks() ── later weeks up to the horizon
                                      │
                         reconcile.reconcile() ⇄ plan_store (table plan_sessions)
                                      │                     │
                                      │          coros_workouts ⇄ table coros_plan_push ⇄ COROS
                                      │
                /api/v1/overview/* + /api/v1/overview/plan/* ── backend/static/overview.html
```

| Layer | Responsibility | Entry point |
|---|---|---|
| Categories / helpers | Workout → category, moving time, effort km | `backend/engine/overview.py:68` |
| Periods | Week (Monday) / month / year buckets and totals | `backend/engine/overview.py:207` |
| PMC | Same `tl()` recurrence as the chart expressions `ctl` / `atl` / `tsb` | `backend/engine/overview.py:269` |
| Week plan | Volume target, session template, done-matching, day placement, projection | `backend/engine/overview.py:405` |
| Multi-week projection | Rolls the week-plan rules forward to the horizon | `backend/engine/projection.py:213` |
| Reconcile | Pure rules: stored plan vs regenerated weeks vs activities | `backend/engine/reconcile.py:74` |
| Plan store | Table I/O, edits, tombstones, stored-plan summary | `backend/engine/plan_store.py:89` |
| COROS push | Session → structured COROS workout, idempotent push / remove | `backend/sync/coros_workouts.py:580` |
| API | Memoised Status, the endpoints, the page | `backend/api/overview.py:40`, `backend/api/plan_sessions.py:32` |

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
(`backend/engine/plan_store.py:203`): today's CTL / ATL continued with the TSS of the **stored**
active sessions on each day after today, up to the horizon, so edits change the curve. It
replaces `week_plan()`'s own projection once the stored plan has loaded
(`backend/static/overview.html:376`).

## Week plan (`week_plan`)

Inputs: the computed `Status` (phase kind, goals, indicators), the last 8 complete weeks of
moving hours / TSS, today's CTL / ATL / TSB (`backend/engine/overview.py:405`).

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

**Sessions** (dataclass `Session`, `backend/engine/overview.py:314`)
- Base / specific (not a recovery week): one long easy session (30 % of the week, ≥ 60 min,
  ≤ 1.15 × the longest of the last 28 days; specific: toward 70 % of the goal event's hours,
  ≥ 90 min), terrain from the goal's climb density; then one of, in this order
  (`backend/engine/overview.py:532`):
  1. **CP test 3'/12'** when the `testing` indicator is bad / watch and the A event is > 10
     days away (independent of the quality gate);
  2. specific → uphill intervals 5×4';
  3. base, when the last quality session (run or hike, date-sorted) of the past 28 days is
     missing or faded → **閾值下 N×8'** at 88–95 % CP, 3×8 the first time, one rep fewer (not
     below 2) after a faded one (`backend/engine/overview.py:541`,
     `backend/engine/workout_review.py:533`, `backend/engine/workout_review.py:820`);
  4. a good `intensity` indicator → threshold 3×10' (`backend/engine/overview.py:554`).
- **Quality gate** (`workout_review.quality_gate`, `backend/engine/workout_review.py:524`,
  called at `backend/engine/overview.py:512`): no quality session when `intensity` or `drift`
  is bad; in base phase (or no phase) the drift streak — ≥ 3 consecutive fair easy road runs
  with drift < 5 % (`STREAK_NEED`, `backend/engine/workout_review.py:66`) — must also be
  there. The streak comes from the `drift` indicator's `extra.streak_ok`
  (`backend/engine/overview.py:510`). The gate's inputs are returned as `quality_gate`
  (`levels`, `streak_ok`, `allowed`) for the projection (`backend/engine/overview.py:701`).
- Taper: one short intensity 4×3'. Event week: the race.
- Strength ×2 in base / transition / recovery or when the `strength` indicator is bad / watch,
  else ×1 (not counted in the hours).
- Easy runs fill the remaining minutes in 40–60 min sessions; in base the first one carries
  8×10 s hill strides.
- Targets per session come from `zones.training_targets` (CP / LTHR / AeT, estimate-aware),
  formatted by `_targets` (`backend/engine/overview.py:371`).

**Done-matching**: strength ← a strength workout; long ← an endurance session ≥ 80 % of the
planned minutes; quality / test ← a session with ≥ 10 min at ≥ LTHR or ≥ 0.95 CP run power
(`backend/engine/overview.py:390`); easy ← any other endurance session.

**Placement**: remaining days from today (tomorrow when something is already logged today)
to Sunday. The long session goes on the athlete's usual long-day weekday (mode over 12 weeks,
`backend/engine/overview.py:346`) or the last free day; quality ≥ 2 days from the long one;
easy on the next free days; strength on easy or free days, never the day before the long one.
Sessions that don't fit are reported as a note, not squeezed in.

**Output**: target / done / remaining (hours, TSS), the reasons (`why`), the rules cited,
8-week history, load now and at Sunday (CTL, ATL, next-Monday TSB, weekly ramp), the daily
projection, sessions with day / done state, thresholds and their sources, and notes (data /
testing to-dos from the indicators) (`backend/engine/overview.py:679`).

## Multi-week projection (`projection.py`)

`project_weeks(cur, phases, until, ctlconstant, atlconstant)` (`backend/engine/projection.py:213`) starts
from this week's `week_plan()` output and rolls the same rules forward week by week, never
more than `MAX_WEEKS` = 8 ahead (`backend/engine/projection.py:30`):

- Hours per week (`week_hours`, `backend/engine/projection.py:71`): base / specific use the CTL
  ramp goal capped at +10 % (≥ +0.5 h) of max(4-week mean, last week), with a 65 % recovery
  week after 3 build weeks; taper 40–50 % of the 6-week mean; event 30 %; recovery 50 %;
  transition 65 % of the 4-week mean.
- Sessions (`week_sessions`, `backend/engine/projection.py:98`): the same template (long, one
  quality, strength, easy fill) placed by `_place` (`backend/engine/projection.py:153`).
  Projected base weeks repeat this week's 閾值下 session when there is one.
- Whether a projected week gets a quality session is decided per week, for that week's phase
  (`allow_quality`, `backend/engine/projection.py:198`, called at
  `backend/engine/projection.py:247`): week_plan's gate (`quality_gate` with this week's
  indicator levels and drift streak), then in base either the carried 閾值下 session or a good
  `intensity`. A CP-test week or the base drift gate no longer carries into later weeks; a
  `cur` without `quality_gate` falls back to reading this week's sessions
  (`backend/engine/projection.py:184`).
- CTL / ATL roll forward with the athlete's constants (`ds.athlete.ctlconstant` /
  `atlconstant`, `backend/engine/projection.py:255`); a session `_place` left without a day is
  kept out of the date filter.
- Each projected week carries `mode`, hours, TSS, CTL start / end, `why`, and `provisional`
  (true beyond next week).

The horizon is the current phase end, at least two weeks out, capped at `MAX_WEEKS`
(`backend/api/plan_sessions.py:55`).

## Stored plan (`plan_store.py`, `reconcile.py`)

**Table** `plan_sessions` (`backend/db/models.py:145`): one row per planned session — `uid`,
`week_start`, `gen_key` (the generator's id: long / quality / easy1 …; none for custom), `day`,
`kind`, `title`, `minutes`, `target`, `detail`, `source`, `tss`, `origin` (auto / custom),
`edited`, `provisional`, `state` (active / done / missed / deleted / superseded), `done_by`
(JSON activity row), `note`.

**Kinds** (`backend/engine/plan_store.py:19`): easy 輕鬆跑, long 長時間, quality 強度課, test 測試,
hike 健行／登山, strength 肌力.

**Reconcile rules** (`reconcile()`, `backend/engine/reconcile.py:74`; documented at
`backend/engine/reconcile.py:12`):
1. Active (and previously missed) sessions up to today that match an activity — the
   generator's own done-match for auto sessions, else same day + same kind of activity — become
   **done**; the rest on past days become **missed**, but only up to the day the synced data
   covers (`covered`), so a late sync can turn a missed session back into done
   (`backend/engine/reconcile.py:107`).
2. Per generated week, unedited auto sessions from today on are replaced by the regenerated
   ones: same `gen_key` → changed, gone → removed, new → added.
3. Edited and custom sessions are kept. An edited long / quality / test is **superseded** when
   the regenerated week is a rest week (recovery / taper / event / transition) that no longer
   has it (`backend/engine/reconcile.py:137`). Deleted auto sessions stay deleted: their
   tombstone blocks the `gen_key` for that week.
4. An auto session on the same day as a kept edited / custom session moves to a free day of
   that week, or is dropped (`backend/engine/reconcile.py:167`).
5. Unedited auto sessions past the horizon are removed.

Each change is returned as `{action, uid, day, title, kind, minutes, origin, edited, reason?,
before?}` and grouped by day for the preview (`backend/engine/reconcile.py:200`).

**Coverage** (`_covered`, `backend/api/plan_sessions.py:77`): the later of the latest activity
day and the day before the latest successful COROS / generic sync.

**Automatic reconcile** (`_ensure`, `backend/api/plan_sessions.py:114`): on the first visit of
a week, or while an earlier week still has active sessions, the plan is reconciled and saved
before anything else is returned.

**Edits** (`backend/engine/plan_store.py:114`, `backend/engine/plan_store.py:145`): editable
fields are day, kind, title, minutes, target, detail. A day must be ISO and not in the past;
kind must be known; minutes 0–1440; title not blank. An edit marks the session `edited` and
non-provisional. Moving an auto session to another week leaves a tombstone in the old week and
turns the session into a custom one. Only active sessions can be edited.

**Add** (`backend/engine/plan_store.py:170`): a custom session needs a day; defaults kind easy,
45 min, a title per kind. **Delete** (`backend/engine/plan_store.py:187`): an auto session
becomes a tombstone (`state = deleted`), a custom one is removed.

**Stored-plan summary** (`plan_summary`, `backend/engine/plan_store.py:203`): the week's
target hours (active + done sessions, strength excluded) and TSS, plus the CTL / ATL
projection described under PMC, ending CTL / ATL and next-Monday TSB.

**Concurrency**: plan writes are serialized by one asyncio lock per event loop
(`backend/api/plan_sessions.py:102`), so two tabs or a preview racing a push cannot generate
the same week twice.

## COROS push (`coros_workouts.py`)

Pushes stored sessions to COROS Training Hub as structured, scheduled workouts through the
unofficial Training Hub API (same host and token as the COROS sync client; endpoints listed at
`backend/sync/coros_workouts.py:6`).

- **Scope** (`_range`, `backend/api/plan_sessions.py:122`): `day` = that day; `week` = the
  Monday–Sunday week of `day`, from today on; `phase` = today to the phase end, capped at
  `MAX_WEEKS`. `day` defaults to today; the plan's "today" is never earlier than the real date
  (`backend/api/plan_sessions.py:110`). A `week` entirely before today is a 400 for preview,
  push and unpush instead of an empty range (`backend/api/plan_sessions.py:136`); a past `day`
  scope is not guarded.
- **Every push reconciles first** and applies the result, then pushes the active sessions in
  range (`backend/api/plan_sessions.py:262`).
- **Session → steps** (`session_steps`, `backend/sync/coros_workouts.py:180`): long / hike /
  easy are one time step at HR ≤ AeT; an easy session whose title has `N×S 秒` gets a strides
  repeat when ≥ 10 min remain; quality and test sessions get their own step builders. Strength,
  race and rest are not pushed (skipped, with a reason). Done, unplaced and past-day sessions
  are not pushed (`backend/sync/coros_workouts.py:293`).
- **Program** (`build_program`, `backend/sync/coros_workouts.py:239`): run sport; HR targets as
  absolute bpm with the LTHR zone scheme; names `TRC <title> <m>/<d>`, ≤ 30 chars
  (`backend/sync/coros_workouts.py:288`).
- **Idempotency** (`_push_one`, `backend/sync/coros_workouts.py:521`): each push is recorded in
  `coros_plan_push` (`backend/db/models.py:123`) with the COROS program / plan / schedule ids
  and a SHA-256 fingerprint of day + payload. Same fingerprint → left alone; changed → the old
  COROS entry is removed and a new one created; an entry already executed on the watch is kept
  as done. The stored-plan push keys rows by session `uid` (`session_key`,
  `backend/db/models.py:130`).
- **Clean-up** (`push_sessions`, `backend/sync/coros_workouts.py:580`): pushed sessions that
  left the plan (deleted / superseded / regenerated away) are removed unless on a past day;
  missed sessions are removed from the calendar. Only entries recorded in `coros_plan_push` are
  ever deleted (`_remove_row`, `backend/sync/coros_workouts.py:619`).
- **Unpush** (`DELETE /push-coros`, `backend/api/plan_sessions.py:284`) removes every recorded
  entry whose day falls in the range (`remove_keys`, `backend/sync/coros_workouts.py:604`).
- **Status per session** (`status_of`, `backend/sync/coros_workouts.py:478`): done / skipped /
  not_pushed / pushed / outdated / failed; sessions no longer active but still recorded show
  `pushed_<state>` (`backend/api/plan_sessions.py:144`).
- The old week-keyed helpers (`push_week` / `remove_week` / `week_status`, keys
  `<week start>/<session id>`) were only used by tests and are gone; the tests drive
  `push_sessions` / `remove_keys` / `status_of` directly.
- Pushes and removals are serialized by a module-level lock
  (`backend/sync/coros_workouts.py:74`). An expired COROS login returns 401
  `COROS_AUTH_REQUIRED` with a hint to log in again on the settings page
  (`backend/api/plan_sessions.py:258`).

## Page (`backend/static/overview.html`)

- Title 訓練總覽, heading and nav entry 總覽 (`backend/static/overview.html:6`,
  `backend/static/overview.html:186`).
- **Glossary hovers**: the terms `AeT` and `CP 測試` inside engine text (actions, indicator
  verdict / why / action, week reasons, notes, the thresholds line) get a hover / tap
  explanation of what they are and how to test them (`backend/static/overview.html:286`).
- **Sources removed from the plan**: the to-do list no longer prints sources, and the 依據 block
  of the week plan is gone — `w.rules` still carries them, noted in a code comment
  (`backend/static/overview.html:404`). Indicator cards keep a collapsible 來源
  (`backend/static/overview.html:355`).
- The day list, the week's progress-bar targets, the Sunday CTL, next-Monday TSB and the PMC
  projection come from the stored plan once `GET /plan/sessions` returns
  (`backend/static/overview.html:376`, `backend/static/overview.html:637`).
- Plan editing: session dialog (add / edit), delete, drag-to-move, reconcile preview with changes
  by day (`backend/static/overview.html:645`, `backend/static/overview.html:722`).
- COROS push with a scope selector (day / week / whole phase), a preview confirm, a per-session
  COROS badge, and unpush for the selected range (`backend/static/overview.html:737`).

## Status engine change

- `Status.weekly_hours()` sums moving time (fallback recorded time) instead of recorded
  time (`backend/engine/status.py:168`), so the volume indicators aren't inflated by multi-day
  trips.
- **`i_drift`** (`backend/engine/status.py:390`) uses the same per-run drift as the single-activity
  review (`workout_review.drift_streak`, `backend/engine/workout_review.py:814`): road runs,
  ≥ 40 min, avg HR ≤ AeT+3, hilly / stopped / unsteady runs refused. It reports the streak of
  consecutive runs < 5 % in `extra` (`streak`, `streak_ok`, `runs`, `fair`). In base phase (or
  no phase) a good median without the streak is only **watch**, with a "還差 N 次" action; with
  fewer than 2 fair runs it is watch instead of n/a (`backend/engine/status.py:403`,
  `backend/engine/status.py:414`). `streak_ok` is what gates the base-phase quality session.
- **`i_testing`** (`backend/engine/status.py:538`) also reads the latest 3'/12' CP test found in
  the data (`workout_review.latest_cp_test`, 120 days, `backend/engine/workout_review.py:848`).
  When its CP differs from the CP in effect by more than `CP_DELTA` = 3 %
  (`backend/engine/workout_review.py:77`) and no CP threshold dated on or after the test exists,
  the indicator becomes at least watch with text 要更新 and an action to apply the new CP
  (`backend/engine/status.py:574`). The test is always appended to `why` and returned in
  `extra.cp_test`.
- Inline source names were removed from engine text (e.g. the ramp verdict, phase focus).

## API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/v1/overview/status` | `Status.to_dict()`: today, phase, goals, headline, indicators, actions, counts (`backend/api/overview.py:62`) |
| GET | `/api/v1/overview/summary?unit=week\|month\|year&anchor=&n=` | buckets + current detail; n capped 104 / 60 / 12 (`backend/api/overview.py:69`) |
| GET | `/api/v1/overview/pmc?begin=&end=` | daily tss / ctl / atl / tsb; default the last 180 days (`backend/api/overview.py:79`) |
| GET | `/api/v1/overview/weekplan` | the generated week plan (`backend/api/overview.py:90`) |
| GET | `/api/v1/overview/page` | `backend/static/overview.html` (`backend/api/overview.py:97`) |
| GET | `/api/v1/overview/plan/sessions?start=&end=` | reconcile-if-needed, then stored sessions (not deleted / superseded) with COROS status, week meta, projected weeks and `summary` (`backend/api/plan_sessions.py:160`) |
| POST | `/api/v1/overview/plan/sessions` | add a custom session; 400 on a bad field (`backend/api/plan_sessions.py:188`) |
| PATCH | `/api/v1/overview/plan/sessions/{uid}` | edit day / kind / minutes / title / target / detail; 400 on a bad field (`backend/api/plan_sessions.py:198`) |
| DELETE | `/api/v1/overview/plan/sessions/{uid}` | tombstone (auto) or remove (custom); 404 when unknown (`backend/api/plan_sessions.py:208`) |
| GET | `/api/v1/overview/plan/reconcile` | preview: changes and changes by day (`backend/api/plan_sessions.py:217`) |
| POST | `/api/v1/overview/plan/reconcile` | apply the same (`backend/api/plan_sessions.py:226`) |
| GET | `/api/v1/overview/plan/push-coros/preview?scope=day\|week\|phase&day=` | sessions in range with COROS status, counts to send / unchanged / skipped, missed to remove, pending changes; 400 for a past week (`backend/api/plan_sessions.py:239`) |
| POST | `/api/v1/overview/plan/push-coros?scope=&day=` | reconcile, push the range, clean up; 401 `COROS_AUTH_REQUIRED`; 400 for a past week (`backend/api/plan_sessions.py:262`) |
| DELETE | `/api/v1/overview/plan/push-coros?scope=&day=` | remove what was pushed in the range; 400 for a past week (`backend/api/plan_sessions.py:284`) |
| GET | `/` | redirects to the overview page when no `frontend/dist` build exists (`backend/main.py:79`) |

`Status` is memoised per (dataset, day, `plan.json` mtime) (`backend/api/overview.py:40`); the
plan endpoints memoise their generator inputs on the same key (`backend/api/plan_sessions.py:39`).
Bad scope or day → 400 (`backend/api/plan_sessions.py:122`).

## Testing

- `backend/tests/test_overview.py`: category mapping, period arithmetic and labels, the
  projection matching the `tl()` recurrence and TSB = yesterday's CTL − ATL, and the ramp
  formula reaching exactly +3 CTL in 7 days.
- `backend/tests/test_plan_store.py`: projection ramp / cap / horizon, every reconcile rule
  (regeneration, tombstones, missed and late-sync done, coverage, collisions, supersede,
  horizon), persistence and edits, API initialisation, push scopes and idempotency, missed
  removal, concurrent first loads, unpush, and the stored-plan summary moving bars and projection.
- `backend/tests/test_coros_workouts.py`: step building, program payload, push / replace /
  remove against a fake Training Hub.

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
| quality gate | intensity and drift not bad; in base phase also the drift streak |
| drift streak | consecutive fair easy road runs with drift < 5 %; 3 unlocks the base-phase interval |
| 閾值下 N×8 | base-phase sub-threshold interval (88–95 % CP), 3×8 first, one rep fewer after a faded one |
| projection | the tl() recurrence continued with planned daily TSS; on the page, from the stored plan |
| stored plan | the `plan_sessions` table — the source of truth once generated |
| auto / custom session | generated by the planner vs added by the athlete |
| edited | a session the athlete changed; regeneration never overwrites it |
| tombstone | a deleted auto session kept as `state = deleted` so its `gen_key` is not regenerated |
| superseded | an edited hard session dropped because its week became a rest week |
| missed | an active past session with no matching activity, within the synced-data coverage |
| reconcile | refresh the stored plan from activities and a regenerated plan (rules 1–5) |
| horizon | the last day planned: phase end, ≥ 2 weeks, ≤ 8 weeks |
| provisional | a projected week beyond next week; recalculated as weeks arrive |
| push scope | day, week or phase range sent to COROS |
| fingerprint | SHA-256 of day + COROS payload; unchanged → not re-pushed |

### Domain Events
None as explicit events. State transitions of a stored session (active → done / missed /
deleted / superseded) are returned as reconcile `changes` (`backend/engine/reconcile.py:63`).

## Change History

| Date | Source | Feature SRS | Summary |
|------|--------|-------------|---------|
| 2026-09-30 | code-sync | N/A | Created from brownfield analysis — all-sport home page: status, week/month/year totals, combined PMC with projection, rule-based weekly plan |
| 2026-09-30 | code-sync | N/A | Stored editable plan (plan_store / reconcile / projection, /plan/* endpoints), COROS push by day/week/phase via coros_plan_push, bars + PMC projection from the stored plan, 總覽 page with AeT/CP glossary and sources removed, drift-streak quality gate and CP-test delta in status |
| 2026-09-30 | bugfix | N/A | Spec-sync fixes: projection quality gate per projected week (week_plan returns `quality_gate`), athlete ATL constant, sessions without a day; past-week push scope is a 400; test-only push_week / remove_week / week_status removed; last_quality includes hikes |
