# TSS、IF、NP 等 TrainingPeaks 商標用語的盤點與替換建議（SP-391）

> 調查日期：2026-10-12。只做調查，沒有改程式、沒有改 i18n。
> 背景：SP-343 第 8 題（`sync-platforms.md` §3.3、§6）。商業版開放註冊（SP-390）前，畫面、匯出、手錶課表、說明文字裡用到別人註冊商標的地方要先整理。
> **標記**：**已驗證**＝今天讀了一手頁面或程式碼，引句逐字吻合；**摘要**＝搜尋引擎摘要、第三方鏡像站（Justia、uspto.report、Trademarkia）或 repo 內其他調查文件的轉述；**推估**＝我的推論；**未找到來源**＝查過，寫出試過什麼。
> **怎麼查的**：
> - 盤點：寫腳本掃 `backend/static/*.html|*.js`（去掉註解，只看文字節點、`title／placeholder／aria-label` 等屬性、`<script>` 裡的字串）、`backend/static/i18n/{zh-TW,en}/*.json`、`backend/i18n/locales/en.json`（msgid＝中文原文、值＝英文）、`views/*.json` 的 `name／title／description／label`、`views/i18n/en.json`、後端 `.py` 的字串（用 `ast`，去掉 docstring、log 和 SQL）、`README.md`。再人工看過每一句，排除 id、URL、內部 key。行號以今天的工作目錄為準。
> - 商標：**今天這個環境連不上 trainingpeaks.com、USPTO（tsdr／tmsearch）、TIPO、intervals.icu、Strava、Stryd、COROS、Garmin**（代理以組織政策拒絕連線；WebFetch 也全部 DNS 失敗）。所以外部資料只能靠搜尋引擎的摘要，**今天查到的外部事實一律標「摘要」**。`sync-platforms.md`（2026-10-07）和 `competitor-charts.md`（2026-09-30）當時有讀一手頁面的，照原標記引用並註明日期。
> - **動工或寫進條款前，§2 的註冊號要有人用瀏覽器在 USPTO TSDR 和 TIPO 商標檢索系統再看一次**（見 §6 問題 11）。

## 摘要

1. **TrainingPeaks 自己列為註冊商標的是：TrainingPeaks、TSS、Training Stress Score、IF、Intensity Factor、NP、Normalized Power、VirtualCoach。** 它的「關於我們」頁："Our registered trademarks include TrainingPeaks®, TSS®, Training Stress Score®, IF®, Intensity Factor®, NP®, Normalized Power® and VirtualCoach®."（摘要）。USPTO 鏡像站查得到 TSS、TRAINING STRESS SCORE、NORMALIZED POWER、INTENSITY FACTOR、WKO+、TRAININGPEAKS 的註冊（摘要，§2）。
2. **CTL、ATL、TSB、PMC（Performance Management Chart）、hrTSS、rTSS、iLevels、mFTP 都沒找到註冊紀錄**，也不在 TrainingPeaks 自己的清單上（未找到來源）。但它們是 TrainingPeaks／WKO 推廣的名稱；intervals.icu、Golden Cheetah、Strava 都改用自己的字（§3）。
3. **2026-07-22 Garmin 買下了 TrainingPeaks**（摘要，Garmin 新聞稿）。這些商標現在在 Garmin 旗下。Golden Cheetah 有前例：TrainingPeaks 要求它拿掉商標，3.5 版把 TSS／IF／NP 改名為 BikeStress／BikeIntensity／IsoPower（摘要，GC 官方 FAQ）。
4. **我們用得很多。** 使用者看得到的地方（不含程式內部）：
   - TSS 家族約 330 處（不重複的中文句子約 150 句、英文 83 句）。
   - CTL／ATL／TSB／PMC 約 390 處（中文約 116 句、英文 45 句）。
   - IF 46 處、NP 14 處；WKO5 產品名約 210 處（大多在自架才有的 WKO5 對照功能）；mFTP、TTE、FRC 這些 WKO 指標名約 70 處。

   最顯眼的有：總覽的「體能 CTL／疲勞 ATL／狀況 TSB」、課表與完成度頁的「TSS」欄、行事曆訂閱（ICS）的「TSS 120」、推到 COROS 手錶的課表步驟「負荷 80 TSS（約 …TL）」，以及公開示範站。細節見 §1。
5. **建議：畫面、匯出、手錶上一律改用我們自己的字**，對照只留在少數說明裡：
   - TSS → **負荷／Load**（app 的課表編輯器已經用「負荷」）；
   - IF → **強度比／Intensity**；NP → **加權功率／Weighted power**；
   - CTL／ATL／TSB → **體能／疲勞／狀況（Fitness／Fatigue／Form）**，拿掉縮寫；PMC → **體能圖／Fitness chart**；
   - hrTSS／rTSS／功率 TSS → **心率負荷／配速負荷／功率負荷**。

   完整對照表在 §4。
6. **要保留對照時**（例如「和 TrainingPeaks 同一個量尺」），寫成指示性用法：用對方的全名、第一次出現加 ®，再放一段聲明。聲明放在新的「關於」頁、服務條款（SP-60）和 README（§5）。
7. **工作量**：大約 65 個檔案、350 處字串，外加 i18n 目錄重新對 key、約 60 行測試斷言、2 處程式拿顯示字串做判斷。估 M（2–3 天），可以分三輪（§7）。有 11 個要你決定的問題（§6），最要緊的是 1、2、4、5。

---

## 1. 盤點（使用者看得到的地方）

### 1.1 計數口徑

- **算**：畫面文字、`title`／`data-tip` 說明、圖表名稱與軸、i18n 目錄、後端回給畫面的句子（狀況卡、建議、判讀、錯誤訊息）、ICS 行事曆、推到 COROS 的課表文字、views 的圖表標題與說明、README。
- **不算**：變數名、Python 識別字、API JSON 的 key（`tss`、`ctl` 等，畫面不直接顯示）、註解、docstring、測試、`backend/scripts/`、`data_registry.py` 的開發者說明、`wko5expr/config.py` 的欄位說明。
- **重複**：HTML 裡有一份中文備援文字（`data-i18n` 的預設內容），和 `zh-TW` 目錄是同一句，所以「畫面 HTML」和「前端目錄 zh-TW」會重複計算同一句話。後端句子和 `en.json` 的 msgid 也一樣。改的時候這些是**同一個字串的兩份**，要一起改。
- 「次數」是出現次數；同一句裡有兩個 TSS 算 2。

### 1.2 總表：哪個詞出現在哪裡（出現次數）

