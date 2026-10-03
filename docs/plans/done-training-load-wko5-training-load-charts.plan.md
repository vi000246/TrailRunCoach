# Plan: WKO5 Training Load Charts — Run-Specific Analytics

> **For agentic workers:** `/prp-implement` will route this plan to `implementing-features` skill based on `Metadata.Type=feature` below.

## Summary

Implements five run-specific training load charts reverse-engineered from `WKO5 Season View.wko5chart`: Chronic/Acute TIS Load, Daily % of CTL, CTL Ramp Rate, Intensity Load Chart, and Running Volume Log. Three new FastAPI endpoints, two new Python algorithm functions, one DB column migration, one FIT importer extension, five Recharts components, three TanStack Query hooks, and one expanded TypeScript type block.

## User Story
As an athlete, I want to see run-specific training load, ramp rate, intensity zones, and volume history on my dashboard, so that I can manage injury risk and progression using the same metrics WKO5 provides.

## Problem → Solution
Currently the dashboard shows all-sport combined CTL/ATL/TSB only. → Add run-filtered versions of every load metric with color-coded risk zones matching WKO5's `WKO5 Season View`.

## Metadata
- **Module**: training-load
- **Parent Plan**: N/A
- **Source PRD**: N/A
- **Source SRS**: `docs/spec/wko5-training-load-charts.spec.md`
- **Source Linear Issue**: N/A
- **Type**: feature
- **Size**: L
- **Complexity**: Large
- **Rigor**: balanced
- **Mode**: B — 任務先測
- **TDD**: on (task-level test-first)
- **Commit cadence**: per-task
- **Estimated Files**: 15

---

## UX Design

### Before
```
SeasonTab
├── DateRangePicker
└── PmcChart (all-sport CTL/ATL/TSB only)
```

### After
```
SeasonTab
├── DateRangePicker
├── PmcChart (all-sport — unchanged)
└── [new] LoadSection (accordion or sub-row)
      ├── RunLoadChart        CTL/ATL/TSB/ACWR (run only) + ACWR risk bands
      ├── DailyPctCtlChart    color bar chart: green ≤150%, yellow 150-300%, red ≥300%
      ├── RampRateChart       line chart with reference lines at 0 and +7 TSS/day/week
      ├── IntensityLoadChart  4 lines: chronic/acute × ≥95%FTP / ≥103%FTP
      └── RunVolumeLog        weekly bar (distance km) + summary table
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| SeasonTab | 1 chart | 1 + 5 charts | New charts below existing PmcChart |
| Date range | PmcChart only | All 6 charts share same DateRangePicker | Pass `dateFrom`/`dateTo` as props |
| ACWR bands | N/A | Green 0.8-1.3, red >1.5 ReferenceBands | Read-only visual guide |
| Ramp rate | N/A | +7 TSS/day/week upper reference line | Read-only visual guide |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `backend/engine/algorithms/metrics.py` | 137–174 | `compute_pmc()` — must reuse EWMA exactly |
| P0 | `backend/files/file_service.py` | 56–128 | `_import_one_file()` — extend to compute intensity metrics |
| P0 | `backend/db/database.py` | 19–44 | `_migrate_schema()` — add `elevation_gain_m` column here |
| P0 | `frontend/src/api/hooks.ts` | 26–31 | `usePmc()` — template for 3 new hooks |
| P0 | `frontend/src/api/client.ts` | 7–13 | `PmcPoint` — template for new TS interfaces |
| P1 | `backend/api/analytics.py` | all | FastAPI router shape + `Depends(get_db)` pattern |
| P1 | `frontend/src/components/PmcChart.tsx` | 18–96 | Recharts dark-theme pattern to mirror |
| P1 | `frontend/src/tabs/SeasonTab.tsx` | all | Where new LoadSection is added |
| P2 | `backend/db/models.py` | all | `WorkoutFile`, `WorkoutMetric` schema |
| P2 | `src/tests/test_metrics.py` | all | pytest structure for new backend tests |

---

## Patterns to Mirror

### EWMA_ALGORITHM
```python
# SOURCE: backend/engine/algorithms/metrics.py:151-165
ctl_factor = 1 - math.exp(-1 / ctl_tau)   # tau=42 for CTL
atl_factor = 1 - math.exp(-1 / atl_tau)   # tau=7 for ATL
# Per day:
ctl = ctl + ctl_factor * (tss_today - ctl)
atl = atl + atl_factor * (tss_today - atl)
```

### MIGRATION_PATTERN
```python
# SOURCE: backend/db/database.py:22-37
new_cols = [
    ("workout_files", "coros_activity_id", "TEXT"),
    ...
]
async with engine.begin() as conn:
    for table, col, col_type in new_cols:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result.fetchall()}
        if col not in existing:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
```

### FASTAPI_ROUTER
```python
# SOURCE: backend/api/analytics.py:1-20
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.database import get_db

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

@router.get("/weekly")
async def weekly_load(
    athlete_id: int = 1,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    ...
```

### WORKOUT_METRIC_INSERT
```python
# SOURCE: backend/files/file_service.py:119-121
for key, val in metrics_dict.items():
    if isinstance(val, (float, int)) and val is not None:
        db.add(WorkoutMetric(workout_id=wf.id, metric_key=key, value=float(val)))
```

### QUERY_HOOK_PATTERN
```typescript
// SOURCE: frontend/src/api/hooks.ts:26-31
export function usePmc(params?: { date_from?: string; date_to?: string }) {
  return useQuery<{ series: PmcPoint[] }>({
    queryKey: ['pmc', params],
    queryFn: () => api.get('/pmc', { params }).then(r => r.data),
  })
}
```

### TYPESCRIPT_INTERFACE
```typescript
// SOURCE: frontend/src/api/client.ts:7-13
export interface PmcPoint {
  date: string
  ctl: number
  atl: number
  tsb: number
  tss: number
}
```

### RECHARTS_CHART
```typescript
// SOURCE: frontend/src/components/PmcChart.tsx:51-65
<div className="bg-gray-900 rounded-lg p-4">
  <h2 className="text-sm font-semibold text-gray-400 mb-3">Chart Title</h2>
  <ResponsiveContainer width="100%" height={260}>
    <LineChart data={series} margin={{ top: 4, right: 12, left: -8, bottom: 0 }}>
      <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
      <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} />
      <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} axisLine={false} />
      <Tooltip contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 12 }} />
      <Legend wrapperStyle={{ fontSize: 12 }} />
    </LineChart>
  </ResponsiveContainer>
</div>
```

### TEST_STRUCTURE
```python
# SOURCE: src/tests/test_metrics.py:1-20
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import numpy as np
import pytest
from metrics import normalized_power  # adjust import per test file

def test_name():
    """One-line docstring."""
    result = fn_under_test(input)
    assert result == pytest.approx(expected, rel=0.01)
