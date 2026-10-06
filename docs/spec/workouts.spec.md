# Module Spec: workouts (activity metadata)

> **Last Updated**: 2026-10-04
> **Status**: Active
> **Domain Layer**: Supporting

## Overview

Per-activity metadata the user can correct, like WKO5's workout metadata: the terrain
classification (road / trail, `PATCH /api/v1/workouts/{id}/classification`) and, since
2026-10-01, the **activity tags**: activity type, effort and a note; since 2026-10-02 also the
user's **name** for the activity, free-form **tags** and a **pain** mark (傷病紀錄). Auto values come
from the existing rules; a user value always wins and is never overwritten by auto
re-classification. All of it is edited on the 活動編輯 page (`backend/static/activity.html`).

## Domain Model

**Bounded Context**
- Context Name: 活動資料 (Activity Metadata)
- Domain Layer: Supporting
- Parent Module: the training-data datasets (WKO5 / COROS / TP, `backend/engine/wko5expr/`); read by
  race power (racepower.spec.md), the plan and the workout review

**Ubiquitous Language**

| Term | Meaning | Code |
|---|---|---|
| activity tag row | the user's values for one activity, keyed by local start minute (+ file) | `activity_tags` table, `activity_tags.find` |
| auto vs user value | auto = computed at read time; user = stored with `*_overridden`; user wins | `activity_tags.merge` |
| activity type / effort | 比賽 / 練跑 / … and 全力 / 有拼但有休息 / 一般 / 輕鬆 | `TYPES`, `EFFORTS` |
| recorded RPE / feel | the watch's post-workout rating from the FIT session | `activity_tags.load_recorded` |
| power source | stryd / watch / none per workout; watch power unused by default | `engine/power_source.py` |
| bad activity / exclusion | a file that was not a foot activity (vehicle / bike / impossible power), left out of every model | `engine/bad_activity.py`, `exclusion` |
| pain mark | 沒痛 / 痠 / 痛 / 中斷 on an activity; 痛 / 中斷 joins or opens a 傷病紀錄 event | `pain`, `engine/injuries.py` |

**Domain Events**: 活動標記變更 (PATCH: type / effort / note / name / tags / exclusion), 疼痛標記 →
傷病紀錄 draft opened / joined (`injuries.attach`), 排除規則開關變更 (`activities.exclude_bad`; rebuilds the
datasets via `source_stamp`).

## Activity tags (`backend/engine/activity_tags.py`)

| Field | Values (label) |
|---|---|
| `activity_type` | race 比賽 · training 練跑 · hike 爬山 · baiyue_group 百岳跟團 · test 測試 · other 其他 |
| `effort` | max 全力 · hard_with_rests 有拼但有休息 · moderate 一般 · easy 輕鬆 |
| `note` | free text |
| `name` | the user's title (≤ 200 chars; null / blank = the original) |
| `tags` | free-form list (stored `tags_json`; ≤ 20 tags, ≤ 30 chars each, case-insensitive dedupe). The tag 「當作間歇」 makes the workout review judge the run as intervals (`interval_eval.FLAG_TAG`) |
| `pain`, `pain_area`, `injury_id` | 疼痛 mark (null 沒填 / 0 沒痛 / 1 痠 / 2 痛 / 3 中斷), area, the linked 傷病紀錄 event (`engine/injuries.py`; hidden and 404 in demo mode) |
| `pain_score` | optional 0–10 「跑的時候最痛幾分」 next to the mark (SP-271; cleared with it; drives the pain light) |

The pack carried (`pack_kg`, PATCH on the dataset workout) is not a tag column: it goes to
`racepower_hike_meta.json` (the 百岳 prediction).

