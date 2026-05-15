# SRS: WKO5 Training Load Charts — Run-Specific Analytics

## Metadata
- **Source PRD**: N/A — standalone technical spec
- **Source Linear Issue**: N/A
- **Owner**: maintainer
- **Status**: IMPLEMENTED — 2026-05-15
- **Generated**: 2026-05-15
- **Implementation Report**: `docs/reports/wko5-training-load-charts-feature-report.md`

## Summary

Implements five run-specific training load charts reverse-engineered from `WKO5 Season View.wko5chart`: Chronic/Acute TIS Load, Daily % of CTL, CTL Ramp Rate, Intensity Load Chart, and Running Volume Log (跑量日誌). All formulas were extracted verbatim from the `.wko5chart` binary; no estimation was required. New backend endpoints extend the existing FastAPI + SQLAlchemy async pattern; new frontend components extend the existing Recharts stack and are wired into `SeasonPage` as collapsible sections.

---

## Reverse-Engineered Formulas (Source of Truth)

The following WKO5 expression language snippets were extracted from `WKO5 Season View.wko5chart`. These are the authoritative formulas the implementation must match.

### CTL / ATL (Run TIS Load)
```
CTL_run = tl(if(sport="run", tss), ctlconstant)   // EWMA, tau=42 days
ATL_run = tl(if(sport="run", tss), atlconstant)   // EWMA, tau=7 days
TSB_run = shift(CTL_run - ATL_run, 1)             // yesterday's form
ACWR    = ATL_run / CTL_run
```
Math: same as `compute_pmc()` in `backend/engine/algorithms/metrics.py:137` — only the TSS input must be filtered to `sport="run"` workouts.

### Daily % of CTL
```
@ctlPercent := if(sport="run", tss) / tl(if(sport="run", tss), ctlconstant)
// color bands:
if(@ctlPercent <= 1.5, @ctlPercent)          // green  — "safe"
if(@ctlPercent > 1.5 and @ctlPercent < 3, …) // yellow — "caution"
if(@ctlPercent >= 3, @ctlPercent)             // red    — "danger"
```
Returns `run_tss_today / ctl_run_today`. Exposed as a field on the run PMC response.

### CTL Ramp Rate
```
ramp_rate = (CTL_today - shift(CTL, rampconstant)) / (rampconstant / 7)
// WKO5 default: rampconstant = 7 → unit = TSS/day/week
// Reference lines: 0 and +7 TSS/day/week
ramp_pct_ctl = ramp_rate / CTL
```
`shift(x, n)` = value of x n days ago. With rampconstant=7: `ramp_rate = CTL_today - CTL_{today-7}`. Already has a column stub (`ramp_rate`) in `PmcCache`.

Planned ramp uses `plannedtss` for future dates; completed ramp uses actual `tss`.

### Intensity Load Chart
```
// Per-workout time in zone (seconds), stored as WorkoutMetric:
high_intensity_95pct_s  = sum(if(runpower >= 0.95 * runFTP, deltatime))
high_intensity_103pct_s = sum(if(runpower >= 1.03 * runFTP, deltatime))

// Then apply EWMA (same tl() function):
Chronic_Intensity_≥95%  = tl(high_intensity_95pct_s,  ctlconstant)  // tau=42
Acute_Intensity_≥95%    = tl(high_intensity_95pct_s,  atlconstant)  // tau=7
Chronic_Intensity_≥103% = tl(high_intensity_103pct_s, ctlconstant)
Acute_Intensity_≥103%   = tl(high_intensity_103pct_s, atlconstant)
```
Requires FIT power timeseries re-parse during import. Values stored as new `WorkoutMetric` keys.

### Running Volume Log (跑量日誌)
```
Weekly run distance:  sum(if(sport="run", distance), "week")
Weekly run duration:  sum(if(sport="run", duration), "week")
Monthly distance:     sum(if(sport="run", distance), "month")
Monthly elevation:    sum(if(sport="run", climbing / 100), "month")  // in 100m units
// sFTP as % of VO2max (gauge):
brev(sqrt(sum(if(sport="run",tss),"week") /
      sum(if(sport="run",tssduration*isvalid(tss)),"week") * 36))
```
Uses `WorkoutFile.total_distance_m`, `WorkoutFile.duration_s`, `WorkoutFile.sport`. Elevation requires new `elevation_gain_m` column on `WorkoutFile` (populated from FIT record `total_ascent`).

