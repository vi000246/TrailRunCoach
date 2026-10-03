---
linear_issue: null
---
# Plan: sport-pages 前端（三頁面分流、多選篩選、越野圖表、圖表白話化層）

> **For agentic workers:** `/prp-implement` 依 `Metadata.Type` 路由。Mode B（任務先測）。**注意**：前端無測試框架（package.json 無 vitest/jest/playwright），balanced rigor 下「TEST FIRST」以 `tsc --noEmit` 型別綠燈 + `npm run build` + 瀏覽器/截圖驗證取代單元測試。依賴 `sport-pages-backend-foundation.plan.md` 的端點先完成。

## Summary

把單一 `SeasonPage` 拆成三個資料獨立頁面（總體 OverviewPage / 跑步 RunningPage / 越野跑 TrailPage），更新側欄 nav，總體頁加多選 sport checkbox 篩選（只作用於本頁），越野頁加爬升導向圖表（越野 PMC：hrTSS 主+rTSS 並陳、爬升負荷、VAM、GAP，沿用既有 TrailAnalysisSection），並為每個圖表加一層規則產生的「白話一句話 + 狀態號誌」可讀性層。

## User Story
As 看不懂 WKO5 艱澀圖表的越野跑者，
I want 三個依運動別分流、每張圖都附白話解讀與號誌的頁面，
So that 不費力就能判讀訓練狀態並在運動別間切換檢視。

## Problem → Solution
單一 SeasonPage 混所有運動、圖表生硬難讀 → 三獨立頁面 + 總體多選篩選 + 越野專屬圖表 + 規則白話化層。

## Metadata
- **Module**: sport-pages
- **Parent Plan**: docs/plans/sport-pages-backend-foundation.plan.md
- **Source PRD**: docs/prd/wko5-trail-multipage-sync-coach.prd.md
- **Source Feature SRS**: docs/srs/sport-pages-multi-sport-views-trail-analytics.srs.md
- **Source Module Spec**: docs/spec/sport-pages.spec.md
- **Source Linear Issue**: N/A
- **Type**: feature
- **Size**: L
- **Complexity**: Large
- **Rigor**: balanced
- **Mode**: B — 任務先測（前端以 tsc + build + 瀏覽器驗證替代單元測試）
- **TDD**: off（無前端測試框架；型別/建置/視覺為 gate）
- **Commit cadence**: per-task
- **Estimated Files**: ~12

---

## UX Design

### Before
```
┌ Season ─ Activities ─ Config ─ AI ─ Coros ───────────┐
│ 單一 SeasonPage：所有運動混在 PMC/Weekly/RunLoad...   │
│ 圖表為生硬 Recharts，無白話解讀                       │
└──────────────────────────────────────────────────────┘
```

