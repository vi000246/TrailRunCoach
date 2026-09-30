# Module Spec: overview

> **Last Updated**: 2026-09-30
> **Status**: Active
> **Domain Layer**: Core Domain

## Overview

The home page. It answers four questions without splitting by sport: *how am I
doing* (the training-status indicators), *what's missing* (their prioritised
actions), *what did I do* (totals by week / month / year) and *what should I do
this week* (a day-by-day plan with a load projection). The athlete's trail, 百岳
and bike days are too few to read on their own, so every total, the PMC and the
plan use all sports together; categories exist only to colour stacked bars.

Volume is **moving time**, never recorded time — a multi-day 百岳 file records
the nights too (one 51 h trip held about 7 h of walking).

## Architecture

```
 Dataset (wko5-engine) ──┬─ Status engine (status.py) ── indicators, actions, phase, goals
                         ├─ overview.summary()  ── period buckets + current-period detail
                         ├─ overview.pmc()      ── CTL / ATL / TSB per day
                         └─ overview.week_plan() ─ target, sessions by day, projection
                                      │
                         /api/v1/overview/* ── backend/static/overview.html
```

| Layer | Responsibility | Entry point |
|---|---|---|
| Categories / helpers | Workout → category, moving time, effort km | `backend/engine/overview.py:68` |
| Periods | Week (Monday) / month / year buckets and totals | `backend/engine/overview.py:207` |
| PMC | Same `tl()` recurrence as the chart expressions `ctl` / `atl` / `tsb` | `backend/engine/overview.py:269` |
| Week plan | Volume target, session template, done-matching, day placement, projection | `backend/engine/overview.py:405` |
| API | Memoised Status, the endpoints, the page | `backend/api/overview.py:40` |

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
  ≥ 90 min), terrain from the goal's climb density; then one of: **CP test 3'/12'** when the
  `testing` indicator is bad / watch and the A event is > 10 days away; specific → uphill
  intervals 5×4'; base with a good `intensity` indicator → threshold 3×10'. No quality session
  when `intensity` or `drift` is bad.
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
testing to-dos from the indicators).

## Status engine change

`Status.weekly_hours()` now sums moving time (fallback recorded time) instead of recorded
time (`backend/engine/status.py:168`), so the volume indicators aren't inflated by multi-day
trips.

## API

| Method | Path | Returns |
|---|---|---|
| GET | `/api/v1/overview/status` | `Status.to_dict()`: today, phase, goals, headline, indicators, actions, counts (`backend/api/overview.py:62`) |
| GET | `/api/v1/overview/summary?unit=week\|month\|year&anchor=&n=` | buckets + current detail; n capped 104 / 60 / 12 (`backend/api/overview.py:69`) |
| GET | `/api/v1/overview/pmc?begin=&end=` | daily tss / ctl / atl / tsb; default the last 180 days (`backend/api/overview.py:79`) |
| GET | `/api/v1/overview/weekplan` | the week plan (`backend/api/overview.py:90`) |
| GET | `/api/v1/overview/page` | `backend/static/overview.html` (`backend/api/overview.py:97`) |
| GET | `/` | redirects to the overview page when no `frontend/dist` build exists (`backend/main.py:63`) |

`Status` is memoised per (dataset, day, `plan.json` mtime) (`backend/api/overview.py:40`).

## Testing

`backend/tests/test_overview.py`: category mapping, period arithmetic and labels, the
projection matching the `tl()` recurrence and TSB = yesterday's CTL − ATL, and the ramp
formula reaching exactly +3 CTL in 7 days.

## Domain Model

### Bounded Context
- **Context Name**: TrainingOverview（訓練總覽）
- **Domain Layer**: Core Domain
- **Parent Module**: N/A (consumes `wko5-engine` and the Status engine)

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
| projection | the tl() recurrence continued with the plan's daily TSS |

## Change History

| Date | Source | Feature SRS | Summary |
|------|--------|-------------|---------|
| 2026-09-30 | code-sync | N/A | Created from brownfield analysis — all-sport home page: status, week/month/year totals, combined PMC with projection, rule-based weekly plan |
