# Module Spec: plan-auto (自動調整課表)

> **Last Updated**: 2026-10-08
> **Status**: Active
> **Domain Layer**: Core Domain

## Overview

The plan runs on its own. After every sync that imports at least one new activity, the app
marks what was done or missed, adapts the current week to what happened, regenerates the
upcoming weeks and pushes the next 7 days to COROS. The athlete only reviews: they mark
不排課日期, edit a session by hand, or approve a held change. **Their edits always win**:
edited and custom sessions, tombstones and blackout decisions are never changed by
automation (the reconcile rules in `backend/engine/reconcile.py`). A 交換 of two sessions (SP-359,
`plan_store.swap`, `backend/engine/plan_store.py:359`) is two such edits in one commit — each move
is the user's own, exactly like a drag — so reconcile and auto-adjust never undo it.

An easy run done too fast or too hard is **never voided**. It stays done, counts its real
TSS, and the following days adapt.

## Domain Model

**Bounded Context**
- Context Name: 自動調整課表 (Plan Automation)
- Domain Layer: Core Domain
- Parent Module: the training plan (課表; `backend/engine/plan_store.py`, `backend/engine/reconcile.py`,
  `backend/api/plan_sessions.py`)

**Ubiquitous Language**

| Term | Meaning | Code |
|---|---|---|
| 自動調整 run | one pass: done / missed → adapt → regenerate → push the window | `plan_auto.run` |
| data stamp | hash of today + the activities; same stamp as last run = noop | `plan_auto.stamp` |
| adapt rule | an outcome-based change to the current week (A–E) applied before reconcile | `engine/adapt.py` |
| locked session | done / edited / custom / deleted: automation never touches it | `adapt._Week.locked` |
| big change / proposal (課表待確認) | a change held for approval; the stored plan takes only done / missed / notes | `plan_auto.classify`, status `pending` |
| notice workout | the 1-minute 「⚠ 課表待確認」 reminder on the watch, never load | kind `notice` |
| push window | today … today + `push_days` − 1, pushed through the push provider | `plan_auto.push_window` |
| change log / 復原 | `plan_change_log` rows; undo restores the before-state as the user's own | `PlanChangeLog`, `plan_auto.undo` |
| dose step / rung | the interval ladder position, moved only by 達標 | `quality_gate.dose_step`, `interval_library` |
| Zone 5 gate | Zone 5 opens after an aerobic-base confirmation + Zone 3 達標 | `base_check`, `quality_gate.z5_card` |
| 恢復期 (re-entry block) | the reduced block after a break ≥ 6 days (傷停 when it overlaps an injury, 生病停跑 an illness in the 傷病紀錄) | `engine/reentry.py` |
| 賽後階段 (post-race phases) | the A race's 恢復期, 轉換期 and 回量期 (`rebuild`, SP-98): planned, not a running break | `planning.POST_RACE_KINDS` |
| A 賽後重新打底 (rebase) | after an A race, the gates count only evidence from the first day after its post-race phases | `base_check.a_race_rebase` |
| 生病 (illness event) | a 傷病紀錄 entry of category `illness`, type `cold` / `fever` (SP-117) | `injuries.illness_rule` |
| 週課表紀錄 (week snapshot) | the week's plan recorded at the end of every run (SP-71) | `plan_history.record_safe`, `plan_week_snapshots` |

**Domain Events** (each writes a change-log row): 課表已自動調整 (`applied`), 課表待確認
(`pending`), 同意／拒絕／被取代 (`approved` / `rejected` / `superseded`), 復原 (`undone` +
`restore`), CP 變更 (`cp_change`: power targets re-zoned), Zone 5 狀態變更 and 新恢復期
(`plan_auto.state_changes`), 自動調整失敗 (`failed`).

## Flow

```
sync/runner.stream()  ── finally: last_result stored
      │  status ok / partial and (downloaded ≥ 1 or rpe_filled ≥ 1 — a COROS self-rating
      │  stored on an already-imported activity, SP-231)
      ▼
plan_auto.after_sync() ── asyncio task, own DB session (run_safe never raises)
      ▼
plan_auto.run()  ── inputs first (API._inputs, no lock; SP-362 B3)
      ▼
_run()  ── plan writer lock (api/plan_sessions._wlock, shared with the page)
      │  inputs_key ≠ the key now (a settings edit / newer data meanwhile) → release, recompute,
      │  again (RUN_TRIES 3; the last try computes inside the lock)
      │  same data stamp as last run → noop (nothing changed, nothing pushed)
      ▼
plan_store.reconcile_with_adapt()
      │  gen_weeks(inputs) → adapt.adapt() → reconcile.reconcile() → adapt.apply_notes()
      ▼
plan_auto.diff() → classify()
      ├─ not big (or confirm_big off) → save → log "applied" (push: pushing)
      └─ big → save only done / missed / notes → log "pending" (+ 課表待確認 notice in the plan)
                 approve → run(force) → "applied" ; reject → fingerprint remembered
      ▼
state (stamp, cp, phase) → plan_history.record_safe() ── the week snapshot (SP-71)
      ▼  writer lock released
_push_after()  ── push lock (api/plan_sessions._plock; COROS only)
      │  old notices off the watch; the new notice, or push_window(today … today+N−1)
      │  from the stored plan as it is now (an edit saved meanwhile goes out as edited)
      ▼
the row's push result filled in (or an "applied" row for a push-only change); CP row (_log_cp)
```

`adapt` runs on the generator's weeks **before** reconcile. Reconcile rule 2 overwrites every
unedited auto session with the generator's output, so an adjustment made on stored rows would
be undone by the next reconcile. Same inputs give the same adjusted weeks, so a second
reconcile reports no change. Every reconcile path applies it: the page's first visit, the
reconcile preview, a manual push and the automatic run. They all show the same plan.
`plan.auto.enabled = false` gives the plain generator.

Other entry points:
- **CP change**: `api/plan.py` calls `plan_auto.after_thresholds()` after a threshold edit; that
  run (trigger `cp_change`) also starts when the stamp is unchanged (see 「CP change」 below).
- **Settings**: `api/calib.py` calls `plan_auto._after_settings()` (trigger `settings`,
  `backend/engine/plan_auto.py:1131`) after a 進階設定 value the plan reads changed (the Zone 3
  unlock rule, SP-295). It goes through the same stamp check without `force` (see Known limits).
- **Week snapshot** (SP-71): the end of `_run` records the week in `plan_week_snapshots`
  (`plan_history.record_safe`, `backend/engine/plan_auto.py:881-883`; `PlanWeekSnapshot`,
  `backend/db/models.py:224`), so a sync alone records it with
  no page opened; the 課表 page reads them through `GET /api/v1/overview/plan/history`
  (`backend/api/plan_sessions.py:2810`).
- **Done / missed matching** (reconcile rule 1) is `backend/engine/plan_match.py`: same day + the
  planned sport first, one activity per session (long / quality / test also by the generator's
  week-wide match). The user can link / unlink by hand; an unlinked activity is never
  auto-matched again (`plan.match.unlinked`). The 課表 page's `GET /overview/plan/sessions` runs
  the match-only part on every load (`plan_store.match_only`: nothing becomes missed), so a synced
  run shows on its session at once, also while a proposal waits. Exception (SP-362 B4): a 課表
  calendar answered from the previous inputs (`stale`, while the fresh ones are computed after a
  sync / on a new day) matches and writes nothing; the page reloads when the fresh inputs are in
  and that load matches. A run always computes fresh inputs (it never sees the stale view).

## Settings (`user_settings`, `backend/settings/repository.py`)

| key | default | meaning |
|---|---|---|
| `plan.auto.enabled` | true | run after a sync, and apply the adapt rules |
| `plan.auto.push` | null = auto | push the window automatically; auto = on when the push provider is connected (today only COROS can be) |
| `plan.auto.push_days` | 7 | days pushed from today (1–14); later sessions update in the app only |
| `plan.auto.confirm_big` | true | hold big changes for approval |
| `plan.auto.notify` | null = auto | `watch`: also push a 1-minute 「⚠ 課表待確認」 workout; `overview`: banner only; auto = `watch` when connected, else `overview` |
| `plan.auto.rpe_rule` | true | adapt rule D′: an easy / long run self-rated Hard or more moves the next hard session (SP-231); in `AUTO_KEYS` and `PUT /settings` |
| `plan.auto.state` | — | internal: last data stamp, last phase, last CP, Zone 5 / re-entry keys, rejected fingerprints |
| `plan.push.provider` | coros | the push target (`backend/sync/workout_targets/`); Garmin / intervals.icu are stubs, not enabled |

**Push window and moved sessions** (SP-358, `push_window`, `backend/engine/plan_auto.py:535`): the
run pushes the active sessions of the window, removes pushed ones that left the plan / were
missed / sit on a blocked day, and re-sends the sessions whose pushed copy sits in the window
although they moved out of it (dragged to next week: the copy would otherwise stay on the old
day). A removal that fails is a failure of the push (`partial`, with its error). A calendar delete
COROS accepted but still lists after a short pause is not a failure: the result carries
`check_days` (a reminder to check the COROS app; `coros_workouts._remove_remote`). The 課表 page's
own changes call it with `only` = the changed sessions (`_sync_watch`,
`backend/api/plan_sessions.py:2024`): everything — the window push and the stale / blocked /
missed clean-up — is limited to them (`backend/engine/plan_auto.py:561`); other leftover copies
are left to this run and the manual push. `window` False (自動推送 off) re-sends / removes only
copies already on the watch. A failure there adds a `failed` row (trigger `edit`) to the change log.

**Phases** (SP-73): the automatic run reads the same phases as the page (`planning.phases`,
`backend/engine/planning.py:1019`), including the 轉換期 after an A race's recovery (課表偏好
`plan.prefs.transition_weeks`, default 3, 0 = off; overview.spec.md 課表偏好) and the 回量期
(`rebuild`, SP-98) after it. Entering and leaving the 轉換期 or the 回量期 — and changing the
轉換期's length while in it — is a phase change, so it is held for approval like any other (Big
changes below). Adapt and reconcile treat the 轉換期 and the 回量期 as rest phases
(`reconcile.REST_MODES`, `backend/engine/reconcile.py:46`; adapt's rest week,
`backend/engine/adapt.py:505`); the re-entry block applies only in base / specific. Days inside a
planned post-race phase — `planning.POST_RACE_KINDS` (`backend/engine/planning.py:1040`) = the A race's 恢復期 (7 or 14 days by the
event's size, `planning.recovery_plan`), the 轉換期 and the 回量期 after it (auto or manual,
`planning.post_race_days`, `backend/engine/planning.py:1071`) — are **not a running break**
(owner 2026-10-05): a 恢復期 without a run or a transition of only cross-training / strength starts
no re-entry block when base resumes — `reentry.find_all` counts a break's days outside those
phases only (still ≥ 6 → a block of that length, its text 「停跑 N 天（不含賽後恢復期／轉換期 M 天）」,
the wording unchanged although M also counts 回量期 days) — and doesn't break the Zone 3 gate's
streak or re-lock it (below).

The toggles are in 課表 › ⚙ 課表偏好 › 自動調整 (`backend/static/schedule.html`, saved through
`PUT /settings`); `autoplan.js` no longer draws them.

## Adapt rules (`backend/engine/adapt.py`)

Only the current week is adjusted. Sessions that are done, edited, custom or deleted are
"locked" and never touched.