### After
```
┌ 總體 ─ 跑步 ─ 越野跑 ─ Activities ─ Config ─ AI ─ Coros ┐
│ 總體頁：☑跑步 ☑單車 ☑肌力 ☑游泳（多選，只此頁）      │
│   每張圖上方：🟢 一句話白話重點                        │
│ 跑步頁：只含跑步活動                                   │
│ 越野跑頁：越野 PMC(hrTSS) / 爬升負荷 / VAM / GAP        │
│   每張圖附號誌 + 白話                                  │
└────────────────────────────────────────────────────────┘
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Nav | Season 單項 | 總體/跑步/越野跑 三項 | 取代 Season |
| 篩選 | 無 | 總體頁多選 checkbox | 只作用總體頁 |
| 圖表 | 純數字 | +號誌+白話 | 規則產生 |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `docs/srs/sport-pages-multi-sport-views-trail-analytics.srs.md` | all | AC、端點合約 |
| P0 | `docs/plans/sport-pages-backend-foundation.plan.md` | all | 依賴的端點與回應形狀 |
| P0 | `frontend/src/App.tsx` | 1-26 | 路由結構 |
| P0 | `frontend/src/layouts/AppShell.tsx` | 1-43 | NAV_ITEMS + 樣式 |
| P0 | `frontend/src/pages/SeasonPage.tsx` | 1-77 | CollapsibleSection + 頁面組成、要拆分的對象 |
| P0 | `frontend/src/components/charts/SmartDashboardSection.tsx` | 1-108 | TSB_COLORS/LABELS + StatCard（白話化層藍本） |
| P0 | `frontend/src/api/hooks.ts` | 1-60 | useQuery hook 模式 |
| P0 | `frontend/src/api/client.ts` | 1-240 | api client + 型別匯出位置 |
| P1 | `frontend/src/components/charts/RunLoadChart.tsx` | 14-76 | ComposedChart + ReferenceArea zone 模式（越野圖表藍本） |
| P1 | `frontend/src/components/charts/TrailAnalysisSection.tsx` | all | 既有 GAP/climb 越野元件，越野頁複用 |
| P1 | `frontend/src/components/DateRangePicker.tsx` | all | range 狀態模式 |

## External Documentation
No external research needed — 沿用既有 React Router / React Query / Recharts / Tailwind 內部模式。

---

## Patterns to Mirror

### QUERY_HOOK
```ts
// SOURCE: frontend/src/api/hooks.ts:28-33
export function usePmc(params?: { date_from?: string; date_to?: string }) {
  return useQuery<{ series: PmcPoint[] }>({
    queryKey: ['pmc', params],
    queryFn: () => api.get('/pmc', { params }).then(r => r.data),
  })
}
```

### MUTATION_HOOK (invalidate)
```ts
// SOURCE: frontend/src/api/hooks.ts:42-51
export function useSync() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => fetch('/api/v1/sync/start', { method: 'POST' }).then(r => r.body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['workouts'] }) },
  })
}
```

### NAV_ITEM
```tsx
// SOURCE: frontend/src/layouts/AppShell.tsx:4-10
const NAV_ITEMS = [
  { to: '/season', label: 'Season' },
  // ...
]
```

### ROUTE
```tsx
// SOURCE: frontend/src/App.tsx:14-22
<Route path="season" element={<SeasonPage />} />
```

### STATUS_BADGE (白話化層藍本)
```tsx
// SOURCE: frontend/src/components/charts/SmartDashboardSection.tsx:4-16
const TSB_COLORS: Record<string,string> = { fresh:'text-green-400', optimal:'text-blue-400', tired:'text-yellow-400', overreached:'text-red-400' }
const TSB_LABELS: Record<string,string> = { fresh:'新鮮', optimal:'最佳', tired:'疲勞', overreached:'過度訓練' }
```

### COLLAPSIBLE_SECTION + PAGE
```tsx
// SOURCE: frontend/src/pages/SeasonPage.tsx:21-35,43-54
function CollapsibleSection({ title, children }) { /* ▼/▶ toggle */ }
<CollapsibleSection title="Performance Management Chart">
  <PmcChart dateFrom={range.from} dateTo={range.to} />
</CollapsibleSection>
```

### CHART_ZONES
```tsx
// SOURCE: frontend/src/components/charts/RunLoadChart.tsx:60-64
<ReferenceArea y1={0.8} y2={1.3} fill="#22c55e" fillOpacity={0.08} />
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `frontend/src/api/client.ts` | UPDATE | 新增 SportFacet/TrailLoad/TrailSummary/Interpretation 型別 |
| `frontend/src/api/hooks.ts` | UPDATE | useSportsFacets/useTrailLoad/useTrailSummary/useInterpretation/useUpdateClassification；usePmc/useRunLoad 加 sports 參數 |
| `frontend/src/components/ChartCard.tsx` | CREATE | 白話化外殼：標題+號誌+一句話 包住 children |
| `frontend/src/components/SportFilter.tsx` | CREATE | 多選 checkbox（依 facets 動態） |
| `frontend/src/pages/OverviewPage.tsx` | CREATE | 總體頁（含 SportFilter） |
| `frontend/src/pages/RunningPage.tsx` | CREATE | 跑步頁 |
| `frontend/src/pages/TrailPage.tsx` | CREATE | 越野跑頁 |
| `frontend/src/components/charts/TrailPmcChart.tsx` | CREATE | 越野 PMC：hrTSS 主 + rTSS 並陳 |
| `frontend/src/components/charts/ClimbLoadChart.tsx` | CREATE | 爬升負荷 / VAM |
| `frontend/src/App.tsx` | UPDATE | 三頁路由 |
| `frontend/src/layouts/AppShell.tsx` | UPDATE | NAV_ITEMS 三項 |
| `frontend/src/pages/SeasonPage.tsx` | DELETE/重命名 | 內容拆進三頁；保留共用片段 |

