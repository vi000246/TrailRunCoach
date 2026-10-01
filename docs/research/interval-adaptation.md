# 間歇適應：怎麼判斷練起來了、下一堂怎麼排

> 狀態：研究＋設計提案（2026-10-01），尚未實作。
> 範圍：間歇適應的指標、什麼時候該進階／什麼時候是「還沒準備好」、組休與每趟時間怎麼定，以及 app 怎麼自動排間歇、自動檢討每一堂。
> 標記：每個門檻都標來源；**自組** = 我們自己定的；**未找到來源** = 找過但沒找到；**未驗證** = 有引用但沒讀到原文。

---

## 0. 結論先講

1. **「休息 60 秒內心率掉回有氧閾值＝適應了，可以加趟」只有徐國峰一個來源，而且他的有氧閾值指的是 70% 儲備心率（HRR），不是 app 實測的 AeT。** 文獻裡沒有這條規則。心率恢復在研究裡是很雜訊的指標，而且功能性過量訓練時反而會變快（§3.1）。所以它只能當**輔助訊號**，要跟功率達標一起看，不能單獨拿來決定加趟。
2. **WKO5 有的是「目標」演算法，不是「進階」演算法。**
   - Optimized Intervals／iLevels 用功率–時間模型算出每一級的**每趟時間＋功率**。
   - 組數、組休比、區間總時間（TIZ）是教練寫死的建議值（Cusick 和 Golich 定的）。
   - dFRC 講者明講「不要拿來開課表」。
   - 心率恢復「有參考，但不是主要依據」。
   - WKO5 的公式裡沒有任何心率恢復或間歇進階的函式（§2）。
3. **app 現在的「最後一組比第一組低 5% → 退一步」，跟它自己引用的 WKO 講者說法相反。** 三場研討會都明講不贊成「掉 X% 就收工」。Golich 的判斷是看**在第幾組掉出目標**：最後一組掉沒關係，第 2–3 組就掉代表課表開錯了。建議改掉（§4.3）。
4. **進階的順序：**
   - 先加組數或區間總時間，加到上限（短間歇約 8 組，VO2max 總量 12–20 分）。
   - 再加長每趟時間，或縮短組休。
   - 最後才加功率。
   - 平日 40–50 分鐘的上限會擋住「加組」，所以要先把上限算進去再選哪個維度（§4.1）。
5. **資料缺口：**
   - COROS 的 FIT lap 沒有 `wkt_step_index`、`intensity`、`lap_trigger`（已查最近 12 個檔）。
   - 推送的結構化課表會不會把每個 step 切成一個 lap，還沒驗證。
   - 也沒有靜息心率，所以算不出 HRR 70%（§4.4）。

---

## 1. 你的筆記怎麼說

### 1.1 徐國峰的 60 秒規則

- `60 🏃 有氧訓練/跑力提升 影片筆記/間歇運動.md:4`：「如果在間歇完成後的休息，60秒內能回到有氧閾值，就代表適應了」
- `…/間歇運動.md:5`：「如果適應了就能增加趟數」
- `…/間歇運動.md:6`：「趟數的上限是單次的TSS不能超過xxx, 這個就要看WKO5了」
- 同一則筆記附的影片截圖 `…/assets/間歇運動-20251114124454811.jpg`：
  - 影片：「6.1.2 常見跑步課表的訓練要領｜間歇跑(Interval)」，13:53。
  - 投影片寫：「關鍵指標：恢復秒數」「看每一趟間歇結束後，心率從高點 → 掉到有氧閾值（HRR 70%）所需的時間長短」。
  - **投影片上沒有「60 秒」**。60 秒是你從口述記下來的。
  - **投影片上的有氧閾值＝70% 儲備心率**：靜息心率＋0.7×（最大心率−靜息心率）。

徐國峰另外的門檻是**開始練間歇**的時機，不是適應：
- `70 ⏳ 周期化訓練/400 爆發力、敏捷性、專項耐力周期.md:5`：「E配速90分鐘的心率飄移%…確定在10%以下之後就可以開始練間歇」
- app 已經實作成 `xu_drift`。

### 1.2 其他教練的心率規則（都是教練說法）

- 鐵人J帥（`60 🏃 有氧訓練/間歇訓練.md:127-134`）：
  - 「下一組比前一組出現心率會很快的升進入LT2」
  - 「速度差很多甚至想跑走，那要嘛就是速度設得太快了，就是組休時間不夠了」
  - 新手「還要降到最大心率的60%以下」才開始下一組。
- 運動訓練法筆記（`間歇訓練.md:8`）：「休息時間可以設為恢復到65%…最大心跳率所需的時間」。

### 1.3 組休比與每趟時間（你自己的課表模板）

- `65 ⚡ 功率訓練/間歇通用模板整理 --star.md:45-56`：
  - VO2max：3–8 組，組休 1:1～1:2，每趟 2:30–5 分。
  - Supra Threshold：每趟 4:30–5 分，2–6 組。
  - Near Threshold：1–4 組，約 10 分。
  - Power Tempo：20–30 分，組休 3 分。
