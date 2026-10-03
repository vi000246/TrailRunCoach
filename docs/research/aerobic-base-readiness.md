# 有氧基礎夠了沒？——開始 VO2max／高強度間歇的判斷依據

> 研究與設計文件（2026-09-30），不改程式碼。對象：越野跑＋百岳的業餘選手；資料有 FIT 心率、功率、配速、爬升、步頻、Stryd，**沒有 HRV、沒有血乳酸**。
>
> 標記方式：
> - **你的筆記**：notes（`notes/300 Sport/...`），附路徑與行號。只引用和本題相關的句子。
> - **已驗證**：本次讀過原文（官方網站文章、論文摘要／全文），附 URL 或 DOI。
> - **未找到來源**：找不到一手資料，不從記憶補。
> - **自訂**：我們自己定的數字或規則，沒有外部依據。
>
> 查證限制：這次 WebSearch 額度用完，後半段改用 WebFetch 直接讀頁面，論文用 Europe PMC / PubMed 讀摘要。WebFetch 會先經過一個小模型摘錄，所以引號裡的句子是它回報的「原文」。關鍵的四項（UA 10% 差距、UA 從 Zone 3 開始、Friel 飄移 < 5% 與時間、Seiler 80/20 以課表次數計）已經再抓一次原文核對過。

---

## 0. 結論先講

1. **現在的規則「連續 3 次輕鬆路跑飄移 < 5%」有三個問題：**
   - **來源對不上。** 它標的是 `SRC_UA`，但 Uphill Athlete 並不用飄移判斷「可以加強度了沒」。UA 用飄移測試來**找出 AeT**，判斷準備好了沒是看 **AeT 和 AnT 的差距 ≤ 10%**。Friel 的 < 5% 講的是在 **AeT 強度**跑 1–2 小時。「連續 3 次」則完全沒有來源。
   - **量錯了東西。** 篩選條件是「平均心率 ≤ AeT+3」，所以遠低於 AeT 的慢跑也算進去。這種跑步飄移本來就小，結果量到的是「有沒有跑得夠慢」，而不是「有氧基礎夠不夠」。另外 AeT 多半是 0.89×LTHR 的估計值（`Dataset.aethr`），所以這道門檻其實默默依賴一個沒測過的 AeT。
   - **比你自己筆記裡的標準嚴。** 你筆記的徐國峰標準是「E 配速 90 分鐘心率飄移 < 10% 就可以練間歇」，5% 是「國家級」。App 用 40 分鐘、5%，時間更短、門檻更嚴。
2. **有 AeT 實測時**：預設用 UA 差距法（LTHR/AeT − 1 ≤ 10%），再加 Friel 飄移法作為第二條解鎖路徑。
3. **沒有 AeT 實測時**（目前的狀況）：不設門檻，照 Seiler / Koop / Palladino 的做法，基礎期每週最多 1 次間歇。用 app 已經有的護欄（強度分配、CTL ramp、週增量、3:1、TSB、48 小時間隔）決定「這週能不能排」，劑量從 Palladino 的 1 分鐘 fartlek 開始，6 週內漸進到 4×4。
4. **課表偏好**加 `plan.prefs.quality_gate`，選項：`auto`（預設）／`ua_gap`／`friel_drift`／`xu_drift`／`plateau`／`weeks`／`none`。
5. **AeT 測試不一定要 60 分鐘。** UA 明講 40–60 分鐘都可以，不建議少於 40 分鐘；Evoke 用 60 分鐘。兩種長度都沒有同儕審查的驗證。建議這位選手做 **15 分鐘暖身＋45–60 分鐘固定功率／配速，平路環線或跑步機 2–3%，不要在山路做**，用 UA 的 3.5% / 5% 兩段判讀。結果經「套用這次的 AeT」寫進 plan 的 aethr。

---

## 1. 你的筆記怎麼說

### 1.1 徐國峰：90 分鐘 E 配速心率飄移 < 10%（最明確的一條）

`notes/300 Sport/60 🏃 有氧訓練/跑者都該懂的跑步數據，讀書心得.md`

- L58–L61：「如何知道自己的有氧體能基礎已經足夠了? 測試在區間1的情況跑90分鐘，看心率飄移的幅度」「在全程平坦的路段，Ｅ配速的心率若能在90分鐘內飄移10%以下就算具備『優秀的有氧體能』（5%以內是國家級的有氧體能）」
- L62–L66，測試方式：
  1. 氣溫 25 °C 以下
  2. 知道自己的 E 配速
  3. 第 10 分鐘的心率記為 A
  4. 以 E 配速持續跑 90 分鐘，補給每次停不超過 30 秒；90 分鐘時的心率記為 B
  5. 飄移 = (B − A) ÷ A × 100%
- L67：「只要心率飄移能維持在10%以內（等級8），『有氧體能的基礎』就算夠扎實了。」
- L75–L77：「什麼時候要開始拉強度練間歇，正是上述90分鐘心率飄移檢測的主要目的。只要『E配速90分鐘的心率飄移百分比』可以下降到10%以內，就可以開始練高強度間歇」，而且之後要用新的 E 配速「重新打底」，反覆循環。
- L12–L13，另一個訊號：「過了一段時間若錶上的最大攝氧量不再提升時，就表示你的有氧體能基礎已經建立得差不多了，此時就可以開始提高到會喘的強度」。
- `70 ⏳ 周期化訓練/400 爆發力、敏捷性、專項耐力周期.md` L5 講的是同一條：「確定在10%以下之後就可以開始練間歇」。

這裡要注意，徐國峰的算法和 app 不同：
- 徐國峰是**固定配速下第 10 分鐘對第 90 分鐘的心率**。
- App 的 `drift_of` 是**前半對後半的速度／心率比**（Pa:HR）。

同一次跑步兩種算法的數字不一樣，不能直接套同一個門檻。

### 1.2 Pw:HR / Pa:HR < 5%（你自己寫的解讀）

`65 ⚡ 功率訓練/名詞解術與分析/心率飄移 pwhr.md`
- L7：「前後半段的 EF 變化很小（少於 5%），這可能表示你的有氧耐力正在改善。」
- L9：「意思是當一段>20min的跑步的pwhr或pahr小於5%，就能增加強度了」

這條的時間下限（20 分鐘）比任何外部來源都短，見 §2。

`65 ⚡ 功率訓練/名詞解術與分析/效率指標 跟效率因子.md`
- L53：「跑前與跑後 EF 變化小於 5%，代表有耐力支撐」
- L61 起記了 Coggan 對 Pw:HR 和 EF 的批評：
  - 受氣溫、水分、疲勞、睡眠影響太大
  - 「缺乏生理機制基礎支持」

### 1.3 Palladino：基礎期分三段，高強度放在後段

`70 ⏳ 周期化訓練/palladino基礎期訓練.md`（Palladino, *General Prep / Base Building*, 2023-12-12，筆記 L2 有 Google Doc 連結）

- L10–L13：「palladino將基礎期分成三期：前期都跑LT1強度、中期加入LT2 FTP強度 約佔85%、後期加入高強度間歇」
- 早期（L42–L53）：
  - 輕鬆跑 ≤ 80% CP
  - 末期可以加「4‑6 × 1 分鐘，強度約 98‑101% FTP/CP，搭配 2 分鐘輕鬆跑恢復」
  - 有經驗的人 1–3 週，初學者 3–6 週
  - CTL 每週 +1–3 TSS/day
- 中期（L61–L77）：
  - 約 15% 時間在 94–101% CP
  - 「< 1% 的時間為短坡 sprint（8‑15 秒）訓練，通常每週一次，4‑8 次」
  - 「中期避免 Zone 4‑6 的高強度訓練」
  - 4–6 週，最多 8–10 週
- 恢復週（L86）：「減少跑量並保留一兩次 98‑101% FTP/CP 的 Fartlek」
- 後期（L94–L97）：
  - 「每週加入一次 Zone 4–5 的 HIIT 過渡訓練」
  - 例：2×8 分 @ 98–101% ＋ 2×4 分 @ 102–104% ＋ 1×2 分 @ 105–110%，或 4–6×1 分 @ 105–110%
  - 1–3 週
- **Palladino 用週數和階段安排，沒有用任何指標設門檻。**
- `65 ⚡ 功率訓練/研討會整理/研討會Advanced Running with Power by palladino.md` L705–L709：要判斷兩小時長跑有沒有 drift，「要確保整場跑步是對稱的，包括坡度、配速努力程度、氣溫」，天氣變熱或快速結尾都會讓它看起來像脫鉤。

### 1.4 WKO5 研討會（Tim Cusick）：看 LT1 和 FTP 的差距

`70 ⏳ 周期化訓練/wko5研討會 基礎期.md`
- L567–L570：「在基礎期與適應期時，我會觀察 LT1 的變化……我希望 LT1 與 LT2（或 FTP）之間的落差可以縮小。」
- L832–L834，三個階段：
  1. 「第一階段，我希望縮小 LT1 與 FTP 之間的差距」
  2. 「第二階段，我希望縮小 FTP 與 VO2max 之間的差距」
  3. 第三階段提升 VO2max
- L863–L875：追蹤「在估算的 LT1 心率下的平均功率」和它與 LT2 的差距。LT1 可以用下列方式估，但「很難正式化」：
  - 70% MFTP
  - 65–70% 最大心率
  - 55% VO2max 功率
- L879–L888：這些是「軟性資料」，要看幾個 microcycle 的趨勢。

`wko5研討會 強化期.md`
- L500–L501：「在早期基礎期，我觀察的是 LT1 的進步」
- L508–L509：「到了中後期……我的目標是讓 FTP 提升到 VO₂max 的 84～85%」

`研討會Building FTP, TTE, and Stamina with WKO5.md` L324–L327：「第一階段：4～8 週（有些人會長達 12 週），建立有氧基礎」，接著 4 週 intensive aerobic，再 4 週 VO2max。

→ 這是你筆記裡**第二種差距法**，和 UA 的 AeT/AnT 差距同一個概念。

### 1.5 江晏慶與 Daniels：用週期長度安排