| 詞 | 畫面 HTML | 畫面 JS | 前端目錄 zh-TW | 前端目錄 en | 後端句子 | 後端目錄 en | views 中文 | views en | README | 合計 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| TSS（單獨） | 67 | 9 | 64 | 63 | 46 | 32 | 11 | 6 | 0 | 298 |
| hrTSS | 10 | 0 | 3 | 3 | 6 | 6 | 0 | 0 | 0 | 28 |
| rTSS | 1 | 0 | 2 | 2 | 1 | 1 | 0 | 0 | 0 | 7 |
| IF | 3 | 2 | 9 | 9 | 11 | 6 | 0 | 0 | 0 | 40 |
| NP | 5 | 0 | 1 | 1 | 1 | 1 | 2 | 2 | 0 | 13 |
| Normalized Power／Intensity Factor／Training Stress Score（全名） | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| CTL | 19 | 0 | 17 | 17 | 28 | 8 | 51 | 33 | 0 | 173 |
| ATL | 10 | 0 | 14 | 14 | 2 | 0 | 18 | 14 | 0 | 72 |
| TSB | 8 | 0 | 7 | 7 | 31 | 2 | 29 | 19 | 0 | 103 |
| PMC | 2 | 0 | 4 | 5 | 1 | 0 | 8 | 8 | 2 | 30 |
| Performance Management Chart／Performance Manager | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| Fitness／Fatigue／Form（英文） | — | — | — | 16 | — | 19 | — | 33 | 1 | 69 |
| WKO／WKO5（產品名，含網址與資料夾名） | 53 | 8 | 20 | 20 | 52 | 8 | 16 | 15 | 9 | 201 |
| TrainingPeaks | 18 | 1 | 12 | 12 | 18 | 8 | 9 | 9 | 4 | 91 |
| mFTP | 8 | 0 | 3 | 3 | 14 | 3 | 1 | 1 | 0 | 33 |
| TTE | 8 | 0 | 0 | 0 | 12 | 1 | 0 | 0 | 0 | 21 |
| FRC（含 dFRC） | 1 | 0 | 0 | 0 | 8 | 2 | 1 | 1 | 0 | 13 |
| Pmax | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 1 |
| TIS（Training Impact Score，WKO5 指標；粗估，grep 未逐句排除內部字串） | 3 | — | — | — | 約 36 | 有 | 14 | 有 | 0 | 約 55 |
| iLevels、Stamina、Power Duration（WKO 名稱） | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **0** |
| kJ | 9 | 0 | 1 | 1 | 19 | 0 | 1 | 1 | 0 | 32 |

（已驗證，讀程式碼並用腳本計數）

補充：
- **後端目錄 msgid(zh)** 的數字和「後端目錄 en」相同，所以表裡省略。後端句子比 msgid 多，是因為還有句子沒進翻譯（i18n P3 之後的頁面）。
- **iLevels** 只剩程式內部：`activity_charts.py:26` 註解寫 "WKO5 iLevels were dropped (owner 2026-10-02)"。
- **Stamina**、**FRC 的 iLevel 名稱**（例如 `evaluator.py:2999` 的 "Extensive Anaerobic (FRC)"）只會在**匯入使用者自己的 WKO5 view** 時出現。
- **kJ** 是 SI 單位，不是商標，列出來只是因為任務要求查；不用改。
- **全名一次都沒出現**：Normalized Power、Intensity Factor、Training Stress Score、Performance Management Chart。只有 `wko5expr/config.py` 的開發者說明寫過 "power-duration"。

### 1.3 各介面：使用者實際看到的字

**A. 標籤、欄名、圖例（最顯眼，優先改）**

- 總覽 `overview.html`：
  - 指標卡「體能 CTL」（`:414`）；
  - PMC 圖例「CTL／ATL／TSB」（`:956–962`）；
  - 本週列「CTL **54** → 週日 **56**」（`:711`）、「下週一 TSB」（`:705`）；
  - 「TSS 計畫／完成」（`:649–721`）。
- 總覽的後端句子 `engine/status.py`：
  - 「體能 CTL」（`:365`）、「狀況 TSB」（`:405`）；
  - 「TSB < −30，過度訓練風險」（`:396`）、「TSB > +25，體能在流失」（`:404`）；
  - 「減量期 CTL 還在上升，代表量沒有真的減」（`:349`）。
- 課表 `schedule.html`：
  - 欄名「TSS」（`:703`、`:2019`）、「鎖 TSS」（`:706`）；
  - 「計畫 TSS」「預估 TSS ≈ 64 TSS/小時」（`:1539–1543`）；
  - 週摘要「TSS 完成 x / 計畫 y」（`:1162`）。
- 課表編輯器 `workout_editor.js`：結束條件「負荷 TSS」（`:522`）、「TSS 估」（`:582`）、下拉選單「TSS／用感覺填」（`:514`）。
- 完成度頁 `compliance.json`：「TSS 完成度」（`kpi.tss`）、「負荷比 ATL ÷ CTL」（`ratio.title`）、「TSS {pct}%」。
- 活動列表 `activity.json`：欄名 `col.tss`「TSS」。
- 比對頁 `compare.html:61`：欄名「NP W」「TSS」。來源標籤 `compare.json`：「功率 TSS」「配速 rTSS」「心率 hrTSS」「TrainingPeaks 的 TSS」。
- 圖表頁：
  - `wko5_viewer.html`：「TSS 來源」（`:912`）、「EF NP÷心率」（`:2925`）、「mFTP 線」（`:1509`）；
  - 軸單位「TSS/天」「TSS/週」「TSS/天/週」（`engine/wko5expr/units.py:103–137`）。
- 設定 `settings.html`：
  - 「起始 CTL／ATL」（`:277`）、欄名「CTL」「ATL」（`:282–283`）；
  - 「hrTSS 只算實際移動的時間」（`:188`）、「爬升加成 TSS」（`:189`）。
- views（圖表分析，示範站也看得到）`views/training.json`：
  - dashboard「負荷 PMC」；
  - 圖名「PMC：體能 CTL・疲勞 ATL・狀況 TSB（全部運動）」；
  - 系列名「CTL 體能」「ATL 疲勞」「TSB 過度疲勞／有效訓練／持平／比賽狀態／休息過久」；
  - 「狀況 Form%（TSB ÷ CTL）」，另一處寫成「新鮮度 Form%」（**兩種中文並存**）；
  - 「每日 TSS（跟體能比）」「Ramp rate（CTL 每週變化）」。
- 賽事計算機：
  - `racepower.json`：「預估 TSS {tss}（推估）」；
  - `racepower.html`：「TTE 秒」（`:1031`）、「進階設定（W′、TTE、Riegel k…）」（`:1028`）。

**B. 說明文字（`?` 提示、圖表「怎麼看／方法」）**

都是長句，主要的有：
- `overview.json` 的 `pmc.tip`；
- `settings.json` 的 `pmc.help`、`rpe_load.help`、`tl.help`、`thr.help`；
- `common.json` 的 `workout.tip.tss`，例如「TSS 估＝Σ 秒 × IF² × 100 ÷ 3600，IF＝目標中點 ÷ CP」；
- `schedule.json` 的 `vs.levels_tip`、`pf.adapt_note_tip`；
- `compliance.json` 的 `kpi.ok_tip`、`kinds.tip`；
- views 的 23 段 `description`。

