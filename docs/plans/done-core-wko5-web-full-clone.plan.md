# Plan: WKO5 Web Full Clone — Backend + Dashboard + TrainingPeaks Sync

> **For agentic workers:** `/prp-implement` → `implementing-features` skill.
> Steps use checkbox (`- [ ]`) syntax for tracking.

## Summary

全端 Web 應用：FastAPI 後端讀取 `~/WKO5/` 目錄的 `.wko4`/`.fit` 檔案，Python 算法引擎執行所有逆向取得的 WKO5 指標（MMP、NP、TSS、CTL/ATL/TSB、FTP 等），React 前端提供可自訂 Dashboard，TrainingPeaks OAuth2 自動同步 FIT 檔案到現有 WKO5 目錄。

## User Story

As 個人運動員,
I want 在瀏覽器中看到 WKO5 等級的訓練分析 Dashboard,
So that 我可以不用開啟 WKO5 桌面版、直接用網頁查詢訓練狀況。

## Metadata
- **Module**: core
- **Parent Plan**: N/A
- **Source PRD**: `docs/prd/wko5-training-ai.prd.md`
- **Source SRS**: `docs/spec/wko5-web-full-clone.spec.md`
- **Source Linear Issue**: N/A
- **Type**: feature
- **Size**: XL
- **Complexity**: Large
- **Rigor**: balanced
- **Mode**: A — 快建
- **TDD**: off (驗收測試在最後)
- **Commit cadence**: per-phase
- **Estimated Files**: 35+

---

## UX Design

### Before
```
User → Opens WKO5.app → loads .wko4 files → views charts
       (macOS only, subscription required, no natural language)
```

### After
```
User → Opens browser http://localhost:8000
     → Dashboard with PMC / MMP / Workout List widgets
     → Click "Sync TP" → new workouts auto-downloaded
     → Drag widgets to customize layout
     → All 1,011 historical workouts available immediately
```

---

## Mandatory Reading

| Priority | File | Lines | Why |
|----------|------|-------|-----|
| P0 | `src/mmp.py` | 1-190 | Port to backend/engine/algorithms/mmp.py |
| P0 | `src/metrics.py` | 1-143 | Port to backend/engine/algorithms/metrics.py |
| P0 | `src/fit_parser.py` | 1-273 | Port to backend/files/fit_reader.py |
| P1 | `src/storage.py` | 1-148 | Replace with SQLite; use as interface reference |
| P1 | `src/importer.py` | 1-139 | Port pipeline logic to backend/files/file_service.py |
| P2 | `docs/spec/wko5-web-full-clone.spec.md` | all | Authoritative DB schema + API contracts |

## External Documentation

| Topic | Source | Key Takeaway |
|-------|--------|--------------|
| FastAPI | https://fastapi.tiangolo.com | `app = FastAPI(); @app.get("/")` |
| SQLAlchemy 2.0 async | https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html | `AsyncSession`, `async_sessionmaker` |
| Alembic | https://alembic.sqlalchemy.org | `alembic init`, `alembic revision --autogenerate` |
| react-grid-layout | https://github.com/react-grid-layout/react-grid-layout | `<GridLayout layouts={...} onLayoutChange={...}>` |
| React Query v5 | https://tanstack.com/query/latest | `useQuery({ queryKey, queryFn })` |
| WKO4 binary (GoldenCheetah) | https://github.com/GoldenCheetah/GoldenCheetah/blob/master/src/FileIO/WkoRideFile.cpp | VarInt + field tags, magic `wko4` |

---

## Patterns to Mirror

### FASTAPI_ROUTER
```python
# SOURCE: FastAPI convention (no existing file yet — establish this pattern)
from fastapi import APIRouter, Depends, HTTPException
router = APIRouter(prefix="/api/v1/workouts", tags=["workouts"])

@router.get("/{workout_id}")
async def get_workout(workout_id: int, db: AsyncSession = Depends(get_db)):
    ...
    raise HTTPException(status_code=404, detail="WORKOUT_NOT_FOUND")
```

### SQLALCHEMY_ASYNC_MODEL
```python
# SOURCE: SQLAlchemy 2.0 async pattern
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
class Base(DeclarativeBase): pass
class WorkoutFile(Base):
    __tablename__ = "workout_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    file_path: Mapped[str] = mapped_column(unique=True)
```

### ALGORITHM_FUNCTION_SIGNATURE
```python
# SOURCE: src/mmp.py:29-35 (established pattern)
def compute_mmp(
    power: np.ndarray,
    time: np.ndarray,
    durations: Optional[list[int]] = None,
    ignore_gaps: bool = False,
) -> dict[int, float]:
```

### RAWWORKOUT_DATACLASS
```python
# SOURCE: src/fit_parser.py:17-55 (port as-is)
@dataclass
class RawWorkout:
    source_file: str
    sport: str
    start_time: Optional[datetime]
    power_w: np.ndarray
    ...
```

### REACT_QUERY_HOOK
```typescript
// SOURCE: React Query v5 pattern (establish)
export function useWorkoutMmp(workoutId: number) {
  return useQuery({
    queryKey: ['workout', workoutId, 'mmp'],
    queryFn: () => api.get<MmpCurve>(`/api/v1/workouts/${workoutId}/mmp`),
    staleTime: Infinity, // MMP doesn't change after import
  })
}
```

### TEST_PATTERN
```python
# SOURCE: src/tests/test_mmp.py (established pattern)
def test_feature():
    # Arrange
    power = np.full(3600, 200.0)
    time = np.arange(3600, dtype=float)
    # Act
    result = compute_mmp(power, time)
    # Assert
    assert result[300] == pytest.approx(200.0, rel=0.01)
```

---

## Files to Change

### Phase 1 — Backend Foundation

| File | Action | Justification |
|------|--------|---------------|
| `backend/__init__.py` | CREATE | Package root |
| `backend/main.py` | CREATE | FastAPI app entry point |
| `backend/db/database.py` | CREATE | SQLAlchemy async engine + session |
| `backend/db/models.py` | CREATE | All ORM models from SRS schema |
| `backend/db/migrations/` | CREATE | Alembic migrations directory |
| `backend/files/fit_reader.py` | CREATE | Port src/fit_parser.py |
| `backend/files/wko4_reader.py` | CREATE | WKO4 binary metadata reader |
| `backend/files/file_service.py` | CREATE | ~/WKO5 directory scanner + importer |
| `backend/engine/algorithms/mmp.py` | CREATE | Port src/mmp.py |
| `backend/engine/algorithms/metrics.py` | CREATE | Port src/metrics.py + add PMC |
| `backend/engine/algorithms/training_levels.py` | CREATE | 7 zone systems |
| `backend/engine/algorithms/power_model.py` | CREATE | FTP/FRC/Pmax estimation |
| `backend/api/workouts.py` | CREATE | CRUD + metrics endpoints |
| `backend/api/pmc.py` | CREATE | CTL/ATL/TSB time series |
| `backend/api/expr.py` | CREATE | Expression evaluator endpoint |
| `backend/api/dashboard.py` | CREATE | Dashboard config CRUD |
| `backend/api/scan.py` | CREATE | Directory re-scan endpoint |

### Phase 2 — TrainingPeaks Integration

| File | Action | Justification |
|------|--------|---------------|
| `backend/sync/tp_client.py` | CREATE | TP OAuth2 + FIT download |
| `backend/api/sync.py` | CREATE | Sync trigger + SSE progress |
| `backend/api/auth.py` | CREATE | OAuth callback handler |

### Phase 3 — React Frontend

| File | Action | Justification |
|------|--------|---------------|
| `frontend/package.json` | CREATE | Vite + React + TS + deps |
| `frontend/vite.config.ts` | CREATE | Proxy /api → FastAPI |
| `frontend/src/main.tsx` | CREATE | React entry + QueryClient |
| `frontend/src/api/client.ts` | CREATE | axios instance |
| `frontend/src/api/hooks.ts` | CREATE | React Query hooks |
| `frontend/src/store/index.ts` | CREATE | Zustand store |
| `frontend/src/components/widgets/MmpCurveWidget.tsx` | CREATE | MMP chart |
| `frontend/src/components/widgets/PmcWidget.tsx` | CREATE | CTL/ATL/TSB chart |
| `frontend/src/components/widgets/WorkoutListWidget.tsx` | CREATE | Recent workouts table |
| `frontend/src/components/widgets/WorkoutDetailWidget.tsx` | CREATE | Single workout metrics |
| `frontend/src/components/DashboardGrid.tsx` | CREATE | react-grid-layout wrapper |
| `frontend/src/pages/Dashboard.tsx` | CREATE | Main dashboard page |
| `frontend/src/pages/WorkoutList.tsx` | CREATE | Full workout list |
| `requirements.txt` | UPDATE | Add fastapi, uvicorn, sqlalchemy, etc. |

## NOT Building (Phase 1 scope)

- Expression parser (custom `expr` string evaluation) — Phase 5
- iLevels / PKCogganOptimizedPowerLevels (needs Ghidra) — Phase 3
- WKO4 raw channel data (metadata only; FIT files from TP for channels) — deferred
- Workout Builder / Training Plan — out of scope
- Multi-athlete switching UI — foundation only, Athlete hardcoded initially

---

## Step-by-Step Tasks

