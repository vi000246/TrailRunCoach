# Personal Training AI — Coros-First, WKO5-Independent

> ⛔ **CANCELED（2026-10-04）**：5 月版的設計已作廢——React SPA 與 5 月的舊程式移除，相關功能之後另做新版。本文件只留作歷史紀錄；現行實作以 `docs/spec/` 為準。

## Problem Statement

作為個人運動員，訓練資料被鎖在 Coros app 的雲端，只能用 Coros 或 TrainingPeaks 的介面查看，無法自行分析或用自然語言問答。
WKO5 桌面版提供深度功率分析，但需要付費訂閱、僅能在桌面開啟，且與 Coros 整合能力有限。
不解決這個問題，訓練洞察就依賴商業軟體，無法客製化、無法自動化、也無法用 AI 對話查詢。

## Evidence

- Coros 無公開 API；第三方同步需透過 TrainingPeaks，但 TP FIT 下載需 premium 帳號
- WKO5 分析功能依賴本機安裝，無法在手機或非本機環境存取
- 社群已逆向 Coros Training Hub API（`xballoy/coros-api`、`cygnusb/coros-mcp` 等多個開源實作）
- Coros 非官方 API 支援 FIT 檔案下載，但文件不公開，可能隨時變更
- **已驗證（2026-05-15）**：帳密登入 + FIT 下載 + 1088 筆活動匯入成功，PMC 圖表正常顯示

## Proposed Solution

直接串接 Coros 非官方 API 下載 .fit 檔案到獨立資料夾（`~/.wko5coach/fits/`），在此專案內實作所有訓練分析算法（MMP、TSS、CTL/ATL/TSB、FTP），並搭配 Web UI 視覺化與 Claude AI 對話介面。
本系統完全獨立，不依賴 WKO5 資料夾或 TrainingPeaks 帳號；使用者仍可獨立使用 WKO5，兩者互不干擾。

## Key Hypothesis

We believe 從 Coros 直接同步 .fit 並在本機計算訓練指標，will 讓個人運動員不依賴任何訂閱服務即可查詢訓練狀況，for 個人使用。
We'll know we're right when 訓練結束後 1 小時內，資料自動出現在系統中，且能透過 Web UI 查看 PMC 圖表並透過 AI 問答得到基於真實功率資料的具體回答。

## What We're NOT Building

- **WKO5 資料夾依賴** — 不讀取 `~/WKO5/`，兩個工具各自獨立
- **TrainingPeaks 帳號依賴** — TP sync 為可選功能，不作為主要資料源
- **TrainingPeaks 替代品** — 純個人工具，不公開
- **完整 WKO5 UI 複製** — 只做 AI 問答 + 核心指標，不做視覺化 dashboard 設計
- **多用戶平台** — 單人使用，無帳號系統
- **Coros 官方支援** — 使用非官方 API，自行承擔可用性風險

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| Coros 同步成功率 | ≥95% 連續 10 次 | 每次訓練後手動觸發並記錄 |
| 同步延遲 | <1 小時（手動觸發立即） | 訓練結束到 DB 可查詢的時間 |
| MMP 曲線誤差 | ±1% vs WKO5 | 用同一份 .fit 對照 WKO5 輸出 |
| AI 問答相關度 | 主觀 ≥4/5 | 個人評分 10 個問題 |
| FTP 估算誤差 | ±5W vs WKO5 | 5 份訓練對照 |

## Open Questions

- [x] ~~Coros API 是否需要裝置 ID 或 token 綁定~~ → 不需要裝置 ID；token 對特定 region server 有效
- [x] ~~`teamcnapi.coros.com` 的穩定性~~ → 台灣帳號實際使用 EU server（`teameuapi.coros.com`），資料 API 用 `teamapi.coros.com`；需動態偵測
- [ ] FIT 下載是否有速率限制（rate limit）？長時間同步後偶見 token invalid
- [ ] iLevels 算法的確切輸入格式（需逆向或對照 GoldenCheetah）
- [ ] TSS 應使用 WKO5 BikeScore 公式，還是標準 Coggan TSS？目前用 (NP/FTP)²×dur/3600×100
- [ ] FTP config 頁面：應允許手動覆蓋 Coros 自動匯入的 FTP 值

---

## Users & Context

**Primary User**
- **Who**: 個人運動員，使用 Coros 手錶記錄訓練，具備技術能力
- **Current behavior**: 用 WKO5 桌面版手動查看，或用 Coros app 看基礎統計
- **Trigger**: 完成訓練後想了解訓練品質，或定期回顧訓練趨勢
- **Success state**: 開啟 web UI 看到 PMC 圖表 + 問 AI「本週訓練量如何」，得到基於真實功率數據的具體回答

**Job to Be Done**
When 完成一次訓練或想規劃下次訓練，I want to 快速理解訓練資料顯示的狀況，so I can 做出有根據的訓練調整決策。