- Daniels：
  - I 強度每趟 3–5 分（`間歇訓練.md:36`）。
  - R 強度工休比 1:2～1:3（`丹尼爾的跑步方程式筆記 還有課表.md:115`）。
- Palladino 的跑步工休比（`功率區間說明與訓練目的 --star.md`）：
  - Near-threshold 3:1～5:1（:54）。
  - Supra-threshold 2:1（:102）。
  - MAP 1:1，進階 2:1（:142-143）。
  - 三種都靠「延長每組時間／縮短恢復」進步（:61-63、:110-112、:151-153）。
- 密度原則：`間歇訓練設計指南.md:3-32`（1:2 以上練爆發、1:1 練 VO2max、2:1 練有氧容量與抗疲勞）。這份是 GPT 整理的，不是一手來源。

---

## 2. WKO5 實際做了什麼

### 2.1 有算的：每趟的時間和功率（Optimized Intervals）

- 反組譯（`docs/wko5-internals/formulas.md` §6.9b）：`targetduration(level)` 和 `targetpower(level)` 從 mFTP、FRC、TTE、τ1 算出六級的目標。

| 級 | 每趟時間 | 功率 |
|---|---|---|
| 0 Extensive Aerobic | TTE | FTP |
| 1 Intensive Aerobic | 0.9625·FRC/(0.0375·FTP) | 1.02·FTP |
| 2 Max Aerobic | 0.1625·FRC/(0.032·FTP) | 1.20·FTP |
| 3–5 Anaerobic / Max | 5·ln2·τ1、3·ln2·τ1、ln2·τ1 | 曲線上該時間的功率 |

- iLevels（§6.9）：FTP 以上每一級都附「建議時間範圍」，邊界是能量系統的交叉點（研討會 ROLE:444-446）。
- **組數、組休比、總時間不是模型算的：**
  - ROLE:398：「這些時間與組數…是我和 Dean Golich 一起設定的建議值，像是休息比例、目標總時數、建議組數」。
  - 例子：VO2max 3–8 組、總時間 12–30 分、組休 1:1（BI:331、BI:360-361、BI:370）；Golich 版 1:1 或 1:2（AP1:53-54）。
  - DFRC:684-685：「它不會告訴你：『你可以做幾組』」。
- 允許下修：ROLE:499「下修 5～10%」、ROLE:555「乘上 95%」。BI:516-519 重複功率預設 0.95，個人 85–95%。

### 2.2 進階規則（研討會的教練說法，不是演算法）

- **先加組數或總時間：**
  - BI:354、BI:357-358：「當他能穩定做到，下一步就要漸進增加組數，或是總時間」。
  - ROLE:509-527 的流程：3 分×5 組 → 覺得輕鬆就 6 組 → 維持幾次 → 3 分鐘的不超過 8 組 → 改成 4 分×4 組。判斷依據是主觀感受。
- **閾值以下先延長時間：** FR:214-218「先延長時間，再談加功率」，例如 2×20 → 1×45。
- **VO2max 總量固定，進步靠每組更用力：**
  - FR:356。
  - CW:309-313：累積 12–15 分就有效，超過 15 分「通常就是在硬撐」。
  - 你的註記（FR:232）：跑步大約 11 分。
- **大部分進步在前面：** CW:160、CW:169 說前 6–8 次課表就拿到約 90% 的進步；CW:264 說適應在 4–8 週。
- **反對「功率掉 X% 就收工」：**
  - BI:639-644：「第二組間歇下降 5% 就收工…我不會這麼做」。
  - FR:234-235：不贊成「掉4%以上就回家」。
  - IT2:340（Tim Cusick）。
- **什麼時候功率下降才算數（Golich，FRC 課）：**
  - IT2:84-86：「如果 350 瓦是出現在最後一個間歇，沒關係；但如果是第二或第三個，那就代表整體安排出了問題」。
  - IT2:233-235：「恢復時間不夠長，或者強度設得太高」。
- **心率恢復只是輔助：**
  - BI:776-784：「會，但不是主要依據…看間歇後心率下降的速度…核心依據還是功率」。
  - Palladino（PAL:699）：「心率最多只是冗餘，最糟還可能誤導」。

### 2.3 dFRC 和組休

- 引擎已實作 WKO5 的 `dfrc`（`evaluator._dfrc`）：
  - 高於 FTP 時線性消耗。
  - 恢復是雙指數：30% 用 τ = 25 s、70% 用 τ = 300 s。
  - **還沒對 WKO5 的實際輸出驗證過**（`functions.md:507`）。
- 講者的態度：
  - DFRC:168、DFRC:176-177：不拿 dFRC 開課表。
  - DFRC:424-425：還沒準到能決定「下一組該什麼時候做」。