這些句子常同時寫「TSS」和「TrainingPeaks 的做法」，等於把對方的商標當成我們功能的名稱，又引用對方當出處。

**C. 匯出與手錶（離開 app 之後還帶著這個字）**

- **ICS 行事曆訂閱**：`engine/calendar_feed.py:152`、`:161` 寫 `f"TSS {round(...)}"`，事件內容變成「計畫：60 分 · TSS 64」。這是寫死的字面字串，不經 i18n。（已驗證）
- **推到 COROS 手錶的課表**：`sync/coros_workouts.py:526`「負荷 {…} TSS（約 {…} TL）」；`sync/workout_targets/coros.py:24`「負荷：app 填 TSS，送 COROS 的 TL（推估換算）」。（已驗證）
- **CSV 匯出**：沒有找到 CSV 匯出（已驗證：`grep csv|Content-Disposition` 只找到 GPX 路線與賽道下載）。GPX 下載不含這些詞。
- **分享頁** `share.html`：沒有命中。

**D. AI**

- app 目前**沒有內建的 AI 回覆或提示詞**：沒有 LLM 呼叫，`grep anthropic|openai|prompt` 沒有命中（已驗證）。
- 給 AI agent 用的 Debug API（SP-371）回傳 JSON，key 是 `tss`、`ctl` 等，不算畫面。
- 但 `workout_review.py:4176` 的註解寫明 `series` 會把完整判讀句子留給 AI／API 用，所以 B 類句子改了，AI 讀到的也會跟著改（推估）。
- 之後做 AI 教練時，提示詞要照 §4 的用語表寫，並把它加進 `backend/i18n/glossary.json`（推估）。

**E. 示範站與 README**

- **公開示範站**（GitHub Pages）是 `backend/demo/export_static` 把同一批頁面匯出成靜態網站，所以 A、B 類的字全部都在。這是目前**最公開**的地方。
- `shell.js:314–336` 在示範模式會**刪掉「WKO5」字樣**（擁有者決定），但 TSS、CTL、TrainingPeaks 沒刪（已驗證）。
- README：
  - `:21`「體能／疲勞／狀態（PMC）」（又一個中文版本「狀態」）；
  - `:122` "an overview dashboard (PMC, …)"；
  - `:133` "> Not affiliated with COROS, TrainingPeaks or Stryd."；
  - `:45`、`:64–101` 的 `~/.wko5coach`、`WKO5COACH_*` 環境變數。

**F. WKO5 產品名與內部名稱「wko5coach」**

- 自架才有的功能：
  - 「WKO5 對照模式」（`settings.html:195`、`wko5_viewer.html:588`、`:662`）；
  - 「WKO5 資料夾（比對用）」（`settings.html:329`、`sourcechip.js:26`）；
  - 「WKO5 設定」「LTHR 還是 WKO5 預設值」（`status.py:1044`、`:1050`）；
  - 「OAuth（WKO5 client）」「沿用 WKO5 的 client 憑證登入（有違反服務條款的風險…）」（`settings.html:382–383`）。
- 說明裡的出處：「dFRC 是 WKO5 的模型」（`workout_review.py:4366`、`:4400`）、「WKO5 的 Training Impact Score」（`:2992`）、「WKO5 的 5 Min PDC 參考」（`:4369`）、「類似 WKO5 的 Daily % of CTL」（views）。
- **每個使用者都看得到的**：
  - 網址路徑 `/api/v1/wko5/viewer`、`/api/v1/wko5/settings`（`shell.js:21–29`，網址列看得到）；
  - 圖表頁的分頁標題 `<title>WKO5 View Check</title>`（`wko5_viewer.html:6`；示範模式會被 `shell.js` 刪成「View Check」）；
  - localStorage key `wko5viewer.*`（使用者看不到，不算）。

**G. TrainingPeaks 名稱**

- 當**資料來源名稱**：設定的同步分頁、來源標籤（`activity.html:532`、`sourcechip.js:31`）。
- 當**出處**：「TrainingPeaks 的完成度顏色門檻」「Friel／TrainingPeaks」「TrainingPeaks TSS 公式」（`overview.py:749`）、「TrainingPeaks 閾值通知」（`threshold_confidence.py:128`）。
- 這兩種都是指示性用法（指出對方或對方的文章），本身不是問題（推估，§2.3）。要處理的是 **「TrainingPeaks TSS 公式」這種把對方商標當成我們算法名稱的句子**。

### 1.4 i18n 目錄重點

- **前端 `en`**：
  - 標籤是 "CTL fitness"、"ATL fatigue"、"TSB form"（`overview.json` `pmc.ctl/atl/tsb`）；
  - 標題是 "Fitness · fatigue · form"（`pmc.title`），首頁用途是 "Form · to-dos · this week"（`shell.json`）；
  - 課表的結束條件**已經叫 "Load"**（`common.json` `workout.end.load`）。
- **前端 `zh-TW`**：標籤「CTL 體能／ATL 疲勞／TSB 狀況」，標題「體能・疲勞・狀況」，結束條件「負荷」。
- **`views/i18n/en.json`**：用 "Fitness (CTL)"、"Fatigue (ATL)"、"Form (TSB)"、"Load (PMC)"、"the daily TSS"；系列名的翻譯以**中文原名當 key**（例如 `"CTL 體能": "CTL Fitness"`，`wko5expr/viewi18n.py:11`）。
- **`backend/i18n/glossary.json`**：已經有「體能 CTL → Fitness (CTL)」「疲勞 ATL → Fatigue (ATL)」「狀況 TSB → Form (TSB)」，note 寫 "TrainingPeaks wording"。還有五個 Form 區間英文名 High Risk／Optimal／Grey Zone／Fresh／Transition，note 寫 "TrainingPeaks' own name; owner decision 2026-10-02"（`i18n.plan.md` §0）。
- **中文同一個概念有多個說法**：狀況／狀態／新鮮度（TSB、Form%），體能（CTL）。改名時順便統一。

---

## 2. 哪些是註冊商標

### 2.1 TrainingPeaks（Peaksware／Garmin）自己的說法

- 指標詞彙頁："Any metrics with a trademark symbol Ⓡ were developed and are owned by TrainingPeaks."
  - https://www.trainingpeaks.com/learn/articles/glossary-of-trainingpeaks-metrics/
  - 2026-10-07 已驗證（`sync-platforms.md` §3.3）；2026-10-12 連不上，今天沒能重讀（摘要）。
- 「關於我們」頁："Our registered trademarks include TrainingPeaks®, TSS®, Training Stress Score®, IF®, Intensity Factor®, NP®, Normalized Power® and VirtualCoach®."
  - https://www.trainingpeaks.com/about-us/
  - 2026-10-12 搜尋摘要（摘要）。
