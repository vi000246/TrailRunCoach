# 山路長天的達標標準，以及連續兩天（Back-to-Back）長天訓練

- 日期：2026-10-01
- 分支：`docs/b2b-long-day`（只寫文件，不改程式，不寫 DB）
- 相關文件：`vo2max-gate-and-trail-metric.md`（§2.4 (a)、(e)）、`coaching-dashboards-mountain.md` §3.3、
  `baiyue-from-running.md`、`unsourced-rules.md` §B5、`heat-acclimation.md`、`interval-prescription.md`；
  程式（唯讀）：`backend/engine/overview.py`、`plan_prefs.py`、`adapt.py`、`planning.py`、
  `workout_review.py`、`racepower/hikehr.py`
- 標記：**同儕審查**、**教練經驗**、**廠商**（裝置／平台的教練文章）、**推估**（沒有來源的數字或做法）、
  **未驗證**（只有搜尋摘要、查不到原文，或沒能逐字核對）。徐國峰算來源。
- 引文：網頁引文是 WebFetch 工具從原頁抽出的句子；論文只讀了摘要（Europe PMC / PubMed E-utilities），沒有讀全文。

---

## 摘要

### Q1：「≥ 85 % 時間在 AeT 以下」有沒有出處？

1. **沒有。** UA／Evoke、Seiler、Friel、Koop、Daniels、徐國峰，還有找得到的研究，都**沒有**「一堂長天要有 X % 時間在 AeT（LT1／VT1）以下才算達標」這種數字。85 % 是上一份文件自己訂的，標**推估**是對的。
2. **最接近的有來源的說法**，都不是「單堂」的門檻：
   - 整體訓練分配：UA「90 percent or more in Zones 1 and 2」（每週，**教練經驗**）。菁英滑雪選手在海拔訓練營，用時間算 96.1 % 在 1 區；用課表目的算，86.6 % 的課是 1 區課（Sylta 2014，**同儕審查**）。
   - 「80/20」是**用課表堂數算**的（Seiler & Kjerland 2006：用心率算，75 % 的課是 1 區課），不是一堂課裡的時間比例。
   - 單堂課只有定性的說法：Friel「heart rate mostly in zone 2」；Evoke 教練 Keena「a little bit of volume above AeT is OK」。
   - 徐國峰看的是**飄移**，不是時間比例：90 分鐘 1 區跑，後段心率飄移 < 10 %。
3. **建議的替代做法：** 不要另外訂一個 85 %。直接沿用 app 現在判斷「輕鬆跑偏強」的規則：AeT + 3 bpm 以上的時間 ≤ 10 %，也就是 ≥ 90 % 的時間在 AeT + 3 以下（`workout_review.AET_MARGIN`、`OVER_AET_SHARE`）。這樣輕鬆跑和長天用同一把尺，少一個推估數字。
   - 再加兩條專門處理山路的規則：「陡坡走路還是超標」和「熱天、後段漂移」。這兩種情況另外標示，不算違規（§1.4）。
   - 這些數字都還是**推估**。
4. **爬坡怎麼處理（教練的做法）：** 心率上限不變，用放慢和走路來守住（UA、Koop）。
   - Koop 給了換成快走的界線：4–15 % 的坡，跑的速度已經慢到每英里 18–19 分鐘（約每公里 11:10–11:50）以下，就改成快走。
   - 走路還壓不住時，各家說法是「短時間超過可以接受」。沒有人給出可以超過幾分鐘。

### Q2：連續兩天長天

1. **你的看法大致成立，但要修正兩點。**
   - 主流教練（UA、Koop／CTS、COROS 教練）都把 B2B 放在**專項期**，用來準備**比較長的賽事**，最後一次在賽前 3–6 週。
   - 修正一：江晏慶把 B2B 放在「基礎後期」。UA 的「overreaching」週末也不限定在專項期，只說「only very occasionally」。
   - 修正二：「多日行程」不是唯一的理由。單日超過約 6 小時的賽事（app 的 `Event.is_long`），教練也會用 B2B。
2. **同儕審查的證據很少。** 沒有找到直接比較「做 B2B」和「不做 B2B」的試驗。
   - 能用的是間接證據：耐久性（durability）的概念（Maunder 2021），以及 Jones & Kirby 2025 的看法：「規律的長時間課可能」提升耐久性，但「data are scant」。
   - 還有一篇：161 km 完賽者的「最長一次訓練跑」比未完賽者長（Tan 2017，p = 0.07）。
   - 另外有肌肉損傷的重複負荷效應，以及跑完到隔天之間的補糖。
3. **怎麼排：**
   - 第 1 天比較長，或比較硬（Koop、CTS、COROS）。第 2 天大約是第 1 天的 2/3，而且兩天都在 AeT 以下。
   - 3 天的行程：在賽前 4–6 週做一次 3 天的區塊（CTS）。
   - 總週量不要因此增加（Koop）。B2B 前一週稍微輕一點，做完後 3–4 天輕鬆（UA）。
4. **app 怎麼判斷做得好不好（不用 RPE）：**
   - 比較第 2 天和第 1 天：同樣 VAM 下的心率差（`hikehr` 已經有 `fatigue`）、同樣心率下的 VAM，再把兩者放進一個 2 × 2 的組合來判讀。
   - 長期看的是：同一個專項期裡，每做一次 B2B，第 2 天掉的幅度有沒有變小。

---

## 1. Q1：長天的達標標準

### 1.1 逐一查過的來源

