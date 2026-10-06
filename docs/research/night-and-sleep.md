# 夜間與睡眠剝奪：夜跑配速、100 英里的小睡與賽前睡眠儲備（SP-195）

> 調查日期：2026-10-06。只做調查，沒有改程式。
> 標記：**已驗證（全文）**＝讀到全文；**已驗證（摘要）**＝讀到 Europe PMC 摘要；**摘要**＝只讀到擷取工具摘述或搜尋摘要；**教練級**＝教練、廠商、社群說法；**推估**＝我的延伸；**未找到來源**＝找過沒找到。
> 本文編號用 [N1]、[N2]…。和 SP-114（連續賽事的睡眠點）相關的部分在 §4.2。

## 摘要

1. **計算機不分日夜。** 它知道每一段的預估時鐘（`planner.py:57`、`:617`），熱也已經逐時對到分段（`planner.py:178`），但速度沒有任何「天黑」的項。日內衰減只看「當天第幾小時」（`capacity.py:100`、`:418`），越野跑的耐久度只看跑了幾小時（`trailhr.py` 第 2、6 步）。
2. **夜間會慢很多，但「天黑本身」佔多少，沒有研究拆開。** 唯一有數字的是一場 100 英里：日落後平均慢 35.9 %，最慢在凌晨約 3–4 點（日出後第 20 小時）[N1]。這同時包含疲勞、睡意、天黑，n = 21。24 小時賽是「倒 J 形」，最後才掉 [N2]。
3. **睡眠剝奪對耐力的平均影響約 −5.5 %**（統合分析，20 篇）[N3]；一夜沒睡後 30 分鐘測驗少跑約 3 %，主觀費力不變 [N4]。真正的大問題在**安全**：山地超馬完賽者 80 % 有睡眠剝奪症狀，34 % 有幻覺、15 % 跌倒 [N5]。
4. **100 英里多半不睡，睡也很短。** 超過 100 英里的跑者 74 % 全程沒睡 [N6]；UTMB 完賽者 72 % 沒睡、沒睡的比較快 [N7]；165 km 山地賽累計只睡 76 分鐘，82 % 的小睡 < 30 分鐘、79.5 % 在 00:00–05:00 [N5]。**app 的睡眠點預設 90 分鐘（`fuel.py:94`）比實際長很多。**
5. **賽前睡眠儲備有證據，但都是小樣本。** 連 3 晚多睡 30 %，第 4 天計時賽快約 3 % [N8]；6 晚每晚多躺約 1.6 小時，接下來整夜不睡時注意力失誤和微睡眠較少 [N9]；賽前一週有多睡的人，賽中跌倒比較少（12.3 % 對 17.3 %）[N5]。
6. **建議**：(1) 計算機標出夜間段，並提供使用者自選的夜間減速；(2) 睡眠點預設改成依賽事長度；(3) 減量週對跨夜賽事加一則「賽前一週多睡」提醒。都不需要全天戴錶。

## 1. 現況

| 地方 | 現在的做法 | 程式 |
|---|---|---|
| 每段時鐘 | 起跑時間＋累積時間＋停留，算出每段 ETA | `backend/engine/racepower/planner.py:57`（`_clock`）、`:617`、`:697` |
| 逐時熱 | 預報逐時溫度對到每段的時鐘 | `planner.py:153–228`、`weather.py:369`（`hourly_at`） |
| 百岳／健行的時間衰減 | `f_time(h) = e^{γh}`，h＝當天第幾小時；個人斜率往 0 收縮，τ 0.03/h（推估） | `backend/engine/racepower/capacity.py:100`、`:418` |
| 越野跑的耐久度 | δ：跑 1 小時後每小時掉多少，先驗 0.05/h（Clark 2019） | `backend/engine/racepower/trailhr.py` 模組說明第 2、6、7 步 |
| 多日疲勞 | 第 n 天÷第 1 天，個人資料 < 3 趟時用 1.0（無來源） | `backend/engine/racepower/hike.py:66` |
| 連續賽事 | `Event.continuous`：多天、賽制「連續」 | `backend/engine/planning.py:183` |
| 睡眠點 | 補給站類型 `sleep`，預設 90 分鐘（「一個睡眠週期」，推估），時間算進 ETA | `backend/engine/racepower/fuel.py:92–94`、`calc.py:137` |
| 睡眠點的用途 | 賽事評估把連續賽事在睡眠點切段，比「最難的一段」 | `backend/engine/panels/race_refs.py:129`（`sleep_course`）、`race_feasibility.py:756` |
| 跨午夜 | 超過 24 小時或跨午夜的行程不匯出到手錶課表 | `watch_export.py:312–323` |
| 氣候值（沒有預報時） | 只取 06:00–17:59 的白天溫度 | `weather.py:54`、`:298` |