---

### Task 1: Install Backend Dependencies

- **ACTION**: Update requirements.txt and install
- **IMPLEMENT**:
  Add to `requirements.txt`:
  ```
  fitparse>=1.2.0
  numpy>=1.24.0
  click>=8.1.0
  fastapi>=0.115.0
  uvicorn[standard]>=0.30.0
  sqlalchemy>=2.0.0
  aiosqlite>=0.20.0
  alembic>=1.13.0
  httpx>=0.27.0
  python-multipart>=0.0.9
  sse-starlette>=2.1.0
  pydantic>=2.0.0
  pydantic-settings>=2.0.0
  ```
  Run: `/opt/homebrew/bin/python3.12 -m pip install fastapi uvicorn sqlalchemy aiosqlite alembic httpx python-multipart sse-starlette pydantic pydantic-settings --break-system-packages`
- **VALIDATE**: `/opt/homebrew/bin/python3.12 -c "import fastapi, sqlalchemy, alembic, httpx; print('ok')"`

---

### Task 2: Database Models (SQLAlchemy 2.0 async)

- **ACTION**: Create `backend/db/models.py` with all entities from SRS schema
- **IMPLEMENT**: `backend/db/models.py`

  ```python
  from datetime import datetime, date
  from typing import Optional
  from sqlalchemy import String, Integer, Float, Boolean, DateTime, Date, Text, ForeignKey, UniqueConstraint
  from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

  class Base(DeclarativeBase):
      pass

  class Athlete(Base):
      __tablename__ = "athletes"
      id: Mapped[int] = mapped_column(primary_key=True)
      name: Mapped[str] = mapped_column(String(100), unique=True)
      tp_athlete_id: Mapped[Optional[int]]
      data_dir: Mapped[str] = mapped_column(String(500))  # ~/WKO5/Athlete
      created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
      workouts: Mapped[list["WorkoutFile"]] = relationship(back_populates="athlete")
      settings: Mapped[list["AthleteSettings"]] = relationship(back_populates="athlete")

  class AthleteSettings(Base):
      __tablename__ = "athlete_settings"
      __table_args__ = (UniqueConstraint("athlete_id", "effective_date"),)
      id: Mapped[int] = mapped_column(primary_key=True)
      athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"))
      effective_date: Mapped[date] = mapped_column(Date)
      ftp_w: Mapped[Optional[float]]
      weight_kg: Mapped[Optional[float]]
      lthr: Mapped[Optional[int]]
      athlete: Mapped["Athlete"] = relationship(back_populates="settings")

  class WorkoutFile(Base):
      __tablename__ = "workout_files"
      id: Mapped[int] = mapped_column(primary_key=True)
      athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"))
      file_path: Mapped[str] = mapped_column(String(500), unique=True)
      file_format: Mapped[str] = mapped_column(String(10))  # 'wko4' | 'fit'
      workout_date: Mapped[date] = mapped_column(Date, index=True)
      sport: Mapped[Optional[str]] = mapped_column(String(50))
      duration_s: Mapped[Optional[float]]
      total_distance_m: Mapped[Optional[float]]
      source: Mapped[str] = mapped_column(String(20), default="local")
      tp_workout_id: Mapped[Optional[int]]
      imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
      athlete: Mapped["Athlete"] = relationship(back_populates="workouts")
      metrics: Mapped[list["WorkoutMetric"]] = relationship(back_populates="workout", cascade="all, delete-orphan")
      mmp_cache: Mapped[list["MmpCache"]] = relationship(back_populates="workout", cascade="all, delete-orphan")

  class WorkoutMetric(Base):
      __tablename__ = "workout_metrics"
      __table_args__ = (UniqueConstraint("workout_id", "metric_key"),)
      id: Mapped[int] = mapped_column(primary_key=True)
      workout_id: Mapped[int] = mapped_column(ForeignKey("workout_files.id"))
      metric_key: Mapped[str] = mapped_column(String(50))
      value: Mapped[Optional[float]]
      computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
      workout: Mapped["WorkoutFile"] = relationship(back_populates="metrics")

  class MmpCache(Base):
      __tablename__ = "mmp_cache"
      __table_args__ = (UniqueConstraint("workout_id", "channel", "duration_s"),)
      id: Mapped[int] = mapped_column(primary_key=True)
      workout_id: Mapped[int] = mapped_column(ForeignKey("workout_files.id"))
      channel: Mapped[str] = mapped_column(String(30), default="power")
      duration_s: Mapped[int]
      value: Mapped[Optional[float]]
      workout: Mapped["WorkoutFile"] = relationship(back_populates="mmp_cache")

  class PmcCache(Base):
      __tablename__ = "pmc_cache"
      __table_args__ = (UniqueConstraint("athlete_id", "date"),)
      id: Mapped[int] = mapped_column(primary_key=True)
      athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"))
      date: Mapped[date] = mapped_column(Date, index=True)
      ctl: Mapped[Optional[float]]
      atl: Mapped[Optional[float]]
      tsb: Mapped[Optional[float]]
      ramp_rate: Mapped[Optional[float]]
      tss: Mapped[Optional[float]]

  class SyncState(Base):
      __tablename__ = "sync_state"
      athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"), primary_key=True)
      tp_access_token: Mapped[Optional[str]] = mapped_column(Text)
      tp_refresh_token: Mapped[Optional[str]] = mapped_column(Text)
      tp_token_expires: Mapped[Optional[datetime]] = mapped_column(DateTime)
      last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
      last_sync_cursor: Mapped[Optional[str]] = mapped_column(String(50))

  class DashboardConfig(Base):
      __tablename__ = "dashboard_configs"
      id: Mapped[int] = mapped_column(primary_key=True)
      athlete_id: Mapped[int] = mapped_column(ForeignKey("athletes.id"))
      name: Mapped[str] = mapped_column(String(100))
      is_default: Mapped[bool] = mapped_column(Boolean, default=False)
      layout_json: Mapped[str] = mapped_column(Text)
      created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
      updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
  ```

- **VALIDATE**: `python3.12 -c "from backend.db.models import Athlete, WorkoutFile; print('models ok')"`

---

### Task 3: Database Engine + Session + Alembic Init

- **ACTION**: Create async engine, session factory, and Alembic setup
- **IMPLEMENT**:

  `backend/db/database.py`:
  ```python
  from pathlib import Path
  from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
  from backend.db.models import Base

  DB_PATH = Path.home() / ".wko5coach" / "wko5coach.db"
  DB_PATH.parent.mkdir(exist_ok=True)
  DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

  engine = create_async_engine(DATABASE_URL, echo=False)
  AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

  async def get_db() -> AsyncSession:
      async with AsyncSessionLocal() as session:
          yield session

  async def init_db():
      async with engine.begin() as conn:
          await conn.run_sync(Base.metadata.create_all)
  ```

  Then from project root: `cd <repo> && /opt/homebrew/bin/python3.12 -m alembic init backend/db/migrations`

  Edit `alembic.ini`: `sqlalchemy.url = sqlite:///%(here)s/../../../.wko5coach/wko5coach.db`

  Edit `backend/db/migrations/env.py`: import Base and set `target_metadata = Base.metadata`

- **VALIDATE**: `python3.12 -c "import asyncio; from backend.db.database import init_db; asyncio.run(init_db()); print('db ok')"`

---

### Task 4: Port Algorithm Engine