| 來源 | 對「單堂長天」怎麼說 | 有沒有時間比例的數字 | 類別 |
|---|---|---|---|
| **Uphill Athlete**（trail-running 頁） | 「Polarized training involves spending the majority of your training time (90 percent or more) in Zones 1 and 2 and only 10 percent or less at higher intensities」 | 有，但是**整體訓練**，不是單堂 | 教練經驗 |
| **UA**（AeT／心率飄移頁） | 同樣的配速跑 1 小時，心率上升 > 5 %，或心率不變但配速掉 > 5 %，「the chances are excellent that you began that hour at a heart rate above your aerobic threshold」 | 沒有；判準是飄移，不是時間比例 | 教練經驗 |
| **UA／Steve House**（Durability 頁，2026-07-20） | 解耦（decoupling）「Under five percent is the working benchmark for build-phase aerobic efforts」 | 沒有；判準是解耦 | 教練經驗 |
| **Evoke／Seth Keena**（論壇回覆） | 「the total duration HR is above AeT is very small relatively speaking. And, for almost everyone, a little bit of volume above AeT is OK, even if you are very aerobically deficient」 | 沒有；而且講的是**肌力課**時心率超過 AeT | 教練經驗 |
| **Seiler & Kjerland 2006**，*Scand J Med Sci Sports* 16:49–56，DOI 10.1111/j.1600-0838.2004.00418.x | 青年越野滑雪選手 318 堂耐力課，用心率算，75 ± 3 % 是 1 區（< VT1）課 | 有，但用**堂數**算；「80/20」就是這種算法 | 同儕審查 |
| **Sylta, Tønnessen & Seiler 2014**，*IJSPP* 9:100–107，DOI 10.1123/IJSPP.2013-0298 | 570 堂課：用時間算（TIZ）96.1 % 在 1 區；用課表目的算（SG）86.6 % 是 1 區課；兩種算法在 1 區的換算係數 0.9／1.1 | 有，但是**整體分配**；它說明同一批訓練用時間算的 1 區比例會比用堂數算的高 | 同儕審查 |
| **Esteve-Lanao 2005**，*MSSE* 37:496–504，DOI 10.1249/01.mss.0000155393.78744.86 | 次菁英跑者 6 個月：1 區（< VT）4581 分、2 區 1354 分、3 區 487 分，換算約 71 % 在 1 區（換算是我做的） | 整體分配 | 同儕審查 |
| **Friel**（部落格〈The Aerobic Base Ride〉，2009-11-30／12-01） | 「a long, steady workout with heart rate mostly in zone 2」；舉一位選手當「excellent example」，他是「close to 60% in zone 1, 35% in zone 2 and 5% in zone 3」 | 沒有門檻；搜尋摘要寫「at least half the time in z2」，原文找不到這句（**未驗證**） | 教練經驗 |
| **Koop**（CTS〈Should You Run or Hike That Hill?〉） | 「if you are running on any normal climb (4 to 15 percent grade) around 18- to 19-min/mile or slower, it's in your best interest to drop to a power-hike」 | 沒有時間比例；只講什麼時候改走 | 教練經驗 |
| **Daniels**（你的筆記） | L 長跑用 E 配速；長度 ≤ 週跑量 25 % 或 150 分鐘（`300 Sport/60 🏃 有氧訓練/丹尼爾的跑步方程式筆記 還有課表.md:10–12`）；「跑步動作開始變形或不受控制，可以直接刪減跑量」（`300 Sport/70 ⏳ 周期化訓練/越野跑周期化訓練(晏慶、K天王、丹尼爾).md:80–81`） | 沒有；用配速和長度管 | 教練經驗 |
| **徐國峰** | 90 分鐘 1 區有氧基礎檢測（部落格 2016-12，http://rocky549.blogspot.com/2016/12/rq.html）；「E 配速 90 分鐘的心率飄移 %……在 10 % 以下」（`300 Sport/70 ⏳ 周期化訓練/400 爆發力、敏捷性、專項耐力周期.md:5`） | 沒有時間比例；判準是**飄移 < 10 %** | 徐國峰 |
| **Gordo Byrn**（Substack，2023-12-07） | 「be willing to PowerWalk to keep stay the HR cap」 | 沒有。**注意**：搜尋摘要把這句歸給 UA，其實是 Byrn 寫的；摘要裡「走路還進 3 區就縮短，算成 Tempo 課」那句，原文沒有核對到（**未驗證**） | 教練經驗 |

另外查了 Europe PMC，關鍵字是 session goal、time-in-zone，以及長跑在 LT1／VT1 以下的時間。**沒有找到**用「一堂長跑在 VT1 以下的時間比例」來判斷達標的研究。

### 1.2 結論：85 % 沒有出處

- 時間比例這種說法，在文獻和教練文章裡**只用在整體訓練分配**（每週、每個訓練期）。
- 單堂長天，各家都用定性的上限：「mostly」、「stay below AeT」、「a little above is OK」。要看長天做得好不好，用的是飄移或解耦（UA 5 %、徐國峰 10 %）。
- Sylta 2014 的數字可以拿來做一個**合理性檢查**：菁英選手 1 區的總時間大約 96 %，裡面還混了間歇課的暖身和緩和。所以一堂純 1 區的長天，「≥ 90 % 在上限以下」不算嚴格。不過這是**推論**，不是那篇的結論。

### 1.3 建議的達標標準（推估，但盡量不新增數字）