- 拿來事後檢討可以：
  - DFRC:338：8 分鐘 VO2max 間歇至少消耗一半到三分之二。
  - DFRC:560：無氧課要「用到接近 0」。
  - DFRC:793-797：SST 掉很多＝做太用力。
- WKO5 Cloud Library 社群圖表（**不是內建演算法**）：
  - 「dFRC recovery」（Steve Bateman）：恢復時間＝dFRC 最低點到回升 ≥ `@RecoveredPct:=90` % FRC 的秒數。
  - 「Skiba Estimates of Recovery of FRC or W′」（William Renfroe）：比較 Skiba 1/2 和 dFRC。
  - 「Auto Marking VO2max Intervals」（William Renfroe）：自動標出 ≥ 約 2 分、≥ 75% VO2max 的趟。

### 2.4 找過但沒有的

**未找到來源：**
- WKO5 Expression Reference 裡沒有心率恢復、lap 或「間歇重複性」的函式。
- 研討會筆記裡沒有任何「心率 X 秒內降到 Y」的數字。
- 沒有任何依 dFRC 設定組休長度的規則。
- 沒有 WKO5 自己算出來的組數。

---

## 3. 文獻

這輪 WebSearch 額度用完，下面全部從 Europe PMC 抓摘要（標「摘要」）。原文都沒讀，只有 Skiba 的 τ 公式從 Raimundo 2022 的全文核對過。

### 3.1 心率恢復（HRR）是適應的指標還是疲勞的指標

**兩者都是：適應和功能性過量訓練（f-OR）都會讓 HRR 變快。**

| 研究 | 發現 | 讀到哪裡 |
|---|---|---|
| Bellenger 2016 統合分析 | 正向適應時 HRR 上升 SMD 0.63；過量訓練時也上升 SMD 0.46。作者說 HRR 的上升 "also occur in response to overreaching"。比較能分辨兩者的是 HR acceleration（運動開始時心率上升的速度）。 | 摘要 |
| Aubry 2015 | f-OR 組 HRR 快了 8±5 bpm，而且 HRR 的變化和表現的變化呈**負相關**。 | 摘要 |
| Le Meur 2017 | f-OR 組在 11 km/h 時 HRR 快 16±7 bpm，對照組 3±5 bpm。 | 摘要 |
| Siegl 2017、Mann 2015 | 超馬後 HRR 變快，但同時 RPE 變高。 | 摘要 |
| Mann 2014 | 前一段強度越高，HRR60 越快。前一段的強度本身就會干擾 HRR。 | 摘要 |
| Lamberts 2010、Capostagno 2021 | 4 週 HIIT 期間 HRR 上升的那一組，表現進步得比較多。 | 摘要 |
| Daanen 2012 回顧 | "HRR was related to training status"，但量測需要標準化。 | 摘要 |
| Buchheit 2014 | 主要靠靜息心率和次最大運動心率，再搭配訓練日誌、問卷和表現測試。 | 摘要 |

- HRR 的「最小有意義變化」（幾 bpm 才算有變）：**未找到來源**。

**「60 秒內掉回 AeT／70% HRR 就能加趟」：**
- 同儕審查文獻裡**未找到來源**。
- 中文網路來源因為搜尋額度用完沒查到，等額度恢復再查一次。
- 經典的 Gerschler／Reindell「休到心率 120 下一趟」：**未驗證**。

**對設計的意思：**
- 心率掉得快不能證明適應了（Bellenger、Aubry）。
- 心率掉得慢可能只是前一趟比較用力（Mann 2014）。
- 所以 60 秒規則只能當**煞車**：功率都達標、但心率沒回到 AeT → 先不加趟。它不能當**油門**：心率回得快 → 就加。

### 3.2 組休長度與方式

| 研究 | 發現 | 讀到哪裡 |
|---|---|---|
| Seiler & Hetlelid 2005 | 訓練有素的跑者做 6×4 分，組休 1／2／4 分。組休 2 分時配速最好、VO2 最高（66.2 vs 65.1／64.9 mL/kg/min）。自選組休平均 118±23 秒。結論：4 分鐘的趟，約 2 分鐘主動恢復最好。 | 摘要 |
| Seiler & Sjursen 2004 | 1 分鐘的趟 VO2 只到 82±5%；2–6 分鐘的趟到 92±4%。自選強度時，每趟 3–5 分最好。 | 摘要 |
| Dupont 2004、Thevenet 2007、Tardieu-Berger 2004、Ben Abderrahman 2013 | 被動恢復能撐的時間約是主動恢復的 2 倍。在 ≥ 90% VO2max 的絕對時間差不多。 | 摘要 |
| Thevenet 2008 | 主動恢復強度在最大有氧速度的 50% 或 67% 時，≥ 90% VO2max 的時間差不多；84% 時明顯變少。 | 摘要 |
| Buchheit & Laursen 2013 I/II | 可以調的變數有 9 個：強度、時間、恢復強度、恢復時間、模式、組數、組別、組間恢復的時間和強度。目標是每堂累積「數分鐘」在 ≥ 90% VO2max。具體的工休比建議沒讀到全文，**未驗證**。 | 摘要 |
| Billat 2000 | 30-30（100%／50% vVO2max）累積 VO2max 時間 7′51″，持續跑只有 2′42″。 | 摘要 |
| Rønnestad 2015、2020 | 30/15 短間歇（3 組×13 趟）提升 VO2max／最大有氧功率，比 4×5 分多。 | 摘要 |
| Seiler 2013 | 4×8 分的 VO2peak 進步（11.4%）比 4×4 和 4×16 多。 | 摘要 |

