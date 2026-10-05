# 專項期內部要不要逐週收斂（SP-75）

> 調查日期：2026-10-05。只做調查，沒有改程式。
> 標記：**已驗證**＝這次讀到原文或摘要（附 URL／DOI）；**未驗證**＝只看到二手轉述；**推估**＝我的延伸。
> 來源等級：同儕審查／教練（作者本人的文章或書）／教練二手（別人整理某教練的方法）／社群。

## 摘要

1. **長跑有逐週進展，質量課沒有。** 專項期 8 週裡，長天的目標每週不同（賽事單日目標的 50 % → 90 %），但路跑的三區課固定是 2×15 分、越野的五區課固定是 5×4 分，第 1 週和第 8 週一樣。
2. **各學派一致的只有一句話：越接近比賽，練的東西越像比賽。** 怎麼排，各派不同，而且有互相矛盾的地方（§3.3）。沒有找到比較「專項期不同排法」的對照試驗。
3. **建議把專項期切成前段（支持）和後段（專項）**，不新增課型，只改三件事：兩軌的比例隨前後段變、專項期的課改回走階梯、路跑長跑裡的比賽配速段逐週加長。
4. **5×4 分這堂課該換。** 它的量（20 分）超過 app 自己給五區的單堂上限（10–16 分），而且不看使用者階梯做到哪一階。對長距離越野和超馬，五區本來就是最不專項的強度。換成「五區階梯目前那一階的上坡版」，長距離越野的後段改以上坡三區為主。
5. 有五件事要你決定（§5）。

## 1. 現在的程式怎麼排專項期

專項期固定 8 週，接 14 天減量（`backend/engine/planning.py:42–43`、`planning.py:340–349`）。也就是賽前第 10–3 週（`backend/engine/specific_phase.py:41`）。

| 課 | 專項期內有沒有逐週變化 | 怎麼變 | 程式 |
|---|---|---|---|
| 長天（越野／百岳） | **有** | 賽事單日目標的 50 → 55 → 60 → 70 → 85 → 70 → 90 → 70 %（賽前第 10 → 3 週）；每次最多比近 4 週最長多 15 % | `specific_phase.py:42`（`FRAC`，推估）、`specific_phase.py:289–302` |
| 長跑（路跑） | **有** | 賽事距離的 55 → 60 → 65 → 70 → 75 → 70 → 80 → 65 %，上限 35 km、180 分 | `specific_phase.py:58–61`（`ROAD_FRAC`，推估） |
| 長跑裡的馬拉松配速段（路跑） | 間接 | 固定是長跑時間的 40 %（20–75 分），長跑變長它才變長；第一週就是 40 % | `backend/engine/overview.py:407`、`overview.py:866–868`、`overview.py:879–888` |
| 三區課（路跑） | **沒有** | 每週都是「閾值節奏 2×15 分（平路）」，不走階梯 | `overview.py:688–689`、`overview.py:894–896` |
| 三區課（越野） | 走階梯 | 階梯 A1–A4，允許上坡版；達標才進階 | `overview.py:693–695`、`overview.py:1220`（`not road`） |
| 五區課（越野） | **沒有** | 每週都是「爬坡間歇 5×4 分」，6–10 % 坡，目標 101–106 % CP，不走階梯 | `overview.py:690–691`、`overview.py:898–900`；`backend/engine/zones.py:407` |
| 五區課（路跑） | 走階梯 | 階梯 V1–V4 | `overview.py:693–695` |
| 兩軌的比例（一週一堂時） | **沒有** | 整個專項期固定：路跑 ≤ 10 km 是 1:1，其餘 2:1（三區:五區） | `backend/engine/quality_gate.py:1332–1343`（`track_ratio`，推估） |
| 長爬坡反覆（越野，有 GPX） | 沒有 | 每週一堂，取代一堂輕鬆跑；賽道最長那段坡的坡度，每趟 ≤ 20 分、上坡共 ≤ 60 分，心率在輕鬆跑上限 | `specific_phase.py:366–454` |
| 賽事模擬 | 只在賽前第 4–3 週 | 是建議，使用者按了才排 | `specific_phase.py:43`、`specific_phase.py:547–588` |
| B2B | 每個 3:1 周期最多一次 | 是建議 | `backend/engine/b2b.py`（`back-to-back-and-long-day.md` §3） |
| 技術地形課（越野） | 沒有 | 每週一堂，RPE 6–7 或 4–5 | `backend/engine/technical.py`（`docs/spec/overview.spec.md` SP-74） |
| 週量 | 有 | CTL 每週目標比基礎期高（max(2.5, 7 %)），3:1 恢復週 | `backend/engine/load_guard.py:299`、`overview.py:427` |
| 恢復週（專項期） | — | 沒有長跑、沒有質量課（恢復週的 fartlek 只在基礎期排） | `overview.py:1179`、`overview.py:1222` |
| 減量期 | — | 一堂：三區軌是節奏 2×8 分，五區軌或兩軌都沒開是 4×3 分（98–102 % CP） | `overview.py:903–907` |