- **ACTION**: Copy and adapt src/ algorithms into backend/engine/algorithms/
- **IMPLEMENT**:

  **`backend/engine/algorithms/mmp.py`** — exact copy of `src/mmp.py` (same content, update imports to be relative-free):
  - Copy `src/mmp.py` → `backend/engine/algorithms/mmp.py` verbatim (no changes needed, no local imports)

  **`backend/engine/algorithms/metrics.py`** — exact copy of `src/metrics.py` + add PMC functions:
  - Copy `src/metrics.py` → `backend/engine/algorithms/metrics.py`
  - Append PMC functions:

  ```python
  def exponential_smoothing_ctl(tss_series: list[tuple[date, float]], time_constant: float = 42.0) -> list[tuple[date, float]]:
      """
      CTL = tl(tss, ctlconstant) — WKO5 formula from strings extraction.
      EMA with tau=42 days: factor = 1 - exp(-1/tau)
      """
      import math
      from datetime import timedelta
      if not tss_series:
          return []
      factor = 1 - math.exp(-1 / time_constant)
      result = []
      tss_dict = {d: t for d, t in tss_series}
      if not tss_series:
          return []
      start_date = min(d for d, _ in tss_series)
      end_date = max(d for d, _ in tss_series)
      ctl = 0.0
      current = start_date
      while current <= end_date:
          tss_today = tss_dict.get(current, 0.0)
          ctl = ctl + factor * (tss_today - ctl)
          result.append((current, round(ctl, 2)))
          current += timedelta(days=1)
      return result

  def compute_pmc(tss_series: list[tuple[date, float]], ctl_tau: float = 42.0, atl_tau: float = 7.0) -> list[dict]:
      """
      PMC: CTL, ATL, TSB from TSS time series.
      WKO5 formulas:
        CTL = tl(tss, ctlconstant)
        ATL = tl(tss, atlconstant)
        TSB = shift(CTL - ATL, 1)  [yesterday's CTL-ATL]
      """
      import math
      from datetime import timedelta
      if not tss_series:
          return []
      ctl_factor = 1 - math.exp(-1 / ctl_tau)
      atl_factor = 1 - math.exp(-1 / atl_tau)
      tss_dict = {d: t for d, t in tss_series}
      start_date = min(d for d, _ in tss_series)
      end_date = max(d for d, _ in tss_series)
      ctl = atl = 0.0
      prev_ctl = prev_atl = 0.0
      result = []
      current = start_date
      while current <= end_date:
          tss_today = tss_dict.get(current, 0.0)
          prev_ctl, prev_atl = ctl, atl
          ctl = ctl + ctl_factor * (tss_today - ctl)
          atl = atl + atl_factor * (tss_today - atl)
          tsb = round(prev_ctl - prev_atl, 2)  # yesterday's CTL-ATL = shift(CTL-ATL, 1)
          result.append({"date": current.isoformat(), "ctl": round(ctl, 2), "atl": round(atl, 2), "tsb": tsb, "tss": tss_today})
          current += timedelta(days=1)
      return result
  ```

  **`backend/engine/algorithms/training_levels.py`**:
  ```python
  from dataclasses import dataclass
  from typing import Optional

  @dataclass
  class PowerZone:
      number: int
      name: str
      low_pct_ftp: float
      high_pct_ftp: float
      low_w: Optional[float] = None
      high_w: Optional[float] = None

  COGGAN_CLASSIC_ZONES = [
      PowerZone(1, "Active Recovery", 0.0, 0.55),
      PowerZone(2, "Endurance", 0.55, 0.75),
      PowerZone(3, "Tempo", 0.75, 0.90),
      PowerZone(4, "Lactate Threshold", 0.90, 1.05),
      PowerZone(5, "VO2max", 1.05, 1.20),
      PowerZone(6, "Anaerobic Capacity", 1.20, 1.50),
      PowerZone(7, "Neuromuscular Power", 1.50, 9999.0),
  ]

  def coggan_classic_zones(ftp_w: float) -> list[PowerZone]:
      zones = []
      for z in COGGAN_CLASSIC_ZONES:
          z2 = PowerZone(z.number, z.name, z.low_pct_ftp, z.high_pct_ftp,
                         round(z.low_pct_ftp * ftp_w), round(z.high_pct_ftp * ftp_w) if z.high_pct_ftp < 9999 else None)
          zones.append(z2)
      return zones

  def ftp_from_mmp(mmp_curve: dict[int, float]) -> Optional[float]:
      """ftp(meanmax(power)) — peak 60-min average power."""
      # Try exact 3600s
      if 3600 in mmp_curve and mmp_curve[3600] > 0:
          return round(mmp_curve[3600], 1)
      # Fallback: 95% of 20min
      if 1200 in mmp_curve and mmp_curve[1200] > 0:
          return round(mmp_curve[1200] * 0.95, 1)
      return None
  ```

- **VALIDATE**: `/opt/homebrew/bin/python3.12 -c "from backend.engine.algorithms.mmp import compute_mmp; from backend.engine.algorithms.metrics import compute_pmc; print('engine ok')"`

---

### Task 5: File Service — WKO4 Metadata Reader

- **ACTION**: Create WKO4 binary parser (metadata only: date/sport/duration from file header)
- **IMPLEMENT**: `backend/files/wko4_reader.py`

  WKO4 format: magic `wko4` (4 bytes) + VarInt-encoded fields. From hex dump:
  - sport string appears near offset 8 (length-prefixed)
  - ISO date string appears around offset 32
  - For MVP: extract date from filename pattern `{Name}_{YYYY}_{MM}_{DD}_{HH}_{MM}.wko4`

  ```python
  import re
  from dataclasses import dataclass
  from datetime import datetime
  from pathlib import Path
  from typing import Optional

  WKO4_MAGIC = b"wko4"
  _FILENAME_RE = re.compile(r"_(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})\.wko4$")
  _SPORT_MAP = {b"Walking": "walking", b"Cycling": "cycling", b"Running": "running",
                b"Swimming": "swimming", b"Biking": "cycling"}

  @dataclass
  class Wko4Metadata:
      start_time: Optional[datetime]
      sport: str
      source_file: str

  def parse_wko4_metadata(path: str) -> Wko4Metadata:
      """
      Extract metadata from .wko4 file.
      Strategy: parse filename for datetime; scan first 256 bytes for sport string.
      Avoids full binary format reversal — channel data comes from TP FIT files.
      """
      p = Path(path)
      start_time = None
      sport = "unknown"

      # 1. Extract datetime from filename (most reliable)
      m = _FILENAME_RE.search(p.name)
      if m:
          yyyy, mm, dd, hh, mn = (int(x) for x in m.groups())
          try:
              start_time = datetime(yyyy, mm, dd, hh, mn)
          except ValueError:
              pass

      # 2. Scan first 256 bytes for sport keyword
      try:
          with open(path, "rb") as f:
              header = f.read(256)
          if header[:4] != WKO4_MAGIC:
              return Wko4Metadata(start_time, sport, path)
          for keyword, sport_name in _SPORT_MAP.items():
              if keyword in header:
                  sport = sport_name
                  break
          # Also try ISO date string inside file as fallback
          if start_time is None:
              import re as _re
              date_m = _re.search(rb"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})", header)
              if date_m:
                  try:
                      start_time = datetime.fromisoformat(date_m.group(1).decode())
                  except ValueError:
                      pass
      except OSError:
          pass

      return Wko4Metadata(start_time, sport, path)
  ```

- **VALIDATE**: `/opt/homebrew/bin/python3.12 -c "from backend.files.wko4_reader import parse_wko4_metadata; r = parse_wko4_metadata('$HOME/WKO5/Athlete/<year>/Athlete_<YYYY>_<MM>_<DD>_<HH>_<MM>.wko4'); print(r)"`

---

### Task 6: File Service — FIT Reader (Port src/fit_parser.py)

- **ACTION**: Port `src/fit_parser.py` to `backend/files/fit_reader.py`
- **IMPLEMENT**: `backend/files/fit_reader.py`
  - Exact copy of `src/fit_parser.py`
  - Change module path if needed (no local imports — already standalone)
  - Export: `parse_fit(path: str) -> RawWorkout`

- **VALIDATE**: `/opt/homebrew/bin/python3.12 -c "from backend.files.fit_reader import parse_fit; print('fit_reader ok')"`

---

### Task 7: File Service — Directory Scanner

- **ACTION**: Create `backend/files/file_service.py` that scans `~/WKO5/` and registers workout files
- **IMPLEMENT**: `backend/files/file_service.py`

  ```python
  import asyncio
  from pathlib import Path
  from datetime import datetime, timezone
  from typing import AsyncIterator
  from sqlalchemy.ext.asyncio import AsyncSession
  from sqlalchemy import select
  import numpy as np

  from backend.db.models import Athlete, WorkoutFile, WorkoutMetric, MmpCache
  from backend.files.wko4_reader import parse_wko4_metadata
  from backend.files.fit_reader import parse_fit
  from backend.engine.algorithms.mmp import compute_mmp, MMP_DURATIONS
  from backend.engine.algorithms.metrics import compute_all_metrics

  WKO5_ROOT = Path.home() / "WKO5"

  def discover_workout_files(athlete_dir: Path) -> list[Path]:
      """Scan athlete directory for .wko4 and .fit files, sorted by date."""
      files = []
      for year_dir in sorted(athlete_dir.iterdir()):
          if not year_dir.is_dir() or not year_dir.name.isdigit():
              continue
          for f in sorted(year_dir.iterdir()):
              if f.suffix.lower() in (".wko4", ".fit"):
                  files.append(f)
      return files

  async def scan_and_import(db: AsyncSession, athlete_id: int, athlete_dir: str) -> dict:
      """Scan directory and import all new workout files. Returns summary."""
      files = discover_workout_files(Path(athlete_dir))
      new_count = 0
      skip_count = 0
      error_count = 0

      for f in files:
          path_str = str(f)
          # Check if already imported
          existing = await db.execute(select(WorkoutFile).where(WorkoutFile.file_path == path_str))
          if existing.scalar_one_or_none():
              skip_count += 1
              continue
          try:
              wf = await _import_one_file(db, athlete_id, f)
              if wf:
                  new_count += 1
          except Exception as e:
              error_count += 1
          await asyncio.sleep(0)  # yield to event loop

      await db.commit()
      return {"new": new_count, "skipped": skip_count, "errors": error_count, "total": len(files)}

  async def _import_one_file(db: AsyncSession, athlete_id: int, path: Path) -> WorkoutFile:
      """Parse one file, compute metrics, persist to DB."""
      fmt = path.suffix.lower().lstrip(".")

      if fmt == "wko4":
          meta = parse_wko4_metadata(str(path))
          raw = None  # no channel data from wko4 in MVP
          start_time = meta.start_time
          sport = meta.sport
      elif fmt == "fit":
          raw = parse_fit(str(path))
          start_time = raw.start_time
          sport = raw.sport
      else:
          return None

      wf = WorkoutFile(
          athlete_id=athlete_id,
          file_path=str(path),
          file_format=fmt,
          workout_date=start_time.date() if start_time else None,
          sport=sport,
          duration_s=raw.duration_s if raw else None,
          total_distance_m=raw.total_distance_m if raw else None,
          source="local",
      )
      db.add(wf)
      await db.flush()  # get wf.id

      # Compute metrics for FIT files (wko4 has no channel data in MVP)
      if raw and raw.has_power:
          from backend.engine.algorithms.metrics import compute_all_metrics
          from backend.engine.algorithms.mmp import compute_mmp, MMP_DURATIONS
          metrics_dict = compute_all_metrics(raw.power_w, ftp_w=None, duration_s=raw.duration_s,
                                              hr=raw.heart_rate_bpm if raw.has_hr else None,
                                              cadence=raw.cadence_rpm if raw.has_cadence else None)
          for key, val in metrics_dict.items():
              if isinstance(val, float) or isinstance(val, int):
                  db.add(WorkoutMetric(workout_id=wf.id, metric_key=key, value=float(val)))

          mmp = compute_mmp(raw.power_w, raw.time_s)
          for dur, val in mmp.items():
              if val > 0:
                  db.add(MmpCache(workout_id=wf.id, channel="power", duration_s=dur, value=val))

      return wf
  ```

