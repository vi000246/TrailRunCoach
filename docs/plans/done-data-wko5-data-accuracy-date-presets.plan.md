# Plan: Data Accuracy Fix + Date Range Presets

> **For agentic workers:** `/prp-implement` will route this plan to the matching skill based on `Metadata.Type` below. Steps use checkbox (`- [ ]`) syntax for tracking.

## Summary

Seeds the Run PMC EWMA from a user-supplied WKO5 CTL value, fills historical TSS gaps via pace-based rTSS for GPS-only runs, exposes a backfill endpoint, and adds calendar-aligned date presets to `DateRangePicker`. Together these bring the Run Training Load chart from max CTL ≈ 26 (only a few months of synced data) toward the correct steady-state range of 40–70 that WKO5 Season View shows.

## User Story

As an athlete, I want to seed the Run PMC from my WKO5 CTL value and backfill historical pace-based TSS, so that the CTL curve starts from a correct baseline instead of rebuilding from zero.

## Problem → Solution

`compute_run_pmc()` starts from CTL=0 with only a few months of data → CTL maxes at 26. WKO5 has years of history and shows CTL ≈ 40–70. Fix: add `initial_ctl` / `initial_atl` seed params, add pace-based rTSS for the GPS-only WKO4 runs, expose a backfill endpoint, and let the user choose date windows with calendar presets.

## Metadata

- **Module**: data
- **Parent Plan**: N/A
- **Source PRD**: `docs/prd/wko5-training-ai.prd.md`
- **Source SRS**: `docs/spec/wko5-data-accuracy-date-presets.spec.md`
- **Source Linear Issue**: N/A
- **Type**: feature
- **Size**: L
- **Complexity**: Large
- **Rigor**: strict
- **Mode**: B — 任務先測
- **TDD**: on
- **Commit cadence**: per-task
- **Estimated Files**: 12

---

## UX Design

### Before

```
DateRangePicker:
  [1M] [3M] [6M] [1Y] [All]  [YYYY-MM-DD] — [YYYY-MM-DD]

Config:
  FTP (W): [220]   LTHR (bpm): [160]   Weight: [70]
  [Save & Recompute PMC]

Run PMC chart: CTL starts at 0, peaks at 26 (only a few months of data)
```

### After

```
DateRangePicker:
  Rolling:  [30d] [90d] [6M] [1Y] [All]
  Calendar: [本月] [上月] [近3月] [近6月] [今年YTD] [去年]
            [YYYY-MM-DD] — [YYYY-MM-DD]

Config:
  FTP (W): [220]   LTHR (bpm): [160]   Weight: [70]
  [Save & Recompute PMC]

  ─── Run Training Load Settings ──────────────────────
  Threshold Pace (sec/km)  [300]  = 5:00/km
                           Used for pace-based rTSS on GPS-only runs

  Seed CTL from WKO5
    Initial CTL (Run)  [55]  TSS/day   (read from WKO5 Season View)
    Initial ATL (Run)  [35]  TSS/day   (read from WKO5 Season View)

  [Backfill TSS]  → shows toast: "Recomputed 45 pace-rTSS, 12 power-TSS, 306 skipped"
  ─────────────────────────────────────────────────────

Run PMC chart: CTL starts at 55 (seeded), builds from there
```

### Interaction Changes

| Touchpoint | Before | After | Notes |
|---|---|---|---|
| DateRangePicker | 5 rolling presets | + 6 calendar presets in second row | Active preset highlighted |
| Config page | FTP/LTHR/Weight only | + Run PMC Seed section | New Card |
| PUT settings | `ftp_w`, `lthr`, `weight_kg` | + `initial_ctl_run`, `initial_atl_run`, `threshold_pace_s_per_km` | All optional |
| GET run-load | Response: `{series, athlete_id}` | + `seeded: bool, initial_ctl_used: float\|null` | Backwards compat |
| POST backfill-tss | N/A | New endpoint: triggers backfill, returns counts | Idempotent |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `backend/db/database.py` | 19–39 | `_migrate_schema()` — exact pattern to follow for new columns |
| P0 | `backend/db/models.py` | 22–31 | `AthleteSettings` ORM model — column declaration syntax |
| P0 | `backend/engine/algorithms/metrics.py` | 177–224 | `compute_run_pmc()` — the function we're extending |
| P0 | `backend/api/athletes.py` | 99–130 | Pydantic model + PUT endpoint pattern |
| P0 | `frontend/src/components/DateRangePicker.tsx` | 1–65 | Existing preset structure to extend |
| P1 | `backend/api/analytics.py` | 61–93 | `run_load` endpoint — pattern to modify for seeding |
| P1 | `frontend/src/pages/ConfigPage.tsx` | 56–183 | Form section pattern to replicate |
| P1 | `frontend/src/api/hooks.ts` | 99–104 | `useUpdateSettings` mutation — pattern for new mutation |
| P1 | `frontend/src/api/client.ts` | 66–80 | `AthleteSettingsResponse` + `SettingsUpdatePayload` to extend |
| P2 | `backend/files/wko4_reader.py` | 1–64 | WKO4 reader to extend with distance/duration extraction |
| P2 | `backend/files/file_service.py` | 68–74 | Shows WKO4 sets `duration_s=None` — confirms the gap |
| P2 | `backend/tests/test_run_pmc.py` | 1–79 | Existing test style to mirror |

---

## Patterns to Mirror

### SCHEMA_MIGRATION_PATTERN
```python
# SOURCE: backend/db/database.py:19-39
async def _migrate_schema():
    new_cols = [
        ("workout_files", "coros_activity_id", "TEXT"),
        ("workout_files", "elevation_gain_m", "REAL"),
    ]
    async with engine.begin() as conn:
        for table, col, col_type in new_cols:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if col not in existing:
                await conn.execute(
                    text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                )
```

### ORM_NULLABLE_COLUMN
```python
# SOURCE: backend/db/models.py:48
elevation_gain_m: Mapped[Optional[float]] = mapped_column(nullable=True)
```

### FASTAPI_ROUTE_WITH_DB
```python
# SOURCE: backend/api/athletes.py:48-57
@router.get("/{athlete_id}/settings")
async def get_settings(athlete_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == athlete_id)
        .order_by(AthleteSettings.effective_date.desc())
    )
    s = result.scalars().first()
    if s is None:
        raise HTTPException(status_code=404, detail="NO_SETTINGS")
```

### PYDANTIC_OPTIONAL_FIELDS
```python
# SOURCE: backend/api/athletes.py:99-103
class SettingsUpdate(BaseModel):
    ftp_w: Optional[float] = None
    lthr: Optional[int] = None
    weight_kg: Optional[float] = None
    effective_date: Optional[date] = None
```

### SETTINGS_UPDATE_SAVE_PATTERN
```python
# SOURCE: backend/api/athletes.py:107-130
if s:
    if body.ftp_w is not None:
        s.ftp_w = body.ftp_w
    if body.lthr is not None:
        s.lthr = body.lthr
else:
    s = AthleteSettings(athlete_id=athlete_id, ...)
    db.add(s)
await db.commit()
return {"saved": True}
```