`70 ⏳ 周期化訓練/越野跑周期化訓練(晏慶、K天王、丹尼爾).md`
- 江晏慶：
  - L11：「共四個月，2.5個月的基礎前期跟1.5個月的基礎後期」
  - L14：基礎前期「在平地練習馬拉松強度(Zone 2) 3~4小時左右的長跑」
  - L17–L19：基礎後期「著重在zone2 zone3的訓練」
  - L25–L26：強化期（約一個月）才是「間歇訓練」
  - L34：「對於超長距離賽事而言，太多的速度訓練會失焦」
- Daniels：
  - L67–L73：第一周期「E日佔100%，一周一次LSD」「這個周期是最長的」
- `60 🏃 有氧訓練/丹尼爾的跑步方程式筆記 還有課表.md`：
  - L97：「ST代表15～20秒的快步跑（不是衝刺）」，第一周期的課表裡就有
  - L127–L128：「每週期只以一種主要的訓練強度為主，但也要再添加前幾個週期所強調的強度」

→ **江晏慶和 Daniels 都沒有「指標門檻」，都是依週數推進。**

### 1.6 VO2max 的時機、極化

- `65 ⚡ 功率訓練/練VO2max的時機.md`
  - L11–L13：LT 練到紮實、「訓練會出現『停滯期』，此時就用更高強度的VO2MAX刺激」
  - L18–L19：「通常是比賽前一個多月（4-6週）才會有所安排」
  - L37–L42：FTP/VO2max 比例表，80–85% 以上建議加 Z5
  - 這篇是自行車部落格的轉述，來源是 Hunter Allen blog。
- `70 ⏳ 周期化訓練/極化訓練的80-20比例 以時間還是課表次數計算.md` L18：80/20 是依「課表次數」計算，依時間約 90/10。
  - 筆記 L37 引用的 Seiler 那句英文原話**沒有找到出處**。
  - 但 Seiler & Tønnessen 2009 的數據支持這個結論，見 §2.4。
- `70 ⏳ 周期化訓練/哪一套周期化訓練比較好.md` L17–L22：金字塔轉極化（PYR→POL）進步最多，也就是 Filipas 2022，見 §2.8。

### 1.7 你的筆記歸納起來

| 類型 | 準則 | 出處 |
|---|---|---|
| 飄移法 | 平地、< 25 °C、E 配速 90 分鐘，(HR90 − HR10)/HR10 **< 10%** 就可以練間歇（< 5% = 國家級） | 徐國峰（讀書心得 L58–L77） |
| 飄移法 | Pa:HR / Pw:HR < 5%（> 20 分鐘穩定段）→ 可以加強度 | 你的解讀（心率飄移 pwhr L9） |
| 差距法 | LT1 往右移、LT1 和 FTP 的差距縮小 → 往下一階段 | Cusick（wko5 研討會 基礎期 L567–L570、L832） |
| 停滯法 | 錶上的 VO2max 不再上升 → 基礎差不多了 | 徐國峰（讀書心得 L12–L13） |
| 週數法 | 基礎 4 個月（江晏慶）；GP 早 1–3 週＋中 4–6 週，後期才加 HIIT（Palladino）；第一階段 4–8 週（Cusick）；Daniels 第一周期最長 | 見 §1.3–§1.5 |
| 基礎期可做的快的東西 | 8–15 秒坡衝刺每週一次（Palladino）；15–20 秒 ST 快步跑（Daniels） | palladino基礎期 L66；丹尼爾 L97 |

---

## 2. 文獻與教練來源（已驗證）

### 2.1 Uphill Athlete（House / Johnston）

- **差距法（ADS）**，https://uphillathlete.com/aerobic-training/when-to-add-intensity-training/
  - "If the spread between your AeT and AnT heart rates is greater than 10 percent, you have Aerobic Deficiency."
  - 算法是 **AnT/AeT − 1**。原文例子："150 ÷ 128 = 1.17. Your AnT is 17 percent greater than your AeT."
  - 差距 ≤ 10% 之後怎麼加：
    - "Start with Zone 3"
    - "Start with total high-intensity work time equal to roughly 5 percent of your weekly aerobic volume"
    - 每週 1 次，能輕鬆應付再加第二次
  - 頁面沒有給差距要多久才會縮小。
  - 頁面有兩種寫法：zones 頁寫「AeT 比 AnT 低 10% 以上」，也就是 1 − AeT/AnT。兩者只在 AnT/AeT 1.10–1.111 之間判定不同（見 `docs/research/uphill-athlete-mountain-metrics.md` §2(b)）。我們用上面有數字例子的那一種。
  - 10% 門檻本身沒有同儕審查驗證，是教練經驗法則。
- **越野跑的強度順序**，https://uphillathlete.com/trail-running/training-for-trail-running/
  - "Athletes new to high-intensity work should start with Zone 3 workouts and slowly incorporate Zone 4 work, due to their higher strain on soft tissues."
  - 坡衝刺是 power training："repetitions of very short duration (8 to 10 seconds) at the maximum intensity… Utilize a hill that is at least 20 percent in grade… start with 6 to 8 repetitions with a 2-to-3-minute rest"
  - 專項期寫的是 "retain the hill sprints"，表示基礎期就有坡衝刺。但**「ADS 期間也可以做坡衝刺」這句明文沒找到**。
  - https://uphillathlete.com/aerobic-training/what-norwegian-endurance-science-teaches-us-about-building-aerobic-base/ 把坡衝刺歸在 "neuromuscular development"。
- **鼻呼吸／講話測試**，https://uphillathlete.com/aerobic-training/aerobic-anaerobic-threshold-self-assessment/
  - 對受過訓練的人，鼻呼吸上限和 AeT 對得很好。
  - 但 "once we began to work with untrained and less well-trained climbers, we discovered that this nice, simple test no longer worked"
  - 現在只當作「輕鬆跑是不是真的輕鬆」的每日檢查，要找 AeT 請做心率飄移測試。
- **沒有實測的人**，https://uphillathlete.com/aerobic-training/should-you-test/
  - "if you can speak in full sentences, you're below LT1"
  - "the training itself is the test"
  - 這頁沒有說沒測過的人什麼時候可以加強度。
- **心率飄移測試**：見 §6.2。

### 2.2 Friel：Aerobic decoupling < 5%

https://www.trainingpeaks.com/blog/aerobic-endurance-and-decoupling/（Friel，2008 初版，2026-06-03 更新；本次親自核對）

- 分級：
  - "< 5% decoupling: Strong aerobic endurance at that intensity"
  - "5–10%: Moderate endurance limitations or fatigue"
  - "> 10%: The effort was likely above aerobic threshold or the athlete lacks the endurance to sustain it"
- 準備好的判斷："When an athlete can hold the target duration with less than 5% decoupling… the athlete has likely built the aerobic support needed to move into the build phase"
- 時間：
  - 跑步是 "one to two hours at aerobic threshold"
  - 自行車 2–4 小時
  - 不含暖身和緩和
- 頻率："once or twice per week during the Base phase"
- 前提：強度要**在 AeT**，不是隨便輕鬆跑。沒有「連續幾次」的規定。
- 出自哪本書：未找到來源（只核對了 TrainingPeaks 文章）。

### 2.3 Maffetone：停滯是警訊，不是解鎖訊號

- https://philmaffetone.com/180-formula/：180 − 年齡，再依狀況 −10／−5／0／+5。
- https://philmaffetone.com/maf-test/："I recommend doing the test every month."
- https://philmaffetone.com/method/step-5/
  - "If your speed plateaus (stops increasing) for two or three tests, or decreases, this is a warning that aerobic function may be compromised"
  - 有氧功能不好的人 "often takes three to six months of strict aerobic exercise"
  - MAF 測試持續進步時才加肌力／無氧（這句摘錄的可信度較低）
  - 無氧和有氧比約 80/20
- https://philmaffetone.com/hit-helps-hurts/：HIIT "one to three sessions (more often one to two) per week"

→ **「MAF 停滯就往下走」和 Maffetone 本人說的相反**，不能用他的名字做成選項。徐國峰的「錶上 VO2max 不再上升」才是「停滯＝基礎差不多」的來源，見 §4 的 `plateau`。

### 2.4 Seiler／極化：不設門檻

- Seiler & Kjerland 2006, *Scand J Med Sci Sports* 16:49–56, DOI 10.1111/j.1600-0838.2004.00418.x
  - 依心率分析的課表比例 "75+/-3%, zone 1; 8+/-3%, zone 2; 17+/-4%, zone 3"
  - 對象：青年越野滑雪選手，318 堂課
- Seiler & Tønnessen 2009, *Sportscience* 13, https://www.sportsci.org/2009/ss.htm
  - "About 80 % of training sessions are performed completely or predominantly at intensities under the first ventilatory turn point"
  - 同一批選手依時間算："91 % of all training time was spent at a heart rate below VT1… ~6 % between VT1 and VT2, and only 2.6 %… above VT2"
  - 間歇頻率："likely to dedicate 1-3 sessions weekly"
  - "3-4 sessions per week" 會造成 overreaching
- Seiler 2010, *IJSPP* 5:276–91, DOI 10.1123/ijspp.5.3.276，摘要："careful application of high-intensity training incorporated throughout the training cycle"。**摘要裡沒有高強度之前要先達標的門檻。**
- Seiler et al. 2013, *Scand J Med Sci Sports* 23:74–83, DOI 10.1111/j.1600-0838.2011.01351.x
  - 對象：35 名受過訓練的休閒自行車手，7 週，每週 2 次間歇
  - 4×8 分（約 90% HRmax）的進步 11.4%，大於 4×4 和 4×16
  - "Accumulating 32 min of work at 90% HR max induces greater adaptive gains than accumulating 16 min of work at ∼95% HR max"
- Helgerud et al. 2007, *MSSE* 39:665–71, DOI 10.1249/mss.0b013e3180304570
  - 4×4 分 @ 90–95% HRmax，恢復 3 分 @ 70%
  - 每週 3 天、8 週，VO2max +7.2%
  - LSD 組和乳酸閾值組沒有顯著進步

### 2.5 Koop／CTS

