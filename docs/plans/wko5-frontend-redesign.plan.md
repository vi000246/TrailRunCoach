# Plan: WKO5 Coach Frontend Redesign

## Metadata
```yaml
Type: feature
Size: Large
Source PRD: docs/prd/wko5-frontend-redesign.prd.md
Source SRS: docs/spec/wko5-frontend-redesign.spec.md
Status: pending
Created: 2026-05-15
```

## Context

WKO5 Coach 前端目前有嚴重的樣式問題：Tailwind CSS 沒有安裝，所有 utility class 無效。需要：
1. 安裝 Tailwind v4 + shadcn/ui
2. 定義 WKO5 風格設計 token
3. 加入 React Router 路由
4. 實作 Activity List、Activity Detail 兩個新頁面
5. 重構 Season、Config 頁面
6. 後端新增 3 個 endpoint（timeseries、zones、weekly）

Working directory: `~/Projects/WKO5reverse/`
Frontend: `frontend/` (Vite 8, React 19, TypeScript 6)
Backend: `backend/` (FastAPI + SQLAlchemy async + SQLite)

---

## Phase 1: Bootstrap — 安裝 Tailwind v4 + shadcn/ui

### Task 1.1 — 安裝 Tailwind CSS v4

```bash
cd frontend
npm install tailwindcss @tailwindcss/vite
```

**修改 `frontend/vite.config.ts`**：
```ts
import tailwindcss from '@tailwindcss/vite'
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } } },
})
```

**修改 `frontend/src/index.css`**（完全替換）：
```css
@import "tailwindcss";

@theme {
  --color-bg-base:    #07090f;
  --color-bg-surface: #0e1117;
  --color-bg-raised:  #141922;
  --color-bg-overlay: #1c2333;
  --color-border:     #1c2333;
  --color-border-muted: #131824;
  --color-text-primary:   #e8edf5;
  --color-text-secondary: #7d8fa6;
  --color-text-muted:     #3e4e63;
  --color-accent-purple: #7c3aed;
  --color-accent-teal:   #06b6d4;
  --color-accent-red:    #ef4444;
  --color-accent-green:  #22c55e;
  --color-accent-amber:  #f59e0b;
  --color-accent-blue:   #3b82f6;
}

* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--color-bg-base);
  color: var(--color-text-primary);
  font-family: system-ui, -apple-system, sans-serif;
  font-size: 14px;
}
#root { min-height: 100vh; }
```

**驗證**：`npm run dev` → 打開瀏覽器 → 看到深色背景（`#07090f`）而不是白色或灰色

---

### Task 1.2 — 安裝 shadcn/ui

```bash
cd frontend
npx shadcn@latest init
# 選擇：
#   style: Default
#   base color: Zinc (最接近 WKO5 灰調)
#   CSS variables: yes
```

安裝需要的元件：
```bash
npx shadcn@latest add button card badge input label select separator scroll-area
```

**若 shadcn init 覆寫 index.css**：手動還原 Task 1.1 的 `@theme` block，保留 shadcn 生成的 `@layer base` variables。

---

### Task 1.3 — 建立 Chart Theme

新增 `frontend/src/lib/chartTheme.ts`：
```ts
export const CHART_COLORS = {
  ctl:     '#7c3aed',
  atl:     '#ef4444',
  tsb:     '#22c55e',
  power:   '#f59e0b',
  hr:      '#ef4444',
  cadence: '#06b6d4',
  pace:    '#3b82f6',
  grid:    '#1c2333',
  axis:    '#3e4e63',
}

export const BASE_AXIS_PROPS = {
  tick: { fontSize: 11, fill: CHART_COLORS.axis },
  tickLine: false,
  axisLine: false,
}

export const BASE_GRID_PROPS = {
  strokeDasharray: '3 3',
  stroke: CHART_COLORS.grid,
}

export const BASE_TOOLTIP_STYLE = {
  contentStyle: {
    background: '#0e1117',
    border: '1px solid #1c2333',
    fontSize: 12,
    borderRadius: 6,
  },
  labelStyle: { color: '#7d8fa6' },
}
```