- **VALIDATE**: File service module imports without error.

---

### Task 8: FastAPI Application Entry Point + Core Router

- **ACTION**: Create `backend/main.py` and wire all routers
- **IMPLEMENT**: `backend/main.py`

  ```python
  from contextlib import asynccontextmanager
  from fastapi import FastAPI
  from fastapi.staticfiles import StaticFiles
  from fastapi.middleware.cors import CORSMiddleware
  from pathlib import Path

  from backend.db.database import init_db
  from backend.api import workouts, pmc, expr, dashboard, scan, sync, auth

  @asynccontextmanager
  async def lifespan(app: FastAPI):
      await init_db()
      yield

  app = FastAPI(title="WKO5 Coach", lifespan=lifespan)

  app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"],
                     allow_methods=["*"], allow_headers=["*"])

  app.include_router(workouts.router)
  app.include_router(pmc.router)
  app.include_router(expr.router)
  app.include_router(dashboard.router)
  app.include_router(scan.router)
  app.include_router(sync.router)
  app.include_router(auth.router)

  # Serve React build in production
  frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
  if frontend_dist.exists():
      app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
  ```

  Create `backend/__init__.py` (empty) and `backend/api/__init__.py` (empty).

- **VALIDATE**: `cd <repo> && /opt/homebrew/bin/python3.12 -c "from backend.main import app; print('app ok')"`

---

### Task 9: Workouts API Router

- **ACTION**: Create `backend/api/workouts.py` with list + detail + MMP endpoints
- **IMPLEMENT**: `backend/api/workouts.py`

  ```python
  from fastapi import APIRouter, Depends, HTTPException, Query
  from sqlalchemy.ext.asyncio import AsyncSession
  from sqlalchemy import select, func
  from sqlalchemy.orm import selectinload
  from datetime import date
  from typing import Optional
  from backend.db.database import get_db
  from backend.db.models import WorkoutFile, WorkoutMetric, MmpCache, AthleteSettings
  from backend.engine.algorithms.mmp import compute_mmp
  from backend.files.fit_reader import parse_fit

  router = APIRouter(prefix="/api/v1/workouts", tags=["workouts"])

  @router.get("")
  async def list_workouts(
      athlete_id: int = 1,
      page: int = 1,
      per_page: int = 20,
      sport: Optional[str] = None,
      date_from: Optional[date] = None,
      date_to: Optional[date] = None,
      db: AsyncSession = Depends(get_db),
  ):
      q = select(WorkoutFile).where(WorkoutFile.athlete_id == athlete_id)
      if sport:
          q = q.where(WorkoutFile.sport == sport)
      if date_from:
          q = q.where(WorkoutFile.workout_date >= date_from)
      if date_to:
          q = q.where(WorkoutFile.workout_date <= date_to)

      count_result = await db.execute(select(func.count()).select_from(q.subquery()))
      total = count_result.scalar()

      q = q.order_by(WorkoutFile.workout_date.desc()).offset((page - 1) * per_page).limit(per_page)
      q = q.options(selectinload(WorkoutFile.metrics))
      result = await db.execute(q)
      workouts = result.scalars().all()

      return {
          "total": total, "page": page, "per_page": per_page,
          "items": [_workout_summary(w) for w in workouts]
      }

  @router.get("/{workout_id}")
  async def get_workout(workout_id: int, db: AsyncSession = Depends(get_db)):
      q = select(WorkoutFile).where(WorkoutFile.id == workout_id).options(selectinload(WorkoutFile.metrics))
      result = await db.execute(q)
      w = result.scalar_one_or_none()
      if not w:
          raise HTTPException(404, "WORKOUT_NOT_FOUND")
      return _workout_detail(w)

  @router.get("/{workout_id}/mmp")
  async def get_workout_mmp(workout_id: int, channel: str = "power", db: AsyncSession = Depends(get_db)):
      # Check cache
      q = select(MmpCache).where(MmpCache.workout_id == workout_id, MmpCache.channel == channel)
      result = await db.execute(q)
      cached = result.scalars().all()
      if cached:
          curve = {str(c.duration_s): c.value for c in cached}
          return {"workout_id": workout_id, "channel": channel, "curve": curve, "cached": True}

      # Compute on demand
      w_result = await db.execute(select(WorkoutFile).where(WorkoutFile.id == workout_id))
      w = w_result.scalar_one_or_none()
      if not w:
          raise HTTPException(404, "WORKOUT_NOT_FOUND")
      if w.file_format != "fit":
          raise HTTPException(204, "NO_POWER_DATA")

      try:
          raw = parse_fit(w.file_path)
      except Exception as e:
          raise HTTPException(422, f"FILE_PARSE_ERROR: {e}")

      if not raw.has_power:
          raise HTTPException(204, "NO_POWER_DATA")

      mmp = compute_mmp(raw.power_w, raw.time_s)
      for dur, val in mmp.items():
          if val > 0:
              db.add(MmpCache(workout_id=workout_id, channel=channel, duration_s=dur, value=val))
      await db.commit()

      curve = {str(d): v for d, v in mmp.items() if v > 0}
      return {"workout_id": workout_id, "channel": channel, "curve": curve, "cached": False}

  def _workout_summary(w: WorkoutFile) -> dict:
      metrics = {m.metric_key: m.value for m in w.metrics}
      return {"id": w.id, "date": w.workout_date.isoformat() if w.workout_date else None,
              "sport": w.sport, "duration_s": w.duration_s, "file_format": w.file_format,
              "source": w.source, "metrics": metrics}

  def _workout_detail(w: WorkoutFile) -> dict:
      return {**_workout_summary(w), "file_path": w.file_path}
  ```

- **VALIDATE**: Server starts and `curl http://localhost:8000/api/v1/workouts?athlete_id=1` returns JSON.

---

### Task 10: PMC API Router

- **ACTION**: Create `backend/api/pmc.py` with CTL/ATL/TSB endpoint
- **IMPLEMENT**: `backend/api/pmc.py`

  ```python
  from fastapi import APIRouter, Depends, Query
  from sqlalchemy.ext.asyncio import AsyncSession
  from sqlalchemy import select
  from datetime import date, timedelta
  from typing import Optional

  from backend.db.database import get_db
  from backend.db.models import WorkoutMetric, WorkoutFile, PmcCache
  from backend.engine.algorithms.metrics import compute_pmc

  router = APIRouter(prefix="/api/v1/pmc", tags=["pmc"])

  @router.get("")
  async def get_pmc(
      athlete_id: int = 1,
      date_from: Optional[date] = None,
      date_to: Optional[date] = None,
      db: AsyncSession = Depends(get_db),
  ):
      if date_to is None:
          date_to = date.today()
      if date_from is None:
          date_from = date_to - timedelta(days=365)

      # Get all TSS values for the athlete (need full history for CTL to be accurate)
      q = (select(WorkoutFile.workout_date, WorkoutMetric.value)
           .join(WorkoutMetric, WorkoutMetric.workout_id == WorkoutFile.id)
           .where(WorkoutFile.athlete_id == athlete_id, WorkoutMetric.metric_key == "tss",
                  WorkoutFile.workout_date.isnot(None)))
      result = await db.execute(q)
      tss_rows = result.all()

      tss_by_date: dict[date, float] = {}
      for d, v in tss_rows:
          if v and d:
              tss_by_date[d] = tss_by_date.get(d, 0.0) + v

      tss_series = sorted(tss_by_date.items())
      pmc_data = compute_pmc(tss_series)

      # Filter to requested range
      filtered = [p for p in pmc_data if date_from.isoformat() <= p["date"] <= date_to.isoformat()]
      return {"series": filtered, "athlete_id": athlete_id}
  ```

- **VALIDATE**: `curl "http://localhost:8000/api/v1/pmc?athlete_id=1"` returns `{"series": [...]}`.

---

### Task 11: Scan + Dashboard + Expr API Stubs

