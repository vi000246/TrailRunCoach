# WKO5 Coach Frontend Redesign — WKO5-Inspired Training Analysis UI

## Problem Statement

現有的 Web UI 視覺上幾乎是無樣式狀態：元件全部使用 Tailwind utility class，但 **Tailwind CSS 根本沒有安裝**，導致所有 `bg-gray-900`、`text-gray-400` 等 class 完全不生效，呈現出原生 HTML 的醜陋外觀。
除此之外，Activities 和 AI 兩個 tab 都只是「coming soon」空殼，Config tab 缺乏 FTP/LTHR 設定欄位，整個應用缺乏設計系統、缺乏視覺層次感，作為訓練資料分析工具的使用體驗遠遠不夠。
不解決這些問題，即使後端算法正確，工具也很難用，資料的洞察力無法有效傳遞。

## Evidence

- `frontend/package.json` 無任何 `tailwindcss` 依賴，但 Dashboard/PmcChart/TabNav/DashboardGrid 全部使用 Tailwind class
- `main.tsx` 直接渲染 `<Dashboard />`；`App.tsx` 仍是 Vite 預設模板（從未整合）
- `activeTab === 'activities'` 和 `activeTab === 'ai'` 分支都只顯示「coming soon」文字
- `ConfigTab.tsx` 僅有基礎設定，缺乏 Power Zone 和 HR Zone 視覺化展示
- DashboardGrid 使用 react-grid-layout，但 grid 寬度硬寫為 1200px，且後端 dashboard API 幾乎不填充 widget

## Proposed Solution

完整重建前端設計系統：安裝 Tailwind CSS v4（CSS-first 設定，無需 config 檔案），整合 shadcn/ui 元件庫（dark theme 基底），定義 WKO5 風格的設計 token（近黑背景、資料密集、專業運動分析色彩）。
同時建立 React Router 路由系統，補齊所有缺少的頁面（Activity List、Activity Detail、Season 增強、Config 增強、AI Chat 入口），並加入 3 個缺失的後端 API endpoints 以支援新頁面。

## Key Hypothesis

We believe 安裝 Tailwind + shadcn/ui + WKO5 風格設計 token，will 讓前端從無樣式狀態變為視覺上專業的訓練分析工具，for 自我訓練的個人運動員。
We'll know we're right when 開啟瀏覽器後，看到和 WKO5 dashboard 類似的深色、資料密集、多圖表佈局，所有 4 個 tab 均有實際內容。

## What We're NOT Building

- **Mobile-first RWD** — 個人本機工具，以桌面 1280px+ 為主，響應式為輔
- **動態 Dashboard 自訂（drag & drop widget）** — react-grid-layout 方案暫時移除，改為固定佈局；若未來需要再加回
- **多主題 / 亮色模式** — 只做深色主題，對標 WKO5 風格
- **動畫 / 過場效果** — 保持靜態，數據優先
- **完整 AI Chat 實作（Claude API 整合）** — AI 頁面此 milestone 只做「如何連接 MCP server」說明頁，不實作即時對話

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| Tailwind class 生效 | 100%（視覺確認） | 開啟 devtools 確認 computed style |
| 頁面完整度 | 4 個 tab 均有實際內容 | 手動點選每個 tab |
| Activity Detail 可用 | 點開單次活動可看到功率/心率圖表 | 手動測試 |
| Config 設定可運作 | 修改 FTP 後 PMC 重算 | 手動測試 + API 觀察 |
| 主觀視覺品質 | 「看起來像 WKO5 那樣」（使用者自評） | 使用者主觀評估 |

## Open Questions

- [ ] Activity Detail 的時序圖需要後端新增 `/workouts/{id}/timeseries` endpoint；FIT 解析器已有 `parse_fit()` 但需要加 downsampling（目前測試資料最長約 3 小時，1Hz = 10800 資料點）
- [ ] Config tab 的 FTP 設定是否需要「覆蓋歷史 TSS」重算功能？（Milestone 4.5 規格）
- [ ] shadcn/ui 最新版對 Tailwind v4 的支援度，可能需要使用 canary 版本
- [ ] Activity Detail 中是否要整合地圖（GPS route）？目前 FIT 解析器有 lat/lon，但地圖需要額外依賴（leaflet 等）— 建議此 milestone 先跳過

---

## Users & Context

**Primary User**
- **Who**: 個人運動員，用 Coros 手錶記錄，技術能力強，熟悉 WKO5 桌面版
- **Current behavior**: 依賴 WKO5 桌面版查看訓練分析，偶爾查看 Coros app 基礎統計
- **Trigger**: 完成訓練後想在 Web UI 中快速看到相同品質的分析資訊
- **Success state**: 開啟 `localhost:8000`，看到和 WKO5 相同風格的深色介面，可以查看 PMC 趨勢、點開單次活動看功率曲線、設定 FTP

**Job to Be Done**
When 想分析訓練資料時，I want to 開啟一個視覺上清晰、資訊密集的 Web 介面，so I can 快速取得和 WKO5 一樣的訓練洞察，不需要開啟桌面版。