---

## Phase 2: Routing + AppShell

### Task 2.1 — 安裝 React Router v7

```bash
cd frontend
npm install react-router
```

（React Router v7 的 npm package 名稱是 `react-router`，不再是 `react-router-dom`）

---

### Task 2.2 — 重寫 App.tsx（路由定義）

**替換 `frontend/src/App.tsx`** 為：
```tsx
import { BrowserRouter, Routes, Route, Navigate } from 'react-router'
import { AppShell } from './layouts/AppShell'
import { SeasonPage } from './pages/SeasonPage'
import { ActivityListPage } from './pages/ActivityListPage'
import { ActivityDetailPage } from './pages/ActivityDetailPage'
import { ConfigPage } from './pages/ConfigPage'
import { AiPage } from './pages/AiPage'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/season" replace />} />
          <Route path="season" element={<SeasonPage />} />
          <Route path="activities" element={<ActivityListPage />} />
          <Route path="activities/:id" element={<ActivityDetailPage />} />
          <Route path="config" element={<ConfigPage />} />
          <Route path="ai" element={<AiPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
```

**修改 `frontend/src/main.tsx`**：
```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import './index.css'

const queryClient = new QueryClient()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
)
```

---

### Task 2.3 — 建立 AppShell

新增 `frontend/src/layouts/AppShell.tsx`：

```tsx
import { NavLink, Outlet } from 'react-router'
import { CorosSyncButton } from '../components/CorosSyncButton'

const NAV_ITEMS = [
  { to: '/season', label: 'Season' },
  { to: '/activities', label: 'Activities' },
  { to: '/config', label: 'Config' },
  { to: '/ai', label: 'AI' },
]

export function AppShell() {
  return (
    <div className="min-h-screen bg-[#07090f] text-[#e8edf5]">
      <header className="fixed top-0 left-0 right-0 z-50 h-12 bg-[#0e1117] border-b border-[#1c2333] flex items-center px-6 gap-8">
        <span className="text-sm font-semibold text-[#a78bfa] tracking-wide">WKO5 Coach</span>
        <nav className="flex items-center gap-1 flex-1">
          {NAV_ITEMS.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `px-3 py-2 text-sm font-medium transition-colors border-b-2 -mb-px ${
                  isActive
                    ? 'border-[#7c3aed] text-[#a78bfa]'
                    : 'border-transparent text-[#7d8fa6] hover:text-[#e8edf5]'
                }`
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <CorosSyncButton />
      </header>
      <main className="pt-12">
        <Outlet />
      </main>
    </div>
  )
}
```

新增 `frontend/src/components/CorosSyncButton.tsx`（從舊 Dashboard.tsx 的 CorosPanel 抽出核心邏輯，精簡為 header 按鈕）：
- 顯示 sync 狀態（● email or 未登入）
- 「↓ Sync」按鈕
- TpLoginModal 觸發（如果未登入）

---

## Phase 3: 後端新增 Endpoints

### Task 3.1 — `GET /workouts/{id}/timeseries`

在 `backend/api/workouts.py` 新增：