### PYTEST_METRICS_STYLE
```python
# SOURCE: backend/tests/test_run_pmc.py:1-9
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from datetime import date, timedelta
import pytest
from backend.engine.algorithms.metrics import compute_run_pmc

def _days(start: date, n: int, tss: float) -> list[tuple[date, float]]:
    return [(start + timedelta(days=i), tss) for i in range(n)]
```

### DATE_RANGE_PRESET_STRUCTURE
```typescript
// SOURCE: frontend/src/components/DateRangePicker.tsx:21-27
const PRESETS = [
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
] as const

// Active preset: compare computed range against current value
```

### TANSTACK_MUTATION
```typescript
// SOURCE: frontend/src/api/hooks.ts:99-104
export function useUpdateSettings(athleteId = 1) {
  const qc = useQueryClient()
  return useMutation<{ saved: boolean }, Error, SettingsUpdatePayload>({
    mutationFn: (body) => api.put(`/athletes/${athleteId}/settings`, body).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['athlete_settings', athleteId] }),
  })
}
```

### CONFIG_PAGE_FORM_CARD
```typescript
// SOURCE: frontend/src/pages/ConfigPage.tsx:96-153
<Card>
  <CardHeader><CardTitle>Athlete Settings</CardTitle></CardHeader>
  <CardContent>
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-3 gap-4">
        <div className="space-y-1.5">
          <Label htmlFor="ftp">FTP (W)</Label>
          <Input id="ftp" name="ftp" type="number" defaultValue={settings?.ftp_w ?? ''} />
        </div>
      </div>
      <Button type="submit" disabled={update.isPending} size="sm">
        {update.isPending ? 'Saving...' : 'Save & Recompute PMC'}
      </Button>
    </form>
  </CardContent>
</Card>
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `backend/db/database.py` | UPDATE | Add 3 new columns to `_migrate_schema()` |
| `backend/db/models.py` | UPDATE | Add 3 fields to `AthleteSettings` ORM model |
| `backend/engine/algorithms/metrics.py` | UPDATE | Add `initial_ctl/atl` params to `compute_run_pmc()` + add `pace_rtss()` |
| `backend/api/athletes.py` | UPDATE | Extend `SettingsUpdate`, `get_settings`, `update_settings`; add `backfill-tss` endpoint |
| `backend/api/analytics.py` | UPDATE | Fetch seed values and pass to `compute_run_pmc()` in `run_load` |
| `backend/files/wko4_reader.py` | UPDATE | Add `duration_s` + `total_distance_m` extraction from binary channels |
| `backend/tests/test_run_pmc.py` | UPDATE | Add tests for `pace_rtss()` + seeded `compute_run_pmc()` |
| `frontend/src/api/client.ts` | UPDATE | Add 3 new fields to `AthleteSettingsResponse`; add `BackfillResult` interface |
| `frontend/src/api/hooks.ts` | UPDATE | Add `useBackfillTss` mutation; extend `useUpdateSettings` payload type |
| `frontend/src/components/DateRangePicker.tsx` | UPDATE | Add calendar-aligned preset group |
| `frontend/src/pages/ConfigPage.tsx` | UPDATE | Add "Run Training Load Settings" Card section |

## NOT Building

- Full WKO4 binary channel decoding (power, HR, cadence — future milestone)
- NGP (Normalized Graded Pace) — using simplified avg-pace rTSS only
- HR-based TSS for non-running sports (table tennis, weight training, cycling without power) — **future milestone**: will need sport-specific TSS dispatcher and per-sport PMC chart; `pace_rtss()` naming convention (sport_rtss) should be followed when adding HR TSS
- Garmin / multi-platform sync — architecture already supports it via `WorkoutFile.source` field; adding Garmin means a new sync module mirroring `backend/api/sync.py`; FIT parsing already works for Garmin files
- Per-date threshold pace history (single global value only)
- `initial_ctl_run` applied to overall PMC chart (only run-load endpoint)
- Protection/confirmation on backfill endpoint (idempotent by design)

---

## Step-by-Step Tasks

### Task 1: Schema Migration — 3 new columns in `athlete_settings`

**Files:**
- Modify: `backend/db/database.py:21-31`

**TEST FIRST:**
```python
# backend/tests/test_migration.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import asyncio
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

