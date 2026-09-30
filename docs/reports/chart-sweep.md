# 全部圖表掃描（chart sweep）

2026-09-30。範圍：圖表分析頁（`/api/v1/wko5/viewer`）四個 view 的每一張圖——WKO5 的 WKO5 Season View、WKO5 Workout View，自訂的 我的訓練、周期化訓練——加上首頁（`/api/v1/overview/page`）和賽事功率頁（`/api/v1/racepower/page`）。

## 方法

- **API 掃描**：`GET /views`，每張圖打 `/views/{view}/dashboards/{d}/charts/{c}`。趨勢圖用 `begin=2025-10-01&end=2026-09-30`；單次活動圖各跑 #1097（有功率的路跑）、#1094（越野跑）、#1073（登山健行）。每條 series 記錄 data.kind、點數、錯誤訊息，以及是否全部 NaN／0／常數。共 270 次圖表請求（單次活動圖每筆活動算一次）。
- **瀏覽器掃描**（Playwright）：用 `?view=&dash=` 開每個 dashboard，讀每張卡片的 ECharts option，找出沒畫任何 series、只有座標軸、空表格、錯誤文字、console error 的卡片，並截圖。首頁與賽事功率頁另外切換週／月／年、上一期、路跑／越野／百岳。
- 「單次活動」的卡片數以 3 筆活動 × 圖表計。

## 總數

| | 修正前 | 修正後 |
|---|---:|---:|
| 檢查的圖（含每筆活動） | 270 | 270 |
| 什麼都沒畫／只有座標軸 | 24 | 0 |
| 顯示「尚未支援」的面板（地圖） | 3 | 0 |
| 空卡片但會說明原因（資料確實不存在） | 0 | 22 |
| 有畫出東西但部分 series 因資料壞掉而缺，且卡片會說明 | 0 | 6 |
| 首頁、賽事功率頁的壞圖 | 0 | 0 |

## 修正（依根因分類）

### 1. 評估器（evaluator gap）

- **核准的資料校正對 mean-max／PD 圖沒效果** — `backend/engine/wko5expr/evaluator.py: Evaluator._cached_curve`。`meanmax(power)` 先讀 WKO5 Cache5 的曲線，而那是原始樣本的曲線，已核准的功率尖峰校正又被帶回來。現在只要該檔有核准的校正，就改用校正後的樣本重算。實測：在拋棄式校正檔核准三筆尖峰提案後，2025-10-01–2026-09-30 的 PD 模型從「擬合失敗」變成 mFTP 185 W、TTE 1:01:48、valid。

### 2. Render／JSON 形狀

- **EPH by EP**（及所有 `(每筆活動的值, 每筆活動的值)` 散佈圖） — `render.py: _pair_series_json`。兩邊都是逐筆活動（WS）或逐日（Daily）序列的 pair，原本被壓成一個 null 點。現在每筆活動一點。
- **`(x,)` 是垂直線** — `render.py: _scalar_pair_json`，新增 `vline`。`(avg(power),)`（HR vs Power 圖的平均功率線）、`(0,)`（LSS 飄移率 by 坡度的 0% 線）、`(153,)`／TTE 線原本回傳空值或 null 點。單次活動圖裡兩個單值的 `(x, y)` 也改成一個點（原本是空的 value）。
- **單次活動的 x-y 散佈圖被標成時間軸** — `render.py: workout_result_to_json / _is_time`。`(ewma(power,30), heartrate)`、`(rgrade, LSS)` 原本 `x: "seconds"`，檢視器因此把它們從功率／坡度 x 軸的圖中濾掉（HR vs Power by % of work／by grade、LSS 飄移率 by 坡度的散佈點都沒畫）。現在只有 x 真的是 elapsedtime 才算時間軸。
- **地圖面板** — `backend/api/wko5views.py: _panel_kind / chart`、`render.py: render_map`。WKO5 的 `PKMapPanelConfig` 原本是「地圖／其他面板尚未支援」。現在回傳 GPS 軌跡（最多 2000 點），檢視器畫成依海拔上色的軌跡（沒有底圖）。

### 3. 檢視器繪製（viewer drawing）— `backend/static/wko5_viewer.html`

- **單一數值的 gauge 從來沒畫過**（This Week Climbing／Run Distance／Run Duration／Run TSS、Stamina）：趨勢圖的單值回傳 `hline`，但 `drawGauges` 只收 `values`／`value`。現在 `hline`／`none` 也畫成 gauge；沒有值時顯示「本週還沒有活動」或「這段期間沒有資料」。
- **只有單值的報表**（MMP Peaks and Clusters Report，15 條 `hline`、沒有 points）原本整張空白：`drawPlot` 只在有 points 時才畫。現在沒有 points 時把 hline 列成 名稱／數值／單位 表格。
- **`line_style: "none"` 是散佈圖**：原本當實線畫，數千個樣本點依時間順序連成一團線。現在畫成 scatter（`large` 模式）。
- `vline` 畫成 x 軸 markLine，而且不會為它多開一條 y 軸。
- 新增 `drawMap`。