## NOT Building
- 後端端點（→ backend plan）
- 單車/游泳專屬頁
- AI 即時圖表解讀（只接規則端點/前端規則）
- Menu Bar 完整美化（走 /prp-hotfix；本計畫只把 nav 從 1 項變 3 項）

---

## Step-by-Step Tasks

### Task 1: 新增 API 型別與 hooks
- **ACTION**: client.ts 加型別；hooks.ts 加 5 個 hook，並讓 usePmc/useRunLoad 接受 `sports?: string[]`。
- **TEST FIRST (gate)**: `cd frontend && npx tsc --noEmit` — 先在 OverviewPage 引用尚未存在的 `useSportsFacets` 製造型別錯誤，確認 gate 會紅（或直接以「新增後 tsc 綠」為通過條件）。
- **IMPLEMENT**: client.ts 追加
  ```ts
  export interface SportFacet { key: string; count: number; label: string }
  export interface SportsFacetsResponse { sports: SportFacet[] }
  export interface TrailLoadPoint { date: string; ctl: number; atl: number; tsb: number; hr_tss: number; r_tss: number | null }
  export interface TrailSummaryResponse { total_gain_m: number; activity_count: number; recent: { date: string; gain_m: number; vam: number | null }[] }
  export interface ChartInterpretation { status: string; label: string; color: string; summary: string }
  ```
  hooks.ts 追加（MIRROR QUERY_HOOK）：
  ```ts
  export function useSportsFacets(athleteId = 1) {
    return useQuery<SportsFacetsResponse>({
      queryKey: ['sports-facets', athleteId],
      queryFn: () => api.get('/sports/facets', { params: { athlete_id: athleteId } }).then(r => r.data),
    })
  }
  export function useTrailLoad(athleteId = 1) {
    return useQuery<{ series: TrailLoadPoint[] }>({
      queryKey: ['trail-load', athleteId],
      queryFn: () => api.get('/analytics/trail-load', { params: { athlete_id: athleteId } }).then(r => r.data),
    })
  }
  export function useTrailSummary(athleteId = 1) {
    return useQuery<TrailSummaryResponse>({
      queryKey: ['trail-summary', athleteId],
      queryFn: () => api.get('/analytics/trail-summary', { params: { athlete_id: athleteId } }).then(r => r.data),
    })
  }
  export function useInterpretation(chart: string, athleteId = 1) {
    return useQuery<ChartInterpretation>({
      queryKey: ['interpretation', chart, athleteId],
      queryFn: () => api.get('/analytics/chart-interpretation', { params: { chart, athlete_id: athleteId } }).then(r => r.data),
    })
  }
  export function useUpdateClassification() {
    const qc = useQueryClient()
    return useMutation({
      mutationFn: (v: { id: number; trail_classification: string }) =>
        api.patch(`/workouts/${v.id}/classification`, { trail_classification: v.trail_classification }).then(r => r.data),
      onSuccess: () => { qc.invalidateQueries({ queryKey: ['workouts'] }); qc.invalidateQueries({ queryKey: ['trail-load'] }) },
    })
  }
  ```
  並把 `usePmc`/`useRunLoad` 的 params 型別加 `sports?: string[]`（axios 會序列化成重複 query）。
- **MIRROR**: QUERY_HOOK、MUTATION_HOOK
- **VALIDATE**: `npx tsc --noEmit` 綠
- **COMMIT**: `feat(sport-pages): API types + hooks for facets/trail/interpretation`