- Koop 本人說 VO2 間歇放在計畫前面：**未找到來源**。
  - CTS 教練 Addison Smith 說 "the highest intensity training (RunningIntervals) would be done at the beginning of the training plan because it is the least race-specific"，而且 "3-5 week blocks"：https://trainright.com/how-speedwork-improves-ultrarunning-performance/
- 課表範例（https://trainright.com/decoding-ultramarathon-interval-workouts/）
  - "90min ER with 6x3min RI, 3min recovery"
  - 每趟 2–4 分，總量 12–24 分
- 地形：Koop 建議 "uphill if possible… to reach 90% of your VO2max… more consistently"（https://trainright.com/key-workouts-every-ultrarunner-should-do/）
- 門檻：沒有數字門檻。強度課約佔 "20% of training sessions and only about 10% of total training hours"，先拿到基礎耐力的大進步（https://trainright.com/hierarchy-ultramarathon-training-needs-jason-koop/）。
- Koop 對 strides／坡衝刺的看法：未找到來源（書沒有取得）。

### 2.6 Daniels、Lydiard

- Daniels 四個周期的長度、第一周期轉第二周期的準則：**未找到來源**（Human Kinetics 頁面沒有這段）。你的筆記（§1.5）記有第一周期全是 E、有 ST 快步跑、「最長」。
- Lydiard 基礎期長度、坡道期長度：**未找到來源**（lydiardfoundation.org 沒有給數字）。

### 2.7 生理指標

| 指標 | 數字 | 來源 | 能不能當門檻 |
|---|---|---|---|
| LT1/LT2 差距 | 沒有論文直接給 VT1 佔 VT2 的比例、受訓前後比較 | 未找到來源 | 間接數據：訓練過的自行車手 VT1 71–78% HRmax、VT2 87–92% HRmax（Pallarés 2016, *PLoS One*, DOI 10.1371/journal.pone.0163389），中點相除約 83%。這是我們算的，不是論文的 |
| LT1 心率佔 HRmax | VT1 71–78% HRmax | Pallarés 2016（同上） | 「Seiler：VT1 ≈ 77–79% HRmax」未找到來源 |
| LT1 心率佔 LTHR | Friel 跑步 Zone 2 = 85–89% LTHR | https://www.trainingpeaks.com/learn/articles/joe-friel-s-quick-guide-to-setting-zones | 這是分區慣例。App 的 0.89×LTHR 就是 Friel Zone 2 的上限，**不是測出來的 LT1** |
| VT1 佔 VO2max | 61–86%，平均 70 ± 6% | Rogers et al. 2021, *Front Physiol* 11:596567, DOI 10.3389/fphys.2020.596567 | 受訓者乳酸閾值 79%（男）/ 73%（女）VO2max，未受訓 66.5% / 58.9%（*MSSE* 1987, DOI 10.1249/00005768-198708000-00006）。需要氣體分析 |
| DFA-α1 | α1 = 0.75 ≈ VT1。HRVT 和 VT1 的 r = 0.99（VO2）/ 0.97（HR），心率 154 vs 152 bpm | Rogers 2021（同上），n = 15、ECG。摘要原文："DFA a1 reaching a value of 0.75 (HRVT)" | α1 = 0.5 ≈ VT2（Rogers 2021, *J Funct Morphol Kinesiol* 6:38, DOI 10.3390/jfmk6020038）。3–6% 漏拍會有偏差，但 HRVT 仍在 1 bpm 內（Rogers 2021, *Sensors* 21:821, DOI 10.3390/s21030821）。反例：*Physiol Rep* 2026, DOI 10.14814/phy2.70777，自行車 HRVT1 和 LT1/VT1 一致性差 |
| DFA-α1 在 COROS 上 | "COROS does not support HRV data from external accessories" | https://support.coros.com/hc/en-us/articles/360058469472 | **不能做**：活動 FIT 沒有 RR 間期（官方沒說有，視為不支援）。替代方式：Polar H10＋FatMaxxer（https://github.com/IanPeake/FatMaxxer）另外錄 |
| EF 停滯 | Friel：有氧進步時 EF "will rise over the course of a few weeks" | https://www.trainingpeaks.com/blog/efficiency-factor-and-decoupling/ | 「EF 停滯＝可以加間歇」未找到來源 |
| FATmax | 64 ± 4% VO2max、74 ± 3% HRmax（Achten 2002, *MSSE*, DOI 10.1097/00005768-200201000-00015） | FATmax 和乳酸剛上升點（LIAB）沒有差異：63% vs 61% VO2max（Achten & Jeukendrup 2004, *IJSM*, DOI 10.1055/s-2003-45231） | 需要氣體分析。當準備好的指標：未找到來源 |
| 飄移 5% 的生理驗證 | 馬拉松 82,303 人：decoupling 平均 1.16，出現在 25.2 km；加進去後預測誤差 6.45% → 5.16%（Smyth 2022, *Sports Med*, DOI 10.1007/s40279-022-01680-5） | 他們的分級是 onset > 1.025、低 < 1.1 | **5% 當 AeT 或體能標記：沒有同儕審查驗證**，是 Friel / UA 的教練規則 |

### 2.8 先打基礎再練間歇，研究怎麼說

- **沒有找到在跑者身上直接比較「先純基礎再間歇」和「一開始就混合」的 RCT。**
- 強度分配（TID）的統合分析：
  - Stöggl & Sperlich 2014, *Front Physiol* 5:33, DOI 10.3389/fphys.2014.00033：48 名訓練有素的選手，9 週，極化組 VO2peak +11.7%，最多。
  - Rosenblat et al. 2019, *JSCR* 33:3491–3500, DOI 10.1519/JSC.0000000000002618：極化優於閾值訓練，ES = −0.66。只有 3 篇 RCT，對象是 VO2max > 50 的人。
  - Silva Oliveira et al. 2024, *Sports Med* 54:2071–95, DOI 10.1007/s40279-024-02034-z：
    - 17 篇，VO2peak SMD 0.24，只在 < 12 週和高水準選手身上成立
    - 計時賽 SMD −0.01，**沒有差別**
  - Rosenblat et al. 2025, *Sports Med* 55:655–73, DOI 10.1007/s40279-024-02149-3：
    - 極化對金字塔沒有差別
    - "recreational athletes may improve more with a PYR TID"
- 排序：Filipas et al. 2022, *Scand J Med Sci Sports* 32:498–511, DOI 10.1111/sms.14101
  - 60 名訓練有素的跑者，16 週
  - 5 km 進步：PYR→POL −1.5%，POL −1.1%，POL→PYR −0.9%，PYR −0.6%
  - 意思是**先金字塔（中強度多）、後極化（高強度多）**效果最好。高強度從第一週就有，只是比例不同。這就是你筆記 §1.6 那篇。
- Block 週期：
  - Rønnestad 2014, *Scand J Med Sci Sports* 24:34–42, DOI 10.1111/j.1600-0838.2012.01485.x：自行車 4 週，block 組 VO2max +4.6%。
  - Almquist 2022, *Front Physiol* 13:837634, DOI 10.3389/fphys.2022.837634：12 週負荷相同時，block 和傳統**沒有差別**。
- 休閒跑者一開始就有高強度：
  - Muñoz 2014, *IJSPP* 9:265–72, DOI 10.1123/ijspp.2012-0350：30 名休閒跑者，10 週，極化（依時間 77/3/20）10K 進步 5.0%，閾值間 3.6%，差異不顯著。**摘要沒有傷害資料。**
  - Esteve-Lanao 2007, *JSCR* 21:943–9, DOI 10.1519/R-19725.1：次菁英跑者，約 5 個月，Z3 都約 8%，Z1 多的那組進步較多，"provided that the contribution of high-intensity training remains sufficient"。
- **低劑量 VO2max 間歇對休閒越野跑者、登山者的安全性：未找到來源。**

→ 研究**不支持**「一定要先過門檻才能練高強度」。支持的是「大部分時間低強度，高強度少量但一直都有」，以及「中後段才提高高強度比例」。

### 2.9 準則對照總表

| 來源 | 準則 | 數字 | 時間／條件 | 驗證 |
|---|---|---|---|---|
| **UA 差距法** | AnT/AeT − 1 ≤ 10% → 可以加 Zone 3、Zone 4 | 10% | AeT、AnT 都要實測 | 已驗證；教練規則，無同儕審查 |
| UA 加法 | 先 Zone 3，約週有氧量的 5%，每週 1 次 | 5%、1 次/週 | 差距 ≤ 10% 之後 | 已驗證 |
| **Friel 飄移法** | 在 AeT 跑目標時間 decoupling < 5% → 進 build | < 5% | 跑步 1–2 小時、在 AeT、不含暖身 | 已驗證 |
| UA 飄移測試 | 用來**找 AeT**，不是判斷準備好了沒 | < 3.5 / 3.5–5 / > 5% | 40–60 分鐘、平路或跑步機 | 已驗證 |
| 徐國峰 | E 配速 90 分鐘 (HR90 − HR10)/HR10 < 10% → 可以練間歇 | < 10%（< 5% 國家級） | 平地、< 25 °C、補給停 ≤ 30 秒 | 你的筆記（書） |
| Maffetone | MAF 測試每月一次；停滯 2–3 次＝**警訊**；基礎 3–6 個月 | — | — | 已驗證 |
| Seiler | 不設門檻；約 80% 課表低強度（時間約 91%），每週 1–3 次高強度 | 80/20（課表數） | 整個週期都有 | 已驗證 |
| Koop | 沒有指標門檻；VO2 放前面（CTS）；6×3 分上坡 | 12–24 分 | 3–5 週 block | 部分驗證（CTS 教練，不是 Koop 本人） |
| Palladino | 分階段：早期末 4–6×1 分 @ 98–101% CP、中期不做 Z4–6、後期每週 1 次 HIIT | 週數 | 早 1–3（或 3–6）＋中 4–6（最多 8–10）＋後 1–3 週 | 你的筆記（Palladino 原文連結） |
| Cusick（WKO5） | LT1–FTP 差距縮小 → 下一階段；FTP ≈ 84–85% VO2max | — | 第一階段 4–8（最多 12）週 | 你的筆記（研討會） |
| 江晏慶 | 基礎 4 個月（2.5＋1.5），強化期約 1 個月才間歇 | 週數 | — | 你的筆記 |
| Daniels | 第一周期全 E＋ST，最長；依週期推進 | — | — | 你的筆記；書中數字未找到來源 |
| Lydiard | — | — | — | 未找到來源 |
| **App 現在** | 連續 3 次輕鬆路跑 Pa:HR < 5% | 5%、3 次 | ≥ 40 分鐘、平均心率 ≤ AeT+3 | 「3 次」自訂；對 UA 的引用不正確 |