| # | 規則 | 數字從哪來 |
|---|---|---|
| ① | **主判準：** 移動時間中，心率 > AeT + 3 的比例 ≤ 10 %，也就是 ≥ 90 % 在 AeT + 3 以下 | app 判斷輕鬆跑偏強的同一條規則（`workout_review.py:138–139`，`unsourced-rules.md` §B5，**推估**）。好處是輕鬆跑和長天用同一把尺。+3 bpm 吸收心率延遲（τ ≈ 55–70 秒）和雜訊 |
| ② | **持續超標段：** 心率 > AeT + 5、持續 ≥ 3 分鐘的段，記段數和總分鐘數 | 上一份文件的推估（`vo2max-gate-and-trail-metric.md` §2.4 (a)），維持 |
| ③ | **陡坡例外：** 坡度 ≥ 15 % 而且是走路（速度低於步行門檻）時，② 的超標段標成「陡坡走路仍超過 AeT」，① 不因此判失敗，但這些分鐘另外列出來 | UA、Koop：走路是守住上限的手段；走路還壓不住，表示這段坡對你來說已經是 3 區／肌耐力的刺激。15 % 對應 Koop 4–15 % 的上限，以及 Minetti 走／跑機制改變的 ±15 %（`uphill-athlete-mountain-metrics.md` §4）。「不判失敗」是**推估** |
| ④ | **後段漂移：** 超標時間如果集中在最後 1/3，而同一段的 VAM 或配速沒有變快，標成「後段漂移（耐久性訊號）」，不算違規 | Maunder 2021：門檻在長時間運動中會移動；Coyle & González-Alonso 2001：心血管漂移主要是心率上升；UA 解耦 5 %。標示方式是**推估** |
| ⑤ | **熱天：** Hadley > 150（`heat.race_is_hot` 的門檻）的日子，① 照算，但結果附註「熱天心率偏高」 | UA 的做法是上限不變、自己放慢（熱也是真的壓力）；Koop 認為熱、海拔、脫水會扭曲心率（`vo2max-gate-and-trail-metric.md` §2.1）。只附註、不改門檻是**推估** |
| — | **不用平均心率當主判準** | 30 分鐘 3 區加上長時間慢走，平均也可能 < AeT，看不出超標。平均心率只當參考 |

如果你還是想保留「≤ AeT 的比例」這種寫法：

- 「≥ 85 % ≤ AeT」和「≥ 90 % ≤ AeT + 3」在你的資料上可能差不多，但**沒有實測過**。
- 建議先上線 ①，同時記錄兩種算法。累積 10 次長天以後再比較（**推估**）。

### 1.4 爬坡和熱天：教練實際怎麼做

1. **上限不動，靠速度讓步。**
   - UA：長天和基礎課都以 AeT 為上限，「當天的速度」是結果，不是目標。
   - Koop：4–15 % 的坡速度已經很慢時，改成快走，比較省能量，心率也比較低。他用跑步機示範過：13 % 坡、每英里 18 分鐘，跑的心率比走高。
2. **短時間超過可以接受，但沒有人量化。**
   - Evoke 的 Keena 說的是肌力課，不是長天。
   - 上一份文件從心率延遲推估可以容忍 90 秒。
   - 這份文件改用 ② 和 ③：持續 ≥ 3 分鐘才算一段，陡坡走路另外標。
3. **肌肉疲勞時，心率會往下掉。**
   - UA：「When local muscular fatigue sets in, heart rate actually drops」（**教練經驗**）。
   - Kerhervé 2015：106 km 的比賽中，各坡度的心率從 10 % 進度到 70–90 % 進度明顯下降（**同儕審查**，轉引自 `unsourced-rules.md`，沒有重新核對）。
   - 所以長天後段「心率很漂亮地在 AeT 以下」不一定代表輕鬆。① 只能抓到「太硬」，抓不到「腿已經沒力」。腿沒力要在 Q2 的第 2 天判讀裡看（§2.6）。
4. **熱：** 熱適應會讓同樣強度的心率降低 12–14 bpm（`heat-acclimation.md`）。夏天的長天超標多半是熱造成的，不是跑太快。⑤ 只附註，不懲罰。

---

## 2. Q2：連續兩天長天（Back-to-Back，B2B）

### 2.1 同儕審查的證據

| 研究 | 發現 | 對 B2B 的意義 |
|---|---|---|
| **Maunder, Seiler, Mildenhall, Kilding & Plews 2021**，*Sports Med* 51:1619–1628，DOI 10.1007/s40279-021-01459-0，PMID 33886100（摘要） | 生理指標在長時間運動中「are not static, but change over time」；把 durability 定義為「the time of onset and magnitude of deterioration in physiological-profiling characteristics over time during prolonged exercise」 | B2B 想練的就是這個能力。這篇是概念和綜論，**沒有**測試 B2B |
| **Jones & Kirby 2025**，*Scand J Med Sci Sports*，DOI 10.1111/sms.70032（PMC11872681） | 「The inclusion of regular prolonged exercise sessions … might also represent an effective means of enhancing resilience」；長期累積大量訓練「might be a key stimulus」；但「data are scant」 | 支持「規律的長時間課」，但證據等級低，而且**沒有**直接談連續兩天 |
| **Tan, Tan & Bosch 2017**，*Int J Exerc Sci* 10:465–478，DOI 10.70252/hbei2580（PMC5609674） | 熱帶 161 km：完賽者 12 人、未完賽者 14 人。「longest run attempted」ES = 0.73、p = 0.07；交叉訓練時數 ES = 0.73 | 長跑的量和完賽有關（樣本小、只是邊緣顯著）。B2B 是累積長時間的一種做法 |
| **Burke, Hawley, Wong & Jeukendrup 2011**，*J Sports Sci* 29 Suppl 1:S17–27，DOI 10.1080/02640414.2011.585473（摘要） | 提高醣類可用度的方法包括「refuelling during recovery between sessions」；長時間運動中 30–60 g/h，> 2.5 小時「up to 90 g/h」 | 第 1 天練比賽補給，第 1 天晚上補回來，第 2 天才是真的在練「接續的那一天」。每日 g/kg 的建議在全文裡，**沒有讀到** |
| **Le Meur et al. 2013**，*MSSE* 45:2061–2071，DOI 10.1249/mss.0b013e3182980125（摘要） | 3 週超負荷造成功能性過度訓練（F-OR）：最大測驗表現 −9 %，靜止心率下降，副交感活性上升 | 累的時候心率會**偏低**。所以不能用「第 2 天心率比較低」當成體能變好 |
| **Berger et al. 2021**，*IJERPH* 18:12066，DOI 10.3390/ijerph182212066（個案） | 10 天 10 場馬拉松，強度約 60 % VO2max，心率 143 ± 4，「without any substantial physiological decrements」 | 低強度的多日負荷可以很穩定，前提是強度夠低。只是**個案** |
| **Bontemps et al. 2025**，*Eur J Sport Sci* 25:e12240，DOI 10.1002/ejsc.12240（轉引自 `interval-prescription.md`，沒有重新核對） | 做過 10 次下坡跑以後，股四頭肌痠痛 8.7 vs 29.6 mm（重複負荷效應） | 多日下坡的耐受度可以練出來。B2B 的第 2 天應該包含下坡 |
| **Nielsen 2014／Damsted 2019**（轉引自 `unsourced-rules.md` §B2） | 短期週量暴增 > 20–30 % 和受傷有關 | B2B 不應該讓週量增加；用 Koop 的「不加總量」來對應 |