補充兩點：

- 路跑專項期的三區課固定是 2×15 分。使用者如果在基礎期已經做到階梯的 2×20 分或連續 30 分，進專項期反而**退回**比較短的課。
- 越野專項期的五區課固定是 20 分。使用者如果五區階梯才到第一階（5×2 分，10 分），進專項期當週就**跳**到兩倍的量。

沒查到底：專項期這兩堂固定的課做完之後，算不算階梯的「達標」。`plan-auto.spec.md` 寫階梯以外的課不計入，我沒有追到 `dose_step` 裡確認。

## 2. 來源

### 2.1 教練

| 來源 | 等級 | 說法 | 驗證 |
|---|---|---|---|
| Jason Koop〈3 Steps for Creating Your Ultrarunning Long-Range Plan〉（CTS，2025-03-07 更新） | 教練（本人） | 「If an aspect is highly specific to the event, you should develop it closer to the event」；「The last 6-8 weeks of training should include primarily lower intensity EnduranceRuns and SteadyStateRuns, as those intensities will be most similar to just about any ultramarathon race」；RunningIntervals 放最前面；Hardrock 的例子：健行訓練集中在賽前幾週，VO2max 配速是最不重要的、年初就練 | 已驗證 https://trainright.com/ultrarunning-long-range-plan-3-steps/ |
| Addison Smith〈How Speedwork Improves Ultrarunning Performance〉（CTS 教練，2025-04-30 更新） | 教練（CTS，不是 Koop 本人） | RunningIntervals 3–5 週在最前面（「least race-specific」）→ TempoRun 3–6 週 → SteadyStateRun 4–6 週「or a high-volume block of low intensity training closest to the event」；「easy long runs are the most specific type of training to most ultramarathon race efforts」 | 已驗證 https://trainright.com/how-speedwork-improves-ultrarunning-performance/ |
| Jason Koop〈Decoding Interval Workouts〉（2025-03-07 更新） | 教練（本人） | RunningIntervals：RPE 10、每趟 2–4 分、共 12–24 分、1:1。TempoRun：RPE 8–9、每趟 8–20 分、共 30–60 分、2:1。SteadyStateRun：RPE 7、每趟 20–60 分、共 30 分到 2 小時、5–8:1 | 已驗證 https://trainright.com/decoding-interval-workouts-for-ultramarathon-training/ |
| Jason Koop〈Reasons Ultrarunners Should Do More Uphill Running Intervals〉（2026-02-11 更新） | 教練（本人） | 「I prescribe intervals to be done uphill roughly 80% of the time」。沒有給坡度和每趟長度 | 已驗證 https://trainright.com/benefits-of-uphill-running-intervals-ultrarunners/ |
| Jason Koop〈How to Train for Mountains When You Live in a Flat Area〉（2025-03-07 更新） | 教練（本人） | 山區訓練營 2–3 天，「performed 4-6 weeks out from the event」，全部 EnduranceRun 強度；短坡繞圈每週 2–3 次、賽前約 4–6 週；下坡「a little downhill dose will go a long way」 | 已驗證 https://trainright.com/train-for-mountainous-ultramarathon-live-in-flat-area/ |
| Jason Koop〈The Hierarchy of Ultramarathon Training Needs〉 | 教練（本人） | 高強度「maybe 20% of training sessions and only about 10% of total training hours per year」；體能優先於地形專項 | 已驗證 https://trainright.com/hierarchy-ultramarathon-training-needs-jason-koop/ |
| Jason Koop〈Block or Mixed-Intensity Periodization〉 | 教練（本人） | 一年大部分時間用 block（一週內練相近的強度）。沒有給 block 的週數，也沒有講最後一個 block | 已驗證 https://trainright.com/block-mixed-intensity-periodization-ultrarunning/ |
| Uphill Athlete〈How to Train for Trail Running〉（2022-05-23） | 教練（官方網站） | 專項期在 A 賽事前 2–3 個月開始；「All athletes should drop muscular endurance training」；短賽事（VK、短距離越野）：「two interval workouts, one Zone 3 and one Zone 4」；長賽事：「back-to-back long runs and one Zone 3 or Zone 4 interval workout」。專項期沒有給每趟長度、趟數、坡度 | 已驗證 https://uphillathlete.com/trail-running/training-for-trail-running/ |
| Uphill Athlete 論壇〈Zone 3 work outs〉 | 社群 | 三區總時間 30–60 分、坡度像比賽、例 3×10 分上坡 | 未驗證（頁面 404，只看到搜尋摘要） |
| Renato Canova 2017 Valencia 演講（John Davis 逐字稿與註解） | 教練（本人的演講，二手轉錄） | 轉換 4 週、一般期 4 週、基本期約 6 週、專項期約 10 週；「extend the intensity, not to qualify the volume」；基本期 87–93 % MP、專項期 98–103 % MP。逐字稿裡沒有 funnel 的細節，也沒有最後 2–3 週的做法 | 已驗證 https://runningwritings.com/2023/07/renato-canova-marathon-training-lecture.html |
| Arcelli & Canova《Marathon Training – A Scientific Approach》1999（John Davis 書評） | 教練二手 | 專項期是賽前最後 6–8 週；專項期連續跑 30–35 km @ 97–100 % MP；MP 間歇 100–105 % MP（例 4×6 km @ 102–104 %）。Davis 明寫書裡「no guidance on how to structure or progress these marathon-specific workouts over time」 | 已驗證（書評）；原書未讀 https://runningwritings.com/2023/06/canova-marathon-book.html |
| John Davis〈Canova-style percentage-based training〉 | 教練二手（作者自己的整理） | 漏斗：「start at either end of the ladder of support and work inward over time, with speed and endurance support converging on race pace」；一般期 70–90 % 與 115 %+ 比賽配速、支持期 90 % 與 110 %、專項期 95／100／105 %；「training is to add, not to replace」；適用 800 m 到馬拉松。沒有給各期週數 | 已驗證 https://runningwritings.com/2023/12/percentage-based-training.html |
| John Davis〈Emile Cairess 倫敦馬拉松前的訓練分析〉 | 教練二手（菁英個案） | MP 間歇第 5 週才出現，之後固定；專項課在 MP 的 1–3 % 內；每堂 MP 量 18–25 km；最長連續 MP 段 3 → 6 → 10 km；重點逐漸從 105 % MP 移到 100 % MP | 已驗證 https://runningwritings.com/2024/05/renato-canova-marathon-training-emile-cairess.html |
| Jeff Gaudette〈Canova Special Block Training〉 | 教練二手 | special block 每 3–4 週一次，一天兩堂。最後一次在賽前幾週沒有寫 | 已驗證 https://runnersconnect.net/special-block-training/ |
| Pfitzinger & Douglas《Advanced Marathoning》 | 教練二手 | 四個 mesocycle：耐力 → 乳酸閾值＋耐力 → 比賽準備（重點從閾值轉到 VO2max，兩場調整賽）→ 減量 3 週。MP 長跑的進度是 16 英里含 8 英里 MP → 18 含 10 → 18 含 14 | 未驗證（只看到第三方整理和搜尋摘要；原書未讀） https://runningwithrock.com/pfitz-marathon-training-explained/ |
| Daniels《Daniels' Running Formula》第 3 版（讀書筆記） | 教練二手 | M 配速單堂 ≤ min(110 分, 29 km)，且 ≤ 週跑量 20 %；T 單堂 ≤ 週跑量 10 %；I ≤ min(10 km, 8 %)。筆記沒有記 M 配速跨期怎麼進展 | 已驗證（筆記）；原書未讀 https://run.wxm.be/books/jack-daniels-running-formula.html |
| Daniels 的 Phase IV | 教練二手 | Phase IV 以 T 為主、長跑改成全輕鬆（不再有 M 配速） | 未驗證（搜尋摘要，而且講的是半馬計畫） |
| Bompa & Buzzichelli 2015 p.96、p.112、p.125、p.173 | 教練／教科書 | 一般 → 專項；專項準備期強度高，周期改短（2+1）；接近比賽時量下降、強度和專項比例上升 | 已驗證（`bompa-periodization-strength.md` §2.2、§2.6） |
| 既有文件裡已經整理的 | — | UA 三區 30–60 分、4:1–5:1；Koop 上坡 TempoRun；江晏慶「抓比賽距離爬升的七成」；B2B 放專項期 | 見 `interval-prescription.md`、`back-to-back-and-long-day.md`、`coach-schools-zones-periodization.md` Finding 4 |