- 使用規範：**未找到來源**。
  - 搜尋 trainingpeaks.com 的 trademark guidelines、brand usage、legal 頁，沒找到公開的商標使用規範。
  - 只找到 2012 年 Peaksware 當時的 CEO Gear Fisher 在 Slowtwitch 論壇的發言："We are happy to discuss the use of the terms with anyone, we claim no royalty, simply attribution."（摘要；14 年前，公司已被收購，不能當現行政策）。
- **授權實務**：Stages 碼錶手冊、Polar 手冊寫 TSS／NP／IF "are trademarks of TrainingPeaks, LLC and are used with permission"（摘要）。the5krunner 2026-07-22 的文章說 Wahoo、Hammerhead、TrainerRoad、Garmin 都有授權，Strava 和 Today's Plan 則自己取名避開（摘要，非官方）。
- **所有權變動**：Garmin 2026-07-22 新聞稿："Garmin Ltd. … today announced it has acquired the TrainingPeaks and TrainHeroic training platforms"，賣方是 Peaksware Holdings（摘要；garmin.com 與 peaksware.com 都有同一篇）。商標有沒有跟著移轉、移轉到哪個法人：**未找到來源**。

### 2.2 逐詞結果

| 詞 | 結果 | 依據（2026-10-12 查） | 標記 |
|---|---|---|---|
| **TSS** | 註冊 | USPTO serial 85913859，reg. 4554475，所有人 TrainingPeaks, LLC，class 41（uspto.report 鏡像）；TP 關於頁列為 ® | 摘要 |
| **Training Stress Score** | 註冊（美、歐） | USPTO serial 85913838，reg. 4566410，2014-07-15 註冊，Section 8 & 15 已受理（Justia 鏡像）；EUIPO 005194568「Training Stress Score (TSS)」（Trademark Elite 鏡像） | 摘要 |
| **NP** | TP 自稱 ® | 關於頁列為 ®；縮寫 NP 本身的 USPTO 紀錄沒找到 | 摘要／未找到來源 |
| **Normalized Power** | 註冊 | USPTO serial 85913884，reg. 4450849，Section 8 & 15 已受理（Justia 鏡像） | 摘要 |
| **IF** | TP 自稱 ® | 關於頁列為 ®；縮寫 IF 的 USPTO 紀錄沒找到 | 摘要／未找到來源 |
| **Intensity Factor** | 註冊 | USPTO serial 85913945，reg. 4558501，2014-07-01 註冊，首次使用 2006-05-31（Justia 鏡像） | 摘要 |
| **TrainingPeaks** | 註冊 | USPTO serial 85913738，reg. 4443809，2024 續展（Trademarkia 鏡像） | 摘要 |
| **WKO+** | 註冊 | USPTO serial 85913825，reg. 4443813，軟體類（Justia 鏡像） | 摘要 |
| **WKO、WKO4、WKO5** | 沒找到單獨的註冊 | 只找到 WKO+；WKO5 是 TP 的產品名（help.trainingpeaks.com、wko5.com） | 未找到來源（產品名仍屬對方，推估） |
| **Performance Management Chart／Performance Manager／PMC** | 沒找到註冊 | 搜 Justia、USPTO 鏡像與 TP 關於頁都沒有；TP 在文章裡當功能名（"The Science of the TrainingPeaks Performance Manager"）。the5krunner 的表把 PMC 列為商標，但沒附紀錄 | 未找到來源 |
| **CTL、ATL、TSB** | 沒找到註冊 | 不在 TP 關於頁清單；有第三方部落格（Patreon 上的 intervals.icu 指南）說是 Peaksware 的註冊商標，但沒附紀錄，和 TP 自己的清單也對不上 | 未找到來源（第三方說法標摘要） |
| **hrTSS、rTSS、sTSS** | 沒找到註冊 | 不在 TP 清單；但內含註冊商標 TSS，延伸使用一樣有風險 | 未找到來源／推估 |
| **iLevels** | 沒找到註冊 | TP、Garmin 都當產品功能名 | 未找到來源 |
| **mFTP、TTE、FRC、Pmax、TIS、Stamina** | 沒查到註冊 | 沒有逐一搜尋註冊（只在 TP 清單與 Justia 擁有人列表的摘要裡沒看到）；TTE（time to exhaustion）、Pmax 是生理學通用詞 | 未找到來源／推估 |

**台灣（TIPO）**：
- 今天連不上智慧財產局的商標檢索系統（代理拒絕連線）。
- 搜尋引擎也找不到 TrainingPeaks、TSS、Training Stress Score 在台灣的註冊資料（未找到來源）。
- 台灣沒註冊，不代表可以放心用（推估）：
  1. 我們的英文版和示範站對全世界公開；
  2. Garmin 在台灣有分公司，可以隨時申請；
  3. 用別人的知名名稱，還可能涉及公平交易法的攀附他人商譽（推估，沒有查判決）。

  **建議請律師在 SP-60 審閱時一起查 TIPO**（§6 問題 11）。

### 2.3 什麼樣的使用比較安全（法律面只到「推估」）

- 台灣商標法第 36 條第 1 項第 1 款的「描述性／指示性合理使用」：以誠實信用的方法說明自己商品或服務的相關資訊，而且**不是作為商標使用**，就不受他人商標權拘束（摘要，理律新知與智慧財產法院判決整理；條文逐字未能讀到）。
- 套到我們的情況（推估）：

| 用法 | 例子 | 判斷 |
|---|---|---|
| 指出對方或對方的資料 | 「從 TrainingPeaks 同步」「TrainingPeaks 算的 TSS®」「Friel（TrainingPeaks 部落格）」 | **風險低**（推估） |
| 把對方商標當自己指標的名字 | 課表欄「TSS」、手錶上「80 TSS」、ICS「TSS 64」、「功率 TSS」 | **風險高**（推估）。使用者會以為是 TrainingPeaks 的指標或得到授權，Golden Cheetah 被要求改名的也正是這一種 |
| 沒註冊的通用縮寫 | CTL／ATL／TSB | **法律風險低，但有爭議風險**（推估）：沒找到註冊，業界普遍在用，但 Garmin 接手後態度不確定，第三方也有人說它們是商標 |

---

## 3. 同類 app 的做法