**缺的證據：**

- 沒有找到「B2B vs 一天長跑（總時間相同）」的隨機試驗。
- 沒有找到多日賽或多日登山的訓練介入研究。
- 搜尋了 Europe PMC，關鍵字有「back-to-back」ultramarathon、consecutive days、multistage training predictors，找到的都是**比賽當中**的生理變化，例如七天七場馬拉松的水分代謝、TransEurope 的身體組成。

### 2.2 教練的說法

| 來源 | 什麼時候 | 怎麼排 | 前提和注意 | 類別 |
|---|---|---|---|---|
| **UA**（trail-running 頁） | 專項期，「two or three months prior to an athlete's A race」 | 比較長的賽事「replace the muscular endurance work with back-to-back long runs and one Zone 3 or Zone 4 interval workout」 | 「strategically overreaching and recovering is an excellent strategy, implemented only very occasionally, when training for ultra-length events」 | 教練經驗 |
| **Scott Johnston（UA，TrainingPeaks 部落格）** | 不限訓練期，偶爾用 | 「Overreaching could be two massive back to back weekend workouts followed by three of four light days」；前一週「slightly below-average」，之後「a substantial recovery period」 | 「sometimes employed by advanced athletes」；「Planned overreaching is not the same as randomly exercising」 | 教練經驗 |
| **UA Wonderland 3 天分段跑計畫** | 16 週計畫裡，週末逐步拉長 | 「The three-day series of progressively longer runs will help you build durability and endurance」；3 週加量、1 週恢復 | 「Runners should be able to handle 25-30 mile weeks to start and have experience running back-to-back longer days」 | 教練經驗 |
| **UA 登山／健行** | 行程前 8 週起，每週一次 4,000 ft（約 1,220 m）的爬升課 | 最長一課 ≈ 週有氧量的 50 %；背包 5 % → 10 % → 15 %（–20 %）體重，最多 25 % | 最後 1–2 週減量、保留強度 | 教練經驗 |
| **Koop（CTS）**〈Block Training〉 | — | B2B 的價值是「concentrate a large amount of training load in a short period of time」；「the first workout should be the harder of the two」；「Do not increase the total amount of time or miles you would normally run」 | 進步停滯、休息已經足夠才考慮；「history of injuries (>1 every 9 months)… I would not take the risk」 | 教練經驗 |
| **Koop**〈Do hardest workouts first〉 | — | 「position that two-day block after a 2-3 period of rest」（原文少了一個字，應該是 2–3 天） | 最硬的課放在最有精神的時候 | 教練經驗 |
| **Koop**〈Longest run〉 | — | 練補給的長跑「The minimum for this is 4 hours」 | 最長一次跑 20–80 % 賽事距離都有人成功 | 教練經驗 |
| **Andy Jones-Wilkins（CTS）**〈100 英里〉 | 「four to six weeks in advance of your event」 | 3 天自主訓練營，「The first day should be the longest」，例如 30 + 20 + 20 英里 | 心理效益最大 | 教練經驗 |
| **Rachel Spaulding（COROS 教練，2026-09-11）** | 新手賽前做 2–3 次就夠 | 「The first run should be more challenging」；第 2 天「a very easy effort」 | 跑步滿 1 年、賽事 > 20 英里、沒有受傷 | 廠商 |
| **Roche**（Trail Runner，付費牆） | — | 一次 40–50 英里的訓練容易受傷，分成兩天；Megan Roche 兩天 30–35 英里，第 1 天練速度、第 2 天練爬坡 | — | 教練經驗，**未驗證**（只有搜尋摘要） |
| **江晏慶**（你的筆記） | **基礎後期**：「連續兩天的 Back to Back 訓練」（`300 Sport/70 ⏳ 周期化訓練/越野跑周期化訓練(晏慶、K天王、丹尼爾).md:17–18`） | 專項巔峰期（賽前約 1.5 個月）「以貼近比賽的強度、距離、總爬升/下降、環境」整合，「抓比賽距離爬升的七成」（同檔 :40–43） | 長距離訓練有明顯突破後，安排 1.5 週恢復（同檔 :21） | 教練經驗 |
| 搜尋摘要（出處不明） | 「3 to 6 weeks before an ultra」；100 英里「biggest back-to-back… 4 to 5 weeks before」 | 50 英里／100K：25 + 20 英里，賽前 3 週 | — | **未驗證** |