---

## 3. App 能從資料算出什麼

| 指標 | 能算嗎 | 怎麼算（現有函式） | 還需要的測試 |
|---|---|---|---|
| Pa:HR / Pw:HR 飄移 | 能 | `workout_review.drift_of`（前後半速度／心率，跳過前 10 分鐘，會排除有坡、有停頓、功率不穩、> 90% CP 的跑步）；`threshold_estimate.steady_drift`（Pw:HR，≥ 45 分鐘） | 無（日常跑）；要找 AeT 需做一次 AeT 飄移測試（§6） |
| AeT | 半套 | 三個來源：① plan 的 aethr（`plan.threshold_on("aethr")`，實測）；② `Dataset.aethr` 找不到時用 0.89×LTHR（`api/plan.py:70` 標為 `friel_0.89`）；③ `thresholds.estimate()` 用多次穩定跑做回歸，類似 UA 的 Continuous AeT，只是建議值，要按「套用」才寫進 plan | AeT 飄移測試 |
| AnT / LTHR | 能（有測過時） | plan 的 lthr；`thresholds.estimate` 的 `estimate_lthr`；否則用 WKO5 設定（可能是預設 160，`i_data` 會標 BAD） | 30–60 分鐘全力（UA AnT 測試）或 Friel 30 分鐘計時跑 |
| **UA 差距** | 能，但一定要**實測 AeT** | `LTHR / AeT − 1`。用 0.89×LTHR 時差距永遠是 1/0.89 − 1 = **12.4%**，會永遠鎖住，所以沒有實測 AeT 時不能用這一條 | AeT 測試＋LTHR 測試 |
| 徐國峰 90 分鐘飄移 | 能（要新增函式） | 固定配速段，第 10 分鐘心率 vs 第 90 分鐘心率（取 ±1 分鐘平均），沿用 `drift_of` 的平路／停頓／功率不穩檢查 | 平地 90 分鐘 E 配速跑，< 25 °C |
| EF 趨勢／停滯 | 能 | `status.i_efficiency`（路跑、心率 ≤ AeT+3，近 8 週對之前，±2% 算持平，`EF_TREND` 是自訂值） | 無 |
| MAF 配速 | 能（要新增） | 180 − 年齡 ± 調整；每月取一次心率在 MAF ± 2 的平路段配速 | 每月 MAF 測試（Maffetone） |
| 強度分配 | 能 | `status.i_intensity`：4 週心率 < AeT 的時間佔比（`LOW_SHARE_GOOD` 0.75 / `WATCH` 0.65）；跑步功率 < 80% CP 的佔比 | 無 |
| CTL ramp / TSB | 能 | `i_fitness`（`RAMP`）、`i_form`；`week_plan` 的 TSB < −30 → 恢復週、< −20 → 不加量 | 無 |
| 週增量、3:1 | 能 | `i_volume`（`VOLUME_STEP_WATCH` 10%）；`week_plan` 的 `build3` → 恢復週 | 無 |
| 48 小時間隔 | 已經有 | `plan_prefs.place()`：品質課離長跑和其他硬課 ≥ 2 天（排不下時退到 ≥ 1 天） | 無 |
| DFA-α1 | **不能** | COROS 不存 RR 間期 | Polar H10＋外部 app |
| FATmax、VT1 %VO2max、乳酸 LT1 | **不能** | — | 實驗室 |
| 鼻呼吸／講話測試 | 不能自動 | 只能當提示文字 | 自己感覺 |
| 階梯／Conconi 心率拐點找 LT1 | 不建議 | 很多人根本沒有拐點：Vachon 1999 *J Appl Physiol* DOI 10.1152/jappl.1999.87.1.452「只有一半的受試者」有；Carey 2002 60% 沒有拐點（PMC3979002）。拐點比較接近 LT2 / MLSS（Pereira 2016, PMID 26014090） | — |

---

## 4. 建議：換掉「連續 3 次 < 5%」

### 4.1 原則

1. **有實測 AeT 才用 AeT 門檻。** 沒有實測時，AeT 門檻其實是在用 0.89×LTHR 做判斷，沒有意義。
2. **沒有實測時不擋間歇**：照 Seiler 的「整個週期都有少量高強度」、Koop 的「VO2 放前面」、Palladino 的階段劑量。改由既有護欄決定「這週可不可以排」。
3. **坡衝刺、快步跑不算間歇**，所有模式都保留（UA 歸為神經肌肉訓練、Palladino 中期、Daniels ST）。App 已經在基礎期第一次輕鬆跑加「坡道衝刺 8×10 秒」（`overview.week_plan`）。

### 4.2 課表偏好：`plan.prefs.quality_gate`

加到 `plan_prefs.KEY_FIELDS`（欄位 `quality_gate`），並在 `settings/repository.py` 的 DEFAULTS 和允許值裡登記。預設值是 `auto`。

| 值 | 顯示名稱 | 解鎖條件（基礎期；其他周期不受影響） | 依據 |
|---|---|---|---|
| `auto`（預設） | 自動 | plan 有**實測 AeT**（`plan.threshold_on("aethr", today)` 不是 None，而且那筆的日期 ≤ 16 週前）**且** LTHR 不是 WKO5 預設值 → 用 `ua_gap`，另外 `friel_drift` 也可以解鎖。否則 → `none`＋護欄 | UA、Friel、Seiler。「16 週」是自訂：i_testing 建議每 4–6 週重測，超過 16 週視為過期 |
| `ua_gap` | Uphill Athlete 差距法 | `LTHR/AeT − 1 ≤ 0.10`（兩者都要是 plan 裡的實測值）。第一次解鎖先排 **Zone 3（AeT–LTHR）**，每週 1 次，總量約週有氧時數的 5%。穩定後才排 Zone 4 | UA when-to-add-intensity；trail-running 頁 |
| `friel_drift` | Friel 飄移法 | 最近 8 週有一次**在 AeT 附近**的穩定跑：平均心率在 AeT−5 到 AeT+3 之間（自訂範圍）、移動時間 ≥ 60 分鐘（不含 10 分鐘暖身），`drift_of` 判為可採用而且飄移 < 5%。**一次就夠**，Friel 沒有連續次數的規定 | Friel TrainingPeaks（跑步 1–2 小時、在 AeT） |
| `xu_drift` | 徐國峰 90 分鐘法 | 最近 8 週有一次平路 ≥ 90 分鐘 E 配速跑：(HR@90′ − HR@10′)/HR@10′ < 10%，沿用 `drift_of` 的平路、停頓、功率穩定檢查。要有溫度資料時再加 < 25 °C | 你的筆記：徐國峰（讀書心得 L58–L77） |
| `plateau` | 有氧停滯法 | 基礎期已經 ≥ 8 週，而且 `i_efficiency` 近 8 週和之前比 < +2%（持平） | 徐國峰「錶上 VO2max 不再提升」（讀書心得 L12–L13）；Cusick「指標會比表現先到平台期」（強化期 L492）。EF 代替錶上 VO2max、8 週、2% 都是自訂。**不叫「MAF 停滯」**，因為 Maffetone 把停滯當警訊 |
| `weeks` | 週數法 | 基礎期開始後 ≥ N 週（`plan.prefs.quality_gate_weeks`，預設 8，範圍 2–16） | Palladino GP 早期 1–3 週＋中期 4–6 週之後才加 HIIT（palladino基礎期 L49、L77、L94）；Cusick 第一階段 4–8 週。8 週取中間值，屬自訂 |
| `none` | 不設門檻（Seiler） | 只看 §4.4 的護欄 | Seiler 2010、Seiler & Tønnessen 2009、Koop／CTS |

**強制選了某個模式、但缺少它需要的資料時**：
- 例如選了 `ua_gap`、`friel_drift`，但沒有實測 AeT 或 LTHR 還是預設值；或選了 `xu_drift`、`friel_drift`，但 8 週內沒有符合條件的跑步。
- 這時 `i_gate` 顯示 WATCH，說明寫原因，例如「沒有實測 AeT，差距法算不出來」，行動寫「先做 AeT 飄移測試，或把間歇門檻改回自動」。
- `allow_quality` **不會一直鎖住**，而是退回 `none` 的護欄（§4.4）。這是自訂的選擇：理由是缺資料不應該讓間歇永久停掉，這和「沒有 AeT 就不擋」的原則一致。
- 資料齊全、只是沒達標（差距 > 10%、飄移 ≥ 5%、≥ 10%）時才真的鎖住。

**「套用」的自動估算算不算實測？**
- 算。plan 裡 aethr 那一筆不論來源，都是使用者按過「套用」才寫進去的。
- `thresholds.estimate()` 用多次穩定跑做回歸，和 UA 的 Continuous AeT 是同一個概念（UA 說需要約 4 週資料，見 `docs/research/uphill-athlete-mountain-metrics.md` §2(b) Continuous AeT）。
- 但 `i_gate` 的說明要把來源寫出來：note 含「自動估算」時顯示「AeT 146（活動資料估算）」；飄移測試套用的顯示「AeT 146（{date} 飄移測試）」。這樣使用者看得出門檻是靠什麼判斷的。

舊的「連續 3 次」**拿掉**。要保留相容的話可以留一個 `legacy_streak` 值，但不出現在 UI。

「差距法和飄移法都可以解鎖」的理由：UA 的飄移測試本來就是量 AeT 的方法，差距法才是 UA 的門檻；Friel 則直接把飄移當門檻。兩條都有來源，任一條通過都合理。

