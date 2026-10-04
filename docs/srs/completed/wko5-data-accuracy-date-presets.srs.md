# SRS: Chart Data Accuracy Fix + Date Range Presets

> ⛔ **CANCELED（2026-10-04）**：初始 CTL/ATL 種子、Config 頁、`/api/v1/analytics/run-load` 與 React 日期預設按鈕已隨 `frontend/` 刪除（`backfill-tss` 端點從未實作）。本文是當時的設計紀錄。

## Metadata
- **Source PRD**: `docs/prd/wko5-training-ai.prd.md` (Milestones 4.6, 4.7)
- **Source Linear Issue**: N/A
- **Owner**: maintainer
- **Status**: CANCELED (2026-10-04)
- **Generated**: 2026-05-15

## Summary

Fixes systematic under-reporting of running CTL/ATL caused by missing historical TSS data (363 WKO4
workouts have no TSS because `parse_wko4_metadata()` reads only `start_time`/`sport`), and adds an
initial CTL seed mechanism so the EWMA can be primed from the user's known WKO5 values. Separately,
extends `DateRangePicker` with calendar-aware presets (This Month, Last Month, Last 3M, YTD, etc.)
for rapid chart navigation.

---

## Root Cause Analysis

### Current data state (2026-05-15)

| Source | Count | Has TSS? | Reason |
|---|---|---|---|
| Coros FIT (recent months) | 59 running | Yes (power-based) | FIT has power channel; FTP set |
| WKO4 binary (earlier years) | 363 running | No | `parse_wko4_metadata()` reads only `start_time` + `sport` |
| **Total running** | **422** | **14% have TSS** | — |

**Effect**: `compute_run_pmc()` starts EWMA from 0 at the first Coros activity; max CTL reached = 26.
WKO5 Season View shows CTL built over several years; typical steady-state ≈ 40–70 for consistent runner.

### Why WKO5 values differ

WKO5 computes `rTSS` (running TSS) from pace/HR even for workouts without power:
```
rTSS = (duration_s / 3600) × NGP_IF² × 100
where NGP_IF = normalized_graded_pace / threshold_pace
```
WKO4 files contain `_ragpace` (rolling average pace) and `elapseddistance` channels (confirmed via
`strings` inspection). Extracting these enables rTSS calculation for all historical runs.

---

## System Context

### Scope & Boundaries

**In scope**:
- `AthleteSettings`: 3 new fields — `initial_ctl_run`, `initial_atl_run`, `threshold_pace_s_per_km`
- `compute_run_pmc()`: accept `initial_ctl` / `initial_atl` seed parameters
- `POST /api/v1/athletes/{id}/backfill-tss`: re-compute TSS for all workouts using current FTP
- WKO4 minimal pace extractor: read `duration_s` and `total_distance_m` from WKO4 binary (already stores these as readable field names in the file body)
- `DateRangePicker.tsx`: add calendar-aware preset group
- `ConfigPage`: add "Run PMC Seed" settings section

**Out of scope**:
- Full WKO4 timeseries parsing (power channel binary decoding — future milestone)
- NGP (Normalized Graded Pace) — simplified rTSS using average pace only (no elevation correction)
- Multi-athlete support
- rHR TSS (HR-based TSS) — not enough LTHR calibration data

### Actors

| Actor | Type | Interaction |
|---|---|---|
| Athlete | Human | Enters initial CTL/threshold pace from WKO5; triggers TSS backfill |
| `compute_run_pmc()` | Algorithm | Accepts seed values; produces correct historical CTL curve |
| `backfill-tss` endpoint | Batch service | Re-processes all workout files for missing TSS metrics |

---

## Architecture

### High-Level Diagram