### 2.3 你的看法對不對？

你的看法是「B2B 只放在長的多日賽事前的巔峰期」。

| 你的看法 | 判斷 | 依據 |
|---|---|---|
| 放在巔峰期／專項期 | **大致對** | UA（賽前 2–3 個月起）、CTS（4–6 週）、Koop（最後 2–3 週不要硬塞）。例外是江晏慶放在基礎後期，UA 的 overreaching 也不限訓練期 |
| 只為多日賽事 | **太窄** | UA 說「longer races」「ultra-length events」，COROS 說「> 20 英里」。單日 ≥ 6 小時的超馬或越野賽也適用，正好對應 app 的 `Event.is_long`（`planning.py:86–89`） |
| 單日短賽不需要 | **對** | 沒有任何來源建議 < 6 小時的單日賽做 B2B；UA 對短賽是保留肌耐力課（ME） |

**對你的情況再補兩點：**

- 你的百岳行程本身就是 2–3 天的 B2B，而且是背負、爬升、高海拔的最專項版本。
  所以「訓練用的 B2B」主要是為了 A 級多日行程或 A 級長賽事做**預演**，不是每個月都要做。
- 基礎期可以有「長天＋隔天 60 分鐘輕鬆」的小型版本（江晏慶）。
  但這不需要特別排，app 現在週末的輕鬆跑就有這個效果。
  這份文件只把「兩天都是長天」當成 B2B（**推估**的分界）。

### 2.4 什麼時候做（建議）

| 條件 | 建議 | 來源 |
|---|---|---|
| 訓練期 | 只在 `specific`（專項期）；app 的專項期是賽前第 10–3 週（`planning.py:30–31, 240–244`：減量 14 天、專項 8 週） | UA、CTS、Koop |
| 賽事類型 | 下一個 A 賽事的 `days > 1`，或 `is_long`（`est_hours ≥ 6`；沒有填時間就看 `days > 1` 或 ≥ 42 km） | UA「longer races」；`is_long` 的 6 小時是 app 既有的門檻（`LONG_EVENT_HOURS`，**推估**） |
| 最後一次 | 2 天版本：最晚在專項期最後一週（賽前 3 週）。3 天版本：賽前 4–6 週，只做一次 | 搜尋摘要「3 weeks」（**未驗證**）；CTS 4–6 週；Koop 最後 2–3 週不要硬塞 |
| 頻率 | 每個 3:1 週期最多 1 次，排在恢復週後的第一個加量週。8 週專項期大約 2 次，3 天行程再加 1 次 3 天區塊 | Johnston（前一週稍輕）、Koop（休息後）；次數是**推估** |
| 前提 | ① 近 28 天最長一次 ≥ 第 1 天的計畫時間 × 0.87（也就是 app 現有的「每次最多 +15 %」反推）② 不在停訓恢復期（reentry）、不在恢復週 ③ TSB ≥ −20（app 的「維持量」線）④ 有過連續兩天的經驗（百岳算） | ①③ 是 app 既有規則；④ 是 UA Wonderland 的前提；組合方式是**推估** |
| 不做 | 近 9 個月受傷 > 1 次（Koop）。app 沒有受傷紀錄，先當成使用者可以關閉的偏好 | Koop |

### 2.5 怎麼排

**兩天的長度和強度**

| 賽事 | 第 1 天（週六） | 第 2 天（週日） | 依據 |
|---|---|---|---|
| 百岳 2 天 | 山路或健行，app 現有的長天時間（`long_min`），全程 ≤ AeT，背包照下表 | 第 1 天的 0.6–0.7，≤ AeT，包含下坡 | CTS 30:20 ≈ 0.67；Koop、COROS「第 1 天比較硬」；0.6–0.7 是**推估** |
| 百岳 3 天（A 級） | 賽前 4–6 週做一次 3 天：第 1 天最長，第 2、3 天各為第 1 天的 0.6–0.7 | 同左 | CTS 30 + 20 + 20；UA Wonderland 用逐步拉長，兩家不同，這裡採 CTS（**推估**的選擇） |
| 單日長賽（≥ 6 小時） | ≥ 4 小時，練比賽補給 | 1.5–2.5 小時輕鬆 | Koop 4 小時補給；第 2 天的長度是**推估** |
| 單日短賽（< 6 小時） | 不排 B2B | — | 沒有來源支持 |

- **強度：** 兩天都以 AeT 為上限，第 1 天不放間歇。
  - Koop 說第 1 天比較「hard」，在越野的意思是比較長或爬升比較多，不是加間歇（COROS：「higher volume, intensity, or more elevation gain」）。
  - Roche 第 1 天加速度的做法，不適合你這種一週只有一次質量課的週期（**推估**）。
- **地形：** 第 1 天照 app 現在的「每公里爬升 ≥ 目標的 70 %」（江晏慶「抓七成」、`overview.py:644`）。
  第 2 天下坡比例比較高，練多日下坡的耐受度（Bontemps 的重複負荷效應）。
- **百岳背包：** app 預設 9 kg（`racepower/capacity.py:45–46`）。
  - 照 UA 的進度：5 % → 10 % → 15 % 體重，每階段約 2 週，最多 25 %。
  - 以 9 kg 來說，體重 60 kg 時是 15 %、70 kg 時是 13 %（體重以 `plan.weights` 為準）。
  - 建議：第一次 B2B 第 1 天背約一半（約 5 kg），第 2 次背到預設 9 kg，第 2 天比第 1 天輕或一樣（**推估**）。
