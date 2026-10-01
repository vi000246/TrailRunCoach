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
| B missed quality / test | stored quality / test `missed` | stays on the generator's day if it is ≥ 2 days from the long run and every other hard day (done or planned). Else it moves to a free day that keeps that gap. Else it is cancelled. Next week repeats the dose step (the step only counts sessions done) | ≥ 2 days between hard days: 台灣教練 |
| C missed long | stored long `missed` | same week, on a free day not next to a quality / test day, else cancelled; never carried into next week | 2-day rule; no carry-over is 推估 |
| D easy run too hard | done easy run with avg HR > AeT + 3 bpm **and** > 10 % of the time above AeT + 3 (both, `unsourced-rules.md` §B5), **or** avg power > 80 % CP, **or** TSS > planned + 20 % | (1) the done session counts its actual TSS (`plan_store.session_tss`); (2) a hard session < 2 days later moves later in the week if the gap allows, else it steps down one ladder step, else it becomes an easy run; (3) the remaining easy runs lose the excess TSS, each ≥ 20 min, else the last easy run is dropped (long and quality are never trimmed); (4) the note 「輕鬆跑偏強（…）：已調整之後的課表」 goes on that day | AeT + 3 / 10 %: `workout_review.AET_MARGIN` / `OVER_AET_SHARE`; 80 % CP: zones z2 (Palladino 1C); +20 %: TrainingPeaks compliance green band. HR needs both because this athlete's summer easy runs sit high on HR alone (heat); the combination, 20 min and the downgrade order are 推估 |
| E fatigue guard | TSB < −30 (only when week_plan has not already made it a recovery week), CTL ramp ≥ `status.RAMP["short"]` (8/week; not in a re-entry block), or two red-compliance sessions in a row | TSB / ramp: the quality is removed. Two reds: the quality is downgraded to the recovery fartlek. Easy minutes × 0.8 (≥ 20 min) in all three cases | CTL ramp 5 warn / 8 block: Friel (coach, https://joefrieltraining.com/the-ctl-ramp-rate/ — 5–8 suits most, 10 the ceiling); TSB −20 / −30: Friel / TrainingPeaks (coach); the 2-red trigger and the 20 % cut are 推估 |

The guardrails behind the gate (`quality_gate.guard`, `status`) use the same sources
(`unsourced-rules.md` §B2): CTL ramp ≥ 5 → threshold only, ≥ 8 → no interval (Friel); last
week's volume step > 20 % → no interval (Nielsen et al. 2014, JOSPT 44:739; Damsted et al.
2019, JOSPT 49:230 — peer-reviewed; the 「10 % 法則」 itself has no evidence); 10–20 % → hold
the dose (推估, conservative); TSB −30…−20 → hold (Friel / TrainingPeaks).

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
| 達標 | otherwise | the next ladder step |
| 無法判定 | no bouts or no CP (the old rule counted it as 達標) | the same step again — progress only on 達標 (`unsourced-rules.md` §B4) |

Thresholds: 98 % in-band, 50 % AeT-at-60 s and the 5 % last-rep fade are 推估 (doc §4.2–4.3).
RPE is not recorded, so the RPE rows are skipped. Reps come from power (`count_reps`, or
`detect_efforts`).

### The ladder: Zone 3 first, then Zone 5 (台灣教練)

「第一個加進來的質量課表我會先選強度 3 區…等 3 區跑順了、恢復也跟得上，再把 5 區間歇排進來」.
The old first rungs (5×1′ @ 98–101 % CP) were, in his terms, too short to train VO2max yet a
Zone 5 load; they are gone (`LEGACY_TITLES` are neutral in the history).

