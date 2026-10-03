---
linear_issue: null
---
# WKO5 個人越野跑戰情室：圖表驗證、三頁面分流、資料同步與 AI 教練處方

## Problem Statement

使用者是一位以功率/負荷概念（FTP、PMC、TSS）管理多項運動（越野跑、跑步、單車、肌力、桌球）的訓練者，原本依賴 WKO5 桌面版，但 WKO5 卡在資料下載、圖表難懂、且沒有越野跑視角。目前這個自製替代網站雖已有圖表與 AI 教練骨架，卻有四個讓他「不敢信、用不順」的缺口：(a) 圖表公式與**活動篩選**是否與真正的 WKO5 一致無從驗證；(b) 越野跑被當成一般跑步、爬升負荷沒被衡量、也沒有專屬頁面；(c) 資料同步無可靠管道、看不到哪些資料已載入；(d) AI 教練不會給可執行的處方（狀態判讀、zone 幾、間歇怎麼排）。再加上 WKO5 的圖表本身過於艱澀，需要費力解讀。不解決的代價：使用者持續累積的越野跑訓練資料無法被正確、可信、且看得懂地分析，等於沒有可用的訓練決策工具。

## Evidence

- 使用者原話：「不確定圖表公式有沒有用準確」「越野跑跟跑步不太一樣，它有爬升」「我一直無法找到正確下載資料的方式」「AI 教練我還沒有使用過它的功能」「WKO5 的圖表太難懂，希望呈現結果平易近人，不需要吃力閱讀圖表」。
- 程式碼現況（探索確認）：目前所有運動只正規化成 4 類（running/cycling/swimming/walking），**無 trail/road 區分**；無 per-sport 獨立頁面；COROS 同步已實作並驗證 1088+ 筆，但**無同步管理 UI 頁面**；AI 教練 chat 與 context 組裝可運作，但 Trail Analysis / Ask Coach 未完成、無知識庫 RAG。
- 逆向素材確認：WKO5 chart 以 `if(sport="run", ...)` 條件式做運動篩選，bike FTP 與 running FTP（mFTP/rFTP）按 sport 切換——本專案缺此分流即構成可驗證的落差。
- 使用者 筆記已有完整個人化資料：CP 192W、TTE 30min、Trail PI 341、爬升配速 30min/km @ HR160、Palladino 個人化功率區間、PDC 分析、間歇模板——足以支撐越野跑圖表與 AI 教練處方。

## Proposed Solution

在既有後端（COROS 同步、FIT 解析、PMC/MMP、SQLite）之上，做三件事並貫穿一條 UX 原則：
1. **可信**：以 WKO5 逆向素材為對照基準，驗證並修正圖表公式與 sport 篩選邏輯，補上 trail/road 區分。
2. **分流**：拆成三個彼此獨立的頁面——總體（含 WKO5 式多選 checkbox 篩選）、跑步、越野跑（含 GAP、垂直速度、PMC 等專屬指標）。
3. **進得來、看得到**：獨立的資料同步頁面，先嘗試解析逆向出的 WKO5/TrainingPeaks 下載格式，不行則退回已驗證的 COROS 直抓；並顯示哪些資料已載入。
4. **看得懂（橫向原則）**：每個圖表都附白話解讀、狀態號誌（如紅/黃/綠）與一句話重點，讓使用者不需費力判讀數字。
5. **可執行**：AI 教練接上使用者筆記知識，輸出訓練狀態判讀與間歇/zone 處方。

選此路徑而非繼續用 WKO5：WKO5 桌面版的下載與越野跑/易讀性缺口無法靠它自己補；本網站已有最難的基礎建設（同步+解析+計算），缺的是驗證、分流、易讀化與處方。

## Key Hypothesis

We believe 一個經 WKO5 驗證、依運動分流、且把圖表結果白話化並接上個人筆記知識的越野跑戰情室
will 讓使用者敢信任數字、看得懂結果、並得到可直接執行的訓練決策（zone / 間歇 / 調整）
for 這位以功率概念訓練越野跑的使用者本人。
We'll know we're right when 使用者把它當成主要訓練決策工具（取代 WKO5 桌面版），每次訓練後/訓練前都會打開，且 AI 教練的處方他會實際採用。

## What We're NOT Building