```python
@router.get("/{workout_id}/timeseries")
async def get_workout_timeseries(workout_id: int, db: AsyncSession = Depends(get_db)):
    w = await _get_workout_or_404(workout_id, db)
    if w.file_format != "fit":
        raise HTTPException(422, "NO_FIT_FILE")
    try:
        raw = parse_fit(w.file_path)
    except Exception as e:
        raise HTTPException(422, f"PARSE_ERROR: {e}")

    time_s = raw.time_s or []
    power_w = raw.power_w or []
    hr_bpm = getattr(raw, 'hr_bpm', []) or []
    cadence = getattr(raw, 'cadence_rpm', []) or []

    n = len(time_s)
    step = max(1, n // 1800)

    series = []
    for i in range(0, n, step):
        point = {"t": int(time_s[i])}
        if i < len(power_w) and power_w[i] is not None:
            point["power"] = round(power_w[i])
        if i < len(hr_bpm) and hr_bpm[i] is not None:
            point["hr"] = round(hr_bpm[i])
        if i < len(cadence) and cadence[i] is not None:
            point["cadence"] = round(cadence[i])
        series.append(point)

    return {
        "workout_id": workout_id,
        "duration_s": int(time_s[-1]) if time_s else 0,
        "sample_rate_s": step,
        "series": series,
    }
```

Helper `_get_workout_or_404`（避免重複 query）：
```python
async def _get_workout_or_404(workout_id: int, db: AsyncSession) -> WorkoutFile:
    result = await db.execute(select(WorkoutFile).where(WorkoutFile.id == workout_id))
    w = result.scalar_one_or_none()
    if not w:
        raise HTTPException(404, "WORKOUT_NOT_FOUND")
    return w
```

---

### Task 3.2 — `GET /workouts/{id}/zones`

在 `backend/api/workouts.py` 新增：

```python
POWER_ZONES = [
    (1, "Recovery",   0.00, 0.55),
    (2, "Endurance",  0.55, 0.75),
    (3, "Tempo",      0.75, 0.90),
    (4, "Threshold",  0.90, 1.05),
    (5, "VO2max",     1.05, 1.20),
    (6, "Anaerobic",  1.20, 1.50),
    (7, "Neuromuscular", 1.50, 99),
]

HR_ZONES = [
    (1, "Recovery",   0.00, 0.68),
    (2, "Aerobic",    0.68, 0.83),
    (3, "Tempo",      0.83, 0.94),
    (4, "Threshold",  0.94, 1.05),
    (5, "VO2max",     1.05, 99),
]

@router.get("/{workout_id}/zones")
async def get_workout_zones(workout_id: int, db: AsyncSession = Depends(get_db)):
    w = await _get_workout_or_404(workout_id, db)

    # 取 athlete settings（FTP/LTHR）
    athlete_result = await db.execute(select(Athlete).where(Athlete.id == 1))
    athlete = athlete_result.scalar_one_or_none()
    ftp = (athlete.ftp if athlete else None) or 200
    lthr = (athlete.lthr if athlete else None) or 165

    raw = parse_fit(w.file_path)
    power_w = raw.power_w or []
    hr_bpm = getattr(raw, 'hr_bpm', []) or []

    def time_in_zones(values, zones, threshold):
        counts = {z[0]: 0 for z in zones}
        for v in values:
            if v is None:
                continue
            ratio = v / threshold
            for z_id, _, lo, hi in zones:
                if lo <= ratio < hi:
                    counts[z_id] += 1
                    break
        return counts

    pw_counts = time_in_zones(power_w, POWER_ZONES, ftp)
    hr_counts = time_in_zones(hr_bpm, HR_ZONES, lthr)

    return {
        "workout_id": workout_id,
        "ftp": ftp,
        "lthr": lthr,
        "power_zones": [
            {"zone": z, "name": n, "min_w": round(lo * ftp), "max_w": round(hi * ftp) if hi < 10 else None, "time_s": pw_counts[z]}
            for z, n, lo, hi in POWER_ZONES
        ],
        "hr_zones": [
            {"zone": z, "name": n, "min_bpm": round(lo * lthr), "max_bpm": round(hi * lthr) if hi < 10 else None, "time_s": hr_counts[z]}
            for z, n, lo, hi in HR_ZONES
        ],
    }
```

---

### Task 3.3 — `GET /analytics/weekly`

新建 `backend/api/analytics.py`：

