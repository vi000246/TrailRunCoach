# 主流訓練分析平台的圖表與指標調查，以及哪些值得加進 wko5_coach

日期：2026-09-30。範圍：網路公開資料加上 repo 唯讀盤點，沒有改任何程式。

對象：路跑、越野、百岳（常是多日、背重裝），裝置是 COROS 錶加 Stryd，另有少量自行車和肌力。

相關文件，本文不重複其內容：
- `docs/research/coaching-dashboards-mountain.md`（下稱 **CDM**）：教練實際看哪些圖，以及 Intervals.icu、GC、Runalyze、Stryd 的概覽。
- `docs/research/uphill-athlete-mountain-metrics.md`（下稱 **UA-notes**）：UA 的 TSS 修正、AeT 飄移、VAM、Minetti、EP。
- `docs/research/periodization-phase-metrics.md`：各周期該看的圖。

## 怎麼讀這份文件

- **公式欄**只填實際讀到的頁面上寫明的式子。廠商沒公開的一律寫「未公開／專有」，只描述廠商自己講過的輸入。
- 標籤沿用 CDM 的慣例：
  - **[vendor]**：廠商的說法，視同行銷。
  - **[validated]**：同儕審查研究。
  - **[coach]**：教練經驗。
  - **[ours]**：我們自己的推論。
- 標「非官方」的數值來自第三方網站（例如 garminrumors wiki、DC Rainmaker），沒有對照過原廠手冊。
- 調查限制：
  - WebSearch 額度（200 次）中途用完，後段只能抓已知網址。
  - help.trainingpeaks.com 全站回 403。
  - support.coros.com 直接抓會 403，改經代理才讀到。
  - 讀不到的頁面列在 §6。

---

## 1. 各平台的圖表／指標表

### 1.1 intervals.icu

| 圖表／指標 | 顯示什麼 | 怎麼算 | 怎麼呈現 | 來源 |
|---|---|---|---|---|
| Fitness (CTL) | 長期負荷 | 訓練負荷的 42 天 EWMA，時間常數可調 | 折線（論壇描述是藍色，官方沒給色碼） | https://forum.intervals.icu/t/fitness-page-a-guide-to-getting-started/17702 |
| Fatigue (ATL) | 短期疲勞 | 7 天 EWMA | 折線（論壇描述是紫色） | 同上 |
| Form / Form % | 準備度 | 絕對值 = CTL − ATL；百分比 = (CTL − ATL) × 100 / CTL，新帳號預設用百分比 | 折線加背景色帶，另有「Clip Form」選項 | 同上；https://forum.intervals.icu/t/form-as-a-percentage-of-fitness/869 |
| **Form zones** | 把 Form% 分成五區 | Transition > +20%、Fresh +5～+20%、Grey −10～+5%、Optimal −30～−10%、High risk < −30%。這是把 Friel 的絕對 TSB 區間直接拿來當百分比用；−30% 等同 ATL/CTL > 1.3 | 背景色帶：Fresh 藍、Optimal 綠、Grey 灰、High risk 紅。Transition 的顏色沒找到出處 | https://forum.intervals.icu/t/zones-of-form-in-fitness-chart/3623 |
| Ramp rate | CTL 每週的增量 | Fitness 頁：本週一 CTL − 上週一 CTL。自訂圖版本：每天對 7 天前的差，再取 7 日平均 | 可獨立成一張圖，官方沒有警示色帶 | https://forum.intervals.icu/t/ramp-rate-calculation/2107 |
| Training load / HR load / HRSS | 單次負荷 | power load 同 TSS，但扣掉不動的時間。HR load 預設是對各心率區時間做回歸，權重未公開。HRSS 是 normalized TRIMP | 數值，累加進 CTL/ATL | https://forum.intervals.icu/t/how-training-load-estimation-from-hr-works/280 |
| Pace load + GAP | 跑步用配速算負荷 | GAP 用 Strava 的模型並扣掉停止時間；pace load 的式子未公開 | pace curve 可在 GAP 和一般配速間切換 | https://forum.intervals.icu/t/gradient-adjusted-pace-pace-training-load/4031 |
| Pace curve / CS・D′ | 配速—時長曲線與臨界速度 | CS 是距離—時間線性關係的斜率，D′ 是截距 | 曲線加 best efforts 表，可跨季疊圖 | https://forum.intervals.icu/t/pace-curves-and-best-efforts-for-running-etc/12929?page=2 |
| Power curve + eFTP | 功率曲線與模型 | 用一次 3–30 分鐘的最大努力，對到一條事先用大量資料以 Morton 3P 建好的曲線上，取其 1 小時點。另有 Morton 3P 和 Monod-Scherrer 模型 | 多季疊圖，W/kg 可切換 | https://www.intervals.icu/features/power-curve/ |
| Aerobic decoupling | 前後半段效率差 | `(ef1 − ef2) × 100 / ef1`，ef = 功率 / HR | 活動的 Power:HR 圖 | https://forum.intervals.icu/t/aerobic-decoupling-calculation-question/1823 |
| **Polarization Index** | 強度分布有多極化 | Treff 2019：PI = log10(Z1/Z2 × Z3 × 100)，Z 是三區時間占比。Z2 = 0 時改用 log10(Z1/Z3 × 100)；Z3 = 0 時 PI = 0；S3 > S1 時回傳 null。PI > 2.00 算極化 | 同一個數出現在活動摘要、週摘要、列表欄位和趨勢圖 | https://forum.intervals.icu/t/polarization-index-added/49877 ；https://www.frontiersin.org/articles/10.3389/fphys.2019.00707/full |
| Compliance | 計畫的完成度 | 實際負荷 × 100 / 計畫負荷，沒有負荷時改用時間 | 80–120% 綠、50–150% 橘、其他紅，標在日曆上 | https://forum.intervals.icu/t/compliance-activity-field/47805 |
| Normalised HRV | 用靜息心率修正的 rMSSD | HRV:HR = rMSSD × RHR / 600 | /fitness 頁的綠點 | https://forum.intervals.icu/t/normalised-hrv-added-to-fitness-page/106679 |
| Wellness / Custom charts | 睡眠、HRV、RHR、體重，以及從 70 多個指標自組的圖 | — | 每張圖有兩個色槽，一條線、一條線周圍的 band；可設背景區帶；可搜尋別人分享的圖 | https://www.intervals.icu/features/wellness/ ；https://www.intervals.icu/features/custom-charts/ |
| Routes（CDM §6.1） | 自動辨識重複路線，看同一路線的進步 | 未公開 | — | https://www.intervals.icu/features/track/ |

### 1.2 TrainingPeaks