### 2.2 同儕審查

| 來源 | 說法 | 和這題的關係 | 驗證 |
|---|---|---|---|
| Haugen, Sandbakk, Seiler, Tønnessen 2022, *Sports Med Open*, DOI 10.1186/s40798-022-00438-7 | 世界級長跑選手：「the focus gradually shifts throughout the preparation period from achieving high total running volume to achieving more running volume at or near race pace」；馬拉松和場地選手都是「volume of race-pace running increases as the main competition approaches」；馬拉松選手接近比賽時中強度比例上升，場地選手則是高強度上升、中強度下降；全年 ≥ 80 % 低強度；減量從賽前 7–10 天開始 | 直接支持「比賽配速的量逐步增加」，也支持「收斂的方向依賽距不同」。對象是世界級選手，描述性 | 已驗證（PMC 全文） https://pmc.ncbi.nlm.nih.gov/articles/PMC8975965/ |
| Casado, González-Mohíno, González-Ravé, Foster 2022, *IJSPP* 17:820, DOI 10.1123/ijspp.2021-0435 | 系統性回顧，10 篇：菁英跑者準備期是金字塔分配，比賽期轉向極化；馬拉松選手較金字塔，1500 m 較極化 | 支持「分配隨周期變」。10 篇、菁英 | 已驗證（摘要） |
| Kenneally, Casado, Santos-Concejero 2018, *IJSPP*, DOI 10.1123/ijspp.2017-0327 | 系統性回顧，16 篇：金字塔和極化比閾值型有效；建議用「目標比賽配速的百分比」定區間，讓不同的周期化方法可以相容 | 支持用比賽配速當尺 | 已驗證（摘要） |
| Kenneally, Casado, Gomez-Ezeiza, Santos-Concejero 2021, *Eur J Sport Sci*, DOI 10.1080/17461391.2020.1773934 | 7 位世界級選手 50 週：用比賽配速分區和用生理分區，得到的強度分配不同 | 提醒「比賽配速」和「生理區間」是兩把尺 | 已驗證（摘要） |
| Barnes, Hopkins, McGuigan, Kilding 2013, *IJSPP* 8:639, DOI 10.1123/ijspp.8.6.639 | 20 位訓練有素的跑者，5 種上坡間歇、6 週：5 km 計時賽平均進步 2.0 %（±0.6 %），**沒有哪一種強度明顯最好**；最高強度對跑步經濟性最好 | 上坡間歇有效，但選哪一種強度沒有定論。對象是路跑、結果是 5 km | 已驗證（摘要） |
| Alemu, Tadesse, Birhanu 2025, *Sci Rep*, DOI 10.1038/s41598-025-08275-w | 40 位青少年中距離選手、8 週：約 7.6 % 坡組的 800 m 成績和最大速度進步比 2.5 %、5.1 % 坡組多 | 坡度的證據，但對象是青少年 800 m，離越野很遠 | 已驗證（摘要） |
| Ehrström et al. 2018, *MSSE*, DOI 10.1249/mss.0000000000001467 | 9 位菁英、27 km 越野賽：傳統耐力模型預測不好；股四頭肌耐力指標（r = 0.91）和 VO2max（r = −0.76）相關最高；加入局部肌耐力和坡度上的跑步經濟性後預測變好 | 短距離越野裡 VO2max 仍然重要；專項的東西是坡上的經濟性和肌耐力。只有 9 人 | 已驗證（摘要） |
| Aubry et al. 2014, *MSSE* 46:1769, DOI 10.1249/MSS.0000000000000301 | 33 位鐵人選手，3 週加量後減量 4 週：只有急性疲勞的組進步最多（2.6 %）；達到功能性過度負荷的組進步較少，感染率 70 %；60–83 % 的人在減量前兩週達到最佳 | 減量前可以加量，但不能加到過度負荷 | 已驗證（摘要） |
| Bosquet et al. 2007, *MSSE*, DOI 10.1249/mss.0b013e31806010e0 | 統合分析 27 篇：2 週、量指數式減 41–60 %、強度和頻率不變 | 減量期保留強度 | 已驗證（摘要） |

