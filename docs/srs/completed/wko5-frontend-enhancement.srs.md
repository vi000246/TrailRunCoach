# SRS: WKO5 Frontend Enhancement — Multi-Tab Dashboard + MCP Server

> ⛔ **CANCELED（2026-10-04）**：React SPA（`frontend/`）的多分頁 dashboard、Config 頁與相關端點（`/api/v1/pmc`、`/api/v1/athletes/{id}/settings`）已刪除。本文是當時的設計紀錄。

## Metadata
- **Source PRD**: `docs/prd/wko5-training-ai.prd.md`
- **Source Linear Issue**: N/A
- **Owner**: maintainer
- **Status**: CANCELED (2026-10-04)
- **Generated**: 2026-05-15

## Summary

Extends the existing single-page WKO5 Coach dashboard into a multi-tab training analytics application with date range selection, year-over-year comparison charts, an athlete config page, and an MCP server that exposes training data to Claude AI. The frontend stays SPA (no server-side routing); tabs are managed by zustand. All new backend endpoints follow the existing FastAPI + SQLAlchemy async pattern.

---

## System Context

### Scope & Boundaries
- **In scope**: Tab navigation (Season / Activities / Config / AI), date range picker, PMC with range filter, weekly load chart, activity list + detail page, MMP year-over-year overlay, HR/power zone display, athlete settings form, MCP stdio server
- **Out of scope**: Mobile layout, multi-user auth, push notifications, Garmin/Wahoo sync, video/map rendering, Training Peaks API re-integration

### Actors
| Actor | Type | Interaction |
|---|---|---|
| Athlete | Human | Views charts, edits settings, triggers Coros sync |
| Claude AI | Service | Queries training data via MCP tools |
| Coros API | External service | Source of workout FIT files (already implemented) |

### External Dependencies
| Dependency | Purpose | Failure Mode |
|---|---|---|
| Coros API | Sync workouts | Sync disabled; existing data still viewable |
| SQLite DB `~/.wko5coach/wko5coach.db` | All training data | App non-functional; startup error shown |
| MCP Python package (`mcp`) | MCP server transport | AI integration unavailable; rest of app unaffected |

---

## Architecture

### High-Level Diagram
```
Browser (React 19 + Vite)
  ├── TabNav (zustand: activeTab)
  ├── Tab: Season
  │     ├── DateRangePicker
  │     ├── PmcChart (filtered by range)
  │     └── WeeklyLoadChart
  ├── Tab: Activities
  │     ├── DateRangePicker
  │     ├── ActivityList
  │     └── ActivityDetail (slide-in or sub-route)
  ├── Tab: Config
  │     ├── AthleteSettingsForm (FTP / LTHR / weight)
  │     ├── PowerZoneDisplay (computed from FTP)
  │     └── HrZoneDisplay (computed from LTHR)
  └── Tab: AI
        └── McpInfoPanel (how to connect Claude)

FastAPI Backend (existing)
  ├── /api/v1/athletes/{id}/settings  [NEW GET]
  ├── /api/v1/athletes/{id}/settings  [existing PUT]
  ├── /api/v1/workouts/{id}/timeseries [NEW GET]
  ├── /api/v1/workouts/{id}/zones      [NEW GET]
  ├── /api/v1/analytics/weekly         [NEW GET]
  └── /api/v1/analytics/mmp-compare    [NEW GET]

MCP Server (new: backend/mcp_server.py)
  └── stdio transport → Claude Desktop / claude.ai
```

### Components
| Component | Responsibility | Interface |
|---|---|---|
| `TabNav` | Top-level tab state and navigation | zustand `useTabStore` |
| `DateRangePicker` | Preset (1M/3M/6M/1Y) + custom date inputs | Props: `value`, `onChange` |
| `SeasonTab` | PMC + weekly load with shared date range | Consumes `DateRangePicker` |
| `PmcChart` | CTL/ATL/TSB line chart, filterable | Props: `dateFrom`, `dateTo` |
| `WeeklyLoadChart` | Bar chart of weekly TSS + hours | Props: `dateFrom`, `dateTo` |
| `ActivitiesTab` | Workout list + slide-in detail | Manages selected workout ID |
| `ActivityList` | Paginated table of workouts | `useWorkouts(params)` hook |
| `ActivityDetail` | Power/HR timeseries, MMP, zones | Props: `workoutId` |
| `ConfigTab` | Athlete settings form + zone tables | `useAthleteSettings` hook |
| `MmpCompareChart` | Year-over-year MMP overlay | Props: `sport`, `years[]` |
| `mcp_server.py` | MCP stdio server with 4 tools | subprocess / Claude Desktop |

