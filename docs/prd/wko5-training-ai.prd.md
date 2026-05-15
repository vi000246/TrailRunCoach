# Personal Training AI — Coros-First, WKO5-Independent

## Problem Statement

作為個人運動員，訓練資料被鎖在 Coros app 的雲端，只能用 Coros 或 TrainingPeaks 的介面查看，無法自行分析或用自然語言問答。
WKO5 桌面版提供深度功率分析，但需要付費訂閱、僅能在桌面開啟，且與 Coros 整合能力有限。
不解決這個問題，訓練洞察就依賴商業軟體，無法客製化、無法自動化、也無法用 AI 對話查詢。

## Evidence

- Coros 無公開 API；第三方同步需透過 TrainingPeaks，但 TP FIT 下載需 premium 帳號
- WKO5 分析功能依賴本機安裝，無法在手機或非本機環境存取
- 社群已逆向 Coros Training Hub API（`xballoy/coros-api`、`cygnusb/coros-mcp` 等多個開源實作，截至 2026-02）
- Coros 非官方 API 支援 FIT 檔案下載，但文件不公開，可能隨時變更

## Proposed Solution

直接串接 Coros 非官方 API 下載 .fit 檔案到獨立資料夾（`~/.wko5coach/fits/`），在此專案內實作所有訓練分析算法（MMP、TSS、CTL/ATL/TSB、FTP），並搭配 Claude AI 對話介面。
本系統完全獨立，不依賴 WKO5 資料夾或 TrainingPeaks 帳號；使用者仍可獨立使用 WKO5，兩者互不干擾。

## Key Hypothesis

We believe 從 Coros 直接同步 .fit 並在本機計算訓練指標，will 讓個人運動員不依賴任何訂閱服務即可查詢訓練狀況，for 個人使用。
We'll know we're right when 訓練結束後 1 小時內，資料自動出現在系統中，且能透過 AI 問答得到基於真實功率資料的具體回答。

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
| Coros 同步成功率 | ≥95% 連續 10 次 | 每次訓練後自動觸發並記錄 |
| 同步延遲 | <1 小時（手動觸發立即） | 訓練結束到 DB 可查詢的時間 |
| MMP 曲線誤差 | ±1% vs WKO5 | 用同一份 .fit 對照 WKO5 輸出 |
| AI 問答相關度 | 主觀 ≥4/5 | 個人評分 10 個問題 |
| FTP 估算誤差 | ±5W vs WKO5 | 5 份訓練對照 |

## Open Questions

- [ ] Coros API 是否需要裝置 ID 或 token 綁定，導致多設備登入受限？
- [ ] 非官方 API `teamcnapi.coros.com` 的穩定性如何，是否有備用 endpoint？
- [ ] FIT 下載是否有速率限制（rate limit）？
- [ ] iLevels 算法的確切輸入格式（需逆向或對照 GoldenCheetah）

---

## Users & Context

**Primary User**
- **Who**: 個人運動員（使用者本人），使用 Coros 手錶記錄訓練，具備技術能力
- **Current behavior**: 用 WKO5 桌面版手動查看，或用 Coros app 看基礎統計
- **Trigger**: 完成訓練後想了解訓練品質，或定期回顧訓練趨勢
- **Success state**: 開啟 web UI 或問 AI「本週訓練量如何」，得到基於真實功率數據的具體回答

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
| Must | Coros 非官方 API 同步 | 主要資料來源，取代 TP |
| Must | Mean Maximal Power (MMP) 曲線計算 | 訓練分析核心指標 |
| Must | Claude AI 對話介面（問訓練狀況）| 主要使用情境 |
| Should | FTP 自動估算 | 個人化訓練區間依賴此值 |
| Should | CTL / ATL / TSB 追蹤（PMC） | 長期訓練狀態 |
| Should | iLevels / Training Levels 計算 | 個人化功率區間 |
| Should | Activity Detail 頁面 | 單次訓練的圖表分析（參考 WKO5 Workout View.wko5chart）|
| Could | 訓練列表 Dashboard（Web UI） | 輔助 AI 問答，覽概訓練趨勢 |
| Could | TrainingPeaks API 整合（可選） | 補充歷史資料 |
| Won't | WKO5 .wko4 格式讀寫 | 兩者獨立，不互動 |
| Won't | 公開 API 或多用戶 | 超出個人工具範疇 |

### MVP Scope

1. Coros API 登入 + 活動列表 + FIT 下載到 `~/.wko5coach/fits/`
2. 計算 MMP 曲線（1s–60min）
3. Claude AI 對話介面：能回答「本週訓練量」、「最大功率」等基礎問題

### User Flow