| App | 單次負荷 | 長期／短期／差值 | 強度、等效功率 | 怎麼處理 TP 名稱 | 標記 |
|---|---|---|---|---|---|
| **intervals.icu** | **Load**（Power load、HR load、Pace load） | **Fitness／Fatigue／Form**（Form 可用 % 顯示） | Intensity、Norm Power（第三方指南的說法） | 第三方指南與論壇說因為商標不能用 CTL／ATL／TSB 和 TSS 的名稱 | 摘要（論壇與 Patreon 指南；`competitor-charts.md` 2026-09-30 讀過 Fitness／Fatigue／Form 與 load 的官方論壇頁） |
| **Strava** | **Relative Effort**（心率）、Training Load（功率） | **Fitness & Freshness** 頁裡的 Fitness／Fatigue／Form | Weighted Average Power | 自己取名（the5krunner：Strava 刻意避開） | 摘要（`competitor-charts.md` 引 support.strava.com 216918477、360000197364） |
| **Golden Cheetah** | **BikeStress**（Coggan 式）、**BikeScore**（Skiba 式） | **LTS／STS／SB**（Long／Short Term Stress、Stress Balance） | **IsoPower**、**BikeIntensity**；Skiba 的 **xPower**、RelativeIntensity | 官方 FAQ："Training Peaks have asked that their trademarks are removed, so starting with v3.5 the 3 metrics in question were renamed." | 摘要（GC GitHub wiki FAQ-METRICS、使用者群組） |
| **Garmin** | **Training Load**（EPOC） | **Acute Load**、Chronic Load、**Load Ratio**（0.8–1.4 optimal） | — | 手錶與 Garmin Connect 自己的指標用自己的名；Edge 碼錶顯示 TSS 時附 Peaksware 授權聲明 | 摘要（Garmin 手冊 "Acute load is a weighted sum of your excess post-exercise oxygen consumption (EPOC)…"） |
| **COROS** | **Training Load（TL）** | **Base Fitness**（42 天）／**Load Impact**（7 天）／**Fatigue**；比值叫 **Intensity Trend** | — | 自己取名 | 摘要（COROS EvoLab 說明頁；`competitor-charts.md`） |
| **Stryd** | **RSS**（Running Stress Score；在 CP 跑 1 小時 ≈ 100） | **RSB**（Running Stress Balance）＝42 天與 7 天加權平均的差 | — | 自己取名，說 RSS 和 TSS 可比（"comparable to cycling's TSS"） | 摘要（help.stryd.com 6879537、blog.stryd.com 2017-01-28） |
| **TrainerRoad、Wahoo、Hammerhead** | 直接用 TSS／IF／NP | — | — | 取得授權並附聲明 | 摘要（the5krunner 2026-07-22，非官方） |

中文名稱：COROS 中文介面的「訓練負荷」「基礎體能」「負荷衝擊」、Garmin 中文的「訓練負荷」「急性負荷」，是我依印象寫的，沒有查證（推估）。

**重點**：

- 有授權的才直接用 TSS、IF、NP；沒授權的都改了名。
- CTL／ATL／TSB 這三個縮寫，intervals.icu、Strava、COROS、Golden Cheetah **都沒有當主要標籤用**。業界的共通說法是 **Fitness／Fatigue／Form**，中文我們已經是「體能／疲勞／狀況」。
- 「Training Load／訓練負荷」是 Garmin、COROS 的叫法。**我們推到 COROS 時會同時出現 COROS 的 TL**，所以我們自己的字最好**不要也叫「訓練負荷」**，否則畫面上會有兩種「訓練負荷」（推估）。

---

## 4. 建議用語（中英對照表）

原則：

1. **標籤用自己的字**，對照只放在說明裡；
2. 盡量用 app **已經在用**的字（「負荷」「體能／疲勞／狀況」「W′ 電池」「功率–時間模型」），改動最少；
3. 中文要短，越野跑者一看就懂；
4. 英文跟 intervals.icu 的說法接近，使用者從那邊來不會陌生。

| 現在 | 建議（zh-TW） | 建議（en） | 其他選項 | 理由 |
|---|---|---|---|---|
| TSS（單次） | **負荷** | **Load** | ①「訓練負荷／Training load」②「負荷分數／Load score」 | 課表結束條件已經叫「負荷／Load」；①會和 COROS TL、Garmin 撞名；②較長 |
| 功率 TSS | **功率負荷** | **Power load** | 「負荷（功率）」 | intervals.icu 同樣的分法 |
| rTSS | **配速負荷** | **Pace load** | 「負荷（配速）」 | 同上 |
| hrTSS | **心率負荷** | **HR load** | 「負荷（心率）」 | 同上 |
| TSS/天、TSS/週 | **負荷/天**、**負荷/週** | **Load/day**、**Load/week** | — | 軸單位 |
| 每小時 TSS | **每小時負荷** | **Load per hour** | — | RPE 填負荷的說明 |
| TrainingPeaks 的 TSS（來源標籤） | **TrainingPeaks 算的負荷（TSS®）** | **Load from TrainingPeaks (TSS®)** | 「TrainingPeaks TSS®」 | 這是對方的資料，指示性使用可以留名稱，加 ® |
| IF | **強度比** | **Intensity** | ①「強度」②「相對強度／Relative intensity」 | 公式寫成「每小時負荷 = 強度比² × 100」，比單寫「強度」清楚，「強度」已經另有很多意思 |
| NP | **加權功率** | **Weighted power** | ①「等效功率／Equivalent power」②「標準化功率」（直譯，不建議：等於翻譯對方商標） | Strava 叫 Weighted Average Power；「加權」說得出怎麼算 |
| VI（NP ÷ 平均功率） | VI（加權功率 ÷ 平均功率） | VI (weighted ÷ average power) | — | 只換分子名稱 |
| EF NP÷心率 | EF 加權功率÷心率 | EF weighted power ÷ HR | — | 同上 |
| CTL 體能 | **體能** | **Fitness** | 「長期負荷／Long-term load」 | 已在用；拿掉縮寫 |
| ATL 疲勞 | **疲勞** | **Fatigue** | 「短期負荷／Short-term load」 | 同上 |
| TSB 狀況 | **狀況** | **Form** | 「新鮮度／Freshness」（Strava） | 同上；順便把「狀態」「新鮮度」統一成「狀況」 |
| Form%（TSB ÷ CTL） | **狀況 %**（狀況 ÷ 體能） | **Form %** | — | Form 是一般英文字 |
| 負荷比 ATL ÷ CTL | **負荷比**（疲勞 ÷ 體能） | **Load ratio** (fatigue ÷ fitness) | — | Garmin 也叫 Load Ratio |
| CTL ramp／Ramp rate | **體能週增量** | **Fitness ramp** | 「體能每週變化」 | — |
| 起始 CTL／ATL | **起始體能／疲勞** | **Starting fitness / fatigue** | — | 設定頁 |
| PMC、「負荷 PMC」 | **體能圖**；dashboard 叫「**負荷與體能**」 | **Fitness chart**；dashboard "**Load & fitness**" | ①「體能・疲勞・狀況」（總覽已用，長）②「負荷趨勢／Load trend」 | 短；intervals.icu 的頁面也叫 Fitness |
| mFTP | **模型閾值功率** | **Modelled threshold power** | 「PD 模型閾值」 | 只在 WKO5 對照模式保留 "mFTP" |
| TTE | **可撐時間（TTE）** | **Time to exhaustion (TTE)** | 「力竭時間」 | 生理學通用詞，風險低；加中文就好 |
| FRC | **W′** | **W′** | — | app 其他地方已統一用 W′ |
| dFRC | **W′ 電池** | **W′ balance** | — | 卡片已叫「W′ 電池」，說明文字跟上 |
| Pmax | **最大功率** | **Max power** | — | 通用 |
| TIS（Training Impact Score） | **有氧／無氧刺激分數** | **Aerobic / anaerobic stimulus** | 「刺激分數」 | 說明裡已經寫「刺激」；出處另寫「算法參考 WKO5」 |
| WKO5 對照模式 | **WKO5 檔案對照模式**（只在自架版） | **WKO5 file comparison** (self-hosted only) | 商業版直接不顯示 | 指出對方檔案格式是指示性用法 |
| WKO5 資料夾 | **WKO5 資料夾（.wko4 檔）**（只在自架版） | **WKO5 folder (.wko4 files)** | — | 同上 |
| 頁面標題 WKO5 View Check | **圖表** | **Charts** | — | 每個人都看得到 |
| Form 區間英文名 High Risk／Optimal／Grey Zone／Fresh／Transition | 中文不變 | **保留**，但 glossary 的理由改成「業界常用」 | 自己取名，例如 Overreaching／Productive／Neutral／Fresh／Detraining | 都是一般英文字，沒有註冊紀錄，intervals.icu 也這樣用；見問題 6 |
| 出處「TrainingPeaks TSS 公式」 | **「負荷的算法：強度比² × 時數 × 100（Coggan 公開的公式）」** | "Load = intensity² × hours × 100 (Coggan's published formula)" | — | 算法是公開的，出處寫作者，不寫對方商標當算法名 |
| 出處「Friel／TrainingPeaks」「TrainingPeaks 的完成度顏色」 | **「Friel（TrainingPeaks 部落格）」「參考 TrainingPeaks 的完成度顏色」** | "Friel (TrainingPeaks blog)" | 保留現狀 | 指示性使用，保留沒問題，只是寫清楚是文章出處 |