### Data Flow
All chart data flows through react-query: `DateRangePicker` → query params → react-query `queryKey` → API call → Recharts. Tab state is ephemeral in zustand (not persisted). MCP server reads the same SQLite DB directly via SQLAlchemy sync session (separate connection from the async FastAPI session).

### Sequence Diagrams (key flows)

**Date range filter:**
```
User clicks "3M" preset
  → DateRangePicker.onChange({ from: "2026-02-15", to: "2026-05-15" })
  → SeasonTab state update
  → PmcChart re-renders with new params
  → react-query fetches GET /api/v1/pmc?date_from=2026-02-15&date_to=2026-05-15
  → Recharts renders filtered series
```

**Activity Detail:**
```
User clicks workout row in ActivityList
  → selectedWorkoutId state set
  → ActivityDetail panel slides in
  → react-query fetches:
      GET /api/v1/workouts/{id}          (summary + metrics)
      GET /api/v1/workouts/{id}/timeseries
      GET /api/v1/workouts/{id}/zones
  → Charts render
```

**Claude AI via MCP:**
```
Claude Desktop subprocess → mcp_server.py (stdio)
  → tool call: get_pmc(date_from, date_to)
  → mcp_server opens SQLite sync session
  → queries pmc_cache or runs compute_pmc()
  → returns JSON to Claude
```

---

## Data Model

### Entities
| Entity | Owner | Lifecycle |
|---|---|---|
| `AthleteSettings` | `athletes.py` | Created on first settings save; one row per (athlete, date) |
| `WorkoutFile` | `files/file_service.py` | Created on import; never deleted automatically |
| `WorkoutMetric` | `files/file_service.py` | Created on import; can be recomputed |
| `MmpCache` | `workouts.py` | Computed on first `/mmp` request; Infinity staleTime |

### Schema (new / changed)
No new tables required for MVP. Optional extension:

```sql
-- Optional: explicit HR zone thresholds (if LTHR-computed zones are insufficient)
ALTER TABLE athlete_settings ADD COLUMN hr_z1_bpm INTEGER;  -- upper bound of zone 1
ALTER TABLE athlete_settings ADD COLUMN hr_z2_bpm INTEGER;
ALTER TABLE athlete_settings ADD COLUMN hr_z3_bpm INTEGER;
ALTER TABLE athlete_settings ADD COLUMN hr_z4_bpm INTEGER;
-- zone 5 = above hr_z4_bpm
```

Default zones computed server-side from LTHR (Friel 7-zone collapsed to 5):
- Z1 < 85% LTHR, Z2 85–89%, Z3 90–94%, Z4 95–99%, Z5 ≥ 100%

Power zones from FTP (Coggan 7-zone collapsed to 5):
- Z1 < 55% FTP, Z2 55–74%, Z3 75–89%, Z4 90–104%, Z5 ≥ 105%

### Migration Strategy
- **Forward**: `_migrate_schema()` in `database.py:19` adds columns with `ALTER TABLE … ADD COLUMN IF NOT EXISTS` pattern
- **Backward**: SQLite has no DROP COLUMN — columns are additive-only; no rollback needed
- **Backfill**: None required; zone columns default NULL (computed on-the-fly when NULL)

---

## API Contracts

### New Endpoints
| Method | Path | Purpose | Auth |
|---|---|---|---|
| GET | `/api/v1/athletes/{id}/settings` | Current FTP, LTHR, weight + computed zones | None |
| GET | `/api/v1/workouts/{id}/timeseries` | Power/HR/cadence/altitude arrays | None |
| GET | `/api/v1/workouts/{id}/zones` | Time-in-zone (power + HR) | None |
| GET | `/api/v1/analytics/weekly` | Weekly TSS + duration aggregates | None |
| GET | `/api/v1/analytics/mmp-compare` | Year-over-year MMP curves | None |

### Request / Response Shape

```json
// GET /api/v1/athletes/{id}/settings
// Response — 200 OK
{
  "athlete_id": 1,
  "effective_date": "2026-05-15",
  "ftp_w": 200.0,
  "lthr": 160,
  "weight_kg": 70.0,
  "power_zones": [
    { "zone": 1, "label": "Recovery", "min_w": 0, "max_w": 109 },
    { "zone": 2, "label": "Endurance", "min_w": 110, "max_w": 149 },
    { "zone": 3, "label": "Tempo",     "min_w": 150, "max_w": 179 },
    { "zone": 4, "label": "Threshold", "min_w": 180, "max_w": 209 },
    { "zone": 5, "label": "VO2max+",   "min_w": 210, "max_w": null }
  ],
  "hr_zones": [
    { "zone": 1, "label": "Recovery",  "min_bpm": 0,   "max_bpm": 135 },
    { "zone": 2, "label": "Aerobic",   "min_bpm": 136, "max_bpm": 143 },
    { "zone": 3, "label": "Tempo",     "min_bpm": 144, "max_bpm": 151 },
    { "zone": 4, "label": "Threshold", "min_bpm": 152, "max_bpm": 159 },
    { "zone": 5, "label": "Anaerobic", "min_bpm": 160, "max_bpm": null }
  ]
}
```