**Storage.** Table `activity_tags` (`backend/db/models.py`, created by `init_db`'s `create_all`).
Columns added later (`exclusion`, `name`, `tags_json`, `pain`, `pain_area`, `injury_id`;
`activity_tags.LATE_COLS`) are added by `database._migrate_schema` and by `upsert` on an older table. Only the user's values are stored, each with `*_overridden`; auto values
are computed at read time and merged (`merge`). Key: the local start minute
(`YYYY-MM-DDTHH:MM`, Dataset `entry.start`). The dataset file (e.g. a `.wko4` name) is matched
first — also without the 同步資料 source's `coros/` / `tp/` prefix (`activity_key.same_file`) — then
the minute, then ±3 min (another source, 推估). The key is not a `workout_files` row
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

**Auto effort.** Precedence: the user's mark > the watch-recorded RPE > the HR / road rule.
- watch RPE (`effort_from_rpe`, 2026-10-02): FIT session `workout_rpe` / `workout_feel` (stored on
  `workout_files.rpe` / `feel` at import; `backend/scripts/backfill_rpe.py` for older rows; only
  some watches write it). RPE ≤ 4 → 輕鬆, ≤ 8 → 一般, 9–10 → 全力 (推估, Borg CR10 words), 全力 with
  long rests over the limit → 有拼但有休息. The HR rule's verdict is kept in the reason.
- trail / hike (`effort_hr`): moving HR ÷ own-date LTHR (`athlete.thresholds_as_of`). Trail runs
  (2026-10-02, unsourced-rules.md §A2): ≥ x*(T) − 0.03, the duration-dependent full-effort HR
  fraction (`racepower.trailhr.auto_max_frac`; Fornasiero 2018: a 12 h race spends most of its
  time below VT1, so the share rule can't hold) → 全力 candidate. Hikes and other non-run
  activities keep ≥ 0.90 (Friel HR Z3 lower bound) and ≥ 2/3 of the HR time above AeT (推估). A
  candidate is 全力 when the long rests (`rest_spells`: stops ≥ 5 min incl. recording gaps, 推估)
  are ≤ the rest limit — 10 % of the elapsed time by default, fitted per athlete
  (`engine/effort_calib.py` P9: p90 × 1.5 of the rest share of their 全力 activities, ≥ 3) —
  else 有拼但有休息. Otherwise ≥ half the HR time below AeT and average < AeT + 3 bpm
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

A "run" that was not a run — the watch left recording on a bike or in a car (e.g. a file of
~12 km in ~17 min ≈ 43 km/h, ~900 W average, resting-level HR) — is
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
index-keyed cache; features disk-cached per `.wko4` stamp in `bad_activity_v1.json` in the app's data folder)
and `FitFolderDataset` (decided while loading). `ds.excluded` / `ds.exclusion_kept` list them.
`cptest.curves` / `scan` (synced FIT files read beside the dataset) drop them via
`cptest.bad_files` (`racepower_bad_activity.json`). `source_stamp` includes the setting and an
overrides hash, so a change rebuilds the cached datasets. The legacy DB-row APIs
(`/api/v1/pmc`, `/api/v1/analytics/*` and the React `frontend/`) were removed with the React SPA on 2026-10-04.

Review scan: `python -m backend.scripts.scan_bad_activities` (read-only; flagged files and the
closest calls). On one runner's history (~680 WKO5, ~515 COROS, ~690 TP foot activities; ratio =
speed ÷ limit): at 1.25 three files are flagged (a whole-file car / bike run, a "hiking" file that
was a car trip, a hike ending with minutes at 28–50 km/h); with 1.15 also two short vehicle
segments inside a run and a hike — all checked minute by minute as vehicle segments. The fastest
genuine activity: ratio 0.60 (a short treadmill run); a long run 0.56. No race or long run is
flagged.

## API

| Method | Path | Body / result |
|---|---|---|
| PATCH | `/api/v1/workouts/{id}/classification` | `{trail_classification}`: `road` / `trail` = a user override; `auto` clears it and re-applies `classify_trail` |
| PATCH | `/api/v1/workouts/{id}/activity` | `{activity_type?, effort?, note?, exclusion?, name?, tags?, pain?, pain_area?, pain_side?}`; a key present with null clears it (back to auto), absent = unchanged; `tags` replaces the list; a pain mark attaches to / opens a 傷病紀錄 event (404 in demo mode); 400 invalid value, 404, 422 no start time. Returns `{id, activity: …}` fields |
| GET | `/api/v1/workouts`, `/api/v1/workouts/{id}` | each item now has `trail_classification`, `classification_overridden` and `activity` (the stored user values: `activity_type`, `effort`, labels, `*_overridden`, `note`, `name`, `tags`, `key`) |
| GET | `/api/v1/wko5/workouts/{idx}/activity` | dataset workout (current source): effective, auto (+ reasons), overridden flags, note, `effort_detail` (HR fraction, above-AeT share, long-rest share), `capacity` (race-power sample or not), the option labels, `power` (`source`, `used`, `label`, `setting`), `title_original`, `terrain`, `pack`, `pain_state` |
| GET | `/api/v1/wko5/workouts/{idx}/pain` | the pain mark only (the chart page's chip); 404 in demo mode |
| GET | `/api/v1/wko5/activities` | every activity of the current source, newest first, with the stored user values (type / effort marks, name, tags, note, exclusion, pain), terrain, power label, recorded RPE / feel; excluded files with `index: null` |
| GET | `/api/v1/wko5/activities/auto` | the auto type / effort (+ reasons) of every activity, computed in the background (`backend/api/activity_auto.py`: one job per Dataset, chunks of 25 newest first, cached on disk per file); `{state computing / ready / error, n_done, n_total, stale, auto}` — the page polls |
| GET | `/api/v1/wko5/activities/stats` | `{key: {avg_hr, avg_power}}` for the list columns |
| PATCH | `/api/v1/wko5/activities` | key-based single or bulk edit (≤ 500 items `{key, file?}`): any tag field, plus `add_tags` / `remove_tags`; works for excluded files |
| GET | `/api/v1/wko5/workouts` | each item also has `power_source` and `power_label` (「手錶推估功率（未採用）」 for unused watch power); `tss_source` is no longer `power` for a blocked watch run |
| PATCH | `/api/v1/wko5/workouts/{idx}/activity` | as above; keyed by start minute + file, so it applies across sources; also `exclusion` (`keep` / `exclude` / null) and `pack_kg`; the GET has `exclusion_state` {override, flagged, enabled} |
| GET | `/api/v1/wko5/workouts` (excluded rows) | an excluded file is listed with `index: null` and `excluded` {key, label, reason, rule, auto, manual, override, avg_kmh} |
| GET | `/api/v1/wko5/exclusions` | `{enabled, setting, source, excluded: […], kept: […]}` of the current source |
| PUT | `/api/v1/wko5/exclusions` | `{key, file?, exclusion}`: override any activity by its start minute; 400 bad key / value |
| PUT | `/api/v1/sync/settings` | `exclude_bad_activities` ↔ `activities.exclude_bad` |

## UI

`backend/static/activity.html` — the 活動編輯 page (shell nav; title 活動列表, served at
`GET /api/v1/wko5/activities/page`, 2026-10-02). A table of every activity with search (name,
tags, note, date), date / sport / source / 有疼痛 filters and sortable columns (incl. RPE and
疼痛); 「編輯」 opens a dialog per activity: name (還原原名), 活動類型, 努力度 (「自動（…）」 =
back to auto, the recorded RPE / feel shown), 疼痛, 背負, tags, note, terrain (路跑 / 越野 /
規則判定; WKO5 source not editable), 排除, and a read-only 功率來源 row (「（未採用）」 for unused
watch power). Each field has a 「自動」 / 「手動」 badge with the auto reason; help behind 「?」.
Bulk edit sets type / effort or adds / removes tags. The auto values stream in while
`/activities/auto` computes (「自動分類計算中：n／N 筆」). The page also hosts the 成就 tab.

The old 「活動資訊」 card (`activity_tags_card.js`) on 圖表分析 → 單次活動 was removed
(2026-10-02); the viewer (`backend/static/wko5_viewer.html`) has a 「編輯活動」 chip linking to the
page, and its activity list shows the user's name.

Bad activity files: the editor's 排除 row has 「手動排除」 (confirmed first; or, for a file the rule
flags that the user kept, the rule's reason and 「恢復自動判定」); a change reloads the list and
reopens the activity by key (indices shift). The viewer's activity list shows an excluded file greyed, not openable, with
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

`python -m backend.scripts.seed_activity_tags [--seed FILE] [--source coros|tp|wko5] [--db PATH] [--apply]` —
one-off, idempotent, from a JSON file of corrections the user writes (`--seed`, else
`$WKO5COACH_TAG_SEED`, else `activity_tag_seed.json` in the app's data folder; the app ships no
rows, `EXAMPLE_SEED` is fictional and used by the tests). A row with `km` is matched by date +
distance ±10 % and terrain; a row with `file` (a WKO5 file name) by that name, or on a COROS / TP
source by the start time the name encodes (±3 min; `start_of_file`,
`backend/scripts/seed_activity_tags.py:83`). Leave `effort` out to keep it auto. `--source`
defaults to `charts.data_source`; the trail rows need the FIT dataset's trail classification
(wko5-coros-sync.spec.md). Writes the app DB unless `--db`. Dry run by default.

## Testing

`backend/tests/test_activity_tags.py`: rules, rest spells, merge, tmp-DB store, the migration,
the PATCH / list API on an in-memory DB, capacity gating with user marks, the seed matcher and
idempotence, the trail HR model and the planner estimate.
`backend/tests/test_activity_edit.py`: name / tags store, key-based and bulk API, the terrain
`auto` reset, the recorded RPE as an effort input. `backend/tests/test_activity_auto.py`: the
background `/activities/auto` job (single flight, progress, disk cache).
`backend/tests/test_effort_calib.py`: the per-athlete rest limit. `backend/tests/test_activity_key.py`:
start-time / file matching across sources.
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
| 2026-10-04 | code-sync | N/A | Domain Model; name / free-form tags / pain columns; watch RPE in the effort precedence; trail 全力 by x*(T) − 0.03 and per-athlete rest limit; 活動編輯 page + `/activities`, `/activities/auto`, `/activities/stats`, bulk PATCH; 活動資訊 card removed; terrain `auto` reset; seed from a JSON file; file match without `coros/` / `tp/` prefix |
