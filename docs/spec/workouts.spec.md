# Module Spec: workouts (activity metadata)

> **Last Updated**: 2026-10-08
> **Status**: Active
> **Domain Layer**: Supporting

## Overview

Per-activity metadata the user can correct, like WKO5's workout metadata: the terrain
classification (road / trail, `PATCH /api/v1/workouts/{id}/classification`) and, since
2026-10-01, the **activity tags**: activity type, effort and a note; since 2026-10-02 also the
user's **name** for the activity, free-form **tags** and a **pain** mark (傷病紀錄); since 2026-10-06
a 0–10 **pain score** (SP-271), a **登山杖** mark (有杖 / 沒杖 / 未標, SP-242; 有杖 「依賽事設定」 from a
race marked 「會用登山杖」, SP-300) and a **路況** mark (乾 / 濕 / 未標, SP-250) with a rain hint (SP-299).
The auto effort also reads COROS's post-run self-rating (SP-231), and the auto type the
platform-neutral app type (`engine/sport_map.py`, SP-263). Auto values come
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
| recorded RPE / feel | the watch's post-workout rating from the FIT session, or COROS's post-run self-rating (1–5, mapped to RPE; SP-231) | `activity_tags.load_recorded`, `engine/coros_rpe.py` |
| 登山杖 mark | 有杖 / 沒杖 / 未標, the user's own (a watch cannot tell); stored as a free-form tag; read by no model | `activity_tags.POLES`, `pole_state` |
| 依賽事設定 | an activity of a race marked 「會用登山杖」 shows 有杖 until the user chooses (computed at read time, never stored) | `activity_tags.race_poles` |
| 路況 mark | 乾 / 濕 / 未標, the user's own; stored as a free-form tag; splits the trail technicality factor | `activity_tags.SURFACES` |
| rain hint | 「這次活動期間下過雨（N mm），要標成濕路嗎？」 while the 路況 is 未標 — a hint, never a mark | `activity_tags.rain_hint` |
| app type / filter kind | one platform-neutral activity type (路跑 / 越野跑 / 登山健行 / 騎車 / 肌力 / 走路 / 其他) from the COROS / FIT / WKO5 sport; the 圖表分析 filter adds 百岳登山 | `sport_map.app_type`, `sport_map.kind_of` |
| power source | stryd / watch / none per workout; watch power unused by default | `engine/power_source.py` |
| bad activity / exclusion | a file that was not a foot activity (vehicle / bike / impossible power), left out of every model | `engine/bad_activity.py`, `exclusion` |
| pain mark | 沒痛 / 痠 / 痛 / 中斷 on an activity; 痛 / 中斷 joins or opens a 傷病紀錄 event | `pain`, `engine/injuries.py` |

**Domain Events**: 活動標記變更 (PATCH: type / effort / note / name / tags / exclusion / poles / surface), 疼痛標記 →
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
| `poles` (in `tags`) | 登山杖: `with` 有杖 / `without` 沒杖 / `none` the user's own 未標 / null no choice — see 登山杖 below |
| `surface` (in `tags`) | 路況: `dry` 乾路 / `wet` 濕路 / null 未標 — see 路況 below |

The pack carried (`pack_kg`, PATCH on the dataset workout) is not a tag column: it goes to
`racepower_hike_meta.json` (the 百岳 prediction).