### 3.3 W′ 恢復（組休長度的上限邏輯）

| 研究 | 發現 | 讀到哪裡 |
|---|---|---|
| Skiba 2012 | τ = 546·e^(−0.01·D_CP) + 316 s。只在騎車驗證過。 | Raimundo 2022 全文引用 |
| Ferguson 2010 | W′ 在 2／6／15 分鐘後分別恢復 37／65／86%。 | 摘要 |
| Caen 2019 | 2／4／6 分鐘後恢復 46／51／59%。在 33% CP 恢復比 66% CP 多 9.4%。 | 摘要 |
| Caen 2021、Chorley 2022 | 雙指數：快成分 τ ≈ 11–22 s，慢成分 τ ≈ 256–388 s。WKO5 dFRC 的 25 s／300 s 落在這個範圍。 | 摘要 |
| Bartram 2018 | 菁英車手的實際恢復比 Skiba 預測的快。 | 摘要 |
| Black 2023 | 微分式的 W′bal 會高估恢復（9.8 vs 6.3 kJ）。 | 摘要 |

**結論：**
- 1–2 分鐘的組休只回來一半左右。
- 所以 W′bal 只用來**事後檢討**這堂到底挖得多深，不拿來決定組休。這和 WKO 講者的態度一樣（DFRC:424-425）。

### 3.4 課中疲勞訊號

- **掉速：**
  - Glaister 2008：percent decrement score 是最可靠的掉速算法。
  - Girard 2011：掉速和第一趟的速度成反比。
  - 「掉 > X% 就算疲勞」的門檻：**未找到來源**。
- **RPE：**
  - Foster 2001：session RPE 是有效的負荷指標。
  - Seiler & Sjursen 2004：配速得當的課，RPE 在課中本來就會爬到約 17。
- **HRV：** Kiviniemi 2007、Vesterinen 2016：HRV 在個人正常範圍內才排高強度，結果比固定課表好。app 沒有 HRV 資料。
- **心率逐趟爬升（cardiac drift）：** 門檻**未找到來源**（只有鐵人J帥的教練說法）。

### 3.5 越野：上坡間歇

- Barnes 2013：6 週、5 種強度的上坡間歇，5 km 都進步約 2%；強度最高的那組跑步經濟性最好。
- Ferley 2016：短的上坡趟（30 秒）在乳酸閾和力竭測試的進步，比長的（約 3 分）多。
- Ferley 2013：平路和上坡都進步，摘要說平路進步較多。
- Ferley & Vukovich 2015：10% 坡跑約 68% Vmax，力竭時間等於平路 Vmax。
- Uphill Athlete（教練來源）：有氧不足時加強度會讓進步變慢甚至倒退。這部分 app 已經用 `ua_gap` 處理；它沒有談組間恢復。

---

## 4. App 設計提案

### 4.0 現況

- `quality_gate.py` 的劑量表 `DOSE`／`AFTER` 是固定階梯：
  - 5×1′ → 6×1′ → 4×3′（上坡）→ 5×3′ → 4×4′，之後 3×8′／4×8′ 交替。
  - 用 8 週內做了幾次（`dose_history`）推進一步。
  - 最後一堂 `fade < −5%` 就退一步（`dose_step`）。
- 專項期只有固定的「爬坡間歇 5×4 分」（`overview.py:606`），沒有進階。
- `workout_review.detect_efforts` 已經算出這些欄位：
  - 每趟的功率、%CP、平均／最高心率。
  - `hr_drop60`：峰值減掉結束後第 60 秒的心率。條件是下一趟在 60 秒後才開始，所以組休 < 60 秒時沒有這個值。
- `count_reps`（10 秒功率 ≥ 95% CP、≥ 40 秒）和 `detect_efforts`（30 秒功率、≥ 60 秒）是兩套偵測，門檻不同。
- `plan_prefs.trim_quality` 遇到平日上限時，會先砍暖身和緩和，然後**減一組**，等於默默把「加組」的進階吃掉。

### 4.1 (a) 開課表：下一堂怎麼排

**課表的資料結構：**
- 把固定的 `DOSE` tuple 改成可調的 `IntervalSpec(type, reps, work_s, rest_s, rest_mode, lo, hi, uphill)`。
- 劑量表只當**起點**；之後的每一堂由 §4.3 的狀態機從上一堂的規格推出來。

