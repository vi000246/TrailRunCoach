# Spec Roadmap

> Auto-updated index. Last updated: 2026-06-13
>
> **AI Agents**: Read this file first to decide which specs to load. Load only what's relevant to your task to avoid context bloat.

## Module Index

| Module | Spec | Domain Layer | Description | Sub-modules |
|--------|------|--------------|-------------|-------------|
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
| 2026-06-13 | coros-sync | [unified-sync-page-data-inventory.srs.md](../srs/coros-sync-unified-sync-page-data-inventory.srs.md) | 統一同步頁：TP 下載接 UI、已載入資料盤點端點 |
| 2026-06-13 | sport-pages | [multi-sport-views-trail-analytics.srs.md](../srs/completed/sport-pages-multi-sport-views-trail-analytics.srs.md) | 三頁面分流、越野跑專屬圖表、trail 分類持久化、圖表白話化、公式驗證工具 |