### 2.3 沒有找到的

- 比較「專項期不同排法」的對照試驗（逐週收斂 vs 固定課表）：沒有找到。
- 越野或超馬專項期的介入研究：沒有找到。找到的越野研究都是橫斷面或下坡肌肉損傷。
- Pfitzinger、Daniels 的原書：沒有讀到。表裡這兩派的內容都是二手。
- UA 專項期的上坡間歇規格（每趟長度、趟數、坡度）：官方文章沒有寫；論壇那篇打不開。
- Canova 的「±3 % MP」：這次讀到的是「專項期 95–105 %」（Davis）和「MP 的 1–3 % 內」（Cairess 個案）。`coach-schools-zones-periodization.md` 寫的 ±3 % 大致對，但不是一個固定的規則。

## 3. 讀完之後的判斷

### 3.1 一致的地方

1. **最專項的東西放最後。** Koop、UA、Canova、Bompa 都這樣說；Haugen 2022 在世界級選手身上觀察到同樣的事（比賽配速的量隨比賽接近而增加）。
2. **「專項」依賽事不同。** 超馬的比賽強度很低，所以最專項的是長時間的低強度和穩態（Koop）。馬拉松是比賽配速附近（Canova、Haugen）。場地賽和短距離是高強度（Casado、Haugen）。
3. **加，不是換。** Davis 整理 Canova：「training is to add, not to replace」。專項期不是把基礎的東西全部拿掉。
4. **減量前不要練到過度負荷**（Aubry 2014）；減量期保留強度（Bosquet 2007）。