- 多使用者 / 帳號登入 / 分享 — 這是個人單機工具，徒增複雜度。
- 寫回 TrainingPeaks / 上傳 / 雙向同步 — 只下載讀取。
- 即時 / 裝置端推播、手錶 live 串流 — 只做訓練後分析。
- Menu Bar 美化 — 已決定走 `/prp-hotfix` 獨立處理，不在本 PRD。

## Success Metrics

| Metric | Target | How Measured |
|--------|--------|--------------|
| 圖表公式/篩選驗證一致性 | 核心圖表（PMC、FTP bike/run、TSS、MMP）100% 對照 WKO5 逆向素材通過 | 逐圖表建驗證對照表，標記 pass/diff/修正 |
| 三頁面活動分流正確 | 三頁面（總體/跑步/越野跑）各自只含對應活動，0 錯置 | 抽樣比對每頁活動清單 vs sport 分類 |
| 圖表可讀性 | 每個圖表都有白話解讀 + 狀態號誌 + 一句話重點 | 圖表清單逐項檢查覆蓋率 |
| 資料同步可用 | 同步頁面能一鍵抓資料並顯示已載入筆數/最新日期 | 實際執行同步並核對載入清單 |
| AI 教練處方可用 | 能依筆記知識輸出「目前狀態 + zone/間歇處方」 | 使用者實測一輪並回饋是否可採用 |

## Open Questions

- [ ] 方案 3（解析逆向 WKO5/TrainingPeaks 下載格式）能否穩定解出 FIT 內容？解不出來的判定標準與退回方案 1 的時機？
- [ ] 越野跑負荷該用哪個為主：NGP/rTSS（TP 自承技術地形低估）還是 hrTSS？是否兩者並陳？
- [ ] AI 教練的知識來源如何接入 筆記（RAG / 預先萃取 / 手動整理成知識檔）？筆記會持續更新，更新如何反映？
- [ ] 越野跑「爬升 TSS / 垂直負荷」是否要自訂公式？是否有 WKO5 逆向素材可對照，或屬於本專案原創指標（無法對 WKO5 驗證）？
- [ ] 圖表白話解讀由規則產生還是由 AI 即時生成？

---

## Users & Context

**Primary User**
- **Who**: 使用者本人——以功率/負荷概念訓練的越野跑者，兼做跑步、單車、肌力、桌球；用 COROS 手錶記錄，資料經 FitnessSyncer → TrainingPeaks → 原本的 WKO5。
- **Current behavior**: 原依賴 WKO5 桌面版做進階分析（30+ 自訂圖表/運算式），但卡在資料下載、圖表難懂、無越野跑視角；目前的自製網站尚未取得他的信任。
- **Trigger**: 剛結束一次訓練/比賽想看負荷與成效；或訓練前想知道今天該練什麼、zone 幾、間歇怎麼排；或週期回顧與賽前調整。
- **Success state**: 三頁面看得懂、數字敢信、同步順、AI 教練給得出可執行處方——完全不需再開 WKO5 桌面版。

**Job to Be Done**
When 我剛訓練完想檢視成效、或訓練前要決定今天怎麼練，
I want to 打開一個依運動分流、數字可信且一眼看懂的分析頁面，並問 AI 教練我現在的狀態與處方，
so I can 不費力地做出正確的訓練決策，不必再依賴難懂又抓不到資料的 WKO5。

**Non-Users**
非本人——不為其他運動員、教練團隊或公開分享設計；不做多帳號。

---

## Solution Detail

### Core Capabilities (MoSCoW)

