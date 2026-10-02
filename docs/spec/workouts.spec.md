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
first, then the minute, then ±3 min (another source, 推估). The key is not a `workout_files` row
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

**Auto activity type** (first match, 推估 order): a matched season-plan road / 越野賽 event →
比賽; a test the plan / title says (`workout_review.classify`, not the power pattern) → 測試; a
race word in the title → 比賽; hiking / mountaineering with a plan 百岳 event that day →
百岳跟團; hiking / mountaineering, or a trail run with a hike word in the title → 爬山; any run →
練跑; else 其他.

**Auto effort.**
- trail / hike (`effort_hr`): moving HR ÷ own-date LTHR (`athlete.thresholds_as_of`) ≥ 0.90
  (Friel HR Z3 lower bound) and ≥ 2/3 of the HR time above AeT (推估) → 全力 when the long rests
  (`rest_spells`: stops ≥ 5 min incl. recording gaps, 推估) are ≤ 10 % of the elapsed time
  (推估), else 有拼但有休息. Otherwise ≥ half the HR time below AeT and average < AeT + 3 bpm
  (intensity.py's easy rule) → 輕鬆, else 一般.
- road (`effort_road`): `maximal.road_maximal` passes → 全力; else the easy rule → 輕鬆, else 一般.

## Power source (`backend/engine/power_source.py`, 2026-10-01)

Each dataset workout has a power source: `stryd` (Stryd developer fields Form Power / Air Power /
Leg Spring Stiffness, or a Stryd `device_info` row), `watch` (power without them: estimated from the
wrist) or `none`. Reading the fields as Stryd is 推估. `Dataset.power_source(w)` / `power_ok(w)` /
`power_label(w)`; FIT datasets classify at load (`FitChannels.power_source`), WKO5 `.wko4` files from
their channels (cached per file stamp). Setting `power.accept_watch_power` (default false): watch
power feeds no power-based model and no power TSS; HR and pace paths still use the run. Parity mode
reads every power (WKO5 does not tell them apart). The channel itself is never hidden (charts and
the activity view still show it).

## Bad activity files (`backend/engine/bad_activity.py`, 2026-10-01)

A "run" that was not a run — the watch left recording on a bike or in a car (TP 2025-12-14
`tp_2025_12_14_3477204875.fit`: 12.28 km in 17.3 min ≈ 43 km/h, 899 W average, HR 66) — is
**excluded**: it stays in the DB and the activity list, marked
「已排除：疑似交通工具／騎車（均速 43 km/h）」, and leaves `ds.workouts`, so no model reads it
(PMC / TSS, mean-max / PD / CP, race-power samples and back-tests, drift / AeT, plan matching,
charts).

**Rules** (foot sports only: sport group `run` / `walk`; a hike can include running, so walk uses
the running limits). Distance resampled to 1 s; a second faster than 144 km/h (`fit_to_channels.
MAX_SPEED_KMH`, WKO5's GPS-spike rule) counts as no distance, so a GPS jump never flags a file.
`limit(T)` = men's world-record average speed at duration T (World Athletics: 400 m 43.03 van
Niekerk 2016, 1500 m 3:26.00 El Guerrouj 1998, 10 000 m 26:11.00 Cheptegei 2020, marathon 2:00:35
Kiptum 2023; log-interpolated, clamped outside) × 1.15 (推估 margin: GPS error, steep descents;
chosen on the real-data scan below):
1. average moving speed > limit(moving time) → 「疑似交通工具／騎車（均速 N km/h）」;
2. a sustained 60 s / 5 min / 20 min window > limit(window) (≈ 36.7 / 29.4 / 26.9 km/h) — a bike /
   car segment inside a real run → 「疑似交通工具／騎車（第 a–b 分鐘連續 … N km/h，全程均速 M km/h）」;
3. average of the non-zero power samples > 10 W/kg × weight (推估: Stryd-style power ≈ speed in m/s
   × ~1 W/kg, so the 1500 m record pace is ~7–8 W/kg; weight 70 kg 推估 when unknown).
Windows, never single samples: a fast descent, a sprint or a GPS spike is not flagged. Cadence is
not used (the car file's 54 reads like a slow hike's 56).

**Trim vs exclude: exclude the whole file.** Trimming would have to recompute duration, distance,
TSS / NP and moving time, and the WKO5 source takes those from WKO5's own index, so a trimmed file
would disagree with itself; the run's remaining load is small next to a broken PD fit. The reason
names the bad segment, and the user can keep the file (segment included).

**Overrides** — `activity_tags.exclusion` (same user-value store as type / effort; migration adds
the column; `load` reads an older table): `keep` 這筆是正常的，不要排除, `exclude` 手動排除,
NULL = the rule. Setting `activities.exclude_bad` (default true; 設定 → 資料校正) switches the
auto rule off; manual exclusions still apply. Parity mode excludes nothing (WKO5 reads every file),
like the corrections.

**Where it applies.** `Dataset._apply_exclusion_policy` (WKO5: filtered and renumbered before any
index-keyed cache; features disk-cached per `.wko4` stamp in `~/.wko5coach/bad_activity_v1.json`)
and `FitFolderDataset` (decided while loading). `ds.excluded` / `ds.exclusion_kept` list them.
`cptest.curves` / `scan` (synced FIT files read beside the dataset) drop them via
`cptest.bad_files` (`racepower_bad_activity.json`). `source_stamp` includes the setting and an
overrides hash, so a change rebuilds the cached datasets. The legacy DB-row APIs
(`/api/v1/pmc`, `/api/v1/analytics/*`, the unbuilt React `frontend/`) are not covered.

Review scan: `python -m backend.scripts.scan_bad_activities` (read-only; flagged files and the
closest calls). 2026-10-01 on this athlete (WKO5 682, COROS 515, TP 692 foot activities; ratio =
speed ÷ limit at 1.25): flagged files — 2025-12-14 run 12.28 km avg 45.5 km/h, 917 W (WKO5 + TP;
not in COROS); 2021-05-16 "hiking" 57.0 km avg 26.6 km/h (car; WKO5); 2021-04-10 hiking 10.9 km,
minutes at 28–50 km/h at the end (WKO5); with 1.15 also 2024-03-18 run 6.67 km, 7 min at 25–38
km/h (all three sources) and 2021-07-17 hiking 4.83 km, 5 min at 31 km/h (WKO5) — all checked
minute by minute as vehicle segments. The fastest genuine activity: ratio 0.60 (2024-06-14
treadmill 4 km at 17.9 km/h); 2024-08-25 28.4 km run 0.56. No race or long run is flagged.

## API

| Method | Path | Body / result |
|---|---|---|
| PATCH | `/api/v1/workouts/{id}/activity` | `{activity_type?, effort?, note?}`; a key present with null clears it (back to auto), absent = unchanged; 400 invalid value, 404, 422 no start time. Returns `{id, activity: …}` fields |
| GET | `/api/v1/workouts`, `/api/v1/workouts/{id}` | each item now has `trail_classification`, `classification_overridden` and `activity` (the stored user values: `activity_type`, `effort`, labels, `*_overridden`, `note`, `key`) |
| GET | `/api/v1/wko5/workouts/{idx}/activity` | dataset workout (current source): effective, auto (+ reasons), overridden flags, note, `effort_detail` (HR fraction, above-AeT share, long-rest share), `capacity` (race-power sample or not), the option labels, `power` (`source`, `used`, `label`, `setting`) |
| GET | `/api/v1/wko5/workouts` | each item also has `power_source` and `power_label` (「手錶推估功率（未採用）」 for unused watch power); `tss_source` is no longer `power` for a blocked watch run |
| PATCH | `/api/v1/wko5/workouts/{idx}/activity` | as above; keyed by start minute + file, so it applies across sources; also `exclusion` (`keep` / `exclude` / null); the GET has `exclusion_state` {override, flagged, enabled} |
| GET | `/api/v1/wko5/workouts` (excluded rows) | an excluded file is listed with `index: null` and `excluded` {key, label, reason, rule, auto, manual, override, avg_kmh} |
| GET | `/api/v1/wko5/exclusions` | `{enabled, setting, source, excluded: […], kept: […]}` of the current source |
| PUT | `/api/v1/wko5/exclusions` | `{key, file?, exclusion}`: override any activity by its start minute; 400 bad key / value |
| PUT | `/api/v1/sync/settings` | `exclude_bad_activities` ↔ `activities.exclude_bad` |

## UI

`backend/static/activity_tags_card.js`, loaded by `wko5_viewer.html` (圖表分析 → 單次活動) the same
way as `segments_card.js`: a 「活動資訊」 card first in the grid with two selects (活動類型, 努力度;
「自動（…）」 = back to auto), a note, and a 「自動」 / 「手動」 badge per field with the auto reason. Below them 「功率來源：Stryd」 or
「功率來源：手錶推估功率（未採用）（功率模型、功率 TSS 不採用；心率／配速照常使用）」.
No served static page edited the terrain classification (only the unbuilt React `frontend/` has a
hook), so the single-activity view is where both live.

Bad activity files: the card's 「排除：」 line has 「手動排除」 (or, for a file the rule flags that the
user kept, the rule's reason and 「恢復自動判定」); a change drops the selection and reloads the list
(indices shift). The viewer's activity list shows an excluded file greyed, not openable, with
「已排除：…」 and 「這筆是正常的，不要排除」 (「取消手動排除」 for a manual one). 設定 → 資料校正 →
「排除壞掉的活動檔」: the toggle (default on), the excluded files with reasons and the same button,
and the files the user marked normal (「恢復自動判定」).

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
`backend/tests/test_bad_activity.py`: the limits, car / vehicle-segment / power rules, descents /
sprints / GPS spikes / pauses not flagged, overrides, the FIT dataset leaving files out (contiguous
indices), keep / manual exclude through a tmp tags DB, the WKO5 policy renumbering, parity and the
setting off, `source_stamp`, `cptest.bad_files`, the list / exclusions / PUT endpoints, the old-table
load and the setting key (synthetic FITs, `fit_builder.build_run(speeds_m_s=…)`).

## Change History

| Date | Type | Feature SRS | Summary |
|------|------|-------------|---------|
| 2026-10-01 | feature | user request | Activity tags (type / effort / note), auto + user override, API, 活動資訊 card, seed script |
| 2026-10-01 | feature | user request (COROS vs TP back-test) | Power source per workout (stryd / watch / none), `power.accept_watch_power` (default false), API fields and the 功率來源 line on the activity card; tests `backend/tests/test_power_source.py` |
| 2026-10-01 | feature | user request (bad activity files) | Bad activity files excluded from every model (vehicle / bike speed vs world-record limits, impossible power), whole-file exclusion, keep / exclude overrides in `activity_tags.exclusion`, setting `activities.exclude_bad`, API, list / card / settings UI; tests `backend/tests/test_bad_activity.py` |
| 2026-10-01 | bugfix | docs/research/unsourced-rules.md §0.10 step 0 | Seed matches COROS / TP races by the WKO5 start (±3 min), `--source` defaults to the data source; documented that the tags live in the app DB (table created on first write) |