| 圖表／指標 | 顯示什麼 | 怎麼算 | 怎麼呈現 | 來源 |
|---|---|---|---|---|
| PMC：CTL / ATL / TSB | 體能、疲勞、狀況 | 42 天與 7 天的指數加權平均；TSB = 昨日 CTL − 昨日 ATL | TSB 是黃色線，沒練的日子在底部畫紅點；官方建議範圍是略負到 +25 | https://www.trainingpeaks.com/learn/articles/what-is-the-performance-management-chart/ |
| TSB 區間（教練文） | 狀態判讀 | — | 巔峰 +15～+25、訓練區 −10～−30、低於 −30 要連續休息 | https://www.trainingpeaks.com/coach-blog/a-coachs-guide-to-atl-ctl-tsb/ |
| CTL Ramp Rate | CTL 每週增量 | 未公開 | 手機版 PMC Report 顯示 7/28/90/365 天的值；引用 Friel 的每週 5–8 | https://www.trainingpeaks.com/blog/4-new-mobile-features-you-should-know-about/ |
| TSS / rTSS / hrTSS | 單次負荷 | TSS = (秒 × NP × IF)/(FTP × 3600) × 100。rTSS 用 NGP 對 FTPace，細節未公開 | 儀表板有每日／每週 TSS 長條 | https://www.trainingpeaks.com/learn/articles/estimating-training-stress-score-tss/ ；https://www.trainingpeaks.com/learn/articles/running-training-stress-score-rtss-explained/ |
| Efficiency Factor | 有氧效率 | NP 或 NGP ÷ 平均心率 | 趨勢 | https://www.trainingpeaks.com/learn/articles/glossary-of-trainingpeaks-metrics/ |
| Aerobic Decoupling | Pa:Hr / Pw:Hr | 前半段對後半段比較 | <5% 耐力好、5–10% 受限、>10% 超過 AeT 或耐力不足 | https://www.trainingpeaks.com/learn/articles/aerobic-endurance-and-decoupling/ |
| **Compliance colors** | 實際與計畫的吻合度（時間、距離或 TSS） | 公開規則 | 綠 ±20%；黃 50–79% 或 121–150%；橘偏離 >50%；紅未完成；灰無計畫 | https://www.trainingpeaks.com/learn/trainingpeaks-athlete-user-guide/ |
| Fitness History | 最近 4 週峰值對比過去 12 個月 | — | 各時長或距離的峰值並列 | https://www.trainingpeaks.com/blog/the-top-7-dashboard-charts-for-coaches/ |
| Peak Power / Pace / HR | 兩段期間的峰值曲線 | — | 雙期間疊圖，也能用來抓離群資料 | 同上 |
| Completed Distance/Duration | 計畫量 vs 實際量 | — | 計畫灰、照計畫完成淺色、超出深色 | 同上 |
| 2025–2026 新功能 | Analyze 360（自動抓 interval 與爬坡、Stacked Charts）、Modeled Power、Thresholds Over Time、Health Insights（Oura HRV） | 未公開 | — | https://www.trainingpeaks.com/trainingpeaks-feature-updates/ ；https://www.trainingpeaks.com/changelog/ |

**ACWR**：在讀得到的 TP 頁面裡**找不到** ACWR 圖表或它的閾值。常見的 0.8–1.3 出自第三方（scienceforsport、Catapult），不是 TP 公開的內容。

### 1.3 Golden Cheetah

| 圖表／指標 | 顯示什麼 | 怎麼算 | 怎麼呈現 | 來源 |
|---|---|---|---|---|
| PMC（LTS/STS/SB/RR） | 可用任何壓力指標當輸入的 PMC | STS 預設 7 天 EWMA、LTS 預設 42 天；SB = 昨日 LTS − 昨日 STS；RR 是每 7 天的 LTS 增量 | Metric Trends 曲線；公式語言有 `pmc(expr, …)` | https://github.com/GoldenCheetah/GoldenCheetah/wiki/UG_Glossary |
| Banister model | 負荷對實際表現的擬合 | Perf = p0 + k1·g − k2·h，預設 t1 = 50 天、t2 = 11 天，用 LM 擬合 k1、k2 | Trends 曲線 | https://raw.githubusercontent.com/GoldenCheetah/GoldenCheetah/master/src/Metrics/Banister.cpp |
| W′bal | 剩餘無氧容量 | Skiba：τ = 546·e^(−0.01·(CP − 低於 CP 時的平均功率)) + 316。微分式：P < CP 時 W += (CP−P)(W′−W)/W′，否則 W += (CP−P) | 疊在活動圖上 | https://raw.githubusercontent.com/GoldenCheetah/GoldenCheetah/master/src/Metrics/WPrime.cpp |
| CP / MMP 與模型 | 平均最大功率曲線加 CP 擬合 | CP2 / CP3 / Extended CP | 可加區間底色、百分位；「curve heat」標出落在最佳值 10% 內的活動 | https://github.com/GoldenCheetah/GoldenCheetah/wiki/UG_ChartTypes_Trends |
| TRIMP Points | Banister 心率負荷 | 分鐘 × HRr × 0.64 × e^(k·HRr)，k 男 1.92、女 1.67 | 數值，也能當 PMC 的輸入 | https://raw.githubusercontent.com/GoldenCheetah/GoldenCheetah/master/src/Metrics/TRIMPPoints.cpp |
| TRIMP(100) / Zonal / sRPE | 不同 TRIMP 變體 | TRIMP(100)：在 LTHR 跑 1 小時 = 100；Zonal = Σ(區間係數 × 分鐘)；sRPE = 分鐘 × RPE | 數值 | 同上 |
| Aerobic Decoupling | 同 TP | 100 × (前半 P/HR − 後半 P/HR) / 前半；跑步版用速度取代功率 | 數值 | https://raw.githubusercontent.com/GoldenCheetah/GoldenCheetah/master/src/Metrics/AerobicDecoupling.cpp |
| xPower / RI | 類 NP | 25 秒 EWMA（NP 是 30 秒移動平均）；RI = xPower / CP | 數值 | https://github.com/GoldenCheetah/GoldenCheetah/wiki/UG_Glossary |
| **Overview Metric tile** | 單一指標 | — | 大數字，加近 6 週同運動的 sparkline、min/avg/max，以及金銀銅名次 | https://github.com/GoldenCheetah/GoldenCheetah/wiki/UG_ChartTypes_Activities |
| Overview KPI / Zones / Bubble / Table | 目標進度、區間分布、三指標散佈 | — | KPI 是進度條；Zones 可切三區極化模型；Table 可做成熱圖 | https://github.com/GoldenCheetah/GoldenCheetah/wiki/UG_ChartTypes_Trends |

### 1.4 Runalyze

| 圖表／指標 | 顯示什麼 | 怎麼算 | 怎麼呈現 | 來源 |
|---|---|---|---|---|
| Effective VO2max | 用每次跑步的配速對心率估 VO2max；目前狀態 = 近 30 天平均 | 依 Daniels 的 %HRmax 對 %vVO2max 關係，式子未寫。另乘個人 correction factor（建議 0.85–0.95），可依爬升／下降修正距離 | 時間曲線加單次點和比賽點；每筆活動有箭頭標示高於或低於目前狀態；可把離群活動排除 | https://runalyze.com/help/article/vo2max ；https://blog.runalyze.com/features/new-view-for-your-running-performance-status/ |
| Marathon Shape | 目前的量能不能撐該距離 | 週跑量佔 2/3（看 182 天）、長跑佔 1/3（看 10 週，13 km 起算）；目標值依 VO2max 查表 | 各距離門檻：21k 43%、42k 100%、50k 123%、100k 288%、100mi 518% | https://runalyze.com/help/article/marathon-shape ；https://blog.runalyze.com/tutorial/runalyze-understanding-the-calculations/ |
| **Race prognosis** | 各距離預測時間 | 可選 VDOT（Daniels）、Cameron、Steffny、Bock CPP 四種模型，長距離再用 Marathon Shape 修正；式子未公開 | 表格，同一畫面比較多個模型 | https://runalyze.com/help/article/features |
| TRIMP / ATL / CTL / TSB | 心率負荷與 PMC | Banister TRIMP（HRR 加性別權重）；ATL 顯示成**歷史最大 ATL 的百分比**；TSB 官方說無法給門檻 | ATL% 到 100% 不建議 | https://runalyze.com/glossary/atl ；https://runalyze.com/glossary/tsb |
| A:C ratio | 短長期負荷比 | ATL : CTL | <0.80 undertraining、0.80–1.30 optimal、1.30–1.50 overreaching、>1.50 overtraining | https://runalyze.com/glossary/ac-ratio |
| **Monotony** | 最近 7 天有多單調 | 2025 起改成 avg / (SD + avg)，值域 0.29–1.0，<0.6 推薦、>0.67 危險。舊版是 Foster 的 avg / SD，門檻 1.5 / 2.0 | 百分比 | https://runalyze.com/glossary/monotony |
| **Training strain** | 過度訓練風險 | Strain = sum(TRIMP) × Monotony / 0.5 | 綠／橙／紅，門檻依 ATL 推估（未公開） | https://runalyze.com/glossary/training-strain |
| **Climb Score / FIETS** | 路線的爬坡難度 | FIETS = 爬升² / (距離 m × 10) + (山頂海拔 − 1000)/1000。Climb Score = 2.0 × log2(0.5 + (1+S)(1−p²)) | 0–10 分；爬坡分級 HC ≥6.5、Cat1 ≥5.0、Cat2 ≥3.5、Cat3 ≥2.0、Cat4 ≥0.5、Cat5 ≥0.25 | https://runalyze.com/glossary/climb-score |
| **Uphill/Downhill Efficiency** | 上坡／下坡段與平路的有氧效率比 | 預期約 1.0，<1 可能是技術或路況；式子未公開 | 單一數值 | https://runalyze.com/glossary/uphill-downhill-efficiency |
| Aerobic Decoupling | Pa:Hr | (AE₁ − AE₂) / AE₁ | 0–10% | https://runalyze.com/glossary/aerobic-decoupling |
| Running Effectiveness | 功率轉速度的效率 | RE = speed(m/s) / (W/kg) | ≤0.95 低、0.96–0.98 平均、0.99–1.01 高、≥1.02 世界級（Stryd、平地） | https://runalyze.com/glossary/running-effectiveness |
| **Running dynamics 色階** | 跑姿五色分級 | 依百分位：紫 >95、藍 70–95、綠 30–69、橙 5–29、紅 <5 | 步頻 >183 / 174–183 / 164–173 / 153–163 / <153；GCT(ms) <218 / 218–248 / 249–277 / 278–308 / >308；VO(cm) <6.4 / 6.4–8.1 / 8.2–9.7 / 9.8–11.5 / >11.5 | https://runalyze.com/glossary/running-dynamics-color-scale |
| Trend / ANOVA | 單項數值的走勢與分期比較 | — | 散點加趨勢；ANOVA 用 box plot | https://runalyze.com/help/article/features |