**Storage.** Table `activity_tags` (`backend/db/models.py`, created by `init_db`'s `create_all`).
Columns added later (`exclusion`, `name`, `tags_json`, `pain`, `pain_area`, `injury_id`;
`activity_tags.LATE_COLS`) are added by `database._migrate_schema` and by `upsert` on an older table.
Every write (`apply_update`) sets `row.updated_at` (`backend/engine/activity_tags.py:606`). Only the user's values are stored, each with `*_overridden`; auto values
are computed at read time and merged (`merge`). Key: the local start minute
(`YYYY-MM-DDTHH:MM`, Dataset `entry.start`). The dataset file (e.g. a `.wko4` name) is matched
first — also without the 同步資料 source's `coros/` / `tp/` prefix (`activity_key.same_file`) — then
the minute, then ±3 min (another source, 推估). The key is not a `workout_files` row
because the race-power engine reads the WKO5 / COROS / TP datasets, and most WKO5 activities
have no row (the app DB holds only the synced COROS / TP files). The engine reads the table
sync and read-only (`load`, like `datasource.read_setting`; memoised on the DB file's stamp with its
WAL included, `db/filestamp.py`, so a commit still in the WAL is seen —
`backend/engine/activity_tags.py:390`); `WKO5COACH_TAGS_DB` points it at
another DB (back-test what-ifs); tests patch `_default_db`. The table appears in the app DB on the
first write (an API PATCH, `upsert`, or `init_db` at server start); until then `load` returns
`[]`. As of 2026-10-01 the real app DB has no `activity_tags` table yet: the seed and `load` both
default to the app DB, so the research note "seed writes another DB" (unsourced-rules.md §0.1)
was presumably a `--db` / `--tags-db` scratch run (推定). On a COROS / TP
dataset a tag written from the WKO5 source (file = a `.wko4` name) matches by the local start
minute ±3 min (`test_tags_written_from_wko5_apply_to_coros_workouts`,
`backend/tests/test_fit_dataset_prereqs.py:237`).

**Auto activity type** (first match, 推估 order): a matched season-plan road / 越野賽 event →
比賽; a test the plan / title says (`workout_review.classify`, not the power pattern) → 測試; a
race word in the title → 比賽; a hike with a plan 百岳 event that day →
百岳跟團; a hike, or a trail run with a hike word in the title → 爬山; any run →
練跑; else 其他. "A hike" = the platform-neutral app type `hike` (SP-263: `auto_type(app_type=…)`,
`backend/engine/activity_tags.py:762`, passed by `athlete.auto_tags`,
`backend/engine/racepower/athlete.py:773`); without an app type, the sport type hiking /
mountaineering as before.

**App type and the 圖表分析 filter** (`backend/engine/sport_map.py`, SP-263). One table maps every
platform's sport code (COROS, Garmin / FIT sport + sub_sport, WKO5) to one app type: 路跑 / 越野跑 /
登山健行 / 騎車 / 肌力 / 走路 / 其他 (`app_type`, `backend/engine/sport_map.py:292`; a trail run by the
existing `classify.is_trail`). The 圖表分析 filter (`kind_of`, `backend/engine/sport_map.py:305`)
adds 百岳登山: the user's type mark wins (百岳跟團 → 百岳, 爬山 → 登山健行; any other mark keeps
the sport), else a hike with a plan 百岳 event that day or whose GPS track passed a 百岳 summit
(`achievements.baiyue_summits`) is 百岳. `GET /api/v1/wko5/sports` lists the kinds present.

**Auto effort.** Precedence: the user's mark > the watch-recorded RPE > the HR / road rule.
- watch RPE (`effort_from_rpe`, 2026-10-02): FIT session `workout_rpe` / `workout_feel` (stored on
  `workout_files.rpe` / `feel` at import; `backend/scripts/backfill_rpe.py` for older rows; only
  some watches write it). RPE ≤ 4 → 輕鬆, ≤ 8 → 一般, 9–10 → 全力 (推估, Borg CR10 words), 全力 with
  long rests over the limit → 有拼但有休息. The HR rule's verdict is kept in the reason.
  Since SP-231 the RPE can also be COROS's post-run self-rating (1–5, read from the activity
  detail at sync, stored as `workout_files.coros_feel` with `rpe_source = "coros"` and mapped to a
  10-point `rpe`; `load_recorded`, `backend/engine/activity_tags.py:682`); the reason then says
  「COROS 跑後自評 … （換算成 RPE …，推估）」 (`effort_from_rpe`, `backend/engine/activity_tags.py:652`).
  The same rating (FIT or COROS, COROS 1–5 = the five load levels) feeds the per-level 「負荷」 RPE
  → TSS/h fit (SP-57, `level_of_rpe` / `samples`, `backend/engine/rpe_load.py:135`, `:280`; a FIT RPE 1–10 maps two values per level, 6 → 累). COROS
  FIT files carry no RPE / feel field — re-checked 2026-10-08 on the 2026-09 / 10 files, rated ones
  included; the rating only comes from the activity detail.
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

### 登山杖 (SP-242, SP-243, SP-300)

The user's own mark — a watch cannot tell — stored as one free-form tag, no schema change:
`有杖` / `沒杖` (`POLES`, `backend/engine/activity_tags.py:164`), plus the hidden `杖未標` for the
user's own 未標 (`POLE_NONE_TAG`, `backend/engine/activity_tags.py:169`); no tag = never chosen. The
pole tags are mutually exclusive (`exclusive_poles`) and kept out of the tag chips. **No model reads
the mark** (calculator, HR model, downhill bump unchanged; docs/research/trekking-poles.md §4).