| Priority | Capability | Rationale |
|----------|------------|-----------|
| Must | 圖表公式 + sport 篩選驗證（對照 WKO5 逆向素材，補 trail/road 區分） | 「敢信數字」是一切前提 |
| Must | 三個獨立頁面：總體（含多選 checkbox 篩選）、跑步、越野跑 | 使用者明確要求、活動需彼此獨立 |
| Must | 越野跑專屬圖表（GAP、垂直速度/VAM、爬升負荷、PMC、equivalent flat 等） | 越野跑有爬升、與平路跑本質不同 |
| Must | 圖表白話化（解讀 + 狀態號誌 + 一句話重點） | 使用者明確要求易讀、不費力 |
| Must | 資料同步頁面（方案 3→退方案 1）+ 已載入資料檢視 | 資料進不來則全盤皆空 |
| Must | AI 教練處方強化（依筆記知識判讀狀態 + zone/間歇處方） | 使用者要可執行建議，非空泛 chat |
| Should | 間歇計算機（時間 × 瓦數，類 WKO5 Interval Calculator） | 對應使用者點名的需求 |
| Should | 總體頁面跨運動負荷彙整（肌力、桌球等也納入） | 使用者要「一張總體圖看全部」 |
| Could | 圖表解讀由 AI 即時生成（vs 規則產生） | 視 Open Question 結果 |
| Won't | 多使用者 / 上傳寫回 TP / 即時推播 / Menu Bar 美化 | 已明確 out of scope |

### MVP Scope

M1 先交付使用者最痛的兩件：**越野跑頁面 + 圖表公式/篩選驗證**。即：完成 sport 分流與 trail/road 區分、建立越野跑專屬頁面與其核心圖表（PMC + 至少 GAP/垂直速度/爬升負荷），且這些圖表都已對照 WKO5 逆向素材驗證、並附白話解讀。資料同步頁面與 AI 教練強化排在後續 milestone（同步先用既有已驗證的 COROS 管道餵資料即可支撐 M1）。

### User Flow

訓練後：開啟網站 → 進越野跑頁面 → 看 PMC/爬升負荷/單次成效（每圖附白話解讀與號誌）→（資料未進來時）到同步頁面一鍵同步並確認已載入 → 訓練前：問 AI 教練「我現在狀態如何、今天該 zone 幾、間歇怎麼排」→ 取得處方。

---

## Feasibility

**Verdict**: HIGH — 後端已有可用的 COROS 同步（1088+ 筆）、FIT 解析、PMC/MMP 計算、SQLite schema（含 elevation_gain_m）與 AI 教練 chat 骨架；三頁面/篩選是在既有圖表加 sport 維度，越野跑指標所需欄位 FIT 已保留。唯一中度風險為方案 3 解析 WKO5/TP 下載格式的穩定性，已預先定好退回方案 1。

> Architecture, data model, API contracts, technology choices, and detailed technical risks belong in the SRS. Run `/prp-srs docs/prd/wko5-trail-multipage-sync-coach.prd.md` to produce that next.

---

## Product Milestones

| # | Milestone | User-Visible Value | Status | Depends | SRS | Plan |
|---|-----------|--------------------|--------|---------|-----|------|
| 1 | 圖表驗證 + 越野跑頁面 | 使用者有一個數字可信、看得懂、含爬升視角的越野跑分析頁面 | complete | - | `docs/srs/completed/sport-pages-multi-sport-views-trail-analytics.srs.md` | `docs/plans/completed/sport-pages-*.plan.md` |
| 2 | 三頁面分流 + 總體篩選 | 使用者能在總體/跑步/越野跑三個獨立頁面間切換，總體頁可用 checkbox 多選運動 | complete | 1 | 同上 | 同上 |
| 3 | 資料同步頁面 | 使用者能一鍵同步並看到哪些資料已載入網站圖表 | complete | - | `docs/srs/completed/coros-sync-unified-sync-page-data-inventory.srs.md` | `docs/plans/completed/coros-sync-unified-sync-page-data-inventory.plan.md` |
| 4 | AI 教練處方強化 | 使用者能得到依其筆記知識判讀的訓練狀態與 zone/間歇處方 | complete | 1 | `docs/srs/completed/ai-coach-knowledge-driven-prescription.srs.md` | `docs/plans/completed/ai-coach-knowledge-driven-prescription.plan.md` |

### Milestone Details

**Milestone 1: 圖表驗證 + 越野跑頁面**
- **User can now**: 打開一個越野跑專屬頁面，看到 PMC 與爬升相關圖表，每個圖表都附白話解讀與狀態號誌，且公式已對照 WKO5 逆向素材驗證為一致。
- **Success signal**: 核心圖表驗證對照表全數 pass；使用者表示看得懂、敢信。
- **Out of scope for this milestone**: 跑步/總體頁面、同步 UI、AI 處方（同步先用既有 COROS 管道餵資料）。

