# Spec Roadmap

> Auto-updated index. Last updated: 2026-09-29
>
> **AI Agents**: Read this file first to decide which specs to load. Load only what's relevant to your task to avoid context bloat.

## Module Index

| Module | Spec | Domain Layer | Description | Sub-modules |
|--------|------|--------------|-------------|-------------|
| wko5-engine | [wko5-engine.spec.md](./wko5-engine.spec.md) | Core Domain | 直接讀 WKO5 二進位檔、逐位元驗證的指標演算法、WKO5 表達式引擎、parity/自有算式雙模式、核准制資料校正、自訂圖表 | — |
| sport-pages | [sport-pages.spec.md](./sport-pages.spec.md) | Core Domain | 把單一 SeasonPage 拆成總體/跑步/越野跑三頁、持久化 trail 分類、越野跑專屬圖表與圖表白話化層 | — |
| coros-sync | [wko5-coros-sync.spec.md](./wko5-coros-sync.spec.md) | Supporting | Coros 非官方 API 自動下載 FIT，免依賴 WKO5/TrainingPeaks | — |
| ai-coach | [ai-coach-training-analysis.spec.md](./ai-coach-training-analysis.spec.md) | Core Domain | In-app AI Coach chat、Smart Dashboard、Trail Analysis | — |
| training-load-charts | [wko5-training-load-charts.spec.md](./wko5-training-load-charts.spec.md) | Core Domain | 逆向 WKO5 的五張 run-specific 訓練負荷圖表 | — |

## Loading Guide

| Task Type | Load These Specs |
|-----------|-----------------|
| 實作特定子模組功能 | 該子模組 spec + parent spec |
| 跨模組整合 | 相關模組各自的 root spec |
| 第一次理解系統 | 先讀本 SPEC_ROADMAP，再按需載入 |

## Recent Feature Changes

| Date | Module | Feature SRS | One-line Summary |
|------|--------|-------------|-----------------|
| 2026-09-29 | wko5-engine | — | code-sync 建立：WKO5 檔案解析、驗證演算法（NP 359/359、hrTSS 1030/1030、mean-max 49,188 點）、表達式引擎、雙模式、資料校正、自訂圖表 |
| 2026-06-13 | ai-coach | [knowledge-driven-prescription.srs.md](../srs/ai-coach-knowledge-driven-prescription.srs.md) | 知識驅動處方：curated 知識 + zone 計算 + 處方 prompt |
| 2026-06-13 | coros-sync | [unified-sync-page-data-inventory.srs.md](../srs/completed/coros-sync-unified-sync-page-data-inventory.srs.md) | 統一同步頁：TP 下載接 UI、已載入資料盤點端點 |
| 2026-06-13 | sport-pages | [multi-sport-views-trail-analytics.srs.md](../srs/completed/sport-pages-multi-sport-views-trail-analytics.srs.md) | 三頁面分流、越野跑專屬圖表、trail 分類持久化、圖表白話化、公式驗證工具 |