- **補給：**
  - 第 1 天照比賽補給練：30–60 g/h，> 2.5 小時可以到 90 g/h（Burke 2011）。
  - 第 1 天晚上要補回來，第 2 天才是在練「接續的一天」，而不是在練低醣（Burke：refuelling between sessions）。
  - 百岳要練「背在身上、會吃得下的食物」（`300 Sport/400 🥾 裝備/🍜 輕量化食物.md`，這次沒有逐行引用）。

**一週怎麼放（對應你的限制：週末有空，平日 40–50 分鐘）**

| 星期 | B2B 週 | 依據 |
|---|---|---|
| 前一週 | 恢復週（app 的 3:1 本來就有） | Johnston「slightly below-average」、Koop「after rest」 |
| 一 | 休息或 30 分恢復跑 | — |
| 二 | 質量課（爬坡間歇），距週六 ≥ 3 天 | app 硬課間隔 48 小時（`plan_prefs.py:540–543`）；≥ 3 天是**推估** |
| 三、四 | 40–50 分輕鬆＋肌力（週四不排下肢大重量） | app「長跑前一天不排肌力」（`overview.py:820–824`） |
| 五 | 休息 | — |
| 六、日 | B2B | — |
| 下週一～四 | 只排輕鬆跑和肌力，**不排質量課**；週量照 app 的規則算 | Johnston「three or four light days」 |

**和週增量、TSB 護欄的關係**

- **週量不因為 B2B 增加。** 照 Koop 的「不加總量」，週量目標維持 `week_plan` 的算法：CTL ramp、≤ +10 %（至少 +0.5 h），以及 3:1。
  B2B 只是把週量**重新分配**到週末，平日的輕鬆跑跟著縮短。
  平日 40–50 分鐘的上限反而剛好，多出來的時間留給週日。
- **TSB 一定會掉。** 週一 TSB 很可能 < −20，甚至 < −30。
  - 這時 `adapt.py` 規則 E（TSB < −30 → 取消質量課、輕鬆跑 × 0.8）會觸發，這正好就是 Johnston 說的 3–4 天輕鬆，可以照常讓它觸發。
  - 但下一週的 `week_plan` 會因為 TSB < −30 把整週改成恢復週（`overview.py:482–484`）。
  - 建議：B2B 結束後的那一週，TSB 低於門檻如果是 B2B 造成的，只套「前 4 天輕鬆」，**不要**整週改成恢復週（**推估**）。否則 3:1 會變成 2:2。
- **CTL ramp：** B2B 週的 ramp 可能接近 `status.RAMP["short"]` = 8（Friel 5–8）。
  因為總量沒有增加，ramp 應該跟平常的加量週差不多。如果超過 8，代表週量算錯了，不是 B2B 的問題。

### 2.6 怎麼判斷做得好不好（不用 RPE）

**每一天：** 用 §1.3 的 ① 到 ⑤。

**第 2 天和第 1 天比：**

| 指標 | 怎麼算 | 現有程式 | 門檻 |
|---|---|---|---|
| ΔHR@VAM | 第 1 天在坡度 ≥ 10 %、心率 ≥ AeT 的爬坡段，擬合 HR ~ VAM；看第 2 天殘差的中位數（bpm） | `racepower/hikehr.py` 的 `fatigue`（百岳多日，「無外部來源（F17）」） | 只看方向 |
| ΔVAM@AeT | 坡度 ≥ 10 %、心率在 AeT −10 到 AeT 的段，第 2 天 ÷ 第 1 天 | `vo2max-gate-and-trail-metric.md` §2.4 (a) ③ | 下降 > 5 % 標「疲勞」（**推估**，沿用） |
| 解耦 | 兩天各自平路或緩坡、連續 ≥ 20 分鐘的段，前半和後半的 Pa:HR | `drift_of` 排除越野，要另外取段（steady window，`drift-algorithm.md`） | UA < 5 %（教練經驗） |
| 第 2 天的超標比例 | §1.3 ① | `workout_review` | 同 ① |

**沒有 RPE，就用心率和速度的 2 × 2 組合來判讀：**

| 第 2 天 vs 第 1 天 | 同 VAM 的心率**較低** | 同 VAM 的心率**差不多或較高** |
|---|---|---|
| **同心率的 VAM 差不多** | 不常見；可能是第 1 天熱或脫水 → 先看 Hadley | **耐久性好** ✔ |
| **同心率的 VAM 較低** | **肌肉疲勞**：心率被壓住，腿出不了力（UA；Le Meur 2013、Kerhervé 2015 都看到心率下降）→ 下一次縮短第 2 天 | **心血管漂移或補給不足**：同樣的速度要更高的心率（Coyle 2001）→ 檢查補給、熱、睡眠 |

- 這個分類完全不用 RPE。
- 「差不多」的界線建議 ±3 bpm、±5 % VAM（**推估**；3 bpm 和 AET_MARGIN 一致，5 % 和 UA 的 5 % 一致）。

**長期要看的（這才是目標）：**

- 同一個專項期裡每一次 B2B，第 2 天的 ΔVAM@AeT 有沒有變小。
- 第 2 天落在「耐久性好」那格的比例有沒有變高。
- 這直接對應 Maunder 的定義：衰退開始的時間和幅度。

**樣本：**

- 爬坡段太少時（第 1 天 < 5 段，`hikehr` 的 `FAT_MIN_N`），只顯示兩天的 ① 和總爬升，不判讀。
- 不要用平路配速取代：越野的配速受地形影響太大。

---

## 3. app 實作建議（只是提案，這次沒有改程式）

### 3.1 什麼時候自動排