```
WKO5 (user reads CTL value)
    │ manual input
    ▼
ConfigPage ──PUT /athletes/1/settings──► AthleteSettings
                                             ├── ftp_w (existing)
                                             ├── threshold_pace_s_per_km  [NEW]
                                             ├── initial_ctl_run          [NEW]
                                             └── initial_atl_run          [NEW]

GET /api/v1/analytics/run-load
    │
    ├── query: run TSS by date (existing)
    ├── fetch settings: initial_ctl_run, initial_atl_run
    └── compute_run_pmc(series, initial_ctl=..., initial_atl=...)

POST /api/v1/athletes/1/backfill-tss               [NEW]
    │
    ├── for each run workout missing TSS:
    │     if has power → recompute power-TSS with current FTP
    │     elif has distance+duration → compute pace rTSS with threshold_pace
    └── upsert WorkoutMetric(tss=...)

WKO4 minimal extractor                              [NEW]
    ├── read _ragpace / elapseddistance from existing wko4 file body
    └── populate WorkoutFile.duration_s, total_distance_m on re-scan
```

### Components

| Component | Responsibility | Interface |
|---|---|---|
| `AthleteSettings` (extended) | Store seed CTL/ATL and threshold pace | ORM model + `_migrate_schema()` |
| `compute_run_pmc(initial_ctl, initial_atl)` | EWMA with non-zero start | `backend/engine/algorithms/metrics.py` |
| `POST /backfill-tss` | Re-compute TSS for all workouts missing it | FastAPI route, async, streams progress |
| WKO4 minimal extractor | Extract `duration_s` + `total_distance_m` from WKO4 binary | `backend/files/wko4_reader.py` extension |
| `pace_rtss(distance_m, duration_s, threshold_pace_s_per_m)` | rTSS from pace | New function in `metrics.py` |
| `DateRangePicker` (extended) | Calendar-aware preset buttons | React component, no API calls |

### Data Flow — seeded CTL

```
User sets initial_ctl_run=55 in Config
  → AthleteSettings record updated
  → GET /run-load fetches initial_ctl_run=55
  → compute_run_pmc(run_series, initial_ctl=55, initial_atl=35)
     → EWMA starts from CTL=55 at day 0 instead of 0
  → Chart shows CTL converging correctly from the first synced activity onward
```

### Data Flow — TSS backfill

```
POST /athletes/1/backfill-tss
  1. SELECT workouts WHERE sport='running' AND no TSS metric
  2. For each:
     a. If file exists + has_power: re-parse FIT → compute_all_metrics(ftp_w=current)
                                  → upsert WorkoutMetric(tss=...)
     b. If has distance+duration + threshold_pace set:
        pace_rtss(distance_m, duration_s, threshold_pace)
        → upsert WorkoutMetric(tss=..., metric_key='rtss_pace')
  3. Return {recomputed_power: N, recomputed_rtss: M, skipped: K}
```

---

## Data Model

### Schema Changes

```sql
-- AthleteSettings: 3 new nullable columns
ALTER TABLE athlete_settings ADD COLUMN threshold_pace_s_per_km REAL;
-- Threshold pace in sec/km (e.g. 300 = 5:00/km). Used for rTSS.

ALTER TABLE athlete_settings ADD COLUMN initial_ctl_run REAL;
-- User-provided starting CTL from WKO5 (seeds EWMA). NULL = start from 0.

ALTER TABLE athlete_settings ADD COLUMN initial_atl_run REAL;
-- User-provided starting ATL. NULL = start from 0.
```

### Migration Strategy

- **Forward**: 3 idempotent `ALTER TABLE ADD COLUMN` via `_migrate_schema()` in `database.py`
- **Backward**: Columns are nullable; removing them needs a migration but no data loss
- **Backfill**: Not needed — all three fields are optional (NULL = legacy behavior)
- **Coexistence**: If `initial_ctl_run` is NULL, `compute_run_pmc()` starts from 0 (current behavior)

### WorkoutMetric new key

| Key | Unit | Source | Notes |
|---|---|---|---|
| `rtss_pace` | TSS points | pace rTSS formula | Only added if no power-TSS exists for that workout |

Existing `tss` key stays as the canonical TSS. `rtss_pace` is stored separately to distinguish estimation from measurement.

---

## Algorithm Contracts

