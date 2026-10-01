# Module Spec: workouts (activity metadata)

> **Last Updated**: 2026-10-01
> **Status**: Active
> **Domain Layer**: Supporting

## Overview

Per-activity metadata the user can correct, like WKO5's workout metadata: the terrain
classification (road / trail, `PATCH /api/v1/workouts/{id}/classification`) and, since
2026-10-01, the **activity tags**: activity type, effort and a note. Auto values come from the
existing rules; a user value always wins and is never overwritten by auto re-classification.

## Activity tags (`backend/engine/activity_tags.py`)

| Field | Values (label) |
|---|---|
| `activity_type` | race 比賽 · training 練跑 · hike 爬山 · baiyue_group 百岳跟團 · test 測試 · other 其他 |
| `effort` | max 全力 · hard_with_rests 有拼但有休息 · moderate 一般 · easy 輕鬆 |
| `note` | free text |

**Storage.** Table `activity_tags` (`backend/db/models.py`, created by `init_db`'s `create_all`;
no column migration). Only the user's values are stored, each with `*_overridden`; auto values
are computed at read time and merged (`merge`). Key: the local start minute
(`YYYY-MM-DDTHH:MM`, Dataset `entry.start`). The dataset file (e.g. a `.wko4` name) is matched
first, then the minute, then ±3 min (another source, 自組). The key is not a `workout_files` row
because the race-power engine reads the WKO5 / COROS / TP datasets, and most WKO5 activities
have no row (the app DB holds only the synced COROS / TP files). The engine reads the table
sync and read-only (`load`, like `datasource.read_setting`); `WKO5COACH_TAGS_DB` points it at
another DB (back-test what-ifs); tests patch `_default_db`. The table appears in the app DB on the
first write (an API PATCH, `upsert`, or `init_db` at server start); until then `load` returns
`[]`. As of 2026-10-01 the real app DB has no `activity_tags` table yet: the seed and `load` both
default to the app DB, so the research note "seed writes another DB" (unsourced-rules.md §0.1)
was presumably a `--db` / `--tags-db` scratch run (推定). On a COROS / TP
dataset a tag written from the WKO5 source (file = a `.wko4` name) matches by the local start
minute ±3 min (`backend/tests/test_fit_dataset_prereqs.py:210`).

**Auto activity type** (first match, 自組 order): a matched season-plan road / 越野賽 event →
比賽; a test the plan / title says (`workout_review.classify`, not the power pattern) → 測試; a
race word in the title → 比賽; hiking / mountaineering with a plan 百岳 event that day →
百岳跟團; hiking / mountaineering, or a trail run with a hike word in the title → 爬山; any run →
練跑; else 其他.

**Auto effort.**
- trail / hike (`effort_hr`): moving HR ÷ own-date LTHR (`athlete.thresholds_as_of`) ≥ 0.90
  (Friel HR Z3 lower bound) and ≥ 2/3 of the HR time above AeT (自組) → 全力 when the long rests
  (`rest_spells`: stops ≥ 5 min incl. recording gaps, 自組) are ≤ 10 % of the elapsed time
  (自組), else 有拼但有休息. Otherwise ≥ half the HR time below AeT and average < AeT + 3 bpm
  (intensity.py's easy rule) → 輕鬆, else 一般.
- road (`effort_road`): `maximal.road_maximal` passes → 全力; else the easy rule → 輕鬆, else 一般.

## API

| Method | Path | Body / result |
|---|---|---|
| PATCH | `/api/v1/workouts/{id}/activity` | `{activity_type?, effort?, note?}`; a key present with null clears it (back to auto), absent = unchanged; 400 invalid value, 404, 422 no start time. Returns `{id, activity: …}` fields |
| GET | `/api/v1/workouts`, `/api/v1/workouts/{id}` | each item now has `trail_classification`, `classification_overridden` and `activity` (the stored user values: `activity_type`, `effort`, labels, `*_overridden`, `note`, `key`) |
| GET | `/api/v1/wko5/workouts/{idx}/activity` | dataset workout (current source): effective, auto (+ reasons), overridden flags, note, `effort_detail` (HR fraction, above-AeT share, long-rest share), `capacity` (race-power sample or not), the option labels |
| PATCH | `/api/v1/wko5/workouts/{idx}/activity` | as above; keyed by start minute + file, so it applies across sources |

## UI

`backend/static/activity_tags_card.js`, loaded by `wko5_viewer.html` (圖表分析 → 單次活動) the same
way as `segments_card.js`: a 「活動資訊」 card first in the grid with two selects (活動類型, 努力度;
「自動（…）」 = back to auto), a note, and a 「自動」 / 「手動」 badge per field with the auto reason.
No served static page edited the terrain classification (only the unbuilt React `frontend/` has a
hook), so the single-activity view is where both live.

## Consumers

- race-power capacity samples (`athlete.capacity_samples`): effective effort 全力 and type ≠ 測試;
  the user's effort mark always wins (docs/spec/racepower.spec.md);
- the back-test: user-marked 比賽 / 全力 runs are cases over the full history;
- the trail HR pace model's race HR level (earlier 比賽 / 全力 trail runs);
- `workout_review.classify`: type 測試 = a test mark (`test_match` "user").

## Seed

`python -m backend.scripts.seed_activity_tags [--source coros|tp|wko5] [--db PATH] [--apply]` —
one-off, idempotent: 2025-10-18 road 5 km → 練跑 / 一般, 2025-11-02 trail 14.4 km → 爬山 / 一般,
2026-07-27 trail 11.7 km → 爬山 / 有拼但有休息 (date + distance ±10 %), and the 7 diary trail
races (date + WKO5 file) → 比賽 with effort left auto. `--source` defaults to `charts.data_source`.
On a COROS / TP source a race row matches the activity starting within ±3 min of the start its
WKO5 file name encodes (`start_of_file`, `backend/scripts/seed_activity_tags.py:56`); the trail
rows need the FIT dataset's trail classification (wko5-coros-sync.spec.md). Writes the app DB
unless `--db`. Dry run by default.

## Testing

`backend/tests/test_activity_tags.py`: rules, rest spells, merge, tmp-DB store, the migration,
the PATCH / list API on an in-memory DB, capacity gating with user marks, the seed matcher and
idempotence, the trail HR model and the planner estimate.

## Change History

| Date | Type | Feature SRS | Summary |
|------|------|-------------|---------|
| 2026-10-01 | feature | user request | Activity tags (type / effort / note), auto + user override, API, 活動資訊 card, seed script |
| 2026-10-01 | bugfix | docs/research/unsourced-rules.md §0.10 step 0 | Seed matches COROS / TP races by the WKO5 start (±3 min), `--source` defaults to the data source; documented that the tags live in the app DB (table created on first write) |