### 3.2 對 app 的含意

- app 的階梯終點固定在五區。對半馬以上和越野，終點應該是三區或更低的穩態。這點 `coach-schools-zones-periodization.md` 的「模式三」已經寫過，這次的來源再次支持。
- app 已經有的專項元素（長天照賽事目標、長爬坡反覆、技術地形、B2B、賽事模擬）都對得上來源。缺的只有質量課這一塊。

### 3.3 互相矛盾的地方

| 題目 | 一邊 | 另一邊 | 怎麼處理 |
|---|---|---|---|
| 賽前最後一段練什麼強度 | Koop：最後 6–8 週以低強度和穩態為主，VO2max 放最前面 | Pfitzinger（二手）：比賽準備期把重點從閾值轉到 VO2max，放在減量前 | 兩邊的賽事不同（超馬 vs 馬拉松），不算直接衝突。但 Pfitzinger 和 Canova 同樣是馬拉松，卻一個轉 VO2max、一個收斂到 MP。app 現在的馬拉松做法（閾值＋ MP 長跑）比較接近 Canova；Pfitzinger 這條只有二手來源，不採用 |
| 長跑裡的比賽配速段要不要一路做到減量前 | Pfitzinger（二手）：MP 段一路加到 14 英里；Canova：專項期才是 MP 的重點 | Daniels Phase IV（未驗證）：長跑改回全輕鬆 | Daniels 這條沒有驗證，而且講的是半馬計畫。採用前者，但把最後一次 MP 長跑放在賽前第 4 週（§4.2） |
| 專項期還要不要五區 | UA 長賽事：B2B ＋「one Zone 3 or Zone 4」，二選一 | Koop：VO2max 是最不專項的，年初就練完 | 長距離越野的後段：不排五區，或只留維持量。要你決定（§5） |
| 用 block 還是混合 | Koop：一年大部分時間用 block（一週只練一種強度） | UA 短賽事、Casado 2022：一週內三區和高強度各一堂 | app 是混合式，加上兩軌比例。維持；用比例的變化達到「這幾週以哪一種為主」 |
| 上坡間歇選哪一種強度 | Koop：80 % 的間歇都在上坡，不分強度 | Barnes 2013：沒有哪一種強度明顯最好 | 不衝突。結論是「在上坡做」比「做哪一種」重要 |

## 4. 提案

原則：不新增課型，全部用現有的雙軌階梯、20 % 強度預算、48 小時規則和護欄。只改「專項期裡兩軌的比例」「專項期的課走不走階梯」「長跑裡的專項內容」。以下的週數切法和比例都是**推估**，來源只支持方向。

