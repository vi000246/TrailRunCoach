# WKO5 Reverse + Personal Training AI

## Problem Statement

作為個人運動員，市面上的訓練分析工具（WKO5、TrainingPeaks）需要訂閱費用、僅支援桌面、且無法用自然語言問答。
目前缺乏一個可以整合 Coros/Garmin 裝置資料、在雲端運行、並能用 AI 對話分析訓練紀錄的個人工具。
不解決這個問題，訓練洞察就被鎖在付費軟體的黑盒子裡，無法客製化或自動化。

## Evidence

- WKO5 Build 590 已安裝於本機，源碼路徑洩漏至 `/Users/kevin/Projects/WKO5/PowerAppOSX/PAApp.mm`
- 靜態分析確認：`activateLicense:`、`syncWithTrainingPeaks:` 等方法存在於主二進位，授權依賴 TrainingPeaks 帳號
- PowerKitOSX.framework 包含完整的訓練分析邏輯（MMP、FTP、iLevels 等）但以 C++ 編譯，無公開 API
- WKO5 原生支援 `/Volumes/GARMIN` 掛載，但無 Coros 直接整合
- FIT Protocol 是 Garmin 開放標準，不需要逆向即可解析

## Proposed Solution

逆向工程 PowerKitOSX.framework 取得算法邏輯，以 Python 重新實作核心訓練指標計算（MMP 曲線、FTP 估算、iLevels），
並在此基礎上建立一個對話式 AI 訓練分析系統，接受手動或自動匯入的 FIT 檔案作為資料來源。
最終系統作為個人工具在本機或輕量雲端環境運行，不公開發布。

## Key Hypothesis

We believe 將 WKO5 的功率分析算法與 Claude AI 對話能力結合，will 讓我能用自然語言即時查詢並理解自己的訓練狀況，for 個人使用。
We'll know we're right when 能問「本週訓練量如何」並得到基於真實 FIT 數據的有意義回答。

## What We're NOT Building

- TrainingPeaks 替代品或競爭產品 — 純個人工具，不公開
- 完整的 WKO5 UI 複製品 — 只取核心算法，不做視覺化 dashboard
- 多用戶平台 — 單人使用，無帳號系統
- 商業 API 服務 — 不對外提供 API

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| FIT 解析正確率 | 100% 對比 WKO5 顯示值 | 用同一份 FIT 檔對照 WKO5 輸出 |
| MMP 曲線誤差 | ±1% vs WKO5 | 5 份訓練紀錄對照測試 |
| AI 問答相關度 | 主觀 ≥4/5 | 個人評分 10 個問題 |
| Coros 自動同步延遲 | <1 小時 | 訓練後到資料可查詢的時間 |

## Open Questions

- [ ] Coros 是否有非官方 API？需研究是否有開發者逆向過其雲端 API
- [ ] iLevels 算法是否在 GoldenCheetah 中有開源實作可參考？
- [ ] PowerKitOSX 的 C++ 函數去混淆後是否可讀？（需實際用 Ghidra 確認）
- [ ] TrainingPeaks API 的 auth token 格式（JWT？OAuth 2.0？）
- [ ] 部署方式最終決策（本機 CLI vs. 輕量雲端）

---

## Users & Context

**Primary User**
- **Who**: 個人運動員（使用者本人），具備技術能力，使用 Coros 手錶記錄訓練
- **Current behavior**: 用 WKO5 桌面版手動查看訓練數據，需開啟 app 才能分析
- **Trigger**: 完成訓練後想立刻了解訓練品質，或週期性回顧訓練趨勢
- **Success state**: 直接問 AI「上週的訓練強度夠嗎」，獲得基於功率數據的具體回答

**Job to Be Done**
When 完成一次訓練或想規劃下次訓練，I want to 快速理解訓練資料顯示的狀況，so I can 做出有根據的訓練調整決策。

**Non-Users**
不為其他用戶設計，不考慮非技術背景使用者，不做多人協作功能。

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | FIT 檔案解析（Coros/Garmin）| 所有分析的資料基礎 |
| Must | Mean Maximal Power (MMP) 曲線計算 | 訓練分析核心指標 |
| Must | Claude AI 對話介面（問訓練狀況）| 主要使用情境 |
| Should | FTP 自動估算 | 個人化訓練區間依賴此值 |
| Should | iLevels / Training Levels 計算 | WKO5 核心差異化功能 |
| Should | Coros 雲端自動同步 | 減少手動匯入摩擦 |
| Could | TrainingPeaks API 反向工程 | 可選的資料同步來源 |
| Could | 訓練負荷趨勢（CTL/ATL/TSB）| 長期狀態追蹤 |
| Won't | 視覺化 Dashboard | 對話介面已足夠，避免過度設計 |
| Won't | 公開 API 或多用戶 | 超出個人工具範疇 |

### MVP Scope

1. 讀取 `.fit` 檔（手動匯入）
2. 計算 MMP 曲線（1s 到 60min）
3. 將資料送入 Claude API，支援自然語言問答

### User Flow