- **ACTION**: Create remaining API routers
- **IMPLEMENT**:

  **`backend/api/scan.py`**:
  ```python
  from fastapi import APIRouter, Depends, BackgroundTasks
  from sqlalchemy.ext.asyncio import AsyncSession
  from backend.db.database import get_db
  from backend.db.models import Athlete
  from backend.files.file_service import scan_and_import
  from sqlalchemy import select

  router = APIRouter(prefix="/api/v1/scan", tags=["scan"])

  @router.post("")
  async def trigger_scan(background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
      result = await db.execute(select(Athlete).where(Athlete.id == 1))
      athlete = result.scalar_one_or_none()
      if not athlete:
          return {"error": "No athlete configured. POST /api/v1/athletes first."}
      summary = await scan_and_import(db, athlete.id, athlete.data_dir)
      return summary
  ```

  **`backend/api/expr.py`**:
  ```python
  from fastapi import APIRouter
  from pydantic import BaseModel
  from typing import Optional

  router = APIRouter(prefix="/api/v1/expr", tags=["expr"])

  class ExprRequest(BaseModel):
      expr: str
      athlete_id: int = 1
      date_range_days: Optional[int] = 90

  @router.post("/evaluate")
  async def evaluate_expr(req: ExprRequest):
      # Phase 5: full expression parser — stub returns informative error
      return {"error": "Expression parser not yet implemented", "expr": req.expr,
              "hint": "Use dedicated endpoints: /workouts/{id}/mmp, /pmc"}
  ```

  **`backend/api/dashboard.py`**:
  ```python
  import json
  from fastapi import APIRouter, Depends, HTTPException
  from sqlalchemy.ext.asyncio import AsyncSession
  from sqlalchemy import select
  from pydantic import BaseModel
  from backend.db.database import get_db
  from backend.db.models import DashboardConfig

  router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

  DEFAULT_LAYOUT = {
      "version": 1, "name": "Default",
      "widgets": [
          {"id": "w1", "type": "pmc_chart", "position": {"col": 0, "row": 0, "w": 12, "h": 4},
           "config": {"show_ctl": True, "show_atl": True, "show_tsb": True, "date_range": "ytd"}},
          {"id": "w2", "type": "workout_list", "position": {"col": 0, "row": 4, "w": 12, "h": 3},
           "config": {"last_n": 10}},
      ]
  }

  @router.get("/{config_id}")
  async def get_dashboard(config_id: int, db: AsyncSession = Depends(get_db)):
      result = await db.execute(select(DashboardConfig).where(DashboardConfig.id == config_id))
      cfg = result.scalar_one_or_none()
      if not cfg:
          raise HTTPException(404, "DASHBOARD_NOT_FOUND")
      return {"id": cfg.id, "name": cfg.name, "layout": json.loads(cfg.layout_json)}

  @router.get("/default/{athlete_id}")
  async def get_default_dashboard(athlete_id: int, db: AsyncSession = Depends(get_db)):
      result = await db.execute(select(DashboardConfig).where(
          DashboardConfig.athlete_id == athlete_id, DashboardConfig.is_default == True))
      cfg = result.scalar_one_or_none()
      if not cfg:
          return {"id": None, "name": "Default", "layout": DEFAULT_LAYOUT}
      return {"id": cfg.id, "name": cfg.name, "layout": json.loads(cfg.layout_json)}

  class DashboardSave(BaseModel):
      name: str
      layout: dict
      is_default: bool = False

  @router.put("/{config_id}")
  async def save_dashboard(config_id: int, body: DashboardSave, db: AsyncSession = Depends(get_db)):
      result = await db.execute(select(DashboardConfig).where(DashboardConfig.id == config_id))
      cfg = result.scalar_one_or_none()
      if cfg:
          cfg.name = body.name
          cfg.layout_json = json.dumps(body.layout)
          cfg.is_default = body.is_default
      else:
          cfg = DashboardConfig(id=config_id, athlete_id=1, name=body.name,
                                layout_json=json.dumps(body.layout), is_default=body.is_default)
          db.add(cfg)
      await db.commit()
      return {"id": config_id, "saved": True}
  ```

- **VALIDATE**: All routers import without error.

---

### Task 12: Athlete Bootstrap + Initial Scan

- **ACTION**: Create `backend/api/athletes.py` and bootstrap Athlete athlete on startup
- **IMPLEMENT**: `backend/api/athletes.py`

  ```python
  from pathlib import Path
  from fastapi import APIRouter, Depends
  from sqlalchemy.ext.asyncio import AsyncSession
  from sqlalchemy import select
  from pydantic import BaseModel
  from typing import Optional
  from backend.db.database import get_db
  from backend.db.models import Athlete, AthleteSettings
  from datetime import date

  router = APIRouter(prefix="/api/v1/athletes", tags=["athletes"])

  WKO5_ROOT = Path.home() / "WKO5"

  @router.get("")
  async def list_athletes(db: AsyncSession = Depends(get_db)):
      result = await db.execute(select(Athlete))
      athletes = result.scalars().all()
      return [{"id": a.id, "name": a.name, "data_dir": a.data_dir} for a in athletes]

  @router.post("/bootstrap")
  async def bootstrap_athletes(db: AsyncSession = Depends(get_db)):
      """Auto-detect athlete directories in ~/WKO5/ and create DB records."""
      created = []
      for item in WKO5_ROOT.iterdir():
          if not item.is_dir() or item.name.startswith(".") or item.name in ("Views", "Chart History", "Smart Segments"):
              continue
          # Check it has year subdirs (athlete pattern)
          year_dirs = [d for d in item.iterdir() if d.is_dir() and d.name.isdigit()]
          if not year_dirs:
              continue
          existing = await db.execute(select(Athlete).where(Athlete.name == item.name))
          if existing.scalar_one_or_none():
              continue
          athlete = Athlete(name=item.name, data_dir=str(item))
          db.add(athlete)
          created.append(item.name)
      await db.commit()
      return {"created": created}

  class SettingsUpdate(BaseModel):
      ftp_w: Optional[float] = None
      weight_kg: Optional[float] = None
      effective_date: Optional[date] = None

  @router.put("/{athlete_id}/settings")
  async def update_settings(athlete_id: int, body: SettingsUpdate, db: AsyncSession = Depends(get_db)):
      eff_date = body.effective_date or date.today()
      result = await db.execute(select(AthleteSettings).where(
          AthleteSettings.athlete_id == athlete_id, AthleteSettings.effective_date == eff_date))
      s = result.scalar_one_or_none()
      if s:
          if body.ftp_w is not None: s.ftp_w = body.ftp_w
          if body.weight_kg is not None: s.weight_kg = body.weight_kg
      else:
          s = AthleteSettings(athlete_id=athlete_id, effective_date=eff_date,
                              ftp_w=body.ftp_w, weight_kg=body.weight_kg)
          db.add(s)
      await db.commit()
      return {"saved": True}
  ```

  Add to `backend/main.py` routers: `from backend.api import athletes` + `app.include_router(athletes.router)`

- **VALIDATE**: `POST /api/v1/athletes/bootstrap` detects `Athlete` and creates athlete record.

---

### Task 13: TrainingPeaks OAuth2 Client