### Task 2: ChartCard 白話化外殼
- **ACTION**: 建可重用 `ChartCard`：標題 + 號誌圓點 + 一句話 summary（吃 `useInterpretation` 或直接傳 props），包住任一圖表 children。
- **TEST FIRST (gate)**: 新建檔後 `npx tsc --noEmit` 綠；瀏覽器確認號誌顏色對應。
- **IMPLEMENT**: `frontend/src/components/ChartCard.tsx`
  ```tsx
  import type { ReactNode } from 'react'
  import { useInterpretation } from '../api/hooks'

  const DOT: Record<string,string> = { green:'bg-green-400', blue:'bg-blue-400', yellow:'bg-yellow-400', red:'bg-red-400' }

  export function ChartCard({ title, chart, children }: { title: string; chart?: string; children: ReactNode }) {
    const { data } = useInterpretation(chart ?? '', 1)
    const enabled = !!chart
    return (
      <div className="space-y-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-[#7d8fa6] uppercase tracking-wide">{title}</span>
          {enabled && data && <span className={`w-2 h-2 rounded-full ${DOT[data.color] ?? 'bg-gray-500'}`} />}
          {enabled && data && <span className="text-xs text-gray-400">{data.summary}</span>}
        </div>
        {children}
      </div>
    )
  }
  ```
- **MIRROR**: STATUS_BADGE（SmartDashboardSection 顏色語彙）
- **GOTCHA**: `chart` 未提供時不呼叫解讀（避免無效請求）——hook 內可 `enabled: !!chart`（在 Task 1 hook 加 enabled）。
- **VALIDATE**: tsc 綠 + 瀏覽器看到號誌+白話
- **COMMIT**: `feat(sport-pages): ChartCard readability wrapper (status dot + plain summary)`

### Task 3: 三頁面路由與 nav
- **ACTION**: 新建 OverviewPage/RunningPage/TrailPage（先放最小骨架），App.tsx 加三路由（index 導到 /overview），AppShell NAV_ITEMS 改三項。
- **TEST FIRST (gate)**: `npx tsc --noEmit` + `npm run build` 綠；瀏覽器三個 nav 可點且各自渲染。
- **IMPLEMENT**:
  - `App.tsx`：
    ```tsx
    import { OverviewPage } from './pages/OverviewPage'
    import { RunningPage } from './pages/RunningPage'
    import { TrailPage } from './pages/TrailPage'
    // ...
    <Route index element={<Navigate to="/overview" replace />} />
    <Route path="overview" element={<OverviewPage />} />
    <Route path="running" element={<RunningPage />} />
    <Route path="trail" element={<TrailPage />} />
    ```
    （移除舊 `season` route 或保留為 redirect 到 /overview。）
  - `AppShell.tsx` NAV_ITEMS：
    ```tsx
    const NAV_ITEMS = [
      { to: '/overview', label: '總體' },
      { to: '/running',  label: '跑步' },
      { to: '/trail',    label: '越野跑' },
      { to: '/activities', label: 'Activities' },
      { to: '/config', label: 'Config' },
      { to: '/ai', label: 'AI' },
      { to: '/coros', label: 'Coros' },
    ]
    ```
  - 三頁面骨架先各放標題 + DateRangePicker（複製 SeasonPage 頭部）。
- **MIRROR**: ROUTE、NAV_ITEM、SeasonPage 頭部
- **VALIDATE**: build 綠 + 三 nav 可切換
- **COMMIT**: `feat(sport-pages): three independent pages + nav (overview/running/trail)`

### Task 4: 跑步頁內容
- **ACTION**: RunningPage 放跑步相關圖表（PMC/RunLoad/Intensity/RunVolume），用 ChartCard 包裝，呼叫端點帶 `sports=['running']`、trail 排除。
- **TEST FIRST (gate)**: tsc + build 綠；瀏覽器跑步頁只見跑步資料。
- **IMPLEMENT**: 比照 SeasonPage 組成，但每個 CollapsibleSection 換成 `ChartCard`（傳 chart 識別字串給解讀層）：
  ```tsx
  <ChartCard title="跑步 PMC" chart="run-pmc">
    <RunLoadChart dateFrom={range.from} dateTo={range.to} />
  </ChartCard>
  ```
  RunLoadChart 既已只取 running（後端預設），無需改參數。
- **MIRROR**: COLLAPSIBLE_SECTION + PAGE
- **VALIDATE**: build 綠 + 視覺
- **COMMIT**: `feat(sport-pages): running page content`