專項期切成兩段：

- **前段（支持）**：賽前第 10–7 週。課和基礎期後段相同，繼續爬階梯。
- **後段（專項）**：賽前第 6–3 週。兩軌比例往比賽強度那一軌偏，長跑裡加比賽強度。

專項期長度不同時：8 週是 4＋4；6 週是 2＋4；4 週只有後段。後段固定 4 週，因為 app 的長天進度、賽事模擬、B2B 都集中在賽前第 6–3 週。

### 4.1 越野、百岳

先依賽事分兩類。分界建議用**預估完賽時間 4 小時**（推估；UA 只分「短」和「長」，沒有給數字）。

| | 短（< 4 小時、VK） | 長（≥ 4 小時、超馬、百岳多日） |
|---|---|---|
| 比賽強度 | 三區到四區 | 輕鬆跑上限附近，爬坡偶爾到三區 |
| 來源 | UA：一堂三區＋一堂四區；Ehrström 2018：VO2max 仍然相關 | Koop：最後 6–8 週低強度和穩態；UA：B2B ＋一堂三區或四區 |

**質量課**

| 階段 | 短 | 長 |
|---|---|---|
| 前段 | 一週一堂時三區:五區 = 1:1；兩堂時各一 | 三區:五區 = 2:1（和現在一樣） |
| 後段 | 同前段 | **只排三區**；五區不排，或每 3 週一次維持（要你決定） |
| 三區的課 | 階梯目前那一階的上坡版 | 階梯目前那一階的上坡版，坡度選像比賽的坡；達標後照階梯往 2×20 分、連續 30 分走 |
| 五區的課 | **階梯目前那一階的上坡版**，取代固定的 5×4 分 | 前段同左 |

- 上坡版在範本庫裡已經有（`interval-prescription.md` 的 t1c、t2d、t3c、v1c–v3d）。
- 強度目標和現在的階梯相同。坡度 > 8 % 時功率目標是推估，改看心率和 VAM（`vo2max-gate-and-trail-metric.md` §2.4）。

**長天和其他專項課**：不改。長天進度、長爬坡反覆、技術地形、B2B、賽事模擬照現在的排法。

**下坡**：Koop 說賽前 4–6 週開始累積一點下坡量就夠。app 的長爬坡反覆已經要求「下坡用跑的」，長天也照賽事的每公里下降排路線。不用加新課。

**減量期**：長賽事固定用三區的節奏 2×8 分，不排 4×3 分。理由：減量期保留的應該是比賽強度，長賽事的比賽強度不到五區。短賽事照現在的兩軌結果。

### 4.2 路跑

| 賽距 | 比賽強度（對 CP，推估） | 前段 | 後段 |
|---|---|---|---|
| ≤ 10 km | 5 km ≈ 五區；10 km ≈ 閾值到略高 | 三區:五區 = 1:1，走階梯 | 5 km：以五區為主（2:1）；10 km：1:1，三區那堂用巡航偏上緣（T+ 3×7 分 @ 97–100 % CP） |
| 半馬 | 閾值略低 | 2:1，走階梯 | 只排三區為主（3:1）；三區那堂在「階梯的長節奏」和「T+」之間輪替 |
| 馬拉松 | 閾值以下（MP） | 2:1，走階梯 | 三區為主（3:1）；專項內容主要放在長跑的 MP 段 |

- 「依賽距決定比例」的方向有 Haugen 2022 和 Casado 2022 支持（馬拉松選手接近比賽時中強度增加，短距離是高強度增加）。表裡的比例數字是推估。
- 比賽強度對 CP 的位置是推估。馬拉松配速大約是閾值速度的 0.97–0.98（`coach-schools-zones-periodization.md` Finding 2，受過訓練的人）；休閒跑者差很多。

**三區課改回走階梯**。現在專項期固定 2×15 分，等於把階梯停掉。改成階梯目前那一階，只是指定平路。

**馬拉松和半馬的長跑：比賽配速段逐週加長。** 現在第一週就是長跑時間的 40 %。

| 賽前第幾週 | 10 | 9 | 8 | 7 | 6 | 5 | 4 | 3 |
|---|---|---|---|---|---|---|---|---|
| 現在：MP 段占長跑 | 40 % | 40 % | 40 % | 40 % | 40 % | 40 % | 40 % | 40 % |
| 建議：MP 段占長跑 | 20 % | 25 % | 30 % | 輕鬆 | 35 % | 輕鬆 | 40 % | 輕鬆 |