```json
// GET /api/v1/workouts/{id}/timeseries?channel=power,heart_rate,cadence,altitude
// Response — 200 OK
{
  "workout_id": 42,
  "sample_rate_s": 1,
  "channels": {
    "power":      [0, 0, 145, 220, ...],   // null for missing samples
    "heart_rate": [120, 122, 125, ...],
    "cadence":    [0, 85, 88, ...],
    "altitude":   [450.2, 450.3, ...]
  },
  "duration_s": 3600
}
```

```json
// GET /api/v1/workouts/{id}/zones
// Response — 200 OK
{
  "workout_id": 42,
  "power_zones": [
    { "zone": 1, "seconds": 300 },
    { "zone": 2, "seconds": 1800 },
    { "zone": 3, "seconds": 900 },
    { "zone": 4, "seconds": 480 },
    { "zone": 5, "seconds": 120 }
  ],
  "hr_zones": [
    { "zone": 1, "seconds": 120 },
    ...
  ]
}
```

```json
// GET /api/v1/analytics/weekly?athlete_id=1&date_from=2026-01-01&date_to=2026-05-15
// Response — 200 OK
{
  "weeks": [
    { "week_start": "2026-01-05", "tss": 320.5, "duration_h": 8.2, "workout_count": 5 },
    ...
  ]
}
```

```json
// GET /api/v1/analytics/mmp-compare?athlete_id=1&sport=cycling&years=2025,2026
// Response — 200 OK
{
  "sport": "cycling",
  "series": {
    "2025": { "300": 285.0, "600": 265.0, "1200": 240.0, ... },
    "2026": { "300": 295.0, "600": 270.0, "1200": 245.0, ... }
  }
}
```

### Error Codes
| Code | HTTP Status | Meaning | Caller Action |
|---|---|---|---|
| `NOT_FOUND` | 404 | Workout / athlete does not exist | Check ID |
| `NO_SETTINGS` | 404 | No AthleteSettings row exists yet | Use PUT to create first |
| `NO_TIMESERIES` | 404 | FIT file has no matching channel data | Show "No power data" message |

### Versioning Strategy
All new endpoints added under `/api/v1/`. No breaking changes to existing endpoints. No deprecation needed.

---

## Non-Functional Requirements

| Category | Target | Measurement | How Achieved |
|---|---|---|---|
| Chart render | < 200ms for date range change | Browser DevTools paint timing | react-query cache; no re-fetch on tab switch |
| Timeseries load | < 1s for 3-hour ride (10 800 samples) | Network tab | SQLite indexed by workout_id; JSON streaming |
| MCP tool response | < 500ms | Claude tool latency | Direct SQLite sync read; no HTTP round-trip |
| Offline use | All previously synced data viewable | Manual test | SQLite local; no cloud dependency |
| Accessibility | Keyboard-navigable tabs, ARIA labels on charts | VoiceOver smoke test | Tailwind focus rings; Recharts title props |

---

## Technology Choices

| Concern | Choice | Alternatives | Rationale |
|---|---|---|---|
| Tab state | zustand (already installed) | React Router DOM, URL hash | Single-user local app; URL routing adds complexity without benefit |
| Date picker | Native `<input type="date">` + preset buttons | react-datepicker, flatpickr | Zero dependencies; Tailwind styled; sufficient for this use case |
| Timeseries chart | Recharts `ComposedChart` (already installed) | uPlot, Highcharts | Already installed; good enough for ≤ 10k points |
| MMP compare overlay | Recharts `LineChart` with multiple `<Line>` | D3 | Consistent with existing charts |
| MCP transport | stdio (subprocess) | HTTP SSE | Simpler; Claude Desktop native; no port conflict |
| MCP SDK | `mcp` Python package (Anthropic official) | Custom JSON-RPC | Official SDK; maintained; typed |

---

## Integration Points

| Touchpoint | Type | Contract | Backwards Compat |
|---|---|---|---|
| `GET /api/v1/pmc` | HTTP | Existing — accepts `date_from`, `date_to`; no change needed | Yes |
| `PUT /api/v1/athletes/{id}/settings` | HTTP | Existing in `athletes.py`; no change needed | Yes |
| `backend/db/models.py` `AthleteSettings` | Import | Adding optional HR zone columns; existing rows unaffected | Yes |
| `backend/files/fit_reader.py` | Import | `RawWorkout` dataclass; used by timeseries endpoint | Yes |
| `backend/engine/algorithms/metrics.py` | Import | `compute_pmc`, zone computation helpers | Yes |
| Claude Desktop `claude_desktop_config.json` | Config file | `{"command": "python", "args": ["backend/mcp_server.py"]}` | N/A |