- **依賽事設定** (SP-300, `race_poles`, `backend/engine/activity_tags.py:217`): a season-plan event
  marked 「會用登山杖」 makes its activities 有杖 — every trail run / hike (`climb_vam.kind_of`) on the
  event's day, each day of a multi-day event (`backend/engine/activity_tags.py:236-243`), whatever the
  watch distance and also a 越野賽 recorded as a hike (owner 2026-10-07: the ±25 % date + kind +
  distance match no longer limits this default); plus, on a 1-day road / 越野賽 event, its run by that
  match (`maximal.match_events`: a road race's road run). The auto 比賽 type keeps the match.
  Computed at read time, never stored: unticking the race puts its un-chosen activities back to 未標.
  The user's choice (incl. their own 未標) always wins (`pole_state`,
  `backend/engine/activity_tags.py:249`); no 「回到依賽事」 button.
- **有杖 vs 沒杖 comparison** (SP-243, 能力 tab): only for someone who uses poles — in the last 365
  days ≥ 5 activities 有杖 and ≥ 5 沒杖 (`POLE_COMPARE_MIN`, `backend/engine/activity_tags.py:262`),
  counting only the trail runs and hikes the chart uses (`used`,
  `backend/engine/panels/pole_compare.py:172`; the race default counts). Below that the editor says
  「再標有杖 N 次、沒杖 M 次…」.

### 路況 and the rain hint (SP-250, SP-299)

The user's own mark of a dry or a wet / slippery trail, stored like the pole mark as one of two
free-form tags `乾路` / `濕路` (`SURFACES`, `backend/engine/activity_tags.py:304`); 未標 = neither. No
synonym typed as a free tag (雨天, 泥濘 …) counts. The trail technicality factor splits on it when
both groups have ≥ 30 windows on g ≤ +2 % (`SURFACE_MIN_N`, `backend/engine/racepower/grade_model.py:178`;
`fit_gait_re`), and only then does the calculator offer 「路況：乾／濕」 (racepower.spec.md).