**和 COROS TL 放在一起時**：「負荷 80（約 COROS TL 95）」，英文 "Load 80 (≈ COROS TL 95)"。不用「訓練負荷」，兩邊才分得開。

**數值不變**：負荷照樣是「在閾值跑 1 小時 = 100」，只是名稱換掉。使用者從 TrainingPeaks 搬過來，數字還是對得上；要讓他們知道這件事，就用 §5 的對照寫法。

---

## 5. 保留對照時的寫法

### 5.1 什麼時候需要對照

只在兩種地方（推估，見問題 3）：

1. **比對頁、圖表的「TSS 來源」**：資料本身就是 TrainingPeaks 算的，要講清楚來源。
2. **「負荷」的 `?` 說明和「關於／方法」頁**：告訴從 TrainingPeaks、intervals.icu 過來的人，數字的量尺一樣。

其他地方都**不寫** TSS／CTL：標籤、欄名、手錶、ICS、示範站首屏、行銷文案、App 名稱與網址。

### 5.2 寫法規則

- 寫**對方的名稱**，主詞是我們自己的指標，不要寫成「我們的 TSS」「TSS（本 app 計算）」。
- 每頁（或每段說明）**第一次出現加 ®**，後面可以不加。
- 不用對方的 logo、字型、配色。
- 不說「官方」「認證」「相容於 TrainingPeaks 的 TSS 算法」。可以說「**量尺相同**」「**數字大致可以對照**」，因為我們的算法細節不同（移動時間、爬升加成、CP 取代 FTP），本來就不會完全一樣。

### 5.3 範本

**負荷的說明（`?`）**

> 負荷：一次訓練有多累，綜合時間和強度算成一個數字。在閾值強度跑 1 小時＝100。算法是 Coggan 公開的公式（強度比² × 時數 × 100），有功率用功率，沒有就用配速或心率。量尺和 TrainingPeaks 的 TSS® 相同，數字大致可以對照，但細節不同（例如只算移動時間、爬升有加成），不會完全一樣。

> Load: how hard one session was, combining time and intensity into one number. One hour at threshold = 100. It uses Coggan's published formula (intensity² × hours × 100), from power when you have it, otherwise pace or heart rate. It is on the same scale as TrainingPeaks' TSS®, so the numbers are roughly comparable, but details differ (e.g. moving time only, a climbing bonus), so they will not match exactly.

**體能圖的說明**

> 體能、疲勞、狀況：體能是 42 天的平均負荷，疲勞是 7 天，狀況＝昨天的體能 − 疲勞（Banister 模型，Coggan 用在功率訓練）。其他平台常寫成 CTL、ATL、TSB。

> Fitness, fatigue and form: fitness is the 42-day average load, fatigue the 7-day one, form = yesterday's fitness − fatigue (the Banister model, as applied to power training by Coggan). Other platforms often call these CTL, ATL and TSB.

（CTL／ATL／TSB 沒找到註冊，所以這裡不加 ®。要不要連這句都拿掉，見問題 2。）

**資料來源標籤**

> TrainingPeaks 算的負荷（TSS®）

> Load from TrainingPeaks (TSS®)

### 5.4 商標聲明（中英）

> **商標聲明**
> TrainingPeaks®、TSS®、Training Stress Score®、NP®、Normalized Power®、IF®、Intensity Factor®、WKO+® 是 TrainingPeaks, LLC 的註冊商標；WKO5 是 TrainingPeaks 的產品名稱。COROS、Garmin、Stryd、Strava 及其他名稱是各自所有人的商標。TrailRunCoach 與上述公司沒有任何關係，也沒有獲得它們的授權或背書。本服務的「負荷」「強度比」「加權功率」「體能／疲勞／狀況」是我們自己的指標；提到上述名稱，只是為了說明資料來源、檔案相容性，或讓你對照數字。

> **Trademarks**
> TrainingPeaks®, TSS®, Training Stress Score®, NP®, Normalized Power®, IF®, Intensity Factor® and WKO+® are registered trademarks of TrainingPeaks, LLC; WKO5 is a TrainingPeaks product name. COROS, Garmin, Stryd, Strava and other names are trademarks of their respective owners. TrailRunCoach is not affiliated with, authorised or endorsed by any of these companies. Load, Intensity, Weighted power and Fitness / Fatigue / Form in this service are our own metrics; the names above are mentioned only to identify a data source, a compatible file format, or to let you compare numbers.

注意：

- 所有人寫 **TrainingPeaks, LLC**：USPTO 鏡像站上的登記名義人是它（摘要）。Garmin 收購後名義人有沒有改，動工前要再查（問題 11）。
- 這段聲明會取代 README `:133` 那行 "Not affiliated with COROS, TrainingPeaks or Stryd."，也和 `todo-multi-user-sharing.plan.md` §7.4 擬的那句合併。

### 5.5 放在哪裡

