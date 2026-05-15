# SRS: WKO5 Coach Frontend Redesign — Design System + Full Page Architecture

## Metadata
- **Source PRD**: `docs/prd/wko5-frontend-redesign.prd.md`
- **Owner**: vi000246
- **Status**: DRAFT
- **Generated**: 2026-05-15

---

## Summary

為 WKO5 Coach 前端安裝完整設計系統（Tailwind CSS v4 + shadcn/ui），定義 WKO5 風格設計 token，引入 React Router v7 路由，並完成所有缺失頁面（Activity List、Activity Detail、Season 增強、Config 增強、AI 入口頁）。同時新增 3 個後端 FastAPI endpoint 支援 Activity Detail 所需的時序資料和 Zone 分佈，以及 Season tab 所需的週載量資料。

---

## System Context

### Scope & Boundaries

**In scope**:
- Tailwind CSS v4 安裝與設定
- shadcn/ui 元件庫整合（Button、Card、Tabs、Input、Label、Select、Badge、Separator、ScrollArea）
- WKO5 風格設計 token（CSS custom properties）
- React Router v7 路由結構
- AppShell layout（Header + main content）
- Season 頁面重構（PMC + WeeklyLoad + CorosPanel → Header）
- Activity List 頁面（分頁、篩選）
- Activity Detail 頁面（時序圖、MMP 曲線、指標摘要卡）
- Config 頁面重構（FTP/LTHR form + Zone 展示）
- AI 頁面（MCP 說明頁）
- recharts chart theme（統一 WKO5 風格）
- 後端 3 個新 endpoint

**Out of scope**:
- 地圖路線顯示（GPS lat/lon）
- Mobile RWD
- Drag & drop widget Dashboard
- AI Chat（Claude API 直連）
- 亮色主題
- Multi-user auth

### Actors

| Actor | Type | Interaction |
|---|---|---|
| Athlete（使用者本人） | Human | 查看圖表、設定 FTP、觸發 Coros sync |
| Backend FastAPI | Service | 提供訓練資料、PMC、時序 API |

---

## Architecture

### Tech Stack

| Layer | Technology | Version | Notes |
|-------|-----------|---------|-------|
| Build | Vite | ^8 (existing) | |
| UI Framework | React | ^19 (existing) | |
| Language | TypeScript | ~6 (existing) | |
| CSS | Tailwind CSS v4 | `@tailwindcss/vite` plugin | CSS-first, no config file |
| Component Library | shadcn/ui | latest (Tailwind v4 compat) | copy-paste components |
| Routing | React Router | v7 (new) | SPA mode with BrowserRouter |
| State | Zustand | v5 (existing) | activeTab → URL, keep for UI state |
| Server State | TanStack Query | v5 (existing) | |
| Charts | Recharts | v3 (existing) | |
| Icons | Lucide React | existing | |
| HTTP | Axios | existing | |

### Route Structure

```
/ (AppShell)
├── /                    → redirect → /season
├── /season              → SeasonPage
├── /activities          → ActivityListPage
├── /activities/:id      → ActivityDetailPage
├── /config              → ConfigPage
└── /ai                  → AiPage
```

AppShell 包含：Header（帶全局 CorosSync 狀態）+ `<Outlet />` 主內容區。

### File Structure（新增/重構）