### 1.5 Stryd PowerCenter

| 圖表／指標 | 顯示什麼 | 怎麼算 | 怎麼呈現 | 來源 |
|---|---|---|---|---|
| RSS | 代謝面的負荷 | 100 × 時數 × (P/CP)^K，在 CP 跑 1 小時 = 100；a、b、K 未公開 | My Training 長條 | https://blog.stryd.com/2017/01/28/running-stress-score/ ；https://help.stryd.com/en/articles/6879537 |
| **RSB** | 狀態 | 42 天加權平均 − 7 天加權平均 | 折線加色帶：−45～−40 Overreaching、−40～−25 Cautionary、−25～−10 Productive、−10～5 Maintenance、5～25 Performance | https://help.stryd.com/en/articles/6879346 |
| My Training | 每日量 | — | 長條可切 RSS、時間、距離、爬升；CP 上升畫綠三角、下降畫紅三角 | https://help.stryd.com/en/articles/6879350 |
| **Power-Duration Curve** | 1 秒到 5 小時的最佳功率 | 取 90 天內資料加自動 CP 模型（未公開） | 依資料新舊上色：藍 = 近 30 天，紫、紅 = 權重遞減，紅色即將過期 | https://help.stryd.com/en/articles/6879351 |
| Training Distribution | Fitness、Muscle Power、Endurance、Fatigue Resistance 與社群比較 | 未公開 | 放在 PDC 旁；點一項就推薦對應課表 | https://help.stryd.com/en/articles/8809297 |
| Form Power Ratio | 跑步經濟性 | FPR = Form Power / Power | 趨勢；同速度下降代表經濟性變好 | https://help.stryd.com/en/articles/6879522-stryd-metrics |
| LSS / ILR | 腿剛性、衝擊負荷率 | LSS = 最大垂直力 ÷ 位移；ILR 的單位是 bw/s | 個人趨勢，沒有公開區間 | 同上 |
| **LBSS** | 機械負荷（骨骼、肌腱），與 RSS 互補 | 累計 ILR 相對 Critical Impact，1 小時 ≈ 100；式子未公開 | 2026-03 的文章主張應獨立成一個分數 | https://blog.stryd.com/2026/03/03/why-mechanical-load-deserves-its-own-score/ |
| Race Power Calculator | 目標功率與預測時間 | CP 模型加 fatigue factor，並修正海拔、溫度、濕度、風、爬升；未公開 | 選路線後輸出結果 | https://help.stryd.com/en/articles/8955821 |

### 1.6 COROS Training Hub / EvoLab

| 圖表／指標 | 顯示什麼 | 怎麼算 | 怎麼呈現 | 來源 |
|---|---|---|---|---|
| Base Fitness | 長期負荷 | 過去 6 週 Training Load 的指數加權（式子專有） | 趨勢線（DCR 描述是綠色） | https://support.coros.com/hc/en-us/articles/26485283220884-EvoLab |
| Load Impact | 短期負荷 | 近 7 天的指數加權（專有） | 趨勢線（DCR 描述是藍色） | 同上；https://www.dcrainmaker.com/2021/05/revamped-training-explainer.html |
| **Intensity Trend** | 短長期負荷比 | **Load Impact ÷ Base Fitness（公開）** | 5 區：≥150% Excessive、100–149% Optimized、80–99% Maintaining、50–79% Resuming、0–49% Decreasing | https://support.coros.com/hc/en-us/articles/4412789816724-EvoLab |
| Fatigue | 0–100 分 | 專有 | 官方說有 5 區，但沒找到公開的數字邊界 | https://the5krunner.com/2021/05/18/coros-evolab-sports-physiology-to-rival-garmin-firstbeat/ |
| Training Load（7 天） | 7 天負荷加總，附建議區間 | 專有，輸入是心率區間時間 | 數值加建議區間帶（依約 42 天推算） | https://www.dcrainmaker.com/2021/05/revamped-training-explainer.html |
| Training Focus | 單次課表分類 | 專有 | Easy、Base、Tempo、Threshold、VO2 Max、Anaerobic 六類 | https://support.coros.com/hc/en-us/articles/4412789816724-EvoLab |
| Running Performance | 本次相對平時的表現 | 專有 | 80–95 Poor、96–98 Fair、99–101 Good、102–104 Great、105–120 Excellent | 同上 |
| Recovery % | 恢復程度 | 專有；官方明說不含睡眠和 HRV | 兩頁官方文件的分級不一致 | https://support.coros.com/hc/en-us/articles/360061452651-COROS-Fitness-Metrics-Explained |
| Marathon Level / Race Predictor | 全馬能力與各距離預測 | 專有，依 6 週訓練推算 | 0–100 分共 5 級；另有列表 | https://support.coros.com/hc/en-us/articles/4412802807444-EvoLab |
| Overnight HRV | 夜間 HRV 對照個人 baseline | 專有 | Elevated / Normal / Reduced / Low；呈現方式是數值加 baseline 區間（例：44 對 45–57 ms） | https://coros.com/stories/coros-metrics/c/your-coros-recovery-metrics-explained |
| Hill Alerts | 路線上的爬坡段 | 專有 | 依難度分色的分段清單 | https://support.coros.com/hc/en-us/articles/47116651977364-Hill-Alerts （403，未讀到） |

**COROS 的 HRV 能不能拿到？**
- COROS API 提供 RHR 和睡眠起訖（沒有睡眠階段）：https://forum.intervals.icu/t/coros-support-added/37006 、https://runalyze.com/help/article/coros
- intervals.icu 開發者在 2024-08 表示已接上夜間 HRV，但 2024-11 仍有人回報沒匯入：https://forum.intervals.icu/t/coros-support-added/37006?page=5
- FIT 檔是否含 HRV：未驗證。
- **我們的同步（`backend/sync/coros_client.py`）目前沒抓任何 wellness 資料**，所以下面的推薦都不含 HRV。