```python
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from datetime import date
from typing import Optional

from backend.db.database import get_db
from backend.db.models import WorkoutFile, WorkoutMetric

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])

@router.get("/weekly")
async def weekly_load(
    athlete_id: int = 1,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(
            func.strftime('%Y-%W', WorkoutFile.workout_date).label('week_key'),
            func.min(WorkoutFile.workout_date).label('week_start'),
            func.coalesce(func.sum(
                select(WorkoutMetric.value)
                .where(WorkoutMetric.workout_id == WorkoutFile.id)
                .where(WorkoutMetric.metric_key == 'tss')
                .correlate(WorkoutFile)
                .scalar_subquery()
            ), 0).label('tss'),
            (func.sum(WorkoutFile.duration_s) / 3600.0).label('hours'),
            func.count(WorkoutFile.id).label('count'),
        )
        .where(WorkoutFile.athlete_id == athlete_id)
    )
    if date_from:
        q = q.where(WorkoutFile.workout_date >= date_from)
    if date_to:
        q = q.where(WorkoutFile.workout_date <= date_to)
    q = q.group_by('week_key').order_by('week_key')

    result = await db.execute(q)
    rows = result.all()

    return {
        "weeks": [
            {
                "week_start": row.week_start.isoformat() if row.week_start else None,
                "tss": round(row.tss or 0),
                "hours": round(row.hours or 0, 1),
                "count": row.count,
            }
            for row in rows
        ]
    }
```

在 `backend/main.py` 新增：
```python
from backend.api import analytics
app.include_router(analytics.router)
```

---

## Phase 4: 頁面實作

### Task 4.1 — SeasonPage（重構）

`frontend/src/pages/SeasonPage.tsx`：

```tsx
import { useState } from 'react'
import { DateRangePicker, type DateRange } from '../components/DateRangePicker'
import { PmcChart } from '../components/charts/PmcChart'
import { WeeklyLoadChart } from '../components/charts/WeeklyLoadChart'

const daysAgo = (n: number) => {
  const d = new Date(); d.setDate(d.getDate() - n); return d.toISOString().slice(0, 10)
}

export function SeasonPage() {
  const [range, setRange] = useState<DateRange>({
    from: daysAgo(365),
    to: new Date().toISOString().slice(0, 10),
  })

  return (
    <div className="p-6 space-y-4 max-w-6xl mx-auto">
      <div className="flex items-center justify-between">
        <h1 className="text-base font-semibold text-[#e8edf5]">Season Overview</h1>
        <DateRangePicker value={range} onChange={setRange} />
      </div>
      <PmcChart dateFrom={range.from} dateTo={range.to} />
      <WeeklyLoadChart dateFrom={range.from} dateTo={range.to} />
    </div>
  )
}
```

**重構 PmcChart** (`frontend/src/components/charts/PmcChart.tsx`)：
- 套用 `BASE_AXIS_PROPS`, `BASE_GRID_PROPS`, `BASE_TOOLTIP_STYLE`
- 顏色改用 `CHART_COLORS.ctl`, `CHART_COLORS.atl`, `CHART_COLORS.tsb`

**新增 WeeklyLoadChart** (`frontend/src/components/charts/WeeklyLoadChart.tsx`)：
- `useWeeklyLoad(params)` → recharts `ComposedChart`
- Bar: TSS（左軸，橙色 `#f59e0b`）
- Line: Hours（右軸，青色 `#06b6d4`）

---

### Task 4.2 — ActivityListPage（新）

`frontend/src/pages/ActivityListPage.tsx`：

**功能**：
- `useWorkouts(params)` query（page, per_page, sport, date_from, date_to）
- 表格欄位：Date | Sport | Duration | Distance | NP | TSS | Source
- Badge：sport 類型（cycling=紫，run=青，other=灰）
- 分頁：上下頁按鈕 + 「第 N 頁，共 M 筆」
- 每行 `onClick` → `navigate('/activities/' + workout.id)`