### 4.3 程式介面

- `workout_review.quality_gate(kind, levels, streak_ok)` 改成 `quality_gate(kind, levels, gate: dict)`。
  - `gate` 的內容是 `{"mode", "ok", "reason", "dose_week"}`。
  - 非基礎期照舊：intensity / drift 不是 bad 就可以。
- 新增 `workout_review.gate_status(ds, today, prefs) -> dict`：依模式計算 `ok` 和 `reason`。
  - `auto` 要解析成實際使用的模式。
  - 會用到的資料：`i_intensity`、`i_fitness`、`i_volume`、`i_form` 的 extra，以及 `plan.threshold_on`。
- `status.i_drift`：**改成資訊性指標**，不再當門檻。
  - 保留中位數和走勢圖。
  - `extra` 拿掉 `streak_ok`，改成 `{"fair", "median"}`。
  - 等級：`> 10%` 仍然是 BAD（輕鬆跑太快），其他都是 INFO。
  - 文字改成「飄移是 AeT 測試用的，不是間歇門檻」。
- 新增 `status.i_gate`（id `gate`，標題「間歇門檻」）。
  - 放進 `PHASE_PRIORITY["base"]` 的第 2 位，在 intensity 之後。
  - 它的 action 會出現在「還缺什麼」。
- `overview.week_plan`：`streak_ok` 改讀 `by["gate"].extra`，並把 `quality_gate` 放進回傳值（`projection._gate_inputs` 會讀）。
- `projection._gate_inputs` / `allow_quality`：讀新的 `gate` 字典。每一個預測週依 `dose_week + i` 推下一步的劑量；`weeks` 模式依預測週的日期判斷。

### 4.4 沒有 AeT 時的護欄（`none` 模式，`auto` 沒有實測 AeT 時也用這個）

每週最多 1 次間歇（基礎期）。以下任一條不通過，這週就不排間歇；坡衝刺照常。

| 護欄 | 規則 | 現有程式 | 依據 |
|---|---|---|---|
| 強度分配 | 4 週心率 < AeT（估計值）的**時間**佔比 ≥ 75%（`LOW_SHARE_GOOD`）才排 VO2 類；65–75% 只排閾值下 3×8 或不排；< 65% 不排 | `i_intensity` | Seiler：課表數約 80%、時間約 91% 低強度。用時間算，75% 已經偏寬，所以不再放寬。有功率時也要看 < 80% CP 的佔比 ≥ 75%（Palladino 早期 ≤ 80% CP） |
| CTL ramp | 每週 ≥ `RAMP["elite"]`（5）→ 這週只排閾值；≥ `RAMP["short"]`（**8**）→ 不排 | `i_fitness` | **Friel**（教練，https://joefrieltraining.com/the-ctl-ramp-rate/ ：5–8 適合多數人、10 是上限；`unsourced-rules.md` §B2，2026-10-01 從 7 改 8）；Palladino 的每週 +1–3 當「可長期維持」顯示 |
| 週增量 | 上週增幅 > 20%（`i_volume` 的 BAD）→ 不排；10–20% → 維持上週的劑量，不往上加 | `i_volume`、`VOLUME_STEP_WATCH` | > 20%：**Nielsen et al. 2014**（JOSPT 44:739，DOI 10.2519/jospt.2014.5164）、**Damsted et al. 2019**（JOSPT 49:230，DOI 10.2519/jospt.2019.8541），同儕審查；10–20% 維持：推估（保守）。「10% 法則」本身沒有證據 |
| 3:1 恢復週 | `mode == "recovery_week"` → 不排正式間歇，改成 4×1 分 @ 98–101% CP fartlek | `week_plan` 的 `build3` | Palladino 恢復週保留 1–2 次 98–101% fartlek（palladino基礎期 L86）；3:1 是 Friel / UA 的常見做法（`SRC_31`） |
| TSB | TSB < −30 → 恢復週（現有）；−30 到 −20 → 劑量不往上加 | `week_plan` | Friel／TrainingPeaks（Simmons 2020，教練）TSB 區間 |
| 2 天 | 間歇離長跑和其他硬課 ≥ 2 天；5 區一週最多 2 次 | `plan_prefs.place()`（已經有） | **台灣教練**：5 區一週最多 2 次、間隔至少 2 天 |
| 停訓 | 連續 ≥ 6 天沒跑 → 恢復期，期內不排 3 區、5 區 | `engine/reentry.py` | Daniels 表 9.2（`detraining.md` §6） |

**兩道門（2026-10-01 使用者決定）**：3 區（閾值）只要護欄通過就排；5 區要先確認有氧基礎——**三種測試做了其中一種而且達標**：① 徐國峰 90 分鐘測試（平路 1 區，第 90 分 vs 第 10 分心率飄移 < 10%）、② 實測 AeT 的 UA 差距法（LTHR ÷ AeT − 1 ≤ 10%）、③ 實測 AeT 附近 ≥ 60 分鐘的 Friel 飄移（前後半 < 5%）。方法「鎖住」只關 5 區，不再擋 3 區。確認後沒有到期日，每週檢查：1 區時間沒有連 3 週 < 確認時的 2/3（Hickson 1982；3 週推估），掉到線下就暫停到下次確認；停跑 ≥ 6 天照恢復期規則。規則細節見 `docs/spec/plan-auto.spec.md`。

**舊的替代訊號路徑拿掉了（2026-10-01 使用者決定）**：原本還有一條 `xu_signals` 路徑（每週 1 區時間＋長跑後段不變差），跟 90 分鐘測試是同一件事，所以改成上面的「三選一」。每週 1 區時間（台灣教練：約 150–210 分鐘）不再是解鎖條件，但留著當**維持／暫停**規則（連 3 週 < 確認時的 2/3 就暫停）和歷程圖的長條；長跑後段檢查只留在停跑 14–28 天後的恢復期長跑飄移檢查（`detraining.md`，不同的規則）。舊設定存的 `xu_signals` 讀成「自動」。
| 上次掉速 | 上次間歇最後一組比第一組低 > 5% → 劑量退一步 | `WR.last_quality`、`next_quality` | 自訂（現有規則） |
| 資料品質 | LTHR 還是 WKO5 預設 160 → 間歇目標只用功率（CP）或 RPE，不用心率 | `i_data` | — |

### 4.5 沒有 AeT 時前 6 週的間歇劑量

> 2026-10-01：這一節的階梯已由 `interval-prescription.md` §A5.3 取代（3 區 3×6 → 3×8 → 2×12，
> 5 區 5×2 → 4×3 → 5×3 → 4×4，每階有同等課表庫；實作在 `backend/engine/interval_library.py`）。

地形：有 `terrain_quality = hill` 或目標是山的時候，用 6–10% 坡、上坡跑、走或慢跑下來，對應 Koop 的 "uphill if possible"。功率目標依 CP（Stryd）。

**2026-10-01 改：先 3 區、後 5 區（台灣教練，使用者決定）。** 原本第 1 步的 5×1 分 @ 98–101% CP 太短、練不到最大攝氧量，又有 5 區的負荷。

| 步（達標次數） | 課表 | 目標 | 依據 |
|---|---|---|---|
| 0 | 閾值 3×8 分，休 2 分 | 88–95% CP | 台灣教練：第一個質量課選 3 區；Palladino 3A |
| 1 | 閾值 4×8 分 | 88–95% CP | Seiler 2013：4×8 對休閒選手效果最好 |
| 2 | 閾值 3×10 分，休 3 分 | 95–101% CP | Palladino 3B |
| 3（5 區已確認才排） | VO2max 5×2 分，休 2 分 | 106–112% CP（Palladino Z5 的下段，推估） | 台灣教練：5 區每趟至少 2 分鐘 |
| 4–6 | 4×3、5×3、4×4 分 | 105–110％／103–107% CP | Koop；Helgerud 2007（%CP 換算推估） |
| 之後 | 4×4 與 3×10 交替 | | |
| 3:1 恢復週 | 4×1 分 fartlek（不算一步） | 98–101% CP | Palladino 恢復週（不變） |

- 每一步只有「達標」才往前（`interval-adaptation.md` §4.3；`unsourced-rules.md` §B4）：邊界、無法判定（沒有功率或 CP）都重做同一步；未適應先加休息、連兩次退一步。
- 3 堂 3 區達標才進 5 區（3 堂是推估）；5 區沒開時排最上面兩階 3 區、5 區的步數停住。5 區一週最多 2 次、隔 ≥ 2 天（台灣教練）。
- 劑量累計看近 8 週的品質課（`dose_history`）。

### 4.6 總覽與「還缺什麼」要顯示什麼

`i_gate` 的文字。只有 WATCH / BAD 並且有 action 時才會出現在「還缺什麼」（`_recommend`）。

| 情境 | 等級 | 說明（verdict） | 行動（action；出現在「還缺什麼」） |
|---|---|---|---|
| auto、沒有 AeT 實測、護欄都通過 | INFO | 沒有 AeT 實測：照 80/20 原則每週 1 次間歇（第 N 步：5×1 分） | ——（改由 `i_data` 的 action：「排一次 AeT 飄移測試（15 分暖身＋45–60 分固定功率，平路）；測了可以改用有氧基礎門檻」） |
| none、護欄擋下（例如低強度 68%） | WATCH | 本週不排間歇：低強度只有 68%（< 75%） | 輕鬆跑壓在 {aet} bpm 以下，下週再看 |
| none、ramp ≥ 5 | WATCH | CTL 每週 +5.6，本週只排閾值下 | 先穩住量 |
| ua_gap 鎖住 | WATCH | AeT 142 / LTHR 165：差距 16%（> 10%，有氧不足） | 繼續基礎：輕鬆跑壓在 AeT 以下＋坡衝刺；每 4–6 週重測 AeT |
| ua_gap 解鎖 | GOOD | 差距 9% ≤ 10%：可以加 Zone 3 | 本週 1 次 Zone 3（AeT–LTHR），總量約 {5% 週時數} 分 |
| friel_drift 鎖住 | WATCH | 8 週內沒有 ≥ 60 分鐘、心率在 AeT 附近、飄移 < 5% 的平路跑 | 排一次 60–90 分鐘平路跑，心率壓在 AeT 附近 |
| xu_drift 鎖住 | WATCH | 最近一次 90 分鐘 E 跑飄移 12%（≥ 10%） | 繼續低強度長跑，下次選 < 25 °C 的日子再測 |
| plateau 鎖住 | INFO | EF 還在進步（+3%）：基礎還在長，先不加 | —— |
| weeks 鎖住 | INFO | 基礎期第 5 週 / 8 週 | —— |
| AeT 過期（> 16 週） | WATCH（`i_testing`） | AeT 已經 17 週沒測，門檻改用不設門檻模式 | 重測 AeT |