### 4. 空卡片說明（資料確實不存在）— `render.py: empty_reason / pd_notice`、檢視器 `.nodata`

所有 series 都沒資料時，API 回傳 `empty`（一句話），卡片顯示這句話，而不是畫出空的座標軸：

| 卡片 | 原因 | 顯示 |
|---|---|---|
| #1073 登山健行：Power chart、Power Summary、Avg Power、np、Power Variation and Trend、Energy System Impact、PWHR、Aerobic／Anaerobic TIS（9 張） | 手錶沒有記錄功率 | 這筆活動沒有功率資料（裝置沒有記錄）。 |
| #1073：Cadence、Cadence Summary、Cadence Variation and Trend、步頻 vs 垂直比（4 張） | 沒有步頻 | 這筆活動沒有步頻資料（裝置沒有記錄）。 |
| This Week Climbing／Run Distance／Run Duration／Run TSS（4 張） | 本週（9/28 起）還沒有活動；最後一筆是 9/24 | 本週還沒有活動，這段期間沒有資料。 |
| 本周目標runTSS、本周EP v.s 目標EP | 同上（目標值有畫，本週值是 —） | gauge 下方顯示「本週還沒有活動」 |
| Stamina、Run Interval Targeting FRC、Aerobic and Anaerobic Contribution to Power Run（3 張） | **PD 模型擬合失敗**，原因是功率資料壞掉，見下 | 說明＋到「設定 › 資料校正」核准的指引 |

Donny's Optimized Interval Targeting、PD Curve with Metrics、Best Times for Informal Testing、PD Curve Profile、VLamax、MMP Peaks and Clusters Chart 有畫出 mean-max，但 PD 線是空的 → 卡片上方顯示同一段 `notice`。

**PD 模型為什麼擬合失敗**：2025-12-14 的路跑（#929）有 520 個 >905 W 的樣本，10 分鐘平均 908 W；2025-10-26 越野跑（#895）有 1594 W 的尖峰（另外還有 2024-06-29 #600）。所以一年的 run-power 包絡線 5 分鐘 1173 W、20 分鐘 193 W，Gauss-Newton 在 FTP 撞到下限後矩陣奇異。`/corrections/proposals` 已經列出這三筆，但使用者還沒核准。依照資料校正的設計（「只列建議，按下套用才生效」），我沒有替使用者核准；核准後（加上修正 1）這些圖就會畫出來。MMP Peaks Report 的 1449 W／1319 W 也是同樣的尖峰。

## 資料確實不存在、只有部分 series 空白的（沒有動）

卡片其他 series 有畫，空的那條在「數值與公式」表裡顯示 —：

- `if(hastag("race"), …)` 的 Races 標記：這段期間沒有任何活動標 race。
- Daily Training Log 的 Notes／Description：所有活動的 notes、desc 都是空的。
- `if(TSB >= 25, …)`、`if(tisaerobic > 6, …)`：TSB 沒有超過 25、有氧 TIS 最高 6。
- `goalclimbperkm`、`goalhours`（我的訓練、周期化訓練）：賽季計畫沒有設 A 賽事。
- 溫度、Garmin `@vertical_oscillation`：裝置沒有記錄（Stryd 的垂直振幅有）。
- 路跑 #1097 的 `rgrade >= 0.3`：路跑沒有 30% 以上的坡。
- VO2max 間歇標記（v3／v4）全 0：這些活動沒有符合條件的 VO2max 間歇。

## 仍然存在、屬於圖表設計的小問題

- EPH by EP：WKO5 的定義把 `EPH=5`／`EPH = 7` 常數線和第一條 series 放在不同的 y 軸（NONE 與 CUSTOM@eph），所以兩條參考線畫在左軸刻度上，和右軸的 EP/h 點位置不對齊。要修的話在 `views/wko5_fixes.json` 把兩條線改到 CUSTOM@eph。

## 首頁、賽事功率頁

首頁（PMC、每日 TSS、做了什麼 週／月／年／上一期、爬升、指標）和賽事功率頁（CP、Riegel；路跑／越野／百岳）的每個 ECharts 都有畫出資料，沒有錯誤文字，也沒有 console error。賽事功率頁本來就排除了 2025-12-14 的異常活動。

## 測試

`backend/tests/test_chart_sweep_fixes.py`（15 項）：快取曲線與校正、WS pair、`(x,)` vline、單次活動的 scalar pair、散佈圖的 x 種類、各種空卡片原因（缺功率／步頻、功率衍生指標、本週無活動、PD 失敗、部分 PD notice）、只有 hline 的報表不算空、地圖、檢視器的靜態檢查。