所以：

- 計算機已經有每段的時鐘，**加夜間標示不需要新的資料**，只要日出日落時間（Open-Meteo `daily=sunrise,sunset`，官方文件已驗證）。
- 沒有預報、用氣候值時，夜間段的溫度會用白天平均，偏暖。這和 SP-194（冷）有關，列在 §3。
- 擁有者只在跑步時戴錶，沒有睡眠資料。所以「依你的睡眠調整」做不到，只能做提醒。

既有文件提過的：

- `readiness-signals.md`：不建議做 HRV／睡眠同步，擁有者沒有全天資料。本文不重談。
- `racepower-v2.md` §2.4：多日疲勞沒有研究。本文同樣沒找到「第幾夜慢多少」的研究。
- `baiyue-mountaineering-training.md`：Vieira 2015，4 天賽姿勢穩定度變差，原因之一是睡 6.5 小時。

## 2. 文獻

### 2.1 夜間配速

| 來源 | 說法 | 等級 |
|---|---|---|
| Brager 2020 [N1] | 一場 100 英里（佛州，日出起跑，平均完賽 25.2 h）21 人。日落前平均 13.7 min/mile；「After sunset, runners slowed down by 35.9%」；最慢的 5 英里在日出後約 20 小時（凌晨），20.8 min/mile。作者說這時間對上體溫和警覺的晝夜低點 | 已驗證（全文） |
| Bossi 2017 [N2] | 24 小時賽 501 人：倒 J 形，最後一小時掉最多；快的人起步較保守、配速較平均 | 已驗證（摘要） |
| Thun 2015 回顧 [N10] | 113 篇：運動表現大約在傍晚、核心體溫最高時最好 | 已驗證（摘要） |
| Suter 2020，UTMB 2008–2019 配速 [N11] | 平均配速和較快完賽有關；作者提到夜間可能影響配速變化 | 摘要（搜尋摘要；全文頁面被驗證碼擋） |
| 夜跑經驗文章 [N12] | 步幅變短、步頻變高、下坡變慢；頭燈視野窄讓人覺得自己比較快 | 教練級（搜尋摘要） |

讀完的判斷：

- **天黑本身慢多少：未找到來源。** Brager 的 35.9 % 混了 12 小時以上的疲勞和睡意，不能拿來當「天黑係數」。
- 合理的拆法（推估）：夜間減速 ≈ 疲勞（app 已有 δ、γ）＋晝夜低點（凌晨 2–5 點）＋視線（技術下坡最明顯）。前一項已有，後兩項沒有數字。
- 所以夜間項應該先讓使用者自選，或從自己過去的夜跑學（§4.2 單 1）。

### 2.2 睡眠剝奪對表現

| 來源 | 說法 | 等級 |
|---|---|---|
| Craven 2022 統合分析 [N3] | 69 篇、227 個指標；睡眠 ≤ 6 h 後表現平均 −7.56 %。**耐力 −5.55 %（20 篇、237 人，95 % CI −8.12 到 −2.99）**；耐力的變化和醒著多久沒有關係；下午做的測驗受影響較大 | 已驗證（全文） |
| Oliver 2009 [N4] | 11 人，30 小時不睡後，60 % VO2max 30 分鐘＋30 分鐘自選配速：6037 m 對 6224 m（約 −3 %）；心率、RPE、配速策略沒差；作者：「altered perception of effort may account for decreased endurance performance」 | 已驗證（摘要） |
| Roberts 2019 [N8] | 9 名自行車／三鐵選手，連 3 晚少睡 30 %：第 3 天計時賽 60.4 對 58.8 分（慢）；連 3 晚多睡 30 %：第 4 天 56.8 對 58.7 分（快）；作者建議耐力選手每晚 > 8 小時 | 已驗證（摘要） |
| Hurdiel 2015 [N13] | UTMB 17 人，27–44 小時、賽中平均只休 12 ± 17 分鐘：反應時間變長到視幻覺都有 | 已驗證（摘要） |