**類型跟著周期走：**

| 周期 | 怎麼排 | 來源 |
|---|---|---|
| 基礎期 | 照現有的劑量表與門檻 | aerobic-base-readiness.md §4.5 |
| 專項期 | 把固定的 5×4 改成爬坡 supra／VO2max 階梯：3 分 → 4 分 → 5 分，105–110% CP | Palladino ZONES:89-90、142；Barnes 2013 |
| 減量期 | 強度不變、組數減少 | CW:562；Bosquet（現有） |

- 同一種類型連續 6–8 堂或 4–8 週以後換類型（CW:160、CW:264、AP1:380）。這個換法屬自組。

**每趟時間：**
- 用劑量表的值。
- 另外用 WKO5 的 `targetduration(2)` = 0.1625·W′/(0.032·CP) 交叉檢查。例：W′ 13.1 kJ、CP 220 W → 302 秒，約 5 分。
- `targetpower(2)` = 1.20·FTP 是騎車的設定，跑步不套用（BI:251-252）。跑步強度用 Palladino 的 %CP 帶。

**組休：**

| 類型 | 組休 | 來源 |
|---|---|---|
| 4 分鐘的 VO2max 趟 | 2 分 | Seiler & Hetlelid 2005 |
| 3 分以下 | 1:1 | Palladino MAP；BI:630 |
| 1 分鐘 | 1:2 | 你的模板；Palladino 早期 fartlek |
| 閾值下 | 3:1～4:1 | Palladino ZONES:54 |

- 恢復方式寫進 detail：
  - VO2max 用走或極慢跑。被動恢復能多做幾趟，VO2max 時間不會少（Dupont 2004、Thevenet 2007）；你的筆記 JS:24 也是這樣。
  - 閾值下用慢跑。
  - 恢復強度不要超過約 2/3 最大有氧速度（Thevenet 2008）。

**平日時間上限（`cap_weekday`，選填）：**
- 進階之前先算這一堂要幾分鐘：`15 + reps×(work+rest) + 10`。
- 加一組會超過上限 → 改走 §4.3 的第二維度：拉長每趟（總時間不變、組數減少，BI:396-397 允許），或縮短組休。
- `trim_quality` 不能再減到比上一堂完成的組數還少。
- 沒設上限就不受影響。

### 4.2 (b) 檢討：從 FIT 判斷每一趟

**找出每一趟：**
1. **有推送的課表 → 用 FIT 的 lap 對到計畫的 step。** 用時長比對，誤差 ±5 秒內（自組）。
   - 2026-09-30 的 CP 測試 lap 正好是 180.0 s 和 720.0 s，休息 lap（989 s）也有記錄。
   - 但那是手動按 lap 的，推送的課表會不會自動切 lap 還**未驗證**（見 §4.5 S0）。
   - lap 對應用 `racepower/cptest._read` 的同一套讀法。
2. **對不到 → 用功率型態偵測。** 把 `count_reps` 和 `detect_efforts` 合成一支 `find_reps(t, power, plan)`：
   - 平滑窗：每趟 < 90 秒用 10 秒，否則 30 秒。
   - 門檻：`0.95 × 計畫下限`。
   - 最短：`0.67 × 計畫的每趟時間`。
   - 這三個數字都屬自組。
   - 沒有計畫時，退回現在的 `detect_efforts`。

**每一趟的指標：**

| 欄位 | 定義 | 來源 |
|---|---|---|
| `power`、`pct_target` | 整趟平均，以及對計畫帶中點的比例 | — |
| `in_band` | `power ≥ 計畫下限 × 0.98` | 自組（容許 2%，接近 ROLE:555 的 95% 下修） |
| `hr_end` | 結束前 5 秒的平均心率 | 自組 |
| `hr_peak` | 結束前 10 秒到結束後 15 秒的最高心率 | 現有 `detect_efforts` |
| `hr_at60`、`drop60` | 結束後第 60 秒的心率，以及 `hr_peak − hr_at60` | 現有；組休 < 60 秒時沒有值 |
| `t_to_aet` | 組休內第一次 ≤ AeT 的秒數 | 徐國峰 |
| `aet60` | `t_to_aet ≤ 60` | 徐國峰的門檻；**AeT 用 app 的實測 AeT 代替 HRR 70%**，有靜息心率時改用 HRR 70% |
| `wbal_min` | 每趟結束時 dFRC 的最低點 | WKO5 `dfrc`（`_dfrc`），只顯示 |
| `hr_creep` | 每一趟 `hr_peak` 比第一趟高多少（同功率） | 只顯示，**未找到來源**門檻 |

**整堂的指標：**
- `fade`：最後一趟比第一趟，沿用現有欄位，但不再單獨用來判斷。
- `first_miss`：第一個不在帶內的是第幾趟。
- `done`：完成趟數 ÷ 計畫趟數。
- `aet60_share`：符合 `aet60` 的趟數比例。
- `rpe`：選填，Foster 2001 的 session RPE 0–10，要在 UI 加輸入。