- 依據：Pfitzinger 的 MP 段是逐次加長（8 → 10 → 14 英里，未驗證）；Cairess 個案是最長連續 MP 段 3 → 6 → 10 km（已驗證，菁英）；Haugen 2022：比賽配速的量隨比賽接近而增加。
- 隔週排輕鬆長跑：Canova 的專項課每堂很重、不是每週做；Pfitzinger 的計畫裡 MP 長跑也只有幾次。哪幾週排是推估，我讓它和 app 現有長跑進度的「退一步」週（賽前第 5、3 週）對齊。
- 上限：單堂 MP ≤ min(110 分, 週跑量的 20 %)（Daniels 第 3 版筆記）。現在的上限是 75 分，比 Daniels 緊，維持。
- 最後一次 MP 長跑在賽前第 4 週，和賽事模擬同一段時間。

**≤ 10 km 的長跑**：不加比賽配速段，維持輕鬆。

### 4.3 和現有規則的關係

| 現有規則 | 影響 |
|---|---|
| 20 % 強度預算 | 不變。後段只排一軌時，那一軌可以用到整個預算，但三區仍然受「≤ 週時數 10 %」限制 |
| 48 小時 | 不變 |
| CTL 增幅、週量護欄 | 不變。MP 段變短的那幾週 TSS 會比現在低一點 |
| 階梯達標才進階 | 不變。專項期的課改回走階梯之後，專項期做的課也算進階梯 |
| 五區的維持規則（低強度時間掉太多會暫停） | 不變。長賽事後段不排五區，五區軌只是沒有排課，不算上鎖 |
| 3:1 恢復週 | 不變。Bompa 建議專項期改 2:1，那是另一題（`bompa-periodization-strength.md` §4.5） |
| 兩軌比例 `track_ratio` | 從「只看賽事類型」改成「賽事類型 × 前段／後段」 |

### 4.4 單上點名的問題：5×4 分

**是，比 repo 文件建議的重，而且有三個問題。**

| 項目 | 現在的 5×4 分 | repo 文件的建議 | 差別 |
|---|---|---|---|
| 區內時間 | 20 分 | 五區單堂 10–16 分（`interval-prescription.md` §A4.2）；爬坡重複 4–6×3–4 分（`vo2max-gate-and-trail-metric.md` §2.4(b)） | 超過五區的單堂上限；在爬坡重複建議範圍的上半 |
| 強度 | 101–106 % CP | 4 分趟 95–106 % CP | 只用上半段 |
| 坡度 | 6–10 % | 3–8 %（功率只驗證到 8 %） | 超過功率有驗證的範圍 |
| 和階梯的關係 | 不看使用者在哪一階 | — | 階梯才到 5×2 分的人，一進專項期就做兩倍的量 |
| 對賽事的專項性 | 所有越野賽事都排 | Koop：超馬的 VO2max 最不專項；UA 長賽事：三區或四區二選一 | 長賽事的專項期不該以它為代表課 |

**該換。** 換法：

1. 五區那堂改成「五區階梯目前那一階的上坡版」。量自動落在 10–16 分，也和使用者的進度接得上。
2. 長賽事的後段以上坡三區為主（§4.1）。
3. 坡度文字改成「選像比賽的坡；> 8 % 時看心率和 VAM，不看功率」。

## 5. 要你決定的事

1. **長距離越野的後段要不要完全不排五區**，還是每 3 週留一堂維持。我建議留維持（每 3 週一次），理由是 UA 長賽事仍然允許「三區或四區」，而且完全不排會讓五區階梯停 4 週再加 2 週減量。
2. **短／長越野的分界**用預估完賽時間 4 小時可以嗎。這個數字是推估。
3. **5×4 分換成階梯的上坡版**，同意嗎。
4. **路跑專項期的 2×15 分改回走階梯**，同意嗎。
5. **馬拉松長跑的 MP 段改成逐週加長、隔週排**，同意嗎。表裡的百分比是推估，可以調。

## 6. 限制