@pytest.mark.asyncio
async def test_migrate_schema_adds_new_columns():
    """_migrate_schema() must add 3 columns to athlete_settings idempotently."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    from backend.db.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Simulate old schema (no new cols) then run migration
    from backend.db.database import _migrate_schema
    # Monkey-patch engine for test
    import backend.db.database as db_mod
    orig = db_mod.engine
    db_mod.engine = engine
    try:
        await _migrate_schema()
        await _migrate_schema()  # second call must be idempotent
        async with engine.begin() as conn:
            result = await conn.execute(text("PRAGMA table_info(athlete_settings)"))
            cols = {row[1] for row in result.fetchall()}
        assert "threshold_pace_s_per_km" in cols
        assert "initial_ctl_run" in cols
        assert "initial_atl_run" in cols
    finally:
        db_mod.engine = orig
```

Run: `cd <repo> && python -m pytest backend/tests/test_migration.py -v` — expect FAIL (columns don't exist yet)

**IMPLEMENT:**
```python
# In backend/db/database.py, add to the new_cols list:
async def _migrate_schema():
    new_cols = [
        ("workout_files", "coros_activity_id", "TEXT"),
        ("workout_files", "coros_sport_type", "INTEGER"),
        ("workout_files", "elevation_gain_m", "REAL"),
        ("sync_state", "coros_access_token", "TEXT"),
        ("sync_state", "coros_token_expires", "DATETIME"),
        ("sync_state", "coros_last_sync_at", "DATETIME"),
        ("sync_state", "coros_email", "TEXT"),
        ("sync_state", "coros_base_url", "TEXT"),
        ("sync_state", "coros_user_id", "TEXT"),
        # NEW:
        ("athlete_settings", "threshold_pace_s_per_km", "REAL"),
        ("athlete_settings", "initial_ctl_run", "REAL"),
        ("athlete_settings", "initial_atl_run", "REAL"),
    ]
    # rest unchanged
```

**VALIDATE:** Run test — expect PASS. Run twice to verify idempotency.

**COMMIT:** `feat: add threshold_pace_s_per_km, initial_ctl_run, initial_atl_run columns to athlete_settings`

---

### Task 2: ORM Model — `AthleteSettings` 3 new fields

**Files:**
- Modify: `backend/db/models.py:22-31`

**TEST FIRST:**
```python
# In backend/tests/test_migration.py, add:
@pytest.mark.asyncio
async def test_athlete_settings_orm_has_new_fields():
    """AthleteSettings ORM model must have the 3 new fields."""
    from backend.db.models import AthleteSettings
    s = AthleteSettings(
        athlete_id=1,
        effective_date=date(2026, 1, 1),
        threshold_pace_s_per_km=300.0,
        initial_ctl_run=55.0,
        initial_atl_run=35.0,
    )
    assert s.threshold_pace_s_per_km == 300.0
    assert s.initial_ctl_run == 55.0
    assert s.initial_atl_run == 35.0
```

Run: expect FAIL (AttributeError: 'AthleteSettings' object has no attribute 'threshold_pace_s_per_km')

**IMPLEMENT:**
```python
# In backend/db/models.py, add to AthleteSettings after lthr:
class AthleteSettings(Base):
    __tablename__ = "athlete_settings"
    __table_args__ = (UniqueConstraint("athlete_id", "effective_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"))
    effective_date: Mapped[date] = mapped_column(Date)
    ftp_w: Mapped[Optional[float]]
    weight_kg: Mapped[Optional[float]]
    lthr: Mapped[Optional[int]]
    # NEW:
    threshold_pace_s_per_km: Mapped[Optional[float]] = mapped_column(nullable=True)
    initial_ctl_run: Mapped[Optional[float]] = mapped_column(nullable=True)
    initial_atl_run: Mapped[Optional[float]] = mapped_column(nullable=True)
    athlete: Mapped["Athlete"] = relationship(back_populates="settings")
```

**VALIDATE:** Run `python -m pytest backend/tests/test_migration.py -v` — expect both tests PASS.

**COMMIT:** `feat: add threshold_pace_s_per_km, initial_ctl_run, initial_atl_run to AthleteSettings ORM`

---

### Task 3: Algorithm — `compute_run_pmc()` seed parameters

**Files:**
- Modify: `backend/engine/algorithms/metrics.py:177-224`
- Modify: `backend/tests/test_run_pmc.py`

**TEST FIRST:**
```python
# In backend/tests/test_run_pmc.py, add:
def test_run_pmc_seeded_initial_ctl():
    """compute_run_pmc with initial_ctl=50 starts CTL above 0 on day 1."""
    start = date(2025, 1, 1)
    series = _days(start, 30, 50.0)
    # Unseeded
    unseeded = compute_run_pmc(series)
    # Seeded
    seeded = compute_run_pmc(series, initial_ctl=50.0, initial_atl=30.0)
    assert seeded[0]["ctl"] > unseeded[0]["ctl"]
    assert seeded[0]["ctl"] == pytest.approx(50.0 * (1 - (1 - math.exp(-1/42))) + 50.0 * (1 - math.exp(-1/42)), rel=0.01)


def test_run_pmc_seed_zero_is_backwards_compatible():
    """Default seed=0 must produce same result as old behavior."""
    start = date(2025, 1, 1)
    series = _days(start, 30, 50.0)
    new_result = compute_run_pmc(series, initial_ctl=0.0, initial_atl=0.0)
    old_result = compute_run_pmc(series)
    assert new_result == old_result
```

Run: `python -m pytest backend/tests/test_run_pmc.py -v -k "seed"` — expect FAIL (no initial_ctl param)

**IMPLEMENT:** In `compute_run_pmc()` signature and body:
```python
def compute_run_pmc(
    run_tss_series: list[tuple[date, float]],
    ctl_tau: float = 42.0,
    atl_tau: float = 7.0,
    ramp_days: int = 7,
    initial_ctl: float = 0.0,   # NEW
    initial_atl: float = 0.0,   # NEW
) -> list[dict]:
    ...
    ctl_factor = 1 - math.exp(-1 / ctl_tau)
    atl_factor = 1 - math.exp(-1 / atl_tau)
    tss_dict = {d: t for d, t in run_tss_series}
    ...
    ctl = initial_ctl   # WAS: ctl = atl = 0.0
    atl = initial_atl   # WAS: part of above
    prev_ctl = initial_ctl
    prev_atl = initial_atl
    history: list[dict] = []
    ...  # rest unchanged
```

Also add `import math` if not already at the top (it's already imported).

**VALIDATE:** `python -m pytest backend/tests/test_run_pmc.py -v` — all 9 tests pass.

**COMMIT:** `feat: add initial_ctl/initial_atl seed params to compute_run_pmc`

---

### Task 4: `pace_rtss()` function

**Files:**
- Modify: `backend/engine/algorithms/metrics.py` (add new function after `compute_run_pmc`)
- Modify: `backend/tests/test_run_pmc.py`

**TEST FIRST:**
```python
# In backend/tests/test_run_pmc.py, add:
# Also import at top: from backend.engine.algorithms.metrics import ..., pace_rtss

def test_pace_rtss_basic():
    """pace_rtss: run at threshold pace → IF=1.0 → rTSS = duration_hours * 100."""
    threshold_pace_s_per_m = 5.0  # 5 s/m = 5:00/km threshold
    distance_m = 10_000.0
    duration_s = 50_000.0  # exactly threshold pace: 50000/10000 = 5.0 s/m
    result = pace_rtss(distance_m, duration_s, threshold_pace_s_per_m)
    # IF = 1.0; rTSS = (50000/3600) * 1.0^2 * 100 = 1388.9
    assert result == pytest.approx(50000 / 3600 * 100, rel=0.01)


def test_pace_rtss_faster_than_threshold():
    """Faster pace → IF > 1.0 → rTSS > duration_hours * 100."""
    threshold_pace_s_per_m = 5.0
    distance_m = 10_000.0
    duration_s = 40_000.0  # 4.0 s/m, faster than threshold
    result = pace_rtss(distance_m, duration_s, threshold_pace_s_per_m)
    # IF = 5.0 / 4.0 = 1.25
    expected = (40000 / 3600) * (1.25 ** 2) * 100
    assert result == pytest.approx(expected, rel=0.01)


def test_pace_rtss_zero_guard():
    """pace_rtss returns 0.0 for any None/zero input."""
    assert pace_rtss(0.0, 3600.0, 5.0) == 0.0
    assert pace_rtss(1000.0, 0.0, 5.0) == 0.0
    assert pace_rtss(1000.0, 3600.0, 0.0) == 0.0
    assert pace_rtss(None, 3600.0, 5.0) == 0.0
```

Run: `python -m pytest backend/tests/test_run_pmc.py -v -k "pace_rtss"` — expect FAIL (ImportError)

**IMPLEMENT:** Add after `compute_run_pmc()` in `metrics.py`:
```python
def pace_rtss(
    distance_m: Optional[float],
    duration_s: Optional[float],
    threshold_pace_s_per_m: Optional[float],
) -> float:
    """
    Simplified pace-based running TSS (no elevation correction).
    IF = threshold_pace_s_per_m / (duration_s / distance_m)
    rTSS = (duration_s / 3600) * IF^2 * 100
    """
    if not distance_m or not duration_s or not threshold_pace_s_per_m:
        return 0.0
    if distance_m <= 0 or duration_s <= 0 or threshold_pace_s_per_m <= 0:
        return 0.0
    avg_pace_s_per_m = duration_s / distance_m
    intensity_factor = threshold_pace_s_per_m / avg_pace_s_per_m
    rtss = (duration_s / 3600.0) * (intensity_factor ** 2) * 100.0
    return round(rtss, 1)
```

Also update the import in `test_run_pmc.py`:
```python
from backend.engine.algorithms.metrics import compute_run_pmc, compute_intensity_load_series, pace_rtss
import math
```

**VALIDATE:** `python -m pytest backend/tests/test_run_pmc.py -v` — all 12 tests pass.

**COMMIT:** `feat: add pace_rtss() function for GPS-only run TSS estimation`

---

### Task 5: Settings API — extend for 3 new fields

**Files:**
- Modify: `backend/api/athletes.py:48-130`

**TEST FIRST** (manual API test):
After implementation, run:
```bash
curl -s http://localhost:8000/api/v1/athletes/1/settings | python3 -m json.tool | grep -E "threshold|initial_ctl|initial_atl"
```
Expected: fields appear in response (initially null).
```bash
curl -s -X PUT http://localhost:8000/api/v1/athletes/1/settings \
  -H 'Content-Type: application/json' \
  -d '{"threshold_pace_s_per_km": 300, "initial_ctl_run": 55.0, "initial_atl_run": 35.0}' | python3 -m json.tool
```
Expected: `{"saved": true}`

**IMPLEMENT:**

Update `SettingsUpdate` Pydantic model to add 3 new optional fields:
```python
class SettingsUpdate(BaseModel):
    ftp_w: Optional[float] = None
    lthr: Optional[int] = None
    weight_kg: Optional[float] = None
    effective_date: Optional[date] = None
    # NEW:
    threshold_pace_s_per_km: Optional[float] = None
    initial_ctl_run: Optional[float] = None
    initial_atl_run: Optional[float] = None
```

Update `get_settings()` response to include new fields:
```python
return {
    "athlete_id": athlete_id,
    "effective_date": s.effective_date.isoformat(),
    "ftp_w": ftp,
    "lthr": lthr,
    "weight_kg": s.weight_kg,
    # NEW:
    "threshold_pace_s_per_km": s.threshold_pace_s_per_km,
    "initial_ctl_run": s.initial_ctl_run,
    "initial_atl_run": s.initial_atl_run,
    "power_zones": power_zones,
    "hr_zones": hr_zones,
}
```

Update `update_settings()` to save new fields (follow the existing `if body.X is not None: s.X = body.X` pattern):
```python
# In the if s: block, add:
if body.threshold_pace_s_per_km is not None:
    s.threshold_pace_s_per_km = body.threshold_pace_s_per_km
if body.initial_ctl_run is not None:
    s.initial_ctl_run = body.initial_ctl_run
if body.initial_atl_run is not None:
    s.initial_atl_run = body.initial_atl_run
# In the else: (new AthleteSettings creation), add new fields to constructor:
s = AthleteSettings(
    athlete_id=athlete_id, effective_date=eff_date,
    ftp_w=body.ftp_w, lthr=body.lthr, weight_kg=body.weight_kg,
    threshold_pace_s_per_km=body.threshold_pace_s_per_km,
    initial_ctl_run=body.initial_ctl_run,
    initial_atl_run=body.initial_atl_run,
)
```

**VALIDATE:** Run the curl commands above against the live server.

**COMMIT:** `feat: extend settings API with threshold_pace, initial_ctl_run, initial_atl_run`

---

### Task 6: `POST /athletes/{id}/backfill-tss` endpoint

**Files:**
- Modify: `backend/api/athletes.py` (add new endpoint and needed imports)

**TEST FIRST** (manual):
```bash
# Start server, then:
curl -s -X POST http://localhost:8000/api/v1/athletes/1/backfill-tss | python3 -m json.tool
```
Expected response shape:
```json
{
  "recomputed_power_tss": 0,
  "computed_rtss_pace": 0,
  "skipped_no_data": 300,
  "skipped_already_has_tss": 50
}
```
After setting `threshold_pace_s_per_km=300` in settings, expect `computed_rtss_pace` > 0.

**IMPLEMENT:**

Add imports at top of `backend/api/athletes.py`:
```python
from sqlalchemy import select, not_, exists
from backend.db.models import Athlete, AthleteSettings, WorkoutFile, WorkoutMetric
from backend.engine.algorithms.metrics import compute_all_metrics, pace_rtss
from backend.files.fit_reader import parse_fit
```

Add new endpoint:
```python
@router.post("/{athlete_id}/backfill-tss")
async def backfill_tss(athlete_id: int, db: AsyncSession = Depends(get_db)):
    """Re-compute TSS for all running workouts missing it. Never overwrites existing TSS."""
    # Fetch latest settings
    settings_q = await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == athlete_id)
        .order_by(AthleteSettings.effective_date.desc())
    )
    settings = settings_q.scalars().first()
    ftp_w = settings.ftp_w if settings else None
    threshold_pace_s_per_m = (
        settings.threshold_pace_s_per_km / 1000.0
        if settings and settings.threshold_pace_s_per_km
        else None
    )

    # Find all run workouts that have NO tss metric
    has_tss = (
        select(WorkoutMetric.workout_id)
        .where(WorkoutMetric.metric_key == "tss")
    )
    q = (
        select(WorkoutFile)
        .where(
            WorkoutFile.athlete_id == athlete_id,
            WorkoutFile.sport == "running",
            WorkoutFile.workout_date.isnot(None),
            ~WorkoutFile.id.in_(has_tss),
        )
    )
    result = await db.execute(q)
    missing_tss_workouts = result.scalars().all()

    recomputed_power = 0
    computed_rtss = 0
    skipped_no_data = 0

    for wf in missing_tss_workouts:
        if wf.file_format == "fit" and ftp_w and ftp_w > 0:
            try:
                raw = parse_fit(wf.file_path)
                if raw.has_power:
                    metrics = compute_all_metrics(raw.power_w, ftp_w=ftp_w, duration_s=raw.duration_s)
                    tss_val = metrics.get("tss")
                    if tss_val is not None:
                        db.add(WorkoutMetric(
                            workout_id=wf.id, metric_key="tss", value=float(tss_val)
                        ))
                        recomputed_power += 1
                        continue
            except Exception:
                pass

        if wf.total_distance_m and wf.duration_s and threshold_pace_s_per_m:
            rtss_val = pace_rtss(wf.total_distance_m, wf.duration_s, threshold_pace_s_per_m)
            if rtss_val > 0:
                db.add(WorkoutMetric(
                    workout_id=wf.id, metric_key="rtss_pace", value=rtss_val
                ))
                # Also set canonical tss key
                db.add(WorkoutMetric(
                    workout_id=wf.id, metric_key="tss", value=rtss_val
                ))
                computed_rtss += 1
                continue

        skipped_no_data += 1

    # Count already-having-tss separately
    has_tss_count_q = await db.execute(
        select(WorkoutFile)
        .join(WorkoutMetric, WorkoutMetric.workout_id == WorkoutFile.id)
        .where(
            WorkoutFile.athlete_id == athlete_id,
            WorkoutFile.sport == "running",
            WorkoutMetric.metric_key == "tss",
        )
    )
    already_has_tss = len(has_tss_count_q.scalars().all())

    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {
        "recomputed_power_tss": recomputed_power,
        "computed_rtss_pace": computed_rtss,
        "skipped_no_data": skipped_no_data,
        "skipped_already_has_tss": already_has_tss,
    }
```

**GOTCHA**: `WorkoutMetric` has a UniqueConstraint on `(workout_id, metric_key)`. The INSERT for `tss` key will fail if it already exists. We've already filtered out workouts with TSS via the subquery, but the race condition on `rtss_pace` + `tss` double-insert needs care. Use `try/except` around the commit or use `INSERT OR IGNORE`. Since SQLAlchemy's `db.add()` doesn't do ON CONFLICT, wrap each workout's adds in a try/except with rollback to savepoint.

Actually, the safest approach is to use two separate queries — the subquery already ensures no existing tss. But `rtss_pace` might already exist from a previous run. Use `merge` or check existence first:

```python
# Before adding tss metric, verify no existing tss (double-check):
existing_check = await db.execute(
    select(WorkoutMetric).where(
        WorkoutMetric.workout_id == wf.id,
        WorkoutMetric.metric_key == "tss",
    )
)
if existing_check.scalar_one_or_none() is None:
    db.add(WorkoutMetric(workout_id=wf.id, metric_key="tss", value=rtss_val))
```

**COMMIT:** `feat: add POST /athletes/{id}/backfill-tss endpoint`

---

### Task 7: `GET /run-load` — fetch and apply seed values

**Files:**
- Modify: `backend/api/analytics.py:61-93`

**TEST FIRST** (manual):
```bash
# Set initial_ctl_run=55 first (Task 5 must be done):
curl -s -X PUT http://localhost:8000/api/v1/athletes/1/settings \
  -H 'Content-Type: application/json' \
  -d '{"initial_ctl_run": 55.0, "initial_atl_run": 35.0}'

# Then check run-load response:
curl -s 'http://localhost:8000/api/v1/analytics/run-load?date_from=2026-01-05&date_to=2026-01-11' | python3 -m json.tool
```
Expected: `seeded: true`, `initial_ctl_used: 55.0`, and `series[0].ctl > 0`.

**IMPLEMENT:**

Update `run_load()` in `analytics.py`:

```python
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

    # NEW: fetch seed from settings
    from backend.db.models import AthleteSettings
    settings_q = await db.execute(
        select(AthleteSettings)
        .where(AthleteSettings.athlete_id == athlete_id)
        .order_by(AthleteSettings.effective_date.desc())
    )
    settings = settings_q.scalars().first()
    initial_ctl = (settings.initial_ctl_run or 0.0) if settings else 0.0
    initial_atl = (settings.initial_atl_run or 0.0) if settings else 0.0

    # existing TSS query (unchanged)
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
    pmc_data = compute_run_pmc(run_series, initial_ctl=initial_ctl, initial_atl=initial_atl)  # CHANGED
    filtered = [p for p in pmc_data if date_from.isoformat() <= p["date"] <= date_to.isoformat()]
    return {
        "series": filtered,
        "athlete_id": athlete_id,
        "seeded": initial_ctl > 0.0,           # NEW
        "initial_ctl_used": initial_ctl if initial_ctl > 0.0 else None,  # NEW
    }
```

**VALIDATE:** Run the curl commands above. Verify first series point has CTL > 50 when seeded.

**COMMIT:** `feat: pass seed CTL/ATL from settings to compute_run_pmc in run-load endpoint`

---

### Task 8: WKO4 Minimal Extractor (EXPLORATORY)

**Files:**
- Modify: `backend/files/wko4_reader.py`
- Modify: `backend/db/database.py` (scan re-import trigger — see IMPLEMENT)

**Context from binary analysis (confirmed):**
- `elapsedtime` channel exists at byte 52138 in a sample WKO4 file
- `elapseddistance` channel exists at byte 88470
- Both use `\xb4\x06` marker after the field name
- After `\xb4\x06`, the next 2 bytes are a LE uint16 sample count
- Each sample appears to be 2-byte timestamp + 8-byte float64 LE = 10 bytes per record
- The last float64 in the `elapseddistance` channel = total distance in meters
- The last float64 in the `elapsedtime` channel = elapsed time in seconds

**CAUTION**: The 10-byte-per-record structure is derived from limited observation. The test below must validate against a known WKO4 file.

**TEST FIRST:**
```python
# backend/tests/test_wko4_extractor.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import pytest
from pathlib import Path
from backend.files.wko4_reader import extract_wko4_metrics

SAMPLE_FILE = Path.home() / "WKO5/Athlete/<year>/Athlete_<YYYY_MM_DD_HH_MM>.wko4"  # any local sample

@pytest.mark.skipif(not SAMPLE_FILE.exists(), reason="WKO4 sample file not available")
def test_extract_wko4_metrics_returns_reasonable_values():
    """Extracted distance and duration must be within plausible running ranges."""
    result = extract_wko4_metrics(str(SAMPLE_FILE))
    if result["total_distance_m"] is not None:
        assert 100 < result["total_distance_m"] < 100_000, f"Unexpected distance: {result['total_distance_m']}"
    if result["duration_s"] is not None:
        assert 60 < result["duration_s"] < 86400, f"Unexpected duration: {result['duration_s']}"


def test_extract_wko4_metrics_missing_file_returns_none():
    """extract_wko4_metrics must not crash on missing or invalid files."""
    result = extract_wko4_metrics("/nonexistent/file.wko4")
    assert result["total_distance_m"] is None
    assert result["duration_s"] is None
```

Run: `python -m pytest backend/tests/test_wko4_extractor.py -v` — expect FAIL (ImportError)

**IMPLEMENT:**

Add `extract_wko4_metrics()` to `wko4_reader.py`:
```python
import struct

_CHANNEL_MARKER = b"\xb4\x06"

def extract_wko4_metrics(path: str) -> dict:
    """
    Extract total_distance_m and duration_s from WKO4 binary channels.
    Returns {"total_distance_m": float|None, "duration_s": float|None}.
    Gracefully returns None values on any parse failure.
    """
    result = {"total_distance_m": None, "duration_s": None}
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return result

    def _read_last_float64(field_name: bytes) -> Optional[float]:
        """Find a binary channel by name and read its last float64 sample."""
        marker = field_name + _CHANNEL_MARKER
        idx = data.find(marker)
        if idx < 0:
            return None
        idx += len(marker)
        if idx + 2 > len(data):
            return None
        count = struct.unpack_from("<H", data, idx)[0]
        if count == 0:
            return None
        # Records: 2-byte timestamp + 8-byte float64 = 10 bytes each
        last_offset = idx + 2 + (count - 1) * 10 + 2  # skip last timestamp
        if last_offset + 8 > len(data):
            # Fallback: try 4-byte float32 records (2 + 4 = 6 bytes each)
            last_offset_f32 = idx + 2 + (count - 1) * 6 + 2
            if last_offset_f32 + 4 <= len(data):
                val = struct.unpack_from("<f", data, last_offset_f32)[0]
                return float(val) if 0 < val < 1e9 else None
            return None
        val = struct.unpack_from("<d", data, last_offset)[0]
        return float(val) if 0 < val < 1e9 else None

    result["total_distance_m"] = _read_last_float64(b"elapseddistance")
    result["duration_s"] = _read_last_float64(b"elapsedtime")
    return result
```

Update `parse_wko4_metadata()` to also call this (so that `_import_one_file` can use it):
```python
@dataclass
class Wko4Metadata:
    start_time: Optional[datetime]
    sport: str
    source_file: str
    duration_s: Optional[float] = None        # NEW
    total_distance_m: Optional[float] = None  # NEW
```

In `parse_wko4_metadata()`, after reading sport:
```python
metrics = extract_wko4_metrics(path)
return Wko4Metadata(
    start_time=start_time,
    sport=sport,
    source_file=path,
    duration_s=metrics["duration_s"],
    total_distance_m=metrics["total_distance_m"],
)
```

Update `file_service.py:68-74` to use the new fields:
```python
if fmt == "wko4":
    meta = parse_wko4_metadata(str(path))
    raw = None
    start_time = meta.start_time
    sport = meta.sport
    duration_s = meta.duration_s       # WAS: None
    total_distance_m = meta.total_distance_m  # WAS: None
```

**VALIDATE:**
```bash
python -m pytest backend/tests/test_wko4_extractor.py -v
```
If values are None (format mismatch), inspect the actual bytes and adjust `_read_last_float64()`. The test only validates plausibility, not exact values.

**COMMIT:** `feat: extract duration_s and total_distance_m from WKO4 binary channels`

---

### Task 9: TypeScript types — extend `client.ts`

**Files:**
- Modify: `frontend/src/api/client.ts`

**TEST FIRST** (TypeScript compiler):
```bash
cd <repo>/frontend && npx tsc --noEmit 2>&1 | head -20
```
After adding the types, this should still pass (no new errors).

**IMPLEMENT:**

In `frontend/src/api/client.ts`:

1. Extend `AthleteSettingsResponse` (add 3 new nullable fields):
```typescript
export interface AthleteSettingsResponse {
  athlete_id: number
  effective_date: string
  ftp_w: number | null
  lthr: number | null
  weight_kg: number | null
  // NEW:
  threshold_pace_s_per_km: number | null
  initial_ctl_run: number | null
  initial_atl_run: number | null
  power_zones: PowerZone[]
  hr_zones: HrZone[]
}
```

2. Extend `SettingsUpdatePayload` (add 3 new optional fields):
```typescript
export interface SettingsUpdatePayload {
  ftp_w?: number
  lthr?: number
  weight_kg?: number
  // NEW:
  threshold_pace_s_per_km?: number
  initial_ctl_run?: number
  initial_atl_run?: number
}
```

3. Add `BackfillResult` interface:
```typescript
export interface BackfillResult {
  recomputed_power_tss: number
  computed_rtss_pace: number
  skipped_no_data: number
  skipped_already_has_tss: number
}
```

**VALIDATE:** `cd frontend && npx tsc --noEmit` — zero errors.

**COMMIT:** `feat: extend TypeScript types for settings + add BackfillResult interface`

---

### Task 10: `DateRangePicker.tsx` — calendar-aligned preset group

**Files:**
- Modify: `frontend/src/components/DateRangePicker.tsx`

**TEST FIRST** (manual visual):
After implementation, open Config or Season page, verify:
- Two rows of preset buttons appear
- "本月" sets `from` to first day of current month
- "去年" sets `from` = Jan 1 of prev year, `to` = Dec 31 of prev year
- Active preset button is highlighted (different background)
- Manual date input clears active highlight

**IMPLEMENT:**

Replace `DateRangePicker.tsx` content:
```typescript
import { Button } from './ui/button'

export interface DateRange {
  from: string  // YYYY-MM-DD
  to: string
}

interface Props {
  value: DateRange
  onChange: (range: DateRange) => void
}

const toIso = (d: Date) => d.toISOString().slice(0, 10)
const today = () => toIso(new Date())

function daysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return toIso(d)
}

interface CalendarPreset {
  label: string
  getRange: () => DateRange
}

function computeCalendarPresets(): CalendarPreset[] {
  return [
    {
      label: '本月',
      getRange: () => {
        const d = new Date()
        return { from: toIso(new Date(d.getFullYear(), d.getMonth(), 1)), to: today() }
      },
    },
    {
      label: '上月',
      getRange: () => {
        const d = new Date()
        const first = new Date(d.getFullYear(), d.getMonth() - 1, 1)
        const last = new Date(d.getFullYear(), d.getMonth(), 0)
        return { from: toIso(first), to: toIso(last) }
      },
    },
    {
      label: '近3月',
      getRange: () => {
        const d = new Date()
        return { from: toIso(new Date(d.getFullYear(), d.getMonth() - 3, 1)), to: today() }
      },
    },
    {
      label: '近6月',
      getRange: () => {
        const d = new Date()
        return { from: toIso(new Date(d.getFullYear(), d.getMonth() - 6, 1)), to: today() }
      },
    },
    {
      label: '今年YTD',
      getRange: () => {
        const d = new Date()
        return { from: toIso(new Date(d.getFullYear(), 0, 1)), to: today() }
      },
    },
    {
      label: '去年',
      getRange: () => {
        const d = new Date()
        const y = d.getFullYear() - 1
        return { from: `${y}-01-01`, to: `${y}-12-31` }
      },
    },
  ]
}

const ROLLING_PRESETS = [
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
  { label: '6M', days: 180 },
  { label: '1Y', days: 365 },
  { label: 'All', days: 365 * 10 },
] as const

const CALENDAR_PRESETS = computeCalendarPresets()

function isActiveRolling(value: DateRange, days: number): boolean {
  const expected = { from: daysAgo(days), to: today() }
  return value.from === expected.from && value.to === expected.to
}

function isActiveCalendar(value: DateRange, preset: CalendarPreset): boolean {
  const expected = preset.getRange()
  return value.from === expected.from && value.to === expected.to
}

const activeClass = 'bg-[#7c3aed] text-white'
const inactiveClass = 'text-[#7d8fa6]'

export function DateRangePicker({ value, onChange }: Props) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] text-[#3e4e63] uppercase tracking-wide w-12">Rolling</span>
        {ROLLING_PRESETS.map(p => (
          <Button
            key={p.label}
            variant="secondary"
            size="sm"
            onClick={() => onChange({ from: daysAgo(p.days), to: today() })}
            className={`text-xs h-7 px-2.5 ${isActiveRolling(value, p.days) ? activeClass : inactiveClass}`}
          >
            {p.label}
          </Button>
        ))}
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] text-[#3e4e63] uppercase tracking-wide w-12">Cal</span>
        {CALENDAR_PRESETS.map(p => (
          <Button
            key={p.label}
            variant="secondary"
            size="sm"
            onClick={() => onChange(p.getRange())}
            className={`text-xs h-7 px-2.5 ${isActiveCalendar(value, p) ? activeClass : inactiveClass}`}
          >
            {p.label}
          </Button>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <input
          type="date"
          value={value.from}
          max={value.to}
          onChange={e => onChange({ ...value, from: e.target.value })}
          className="h-7 px-2 text-xs bg-[#141922] border border-[#1c2333] rounded text-[#e8edf5] focus:outline-none focus:ring-1 focus:ring-[#7c3aed]"
          style={{ colorScheme: 'dark' }}
        />
        <span className="text-xs text-[#3e4e63]">—</span>
        <input
          type="date"
          value={value.to}
          min={value.from}
          max={today()}
          onChange={e => onChange({ ...value, to: e.target.value })}
          className="h-7 px-2 text-xs bg-[#141922] border border-[#1c2333] rounded text-[#e8edf5] focus:outline-none focus:ring-1 focus:ring-[#7c3aed]"
          style={{ colorScheme: 'dark' }}
        />
      </div>
    </div>
  )
}
```

**GOTCHA**: `computeCalendarPresets()` is called at module load time and captures `new Date()`. Presets will be computed once. For production this is fine since they're clicked at runtime (the functions are called on click). The `getRange()` functions use closures over `new Date()` called at click time — correct behavior.

**VALIDATE:**
```bash
cd <repo>/frontend && npx tsc --noEmit && npm run build
```
Then open browser and verify both preset rows appear and active highlighting works.

**COMMIT:** `feat: add calendar-aligned presets to DateRangePicker`

---

### Task 11: `hooks.ts` — add `useBackfillTss`

**Files:**
- Modify: `frontend/src/api/hooks.ts`

**TEST FIRST** (TypeScript compiler):
After adding the hook, `npx tsc --noEmit` must pass.

**IMPLEMENT:**

Add import for `BackfillResult`:
```typescript
import type {
  ...,
  BackfillResult,
} from './client'
```

Add new mutation:
```typescript
export function useBackfillTss(athleteId = 1) {
  const qc = useQueryClient()
  return useMutation<BackfillResult, Error>({
    mutationFn: () => api.post(`/athletes/${athleteId}/backfill-tss`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['run_load'] })
      qc.invalidateQueries({ queryKey: ['run_volume'] })
      qc.invalidateQueries({ queryKey: ['intensity_load'] })
    },
  })
}
```

**VALIDATE:** `npx tsc --noEmit` — zero errors.

**COMMIT:** `feat: add useBackfillTss mutation hook`

---

### Task 12: `ConfigPage.tsx` — Run PMC Seed section

**Files:**
- Modify: `frontend/src/pages/ConfigPage.tsx`

**TEST FIRST** (manual):
After implementation, open `/config` in browser and verify:
- New "Run Training Load Settings" Card appears below Training Zones
- Threshold Pace input shows current value if set
- Initial CTL/ATL inputs show current values if set
- [Backfill TSS] button triggers the endpoint and shows a toast with the result counts

**IMPLEMENT:**

Add imports at top of `ConfigPage.tsx`:
```typescript
import { useBackfillTss } from '../api/hooks'
```

Update `ConfigPage` component to use the new hook and add the new section.

Inside `ConfigPage()` function, add:
```typescript
const backfill = useBackfillTss()
const [backfillResult, setBackfillResult] = useState<string | null>(null)
```

Update `handleSubmit` to include new fields:
```typescript
const handleSubmit = (e: React.FormEvent<HTMLFormElement>) => {
  e.preventDefault()
  const fd = new FormData(e.currentTarget)
  const ftpVal = fd.get('ftp') as string
  const lthrVal = fd.get('lthr') as string
  const weightVal = fd.get('weight') as string
  const thresholdPaceVal = fd.get('threshold_pace') as string
  const initialCtlVal = fd.get('initial_ctl') as string
  const initialAtlVal = fd.get('initial_atl') as string
  update.mutate(
    {
      ftp_w: ftpVal ? parseFloat(ftpVal) : undefined,
      lthr: lthrVal ? parseInt(lthrVal, 10) : undefined,
      weight_kg: weightVal ? parseFloat(weightVal) : undefined,
      threshold_pace_s_per_km: thresholdPaceVal ? parseFloat(thresholdPaceVal) : undefined,
      initial_ctl_run: initialCtlVal ? parseFloat(initialCtlVal) : undefined,
      initial_atl_run: initialAtlVal ? parseFloat(initialAtlVal) : undefined,
    },
    {
      onSuccess: () => {
        recompute.mutate()
        showToast('Saved — PMC recomputing...')
      },
      onError: () => showToast('Save failed'),
    },
  )
}
```

Add new Card section AFTER the Training Zones card (before the closing `</div>`):

```typescript
<Card>
  <CardHeader>
    <CardTitle>Run Training Load Settings</CardTitle>
  </CardHeader>
  <CardContent className="space-y-4">
    <form key={`run-seed-${settings?.effective_date ?? 'empty'}`} onSubmit={handleSubmit} className="space-y-4">
      <div className="space-y-1.5">
        <Label htmlFor="threshold_pace">Threshold Pace (sec/km)</Label>
        <div className="flex items-center gap-3">
          <Input
            id="threshold_pace"
            name="threshold_pace"
            type="number"
            min={180}
            max={600}
            defaultValue={settings?.threshold_pace_s_per_km ?? ''}
            placeholder="e.g. 300"
            className="w-32"
          />
          {settings?.threshold_pace_s_per_km ? (
            <span className="text-xs text-[#7d8fa6]">
              = {Math.floor(settings.threshold_pace_s_per_km / 60)}:{String(Math.round(settings.threshold_pace_s_per_km % 60)).padStart(2, '0')}/km
            </span>
          ) : null}
        </div>
        <p className="text-[11px] text-[#3e4e63]">Used for pace-based rTSS on GPS-only runs (no power)</p>
      </div>

      <Separator />

      <div className="space-y-1.5">
        <div className="text-xs font-medium text-[#7d8fa6]">Seed CTL from WKO5</div>
        <p className="text-[11px] text-[#3e4e63]">Enter today's values from WKO5 Season View to prime the EWMA start</p>
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <Label htmlFor="initial_ctl">Initial CTL (Run)</Label>
            <div className="flex items-center gap-2">
              <Input
                id="initial_ctl"
                name="initial_ctl"
                type="number"
                step="0.1"
                min={0}
                max={200}
                defaultValue={settings?.initial_ctl_run ?? ''}
                placeholder="e.g. 55"
                className="w-28"
              />
              <span className="text-xs text-[#3e4e63]">TSS/day</span>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="initial_atl">Initial ATL (Run)</Label>
            <div className="flex items-center gap-2">
              <Input
                id="initial_atl"
                name="initial_atl"
                type="number"
                step="0.1"
                min={0}
                max={200}
                defaultValue={settings?.initial_atl_run ?? ''}
                placeholder="e.g. 35"
                className="w-28"
              />
              <span className="text-xs text-[#3e4e63]">TSS/day</span>
            </div>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <Button type="submit" disabled={update.isPending} size="sm">
          {update.isPending ? 'Saving...' : 'Save Settings'}
        </Button>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={backfill.isPending}
          onClick={() => {
            backfill.mutate(undefined, {
              onSuccess: (data) => {
                setBackfillResult(
                  `Backfill done: ${data.computed_rtss_pace} pace-rTSS, ${data.recomputed_power_tss} power-TSS, ${data.skipped_no_data} skipped`
                )
                setTimeout(() => setBackfillResult(null), 5000)
              },
              onError: () => setBackfillResult('Backfill failed'),
            })
          }}
        >
          {backfill.isPending ? 'Backfilling...' : 'Backfill TSS'}
        </Button>
        {backfillResult && <span className="text-xs text-[#22c55e]">{backfillResult}</span>}
      </div>
    </form>
  </CardContent>
</Card>
```

**GOTCHA**: The `handleSubmit` form handler is also reused here via `type="submit"`. The existing `ConfigPage` form has `key={settings?.effective_date ?? 'empty'}` which causes remounting when settings load. The new section uses a different key to avoid that.

Actually, there's a conflict: the existing form `handleSubmit` is attached to the first form but the new Card above uses a second `<form>` with the same `handleSubmit`. Both forms need to share the same submit handler. 

**Fix**: Merge both forms into ONE form. Move the `<form onSubmit={handleSubmit}>` to wrap both the existing fields AND the new section, with a single "Save" button at the bottom of the new section (and remove the Save button from the original section, or keep both as they're in the same form — only one will submit via the clicked button's form).

Actually the simplest fix: keep the two sections in ONE `<form>` tag. Move the `<form onSubmit={handleSubmit}>` to wrap both Cards' content. Add a single save button in the Run Training Load section. The FTP section can have its own save button too if we duplicate the form, or we can merge them.

Simplest: move the entire form to wrap a `space-y-4` div containing both Card `CardContent`s, and put one save button at the end.

**VALIDATE:**
```bash
cd <repo>/frontend && npx tsc --noEmit && npm run build
```
Then open browser `/config`, verify new section appears, test threshold pace display (5:00/km formatting).

**COMMIT:** `feat: add Run PMC Seed section to ConfigPage`

---

## Testing Strategy

### Unit Tests (Python)

| Test | Input | Expected Output | Edge Case? |
|---|---|---|---|
| `test_migrate_schema_adds_new_columns` | in-memory SQLite | 3 cols present | No |
| `test_migrate_schema_idempotent` | run twice | no error | Yes |
| `test_run_pmc_seeded_initial_ctl` | series + initial_ctl=50 | day-1 CTL > 0 | No |
| `test_run_pmc_seed_zero_is_backwards_compatible` | series + seed=0 | same as old | Yes |
| `test_pace_rtss_basic` | pace = threshold | rTSS = duration_h * 100 | No |
| `test_pace_rtss_faster_than_threshold` | pace > threshold | rTSS uses IF^2 | No |
| `test_pace_rtss_zero_guard` | None or 0 inputs | return 0.0 | Yes |
| `test_extract_wko4_metrics_returns_reasonable_values` | real .wko4 file | 100 < dist < 100000 | Exploratory |
| `test_extract_wko4_metrics_missing_file_returns_none` | nonexistent path | None, None | Yes |

### Edge Cases Checklist
- [x] Backfill is idempotent (uses `INSERT OR IGNORE` via existence check)
- [x] `initial_ctl_run = NULL` → `compute_run_pmc()` defaults to 0.0 (backwards compat)
- [x] Calendar presets handle month-boundary correctly (Feb 28/29: `new Date(year, month, 0)` returns last day of prev month — JS built-in)
- [x] WKO4 extractor returns None on any parse error (no crash)
- [x] DateRangePicker active highlight clears when manual date input used

---

## Validation Commands

### Static Analysis
```bash
cd <repo>/frontend && npx tsc --noEmit
```
EXPECT: Zero type errors

### Python Tests
```bash
cd <repo> && python -m pytest backend/tests/ -v
```
EXPECT: All tests pass (12 unit + 2 migration + 2 wko4 = 16 total)

### Build
```bash
cd <repo>/frontend && npm run build
```
EXPECT: Build succeeds, dist/ updated

### Deploy
```bash
cd <repo> && ./deploy.sh
```
EXPECT: Docker Compose rebuilds and starts

### Manual Validation
- [ ] Open Config page `/config` → new "Run Training Load Settings" section visible
- [ ] Enter Threshold Pace = 300 → shows "= 5:00/km" label
- [ ] Enter Initial CTL = 55, ATL = 35 → Save → success toast
- [ ] Open Season page → Run PMC chart CTL starts at ~55 (not near 0)
- [ ] Click [Backfill TSS] → toast shows counts (expected: 0 power-recomputed, >0 or 0 pace-rTSS depending on WKO4 extraction)
- [ ] DateRangePicker: click 本月 → from = first of current month, to = today
- [ ] DateRangePicker: click 去年 → from = 2025-01-01, to = 2025-12-31
- [ ] DateRangePicker: active preset highlighted, manual date clears highlight

---

## Acceptance Criteria

- [ ] All 12 tasks completed
- [ ] `npx tsc --noEmit` passes with zero errors
- [ ] `python -m pytest backend/tests/ -v` all pass
- [ ] `npm run build` succeeds
- [ ] Config page shows Run PMC Seed section
- [ ] After saving CTL=55, run-load API returns `seeded: true, initial_ctl_used: 55.0`
- [ ] Run PMC chart CTL starts from seed value (visible on Season page)
- [ ] DateRangePicker shows two rows: Rolling and Calendar
- [ ] 本月/上月/近3月/近6月/今年YTD/去年 presets produce correct ISO date ranges
- [ ] Active preset highlights correctly; clears on manual input
- [ ] Backfill endpoint returns JSON with 4 count fields

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| WKO4 record structure is not 2+8 bytes (extraction returns None) | M | M | Task 8 has fallback to float32; test validates plausibility not exact values; backfill still works for FIT files |
| WorkoutMetric UniqueConstraint violation on double tss insert | M | H | Task 6 GOTCHA: check existence before inserting tss key |
| Calendar preset "近3月" uses `month - 3` which can go negative (Jan) | L | L | JS `new Date(year, -2, 1)` correctly wraps to Nov of prev year — built-in behavior |
| Form merge (Task 12) breaks existing FTP save | M | M | Test both FTP and threshold pace save after merging into one form |

## Notes

**Architecture for future HR TSS (not building now)**:
- Add a `sport_tss_type` discriminator per `WorkoutFile` (power / pace / hr)
- Add `hrr_lthr_bpm` to `AthleteSettings` for HR reserve baseline
- `hr_rtss(avg_hr_bpm, lthr, duration_s) → float` function in `metrics.py`
- Separate `compute_sport_pmc(sport="cycling"|"strength"|"other")` using TRIMP-based TSS
- UX: SeasonPage gets a sport selector for the PMC chart; or multiple stacked PMC cards

**Architecture for Garmin (not building now)**:
- `WorkoutFile.source` already supports arbitrary strings ('local', 'coros', 'garmin')  
- `WorkoutFile.file_format = 'fit'` — Garmin FIT files parse with existing `parse_fit()`
- New sync endpoint: `POST /sync/garmin/start` mirroring `backend/api/sync.py` coros pattern
- OAuth: Garmin Connect OAuth2 flow (similar to TP flow already in `auth.py`)
- No schema changes needed for basic Garmin support