### 1.7 Garmin Connect

基底網址：`https://www8.garmin.com/manuals/webhelp/GUID-0221611A-992D-495E-8DED-1DD448F7A066/EN-US/`（Forerunner 965 手冊）。

| 圖表／指標 | 顯示什麼 | 怎麼算 | 怎麼呈現 | 來源 |
|---|---|---|---|---|
| Training Status | 綜合訓練狀態 | 專有；輸入是 VO2max 趨勢、HRV status、Acute Load、Load Focus | 9 種狀態，從 Detraining 到 Strained | 基底 + GUID-6F81BF5B-B49A-4506-95E2-0F4A04D8B319.html |
| Acute Load | 短期負荷 | 近幾天 EPOC 的加權總和（權重未公開） | 儀表，分 low / optimal / high / very high | 基底 + GUID-AEDB0872-C5A1-4378-86D5-2239734B59E8.html |
| **Load Ratio** | acute / chronic | 公開到比值層級 | <0.8 Low、0.8–1.4 Optimal（綠）、1.5–1.9 High、≥2.0 Very High | 基底 + GUID-200689D7-F65C-40F0-BB82-3C51236C676A.html |
| **Training Load Focus** | 4 週負荷分成三類 | 專有 | 三根長條，各有目標區間框；顏色 Anaerobic 紫、High Aerobic 橘、Low Aerobic 淺藍（非官方） | 基底 + GUID-C3205D96-DAB6-4C93-A225-5B8D7B5A5621.html ；https://wiki.garminrumors.com/Training_Load |
| HRV Status | 7 天平均對照 baseline | 專有 | Balanced 綠、Unbalanced 橘、Low 紅；圖表是 baseline 區間帶加每日值 | 基底 + GUID-9282196F-D969-404D-B678-F48A13D8D0CB.html |
| Training Readiness | 今天適不適合練 | 專有，6 個輸入 | 95–100 紫、75–94 藍、50–74 綠、25–49 橘、1–24 紅，並逐一列出各輸入的狀態 | 基底 + GUID-C21BE0C8-A08E-4DA1-B6C6-2E0E2DDDB372.html |
| Training Effect | 單次課表的刺激 | 專有（EPOC 對映） | 0–5 分，從 No benefit 到 Overreaching | …/EN-GB/GUID-7275629E-743A-4658-A284-C84F42A66AE5.html |
| Endurance Score | 耐力能力 | 專有 | 7 級，依年齡性別（非官方數值） | https://wiki.garminrumors.com/Endurance_Score |
| **Hill Score** | 爬坡能力 | 專有；分成 Hill Strength 和 Hill Endurance，只計坡度 >2% | 0–100 共 6 級（非官方） | https://wiki.garminrumors.com/Hill_Score |
| **Running Tolerance**（2025） | 跑步機械衝擊負荷對照可承受上限 | 專有；輸入是體重、速度、坡度、步頻、觸地時間。公開的比例：走路約為耐力跑的 ½，下坡可達同速平地的 3 倍 | 換算成「等效里程」，畫多週 tolerance 與 impact 的趨勢 | 基底 + GUID-C83A37F8-1ECE-4FDD-A852-03764F1ECCFF.html ；https://the5krunner.com/2025/05/20/garmin-running-tolerance-all-you-need-to-know/ |

### 1.8 Strava

| 圖表／指標 | 顯示什麼 | 怎麼算 | 怎麼呈現 | 來源 |
|---|---|---|---|---|
| Fitness & Freshness | Fitness、Fatigue、Form | Banister／Coggan 模型，輸入是 Training Load 或 Relative Effort；時間常數未公開 | 折線，比賽用紅點；點某天會列出當天的活動 | https://support.strava.com/hc/en-us/articles/216918477-Fitness-Freshness |
| Relative Effort | 心率負荷，可跨運動比較 | 專有 | 每次一個數字 | https://support.strava.com/hc/en-us/articles/360000197364-Relative-Effort |
| **Weekly RE range** | 本週建議負荷範圍 | 依前 3 週平均，寬度未公開 | 週累積曲線疊在淡色範圍帶上，分低於、範圍內、高於 | 同上 |
| GAP | 等值平地配速 | 由真實資料建模（2017）；下坡加成約在 −10% 最大，更陡反而變小 | 曲線、split、segment | https://support.strava.com/hc/en-us/articles/216917067-Grade-Adjusted-Pace-GAP |
| **Matched Activities** | 同一路線的進步 | 依起終點、方向、距離配對 | 散點加趨勢線；最快那次黃點、目前這次橘點 | https://support.strava.com/hc/en-us/articles/216918597-Matched-Activities |
| Training Log | 每週活動總覽 | — | 每次活動畫一個泡泡，大小依時間、距離或爬升，顏色依運動 | https://support.strava.com/hc/en-us/articles/206535704-Training-Log |

### 1.9 越野／登山專用工具

| 工具：指標 | 顯示什麼 | 怎麼算 | 怎麼呈現 | 來源 |
|---|---|---|---|---|
| ITRA km-effort | 難度距離 | km + D+(m)/100（跟我們的 EP 同源，見 UA-notes §3(b)） | 數字 | https://trailia.run/tools/itra-index-calculator （第三方引用 ITRA） |
| ITRA 分級與點數 | 賽事難度 | XXS <25、XS 25–44、S 45–74、M 75–114、L 115–154、XL 155–209、XXL ≥210；對應 0–6 點 | 類別標籤 | 同上；https://itra.run/FAQ/ItraScore （搜尋摘要） |
| ITRA Performance Index | 跨賽事能力 | 近 36 個月最好 5 場的加權平均；單場算法專有 | 0–1000，男 Elite >825 | https://en.run-motion.com/the-ranking-in-trail-running-with-the-itra-performance-index/ |
| UTMB Index | 分距離能力 | 各類別近 36 個月前 5 名成績的平均 | 20K/50K/100K/100M 並列 | https://en.wikipedia.org/wiki/UTMB_Index |
| Polar Cardio Load | TRIMP 負荷 | 權重專有 | 相對近 90 天，分五級 | https://support.polar.com/en/training-load-pro |
| **Polar Strain / Tolerance** | 負荷比 | Strain = 7 天平均日負荷；Tolerance = 28 天平均 | 比值 <0.8 Detraining、0.8–1.0 Maintaining、1.0–1.3 Productive、>1.3 Overreaching | 同上 |
| **Polar Muscle Load** | 機械功 | 由跑步功率算 kJ（60 分鐘約 700–1400 kJ） | 數字，與 cardio load 分開呈現 | 同上 |
| Polar Running Index | 類 VO2max | 專有（心率加速度，跑步 >12 分） | 依年齡分 7 級 | https://support.polar.com/en/running-index |
| Suunto Form zones | TSB 四區 | TP 式 PMC | Losing / Maintaining / Productive / Going Too Hard | https://www.suunto.com/sports/News-Articles-container-page/understand-and-manage-your-training-load-with-suunto/ |
| Suunto Climb guidance | 路線坡段 | 分段門檻未公開 | 剖面依 Flat / Uphill / Downhill / Climb / Descent 上色 | https://www.suunto.com/Support/Product-support/suunto_vertical/suunto_vertical/navigation/climb-guidance/ |
| **コース定数（山本正嘉）** | 登山路線的能量消耗 | 1.8 × 行動時間(h) + 0.3 × 距離(km) + 10.0 × 爬升(km) + 0.6 × 下降(km) | ~10 初級、~20 一般、~30 吃體力的單日、40+ 要過夜；另有體力度 1–10 | https://www.yamakei-online.com/yama-ya/detail.php?id=363 |
| 健行筆記 行程時間 | 用個人腳程推時間 | 平路 3–3.5 km/h；上下坡時間 = 落差 ÷ 個人上下坡速率；每公里落差 >350 m 時，下山約等於上山時間 | 文字 | https://hiking.biji.co/index.php?q=review&act=info&review_id=5685 |
| Naismith / Tobler | 健行時間與速度 | Naismith：5 km/h，每爬 600 m 加 1 小時。Tobler：W = 6·exp(−3.5·\|S+0.05\|) km/h | 速度對坡度曲線 | https://en.wikipedia.org/wiki/Naismith's_rule ；https://en.wikipedia.org/wiki/Tobler's_hiking_function |
| Minetti / Strava GAP 近似 | 坡度成本 | Minetti 多項式（UA-notes §4）；Fellrnr 給的 Strava 近似是 15.14i² − 2.896i | 成本對坡度曲線 | https://fellrnr.com/wiki/Grade_Adjusted_Pace |
| Vert.run | Mountain Index 與比賽預測 | 專有 | 進步曲線 | https://vert.run |

