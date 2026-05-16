# Plan: AI Coach — 訓練分析與圖表理解

> **For agentic workers:** `/prp-implement` will route this plan to `implementing-features` skill based on `Metadata.Type = feature`.

## Summary

在現有 WKO5 Coach 網站新增三個功能：(1) In-app AI Coach Chat（POST SSE 串流，支援 Claude/OpenAI）、(2) Smart Dashboard（今日 TSB 狀態卡 + 本週負荷彙整）、(3) Trail Analysis（GAP/VAM/心率漂移，越野跑自動偵測）。後端沿用 FastAPI async + SQLAlchemy 模式，前端沿用 React Query + Recharts + zustand。

## User Story

As a 自我訓練的越野跑運動員,
I want 在網站內直接用中文問 AI 教練問題並看到越野跑專屬分析圖表,
So that 我能在 5 分鐘內理解自己的訓練狀態並做出有依據的訓練決策。

## Problem → Solution

現有：圖表 8 張但無解讀、AiPage 只有 MCP 設定說明、timeseries 缺 altitude/gap 資料、無越野跑分析。
目標：AI Chat in-app + Smart Dashboard 5 卡片 + Trail 4 圖表（爬坡自動偵測）。

## Metadata
- **Source PRD**: `docs/prd/ai-coach-training-analysis.prd.md`
- **Source SRS**: `docs/spec/ai-coach-training-analysis.spec.md`
- **Source Linear Issue**: N/A
- **Type**: feature
- **Size**: L
- **Complexity**: Large
- **Rigor**: balanced
- **Mode**: A — 快建
- **TDD**: off
- **Commit cadence**: per-task
- **Estimated Files**: 19（9 backend, 10 frontend）

---

## UX Design

### Before
```
[AI tab] → MCP 設定說明頁（靜態文字，無互動）
[Season tab] → 圖表堆疊（8 張，無狀態彙整）
[Activity] → Timeseries + MMP + Zones（無越野跑圖表）
[Config] → FTP + LTHR 輸入（無 AI key 設定）
```

### After
```
[AI tab] → AiChat 全頁對話
  ┌──────────────────────────────────────────┐
  │ AI 教練  ● 已連線 (claude-sonnet-4-6)    │
  │──────────────────────────────────────────│
  │ [快捷] 本週狀態？ 昨天練得怎樣？ 適合高強度嗎？│
  │──────────────────────────────────────────│
  │ 用戶：本週 CTL 是多少？                   │
  │ AI：你的 CTL 目前是 52.1，比上週...        │
  │──────────────────────────────────────────│
  │ [輸入框]                         [送出]   │
  └──────────────────────────────────────────┘

[Season tab] → SmartDashboardSection（新，置頂）
  ┌─────┬─────┬──────┬─────┬────────┐
  │ TSB │ CTL │週 TSS│週時數│最近訓練│
  │ -8  │ 52  │ 312  │ 6.5h│ 昨跑步 │
  │輕疲勞│上升 │      │     │TSS 87  │
  └─────┴─────┴──────┴─────┴────────┘
  [問 AI 教練 →]
  + 原有 8 張圖表（保留）

[Activity] → 爬升 > 100m 時自動顯示 TrailAnalysisSection
  ① 海拔 + GAP 配速疊圖
  ② 坡度 vs 踏頻散點
  ③ VAM 分段表
  ④ 心率漂移卡片

[Config] → 新增 AI 設定區塊
  Provider: [Claude ▼]  Model: [claude-sonnet-4-6 ▼]
  API Key:  [••••••••••••••••••]   [儲存]
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| AI tab | 靜態 MCP 說明 | AiChat 全頁 | AiPage.tsx 完全替換 |
| Season tab top | 無 | SmartDashboardSection 5 卡片 | SeasonTab.tsx 頂部插入 |
| Activity > 越野跑 | 無 | TrailAnalysisSection 4 圖 | 爬升 > 100m 自動觸發 |
| Config tab | FTP/LTHR 輸入 | + AI Provider/Model/Key | ConfigTab.tsx 新增區塊 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `backend/api/sync.py` | 1-35 | SSE `EventSourceResponse` + async generator 完整模式 |
| P0 | `backend/api/athletes.py` | 95-130 | `SettingsUpdate` Pydantic + upsert 模式 |
| P0 | `backend/db/database.py` | 1-55 | `_migrate_schema()` ALTER TABLE 模式 |
| P0 | `backend/engine/algorithms/metrics.py` | 39-110 | algorithm 函式簽名與 numpy 模式 |
| P1 | `backend/api/workouts.py` | 60-120 | `get_workout_timeseries()` 降採樣 + FIT 讀取模式 |
| P1 | `backend/api/analytics.py` | 1-50 | analytics router 結構 + PMC 查詢 |
| P1 | `frontend/src/api/hooks.ts` | 1-30 | `useQuery` / `useMutation` hook 模式 |
| P1 | `frontend/src/api/client.ts` | 1-40 | 型別定義位置 + axios client |
| P1 | `frontend/src/store/tabStore.ts` | all | zustand store 模式（複製用於 aiChatStore）|
| P1 | `frontend/src/lib/chartTheme.ts` | all | `CHART_COLORS` + `BASE_*` 圖表主題 |
| P1 | `frontend/src/components/charts/TimeseriesChart.tsx` | all | ComposedChart + Card wrapper 模式 |
| P2 | `frontend/src/tabs/ConfigTab.tsx` | all | form + `update.mutate()` 設定儲存模式 |
| P2 | `frontend/src/pages/Dashboard.tsx` | 25-55 | SSE fetch + ReadableStream 消費模式（Coros sync）|

---

## Patterns to Mirror

### FASTAPI_SSE_PATTERN
```python
# SOURCE: backend/api/sync.py:14-32
from sse_starlette.sse import EventSourceResponse
import json

@router.post("/start")
async def start_sync(db: AsyncSession = Depends(get_db)):
    async def generate():
        async for event in some_async_generator(db):
            yield {"data": json.dumps(event)}   # 注意：只用 "data" key，不需 "event" key
    return EventSourceResponse(generate())
```

### FASTAPI_ROUTER_PATTERN
```python
# SOURCE: backend/api/workouts.py:1-20
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from backend.db.database import get_db

router = APIRouter(prefix="/api/v1/{resource}", tags=["{resource}"])

async def _get_or_404(id: int, db: AsyncSession) -> Model:
    result = await db.execute(select(Model).where(Model.id == id))
    obj = result.scalar_one_or_none()
    if not obj:
        raise HTTPException(404, "RESOURCE_NOT_FOUND")
    return obj
```

### SETTINGS_UPSERT_PATTERN
```python
# SOURCE: backend/api/athletes.py:112-130
result = await db.execute(
    select(AthleteSettings).where(
        AthleteSettings.athlete_id == athlete_id,
        AthleteSettings.effective_date == eff_date,
    )
)
s = result.scalar_one_or_none()
if s:
    if body.some_field is not None:
        s.some_field = body.some_field
else:
    s = AthleteSettings(athlete_id=athlete_id, some_field=body.some_field)
    db.add(s)
await db.commit()
```

### SCHEMA_MIGRATION_PATTERN
```python
# SOURCE: backend/db/database.py:_migrate_schema()
new_cols = [
    ("table_name", "column_name", "COLUMN_TYPE"),
]
async with engine.begin() as conn:
    for table, col, col_type in new_cols:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        existing = {row[1] for row in result.fetchall()}
        if col not in existing:
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"))
```

### PYDANTIC_OPTIONAL_MODEL
```python
# SOURCE: backend/api/athletes.py:103-110
class SettingsUpdate(BaseModel):
    ftp_w: Optional[float] = None
    lthr: Optional[int] = None
    # 所有欄位 Optional，caller 只送要改的欄位
```

### TIMESERIES_DOWNSAMPLE_PATTERN
```python
# SOURCE: backend/api/workouts.py:get_workout_timeseries()
n = len(time_s)
step = max(1, n // 1800)   # 最多 1800 個點
series = []
for i in range(0, n, step):
    point: dict = {"t": int(time_s[i])}
    if i < len(some_channel) and some_channel[i] is not None:
        point["key"] = round(float(some_channel[i]))
    series.append(point)
```

### NUMPY_ALGORITHM_PATTERN
```python
# SOURCE: backend/engine/algorithms/metrics.py:39-65
import numpy as np

def compute_something(arr: np.ndarray, param: float = 1.0) -> float:
    arr = np.asarray(arr, dtype=np.float64)
    arr = np.where(np.isnan(arr), 0.0, arr)
    # ... pure numpy computation ...
    return round(float(result), 2)
```

### REACT_QUERY_HOOK_PATTERN
```typescript
// SOURCE: frontend/src/api/hooks.ts:useRunLoad()
export function useTrailAnalysis(workoutId: number) {
  return useQuery<TrailResponse>({
    queryKey: ['trail', workoutId],
    queryFn: () => api.get(`/workouts/${workoutId}/trail`).then(r => r.data),
    enabled: !!workoutId,
    staleTime: Infinity,   // FIT 計算結果不變，永久快取
  })
}
```

### ZUSTAND_STORE_PATTERN
```typescript
// SOURCE: frontend/src/store/tabStore.ts
import { create } from 'zustand'

interface MyStore {
  field: Type
  setField: (v: Type) => void
}

export const useMyStore = create<MyStore>((set) => ({
  field: initialValue,
  setField: (v) => set({ field: v }),
}))
```

### FRONTEND_SSE_FETCH_PATTERN
```typescript
// SOURCE: frontend/src/pages/Dashboard.tsx:handleSync() (Coros sync)
// EventSource 不支援 POST body，改用 fetch + ReadableStream
const response = await fetch('/api/v1/ai/chat', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(payload),
})
const reader = response.body!.getReader()
const decoder = new TextDecoder()
while (true) {
  const { done, value } = await reader.read()
  if (done) break
  const chunk = decoder.decode(value)
  for (const line of chunk.split('\n')) {
    if (!line.startsWith('data: ')) continue
    try {
      const evt = JSON.parse(line.slice(6))
      // handle evt
    } catch { /* skip malformed */ }
  }
}
```

### CHART_CARD_PATTERN
```typescript
// SOURCE: frontend/src/components/charts/TimeseriesChart.tsx:37-50
import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'
import { CHART_COLORS, BASE_GRID_PROPS, BASE_TOOLTIP_STYLE } from '../../lib/chartTheme'

