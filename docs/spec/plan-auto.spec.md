# Module Spec: plan-auto (自動調整課表)

> **Last Updated**: 2026-10-01
> **Status**: Active
> **Domain Layer**: Core Domain

## Overview

The plan runs on its own. After every sync that imports at least one new activity, the app
marks what was done or missed, adapts the current week to what happened, regenerates the
upcoming weeks and pushes the next 7 days to COROS. The athlete only reviews: they mark
不排課日期, edit a session by hand, or approve a held change. **Their edits always win**:
edited and custom sessions, tombstones and blackout decisions are never changed by
automation (the reconcile rules in `backend/engine/reconcile.py`).

An easy run done too fast or too hard is **never voided**. It stays done, counts its real
TSS, and the following days adapt.

## Flow

```
sync/runner.stream()  ── finally: last_result stored
      │  downloaded ≥ 1 and status ok / partial
      ▼
plan_auto.after_sync() ── asyncio task, own DB session (run_safe never raises)
      ▼
plan_auto.run()  ── plan writer lock (api/plan_sessions._wlock, shared with the page)
      │  same data stamp as last run → noop (nothing changed, nothing pushed)
      ▼
plan_store.reconcile_with_adapt()
      │  gen_weeks(inputs) → adapt.adapt() → reconcile.reconcile() → adapt.apply_notes()
      ▼
plan_auto.diff() → classify()
      ├─ not big (or confirm_big off) → save → push_window(today … today+N−1) → log "applied"
      └─ big → save only done / missed / notes → log "pending" (+ 課表待確認 notice on the watch)
                 approve → run(force) → "applied" ; reject → fingerprint remembered
```

`adapt` runs on the generator's weeks **before** reconcile. Reconcile rule 2 overwrites every
unedited auto session with the generator's output, so an adjustment made on stored rows would
be undone by the next reconcile. Same inputs give the same adjusted weeks, so a second
reconcile reports no change. Every reconcile path applies it: the page's first visit, the
reconcile preview, a manual push and the automatic run. They all show the same plan.
`plan.auto.enabled = false` gives the plain generator.

## Settings (`user_settings`, `backend/settings/repository.py`)

| key | default | meaning |
|---|---|---|
| `plan.auto.enabled` | true | run after a sync, and apply the adapt rules |
| `plan.auto.push` | true | push the window to COROS automatically |
| `plan.auto.push_days` | 7 | days pushed from today (1–14); later sessions update in the app only |
| `plan.auto.confirm_big` | true | hold big changes for approval |
| `plan.auto.notify` | watch | `watch`: also push a 1-minute 「⚠ 課表待確認」 workout; `overview`: banner only |
| `plan.auto.state` | — | internal: last data stamp, last phase, rejected fingerprints |

The toggles are in 「自動調整設定」 on the 課表 page (`backend/static/autoplan.js`).

## Adapt rules (`backend/engine/adapt.py`)

Only the current week is adjusted. Sessions that are done, edited, custom or deleted are
"locked" and never touched.

| rule | trigger | action | source |
|---|---|---|---|
| A missed easy | stored easy session `missed` | its make-up (same gen_key, re-placed by week_plan) is dropped | Seiler「easy days easy」; not making it up is 自組 |
| B missed quality / test | stored quality / test `missed` | stays on the generator's day if it is ≥ 2 days from the long run and every other hard day (done or planned). Else it moves to a free day that keeps that gap. Else it is cancelled. Next week repeats the dose step (the step only counts sessions done) | `plan_prefs.place()` 48-h rule |
| C missed long | stored long `missed` | same week, on a free day not next to a quality / test day, else cancelled; never carried into next week | 48-h rule; no carry-over is 自組 |
| D easy run too hard | done easy run with avg HR > AeT + 3 bpm, **or** > 10 % of the time above AeT + 3, **or** avg power > 80 % CP, **or** TSS > planned + 20 % | (1) the done session counts its actual TSS (`plan_store.session_tss`); (2) a hard session < 2 days later moves later in the week if the gap allows, else it steps down one dose step, else it becomes an easy run; (3) the remaining easy runs lose the excess TSS, each ≥ 20 min, else the last easy run is dropped (long and quality are never trimmed); (4) the note 「輕鬆跑偏強（…）：已調整之後的課表」 goes on that day | AeT + 3 / 10 %: `workout_review.AET_MARGIN` / `OVER_AET_SHARE`; 80 % CP: zones z2 (Palladino 1C); +20 %: TrainingPeaks compliance green band; OR-combination, 20 min and the downgrade order are 自組 |
| E fatigue guard | TSB < −30 (only when week_plan has not already made it a recovery week), CTL ramp ≥ `status.RAMP["short"]` (7/week), or two red-compliance sessions in a row | TSB / ramp: the quality is removed. Two reds: the quality is downgraded to the recovery fartlek. Easy minutes × 0.8 (≥ 20 min) in all three cases | Palladino ramp; week_plan TSB rule; the 2-red trigger and the 20 % cut are 自組 |

## Interval progression (`backend/engine/quality_gate.py`)