**建立 `frontend/src/components/ActivityRow.tsx`**：
- 單行元件，輸入 workout summary，回傳 `<tr>` 元素
- Duration formatter：`h:mm:ss` 格式（`formatDuration(s: number)`）
- Sport badge（色彩依 sport 類型）

---

### Task 4.3 — ActivityDetailPage（新）

`frontend/src/pages/ActivityDetailPage.tsx`：

```tsx
const { id } = useParams<{ id: string }>()
const workoutId = parseInt(id!)

// parallel queries
const { data: workout } = useWorkout(workoutId)
const { data: timeseries } = useTimeseries(workoutId)
const { data: zones } = useZones(workoutId)
const { data: mmp } = useWorkoutMmp(workoutId)
```

**Layout**：
```
← Activities  {date} · {sport}
[MetricCards row: Duration | Avg Power | NP | TSS | Avg HR]
[TimeseriesChart — full width, 280px height]
[MmpCurveChart — half width] [ZoneTable — half width]
```

**新增元件**：

`frontend/src/components/MetricCard.tsx`：
```tsx
export function MetricCard({ label, value, unit }: { label: string; value: string | number; unit?: string }) {
  return (
    <div className="bg-[#0e1117] border border-[#1c2333] rounded-lg px-4 py-3 min-w-0">
      <div className="text-[11px] text-[#7d8fa6] mb-1">{label}</div>
      <div className="text-[18px] font-semibold text-[#e8edf5]">
        {value}{unit && <span className="text-[12px] text-[#7d8fa6] ml-1">{unit}</span>}
      </div>
    </div>
  )
}
```

`frontend/src/components/charts/TimeseriesChart.tsx`：
- `ComposedChart`（recharts）
- Primary line：Power（amber，左軸）
- Secondary line：HR（red，右軸 secondary）
- Optional line：Cadence（teal，hidden by default, legend toggle）
- X 軸：elapsed time（mm:ss formatter）

`frontend/src/components/charts/MmpCurveChart.tsx`（重構現有 MmpCurveWidget）：
- 套用 chart theme
- X 軸 log scale（5s, 10s, 30s, 1m, 5m, 20m, 1h, 3h labels）

`frontend/src/components/ZoneTable.tsx`：
- Power Zones + HR Zones 兩個 section
- 每個 zone 顯示：Zone # | Name | Range | Time | Bar（比例填色）

---

### Task 4.4 — ConfigPage（重構）

`frontend/src/pages/ConfigPage.tsx`：

**Sections**：
1. **Athlete Settings**：FTP input、LTHR input、Weight input、TSS formula select
   - 呼叫 `GET /api/v1/athletes/1/settings` 初始化
   - Save → `PUT /api/v1/athletes/1/settings` → toast「Saved」→ `POST /api/v1/pmc/recompute`
2. **Power Zones**：根據 FTP 即時計算並展示 7 個 zone（no backend call，純 frontend 計算）
3. **HR Zones**：根據 LTHR 即時計算並展示 5 個 zone

---

### Task 4.5 — AiPage（新）

`frontend/src/pages/AiPage.tsx`：

靜態說明頁：
- 標題：「Connect Claude AI to your training data」
- 說明：MCP Server 啟動指令（`python -m backend.mcp_server` 或 uvicorn cmd）
- Claude Desktop 設定 JSON 範例（`claude_desktop_config.json`）
- 常見問題列表（本週 TSS / 最近 FTP 趨勢 / 上次比 baseline 如何...），每項旁邊有「Copy」按鈕

---

### Task 4.6 — 新增 API Hooks

在 `frontend/src/api/hooks.ts` 新增：