週課表卡片上，品質課的 `detail` 前綴改成依模式顯示：
- `none`：「沒有 AeT 實測：照 80/20 原則每週 1 次間歇，」
- `ua_gap`：「AeT–LTHR 差距 9% ≤ 10%：第一次加 Zone 3，」

`source` 換成對應的來源常數：`SRC_SEILER`、`SRC_UA`、`SRC_PALLADINO`、`SRC_KOOP`，以及新的 `SRC_FRIEL`、`SRC_XU`（「徐國峰（你的筆記：跑者都該懂的跑步數據）」）。

### 4.7 要改的檔案

| 檔案 | 改什麼 |
|---|---|
| `backend/engine/plan_prefs.py` | `KEY_FIELDS` 加 `plan.prefs.quality_gate`、`plan.prefs.quality_gate_weeks`；`Prefs` 加欄位（預設 `auto` / 8）；`check()` 驗證範圍 |
| `backend/settings/repository.py` | DEFAULTS 和允許值（`auto, ua_gap, friel_drift, xu_drift, plateau, weeks, none`） |
| `backend/engine/workout_review.py` | `quality_gate()` 改簽名；新增 `gate_status()`、`xu_drift_of()`、`friel_runs()`；`STREAK_NEED` 和 `drift_streak` 改成只給 legacy；`next_quality()` 改成依 §4.5 的劑量表 |
| `backend/engine/status.py` | `i_drift` 改成資訊性指標；新增 `i_gate`；`PHASE_PRIORITY["base"]` 加 `gate`；`PHASE_GOAL["base"]` 拿掉「飄移 < 5%」；`i_data`、`i_testing` 的 action 文字（§4.6、§6.4） |
| `backend/engine/overview.py` | `week_plan`：`allow_quality` 讀 `gate`；基礎期品質課依 `dose_week` 排；`quality_gate` 回傳 `gate` |
| `backend/engine/projection.py` | `_gate_inputs`、`allow_quality`、預測週的劑量推進 |
| `backend/static/`（課表偏好面板） | 新下拉選單「間歇門檻」＋週數欄 |
| `backend/sync/coros_workouts.py` | fartlek／上坡間歇的 steps（`_quality_steps` 已經能解析「N×M 分」；1 分鐘版要確認可以解析）；AeT 測試 steps（§6.4） |
| `backend/tests/` | `test_workout_review.py`（gate 各模式）、`test_plan_prefs.py`、`test_projection.py` |
| `docs/spec/workout-review.spec.md` | 同步規則與詞彙（Drift streak → Gate） |

---

## 5. 沒有 AeT 時怎麼排間歇（整理）

- **不擋的理由**：
  - Seiler 2010 摘要說高強度 "incorporated throughout the training cycle"。
  - Seiler & Tønnessen 2009 說每週 1–3 次。
  - CTS 把最高強度放在計畫前面。
  - Muñoz 2014 的休閒跑者從第一週就有高強度，結果不差。
  - Filipas 2022 的四組從頭到尾都有高強度。
  - 沒有研究顯示「先過門檻」比較好（§2.8）。
- **基礎期的量**：每週 1 次（UA 的起步量、Palladino 後期每週 1 次、Maffetone 1–2 次）。`plan.prefs.quality_per_week` 可以設 0–2，預設 auto = 最多 1。
- **類型**：前 6 週依 §4.5，從短（1 分）到中（3–4 分）。之後和閾值下 3×8、4×8 交替。
- **坡衝刺**：每週一次 8×10 秒（現有），不算間歇。依據是 UA（8–10 秒，≥ 20% 坡，6–8 趟，休 2–3 分）和 Palladino（8–15 秒，4–8 趟）。
- **護欄**：§4.4。
- **什麼時候改用門檻**：做了 AeT 測試並套用後，`auto` 會自動切到 `ua_gap`＋`friel_drift`。在這之前已經排進去的間歇不回收；如果那時差距 > 10%，下週起改排 Zone 3（UA）。

---

## 6. AeT／心率飄移測試怎麼做？一定要 60 分鐘嗎？

### 6.1 你的筆記

- 你筆記裡**唯一完整的測試流程是徐國峰的 90 分鐘版**（§1.1，讀書心得 L58–L67）：
  - 平地、< 25 °C、E 配速
  - 第 10 分鐘對第 90 分鐘的心率
  - 補給停 ≤ 30 秒
- `心率飄移 pwhr.md` L11–L19 寫的是 TrainingPeaks 的操作方式（框選穩定段看 Pw/Pa:HR），時間下限寫 > 20 分鐘（L9）。
- Palladino 研討會 L705–L709：坡度、配速、氣溫要對稱，快速結尾會干擾。
- 「60 分鐘」「鼻呼吸」「AeT 測試」這幾個詞在 vault 裡**沒有找到對應的筆記**。App 裡「60 分鐘 AeT 飄移測試」的說法（`status.i_testing`）來自 UA / Evoke，不是你的筆記。

### 6.2 有來源的測試流程

| 流程 | 暖身 | 測試長度 | 場地 | 固定什麼 | 比較 | 判讀 | 來源 |
|---|---|---|---|---|---|---|---|
| **UA 心率飄移** | 10–15 分（「快要開始流汗」） | **40–60 分**："If you only have 40 minutes, do that." "We don't recommend relying on tests less than 40 minutes long." | 跑步機 3%（慢跑）／≥ 10%（健行），或平路（田徑場）。"Trails have too many pitch changes" | **固定配速**（"Don't slow down or speed up"），讓心率變 | 前半對後半（例：第一個 30 分鐘 vs 第二個 30 分鐘，(151/144 − 1) = 4.9%）；排除暖身和緩和 | < 3.5%：低於 AeT，下次起始心率 +5 bpm 再測；3.5–5%：起始心率就是 AeT；> 5%：起始太高，降低再測 | https://uphillathlete.com/aerobic-training/heart-rate-drift/ |
| **Evoke（Johnston）** | ≥ 10 分，不超過鼻呼吸配速；找到心率 ±2–3 bpm 穩定 ≥ 3 分的配速 | **60 分** | 跑步機 2%（跑者）／15%（健行）；戶外要平的環線，每 1.6 km 爬升 < 30 m，**不要折返路線**；戶外自然坡不能做健行版 | 固定速度（"DO NOT TOUCH THE SPEED CONTROL"）；或固定心率、看配速掉多少 | 前半從約第 5 分鐘到約 30 分鐘，對後半 | 1 小時內心率升 > 5%（或固定心率時配速掉 > 5%）→ 起始在 AeT 以上；10 分鐘時心率已經高 10 下還在升 → 提早放棄重來 | https://evokeendurance.com/resources/our-latest-thinking-on-aerobic-assessment-for-the-mountain-athlete/ |
| **Friel decoupling** | 不含暖身和緩和 | 跑步 **1–2 小時** | 穩定 | 在 AeT | 前半對後半 | < 5% / 5–10% / > 10% | https://www.trainingpeaks.com/blog/aerobic-endurance-and-decoupling/ |
| **徐國峰** | 前 10 分鐘 | **90 分** | 平地 | E 配速 | 第 10 分鐘對第 90 分鐘 | < 10% 基礎夠 | 你的筆記 |
| 講話測試 | — | 階段式 | — | — | 最後一個能舒服講話的階段 | 「+」階段 64 ± 5% VO2max、82 ± 7% HRmax；「±」階段 71 ± 6% VO2max，和乳酸閾值的關係比 VT 強 | Persinger 2004 *MSSE* PMID 15354048（和 VT 相關良好，n = 16）；Reed & Pipe 2014 *Curr Opin Cardiol* DOI 10.1097/hco.0000000000000097；Quinn & Coons 2011 *J Sports Sci* DOI 10.1080/02640414.2011.585165 |
| 鼻呼吸 | — | — | — | — | — | UA：對訓練不足的人不準；鼻呼吸上限＝VT1 **沒有研究驗證**（Mapelli 2025 *PLoS One* DOI 10.1371/journal.pone.0326661 沒有找出 VT1 上限） | 未找到驗證 |
| DFA-α1 | — | 階梯或斜坡 | — | — | α1 = 0.75 | 見 §2.7 | COROS **不能做** |
| 心率拐點（Conconi） | — | 階梯 | — | — | — | 很多人沒有拐點；比較接近 LT2 | 不建議（§3） |

- **MAF 測試**（Maffetone）：固定 MAF 心率（180 − 年齡）跑固定距離、看配速有沒有進步；它追蹤的是同心率下的速度，**不是飄移測試**，不判 AeT、也沒有「過關」的門檻（§2.3：Maffetone 把停滯當警訊）。app 的「同心率跑更快」由輕鬆跑 EF 自動追蹤（`base_check.easy_targets`），所以不另外提供 MAF 測試。

**一定要 60 分鐘嗎？** 不一定。
- UA 接受 40–60 分鐘，不建議少於 40 分鐘。Evoke 用 60 分鐘，Friel 用 1–2 小時，徐國峰用 90 分鐘。
- **沒有任何長度（30、45、60 分鐘）有同儕審查的驗證**，文獻查詢零筆。
- 生理上，心血管飄移大約在運動 10–20 分鐘後開始（Coyle & González-Alonso 2001, *Exerc Sport Sci Rev*, DOI 10.1097/00003677-200104000-00009），所以扣掉暖身後至少要有 30 分鐘以上才看得到前後半的差別。
- 40 分鐘是教練經驗的下限，45–60 分鐘比較保險，屬自訂判斷。