---

## 2. 我們目前有什麼（盤點）

以下是讀 JSON 和程式得到的，沒有開 server。

- **`views/training.json`**
  - 負荷 PMC：
    - PMC 圖，TSB 在 0、−10、−30、+10、+20 有虛線
    - 每日 TSS
    - Ramp rate：堆疊長條依 <5、5–7、≥7 上色，另有 4 週平均線
  - 訓練量：
    - 每週移動時間（依類別堆疊）
    - 每週時數變化
    - 每週跑量
    - 每週 EP
    - 每週爬升／下降
    - 肌力日曆
  - 強度：
    - 每週心率三區
    - 強度分配（100% 堆疊，75% 目標線）
    - Palladino 功率區間
  - 能力：
    - EF（輕鬆路跑）
    - Pa:HR
    - VAM（越野加登山）
    - 爬升密度
    - 每次移動時間
    - 耐久度（長時間後段飄移）
- **`views/periodization.json`**：
  - 四個周期分頁，各有各的圖，包括減量目標帶和 CTL 週增量
  - 一個區間表分頁
- **`views/workout.json`**：本次重點、有氧／飄移、間歇、爬坡與地形、配速與耐久、跑姿與膝蓋負荷（ILR 對坡度、衝擊 G 對坡度）。
- **匯入的 WKO5 視圖**
  - `docs/wko5-views/season-view.json`：
    - ACWR（ATL/CTL Ratio，只算跑步）
    - Daily % of CTL
    - PMC with Insights
    - TIS load
    - 各種 time in zones
    - PDC 加 metric trends
    - MMP Peaks
    - Aerobic/Anaerobic Impulse
    - VLamax
    - EPH
    - PDC after Fatigue
    - PD by grade
    - RE mean max
    - Stamina
  - `docs/wko5-views/workout-view.json`：
    - Elevation corrected power
    - Hilly Run Summary
    - LSS / LIS
    - VAM Analysis
    - Running Effectiveness
    - Running Dynamics
    - dFRC
- **總覽頁 `backend/static/overview.html`**：
  - 「還缺什麼」的狀態卡，來自 `backend/engine/status.py` 的 14 個 indicator：phase、fitness、form、volume、intensity、efficiency、drift、climb、long、density、strength、durability、testing、data
  - 本週課表
  - PMC（含照課表的預估虛線）
  - 期間統計磚和強度分配條
- **其他頁**：
  - 賽事功率 `racepower.html`：CP、Riegel、RE、登山日 EP/h
  - 登山／越野成就 `achievements.html`
- **運算式語言**，已確認的能力，都在 `backend/engine/wko5expr/evaluator.py`：
  - `tl(x, n)` 是 WKO5 式的指數平滑，v += (x_d − v)/n，逐日計算，沒練的日子 x_d = 0。所以 `tl(tss,1)` 就是含 0 的每日 TSS。
  - `shift(x, n)` 可以對逐日序列位移。
  - `stddev` 只有單參數的彙總形式，**沒有**分組或滾動版本。
  - `{lo:hi}` 範圍字面值會被畫成背景色帶（`render.py` 的 `kind: "band"`，前端用 ECharts markArea，透明度 0.12）。
  - `meanmax`、`workoutrange`、`athleterange`、`slr`、`bin`、`lookup` 都有。
  - `tisaerobic` / `tisanaerobic` 是內建。
  - 可用的通道：heartrate、power、speed、cadence、elevation、stancetime、verticaloscillation、rgrade、elapseddistance；Stryd 的開發者欄位用 `@impact_loading_rate`、`@leg_spring_stiffness`、`@form_power`。
  - 可用的活動欄位：climbing、descending、movingduration、vam、pahr、ef、hrtss 等。

---

## 3. 差距清單

### 3.1 已經有（列出我們的圖名）

| 別家的功能 | 我們的對應 |
|---|---|
| PMC CTL / ATL / TSB（TP、intervals、Strava、GC、Runalyze、Suunto） | 負荷 PMC →「PMC：體能 CTL・疲勞 ATL・狀況 TSB」，加上總覽 PMC |
| Ramp rate（TP、intervals、GC RR） | 負荷 PMC →「Ramp rate（CTL 每週變化）」、③ 專項期「體能成長速度」 |
| 每日／每週負荷長條（TP、Stryd My Training） | 「每日 TSS」、總覽期間統計磚 |
| 強度分布（TP time in zones、GC Zones tile） | 強度 →「每週強度分布（心率）」「強度分配」「Palladino 功率區間」 |
| EF（TP、Runalyze eVO2max 的精神） | 能力 →「有氧效率 EF」 |
| Aerobic decoupling（intervals、TP、GC、Runalyze） | 能力 →「心率飄移 Pa:HR」，加單次活動「有氧／心率飄移」分頁 |
| ITRA km-effort | 訓練量 →「每週努力距離 EP」（同一公式）；總覽 EP 磚 |
| 功率曲線、CP、PD model（Stryd、GC、intervals） | WKO5「PDC耐力模型」分頁、賽事功率頁 CP 分析 |
| Fatigued power curve（intervals fatigue resistance） | WKO5「PDC after Fatigue(Trail only)」；能力「耐久度」 |
| ACWR / Load Ratio（Garmin、Runalyze、COROS） | WKO5「ATL CTL Ratio 訓練負荷比 (only run)」（只算跑步，training.json 沒有） |
| Training Effect 有氧／無氧（Garmin） | WKO5 的 Aerobic / Anaerobic TIS（`tisaerobic` / `tisanaerobic`） |
| Running Effectiveness（Stryd、Runalyze） | WKO5「Running Effectiveness」「RE Mean Maximal」、賽事功率頁 RE |
| LSS / ILR（Stryd） | 單次活動「跑姿與膝蓋負荷」、WKO5「LSS and LIS- Stryd」 |
| Race Power Calculator（Stryd） | 賽事功率頁（`racepower.html`，SuperPower + Riegel） |
| 最長一次（Stryd Endurance view） | 能力「每次移動時間」、③「最長單次訓練」、總覽 long 指標 |

### 3.2 有，但做得比別人差

| 項目 | 別人好在哪 | 我們現在 |
|---|---|---|
| TSB 判讀 | intervals.icu 用 Form% 加**背景色帶**；Stryd RSB 有五區色帶；Suunto 為四區命名 | 只有 5 條灰綠紅**虛線**，沒有色帶，也沒有區名 |
| ACWR | Garmin、COROS、Polar、Runalyze 都**對比值畫色帶**並命名狀態 | 只在匯入的 WKO5 視圖裡，只算跑步，沒有色帶；training.json 沒有 |
| 強度分配 | intervals 用**單一 PI 數字**，同一個數出現在活動、週、列表和趨勢四處 | 有 100% 堆疊和 75% 目標線，但沒有一個可追蹤的單一數字 |
| 本週課表的完成度 | TP、intervals 用 **compliance 色**（綠 ±20%） | 總覽只有完成／未完成 |
| 功率曲線 | Stryd 依**資料新舊上色**，TP 做 4 週對 12 個月的比較 | WKO5 PDC 有，但沒有新舊色，也沒有兩段期間疊圖 |
| 狀態卡的 sparkline | GC Metric tile 有 **6 週 sparkline、min/avg/max、名次** | 總覽卡片有 sparkline，但沒有名次，也沒有「這次排近期第幾」 |
| 爬升／下降 | Garmin Running Tolerance、Stryd LBSS 把**下坡的機械負荷**變成一個會累積、有上限的量 | 只有每週下降公尺數的長條 |
| 跑姿 | Runalyze **五色百分位**色階附數值門檻 | 散佈圖沒有參考帶 |

