# Spec Roadmap

> Auto-updated index. Last updated: 2026-10-08
>
> **AI Agents**: Read this file first to decide which specs to load. Load only what's relevant to your task to avoid context bloat.

## Module Index

| Module | Spec | Domain Layer | Description | Sub-modules |
|--------|------|--------------|-------------|-------------|
| wko5-engine | [wko5-engine.spec.md](./wko5-engine.spec.md) | Core Domain | 直接讀 WKO5 二進位檔、逐位元驗證的指標演算法、WKO5 表達式引擎、parity/自有算式雙模式、核准制資料校正、自訂圖表；圖表頁：日/週/月/季/年切換、render cache、放大與 &chart= 連結、Leaflet 路線地圖與同步 hover | — |
| coros-sync | [wko5-coros-sync.spec.md](./wko5-coros-sync.spec.md) | Supporting | COROS / TrainingPeaks 同步：每來源 FIT 資料夾、增量 cursor、跨來源去重、排程與開站自動同步、資料夾掃描、同步設定（含圖表資料來源、路線圖預設） | — |
| overview | [overview.spec.md](./overview.spec.md) | Core Domain | 首頁「總覽」：全部運動合計的訓練狀況、週/月/年紀錄、整合 PMC；可編輯的儲存課表（reconcile、多週預估），可依日/週/周期推送到 COROS | — |
| racepower | [racepower.spec.md](./racepower.spec.md) | Core Domain | 賽事功率：SuperPower 計算機移植到自己的活動資料，含越野／百岳模型與賽日天氣 | — |
| wko5-chart-units | [wko5-chart-units.spec.md](./wko5-chart-units.spec.md) | Supporting | 圖表單位登錄表、公制顯示、WKO5 圖表設計修正（views/wko5_fixes.json）、週期切換後的標題/圖例/分桶軸顯示 | — |
| workout-review | [workout-review.spec.md](./workout-review.spec.md) | Core Domain | 單次活動判讀：依課表類型自動判讀（飄移、間歇、爬坡、耐久、跑姿參考）、8–12 週自身基準、飄移連續次數與 CP 測試回饋進度決策 | — |
| plan-auto | [plan-auto.spec.md](./plan-auto.spec.md) | Core Domain | 自動調整課表：同步後比對完成／未完成、依規則調整本週、重排後續週並推送 COROS；強度階梯、Zone 3／Zone 5 關卡（含 A 賽後重新打底）、AeT 測試、傷停與生病回歸、CP 變動重推 | — |
| workouts | [workouts.spec.md](./workouts.spec.md) | Supporting | 單次活動中繼資料：地形分類、活動類型／努力程度／名稱／標籤／傷痛標記（自動值＋使用者覆寫）、壞檔排除、活動編輯頁 | — |
| route-progress | [route-progress.spec.md](./route-progress.spec.md) | Supporting | 自動偵測重複路段與路線（類 Strava segments），逐次比較時間、VAM、心率、功率與天氣 | — |

## Loading Guide

| Task Type | Load These Specs |
|-----------|-----------------|
| 實作特定子模組功能 | 該子模組 spec + parent spec |
| 跨模組整合 | 相關模組各自的 root spec |
| 第一次理解系統 | 先讀本 SPEC_ROADMAP，再按需載入 |

## Recent Feature Changes

| Date | Module | Feature SRS | One-line Summary |
|------|--------|-------------|-----------------|
| 2026-10-08 | plan-auto | — | code-sync：補 SP-117 生病、SP-116 A 賽後重新打底、SP-98 回量期、SP-110 疲勞保險、SP-66 單次長度上限、SP-71 每週課表存檔、SP-266 心率可疑的飄移只當參考；Known limits 新增設定變更不重排、LTHR 先驗可能被當實測；11 個錨點重指 |
| 2026-10-04 | 全部 12 個模組 | — | code-sync：依 09-30～10-03 約 700 個 commit 刷新所有 module spec（錨點重指、補 Domain Model、新增端點與行為）；5 份 5 月的 SRS 由 `docs/spec/` 移至 `docs/srs/completed/`；5 月的 PRD／plan／SRS 全部標 CANCELED（React SPA 移除） |
| 2026-09-30 | workout-review | — | code-sync 建立：單次活動判讀卡（課表類型、Pa:HR 飄移、努力段、爬坡、耐久、跑姿）、單次活動判讀 view、飄移連續次數與 CP 測試回饋 status／本週課表 |
| 2026-09-30 | overview | — | code-sync 建立：全部運動合計的首頁、週/月/年彙總、PMC 預估、規則式本週課表 |
| 2026-09-30 | racepower | — | code-sync 建立：SuperPower 計算機移植、越野努力距離＋個人 RE、百岳 EP/h 模型、CWA／Open-Meteo 天氣 |
| 2026-09-30 | wko5-chart-units | — | code-sync 建立：單位登錄表、非對照模式公制化、50 筆 WKO5 圖表修正 |
| 2026-09-29 | wko5-engine | — | code-sync 建立：WKO5 檔案解析、驗證演算法（NP 359/359、hrTSS 1030/1030、mean-max 49,188 點）、表達式引擎、雙模式、資料校正、自訂圖表 |
| 2026-06-13 | ai-coach | [knowledge-driven-prescription.srs.md](../srs/ai-coach-knowledge-driven-prescription.srs.md) | 知識驅動處方：curated 知識 + zone 計算 + 處方 prompt |
| 2026-06-13 | coros-sync | [unified-sync-page-data-inventory.srs.md](../srs/completed/coros-sync-unified-sync-page-data-inventory.srs.md) | 統一同步頁：TP 下載接 UI、已載入資料盤點端點 |
| 2026-06-13 | sport-pages | [multi-sport-views-trail-analytics.srs.md](../srs/completed/sport-pages-multi-sport-views-trail-analytics.srs.md) | 三頁面分流、越野跑專屬圖表、trail 分類持久化、圖表白話化、公式驗證工具 |