**讓測試無效的因素**：

| 因素 | 證據 | `drift_of` 有沒有處理 |
|---|---|---|
| 坡度、地形起伏 | UA "Trails have too many pitch changes"；Evoke 每 1.6 km < 30 m、不折返 | 有：越野 tag 或每公里爬升 ≥ 20 m 就不採用（`TRAIL_CLIMB_RATE_M_PER_KM`）。Evoke 的 30 m/1.6 km ≈ 19 m/km，和我們的 20 m/km 相當 |
| 停頓 | 徐國峰：停 ≤ 30 秒 | 有：暖身後停頓 > 5% 就不採用。2026-10-01 加**自適應暖身**（`steady_start`，**推估**）：出門先過市區路口時，前 20 分鐘內最後一次停等結束後 1 分鐘才起算（至少第 10 分鐘）；起點延後會讓暖身後時間掉到 40／30 分以下時，仍從第 10 分鐘起算。判讀卡顯示「已排除：前段路口停等 N 次…」。實際資料上這條沒有改變任何一次的結果：這位選手的早段停等都在第 0–5 分，真正讓功率不穩的是回程市區段和走路段（`docs/research/drift-steady-window-data.md`） |
| 坡道、快步（平路上的短坡、過路口後加速） | UA "Trails have too many pitch changes"；Evoke 不折返；坡道後心率的遲滯時間未找到來源 | **不排除**（2026-10-01 使用者決定）：上坡後的平路心率可能還沒回來，排除會把真的影響藏起來。坡道、快步留在功率變異檢查和前後半裡；變異太大就不採用。這位選手河濱每趟有 2 組 ±4–5 m 坡，回程市區另有 2–3 組陡坡，大多在後半（資料檔 §3–4） |
| 配速／功率不穩、快速結尾 | UA / Evoke 固定速度；Palladino 研討會 | 有：30 秒功率變異 > 15% 就不採用（**15% 和 30 秒都未找到來源**，推估；現在通過的跑步 p90 是 14.7%，門檻是循環的，重新校準的選項見資料檔 §6）。**但快速結尾不一定會被擋**，因為變異可能還不到 15%。建議加一條：後 10% 時間平均功率比前段高 > 5% 就不採用（自訂） |
| 強度太高 | — | 有：> 90% CP 不採用 |
| 熱 | 35 °C 下 15–45 分鐘心率升 11%，22 °C 只升 2%（Lafrenz 2008 *MSSE* DOI 10.1249/MSS.0b013e3181666ed7）；Wingo 2020 *MSSE* DOI 10.1249/MSS.0000000000002324 升 17–19%；28.7 對 19.2 °C 最高心率 +16 bpm（Beiter 2025 *Physiol Rep* DOI 10.14814/phy2.70305）；台灣教練 < 25 °C | 有，**分區、不拒絕**（2026-10-02 使用者決定，`workout_review.temp_band`）：< 25 °C、25–28 °C、> 28 °C、溫度不明四區，飄移只和同一區比（總覽心率飄移、季圖每區一條 6 次平均、判讀卡同類基準；AeT 聚合用 < 25 °C、溫度不明和熱校正後的 25–28 °C——心率扣 β·(T − 25)，β 文獻 1 bpm／°C（Jenkins 2023）往本人擬合收縮，unsourced-rules.md §B6）。25 °C 是台灣教練＋Lafrenz；**28 °C 是推估**（Beiter 2025 的熱組 28.7 °C，沒有來源給分界）。用氣溫不用 Hadley：飄移的來源都用 °C，手錶那條路也沒有自己的濕度。溫度：Open-Meteo 路線天氣（依檔名，否則當天唯一一筆），沒有才用手錶溫度扣手腕偏差 3.7 °C（本人 72 組配對，推估，較不準）。門檻（Friel／徐國峰 90 分／AeT 測試）照常：熱會讓飄移偏高，**熱天通過仍算數（保守）**，沒通過標「熱環境，結果可能偏高」、可能是熱造成的。**不做熱校正值**：本人 β 0.224 bpm／Hadley 是跑步之間的心率位移，不是一次跑步裡心率往上飄的速度，套在前後半上等於 0。實際資料：原本的 > 25 °C 規則只用 WKO5 檔名找天氣，COROS 資料 161 次路跑只找到 5 次溫度，32 個飄移值一個都沒擋到；加上依日期對照後 147 次有溫度，32 個值是 < 25 °C 5、25–28 °C 19、> 28 °C 7、溫度不明 1（舊規則會擋掉 26 個） |
| 脫水、沒補給 | 2 小時不補水：體重 −2.9%、心率 +10%；補水加葡萄糖可以防止飄移（Hamilton 1991 *J Appl Physiol* DOI 10.1152/jappl.1991.71.3.871） | 沒有（無資料）。只能在測試說明裡提醒 |
| 咖啡因 | 對運動心率有交互作用（Glaister 2025 *RQES* DOI 10.1080/02701367.2024.2377303，效果大小沒取得） | 沒有；只能提醒 |
| 前一天的疲勞 | Evoke "vary daily, depending on your recovery state"；Friel "cardiovascular fatigue"（沒有同儕審查來源） | 沒有；排課時避開（§6.4） |
| 時間不夠 | UA ≥ 40 分鐘（暖身之後）；心血管飄移約在運動 10–20 分鐘後開始（Coyle & González-Alonso 2001 *Exerc Sport Sci Rev* DOI 10.1097/00003677-200104000-00009） | 有，分兩級（2026-10-01 使用者決定）。**嚴格／測試級**：暖身 10 分鐘後的移動時間 ≥ 40 分鐘（`DRIFT_MIN_S`），只有這一級拿來判斷間歇門檻（Friel／徐國峰）、AeT 測試分類和寫門檻。**參考級**：暖身後 30–40 分鐘（`DRIFT_REF_MIN_S`，**推估**，30 分鐘沒有來源；UA 的 40 分鐘是正式 AeT 測試的標準），其他排除條件（熱、坡、停頓、快速結尾、功率變異、強度、功率涵蓋率）照樣套用；只在總覽心率飄移、季圖、判讀卡顯示，標「參考（暖身後 30–40 分，未達 UA 測試標準）」。< 30 分鐘兩級都不採用。這位選手的路跑多是 41–52 分鐘：實際資料（164 次 ≥ 40 分鐘的路跑）嚴格 1 次、參考 32 次。`test_aet` 的穩定跑備援仍用 ≥ 55 分鐘（`TEST_AET_MIN_S`），排進課表的 50 分鐘測試靠課表 done_by／標題辨認 |

### 6.3 這位選手的建議

**2026-10-01 更新：標準版改成徐國峰 90 分鐘，UA 40 分當備案（`plan.prefs.aet_test_protocol`，預設 `auto`）。**
- 理由：
  - 徐國峰公開的有氧基礎檢測就是 90 分鐘平路 1 區（部落格 2016-12，http://rocky549.blogspot.com/2016/12/rq.html）；排在週末長跑日當那次長跑，不用另外排時間，平日 50 分的上限也不再卡住。
  - UA 接受 40 分、Evoke 60 分、Friel 1–2 小時；**沒有任何長度有同儕審查的驗證**（上面）。長一點的版本看得到更多後段飄移。
  - 代價：90 分鐘測試看的是「有氧基礎夠不夠」（第 10 分 vs 第 90 分 < 10%），**不給 AeT 數字**；AeT 數字改由多次飄移的聚合估計（`drift-algorithm.md` §5.4、`unsourced-rules.md` §B3）。
- 排法：週末長跑日、平路（不跑山路那一週）、取代那週的長跑；長跑日上限 < 90 分時改用備案 UA 40 分（平日）。
- 可選的方式（各自的長度、場地、固定什麼、怎麼判讀、來源在課表偏好的「?」）：`xu90`、`ua60`、`ua40`、`evoke60`、`friel`。MAF 不是飄移測試，不提供（§6.2）。
- 熱：測試與課表文字寫「氣溫 25 °C 以下時開始（熱會讓心率偏高、飄移失真）」（台灣教練＋ Lafrenz 2008；使用者決定）。這是建議、不是拒絕：2026-10-02 起分析改成溫度分區（§6.2「熱」那一列），熱天的測試照算，通過仍算數，沒通過標「熱環境，結果可能偏高」。
- 什麼時候排：不再有固定週期（16 週、4–6 週都沒有來源），只在聚合估計不準（SE > 3 bpm）、有偏移、約 6 週沒有可判讀的跑步，或停跑 ≥ 4 週後才排（`unsourced-rules.md` §B3、`detraining.md`）；被動確認（任何一次合格的 90 分平路 1 區跑）優先。

以下是原本（UA 為主）的建議，保留當背景：

- **流程**：UA 心率飄移測試。
  - 固定**功率**：有 Stryd，功率比配速穩；`steady_drift` 本來就是 Pw:HR。
  - 同時記錄 Pa:HR 當作對照。
- **長度**：暖身 15 分鐘＋測試 **60 分鐘**；時間不夠時 **45 分鐘**（不要少於 40 分鐘，UA）。
  - 2026-10-01 更新：選手平日只有約 50 分鐘、週末跑越野。課表偏好有平日上限而且 < 80 分鐘時，改排 UA 的最短版本：暖身 10 分鐘（到開始流汗）＋測試 **40 分鐘**、緩和可省略，共 50 分鐘（UA："If you only have 40 minutes, do that."；"We don't recommend relying on tests less than 40 minutes long"），排在平日。沒有上限時照標準 15＋60＋5。
- **場地**：
  - 首選平路環線（每公里爬升 < 20 m、不折返，Evoke）或田徑場。
  - 次選跑步機 2–3%。
  - **不要在越野路線**上做。
  - 百岳版（健行 AeT）只能在跑步機 ≥ 10–15% 做，戶外自然坡不行（Evoke）。
- **條件**：
  - 選 < 25 °C 的時段（台灣教練；Lafrenz 2008）
  - 前一天休息或輕鬆
  - 平常的補給、咖啡因照舊
  - 途中補水但不停下來