- 各學派的內容，只有 Koop 和 UA 讀到作者本人的文章。Canova 是演講逐字稿和別人的整理；Pfitzinger 和 Daniels 只有二手。
- 同儕審查的來源大多是世界級或訓練有素的跑者，而且是描述性的。對休閒跑者、越野、超馬，沒有介入研究。
- §4 的週數切法、比例、MP 段的百分比都是推估。來源只支持方向（越接近比賽越專項、依賽距不同）。
- 比賽強度對應到 CP 的位置是推估，休閒跑者差異很大。
- 程式的現況是讀程式碼得到的，沒有執行。專項期固定課是否計入階梯，沒有查到底。
- `coach-schools-zones-periodization.md` 把〈How Speedwork Improves Ultrarunning Performance〉（參考文獻 113）當成 Koop 的說法；這篇的作者是 CTS 教練 Addison Smith。Koop 本人在另一篇文章講了同樣的順序，所以結論不受影響。我沒有改那份文件。

## 參考

- Koop J. 3 Steps for Creating Your Ultrarunning Long-Range Plan. https://trainright.com/ultrarunning-long-range-plan-3-steps/
- Smith A. How Speedwork Improves Ultrarunning Performance. https://trainright.com/how-speedwork-improves-ultrarunning-performance/
- Koop J. Decoding Interval Workouts for Ultramarathon Training. https://trainright.com/decoding-interval-workouts-for-ultramarathon-training/
- Koop J. Top 4 Reasons Ultrarunners Should Do More Uphill Running Intervals. https://trainright.com/benefits-of-uphill-running-intervals-ultrarunners/
- Koop J. How to Train for Mountains When You Live in a Flat Area. https://trainright.com/train-for-mountainous-ultramarathon-live-in-flat-area/
- Koop J. The Hierarchy of Ultramarathon Training Needs. https://trainright.com/hierarchy-ultramarathon-training-needs-jason-koop/
- Koop J. Block or Mixed-Intensity Periodization for Ultrarunners. https://trainright.com/block-mixed-intensity-periodization-ultrarunning/
- Uphill Athlete. How to Train for Trail Running. https://uphillathlete.com/trail-running/training-for-trail-running/
- Davis J. The Keys to Marathon Training（Canova 2017 演講）. https://runningwritings.com/2023/07/renato-canova-marathon-training-lecture.html
- Davis J. Review of Marathon Training – A Scientific Approach. https://runningwritings.com/2023/06/canova-marathon-book.html
- Davis J. Canova-style percentage-based training. https://runningwritings.com/2023/12/percentage-based-training.html
- Davis J. Analysis of Emile Cairess' training. https://runningwritings.com/2024/05/renato-canova-marathon-training-emile-cairess.html
- Gaudette J. Canova Special Block Training. https://runnersconnect.net/special-block-training/
- Daniels' Running Formula 第 3 版讀書筆記. https://run.wxm.be/books/jack-daniels-running-formula.html
- Haugen T, Sandbakk Ø, Seiler S, Tønnessen E. *Sports Med Open* 2022. DOI 10.1186/s40798-022-00438-7
- Casado A, González-Mohíno F, González-Ravé JM, Foster C. *IJSPP* 2022;17:820. DOI 10.1123/ijspp.2021-0435
- Kenneally M, Casado A, Santos-Concejero J. *IJSPP* 2018. DOI 10.1123/ijspp.2017-0327
- Kenneally M, Casado A, Gomez-Ezeiza J, Santos-Concejero J. *Eur J Sport Sci* 2021. DOI 10.1080/17461391.2020.1773934
- Barnes KR, Hopkins WG, McGuigan MR, Kilding AE. *IJSPP* 2013;8:639. DOI 10.1123/ijspp.8.6.639
- Alemu Y, Tadesse T, Birhanu Z. *Sci Rep* 2025. DOI 10.1038/s41598-025-08275-w
- Ehrström S et al. *MSSE* 2018. DOI 10.1249/mss.0000000000001467
- Aubry A et al. *MSSE* 2014;46:1769. DOI 10.1249/MSS.0000000000000301
- Bosquet L et al. *MSSE* 2007. DOI 10.1249/mss.0b013e31806010e0
- 既有文件：`coach-schools-zones-periodization.md`（Finding 4、8，模式三，R2、R6）、`vo2max-gate-and-trail-metric.md` §2.3–2.5、`interval-prescription.md`、`back-to-back-and-long-day.md`、`bompa-periodization-strength.md`、`docs/spec/plan-auto.spec.md`、`docs/spec/overview.spec.md`。