### 2.3 安全：跌倒、幻覺

| 來源 | 說法 | 等級 |
|---|---|---|
| Kishi 2024 [N5] | 留尼旺島 Diagonale des Fous（165 km、平均 49.9 h）與 Trail de Bourbon（111 km、33.6 h）1,154 名完賽者。80 % 至少一種睡眠剝奪症狀；警覺下降 54 %、**幻覺 34 %、跌倒 14.6 %**、危險處境 9.7 %；症狀和幻覺隨過夜數增加。賽前一週有多睡的人，歸因於睡眠剝奪的跌倒較少（**12.3 % 對 17.3 %**，p = 0.02），完賽時間沒差。22 % 認為賽後日常生活（開車）事故風險增加 | 已驗證（全文） |

讀完的判斷：

- 對越野和百岳，睡眠剝奪的主要成本是**跌倒和判斷錯誤**，速度只掉幾 %。這支持「提醒」重於「修正時間」。
- 賽後開車：22 % 自己覺得危險 [N5]。這可以放進賽後恢復的提示（推估）。

### 2.4 賽中小睡：睡不睡、睡多久、何時睡

| 來源 | 說法 | 等級 |
|---|---|---|
| Miller 2022 [N6] | 119 位跑過 > 100 英里的人：**74 % 全程沒睡**；≥ 200 英里的比賽睡得多、睡得久 | 已驗證（摘要） |
| Poussel 2015 [N7] | UTMB 2013：完賽者 72 % 沒睡；沒睡的比較快；賽前多睡的比較快 | 已驗證（摘要） |
| Martin 2018 [N14] | 636 位超馬跑者問卷。賽中累計睡眠：< 36 h 的比賽 0.55 ± 0.70 h；36–60 h 1.36 ± 1.51 h；> 60 h 8.24 ± 5.15 h。只跨一夜的比賽 < 20 % 的人睡；多日幾乎都睡。最常見的賽中策略是微睡（micronap） | 已驗證（全文） |
| Kishi 2024 [N5] | 77 % 至少睡一次，但累計很少：165 km **76 分**、111 km **27 分**；**82 % 的小睡 < 30 分**；**79.5 % 在 00:00–05:00** | 已驗證（全文） |
| Hilditch & McHill 2019 回顧 [N15] | 睡眠慣性（剛醒的遲鈍）多在醒後 30 分鐘內消退，完全恢復可能要 1 小時以上；≤ 30 分鐘的小睡較少睡眠慣性；避免在生理夜間低點醒來 | 已驗證（全文） |
| Lastella 2021 系統性回顧 [N16] | 運動員白天小睡 20–90 分鐘、13:00–16:00；醒後留 30 分鐘再上場。研究是一般訓練情境，不是比賽中 | 已驗證（全文） |
| Savoldelli 2017 Tor des Géants 個案 [N17] | 330 km、125 h 完賽，共睡 11 h 40 min（9.3 %）；兩次睡之間平均醒 15 h 40 min（2–23.5 h） | 已驗證（全文），n = 1 |

讀完的判斷：

- **< 36 小時（多數 100 km、快的 100 英里）**：多數人不睡。要睡就是 10–25 分鐘的微睡（推估：Kishi 的 < 30 分＋避免睡眠慣性）。
- **36–60 小時（慢的 100 英里、UTMB 中後段）**：累計約 1–1.5 小時，分成幾次短睡，放在凌晨 [N5][N14]。
- **> 60 小時（200 英里、Tor des Géants、多日連續賽）**：每晚要睡，累計約 8 小時，一次可以到一個 90 分鐘的週期 [N14][N17]。
- 小睡醒來後至少留 10–15 分鐘慢慢走再加速（推估，依 [N15] 的 15–30 分鐘消退）。

### 2.5 賽前睡眠儲備