### Task 5: 總體頁 + 多選 SportFilter
- **ACTION**: 建 `SportFilter`（依 `useSportsFacets` 動態 checkbox），OverviewPage 用其 state 帶 `sports[]` 給各圖表 hook；篩選只在此頁。
- **TEST FIRST (gate)**: tsc + build 綠；瀏覽器勾選/取消即時改變圖表；切到跑步頁不受影響。
- **IMPLEMENT**: `SportFilter.tsx`
  ```tsx
  import { useSportsFacets } from '../api/hooks'
  export function SportFilter({ selected, onChange }: { selected: string[]; onChange: (s: string[]) => void }) {
    const { data } = useSportsFacets(1)
    const toggle = (k: string) => onChange(selected.includes(k) ? selected.filter(x => x !== k) : [...selected, k])
    return (
      <div className="flex flex-wrap gap-3">
        {data?.sports.map(s => (
          <label key={s.key} className="flex items-center gap-1.5 text-xs text-[#a9b6c8] cursor-pointer">
            <input type="checkbox" checked={selected.includes(s.key)} onChange={() => toggle(s.key)} />
            {s.label} <span className="text-gray-600">({s.count})</span>
          </label>
        ))}
      </div>
    )
  }
  ```
  OverviewPage：`const [sports, setSports] = useState<string[]>([])`（空=全部），預設由 facets 載入後全選；把 `sports` 傳入 `usePmc({ ...range, sports })` 等。
- **MIRROR**: QUERY_HOOK、StatCard 樣式語彙
- **GOTCHA**: 篩選 state 留在 OverviewPage 內，不放全域，確保不外溢到跑步/越野頁（AC-4）。
- **VALIDATE**: build 綠 + 勾選即時生效 + 不影響他頁
- **COMMIT**: `feat(sport-pages): overview page with multi-select sport filter`

### Task 6: 越野 PMC 圖表（hrTSS 主 + rTSS 並陳）
- **ACTION**: 建 `TrailPmcChart`，吃 `useTrailLoad`，畫 CTL/ATL/TSB（hrTSS 基底）並以次線/次軸並陳 rTSS。
- **TEST FIRST (gate)**: tsc + build 綠；瀏覽器越野頁見雙指標。
- **IMPLEMENT**: 比照 RunLoadChart 的 ComposedChart 雙軸；series 來自 `useTrailLoad().data.series`，主軸 ctl/atl/tsb，rTSS 以虛線並陳。
  ```tsx
  import { ComposedChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
  import { useTrailLoad } from '../../api/hooks'
  export function TrailPmcChart() {
    const { data } = useTrailLoad(1)
    const series = data?.series ?? []
    return (
      <ResponsiveContainer width="100%" height={280}>
        <ComposedChart data={series}>
          <XAxis dataKey="date" /><YAxis yAxisId="l" /><YAxis yAxisId="r" orientation="right" />
          <Tooltip />
          <Line yAxisId="l" dataKey="ctl" stroke="#3b82f6" dot={false} name="體能(CTL)" />
          <Line yAxisId="l" dataKey="atl" stroke="#ef4444" dot={false} name="疲勞(ATL)" />
          <Line yAxisId="l" dataKey="tsb" stroke="#22c55e" dot={false} name="狀態(TSB)" />
          <Line yAxisId="r" dataKey="r_tss" stroke="#a78bfa" strokeDasharray="4 4" dot={false} name="rTSS(並陳)" />
        </ComposedChart>
      </ResponsiveContainer>
    )
  }
  ```
- **MIRROR**: RunLoadChart ComposedChart 雙軸
- **VALIDATE**: build 綠 + 視覺雙指標
- **COMMIT**: `feat(sport-pages): trail PMC chart (hrTSS primary, rTSS alongside)`

### Task 7: 爬升負荷 / VAM 圖表 + 越野頁組裝
- **ACTION**: 建 `ClimbLoadChart`（吃 `useTrailSummary`，畫每次活動 gain/VAM 趨勢）；TrailPage 組裝 ChartCard(越野PMC) + ClimbLoad + 複用 TrailAnalysisSection（單次 GAP/climb）。
- **TEST FIRST (gate)**: tsc + build 綠；越野頁完整呈現。
- **IMPLEMENT**: ClimbLoadChart 用 BarChart 畫 `recent[].gain_m`，line 疊 `vam`。TrailPage：
  ```tsx
  <ChartCard title="越野 PMC" chart="trail-pmc"><TrailPmcChart /></ChartCard>
  <ChartCard title="爬升負荷 / 垂直速度" chart="climb-load"><ClimbLoadChart /></ChartCard>
  ```