```
frontend/src/
├── index.css              ← 改：@import "tailwindcss"; + design tokens
├── main.tsx               ← 改：wrap with <BrowserRouter>
├── App.tsx                ← 改：路由定義（RouterProvider）
├── layouts/
│   └── AppShell.tsx       ← 新：Header + Outlet
├── pages/
│   ├── SeasonPage.tsx     ← 重構（原 SeasonTab + CorosPanel）
│   ├── ActivityListPage.tsx ← 新
│   ├── ActivityDetailPage.tsx ← 新
│   ├── ConfigPage.tsx     ← 重構（原 ConfigTab）
│   └── AiPage.tsx         ← 新
├── components/
│   ├── charts/
│   │   ├── PmcChart.tsx   ← 重構（套用 chart theme）
│   │   ├── WeeklyLoadChart.tsx ← 新
│   │   ├── TimeseriesChart.tsx ← 新（Activity Detail）
│   │   └── MmpCurveChart.tsx  ← 重構（整合現有 MmpCurveWidget）
│   ├── ui/                ← shadcn/ui 元件（copypaste）
│   │   ├── button.tsx
│   │   ├── card.tsx
│   │   ├── badge.tsx
│   │   ├── input.tsx
│   │   ├── label.tsx
│   │   ├── select.tsx
│   │   └── separator.tsx
│   ├── ActivityRow.tsx    ← 新（activity list row）
│   ├── MetricCard.tsx     ← 新（小指標卡片：TSS, NP, Duration 等）
│   ├── ZoneTable.tsx      ← 新（Power / HR Zone 展示）
│   └── DateRangePicker.tsx ← 保留現有（微調）
├── hooks/
│   └── useWorkout.ts      ← 新（單次 workout 相關 queries）
├── api/
│   ├── client.ts          ← 保留
│   └── hooks.ts           ← 保留，新增 useTimeseries、useZones、useWeeklyLoad
└── store/
    └── uiStore.ts         ← 重構 tabStore → uiStore（CorosSync 狀態）
```

### Design Tokens — WKO5 Dark Theme

定義在 `index.css` 的 `@layer base` 中，覆寫 Tailwind v4 的 CSS 變數：

```css
@import "tailwindcss";

@theme {
  /* Background scale — 近黑藍灰 */
  --color-bg-base:    #07090f;   /* body background */
  --color-bg-surface: #0e1117;   /* cards / panels */
  --color-bg-raised:  #141922;   /* inputs, hover */
  --color-bg-overlay: #1c2333;   /* tooltips, modals */

  /* Border */
  --color-border:     #1c2333;
  --color-border-muted: #131824;

  /* Text */
  --color-text-primary:   #e8edf5;
  --color-text-secondary: #7d8fa6;
  --color-text-muted:     #3e4e63;
  --color-text-accent:    #a78bfa;   /* purple, WKO5 power */

  /* Accent — 對應訓練指標語義 */
  --color-accent-purple: #7c3aed;   /* power, CTL fitness line */
  --color-accent-teal:   #06b6d4;   /* alt fitness */
  --color-accent-red:    #ef4444;   /* ATL fatigue */
  --color-accent-green:  #22c55e;   /* TSB form positive */
  --color-accent-amber:  #f59e0b;   /* performance highlights */
  --color-accent-blue:   #3b82f6;   /* links, info */

  /* Typography */
  --font-size-label:  11px;
  --font-size-value:  13px;
  --font-size-header: 16px;
}
```

### Chart Theme（recharts 統一配置）

建立 `src/lib/chartTheme.ts`：

```ts
export const CHART_COLORS = {
  ctl:     '#7c3aed',  // purple — CTL / Fitness
  atl:     '#ef4444',  // red — ATL / Fatigue
  tsb:     '#22c55e',  // green — TSB / Form
  power:   '#f59e0b',  // amber — Power
  hr:      '#ef4444',  // red — Heart Rate
  cadence: '#06b6d4',  // teal — Cadence
  pace:    '#3b82f6',  // blue — Pace
  grid:    '#1c2333',
  axis:    '#3e4e63',
  tooltip: { bg: '#0e1117', border: '#1c2333' },
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
    background: CHART_COLORS.tooltip.bg,
    border: `1px solid ${CHART_COLORS.tooltip.border}`,
    fontSize: 12,
    borderRadius: 6,
  },
  labelStyle: { color: '#7d8fa6' },
}
```

---

## Components

### AppShell（layouts/AppShell.tsx）

```
AppShell
├── Header (fixed top, h-12, bg-bg-surface, border-b border-border)
│   ├── Logo / App Name ("WKO5 Coach")
│   ├── NavLinks: Season | Activities | Config | AI
│   └── CorosSyncButton（status badge + Sync button）
└── main.content (pt-12, min-h-screen, bg-bg-base)
    └── <Outlet />
```

Header nav links use `NavLink` from React Router（`aria-current` → active style: `text-accent-purple border-b-2 border-accent-purple`）.