Rain hint (SP-299): the archive rain while the activity ran (Open-Meteo hourly `precipitation`,
asked in the same call as the temperature and cached in `activity_weather.json`,
`backend/engine/route_weather.py:45`) — when it reached `RAIN_HINT_MM` = 1 mm (推估,
`backend/engine/activity_tags.py:335`) and the 路況 is still 未標 (`rain_hint`,
`backend/engine/activity_tags.py:360`), the editor shows 「這次活動期間下過雨（N mm），要標成濕路嗎？」
with a one-click 「標成濕」. Nothing is marked until the button is pressed; no hint without
coordinates or weather. Only on 越野跑 / 登山健行 (owner 2026-10-07): `rain_kind`
(`backend/engine/activity_tags.py:340`) = the activity's filter kind (`sport_map.kind_of`, incl. the
user's 爬山 / 百岳跟團 mark) is one of `TRAIL_KINDS`; an excluded file by that mark, its sport type, or
a run whose 地形 (the DB trail classification) is 越野 (`rain_kind_excluded`,
`backend/engine/activity_tags.py:348`). `GET /activities` and the single-activity JSON carry
`rain_kind` (`backend/api/wko5views.py:1143`, `backend/api/wko5views.py:893`), so a type change in the
editor moves the hint (`patchLocal`, `backend/static/activity.html:757`); an excluded row also carries
`rain_kind_auto` (without the user's type, `backend/api/wko5views.py:1160`) and the page applies a
type change on it with the same rule (`MOUNTAIN_TYPES`, `backend/static/activity.html:777`).
The activities cached before precipitation was asked get their rain from the one-time backfill of
the last 12 months (route-progress.spec.md › Rain backfill).

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
index-keyed cache; features disk-cached per `.wko4` stamp in `bad_activity_v1.json` in the app's data folder,
with the code version `dataset.per_workout_code("bad_activity")`, SP-341)
and `FitFolderDataset` (decided while loading). `ds.excluded` / `ds.exclusion_kept` list them.
`cptest.curves` / `scan` (synced FIT files read beside the dataset) drop them via
`cptest.bad_files` (`racepower_bad_activity.json`, versioned by `cptest.bad_cache_code`, SP-341). A cache
file of another code version is recomputed. `source_stamp` includes the setting and an
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
| PATCH | `/api/v1/workouts/{id}/classification` | `{trail_classification}`: `road` / `trail` / `unknown` = a user override (`VALID_CLASSIFICATIONS`, `backend/api/workouts.py:17`); `auto` clears it and re-applies `classify_trail` |
| PATCH | `/api/v1/workouts/{id}/activity` | `{activity_type?, effort?, note?, exclusion?, name?, tags?, pain?, pain_area?, pain_side?, pain_score?, poles?, surface?}` (`ActivityUpdate`, `backend/api/workouts.py:55`); a key present with null clears it (back to auto), absent = unchanged; `tags` replaces the list, then `poles` / `surface` rewrite their tag; a pain mark attaches to / opens a 傷病紀錄 event (404 in demo mode); 400 invalid value, 404, 422 no start time. Returns `{id, …}` with the stored user values (incl. `poles`, `poles_user`, `surface`, `pain_score`) |
| GET | `/api/v1/wko5/workouts/{idx}/activity` | dataset workout (current source): effective, auto (+ reasons), overridden flags, note, `effort_detail` (HR fraction, above-AeT share, long-rest share), `capacity` (race-power sample or not), the option labels, `power` (`source`, `used`, `label`, `setting`), `title_original`, `title_from`, `terrain`, `origin`, `pack`, `poles` / `poles_user` / `poles_race` (SP-300), `pain_state` (+ `pain_score`, the 傷別 pain-monitoring text `monitor` and the re-entry prompt `reentry`, SP-269) (`_activity_json`, `backend/api/wko5views.py:877`) |
| GET | `/api/v1/wko5/workouts/{idx}/pain` | the pain mark only (the chart page's chip); 404 in demo mode |
| GET | `/api/v1/wko5/activities` | every activity of the current source, newest first, with the stored user values (type / effort marks, name, tags, note, exclusion, pain, `pain_score`, `poles` / `poles_race`, `surface`), terrain, power label, recorded RPE / feel and `self_rating` (COROS, SP-231), `rain_mm` and `pole_chart` (counts toward the comparison); excluded files with `index: null`. Top level: `pole_tags`, `pole_none_tag`, `surface_tags`, `pole_compare` (the 5 + 5 counts), `rain_hint_mm` (`activities_list`, `backend/api/wko5views.py:1085`) |
| GET | `/api/v1/wko5/activities/auto` | the auto type / effort (+ reasons) of every activity, computed in the background (`backend/api/activity_auto.py`: one job per Dataset, chunks of 25 newest first, values kept on disk per activity file); `{state computing / ready / error, n_done, n_total, stale, auto}` — the page polls. **Per activity since SP-334**: a cheap whole-data `signature` (`backend/api/activity_auto.py:241`) only decides whether a job starts; the job (in the request's tenant context) then builds every activity's own key (`_Keys`, `backend/api/activity_auto.py:341`) and recomputes only the activities whose key changed — see 「Auto values per activity」 below |
| GET | `/api/v1/wko5/sports` | the 圖表分析 activity-type filter: the kinds present with counts, in the filter's order (`backend/api/wko5views.py:1307`, SP-263) |
| GET | `/api/v1/wko5/activities/stats` | `{key: {avg_hr, avg_power}}` for the list columns |
| PATCH | `/api/v1/wko5/activities` | key-based single or bulk edit (≤ 500 items `{key, file?}`): any tag field (incl. `pain_score`, `poles`, `surface`; `BulkBody`, `backend/api/wko5views.py:1230`), plus `add_tags` / `remove_tags`; works for excluded files |
| GET | `/api/v1/wko5/workouts` | each item also has `power_source` and `power_label` (「手錶推估功率（未採用）」 for unused watch power); `tss_source` is no longer `power` for a blocked watch run |
| PATCH | `/api/v1/wko5/workouts/{idx}/activity` | as above; keyed by start minute + file, so it applies across sources; also `exclusion` (`keep` / `exclude` / null) and `pack_kg`; the GET has `exclusion_state` {override, flagged, enabled} |
| GET | `/api/v1/wko5/workouts` (excluded rows) | an excluded file is listed with `index: null` and `excluded` {key, label, reason, rule, auto, manual, override, avg_kmh} |
| GET | `/api/v1/wko5/exclusions` | `{enabled, setting, source, excluded: […], kept: […]}` of the current source |
| PUT | `/api/v1/wko5/exclusions` | `{key, file?, exclusion}`: override any activity by its start minute; 400 bad key / value |
| PUT | `/api/v1/sync/settings` | `exclude_bad_activities` ↔ `activities.exclude_bad` |

The DB-row `GET /api/v1/workouts` and `/api/v1/workouts/{id}` (and the SPA's mmp / timeseries / zones /
trail routes) were removed with the React SPA on 2026-10-04 (SP-48; `backend/api/workouts.py:1`);
the `/api/v1/workouts` router keeps only the two PATCH routes above.

### Auto values per activity (SP-334)

`activity_auto.json` (FIT cache folder; `activity_auto_<hash>.json` for WKO5) holds one entry per
activity file: `{key, own, pcode, probe, start, auto}` (`save_cache`,
`backend/api/activity_auto.py:547`; a failed write removes its `.tmp`). The key (`_Keys.key`,
`backend/api/activity_auto.py:491`) is a hash of:

- **global**: `CACHE_V` (2), the Dataset type / source / engine config, `power.accept_watch_power`,
  the 每人校正 `effort_rest_max` (P9, `activity_tags._rest_max`: read by `effort_hr` /
  `effort_from_rpe` of every activity; review fix);
- **the branch and its code**: `run` (outdoor runs: `capacity_samples`) or `other`, each with the
  hash of only the functions it reaches (`_branch_code`, `backend/api/activity_auto.py:138`,
  `engine/codehash.py`, SP-320 ①) — a changed rule recomputes only the results of its branch;
- **own** (`_Keys._own`, `backend/api/activity_auto.py:420`): file, file stamp, sport, sport type,
  tags, title, start, platform, the corrections of the file, the day's threshold signature
  (`Dataset._settings_sig`), power use / source;
- **the recorded RPE row** `activity_tags.find` returns for it (looked up on the rows near it only:
  `_Near`, `backend/api/activity_auto.py:301`);
- **the day's thresholds** (`_Keys.thr_sig`, `backend/api/activity_auto.py:437`): the plan's
  threshold rows up to that day, the runs of the `thr_window_days()` (301) days before it that
  `thresholds.estimate` / `cp_as_of` read (day, file stamp, tags, power use / source, corrections,
  NP), the synced FITs and settings the PD refit reads (`_pd_inputs`,
  `backend/api/activity_auto.py:212`: `PdMemo.inputs`, `backend/engine/wko5expr/fitdataset.py:464`;
  a WKO5 Dataset without a PdMemo lists the same files with `synced_fit_files`,
  `backend/engine/wko5expr/fitdataset.py:392`) and — only on a day with no plan LTHR up to it — the
  Dataset's own LTHR history;
- **run**: the matched plan race, trail or not, and for road runs the road rule's cross-run values
  — HRmax as of the day and the longer power reference (`_Keys._cross`,
  `backend/api/activity_auto.py:408`, the real `maximal.hrmax_as_of` / `athlete.longer_power` on a
  window of runs); **other**: the plan 百岳 event of the day, the app type;
- **runs (the test rule, `athlete._test_reason`)** (`_Keys._test_part`,
  `backend/api/activity_auto.py:472`): the plan events and threshold rows of the day, the stored
  test sessions of the day (whether done by this activity), the user's 測試 mark, and the drift
  calibration (`workout_review.apply_calibration`: `drift.ok` → a steady AeT test) — so a weekly
  drift refit recomputes the runs only.

Every workout's `athlete.run_probe` (`backend/engine/racepower/athlete.py:638`: HRmax peak, the
(moving s, power) of a possible longer power reference, moving time) is kept with the entry and
reused while `own` and the run branch's code are unchanged (a kept entry's probe is refreshed when
the run code changed); `run_context`
(`backend/engine/racepower/athlete.py:654`) builds `capacity_samples`' cross-run lists from them
once per job (before, every chunk of 25 rebuilt them: 20 s of a 22 s full run on a 702-activity
synthetic history).