| 來源 | 說法 | 等級 |
|---|---|---|
| Arnal 2015 [N9] | 14 名男性，6 晚每晚 9.8 h 對 8.2 h 在床：基準注意力較好；**接下來整夜不睡時，注意力失誤和不自主微睡眠較少**；效果在恢復睡眠一晚後仍在 | 已驗證（摘要） |
| Roberts 2019 [N8] | 連 3 晚多睡 30 %，第 4 天計時賽快約 3 % | 已驗證（摘要） |
| Kishi 2024 [N5] | 跑者平均帶著約 −50 分鐘／週的睡眠債出發；賽前一週多睡的跌倒較少 | 已驗證（全文） |
| Martin 2018 [N14] | 74 % 的跑者賽前會注意睡眠；最常見的策略是延長夜間睡眠（55 %）與白天小睡（20 %） | 已驗證（全文） |

讀完的判斷：

- 證據方向一致：**賽前 3–7 晚多睡**，對隔夜不睡時的注意力有幫助，可能減少跌倒。
- 樣本都小，沒有越野賽的隨機試驗。可以當提醒，不能當保證。
- 「賽前一晚睡不好」很常見；一晚的影響在研究中比連續幾晚的睡眠債小（Craven 的耐力效果和醒著多久無關 [N3]）。提醒可以順便減少焦慮（推估）。

## 3. 落差

| # | 落差 | 影響 | 證據 |
|---|---|---|---|
| G1 | 計算機不標日夜 | 使用者看不到哪幾段在天黑，頭燈電量、保暖、補給沒有提示 | 日出日落是確定資料：強 |
| G2 | 沒有夜間減速 | 跨夜賽事 ETA、關門與撤退時間偏樂觀 | 方向確定，大小未知：弱 |
| G3 | 睡眠點預設 90 分鐘 | 對多數 100 英里太長；對 > 60 h 的賽事可能剛好 | 實際行為資料：中 |
| G4 | 減量期沒有睡眠提醒 | 跨夜賽事前沒有任何睡眠建議 | 小樣本試驗＋大型問卷：中 |
| G5 | 氣候值只取白天 | 沒預報時，夜間段溫度偏暖（冷提示、補給低估） | 程式事實（`weather.py:54`） |
| G6 | 賽後開車風險 | 賽後提示沒有提 | 問卷：弱 |

## 4. 結論與建議

### 4.1 結論

- 夜間一定慢，但沒有可用的「天黑係數」。**先把夜間段標出來，減速讓使用者自己選或從自己的資料學**，不要寫死一個數字。
- 睡眠剝奪的主要代價是安全（幻覺、跌倒），提醒比修正時間有用。
- 睡眠點的預設應該依賽事總時數，而不是固定 90 分鐘。
- 賽前睡眠儲備適合放進減量期的提示。

### 4.2 建議開的單

**單 1：計算機標出夜間段，加可選的夜間減速**
- 優先度：**P2**
- 內容：取比賽地點的日出日落（Open-Meteo `daily=sunrise,sunset`；沒有網路時用天文公式）。每段 ETA 落在日落後、日出前就標「夜間」。分段表與剖面圖畫出夜間區。另外提供「夜間減速」選項，預設 0 %，使用者可選 5／10／15 %（推估），只乘在夜間段；凌晨 02:00–05:00 可另選加重（推估，依 [N1] 的最慢時段）。
- 驗收條件草案：
  1. 有起跑時間時，分段表多一欄「夜間」，剖面圖畫出夜間背景。
  2. 夜間減速 0 % 時，預估時間和現在完全一樣（回歸測試）。
  3. 選 10 % 時，只有夜間段的時間變長；撤退時間與關門判斷跟著變。
  4. 頁面寫出「沒有研究拆出天黑本身慢多少；Brager 2020 的 35.9 % 含疲勞」。
- 需要使用者決定：預設值要 0 % 還是 5 %；要不要做「從自己過去的夜跑學」（要先確認使用者有沒有足夠的夜間活動，擁有者可能很少）；夜間段要不要加頭燈與保暖提示（和 SP-194 單 1 合併）。