### SeasonPage（pages/SeasonPage.tsx）

```
SeasonPage
├── PageHeader "Season Overview"
├── DateRangePicker（preset: 1M / 3M / 6M / 1Y / All）
├── PmcChart（CTL/ATL/TSB，日期範圍連動）
└── WeeklyLoadChart（新：bar chart 週 TSS + hours）
```

### ActivityListPage（pages/ActivityListPage.tsx）

```
ActivityListPage
├── Filters row（sport selector | date range | search）
├── ActivityTable
│   ├── Columns: Date | Sport | Duration | Distance | NP | TSS | Source
│   └── Row click → navigate('/activities/:id')
└── Pagination（10/20/50 per page）
```

### ActivityDetailPage（pages/ActivityDetailPage.tsx）

```
ActivityDetailPage
├── Breadcrumb: Activities > {date} {sport}
├── MetricCards row（Duration | Avg Power | NP | TSS | Avg HR | Distance）
├── TimeseriesChart（Power / HR / Cadence 多線，x=time elapsed）
├── MmpCurveChart（當次 MMP 曲線，x=log scale duration）
└── ZoneTable（Power Zones | HR Zones — time in zone bar）
```

### ConfigPage（pages/ConfigPage.tsx）

```
ConfigPage
├── Section: Athlete Settings
│   ├── FTP (W) input + save button
│   ├── LTHR (bpm) input
│   ├── Weight (kg) input
│   └── TSS Formula selector (Coggan / hrTSS)
├── Section: Power Zones（computed from FTP，展示 zone 1-7 + range）
└── Section: HR Zones（computed from LTHR，展示 zone 1-5 + range）
```

Save 呼叫 `PUT /api/v1/athletes/1/settings`；儲存成功後呼叫 PMC recompute（`POST /api/v1/pmc/recompute`）。

### AiPage（pages/AiPage.tsx）

```
AiPage
├── 說明卡片：「如何透過 Claude Desktop 連接 MCP Server」
├── MCP 連接設定展示（JSON config 範例）
└── 常見問題列表（預設問題，點選複製到剪貼簿）
```

---

## New Backend Endpoints

### 1. `GET /api/v1/workouts/{id}/timeseries`

**Purpose**: 回傳單次活動的時序資料（功率、心率、踏頻，每秒），用於 ActivityDetail 圖表。

**Response**（downsampled to ≤ 1800 points via every-N-th-point）：
```json
{
  "workout_id": 42,
  "duration_s": 3600,
  "sample_rate_s": 2,
  "series": [
    { "t": 0, "power": 210, "hr": 145, "cadence": 88 },
    { "t": 2, "power": 215, "hr": 146, "cadence": 89 },
    ...
  ]
}
```

**Implementation**:
- `parse_fit(w.file_path)` → 已有 `power_w`, `hr_bpm`, `cadence_rpm`, `time_s` arrays
- Downsampling：`step = max(1, len(time_s) // 1800)`，取 `[::step]`
- File: `backend/api/workouts.py`（新增 route）

**Error Cases**:
- 404 workout not found
- 422 file format not fit / parse error / no data channels

### 2. `GET /api/v1/workouts/{id}/zones`

**Purpose**: 回傳各功率 / 心率區間的時間分佈。

**Response**:
```json
{
  "workout_id": 42,
  "ftp": 280,
  "lthr": 165,
  "power_zones": [
    { "zone": 1, "name": "Recovery", "min_w": 0, "max_w": 168, "time_s": 420 },
    { "zone": 2, "name": "Endurance", "min_w": 168, "max_w": 224, "time_s": 1800 },
    ...
  ],
  "hr_zones": [
    { "zone": 1, "name": "Recovery", "min_bpm": 0, "max_bpm": 115, "time_s": 300 },
    ...
  ]
}
```

**Implementation**:
- 先查 `athletes/{id}/settings` 取得 FTP、LTHR（預設 athlete_id=1）
- Power Zones：Coggan 7-zone（× FTP：55%/75%/90%/105%/120%/150%/150%+）
- HR Zones：5-zone（× LTHR：68%/83%/94%/105%/105%+）
- 計算方式：遍歷 `power_w` array，累加各區間秒數
- File: `backend/api/workouts.py`