1. **「關於」頁（新）**：
   - app 目前**沒有**關於頁（已驗證：`grep 關於|about|disclaim` 只找到說明按鈕的 aria-label 和傷病頁的免責文字）。
   - 建議放在 shell 選單或設定頁最下面一個「關於」區塊，內容是版本、出處（Coggan、Banister、Palladino、Friel…，見 `todo-multi-user-sharing.plan.md` §7.3）、§5.4 的商標聲明。
   - 示範站也要有，是同一個頁面。
2. **服務條款（SP-60）**：在「智慧財產」或「第三方服務與商標」條放同一段，加一句「本服務與第三方平台的串接不代表其背書；第三方可能隨時變更或停止服務」。
3. **README**：取代 `:133`，中英各一段。
4. **對照出現的地方**（§5.1 那兩處）：說明文字末尾一行「TSS® 是 TrainingPeaks, LLC 的註冊商標。」，或連到關於頁的聲明。

---

## 6. 要你決定的問題

1. **TSS 的新名字用「負荷／Load」嗎？**
   - **建議：是。** 課表結束條件已經叫這個，改動最小；英文和 intervals.icu 一樣。
   - 不用「訓練負荷」，因為會和 COROS TL、Garmin 撞名。
   - 變體：功率負荷、配速負荷、心率負荷。
2. **CTL／ATL／TSB 三個縮寫怎麼處理？**
   - (a) 標籤、圖例、圖表標題都拿掉，只在說明裡寫一次「其他平台常寫成 CTL、ATL、TSB」；
   - (b) 全部拿掉；
   - (c) 保留現狀（沒找到註冊）。
   - **建議 (a)**：法律風險本來就低，但它們是 TrainingPeaks 推廣的名稱、現在屬於 Garmin，同類 app 都沒當主標籤用；留一句說明，老手找得到。
3. **要保留「相當於 TSS」的對照嗎？**
   - **建議：只留兩處**：TrainingPeaks 來源的標籤、負荷與體能圖的 `?` 說明。都用 §5 的寫法加 ® 與聲明。
   - 手錶、ICS、欄名、示範站首屏、行銷文案都不寫。
4. **商業版的 WKO5 功能怎麼處理？**（WKO5 對照模式、WKO5 資料夾、WKO5 設定值、WKO5 client 的 OAuth 登入）
   - **建議：商業版不顯示**，只留給自架（示範站已經刪掉 WKO5 字樣）。
   - 自架版改寫成「WKO5 檔案對照」這種指示性說法。
   - 「沿用 WKO5 的 client 憑證登入」本來就有違反條款的風險，商業版一定要關（和 SP-343 問題 7 一起決定）。
5. **使用者看得到的內部名稱「wko5」要改嗎？**
   - 網址 `/api/v1/wko5/...`、圖表頁 `<title>WKO5 View Check</title>`。
   - **建議：商業版上線前改**：網址改成例如 `/app/...`，舊路徑轉址；標題改「圖表」。
   - `~/.wko5coach`、`WKO5COACH_*` 環境變數只有自架的人看得到，之後再遷移（`todo-multi-user-sharing.plan.md` §7.4 本來就說名稱不要用「WKO5 Coach」）。
6. **Form 區間的英文名要不要改？**（High Risk／Optimal／Grey Zone／Fresh／Transition，10-02 決定「照 TrainingPeaks 原名」）
   - **建議：保留這五個字**：都是一般英文字，沒有註冊紀錄，intervals.icu 也這樣用。
   - 但 glossary 與 `i18n.plan.md` 的理由改成「業界常用」，畫面上不要寫「TrainingPeaks 的區間名」。
7. **IF、NP 的新名字用「強度比／Intensity」「加權功率／Weighted power」嗎？**
   - **建議：是。**
   - 不用「標準化功率」「強度係數」：它們是商標的直譯，中文社群雖然常用，等於還是在用對方的名稱。
8. **mFTP、TIS、FRC、dFRC 這些 WKO5 指標名要改嗎？**
   - **建議：改成自己的名字**（模型閾值功率、有氧／無氧刺激分數、W′、W′ 電池），出處寫「算法參考 WKO5」。
   - 原名只留在 WKO5 檔案對照模式。
9. **要不要寫信向 TrainingPeaks／Garmin 申請 TSS 等名稱的授權？**
   - **建議：不要。** 改名比較簡單，也不受收購後政策變動影響。
   - 授權要談條件，Garmin 的態度不確定；2012 年「只要標示出處」的說法不能當現行政策。
10. **聲明放在「關於」頁＋服務條款（SP-60）＋README＋對照處一行，可以嗎？**
    - **建議：可以。** 「關於」頁是新頁面，可以和 `todo-multi-user-sharing.plan.md` §7.3 的出處標示一起做，開一張小單。
11. **要請律師一起看嗎？**（SP-60 已規劃條款由律師審閱）
    - **建議：是，併進 SP-60 的審閱，不另外花錢。** 請律師確認三件事：
      1. TIPO 有沒有相關註冊；
      2. Garmin 收購後的商標名義人；
      3. §5.4 聲明和「量尺相同」這種比較說法在台灣可不可以。
    - 動工前，§2.2 的 USPTO 註冊號也要有人用瀏覽器在 TSDR 直接看一次（今天只能讀鏡像站摘要）。

---

## 7. 實作範圍估計

以 §6 的建議為準（問題 1、2a、3、4、5、7、8 照建議）。數字是腳本加人工抽查的估計（推估）。

| 輪 | 內容 | 字串數（不重複，約） | 主要檔案 |
|---|---|---|---|
| **1. 一定要改（商業版上線前）** | 標籤、欄名、圖例、軸單位、ICS、推到手錶的文字、圖表頁標題、網址路徑 | 約 105 句短標籤（中文）＋對應英文 | `overview.html`、`schedule.html`、`workout_editor.js`、`compare.html`、`settings.html`、`wko5_viewer.html`、`engine/status.py`、`engine/wko5expr/units.py`、`engine/calendar_feed.py`、`sync/coros_workouts.py`、`sync/workout_targets/coros.py`、`views/training.json`、`views/periodization.json`、`static/i18n/*/{overview,schedule,compliance,activity,compare,settings,racepower,common}.json`、`shell.js`（路由） |
| **2. 說明文字** | `?` 提示、「怎麼看／方法」、後端判讀與建議句子 | 中文約 210 句（負荷類約 150、體能類約 116、WKO 指標約 37，有重疊）；英文約 130 句 | 同上，加上 `engine/{overview,adapt,quality_gate,b2b,plan_auto,rpe_load,workout_steps,workout_review,load_guard,coros_tl,debug_view,…}.py`、`api/racepower.py`、`racepower/*.py`、`views/*.json` 的 description |
| **3. 自架專用與出處** | WKO5 對照模式改寫、商業版隱藏、TrainingPeaks 出處改寫、README、關於頁、glossary | WKO 產品名約 100 句、TP 名稱約 48 句（大多保留，只改寫法） | `settings.html`、`wko5_viewer.html`、`sourcechip.js`、`threshold_confidence.py`、`racepower/athlete.py`、`README.md`、`backend/i18n/glossary.json`、新的關於頁 |