### 4.3 (c) 進階狀態機

每一堂分成三類。只有**達標**才往前；判斷依據功率為主，心率只當煞車。

| 結果 | 條件 | 下一堂 | 來源 |
|---|---|---|---|
| **達標** | `done = 1`；所有趟都 `in_band`，或只有最後一趟沒在帶內；有心率時 `aet60_share ≥ 0.5`；RPE ≤ 7（沒填就略過） | 進階一個維度（見下） | BI:357-358；IT2:84-86；徐國峰；0.5 和 7 屬自組 |
| **邊界** | 功率達標，但 `aet60_share < 0.5`；或 RPE ≥ 8；或 `first_miss` 是最後一趟、而且掉 > 5% | 同一份課表再做一次 | ROLE:517「維持六組再做幾次」；徐國峰（煞車）；> 5% 屬自組 |
| **未適應** | `done < 1`；或 `first_miss` 落在第 2 趟到倒數第 2 趟 | 先保持組數、組休多 1 分鐘。下一堂還是未適應，就退回劑量表上一步 | IT2:84-86、IT2:233-235；FR:238 |

**疲勞保險（f-OR）：**
- 情況：`drop60` 比 8 週中位數快，**但**功率沒達標或 RPE 偏高。
- 處理：當作未適應，並在 status 提示「可能累積疲勞」。
- 來源：Aubry 2015、Bellenger 2016。觸發條件屬自組。

**進階的維度順序（達標時）：**
1. **加組數或總時間，加到上限：**
   - 1 分鐘的趟最多 8 組，3 分鐘的最多 8 組（ROLE:521）。
   - VO2max 總時間上限 16 分。這是取 CW:309-313「12–15 分」和 FR:231「15–20 分」的中間，屬自組；你的筆記說跑步大約 11 分（FR:232）。
   - 閾值下總時間上限是 TTE 的 150%（TTE:521）。
2. **拉長每趟時間（總時間不變），或縮短組休 30 秒：**
   - 拉長每趟：ROLE:527 的 3 分×5 → 4 分×4。
   - 縮短組休：Palladino ZONES:61-63；TTE:464-467。
   - 組休的下限：VO2max 1:0.5，閾值下 4:1。屬自組。
3. **加功率 +2%：**
   - FR:356：VO2max「每一組更用力」。2% 屬自組。
   - 新的 CP 測試套用後，目標功率自動跟著變（`session()` 本來就用當週的 CP 算）。
- 平日上限擋住第 1 步時，直接跳到第 2 步（§4.1）。
- 恢復週或護欄的 `hold` 照現有規則：不往上加。

**現在的規則要改的地方：**
- `dose_step` 改成「達標才加一步」。現在是每做一次就推進。
- `fade < −5%` 改成上面的 `first_miss` 判斷。
- `interval_lines` 的「休息 60 秒心率降幅 < 20 → 休息拉長」（`HR_DROP_MIN = 20`；`done-workout-review.plan.md:87` 沒標來源，本次也**未找到來源**），改成顯示 `aet60` 與 `t_to_aet`。

### 4.4 (d) 有的資料 vs 缺的資料

| 項目 | 狀態 |
|---|---|
| 1 秒功率、心率（COROS／Stryd） | 有 |
| CP、W′（3′/12′ 測試）、mFTP／TTE（WKO5 模型） | 有 |
| 實測 AeT（plan threshold 列）、LTHR | 有；LTHR 可能還是 WKO5 預設，`lthr_default` |
| FIT lap（時長、平均功率／心率、最高／最低心率） | 有：COROS 每 1 km 自動 lap；手動 lap 會留下休息 lap |
| FIT lap 的 `wkt_step_index`、`intensity`、`lap_trigger` | **沒有**（最近 12 個 COROS 檔都沒這些欄位） |
| 推送的結構化課表會不會在 FIT 裡把每個 step 切成 lap | **未驗證**：最近 12 個檔裡沒有推送的間歇課 |
| FIT 裡的 `workout`／`workout_step` 訊息 | **未驗證**：`fit_reader` 沒讀這兩種訊息 |
| 活動對應到計畫的哪一堂 | 有一部分：`_done_by_this`／`scheduled_test` 只處理測試課 |
| 靜息心率（算 HRR 70%） | **缺**：只有 racepower 估的 HRmax |
| session RPE | **缺** |
| HRV | **缺** |

### 4.5 (e) 實作計畫（每一步都可以單獨合併）