**Non-Users**
不為其他用戶設計，不考慮非技術背景使用者，不做多人協作。

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | FIT 檔案解析（Coros/Garmin）| 所有分析的資料基礎 |
| Must | Coros 非官方 API 同步 | 主要資料來源 |
| Must | Mean Maximal Power (MMP) 曲線計算 | 訓練分析核心指標 |
| Must | PMC 圖表（CTL/ATL/TSB）| 長期訓練狀態視覺化 |
| Should | FTP / LTHR 設定頁面 | 手動覆蓋 Coros 自動匯入值 |
| Should | TSS 精確計算（WKO5 BikeScore or Coggan）| PMC 數值正確性 |
| Should | Claude AI 對話介面 | 訓練問答 |
| Should | iLevels / Training Levels 計算 | 個人化功率區間 |
| Should | Activity Detail 頁面 | 單次訓練圖表（參考 WKO5 Workout View.wko5chart）|
| Should ✅ | Run 跑步訓練負荷圖表（Run PMC、Daily %CTL、Ramp Rate、Intensity Load、Volume Log）| 跑步專項訓練分析（對標 WKO5 Season View）|
| Could | 訓練列表 Dashboard（Web UI）| 覽概訓練趨勢 |
| Could | TrainingPeaks API 整合（可選）| 補充歷史資料 |
| Won't | WKO5 .wko4 格式讀寫 | 兩者獨立，不互動 |
| Won't | 公開 API 或多用戶 | 超出個人工具範疇 |

### MVP Scope（已完成）

1. Coros API 登入 + 活動列表 + FIT 下載到 `~/.wko5coach/fits/`
2. PMC 圖表（CTL/ATL/TSB）顯示在 Web UI
3. FTP/LTHR 從 Coros 登入回應自動匯入

### User Flow

```
Coros 手錶完成訓練 → 上傳 Coros 雲端 → 本系統手動觸發 sync
  → 下載 .fit → ~/.wko5coach/fits/ → 解析 → DB → TSS/MMP 計算
  → Web UI 顯示 PMC → 問 AI「本週怎樣？」→ 基於真實數據的回答
```

---

## Feasibility

**Verdict**: HIGH — Coros 非官方 API 已完整實作並驗證（1088 筆活動匯入成功）；主要風險在 API 穩定性。

---

## Product Milestones

| # | Milestone | User-Visible Value | Status | Depends | SRS | Plan |
|---|-----------|--------------------|--------|---------|-----|------|
| 1 | FIT 解析 + MMP 計算 | 匯入 FIT 並看到 MMP 曲線數值 | complete | - | wko5-milestone1-fit-mmp.srs.md | - |
| 2 | AI 對話介面 | 用中文問「本週訓練強度」獲得數據驅動回答 | pending | 1 | - | - |
| 3 | FTP 估算 + CTL/ATL/TSB | 自動算出訓練區間 + 疲勞狀態 | complete | 1 | - | - |
| 4 | Coros 自動同步 + PMC Web UI | 訓練後手動 sync，FIT 自動下載，PMC 圖表顯示 | **complete** | 1,3 | wko5-coros-sync.spec.md | coros-sync-pmc-mvp.plan.md |
| 4.5 | FTP/LTHR 設定頁面 | 手動設定或覆蓋 FTP、LTHR | **complete** | 4 | wko5-frontend-redesign.srs.md | wko5-mvp-tab-nav-config-season.plan.md |
| 4.6 | Run 跑步訓練負荷圖表 | Season 頁面新增 5 個跑步圖表（Run PMC、Daily %CTL、Ramp Rate、Intensity Load、Volume Log）| **complete** | 4,4.5 | wko5-training-load-charts.srs.md | docs/plans/completed/wko5-training-load-charts.plan.md |
| 4.7 | 資料準確度修正 + 日期範圍 Presets | 圖表資料修正（CTL 種子值、歷史 rTSS 回填）+ DateRangePicker 行事曆 Preset | **complete** | 4.6 | wko5-data-accuracy-date-presets.srs.md | docs/plans/completed/wko5-data-accuracy-date-presets.plan.md |
| 5 | Activity Detail 頁面 | 點開單次活動，看到功率曲線、心率、區間分佈等圖表 | complete | 1,3 | wko5-web-full-clone.srs.md | - |
| 6 | iLevels / Training Levels | 個人化功率區間（對標 WKO5 iLevels） | pending | 3 | - | - |
| 7 | TrainingPeaks 整合（可選） | 從 TP 帳號補充歷史資料 | pending | 1 | - | - |

### Milestone Details

**Milestone 1: FIT 解析 + MMP 計算** ✅
- **User can now**: 匯入一份 FIT 檔並得到 MMP 曲線數值
- **Success signal**: MMP 數值誤差 ±1% vs WKO5
- **Out of scope**: AI 介面、自動同步

**Milestone 2: AI 對話介面**
- **User can now**: 問「本週我跑了幾公里、平均功率多少」並獲得正確答案
- **Success signal**: 連續 10 個問題，8 個以上有意義回答
- **Out of scope**: iLevels、自動同步

**Milestone 3: FTP 估算 + CTL/ATL/TSB** ✅
- **User can now**: 系統從 Coros 登入自動帶入 FTP/LTHR，計算 TSS，顯示 PMC
- **Success signal**: FTP 數值與 Coros app 顯示一致；PMC 圖表有資料