- **ACTION**: Create `backend/sync/tp_client.py` with OAuth2 + FIT download
- **IMPLEMENT**: `backend/sync/tp_client.py`

  ```python
  import httpx
  from datetime import datetime, timezone, timedelta
  from pathlib import Path
  from typing import Optional, AsyncIterator
  from sqlalchemy.ext.asyncio import AsyncSession
  from sqlalchemy import select
  from backend.db.models import SyncState, Athlete, WorkoutFile

  TP_OAUTH_URL = "https://oauth.trainingpeaks.com/oauth/token"
  TP_API_BASE = "https://tpapi.trainingpeaks.com/"
  # TP OAuth app credentials — set via env or config
  # Register at https://developer.trainingpeaks.com/
  TP_CLIENT_ID = "wko5coach_local"  # replace with real client_id
  TP_CLIENT_SECRET = ""             # replace with real secret
  TP_REDIRECT_URI = "http://localhost:8000/api/v1/auth/tp/callback"

  async def get_auth_url() -> str:
      return (
          "https://oauth.trainingpeaks.com/OAuth/Authorize"
          f"?response_type=code&client_id={TP_CLIENT_ID}"
          f"&redirect_uri={TP_REDIRECT_URI}"
          f"&scope=ATHLETE:READ WORKOUT:READ"
      )

  async def exchange_code(code: str, db: AsyncSession, athlete_id: int) -> dict:
      async with httpx.AsyncClient() as client:
          resp = await client.post(TP_OAUTH_URL, data={
              "grant_type": "authorization_code",
              "code": code,
              "client_id": TP_CLIENT_ID,
              "client_secret": TP_CLIENT_SECRET,
              "redirect_uri": TP_REDIRECT_URI,
          })
          resp.raise_for_status()
          token = resp.json()

      expires_at = datetime.now(timezone.utc) + timedelta(seconds=token.get("expires_in", 3600))
      result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
      state = result.scalar_one_or_none()
      if not state:
          state = SyncState(athlete_id=athlete_id)
          db.add(state)
      state.tp_access_token = token["access_token"]
      state.tp_refresh_token = token.get("refresh_token")
      state.tp_token_expires = expires_at
      await db.commit()
      return token

  async def _get_valid_token(db: AsyncSession, athlete_id: int) -> Optional[str]:
      result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
      state = result.scalar_one_or_none()
      if not state or not state.tp_access_token:
          return None
      if state.tp_token_expires and datetime.now(timezone.utc) >= state.tp_token_expires:
          # Refresh token
          async with httpx.AsyncClient() as client:
              resp = await client.post(TP_OAUTH_URL, data={
                  "grant_type": "refresh_token",
                  "refresh_token": state.tp_refresh_token,
                  "client_id": TP_CLIENT_ID,
                  "client_secret": TP_CLIENT_SECRET,
              })
              if resp.status_code == 200:
                  token = resp.json()
                  state.tp_access_token = token["access_token"]
                  state.tp_refresh_token = token.get("refresh_token", state.tp_refresh_token)
                  state.tp_token_expires = datetime.now(timezone.utc) + timedelta(seconds=token.get("expires_in", 3600))
                  await db.commit()
              else:
                  return None
      return state.tp_access_token

  async def sync_workouts(db: AsyncSession, athlete_id: int) -> AsyncIterator[dict]:
      """Yield progress dicts as workouts are downloaded."""
      token = await _get_valid_token(db, athlete_id)
      if not token:
          yield {"error": "TP_AUTH_REQUIRED"}
          return

      result = await db.execute(select(Athlete).where(Athlete.id == athlete_id))
      athlete = result.scalar_one_or_none()
      if not athlete or not athlete.tp_athlete_id:
          yield {"error": "No TP athlete ID configured. Set athlete.tp_athlete_id."}
          return

      sync_result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
      state = sync_result.scalar_one_or_none()
      since_date = state.last_sync_cursor or "2015-01-01"

      headers = {"Authorization": f"Bearer {token}"}
      page = 1
      total_downloaded = 0

      async with httpx.AsyncClient(base_url=TP_API_BASE, headers=headers, timeout=30) as client:
          while True:
              resp = await client.get(
                  f"fitness/v2/athletes/{athlete.tp_athlete_id}/workouts/changed"
                  f"?date={since_date}&searchDirection=After&pageSize=20&page={page}"
              )
              if resp.status_code != 200:
                  yield {"error": f"TP_API_ERROR: {resp.status_code}"}
                  return

              data = resp.json()
              workouts_page = data if isinstance(data, list) else data.get("workouts", [])
              if not workouts_page:
                  break

              for wo in workouts_page:
                  wo_id = wo.get("workoutId") or wo.get("id")
                  wo_date = wo.get("workoutDay") or wo.get("startTime", "")[:10]
                  yield {"status": "checking", "workout_date": wo_date, "workout_id": wo_id}

                  # Download FIT file
                  fit_path = await _download_fit(client, athlete, wo_id, wo_date, wo)
                  if fit_path:
                      # Register in DB if not already
                      existing = await db.execute(select(WorkoutFile).where(
                          WorkoutFile.file_path == str(fit_path)))
                      if not existing.scalar_one_or_none():
                          from backend.files.file_service import _import_one_file
                          await _import_one_file(db, athlete_id, fit_path)
                          await db.commit()
                          total_downloaded += 1
                          yield {"status": "downloaded", "workout_date": wo_date, "file": str(fit_path)}

              page += 1
              if len(workouts_page) < 20:
                  break

      if state:
          from datetime import date
          state.last_sync_at = datetime.now(timezone.utc)
          state.last_sync_cursor = date.today().isoformat()
          await db.commit()

      yield {"status": "complete", "total_downloaded": total_downloaded}

  async def _download_fit(client, athlete, workout_id, workout_date: str, workout_meta: dict) -> Optional[Path]:
      """Download FIT file for a workout to ~/WKO5/{athlete}/{year}/"""
      try:
          detail_resp = await client.get(f"fitness/v6/athletes/{athlete.tp_athlete_id}/workouts/{workout_id}/detaildata")
          if detail_resp.status_code != 200:
              return None
          detail = detail_resp.json()
          files = detail.get("files", []) or []
          fit_files = [f for f in files if str(f.get("name", "")).lower().endswith(".fit")]
          if not fit_files:
              return None
          fname = fit_files[0]["name"]

          fit_resp = await client.get(f"fitness/v6/athletes/{athlete.tp_athlete_id}/workouts/{workout_id}/filedata/{fname}")
          if fit_resp.status_code != 200:
              return None

          year = workout_date[:4] if workout_date else "2000"
          save_dir = Path(athlete.data_dir) / year
          save_dir.mkdir(exist_ok=True)
          # Mirror WKO5 filename pattern
          dt_str = workout_date.replace("-", "_") if workout_date else "unknown"
          safe_name = f"{Path(athlete.data_dir).name}_{dt_str}_{workout_id}.fit"
          save_path = save_dir / safe_name
          save_path.write_bytes(fit_resp.content)
          return save_path
      except Exception:
          return None
  ```

- **VALIDATE**: Module imports without error.

---

### Task 14: Sync + Auth API Routers

- **ACTION**: Create `backend/api/sync.py` and `backend/api/auth.py`
- **IMPLEMENT**:

  **`backend/api/auth.py`**:
  ```python
  from fastapi import APIRouter, Depends
  from fastapi.responses import RedirectResponse
  from sqlalchemy.ext.asyncio import AsyncSession
  from backend.db.database import get_db
  from backend.sync.tp_client import get_auth_url, exchange_code

  router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

  @router.get("/tp/login")
  async def tp_login():
      url = await get_auth_url()
      return RedirectResponse(url)

  @router.get("/tp/callback")
  async def tp_callback(code: str, db: AsyncSession = Depends(get_db)):
      await exchange_code(code, db, athlete_id=1)
      return RedirectResponse("/?sync=authenticated")
  ```

  **`backend/api/sync.py`**:
  ```python
  from fastapi import APIRouter, Depends
  from sse_starlette.sse import EventSourceResponse
  import asyncio, json
  from sqlalchemy.ext.asyncio import AsyncSession
  from backend.db.database import get_db
  from backend.sync.tp_client import sync_workouts

  router = APIRouter(prefix="/api/v1/sync", tags=["sync"])

  @router.post("/start")
  async def start_sync(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
      async def generate():
          async for event in sync_workouts(db, athlete_id):
              yield {"event": "sync_progress", "data": json.dumps(event)}
      return EventSourceResponse(generate())

  @router.get("/status")
  async def sync_status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
      from sqlalchemy import select
      from backend.db.models import SyncState
      result = await db.execute(select(SyncState).where(SyncState.athlete_id == athlete_id))
      state = result.scalar_one_or_none()
      if not state:
          return {"authenticated": False}
      return {
          "authenticated": bool(state.tp_access_token),
          "last_sync": state.last_sync_at.isoformat() if state.last_sync_at else None,
          "cursor": state.last_sync_cursor,
      }
  ```

- **VALIDATE**: `curl http://localhost:8000/api/v1/sync/status` returns `{"authenticated": false}`.

---

### Task 15: React Frontend Setup (Vite + TypeScript)

- **ACTION**: Scaffold frontend with Vite, install deps, configure proxy
- **IMPLEMENT**:

  ```bash
  cd <repo>
  # Install Node via brew if needed
  brew install node
  # Create frontend with Vite
  npm create vite@latest frontend -- --template react-ts
  cd frontend
  npm install
  npm install recharts react-grid-layout @types/react-grid-layout
  npm install @tanstack/react-query zustand axios
  npm install lucide-react
  ```

  **`frontend/vite.config.ts`**:
  ```typescript
  import { defineConfig } from 'vite'
  import react from '@vitejs/plugin-react'
  export default defineConfig({
    plugins: [react()],
    server: {
      proxy: {
        '/api': { target: 'http://localhost:8000', changeOrigin: true },
      },
    },
  })
  ```

- **VALIDATE**: `cd frontend && npm run dev` starts on port 5173.

---

### Task 16: API Client + React Query Hooks

- **ACTION**: Create `frontend/src/api/client.ts` and `frontend/src/api/hooks.ts`
- **IMPLEMENT**:

  **`frontend/src/api/client.ts`**:
  ```typescript
  import axios from 'axios'
  export const api = axios.create({ baseURL: '/api/v1' })
  export type MmpCurve = Record<string, number>
  export interface PmcPoint { date: string; ctl: number; atl: number; tsb: number; tss: number }
  export interface WorkoutSummary {
    id: number; date: string; sport: string; duration_s: number
    metrics: Record<string, number>; file_format: string; source: string
  }
  export interface WorkoutList { total: number; page: number; per_page: number; items: WorkoutSummary[] }
  ```

  **`frontend/src/api/hooks.ts`**:
  ```typescript
  import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
  import { api, WorkoutList, PmcPoint, MmpCurve } from './client'

  export function useWorkouts(params?: Record<string, unknown>) {
    return useQuery<WorkoutList>({
      queryKey: ['workouts', params],
      queryFn: () => api.get('/workouts', { params }).then(r => r.data),
    })
  }

  export function useWorkoutMmp(workoutId: number | null) {
    return useQuery<{ curve: MmpCurve }>({
      queryKey: ['mmp', workoutId],
      queryFn: () => api.get(`/workouts/${workoutId}/mmp`).then(r => r.data),
      enabled: !!workoutId,
      staleTime: Infinity,
    })
  }

  export function usePmc(params?: { date_from?: string; date_to?: string }) {
    return useQuery<{ series: PmcPoint[] }>({
      queryKey: ['pmc', params],
      queryFn: () => api.get('/pmc', { params }).then(r => r.data),
    })
  }

  export function useDashboard(athleteId = 1) {
    return useQuery({
      queryKey: ['dashboard', athleteId],
      queryFn: () => api.get(`/dashboard/default/${athleteId}`).then(r => r.data),
    })
  }

  export function useSync() {
    const qc = useQueryClient()
    return useMutation({
      mutationFn: () => fetch('/api/v1/sync/start', { method: 'POST' }).then(r => r.body),
      onSuccess: () => { qc.invalidateQueries({ queryKey: ['workouts'] }); qc.invalidateQueries({ queryKey: ['pmc'] }) },
    })
  }

  export function useScan() {
    const qc = useQueryClient()
    return useMutation({
      mutationFn: () => api.post('/scan'),
      onSuccess: () => qc.invalidateQueries({ queryKey: ['workouts'] }),
    })
  }
  ```