What recomputes what: a new activity → itself (its windows look back); deleting an old hike or
editing a note / effort mark → nothing; a user 測試 mark or a recorded RPE → that activity; a plan
threshold row → the activities from its day; a plan race → the runs of its day; a run reclassified
or deleted → it and the activities whose thresholds window (301 days) holds it, plus road runs whose
HRmax / power reference actually changes. Incremental values equal a full recompute
(`backend/tests/test_activity_auto_incremental.py`). The job trigger `signature`
(`backend/api/activity_auto.py:241`) covers every key input that can change while a Dataset lives —
the user's 測試 marks, the stored test sessions, the drift calibration and `effort_rest_max`
(`_calib_part`, `backend/api/activity_auto.py:204`), the engine config, each file's FTP, WKO5's
mFTP snapshot, the whole recorded RPE row — so a change starts a job (the test oracle checks it
for every mutation). Not the PD refit's synced FITs: a new synced FIT is a new Dataset
(`source_stamp`). A version-1 file (one signature) is served meanwhile and rebuilt once. The job
thread carries the request's tenant (`contextvars.copy_context`, like the warm-up).

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

Since 2026-10-06 the dialog also has:
- 疼痛 with the optional 0–10 「跑的時候最痛幾分」 (SP-271; `backend/static/painpicker.js:92`);
- 登山杖 — a 3-way chip choice 有杖 / 沒杖 / 未標, never typed (`backend/static/activity.html:720`);
  with no choice of the user's, a 「依賽事設定」 badge for a race marked 「會用登山杖」 (SP-300), and the
  hint 「再標有杖 N 次、沒杖 M 次…」 toward the comparison (SP-243, `poleHint`,
  `backend/static/activity.html:612`); the pole tags stay out of the tag chips;