| rule | trigger | action | source |
|---|---|---|---|
| A missed easy | stored easy session `missed` | its make-up (same gen_key, re-placed by week_plan) is dropped | Seiler「easy days easy」; not making it up is 推估 |
| B missed quality / test | stored quality / test `missed` | stays on the generator's day if it is ≥ 2 days from the long run and every other hard day (done or planned; a done hard run counts even when unplanned — Z5 / Z3 / 高強度長跑 / CP test from the activities, `hard_days`). Else it moves to a free day that keeps that gap. Else it is cancelled. Next week repeats the dose step (the step only counts sessions done) | ≥ 2 days between hard days: 台灣教練（5 區一週最多 2 次、間隔至少 2 天） |
| C missed long | stored long `missed` | same week, on a free day not next to a quality / test day, else cancelled; never carried into next week | 2-day rule; no carry-over is 推估 |
| D easy run too hard (SP-301, two tiers) | **偏強**: a done easy run with avg power > 80 % CP **or** TSS > planned + 20 % (the plan's easy rate is the easy-only one, SP-302). The old AeT + 3 heart-rate condition is gone (2026-10-06). A run without power (no average power or no CP) reads average HR > 94 % LTHR instead of the power line (`adapt.EASY_HR_LTHR`; above Friel's running Zone 3 = into Zone 4, no time-share condition; lenient on purpose — summer heat — 推估, the user 2026-10-06); with power, HR is not used at all (review row `lthr`). **太強**: the session classifier (`workout_review.classify` / `session_stimulus.verdict`) puts the run in a hard class — Zone 3 threshold or harder: `quality` (Z3 / Z5), `hard_long`, `test_cp` (`adapt.TOO_HARD_TYPES` = `workout_review.HARD_TYPES`; ctx review row `session_type` / `stimulus`, `api/plan_sessions._adapt_ctx`) | 偏強: label only — the note 「輕鬆跑偏強（…）：只標示，課表不變」 on the done session and an adjustment `note`; no move, no step-down, no trimmed easy run. 太強: the run counts as a hard session — the next free quality / test < 2 days later moves to a free day ≥ 2 days after the run (and from the long run / other hard days), else steps down one ladder step (else an easy run); reason 「週四 閾值 3×10 分延到週五：週三輕鬆跑跑成閾值課，間隔不到 48 小時」 (閾值課 / VO2max 課 / 高強度長跑 / CP 測試); when `week_plan` already moved it off its stored day (it spaces hard sessions from a done hard run, `hard_done`) that move gets the reason. Note 「輕鬆跑跑成強度課（閾值課）：已調整之後的強度課」 (or 「…48 小時內沒有強度課，課表不用動」). Runs after E; a session another rule changed and locked (edited / custom / done) ones are left alone, nothing blocked is unblocked; within 14 days of an A race the change is held for approval (`plan_auto.classify` rule `too_hard`); 復原 as every run. Both: the done session counts its actual TSS (`plan_store.session_tss`); the old step 3 (the excess TSS off the remaining easy runs) is gone | 80 % CP: zones z2 (Palladino 1C, coach); 94 % LTHR: the top of Friel's running Zone 3 (`zones.FRIEL_HR`, coach), its use here 推估; +20 %: TrainingPeaks compliance green band (platform); Seiler「easy days easy」(principle); ≥ 2 days between hard days: 台灣教練. The two tiers, 「太強 = the classifier's hard class」 and the downgrade order are 推估 (the user's decision 2026-10-06) |
| D′ self-rating (SP-231, `rpe_hard`) | a done easy or long run whose post-run self-rating is Hard or more: COROS `feelType` ≥ 4 (mapped RPE ≥ 7, `engine/coros_rpe.py`) or a FIT's own RPE ≥ 7; switch `plan.auto.rpe_rule` (課表偏好 › 自動調整, default on) | rule D step 2 only: the next quality / test < 2 days later moves to a free day ≥ 2 days after the run (and from the long run / other hard days), else steps down one ladder step (else an easy run). Runs after E and D; a session another rule already changed is left alone, and a run D's 太強 already acted on adds nothing (one adjustment per session when both fire); locked sessions never move. Within 14 days of an A race the change is held for approval (`plan_auto.classify` rule `rpe`). Reason 「週四 有氧間歇…延到週六：週三輕鬆跑自評 Hard，間隔不到 48 小時」; 復原 as every run | 推估: no controlled trial (`docs/research/readiness-signals.md` §2.4; Nuuttila 2021: HR recovered, RPE still high); the Hard threshold is the user's (2026-10-06); the 1–5 → RPE mapping is 推估 |
| E fatigue guard | TSB < −30 (only when week_plan has not already made it a recovery week), CTL ramp at `load_guard`'s **block** line min(10, max(5, 15 % × CTL₋₇)) (not in a re-entry block; SP-63 — this rule used 8 = the old block line, so it maps to the new block line, not the watch line), or two red-compliance sessions in a row (`compliance.streak_red`, `backend/engine/compliance.py:161`; SP-370: a session red only because another **foot** sport was done — a hike / walk / trail run for a run and the like, time and TSS both measured and not red themselves — does not count; a non-foot sport such as a bike for a run, and time / TSS reds, still count) | TSB / ramp: the quality is removed. Two reds: the quality is downgraded to the recovery fartlek. Easy minutes × 0.8 (≥ 20 min) in all three cases | CTL ramp lines: Friel (coach, https://joefrieltraining.com/the-ctl-ramp-rate/ — 5–8 suits most, 10 the ceiling) as a share of CTL (推估, `docs/research/ctl-ramp-calibration.md` §4); TSB −20 / −30: Friel / TrainingPeaks (coach); the 2-red trigger and the 20 % cut are 推估. Exception (`b2b.fatigue_exempt`): in an accepted B2B week and its easy days after, TSB < −30 alone only logs a note (expected drop, 推估); the ramp and the red streak still act |

The guardrails behind the gate (`quality_gate.guard`, `status`) use the same sources
(`unsourced-rules.md` §B2), one copy in `backend/engine/load_guard.py` (SP-63): CTL ramp
(7-day ΔCTL against CTL₋₇) 注意 ≥ max(3, 10 % × CTL₋₇), 擋 ≥ min(10, max(5, 15 % × CTL₋₇)) — 注意 → threshold only, 擋 → no interval (Friel's 5–8 /
10 as a share of CTL, 推估). The guardrail CTL is the PMC's own (SP-68): it starts from the
user's manual CTL / ATL at a date (設定 → 閾值), else the mean daily TSS of the first 4 weeks of
data, else 0 (`load_guard.pmc_start`); the ramp is not checked in the first 28 days of an
automatic start or the first 7 after a manual one (推估; `status` passes `ramp_week = None`
then); the volume step still runs. Rule E's TSB < −30 reads `week_plan`'s `load.tsb_today` from
the same started PMC (SP-63 Q3), so a new user's startup weeks give no false fatigue trigger. Last week's **running-time** step against
max(the week before, the 4 weeks before's mean) — normal weeks only (SP-73, owner 2026-10-05: a week
touching a 減量期 / race week / post-race 恢復期 / 轉換期 / 回量期, `load_guard.STEP_SKIP_KINDS`
(`backend/engine/load_guard.py:100`), is left out and
the most recent normal weeks before it count, up to 26 weeks back; the planner's +10 % cap reads the
same weeks, overview.spec.md) — > 20 % → no interval (Nielsen et al. 2014, JOSPT
44:739; Damsted et al. 2019, JOSPT 49:230 — peer-reviewed, they measured running; the 「10 %
法則」 itself has no evidence); 10–20 % → hold the dose (推估, conservative). Exempt: the week after
a short unplanned break — 3–5 days without a run (3 推估; ≥ 6 is a re-entry block, `reentry.MIN_BREAK`)
touching the week before, which pulled the base down (`short_break`,
`backend/engine/load_guard.py:341`; owner 2026-10-04). Only unplanned days count (owner
2026-10-05): days of the user's own 不排課日期 or 休息日 (both blackout kinds, `Status(blackouts=)`,
default `blackouts.load()`) and the weekdays not ticked as 可練日 in 課表偏好 (`plan_prefs.days`,
`Status(prefs=)`: a Fri–Sun runner's weekly Mon–Thu gap is their week, not a break) are a chosen
rest, so a planned gap is still checked and a partly planned one is exempt only when its unplanned
days alone are ≥ 3 (counted, not contiguous). The status card says so (「前一週非計畫停跑 N 天，另 M
天是自己排的休息（不排課日期／休息日／沒勾的可練日）…」) and the week gets
an info note (`guard`'s `step_note` → the week notes, `backend/engine/overview.py:1814`). TSB −30…−20 → hold
(Friel / TrainingPeaks). B2B weekends and the B2B TSB exemption use the block line too.

**Single-run guard** (SP-66, `backend/engine/load_guard.py:107`): no single run longer than
`LONG_CAP` = 1.10 × the longest run of the `LONG_DAYS` = 30 days before (Frandsen 2025, BJSM,
Garmin-RUNSAFE cohort — peer-reviewed). The generator caps the planned long day with it
(`cap_long`, `backend/engine/load_guard.py:383`, rounded down to 5 min) and says so in a week note
(`cap_note`, src `long_cap`: 「長跑縮短為 N 分：過去 30 天最長一次 …」). A run done over the line in the
last `LONG_RECENT_DAYS` = 7 days (推估; `session_spikes`, effort-km else minutes) only turns the status
card's 「最長單次」 to watch (「單次跑太長：比前 30 天最長一次多 x%（> 10%）」,
`backend/engine/status.py:729`) — a reminder, never a block on the intervals.

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
| 邊界 | every rep in band but time in the target zone < 85 % of the plan (`TIZ_GOAL`, 推估; interval_eval's verdict) | the same step again |
| 未適應（疲勞保險） | SP-110, all three: the session's 60-s HR drop (median of its reps) ≥ `FOR_FAST` = 25 % faster than the median of the same-spec sessions (rep length, rest, rest mode — `spec_key`) of the `FOR_DAYS` = 56 before it, with ≥ `FOR_MIN_N` = 5 of them; the same-spec session before it was that fast too (`FOR_STREAK` = 2); and its power missed (`interval_outcome`'s `power_ok` False) | no step forward; the note 「心率恢復變快但功率沒到，可能累積疲勞：…」 (`fatigue_check`, `backend/engine/quality_gate.py:863`); the indicator raises good / info to watch with 「這週間歇不往上加；睡眠、輕鬆跑先顧好…」. Sources Aubry 2015, Bellenger 2016, Buchheit 2014; the 25 % and 2-in-a-row are 推估 (`backend/engine/quality_gate.py:771`) |
| 達標 | otherwise | the next ladder step |
| 無法判定 | no bouts or no CP (the old rule counted it as 達標) | the same step again — progress only on 達標 (`unsourced-rules.md` §B4) |

Thresholds: 98 % in-band, 50 % AeT-at-60 s and the 5 % last-rep fade are 推估 (doc §4.2–4.3).
In-band, last-rep fade and the TIZ goal are the defaults; the values in effect are fitted per
athlete (SP-69, `engine/interval_calib.py`), refitted at most every `CALIB_EVERY_DAYS` = 7 days
after a sync (SP-320 ④, `calibrate.run_safe`; the manual `POST /calib/run` always runs).
RPE is not recorded, so the RPE rows are skipped. Reps come from power (`count_reps`, or
`detect_efforts`). A session judged by: the structure the user edited in the 課表 editor
(`steps_spec`: its own reps / band, counted only when equivalent to the rung; a 「負荷」 main-set
step counts by its estimated time TSS ÷ (IF² × 100) h at the band's middle — `load_work_s`,
`backend/engine/workout_steps.py:1415`, SP-38, 推估), else the stored
variant, else the planned title. An unplanned interval run is neutral.

### The ladder: two tracks, Zone 3 and Zone 5 (SP-31, 2026-10-04)

Zone 3 (有氧間歇／節奏, reps 12–30 min) and Zone 5 (VO2max) are two independent tracks, each
with its own ladder, dose step and 達標 count (`dose_tracks`: `gate["dose"]["z3"]` /
`["z5"]`; the top-level `step` / `adjust` / `note` keep the Zone 3 track's values for older
readers). A history row's track is `row_track` (stored rung / variant / edited structure /
title; a run without a plan row by its stimulus). Zone 3 keeps being scheduled after Zone 5
opens (coach-schools-zones-periodization.md R2: no school's norm is two sessions of one
intensity). Each rung is the canonical variant of `backend/engine/interval_library.py`; rests
< 2–3 min are walks (Buchheit & Laursen 2013).