---

## System Context

### Scope & Boundaries
- **In scope**: 5 new chart components in collapsible sections of `SeasonPage`; 3 new API endpoints; 2 new algorithm functions; 2 new `WorkoutMetric` keys; 1 new `WorkoutFile` column; idempotent migration for `elevation_gain_m`
- **Out of scope**: Planned TSS input (no planning feature), multi-sport intensity charts, HR-based TIS, re-implementing TP sync, mobile layout

### Actors
| Actor | Type | Interaction |
|---|---|---|
| Athlete | Human | Views load charts on Load tab; selects date range |
| FIT Importer | Internal service | Computes and stores `high_intensity_95pct_s`, `high_intensity_103pct_s`, `elevation_gain_m` per workout at import time |
| FastAPI backend | Service | Serves aggregated time series to frontend |

### External Dependencies
| Dependency | Purpose | Failure Mode |
|---|---|---|
| SQLite `~/.wko5coach/wko5coach.db` | All training data | Charts show empty state |
| FIT file on disk | Intensity metric computation at import | Metric skipped; intensity chart empty for that workout |
| `numpy` | EWMA and rolling calculations | Already a hard dependency |

---

## Architecture

### High-Level Diagram
```
Browser (React + Recharts)
  └── SeasonPage  (frontend/src/pages/SeasonPage.tsx)
        ├── [existing] PmcChart           ← Overall PMC section
        ├── [existing] WeeklyLoadChart    ← Weekly Load section
        └── [new] collapsible sections
              ├── RunLoadChart          ← CTL/ATL/TSB/ACWR (run-only)
              ├── DailyPctCtlChart      ← colored bar chart
              ├── RampRateChart         ← bar + reference lines
              ├── IntensityLoadChart    ← 4 series (chronic/acute × 95%/103%)
              └── RunVolumeLog          ← weekly bar + monthly table

FastAPI
  ├── GET /api/v1/analytics/run-load         [NEW]
  ├── GET /api/v1/analytics/intensity-load   [NEW]
  └── GET /api/v1/analytics/run-volume       [NEW]

backend/engine/algorithms/metrics.py
  ├── compute_pmc()                   [existing — reused unchanged]
  ├── compute_run_pmc()               [NEW — sport="run" filter]
  └── compute_intensity_load_series() [NEW — EWMA over time-in-zone]
```

### Components
| Component | Responsibility | Interface |
|---|---|---|
| `compute_run_pmc()` | Filter TSS to run workouts, call `compute_pmc()`, append `daily_pct_ctl` and `ramp_rate` fields | `(tss_series, run_tss_series, ctl_tau, atl_tau, ramp_days) → list[dict]` |
| `compute_intensity_load_series()` | EWMA of per-workout high-intensity seconds | `(intensity_series: list[(date, float)], tau) → list[dict]` |
| `GET /api/v1/analytics/run-load` | Query run TSS, return run PMC + daily %CTL + ramp | JSON (see API Contracts) |
| `GET /api/v1/analytics/intensity-load` | Query `high_intensity_95pct_s` and `_103pct_s` metrics, return 4-series EWMA | JSON |
| `GET /api/v1/analytics/run-volume` | GROUP BY week/month on run workouts | JSON |
| `RunLoadChart` | Recharts LineChart with CTL/ATL/TSB/ACWR | Props: `dateFrom`, `dateTo` |
| `DailyPctCtlChart` | Recharts ComposedChart bar chart with 3 color series | Props: `dateFrom`, `dateTo` |
| `RampRateChart` | Recharts LineChart + ReferenceLine at 0 and +7 | Props: `dateFrom`, `dateTo` |
| `IntensityLoadChart` | Recharts LineChart, 4 series in 2 colors (chronic=solid, acute=dashed) | Props: `dateFrom`, `dateTo` |
| `RunVolumeLog` | Recharts BarChart (weekly distance/duration) + summary table | Props: `dateFrom`, `dateTo` |