### Rollout Strategy
All changes are additive. New backend endpoints can be deployed independently. New frontend tabs are hidden from users on other browsers (local app). MCP server is an opt-in subprocess — no impact on main FastAPI server.

---

## Codebase Patterns to Follow

| Pattern | Where to Find | Why Follow |
|---|---|---|
| FastAPI router registration | `backend/main.py:1-30` | All new routers must be registered here with `include_router` |
| Async endpoint with `Depends(get_db)` | `backend/api/pmc.py:14-47` | Standard session injection; do not open sessions manually |
| SQLAlchemy `select()` + `.where()` + `await db.execute()` | `backend/api/pmc.py:26-36` | All DB queries must use this async pattern |
| `_migrate_schema()` column addition | `backend/db/database.py:19-38` | Extend this list for new columns; never use `CREATE TABLE` |
| react-query `useQuery` hook | `frontend/src/api/hooks.ts:5-19` | All API calls go through react-query; no raw `fetch` in components |
| Recharts `<ResponsiveContainer>` | `frontend/src/components/PmcChart.tsx:44-87` | All charts must wrap in `ResponsiveContainer` for responsiveness |
| Tailwind dark card style | `frontend/src/pages/Dashboard.tsx:53` | `bg-gray-900 border border-gray-800 rounded-lg p-4` — consistent card look |
| Axios `api` instance | `frontend/src/api/client.ts:3` | All HTTP calls use the shared `api` axios instance at `/api/v1` |

---

## MCP Server Design

### File
`backend/mcp_server.py` — standalone script, run as subprocess by Claude Desktop.

### Tools
| Tool | Input | Output |
|---|---|---|
| `get_athlete_summary` | `athlete_id: int = 1` | FTP, LTHR, current CTL/ATL/TSB, workout count |
| `get_pmc` | `date_from: str, date_to: str, athlete_id: int = 1` | Array of `{date, ctl, atl, tsb, tss}` |
| `list_workouts` | `date_from: str, date_to: str, sport?: str, athlete_id: int = 1` | Array of workout summaries |
| `get_workout_detail` | `workout_id: int` | Metrics dict, sport, duration, MMP curve top values |

### DB Access
Uses `sqlalchemy` sync engine (not async) to read the same `~/.wko5coach/wko5coach.db`. Read-only — MCP server never writes.

### Claude Desktop Registration
```json
{
  "mcpServers": {
    "wko5coach": {
      "command": "python",
      "args": ["/path/to/WKO5reverse/backend/mcp_server.py"]
    }
  }
}
```

---

## Risks & Trade-offs

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Timeseries payload too large for 5-hour rides (18k+ samples) | M | M | Downsample to 5s or 10s resolution server-side; add `?resolution=5` param |
| MCP server SQLite lock contention with FastAPI async session | L | M | MCP uses WAL mode SQLite (default) + short-lived sync sessions; read-only access |
| No router = no deep-linking to specific workout | M | L | Acceptable for single-user local tool; can add hash routing later |
| MMP compare query slow for large datasets (1000+ workouts) | M | M | Query only `mmp_cache` table (pre-computed); filter by sport + year at DB level |
| `<input type="date">` browser inconsistencies (locale, format) | L | L | Force `min`/`max` attributes and `ISO8601` value format; test on Chrome/Safari |

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|---|---|---|---|
| Tab state | zustand (in-memory) | React Router DOM | Zero setup; zustand already installed; local tool doesn't need URLs |
| Zone computation | Server-side from LTHR/FTP | Store explicit zone values | Fewer form fields; consistent with how TrainingPeaks computes zones |
| MCP transport | stdio | HTTP SSE endpoint | Claude Desktop native; no port management; simpler |
| Timeseries storage | Read raw FIT file on demand vs. cache in DB | Cache in DB | Raw FIT always authoritative; avoid DB bloat for rarely-viewed data |
| Date picker | Native HTML input | Third-party library | No extra dependencies; already dark-themed with Tailwind |

---

## Open Questions

- [ ] Should the weekly load chart show TSS bars only, or split by sport (cycling vs run)?
- [ ] MMP compare: compare by calendar year or by training season (e.g., Oct–Sep)?
- [ ] Should Config tab include a "Recompute TSS" button (trigger `/pmc/recompute`) after FTP change?
- [ ] Timeseries resolution: default to raw 1s or downsample to 5s for rides > 2 hours?
- [ ] AI tab content: static instructions panel only, or interactive query UI?