export function MyChart({ data }: Props) {
  return (
    <Card>
      <CardHeader><CardTitle>標題</CardTitle></CardHeader>
      <CardContent className="pt-0">
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={data} margin={{ top: 4, right: 40, left: -12, bottom: 0 }}>
            <CartesianGrid {...BASE_GRID_PROPS} />
            {/* axes, lines, etc */}
            <Tooltip {...BASE_TOOLTIP_STYLE} />
          </ComposedChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `requirements.txt` | UPDATE | 新增 `anthropic`, `openai` |
| `backend/db/models.py` | UPDATE | AthleteSettings 新增 3 欄 |
| `backend/db/database.py` | UPDATE | `_migrate_schema()` 新增 3 欄 |
| `backend/engine/algorithms/trail.py` | CREATE | GAP / VAM / HR drift 算法 |
| `backend/engine/ai/__init__.py` | CREATE | 空 init |
| `backend/engine/ai/client.py` | CREATE | AIClient + ClaudeClient + OpenAIClient |
| `backend/engine/ai/context.py` | CREATE | 組裝 system prompt + athlete context |
| `backend/api/ai.py` | CREATE | POST /ai/chat (SSE) + GET /ai/status |
| `backend/api/workouts.py` | UPDATE | 新增 GET /workouts/{id}/trail |
| `backend/api/analytics.py` | UPDATE | 新增 GET /analytics/dashboard-summary |
| `backend/main.py` | UPDATE | 註冊 ai router |
| `frontend/src/api/client.ts` | UPDATE | 新增 Trail/AI/Dashboard 型別 |
| `frontend/src/api/hooks.ts` | UPDATE | 新增 useTrailAnalysis / useAiStatus / useDashboardSummary |
| `frontend/src/store/aiChatStore.ts` | CREATE | zustand chat messages store |
| `frontend/src/tabs/ConfigTab.tsx` | UPDATE | 新增 AI 設定區塊 |
| `frontend/src/components/AiChat.tsx` | CREATE | AI 對話 UI |
| `frontend/src/components/charts/SmartDashboardSection.tsx` | CREATE | 5 張狀態卡 |
| `frontend/src/components/charts/TrailAnalysisSection.tsx` | CREATE | 4 張越野跑圖表 |
| `frontend/src/pages/ActivityDetailPage.tsx` | UPDATE | 插入 TrailAnalysisSection |
| `frontend/src/tabs/SeasonTab.tsx` | UPDATE | 頂部插入 SmartDashboardSection |
| `frontend/src/pages/AiPage.tsx` | UPDATE | 替換為 AiChat |

## NOT Building
- 對話歷史 DB 持久化（React state only）
- Claude Desktop MCP server（保留 ConfigTab 下方說明文字即可）
- 地圖可視化（需 GPS lat/lon，FIT 解析器未提取）
- GAP TSS 存進 DB（只在 trail endpoint 即時計算回傳）
- 多運動員切換 UI

---

## Step-by-Step Tasks

---

### Task 1: 新增 AI SDK 依賴

- **ACTION**: 在 `requirements.txt` 新增 anthropic 和 openai SDK
- **IMPLEMENT**: 在 `requirements.txt` 末尾加入：
  ```
  anthropic>=0.40.0
  openai>=1.30.0
  ```
- **MIRROR**: 與現有 `sse-starlette>=2.1.0` 格式一致
- **GOTCHA**: 兩個 SDK import 放在各自 client class 內部（lazy import），避免啟動時如果 key 未設定也要安裝才能跑
- **VALIDATE**: `pip install -r requirements.txt` — 無錯誤

---

### Task 2: AthleteSettings schema 擴充（DB + Model）

- **ACTION**: 在 `backend/db/models.py` 新增 3 個 nullable 欄位；在 `backend/db/database.py` 的 `_migrate_schema()` 新增對應 ALTER TABLE
- **IMPLEMENT** — `backend/db/models.py`，在 `AthleteSettings` class 的 `initial_atl_run` 行後加入：
  ```python
  ai_provider: Mapped[Optional[str]] = mapped_column(nullable=True)
  ai_api_key:  Mapped[Optional[str]] = mapped_column(nullable=True)
  ai_model:    Mapped[Optional[str]] = mapped_column(nullable=True)
  ```
- **IMPLEMENT** — `backend/db/database.py`，在 `new_cols` list 的 `("athlete_settings", "initial_atl_run", "REAL")` 行後加入：
  ```python
  ("athlete_settings", "ai_provider", "TEXT"),
  ("athlete_settings", "ai_api_key",  "TEXT"),
  ("athlete_settings", "ai_model",    "TEXT"),
  ```
- **MIRROR**: `backend/db/database.py:_migrate_schema()` — PRAGMA 檢查後 ALTER TABLE
- **GOTCHA**: SQLAlchemy `Mapped[Optional[str]]` 已隱含 nullable；`mapped_column(nullable=True)` 是明確標記，與現有欄位風格一致
- **VALIDATE**: 啟動 `uvicorn backend.main:app` — 無 DB 錯誤；`sqlite3 ~/.wko5coach/wko5coach.db ".schema athlete_settings"` 確認新欄位存在

---

### Task 3: 更新 `PUT /athletes/{id}/settings` 支援 AI 欄位

- **ACTION**: 在 `backend/api/athletes.py` 的 `SettingsUpdate` Pydantic model 和 `update_settings` endpoint 新增 ai_* 欄位
- **IMPLEMENT** — `SettingsUpdate` class（`backend/api/athletes.py:103`）新增：
  ```python
  ai_provider: Optional[str] = None
  ai_api_key:  Optional[str] = None
  ai_model:    Optional[str] = None
  ```
- **IMPLEMENT** — `update_settings` function 的 upsert 區塊（`backend/api/athletes.py:~125`）新增：
  ```python
  if body.ai_provider is not None:
      s.ai_provider = body.ai_provider
  if body.ai_api_key is not None:
      s.ai_api_key = body.ai_api_key
  if body.ai_model is not None:
      s.ai_model = body.ai_model
  ```
- **IMPLEMENT** — `get_settings` endpoint 回傳值（`backend/api/athletes.py:get_settings`）在 return dict 中加入：
  ```python
  "ai_provider": s.ai_provider,
  "ai_model": s.ai_model,
  # 注意：不回傳 ai_api_key（安全）
  ```
- **MIRROR**: `backend/api/athletes.py:104-130` — 每欄位 `if body.X is not None: s.X = body.X`
- **GOTCHA**: `ai_api_key` **不回傳**到前端（GET settings），只允許寫入；前端顯示 `"已設定"` 或 `"未設定"` 即可
- **VALIDATE**: `curl -X PUT http://localhost:8000/api/v1/athletes/1/settings -H "Content-Type: application/json" -d '{"ai_provider":"claude","ai_model":"claude-sonnet-4-6","ai_api_key":"test"}'` 回傳 `{"saved": true}`

---

### Task 4: `backend/engine/algorithms/trail.py`

- **ACTION**: 建立新檔案，實作 4 個 trail 分析函式
- **IMPLEMENT** — 建立 `backend/engine/algorithms/trail.py`：
  ```python
  """Trail running analysis: GAP, VAM, HR drift."""
  from dataclasses import dataclass
  from typing import Optional
  import numpy as np


  @dataclass
  class ClimbSegment:
      start_km: float
      end_km: float
      elevation_gain_m: float
      vam_m_per_hr: float
      avg_hr: Optional[float]
      avg_cadence: Optional[float]
      is_hiking: bool


  def compute_grade(altitude_m: np.ndarray, distance_m: np.ndarray) -> np.ndarray:
      """Smoothed gradient in percent. Safe for uniform 1s samples."""
      altitude_m = np.asarray(altitude_m, dtype=np.float64)
      distance_m = np.asarray(distance_m, dtype=np.float64)
      n = len(altitude_m)
      if n < 2:
          return np.zeros(n)
      d_alt = np.diff(altitude_m, prepend=altitude_m[0])
      d_dist = np.diff(distance_m, prepend=distance_m[0])
      with np.errstate(divide='ignore', invalid='ignore'):
          grade = np.where(d_dist > 0.1, d_alt / d_dist * 100.0, 0.0)
      grade = np.clip(grade, -45.0, 45.0)
      # 30s rolling smooth (30 samples at 1s resolution)
      window = 30
      cumsum = np.zeros(n + 1)
      cumsum[1:] = np.cumsum(grade)
      smoothed = np.zeros(n)
      for i in range(n):
          lo = max(0, i - window // 2)
          hi = min(n, i + window // 2 + 1)
          smoothed[i] = (cumsum[hi] - cumsum[lo]) / (hi - lo)
      return smoothed


  def compute_gap(pace_s_per_km: np.ndarray, grade_pct: np.ndarray) -> np.ndarray:
      """
      Grade-Adjusted Pace (s/km). GAP = actual_pace / grade_factor.
      Uphill: factor > 1 → GAP faster (smaller) than actual.
      Downhill: factor < 1 → GAP slower (larger) than actual.
      Linear approximation: Strava-style.
      """
      pace = np.asarray(pace_s_per_km, dtype=np.float64)
      grade = np.asarray(grade_pct, dtype=np.float64)
      factor = np.where(
          grade >= 0,
          1.0 + 0.033 * grade,
          np.maximum(0.5, 1.0 + 0.015 * grade),  # grade < 0
      )
      gap = pace / factor
      return np.clip(gap, 30.0, 1800.0)  # 0:30–30:00 min/km


  def segment_climbs(
      altitude_m: np.ndarray,
      distance_m: np.ndarray,
      time_s: np.ndarray,
      grade_pct: np.ndarray,
      hr_bpm: Optional[np.ndarray] = None,
      cadence_rpm: Optional[np.ndarray] = None,
      min_grade_pct: float = 5.0,
      min_gain_m: float = 50.0,
  ) -> list[ClimbSegment]:
      """Identify continuous uphill segments meeting min thresholds."""
      n = len(altitude_m)
      is_up = grade_pct >= min_grade_pct
      segments: list[ClimbSegment] = []
      i = 0
      while i < n:
          if not is_up[i]:
              i += 1
              continue
          start = i
          while i < n and is_up[i]:
              i += 1
          end = i - 1
          gain = float(altitude_m[end] - altitude_m[start])
          if gain < min_gain_m:
              continue
          elapsed_s = float(time_s[end] - time_s[start])
          vam = (gain / elapsed_s * 3600.0) if elapsed_s > 0 else 0.0
          seg_hr = (
              float(np.nanmean(hr_bpm[start:end+1]))
              if hr_bpm is not None and np.any(hr_bpm[start:end+1] > 0)
              else None
          )
          seg_cad = (
              float(np.nanmean(cadence_rpm[start:end+1]))
              if cadence_rpm is not None and np.any(cadence_rpm[start:end+1] > 0)
              else None
          )
          segments.append(ClimbSegment(
              start_km=round(float(distance_m[start]) / 1000, 2),
              end_km=round(float(distance_m[end]) / 1000, 2),
              elevation_gain_m=round(gain, 1),
              vam_m_per_hr=round(vam, 0),
              avg_hr=round(seg_hr, 0) if seg_hr else None,
              avg_cadence=round(seg_cad, 0) if seg_cad else None,
              is_hiking=bool(seg_cad is not None and seg_cad < 155),
          ))
      return segments


  def compute_hr_drift(
      hr_bpm: np.ndarray,
      gap_pace: np.ndarray,
      time_s: np.ndarray,
  ) -> Optional[dict]:
      """
      Cardiac drift: compare HR/GAP ratio between first and second half.
      Only computed for workouts > 10 minutes with HR data.
      """
      if len(time_s) < 2:
          return None
      duration = float(time_s[-1] - time_s[0])
      if duration < 600:
          return None
      has_hr = hr_bpm is not None and np.any(hr_bpm > 0)
      if not has_hr:
          return None
      mid = len(time_s) // 2
      # Use GAP-normalized HR ratio (higher = less efficient)
      with np.errstate(divide='ignore', invalid='ignore'):
          ratio = np.where(gap_pace > 0, hr_bpm / gap_pace * 60.0, np.nan)
      first = float(np.nanmean(ratio[:mid]))
      second = float(np.nanmean(ratio[mid:]))
      if first <= 0:
          return None
      drift_pct = (second - first) / first * 100.0
      return {
          "first_half_ratio": round(first, 3),
          "second_half_ratio": round(second, 3),
          "drift_pct": round(drift_pct, 1),
      }
  ```
- **MIRROR**: `backend/engine/algorithms/metrics.py:39-65` — numpy 向量化，`np.asarray`，`round(float(result), N)`
- **GOTCHA**: `compute_grade` 中 distance diff 用 `prepend=` 讓輸出與輸入等長；`np.errstate(divide='ignore')` 避免 0-division warning
- **VALIDATE**: 在專案根目錄執行：
  ```bash
  cd /Users/<user>/Projects/WKO5reverse
  python -c "
  import numpy as np, sys
  sys.path.insert(0, '.')
  from backend.engine.algorithms.trail import compute_grade, compute_gap, segment_climbs, compute_hr_drift
  alt = np.array([100.0, 110.0, 120.0, 115.0, 100.0])
  dist = np.array([0.0, 100.0, 200.0, 300.0, 400.0])
  grade = compute_grade(alt, dist)
  pace = np.full(5, 360.0)
  gap = compute_gap(pace, grade)
  print('grade:', grade.round(1))  # expect: [0, 10, 10, -5, -15]
  print('gap[1]:', round(gap[1], 1))  # uphill: 360 / 1.33 ≈ 270.7
  print('OK')
  "
  ```

---

### Task 5: `backend/engine/ai/client.py`

- **ACTION**: 建立 AI provider 抽象層，實作 ClaudeClient + OpenAIClient + factory
- **IMPLEMENT** — 建立目錄並建立兩個檔案：
  ```bash
  touch backend/engine/ai/__init__.py
  ```
  建立 `backend/engine/ai/client.py`：
  ```python
  """AI provider abstraction. Supports Claude (Anthropic) and OpenAI."""
  from typing import Optional, TYPE_CHECKING
  from backend.db.models import AthleteSettings

  if TYPE_CHECKING:
      from typing import AsyncGenerator


  class ClaudeClient:
      def __init__(self, key: str, model: str):
          self._key = key
          self.model = model

      async def stream(self, messages: list[dict], system: str):
          import anthropic
          client = anthropic.AsyncAnthropic(api_key=self._key)
          async with client.messages.stream(
              model=self.model,
              max_tokens=1024,
              system=system,
              messages=messages,
          ) as s:
              async for text in s.text_stream:
                  yield text


  class OpenAIClient:
      def __init__(self, key: str, model: str):
          self._key = key
          self.model = model

      async def stream(self, messages: list[dict], system: str):
          import openai
          client = openai.AsyncOpenAI(api_key=self._key)
          full_msgs = [{"role": "system", "content": system}, *messages]
          response = await client.chat.completions.create(
              model=self.model,
              messages=full_msgs,
              stream=True,
              max_tokens=1024,
          )
          async for chunk in response:
              delta = chunk.choices[0].delta.content
              if delta:
                  yield delta


  def get_ai_client(settings: Optional[AthleteSettings]):
      """Return the configured AI client, or None if not configured."""
      if not settings or not settings.ai_api_key or not settings.ai_provider:
          return None
      key = settings.ai_api_key
      model = settings.ai_model or _default_model(settings.ai_provider)
      if settings.ai_provider == "claude":
          return ClaudeClient(key=key, model=model)
      if settings.ai_provider == "openai":
          return OpenAIClient(key=key, model=model)
      return None


  def _default_model(provider: str) -> str:
      return {"claude": "claude-sonnet-4-6", "openai": "gpt-4o-mini"}.get(provider, "")


  CLAUDE_MODELS = [
      ("claude-haiku-4-5-20251001", "Haiku — 最快/最省費"),
      ("claude-sonnet-4-6",          "Sonnet — 均衡（推薦）"),
      ("claude-opus-4-7",            "Opus — 最強分析力"),
  ]

  OPENAI_MODELS = [
      ("gpt-4o-mini", "GPT-4o mini — 最快/最省費"),
      ("gpt-4o",      "GPT-4o — 均衡"),
      ("gpt-4.1",     "GPT-4.1 — 最新"),
  ]
  ```
- **MIRROR**: lazy import 在 method 內，避免安裝 SDK 前無法 import module
- **GOTCHA**: `ClaudeClient.stream` 是 `async def` 但用了 `yield`，Python 會自動識別為 async generator，呼叫時不需 `await`，只能 `async for`
- **VALIDATE**: `python -c "from backend.engine.ai.client import get_ai_client, CLAUDE_MODELS; print(CLAUDE_MODELS[0])"` — 無錯誤

---

### Task 6: `backend/engine/ai/context.py`

- **ACTION**: 實作訓練 context 組裝，查詢 DB 並產生 system prompt 字串
- **IMPLEMENT** — 建立 `backend/engine/ai/context.py`：
  ```python
  """Assemble training context for AI coach system prompt."""
  from datetime import date, timedelta
  from sqlalchemy.ext.asyncio import AsyncSession
  from sqlalchemy import select, func
  from typing import Optional

  from backend.db.models import AthleteSettings, WorkoutFile, WorkoutMetric, PmcCache


  _SYSTEM_BASE = """你是一位專業的耐力運動教練，專精於越野跑、山岳跑和路跑。
  請用繁體中文回答所有問題。語氣像一位關心學員的教練：具體、直接、給數字、給建議。
  不要說「根據您的資料」，直接說「你的 CTL 是 52」這樣的語氣。
  如果資料不足以回答，請說明缺少什麼資料。"""

  _TSB_STATE = {
      "peak_form":    "峰值狀態，適合比賽或測驗",
      "optimal":      "最佳訓練狀態",
      "training":     "正常訓練負荷",
      "tired":        "輕度疲勞，建議降量或恢復",
      "overreached":  "過度訓練警示，建議休息",
  }


  def _tsb_state(tsb: Optional[float]) -> str:
      if tsb is None:
          return "unknown"
      if tsb > 25:
          return "peak_form"
      if tsb > 5:
          return "optimal"
      if tsb > -10:
          return "training"
      if tsb > -25:
          return "tired"
      return "overreached"


  async def build_context(
      db: AsyncSession,
      athlete_id: int,
      workout_id: Optional[int] = None,
  ) -> str:
      today = date.today()
      lines = [_SYSTEM_BASE, "", "---", f"【運動員資料 — {today}】", ""]

      # 1. Athlete settings
      sq = await db.execute(
          select(AthleteSettings)
          .where(AthleteSettings.athlete_id == athlete_id)
          .order_by(AthleteSettings.effective_date.desc())
      )
      s = sq.scalars().first()
      if s:
          ftp_str = f"FTP {s.ftp_w}W" if s.ftp_w else "FTP 未設定"
          pace_str = f"閾值配速 {_fmt_pace(s.threshold_pace_s_per_km)}" if s.threshold_pace_s_per_km else ""
          lthr_str = f"LTHR {s.lthr}bpm" if s.lthr else ""
          wt_str = f"體重 {s.weight_kg}kg" if s.weight_kg else ""
          lines.append("基本設定：" + " | ".join(x for x in [ftp_str, pace_str, lthr_str, wt_str] if x))
          lines.append("")

      # 2. PMC snapshot (today)
      pq = await db.execute(
          select(PmcCache)
          .where(PmcCache.athlete_id == athlete_id, PmcCache.date <= today)
          .order_by(PmcCache.date.desc())
      )
      pmc = pq.scalars().first()
      if pmc:
          state = _tsb_state(pmc.tsb)
          lines.append(f"體能狀態 (PMC)：")
          lines.append(f"  CTL(體能) {pmc.ctl:.1f} | ATL(疲勞) {pmc.atl:.1f} | TSB(狀態) {pmc.tsb:.1f}")
          lines.append(f"  狀態判讀：{_TSB_STATE.get(state, '未知')}")
          lines.append("")

      # 3. Recent 10 workouts
      wq = await db.execute(
          select(WorkoutFile)
          .where(WorkoutFile.athlete_id == athlete_id, WorkoutFile.workout_date.isnot(None))
          .order_by(WorkoutFile.workout_date.desc())
          .limit(10)
      )
      workouts = wq.scalars().all()
      if workouts:
          lines.append("最近 10 次訓練：")
          for w in workouts:
              metrics = await _get_metrics(db, w.id)
              tss = metrics.get("tss")
              np_w = metrics.get("normalized_power_w") or metrics.get("avg_power_w")
              tss_str = f"TSS:{tss:.0f}" if tss else "TSS:-"
              np_str = f"NP:{np_w:.0f}W" if np_w else ""
              dur_str = _fmt_duration(w.duration_s)
              lines.append(f"  {w.workout_date} {w.sport or '?'} {dur_str} {tss_str} {np_str}".rstrip())
          lines.append("")

      # 4. Specific workout context
      if workout_id:
          wres = await db.execute(select(WorkoutFile).where(WorkoutFile.id == workout_id))
          w = wres.scalar_one_or_none()
          if w:
              metrics = await _get_metrics(db, w.id)
              tss = metrics.get("tss")
              np_w = metrics.get("normalized_power_w")
              lines.append(f"本次訓練 #{workout_id}：")
              lines.append(f"  {w.workout_date} {w.sport} {_fmt_duration(w.duration_s)}")
              if tss:
                  lines.append(f"  TSS:{tss:.0f}" + (f" NP:{np_w:.0f}W" if np_w else ""))
              if w.elevation_gain_m and w.elevation_gain_m > 100:
                  lines.append(f"  爬升:{w.elevation_gain_m:.0f}m（越野跑模式）")
              lines.append("")

      lines.append("---")
      return "\n".join(lines)


  async def _get_metrics(db: AsyncSession, workout_id: int) -> dict:
      res = await db.execute(
          select(WorkoutMetric).where(WorkoutMetric.workout_id == workout_id)
      )
      return {m.metric_key: m.value for m in res.scalars().all()}


  def _fmt_pace(s_per_km: Optional[float]) -> str:
      if not s_per_km:
          return ""
      m, s = divmod(int(s_per_km), 60)
      return f"{m}:{s:02d}/km"


  def _fmt_duration(seconds: Optional[float]) -> str:
      if not seconds:
          return ""
      h, rem = divmod(int(seconds), 3600)
      m = rem // 60
      return f"{h}h{m:02d}m" if h else f"{m}m"
  ```
- **MIRROR**: `backend/api/analytics.py` 的 DB 查詢模式（`await db.execute(select(...))` + `.scalars().first()`）
- **GOTCHA**: `PmcCache` model 在 `backend/db/models.py` 已存在但沒有 relationship；直接查即可。`_get_metrics` 每次查一次 DB，10 個訓練就 10 次查詢 — 可接受（本地 SQLite，< 10ms each）
- **VALIDATE**: `python -c "print('import ok')"` from 專案根。實際測試在 Task 8 的 API 整合後進行

---

### Task 7: `backend/api/ai.py` — AI Chat + Status endpoints

- **ACTION**: 建立 AI router，`POST /ai/chat` 以 SSE 串流回應，`GET /ai/status` 回傳設定狀態
- **IMPLEMENT** — 建立 `backend/api/ai.py`：
  ```python
  """AI coach chat endpoint."""
  import json
  from typing import Optional

  from fastapi import APIRouter, Depends
  from pydantic import BaseModel
  from sse_starlette.sse import EventSourceResponse
  from sqlalchemy.ext.asyncio import AsyncSession
  from sqlalchemy import select

  from backend.db.database import get_db
  from backend.db.models import AthleteSettings
  from backend.engine.ai.client import get_ai_client
  from backend.engine.ai.context import build_context

  router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


  class ChatMessage(BaseModel):
      role: str   # "user" | "assistant"
      content: str


  class ChatRequest(BaseModel):
      athlete_id: int = 1
      messages: list[ChatMessage]
      workout_id: Optional[int] = None


  async def _get_settings(db: AsyncSession, athlete_id: int) -> Optional[AthleteSettings]:
      res = await db.execute(
          select(AthleteSettings)
          .where(AthleteSettings.athlete_id == athlete_id)
          .order_by(AthleteSettings.effective_date.desc())
      )
      return res.scalars().first()


  @router.post("/chat")
  async def chat(body: ChatRequest, db: AsyncSession = Depends(get_db)):
      async def generate():
          settings = await _get_settings(db, body.athlete_id)
          client = get_ai_client(settings)
          if not client:
              yield {"data": json.dumps({"error": "NO_API_KEY", "message": "請先在設定頁填入 API Key"})}
              return

          system = await build_context(db, body.athlete_id, body.workout_id)
          messages = [{"role": m.role, "content": m.content} for m in body.messages]

          try:
              async for token in client.stream(messages, system):
                  yield {"data": json.dumps({"token": token})}
              yield {"data": json.dumps({"done": True})}
          except Exception as e:
              yield {"data": json.dumps({"error": "API_ERROR", "message": str(e)[:200]})}

      return EventSourceResponse(generate())


  @router.get("/status")
  async def status(athlete_id: int = 1, db: AsyncSession = Depends(get_db)):
      settings = await _get_settings(db, athlete_id)
      if not settings or not settings.ai_api_key or not settings.ai_provider:
          return {"configured": False, "provider": None, "model": None}
      return {
          "configured": True,
          "provider": settings.ai_provider,
          "model": settings.ai_model,
      }
  ```
- **MIRROR**: `backend/api/sync.py:14-32` — `EventSourceResponse(generate())` + async generator yield `{"data": json.dumps(...)}`
- **GOTCHA**: SSE response 永遠回傳 HTTP 200，error 透過 `{"error": "..."}` payload 傳遞，不用 `HTTPException`（因為 response 已開始串流）
- **VALIDATE**: 下一步註冊 router 後測試

---

### Task 8: 在 `backend/main.py` 註冊 ai router

- **ACTION**: import `ai` router 並 `include_router`
- **IMPLEMENT** — `backend/main.py`：
  ```python
  # 在 from backend.api import ... 這行加入 ai：
  from backend.api import workouts, pmc, expr, dashboard, scan, sync, auth, athletes, analytics, ai

  # 在 app.include_router(analytics.router) 後加入：
  app.include_router(ai.router)
  ```
- **MIRROR**: `backend/main.py:12-21` — 現有 router 註冊模式
- **VALIDATE**: `uvicorn backend.main:app --reload` 啟動後：
  ```bash
  curl http://localhost:8000/api/v1/ai/status
  # expect: {"configured":false,"provider":null,"model":null}
  ```

---

### Task 9: Trail endpoint — `GET /workouts/{id}/trail`

- **ACTION**: 在 `backend/api/workouts.py` 新增 trail endpoint，從 FIT 計算 GAP/VAM/HR drift
- **IMPLEMENT** — 在 `backend/api/workouts.py` 的 import 區塊加入：
  ```python
  from backend.engine.algorithms.trail import (
      compute_grade, compute_gap, segment_climbs, compute_hr_drift
  )
  ```
  在最後一個 `@router.get` 後加入：
  ```python
  @router.get("/{workout_id}/trail")
  async def get_workout_trail(workout_id: int, db: AsyncSession = Depends(get_db)):
      w = await _get_workout_or_404(workout_id, db)
      if w.file_format != "fit":
          raise HTTPException(422, "NO_FIT_FILE")
      try:
          raw = parse_fit(w.file_path)
      except Exception as e:
          raise HTTPException(422, f"PARSE_ERROR: {e}")

      if len(raw.altitude_m) < 10 or not np.any(raw.altitude_m > 0):
          raise HTTPException(422, "NO_ALTITUDE_DATA")

      total_gain = float(np.sum(np.diff(raw.altitude_m).clip(min=0)))
      is_trail = total_gain > 100.0

      # Compute derived channels
      grade = compute_grade(raw.altitude_m, raw.distance_m)
      with np.errstate(divide='ignore', invalid='ignore'):
          speed = raw.speed_ms
          pace = np.where(speed > 0.1, 1000.0 / speed, 1800.0)
      gap = compute_gap(pace, grade)

      # Downsample timeseries (≤ 1800 points)
      n = len(raw.time_s)
      step = max(1, n // 1800)
      series = []
      for i in range(0, n, step):
          pt: dict = {"t": int(raw.time_s[i])}
          pt["dist_m"] = round(float(raw.distance_m[i]), 1)
          pt["altitude"] = round(float(raw.altitude_m[i]), 1)
          pt["pace"] = round(float(pace[i]), 1)
          pt["gap"] = round(float(gap[i]), 1)
          pt["grade"] = round(float(grade[i]), 1)
          if i < len(raw.heart_rate_bpm) and raw.heart_rate_bpm[i] > 0:
              pt["hr"] = round(float(raw.heart_rate_bpm[i]))
          if i < len(raw.cadence_rpm) and raw.cadence_rpm[i] > 0:
              pt["cadence"] = round(float(raw.cadence_rpm[i]))
          series.append(pt)

      # Climb segments
      hr = raw.heart_rate_bpm if raw.has_hr else None
      cad = raw.cadence_rpm if raw.has_cadence else None
      climbs = segment_climbs(raw.altitude_m, raw.distance_m, raw.time_s, grade, hr, cad)

      # HR drift
      hr_drift = compute_hr_drift(
          raw.heart_rate_bpm if raw.has_hr else np.array([]),
          gap,
          raw.time_s,
      )

      # GAP TSS (if threshold pace set)
      settings_q = await db.execute(
          select(AthleteSettings)
          .where(AthleteSettings.athlete_id == w.athlete_id)
          .order_by(AthleteSettings.effective_date.desc())
      )
      settings = settings_q.scalars().first()
      gap_tss = None
      if settings and settings.threshold_pace_s_per_km:
          tp = settings.threshold_pace_s_per_km
          intensity = tp / gap  # > 1 = above threshold
          gap_tss = round(float(np.sum(intensity ** 2) / (tp * 3600) * 100), 1)

      return {
          "workout_id": workout_id,
          "is_trail": is_trail,
          "total_elevation_gain_m": round(total_gain, 1),
          "gap_tss": gap_tss,
          "timeseries": series,
          "climb_segments": [
              {
                  "start_km": c.start_km, "end_km": c.end_km,
                  "elevation_gain_m": c.elevation_gain_m,
                  "vam_m_per_hr": c.vam_m_per_hr,
                  "avg_hr": c.avg_hr, "avg_cadence": c.avg_cadence,
                  "is_hiking": c.is_hiking,
              }
              for c in climbs
          ],
          "hr_drift": hr_drift,
      }
  ```
  在 `workouts.py` import 區加入 `import numpy as np` 和 `from backend.db.models import ... AthleteSettings`（若未import）
- **MIRROR**: `backend/api/workouts.py:get_workout_timeseries()` — FIT 讀取 + 降採樣 + 422 error pattern
- **GOTCHA**: `raw.altitude_m` 在平路 FIT 可能全為 0 或 NaN；`np.any(raw.altitude_m > 0)` 確認有真實高度資料才繼續
- **VALIDATE**:
  ```bash
  curl http://localhost:8000/api/v1/workouts/1/trail
  # 若無 FIT 或平路：{"detail":"NO_ALTITUDE_DATA"} 或正常 JSON
  ```

---

### Task 10: Dashboard Summary endpoint

- **ACTION**: 在 `backend/api/analytics.py` 新增 `GET /analytics/dashboard-summary`
- **IMPLEMENT** — 在 `backend/api/analytics.py` 尾端加入：
  ```python
  @router.get("/dashboard-summary")
  async def dashboard_summary(
      athlete_id: int = 1,
      db: AsyncSession = Depends(get_db),
  ):
      from datetime import date, timedelta
      today = date.today()
      week_start = today - timedelta(days=today.weekday())  # Monday

      # PMC today
      pmc_q = await db.execute(
          select(PmcCache)
          .where(PmcCache.athlete_id == athlete_id, PmcCache.date <= today)
          .order_by(PmcCache.date.desc())
      )
      pmc = pmc_q.scalars().first()

      # PMC 7 days ago for trend
      pmc7_q = await db.execute(
          select(PmcCache)
          .where(PmcCache.athlete_id == athlete_id, PmcCache.date <= today - timedelta(days=7))
          .order_by(PmcCache.date.desc())
      )
      pmc7 = pmc7_q.scalars().first()

      tsb = pmc.tsb if pmc else None
      ctl = pmc.ctl if pmc else None
      atl = pmc.atl if pmc else None
      ctl7 = pmc7.ctl if pmc7 else None

      def _tsb_state(tsb):
          if tsb is None: return "unknown"
          if tsb > 25: return "peak_form"
          if tsb > 5: return "optimal"
          if tsb > -10: return "training"
          if tsb > -25: return "tired"
          return "overreached"

      ctl_trend = "stable"
      if ctl and ctl7:
          diff = ctl - ctl7
          if diff > 1.5: ctl_trend = "rising"
          elif diff < -1.5: ctl_trend = "falling"

      # This week TSS + hours
      tss_subq = (
          select(func.coalesce(func.sum(WorkoutMetric.value), 0))
          .where(WorkoutMetric.workout_id == WorkoutFile.id)
          .where(WorkoutMetric.metric_key == "tss")
          .correlate(WorkoutFile)
          .scalar_subquery()
      )
      wk_q = await db.execute(
          select(
              func.sum(tss_subq).label("tss"),
              (func.sum(WorkoutFile.duration_s) / 3600.0).label("hours"),
          )
          .where(
              WorkoutFile.athlete_id == athlete_id,
              WorkoutFile.workout_date >= week_start,
              WorkoutFile.workout_date <= today,
          )
      )
      wk = wk_q.first()

      # Last workout
      last_q = await db.execute(
          select(WorkoutFile)
          .where(WorkoutFile.athlete_id == athlete_id, WorkoutFile.workout_date.isnot(None))
          .order_by(WorkoutFile.workout_date.desc())
          .limit(1)
      )
      last_w = last_q.scalar_one_or_none()
      last_metrics = {}
      if last_w:
          lm_q = await db.execute(
              select(WorkoutMetric)
              .where(WorkoutMetric.workout_id == last_w.id, WorkoutMetric.metric_key == "tss")
          )
          lm = lm_q.scalar_one_or_none()
          last_metrics = {"tss": lm.value if lm else None}

      return {
          "today_tsb": round(tsb, 1) if tsb is not None else None,
          "tsb_state": _tsb_state(tsb),
          "ctl": round(ctl, 1) if ctl else None,
          "atl": round(atl, 1) if atl else None,
          "ctl_trend": ctl_trend,
          "this_week_tss": round(float(wk.tss or 0)),
          "this_week_hours": round(float(wk.hours or 0), 1),
          "last_workout": {
              "date": last_w.workout_date.isoformat(),
              "sport": last_w.sport,
              "tss": round(last_metrics["tss"], 0) if last_metrics.get("tss") else None,
              "duration_s": last_w.duration_s,
          } if last_w else None,
      }
  ```
  在 `analytics.py` import 確認有 `PmcCache`（`from backend.db.models import WorkoutFile, WorkoutMetric, AthleteSettings, PmcCache`）
- **VALIDATE**: `curl http://localhost:8000/api/v1/analytics/dashboard-summary` — 回傳 JSON 含 `today_tsb`, `tsb_state`

---

### Task 11: 前端型別定義 — `client.ts`

- **ACTION**: 在 `frontend/src/api/client.ts` 新增 Trail、AI、DashboardSummary 型別
- **IMPLEMENT** — 在 `client.ts` 現有型別後加入：
  ```typescript
  // --- AI ---
  export interface AiStatus {
    configured: boolean
    provider: string | null
    model: string | null
  }

  // --- Trail ---
  export interface TrailPoint {
    t: number
    dist_m: number
    altitude: number
    pace: number
    gap: number
    grade: number
    hr?: number
    cadence?: number
  }

  export interface ClimbSegment {
    start_km: number
    end_km: number
    elevation_gain_m: number
    vam_m_per_hr: number
    avg_hr: number | null
    avg_cadence: number | null
    is_hiking: boolean
  }

  export interface HrDrift {
    first_half_ratio: number
    second_half_ratio: number
    drift_pct: number
  }

  export interface TrailResponse {
    workout_id: number
    is_trail: boolean
    total_elevation_gain_m: number
    gap_tss: number | null
    timeseries: TrailPoint[]
    climb_segments: ClimbSegment[]
    hr_drift: HrDrift | null
  }

  // --- Dashboard Summary ---
  export interface DashboardSummary {
    today_tsb: number | null
    tsb_state: 'peak_form' | 'optimal' | 'training' | 'tired' | 'overreached' | 'unknown'
    ctl: number | null
    atl: number | null
    ctl_trend: 'rising' | 'stable' | 'falling'
    this_week_tss: number
    this_week_hours: number
    last_workout: {
      date: string
      sport: string
      tss: number | null
      duration_s: number
    } | null
  }

  // --- Settings (擴充) ---
  export interface SettingsUpdatePayload {
    ftp_w?: number
    lthr?: number
    weight_kg?: number
    threshold_pace_s_per_km?: number
    run_ftp_w?: number
    initial_ctl_run?: number
    initial_atl_run?: number
    ai_provider?: string
    ai_api_key?: string
    ai_model?: string
  }

  export interface AthleteSettingsResponse {
    athlete_id: number
    effective_date: string
    ftp_w: number | null
    run_ftp_w: number | null
    lthr: number | null
    weight_kg: number | null
    threshold_pace_s_per_km: number | null
    initial_ctl_run: number | null
    initial_atl_run: number | null
    ai_provider: string | null
    ai_model: string | null
    power_zones: PowerZone[]
    hr_zones: HrZone[]
  }
  ```
- **MIRROR**: `frontend/src/api/client.ts` — `export interface PmcPoint { ... }` 格式
- **VALIDATE**: `cd frontend && npx tsc --noEmit` — 無型別錯誤

---

### Task 12: 前端 hooks — `hooks.ts`

- **ACTION**: 在 `frontend/src/api/hooks.ts` 新增 `useAiStatus`, `useTrailAnalysis`, `useDashboardSummary`
- **IMPLEMENT** — 在 `hooks.ts` 尾端加入：
  ```typescript
  import type { AiStatus, TrailResponse, DashboardSummary } from './client'

  export function useAiStatus(athleteId = 1) {
    return useQuery<AiStatus>({
      queryKey: ['ai_status', athleteId],
      queryFn: () => api.get(`/ai/status?athlete_id=${athleteId}`).then(r => r.data),
      staleTime: 30_000,
    })
  }

  export function useTrailAnalysis(workoutId: number | null) {
    return useQuery<TrailResponse>({
      queryKey: ['trail', workoutId],
      queryFn: () => api.get(`/workouts/${workoutId}/trail`).then(r => r.data),
      enabled: !!workoutId,
      staleTime: Infinity,
      retry: false,  // 422 不重試
    })
  }

  export function useDashboardSummary(athleteId = 1) {
    return useQuery<DashboardSummary>({
      queryKey: ['dashboard_summary', athleteId],
      queryFn: () => api.get(`/analytics/dashboard-summary?athlete_id=${athleteId}`).then(r => r.data),
      staleTime: 60_000,
    })
  }
  ```
- **MIRROR**: `frontend/src/api/hooks.ts:useRunLoad()` — `useQuery` + `staleTime`
- **VALIDATE**: `npx tsc --noEmit` in frontend/

---

### Task 13: `frontend/src/store/aiChatStore.ts`

- **ACTION**: 建立 zustand chat store，管理 messages 和 streaming 狀態
- **IMPLEMENT** — 建立 `frontend/src/store/aiChatStore.ts`：
  ```typescript
  import { create } from 'zustand'

  export interface ChatMessage {
    role: 'user' | 'assistant'
    content: string
  }

  interface AiChatStore {
    messages: ChatMessage[]
    isStreaming: boolean
    currentWorkoutId: number | null
    addMessage: (msg: ChatMessage) => void
    setStreaming: (v: boolean) => void
    appendToLast: (token: string) => void
    setCurrentWorkoutId: (id: number | null) => void
    clear: () => void
  }

  export const useAiChatStore = create<AiChatStore>((set) => ({
    messages: [],
    isStreaming: false,
    currentWorkoutId: null,
    addMessage: (msg) => set((s) => ({ messages: [...s.messages, msg] })),
    setStreaming: (v) => set({ isStreaming: v }),
    appendToLast: (token) =>
      set((s) => {
        const msgs = [...s.messages]
        if (msgs.length === 0 || msgs[msgs.length - 1].role !== 'assistant') {
          msgs.push({ role: 'assistant', content: token })
        } else {
          msgs[msgs.length - 1] = {
            ...msgs[msgs.length - 1],
            content: msgs[msgs.length - 1].content + token,
          }
        }
        return { messages: msgs }
      }),
    setCurrentWorkoutId: (id) => set({ currentWorkoutId: id }),
    clear: () => set({ messages: [], isStreaming: false }),
  }))
  ```
- **MIRROR**: `frontend/src/store/tabStore.ts` — `create<Store>((set) => ({...}))` pattern
- **VALIDATE**: `npx tsc --noEmit`

---

### Task 14: ConfigTab — AI 設定區塊

- **ACTION**: 在 `frontend/src/tabs/ConfigTab.tsx` 現有設定表單下方加入 AI Provider / Model / Key 設定 UI
- **IMPLEMENT** — 在 `ConfigTab.tsx` 的 import 區加入：
  ```typescript
  import { CLAUDE_MODELS, OPENAI_MODELS } from '../engine/ai/models'
  ```
  建立 `frontend/src/engine/ai/models.ts`（新檔）：
  ```typescript
  export const CLAUDE_MODELS = [
    { value: 'claude-haiku-4-5-20251001', label: 'Haiku — 最快/最省費' },
    { value: 'claude-sonnet-4-6',          label: 'Sonnet — 均衡（推薦）' },
    { value: 'claude-opus-4-7',            label: 'Opus — 最強分析力' },
  ]
  export const OPENAI_MODELS = [
    { value: 'gpt-4o-mini', label: 'GPT-4o mini — 最快/最省費' },
    { value: 'gpt-4o',      label: 'GPT-4o — 均衡' },
    { value: 'gpt-4.1',     label: 'GPT-4.1 — 最新' },
  ]
  ```
  在 `ConfigTab.tsx` 內，在現有 form state 後加入：
  ```typescript
  const [aiProvider, setAiProvider] = useState(settings?.ai_provider ?? 'claude')
  const [aiModel, setAiModel] = useState(settings?.ai_model ?? 'claude-sonnet-4-6')
  const [aiKey, setAiKey] = useState('')
  const [aiSaved, setAiSaved] = useState(false)

  const models = aiProvider === 'openai' ? OPENAI_MODELS : CLAUDE_MODELS

  const handleAiSave = (e: React.FormEvent) => {
    e.preventDefault()
    const payload: Record<string, string> = { ai_provider: aiProvider, ai_model: aiModel }
    if (aiKey) payload.ai_api_key = aiKey
    update.mutate(payload as any, {
      onSuccess: () => { setAiSaved(true); setAiKey(''); setTimeout(() => setAiSaved(false), 2000) }
    })
  }
  ```
  在現有 JSX 最後（`</div>` 前）加入 AI 設定 section：
  ```tsx
  <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
    <h2 className="text-sm font-semibold text-gray-400 mb-3">AI 教練設定</h2>
    <form onSubmit={handleAiSave} className="space-y-3">
      <div className="flex flex-col gap-1">
        <label className="text-xs text-gray-500">AI Provider</label>
        <select
          value={aiProvider}
          onChange={e => { setAiProvider(e.target.value); setAiModel('') }}
          className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-48"
        >
          <option value="claude">Claude (Anthropic)</option>
          <option value="openai">OpenAI</option>
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-gray-500">Model</label>
        <select
          value={aiModel}
          onChange={e => setAiModel(e.target.value)}
          className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-64"
        >
          {models.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label className="text-xs text-gray-500">
          API Key {settings?.ai_provider ? <span className="text-green-500 ml-1">（已設定）</span> : <span className="text-gray-600 ml-1">（未設定）</span>}
        </label>
        <input
          type="password"
          autoComplete="off"
          value={aiKey}
          onChange={e => setAiKey(e.target.value)}
          placeholder="貼上新 API Key（留空則不更新）"
          className="px-2 py-1 text-sm bg-gray-800 border border-gray-700 rounded text-gray-100 w-72"
        />
      </div>
      <button
        type="submit"
        disabled={update.isPending}
        className="px-4 py-1.5 text-sm bg-purple-700 hover:bg-purple-600 rounded transition disabled:opacity-50"
      >
        {update.isPending ? '儲存中...' : aiSaved ? '✓ 已儲存' : '儲存 AI 設定'}
      </button>
    </form>
  </div>
  ```
- **MIRROR**: `frontend/src/tabs/ConfigTab.tsx:handleSubmit()` + input className 全部照抄
- **GOTCHA**: `ai_api_key` 只在 `aiKey` 非空時才送（避免覆蓋掉已設定的 key）
- **VALIDATE**: 瀏覽器開啟 Config tab → 看到 AI 設定區塊，選 Provider/Model，輸入 key，儲存後 API Key 欄位顯示「已設定」

---

### Task 15: `frontend/src/components/AiChat.tsx`

- **ACTION**: 建立完整 AI 對話元件，含快捷問題、訊息列表、SSE 串流輸入
- **IMPLEMENT** — 建立 `frontend/src/components/AiChat.tsx`：
  ```typescript
  import { useRef, useEffect, useState } from 'react'
  import { useAiChatStore } from '../store/aiChatStore'
  import { useAiStatus } from '../api/hooks'
  import { Card, CardContent } from './ui/card'

  const SHORTCUTS = [
    '本週訓練狀態如何？',
    '昨天的訓練練得怎樣？',
    '今天適合高強度訓練嗎？',
    '最近 CTL 趨勢如何？',
    '幫我分析越野跑訓練效率',
  ]

  export function AiChat({ workoutId }: { workoutId?: number }) {
    const { messages, isStreaming, addMessage, setStreaming, appendToLast, setCurrentWorkoutId } =
      useAiChatStore()
    const { data: status } = useAiStatus()
    const [input, setInput] = useState('')
    const bottomRef = useRef<HTMLDivElement>(null)

    useEffect(() => {
      if (workoutId !== undefined) setCurrentWorkoutId(workoutId)
    }, [workoutId])

    useEffect(() => {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, [messages])

    const send = async (text: string) => {
      if (!text.trim() || isStreaming) return
      const userMsg = { role: 'user' as const, content: text.trim() }
      addMessage(userMsg)
      setInput('')
      setStreaming(true)

      try {
        const response = await fetch('/api/v1/ai/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            athlete_id: 1,
            messages: [...messages, userMsg],
            workout_id: workoutId ?? null,
          }),
        })
        const reader = response.body!.getReader()
        const decoder = new TextDecoder()
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          const chunk = decoder.decode(value)
          for (const line of chunk.split('\n')) {
            if (!line.startsWith('data: ')) continue
            try {
              const evt = JSON.parse(line.slice(6))
              if (evt.done) break
              if (evt.error) {
                addMessage({ role: 'assistant', content: `⚠️ ${evt.message}` })
                break
              }
              if (evt.token) appendToLast(evt.token)
            } catch { /* skip malformed */ }
          }
        }
      } catch (e) {
        addMessage({ role: 'assistant', content: '⚠️ 連線錯誤，請確認後端是否正在執行' })
      } finally {
        setStreaming(false)
      }
    }

    const handleKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        send(input)
      }
    }

    return (
      <div className="flex flex-col h-full max-w-3xl mx-auto p-4 space-y-3">
        {/* Status bar */}
        <div className="flex items-center gap-2 text-xs text-gray-500">
          {status?.configured ? (
            <span className="text-green-500">● 已連線 ({status.provider} / {status.model})</span>
          ) : (
            <span className="text-yellow-500">● 未設定 API Key — 請前往設定頁</span>
          )}
        </div>

        {/* Shortcuts */}
        {messages.length === 0 && (
          <div className="flex flex-wrap gap-2">
            {SHORTCUTS.map(q => (
              <button
                key={q}
                onClick={() => send(q)}
                disabled={isStreaming}
                className="px-3 py-1 text-xs bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded-full text-gray-300 transition disabled:opacity-50"
              >
                {q}
              </button>
            ))}
          </div>
        )}

        {/* Messages */}
        <div className="flex-1 overflow-y-auto space-y-3 min-h-0" style={{ maxHeight: 'calc(100vh - 280px)' }}>
          {messages.map((msg, i) => (
            <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[80%] px-4 py-2 rounded-lg text-sm whitespace-pre-wrap ${
                  msg.role === 'user'
                    ? 'bg-purple-700 text-white'
                    : 'bg-gray-800 text-gray-100'
                }`}
              >
                {msg.content}
                {isStreaming && i === messages.length - 1 && msg.role === 'assistant' && (
                  <span className="inline-block w-1 h-4 bg-gray-400 ml-1 animate-pulse" />
                )}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="flex gap-2">
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={handleKey}
            disabled={isStreaming}
            rows={2}
            placeholder="輸入問題… (Enter 送出, Shift+Enter 換行)"
            className="flex-1 px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-100 resize-none focus:outline-none focus:border-purple-600 disabled:opacity-50"
          />
          <button
            onClick={() => send(input)}
            disabled={isStreaming || !input.trim()}
            className="px-4 py-2 bg-purple-700 hover:bg-purple-600 rounded-lg text-sm text-white transition disabled:opacity-50"
          >
            {isStreaming ? '…' : '送出'}
          </button>
        </div>
      </div>
    )
  }
  ```
- **MIRROR**: `frontend/src/pages/Dashboard.tsx:handleSync()` — fetch + ReadableStream + `line.startsWith('data: ')` + `JSON.parse(line.slice(6))`
- **GOTCHA**: `appendToLast` 在 messages 為空時會先 `push` 一條 assistant 訊息，所以第一個 token 也能正確顯示。`maxHeight: calc(100vh - 280px)` 避免頁面整體捲動
- **VALIDATE**: 開啟 AI tab → 看到快捷問題按鈕 + 輸入框；若 key 已設定可測試實際對話

---

### Task 16: `frontend/src/components/charts/SmartDashboardSection.tsx`

- **ACTION**: 建立 Smart Dashboard Section，5 張 MetricCard + AI 教練入口
- **IMPLEMENT** — 建立 `frontend/src/components/charts/SmartDashboardSection.tsx`：
  ```typescript
  import { useDashboardSummary } from '../../api/hooks'
  import { MetricCard } from '../MetricCard'
  import { useTabStore } from '../../store/tabStore'

  const TSB_LABEL: Record<string, string> = {
    peak_form:   '峰值狀態 🏆',
    optimal:     '最佳訓練狀態',
    training:    '正常訓練負荷',
    tired:       '輕度疲勞',
    overreached: '⚠️ 過度訓練',
    unknown:     '—',
  }

  const CTL_ARROW: Record<string, string> = {
    rising: '↑',
    stable: '→',
    falling: '↓',
  }

  function fmtDuration(s?: number | null) {
    if (!s) return '—'
    const h = Math.floor(s / 3600)
    const m = Math.floor((s % 3600) / 60)
    return h > 0 ? `${h}h${m.toString().padStart(2,'0')}m` : `${m}m`
  }

  export function SmartDashboardSection() {
    const { data, isLoading } = useDashboardSummary()
    const setTab = useTabStore(s => s.setTab)

    if (isLoading) return (
      <div className="h-24 flex items-center justify-center text-gray-600 text-sm">載入中...</div>
    )
    if (!data) return null

    return (
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold text-gray-400">今日狀態</h2>
          <button
            onClick={() => setTab('ai')}
            className="text-xs text-purple-400 hover:text-purple-300 transition"
          >
            問 AI 教練 →
          </button>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
          <MetricCard
            label="狀態 (TSB)"
            value={data.today_tsb !== null ? data.today_tsb.toFixed(1) : '—'}
            unit={TSB_LABEL[data.tsb_state] ?? ''}
          />
          <MetricCard
            label={`CTL ${CTL_ARROW[data.ctl_trend] ?? ''}`}
            value={data.ctl !== null ? data.ctl.toFixed(1) : '—'}
            accent
          />
          <MetricCard
            label="本週 TSS"
            value={data.this_week_tss}
          />
          <MetricCard
            label="本週時數"
            value={data.this_week_hours.toFixed(1)}
            unit="h"
          />
          <MetricCard
            label={data.last_workout ? `${data.last_workout.date} ${data.last_workout.sport}` : '最近訓練'}
            value={data.last_workout?.tss ? `TSS ${data.last_workout.tss}` : fmtDuration(data.last_workout?.duration_s)}
          />
        </div>
      </div>
    )
  }
  ```
- **MIRROR**: `frontend/src/components/MetricCard.tsx` 用法 + `frontend/src/store/tabStore.ts` `useTabStore`
- **VALIDATE**: SeasonTab 頂部插入後在瀏覽器確認 5 張卡片正常顯示

---

### Task 17: `frontend/src/components/charts/TrailAnalysisSection.tsx`

- **ACTION**: 建立越野跑分析 Section，4 個子圖表
- **IMPLEMENT** — 建立 `frontend/src/components/charts/TrailAnalysisSection.tsx`：
  ```typescript
  import {
    ComposedChart, Line, Area, XAxis, YAxis, CartesianGrid,
    Tooltip, Legend, ResponsiveContainer, Scatter, ScatterChart, ZAxis,
  } from 'recharts'
  import { Card, CardHeader, CardTitle, CardContent } from '../ui/card'
  import { BASE_GRID_PROPS, BASE_TOOLTIP_STYLE, CHART_COLORS } from '../../lib/chartTheme'
  import type { TrailResponse } from '../../api/client'

  const AXIS = { tick: { fontSize: 11, fill: '#3e4e63' }, tickLine: false, axisLine: false } as const

  function fmtPace(s: number) {
    const m = Math.floor(s / 60); const sec = Math.round(s % 60)
    return `${m}:${sec.toString().padStart(2, '0')}`
  }

  // Chart 1: Elevation + Pace + GAP
  function ElevationPaceChart({ data }: { data: TrailResponse['timeseries'] }) {
    const pts = data.map(p => ({ ...p, dist_km: +(p.dist_m / 1000).toFixed(2) }))
    return (
      <Card>
        <CardHeader><CardTitle>海拔 + 配速 + GAP</CardTitle></CardHeader>
        <CardContent className="pt-0">
          <ResponsiveContainer width="100%" height={260}>
            <ComposedChart data={pts} margin={{ top: 4, right: 40, left: -12, bottom: 0 }}>
              <CartesianGrid {...BASE_GRID_PROPS} />
              <XAxis dataKey="dist_km" {...AXIS} unit="km" />
              <YAxis yAxisId="alt" {...AXIS} domain={['auto', 'auto']} unit="m" />
              <YAxis yAxisId="pace" orientation="right" {...AXIS} tickFormatter={fmtPace} reversed />
              <Tooltip
                {...BASE_TOOLTIP_STYLE}
                formatter={(v: number, name: string) => {
                  if (name === '海拔') return [`${v}m`, name]
                  if (name === '配速' || name === 'GAP') return [fmtPace(v), name]
                  return [v, name]
                }}
              />
              <Legend wrapperStyle={{ fontSize: 11, color: '#7d8fa6' }} />
              <Area yAxisId="alt" dataKey="altitude" name="海拔" fill="#1c2333" stroke="#374151" dot={false} isAnimationActive={false} />
              <Line yAxisId="pace" dataKey="pace" name="配速" stroke={CHART_COLORS.pace} dot={false} strokeWidth={1.5} isAnimationActive={false} />
              <Line yAxisId="pace" dataKey="gap" name="GAP" stroke="#f59e0b" dot={false} strokeWidth={1.5} strokeDasharray="4 2" isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>
    )
  }

  // Chart 2: Grade vs Cadence scatter
  function GradeCadenceChart({ data }: { data: TrailResponse['timeseries'] }) {
    const pts = data.filter(p => p.cadence && p.cadence > 0).map(p => ({ grade: p.grade, cadence: p.cadence }))
    return (
      <Card>
        <CardHeader><CardTitle>坡度 vs 踏頻</CardTitle></CardHeader>
        <CardContent className="pt-0">
          <ResponsiveContainer width="100%" height={220}>
            <ScatterChart margin={{ top: 4, right: 20, left: -12, bottom: 0 }}>
              <CartesianGrid {...BASE_GRID_PROPS} />
              <XAxis dataKey="grade" {...AXIS} unit="%" name="坡度" domain={[-20, 30]} />
              <YAxis dataKey="cadence" {...AXIS} unit="spm" name="踏頻" />
              <ZAxis range={[8, 8]} />
              <Tooltip
                {...BASE_TOOLTIP_STYLE}
                formatter={(v: number, name: string) => [name === '坡度' ? `${v}%` : `${v}spm`, name]}
              />
              <Scatter data={pts} fill={CHART_COLORS.cadence} fillOpacity={0.4} />
            </ScatterChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>
    )
  }

  // Chart 3: VAM segments table
  function VamSegmentsTable({ segments }: { segments: TrailResponse['climb_segments'] }) {
    if (segments.length === 0) return null
    return (
      <Card>
        <CardHeader><CardTitle>爬坡分段（VAM）</CardTitle></CardHeader>
        <CardContent className="pt-0">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500">
                <th className="text-left py-1 px-2 font-normal">路段 (km)</th>
                <th className="text-right py-1 px-2 font-normal">爬升</th>
                <th className="text-right py-1 px-2 font-normal">VAM</th>
                <th className="text-right py-1 px-2 font-normal">心率</th>
                <th className="text-right py-1 px-2 font-normal">踏頻</th>
                <th className="text-right py-1 px-2 font-normal">模式</th>
              </tr>
            </thead>
            <tbody>
              {segments.map((seg, i) => (
                <tr key={i} className="border-t border-gray-800">
                  <td className="py-1 px-2 text-gray-300">{seg.start_km}–{seg.end_km}</td>
                  <td className="py-1 px-2 text-right text-gray-300">{seg.elevation_gain_m}m</td>
                  <td className="py-1 px-2 text-right text-purple-400">{seg.vam_m_per_hr}m/hr</td>
                  <td className="py-1 px-2 text-right text-gray-300">{seg.avg_hr ? `${seg.avg_hr}bpm` : '—'}</td>
                  <td className="py-1 px-2 text-right text-gray-300">{seg.avg_cadence ? `${seg.avg_cadence}spm` : '—'}</td>
                  <td className="py-1 px-2 text-right">{seg.is_hiking ? <span className="text-yellow-400">健行</span> : <span className="text-green-400">跑步</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    )
  }

  // Chart 4: HR drift card
  function HrDriftCard({ drift }: { drift: TrailResponse['hr_drift'] }) {
    if (!drift) return null
    const isGood = Math.abs(drift.drift_pct) < 5
    const isMod = Math.abs(drift.drift_pct) < 8
    const color = isGood ? 'text-green-400' : isMod ? 'text-yellow-400' : 'text-red-400'
    const label = isGood ? '有氧效率良好' : isMod ? '輕度漂移（可接受）' : '明顯心率漂移，強度偏高'
    return (
      <Card>
        <CardHeader><CardTitle>心率漂移分析</CardTitle></CardHeader>
        <CardContent>
          <div className="flex items-center gap-4">
            <div className={`text-3xl font-mono ${color}`}>{drift.drift_pct > 0 ? '+' : ''}{drift.drift_pct}%</div>
            <div className="text-sm text-gray-400">{label}</div>
          </div>
          <div className="mt-2 text-xs text-gray-600">
            前半段 HR/GAP: {drift.first_half_ratio.toFixed(3)} → 後半段: {drift.second_half_ratio.toFixed(3)}
          </div>
        </CardContent>
      </Card>
    )
  }

  // Main section
  export function TrailAnalysisSection({ trail }: { trail: TrailResponse }) {
    return (
      <div className="space-y-4">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-gray-400">越野跑分析</h2>
          <span className="text-xs text-gray-600">爬升 {trail.total_elevation_gain_m}m</span>
          {trail.gap_tss !== null && (
            <span className="text-xs text-purple-400">GAP-TSS: {trail.gap_tss}</span>
          )}
        </div>
        <ElevationPaceChart data={trail.timeseries} />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <GradeCadenceChart data={trail.timeseries} />
          <HrDriftCard drift={trail.hr_drift} />
        </div>
        <VamSegmentsTable segments={trail.climb_segments} />
      </div>
    )
  }
  ```
- **MIRROR**: `frontend/src/components/charts/TimeseriesChart.tsx` — Card wrapper + `ResponsiveContainer` + `BASE_*` 主題 + `isAnimationActive={false}` 大資料集
- **GOTCHA**: `ScatterChart` 的 `data` 在 `<Scatter>` 元件上，不在 `<ScatterChart>` 上；`YAxis reversed` 讓配速數字（s/km 大 = 慢）對應直覺的視覺方向
- **VALIDATE**: `npx tsc --noEmit` 無錯誤；Activity detail 頁確認圖表渲染

---

### Task 18: 更新 `ActivityDetailPage.tsx` — 插入 Trail Section

- **ACTION**: 在 `frontend/src/pages/ActivityDetailPage.tsx` 偵測爬升並顯示 TrailAnalysisSection
- **IMPLEMENT** — 在 import 區加入：
  ```typescript
  import { useTrailAnalysis } from '../api/hooks'
  import { TrailAnalysisSection } from '../components/charts/TrailAnalysisSection'
  ```
  在 `ActivityDetailPage` function 內，`useZones` 後加入：
  ```typescript
  const elevationGain = workout?.metrics?.elevation_gain_m ?? 0
  const isTrail = elevationGain > 100
  const { data: trail } = useTrailAnalysis(isTrail ? workoutId : null)
  ```
  在 JSX 的 `{zones && ...}` 區塊後加入：
  ```tsx
  {trail && trail.is_trail && (
    <TrailAnalysisSection trail={trail} />
  )}
  ```
- **MIRROR**: `frontend/src/pages/ActivityDetailPage.tsx:26-30` — `useWorkout` + conditional render pattern
- **GOTCHA**: `workout.metrics.elevation_gain_m` 是 `WorkoutMetric` key，但實際上 `elevation_gain_m` 存在 `WorkoutFile` model 而非 `WorkoutMetric`。需確認後端 `_workout_summary` 回傳是否有 `elevation_gain_m`。若沒有，改用 `workout.metrics` 以外的方式——暫時以 `useTrailAnalysis` 的回傳 `is_trail` 作為判斷（lazy load，先打 trail API，若回傳 422 則 `data` 為 undefined）。實作改為：
  ```typescript
  const { data: trail } = useTrailAnalysis(workoutId)  // 永遠打，422 時 data = undefined
  ```
- **VALIDATE**: 有越野跑 FIT 的訓練 → 顯示 TrailAnalysisSection；平路訓練 → 不顯示

---

### Task 19: 更新 `SeasonTab.tsx` + `AiPage.tsx`

- **ACTION**: (A) SeasonTab 頂部插入 SmartDashboardSection；(B) AiPage 替換為 AiChat 全頁
- **IMPLEMENT (A)** — `frontend/src/tabs/SeasonTab.tsx` import 區加入：
  ```typescript
  import { SmartDashboardSection } from '../components/charts/SmartDashboardSection'
  ```
  在 `return` 的 `<div className="space-y-6">` 內最頂部加入：
  ```tsx
  <SmartDashboardSection />
  ```
- **IMPLEMENT (B)** — 完全替換 `frontend/src/pages/AiPage.tsx` 內容為：
  ```typescript
  import { AiChat } from '../components/AiChat'

  export function AiPage() {
    return (
      <div className="h-screen flex flex-col">
        <div className="flex-1 overflow-hidden">
          <AiChat />
        </div>
      </div>
    )
  }
  ```
- **MIRROR**: SeasonTab 現有 `CollapsibleSection` 模式；AiPage 原始結構
- **GOTCHA**: AiPage 原有 MCP 說明消失 — 可在 ConfigTab AI 設定區塊下方加一行小字：`也可透過 MCP 連接 Claude Desktop（見設定頁）`，不需保留整頁說明
- **VALIDATE**: 瀏覽器：Season tab 頂部出現 5 張狀態卡；AI tab 開啟對話介面

---

## Testing Strategy

### Unit Tests (Backend algorithms)

| Test | Input | Expected Output |
|---|---|---|
| `compute_grade` 平路 | altitude 全 0，dist 遞增 | grade 全 0 |
| `compute_grade` 均勻上坡 | alt [0,10,20]，dist [0,100,200] | grade ≈ 10% |
| `compute_gap` 上坡 10% | pace=360 s/km，grade=10 | gap ≈ 270 s/km（360/1.33）|
| `compute_gap` 下坡 -10% | pace=360，grade=-10 | gap ≈ 410 s/km（360/0.85）|
| `segment_climbs` 無上坡 | alt 全平 | `[]` |
| `segment_climbs` 爬升 80m | 連續 80m | `[]`（低於 min_gain=100m）|
| `compute_hr_drift` 短訓練 | duration < 10min | `None` |
| `get_ai_client` key=None | settings.ai_api_key=None | `None` |
| `get_ai_client` provider=claude | valid settings | `ClaudeClient` instance |

### Edge Cases Checklist
- [x] FIT 無 altitude 資料 → trail endpoint 回 422 `NO_ALTITUDE_DATA`
- [x] FIT 平路（爬升 < 100m）→ `is_trail: false`，前端不顯示 section
- [x] AI key 未設定 → SSE 回傳 `{"error": "NO_API_KEY"}`，前端顯示 ⚠️
- [x] AI API 呼叫失敗（網路錯誤/rate limit）→ SSE 回傳 `{"error": "API_ERROR"}`
- [x] messages 為空陣列發送 → 後端仍能正常處理（context.build_context 不依賴 messages）
- [x] 無 PmcCache 資料 → context 跳過 PMC 段落，不崩潰

---

## Validation Commands

### Backend 啟動
```bash
cd /Users/<user>/Projects/WKO5reverse
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```
EXPECT: `Application startup complete.` 無錯誤

### Schema 驗證
```bash
sqlite3 ~/.wko5coach/wko5coach.db ".schema athlete_settings" | grep ai_
```
EXPECT: 三行含 `ai_provider`, `ai_api_key`, `ai_model`

### API Smoke Tests
```bash
# AI status
curl -s http://localhost:8000/api/v1/ai/status | python3 -m json.tool
# EXPECT: {"configured": false, ...}

# Dashboard summary
curl -s http://localhost:8000/api/v1/analytics/dashboard-summary | python3 -m json.tool
# EXPECT: JSON with today_tsb, tsb_state, ...

# Trail (用實際 workout id)
curl -s http://localhost:8000/api/v1/workouts/1/trail | python3 -m json.tool
# EXPECT: JSON 或 {"detail": "NO_ALTITUDE_DATA"}
```

### Algorithm Unit Test
```bash
cd /Users/<user>/Projects/WKO5reverse
python -m pytest src/tests/ backend/tests/ -v 2>&1 | tail -20
```
EXPECT: All tests pass（新的 trail.py 算法不在現有測試中，手動驗證即可）

### Frontend Build
```bash
cd /Users/<user>/Projects/WKO5reverse/frontend
npm run build
```
EXPECT: 無 TypeScript 或 Vite 錯誤

### Type Check
```bash
cd /Users/<user>/Projects/WKO5reverse/frontend
npx tsc --noEmit
```
EXPECT: Zero errors

### Manual Browser Validation
- [ ] 開啟 `http://localhost:5173`（或 8000）
- [ ] Config tab → AI 設定區塊顯示 Provider/Model/Key 欄位
- [ ] 填入有效 API Key → 儲存 → Key 欄顯示「已設定」
- [ ] AI tab → 對話介面顯示、快捷問題按鈕存在
- [ ] 送出「本週狀態如何？」→ AI 開始串流中文回應
- [ ] Season tab → 頂部出現 5 張狀態卡片
- [ ] 點擊「問 AI 教練 →」→ 切換到 AI tab
- [ ] 開啟任一越野跑訓練 → 頁面下方出現越野跑分析 4 圖
- [ ] 開啟平路跑步訓練 → 無越野跑分析圖表

---

## Acceptance Criteria
- [ ] `GET /api/v1/ai/status` 回傳正確設定狀態
- [ ] `POST /api/v1/ai/chat` SSE 串流中文回應，含訓練資料引用
- [ ] `GET /api/v1/workouts/{id}/trail` 平路回 422，越野跑回含 timeseries + climb_segments
- [ ] `GET /api/v1/analytics/dashboard-summary` 回傳 5 個指標
- [ ] Config tab 可儲存 Claude / OpenAI 的 provider + model + key
- [ ] AI tab 顯示對話 UI，串流輸入正常，enter 送出
- [ ] Season tab 頂部 5 張狀態卡正常顯示
- [ ] 越野跑訓練詳情頁自動顯示 4 張分析圖
- [ ] `npx tsc --noEmit` 無錯誤
- [ ] `npm run build` 成功

## Completion Checklist
- [ ] 所有 19 tasks 完成
- [ ] `requirements.txt` 已更新（anthropic, openai）
- [ ] DB schema 已 migrate（3 新欄位）
- [ ] `backend/main.py` 已註冊 ai router
- [ ] `ai_api_key` 不回傳到前端 GET 端點
- [ ] 所有 API 錯誤碼使用 `SCREAMING_SNAKE` 格式
- [ ] 所有前端圖表使用 `CHART_COLORS` + `BASE_*` 主題
- [ ] SSE 串流 error 用 inline payload，不用 HTTPException

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `anthropic` SDK 版本 API 變動（stream 介面）| M | M | lazy import；`async with client.messages.stream()` 是 SDK 穩定 API |
| FIT 無 `altitude_m` 資料（Coros 某些跑步模式）| M | L | `np.any(alt > 0)` 檢查，回 422，前端優雅降級 |
| `PmcCache` 尚無資料（新安裝）| H | L | context.py 跳過 PMC 段落，AI 回應不含 PMC 資訊，不崩潰 |
| Trail timeseries 過大導致前端渲染卡頓 | L | M | 已降採樣至 ≤ 1800 點，與 timeseries endpoint 一致 |

## Notes
- `compute_gap` 公式：上坡 `pace / (1 + 0.033*grade)`，下坡 `pace / max(0.5, 1 + 0.015*grade)`（grade 為負）。這讓上坡 GAP < 實際配速（更快的等效平路），下坡 GAP > 實際配速（更慢的等效平路）
- `ai_api_key` 明文存 SQLite 是已接受的設計決策（本機 app，不對外）
- `AiPage.tsx` 的 MCP 說明完全移除，功能由 in-app chat 取代
- Task 執行順序建議：1→2→3（schema）→4（算法）→5→6→7→8（後端 AI）→9→10（其他 API）→11→12→13（前端型別+store）→14→15（設定+chat）→16→17（圖表）→18→19（整合）
````