### Data Flow
```
FIT import path (write):
  FitParser → parse power channel → sum(if power ≥ 0.95*ftp, dt) → WorkoutMetric(high_intensity_95pct_s)
                                  → sum(if power ≥ 1.03*ftp, dt) → WorkoutMetric(high_intensity_103pct_s)
  FitParser → read total_ascent field → WorkoutFile.elevation_gain_m

Read path:
  Browser GET /run-load → FastAPI queries WorkoutFile+WorkoutMetric (sport="run", metric="tss")
                       → compute_run_pmc() → JSON
  Browser GET /intensity-load → query metric_key IN (high_intensity_95pct_s, high_intensity_103pct_s)
                             → compute_intensity_load_series() × 4 → JSON
  Browser GET /run-volume → GROUP BY week, month → JSON
```

---

## Data Model

### Entities
| Entity | Owner | Change |
|---|---|---|
| `WorkoutFile` | `backend/db/models.py` | Add `elevation_gain_m: Optional[float]` column |
| `WorkoutMetric` | `backend/db/models.py` | 2 new metric keys: `high_intensity_95pct_s`, `high_intensity_103pct_s` |

### Schema Changes

```sql
-- Migration: add elevation column
ALTER TABLE workout_files ADD COLUMN elevation_gain_m REAL;

-- No DDL needed for WorkoutMetric — key-value table accepts new keys automatically
-- New metric keys used:
--   high_intensity_95pct_s   REAL  -- seconds with runpower >= 0.95 * runFTP
--   high_intensity_103pct_s  REAL  -- seconds with runpower >= 1.03 * runFTP
```

### Migration Strategy
- **Forward**: Idempotent `ALTER TABLE ADD COLUMN` via `_migrate_schema()` in `database.py` (uses `PRAGMA table_info` to skip if column already exists). No Alembic required.
- **Backward**: Column is nullable; removing it requires a schema migration but no data loss
- **Backfill**: Not implemented — new workouts get intensity metrics on next import. For historical workouts, `IntensityLoadChart` shows empty state (treated as 0 minutes in zone).
- **Coexistence**: Intensity Load Chart shows empty state if metrics absent; `elevation_gain_m` NULL displayed as `0 m` in RunVolumeLog

---

## Algorithm Contracts

### `compute_run_pmc(run_tss_series, ctl_tau=42, atl_tau=7, ramp_days=7)`
Extend output of `compute_pmc()` (same EWMA logic at `backend/engine/algorithms/metrics.py:137`) with:

```python
# Additional fields per day:
"acwr": round(atl / ctl, 3) if ctl > 0 else None
"daily_pct_ctl": round(run_tss_today / ctl, 3) if ctl > 0 else None
"ramp_rate": round(ctl_today - ctl_n_days_ago, 2)   # n = ramp_days = 7
"ramp_pct_ctl": round(ramp_rate / ctl, 3) if ctl > 0 else None
```

### `compute_intensity_load_series(intensity_by_date, tau)`
Same EWMA as `compute_pmc` applied to `intensity_by_date: dict[date, float]` (seconds in zone).
Returns `[{"date": "...", "value": float}, ...]` in minutes (divide seconds by 60 for display).

---

## API Contracts

### Endpoints
| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/analytics/run-load` | Run-specific PMC: CTL, ATL, TSB, ACWR, daily %CTL, ramp rate | None (single athlete) |
| GET | `/api/v1/analytics/intensity-load` | Chronic/Acute intensity time in zone (95% and 103% FTP) | None |
| GET | `/api/v1/analytics/run-volume` | Weekly + monthly run distance, duration, elevation | None |

### Request / Response Shape

```json
// GET /api/v1/analytics/run-load?athlete_id=1&date_from=2025-01-01&date_to=2026-05-15
// Response — 200 OK
{
  "series": [
    {
      "date": "2025-01-01",
      "ctl": 42.5,
      "atl": 38.1,
      "tsb": 4.4,
      "tss": 80.0,
      "acwr": 0.90,
      "daily_pct_ctl": 1.88,
      "ramp_rate": 0.4,
      "ramp_pct_ctl": 0.009
    }
  ],
  "athlete_id": 1
}

// GET /api/v1/analytics/intensity-load?athlete_id=1&date_from=2025-01-01&date_to=2026-05-15
// Response — 200 OK
{
  "series": [
    {
      "date": "2025-01-01",
      "chronic_95pct_min": 12.3,
      "acute_95pct_min": 18.7,
      "chronic_103pct_min": 4.1,
      "acute_103pct_min": 6.2
    }
  ],
  "athlete_id": 1
}