在 `overview.week_plan` 建立長天的地方（`overview.py:632–655`）之後加判斷：

```text
b2b_due =
    kind == "specific"
    and mode not in ("recovery_week", "reentry")
    and target_event.is_long                        # days > 1 或 est_hours ≥ 6（planning.py:86–89）
    and weeks_to_event ≥ 3                          # 3 天版本：4 ≤ weeks_to_event ≤ 6，只排一次
    and last_week_was_recovery                      # 3:1 的第一個加量週；沒有恢復週就要距上次 B2B ≥ 3 週
    and longest28 ≥ 0.87 × long_min
    and tsb_today ≥ −20
    and prefs.b2b != "off"                          # 新偏好：auto / off（Koop 的受傷前提）
```

- `target_event`：要在 `status.goals` 加上「下一個 A 事件」本身。現在只有 `days_to_next_a`，以及合併後的 `targets.days`（`planning.py:292`，`GOAL_FIELDS["days"]`）。
- `targets.days` 是多個事件取最大值，可以先拿來用。

### 3.2 課表長什麼樣子

- `long`（週六，現有的）：分鐘數不變；`detail` 加上「B2B 第 1 天：練比賽補給 30–60 g/h；百岳背 X kg」。
- **新的** `long2`（週日，`kind="long"`）：
  - 分鐘數 = `round(0.67 × long_min / 5) × 5`。
  - `detail`：「B2B 第 2 天：心率 ≤ AeT，下坡比例高一點，不加速。」
  - 來源標「Koop／CTS；比例推估」。
- 週量：`long2` 的分鐘數從 `easy` 的總量扣掉（`overview.py:685–694` 的 `left`），不另外加。
- `plan_prefs.shape()`：
  - `long2` 要和 `long` 一樣不受 `cap_weekday` 限制，改用 `cap_long`（`plan_prefs.py:412–414`）。
  - `place()` 要把 `long2` 放在 `long` 的隔天；48 小時的硬課間隔要把 `long2` 也當成長天（`plan_prefs.py:540–546`）。
- 標記完成（`overview.py:722–723`）：`long2` 要用另一個 `id` 比對，條件一樣是 ≥ 0.8 × 計畫分鐘，而且日期要在 `long` 的隔天。
- 3 天版本：`long`、`long2`、`long3`。如果週五沒排課，就放五、六、日；要不然就提示使用者「這週請一天假」，不要自動塞進平日（使用者平日只有 40–50 分鐘）。

### 3.3 `adapt.py` 要多處理的情況

| 情況 | 建議 | 依據 |
|---|---|---|
| 第 1 天沒做 | 取消 `long2`，把週日改成一般長天（`long_min`）。規則 C 本來就會把長天移到同一週的空日 | 沒有第 1 天，B2B 就沒有意義（**推估**） |
| 第 2 天沒做 | 不補 | Seiler「easy days easy」，規則 A 的精神 |
| B2B 後 TSB < −30 | 規則 E 照常觸發（本週取消質量課），但下一週的 `week_plan` 不要整週改成恢復週（§2.5） | Johnston 3–4 天輕鬆；**推估** |
| 第 2 天落在「肌肉疲勞」那格 | 下一次 `long2` 的比例從 0.67 降到 0.5，連續兩次「耐久性好」才回到 0.67 | 自適應的劑量（**推估**） |

### 3.4 顯示

- 週日完成以後，在計畫頁顯示一張 B2B 卡片，內容有：
  - 兩天的 ①（超標比例）、ΔHR@VAM、ΔVAM@AeT；
  - 2 × 2 判讀的結果；
  - 這是本期第幾次 B2B，第 2 天的衰退和上一次比較。
- 可以接到 `coaching-dashboards-mountain.md` 的 Chart J（Big-day ladder）：2 天和 3 天的滾動總和。

---

## 4. 待決定

1. Q1 要不要用「AeT + 3 以上 ≤ 10 %」（和輕鬆跑同一把尺），取代「≤ AeT ≥ 85 %」？或兩種都記錄，累積 10 次長天以後再決定？
2. 陡坡走路的超標（§1.3 ③）要不要計入週的 3 區時間？
3. B2B 要不要也讓 B 級多日百岳觸發，還是只看 A 級？
4. 3 天版本要用 CTS 的「第 1 天最長」，還是 UA Wonderland 的「逐步拉長」？兩家不同，這份文件先採 CTS。
5. B2B 後的「只輕鬆 4 天、不改恢復週」要不要做成 `week_plan` 的例外？
6. 要不要新增偏好 `plan.prefs.b2b`（auto／off），給有受傷史的時候關掉？

---

## 5. 參考資料

### 同儕審查（只讀了摘要）