**單 2：睡眠點預設時間依賽事長度**
- 優先度：**P2**（直接修正 SP-114 的預設）
- 內容：睡眠點的預設分鐘改成依預估總時數：< 36 h 每次 20 分；36–60 h 每次 20–30 分，提示「研究中累計約 1–1.5 小時」；> 60 h 每次 90 分（一個週期）。新增睡眠點時，若它的 ETA 在 00:00–05:00 以外，提示「多數人在凌晨小睡」。
- 驗收條件草案：
  1. 新增睡眠點，預設分鐘照上表；使用者改過的值不被覆蓋。
  2. 已存檔的 90 分鐘不自動改。
  3. 說明文字引用 Kishi 2024、Martin 2018。
- 需要使用者決定：門檻 36／60 小時與每次分鐘數（都是依研究分組的推估）；已存的計算機結果要不要提示「預設已改」。

**單 3：跨夜賽事的減量期睡眠提醒**
- 優先度：**P3**
- 內容：A／B 賽事預估 ≥ 20 小時（或 ETA 跨過午夜）時，在賽前第 7 天到前一天的週計畫加一則提示：「賽前一週每晚多睡約 1 小時，下午可以小睡 20–90 分；前一晚睡不好不用擔心」。依 [N5][N8][N9][N16]。
- 驗收條件草案：條件成立的週出現提示，條件不成立的不出現；提示可關閉；不改課表。
- 需要使用者決定：門檻用 20 小時還是「跨夜」；要不要也在賽後提示「賽後 2 天內避免長途開車」（[N5]，推估）。

**單 4：沒有預報時的夜間溫度**
- 優先度：**P3**
- 內容：氣候值目前只取 06:00–17:59（`weather.py:54`）。跨夜賽事改成逐時氣候值（同一個 ±7 天、近 5 年，按小時平均），讓夜間段有自己的溫度。和 SP-194 的冷提示共用。
- 驗收條件草案：ETA 在夜間的分段用夜間的氣候溫度；白天段結果不變；測試涵蓋跨午夜。
- 需要使用者決定：是否值得多一次 API 呼叫（每個賽事一次，Open-Meteo 免費額度內）。

**先不開：睡眠追蹤或依睡眠調整課表**
- 理由：擁有者只在跑步時戴錶，沒有睡眠資料（`readiness-signals.md` 的結論）。

## 5. 沒查到、付費牆、查無出處

**沒查到**

- 把「天黑」和疲勞、睡意分開的夜間配速研究（越野或山地）。
- 夜間跌倒率和白天比較的越野研究（Kishi 只問了「歸因於睡眠剝奪的跌倒」）。
- 頭燈亮度與下坡速度的研究。
- 賽中小睡長度對後續配速影響的對照研究（[N6] 作者也說要再研究）。

**付費牆或讀不到**

- Poussel 2015、Hurdiel 2015、Roberts 2019、Bossi 2017、Oliver 2009 全文：只讀摘要。
- UTMB 配速研究（PMC7578994）與 Western States 配速研究（Sci Rep 2025）全文：頁面被驗證碼或轉址擋住，沒讀到夜間相關段落。

**查無出處、不採用**

- 夜跑文章說的「夜間慢 X %」：搜尋摘要沒有附數字或研究來源。

## 參考文獻