// GET /api/v1/analytics/run-volume?athlete_id=1&date_from=2025-01-01&date_to=2026-05-15
// Response — 200 OK
{
  "weeks": [
    {
      "week_start": "2025-01-06",
      "distance_km": 52.3,
      "duration_h": 4.8,
      "elevation_m": 340.0,
      "count": 5,
      "tss": 380.0
    }
  ],
  "months": [
    {
      "month": "2025-01",
      "distance_km": 215.4,
      "duration_h": 19.2,
      "elevation_m": 1240.0,
      "count": 18
    }
  ],
  "athlete_id": 1
}
```

### Error Codes
| Code | HTTP Status | Meaning |
|---|---|---|
| `ATHLETE_NOT_FOUND` | 404 | athlete_id does not exist |
| Standard FastAPI validation | 422 | Bad date format |

### Versioning Strategy
Path prefix `/api/v1/` inherited from existing routes. No deprecation needed — all new endpoints.

---

## Non-Functional Requirements

| Category | Target | How Achieved |
|---|---|---|
| Performance | p95 < 300ms for any chart endpoint, 1-year window | Pre-computed PMC via `compute_run_pmc()` on query; GROUP BY week in SQL with index on `workout_date` |
| Data freshness | Reflects imports within same request | No caching layer; computed on demand from DB |
| Intensity accuracy | Match WKO5 within 1% | Use `>= threshold` (not `>`) to match WKO5 `if(runpower >= 0.95*runFTP, deltatime)` |
| ACWR reference zones | 0.8–1.3 safe, >1.5 danger | Frontend `ReferenceArea` (Recharts `ReferenceArea` with `yAxisId="acwr"`); `ReferenceBand` not available in Recharts 3.8.1 |
| Ramp Rate reference lines | 0 and +7 TSS/day/week | Frontend `<ReferenceLine>` from Recharts |

---

## Technology Choices

| Concern | Choice | Rationale |
|---|---|---|
| Chart library | Recharts (existing) | Already used in `PmcChart`, `WeeklyLoadChart`; same API |
| EWMA implementation | Extend `compute_pmc()` in `metrics.py` | Single canonical EWMA; reuse `ctl_factor = 1 - exp(-1/tau)` |
| Multi-color bar chart (Daily %CTL) | Single `Bar` with per-bar `Cell` components (green/yellow/red based on threshold) | Simpler than 3 `Bar` series; Recharts `Cell` is the standard per-bar color override pattern |
| Intensity metric storage | `WorkoutMetric` key-value (existing table) | No schema change; fits existing import pipeline |
| Elevation storage | New `elevation_gain_m` column on `WorkoutFile` | Scalar property of the workout, not a metric |

---

## Integration Points

| Touchpoint | Type | Impact |
|---|---|---|
| `backend/engine/algorithms/metrics.py:compute_pmc` | Function import | `compute_run_pmc()` implements same EWMA logic independently; both coexist |
| `backend/api/pmc.py` | Existing endpoint | Unchanged — still serves all-sport PMC for existing PmcChart |
| `backend/files/file_service.py` | FIT importer | Extended in `_import_one_file()` to compute `elevation_gain_m` and intensity metrics |
| `frontend/src/api/hooks.ts` | API hooks file | 3 new `useQuery` hooks: `useRunLoad`, `useIntensityLoad`, `useRunVolume` |
| `frontend/src/pages/SeasonPage.tsx` | Parent page | 5 chart components added as collapsible sections (not SeasonTab.tsx) |

### Rollout Strategy
Feature visible immediately after deploy; no feature flag needed. If intensity metrics backfill has not run, `IntensityLoadChart` shows empty state with a prompt: "Run `wko5 backfill-intensity` to enable this chart."

---

## Codebase Patterns to Follow

| Pattern | Where to Find | Why Follow |
|---|---|---|
| EWMA algorithm | `backend/engine/algorithms/metrics.py:151-165` | Canonical `ctl_factor = 1 - exp(-1/tau)` — must match exactly |
| `WorkoutMetric` scalar storage | `backend/db/models.py:WorkoutMetric` + `backend/api/pmc.py:38-45` | All per-workout scalars live here |
| FastAPI async router shape | `backend/api/pmc.py` and `backend/api/analytics.py` | `@router.get`, `Depends(get_db)`, `AsyncSession` |
| `useQuery` hook pattern | `frontend/src/api/hooks.ts` (via `usePmc`) | Same TanStack Query shape for all new endpoints |
| Recharts responsive wrapper | `frontend/src/components/PmcChart.tsx:51-95` | `<ResponsiveContainer>` + dark theme colors |
| `SeasonPage` date range plumbing | `frontend/src/pages/SeasonPage.tsx` | Pass `dateFrom`/`dateTo` as props to all child charts |

---

## Risks & Trade-offs

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| FTP not set → intensity thresholds undefined | M | H | Skip intensity metrics for that workout; show tooltip "Set FTP in Config to enable intensity zones" |
| `sport` field null or inconsistently set for run workouts | M | M | Normalize on import: check Coros sport type code (mapped in `coros_sport_type`) and FIT sport field |
| FIT file deleted after import → backfill impossible | L | L | Log warning; intensity chart uses 0 for that date |
| `rampconstant` hard-coded to 7 | L | L | Expose as config param in `AthleteSettings` later; 7 is WKO5 default and matches community norm |
| Daily %CTL bars overlap in high-density date ranges | M | L | Recharts `<BarChart>` with `barSize` auto; x-axis tick decimation mirrors `PmcChart.tsx:45-47` |

---

## Decisions Log

| Decision | Choice | Alternatives Considered | Rationale |
|---|---|---|---|
| Store intensity as WorkoutMetric keys | `high_intensity_95pct_s`, `high_intensity_103pct_s` | Compute on-the-fly from FIT | Avoids FIT re-parse on every request; aligns with existing TSS/NP storage |
| Run-specific PMC as separate endpoint | `/api/v1/analytics/run-load` | Extend existing `/api/v1/pmc` with `sport` filter | Separation of concerns; existing PMC consumers unchanged |
| Elevation in `WorkoutFile` column | `elevation_gain_m` nullable float | WorkoutMetric key | Elevation is a workout-level property (like `duration_s`), not a derived metric |
| `rampconstant` default = 7 | Hardcoded | User-configurable | WKO5 default; configurable as future `AthleteSettings` field |
| Multi-color Daily %CTL bars | 3 separate `<Bar>` series | Single bar with custom cell colors | Simpler legend labeling; matches WKO5 chart color semantics |

---

## Implementation Notes (Deviations from Original Spec)

| Item | Spec Said | Actual Implementation | Reason |
|------|-----------|-----------------------|--------|
| UI location | `SeasonTab.tsx` ("Load" accordion or sub-tabs) | `SeasonPage.tsx` (collapsible sections) | App routes to `SeasonPage`, not `SeasonTab`; `SeasonTab` is legacy unused file |
| ACWR risk zones | `ReferenceBand` | `ReferenceArea` with `yAxisId="acwr"` | `ReferenceBand` not exported by Recharts 3.8.1 |
| Daily %CTL bars | 3 separate `Bar` series | Single `Bar` with `Cell` per data point | Cleaner data model; correct Recharts per-bar coloring pattern |
| sport filter | `sport="run"` | `sport="running"` | `_normalize_sport()` in `fit_reader.py` normalizes Coros sport codes to `"running"` |
| DB migration | Alembic | `_migrate_schema()` idempotent pattern | Existing codebase uses this pattern consistently |
| Intensity backfill CLI | Planned | Not implemented | New imports get metrics; historical workouts show empty state |

## Open Questions (Resolved)

- [x] **`WorkoutFile.sport` for runs** → Normalized to `"running"` by `_normalize_sport()` in `fit_reader.py`; reliable for all Coros-synced workouts.
- [x] **Where should charts live?** → Collapsible sections in `SeasonPage.tsx`, below existing PmcChart and WeeklyLoadChart.

## Open Questions (Still Pending)

- [ ] Should intensity metrics use FTP at workout date (from `AthleteSettings` history) or current FTP? Currently uses the most recent `AthleteSettings` at import time.
- [ ] Is `total_ascent` reliable in all Coros FIT files? Outdoor GPS runs: yes. Indoor workouts: falls back to altitude channel diff (`np.diff(altitude)`).
- [ ] Intensity backfill for historical workouts — needs a CLI command or admin endpoint to re-process existing FIT files.