### 3.3 沒有

| 項目 | 來源 | 對本運動員的價值 |
|---|---|---|
| Monotony / Strain（Foster、Runalyze） | Runalyze | 高：百岳週常是一次超大負荷配一整週休息，monotony 反而低，strain 則集中在少數幾天 |
| 下坡／機械負荷分數（LBSS、Running Tolerance、Polar Muscle Load） | Stryd、Garmin、Polar | 高：下山的膝蓋與股四頭肌負荷，心率反映不出來 |
| 同路線／基準爬坡進步（Matched Activities、Routes） | Strava、intervals | 高：UA 和 Koop 都推薦的 benchmark climb（CDM §2.1） |
| 路線難度分數（コース定数、Climb Score、FIETS 分級、ITRA 分級） | 山本正嘉、Runalyze、ITRA | 高：直接對應百岳行程分級 |
| 個人上坡／下坡腳程趨勢（健行筆記式） | 健行筆記、Naismith、Tobler | 高：可以拿來預估百岳每段的時間 |
| 上坡／下坡效率比 | Runalyze | 中：區分出「下坡技術」和「有氧」兩個限制因子 |
| 比賽預測表（多模型並列、附 Shape 門檻） | Runalyze、COROS、Stryd | 中：路跑有用；越野預測可信度低 |
| Polarization Index | intervals.icu | 中 |
| 週負荷建議帶（前 3 週平均） | Strava | 中：比 ramp rate 直覺 |
| Hill Score（爬坡力量 + 爬坡耐力兩軸） | Garmin | 中：可以用 VAM mean-max 的短段與長段近似 |
| Banister 表現模型擬合 | GC | 低：需要規律測試 |
| W′bal | GC | 低：越野很少靠 W′ 決策 |
| HRV status、Readiness、Body Battery、睡眠 | Garmin、COROS、intervals | 暫時不做：我們的同步沒抓 wellness。COROS API 有 RHR 與睡眠，HRV 不穩（§1.6）。要做得先寫同步 |
| VO2max 估計、Marathon Shape、Endurance Score | Runalyze、Garmin | 低：CDM §7 已決定不當 headline |

---

## 4. 前 10 名推薦（依對越野／百岳訓練決策的價值排序）

工作量：**S** 約半天，**M** 約 1–2 天，**L** 3 天以上。

「運算式可行」表示照 evaluator 現有函式判斷寫得出來，**但沒有實際跑過**；給的運算式都是草稿。

| # | 推薦 | 放哪個分頁 | 需要的資料 | 可行性 | 工作量 |
|---|---|---|---|---|---|
| 1 | **下坡機械負荷（每週與 7／28 天比）** | 訓練量（放在「每週爬升／下降」旁），並加一張總覽卡 | elevation、rgrade、speed、deltatime；有 Stryd 時再用 ILR | 單次量寫得出來：如 `sum(if(rgrade < -8, deltatime * speed * (1 - rgrade/10)))` 這類下坡加權距離，或 `sum(if(@impact_loading_rate>0, @impact_loading_rate*deltatime))`。週加總和 `tl(...,7)/tl(...,28)` 也寫得出來。權重是 [ours]：Garmin 只公開「下坡最高 3 倍」，Stryd 沒公開式子 | M |
| 2 | **基準爬坡／同路線進步** | 能力 | 經緯度、elevation、HR、時間 | **要寫後端**：路線或爬坡段配對，依起終點、方向、距離，像 Strava Matched。產出每次通過同一段的 VAM 和平均 HR，畫散點加趨勢，最快那次、最近那次標亮 | L |
| 3 | **PMC 狀況色帶（Form% 或 TSB 色帶）** | 負荷 PMC，總覽 PMC 也一起改 | 只要 TSS | 運算式可行：加一條 `tsb*100/ctl` 的 Form% 線；用 `{-30:-10}` 綠、`{-10:5}` 灰、`{5:20}` 藍、`{-60:-30}` 紅 當背景色帶，前端已經支援 band。要注意 CDM 的立場：沒有登山賽公開過最佳 TSB，色帶只能寫成方向參考 | S |
| 4 | **負荷比 7:42 加狀態色帶**（Garmin Load Ratio、COROS Intensity Trend、Polar） | 負荷 PMC | TSS | 運算式可行：`atl/ctl`（兩者都是 `tl` 指數平滑）；色帶 `{0:0.8}` `{0.8:1.3}` `{1.3:1.5}` `{1.5:3}`。必須標註「不是受傷預測」（CDM §3.2 引 Impellizzeri 2020）。也可以改用時數或 EP 的比值，避開 hrTSS 在長登山日高估的問題（UA-notes §5） | S |
| 5 | **路線難度：コース定数加 ITRA 分級** | 單次活動「本次重點」，加能力分頁「每次難度」散點；百岳計畫也能用 | movingduration、distance、climbing、descending | 運算式可行：`1.8*movingduration/3600 + 0.3*distance + 10*climbing/1000 + 0.6*descending/1000`（distance 是 km、climbing 是 m：現有 EP 圖就是 `distance + climbing/100`）。ITRA 分級用 EP 門檻 25/45/75/115/155/210 對照 | S |
| 6 | **個人上坡／下坡腳程趨勢**（健行筆記式；Garmin Hill Score 的兩軸） | 能力 | elevation、rgrade、deltatime、HR | 運算式大致可行：上坡 m/h = `sum(if(rgrade>8, 正的海拔差))/sum(if(rgrade>8, deltatime))*3600`，下坡同理。單次的海拔差要寫成 `_elevation - shift(_elevation,1)`（RGRADE_EXPR 就是這樣寫的）。若同時要依 HR < AeT 過濾且速度太慢，就改成後端的活動指標 | M |
| 7 | **Monotony 與 Strain** | 負荷 PMC（或 ④ 以外各周期） | TSS（也可換成時數） | 運算式應可行，但很冗長：`@d:=tl(tss,1)`，均值 = (@d + shift(@d,1) + … + shift(@d,6))/7，SD 用同樣 7 個 shift 手算平方和。athlete 層級的 @變數沒實測過；太慢就改成後端每日序列。門檻：Foster 版 1.5 / 2.0；Runalyze 2025 新版 0.6 / 0.67 | M |
| 8 | **上坡／下坡效率比**（Runalyze） | 單次活動「爬坡與地形」，趨勢放能力 | speed、HR、rgrade；有 Stryd 時用功率 | 運算式可行：`avg(if(rgrade>5, speed)) / avg(if(rgrade>5, heartrate))` 除以平路（\|rgrade\| < 2）的同一比值。上坡最好用 GAP 或功率，否則每段坡度不同會混淆 | S–M |
| 9 | **本週課表 compliance 色** | 總覽「本週該做什麼」 | 課表的計畫時間或 TSS，對照實際 | **要寫後端與前端**：`overview.py` 已有 done 與 done_by，再加一個比值；顏色照 intervals（80–120% 綠、50–150% 橘、其他紅）或 TP 的五色 | S–M |
| 10 | **Polarization Index 趨勢** | 強度（放「強度分配」下面） | HR（三區 AeT／LTHR） | 運算式可行：先用現有強度分配的三區每週占比 z1、z2、z3，（z 是 0–1 的小數，不是百分比；用百分比會讓 PI 整體 +2），再算 `if(z3>0 and z1>=z3, if(z2>0, log10(z1/z2*z3*100), log10(z1/z3*100)))`，並加 `(,2)` 門檻線。注意我們的三區是 AeT／LTHR，跟 Treff 的 VT1／VT2 接近但不相同 | S |