- **VALIDATE**: `npm run build` completes without TypeScript errors.

---

### Task 17: PMC Widget Component

- **ACTION**: Create `frontend/src/components/widgets/PmcWidget.tsx`
- **IMPLEMENT**: `frontend/src/components/widgets/PmcWidget.tsx`

  ```typescript
  import { usePmc } from '../../api/hooks'
  import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, ReferenceLine } from 'recharts'

  export function PmcWidget({ dateFrom, dateTo }: { dateFrom?: string; dateTo?: string }) {
    const { data, isLoading } = usePmc({ date_from: dateFrom, date_to: dateTo })
    if (isLoading) return <div className="flex items-center justify-center h-full text-gray-400">Loading PMC...</div>
    const series = data?.series ?? []
    return (
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={series} margin={{ top: 5, right: 10, bottom: 5, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#333" />
          <XAxis dataKey="date" tick={{ fontSize: 10 }} tickFormatter={v => v.slice(5)} />
          <YAxis />
          <Tooltip labelFormatter={l => `Date: ${l}`} />
          <Legend />
          <ReferenceLine y={0} stroke="#666" />
          <Line type="monotone" dataKey="ctl" stroke="#4ade80" dot={false} name="CTL (Fitness)" strokeWidth={2} />
          <Line type="monotone" dataKey="atl" stroke="#f97316" dot={false} name="ATL (Fatigue)" strokeWidth={2} />
          <Line type="monotone" dataKey="tsb" stroke="#60a5fa" dot={false} name="TSB (Form)" strokeWidth={1.5} strokeDasharray="4 2" />
        </LineChart>
      </ResponsiveContainer>
    )
  }
  ```

- **VALIDATE**: PmcWidget renders with data from /api/v1/pmc.

---

### Task 18: MMP Curve Widget

- **ACTION**: Create `frontend/src/components/widgets/MmpCurveWidget.tsx`
- **IMPLEMENT**: `frontend/src/components/widgets/MmpCurveWidget.tsx`

  ```typescript
  import { useWorkoutMmp } from '../../api/hooks'
  import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'

  function formatDuration(s: number): string {
    if (s < 60) return `${s}s`
    if (s < 3600) return `${Math.floor(s / 60)}m`
    return `${Math.floor(s / 3600)}h`
  }

  export function MmpCurveWidget({ workoutId }: { workoutId: number | null }) {
    const { data, isLoading } = useWorkoutMmp(workoutId)
    if (!workoutId) return <div className="flex items-center justify-center h-full text-gray-400">Select a workout</div>
    if (isLoading) return <div className="flex items-center justify-center h-full text-gray-400">Computing MMP...</div>

    const curve = data?.curve ?? {}
    const points = Object.entries(curve)
      .map(([d, v]) => ({ duration: parseInt(d), power: v, label: formatDuration(parseInt(d)) }))
      .sort((a, b) => a.duration - b.duration)

    return (
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points} margin={{ top: 5, right: 10, bottom: 20, left: 40 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#333" />
          <XAxis dataKey="label" tick={{ fontSize: 9 }} angle={-45} textAnchor="end" />
          <YAxis label={{ value: 'Watts', angle: -90, position: 'insideLeft', style: { fontSize: 10 } }} />
          <Tooltip formatter={(v: number) => [`${v.toFixed(0)} W`, 'Power']} labelFormatter={l => `Duration: ${l}`} />
          <Line type="monotone" dataKey="power" stroke="#a78bfa" dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    )
  }
  ```

- **VALIDATE**: MmpCurveWidget renders a log-scale-ish power curve.

---

### Task 19: Workout List Widget

- **ACTION**: Create `frontend/src/components/widgets/WorkoutListWidget.tsx`
- **IMPLEMENT**: `frontend/src/components/widgets/WorkoutListWidget.tsx`

  ```typescript
  import { useWorkouts } from '../../api/hooks'
  import { WorkoutSummary } from '../../api/client'

  function fmtDuration(s: number): string {
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
    return h ? `${h}:${String(m).padStart(2, '0')}h` : `${m}min`
  }

  interface Props { lastN?: number; onSelect?: (id: number) => void }

  export function WorkoutListWidget({ lastN = 10, onSelect }: Props) {
    const { data, isLoading } = useWorkouts({ per_page: lastN })
    if (isLoading) return <div className="text-gray-400 text-sm">Loading...</div>
    const workouts = data?.items ?? []
    return (
      <div className="overflow-auto h-full">
        <table className="w-full text-sm text-left">
          <thead className="text-gray-400 border-b border-gray-700">
            <tr><th className="pb-1 pr-3">Date</th><th className="pr-3">Sport</th><th className="pr-3">Duration</th><th className="pr-3">NP</th><th>TSS</th></tr>
          </thead>
          <tbody>
            {workouts.map((w: WorkoutSummary) => (
              <tr key={w.id} className="border-b border-gray-800 hover:bg-gray-800 cursor-pointer"
                  onClick={() => onSelect?.(w.id)}>
                <td className="py-1 pr-3">{w.date}</td>
                <td className="pr-3 capitalize">{w.sport}</td>
                <td className="pr-3">{w.duration_s ? fmtDuration(w.duration_s) : '—'}</td>
                <td className="pr-3">{w.metrics.normalized_power_w ? `${w.metrics.normalized_power_w.toFixed(0)}W` : '—'}</td>
                <td>{w.metrics.tss ? w.metrics.tss.toFixed(1) : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }
  ```

- **VALIDATE**: Table renders with real workout data.

---

### Task 20: Dashboard Grid + Main Page

- **ACTION**: Create `DashboardGrid.tsx` + `Dashboard.tsx` + wire up app entry
- **IMPLEMENT**:

  **`frontend/src/components/DashboardGrid.tsx`**:
  ```typescript
  import { useState } from 'react'
  import GridLayout from 'react-grid-layout'
  import 'react-grid-layout/css/styles.css'
  import 'react-resizable/css/styles.css'
  import { PmcWidget } from './widgets/PmcWidget'
  import { MmpCurveWidget } from './widgets/MmpCurveWidget'
  import { WorkoutListWidget } from './widgets/WorkoutListWidget'

  interface WidgetConfig { id: string; type: string; position: { col: number; row: number; w: number; h: number }; config: Record<string, unknown> }

  function renderWidget(w: WidgetConfig, selectedWorkout: number | null, onSelect: (id: number) => void) {
    switch (w.type) {
      case 'pmc_chart': return <PmcWidget />
      case 'mmp_curve': return <MmpCurveWidget workoutId={selectedWorkout} />
      case 'workout_list': return <WorkoutListWidget lastN={w.config.last_n as number ?? 10} onSelect={onSelect} />
      default: return <div className="text-gray-500 text-sm p-2">Unknown widget: {w.type}</div>
    }
  }

  export function DashboardGrid({ widgets }: { widgets: WidgetConfig[] }) {
    const [selectedWorkout, setSelectedWorkout] = useState<number | null>(null)
    const layout = widgets.map(w => ({ i: w.id, x: w.position.col, y: w.position.row, w: w.position.w, h: w.position.h, minW: 3, minH: 2 }))
    return (
      <GridLayout className="layout" layout={layout} cols={12} rowHeight={80} width={1200} draggableHandle=".drag-handle">
        {widgets.map(w => (
          <div key={w.id} className="bg-gray-900 border border-gray-700 rounded-lg overflow-hidden flex flex-col">
            <div className="drag-handle h-6 bg-gray-800 flex items-center px-2 cursor-grab">
              <span className="text-xs text-gray-400">{w.type.replace('_', ' ')}</span>
            </div>
            <div className="flex-1 p-2">{renderWidget(w, selectedWorkout, setSelectedWorkout)}</div>
          </div>
        ))}
      </GridLayout>
    )
  }
  ```

  **`frontend/src/pages/Dashboard.tsx`**:
  ```typescript
  import { useDashboard, useScan, useSync } from '../api/hooks'
  import { DashboardGrid } from '../components/DashboardGrid'

  export function Dashboard() {
    const { data: dashboard, isLoading } = useDashboard()
    const scan = useScan()
    const sync = useSync()

    if (isLoading) return <div className="flex items-center justify-center h-screen bg-gray-950 text-gray-400">Loading...</div>

    return (
      <div className="min-h-screen bg-gray-950 text-gray-100">
        <header className="bg-gray-900 border-b border-gray-800 px-6 py-3 flex items-center justify-between">
          <h1 className="text-lg font-semibold text-purple-400">WKO5 Coach</h1>
          <div className="flex gap-3">
            <button onClick={() => scan.mutate()} disabled={scan.isPending}
              className="px-3 py-1 text-sm bg-gray-700 hover:bg-gray-600 rounded transition">
              {scan.isPending ? 'Scanning...' : '⟳ Scan Files'}
            </button>
            <a href="/api/v1/auth/tp/login"
              className="px-3 py-1 text-sm bg-blue-700 hover:bg-blue-600 rounded transition">
              Sync TrainingPeaks
            </a>
          </div>
        </header>
        <main className="p-4">
          {dashboard?.layout?.widgets && <DashboardGrid widgets={dashboard.layout.widgets} />}
        </main>
      </div>
    )
  }
  ```

  **`frontend/src/main.tsx`**:
  ```typescript
  import React from 'react'
  import ReactDOM from 'react-dom/client'
  import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
  import { Dashboard } from './pages/Dashboard'
  import './index.css'

  const queryClient = new QueryClient()
  ReactDOM.createRoot(document.getElementById('root')!).render(
    <React.StrictMode>
      <QueryClientProvider client={queryClient}>
        <Dashboard />
      </QueryClientProvider>
    </React.StrictMode>
  )
  ```

  **`frontend/src/index.css`** — add dark background:
  ```css
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #030712; color: #f9fafb; font-family: system-ui, sans-serif; }
  ```