```
FIT 檔 → 解析 → 計算 MMP → 存入本機 DB → 問 AI「本週訓練怎樣？」→ 得到基於數據的回答
```

---

## Feasibility

**Verdict**: MEDIUM — FIT 解析與 MMP 算法可行，Coros 自動同步和 iLevels 算法需額外逆向工作。

> 架構、資料模型、API 合約、技術選型及詳細技術風險屬於 SRS。執行 `/prp-srs .claude/PRPs/prd/wko5-training-ai.prd.md` 產出下一步。

---

## Product Milestones

| # | Milestone | User-Visible Value | Status | Depends | SRS | Plan |
|---|-----------|--------------------|--------|---------|-----|------|
| 1 | FIT 解析 + MMP 計算 | 能看到自己每次訓練的 MMP 曲線數值 | pending | - | - | - |
| 2 | AI 對話介面 | 能用中文問「本週訓練強度」並獲得數據驅動的回答 | pending | 1 | - | - |
| 3 | FTP 估算 + iLevels | 能自動算出訓練區間，不需手動設定 | pending | 1 | - | - |
| 4 | Coros 自動同步 | 訓練後資料自動進系統，無需手動匯入 | pending | 1 | - | - |
| 5 | TrainingPeaks API 整合 | 歷史資料可從 TP 帳號匯入 | **in progress** | 1 | wko5-web-full-clone.spec.md | - |

### Milestone Details

**Milestone 1: FIT 解析 + MMP 計算**
- **User can now**: 匯入一份 FIT 檔並得到 MMP 曲線數值（對比 WKO5 驗證）
- **Success signal**: MMP 數值誤差在 ±1% 以內
- **Out of scope**: AI 介面、自動同步

**Milestone 2: AI 對話介面**
- **User can now**: 問「本週我跑了幾公里、平均功率多少」並獲得正確答案
- **Success signal**: 連續 10 個問題中，8 個以上得到有意義的回答
- **Out of scope**: iLevels、自動同步

**Milestone 3: FTP 估算 + iLevels**
- **User can now**: 系統自動計算個人化訓練區間
- **Success signal**: FTP 估算值與 WKO5 計算值誤差 ±5W 以內
- **Out of scope**: 自動同步

**Milestone 4: Coros 自動同步**
- **User can now**: 訓練結束後 1 小時內，資料自動出現在系統中
- **Success signal**: 連續 5 次訓練均自動同步成功
- **Out of scope**: TrainingPeaks 整合

**Milestone 5: TrainingPeaks API 整合**
- **User can now**: 一鍵匯入 TP 帳號的歷史訓練資料
- **Success signal**: 成功匯入歷史資料（需驗證 API 可逆向）
- **Out of scope**: N/A

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| MVP 資料來源 | 手動匯入 FIT | 自動同步 | 最快驗證算法正確性，不依賴 API 逆向 |
| AI 介面 | 對話式（Claude API） | 靜態報告 | 使用者明確偏好 |
| 算法參考來源 | 逆向 PowerKitOSX.framework + GoldenCheetah 參考 | 純自研 | 已知 GoldenCheetah 有部分開源實作可作交叉驗證 |
| 不公開 | 個人工具 | 開源 | 法律考量（逆向商業軟體）|

---

## Research Summary

**逆向工程現況**
- WKO5 主二進位：Universal Binary (x86_64 + arm64)，Objective-C/C++ 混合
- PowerKitOSX.framework：核心算法所在，C++ 符號部分可讀（`decodeBase32`、`gzcompress` 等）
- FIT_SDK2.framework：Garmin 開放 SDK，不需逆向
- 授權機制：`activateLicense:` + TrainingPeaks 帳號 + Keychain token 儲存
- Microsoft AppCenter 遙測整合（崩潰回報）

**開源參考**
- GoldenCheetah：C++ 開源，包含 MMP、CP 模型、W' balance 等算法，可作為 WKO5 算法的驗證對照
- python-fitparse / fit_tool：FIT 解析開源工具
- Intervals.icu：雲端平台，有部分公開 API

**TrainingPeaks API 逆向完成（2026-05-15）**

strings + nm 靜態分析 PowerKitOSX.framework 已完整提取 TP sync 機制，無需 mitmproxy：
- OAuth ROPC flow：`grant_type=password`（**不含** client_id），scope = `fitness+baseactivity+users+metrics+software+groundcontrol`（使用 `+` 分隔符）
- Refresh：`grant_type=refresh_token&client_id=WKO5&client_secret=`（empty secret）
- FIT 下載：`fitness/v6/athletes/{id}/workouts/{wid}/filedata/{fileName}` → JSON `{"data": base64(gzip(fit))}` → base64 → zlib inflate → 原始 FIT
- 關鍵發現：`workoutDeviceFileInfos`（非 `files`）；premium/coach 帳號限制
- 實作位置：`backend/sync/tp_client.py`（已完整實作並語法驗證）

**技術待確認**
- Coros API：社群有非官方逆向，需進一步研究

---

*Generated: 2026-05-14*
*Status: DRAFT - needs validation*
*Source Linear Issue: N/A — standalone*