**落選但接近的：**
- 功率曲線依資料新舊上色加兩段期間疊圖（前端 M）。
- 跑姿五色色帶（運算式 S，但對越野決策的價值低）。
- 多模型比賽預測表（後端 M，路跑才有用）。
- 週負荷的「前 3 週平均」建議帶（運算式 S，其實可以併入 #4）。
- HRV 整合（先要寫 COROS wellness 同步，L）。

**排序理由 [ours]：**
- 百岳和越野的主要失敗模式是兩個：一是下山腿先崩，由 #1、#6、#8 處理；二是沒有可比的進步訊號，由 #2、#5、#6 處理。
- 負荷監控的主體已經有了，所以 #3、#4、#7、#10 都是「把已有的東西變好讀」，成本低。

---

## 5. 值得照抄的呈現方式

1. **比值和狀況用背景色帶，不用虛線**：intervals 的 Form zones、Stryd RSB、COROS Intensity Trend、Garmin Load Ratio。我們的 `{lo:hi}` band 已經能畫，只要在 PMC、Ramp rate、負荷比三張圖套上，並在圖例寫區名（Optimal、Grey…）。
2. **Runalyze 的比賽預測表**：一列一個距離，欄位放各模型的預測、PR，再加「需要的 Shape% 對目前值」。一眼看出限制是量還是速度。我們的賽事功率頁可以改成這種多模型並排的表格。
3. **Garmin Load Focus 的三根長條加目標框**：4 週負荷分成低有氧、高有氧、無氧三類，各畫目標區間。我們可以用 TIS 有氧／無氧，或三區時間做同樣的「缺哪一類」提示。
4. **GC 的 Metric tile**：大數字、6 週 sparkline、min/avg/max 加名次。總覽狀態卡可以補上「這次在近 12 週排第幾」。
5. **Stryd PDC 依資料新舊上色**（藍 → 紫 → 紅），並在每日長條上畫 CP 升降三角。這樣 CP 為什麼掉一看就懂。
6. **Strava Matched Activities**：同路線散點加趨勢線，最快的標黃、目前的標橘。這是 #2 的呈現範本。
7. **TP 和 intervals 的 compliance 色**：直接套在總覽本週課表與日曆上。
8. **Polar／Stryd 把「代謝負荷」和「機械負荷」並排**：訓練量分頁可以把 TSS 和下坡負荷（#1）畫成兩個並排的週長條。
9. **Runalyze 跑姿五色百分位**：單次活動的步頻、GCT、VO 直接加色帶。
10. **Garmin Readiness「分數加列出各輸入的狀態」**：總覽的「還缺什麼」卡片已經很接近，可以再加一行「是哪一項拖累」。

---

## 6. 沒讀到或未驗證的來源

| 來源 | 狀況 |
|---|---|
| help.trainingpeaks.com（Dashboard Charts、Performance Insights 等） | 全站 403，TP 部分改用 /learn、/blog、/changelog |
| https://medium.com/strava-engineering/an-improved-gap-model-8b07ae8886c3 | 403，Strava GAP 細節拿不到 |
| https://runalyze.com/help/article/race-prognosis | 404，預測模型的式子沒拿到，也沒確認有沒有 Riegel 選項 |
| itra.run FAQ / PerformanceIndex | 403，改用第三方引用 |
| support.coros.com Hill Alerts、Fitness Metrics | 403（部分經代理讀到） |
| Firstbeat 白皮書、Polar Training Load Pro 白皮書 | DNS 查不到，或 PDF 無法解析 |
| GC wiki UG_Special-Topics_WBAL | 空白頁（可能改名了），W′bal 改讀原始碼 |
| COROS Fatigue 的 5 區數字、Effort Pace、Stamina | 找不到公開來源 |
| Garmin Endurance / Hill Score 的數值、Load Focus 顏色 | 只有非官方 wiki |
| Strava Performance Predictions | 找不到文件 |
| intervals.icu Form zone 的前段門檻 | 除了 −30% 讀到論壇原文，其餘只見於搜尋摘要 |

---

## 7. 實作與驗證紀錄（2026-09-30，branch feat/competitor-charts）

做了 §4 的 #1、#3、#4、#5、#6、#7、#10；#8 沒上線（見 7.8）；#2、#9 由別的 branch 做。

每一項都有三種檢查，程式在 `backend/tests/test_chart_metrics.py`：
- **公式對來源**：`backend/engine/algorithms/chart_metrics.py` 用純 Python 照來源重寫一次，並用來源的算例（有的話）或手算例子驗證。
- **運算式對公式**：golden 測試用真實資料（2026-09-29 的 athlete），拿 `views/*.json` 裡的運算式（直接從 JSON 讀，改圖就會跟著測）跟上面的純 Python 在 2–3 個真實日期、週或活動上比對。
- **合理範圍**：對照文獻或常識的範圍。

§4 草稿的三個錯誤，實作時已修正：
1. `rgrade` 是比值（0.08 = 8%），不是百分比。草稿的 `rgrade < -8`、`rgrade > 5` 都差了 100 倍。
2. PI 在 Z2 = 0 時的 Eq. 2 是 `log10(Z1/0.01 × (Z3 − 0.01) × 100)`（Treff 2019 原文，並用 Table 1 的 80-0-20 → 3.18、72-0-28 → 3.29 驗證過）。§1.1 引 intervals.icu 論壇的 `log10(Z1/Z3 × 100)` 算不出 Table 1 的值，不採用。
3. Friel 的 Fresh 是 +5～+25、Transition 是 > +25（原文），§1.1 寫的 +20 是沒讀到原文的版本。

### 7.1 下坡衝擊負荷（#1）：參考（自訂指標）

- **來源**：沒有公開的標準公式（Stryd LBSS、Garmin Running Tolerance 都是專有）。每個係數出自下列文獻：
  - Gottschall & Kram 2005, *J Biomech* 38:445（PMID 15652542）：3 m/s、坡度 −9° 時，垂直衝擊峰值 +54%（煞車峰值 +73%）。
  - Keller et al. 1996, *Clin Biomech* 11:253（PMID 11415629）：垂直地面反作用力從 1.5 m/s 的 1.2 BW，線性增加到 6 m/s 約 2.5 BW。
- **公式**（每個下坡取樣點）：水平公里 × 坡度權重 × 速度權重。
  - 坡度權重 = 1 + 0.54 × min(|坡度| ÷ tan 9°, 1)
  - 速度權重 = Fz(v) ÷ Fz(3 m/s)，其中 Fz(v) = 1.2 + (1.3 ÷ 4.5) × (v − 1.5)，v 限制在 1.5–6 m/s
  - 1 單位 = 在平路用 3 m/s 跑 1 km。
  - 只算坡度陡於 −3% 且在移動的點。
  - 7:28 比 = 近 7 天每日平均 ÷ 近 28 天每日平均。
- **我們自己的假設**：
  - 0 到 −9° 之間用線性內插；比 −9° 陡的部分維持 +54%（沒有資料）。
  - 走路也用同一條坡度曲線（G&K 只量了跑步）。
  - Keller 說慢跑（jogging）的衝擊比線性預測高 50% 以上，這裡沒有納入。
  - −3% 的門檻是為了濾掉氣壓計和 GPS 的雜訊。
  - 沒有用 Stryd ILR：只有部分活動有，而且 Stryd 沒有公開怎麼從 ILR 算出負荷分數。