| track | step | rung | standard session | target | source |
|---|---|---|---|---|---|
| Zone 3 | 0 | A1 `a1` | 2×15 分, rest 3 jog | 88–95 % CP | Uphill Athlete Zone 3 (15–60 min, 4:1–5:1) |
| Zone 3 | 1 | A2 `a2` | 3×12 分, rest 3 jog | 88–95 % CP | UA Zone 3; Pfitzinger LT 20 → 35–45 min |
| Zone 3 | 2 | A3 `a3` | 2×20 分, rest 4 jog | 88–95 % CP | Friel Base 2 2×20 Zone 3 |
| Zone 3 | 3 | A4 `a4` | 連續 30 分 | 88–92 % CP | Koop SteadyStateRun; Pfitzinger LT |
| Zone 3 | after | A3, A4, T+ (`tp` 3×7 分 at 97–100 % CP) rotating (推估) | | | Palladino near-threshold |
| Zone 5 | 0 | V1 `z5a` | 5×2 分, rest 2 walk | 106–112 % CP | 台灣教練: reps ≥ 2 min; Buchheit & Laursen 2013 |
| Zone 5 | 1 | V2 `z5b` | 4×3 分, rest 3 jog | 105–110 % CP | Koop / CTS; Palladino MAP |
| Zone 5 | 2 | V3 `z5c` | 5×3 分, rest 2.5 walk | 105–110 % CP | Palladino; Wen 2019 |
| Zone 5 | 3 | V4 `z5d` | 4×4 分, rest 3 jog | 104–108 % CP | Helgerud 2007 |
| Zone 5 | after | V3, V4 rotating (推估) | | | |

- **巡航版 T1–T3** (`z3a` 3×6′ / `z3b` 3×8′ / `z3c` 2×12′, 90–95 % CP): the old Zone 3 rungs, kept
  as the Zone 3 track's weekday-cap / low-volume fallback (`interval_library.PREV_RUNG`: A1 → T3)
  and for stored sessions. A stored row of an old rung still resolves: it is judged against its
  own variant and its 達標 counts in `met`, but it doesn't move the A rung.
- **Zone 3 volume** (`z3_budget_min`): the session's time in zone ≤ 10 % of the week's planned
  hours (`Z3_SHARE_MAX`; coach, the book not verified: Daniels' T ≤ 10 % is per **session** and by
  **mileage** — second-hand summaries — the app applies it per **week** and by **time**), 5 % for the track's first session (UA: Zone 3
  starts at ~5 %). Over it, `cruise_for` picks the 巡航版 of the same position (A1 → T1, A2 → T2,
  A3 / A4 → T3, stepping down to fit; T1 the floor); it is stored under the A rung (equiv) and
  counts, with a 「本週 x h：3 區上限 …→ 巡航版」 note.
- **Weekday cap** (owner 2026-10-04, symmetric with the volume cap): a Zone 3 rung the day's cap
  can't fit as the standard or an equivalent (and no other day takes it) becomes the 巡航版 of
  the same position that fits (`overview._cruise_for_cap`: A1 → T1, A2 → T2, A3 / A4 → T3,
  stepping down), stored under the A rung (equiv) — 達標 moves the ladder; note 「平日上限 N 分放不下
  … → 巡航版 …（算這一階）」. Only when no 巡航版 fits does fit's own fallback (縮量版 / the step
  before, not counted) apply.