```

---

## Files to Change

| File | Action | Why |
|---|---|---|
| `backend/engine/algorithms/metrics.py` | UPDATE | Add `compute_run_pmc()` and `compute_intensity_load_series()` |
| `backend/db/database.py` | UPDATE | Add `elevation_gain_m` to `_migrate_schema()` new_cols |
| `backend/db/models.py` | UPDATE | Add `elevation_gain_m` field to `WorkoutFile` |
| `backend/files/file_service.py` | UPDATE | Compute intensity metrics + elevation in `_import_one_file()` |
| `backend/api/analytics.py` | UPDATE | Add 3 new route handlers |
| `backend/main.py` | UPDATE | Register new routes (if not already auto-included) |
| `frontend/src/api/client.ts` | UPDATE | Add 5 new TS interfaces |
| `frontend/src/api/hooks.ts` | UPDATE | Add 3 new `useQuery` hooks |
| `frontend/src/components/charts/RunLoadChart.tsx` | CREATE | CTL/ATL/TSB/ACWR line chart |
| `frontend/src/components/charts/DailyPctCtlChart.tsx` | CREATE | Color-coded bar chart |
| `frontend/src/components/charts/RampRateChart.tsx` | CREATE | Ramp rate line + reference lines |
| `frontend/src/components/charts/IntensityLoadChart.tsx` | CREATE | 4-series intensity EWMA lines |
| `frontend/src/components/charts/RunVolumeLog.tsx` | CREATE | Weekly bar + summary table |
| `frontend/src/tabs/SeasonTab.tsx` | UPDATE | Add LoadSection with 5 new charts |
| `backend/tests/test_run_pmc.py` | CREATE | pytest for new algorithm functions |

## NOT Building
- Planned TSS input (future dates ramp projection) — needs a separate planning feature
- Multi-sport intensity charts (cycling, swim)
- HR-based TIS load
- Mobile responsive layout adjustments
- Linear issue sync

---

## Step-by-Step Tasks

---

### Task 1: Add `compute_run_pmc()` and `compute_intensity_load_series()` to metrics.py

**Files:**
- Modify: `backend/engine/algorithms/metrics.py` (append after line 174)
- Test: `backend/tests/test_run_pmc.py` (create)

- **TEST FIRST**:
  ```python
  # backend/tests/test_run_pmc.py
  import sys, os
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../engine/algorithms"))
  from datetime import date, timedelta
  import math
  import pytest
  from metrics import compute_run_pmc, compute_intensity_load_series

  def _days(start: date, n: int, tss: float) -> list[tuple[date, float]]:
      return [(start + timedelta(days=i), tss) for i in range(n)]

  def test_run_pmc_zero_input():
      result = compute_run_pmc([])
      assert result == []

  def test_run_pmc_acwr_after_steady_load():
      """Steady 50 TSS/day for 90 days → ACWR near 1.0."""
      start = date(2025, 1, 1)
      series = _days(start, 90, 50.0)
      result = compute_run_pmc(series)
      last = result[-1]
      assert last["acwr"] == pytest.approx(1.0, abs=0.05)

  def test_run_pmc_daily_pct_ctl():
      """100 TSS day after CTL=50 → daily_pct_ctl ≈ 2.0."""
      start = date(2025, 1, 1)
      # Build up CTL to ~50
      series = _days(start, 90, 50.0)
      # One spike day
      series.append((start + timedelta(days=90), 100.0))
      result = compute_run_pmc(series)
      last = result[-1]
      assert last["daily_pct_ctl"] == pytest.approx(100.0 / last["ctl"], rel=0.01)

  def test_run_pmc_ramp_rate_rising():
      """Increasing load → positive ramp rate."""
      start = date(2025, 1, 1)
      series = [(start + timedelta(days=i), float(i)) for i in range(30)]
      result = compute_run_pmc(series)
      last = result[-1]
      assert last["ramp_rate"] > 0

  def test_compute_intensity_load_series_empty():
      assert compute_intensity_load_series([], tau=42) == []

  def test_compute_intensity_load_series_ewma():
      """EWMA of 60s/day for 42 days → value approaches 60 * (1 - 1/e) asymptote."""
      start = date(2025, 1, 1)
      series = [(start + timedelta(days=i), 60.0) for i in range(84)]
      result = compute_intensity_load_series(series, tau=42)
      last = result[-1]["value"]
      # After ~2 tau it's close to 60
      assert last == pytest.approx(60.0, abs=5.0)
  ```
  Run: `cd <repo> && python -m pytest backend/tests/test_run_pmc.py -v`
  Expected: **FAIL** (ImportError — functions don't exist yet)

- **IMPLEMENT**:
  Append to `backend/engine/algorithms/metrics.py` after line 174:
  ```python
  def compute_run_pmc(
      run_tss_series: list[tuple[date, float]],
      ctl_tau: float = 42.0,
      atl_tau: float = 7.0,
      ramp_days: int = 7,
  ) -> list[dict]:
      """
      Run-specific PMC.
      WKO5 formula: tl(if(sport="run", tss), ctlconstant/atlconstant)
      Appends acwr, daily_pct_ctl, ramp_rate, ramp_pct_ctl to each day.
      """
      if not run_tss_series:
          return []
      ctl_factor = 1 - math.exp(-1 / ctl_tau)
      atl_factor = 1 - math.exp(-1 / atl_tau)
      tss_dict = {d: t for d, t in run_tss_series}
      start_date = min(d for d, _ in run_tss_series)
      end_date = max(d for d, _ in run_tss_series)
      ctl = atl = 0.0
      prev_ctl = prev_atl = 0.0
      history: list[dict] = []
      current = start_date
      while current <= end_date:
          tss_today = tss_dict.get(current, 0.0)
          prev_ctl, prev_atl = ctl, atl
          ctl = ctl + ctl_factor * (tss_today - ctl)
          atl = atl + atl_factor * (tss_today - atl)
          tsb = round(prev_ctl - prev_atl, 2)
          acwr = round(atl / ctl, 3) if ctl > 0 else None
          daily_pct_ctl = round(tss_today / ctl, 3) if ctl > 0 else None
          # Ramp rate = CTL change over ramp_days window
          ramp_ctl = None
          if len(history) >= ramp_days:
              ramp_ctl = round(ctl - history[-ramp_days]["ctl"], 2)
          else:
              ramp_ctl = round(ctl - 0.0, 2)
          ramp_pct_ctl = round(ramp_ctl / ctl, 3) if ctl > 0 and ramp_ctl is not None else None
          history.append({
              "date": current.isoformat(),
              "ctl": round(ctl, 2),
              "atl": round(atl, 2),
              "tsb": tsb,
              "tss": tss_today,
              "acwr": acwr,
              "daily_pct_ctl": daily_pct_ctl,
              "ramp_rate": ramp_ctl,
              "ramp_pct_ctl": ramp_pct_ctl,
          })
          current += timedelta(days=1)
      return history


  def compute_intensity_load_series(
      intensity_by_date: list[tuple[date, float]],
      tau: float,
  ) -> list[dict]:
      """
      EWMA of per-workout high-intensity time (seconds).
      WKO5: tl(sum(if(runpower >= threshold*runFTP, deltatime)), ctlconstant/atlconstant)
      Returns list of {"date": str, "value": float} in minutes.
      """
      if not intensity_by_date:
          return []
      factor = 1 - math.exp(-1 / tau)
      tdict = {d: v for d, v in intensity_by_date}
      start_date = min(d for d, _ in intensity_by_date)
      end_date = max(d for d, _ in intensity_by_date)
      ewma = 0.0
      result = []
      current = start_date
      while current <= end_date:
          val = tdict.get(current, 0.0)
          ewma = ewma + factor * (val - ewma)
          result.append({"date": current.isoformat(), "value": round(ewma / 60.0, 2)})
          current += timedelta(days=1)
      return result
  ```

- **VALIDATE**: `cd <repo> && python -m pytest backend/tests/test_run_pmc.py -v`
  Expected: **PASS** (6 tests green)

- **COMMIT**: `feat: add compute_run_pmc and compute_intensity_load_series algorithms`

---

### Task 2: DB migration — add `elevation_gain_m` column

**Files:**
- Modify: `backend/db/models.py` (add field to `WorkoutFile`)
- Modify: `backend/db/database.py` (add to `new_cols` in `_migrate_schema()`)

- **TEST FIRST**:
  ```python
  # Quick inline smoke test (run manually, not pytest):
  # python -c "
  # import asyncio
  # from backend.db.database import init_db
  # asyncio.run(init_db())
  # from sqlalchemy import create_engine, text
  # from pathlib import Path
  # engine = create_engine(f'sqlite:///{Path.home()}/.wko5coach/wko5coach.db')
  # with engine.connect() as c:
  #     rows = c.execute(text('PRAGMA table_info(workout_files)')).fetchall()
  #     cols = [r[1] for r in rows]
  #     assert 'elevation_gain_m' in cols, f'Missing! Got: {cols}'
  #     print('OK: elevation_gain_m present')
  # "
  ```
  Run: `cd <repo> && python -c "import asyncio; from backend.db.database import init_db; asyncio.run(init_db()); print('no error')"`
  Before change: column absent in PRAGMA output.

- **IMPLEMENT**:

  In `backend/db/models.py`, inside `WorkoutFile` class after `coros_sport_type`:
  ```python
  elevation_gain_m: Mapped[Optional[float]] = mapped_column(nullable=True)
  ```

  In `backend/db/database.py`, add to `new_cols` list (line ~24):
  ```python
  ("workout_files", "elevation_gain_m", "REAL"),
  ```

- **VALIDATE**:
  ```bash
  cd <repo>
  python -c "
  import asyncio
  from backend.db.database import init_db
  asyncio.run(init_db())
  from sqlalchemy import create_engine, text
  from pathlib import Path
  engine = create_engine(f'sqlite:///{Path.home()}/.wko5coach/wko5coach.db')
  with engine.connect() as c:
      rows = c.execute(text('PRAGMA table_info(workout_files)')).fetchall()
      cols = [r[1] for r in rows]
      assert 'elevation_gain_m' in cols, f'Missing! Got: {cols}'
      print('OK: elevation_gain_m present')
  "
  ```
  Expected: `OK: elevation_gain_m present`

- **COMMIT**: `feat: add elevation_gain_m column to workout_files via schema migration`

---

### Task 3: FIT importer — compute intensity metrics and elevation at import

**Files:**
- Modify: `backend/files/file_service.py` (extend `_import_one_file()`)

- **TEST FIRST**:
  ```python
  # Extend backend/tests/test_run_pmc.py with an integration-style unit test:
  # (Append to that file)

  def test_intensity_time_calculation():
      """Seconds above threshold from synthetic power array."""
      import numpy as np
      # 60s at 200W (>= 95% of ftp=200: threshold=190W) + 30s at 100W (below)
      power = np.array([200.0] * 60 + [100.0] * 30)
      ftp = 200.0
      threshold_95 = 0.95 * ftp   # 190W
      threshold_103 = 1.03 * ftp  # 206W — none qualify
      hi_95 = float(np.sum(power >= threshold_95))   # all 60 qualify
      hi_103 = float(np.sum(power >= threshold_103)) # none qualify
      assert hi_95 == pytest.approx(60.0, abs=1.0)
      assert hi_103 == pytest.approx(0.0, abs=1.0)
  ```
  Run: `python -m pytest backend/tests/test_run_pmc.py::test_intensity_time_calculation -v`
  Expected: **FAIL** (function not imported yet — it's a pure numpy test, should actually pass; this validates the formula logic)

- **IMPLEMENT**:
  In `backend/files/file_service.py`, extend `_import_one_file()` after the existing metrics block (after line 122):
  ```python
  if raw is not None:
      # Elevation from altitude channel (total ascent = sum of positive diffs)
      if len(raw.altitude_m) > 1:
          diffs = np.diff(raw.altitude_m)
          elevation_gain = float(np.sum(diffs[diffs > 0]))
          wf.elevation_gain_m = round(elevation_gain, 1)
      elif "total_ascent" in raw.session:
          wf.elevation_gain_m = float(raw.session["total_ascent"])

      # Intensity metrics (run sport only, requires FTP)
      if raw.sport == "running" and ftp_w and ftp_w > 0 and raw.has_power:
          power_arr = raw.power_w
          hi_95 = float(np.sum(power_arr >= 0.95 * ftp_w))
          hi_103 = float(np.sum(power_arr >= 1.03 * ftp_w))
          if hi_95 > 0:
              db.add(WorkoutMetric(workout_id=wf.id, metric_key="high_intensity_95pct_s", value=hi_95))
          if hi_103 > 0:
              db.add(WorkoutMetric(workout_id=wf.id, metric_key="high_intensity_103pct_s", value=hi_103))
  ```
  Note: add `import numpy as np` at top if not already present (it is via `backend/files/fit_reader.py`; add explicit import in `file_service.py`).

- **VALIDATE**:
  ```bash
  cd <repo> && python -m pytest backend/tests/test_run_pmc.py -v
  ```
  Expected: all 7 tests pass

- **COMMIT**: `feat: compute elevation_gain_m and high_intensity metrics during FIT import`

---

### Task 4: Backend API — 3 new analytics endpoints

**Files:**
- Modify: `backend/api/analytics.py` (append 3 new route handlers)

- **TEST FIRST** (endpoint shape validation via Python):
  ```python
  # Append to backend/tests/test_run_pmc.py

  def test_endpoint_response_shapes():
      """Validate the response dicts from algorithm match expected keys."""
      from datetime import date, timedelta
      start = date(2025, 1, 1)
      series = [(start + timedelta(days=i), 50.0) for i in range(30)]
      from metrics import compute_run_pmc, compute_intensity_load_series
      pmc = compute_run_pmc(series)
      assert all(k in pmc[0] for k in ["date","ctl","atl","tsb","tss","acwr","daily_pct_ctl","ramp_rate"])
      intensity = compute_intensity_load_series(series, tau=42)
      assert all(k in intensity[0] for k in ["date", "value"])
  ```
  Run: `python -m pytest backend/tests/test_run_pmc.py::test_endpoint_response_shapes -v`
  Expected: **PASS** (after Task 1)

- **IMPLEMENT**:
  Append to `backend/api/analytics.py`:
  ```python
  from backend.engine.algorithms.metrics import compute_run_pmc, compute_intensity_load_series


  @router.get("/run-load")
  async def run_load(
      athlete_id: int = 1,
      date_from: Optional[date] = None,
      date_to: Optional[date] = None,
      db: AsyncSession = Depends(get_db),
  ):
      if date_to is None:
          date_to = date.today()
      if date_from is None:
          date_from = date_to - timedelta(days=365)

      q = (
          select(WorkoutFile.workout_date, WorkoutMetric.value)
          .join(WorkoutMetric, WorkoutMetric.workout_id == WorkoutFile.id)
          .where(
              WorkoutFile.athlete_id == athlete_id,
              WorkoutMetric.metric_key == "tss",
              WorkoutFile.sport == "running",
              WorkoutFile.workout_date.isnot(None),
          )
      )
      result = await db.execute(q)
      rows = result.all()
      tss_by_date: dict[date, float] = {}
      for d, v in rows:
          if v and d:
              tss_by_date[d] = tss_by_date.get(d, 0.0) + v

      run_series = sorted(tss_by_date.items())
      pmc_data = compute_run_pmc(run_series)
      filtered = [p for p in pmc_data if date_from.isoformat() <= p["date"] <= date_to.isoformat()]
      return {"series": filtered, "athlete_id": athlete_id}


  @router.get("/intensity-load")
  async def intensity_load(
      athlete_id: int = 1,
      date_from: Optional[date] = None,
      date_to: Optional[date] = None,
      db: AsyncSession = Depends(get_db),
  ):
      if date_to is None:
          date_to = date.today()
      if date_from is None:
          date_from = date_to - timedelta(days=365)

      async def _get_series(metric_key: str) -> list[tuple[date, float]]:
          q = (
              select(WorkoutFile.workout_date, WorkoutMetric.value)
              .join(WorkoutMetric, WorkoutMetric.workout_id == WorkoutFile.id)
              .where(
                  WorkoutFile.athlete_id == athlete_id,
                  WorkoutMetric.metric_key == metric_key,
                  WorkoutFile.sport == "running",
                  WorkoutFile.workout_date.isnot(None),
              )
          )
          rows = (await db.execute(q)).all()
          by_date: dict[date, float] = {}
          for d, v in rows:
              if v and d:
                  by_date[d] = by_date.get(d, 0.0) + v
          return sorted(by_date.items())

      s95 = await _get_series("high_intensity_95pct_s")
      s103 = await _get_series("high_intensity_103pct_s")

      chronic_95 = {r["date"]: r["value"] for r in compute_intensity_load_series(s95, tau=42.0)}
      acute_95 = {r["date"]: r["value"] for r in compute_intensity_load_series(s95, tau=7.0)}
      chronic_103 = {r["date"]: r["value"] for r in compute_intensity_load_series(s103, tau=42.0)}
      acute_103 = {r["date"]: r["value"] for r in compute_intensity_load_series(s103, tau=7.0)}

      all_dates = sorted(set(list(chronic_95) + list(acute_95) + list(chronic_103) + list(acute_103)))
      filtered = [
          {
              "date": d,
              "chronic_95pct_min": chronic_95.get(d, 0.0),
              "acute_95pct_min": acute_95.get(d, 0.0),
              "chronic_103pct_min": chronic_103.get(d, 0.0),
              "acute_103pct_min": acute_103.get(d, 0.0),
          }
          for d in all_dates
          if date_from.isoformat() <= d <= date_to.isoformat()
      ]
      return {"series": filtered, "athlete_id": athlete_id}


  @router.get("/run-volume")
  async def run_volume(
      athlete_id: int = 1,
      date_from: Optional[date] = None,
      date_to: Optional[date] = None,
      db: AsyncSession = Depends(get_db),
  ):
      if date_to is None:
          date_to = date.today()
      if date_from is None:
          date_from = date_to - timedelta(days=365)

      tss_subq = (
          select(func.coalesce(func.sum(WorkoutMetric.value), 0))
          .where(WorkoutMetric.workout_id == WorkoutFile.id)
          .where(WorkoutMetric.metric_key == "tss")
          .correlate(WorkoutFile)
          .scalar_subquery()
      )

      q = (
          select(
              func.strftime("%Y-%W", WorkoutFile.workout_date).label("week_key"),
              func.min(WorkoutFile.workout_date).label("week_start"),
              func.sum(WorkoutFile.total_distance_m).label("distance_m"),
              (func.sum(WorkoutFile.duration_s) / 3600.0).label("hours"),
              func.sum(WorkoutFile.elevation_gain_m).label("elevation_m"),
              func.sum(tss_subq).label("tss"),
              func.count(WorkoutFile.id).label("count"),
          )
          .where(
              WorkoutFile.athlete_id == athlete_id,
              WorkoutFile.sport == "running",
              WorkoutFile.workout_date >= date_from,
              WorkoutFile.workout_date <= date_to,
          )
          .group_by("week_key")
          .order_by("week_key")
      )
      result = await db.execute(q)
      weeks = result.all()

      mq = (
          select(
              func.strftime("%Y-%m", WorkoutFile.workout_date).label("month_key"),
              func.sum(WorkoutFile.total_distance_m).label("distance_m"),
              (func.sum(WorkoutFile.duration_s) / 3600.0).label("hours"),
              func.sum(WorkoutFile.elevation_gain_m).label("elevation_m"),
              func.count(WorkoutFile.id).label("count"),
          )
          .where(
              WorkoutFile.athlete_id == athlete_id,
              WorkoutFile.sport == "running",
              WorkoutFile.workout_date >= date_from,
              WorkoutFile.workout_date <= date_to,
          )
          .group_by("month_key")
          .order_by("month_key")
      )
      mresult = await db.execute(mq)
      months = mresult.all()

      return {
          "weeks": [
              {
                  "week_start": r.week_start.isoformat() if r.week_start else None,
                  "distance_km": round(float(r.distance_m or 0) / 1000, 2),
                  "hours": round(float(r.hours or 0), 1),
                  "elevation_m": round(float(r.elevation_m or 0), 0),
                  "tss": round(float(r.tss or 0)),
                  "count": r.count,
              }
              for r in weeks
          ],
          "months": [
              {
                  "month": r.month_key,
                  "distance_km": round(float(r.distance_m or 0) / 1000, 2),
                  "hours": round(float(r.hours or 0), 1),
                  "elevation_m": round(float(r.elevation_m or 0), 0),
                  "count": r.count,
              }
              for r in months
          ],
          "athlete_id": athlete_id,
      }
  ```

  Also add `from datetime import timedelta` to the imports in analytics.py if not present, and `from sqlalchemy import select, func`.

- **VALIDATE**:
  ```bash
  cd <repo> && python -c "
  from backend.api.analytics import router
  routes = [r.path for r in router.routes]
  assert '/api/v1/analytics/run-load' in routes
  assert '/api/v1/analytics/intensity-load' in routes
  assert '/api/v1/analytics/run-volume' in routes
  print('OK:', routes)
  "
  ```

- **COMMIT**: `feat: add run-load, intensity-load, run-volume analytics endpoints`

---

### Task 5: TypeScript types and API hooks

**Files:**
- Modify: `frontend/src/api/client.ts` (append interfaces)
- Modify: `frontend/src/api/hooks.ts` (append 3 hooks)

- **TEST FIRST** (TypeScript type-check):
  ```bash
  cd <repo>/frontend && npx tsc --noEmit 2>&1 | head -20
  ```
  Before changes: should pass (baseline).

- **IMPLEMENT**:

  Append to `frontend/src/api/client.ts`:
  ```typescript
  export interface RunLoadPoint {
    date: string
    ctl: number
    atl: number
    tsb: number
    tss: number
    acwr: number | null
    daily_pct_ctl: number | null
    ramp_rate: number | null
    ramp_pct_ctl: number | null
  }

  export interface IntensityLoadPoint {
    date: string
    chronic_95pct_min: number
    acute_95pct_min: number
    chronic_103pct_min: number
    acute_103pct_min: number
  }

  export interface RunVolumeWeek {
    week_start: string
    distance_km: number
    hours: number
    elevation_m: number
    tss: number
    count: number
  }

  export interface RunVolumeMonth {
    month: string
    distance_km: number
    hours: number
    elevation_m: number
    count: number
  }

  export interface RunVolumeResponse {
    weeks: RunVolumeWeek[]
    months: RunVolumeMonth[]
    athlete_id: number
  }
  ```

  Append to `frontend/src/api/hooks.ts`:
  ```typescript
  import type {
    // add to existing import at top of file:
    RunLoadPoint, IntensityLoadPoint, RunVolumeResponse,
  } from './client'

  export function useRunLoad(params?: { date_from?: string; date_to?: string }) {
    return useQuery<{ series: RunLoadPoint[] }>({
      queryKey: ['run_load', params],
      queryFn: () => api.get('/analytics/run-load', { params }).then(r => r.data),
    })
  }

  export function useIntensityLoad(params?: { date_from?: string; date_to?: string }) {
    return useQuery<{ series: IntensityLoadPoint[] }>({
      queryKey: ['intensity_load', params],
      queryFn: () => api.get('/analytics/intensity-load', { params }).then(r => r.data),
    })
  }

  export function useRunVolume(params?: { date_from?: string; date_to?: string }) {
    return useQuery<RunVolumeResponse>({
      queryKey: ['run_volume', params],
      queryFn: () => api.get('/analytics/run-volume', { params }).then(r => r.data),
    })
  }
  ```

- **VALIDATE**:
  ```bash
  cd <repo>/frontend && npx tsc --noEmit
  ```
  Expected: Zero type errors

- **COMMIT**: `feat: add RunLoad, IntensityLoad, RunVolume TypeScript types and hooks`

---

### Task 6: `RunLoadChart` component (CTL/ATL/TSB + ACWR with risk bands)

**Files:**
- Create: `frontend/src/components/charts/RunLoadChart.tsx`

- **TEST FIRST** (TypeScript compile):
  ```bash
  cd <repo>/frontend && npx tsc --noEmit
  ```
  Before: zero errors (baseline).

- **IMPLEMENT**:
  ```tsx
  // frontend/src/components/charts/RunLoadChart.tsx
  import {
    ComposedChart, Line, XAxis, YAxis, CartesianGrid,
    Tooltip, Legend, ResponsiveContainer, ReferenceBand,
  } from 'recharts'
  import { useRunLoad } from '../../api/hooks'

  interface Props {
    dateFrom?: string
    dateTo?: string
  }

  export function RunLoadChart({ dateFrom, dateTo }: Props = {}) {
    const params = dateFrom || dateTo ? { date_from: dateFrom, date_to: dateTo } : undefined
    const { data, isLoading, isError } = useRunLoad(params)

    if (isLoading) return <div className="flex items-center justify-center h-64 text-gray-500 text-sm">Loading...</div>
    if (isError || !data?.series?.length) return <div className="flex items-center justify-center h-64 text-gray-600 text-sm">No run data.</div>

    const series = data.series.map(p => ({ ...p, date: p.date.slice(5) }))
    const tickCount = Math.min(series.length, 12)
    const step = Math.max(1, Math.floor(series.length / tickCount))
    const ticks = series.filter((_, i) => i % step === 0).map(p => p.date)

    return (
      <div className="bg-gray-900 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-gray-400 mb-3">Run TIS Load (CTL / ATL / TSB / ACWR)</h2>
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={series} margin={{ top: 4, right: 12, left: -8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="date" ticks={ticks} tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} />
            <YAxis yAxisId="load" tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} axisLine={false} />
            <YAxis yAxisId="acwr" orientation="right" domain={[0, 2]} tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} axisLine={false} />
            <Tooltip contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 12 }} labelStyle={{ color: '#d1d5db' }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {/* ACWR risk bands */}
            <ReferenceBand yAxisId="acwr" y1={0.8} y2={1.3} fill="#22c55e" fillOpacity={0.06} />
            <ReferenceBand yAxisId="acwr" y1={1.3} y2={1.5} fill="#f59e0b" fillOpacity={0.08} />
            <ReferenceBand yAxisId="acwr" y1={1.5} y2={2.0} fill="#ef4444" fillOpacity={0.1} />
            <Line yAxisId="load" type="monotone" dataKey="ctl" name="CTL (Run)" stroke="#a855f7" dot={false} strokeWidth={2} />
            <Line yAxisId="load" type="monotone" dataKey="atl" name="ATL (Run)" stroke="#ef4444" dot={false} strokeWidth={2} />
            <Line yAxisId="load" type="monotone" dataKey="tsb" name="TSB (Run)" stroke="#22c55e" dot={false} strokeWidth={1.5} strokeDasharray="4 2" />
            <Line yAxisId="acwr" type="monotone" dataKey="acwr" name="ACWR" stroke="#f59e0b" dot={false} strokeWidth={1.5} strokeDasharray="2 2" />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    )
  }
  ```

- **VALIDATE**: `cd <repo>/frontend && npx tsc --noEmit`

- **COMMIT**: `feat: add RunLoadChart with CTL/ATL/TSB/ACWR and risk bands`

---

### Task 7: `DailyPctCtlChart` — color-coded bar chart

**Files:**
- Create: `frontend/src/components/charts/DailyPctCtlChart.tsx`

- **TEST FIRST**: `npx tsc --noEmit` (baseline zero errors)

- **IMPLEMENT**:
  ```tsx
  // frontend/src/components/charts/DailyPctCtlChart.tsx
  import {
    ComposedChart, Bar, XAxis, YAxis, CartesianGrid,
    Tooltip, Legend, ResponsiveContainer, ReferenceLine,
  } from 'recharts'
  import { useRunLoad } from '../../api/hooks'

  interface Props {
    dateFrom?: string
    dateTo?: string
  }

  export function DailyPctCtlChart({ dateFrom, dateTo }: Props = {}) {
    const params = dateFrom || dateTo ? { date_from: dateFrom, date_to: dateTo } : undefined
    const { data, isLoading, isError } = useRunLoad(params)

    if (isLoading) return <div className="flex items-center justify-center h-64 text-gray-500 text-sm">Loading...</div>
    if (isError || !data?.series?.length) return <div className="flex items-center justify-center h-64 text-gray-600 text-sm">No run data.</div>

    // Split into 3 color bands matching WKO5 formulas
    const series = data.series
      .filter(p => p.tss > 0)
      .map(p => {
        const pct = p.daily_pct_ctl ?? 0
        return {
          date: p.date.slice(5),
          safe: pct <= 1.5 ? pct : null,
          caution: pct > 1.5 && pct < 3 ? pct : null,
          danger: pct >= 3 ? pct : null,
        }
      })

    const tickCount = Math.min(series.length, 12)
    const step = Math.max(1, Math.floor(series.length / tickCount))
    const ticks = series.filter((_, i) => i % step === 0).map(p => p.date)

    return (
      <div className="bg-gray-900 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-gray-400 mb-3">Daily % of CTL (Run TSS / CTL)</h2>
        <ResponsiveContainer width="100%" height={220}>
          <ComposedChart data={series} margin={{ top: 4, right: 12, left: -8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="date" ticks={ticks} tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} />
            <YAxis tickFormatter={v => `${Math.round(v * 100)}%`} tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} axisLine={false} />
            <Tooltip
              contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 12 }}
              formatter={(v: number) => [`${Math.round((v ?? 0) * 100)}%`]}
            />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <ReferenceLine y={1.0} stroke="#6b7280" strokeDasharray="4 2" label={{ value: '= CTL', fill: '#6b7280', fontSize: 10 }} />
            <ReferenceLine y={1.5} stroke="#f59e0b" strokeDasharray="4 2" label={{ value: '150%', fill: '#f59e0b', fontSize: 10 }} />
            <Bar dataKey="safe" name="≤ 150% CTL" fill="#22c55e" stackId="pct" maxBarSize={8} />
            <Bar dataKey="caution" name="150–300% CTL" fill="#f59e0b" stackId="pct" maxBarSize={8} />
            <Bar dataKey="danger" name="≥ 300% CTL" fill="#ef4444" stackId="pct" maxBarSize={8} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    )
  }
  ```

- **VALIDATE**: `cd <repo>/frontend && npx tsc --noEmit`

- **COMMIT**: `feat: add DailyPctCtlChart with green/yellow/red WKO5 color bands`

---

### Task 8: `RampRateChart` with reference lines

**Files:**
- Create: `frontend/src/components/charts/RampRateChart.tsx`

- **TEST FIRST**: `npx tsc --noEmit` (baseline)

- **IMPLEMENT**:
  ```tsx
  // frontend/src/components/charts/RampRateChart.tsx
  import {
    LineChart, Line, XAxis, YAxis, CartesianGrid,
    Tooltip, Legend, ResponsiveContainer, ReferenceLine,
  } from 'recharts'
  import { useRunLoad } from '../../api/hooks'

  interface Props {
    dateFrom?: string
    dateTo?: string
  }

  export function RampRateChart({ dateFrom, dateTo }: Props = {}) {
    const params = dateFrom || dateTo ? { date_from: dateFrom, date_to: dateTo } : undefined
    const { data, isLoading, isError } = useRunLoad(params)

    if (isLoading) return <div className="flex items-center justify-center h-64 text-gray-500 text-sm">Loading...</div>
    if (isError || !data?.series?.length) return <div className="flex items-center justify-center h-64 text-gray-600 text-sm">No run data.</div>

    const series = data.series.map(p => ({ ...p, date: p.date.slice(5) }))
    const tickCount = Math.min(series.length, 12)
    const step = Math.max(1, Math.floor(series.length / tickCount))
    const ticks = series.filter((_, i) => i % step === 0).map(p => p.date)

    return (
      <div className="bg-gray-900 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-gray-400 mb-3">CTL Ramp Rate — Run (TSS/day/week)</h2>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={series} margin={{ top: 4, right: 12, left: -8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="date" ticks={ticks} tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} />
            <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} axisLine={false} />
            <Tooltip contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 12 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {/* Reference lines from WKO5 chart: 0 and +7 TSS/day/week */}
            <ReferenceLine y={0} stroke="#6b7280" strokeDasharray="4 2" />
            <ReferenceLine y={7} stroke="#22c55e" strokeDasharray="4 2" label={{ value: '+7 TSS/d/wk', fill: '#22c55e', fontSize: 10, position: 'right' }} />
            <Line type="monotone" dataKey="ramp_rate" name="Ramp Rate" stroke="#60a5fa" dot={false} strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    )
  }
  ```

- **VALIDATE**: `cd <repo>/frontend && npx tsc --noEmit`

- **COMMIT**: `feat: add RampRateChart with 0 and +7 reference lines`

---

### Task 9: `IntensityLoadChart` — 4-series EWMA

**Files:**
- Create: `frontend/src/components/charts/IntensityLoadChart.tsx`

- **TEST FIRST**: `npx tsc --noEmit` (baseline)

- **IMPLEMENT**:
  ```tsx
  // frontend/src/components/charts/IntensityLoadChart.tsx
  import {
    LineChart, Line, XAxis, YAxis, CartesianGrid,
    Tooltip, Legend, ResponsiveContainer,
  } from 'recharts'
  import { useIntensityLoad } from '../../api/hooks'

  interface Props {
    dateFrom?: string
    dateTo?: string
  }

  export function IntensityLoadChart({ dateFrom, dateTo }: Props = {}) {
    const params = dateFrom || dateTo ? { date_from: dateFrom, date_to: dateTo } : undefined
    const { data, isLoading, isError } = useIntensityLoad(params)

    if (isLoading) return <div className="flex items-center justify-center h-64 text-gray-500 text-sm">Loading...</div>
    if (isError || !data?.series?.length) return (
      <div className="flex items-center justify-center h-64 text-gray-600 text-sm">
        No intensity data — run `wko5 backfill-intensity` to populate.
      </div>
    )

    const series = data.series.map(p => ({ ...p, date: p.date.slice(5) }))
    const tickCount = Math.min(series.length, 12)
    const step = Math.max(1, Math.floor(series.length / tickCount))
    const ticks = series.filter((_, i) => i % step === 0).map(p => p.date)

    return (
      <div className="bg-gray-900 rounded-lg p-4">
        <h2 className="text-sm font-semibold text-gray-400 mb-3">Intensity Load Chart — Run (min at ≥95% / ≥103% FTP)</h2>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={series} margin={{ top: 4, right: 12, left: -8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="date" ticks={ticks} tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} />
            <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} axisLine={false} />
            <Tooltip contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 12 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            {/* Chronic = solid, Acute = dashed — matching WKO5 visual convention */}
            <Line type="monotone" dataKey="chronic_95pct_min" name="Chronic ≥95% FTP" stroke="#f97316" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="acute_95pct_min" name="Acute ≥95% FTP" stroke="#f97316" dot={false} strokeWidth={1.5} strokeDasharray="4 2" />
            <Line type="monotone" dataKey="chronic_103pct_min" name="Chronic ≥103% FTP" stroke="#a855f7" dot={false} strokeWidth={2} />
            <Line type="monotone" dataKey="acute_103pct_min" name="Acute ≥103% FTP" stroke="#a855f7" dot={false} strokeWidth={1.5} strokeDasharray="4 2" />
          </LineChart>
        </ResponsiveContainer>
      </div>
    )
  }
  ```

- **VALIDATE**: `cd <repo>/frontend && npx tsc --noEmit`

- **COMMIT**: `feat: add IntensityLoadChart with 4-series chronic/acute lines`

---

### Task 10: `RunVolumeLog` — weekly bar + summary table

**Files:**
- Create: `frontend/src/components/charts/RunVolumeLog.tsx`

- **TEST FIRST**: `npx tsc --noEmit` (baseline)

- **IMPLEMENT**:
  ```tsx
  // frontend/src/components/charts/RunVolumeLog.tsx
  import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid,
    Tooltip, Legend, ResponsiveContainer,
  } from 'recharts'
  import { useRunVolume } from '../../api/hooks'

  interface Props {
    dateFrom?: string
    dateTo?: string
  }

  export function RunVolumeLog({ dateFrom, dateTo }: Props = {}) {
    const params = dateFrom || dateTo ? { date_from: dateFrom, date_to: dateTo } : undefined
    const { data, isLoading, isError } = useRunVolume(params)

    if (isLoading) return <div className="flex items-center justify-center h-64 text-gray-500 text-sm">Loading...</div>
    if (isError || !data?.weeks?.length) return <div className="flex items-center justify-center h-64 text-gray-600 text-sm">No run data.</div>

    const weeks = data.weeks.map(w => ({
      ...w,
      date: w.week_start.slice(5),
    }))

    const tickCount = Math.min(weeks.length, 16)
    const step = Math.max(1, Math.floor(weeks.length / tickCount))
    const ticks = weeks.filter((_, i) => i % step === 0).map(w => w.date)

    return (
      <div className="bg-gray-900 rounded-lg p-4 space-y-4">
        <h2 className="text-sm font-semibold text-gray-400">Running Volume Log (跑量日誌)</h2>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={weeks} margin={{ top: 4, right: 12, left: -8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
            <XAxis dataKey="date" ticks={ticks} tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} />
            <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} tickLine={false} axisLine={false} unit=" km" />
            <Tooltip contentStyle={{ background: '#111827', border: '1px solid #374151', fontSize: 12 }} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <Bar dataKey="distance_km" name="Distance (km)" fill="#38bdf8" maxBarSize={14} />
          </BarChart>
        </ResponsiveContainer>
        {/* Monthly summary table */}
        <div className="overflow-x-auto">
          <table className="w-full text-xs text-gray-400">
            <thead>
              <tr className="border-b border-gray-800">
                <th className="text-left py-1">Month</th>
                <th className="text-right py-1">Distance</th>
                <th className="text-right py-1">Hours</th>
                <th className="text-right py-1">Elevation</th>
                <th className="text-right py-1">Runs</th>
              </tr>
            </thead>
            <tbody>
              {data.months.map(m => (
                <tr key={m.month} className="border-b border-gray-800/50">
                  <td className="py-1">{m.month}</td>
                  <td className="text-right">{m.distance_km} km</td>
                  <td className="text-right">{m.hours} h</td>
                  <td className="text-right">{m.elevation_m > 0 ? `${m.elevation_m} m` : '—'}</td>
                  <td className="text-right">{m.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    )
  }
  ```

- **VALIDATE**: `cd <repo>/frontend && npx tsc --noEmit`

- **COMMIT**: `feat: add RunVolumeLog with weekly bar chart and monthly table`

---

### Task 11: Wire all charts into SeasonTab

**Files:**
- Modify: `frontend/src/tabs/SeasonTab.tsx`

- **TEST FIRST**: `npx tsc --noEmit` (baseline)

- **IMPLEMENT**:
  Replace `frontend/src/tabs/SeasonTab.tsx` with:
  ```tsx
  import { useState } from 'react'
  import { DateRangePicker, type DateRange } from '../components/DateRangePicker'
  import { PmcChart } from '../components/PmcChart'
  import { RunLoadChart } from '../components/charts/RunLoadChart'
  import { DailyPctCtlChart } from '../components/charts/DailyPctCtlChart'
  import { RampRateChart } from '../components/charts/RampRateChart'
  import { IntensityLoadChart } from '../components/charts/IntensityLoadChart'
  import { RunVolumeLog } from '../components/charts/RunVolumeLog'

  const toIso = (d: Date) => d.toISOString().slice(0, 10)
  const daysAgo = (n: number) => {
    const d = new Date()
    d.setDate(d.getDate() - n)
    return toIso(d)
  }

  export function SeasonTab() {
    const [range, setRange] = useState<DateRange>({
      from: daysAgo(365),
      to: toIso(new Date()),
    })
    const [showLoad, setShowLoad] = useState(true)

    return (
      <div className="space-y-4">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
          <DateRangePicker value={range} onChange={setRange} />
        </div>
        <PmcChart dateFrom={range.from} dateTo={range.to} />

        {/* Run Load Section */}
        <div className="bg-gray-900 border border-gray-800 rounded-lg overflow-hidden">
          <button
            className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-gray-300 hover:bg-gray-800 transition-colors"
            onClick={() => setShowLoad(v => !v)}
          >
            <span>Run Training Load</span>
            <span className="text-gray-500">{showLoad ? '▲' : '▼'}</span>
          </button>
          {showLoad && (
            <div className="p-4 space-y-4">
              <RunLoadChart dateFrom={range.from} dateTo={range.to} />
              <DailyPctCtlChart dateFrom={range.from} dateTo={range.to} />
              <RampRateChart dateFrom={range.from} dateTo={range.to} />
              <IntensityLoadChart dateFrom={range.from} dateTo={range.to} />
              <RunVolumeLog dateFrom={range.from} dateTo={range.to} />
            </div>
          )}
        </div>
      </div>
    )
  }
  ```

- **VALIDATE**:
  ```bash
  cd <repo>/frontend && npx tsc --noEmit
  ```
  Then start dev server and verify visually:
  ```bash
  cd <repo> && bash start.sh &
  # Open http://localhost:5173 → Season tab → expand "Run Training Load"
  ```
  Expected: 5 chart panels visible; all show loading then empty state (no data yet — needs real workouts).

- **COMMIT**: `feat: wire all run load charts into SeasonTab with collapsible section`

---

## Testing Strategy

### Unit Tests (backend/tests/test_run_pmc.py)

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_run_pmc_zero_input` | `[]` | `[]` | Yes — empty |
| `test_run_pmc_acwr_after_steady_load` | 90 days × 50 TSS | ACWR ≈ 1.0 | No |
| `test_run_pmc_daily_pct_ctl` | 100 TSS spike | daily_pct_ctl = tss/ctl | No |
| `test_run_pmc_ramp_rate_rising` | Increasing series | ramp_rate > 0 | No |
| `test_compute_intensity_load_series_empty` | `[]` | `[]` | Yes — empty |
| `test_compute_intensity_load_series_ewma` | 60s/day × 84d | value ≈ 60 | No |
| `test_endpoint_response_shapes` | 30d series | all keys present | No |
| `test_intensity_time_calculation` | synthetic numpy array | hi_95=60, hi_103=0 | No |

### Edge Cases Checklist
- [x] Empty input → empty list (tested)
- [x] No FTP set → intensity metrics skipped in importer (guarded by `if ftp_w and ftp_w > 0`)
- [x] CTL = 0 → ACWR and daily_pct_ctl return None (guarded by `if ctl > 0`)
- [ ] `workout_files.sport` values: Coros uses "running" (already normalized via `_normalize_sport` in `fit_reader.py`); verify query uses "running" not "run"
- [x] elevation `total_ascent` absent → `elevation_gain_m` stays None, shows `—` in table

---

## Validation Commands

### Static Analysis
```bash
cd <repo>/frontend && npx tsc --noEmit
```
EXPECT: Zero type errors

### Unit Tests
```bash
cd <repo> && python -m pytest backend/tests/test_run_pmc.py -v
```
EXPECT: 8 tests pass

### Full Backend Tests
```bash
cd <repo> && python -m pytest src/tests/ backend/tests/ -v
```
EXPECT: No regressions in existing tests

### DB Migration Validation
```bash
cd <repo> && python -c "
import asyncio
from backend.db.database import init_db
asyncio.run(init_db())
from sqlalchemy import create_engine, text
from pathlib import Path
engine = create_engine(f'sqlite:///{Path.home()}/.wko5coach/wko5coach.db')
with engine.connect() as c:
    rows = c.execute(text('PRAGMA table_info(workout_files)')).fetchall()
    cols = [r[1] for r in rows]
    print('columns:', cols)
    assert 'elevation_gain_m' in cols
    print('OK')
"
```
EXPECT: `elevation_gain_m` in columns list

### Browser Validation
```bash
cd <repo> && bash start.sh
# open http://localhost:5173 → Season tab → Run Training Load section
```
EXPECT: 5 chart panels expand; empty state messages visible if no run workouts present

### Manual Validation
- [ ] Season tab loads without errors in browser console
- [ ] "Run Training Load" section is collapsible
- [ ] All 5 charts display loading state then resolve
- [ ] Date range picker changes propagate to all charts
- [ ] Empty state shows correct message when no run workouts exist
- [ ] With run data: CTL/ATL lines visible, ACWR risk bands green/yellow/red visible, ramp rate reference lines at 0 and +7 visible

---

## Acceptance Criteria
- [ ] All 11 tasks completed
- [ ] `npx tsc --noEmit` passes with zero errors
- [ ] All 8 pytest tests pass
- [ ] No regressions in `src/tests/test_metrics.py` and `src/tests/test_mmp.py`
- [ ] `elevation_gain_m` column present in DB after `init_db()`
- [ ] 3 new API routes respond 200 with correct JSON shape
- [ ] 5 new chart components render in browser without console errors
- [ ] ACWR risk bands (green 0.8–1.3, yellow 1.3–1.5, red >1.5) visible in RunLoadChart
- [ ] Reference lines at y=0 and y=7 visible in RampRateChart
- [ ] Monthly table visible in RunVolumeLog

## Completion Checklist
- [ ] Code follows `compute_pmc()` EWMA math exactly (same `1 - exp(-1/tau)` factor)
- [ ] Intensity threshold uses `>=` (not `>`) to match WKO5 `if(runpower >= 0.95*runFTP, ...)`
- [ ] Sport filter uses `"running"` (output of `_normalize_sport()`) not `"run"`
- [ ] Recharts components use `bg-gray-900` dark theme consistently
- [ ] No hardcoded athlete_id values (always passed as param, default=1)
- [ ] `elevation_gain_m` handles None in SQL aggregation (SQLite `sum(NULL)` = NULL → show `—`)

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `sport="running"` vs `"run"` mismatch in query | M | H | Confirmed: `_normalize_sport()` maps "run/trail" → `"running"` (fit_reader.py:201) — use `"running"` in SQL |
| `ReferenceBand` not in installed Recharts version | L | M | Check `package.json` recharts version; if missing, use `ReferenceArea` instead |
| Intensity metrics absent for old workouts | H | M | Empty state shows backfill prompt; chart renders without error |
| `ramp_rate` for first 7 days uses CTL-from-zero diff | L | L | Produces slightly inflated initial ramp; matches WKO5 behavior (same artifact) |

## Notes
- `compute_run_pmc()` builds its history list internally for the ramp lookback — no circular import needed
- The `_migrate_schema()` function in `database.py` is called every time `init_db()` runs, making it safe to add columns idempotently (PRAGMA check guards against duplicate ALTER)
- Recharts `ComposedChart` is needed for charts combining bars and lines, or multiple Y-axes; `LineChart` suffices for single-axis line charts
- `wko5 backfill-intensity` CLI command is listed in SRS Open Questions — not in scope for this plan; the empty state message points the user to it