- **起始強度**：
  - `thresholds.estimate()` 的 aethr 建議值；沒有的話用 0.89×LTHR − 5 bpm（自訂，保守一點）。
  - 暖身時用鼻呼吸確認不超過（Evoke）。
- **判讀**：UA 三段。
  - < 3.5%：下次 +5 bpm 再測
  - 3.5–5%：前半平均心率＝AeT
  - > 5%：下次 −5 bpm 再測

### 6.4 在 app 裡怎麼排、怎麼分析、怎麼套用

**排課**（`overview.week_plan`）：
- 什麼時候排：基礎期，而且符合以下任一條
  - `i_data` 顯示「AeT 用 0.89×LTHR 估」
  - plan 的 AeT 超過 6 週沒更新（`i_testing` 已經有「每 4–6 週」的說法）
- 放在 CP 測試那一堆的後一週（兩者不同週），取代當週的間歇。
- 放在休息日或輕鬆日之後，離長跑 ≥ 2 天（`place()` 用 kind `test` 已經會處理）。
  - 2026-10-01 實作：`aet_test.pick_day`，三條排課路徑共用。預設平日（`plan.prefs.aet_test_days` = `weekday`），週二優先；離長跑和其他硬課 ≥ 2 天，盡量不排長跑隔天。80 分標準版在平日排不下時可以退到不是長跑日的週末；50 分版只排平日。選 `any` 就照間歇的規則。
- Session 內容：
  - `kind="test"`、`id="test_aet"`；標準版 `title="AeT 飄移測試 60 分"`、`minutes=80`；平日上限 < 80 分時 `title="AeT 飄移測試 40 分"`、`minutes=50`
  - target 寫「固定功率 {P} W（±3%）；心率從 {HR} 附近開始」
  - detail 先寫選哪一版、為什麼（例如「平日上限 50 分 → 用 UA 最短 40 分版本」），再寫「冷氣房跑步機 2–3%＋電扇（首選），或清晨平路環線，不要山路；暖身 N 分到開始流汗，接著測試 N 分固定功率不要調；中途不停；記下溫度；熱的時候結果會偏高」，以及 Evoke 的提早中止：「主課第 10 分鐘心率已經比起始高 10 下還在升 → 起始太高，停掉改天降 5 bpm 再測」（https://evokeendurance.com/resources/our-latest-thinking-on-aerobic-assessment-for-the-mountain-athlete/）。說明寫「氣溫 25 °C 以下時開始（熱會讓心率偏高、飄移失真）」當建議；2026-10-02 起分析不再擋 > 25 °C，改成溫度分區：熱天通過仍算數，沒通過標「熱環境，結果可能偏高」
- **COROS**（`coros_workouts.session_steps`）：目前 `kind == "test"` 一律走 `_test_steps`（CP 的 3′+12′），要依 `id` 或標題分流，新增 `_aet_test_steps`：
  1. 暖身 15 分，`EX_WARMUP`，心率 ≤ 起始心率
  2. 主課 60 分，`EX_TRAIN`，功率目標 P×0.97–P×1.03，名稱「固定功率，不要調」
  3. 緩和 5 分，`EX_COOLDOWN`
  - 用 lap 切段，分析時才能準確排除暖身。

**分析**（`workout_review`）：
- `classify` 已經會把 plan 同一天有 aethr 紀錄、或 ≥ 55 分鐘的穩定跑判成 `test_aet`。建議再加一條：當天的 plan session 是 `test_aet`。
- 測試課要**用 lap 切出主課段**：有 FIT lap 就用 lap 2；沒有就跳過前 15 分鐘，不是固定 10 分鐘。
- 算 Pw:HR（`steady_drift`）和 Pa:HR（`drift_of`）。
- `aerobic_lines` 的 `test_aet` 分支改成 UA 三段。目前只有 < 5% / ≥ 5% 兩段：< 5% 就建議設成 AeT，漏掉「< 3.5% 代表還在 AeT 以下」。
- 新增 `workout_review.latest_aet_test(ds, today, days=120)`，比照 `latest_cp_test`。回傳：
  - `{date, idx, hr1, drift, pw_drift, band: "below"|"at"|"above", aethr_suggest, aethr_now, delta}`
  - `aethr_suggest` 只有在 band == "at" 時才有值，等於 hr1（四捨五入）

**套用**：
- `status.i_testing` 比照 CP：有 `latest_aet_test`、band 是 "at"、而且 plan 的 aethr 在測試日之後沒有新紀錄 → WATCH。
  - 說明：「{date} 的 AeT 測試：飄移 4.2%，AeT = 146 bpm（目前 142）」
  - 行動：「套用這次的 AeT（146 bpm）」
- 前端按鈕呼叫現有的 `POST /api/v1/plan/thresholds/apply-estimate`（`api/plan.py:282`），body 是 `{aethr: 146, note: "AeT 飄移測試 {date}：Pw:HR 4.2%"}`。這支 API 已經會新增「今天」那筆門檻並觸發重算。
  - 建議改成可以帶 `date`，讓那筆記在測試日，不是按按鈕那天（小改）。
- band 是 "below" / "above" 時的行動：「下次起始心率 +5／−5 bpm 再測一次」，不提供套用。
- 套用之後 `auto` 會切到 `ua_gap`＋`friel_drift`。`i_gate` 顯示差距，例如「AeT 146 / LTHR 165 → 13%」。

---

## 7. 自訂數字一覽（沒有外部來源）

2026-10-01 起新寫的條目標「推估」；下表舊條目沿用原本的標記，之後統一改名。

| 數字 | 用在哪 |
|---|---|
| ~~AeT 16 週過期~~ | **已移除**（B3）：AeT 有效＝聚合估計 SE ≤ 3 bpm 且最近 6 次沒有 > 5 bpm 的單向偏移（推估，`unsourced-rules.md` §B3）；停跑 ≥ 4 週視同過期（UA） |
| ~~AeT 每 4–6 週重測、每 5 個基礎週建議一次~~ | **已移除**：只在有理由時排（SE 太大、偏移、約 6 週沒有可判讀的跑步〔推估 6 週；UA 4–6 週，原文未驗證〕、估計值移動超過 SE、停跑 ≥ 4 週）；兩次測試間隔 ≥ 28 天（推估） |
| 3 堂 3 區達標才進 5 區 | §4.5（推估；順序：台灣教練） |
| 5 區 5×2 分 @ 106–112% CP | §4.5（推估：Palladino Z5 的下段） |
| ~~舊替代訊號：隔週跑量 ≥ 70%~~ | **已移除**（2026-10-01，改成三選一測試） |
| 恢復期長跑飄移檢查：後段心率 ≤ +5%、配速 ≤ −5% | 停跑 14–28 天後（推估；現在只留在恢復期） |
| 1 區＝平均心率 ≤ AeT+3、超過的時間 ≤ 10% | 徐國峰「心率 1 區」對應到 app 的輕鬆跑規則（推估） |
| RQ 訓練指數 → TSS：IF 0.70 → 1 點 ≈ 4.1 TSS，30–42 點 ≈ 120–170 TSS | 每週 1 區時間的換算（推估、未驗證；RQ 點數＝Daniels 強度點數 E 每分 0.2，已驗證：徐國峰部落格）；不再是解鎖條件 |
| Z5 維持：1 區時間 < 確認時的 2/3 連 3 週 | 2/3 有 Hickson 1982；3 週推估（`detraining.md` §6.1） |
| 恢復期的長跑上限 90 分（6–13 天）、恢復期後 1／2 堂 3 區才開 5 區 | `detraining.md` §7（推估） |
| 輕鬆跑目標＝最近 6 次輕鬆跑的 EF × AeT | `base_check.easy_targets`（推估） |
| AeT−5 到 AeT+3 | `friel_drift` 的「在 AeT 附近」 |
| 8 週 | `plateau` 最短基礎期；`weeks` 預設；各模式的回看窗 |
| EF < +2% ＝ 停滯 | `plateau`（沿用 `EF_TREND`） |
| 100–105% CP（第 2 步）、4×4 對應約 105% CP | §4.5 |
| 0.89×LTHR − 5 bpm 當測試起始心率 | §6.3 |
| 快速結尾 > 5% 就不採用 | §6.2 |
| ~~溫度 > 25 °C 就不採用~~ | **已改**（2026-10-02）：溫度分區 < 25／25–28／> 28 °C，只在同區比；28 °C 推估（Beiter 2025 熱組 28.7 °C）；熱天通過門檻仍算數（推估：熱只會讓飄移偏高） |
| 手錶溫度扣 3.7 °C 當氣溫 | `workout_review.watch_air`（本人 72 組路線配對，`zone_events.WATCH_BIAS_C`；推估） |
| 暖身後 30 分鐘＝飄移參考級（`DRIFT_REF_MIN_S`） | §6.2；只顯示、不當門檻（Coyle & González-Alonso 2001 只說飄移約 10–20 分鐘後開始，30 分鐘本身沒有來源） |
| 自適應暖身：前 20 分鐘內的停等、停等後 1 分鐘起算（`DRIFT_EARLY_S` / `DRIFT_SETTLE_S`） | §6.2 `drift_of`；不降級 |
| 30 秒功率變異 > 15% 不採用 | §6.2；未找到來源（`docs/research/drift-steady-window-data.md` §6） |
| AeT 測試少 30 秒仍算 40 分鐘（`UA_SLACK_S`） | `aet_test.analyze`，掉幾筆 GPS／Stryd 資料不算失敗 |
| 平日上限 < 80 分 → 改排 50 分版 | `aet_test.variant_for`（UA 有 40 分鐘版本，切換點是我們定的） |
| AeT 測試排平日、週二優先、離長跑 ≥ 2 天、80 分版可退到週末 | `aet_test.pick_day` |
| 標題有 AeT 且 ≥ 48 分，或沒標題 ≥ 55 分，才算做了 AeT 測試 | `overview.week_plan` 標完成 |
| 沒標題時暖身切 15 分（≥ 55 分的跑步）或 10 分 | `aet_test.warm_for` |
| 5% 掉速、48 小時的 2 天 | 現有規則 |
