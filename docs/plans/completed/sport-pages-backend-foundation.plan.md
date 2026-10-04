---
linear_issue: null
---
# Plan: sport-pages 後端基礎（分類持久化、sport 篩選參數化、越野負荷與公式驗證）

> **For agentic workers:** `/prp-implement` 依 `Metadata.Type` 路由。Mode B（任務先測）：每個 task 先寫測試 → 實作 → 跑通 → commit。

## Summary

為 `sport-pages` 模組建立後端資料與 API 基礎：在 `WorkoutFile` 新增持久化的 `trail_classification`（解析時計算、可手動覆寫）、把 `analytics` 端點的寫死 `sport=="running"` 改為 `sports[]` 參數化、新增越野跑專屬端點（trail-load 以 hrTSS 為主、trail-summary、sports/facets、chart-interpretation 規則解讀、classification PATCH），並建立離線公式驗證器對照 WKO5 逆向素材。前端三頁面與圖表由 `sport-pages-frontend-pages.plan.md` 處理。

## User Story
As 以功率/負荷管理多運動的越野跑者，
I want 後端能正確區分 trail/road、依運動別篩選、並提供越野跑爬升負荷與經驗證的公式，
So that 三個頁面能拿到數字可信、運動別獨立的資料。

## Problem → Solution
目前 trail 被正規化抹成 running、analytics 寫死只看 running、無越野負荷與驗證 → trail 持久化分類 + sports[] 參數化 + trail-load(hrTSS) + 公式驗證器。

## Metadata
- **Module**: sport-pages
- **Parent Plan**: N/A
- **Source PRD**: docs/prd/wko5-trail-multipage-sync-coach.prd.md
- **Source Feature SRS**: docs/srs/sport-pages-multi-sport-views-trail-analytics.srs.md
- **Source Module Spec**: docs/srs/completed/sport-pages.srs.md
- **Source Linear Issue**: N/A
- **Type**: feature
- **Size**: L
- **Complexity**: Large
- **Rigor**: strict
- **Mode**: B — 任務先測
- **TDD**: on
- **Commit cadence**: per-task
- **Estimated Files**: ~12

---

## UX Design
N/A — 後端/內部變更，UX 由前端計畫承載。新增的 PATCH/GET 端點供前端消費。

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `docs/srs/sport-pages-multi-sport-views-trail-analytics.srs.md` | all | 功能 delta、AC、端點合約 |
| P0 | `docs/srs/completed/sport-pages.srs.md` | all | 架構上下文、決策、schema |
| P0 | `backend/db/database.py` | 19-53 | `_migrate_schema()` idempotent ALTER 模式 |
| P0 | `backend/db/models.py` | 41-94 | WorkoutFile / WorkoutMetric / PmcCache 結構 |
| P0 | `backend/api/analytics.py` | 140-310 | run-load / intensity-load / run-volume 寫死 sport 篩選的起點 |
| P0 | `backend/files/fit_reader.py` | 294-320 | `_normalize_sport()` 與 RawWorkout 組裝，分類步驟接點 |
| P1 | `backend/engine/algorithms/trail.py` | 1-100 | GAP/grade/segment_climbs 既有越野計算 |
| P1 | `backend/engine/algorithms/metrics.py` | 1-260 | compute_run_pmc / pace_rtss / TSS 既有公式（驗證對照與 hrTSS 落點） |
| P1 | `backend/tests/test_run_pmc.py` | all | 測試風格（sys.path 注入、pytest.approx、純函式測試） |
| P1 | `backend/tests/test_migration.py` | all | migration 與 ORM 欄位測試風格 |
| P1 | `backend/main.py` | 10-43 | router 註冊 |
| P2 | `docs/srs/completed/wko5-training-load-charts.srs.md` | all | WKO5 逆向公式常數（驗證器對照來源） |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| hrTSS / TRIMP | TrainingPeaks hrTSS、研究報告（PRD Research Summary） | 技術地形 rTSS 低估，hrTSS 以 LTHR 相對強度積分；缺 HR 時不可算 |
| NGP / rTSS | 既有 `pace_rtss()` | 越野頁 rTSS 並陳，沿用既有函式 |