| 步驟 | 做什麼 | 檔案 | 測試 |
|---|---|---|---|
| **S0** | 不寫程式。推送一堂 5×1′ 到 COROS，跑完後抓 FIT，用 scratchpad 的 lap dump 確認：每個 step 是否一個 lap、有沒有 `wkt_step_index`、休息 lap 是否存在。後面 S2 怎麼做看這一步的結果。 | — | — |
| **S1** | 每一趟的指標：`detect_efforts` 加 `hr_end`、`hr_at60`、`t_to_aet`、`aet60`、`wbal_min`（呼叫 `_dfrc`）；`interval_summary` 加 `first_miss`、`aet60_share`；`CACHE_KEY` 升到 v9 | `engine/workout_review.py` | `test_workout_review.py`：用 `fit_builder` 合成的 5×1′ 串流，心率在第 40／70 秒掉到 AeT，檢查 `aet60`；組休 45 秒時沒有 `hr_at60` |
| **S2** | 找出每一趟：新檔 `find_reps(t, power, plan)`，以及 `match_laps(plan_steps, laps)`；把活動連到計畫的品質課（plan_store 的 uid） | 新增 `engine/interval_reps.py`；`files/fit_reader.py`（讀 lap 欄位，有的話再讀 `workout_step`） | 新增 `test_interval_reps.py`：lap 完全對得上、少一趟、沒有 lap 時的功率偵測、1 分鐘的趟 |
| **S3** | 判斷結果：`interval_outcome(reps, plan, aet, rpe)` → 達標／邊界／未適應；`dose_history` 改存 `outcome`；`dose_step` 改成達標才 +1 | `engine/quality_gate.py` | `test_quality_gate.py`：每一列（只有最後一趟掉、第 2 趟掉、心率煞車、f-OR 保險） |
| **S4** | 可調的課表規格：`IntervalSpec` 和 `next_spec(prev, outcome, cap)`；`session()` 由規格產生 title／detail（COROS 的 `_quality_steps` 仍能解析）；`trim_quality` 不減到比上一堂完成的組數少 | `quality_gate.py`、`plan_prefs.py`、`sync/coros_workouts.py` | `test_quality_gate.py`、`test_plan_prefs.py`（上限 45 分時走第 2 維度）、`test_coros_workouts.py`（30 秒組休、走路恢復） |
| **S5** | 專項期爬坡階梯、減量期規則、projection 的預測週 | `overview.py`、`projection.py` | `test_overview.py`、`test_planning.py` |
| **S6** | UI：間歇卡加 `t_to_aet`、`aet60`、`wbal_min` 欄位與結論列；session RPE 輸入；選填的靜息心率設定（有了就改用 HRR 70%） | `workout_review._intervals`、static、`settings/repository.py` | `test_panels_workout.py` |

規格同步：S1–S4 合併後跑 `/prp-spec`，更新 `docs/spec/workout-review.spec.md`，並在 `aerobic-base-readiness.md` §4.5 加一行指向這份文件。

---

## 來源

### 你的筆記（`C:\Users\<user>\Projects\notes\notes\300 Sport\`）

**徐國峰與其他教練的心率規則：**
- `60 🏃 有氧訓練/跑力提升 影片筆記/間歇運動.md:4-7` 和截圖 `assets/間歇運動-20251114124454811.jpg`（徐國峰）
- `70 ⏳ 周期化訓練/400 爆發力、敏捷性、專項耐力周期.md:5-8`（徐國峰）
- `60 🏃 有氧訓練/間歇訓練.md:8, 36, 113-134`（Daniels、鐵人J帥）

**組休比與每趟時間：**
- `60 🏃 有氧訓練/間歇訓練設計指南.md:3-32`（GPT 整理）
- `65 ⚡ 功率訓練/間歇通用模板整理 --star.md:40-56`
- `60 🏃 有氧訓練/丹尼爾的跑步方程式筆記 還有課表.md:98, 115`
- `65 ⚡ 功率訓練/功率區間說明與訓練目的 --star.md`（ZONES）
- `65 ⚡ 功率訓練/鐵人J帥文章.md`（JS）

**WKO 研討會（`65 ⚡ 功率訓練/研討會整理/`）：**
- 研討會 Building intervals th WKO5 way（BI）
- Role and Purpose of iLevels and Optimized Intervals（ROLE）
- Interval Training with WKO4 Part 2（IT2，Golich）
- Analyzing Interval Training Part 1（AP1，Golich）
- Coaching with WKO4 Art and Science（CW）
- dFRC by wko5（DFRC）
- Fatigue Resistance Strategy（FR）
- Advanced Running with Power（PAL）
- 另外：`70 ⏳ 周期化訓練/研討會Building FTP, TTE, and Stamina with WKO5.md`（TTE）

### WKO5

- `docs/wko5-internals/formulas.md` §6.9、§6.9b（反組譯）
- `docs/wko5-internals/functions.md` §5 dfrc
- `%LOCALAPPDATA%\WKO4\Documentation\WKO5 Expression Reference.html`（targetduration／targetpower／targetname）
- Cloud Library 社群圖表：
  - 「dFRC recovery」（Steve Bateman）
  - 「Skiba Estimates of Recovery of FRC or W」（William Renfroe）
  - 「Auto Marking VO2max Intervals」（William Renfroe）
  - 「HIIT Validation iLevels」