- 路況 — a 3-way chip choice 乾 / 濕 / 未標 (`backend/static/activity.html:725`), and while it is 未標
  the rain hint with 「標成濕」 on 越野跑 / 登山健行 (SP-299, `rainHint`, `backend/static/activity.html:646`);
- the RPE line says 「自評：… （COROS）」 when the RPE came from COROS's post-run rating (SP-231,
  `backend/static/activity.html:685`).

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
- `workout_review.classify`: type 測試 = a test mark (`test_match` "user");
- the 有杖 vs 沒杖 comparison on the 能力 tab (SP-243, `engine/panels/pole_compare.py`): the pole
  marks, incl. 依賽事設定 — no other model reads them;
- the trail technicality factor split dry / wet and the calculator's 「路況」 choice (SP-250,
  `grade_model.fit_gait_re`): the 路況 marks;
- the 圖表分析 activity-type filter (SP-263, `sport_map.kind_of`): the user's 百岳跟團 / 爬山 type marks;
- the pain light (green / yellow / red) and the week's response (SP-271, `injuries.light`,
  `backend/engine/injuries.py:872`) and the app's 「好了」 proposal (SP-273, `injuries.done_check`,
  `backend/engine/injuries.py:911`): the pain marks and `pain_score`;
- the plan's self-rating rule (SP-231, `adapt.py` rule `rpe_hard`): an easy / long run rated Hard
  or more after the run pushes back the next hard session.

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
`backend/tests/test_activity_auto_incremental.py`: the per-activity keys (SP-334) — add, delete,
user marks, recorded RPE, plan threshold / race, reclassification, a changed branch's code, the old
format — each compared with a fresh full recompute; the row lookup and the cross-run window equal
the whole-list functions.
`backend/tests/test_effort_calib.py`: the per-athlete rest limit. `backend/tests/test_activity_key.py`:
start-time / file matching across sources.
`backend/tests/test_bad_activity.py`: the limits, car / vehicle-segment / power rules, descents /
sprints / GPS spikes / pauses not flagged, overrides, the FIT dataset leaving files out (contiguous
indices), keep / manual exclude through a tmp tags DB, the WKO5 policy renumbering, parity and the
setting off, `source_stamp`, `cptest.bad_files`, the list / exclusions / PUT endpoints, the old-table
load and the setting key (synthetic FITs, `fit_builder.build_run(speeds_m_s=…)`).
`backend/tests/test_activity_poles.py`: the 登山杖 mark (store, switch, clear, the 3-way editor, no
model reads it). `backend/tests/test_race_poles.py`: 依賽事設定 (every trail run / hike of the race day whatever the
distance or a hike record, multi-day trip, the user always wins). `backend/tests/test_pole_compare.py`: the comparison chart and its 5 + 5 gate.
`backend/tests/test_activity_surface.py`: the 路況 mark and the dry / wet split (≥ 30 windows each).
`backend/tests/test_rain_hint.py`: the rain stored without an extra call, the threshold, never over a
mark. `backend/tests/test_coros_rpe.py`: COROS's post-run rating as the RPE. `backend/tests/test_sport_map.py`:
the app-type table and the filter kinds.

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|---|---|---|---|
| How the 登山杖 mark is set and used | the user's own 有杖 / 沒杖 / 未標, stored as a free-form tag; no model reads it | detect it; correct the calculator / HR model / downhill bump by it | a watch cannot tell; the research found no correction worth applying — kept only for the comparison (SP-242) |
| When the 有杖 vs 沒杖 comparison shows | ≥ 5 有杖 and ≥ 5 沒杖 in the last 365 days, only trail runs and hikes count | always show; count every marked activity | only for someone who uses poles; a mark on a road run or a ride says nothing about the chart (SP-243, user decision 2026-10-06) |
| An activity of a race marked 「會用登山杖」 | 有杖 「依賽事設定」 at read time on every trail run / hike of the race day (each day of a trip), whatever the distance; the user's choice (incl. their own 未標) wins; no 「回到依賽事」 button | store the default; ask each time; only the date + kind + ±25 % distance match | it is what the user entered on the race, not a guess; unticking the race undoes it (SP-300, answered 2026-10-07) |
| How the 路況 is set | the user's own 乾 / 濕 / 未標; the archive rain only hints (≥ 1 mm, 推估) | mark wet automatically from the rain | rain on the trail is not the trail being wet; only marked activities enter the dry / wet groups (SP-250, SP-299) |
| Which activities get the rain hint | 越野跑 and 登山健行 (incl. 百岳 and the user's 爬山 mark) | every activity | the 路況 groups are about trails; a road run is never asked — owner 2026-10-07 (SP-299) |
| Rain of the activities cached before SP-299 | one backfill of the last 12 months on the server (~194 calls, paced) | leave them without rain; refetch on every build | owner 2026-10-07 (SP-299) |
| What counts as 百岳 in the activity-type filter | the user's 百岳跟團 mark, a plan 百岳 event that day, or a GPS-detected 百岳 summit | the type mark only | the user's answer 2026-10-06 (SP-263) |
| COROS's post-run self-rating | read at sync and used as the RPE at once (1–5 mapped to 10 points, 推估) | wait for more data before enabling | the user decided to enable it directly (SP-231, 2026-10-06) |
| The React SPA and its DB-row routes | removed (`frontend/`, `GET /api/v1/workouts`, `/{id}` …) | keep the SPA and spec it | the static pages are the UI; the SPA was unmaintained (SP-48, user decision 2026-10-04) |
| How the auto values decide what to recompute | one key per activity: its own inputs, the *values* of the cheap cross-activity inputs (HRmax, longer power, plan race, row lookups) and a 301-day runs window for the expensive one (the as-of thresholds); a branch code hash | dependency windows for everything; computing the thresholds of every day for the key | values recompute only where the answer can change; the thresholds estimate is the costly part, so it is keyed on its inputs (SP-334) |
| Where the per-activity entries live | the existing `activity_auto.json`, one entry per file | a SQLite table | one small file per dataset already, rewritten whole once per job (~700 entries); no new store to register / back up (SP-334) |

## Open Questions

- [ ] `activity_tags` rows get a stable id (UUID); `updated_at` is already written（SP-310，Todo）——尚未實作
- [ ] A change-log sync trial for `user_settings` and `activity_tags`（SP-312，Todo）——尚未實作
- [ ] Manual FIT import (pick files / a folder) on the 活動列表 page（SP-323，Todo）——尚未實作

## Change History

| Date | Type | Feature SRS | Summary |
|------|------|-------------|---------|
| 2026-10-01 | feature | user request | Activity tags (type / effort / note), auto + user override, API, 活動資訊 card, seed script |
| 2026-10-01 | feature | user request (COROS vs TP back-test) | Power source per workout (stryd / watch / none), `power.accept_watch_power` (default false), API fields and the 功率來源 line on the activity card; tests `backend/tests/test_power_source.py` |
| 2026-10-01 | feature | user request (bad activity files) | Bad activity files excluded from every model (vehicle / bike speed vs world-record limits, impossible power), whole-file exclusion, keep / exclude overrides in `activity_tags.exclusion`, setting `activities.exclude_bad`, API, list / card / settings UI; tests `backend/tests/test_bad_activity.py` |
| 2026-10-01 | bugfix | docs/research/unsourced-rules.md §0.10 step 0 | Seed matches COROS / TP races by the WKO5 start (±3 min), `--source` defaults to the data source; documented that the tags live in the app DB (table created on first write) |
| 2026-10-04 | code-sync | N/A | Domain Model; name / free-form tags / pain columns; watch RPE in the effort precedence; trail 全力 by x*(T) − 0.03 and per-athlete rest limit; 活動編輯 page + `/activities`, `/activities/auto`, `/activities/stats`, bulk PATCH; 活動資訊 card removed; terrain `auto` reset; seed from a JSON file; file match without `coros/` / `tp/` prefix |
| 2026-10-07 | fix | SP-341 | The bad-file and power-source caches (`bad_activity_v1.json`, `power_source_v1.json`, `racepower_bad_activity.json`, `racepower_power_source.json`) carry a code version: a changed algorithm recomputes them; registered in `backend/data_registry.py` (SP-311) |
| 2026-10-08 | code-sync（SP-231, 242, 243, 250, 263, 269, 271, 299, 300, 48） | N/A | 登山杖 mark and 依賽事設定, the 有杖 vs 沒杖 gate; 路況 mark and the rain hint; COROS self-rating as the RPE; app type / 百岳 filter kind and `GET /wko5/sports`; pain score; API fields (`poles`, `surface`, `pain_score`, `rain_mm`, `self_rating` …); DB-row GET routes removed; `/activities/auto` signature; anchors re-checked; Decisions Log and Open Questions added |
| 2026-10-08 | SP-299 follow-up | owner decision 2026-10-07 (ticket SP-299) | The rain hint only on 越野跑 / 登山健行 (`rain_kind` / `rain_kind_excluded`, `rain_hint(…, trail)`); `rain_kind` on `GET /activities` and the single-activity JSON, copied by `patchLocal`; 路況 help (zh-TW + en) says road runs are not asked; the one-time rain backfill (route-progress.spec.md). Tests `test_rain_hint.py::test_road_runs_get_no_rain_hint_trail_and_hike_do`, `::test_rain_kind_rule`, `test_rain_backfill.py` |
| 2026-10-08 | SP-299 review | code review of fix/activity-sp81-299-300-258 | An excluded file's rain kind also reads its trail classification (L4); its row carries `rain_kind_auto` and a type change edited by key updates `rain_kind` on the page (L2). Tests `test_rain_hint.py::test_rain_kind_rule`, `::test_excluded_row_carries_its_auto_kind_and_the_page_follows_a_type_change` |
| 2026-10-08 | SP-300 follow-up | owner decision 2026-10-07 (ticket SP-300) | 依賽事設定 covers every trail run / hike on the race day (each day of a multi-day trip) whatever the watch distance, incl. a 越野賽 recorded as a hike; the 1-day match still adds a road race's road run; the auto 比賽 type is unchanged; 登山杖 help (zh-TW + en) says so. Tests `test_race_poles.py::test_one_day_race_covers_every_trail_run_and_hike_of_the_day`, `::test_race_day_default_never_beats_the_users_choice` |
| 2026-10-08 | perf | SP-334 | `/activities/auto` per activity: one key per activity (own inputs, the day's thresholds and their 301-day runs window, the day's plan rows, the road rule's cross-run values, the branch's code hash) in `activity_auto.json` (v2); only changed activities recompute (`backend/api/activity_auto.py:284`); `capacity_samples` takes the cross-run context built once per job (`athlete.run_probe` / `run_context` / `longer_power`, `backend/engine/racepower/athlete.py:638`); `PdMemo.inputs`; synthetic 702-activity history: full 20 s → 1.1–1.8 s, one added activity 0.3–0.5 s; tests `backend/tests/test_activity_auto_incremental.py` |
| 2026-10-08 | review fix | SP-334 code review | `effort_rest_max` (每人校正 P9) in every activity's key and the job signature (a refit or manual change recomputed nothing before); the drift calibration only in the runs' key; the job thread keeps the tenant context; WKO5 Datasets list the PD refit's synced FITs (`synced_fit_files`); the run's day in the thresholds window row; the dataset's own LTHR only on days without a plan LTHR; signature covers config / FTP / mFTP snapshot / the whole RPE row; `.tmp` removed on a failed write; kept entries refresh their probe; tests for test sessions, re-index + done_by, a bad file, own LTHR, watch power, time zone, tenants |
| 2026-10-08 | feature | SP-57 | The recorded RPE (FIT or COROS's post-run rating) also feeds the per-level 「負荷」 RPE conversion (`backend/engine/rpe_load.py:280`); COROS FITs re-checked: no RPE field, the rating comes from the activity detail (SP-231) |
| 2026-10-08 | review fix | SP-57 code review | A FIT RPE maps two values per level (6 → 累, Seiler zone 2 = session RPE 5–6; COROS 1–5 unchanged), `backend/engine/rpe_load.py:121` |