---

## Patterns to Mirror

### MIGRATION_PATTERN
```python
# SOURCE: backend/db/database.py:19-47
async def _migrate_schema():
    new_cols = [
        ("workout_files", "elevation_gain_m", "REAL"),
        # ...新增於此 list：
        ("workout_files", "trail_classification", "TEXT"),
        ("workout_files", "classification_overridden", "INTEGER"),
    ]
    async with engine.begin() as conn:
        for table, col, col_type in new_cols:
            result = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing = {row[1] for row in result.fetchall()}
            if col not in existing:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
```

### SPORT_FILTER_PATTERN (寫死 → 參數化)
```python
# SOURCE: backend/api/analytics.py:162-171 (現況，寫死)
q = (
    select(WorkoutFile.workout_date, WorkoutMetric.value)
    .join(WorkoutMetric, WorkoutMetric.workout_id == WorkoutFile.id)
    .where(
        WorkoutFile.athlete_id == athlete_id,
        WorkoutMetric.metric_key == "tss",
        WorkoutFile.sport == "running",          # ← 改為 .in_(sports) / trail 條件
        WorkoutFile.workout_date.isnot(None),
    )
)
```

### ROUTER_PATTERN
```python
# SOURCE: backend/api/analytics.py:11,140-146
router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

@router.get("/run-load")
async def run_load(athlete_id: int = 1, date_from: Optional[date] = None,
                   date_to: Optional[date] = None, db: AsyncSession = Depends(get_db)):
    ...
```

### TSB_STATE_RULE (規則解讀，後端複用)
```python
# SOURCE: backend/api/analytics.py:30-41
if tsb > 5:    tsb_state = "fresh"
elif tsb > -10: tsb_state = "optimal"
elif tsb > -25: tsb_state = "tired"
else:           tsb_state = "overreached"
```

### NORMALIZE_SPORT (分類步驟接點)
```python
# SOURCE: backend/files/fit_reader.py:294-304
def _normalize_sport(sport: str) -> str:
    sport_lower = sport.lower()
    if any(x in sport_lower for x in ("cycl","bike","virt")): return "cycling"
    if any(x in sport_lower for x in ("run","trail")):        return "running"
    ...
```

### TEST_STRUCTURE
```python
# SOURCE: backend/tests/test_run_pmc.py:1-15
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import pytest
from backend.engine.algorithms.metrics import compute_run_pmc

def test_run_pmc_zero_input():
    assert compute_run_pmc([]) == []
```