### 3. `GET /api/v1/analytics/weekly`

**Purpose**: 週維度的 TSS + training hours，用於 WeeklyLoadChart。

**Query params**: `date_from`, `date_to`, `athlete_id` (default 1)

**Response**:
```json
{
  "weeks": [
    { "week_start": "2026-01-05", "tss": 342, "hours": 5.2, "count": 4 },
    ...
  ]
}
```

**Implementation**:
- GROUP BY `strftime('%Y-%W', workout_date)` on `workout_files` + `workout_metrics`
- 聚合 `metric_key='tss'` 的 SUM，duration_s 的 SUM / 3600
- File: `backend/api/analytics.py`（新檔案）+ register in `main.py`

---

## Data Flow

```
User navigates to /activities
  → ActivityListPage mounts
  → useWorkouts({ page: 1, per_page: 20 }) fires
  → GET /api/v1/workouts?page=1&per_page=20
  → ActivityTable renders rows

User clicks workout row
  → navigate('/activities/42')
  → ActivityDetailPage mounts
  → parallel queries:
      useWorkout(42)          → GET /api/v1/workouts/42
      useTimeseries(42)       → GET /api/v1/workouts/42/timeseries
      useZones(42)            → GET /api/v1/workouts/42/zones
      useWorkoutMmp(42)       → GET /api/v1/workouts/42/mmp
  → Charts render on data arrival
```

---

## API Hooks（frontend/src/api/hooks.ts 新增）

```ts
// 新增
export const useTimeseries = (workoutId: number) =>
  useQuery({ queryKey: ['timeseries', workoutId], queryFn: () => api.get(`/workouts/${workoutId}/timeseries`) })

export const useZones = (workoutId: number) =>
  useQuery({ queryKey: ['zones', workoutId], queryFn: () => api.get(`/workouts/${workoutId}/zones`) })

export const useWeeklyLoad = (params: DateRangeParams) =>
  useQuery({ queryKey: ['weekly', params], queryFn: () => api.get('/analytics/weekly', { params }) })
```

---

## shadcn/ui Setup

### Installation（Tailwind v4）

```bash
cd frontend
npm install tailwindcss @tailwindcss/vite
npx shadcn@latest init
# 選擇：style=Default, base color=Zinc, CSS variables=yes
# 手動確認生成的 components/ui/ 使用正確的 CSS var 名稱
```

### vite.config.ts 修改

```ts
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],  // 新增 tailwindcss()
  server: { proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } } },
})
```

### index.css 修改

```css
@import "tailwindcss";           /* 替換原本的空白 CSS */

/* design tokens 定義於此（見上方 @theme block） */
```

---

## Non-Functional Requirements

| NFR | Target |
|-----|--------|
| Bundle size 增加 | < 200KB gzipped（shadcn/ui 元件 tree-shaken） |
| 首次載入 | < 2s on localhost（本機工具，無嚴格 perf 要求） |
| TypeScript strict | 保持既有 tsconfig strict mode，無 any |
| Chart render | 1800 data points in < 100ms（recharts canvas） |

---

## Migration Notes

- `frontend/src/App.tsx`：完全重寫為路由定義，移除舊的 Vite 預設模板內容
- `store/tabStore.ts`：保留 `useTabStore`（`activeTab`、`setTab`），但 Navigation 改由 React Router 的 `NavLink` 驅動；`useTabStore` 可保留做其他 UI 狀態（e.g. season date range）
- `pages/Dashboard.tsx`：拆解為 `layouts/AppShell.tsx` + 各 page 元件；原有的 `CorosPanel` 移入 `layouts/AppShell.tsx` 的 Header
- `components/DashboardGrid.tsx`：不再使用（react-grid-layout 移除）；widgets 概念整合進各 page 的固定佈局
- `react-grid-layout` 可選擇性 `npm uninstall`（移除後節省 bundle 大小）

---

*Generated: 2026-05-15*
*Status: DRAFT*