| step | session | target | source |
|---|---|---|---|
| 0 | 閾值 3×8 分, rest 2 | 88–95 % CP | 徐國峰 Z3 first; Palladino 3A |
| 1 | 閾值 4×8 分 | 88–95 % CP | Seiler 2013 4×8 |
| 2 | 閾值 3×10 分, rest 3 | 95–101 % CP | Palladino 3B |
| 3 | VO2max 5×2 分 | 106–112 % CP (推估: Palladino Z5's lower part) | 徐國峰: reps ≥ 2 min |
| 4–6 | 4×3, 5×3, 4×4 分 | 105–110 / 103–107 % CP | Koop; Helgerud 2007 |
| after | 4×4 and 3×10 alternating | | |

Zone 5 rungs (step ≥ 3 = three Zone 3 sessions 達標 — the count is 推估) are scheduled only while
Zone 5 is open (below); otherwise the top Zone 3 rungs alternate and the Zone 5 step waits
(those Zone 3 sessions are neutral). At most 2 Zone 5 sessions a week, ≥ 2 days apart
(徐國峰; base phase plans one, `plan.prefs.quality_per_week = 2` places the second ≥ 2 days
away). A ramp-week session (`SUB`, 「閾值 3×8 分（只排閾值）」) and the recovery fartlek are
neutral.

### Two gates and the Zone 5 lifecycle (`backend/engine/base_check.py`)

- **Zone 3** whenever the guardrails pass. A locked method (e.g. the UA gap > 10 %) keeps
  Zone 5 closed but no longer stops Zone 3.
- **Zone 5** opens only when ONE of three tests has been done and passed (auto; 2026-10-01
  使用者決定): 徐國峰's 90-min test (`xu90`: minute 10 vs minute 90, < 10 %); a measured AeT with
  LTHR ÷ AeT − 1 ≤ 10 % (`aet_ua_gap`); ≥ 60 min near a measured AeT, first vs second half
  drift < 5 % (`aet_friel_drift`). Forced modes use their own test (`xu_drift`, `ua_gap`,
  `friel_drift`; `plateau` / `weeks` by their own unlock; `none` = no gate). 三訊號 and its
  mode `xu_signals` were removed (aerobic-base-readiness.md); a stored `xu_signals` reads as
  `auto` (`plan_prefs.from_settings`), new writes are rejected.
- **徐國峰's 90-min test** (`xu_run`): ≥ 90 min, flat (not trail, < 20 m/km), ≤ 25 °C, every
  stop ≤ 30 s, HR in Zone 1 (the app's easy rule: avg ≤ AeT + 3, ≤ 10 % above — mapping his
  E zone to "below AeT" is 推估), (HR@90′ − HR@10′) / HR@10′ < 10 % — his own comparison, not
  drift_of's halves (notes L58–L67). Any qualifying run counts: "就是你週末那一次 LSD".
- **Maintenance** (weekly, no expiry): Zone 1 time < 2/3 of the level at confirmation (mean of
  the 4 weeks up to it) for 3 complete weeks in a row → pause Zone 5 until the next
  confirmation (Hickson 1982; 3 weeks 推估; recovery / taper / event / transition weeks and weeks
  touching a break don't count). The late long-run HR / pace check no longer pauses Zone 5; it
  survives only as the re-entry rule's post-break drift check (`long_check`, 14–28 days off). A
  break ≥ 6 days is the re-entry rule below. A paused state says why in `z5["pause"]`
  (`kind`: `z1` / `reentry_z3` with done / need / `drift_check`).
- **Tracker** (`quality_gate.z5_card`): `base` = one step 「確認有氧基礎（三選一，做了且達標）」 with
  the mode's tests (✓ / ✕ / – and the current value; after a Z1 pause or a ≥ 4-week break only
  results dated after it count); `steps` = ① base ② 3 區達標 n/3 ③ 5 區開放, each done / active /
  todo / paused / wait; `next` = the one 「還缺：…」 line (or 恢復期還剩 n 天 / 都做到了), computed
  once and shown by the 總覽 card and the 基礎期 chart (`wko5views.z5_progress`).
- **Re-confirmation**: passive first — any qualifying run re-confirms. The AeT test is
  scheduled only for a reason (`quality_gate.aet_test_reason`): no interpretable run for ~6
  weeks (UA's 4–6-week retest, coach; wording 未驗證), the aggregated AeT estimate missing or
  SE > 3 bpm, a shift > 5 bpm in the last 6 points (B3), the estimate more than max(SE, 3 bpm)
  from the plan's AeT ("moved": UA — AeT rises toward AnT as the base improves), or after a
  break ≥ 4 weeks. A passive confirmation in the last 6 weeks stands in for no_data / se.
  ≥ 28 days between tests (推估). No fixed cadence any more (16 weeks, 4–6 weeks: no source).
- **Faster at the same HR**: the easy targets show pace / power at the AeT HR from the median
  EF of the last 6 easy road runs (`base_check.easy_targets`, 推估); the AeT HR is unchanged and
  needs no retest. CP drives the Zone 3 / 5 power targets (the CP-test track is unchanged).
- **History** (`quality_gate.z5_history`): the lifecycle replayed on every day of a range with
  the inputs evaluate() would have had that day (the plan's AeT / LTHR in effect, `reentry.find`
  on that day, the interval sessions of the 8 weeks before it) through the same `_z5`, so the
  last day equals the gate. plateau / weeks unlock by their own method and are not replayed
  (noted). Shown on 總覽 (card, `z5_card`) and in 基礎期 (chart, view kind `z5gate`).
- **Visibility**: the gate hover and the overview show 「Zone 5：未確認／已確認（日期、路徑）／
  暫停（原因）／恢復期」 and 「建議測試：…」.

### The AeT test protocol (`plan.prefs.aet_test_protocol`, `backend/engine/aet_test.py`)

| key | length | terrain | held | judged | source |
|---|---|---|---|---|---|
| `auto` | the standard `xu90`; `ua40` when the long-day cap < 90 min | | | | justification: `aerobic-base-readiness.md` §6.3 |
| `xu90` | 10 + 80 = 90 min, on the weekend long day in place of the long run | flat | E pace | HR@10′ vs HR@90′ < 10 % (base check, no AeT number) | 台灣教練 |
| `ua60` | 15 + 60 + 5 | treadmill 2–3 % / flat loop | power | halves, < 3.5 / 3.5–5 / > 5 % | Uphill Athlete |
| `ua40` | 10 + 40 | same | power | same | Uphill Athlete ("If you only have 40 minutes") |
| `evoke60` | 10 + 60 + 5 | treadmill 2 % / flat loop, no out-and-back | speed (power) | halves, > 5 % = above AeT | Evoke |
| `friel` | 10 + 60 + 5 | steady flat | at AeT HR | halves < 5 / 5–10 / > 10 % | Friel (TrainingPeaks) |

The protocol drives the session text (「氣溫 25 °C 以下時開始（熱會讓心率偏高、飄移失真）」,
徐國峰 + Lafrenz 2008), the COROS steps (xu90 / friel: an HR-capped main block; UA / Evoke:
a power range), the placement (xu90 on the weekend; the others by `aet_test_days`) and the
analysis (`analyze_workout`: warm-up cut, window and judging rule by the title's protocol).
The analysis uses VI ≤ 1.04 (drift v2) instead of the old 30-s CV. Every protocol is ≥ 40
min of test, so all are the strict tier. MAF is not a drift test and is not offered.

### 停訓後的恢復期 (`backend/engine/reentry.py`; `docs/research/detraining.md`)

A break = consecutive days without a run (planned: a 不排課日期 range ≥ 6 days with no run
inside, scheduled ahead; unplanned: from the runs — the current gap counts as a break
returning today; the next automatic run applies it and logs it). The block lasts as long as
the break (Daniels table 9.2, coach): the week's volume = the mean of the 4 weeks before the
break (推估) × the block's % per day (break days 0).

| break | block | Zone 3 / 5 | targets |
|---|---|---|---|
| 1–5 d | none, back to 100 %, no make-up | as before | × 1 |
| 6–13 d | first half 50 %, second half 75 %; long ≤ 90 min (推估) | none inside; Z5 after 1 Z3 session (推估 count; 徐國峰 Z3 first) | × FVDOT |
| 14–28 d | as above | Z5 after 2 Z3 sessions and the block's last long run passing the drift check (UA) | × FVDOT 0.973–0.931 |
| 29–56 d | 33 / 50 / 75 % thirds | Z5 only after a confirmation dated after the break (Mujika & Padilla 2000); AeT stale → test | × FVDOT |
| > 56 d | 15 weeks, 33 → 50 → 70 → 85 → 100 % | Z3 from week 13 (推估 mapping), base restart; CP and AeT retests | × FVDOT ≤ 0.847 |

FVDOT: VDOT O2 (2018, coach); FVDOT-2 with cross-training on ≥ half the break's days ≥ 45 min
(推估); the page's 42-day FVDOT-2 0.994 is read as 0.944 (未驗證). Power × FVDOT is 推估. HR
zones stay primary in the block. A week touching the block gets no quality (推估: the whole
week clear). The old `blackouts.step_cap` (+10 %, at least +0.5 h after a blocked week) is
replaced by the block; a break < 6 days doesn't lower the base the next weeks ramp from.
The block's planned step-ups are exempt from the 「+20 % TSS」 big-change rule (推估); adapt's
ramp guard skips it.

## Big changes (held for approval; thresholds 推估)

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

Plan-rule changes get their own `applied` rows without sessions (`plan_auto.state_changes`,
state keys `z5` / `reentry` in `plan.auto.state`): every Zone 5 state change
(「Zone 5：未確認 → Zone 5：已確認（…）」) and every new re-entry block
(「恢復期：停跑 N 天…（不排課日期，事前排好／從活動資料偵測）」).

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

- A 3:1 recovery week inside the Zone 1 maintenance run is not detected from the data (only
  phase-level recovery / taper weeks and break weeks are skipped); three low weeks are needed,
  so one recovery week alone never pauses Zone 5.
- Illness / injury marks (detraining.md §6.2) are not built: a break is only "days without a
  run"; the doc's symptom-free start and the injury hand-off are left to the user.
- Cross-training detection for FVDOT-2 counts non-run endurance sessions ≥ 45 min (推估); the
  doc's FVDOT-2 definition is 未驗證.

- Dataset freshness: the plan reads `charts.data_source`. When that source is `wko5`, FITs from
  a COROS / TP sync reach the plan only after WKO5 imports them. Until then the run is a noop
  for that data.
- Lap-based rep matching (`interval-adaptation.md` S0 / S2) is not built. The dataset gives
  no FIT laps, and whether a 1-minute step gets its own lap is unverified. Reps are detected
  from power.
- The 「休息 60 秒心率降幅 < 20」 line and the fade line in the workout review card
  (`workout_review.interval_lines`) are unchanged. They are display only and no longer drive
  the dose.