```ts
export const useWorkout = (id: number) =>
  useQuery({ queryKey: ['workout', id], queryFn: () => apiClient.get(`/workouts/${id}`).then(r => r.data) })

export const useTimeseries = (id: number) =>
  useQuery({ queryKey: ['timeseries', id], queryFn: () => apiClient.get(`/workouts/${id}/timeseries`).then(r => r.data) })

export const useZones = (id: number) =>
  useQuery({ queryKey: ['zones', id], queryFn: () => apiClient.get(`/workouts/${id}/zones`).then(r => r.data) })

export const useWeeklyLoad = (params: { date_from?: string; date_to?: string }) =>
  useQuery({ queryKey: ['weekly', params], queryFn: () => apiClient.get('/analytics/weekly', { params }).then(r => r.data) })

export const useWorkoutMmp = (id: number) =>
  useQuery({ queryKey: ['mmp', id], queryFn: () => apiClient.get(`/workouts/${id}/mmp`).then(r => r.data) })
```

---

## Phase 5: 整合 + 清理

### Task 5.1 — 移除廢棄元件

- 刪除 `frontend/src/pages/Dashboard.tsx`（功能已拆入 AppShell + 各 page）
- 刪除 `frontend/src/components/DashboardGrid.tsx`（不再使用 react-grid-layout）
- 可選：`npm uninstall react-grid-layout @types/react-grid-layout`
- 刪除 `frontend/src/App.css`（預設 Vite 樣式，無用）

### Task 5.2 — 最終驗證

手動測試以下流程：
- [ ] `npm run dev` → 無 TypeScript error
- [ ] 開啟 `localhost:5173` → Header 顯示，背景深色 `#07090f`
- [ ] 點 Season → PMC 圖表顯示，顏色正確（CTL=紫、ATL=紅、TSB=綠）
- [ ] 點 Activities → 列表顯示訓練記錄
- [ ] 點一筆活動 → URL 變為 `/activities/:id`，功率圖、MMP 圖顯示
- [ ] Config 頁面 → 修改 FTP → Save → toast 顯示
- [ ] `npm run build` → 無 build error

### Task 5.3 — 更新 main.py（backend analytics 路由）

確認 `backend/main.py` 中 analytics router 已 include：
```python
from backend.api import analytics
app.include_router(analytics.router)
```

確認 `backend/api/__init__.py`（若有）或直接 import 能找到 `analytics.py`。

---

## Implementation Order

```
Phase 1 (Bootstrap)
  └── 1.1 Install Tailwind v4
  └── 1.2 Install shadcn/ui
  └── 1.3 Chart theme file

Phase 2 (Routing)
  └── 2.1 Install React Router
  └── 2.2 Rewrite App.tsx
  └── 2.3 AppShell layout

Phase 3 (Backend) ← can be done in parallel with Phase 4 stubs
  └── 3.1 timeseries endpoint
  └── 3.2 zones endpoint
  └── 3.3 weekly endpoint

Phase 4 (Pages) ← start after Phase 2; Phase 3 needed for Detail data
  └── 4.1 SeasonPage
  └── 4.2 ActivityListPage
  └── 4.3 ActivityDetailPage (needs Phase 3 endpoints)
  └── 4.4 ConfigPage
  └── 4.5 AiPage
  └── 4.6 New API hooks

Phase 5 (Cleanup)
  └── 5.1 Remove deprecated files
  └── 5.2 Manual testing
  └── 5.3 Build check
```

---

## Notes

- `parse_fit()` 目前回傳什麼欄位需在實作 Task 3.1 前先確認（`backend/files/fit_reader.py`）；特別是 `hr_bpm` 和 `cadence_rpm` 的屬性名稱。
- Zone 計算的 Athlete model：確認 `backend/db/models.py` 中 `Athlete` model 有 `ftp` 和 `lthr` 欄位（settings 應已有，但需確認欄位名稱）。
- shadcn/ui Tailwind v4 的相容性：若 `npx shadcn@latest init` 失敗，嘗試 `npx shadcn@canary init`。
- React Router v7 的 package 名稱是 `react-router`（不是 `react-router-dom`）；import 路徑相應改變。