- **MIRROR**: WeeklyLoadChart/RunVolumeLog bar 模式、TrailAnalysisSection 複用
- **VALIDATE**: build 綠 + 越野頁三圖到位 + 白話號誌
- **COMMIT**: `feat(sport-pages): climb load chart + trail page assembly`

### Task 8: 清理舊 SeasonPage 與收尾
- **ACTION**: 移除/重導 SeasonPage（內容已拆三頁），確認 `/season` redirect 到 `/overview`，無死連結；既有 SmartDashboardSection 放到總體頁。
- **TEST FIRST (gate)**: `npx tsc --noEmit` + `npm run build` 全綠；grep 無殘留 `SeasonPage` import。
- **IMPLEMENT**: 刪 `pages/SeasonPage.tsx`（或留薄 redirect）；App.tsx 移除其 import；總體頁頂部加 `<SmartDashboardSection />`。
- **VALIDATE**: build 綠 + 全頁手測
- **COMMIT**: `refactor(sport-pages): retire SeasonPage, route /season → /overview`

---

## Testing Strategy

> 前端無單元測試框架；balanced rigor 以型別+建置+瀏覽器驗證為主。

### Manual / Visual Checklist
- [ ] 三 nav（總體/跑步/越野跑）皆可點、各渲染對應內容
- [ ] 總體頁 checkbox 勾選/取消即時改變所有圖表
- [ ] 切到跑步/越野頁，總體頁的篩選不影響（AC-4 獨立性）
- [ ] 跑步頁不含越野/單車；越野頁只含 trail（AC-3）
- [ ] 越野 PMC 同時顯示 hrTSS 基底與 rTSS 並陳線（AC-5）
- [ ] 每張圖上方有號誌圓點 + 一句話白話（AC-6）

### Edge Cases Checklist
- [ ] facets 為空（新帳號）→ SportFilter 不崩、顯示空
- [ ] trail-load 無資料 → 圖表空狀態不崩
- [ ] interpretation 端點失敗 → ChartCard 仍渲染圖表（號誌降級隱藏）

---

## Validation Commands

### Static Analysis
```bash
cd "<repo>reverse/frontend"
npx tsc --noEmit
```
EXPECT: 零型別錯誤

### Build
```bash
cd "<repo>reverse/frontend"
npm run build
```
EXPECT: build 成功

### Browser Validation
```bash
cd "<repo>reverse"
# 依專案啟動方式（start.sh / docker-compose）啟前後端後手測上方 checklist
```
EXPECT: Manual/Visual Checklist 全綠

---

## Acceptance Criteria
- [ ] AC-3 三頁面資料獨立（視覺驗證）
- [ ] AC-4 總體頁多選篩選且不外溢
- [ ] AC-5 越野 PMC 雙指標
- [ ] AC-6 圖表白話化（號誌+一句話）
- [ ] tsc 零錯誤、build 成功

## Completion Checklist
- [ ] 沿用既有 React Query / Recharts / Tailwind 模式
- [ ] 號誌語彙與 SmartDashboardSection 一致
- [ ] 無殘留 SeasonPage 死連結
- [ ] 篩選 state 局部於總體頁
- [ ] 自足，無需實作中再查碼

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| 無前端測試框架，回歸靠手測 | M | M | tsc+build gate + 明確視覺 checklist；必要時引入 vitest（額外決策） |
| 篩選 state 外溢他頁 | L | M | state 局部於 OverviewPage，不放全域 |
| 後端端點未就緒 | M | H | 依賴 backend plan 先完成；hook enabled 防呼叫空端點 |

## Notes
nav 從 1 項變 3 項屬本計畫；完整 Menu Bar 美化走 `/prp-hotfix`。若決定引入前端測試框架（vitest + @testing-library），可把本計畫的視覺 gate 升級為元件測試——屬獨立決策。