- **檢查**：
  - 單元測試：level 在 3 m/s 時 = 1、−9° 時 = 1.54、Keller 的兩個端點。最大權重 2.36，低於 Garmin 公開的「下坡最高 3 倍」。
  - golden 測試：在 2 次越野、1 次百岳上，運算式和純 Python 一致（rel 1e-4，差異來自常數四捨五入）。
  - 總覽卡的 7:28 比跟圖上最後一天的值一致。
- **結果**：越野單次 1.2–7.5 等效 km，42 km 的百岳 11.9（走路慢，權重小於 1）。
- **總覽卡 `descent`**：只會是 INFO 或 WATCH。比值 ≥ 1.5 且 7 天 ≥ 3 等效 km 時為 WATCH；1.5 借自 ACWR 的色帶，沒有驗證過。

### 7.2 Form% 色帶（#3）

- **來源**：
  - Friel, "Managing training using TSB"（joefrieltraining.com）：High risk < −30、Optimal −30～−10、Grey −10～+5、Fresh +5～+25、Transition > +25，都是絕對 TSB。
  - intervals.icu 開發者在論壇（/t/form-as-a-percentage-of-fitness/869、/t/zones-of-form-in-fitness-chart/3623）說明：Form% = (CTL − ATL) × 100 / CTL，並把 Friel 的數字直接當百分比用；他自己也說這是 rule of thumb。
- **公式**：`tsb / shift(ctl, 1)`。我們的 TSB 是昨天的 CTL − ATL，所以分母也用昨天的 CTL，兩邊同一天。
- **檢查**：golden 測試用獨立的 v += (x − v)/n 重算 PMC，3 天一致（rel 1e-6）。
- **結果**：過去一年 −169% 到 +75%，中位數 +7%；多日百岳後掉到圖外（y 軸固定 −60%～+40%）。

### 7.3 負荷比 ATL/CTL（#4）

- **來源**：
  - Runalyze A:C ratio 頁：< 0.8、0.8–1.3、1.3–1.5、> 1.5，引用 Gabbett 2016（*BJSM* 50:273）。
  - Garmin 的 Load Ratio 門檻不同（0.8 / 1.4 / 2.0）。
- **近似**：Gabbett 用的是 7:28 的滾動平均，我們用 7:42 的指數平滑，色帶是借用的，說明裡已經寫明。說明裡也寫了「不是受傷預測」（Impellizzeri 2020，見 CDM §3.2）。
- **檢查**：同 7.2 的獨立 PMC 重算，3 天一致。
- **結果**：過去一年 0.25–2.69，中位數 0.93。

### 7.4 路線難度（#5）

- **來源**：
  - コース定数（山本正嘉；山と溪谷社 yamakei-online.com/yama-ya/detail.php?id=363）：1.8 × 行動時間(h) + 0.3 × 距離(km) + 10.0 × 登り累積標高差(km) + 0.6 × 下り累積標高差(km)。~10 初心者、~20 一般、~30 日帰り健脚、40+ 要過夜。原文的行動時間是「参考コースタイム」。
  - ITRA km-effort = km + D+(m)/100；分級 XXS < 25、XS 25–44、S 45–74、M 75–114、L 115–154、XL 155–209、XXL ≥ 210。itra.run 要 JS 才讀得到，所以這裡依 trailia.run 的引用（例：42 km、2000 m → 62，屬 S）。
- **近似**：行動時間用實際移動時間代入，走得比標準時間快的人數字會偏低；說明裡已經寫明。
- **沒做**：ITRA 的 0–6 點數，找不到原文的對照表。
- **檢查**：
  - 手算例：6 h、12 km、1000 m 上下 = 25.0。
  - ITRA 算例和各分級邊界。
  - golden 測試：3 次活動的運算式和純 Python 一致。
- **結果**：越野單次 1–26，42 km 的百岳 55（「要過夜」，實際上就是兩天一夜）。

### 7.5 上坡／下坡腳程（#6）

- **定義**：
  - 坡度 ≥ +5%（或 ≤ −5%）且速度 > 0.5 km/h 的取樣點，Σ 海拔變化 ÷ Σ 時間 × 3600。
  - 上坡另外排除心率 ≥ LTHR 的點（HR 過濾）；沒心率的點保留。
  - 少於 10 分鐘的活動不畫。
- **來源**：這是直接測量，沒有公式需要驗證。坡度門檻和 HR 過濾都是我們定的。對照值用 Naismith（每 600 m 爬升加 1 小時）。
- **檢查**：golden 測試在 2 次越野、1 次百岳上跟 numpy 重算一致（rel 1e-6）；範圍檢查 150–1500 m/h（上坡）、200–2000 m/h（下坡）。
- **結果**：
  - 越野：上坡 305–826 m/h，下坡 449–1177 m/h。
  - 百岳：上坡 315–511 m/h，下坡 358–526 m/h。

### 7.6 Monotony 與 Strain（#7）

- **來源**：Foster 1998, *MSSE* 30:1164（PMID 9662690）摘要：monotony = daily mean / standard deviation，strain = load × monotony；門檻「individually identifiable」。
- **假設**：
  - 窗口是滾動 7 天。
  - 休息日算 0。
  - 用母體 SD（除以 7）。Foster 沒有寫是哪一種；Runalyze 新版公式的下限 0.29 = 1/(1+√6)，只有用母體 SD 才會出現。
  - 負荷用 TSS（原文是 session-RPE × 分鐘）。
  - 因為原文說門檻因人而異，圖上沒有畫門檻線。§1.4 引的 Runalyze 舊門檻 1.5 / 2.0 這次沒有在原頁讀到，也不畫。
- **運算式**：用 cumsum 差分算滾動 7 天的和與平方和，不必寫 7 個 shift，也沒改 evaluator。
- **檢查**：
  - 手算例：[100, 0, 50, 0, 100, 0, 50] 與單日 1/√6。
  - golden 測試：3 天和獨立的每日 TSS 重算一致（rel 1e-6）。
- **結果**：單調度 0.41–2.44（中位數 1.10），strain 9–484。

### 7.7 Polarization Index（#10）

- **來源**：Treff et al. 2019, *Front Physiol* 10:707。
  - Eq. 1：log10(Z1/Z2 × Z3 × 100)，Z 是占比小數。
  - Eq. 2（Z2 = 0）：log10(Z1/0.01 × (Z3 − 0.01) × 100)。
  - Z3 = 0 時 PI = 0；Z3 > Z1 時 PI 不成立。
  - PI > 2.00 為極化。
- **我們的補充**：
  - Z2 = 0 且 Z3 ≤ 0.01 時 Eq. 2 沒有定義，當成 0。
  - 當週有心率的時間 < 1 h 不算。
  - 三區用 AeT／LTHR，不是 Treff 的 VT1／VT2。
- **檢查**：
  - Table 1 的 6 組例子全部重現到小數第 2 位。
  - golden 測試：用原始心率取樣重算近 11 週，跟運算式一致（abs 1e-6）。
- **結果**：近一年有 32 週可算，0.96–1.87，都是非極化，比較接近金字塔型。其餘週 Z3 > Z1，依定義留白。

### 7.8 上坡／下坡效率比（#8）：沒上線

- Runalyze 沒有公開公式，所以只能自己定義。試過的做法：
  - Minetti 2002 的等效平路速度 ÷ 心率儲備，這一步依 %HRR ≈ %VO2R，用 WKO5 的 `pulse` 當靜息心率。
  - 再拿坡上的值除以平路的值。
- 5 次真實活動的結果：上坡 1.24–1.86，下坡 0.60–0.78。Runalyze 說預期值約 1.0，我們的數字系統性偏離。
- 研判原因：
  - 山路平段本身就慢（地形，不是代謝）。
  - 短坡心率有延遲。
- 這些混淆沒辦法用現有資料驗證或排除，依「不能驗證就不上線」的規則，沒有加進圖表。