**Milestone 2: 三頁面分流 + 總體篩選**
- **User can now**: 在總體（含多選 checkbox 活動篩選）、跑步、越野跑三個獨立頁面間切換，各頁圖表只顯示對應活動。
- **Success signal**: 三頁活動分流抽樣 0 錯置；總體頁篩選即時生效。
- **Out of scope for this milestone**: 同步 UI、AI 處方。

**Milestone 3: 資料同步頁面**
- **User can now**: 在獨立同步頁面一鍵抓資料（方案 3 解析→退方案 1 COROS），並看到已載入筆數、最新日期與資料來源。
- **Success signal**: 同步成功並正確顯示載入清單；方案 3 可行性有明確結論。
- **Out of scope for this milestone**: 上傳/寫回 TP。

**Milestone 4: AI 教練處方強化**
- **User can now**: 問 AI 教練即得到「目前訓練狀態判讀 + 該練 zone 幾 + 間歇時間/瓦數處方」，依據其 筆記知識。
- **Success signal**: 使用者實測一輪並認為處方可採用。
- **Out of scope for this milestone**: 自動排課、行事曆整合。

> Note: Technical phases and parallelism live in the **SRS**. Implementation tasks live in the **Plan**. This PRD only captures product value sequencing.

---

## Decisions Log

| Decision | Choice | Alternatives | Rationale |
|----------|--------|--------------|-----------|
| PRD 切分 | 一份總 PRD（三大功能）+ Menu Bar 走 hotfix | 全放一份 / 拆多份 | 三大功能相關且共用基礎；Menu Bar 是小改版 |
| 資料同步策略 | 先試方案 3（解析逆向 WKO5/TP 下載），不行退方案 1（COROS 直抓） | intervals.icu 中介 / 純 COROS | 使用者想先看能否解析 WKO5 格式；COROS 已驗證可用作後盾 |
| MVP 順序 | M1 = 越野跑頁面 + 圖表驗證 | 先同步頁 / 三項並行 | 這是使用者最痛、也最能建立信任的部分 |
| 圖表呈現 | 每圖附白話解讀 + 狀態號誌 + 一句話重點 | 維持 WKO5 原始艱澀風格 | 使用者明確要求不需費力閱讀 |
| v1 範圍 | 四項全進 v1（依 milestone 分期交付） | 砍到只剩越野跑 | 使用者選四項皆 must，但以 milestone 控制節奏 |

---

## Research Summary

**Market Context**
- TrainingPeaks 官方 API 為合作夥伴審核制、不開放個人；WKO5↔TP 同步協定私有未公開、WKO5 athlete DB 無公開 parser。一次性歷史回補可用 TP 網頁「Export Data」ZIP（原始 FIT）。
- COROS：官方 API 同為審核制；官方第三方自動同步含 Strava/TP/intervals.icu；社群工具（corosexport、xballoy/coros-api）以 email+password 驅動非公開 web API，可批次下載 FIT 但易因改版失效。
- 最穩定的替代路線為 COROS→intervals.icu（官方同步）→intervals.icu 免費文件化 API 取原始 FIT；列為方案 1 失效時的後備。
- 越野跑指標：GAP（Minetti/Strava）、VAM/垂直速度、equivalent flat distance、running power、NGP/rTSS vs hrTSS（技術地形 TP 自承 rTSS 低估、建議改 hrTSS）。

**Technical Context**
- 後端已實作並驗證：COROS 同步（1088+ 筆）、FIT 雙 parser、PMC/MMP cache、SQLite（normalized activity，含新欄位 elevation_gain_m、各 FTP 變體）。
- 落差：sport 僅 4 類無 trail/road 區分；無 per-sport 頁面；無同步 UI；AI 教練 Trail Analysis / Ask Coach 未完成、無知識庫 RAG。
- 逆向素材：WKO5 chart 以 `if(sport="run", ...)` 篩選，bike/run FTP 切換；PMC/ACWR/time-in-zone chart 定義、~100 個演算法函式、TrainingPeaks OAuth2+base64+gzip 下載機制（PowerKitOSX.framework Build 590）已記錄於 docs/spec 與 SRS 素材，可作驗證與方案 3 的依據。

---

*Generated: 2026-06-13*
*Status: DRAFT - needs validation*
*Source Linear Issue: N/A — standalone*