### `compute_run_pmc(series, initial_ctl=0.0, initial_atl=0.0, ctl_tau=42, atl_tau=7, ramp_days=7)`
- Signature change: add `initial_ctl: float = 0.0, initial_atl: float = 0.0`
- Start EWMA loop with `ctl = initial_ctl, atl = initial_atl` instead of `0.0`
- Existing tests pass with defaults (backwards-compatible)

### `pace_rtss(distance_m, duration_s, threshold_pace_s_per_m) → float`
- Simplified rTSS without NGP: `IF = threshold_pace_s_per_m / (duration_s / distance_m)`
- `rTSS = (duration_s / 3600) × IF² × 100`
- Returns 0.0 if any input is None or ≤ 0
- Applied only when workout has no power data

### WKO4 minimal extractor (extension to `wko4_reader.py`)
- WKO4 binary contains human-readable field names (`_ragpace`, `elapseddistance`, timestamp strings)
- Strategy: scan for `2025-01-01T07:00:00` style timestamp → extract duration from file metadata
- Extract `total_distance_m` from `elapseddistance` field encoding (needs one-time binary format study per field)
- **Risk**: WKO4 binary uses variable-length encoding — field extraction may be partial (see Risks section)

---

## API Contracts

### Modified Endpoint

```json
// GET /api/v1/analytics/run-load?athlete_id=1&date_from=...&date_to=...
// No request change. Response adds two new fields:
{
  "series": [...],
  "athlete_id": 1,
  "seeded": true,           // true if initial_ctl_run was applied
  "initial_ctl_used": 55.0  // null if started from 0
}
```

### New Endpoint

```
POST /api/v1/athletes/{athlete_id}/backfill-tss
Request: {} (no body)

Response — 200 OK:
{
  "recomputed_power_tss": 12,  // workouts that had power, got TSS via FTP
  "computed_rtss_pace": 45,    // workouts that got pace-based rTSS
  "skipped_no_data": 306,      // WKO4 with no usable data (no distance/duration)
  "skipped_already_has_tss": 59
}
```

### Modified Settings Endpoint

```json
// PUT /api/v1/athletes/1/settings
// Request — new fields (all optional):
{
  "ftp_w": 220,
  "lthr": 160,
  "threshold_pace_s_per_km": 300,   // 5:00/km threshold pace
  "initial_ctl_run": 55.0,          // seed from WKO5
  "initial_atl_run": 35.0           // seed from WKO5
}

// GET /api/v1/athletes/1/settings — response includes new fields
```

---

## Frontend: Date Range Presets

### Current State

`DateRangePicker.tsx` already has 5 rolling-window presets: `1M` (30d), `3M` (90d), `6M` (180d), `1Y` (365d), `All` (10yr).

### New Preset Groups

**Rolling window** (keep existing, rename for clarity):
- `30d`, `90d`, `6M`, `1Y`, `All`

**Calendar-aligned** (new group):
| Label | From | To |
|---|---|---|
| 本月 | First day of current month | Today |
| 上月 | First day of prev month | Last day of prev month |
| 近3月 | 3 calendar months ago (1st) | Today |
| 近6月 | 6 calendar months ago (1st) | Today |
| 今年 YTD | Jan 1 of current year | Today |
| 去年 | Jan 1 of prev year | Dec 31 of prev year |

### Component Contract

`DateRangePicker` accepts same `{ value, onChange }` props. No new props needed.
Calendar calculations use native `Date` — no additional dependencies.

**Active preset highlighting**: track which preset is active by comparing `value` to computed preset
ranges (exact string match). Highlight the matching button; clear if manual date input is used.

---

## UI: ConfigPage — Run PMC Seed

New section in `ConfigPage` below the FTP form:

```
─── Run Training Load Settings ──────────────────────
Threshold Pace (sec/km)  [300    ] = 5:00/km
                         Used for pace-based rTSS on GPS-only runs

Seed CTL from WKO5                     [optional]
  Initial CTL (Run)      [      ]  TSS/day  (read from WKO5 Season View)
  Initial ATL (Run)      [      ]  TSS/day  (read from WKO5 Season View)
  Seed date              [YYYY-MM-DD]  (date these values are from)
  (Note: Seeds the EWMA start. Use today's WKO5 values for most accurate results.)

  [Backfill TSS]  ← runs POST /backfill-tss, shows result toast
─────────────────────────────────────────────────────
```