`interval_outcome()` and `dose_step()` implement `docs/research/interval-adaptation.md` §4.3.
They replace the old 「最後一組比第一組低 > 5% → 退一步」 rule, which the WKO5 speakers
oppose. Each interval session in the 8-week history is judged against the dose step it was
planned at.

| outcome | condition (checked in this order) | next |
|---|---|---|
| 未適應（目標太高） | rep 1 below 98 % of the band's lower bound | same step, target −5 % (ROLE:499) |
| 未適應 | fewer reps done than planned, or the first miss is rep 2 … second-to-last | same step with rest + 1 min; a second one in a row steps back one |
| 邊界 | HR back under AeT 60 s into the rest on < 50 % of the reps (brake only), or only the last rep missed and it fell > 5 % | the same step again |
| 達標 | otherwise | the next ladder step (reps → rep length → power) |

Thresholds: 98 % in-band, 50 % AeT-at-60 s and the 5 % last-rep fade are 自組 (doc §4.2–4.3).
RPE is not recorded, so the RPE rows are skipped. Reps come from power (`count_reps`, or
`detect_efforts`).

## Big changes (held for approval; thresholds 自組)

- A week's planned TSS rises more than 20 % above the stored (last pushed) version.
  Reductions are the safe direction and apply on their own.
- A long, quality or test session is removed within 14 days before an A race.
- The training phase changed since the last run.
- More than 3 sessions change in the push window and they are not all reductions.

A week generated for the first time is not a change. The phase baseline (`plan.auto.state.phase`)
moves only when a plan is applied, so a held phase change stays held on later syncs. While a
proposal waits, the page's Monday reconcile (`_ensure` with leftovers) does not apply it either;
a manual push still applies everything (an explicit user action), and the next run then marks
the proposal superseded. Only an `applied` entry can be undone.

Interval sessions the plan prescribed outside the ladder (recovery fartlek, Zone 3, a
sub-threshold 3×8′ in a ramp week before the ladder reached it) are neutral in `dose_step`:
`dose_history` reads the planned title of the done session (`plan_store.done_titles`), and
`planned_spec` resolves it.

While a proposal is held, the stored plan only takes the done / missed / note part, and the
watch keeps what was pushed. When the same proposal comes up again, nothing new is logged
or pushed. A proposal the user rejected is skipped when it comes up again. A newer proposal
supersedes the older one.

**Notice workout** (`kind = "notice"`): it goes on today, or on the next session day when
something was already done today. It is one 1-minute open warm-up step, and its overview
holds the reasons. It is never matched as done or missed. It never counts in TSS,
compliance, the week bars or the PMC (`reconcile.SIDE_KINDS`, `plan_store.NOT_LOAD`,
`compliance.session_compliance`). It is removed from the store and from COROS (explicit
`remove_keys`) on approve, on reject, when superseded, or once its day has passed.

## Change log (`plan_change_log`, `backend/db/models.py`)

The table is new, so `init_db`'s `create_all` creates it. That is the schema's migration
path for new tables. Each row holds:
- the trigger and the status: applied / pending / approved / rejected / superseded / undone / restore / failed
- a one-line Traditional Chinese summary
- the items, each with a reason, e.g. 「間歇延到週五：週三沒跑；離長跑（週日）仍有 2 天」
- the affected sessions before and after
- why it was held
- the push outcome, or the push error

**復原** restores the before-state of the affected sessions as the athlete's own
(`edited = true`), so the next run does not redo the change. Sessions the run added are
tombstoned. The window is then re-pushed.

API (`backend/api/plan_auto.py`, prefix `/api/v1/overview/plan/auto`):
- `GET ""`: settings, the pending proposal, the last entries and the thresholds
- `PUT /settings`
- `POST /run`: ignores the data stamp; big changes are still held
- `POST /proposal/{id}/approve`
- `POST /proposal/{id}/reject`
- `POST /log/{id}/undo`

UI: `backend/static/autoplan.js` puts the proposal banner (同意 / 拒絕) and the last entries
(each with 復原) on the overview and on the 課表 page. The 課表 page also gets the settings.

## Safety

- One run at a time, enforced by the shared plan writer lock. Concurrent runs: the second one
  finds the same stamp and does nothing.
- A push failure is stored in the change-log row. It never raises into the run or the sync.
  A crashed run writes a `failed` row.
- The push sends nothing when every session in the window is already up to date on COROS.
- Tests never start a run on the real DB: `conftest._no_auto_plan_after_sync`. COROS is
  mocked in `backend/tests/test_plan_auto.py`, either with a stubbed `push_sessions` or with
  the scripted FakeHub.

## Known limits

- Dataset freshness: the plan reads `charts.data_source`. When that source is `wko5`, FITs from
  a COROS / TP sync reach the plan only after WKO5 imports them. Until then the run is a noop
  for that data.
- Lap-based rep matching (`interval-adaptation.md` S0 / S2) is not built. The dataset gives
  no FIT laps, and whether a 1-minute step gets its own lap is unverified. Reps are detected
  from power.
- The 「休息 60 秒心率降幅 < 20」 line and the fade line in the workout review card
  (`workout_review.interval_lines`) are unchanged. They are display only and no longer drive
  the dose.