### ORM_MIGRATION_TEST
```python
# SOURCE: backend/tests/test_migration.py — in-memory engine + PRAGMA table_info + idempotency
async def _inner():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    db_mod.engine = engine
    await db_mod._migrate_schema()
    await db_mod._migrate_schema()  # idempotent
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `backend/db/models.py` | UPDATE | WorkoutFile 加 trail_classification, classification_overridden |
| `backend/db/database.py` | UPDATE | _migrate_schema 加兩欄 |
| `backend/engine/algorithms/classify.py` | CREATE | trail/road 分類純函式（依爬升率/坡度） |
| `backend/files/fit_reader.py` | UPDATE | 解析時呼叫分類，寫入 RawWorkout/匯入流程 |
| `backend/engine/algorithms/metrics.py` | UPDATE | 新增 compute_hr_tss() |
| `backend/api/analytics.py` | UPDATE | sports[] 參數化；trail-load / trail-summary / chart-interpretation 端點 |
| `backend/api/sports.py` | CREATE | GET /sports/facets |
| `backend/api/workouts.py` | UPDATE | PATCH /workouts/{id}/classification |
| `backend/main.py` | UPDATE | 註冊 sports router |
| `backend/engine/algorithms/validator.py` | CREATE | 公式驗證器對照 WKO5 常數 |
| `backend/scripts/backfill_classification.py` | CREATE | 既有 ~1100 筆回填（idempotent，跳過 overridden） |
| `backend/tests/test_*.py` | CREATE | 各 task 對應測試 |

## NOT Building

- 前端三頁面、nav、多選 checkbox、越野圖表元件、可讀性 UI（→ frontend plan）
- AI 即時生成圖表解讀（本計畫只做規則解讀端點）
- climb_load 自訂負荷公式（標記原創、排除驗證集；本計畫不實作為主負荷）
- 資料同步頁面、AI 教練處方（M3/M4 各自計畫）

---

## Step-by-Step Tasks

### Task 1: 新增 trail_classification 欄位（model + migration）
- **ACTION**: 在 `WorkoutFile` 加 `trail_classification`（預設 'unknown'）與 `classification_overridden`（預設 False）；在 `_migrate_schema()` 加對應 ALTER。
- **TEST FIRST**: `backend/tests/test_classification_migration.py`
  ```python
  import sys, os, asyncio
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
  from sqlalchemy import text
  from sqlalchemy.ext.asyncio import create_async_engine
  from datetime import date

  def _run(c): return asyncio.get_event_loop().run_until_complete(c)

  def test_migration_adds_classification_columns():
      async def _inner():
          engine = create_async_engine("sqlite+aiosqlite:///:memory:")
          from backend.db.models import Base
          async with engine.begin() as conn:
              await conn.run_sync(Base.metadata.create_all)
          import backend.db.database as db_mod
          orig = db_mod.engine; db_mod.engine = engine
          try:
              await db_mod._migrate_schema()
              await db_mod._migrate_schema()  # idempotent
              async with engine.begin() as conn:
                  r = await conn.execute(text("PRAGMA table_info(workout_files)"))
                  cols = {row[1] for row in r.fetchall()}
              assert "trail_classification" in cols
              assert "classification_overridden" in cols
          finally:
              db_mod.engine = orig
      _run(_inner())

  def test_orm_has_classification_fields():
      from backend.db.models import WorkoutFile
      w = WorkoutFile(athlete_id=1, file_path="/x.fit", file_format="fit",
                      trail_classification="trail", classification_overridden=True)
      assert w.trail_classification == "trail"
      assert w.classification_overridden is True
  ```
  Run: `cd "backend/.." && python -m pytest backend/tests/test_classification_migration.py -q` — expect FAIL
- **IMPLEMENT**:
  - `models.py` WorkoutFile 內（緊接 `elevation_gain_m`）：
    ```python
    trail_classification: Mapped[Optional[str]] = mapped_column(String(20), default="unknown")
    classification_overridden: Mapped[Optional[bool]] = mapped_column(Boolean, default=False)
    ```
  - `database.py` `new_cols` list 追加：
    ```python
    ("workout_files", "trail_classification", "TEXT"),
    ("workout_files", "classification_overridden", "INTEGER"),
    ```
- **MIRROR**: MIGRATION_PATTERN、ORM_MIGRATION_TEST
- **VALIDATE**: 上述 pytest — expect PASS
- **COMMIT**: `feat(sport-pages): add trail_classification columns + migration`

### Task 2: trail/road 分類純函式
- **ACTION**: 建 `backend/engine/algorithms/classify.py`，依爬升率（爬升/距離）與總爬升把跑步活動分 `trail`/`road`/`unknown`。
- **TEST FIRST**: `backend/tests/test_classify.py`
  ```python
  import sys, os
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
  from backend.engine.algorithms.classify import classify_trail

  def test_road_run_low_elevation():
      # 10km, 30m gain → 3 m/km → road
      assert classify_trail(sport="running", distance_m=10000, elevation_gain_m=30) == "road"

  def test_trail_run_high_climb_rate():
      # 10km, 600m gain → 60 m/km → trail
      assert classify_trail(sport="running", distance_m=10000, elevation_gain_m=600) == "trail"

  def test_non_running_is_unknown():
      assert classify_trail(sport="cycling", distance_m=40000, elevation_gain_m=800) == "unknown"

  def test_missing_data_unknown():
      assert classify_trail(sport="running", distance_m=None, elevation_gain_m=None) == "unknown"
  ```
  Run: `python -m pytest backend/tests/test_classify.py -q` — expect FAIL
- **IMPLEMENT**: `classify.py`
  ```python
  """Trail vs road classification for running activities."""
  from typing import Optional

  TRAIL_CLIMB_RATE_M_PER_KM = 20.0  # ≥20 m/km climb → trail (tunable)

  def classify_trail(sport: Optional[str], distance_m: Optional[float],
                     elevation_gain_m: Optional[float]) -> str:
      if sport != "running":
          return "unknown"
      if not distance_m or distance_m <= 0 or elevation_gain_m is None:
          return "unknown"
      climb_rate = elevation_gain_m / (distance_m / 1000.0)
      return "trail" if climb_rate >= TRAIL_CLIMB_RATE_M_PER_KM else "road"
  ```
- **MIRROR**: 純函式測試風格（TEST_STRUCTURE）
- **GOTCHA**: 只對 `sport=="running"` 分類；單車/游泳維持 unknown，越野頁只取 trail。
- **VALIDATE**: pytest — expect PASS
- **COMMIT**: `feat(sport-pages): trail/road classifier by climb rate`

### Task 3: 解析時寫入分類
- **ACTION**: 在 FIT 匯入落 DB 處呼叫 `classify_trail()` 設定 `trail_classification`（若 `classification_overridden` 為真則不覆蓋）。
- **TEST FIRST**: `backend/tests/test_import_sets_classification.py` — 用 in-memory DB 建一筆 running + elevation_gain_m=600 的 WorkoutFile，跑匯入/更新函式，斷言 `trail_classification == "trail"`；再設 overridden=True 重跑，斷言不被改回。
  ```python
  # 依實際匯入函式簽章撰寫；核心斷言：
  # assert wf.trail_classification == "trail"
  # wf.classification_overridden = True; <re-run>; assert wf.trail_classification 不變
  ```
  Run: `python -m pytest backend/tests/test_import_sets_classification.py -q` — expect FAIL
- **IMPLEMENT**: 在 `fit_reader.py` 匯入/persist WorkoutFile 的位置（緊鄰設定 `sport`、`elevation_gain_m` 後）加：
  ```python
  from backend.engine.algorithms.classify import classify_trail
  if not getattr(wf, "classification_overridden", False):
      wf.trail_classification = classify_trail(wf.sport, wf.total_distance_m, wf.elevation_gain_m)
  ```
  （precise 落點：先 grep `elevation_gain_m =` 於匯入 persist 路徑，於同處設定。）
- **MIRROR**: NORMALIZE_SPORT 接點
- **GOTCHA**: 確認此處 `wf` 已有 `total_distance_m` 與 `elevation_gain_m`；若匯入順序不同，改在計算完成後統一設定。
- **VALIDATE**: pytest — expect PASS
- **COMMIT**: `feat(sport-pages): set trail_classification on import`

### Task 4: 回填腳本
- **ACTION**: 建 `backend/scripts/backfill_classification.py`，對既有 workout_files 跑 `classify_trail`，跳過 `classification_overridden=1`，idempotent。
- **TEST FIRST**: `backend/tests/test_backfill_classification.py` — in-memory DB 插入混合活動（running 高/低爬升、cycling、一筆 overridden），呼叫 backfill 主函式，斷言分類正確且 overridden 那筆不動。
  Run: `python -m pytest backend/tests/test_backfill_classification.py -q` — expect FAIL
- **IMPLEMENT**: 把可測邏輯抽成 `async def backfill(session) -> dict` 回傳 `{updated, skipped}`；`__main__` 用 `AsyncSessionLocal` 包一層。
  ```python
  async def backfill(session) -> dict:
      from sqlalchemy import select
      from backend.db.models import WorkoutFile
      from backend.engine.algorithms.classify import classify_trail
      rows = (await session.execute(select(WorkoutFile))).scalars().all()
      updated = skipped = 0
      for w in rows:
          if w.classification_overridden:
              skipped += 1; continue
          new = classify_trail(w.sport, w.total_distance_m, w.elevation_gain_m)
          if new != w.trail_classification:
              w.trail_classification = new; updated += 1
      await session.commit()
      return {"updated": updated, "skipped": skipped}
  ```
- **MIRROR**: SPORT_FILTER_PATTERN 的 select 風格
- **VALIDATE**: pytest — expect PASS；手動：`python -m backend.scripts.backfill_classification`
- **COMMIT**: `feat(sport-pages): backfill script for trail_classification`

### Task 5: PATCH /workouts/{id}/classification
- **ACTION**: 在 `workouts.py` 加端點，接受 `{trail_classification}`，驗證值 ∈ {road,trail,unknown}，設 `classification_overridden=True`。
- **TEST FIRST**: `backend/tests/test_classification_patch.py` — httpx/ASGITransport（沿用既有 API 測試風格，若無則用 TestClient）打 PATCH，斷言 200 + overridden=True；打非法值斷言 400；不存在 id 斷言 404。
  Run: `python -m pytest backend/tests/test_classification_patch.py -q` — expect FAIL
- **IMPLEMENT**:
  ```python
  from pydantic import BaseModel
  class ClassificationUpdate(BaseModel):
      trail_classification: str

  @router.patch("/{workout_id}/classification")
  async def update_classification(workout_id: int, body: ClassificationUpdate,
                                  db: AsyncSession = Depends(get_db)):
      if body.trail_classification not in ("road", "trail", "unknown"):
          raise HTTPException(400, "INVALID_CLASSIFICATION")
      wf = (await db.execute(select(WorkoutFile).where(WorkoutFile.id == workout_id))).scalars().first()
      if not wf:
          raise HTTPException(404, "WORKOUT_NOT_FOUND")
      wf.trail_classification = body.trail_classification
      wf.classification_overridden = True
      await db.commit()
      return {"id": wf.id, "trail_classification": wf.trail_classification,
              "classification_overridden": True}
  ```
- **MIRROR**: ROUTER_PATTERN、`workouts.py` 既有 HTTPException 慣例（404 WORKOUT_NOT_FOUND）
- **VALIDATE**: pytest — expect PASS
- **COMMIT**: `feat(sport-pages): PATCH classification endpoint with override`

### Task 6: analytics 端點 sports[] 參數化
- **ACTION**: 把 `run-load`、`intensity-load`、`run-volume`、`weekly` 的 `WorkoutFile.sport == "running"` 改為可選 `sports` query（`Optional[list[str]]`），預設維持原行為（不傳=全部；傳=`.in_(sports)`）；越野跑用 `trail_classification` 過濾另走 trail 端點。
- **TEST FIRST**: `backend/tests/test_sports_filter.py` — in-memory DB 插入 running+cycling+strength 各帶 tss metric，呼叫被抽出的查詢 helper（建議把 `WHERE` 組裝抽成 `_sport_clause(sports)`），斷言不同 sports 參數回不同筆數。
  Run: `python -m pytest backend/tests/test_sports_filter.py -q` — expect FAIL
- **IMPLEMENT**:
  - 加 helper：
    ```python
    from sqlalchemy.sql.elements import ColumnElement
    def _sport_clause(sports: Optional[list[str]]):
        return WorkoutFile.sport.in_(sports) if sports else True
    ```
  - 端點簽章加 `sports: Optional[list[str]] = Query(None)`（`from fastapi import Query`），把 `.where(WorkoutFile.sport == "running")` 換成 `.where(_sport_clause(sports))`。
  - **保留** `run-load` 預設行為：若 `sports` 為 None，維持 `["running"]`（向後相容既有前端呼叫）；於端點頂部 `sports = sports or ["running"]`。
- **MIRROR**: SPORT_FILTER_PATTERN
- **GOTCHA**: 向後相容是硬需求——既有 RunLoadChart 不傳 sports，必須仍只回 running。預設值補 `["running"]`。
- **VALIDATE**: pytest + 手動 curl `/api/v1/analytics/run-load`（不傳 sports）回原結果。
- **COMMIT**: `feat(sport-pages): parameterize analytics endpoints with sports[]`

### Task 7: GET /sports/facets
- **ACTION**: 建 `backend/api/sports.py`，回該 athlete 實有運動別與筆數（供前端動態 checkbox）。
- **TEST FIRST**: `backend/tests/test_sports_facets.py` — 插入多運動活動，呼叫 facets helper，斷言回傳含 running/cycling 與正確 count、label。
  Run: `python -m pytest backend/tests/test_sports_facets.py -q` — expect FAIL
- **IMPLEMENT**:
  ```python
  from fastapi import APIRouter, Depends
  from sqlalchemy import select, func
  from sqlalchemy.ext.asyncio import AsyncSession
  from backend.db.database import get_db
  from backend.db.models import WorkoutFile

  router = APIRouter(prefix="/api/v1/sports", tags=["sports"])
  SPORT_LABELS = {"running":"跑步","cycling":"單車","swimming":"游泳","walking":"健走"}

  @router.get("/facets")
  async def facets(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
      q = (select(WorkoutFile.sport, func.count(WorkoutFile.id))
           .where(WorkoutFile.athlete_id == athlete_id)
           .group_by(WorkoutFile.sport))
      rows = (await db.execute(q)).all()
      return {"sports": [
          {"key": s or "unknown", "count": c, "label": SPORT_LABELS.get(s, s or "其他")}
          for s, c in rows]}
  ```
  並在 `main.py` `include_router(sports.router)`。
- **MIRROR**: ROUTER_PATTERN、weekly_load 的 group_by 風格
- **VALIDATE**: pytest — expect PASS
- **COMMIT**: `feat(sport-pages): GET /sports/facets endpoint`

### Task 8: hrTSS 計算 + GET /analytics/trail-load
- **ACTION**: `metrics.py` 加 `compute_hr_tss(...)`（依 LTHR 相對強度積分，TRIMP 式）；`analytics.py` 加 `/trail-load`：以 `trail_classification=="trail"` 過濾，PMC 用 hrTSS 為主負荷，並同時回 rTSS（沿用 `pace_rtss`）供並陳。
- **TEST FIRST**: `backend/tests/test_hr_tss.py`
  ```python
  import sys, os
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
  import numpy as np, pytest
  from backend.engine.algorithms.metrics import compute_hr_tss

  def test_hr_tss_at_threshold_one_hour():
      # 1hr exactly at LTHR → ~100 TSS
      hr = np.array([160.0] * 3600); lthr = 160
      assert compute_hr_tss(hr, lthr, duration_s=3600) == pytest.approx(100.0, abs=5.0)

  def test_hr_tss_zero_guard():
      assert compute_hr_tss(None, 160, 3600) == 0.0
      assert compute_hr_tss(np.array([150.0]), 0, 3600) == 0.0
  ```
  Run: `python -m pytest backend/tests/test_hr_tss.py -q` — expect FAIL
- **IMPLEMENT**: `metrics.py`
  ```python
  def compute_hr_tss(hr_series, lthr, duration_s) -> float:
      """TRIMP-style hrTSS: 1hr at LTHR ≈ 100. hr_series: per-second HR array."""
      import numpy as np
      if hr_series is None or not lthr or lthr <= 0 or not duration_s:
          return 0.0
      hr = np.asarray(hr_series, dtype=float)
      if hr.size == 0:
          return 0.0
      intensity = hr / float(lthr)                # relative to threshold HR
      avg_if_sq = float(np.mean(intensity ** 2))
      return (duration_s / 3600.0) * avg_if_sq * 100.0
  ```
  `analytics.py` `/trail-load`：複用 run-load 結構但過濾 `WorkoutFile.trail_classification == "trail"`，TSS 取 `hr_tss` metric（見 Task 8b 寫入），rTSS 取 `r_tss` metric 並陳。
- **MIRROR**: `pace_rtss`、`compute_run_pmc`（metrics.py）；run-load 端點結構
- **GOTCHA**: hrTSS 需 per-second HR。若僅有彙整值，改用 avg_hr 近似並於回應標記 `approx=true`。缺 HR → `NO_HR_DATA` 422 或回 rTSS fallback。
- **VALIDATE**: pytest — expect PASS
- **COMMIT**: `feat(sport-pages): hrTSS + trail-load endpoint (hrTSS primary, rTSS alongside)`

> **Task 8b（同 commit 內或緊接）**: 在指標計算管線把 `hr_tss`、`r_tss` 寫入 `WorkoutMetric`（沿用既有 metric_key upsert）。測試：插入一筆 trail 活動帶 hr_tss metric → `/trail-load` 回非空 series。

### Task 9: GET /analytics/trail-summary
- **ACTION**: 越野聚合：期間總爬升、VAM 趨勢（沿用 trail.py segment）、爬升活動次數、最近一次 PI 對照（若有）。
- **TEST FIRST**: `backend/tests/test_trail_summary.py` — 插入 trail 活動帶 elevation_gain_m，呼叫 summary helper，斷言 total_gain_m 加總正確。
  Run: `python -m pytest backend/tests/test_trail_summary.py -q` — expect FAIL
- **IMPLEMENT**: 加 `/trail-summary` 端點，`WHERE trail_classification=="trail"` 聚合 `func.sum(elevation_gain_m)`、count、近期清單；VAM 趨勢可先回每次活動的 gain/hour。
- **MIRROR**: run-volume 的 group_by 聚合
- **VALIDATE**: pytest — expect PASS
- **COMMIT**: `feat(sport-pages): trail-summary endpoint`

### Task 10: GET /analytics/chart-interpretation（規則解讀）
- **ACTION**: 後端規則層：依指標值回 `{status, label, color, summary}`（一句話白話 + 號誌），支援 chart=`pmc`/`trail-load` 等。
- **TEST FIRST**: `backend/tests/test_interpretation.py` — 給定 tsb=8 回 fresh/新鮮/green 與含「新鮮」的 summary；tsb=-30 回 overreached/紅。
  Run: `python -m pytest backend/tests/test_interpretation.py -q` — expect FAIL
- **IMPLEMENT**: 抽 `interpret_tsb(tsb) -> dict` 複用 TSB_STATE_RULE，summary 用中文模板（如 `f"目前 TSB {tsb:.0f}，狀態：新鮮，適合高強度"`）。端點依 chart 參數分派。
- **MIRROR**: TSB_STATE_RULE（analytics.py:30-41）、TSB_LABELS（前端 SmartDashboardSection，後端複製成中文 map）
- **VALIDATE**: pytest — expect PASS
- **COMMIT**: `feat(sport-pages): rule-based chart-interpretation endpoint`

### Task 11: 公式驗證器
- **ACTION**: 建 `backend/engine/algorithms/validator.py`，把核心公式（CTL τ=42、ATL τ=7、TSS、pace_rtss）對照 WKO5 逆向常數，輸出 pass/diff 報告；落檔 `docs/reports/sport-pages-formula-validation.md`。
- **TEST FIRST**: `backend/tests/test_formula_validation.py`
  ```python
  import sys, os
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
  from backend.engine.algorithms.validator import validate_core_formulas

  def test_core_formulas_match_wko5():
      report = validate_core_formulas()
      failing = [r for r in report if not r["pass"]]
      assert failing == [], f"formula mismatches: {failing}"
  ```
  Run: `python -m pytest backend/tests/test_formula_validation.py -q` — expect FAIL
- **IMPLEMENT**: `validator.py`
  ```python
  """Validate core metric formulas against WKO5 reverse-engineered constants.
  Authoritative constants from docs/srs/completed/wko5-training-load-charts.srs.md."""
  import math
  from datetime import date, timedelta
  from backend.engine.algorithms.metrics import compute_run_pmc, pace_rtss

  def validate_core_formulas() -> list[dict]:
      out = []
      # CTL/ATL EWMA tau: steady 50 TSS/day 365d → CTL≈ATL≈50, TSB≈0
      series = [(date(2025,1,1)+timedelta(days=i), 50.0) for i in range(365)]
      last = compute_run_pmc(series)[-1]
      out.append({"name":"CTL_tau42_converges_50","pass": abs(last["ctl"]-50.0) < 0.5,
                  "expected":50.0, "actual":round(last["ctl"],3)})
      out.append({"name":"ACWR_steady_~1.0","pass": abs(last["acwr"]-1.0) < 0.02,
                  "expected":1.0, "actual":round(last["acwr"],3)})
      # rTSS at threshold pace → duration_hours*100
      r = pace_rtss(10000.0, 50000.0, 5.0)
      out.append({"name":"rTSS_at_threshold","pass": abs(r-(50000/3600*100)) < 1.0,
                  "expected":round(50000/3600*100,1), "actual":round(r,1)})
      return out
  ```
  （隨後可擴充 bike/run FTP、MMP 對照。climb_load 標記原創、不納入。）
- **MIRROR**: test_run_pmc.py 的數學斷言風格
- **GOTCHA**: 驗證常數須引自 `wko5-training-load-charts.srs.md`（τ=42/7 等）——逐項註明來源行。
- **VALIDATE**: pytest — expect PASS；落報告 `python -c "from backend.engine.algorithms.validator import validate_core_formulas as v; print(v())"`
- **COMMIT**: `feat(sport-pages): formula validator against WKO5 constants`

---

## Testing Strategy

### Unit Tests
| Test | Input | Expected | Edge? |
|---|---|---|---|
| classify_trail | 10km/600m running | "trail" | Y |
| classify_trail | cycling | "unknown" | Y |
| compute_hr_tss | 1hr @ LTHR | ~100 | Y |
| _sport_clause | None | 全部 | Y |
| validate_core_formulas | — | 全 pass | N |

### Edge Cases Checklist
- [ ] 缺 distance/elevation → unknown
- [ ] overridden 不被回填/匯入覆蓋
- [ ] sports=None 向後相容（run-load 仍只 running）
- [ ] 缺 HR → hrTSS 0 / NO_HR_DATA
- [ ] migration 重跑 idempotent

---

## Validation Commands

### Static Analysis
```bash
cd "<repo>reverse"
python -m pyflakes backend/ 2>/dev/null || true
```
EXPECT: 無新增未定義名稱

### Unit Tests
```bash
cd "<repo>reverse"
python -m pytest backend/tests/ -q
```
EXPECT: 全數 pass（含既有 test_run_pmc / test_migration 無回歸）

### Manual Validation
- [ ] `python -m backend.scripts.backfill_classification` 回填既有資料
- [ ] curl `/api/v1/analytics/run-load`（不傳 sports）結果與改動前一致
- [ ] curl `/api/v1/analytics/trail-load?athlete_id=1` 回 hrTSS 主序列
- [ ] curl `/api/v1/sports/facets?athlete_id=1` 列出實有運動別

---

## Acceptance Criteria
- [ ] AC-1/AC-2（分類持久化+覆寫）對應 Task 1-5 測試通過
- [ ] AC-5（越野負荷雙指標）對應 Task 8 測試通過
- [ ] AC-7（公式驗證）對應 Task 11 測試通過
- [ ] 所有 validation 指令通過、無既有測試回歸
- [ ] 向後相容：既有 run-load/intensity-load 不傳 sports 行為不變

## Completion Checklist
- [ ] 遵循 migration / router / 測試既有模式
- [ ] HTTPException 錯誤碼沿用既有慣例
- [ ] 無硬編路徑（DB 用 get_db）
- [ ] 自足，無需實作中再查碼

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| sports[] 改動破壞既有前端 | M | H | 預設補 ["running"]，向後相容測試 |
| 分類閾值不準 | M | M | 閾值常數可調 + PATCH 覆寫 |
| hrTSS 缺 per-second HR | M | M | avg_hr 近似 + approx 標記 / fallback rTSS |
| climb_load 無法驗證 | L | L | 標記原創、排除驗證集 |

## Notes
分類閾值 `TRAIL_CLIMB_RATE_M_PER_KM=20` 為初值，部署後依使用者實際活動微調（Open Question）。hrTSS 所需 LTHR 取自 `AthleteSettings.lthr`，歷史缺值見 SRS Open Question。