**Milestone 4: Coros 同步 + PMC Web UI** ✅
- **User can now**: 登入 Coros、按 Sync、FIT 自動下載，PMC 圖表即時更新
- **Verified**: 1088 筆活動成功匯入；PMC 有 157 個資料點
- **Out of scope**: iLevels

**Milestone 4.5: FTP/LTHR 設定頁面**
- **User can now**: 在 UI 上手動設定或覆蓋 FTP、LTHR，選擇 TSS 計算公式（Coggan / WKO5 BikeScore / hrTSS）
- **Success signal**: 修改 FTP 後，PMC 圖表自動重新計算
- **Out of scope**: iLevels

**Milestone 5: Activity Detail 頁面**
- **User can now**: 點開任一活動，看到功率時間序列、當次 MMP 曲線、Time in Zones、心率、配速等圖表
- **Source reference**: `~/WKO5/Views/Workout/WKO5 Workout View.wko5chart`（逆向取得圖表定義）
- **Out of scope**: iLevels

**Milestone 6: iLevels / Training Levels**
- **User can now**: 個人化功率區間自動計算，不需手動設定
- **Success signal**: 與 WKO5 iLevels 結果一致（對照同一份資料）

**Milestone 7: TrainingPeaks 整合（可選）**
- **User can now**: 從 TP 帳號一鍵匯入歷史訓練資料（需 premium）
- **Success signal**: 成功匯入 TP 歷史資料

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| MVP 資料來源 | 手動匯入 FIT | 自動同步 | 最快驗證算法正確性 |
| 主要 sync 來源 | Coros 非官方 API | TrainingPeaks ROPC | TP premium 限制；Coros 是原始資料源 |
| .fit 儲存路徑 | `~/.wko5coach/fits/` | `~/WKO5/` | 兩個工具完全獨立，互不干擾 |
| AI 介面 | 對話式（Claude API） | 靜態報告 | 使用者明確偏好 |
| 算法參考來源 | 逆向 PowerKitOSX + GoldenCheetah | 純自研 | GoldenCheetah 有開源實作可交叉驗證 |
| WKO5 依賴 | 完全獨立 | 讀 ~/WKO5/ | 分開操作，各自獨立，不互動 |
| TSS 公式 | (NP/FTP)²×dur/3600×100（Coggan） | WKO5 BikeScore、hrTSS | 暫用標準公式；待 Milestone 4.5 改為可設定 |
| FTP 來源 | Coros 登入回應自動匯入 + 可手動覆蓋 | 純手動 | Coros profile 有 FTP 值，自動帶入減少摩擦 |
| 不公開 | 個人工具 | 開源 | 法律考量（逆向商業軟體） |

---

## Research Summary

**Coros 非官方 API（已驗證，2026-05-15）**

| 面向 | 詳情 |
|------|------|
| 認證 endpoint | `POST https://teameuapi.coros.com/account/login` |
| 認證 payload | `{"account": email, "accountType": 2, "pwd": md5(password)}` |
| 成功判斷 | `result == "0000"`（非 `apiCode`） |
| Token 位置 | `data.accessToken`（非 `result.accessToken`） |
| Token TTL | 約 24 小時（登入回應無 tokenExpiry，固定加 24h） |
| Region 偵測 | 台灣帳號：EU server 登入成功，資料 API 用 US server（`teamapi.coros.com`）；需動態偵測 |
| API header | 所有資料請求需帶 `accessToken` + `yfheader: {"userId": "..."}` |
| 活動列表 | `GET /activity/query?size=20&pageNumber=N&startDay=YYYYMMDD&endDay=YYYYMMDD` |
| 活動列表結構 | `data.dataList`（非 `result.dataList`）；date 欄位為 YYYYMMDD 8 位整數 |
| FIT 下載 | `POST /activity/detail/download?labelId=...&sportType=...&fileType=4` |
| FTP/LTHR | 登入回應 `data.zoneData.ftp`、`data.zoneData.lthr`、`data.weight` |
| 運動類型 | 100=cycling, 102=trail run, 105=hiking, 200=run, 300=swim, 400=triathlon, 402=strength, 9904=custom |

**TrainingPeaks API（已逆向，保留可選）**
- Password grant 不含 `client_id`（僅 refresh token 帶 `client_id=WKO5`）
- FIT 下載需 premium/coach 帳號
- 保留為 Milestone 7 可選功能

**技術基礎現況（2026-05-15）**
- FIT 解析：`fitparse` 已整合，解析 1088+ 筆
- MMP 計算：算法已驗證
- TSS：(NP/FTP)²×dur/3600×100，59 筆有功率資料的活動已計算
- PMC：157 個資料點，CTL/ATL/TSB 正常
- FastAPI + SQLite：已部署於 Docker（localhost:8000）

---

*Generated: 2026-05-15*
*Last updated: 2026-05-15*
*Status: ACTIVE — M4 complete, M4.5 next*
*Source Linear Issue: N/A — standalone*