- Maunder E, Seiler S, Mildenhall MJ, Kilding AE, Plews DJ. The importance of 'durability' in the physiological profiling of endurance athletes. *Sports Med* 2021;51(8):1619–1628. DOI 10.1007/s40279-021-01459-0. PMID 33886100.
- Jones AM, Kirby BS. Physiological resilience: what is it and how might it be trained? *Scand J Med Sci Sports* 2025. DOI 10.1111/sms.70032. PMC11872681.
- Seiler KS, Kjerland GØ. Quantifying training intensity distribution in elite endurance athletes: is there evidence for an "optimal" distribution? *Scand J Med Sci Sports* 2006;16:49–56. DOI 10.1111/j.1600-0838.2004.00418.x. PMID 16430681.
- Sylta Ø, Tønnessen E, Seiler S. From heart-rate data to training quantification: a comparison of 3 methods of training-intensity analysis. *IJSPP* 2014;9(1):100–107. DOI 10.1123/IJSPP.2013-0298. PMID 24408353.
- Esteve-Lanao J, San Juan AF, Earnest CP, Foster C, Lucia A. How do endurance runners actually train? Relationship with competition performance. *MSSE* 2005;37:496–504. DOI 10.1249/01.mss.0000155393.78744.86. PMID 15741850.
- Stöggl TL, Sperlich B. The training intensity distribution among well-trained and elite endurance athletes. *Front Physiol* 2015;6:295. DOI 10.3389/fphys.2015.00295.
- Tan PL, Tan FH, Bosch AN. Assessment of differences in the anthropometric, physiological and training characteristics of finishers and non-finishers in a tropical 161-km ultra-marathon. *Int J Exerc Sci* 2017;10:465–478. DOI 10.70252/hbei2580. PMC5609674.
- Burke LM, Hawley JA, Wong SH, Jeukendrup AE. Carbohydrates for training and competition. *J Sports Sci* 2011;29 Suppl 1:S17–27. DOI 10.1080/02640414.2011.585473. PMID 21660838.
- Le Meur Y, Pichon A, Schaal K, et al. Evidence of parasympathetic hyperactivity in functionally overreached athletes. *MSSE* 2013;45:2061–2071. DOI 10.1249/mss.0b013e3182980125. PMID 24136138.
- Berger N, Cooley D, Graham M, et al. Consistency is key when setting a new world record for running 10 marathons in 10 days. *IJERPH* 2021;18:12066. DOI 10.3390/ijerph182212066.（個案）
- Coyle EF, González-Alonso J. Cardiovascular drift during prolonged exercise: new perspectives. *Exerc Sport Sci Rev* 2001;29:88–92. DOI 10.1097/00003677-200104000-00009.
- 轉引自其他研究文件、這次沒有重新核對：Bontemps 2025（DOI 10.1002/ejsc.12240）、Kerhervé 2015、Nielsen 2014（DOI 10.2519/jospt.2014.5164）、Damsted 2019（DOI 10.2519/jospt.2019.8541）、Minetti 2002。

### 教練經驗、廠商

- Uphill Athlete — Training for Trail Running：https://uphillathlete.com/trail-running/training-for-trail-running/
- Uphill Athlete — Multi-Day Stage Run（Wonderland）plan：https://uphillathlete.com/training-plans/wonderland-trail-rainier-ultra-running-training-plan/
- Uphill Athlete — Training for Mountaineering：https://uphillathlete.com/mountaineering/training-for-mountaineering/
- Uphill Athlete — Training for Trekking and Hiking：https://uphillathlete.com/trekking/training-for-trekking-and-hiking/
- Uphill Athlete — Heart Rate Drift：https://uphillathlete.com/aerobic-training/heart-rate-drift/
- Steve House（UA）— Durability Training: Efficiency Factor and Decoupling（2026-07-20）：https://uphillathlete.com/aerobic-training/durability-training-efficiency-factor-and-decoupling-for-endurance-athletes/
- Scott Johnston — Training Principles for the Uphill Athlete（TrainingPeaks）：https://www.trainingpeaks.com/blog/training-for-the-uphill-athlete-continuity-gradualness-and-modulation/
- Evoke Endurance 論壇，Seth Keena 回覆：https://evokeendurance.com/forums/topic/interpreting-heart-rate-drift-test/
- Jason Koop — How Block Training Can Help or Hurt Ultramarathon Training：https://trainright.com/block-training-ultrarunning-ultramarathon/
- Jason Koop — Which Comes First: Hardest or Easiest Workouts?：https://trainright.com/do-hardest-workouts-first/
- Jason Koop — How Long Should Your Longest Run Be Before An Ultramarathon?：https://trainright.com/longest-run-ultramarathon-training/
- Jason Koop — Should You Run or Hike That Hill?：https://trainright.com/run-walk-hill/
- Andy Jones-Wilkins（CTS）— The Best Training Tool for a 100-Mile Ultramarathon：https://trainright.com/the-best-training-tool-for-a-100-mile-ultramarathon/
- Rachel Spaulding（COROS，廠商）— Double the Fun: Back-to-Back Long Runs：https://coros.com/stories/coros-coaches/c/double-the-fun-back-to-back-long-runs
- Joe Friel — The Aerobic Base Ride（2009）：http://www.trainingbible.com/joesblog/2009/11/aerobic-base-ride.html；後續：http://www.trainingbible.com/joesblog/2009/12/aerobic-base-ride-more.html
- Gordo Byrn — Aerobic Efficiency Workouts（2023-12-07）：https://feelthebyrn.substack.com/p/aerobic-efficiency-workouts
- David & Megan Roche — Back-to-Back Long Runs and Workouts（Trail Runner，付費牆，**未驗證**）：https://run.outsideonline.com/training/workouts/back-back-long-runs-workouts-next-level-training-done-right/

### 你的筆記（notes `300 Sport`）

- 徐國峰部落格 2016-12 http://rocky549.blogspot.com/2016/12/rq.html（90 分鐘 1 區有氧基礎檢測）
- `70 ⏳ 周期化訓練/400 爆發力、敏捷性、專項耐力周期.md:5`（徐國峰：E 配速 90 分鐘飄移 < 10 %）
- `60 🏃 有氧訓練/丹尼爾的跑步方程式筆記 還有課表.md:10–12`（Daniels：L ≤ 25 % 或 150 分鐘）
- `70 ⏳ 周期化訓練/越野跑周期化訓練(晏慶、K天王、丹尼爾).md:17–18, 21, 40–43, 80–81`（江晏慶：基礎後期 B2B、恢復 1.5 週、巔峰期抓七成；Daniels：動作變形就刪減）