**總量**：約 **65 個檔案**、**350 處**左右（中英合計）。

**連帶要處理的**（已驗證，讀程式碼）：

- **後端 msgid 是中文原文**：中文一改，`backend/i18n/locales/en.json` 和 `en.meta.json` 的 key 要跟著換，約 70 筆。用 `backend/scripts/i18n_extract.py` 重抽，再把英文對回去。
- **views 的系列名翻譯以中文原名當 key**（`views/i18n/en.json`、`wko5expr/viewi18n.py:11`）：改中文系列名，sidecar 的 key 要同步。
- **前端拿顯示字串做判斷**：`wko5_viewer.html:539` 的 `u.label === "TSS/天"`，要改成只看 `u.id`（`i18n.plan.md` §2 本來就列為要修的 bug）。
- **`backend/i18n/glossary.json`**：
  - 「體能 CTL」等條目改成新用語；
  - 新增「負荷 → Load」「強度比 → Intensity」「加權功率 → Weighted power」，設 `check: true`；
  - 另加反向檢查：英文出現 "TSS"／"CTL"，而且不是 §5.1 那兩處時就失敗，防止新字串又寫回去（推估，要擴充 `test_i18n_coverage.py`）。
- **測試**：`backend/tests/` 約 61 行斷言含 TSS／CTL／TSB 字樣，要逐一看是比對顯示文字（要改）還是 key（不用改）。
- **`views/wko5_fixes.json`**：對應的是使用者自己的 WKO5 dashboard 名稱（例如「Daily % of CTL」），不是我們的 views，**不受影響**。
- **API 的 key**（`tss`、`ctl`、`atl`、`tsb`、`tss_source`）、資料庫欄位、`wko5expr` 的運算式名稱（`ctl`、`tsb`）都**不用改**，因為使用者看不到。

**規模**：M，約 2–3 天。第 1 輪約半天；第 2 輪約 1.5 天，長句要人工看語氣，順便統一「狀況／狀態／新鮮度」；第 3 輪約半天，加上關於頁另算。i18n 的 SP-78 正在補英文，**最好在 SP-78 繼續往下翻之前先做第 1、2 輪**，免得英文翻好又要改一次。

---

## 參考（全部 2026-10-12 查；「摘要」＝搜尋引擎摘要或鏡像站，今天無法直接開啟原頁）

- TrainingPeaks：
  - 關於我們（註冊商標清單）：https://www.trainingpeaks.com/about-us/ （摘要）
  - 指標詞彙頁：https://www.trainingpeaks.com/learn/articles/glossary-of-trainingpeaks-metrics/ （2026-10-07 已驗證，見 `sync-platforms.md`；今天摘要）
  - Performance Manager 文章：https://www.trainingpeaks.com/learn/articles/the-science-of-the-performance-manager/ （摘要）
  - NP／IF／TSS 文章：https://www.trainingpeaks.com/learn/articles/normalized-power-intensity-factor-training-stress/ （摘要）
- Garmin 收購：https://www.garmin.com/en-US/newsroom/press-release/corporate/garmin-acquires-trainingpeaks-and-trainheroic-leading-endurance-and-strength-training-platforms-for-athletes-and-coaches/ 、https://peaksware.com/garmin-acquires-trainingpeaks-and-trainheroic-leading-endurance-and-strength-training-platforms-for-athletes-and-coaches/ 、https://the5krunner.com/2026/07/22/garmin-acquires-trainingpeaks-what-changes/ （摘要）
- USPTO 鏡像（都是摘要，要回 TSDR 核對）：
  - TSS：https://uspto.report/TM/85913859
  - TRAINING STRESS SCORE：https://trademarks.justia.com/859/13/training-stress-85913838.html
  - NORMALIZED POWER：https://trademarks.justia.com/859/13/normalized-85913884.html
  - INTENSITY FACTOR：https://trademarks.justia.com/859/13/intensity-85913945.html
  - WKO+：https://trademarks.justia.com/859/13/wko-85913825.html
  - TRAININGPEAKS：https://www.trademarkia.com/trainingpeaks-85913738
  - 擁有人列表：https://trademarks.justia.com/owners/trainingpeaks-llc-4253944 、https://www.trademarkia.com/owners/trainingpeaks-llc 、https://uspto.report/company/Trainingpeaks-L-L-C
  - EUIPO：https://www.trademarkelite.com/europe/trademark/trademark-detail/005194568/Training-Stress-Score-TSS-
- 授權實務：Stages 手冊 https://manuals.stagescycling.com/en/dash-m200-l200/user-manual/about-the-dash/trademark/ ；Slowtwitch 討論 https://forum.slowtwitch.com/t/can-you-trademark-a-metric-tss-if-np-etc/579296 （摘要）
- 同類 app：
  - intervals.icu：https://forum.intervals.icu/t/fitness-page-a-guide-to-getting-started/17702 （`competitor-charts.md` 2026-09-30）、https://forum.intervals.icu/t/does-tss-load/62092 、https://www.patreon.com/posts/fitness-scores-135377761 （摘要）
  - Strava：https://support.strava.com/hc/en-us/articles/216918477-Fitness-Freshness 、https://support.strava.com/hc/en-us/articles/360000197364-Relative-Effort （`competitor-charts.md`）
  - Golden Cheetah：https://github.com/GoldenCheetah/GoldenCheetah/wiki/FAQ-METRICS 、https://github.com/GoldenCheetah/GoldenCheetah/wiki/FAQ-RUNNING-&-SWIMMING （摘要）
  - Garmin：https://www8.garmin.com/manuals/webhelp/GUID-708A8F4D-9A78-49CF-9528-DE109BBCC472/EN-US/GUID-AEDB0872-C5A1-4378-86D5-2239734B59E8.html （摘要）
  - COROS：https://support.coros.com/hc/en-us/articles/26485283220884-EvoLab 、https://support.coros.com/hc/en-us/articles/4412789816724-EvoLab （摘要；`competitor-charts.md`）
  - Stryd：https://help.stryd.com/en/articles/6879537-running-stress-score-rss 、https://blog.stryd.com/2017/01/28/running-stress-score/ （摘要；`competitor-charts.md`）
- 台灣商標法合理使用：https://www.leeandli.com/TW/Newsletters/6369.htm 、https://www.leeandli.com/TW/Newsletters/4464.htm （摘要）；條文請看 https://law.moj.gov.tw/ （今天未讀）
- TIPO 商標檢索：https://tiponet.tipo.gov.tw/ （連不上，未找到來源）
- repo 內：`docs/research/sync-platforms.md` §3.3、§5、§6；`docs/research/competitor-charts.md` §1；`docs/plans/i18n.plan.md` §0、§5；`docs/plans/todo-multi-user-sharing.plan.md` §7.3、§7.4；`backend/i18n/glossary.json`。