```
Coros 手錶完成訓練 → 上傳 Coros 雲端 → 本系統 API sync 觸發
  → 下載 .fit → ~/.wko5coach/fits/ → 解析 → DB → MMP 計算
  → 問 AI「本週怎樣？」→ 基於真實數據的回答
```

---

## Feasibility

**Verdict**: MEDIUM — Coros 非官方 API 有多個社群實作可參考（`xballoy/coros-api`、`cygnusb/coros-mcp`），FIT 解析和算法已有現成基礎；主要風險在 API 穩定性（unofficial）。

> 架構、資料模型、API 合約、技術選型及詳細技術風險屬於 SRS。執行 `/prp-srs docs/prd/wko5-training-ai.prd.md` 產出下一步。

---

## Product Milestones

| # | Milestone | User-Visible Value | Status | Depends | SRS | Plan |
|---|-----------|--------------------|--------|---------|-----|------|
| 1 | FIT 解析 + MMP 計算 | 匯入 FIT 並看到 MMP 曲線數值 | complete | - | wko5-milestone1-fit-mmp.spec.md | - |
| 2 | AI 對話介面 | 用中文問「本週訓練強度」獲得數據驅動回答 | pending | 1 | - | - |
| 3 | FTP 估算 + CTL/ATL/TSB | 自動算出訓練區間 + 疲勞狀態 | pending | 1 | - | - |
| 4 | Coros 自動同步 | 訓練後資料自動進系統，無需手動匯入 | **in progress** | 1 | wko5-coros-sync.spec.md | - |
| 5 | Activity Detail 頁面 | 點開單次活動，看到功率曲線、心率、區間分佈等圖表 | pending | 1,3 | wko5-web-full-clone.spec.md | - |
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

**Milestone 3: FTP 估算 + CTL/ATL/TSB**
- **User can now**: 系統自動計算個人化訓練區間與疲勞狀態（PMC 圖）
- **Success signal**: FTP 估算值與 WKO5 誤差 ±5W 以內
- **Out of scope**: Coros 自動同步

**Milestone 4: Coros 自動同步**
- **User can now**: 訓練結束後按一下 sync，FIT 自動下載到 `~/.wko5coach/fits/`
- **Success signal**: 連續 5 次訓練均自動同步成功
- **Out of scope**: iLevels

**Milestone 5: Activity Detail 頁面**
- **User can now**: 點開任一活動，看到功率時間序列、當次 MMP 曲線、Time in Zones、心率、配速、訓練影響分數等圖表
- **Success signal**: 所有核心圖表與 WKO5「WKO5 Workout View」view 對應圖表數值一致
- **Source reference**: `~/WKO5/Views/Workout/WKO5 Workout View.wko5chart`（逆向取得圖表定義）
- **Out of scope**: iLevels（Milestone 6 才計算）；跑步動態（Running Dynamics）列為 Could

**Milestone 6: iLevels / Training Levels**
- **User can now**: 個人化功率區間自動計算，不需手動設定
- **Success signal**: 與 WKO5 iLevels 結果一致（對照同一份資料）
- **Out of scope**: N/A

**Milestone 7: TrainingPeaks 整合（可選）**
- **User can now**: 從 TP 帳號一鍵匯入歷史訓練資料（需 premium）
- **Success signal**: 成功匯入 TP 歷史資料
- **Out of scope**: N/A

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
| 不公開 | 個人工具 | 開源 | 法律考量（逆向商業軟體） |

---

## Research Summary

**Coros 非官方 API 現況（2026-05-15）**
- API base: `https://teamcnapi.coros.com`（Training Hub），`https://apieu.coros.com`（mobile）
- 認證：email + MD5(password)
- 活動列表：支援分頁、日期篩選
- FIT 下載：有社群確認，`xballoy/coros-api` 已實作 bulk export
- 開源參考：`xballoy/coros-api`（TypeScript/NestJS）、`cygnusb/coros-mcp`（Python）、`CuberL/coros-mcp`（Python）
- 風險：非官方 API，可能隨時變更；建議追蹤社群更新

**TrainingPeaks API（已逆向，2026-05-15）**
- Password grant 不含 `client_id`（僅 refresh token 帶 `client_id=WKO5`）
- FIT 下載需 premium/coach 帳號（目前帳號不符）
- 保留為 Milestone 6 可選功能

**技術基礎現況**
- FIT 解析：`fit_tool` 已整合，掃描 1011+ 筆可用
- MMP 計算：算法已驗證
- FastAPI + SQLite：已部署於 Docker（localhost:8000）

---

*Generated: 2026-05-15*
*Status: ACTIVE*
*Source Linear Issue: N/A — standalone*