### 文獻（Europe PMC 擷取；DOI 都是實際抓到的）

**心率恢復與過量訓練：**
- Aubry A, et al. 2015. PLoS One. doi:10.1371/journal.pone.0139754
- Bellenger CR, et al. 2016. Sports Med. doi:10.1007/s40279-016-0484-2
- Buchheit M. 2014. Front Physiol. doi:10.3389/fphys.2014.00073
- Capostagno B, Lambert MI, Lamberts RP. 2021. JSCR. doi:10.1519/jsc.0000000000003227
- Daanen HA, et al. 2012. IJSPP. doi:10.1123/ijspp.7.3.251
- Lamberts RP, et al. 2010. Scand J Med Sci Sports. doi:10.1111/j.1600-0838.2009.00977.x
- Le Meur Y, et al. 2017. IJSPP. doi:10.1123/ijspp.2015-0675
- Mann TN, et al. 2014. EJAP. doi:10.1007/s00421-014-2907-9
- Mann TN, et al. 2015. JSCR. doi:10.1519/jsc.0000000000001004
- Siegl A, et al. 2017. IJSM. doi:10.1055/s-0043-110226

**間歇的設計、組休與恢復方式：**
- Ben Abderrahman A, et al. 2013. EJAP. doi:10.1007/s00421-012-2556-9
- Billat VL, et al. 2000. EJAP. doi:10.1007/s004210050029
- Buchheit M, Laursen PB. 2013. Sports Med Part I doi:10.1007/s40279-013-0029-x；Part II doi:10.1007/s40279-013-0066-5
- Dupont G, et al. 2004. MSSE. doi:10.1249/01.mss.0000113477.11431.59
- Rønnestad BR, et al. 2015. Scand J Med Sci Sports. doi:10.1111/sms.12165
- Rønnestad BR, et al. 2020. Scand J Med Sci Sports. doi:10.1111/sms.13627
- Seiler S, Sjursen JE. 2004. Scand J Med Sci Sports. doi:10.1046/j.1600-0838.2003.00353.x
- Seiler S, Hetlelid KJ. 2005. MSSE. doi:10.1249/01.mss.0000177560.18014.d8
- Seiler S, et al. 2013. Scand J Med Sci Sports. doi:10.1111/j.1600-0838.2011.01351.x
- Tardieu-Berger M, et al. 2004. EJAP. doi:10.1007/s00421-004-1189-z
- Thevenet D, et al. 2007. EJAP. doi:10.1007/s00421-006-0327-1
- Thevenet D, et al. 2008. J Sports Sci. doi:10.1080/02640410802072697

**W′ 恢復：**
- Bartram JC, et al. 2018. IJSPP. doi:10.1123/ijspp.2017-0034
- Black MI, et al. 2023. MSSE. doi:10.1249/mss.0000000000003039
- Caen K, et al. 2019. MSSE. doi:10.1249/mss.0000000000001968
- Caen K, et al. 2021. MSSE. doi:10.1249/mss.0000000000002673
- Chorley A, et al. 2022. EJAP. doi:10.1007/s00421-021-04874-3
- Ferguson C, et al. 2010. J Appl Physiol. doi:10.1152/japplphysiol.91425.2008
- Raimundo JAG, et al. 2022. Front Physiol. doi:10.3389/fphys.2022.952818（Skiba τ 公式的全文出處）
- Skiba PF, et al. 2012. MSSE. doi:10.1249/mss.0b013e3182517a80

**課中疲勞訊號與 HRV：**
- Foster C, et al. 2001. JSCR. doi:10.1519/1533-4287(2001)015<0109:anatme>2.0.co;2
- Girard O, et al. 2011. Sports Med. doi:10.2165/11590550-000000000-00000
- Glaister M, et al. 2008. JSCR. doi:10.1519/jsc.0b013e318181ab80
- Kiviniemi AM, et al. 2007. EJAP. doi:10.1007/s00421-007-0552-2
- Vesterinen V, et al. 2016. MSSE. doi:10.1249/mss.0000000000000910

**上坡間歇：**
- Barnes KR, et al. 2013. IJSPP. doi:10.1123/ijspp.8.6.639
- Ferley DD, et al. 2013. JSCR. doi:10.1519/JSC.0b013e3182736923
- Ferley DD, Vukovich MD. 2015. JSCR. doi:10.1519/jsc.0000000000000834
- Ferley DD, et al. 2016. IJSM. doi:10.1055/s-0042-109539

**教練來源（非同儕審查）：** Uphill Athlete "Why is my Zone 2 so slow?"；Daniels' Running Formula（**未驗證**，只用你的筆記）。

**未驗證或沒有抓到的：** Gerschler／Reindell 心率 120 規則、Billat 2001、Skiba 2015、Plews、徐國峰的原始網路文章。