**Non-Users**
不考慮其他用戶、非技術背景使用者、行動裝置用戶（優先順序低）。

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | 安裝 Tailwind v4 + 設計 token | 沒有樣式等於沒有 UI |
| Must | shadcn/ui 元件庫整合 | 提供統一的深色主題元件基底 |
| Must | WKO5 風格色彩系統（深色，accent 紫/青/紅/綠） | 對標 WKO5 視覺語言 |
| Must | React Router 路由（`/`, `/activities`, `/activities/:id`, `/config`, `/ai`） | Activity Detail 需要獨立 URL |
| Must | Activity List 頁面（分頁列表、sport 篩選、日期篩選） | 現在是空 stub |
| Must | Activity Detail 頁面（功率時序、MMP 曲線、指標摘要） | 現在是空 stub |
| Should | Season 頁面增強（週載量圖 + PMC 改善、日期篩選 preset） | PMC 已存在但設計粗糙 |
| Should | Config 頁面增強（FTP/LTHR 表單 + Power/HR Zone 展示） | 現有 ConfigTab 不完整 |
| Should | Chart 主題（recharts 套用 WKO5 風格色彩） | 圖表目前使用預設色彩 |
| Could | 全局 Header（同步狀態、最後同步時間、Scan 按鈕） | CorosPanel 目前嵌在 SeasonTab |
| Could | AI 頁面（MCP Server 連接說明 + 預設問題列表） | 先做說明頁，後做 chat |
| Won't | Drag & drop widget 自訂 Dashboard | 移除 react-grid-layout 複雜度 |
| Won't | 地圖路線顯示（GPS route） | 超出本 milestone 範疇 |

### MVP Scope

安裝 Tailwind + shadcn/ui → 定義設計 token → React Router → Activity List + Detail 兩頁面可用 → Season/Config 使用新設計系統重構。

### User Flow

```
首頁 (/) → Season Tab（PMC 圖表 + 週載量）
  ↓ 點 Activities tab
Activity List（分頁列表）
  ↓ 點一筆活動
Activity Detail /activities/:id（功率時序 + MMP + 指標摘要）
  ↓ 返回
Config Tab（FTP/LTHR 設定 → 儲存 → PMC 重算）
```

---

## Feasibility

**Verdict**: HIGH — Tailwind v4 + shadcn/ui + React Router 均為成熟工具，後端 FastAPI 只需新增 3 個 endpoint，現有 FIT 解析器已能提供時序資料。

> Architecture, data model, API contracts, technology choices, and detailed technical risks belong in the SRS. Run `/prp-srs docs/prd/wko5-frontend-redesign.prd.md` to produce that next.

---

## Product Milestones

| # | Milestone | User-Visible Value | Status | Depends | SRS | Plan |
|---|-----------|--------------------|--------|---------|-----|------|
| 1 | 樣式基礎（Tailwind + Design System） | UI 從無樣式變為 WKO5 風格深色外觀 | complete | - | wko5-frontend-redesign.spec.md | wko5-mvp-tab-nav-config-season.plan.md |
| 2 | 路由 + Shell | 瀏覽器 URL 對應頁面，有一致的 Header/Nav | complete | 1 | same | same |
| 3 | Activity List 頁面 | 可看到所有訓練列表，可篩選 sport / 日期 | complete | 2 | same | same |
| 4 | Activity Detail 頁面 | 點開單次訓練可看功率曲線、MMP、指標 | complete | 3 | same | same |
| 5 | Season + Config 頁面重構 | PMC/週載量有新設計；Config 可設定 FTP/LTHR | complete | 1 | same | same |
| 5.1 | Run Training Load Charts | 5 個跑步訓練負荷圖表（Run PMC、Daily %CTL、Ramp Rate、Intensity Load、Volume Log）出現在 Season 頁面 | complete | 5 | wko5-training-load-charts.spec.md | docs/plans/completed/wko5-training-load-charts.plan.md |
| 6 | AI 頁面入口 | 有說明頁告知如何連接 MCP server | pending | 2 | same | same |

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| CSS 框架 | Tailwind CSS v4（@tailwindcss/vite plugin） | Tailwind v3、Sass、CSS Modules | v4 無需 config 檔，與 Vite 8 整合最佳；已有大量 v4 production usage in 2026 |
| 元件庫 | shadcn/ui（latest，支援 Tailwind v4） | Radix 純用、MUI、Ant Design | shadcn 可複製原始碼自訂，dark theme 原生支援，輕量 |
| 路由 | React Router v7 | TanStack Router、Next.js | 現有專案是 SPA + Vite，React Router v7 Data Mode 最自然 |
| 動態 Dashboard | 移除 react-grid-layout | 保留並修復 | 固定佈局實作更快，後端 dashboard API 目前欠完善 |
| Navigation | Header tab bar（仿 WKO5 頂部 nav） | 左側 Sidebar | WKO5 桌面版使用頂部 tab bar 風格 |

---

## Research Summary

**現有前端技術現況**
- React 19 + TypeScript 6 + Vite 8（已安裝）
- react-query v5、zustand v5、recharts v3、lucide-react（已安裝）
- react-grid-layout（已安裝但使用效果差，考慮移除）
- **Tailwind：0 安裝，所有 class 無效**
- `main.tsx` 直接渲染 `<Dashboard />`，`App.tsx` 未使用

**WKO5 設計語言（逆向觀察）**
- 背景：深藍黑 `#07090f`；Panel：`#0e1117`；Border：`#1c2333`
- 主 accent：紫色 `#7c3aed`（Power、CTL），已在現有 code 中使用
- 輔助 accent：青色（CTL fitness）、橙色（ATL fatigue）、綠色（TSB form +）、黃色（Performance highlight）
- 字體：系統字體，size 11-13px for labels，14-16px for values，dense
- 圖表風格：深色背景、低對比 grid（`#1c2333`）、dot=false 曲線

**後端 API 現況（3 個 endpoint 需新增）**
- 現有：`/workouts`（列表）、`/workouts/{id}`（摘要）、`/workouts/{id}/mmp`、`/pmc`、`/athletes/{id}/settings`
- 缺少：`/workouts/{id}/timeseries`、`/workouts/{id}/zones`、`/analytics/weekly`

---

*Generated: 2026-05-15*
*Status: DRAFT*
*Source Linear Issue: N/A — standalone*