---

## Non-Functional Requirements

| Category | Target | How Achieved |
|---|---|---|
| Backfill performance | <30s for 422 workouts | Async generator, streams JSON progress; FIT re-parse is in-memory |
| CTL accuracy after seed | Within 5 TSS/day of WKO5 after 30 days | EWMA convergence; seeded start removes the most error |
| Preset responsiveness | Instant (<50ms) | All calendar math is synchronous JS |
| Backwards compatibility | All existing API consumers unaffected | New settings fields are optional; compute_run_pmc defaults unchanged |

---

## Technology Choices

| Concern | Choice | Alternatives | Rationale |
|---|---|---|---|
| Calendar preset math | Native JS `Date` | `date-fns`, `dayjs` | No new dependency; only 6 presets |
| rTSS formula | Simplified (avg pace, no NGP) | NGP (elevation-corrected) | No elevation-per-second channel in WKO4; close enough for CTL shape |
| WKO4 data extraction | Regex on string-encoded fields | Full binary parser | WKO4 encodes some fields as readable strings (confirmed); full parse is future work |
| Initial CTL seed | Manual user input | Auto-read from WKO5 SQLite | WKO5 uses custom binary format (`wko5home`); SQLite not available |

---

## Integration Points

| Touchpoint | Type | Impact | Backwards Compat |
|---|---|---|---|
| `compute_run_pmc()` | Function signature | New optional params `initial_ctl`, `initial_atl` | Yes — defaults=0.0 |
| `GET /analytics/run-load` | HTTP | Passes seed from settings to algorithm | Yes — new response fields only |
| `AthleteSettings` ORM | Schema | 3 new nullable columns | Yes — `_migrate_schema()` |
| `DateRangePicker` | React component | New preset buttons; existing API unchanged | Yes — same `{ value, onChange }` props |
| `ConfigPage` | React page | New form section; existing sections unaffected | Yes |

---

## Risks & Trade-offs

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| WKO4 field extraction fails (binary encoding varies by WKO version) | M | M | Fields available only for duration/distance (not power); fallback to `skip` silently |
| Initial CTL seed becomes stale as user forgets to update | H | L | Show "seeded from: {date}" label in Config; add note to re-seed after long breaks |
| rTSS pace formula overestimates recovery run TSS | M | M | `rtss_pace` stored as separate key from `tss`; only used if no power-TSS exists |
| Backfill alters previously correct TSS values | L | H | Backfill uses `INSERT OR IGNORE` (upsert only if missing); never overwrites existing TSS |
| Calendar preset "last month" edge cases (Feb 28/29, DST) | L | L | Use JS `Date` month arithmetic; test Feb 28 and DST transition |

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|---|---|---|---|
| Seed CTL via manual input | User reads WKO5, enters value | Auto-parse WKO5 `wko5home` binary | WKO5 uses custom binary format; manual input is reliable and takes 30s |
| rTSS formula | Simple avg pace ratio | NGP, HR-based | No elevation/second data in WKO4; HR-based requires LTHR calibration |
| Backfill never overwrites | `INSERT OR IGNORE` on TSS | Always recompute | Preserves power-measured TSS; only fills gaps |
| Active preset highlight | JS string comparison of ISO dates | Zustand preset state | Stateless; computed from value, no extra state |
| Preset groups | Two rows (rolling + calendar) | Single merged list | Clearer semantics; rolling for trend windows, calendar for review periods |

---

## Open Questions

- [ ] Should `initial_ctl_run` apply to the overall PMC chart too, or only to the Run Training Load chart? (Currently only run-load endpoint uses it)
- [ ] Should `threshold_pace_s_per_km` be per-date (like FTP history) or a single global value? Current design: single value; historical pace FTP not tracked
- [ ] WKO4 pace data extraction feasibility: needs one test run on a known WKO4 file to confirm field byte offsets before committing to implementation
- [ ] Should the Backfill endpoint be protected (require confirmation) to avoid accidental re-processing?