- [N1] Brager AJ, Demiral S, Choynowski J, et al. Earlier shift in race pacing can predict future performance during a single-effort ultramarathon under sleep deprivation. *Sleep Sci* 2020;13:25–31. https://doi.org/10.5935/1984-0063.20190132 ；https://pmc.ncbi.nlm.nih.gov/articles/PMC7347363/
- [N2] Bossi AH, Matta GG, Millet GY, et al. Pacing strategy during 24-hour ultramarathon-distance running. *Int J Sports Physiol Perform* 2017;12:590–596. https://doi.org/10.1123/ijspp.2016-0237
- [N3] Craven J, McCartney D, Desbrow B, et al. Effects of acute sleep loss on physical performance: a systematic and meta-analytical review. *Sports Med* 2022;52:2669–2690. https://doi.org/10.1007/s40279-022-01706-y ；https://pmc.ncbi.nlm.nih.gov/articles/PMC9584849/
- [N4] Oliver SJ, Costa RJ, Laing SJ, Bilzon JL, Walsh NP. One night of sleep deprivation decreases treadmill endurance performance. *Eur J Appl Physiol* 2009;107:155–161. https://doi.org/10.1007/s00421-009-1103-9
- [N5] Kishi A, Millet GY, Desplan M, Lemarchand B, Bouscaren N. Sleep and ultramarathon: exploring patterns, strategies, and repercussions of 1,154 mountain ultramarathons finishers. *Sports Med Open* 2024;10:34. https://doi.org/10.1186/s40798-024-00704-w ；https://pmc.ncbi.nlm.nih.gov/articles/PMC11001838/
- [N6] Miller DJ, Bianchi D, Lastella M. Running on empty: self-reported sleep/wake behaviour during ultra-marathon events exceeding 100 miles. *Eur J Investig Health Psychol Educ* 2022;12:792–801. https://doi.org/10.3390/ejihpe12070058
- [N7] Poussel M, Laroppe J, Hurdiel R, et al. Sleep management strategy and performance in an extreme mountain ultra-marathon. *Res Sports Med* 2015;23:330–336. https://doi.org/10.1080/15438627.2015.1040916
- [N8] Roberts SSH, Teo WP, Aisbett B, Warmington SA. Extended sleep maintains endurance performance better than normal or restricted sleep. *Med Sci Sports Exerc* 2019;51:2516–2523. https://doi.org/10.1249/mss.0000000000002071
- [N9] Arnal PJ, Sauvet F, Leger D, et al. Benefits of sleep extension on sustained attention and sleep pressure before and during total sleep deprivation and recovery. *Sleep* 2015;38:1935–1943. https://doi.org/10.5665/sleep.5244
- [N10] Thun E, Bjorvatn B, Flo E, Harris A, Pallesen S. Sleep, circadian rhythms, and athletic performance. *Sleep Med Rev* 2015;23:1–9. https://doi.org/10.1016/j.smrv.2014.11.003
- [N11] Suter D, Sousa CV, Hill L, Scheer V, Nikolaidis PT, Knechtle B. Even pacing is associated with faster finishing times in ultramarathon distance trail running — the "Ultra-Trail du Mont Blanc" 2008–2019. *Int J Environ Res Public Health* 2020;17:7074. https://doi.org/10.3390/ijerph17197074 （全文未讀）
- [N12] Night trail running in ultras: what changes after dark. https://www.enduringmotion.com/en/blog/night-running-ultra （搜尋摘要）
- [N13] Hurdiel R, Pezé T, Daugherty J, et al. Combined effects of sleep deprivation and strenuous exercise on cognitive performances during The North Face Ultra Trail du Mont Blanc. *J Sports Sci* 2015;33:670–674. https://doi.org/10.1080/02640414.2014.960883
- [N14] Martin T, Arnal PJ, Hoffman MD, Millet GY. Sleep habits and strategies of ultramarathon runners. *PLoS One* 2018;13:e0194705. https://doi.org/10.1371/journal.pone.0194705 ；https://pmc.ncbi.nlm.nih.gov/articles/PMC5942705/
- [N15] Hilditch CJ, McHill AW. Sleep inertia: current insights. *Nat Sci Sleep* 2019;11:155–165. https://doi.org/10.2147/nss.s188911 ；https://pmc.ncbi.nlm.nih.gov/articles/PMC6710480/
- [N16] Lastella M, Halson SL, Vitale JA, Memon AR, Vincent GE. To nap or not to nap? A systematic review evaluating napping behavior in athletes and the impact on various measures of athletic performance. *Nat Sci Sleep* 2021;13:841–862. https://doi.org/10.2147/nss.s315556 ；https://pmc.ncbi.nlm.nih.gov/articles/PMC8238550/
- [N17] Savoldelli A, Fornasiero A, Trabucchi P, et al. The energetics during the world's most challenging mountain ultra-marathon — a case study at the Tor des Geants. *Front Physiol* 2017;8:1003. https://pmc.ncbi.nlm.nih.gov/articles/PMC5723401/
- Open-Meteo Forecast API 文件（`daily=sunrise,sunset`）. https://open-meteo.com/en/docs （2026-10-06 讀取）
- 既有文件：`readiness-signals.md`、`racepower-v2.md` §2.4、`baiyue-mountaineering-training.md`。