- **VALIDATE**: `npm run dev` shows Dashboard with PMC chart, workout list, MMP placeholder.

---

### Task 21: Wire Backend __init__ files + create backend/api/__init__ + backend/sync/__init__

- **ACTION**: Create all missing `__init__.py` files for proper Python package structure
- **IMPLEMENT**:
  ```
  backend/__init__.py           (empty)
  backend/api/__init__.py       (empty)
  backend/db/__init__.py        (empty)
  backend/engine/__init__.py    (empty)
  backend/engine/algorithms/__init__.py  (empty)
  backend/files/__init__.py     (empty)
  backend/sync/__init__.py      (empty)
  ```
- **VALIDATE**: `python3.12 -c "from backend.main import app; print('all imports ok')"` passes.

---

### Task 22: Integration — Bootstrap + First Scan + Start Servers

- **ACTION**: Run first-time setup and verify end-to-end flow
- **IMPLEMENT**:

  ```bash
  cd <repo>

  # 1. Install new deps
  /opt/homebrew/bin/python3.12 -m pip install fastapi uvicorn sqlalchemy aiosqlite alembic httpx python-multipart sse-starlette pydantic pydantic-settings --break-system-packages

  # 2. Start backend
  /opt/homebrew/bin/python3.12 -m uvicorn backend.main:app --reload --port 8000 &

  # 3. Bootstrap athletes
  curl -X POST http://localhost:8000/api/v1/athletes/bootstrap

  # 4. Scan WKO5 directory (imports .wko4 metadata + computes FIT metrics)
  curl -X POST http://localhost:8000/api/v1/scan

  # 5. Verify workout count
  curl "http://localhost:8000/api/v1/workouts?per_page=5"

  # 6. Start frontend
  cd frontend && npm run dev
  ```

  Then open http://localhost:5173 in browser.

- **VALIDATE**:
  - Backend: `/api/v1/workouts` returns `{"total": N}` where N > 0
  - Frontend: Dashboard loads at http://localhost:5173
  - PMC widget: shows CTL/ATL/TSB chart for FIT-format workouts with power data

---

### Task 23: Add Startup Script

- **ACTION**: Create `start.sh` for easy launch
- **IMPLEMENT**: `start.sh`

  ```bash
  #!/bin/bash
  set -e
  ROOT="$(cd "$(dirname "$0")"; pwd)"

  # Backend
  cd "$ROOT"
  /opt/homebrew/bin/python3.12 -m uvicorn backend.main:app --reload --port 8000 &
  BACKEND_PID=$!
  echo "Backend started (PID $BACKEND_PID) at http://localhost:8000"

  # Frontend
  cd "$ROOT/frontend"
  npm run dev &
  FRONTEND_PID=$!
  echo "Frontend started (PID $FRONTEND_PID) at http://localhost:5173"

  echo ""
  echo "WKO5 Coach running:"
  echo "  Dashboard: http://localhost:5173"
  echo "  API docs:  http://localhost:8000/docs"
  echo ""
  echo "Press Ctrl+C to stop both servers"

  trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" INT TERM
  wait
  ```

  `chmod +x start.sh`

- **VALIDATE**: `./start.sh` starts both servers.

---

## Testing Strategy

### Unit Tests (run with existing framework)

| Test | Input | Expected | Edge Case? |
|------|-------|----------|------------|
| MMP port | src tests | same results | Already passing (14/14) |
| NP port | constant power=200W 3600s | NP=200W | No |
| PMC compute_pmc | TSS=[100,50,0,0,80] | CTL increases, ATL faster | Yes: zero TSS days |
| ftp_from_mmp | MMP[3600]=240 | 240W | Yes: missing 3600s key |
| wko4_metadata | a sample `Athlete_<YYYY>_<MM>_<DD>_<HH>_<MM>.wko4` | date parsed from the filename, sport from the header | No |

### Edge Cases Checklist
- [ ] FIT file with no power channel → warning, not error; imports as metadata-only
- [ ] .wko4 file with unknown sport string → default to "unknown"
- [ ] PMC with zero TSS days → CTL/ATL decay correctly
- [ ] TP token expired → auto-refresh before API call
- [ ] Scan re-run → skips existing files (idempotent)
- [ ] React Query stale cache → invalidated after scan/sync

---

## Validation Commands

### Backend
```bash
cd <repo>

# Unit tests (existing + new)
/opt/homebrew/bin/python3.12 -m pytest src/tests/ -v

# New algorithm tests
/opt/homebrew/bin/python3.12 -m pytest backend/tests/ -v 2>/dev/null || echo "no backend tests yet"

# Backend start
/opt/homebrew/bin/python3.12 -m uvicorn backend.main:app --port 8000 &
sleep 2

# Bootstrap + scan
curl -s -X POST http://localhost:8000/api/v1/athletes/bootstrap | python3.12 -m json.tool
curl -s -X POST http://localhost:8000/api/v1/scan | python3.12 -m json.tool
curl -s "http://localhost:8000/api/v1/workouts?per_page=3" | python3.12 -m json.tool
curl -s "http://localhost:8000/api/v1/pmc" | python3.12 -m json.tool
```
EXPECT: workouts total > 0; pmc series non-empty

### Frontend
```bash
cd <repo>/frontend
npm run build
```
EXPECT: Zero TypeScript errors, build succeeds

### End-to-End
```bash
open http://localhost:5173
```
EXPECT:
- [ ] Dashboard loads with dark theme
- [ ] "⟳ Scan Files" button visible
- [ ] PMC chart renders CTL/ATL/TSB for athlete with FIT files
- [ ] Workout list shows recent workouts
- [ ] Clicking workout selects it (future: MMP chart updates)

---

## Acceptance Criteria
- [ ] `POST /api/v1/athletes/bootstrap` detects Athlete from ~/WKO5
- [ ] `POST /api/v1/scan` imports all 1,011+ workout files (wko4 metadata + fit data)
- [ ] `GET /api/v1/workouts` returns paginated list with TSS/NP for FIT-format workouts
- [ ] `GET /api/v1/workouts/{id}/mmp` returns MMP curve for FIT workouts with power
- [ ] `GET /api/v1/pmc` returns CTL/ATL/TSB series from earliest FIT workout to today
- [ ] React Dashboard loads at http://localhost:5173
- [ ] PMC chart renders CTL/ATL/TSB correctly
- [ ] Workout list table renders with date/sport/NP/TSS columns
- [ ] `./start.sh` starts both backend and frontend in one command
- [ ] Unit tests: 14/14 pass (existing MMP + metrics tests)

## Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| .wko4 files have no FIT equivalent on TP | M | M | Metadata-only import for wko4; power metrics only for FIT |
| TP OAuth requires registered app credentials | H | H | Use personal TP OAuth app; register at developer.trainingpeaks.com |
| 1,011 workouts scan takes >60s | M | L | Background task + progress via SSE; UI shows live count |
| react-grid-layout CSS conflicts | L | L | Import CSS explicitly in DashboardGrid.tsx |

## Notes

- **TP OAuth credentials**: Must register at https://developer.trainingpeaks.com/ to get `client_id` + `client_secret`. Set in `backend/sync/tp_client.py` before using TP sync. Without credentials, local file scan still works fully.
- **FTP for TSS**: TSS requires FTP. Bootstrap sets `ftp_w=null`; set via `PUT /api/v1/athletes/1/settings` with `{"ftp_w": 250}`.
- **WKO4 channel data**: All 1,011 existing .wko4 files will be imported as metadata-only. Power/HR/cadence metrics only available after TP sync downloads FIT files.
- **Phase 2 (future)**: Full expression parser, iLevels, phenotype, VO2max estimate — these require separate plan after Phase 1 validates.