- **Week total** (`QUALITY_SHARE_MAX = 0.20`, 推估 — Seiler 2010's 80/20 counts **sessions**, not time; Koop): Zone 3 + Zone 5 time in
  zone ≤ 20 % of the planned running time. `overview.quality_sessions` builds Zone 5 first and
  gives Zone 3 what is left (a smaller 巡航版); a session still over is cut to fewer reps as a
  縮量版 (`_shorten`, floor `MIN_REPS`, no progress) or, when it can't be cut, kept with a note —
  never dropped silently. 課表偏好's repeat of a lone track (`plan_prefs.shape`) is skipped when
  the two would pass it. The user's own RPE ≥ 7 技術地形 sessions of the week come off the 20 %
  first (`reserved`, SP-74 follow-up, overview.spec.md): then an interval whose 縮量版 floor still
  doesn't fit is left out (a note) rather than kept over.
- **How many a week**: 課表偏好 `quality_per_week = 2` → one Zone 3 + one Zone 5 when both are
  open (`week_decision(n=2)`; the base phase's guardrail mode still caps it at 1); only one track
  open → for Zone 3 (Zone 5 closed or held by a guardrail; owner 2026-10-04) a **different second
  Zone 3 session**, not a copy: `week_decision` adds a `cruise` item and
  `overview._second_z3` builds a 巡航版 interval (T1–T3 rows with reps ≥ 6 min,
  `CRUISE_REP_MIN_S`; research R1 巡航 6–15′) as close to the first session's time in zone as
  the Zone 3 cap (both sessions ≤ 10 % of the week), the week's interval total and the day's cap
  allow, never the first one's structure; it doesn't move the rung (its 達標 counts in `met`);
  a `z3` note says so, or that it didn't fit (本週排 1 堂). A lone Zone 5 track is still repeated
  by `plan_prefs.shape`. One a week with both open → by the
  A race (`track_ratio`, 推估): the next A race a road race ≤ 10 km → 3 區 : 5 區 = 1:1, else
  (half marathon or longer, trail / 百岳, no A race) 2:1 — Zone 3 the first weeks of each cycle,
  deterministic by the week's Monday.
- **專項期 / 減量期** run the same pick (`overview.quality_sessions`). 專項期 (SP-75): both tracks keep
  climbing their ladders — 越野 the rung's uphill version (`interval_library.fit(hill=True)`), 路跑 on
  the flat; the old fixed 2×15′ / 5×4′ (`ROAD_SPECIFIC_Q`, `TRAIL_SPECIFIC_Z5`) are no longer generated.
  It has two halves (`quality_gate.track_ratio` / `week_ratio`, 推估): 前段 賽前第 10–7 週 and 後段 第 6–3
  週, the 1-a-week ratio by the race (`race_class`, `SPEC_RATIO`): 越野／百岳 < 4 h (`planning.event_size`)
  1:1 → 1:1; ≥ 4 h or multi-day 2:1 → Zone 3 only (no Zone 5 kept; not a lock); 路跑 ≤ 5 km 1:1 → 1:2,
  ≤ 10 km 1:1 → 1:1 with T+ as the Zone 3 session, half 2:1 → 3:1 with T+ in weeks 4–3, marathon 2:1 → 3:1
  (T+ only while Zone 5 is open; it doesn't move the rung). The 後段's turns run in two-week blocks from
  week 6 (weeks 5 / 3 are SP-97 recovery weeks); `week_decision`'s `seg_note` says what changed
  (a week note, src `specific`) — only in a 後段 week (SP-352, `backend/engine/quality_gate.py:2601-2612`):
  a manual 專項期 without an A race, a week after the race and the 前段 have no half, so the default
  ratio (2:1) picks the track, the Zone 3 ladder's own T+ maintenance turn is scheduled as is, no note. 減量期 Zone 3 = 有氧間歇（巡航）2×8′ (`TAPER_Z3`,
  88–95 % CP; Bosquet 2007, Daniels Phase IV), Zone 5 or no track open = the 4×3′ short intensity.
- At most 2 Zone 5 sessions a week, ≥ 2 days apart (台灣教練). A ramp-week session (`SUB`,
  「有氧間歇（巡航）3×6 分（只排閾值）」, only when Zone 3 is open) and the recovery fartlek are neutral.
- `dose_spec(step, z5_open)` stays as the legacy single-ladder reading; the plan reads
  `z3_spec` / `z5_spec`. `ladder_pick` / `rung_now` pick the rung of the session's own track.

### Two gates and the Zone 5 lifecycle (`backend/engine/base_check.py`)

- **Zone 3** (`z3_gate`, SP-31; the owner's rule 2026-10-04) opens on any one of: (a)
  consistency — 4 complete weeks of actual training (imported history counts, whatever the
  phase label) with ≥ 3 runs every week and no ≥ 7-day stretch without running
  (`Z3_WEEKS_NEED`, `Z3_RUNS_PER_WEEK`, `Z3_MAX_GAP_DAYS`, all 推估; `z3_consistency`) — with the
  re-lock days below these are the defaults of 設定 → 進階設定 (SP-295: `advanced_params` items
  `z3_unlock_weeks` 1–16, `z3_unlock_runs_per_week` 1–7, `z3_unlock_max_gap_days` 1–21,
  `z3_relock_days` 7–120, whole numbers, manual only; `quality_gate.z3_rule()` reads the ones in
  effect, the gate's texts and the 間歇門檻 hover quote them tagged 「預設，推估」 or 「手動」; a change
  is in the status / plan cache keys (`z3_rule_stamp`) and starts a background `plan_auto` run,
  trigger `settings`); (b) the
  90-min drift test < 10 %; (c) a measured UA gap ≤ 10 %. Also open: mode `none`, the chosen
  method unlocked, an aerobic-base confirmation of the Zone 5 process, the re-entry rule asking
  for Zone 3, a Zone 3 session 達標 in the 8-week history. Once met it stays open; a break of
  ≥ 21 days without running (`Z3_RELOCK_DAYS`, 推估; Coyle 1984 VO2max −7 % at 21 days,
  detraining.md §1) re-locks it — only what comes after the break counts — and so does an A
  race (below). The consistency rule (with the 進階設定 numbers in effect) is also what 資料等級 2
  reads (`data_level.level` over 365 days, SP-291, overview.spec.md); the gate itself is unchanged. Breaks of 6–20 days
  get the re-entry block only. Post-race 恢復期 / 轉換期 days (`z3_consistency(skip=)`,
  `planning.post_race_days`; SP-73, owner 2026-10-05) are no running gap: they count neither toward the 7-day stretch nor
  the 21-day re-lock (a 3–4-week transition alone never re-locks — chosen: the transition is a
  planned easy block, the fitness loss Coyle measured is for full inactivity), and a week touching
  the transition that fails on its own is see-through (neither counts nor breaks the 4 weeks; one
  with ≥ 3 runs counts as usual). No low-intensity-share condition. Until it opens the base phase
  has no interval (easy running + strides); a projected week opens once the streak would reach 4
  weeks. Zone 3 and Zone 5 are **independent gates** (SP-39): the Zone 5 gate is below.
- **A 賽後重新打底** (SP-116; `base_check.a_race_rebase`, `backend/engine/base_check.py:546`): after
  an A race (priority A only, never B / C, the latest one within `REBASE_SCAN_DAYS` = 400 days),
  `from` = the first day after its 恢復期 / 轉換期 / 回量期 (`POST_RACE_KINDS`); none when no 基礎期
  follows them (推估: nothing to rebuild in). From then on only evidence dated on or after `from`
  counts: the Zone 3 gate (`z3_gate`, `backend/engine/quality_gate.py:1489`) is opened again only by
  a drift run, an AeT test or a 基礎期 start from that day (`_rebased`) — the consistency weeks and
  earlier 達標 don't — and `z3_open_on` never opens a projected week during it; an unlocked method
  re-locks unless its own evidence is that recent; Zone 5 (`z5_status`) drops AeT confirmations
  dated before `from`. `aet_test_reason` gets the code `rebase` (`rebase_reason`): the method's own
  test (`REBASE_TEST`: xu_drift → the 90-min test, friel_drift → Friel, ua_gap → an AeT test with a
  measured LTHR, plateau / weeks → wait), action 「{d} 起{test}；通過前只排輕鬆跑、長跑和加速跑」 (the
  gate reads 「3 區還沒解鎖：A 賽「…」後重新打底，…」). Mode `none` is unaffected. Source: 徐國峰's
  「用新的 E 配速重新打底」 (`SRC_REBASE`, `backend/engine/base_check.py:113`; coach-level, no trial).
- **Guardrails per track** (`guard` → `guard_blocks`): CTL ramp at the block line, a > 20 % running-time step and the
  injury pause block both tracks; the low-intensity share < 75 % blocks Zone 5 only — for Zone 3
  it is a warning note 「輕鬆跑心率偏高：…（底線 75%、基礎期目標 ≥ 90%）」 (the AeT is often
  estimated, climbs inflate HR). SP-39 applies the same reasoning to Zone 5 **only when the AeT
  in effect is not tested** (`aet_info["tested"]` false: no plan row, or an applied estimate —
  the share is computed against that AeT and is noisy): then it is a warning for both tracks
  (「…AeT 是估計值、占比不準，只是提醒：3 區、5 區照排」, `guard(aet_tested=False)`); with a tested
  AeT the 75 % floor keeps blocking Zone 5. 75 % is the floor, the base phase's ≥ 90 % a target.
  專項期: intensity bad keeps Zone 5 out (tested AeT; a warning otherwise), drift bad stops both,
  and this week's load guardrails apply to both tracks as in the base phase (owner 2026-10-04: no
  school exempts the specific phase — Friel ramp 5–8, Nielsen 2014 / Damsted 2019; unsourced-rules.md
  B2): CTL ramp at `load_guard`'s block line or a > `STEP_BLOCK` volume step → no interval (note),
  at the watch line → the threshold-only `SUB` session. 減量期, race / recovery weeks, the re-entry block
  (mode `reentry`) and projected weeks stay exempt (the accepted-B2B TSB exemption is unchanged).
- **Why no Zone 3 this week**: `week_decision` returns `z3_note` (the gate, a guardrail, the
  1-a-week turn, the recovery week); `week_plan` shows it as a note (`src: z3`), the share
  warning as `src: intensity`, the volume / total caps as `src: z3` / `quality_share`.
- **Zone 5** (SP-39, 2026-10-04; coach-schools-zones-periodization.md R3) — its own gate, the one
  flag (`quality_gate.z5_track` → `gate["z5_gate"]`) that `week_decision`, the flow, the template
  推薦 and the change log all read:
  1. **a measured AeT** (`base_check.z5_status`, through `quality_gate._z5`): a tested AeT
     (`aet_tested`: the latest plan aethr row on or before the day whose method is not
     `estimate` — an estimate applied later doesn't undo it) **and** a measured LTHR (`lthr_info
     ["measured"]`: a plan row from a test / race / lab / by hand, or the athlete's own WKO5
     setting — not the WKO5 default, not an applied estimate) with LTHR ÷ AeT − 1 ≤ 10 %
     (`aet_ua_gap`, dated the later of the two rows; `z5_ua_gap`). **No age limit** (owner
     2026-10-05, replacing SP-39's 12-week `LTHR_FRESH_DAYS`: zones-and-thresholds.md §2.5 finds no
     direct evidence for a fixed retest period; unsourced-rules.md B3 moved the AeT to event
     triggers too) — the measured LTHR stays valid **unless an event invalidates it**
     (`lthr_invalid`, `backend/engine/quality_gate.py:1693`, `gate["lthr"]["invalid"]`): (a) a
     running break ≥ 4 weeks after the test (`reentry.find_all`, a block with `reconfirm`;
     detraining.md); (b) evidence since the test (`threshold_confidence.lthr_evidence`, level weak
     or above: a cool long effort above LTHR, a 40–60-min race < 95 % LTHR, the CP-band
     cross-check, CP changed > 5 % since the LTHR date — never its age, never an accepted non-test
     source; a hot long effort is a hint only); (c) the AeT aggregate reports `shift` or `moved`
     (`_aet_shift`, the same rules as `aet_test_reason`). A dateless LTHR counts every event in
     reach. The time since the test is only threshold_confidence's weak reminder (hint, no
     re-lock). The paths are re-read every day; when an event invalidates the LTHR the flow item
     names it (「重測 1 次 30 分鐘 LTHR（LTHR 測完後停跑 N 天…）」) and offers the 30-min LTHR test;
     **or** ≥ 60 min near the
     tested AeT with first vs second half drift < 5 % (`aet_friel_drift`, Friel) — a run whose
     drift window has bad HR (SP-266: spike / step / flat seconds > 3 % of the window or one step that
     doesn't come back, both 推估; a cadence lock is shown only; `workout_review.hr_downgrade`,
     `backend/engine/workout_review.py:661`) is the reference tier (`hr_ref`, `ok` false) and doesn't
     pass, `drift_agg.aet_points` skips it, and its AeT test gets no 「套用」. **The 90-min
     test is not an AeT test** (it yields no AeT number) — it opens Zone 3 only. Modes: `auto`,
     `xu_drift`, `plateau`, `weeks` use both AeT paths (their own method opens Zone 3 only);
     `ua_gap` / `friel_drift` their own; `none` = no gate (Seiler).
  2. **the soft 「3 區先」** (推估: UA 「Start with Zone 3」, Pfitzinger LT before VO2max; Daniels /
     CTS the other way, no RCT): ≥ `Z5_Z3_NEED` = 2 Zone 3 sessions done (達標 or not; ladder,
     巡航版, T+, the ramp week's 閾值 — not the recovery fartlek nor an unplanned hard run) in the
     last `Z5_Z3_DAYS` = 42 days (`z3_recent`, `gate["z3_recent"]`), **or** the Zone 5 track
     already under way (a step or a session done in the 8 weeks). It replaces the hard 「3 堂 3
     區達標」 (`Z3_MET_FOR_Z5`, removed). A projected week counts its own 6-week window
     (`steps["z3_dates"]`: the gate's dates plus each planned / projected Zone 3 week). A gate
     stored before SP-39 (no `z3_recent`) counts its Zone 3 達標.
  The old `xu_signals` path and its mode were removed (aerobic-base-readiness.md); a stored
  `xu_signals` reads as `auto` (`plan_prefs.from_settings`), new writes are rejected.
- **徐國峰's 90-min test** (`xu_run`): ≥ 90 min, flat (not trail, < 20 m/km), every
  stop ≤ 30 s, HR in Zone 1 (the app's easy rule: avg ≤ AeT + 3, ≤ 10 % above — mapping his
  E zone to "below AeT" is 推估), (HR@90′ − HR@10′) / HR@10′ < 10 % — his own comparison, not
  drift_of's halves (blog 2016-12). Any qualifying run counts (it can be the weekend long run).
  SP-275: a **scheduled** test (`is_scheduled_xu`: the plan's test session done by the run, or
  the test's title) is run at a pace / power (SP-274), so Zone 1 doesn't apply; instead
  `output_hold`: minutes 80–90 not > 5 % slower than 10–20 (power when there is power; 推估, UA's
  5 %) — 「後段放慢了 N%：飄移會偏小，下次配速固定」; without speed or power only the HR, with
  「沒辦法確認配速有沒有維持」. The test's own review (`aet_test.analyze_xu`) applies the same check.
  A passive long run keeps the Zone 1 rule and has no pace check.
  **Heat bands** (2026-10-02): ≤ 25 °C (台灣教練) is advice in the session text, no longer a
  refusal. The run carries its temperature band; a pass in heat counts (heat only inflates the
  drift — conservative), a fail in heat is marked 「熱環境，結果可能偏高」 (`quality_gate.heat_suffix`;
  the same for the Friel drift path and the AeT test).
- **Injury pause**: an open 傷病紀錄 with 「受傷期間暫停強度課」 (`injuries.pause_reason`) blocks
  intervals in `guard` first, every phase, until it is resolved.
- **Maintenance** (weekly, no expiry): Zone 1 time < 2/3 of the level at confirmation (mean of
  the 4 weeks up to it) for 3 complete weeks in a row → pause Zone 5 until the next
  confirmation (Hickson 1982; 3 weeks 推估; recovery / taper / event / transition / 回量期 weeks and
  weeks touching a break don't count — `base_check._skip_week`, `backend/engine/base_check.py:497`). The late long-run HR / pace check no longer pauses Zone 5; it
  survives only as the re-entry rule's post-break drift check (`long_check`, 14–28 days off). A
  break ≥ 6 days is the re-entry rule below. A paused state says why in `z5["pause"]`
  (`kind`: `z1` / `reentry_z3` with done / need / `drift_check`).
- **Tracker** (`quality_gate.z5_card`, SP-39): `base` = the Zone 5 AeT tests 「實測 AeT（二選一，
  做了且達標）」 (✓ / ✕ / – with the current value and what is `missing`: aet / lthr / gap; after a Z1
  pause or a ≥ 4-week break only results dated after it count); `z3` = the soft condition
  (done / need / under_way); `z5_gate` = `z5_track`; `open` = the Zone 5 track; `steps` = ① 實測
  AeT ② 近 6 週 3 區 n/2 ③ 5 區開放; `next` = the one 「還缺：…」 line. `flow`
  (`quality_gate.z5_flow`, presentation only) = **two independent, parallel tracks** `tracks:
  [z3, z5]`, each `{title, open, here, stages}`: 三區軌 = 3 區解鎖 (the Zone 3 gate: its three
  tests until one passes) → 3 區階梯 A1–A4; 五區軌 = 5 區解鎖 (the AeT tests as any-of, the soft
  Zone 3 line, re-entry / maintenance items, notes) → 5 區階梯 V1–V4. Stage status done /
  current / locked; each track its own `here` 「你現在在這裡，下一步」 (`next`, `action`, `also`).
  **安排課表**: every unticked item that is a session or a test carries `action` =
  `schedule_action` → `{type, key, proto, href}`: `variant` (the current rung's canonical variant;
  `?add=<variant key>`), `test` (`?test=aet&proto=ua60|xu90|friel` — the Zone 5 AeT test is
  `AET_TEST_PROTOCOL` = UA 60′, one that yields an AeT number), `template` (the LTHR test
  `?add=lib:friel_lthr30&proto=race`); only the current rung of a ladder gets one. The 課表 page
  (`schedule.html` `openPreset`) opens its new-session dialog with that session preselected
  (`WorkoutEditor.applyKey`, or the dialog's 測試 kind / 方式), the user picks the day and saves
  through `POST /sessions` (a variant keeps its `variant_key` → rung, so it counts on its ladder).
  A 徐國峰 90-min test saved there (by its title, or the template row `lib:xu_e_drift` — `_is_xu90`,
  `backend/api/plan_sessions.py:547`) replaces that day's active long run, as 排入測試 does
  (`_replace_long`, shared; owner 2026-10-04) — for the dialog's own 測試 › 徐國峰 too, which posts
  the same body.
  The 總覽 card and the 基礎期 panel draw it with `static/z5flow.js` (`wko5views.z5_progress`).
- **Re-confirmation**: a new measured AeT (UA gap or Friel) re-confirms. The AeT test is
  scheduled only for a reason (`quality_gate.aet_test_reason`): no interpretable run for ~6
  weeks (推估; UA's own retest is every 4–6 months, not weeks), the aggregated AeT estimate missing or
  SE > 3 bpm, a shift > 5 bpm in the last 6 points (B3), the estimate more than max(SE, 3 bpm)
  from the plan's AeT ("moved": UA — AeT rises toward AnT as the base improves), or after a
  break ≥ 4 weeks. (The passive 90-min re-confirmation that stood in for no_data / se is gone
  with SP-39: the 90-min run no longer confirms Zone 5.)
  ≥ 28 days between tests (推估). No fixed cadence any more (16 weeks, 4–6 weeks: no source).
  An AeT that is only a temporary lower bound never fires shift / moved. There is no
  stable-weekly-volume precondition before a test (a 3-weeks-within-±15 % rule was added and
  removed 2026-10-03: no source).
- **Faster at the same HR**: the easy targets show pace / power at the AeT HR from the median
  EF of the last 6 easy road runs (`base_check.easy_targets`, 推估); the AeT HR is unchanged and
  needs no retest. CP drives the Zone 3 / 5 power targets (the CP-test track is unchanged).
- **History** (`quality_gate.z5_history`): the lifecycle replayed on every day of a range with
  the inputs evaluate() would have had that day (the plan's AeT / LTHR in effect, `reentry.find`
  on that day, the interval sessions of the 8 weeks before it) through the same `_z5`, so the
  last day equals the gate. Shown in 基礎期 (view kind `z5gate`) as the collapsed 歷程 list under the stage flow.
- **Visibility**: the gate hover shows the Zone 5 track (`z5_track` text: 「Zone 5：已解鎖（…）／未解鎖
  （AeT 已通過，還差 3 區：…）」) or the AeT state 「Zone 5：未確認／已確認／暫停／恢復期」, and
  「建議測試：…」.

### The AeT test protocol (`plan.prefs.aet_test_protocol`, `backend/engine/aet_test.py`)

| key | length | terrain | held | judged | source |
|---|---|---|---|---|---|
| `auto` | the standard `xu90`; `ua40` when the long-day cap < 90 min | | | | justification: `aerobic-base-readiness.md` §6.3 |
| `xu90` | 10 + 80 = 90 min, on the weekend long day in place of the long run | flat | E pace | HR@10′ vs HR@90′ < 10 % (base check, no AeT number) | 徐國峰部落格 2016-12（有氧基礎檢測） |
| `ua60` | 15 + 60 + 5 | treadmill 2–3 % / flat loop | power | halves, < 3.5 / 3.5–5 / > 5 % | Uphill Athlete |
| `ua40` | 10 + 40 | same | power | same | Uphill Athlete ("If you only have 40 minutes") |
| `evoke60` | 10 + 60 + 5 | treadmill 2 % / flat loop, no out-and-back | speed (power) | halves, > 5 % = above AeT | Evoke |
| `friel` | 10 + 60 + 5 | steady flat | at AeT HR | halves < 5 / 5–10 / > 10 % | Friel (TrainingPeaks) |

The protocol drives the session text (「氣溫 25 °C 以下時開始（熱會讓心率偏高、飄移失真）」,
台灣教練 + Lafrenz 2008), the COROS steps (friel: an HR-capped main block; UA / Evoke: a power
range; xu90, SP-274: `aet_test.xu_target` — the E pace ± 3 % (around the middle of the E
range) of a confirmed race result from the last 180 days (`engine/e_pace.py`, SP-276; Daniels'
table is the reference; an older race is not used — the race is the newest confirmed road row of
the shared list `athlete.race_results`, SP-290: the questionnaire's, the 設定 block's entries and
the activities confirmed with 「用這場」; SP-276's own single `athlete.race_result` is migrated into
the list once at start-up and retired, owner 2026-10-06), else 75–80 % of a tested CP
(`aet_test.cp_tested`: the CP row in effect is not marked `cp_manual` — 設定 marks a CP typed by
hand; a test result or a legacy row saved before the marker counts; Palladino 1C), else no target and the talk test (owner 2026-10-06); never an HR cap on the main block, the warm-up keeps the easy-run cap;
the builders read the numbers back from the stored target, `xu_main_target`), the placement (xu90 on the weekend; the others by `aet_test_days`) and the
analysis (`analyze_workout`: warm-up cut, window and judging rule by the title's protocol).
The analysis uses VI ≤ 1.04 (drift v2) instead of the old 30-s CV. Heat is a band on the
result, not a refusal (`aet_test._tag_heat`, `heat_line`): a pass in heat still counts. Every protocol is ≥ 40
min of test, so all are the strict tier. MAF is not a drift test and is not offered.
The retest line (SP-279, `backend/engine/aet_test.py:404-405`; the > 5 % line `_lower_line`, `backend/engine/aet_test.py:411`): UA < 3.5 % → 「下次起始心率 +5 bpm … 再測一次」
(UA's own number); > 5 % → 「下次起始心率降 5 bpm（約 N；5 bpm 是推估）再測一次」 (`LOWER_BPM`,
`backend/engine/aet_test.py:80`: UA says "lower", Evoke "a slower pace", neither a number). A test
whose HR is bad (SP-266, `hr_ref`) shows the values but no 「套用」 (`aethr_suggest` none).

### 停訓後的恢復期 (`backend/engine/reentry.py`; `docs/research/detraining.md`)

A break = consecutive days without a run (planned: a 不排課日期 range ≥ 6 days with no run
inside, scheduled ahead; unplanned: from the runs — the current gap counts as a break
returning today; the next automatic run applies it and logs it). The block lasts as long as
the break (Daniels table 9.2, coach): the week's volume = the mean of the 4 weeks before the
break (推估) × the block's % per day (break days 0).

| break | block | Zone 3 / 5 | targets |
|---|---|---|---|
| 1–5 d | none, back to 100 %, no make-up | as before | × 1 |
| 6–13 d | first half 50 %, second half 75 %; long ≤ 90 min (推估) | none inside; Z5 after 1 Z3 session (推估 count; 台灣教練 Z3 first) | × FVDOT |
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

**傷停** (2026-10-02, `backend/engine/injuries.py`): a break that overlaps an injury event of the
傷病紀錄 (category `injury` only, `injuries.overlapping`, `backend/engine/injuries.py:970`) is a
傷停 (the text names the area and the event). With `injury.reentry_step_up` (default on) the block
is the next category's (6–13 → 14, 14–28 → 29, 29–56 → 57 days; `reentry.STEP_UP_MIN`, 推估:
the tissue has to re-adapt too); FVDOT stays the real break's.

**生病** (SP-117, owner 2026-10-05; `docs/research/detraining.md` §6.2): an illness lives in the same
傷病紀錄 (`injury_events.category = "illness"`, no body area, `illness` = `cold` 輕微感冒 / `fever`
發燒或全身症狀; onset = the first day of symptoms, resolved = the first symptom-free day). It is
left out of the injury analysis and never takes a pain mark. `injuries.illness_rule`
(`backend/engine/injuries.py:569`) says what a day allows, the strictest when several:

| rule | when | this week's plan (`overview.illness_apply`, `backend/engine/overview.py:2411`) | source |
|---|---|---|---|
| `off` | fever symptoms, and the `FEVER_WAIT_DAYS` = 1 after them | no run, no strength | Wallenfels (≥ 1 day after below-the-neck symptoms), Watson 24–48 h — coach-level |
| `z1` | cold symptoms | every run → 「恢復跑（心率 1 區）」 ≤ `ILL_RUN_MAX_MIN` = 45 min (推估), flat; no interval, test, long run or strides | Kaulback 2023 (IOC consensus review), Wallenfels |
| `first` | the first day a run is allowed after a fever | the first run → 「恢復跑（發燒後第一次）」 ≤ `FIRST_RUN_MAX_MIN` = 30 min (推估) | Wallenfels / Watson |

While a rule applies the intervals pause on their own (`injuries.pause_reason`, no
「受傷期間暫停強度課」 flag needed; text 「傷病紀錄 #id（label）：…」). The week note has src `illness`.
Only this week is changed — the projected weeks are not. After the illness, the break (last run →
first run back) takes reentry's block of its length with the text 「生病停跑 N 天（{type}，傷病紀錄 #id）」
or 「傷停＋生病停跑 N 天（…）」 when an injury overlaps too (`reentry.text_of`,
`backend/engine/reentry.py:173`); an illness never takes the injury step-up (推估: that is for tissue
healing).

**傷別、疼痛燈號、走跑、提議好了** (SP-269–273, 2026-10-06; `docs/research/injury-graded-return.md` §4.6,
§6.1). All only cut — `load_guard` is untouched; edited / custom sessions are left alone by reconcile as
always, and the change log / undo cover them like any generated change.

- **傷別** (`injury_events.condition`, optional: 跟腱 / 足底筋膜 / 髂脛束 / 膝前痛 / 其他, each tied to one
  area; `injuries.monitor`): the pain-monitoring text — 跟腱 Silbernagel 2007, 膝前痛 Esculier 2016
  (≤ 2/10, back within 60 min), the rest and none = the general rules (Ohio State Wexner).
- **Avoided session types** (`injuries.condition_rule` / `condition_week`, `overview.condition_apply`;
  week_plan and the projection): while 膝前痛 / 髂脛束 is open no downhill session, 技術地形 or 長爬坡反覆
  and the trail long run goes flat; while 跟腱 is open no 長爬坡反覆 / ME, 陡坡健走 or strides (推估). The
  downhill / technical / steep_hill `week_context` ask the rule; what is left becomes a flat easy run of
  the same minutes. The rule ends the day the event resolves.
- **Pain light** (`activity_tags.pain_score` 0–10; `injuries.light`, `overview.light_apply`; this week
  only): green / yellow / red from the last marked run (limits 跟腱 5, 膝前痛 2, others relative; +2 =
  yellow; 中斷, ≥ 7, two yellows or severity 重 = red). Yellow (applied on its own — a reduction): no
  interval, long run × 0.75, the rest of the week ≤ last week's actual minutes. Red: no run, strength
  kept; the box offers 不排課日期 (`injury_rest`). A red light also takes the runs out of every projected
  week (`projection.project_weeks`, hours 0; owner's decision, SP-273, 2026-10-06) so nothing is pushed
  to the watch ahead.
- **Walk-run** (`injuries.return_state`, `overview.walkrun_apply`; projection carries the rest): after
  red, a ≥ 30-min walk marked 沒痛／痠 or 「可以開始走跑」 (`injury_events.walkrun_from`) starts walk 4/run 1 →
  1/4 (3 each) and 30 min × 3, every other day; 痛 repeats, 中斷 = red again. `reentry.find_all` starts the
  Daniels block the day after the last continuous 30; no block while still red / in the stages. A run
  marked 沒痛 while red (owner's decision, SP-273, 2026-10-06) skips the walk check and the stages: green
  at once, the Daniels block starting on that run (episode `skipped`).
- **「好了」 proposed** (`injuries.done_check`, suggestion `injury_done`): last 7 days ≥ 75 % of the 4
  weeks before the onset and the last 3 runs 沒痛／痠 over ≥ 14 days; 「好了」 resolves it today, 「還沒」
  hides it 7 days. Never automatic.

## Big changes (held for approval; thresholds 推估)

- A week's planned TSS rises more than 20 % above the stored (last pushed) version.
  Reductions are the safe direction and apply on their own — e.g. the strength sessions taken out
  of an A event's last 14 days (SP-86, overview.spec.md).
- A long, quality or test session is removed within 14 days before an A race.
- Within those 14 days, a hard session moved or stepped down by rule D′'s self-rating
  (`rpe`, SP-231) or by rule D's 太強 tier (`too_hard`, SP-301).
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
`planned_spec` resolves it. Since SP-79 a title may come in either spelling — the raw stored one
(「閾值 3×8 分」) or today's (「有氧間歇（巡航）3×8 分」, `plan_store.to_dict`): `planned_spec`
(`backend/engine/quality_gate.py:1074`), `spec_by_title` (`backend/engine/quality_gate.py:517`) and
`adapt._prev_row` match through `interval_library.renamed`; the old ladder's titles stay neutral in
both spellings (`LEGACY_ANY`, `backend/engine/quality_gate.py:179`), and `spec_by_title` keeps
returning None for a raw old-ladder title (「VO2max 間歇 4×4 分」 is V4's title today).

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

Since SP-362 B3 the run writes its `applied` / `pending` row under the writer lock with push
`{status: pushing}` (or `held` / `off`) and fills the push outcome in once the push, outside the
lock, is done (`_set_push`, `backend/engine/plan_auto.py:523`; `autoplan.js` shows 「推送到手錶中…」
meanwhile). A change that only the push makes (no session item) gets its `applied` row after the
push, as before. The `pending` row must exist before the lock is released: a page load's
`_ensure` checks it and does not apply a waiting proposal.

Plan-rule changes get their own `applied` rows without sessions (`plan_auto.state_changes`,
state keys `z3` / `z5` / `z5_track` / `reentry` in `plan.auto.state`): the Zone 3 gate opening,
every Zone 5 state change (「Zone 5：未確認 → Zone 5：已確認（…）」), the Zone 5 track unlocking
(`z5_track` text 「Zone 5：已解鎖（…）」) or re-locking (「Zone 5：重新上鎖（…）」; SP-39) and every new re-entry block
(「恢復期：停跑 N 天…（不排課日期，事前排好／從活動資料偵測）」).

**復原** restores the before-state of the affected sessions as the athlete's own
(`edited = true`), so the next run does not redo the change. Sessions the run added are
tombstoned. The window is then re-pushed. A session the user deleted as expired
(`plan_store.USER_DELETED`) stays deleted.

## CP change (2026-10-02)

Power targets are % CP, but COROS running workouts take absolute watts. A run also starts when
the CP in effect (`week_plan` thresholds) differs from `plan.auto.state.cp` (`cp_of`); the first
run only stores the baseline. The upcoming active sessions' watt numbers in target / detail (and
the absolute watt overrides of an editor-saved structure, `workout_steps.rescale_abs_power`) are
rescaled new ÷ old (`rescale_sessions`); the pushed ones go out of date and are re-sent through
`push_window`, also those already on the watch beyond the window. One `applied` row (trigger
`cp_change`) says 「CP old → new W：未來 N 堂課的功率目標已更新並重新推送」 or 「…，待推送」 (push off,
held or failed); each item is 已重新推送 / 待推送 / 只在 app (`_log_cp`). `plan.auto.enabled`
off: nothing (the next enabled run catches up).

API (`backend/api/plan_auto.py`, prefix `/api/v1/overview/plan/auto`):
- `GET ""`: settings, the pending proposal, the last entries and the thresholds
- `PUT /settings`
- `POST /run`: ignores the data stamp; big changes are still held
- `POST /proposal/{id}/approve`
- `POST /proposal/{id}/reject`
- `POST /log/{id}/undo`

UI: `backend/static/autoplan.js` puts the proposal banner (同意 / 拒絕) on the overview and on the
課表 page. The change log (each entry with 復原) shows only on the 課表 page, as a collapsible
「自動調整紀錄」 (`data-log="collapsible"`, collapsed by default, open state per browser); the
overview has `data-log="none"`. The settings are in 課表偏好 (above).

## Safety

- One run at a time, enforced by the shared plan writer lock. Concurrent runs: the second one
  finds the same stamp and does nothing (the state is saved before the lock is released).
- The writer lock covers reconcile + save + the log row + the state only (SP-362 B3,
  `run` / `_run` / `_push_after`, `backend/engine/plan_auto.py:744`, `:780`, `:887`). The push to
  the watch (COROS 20–30 s on the NAS) runs after it under the push lock (`_plock`,
  `backend/api/plan_sessions.py:449`), shared with the page's pushes and SP-358's `_sync_watch`;
  so the 課表 page's GET and the user's edits never wait for COROS. The push re-reads the stored
  plan once it holds the push lock: an edit saved before that goes out as edited; an edit saved
  during the push is re-sent by its own `_sync_watch`, queued on the same lock (owner rule: the
  user's edit wins; the push never writes sessions). 復原 and 拒絕 follow the same split
  (`undo` / `reject`, `:1007`, `:974`): the 課表待確認 copy is deleted from the plan under the
  writer lock and taken off the watch after it (`_remove_notice(remote=…)` / `_remove_remote`,
  `:437`, `:455`).
- **Inputs current inside the lock** (review H1, `_inputs_changed` / `RUN_TRIES`,
  `backend/engine/plan_auto.py:731`, `:728`): the run's inputs are computed before the writer
  lock, so inside it their `inputs_key` (`api/plan_sessions._build_inputs`) is compared with the
  key now (`inputs_key_now`). Different — a 不排課日期 / 休息日 / 課表偏好 / accepted B2B saved
  meanwhile, or a newer dataset generation (COROS then TP, two syncs) — the lock is released, the
  inputs recomputed outside and the run tries again (twice); the last try computes them inside
  the lock, as before B3. Without this the run reconciled the stored plan with the old inputs:
  the user's edit was reverted and pushed, and the data-only stamp kept it lost until the next
  activity; or an older generation was applied after a newer one. Inputs without a key (tests
  that replace the computation) are taken as current.
- **Interrupted push** (review M2, `_push_after` / `_interrupted` / `_push_phase`, `:887`, `:908`,
  `:921`): cancelled (shutdown, the task cancelled while it waits for `_plock` or COROS) or failing
  outside the push itself (a locked DB writing the row), the row's push says `interrupted`
  (shielded, best effort; `autoplan.js` 「推送中斷，下次推送會補上」). A row still `pushing` whose
  push is not running in this process (`_PUSHING`, `:400`) reads as `interrupted` too
  (`entry_dict`, `:403`), so it never says 推送中 forever after a restart. The 課表待確認 copies to
  take off the watch are kept in `plan.auto.state.remote` (`_pending_remote`, `:477`, at most
  `REMOTE_KEEP` 20) under the writer lock and dropped once off (`_forget_remote`, `:488`, briefly
  under the writer lock after the push lock); the next run — a no-op one too — retries the rest.
- **The notice push and approve / reject** (review L6, `:932`): a held run's 課表待確認 is pushed
  before an approve / reject of its proposal can take it off — the run queues on `_plock` right
  after releasing `_wlock` with no await in between and `asyncio.Lock` wakes waiters in order — and
  it is re-read from the store under `_plock` (one already gone is not pushed).
- A push failure is stored in the change-log row. It never raises into the run or the sync.
  A crashed run writes a `failed` row.
- The push sends nothing when every session in the window is already up to date on COROS.
- Tests never start a run on the real DB: `conftest._no_auto_plan_after_sync`. COROS is
  mocked in `backend/tests/test_plan_auto.py` (and `backend/tests/test_plan_auto_cp.py` for the
  CP change), either with a stubbed `push_sessions` or with the scripted FakeHub.

## Known limits

- A 3:1 recovery week inside the Zone 1 maintenance run is not detected from the data (only
  phase-level recovery / taper weeks and break weeks are skipped); three low weeks are needed,
  so one recovery week alone never pauses Zone 5.
- Illness is built (SP-117, 生病 above), but "symptoms come back → step back" (Elliott 2020) is
  not: a relapse is only a new illness event the user enters. The injury hand-off is left to the
  user.
- A 進階設定 change that starts a `settings` run (SP-295) doesn't re-plan the stored sessions until
  the next sync: `_run` returns noop 「沒有新的活動」 whenever the data stamp and the CP are unchanged
  (`backend/engine/plan_auto.py:795`), the stamp (`plan_auto.stamp`) has no settings / Zone 3 rule
  part, and `run_safe` doesn't pass `force`. The page itself re-reads the rule (`z3_rule_stamp` in
  the status / plan cache keys). Likewise a questionnaire save (SP-291's `EX.stamp()` is in the
  plan / status cache keys, not in plan_auto's stamp) starts no run. Behaviour as of 2026-10-08,
  not confirmed as intended.
- The SP-289 LTHR prior (0.90 × max HR) is written into the athlete's `runthr` setting
  (`backend/engine/wko5expr/fitdataset.py:865`, dated the first run). `quality_gate.lthr_info`
  (`backend/engine/quality_gate.py:300`) doesn't tell it apart: without a plan LTHR row, the
  WKO5-setting branch counts any non-default value as `measured`, so the prior could pass as a
  measured LTHR for the Zone 5 UA-gap path (with a tested AeT). Adapt rule D's 94 % LTHR line
  (`workout_review._thresholds`, `backend/engine/workout_review.py:1771`) may also run on the prior. Not verified by a test (2026-10-08).
- Cross-training detection for FVDOT-2 counts non-run endurance sessions ≥ 45 min (推估); the
  doc's FVDOT-2 definition is 未驗證.

- Dataset freshness: the plan reads `charts.data_source`. When that source is `wko5`, FITs from
  a COROS / TP sync reach the plan only after WKO5 imports them. Until then the run is a noop
  for that data.
- Lap-based rep matching is built (`engine/interval_reps.py`, 2026-10-01): laps come from
  the FIT that WKO5 keeps inside the .wko4 (or the FIT itself); a rep = a lap of the planned
  rep length (±5 s / ±3 %) at the planned rest gap from the previous one, so 1-km auto laps
  never chain. Without matching laps, 10-s power ≥ 0.95 × the planned lower bound.
- Moves by `adapt.py` (rule B: a missed session to a free day) don't look at 偏好的星期 or the
  課表偏好 休息日 (SP-82, `pref_days["rest"]`): `open_days` (`backend/engine/adapt.py:225`) checks
  only blocked days and the allowed weekdays, so a session can move onto the preferred rest day;
  only the generator's placement (`plan_prefs.place`) honours them.
- 技術地形課 (SP-74, overview.spec.md): the 專項期 RPE 6–7 session is spaced 48 h from the hard
  days when it is generated, but adapt and reconcile key their hard-day checks on the session
  kind (`quality` / `test` / `long`), not on `workout_templates.session_role`, so a missed
  interval that adapt moves can land next to it. Like the 陡坡健走, once the technical run is done
  (matched as an easy run by the generator) the hook may pick another easy run that week; the
  stored done `tech` row blocks a second one and that easy row is removed.

## Interval library, swaps and tests (2026-10-01, interval-prescription.md)

- Every ladder step of either track (base phase, and the 專項期's ladder sessions) is a
  library variant (`engine/interval_library.py`) fitted to the day's cap by `fit()`: the
  standard full-length session when time allows, then the std / min warm-up, an equivalent
  shorter variant, fewer reps (縮量版 doesn't progress), another day, the rung before. The
  stored row carries `variant_key / rung_key / equiv / swap / swap_reason / variant_reps /
  variant_blocks / variant_adj`; `dose_step` judges by the variant, not the title.
- **Titles name the family** (SP-79, 2026-10-04): a library session is titled
  `<family> <structure>` (`title` / `title_prefix`, `backend/engine/interval_library.py:452`,
  `backend/engine/interval_library.py:433`) — 「有氧間歇 2×15 分」 (A rungs, reps ≥ 15′ or continuous),
  「有氧間歇（巡航）3×8 分」 (T rungs, T+), 「VO2max 間歇 4×3 分」 (V rungs, 30/15), with 上坡 for a hill
  variant — instead of 「閾值／近閾值／VO2max …」; a test holds the prefix to
  `workout_templates.family_of_variant`. Raw zh-TW msgids (a stored title is never translated);
  「N×M 分」 stays for the text parsers. The fixed sessions follow (「有氧間歇 2×15 分（平路）」,
  「VO2max 間歇 5×4 分上坡」, 「有氧間歇（巡航）2×8 分」, the projection's 「有氧間歇（巡航）3×10 分」;
  `plan_prefs._quality_terrain` turns the new hill title flat too). Rung / ladder semantics are
  unchanged. A stored pre-SP-79 title is read in today's words (`interval_library.renamed`,
  `backend/engine/interval_library.py:490`, through `plan_store.display_title`), and reconcile
  renames a generated old title the same way (`_titled`, `backend/engine/reconcile.py:67`), so
  renaming alone is never a 「changed」 session or a change-log entry. Titles that were not the
  generator's (「閾值下 3×8 分」, your own) are left as written. The taper's 「短強度 4×3 分」 became
  「有氧間歇（巡航）4×3 分」 (2026-10-05, 98–102 % CP unchanged) and is mapped the same way.
- A swap from the 換一個 drawer or an editor template is a user edit (`swap = user`,
  `edited`): reconcile rule 3 keeps it, so the automatic run never overrides it. A
  non-equivalent swap is stored with `equiv = false` and doesn't move the ladder.
- Due CP / AeT tests are never generated: `week_plan.test_suggestions` → the overview /
  課表 suggestion with a day picker; the automatic run can't add them either.
- 目標依據 / 目標用: `engine/target_policy.py` decides HR vs power for the push. Auto
  (2026-10-02): road easy / long runs by power (% CP) with the easy-run HR cap (「輕鬆跑上限」,
  「（實測 AeT）」 only when measured); trail easy, trail long days and hikes by HR; intervals and
  3–8 % hill repeats by power. Auto power only when the 一般設定 power source is Stryd (watch
  power only when accepted), else HR.
- The 「休息 60 秒心率降幅 < 20」 line and the fade line in the workout review card
  (`workout_review.interval_lines`) are unchanged. They are display only and no longer drive
  the dose.

## Decisions Log

The owner's calls, gathered from the sections above (each is described there with its code).

| Decision | Choice | Alternatives | Rationale |
|---|---|---|---|
| Zone 3 unlock rule | Any one of: 4 complete weeks with ≥ 3 runs each and no ≥ 7-day gap; the 90-min drift test < 10 %; a measured UA gap ≤ 10 %. Sticky; ≥ 21 days without running re-locks — the consistency and re-lock numbers are defaults the user can change in 進階設定 | A fixed published rule (none found) | The owner's own rule, no external source (SP-31, 2026-10-04); opened to 進階設定 as 推估 defaults (SP-295) |
| Weekday cap on a Zone 3 rung | The rung the day can't fit becomes the 巡航版 of the same position | Drop the session; move it | Symmetric with the volume cap (SP-31 follow-up, 2026-10-04) |
| Measured LTHR for Zone 5's UA path | No age limit; invalidated only by an event (≥ 4-week break, evidence, AeT shift) | A 12-week freshness limit (`LTHR_FRESH_DAYS`, removed) | No direct evidence for a fixed retest period (SP-39 follow-up, 2026-10-05) |
| Short break exemption from the volume step | Counts unplanned days only; 不排課日期 / 休息日 / weekdays not ticked as 可練日 are planned rest | Any 3–5 days without a run | A planned rest pattern is not a break (SP-63 follow-up, 2026-10-04 / 05) |
| Post-race phases | 恢復期 / 轉換期 / 回量期 are planned phases, not a running break, and are skipped as volume baselines | Treat them as a break (re-entry block, Zone 3 re-lock) | SP-73 follow-ups, 2026-10-05 |
| Specific phase guardrails | The load guardrails apply to both tracks in 專項期 too | Exempt the specific phase | No school exempts it (2026-10-04; Friel, Nielsen 2014, Damsted 2019) |
| 徐國峰 90-min test target | E pace ± 3 % from a confirmed race in the last 180 days, else 75–80 % of a tested CP, else the talk test; never an HR cap on the main block | An HR cap; any CP | SP-274 / SP-276, 2026-10-06 |
| 沒痛 while red | Skips the walk check and the walk-run stages: green again | Always walk-run first | SP-273, 2026-10-06 |
| Rule D (easy run too hard) | Two tiers: 偏強 labels only; 太強 (hard class) moves / steps down the next hard session | Adjust on any overshoot; the AeT + 3 HR condition | SP-301, 2026-10-06 |
| Rule E red streak | A foot-sport type mismatch alone (time and TSS measured, not red) is not red | Every red compliance counts | SP-370, 2026-10-08 |

## Open Questions

- [ ] What the plan does when there is no A race for a long time: keep adding CTL, or a 「維持＋輪替重點」 mode (`planning.auto_phases`, `backend/engine/planning.py:799`)（SP-102，決策 Todo）——尚未定案
- [ ] 進階設定: the 重新上鎖 days may not be shorter than the 最長間隔 — block and explain (decided 2026-10-07; `advanced_params` has no such check)（SP-295，Todo）——尚未實作
- [ ] SP-69's other items (CTL target, recovery-week volume, RPE, TL label) are undecided; items 1 / 3 / 5 (per-athlete interval verdict, LTHR retest age, easy-run HR margin) are already in the code (`9ff5e202`, 2026-10-06 row below) while the ticket has no record of them（SP-69，分析 Needs Input）——單待補紀錄
- [ ] A 進階設定 change or a questionnaire save doesn't re-plan stored sessions until the next sync (Known limits) — not confirmed as intended
- [ ] The SP-289 LTHR prior may pass as a measured LTHR for the Zone 5 UA path and rule D's 94 % line (Known limits) — not verified by a test
- [ ] Adapt's rule-B moves ignore the 課表偏好 休息日 (Known limits; SP-82 asked for rest-day preferences)（SP-82，In Review）——待確認是否要擋
- [ ] Cross-training as the alternative session in a 傷停 or the 轉換期, cross-training load toward the running CTL（SP-238，Backlog）——尚未實作
- [ ] Several research docs behind these rules ran out of searches before they were finished（SP-107，分析 Needs Input）

## Change History

| Date | Type | Feature SRS | Summary |
|------|------|-------------|---------|
| 2026-10-08 | fix | SP-362 batch-2 review | H1: `run` re-checks the inputs' key inside `_wlock` and retries (`_inputs_changed`, `RUN_TRIES`, `backend/engine/plan_auto.py:728-778`), so a settings edit saved while it computed is not reverted and an older generation never lands after a newer one; M2: interrupted pushes (`_push_after` / `_interrupted`, `:887`, `:908`; `entry_dict` `:403`) and the pending watch removals in `plan.auto.state.remote` (`:477`, `:488`); L6: the notice re-read under `_plock` (`:932`). Tests in `backend/tests/test_push_outside_lock_sp362.py` (blackout while waiting, two generations, cancel mid-push, reject / approve during the notice push, undo during a run's push) |
| 2026-10-08 | perf | SP-362 B3 | The COROS push left the plan writer lock: `run` computes the inputs before the lock, `_run` (`backend/engine/plan_auto.py:702`) holds `_wlock` for reconcile / save / log row (push `pushing`) / state / week snapshot, `_push_after` (`:802`) pushes the notice or the window under `api/plan_sessions._plock` from the stored plan re-read at that point and fills the row in (`_set_push`, `:477`); `undo` / `reject` push / remove after the lock (`:879`, `:848`); an edit during a push wins (its `_sync_watch` re-sends it); `autoplan.js` 「推送到手錶中…」 for `pushing` (`backend/static/autoplan.js:70`). Tests: `backend/tests/test_push_outside_lock_sp362.py` |
| 2026-10-08 | bugfix | SP-370 follow-up | `compliance.streak_red` (`backend/engine/compliance.py:161-167`) exempts a foot-sport swap only when time and TSS are both measured and not red (the same basis as the ◐ softening); a swap with no TSS counts as red |
| 2026-10-08 | bugfix | SP-370 (owner decision 2026-10-08) | Rule E's red streak (`adapt._red_streak`, `backend/engine/adapt.py:492-499`) counts `compliance.streak_red` (`backend/engine/compliance.py:161-167`): a foot-sport type mismatch alone is not red for the streak; a non-foot sport and time / TSS reds are. The compliance levels themselves: overview.spec.md › Compliance |
| 2026-10-06 | feature | SP-269–273 | 傷別 + per-condition pain text; avoided session types by condition; pain light (yellow / red reactions); walk-run after red before the re-entry block; 「好了」 proposed in the box |
| 2026-10-04 | code-sync | N/A | Domain Model; CP-change re-zone / re-push; push provider + auto push / notify defaults; settings moved to 課表偏好, collapsible log; plan_match / match_only; corrected ladder (T1–T3, V1–V4, T+); TIZ / user-structure judging; heat bands in the gates; injury pause and 傷停 step-up; B2B TSB exception; unplanned hard runs space adapt |
| 2026-10-04 | feature | SP-74 | Known limits for the generated 技術地形 session (adapt's hard-day checks by kind; the hook after it is done) |
| 2026-10-04 | feature | SP-73 | The automatic run sees the 轉換期 after an A race (`plan.prefs.transition_weeks`, `planning.phases`); entering / leaving it is a held phase change |
| 2026-10-04 | feature | SP-63 | One shared ramp rule (`load_guard`): 注意 max(3, 10 %), 擋 min(10, max(5, 15 %)) of CTL₋₇; adapt E and B2B on the block line; startup seed + 28-day skip; volume step on running time vs max(last week, 4-week mean) |
| 2026-10-04 | feature | SP-31 follow-up | 專項期 applies this week's CTL-ramp (5 sub / 8 block) and > 20 % volume-step guardrails to both tracks; taper / race / recovery / re-entry exempt |
| 2026-10-04 | feature | SP-31 follow-up | 2 a week with only Zone 3 open: the second session is a 巡航版 sized to the first (within the 10 % / 20 % / day caps), not a copy |
| 2026-10-04 | feature | SP-31 follow-up | The weekday-cap 巡航版 fallback counts as the Zone 3 rung (same rule as the volume cap) |
| 2026-10-04 | feature | SP-39 | Zone 3 and Zone 5 independent gates: Zone 5 needs a measured AeT (tested AeT + measured LTHR, gap ≤ 10 %, or Friel < 5 % at the tested AeT; the 90-min test and plateau / weeks open Zone 3 only) and the soft 「近 6 週 ≥ 2 堂 3 區」 (`Z5_Z3_NEED` / `Z5_Z3_DAYS`, 推估; replaces `Z3_MET_FOR_Z5`), one flag `z5_track` for week_decision / flow / 推薦 / change log; low-intensity share blocks Zone 5 only with a tested AeT; flow = two parallel tracks with 「安排課表」 actions (`?add=` / `?test=` deep links into the 課表 dialog); passive 90-min re-confirmation removed |
| 2026-10-04 | feature | SP-31 | Two interval tracks: Zone 3 A1–A4 (2×15 → 3×12 → 2×20 → 1×30, 88–95 % CP) and Zone 5 V1–V4, own steps / 達標 counts; T1–T3 kept as 巡航版 and legacy; Zone 3 gate (4 weeks ≥ 3 runs, no 7-day gap, sticky, ≥ 21-day break re-locks / 90-min test / UA gap); low-intensity share blocks Zone 5 only; Zone 3 ≤ 10 % of the week, Zone 3 + Zone 5 ≤ 20 %; 2 a week = one of each, 1 a week 1:1 / 2:1 by the A race; 專項期 / 減量期 two-track sessions; z3_note / warn notes; flow stage 1 = the Zone 3 gate |
| 2026-10-04 | feature | SP-68 | Guardrail CTL = the PMC's started CTL (manual at a date → first-4-week mean → 0); rule E's TSB < −30 reads the same PMC (SP-63 Q3); a manual start skips the ramp for 7 days |
| 2026-10-04 | feature | SP-38 follow-up | A main set ended by 「負荷」 counts toward the Zone 3 / Zone 5 ladder: `variant_from_steps` times each load step at TSS ÷ (IF² × 100) h, IF = the band's middle (`load_work_s`, 推估) |
| 2026-10-04 | feature | SP-63 follow-up | The week after a short unplanned break (3–5 days without a run, no re-entry block) is exempt from the running-volume step check (`load_guard.short_break`); the week plan gets an info note |
| 2026-10-04 | feature | SP-39 follow-up | Zone 5's UA path counts a measured LTHR only when tested in the last 12 weeks (`LTHR_FRESH_DAYS` 84, 推估); WKO5-sourced LTHRs carry their setting date; an older one re-locks that path until a retest |
| 2026-10-04 | feature | SP-39 follow-up | A 徐國峰 90-min test saved through `POST /sessions` (the 「安排課表」 deep link, or the dialog's 測試 › 徐國峰 / the `lib:xu_e_drift` row) replaces that day's long run — the 排入測試 code path (`_replace_long`) |
| 2026-10-04 | change | SP-63 follow-up | The short-break exemption from the running-volume step counts unplanned days only: days of the user's 不排課日期 / 休息日 don't make a short break (a partly planned gap needs ≥ 3 unplanned days); the note says 非計畫停跑 N 天 (owner 2026-10-05) |
| 2026-10-04 | change | SP-73 follow-up | 轉換期 days are not a running break: no re-entry block from a cross-training-only transition (`reentry.find_all` counts days outside it), and the Zone 3 gate's 7-day gap / 21-day re-lock skip them, its weeks see-through (`planning.transition_days`; owner 2026-10-05) |
| 2026-10-04 | change | SP-39 follow-up | Zone 5's UA path: no LTHR age limit any more (`LTHR_FRESH_DAYS` removed) — a measured LTHR is invalidated only by an event (`lthr_invalid`: a ≥ 4-week running break after the test, evidence since the test from `threshold_confidence.lthr_evidence`, an AeT aggregate shift / moved); the flow names the event and offers the 30-min LTHR test (owner 2026-10-05) |
| 2026-10-04 | sp-79-quality-families | SP-79 | Generated 強度課 titles name the family (有氧間歇／有氧間歇（巡航）／VO2max 間歇) instead of 閾值／近閾值／VO2max; stored older titles are mapped on read and in reconcile (no spurious change), and the ladder's title matchers accept both spellings |
| 2026-10-05 | feature | SP-75 | 專項期 in two halves: 前段 (weeks 10–7) climbs the ladders, 後段 (6–3) leans the 1-a-week ratio toward the race (long trail ≥ 4 h: Zone 3 only; road half / marathon 3:1, 5 km 1:2, 10 km / half T+); trail intervals = the rung's uphill version instead of the fixed 5×4′, road Zone 3 = the ladder instead of the fixed 2×15′ |
| 2026-10-05 | change | SP-79 follow-up | Taper 「短強度 4×3 分」 renamed 「有氧間歇（巡航）4×3 分」 (intensity unchanged); the stored old title maps through `interval_library.renamed` / `plan_store.display_title` |
| 2026-10-05 | change | SP-63 follow-up | Weekdays not ticked as 可練日 in 課表偏好 count as planned rest for the short-break exemption (like 不排課日期 / 休息日), so a Fri–Sun-only runner's weekly Mon–Thu gap is not exempt from the running-volume step (owner 2026-10-05) |
| 2026-10-05 | change | SP-73 follow-up | The A race's 恢復期 (7–14 days) is a planned post-race phase like the 轉換期: its days are no running break for the re-entry block or the Zone 3 gate's gap / re-lock (`planning.post_race_days`; the block text says 「不含賽後恢復期／轉換期 M 天」; owner 2026-10-05) |
| 2026-10-05 | change | SP-73 follow-up | The running-volume step's base (and the planner's +10 % volume cap) skips weeks touching a 減量期 / race week / post-race 恢復期 / 轉換期 and uses the most recent normal weeks, so the second week after a transition isn't blocked (`load_guard.skip_mondays` / `normal_weeks`; owner 2026-10-05) |
| 2026-10-05 | feature | SP-86 | Strength removed from an A event's last 14 days is a reduction: auto-adjust removes stored ones without asking |
| 2026-10-05 | feature | SP-74 follow-up | The user's own RPE ≥ 7 技術地形 sessions come off the week's 20 % before the intervals (shortened, or left out when the floor doesn't fit) |
| 2026-10-06 | feature | SP-69 | Per-athlete calibration (`engine/calibrate.py` items) of the interval verdict (`interval_in_band_tol` 0.98 from the CP tests, `interval_last_fade` 5 % and `interval_tiz_goal` 85 % from the planned sessions, ≥ 20; `engine/interval_calib.py`), the LTHR retest hint (`lthr_test_age_days` 56 from how fast the LTHR moved between tests) and the easy-run HR margin (`easy_hr_margin_bpm` AeT+3 from the aggregated AeT estimate's SE, ≥ 3; adapt rule D's average-HR condition and the Friel band's upper edge; `engine/threshold_calib.py`). Thin data keeps today's constants; the texts that quote a number say 本人／手動／預設 |
| 2026-10-06 | feature | SP-231 | COROS post-run self-rating (`sportFeelInfo.feelType`, read per new activity with `POST /activity/detail/query`, 8-week backfill) as rule D′ (`rpe_hard`): an easy / long run rated Hard or more moves the next hard session < 48 h later to a free day ≥ 48 h after, else one step down; switch `plan.auto.rpe_rule`; held for approval within 14 days of an A race; the data stamp includes this week's ratings (`rpe_stamp`) and a sync that stored a rating on an already-imported activity (`rpe_filled`) starts a run |
| 2026-10-06 | change | SP-302 | Rule D's 「TSS > planned + 20 %」 now compares against an easy run planned at the easy-only TSS / h (overview.spec.md › Session TSS; before, the all-runs median put the plan near tempo and the check almost never fired) |
| 2026-10-08 | code-sync | N/A | Illness (SP-117: cold / fever rules, 生病停跑 re-entry text, illness pauses intervals; Known limit replaced); A 賽後重新打底 (SP-116: gates count evidence from after the post-race phases, `rebase` AeT test reason); 回量期 (SP-98) added to the post-race / rest-phase / step-skip / maintenance lists; 疲勞保險 interval outcome (SP-110); single-run guard (SP-66); week snapshot at the end of every run + `GET /plan/history` (SP-71); bad-HR drift is reference only for the gates (SP-266); AeT retest ±5 bpm wording (SP-279); `rpe_filled` trigger and `plan.auto.rpe_rule` setting (SP-231); `settings` trigger; weekly calibration refit (SP-320 ④); Known limits: settings run no-ops on an unchanged stamp, LTHR prior may count as measured, adapt moves ignore the 休息日 preference; 11 moved file:line pointers |
| 2026-10-06 | change | SP-301 | Rule D in two tiers: 偏強 = avg power > 80 % CP (without power: avg HR > 94 % LTHR, 推估) or TSS > planned + 20 % (the AeT + 3 HR condition removed) → label only, no session change; 太強 = the session classifier's hard class (Zone 3 or harder) → the next hard session < 48 h later moves / steps down with a reason (also the generator's own move), A-race 14-day confirm (`too_hard`), 復原; the easy-run TSS trim removed; D runs after E; D′ skips a run D's 太強 acted on |
| 2026-10-08 | fix | SP-358 | `push_window` (`backend/engine/plan_auto.py:467`) also re-sends a session whose pushed copy sits in the window but which moved out of it (`plan_sessions.copies_in`), so the copy leaves the old day; a removal that failed makes the result `partial` with its error (it used to count as nothing); new `only` / `window` arguments serve the page's own changes (`plan_sessions._sync_watch`: a drag / edit / delete / 不排課日期 / 休息日 / swap syncs the watch at once, also with 自動推送 off for copies already on the watch; a failure logs a `failed` row, trigger `edit`) |
| 2026-10-08 | fix | SP-358 review | With `only`, the stale / blocked / missed clean-up is limited to those sessions too (an edit syncs only what it touched); a delete COROS still lists after a 1.5 s pause is `check_days` (reminder), not a failure |
| 2026-10-08 | code-sync（SP-359, SP-102, SP-295, SP-352, SP-69） | N/A | 交換 (SP-359) is two user edits that automation never undoes (Overview); 11 moved file:line pointers (settings trigger, week snapshot, history route, `_sync_watch`, adapt rest week, AeT retest lines, noop stamp, LTHR prior, `open_days`, `streak_red`); new Decisions Log (10, gathered from the owner's calls above) and Open Questions (9: SP-102 maintenance mode, SP-295 relock ≥ gap check, SP-352 crash, SP-69 record, the three Known limits not yet confirmed, SP-238, SP-107) |
| 2026-10-08 | fix | SP-352 | A manual 專項期 without an A race (or a 專項期 week after the race) crashed the plan build with `KeyError: 'weeks_out'` when Zone 3's turn was the ladder's T+ maintenance session (`z3_spec` step 6, 9, …): the T+ rule assumed every TP item was the 後段's swap. `week_decision` (`backend/engine/quality_gate.py:2601-2612`) adds the T+ rule only for the 後段's swap (`advance` False) and builds `seg_note` only in a 後段 week; without a half the default ratio picks the track and the T+ turn is scheduled as is, no note. Tests: `backend/tests/test_specific_split.py::test_manual_specific_without_an_a_race_and_zone3_on_its_tplus_turn` and the two after it |
