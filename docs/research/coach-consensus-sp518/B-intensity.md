# SP-518 B-intensity：強度分配、強度預算、3 區占比、硬課間隔、每週強度課次數

組名：B-intensity｜查證日期：2026-10-14｜repo（唯讀）：`/Users/logan/Projects/TrailRunCoach-wt-integrate-1012a`

驗證標記照 BRIEF：
- **已驗證**＝這次用 WebFetch 讀到原句；
- **已驗證（劃線）**＝讀到 Goodreads 上讀者的 Kindle 劃線，是書的原文，但沒有頁碼，也看不到前後文；
- **未驗證（摘要）**＝只看到搜尋摘要；
- **二手**＝別人轉述，會寫是誰轉述；
- **沿用 <文件>§**＝repo 既有文件已驗證過，這次沒有重讀。

區間名稱先說清楚。app 的「低強度」是**心率 < AeT（≈ LT1／VT1）**。app 的 3 區是 88–95 % CP，落在 Seiler 三區的 Zone 2 上半段，也就是 LT1 和 LT2 之間；5 區是 > CP，等於 Seiler 的 Zone 3。手錶 %HRmax 5 區的 Zone 3 和 app 的 3 區不是同一件事（`coach-schools-zones-periodization.md` §2）。

---

## 1. 低強度占比護欄 `LOW_SHARE_MIN = 0.75`

### 程式位置與現值

- `backend/engine/quality_gate.py:118` `LOW_SHARE_MIN = 0.75`。
  - 實測 AeT 的人：低於 75 % 這週不排 5 區，3 區照排。
  - AeT 是估計值的人：只提醒（`quality_gate.py:709-717`）。
- `backend/engine/status.py:55` `LOW_SHARE_GOOD, LOW_SHARE_WATCH = 0.75, 0.65`。
  - 算法（`status.py:505-530`）：最近 4 週「心率 < AeT 的時間 ÷ 所有有心率的時間」。
  - ≥ 75 % 綠，65–75 % 黃，< 65 % 紅。
  - UI 文字是「底線 75%、目標 80%，基礎期目標 ≥ 90%」。
- `backend/engine/panels/period_zones.py:73-78`：`SRC_FLOOR`（75 %）和 `SRC_UA_BASE`（基礎期 ≥ 90 %）。
- **單位：時間；區間：AeT 以下（2 區模型：< AeT vs ≥ AeT）**。

### 來源表

| # | 作者 | 出處 | 原文數字與基準（單位／區間模型） | 驗證 |
|---|---|---|---|---|
| R1 | Seiler & Kjerland 2006 | *Scand J Med Sci Sports* 16:49–56，PMID 16430681 | 「Intensity distribution across endurance training sessions (n=318) … (75+/-3%, zone 1; 8+/-3%, zone 2; 17+/-4%, zone 3)」——**堂數**、VT 三區；青年越野滑雪選手 | 已驗證（Europe PMC 摘要） |
| R2 | Seiler & Tønnessen 2009 | *Sportscience* 13，https://www.sportsci.org/2009/ss.htm | 堂數：「About 80 % of training sessions are performed completely or predominantly at intensities under the first [ventilatory turn point]」。**時間**：「91 % of all training time was spent at a heart rate below VT1」，「only 2.6 % … above VT2」。作者提醒心率的區內時間會低估高強度 | 已驗證 |
| R3 | Seiler 2010 | *IJSPP* 5:276–291，PMID 20861519 | 「about 80% of training sessions are performed at low intensity」——**堂數** | 已驗證（摘要；沿用 `estimated-constants-inventory.md` §5.1，這次也重讀了） |
| R4 | Tønnessen et al. 2014 | *PLoS One*，PMID 25019608 | 「∼90% was low intensity training (LIT, below the first lactate threshold)」「10% … HIT … by time」；「23% of training sessions were characterized as HIT」——**時間**，LT1 為界；奧運／世錦賽金牌耐力選手 | 已驗證（摘要） |
| R5 | Esteve-Lanao et al. 2005 | *MSSE*，PMID 15741850 | Zone 1 4581 分、Zone 2 1354 分、Zone 3 487 分 → 約 **71／21／8 %（時間）**，VT 三區，次菁英跑者 | 已驗證（分鐘數讀到；百分比是我算的） |
| R6 | Esteve-Lanao et al. 2007 | *JSCR*，PMID 17685689 | **時間**：Z1 組 80.5 % Z1、11.8 % Z2、8.3 % Z3；Z2 組 66.8／24.7／8.5 %。5 個月，Z1 組進步較多（−157 s vs −121.5 s） | 已驗證（摘要） |
| R7 | Muñoz et al. 2014 | *IJSPP*，PMID 23752040 | **時間**：極化組 77／3／20 %、兩閾值之間組 46／35／19 %，休閒跑者 10 週 | 已驗證（摘要） |
| R8 | Stöggl & Sperlich 2014 | *Front Physiol* 5:33 | 極化組實際 **時間** 68／6／26 %（表 1）；HVT 組 83／16／1 % | 已驗證（全文表 1） |
| R9 | Stöggl & Sperlich 2015 | *Front Physiol*，PMID 26578968 | 「The majority of studies present a "pyramidal" TID with a high proportion of high volume, low intensity training」——沒有給數字 | 已驗證（摘要） |
| R10 | Campos et al. 2022（系統性回顧，中長跑） | *Int J Sports Med*，PMID 34749417 | 「high-volume at low-intensity (≥ 70% of overall training volume)」「threshold and high-intensity interval training (≤ 30%)」——**量**（多數原始研究用時間） | 已驗證（摘要） |
| R11 | Casado et al. 2022（系統性回顧，菁英跑者） | *IJSPP*，PMID 35418513 | 「typically follow a pyramidal TID approach」「toward a polarized TID during the competitive period」；沒有給百分比 | 已驗證（摘要） |
| R12 | Haugen, Sandbakk, Seiler, Tønnessen 2022 | *Sports Med Open* 8:46，PMID 35362850 | 「≥ 80% of the total running volume is performed at low intensity throughout the training year」——內文說是「time/distance-in-zone approach」 | 已驗證（摘要＋PMC 全文） |
| R13 | Rosenblat et al. 2019（統合分析，3 篇 RCT） | *JSCR*，PMID 29863593 | 極化 > 閾值型；沒有給低強度下限 | 已驗證（摘要） |
| R14 | Rosenblat et al. 2025（網絡統合分析，IPD） | *Sports Med* 55:655–673，PMID 39888556 | 極化和金字塔在 VO2max、計時賽都沒有差別；「Athletes at a more competitive level may benefit from a POL TID」；休閒者金字塔可能更好 | 已驗證（摘要） |
| C1 | Matt Fitzgerald／80/20 Endurance | https://www.8020endurance.com/understanding-your-8020-run-plan/ | 「The split is by time, not by number of runs」；「about 80 percent of your weekly running time at low intensity (Zones 1 and 2)」；「close is good enough」——**時間**，80/20 的 Z2 上緣 ≈ VT1 | 已驗證 |
| C1b | Fitzgerald《80/20 Running》讀書筆記 | https://run.wxm.be/books/80-20-running.html | 寫成「80% of runs being easy」，也說「It is never exactly 80/20」——書和官網的基準寫法不一致（筆記者轉述） | 二手（讀書筆記） |
| C2 | Uphill Athlete〈How to Train for Trail Running〉 | https://uphillathlete.com/trail-running/training-for-trail-running/ | 「spending the majority of your training time (90 percent or more) in Zones 1 and 2」「only 10 percent or less at higher intensities」——**時間**，UA 的 Z2 上緣＝AeT | 已驗證 |
| C3 | Uphill Athlete〈When and How to Add High-Intensity Training〉 | https://uphillathlete.com/aerobic-training/when-to-add-intensity-training/ | 菁英「spend 80 to 90 percent of their training volume at low intensity」 | 已驗證 |
| C4 | House & Johnston《Training for the Uphill Athlete》 | Kindle 劃線（Goodreads，Dmitry Khvatov），無頁碼，約全書 29 % 處 | 「80 percent of time below the AeT, roughly 6 to 8 percent between AeT and LT」——**時間**，AeT 為界（前後文看不到，應是在描述菁英的分配） | 已驗證（劃線） |
| C5 | Joe Friel | Substack〈Serious Training〉https://joefriel.substack.com/p/serious-training-33 | 「75-90% of their workouts were below the first lactate threshold」；「the ratio may vary from roughly 75/25 to 90/10」；也指出 Seiler 的數字「is not based on how much time was spent」——**堂數** | 已驗證 |
| C6 | Cliff Pittman（CTS） | https://trainright.com/ultrarunning-training-zones-mistakes-runners-make/ | 「about 80% of total training volume should be completed at an easy intensity」；「The exact percentages are less important」——量（沒寫時間或堂數） | 已驗證 |
| C7 | Jason Koop（CTS） | https://trainright.com/hierarchy-ultramarathon-training-needs-jason-koop/ | 高強度「maybe 20% of training sessions and only about 10% of total training hours」→ 依時間約 90 % 非高強度（他的「高強度」是 LT 以上） | 已驗證 |
| C8 | Ryne Anderson（CTS），UltraSignup | 搜尋摘要 | 「about 80% for amateurs and up to 90+% for elites」在 Zone 1–2 | 未驗證（摘要） |
| C9 | David Roche | `coach-schools-zones-periodization.md` [119] | 約 80／20 | 沿用（二手：podcast） |
| — | Daniels、Pfitzinger、Magness、Hudson | — | 沒有找到全週低強度的百分比（Daniels 只給 T／I／R／M 各自的上限，見 §2–3） | 沒找到 |

### 統計

只算「建議值」或菁英的典型值，而且基準是**時間或量、AeT／LT1 以下**的：

| 來源 | 數字 |
|---|---|
| C1 80/20 | 80 |
| C2 UA 越野 | ≥ 90 |
| C3 UA 菁英 | 80–90（取 85） |
| C4 TftNA | 80 |
| C6 CTS | 80 |
| C7 Koop | 約 90 |
| R10 Campos（下限） | ≥ 70 |
| R12 Haugen（下限） | ≥ 80 |
| R4 Tønnessen | 約 90 |
| R2 Seiler 依心率時間 | 91 |

- **眾數 80 %（5 個），中位數 80–85 %，範圍 70–91 %**。共 10 個，全部已驗證，其中 C4 是讀劃線。
- 有效的介入組（依時間）：68 % (R8)、77 % (R7)、80.5 % (R6)——低強度在 68–80 % 也有效。
- 唯一明講「下限」的系統性回顧：Campos **≥ 70 %**。
- 依**堂數**的 75–80 %（R1、R3、C5）不能直接拿來比：同一批選手依時間是 91 %（R2）。

### 換算

- app 用心率 < AeT 的時間，就是 R2、R4 的「心率區內時間」法。這種算法會把暖身、緩和、間歇之間的恢復都算成低強度，所以數字會比「堂數」法高。
- 換句話說，依時間的 75 % 比依堂數的 75 % 寬很多。
- 反方向的偏差：AeT 是估計值，或越野爬坡把心率拉高時，低強度會被低估。程式只對實測 AeT 的人擋 5 區，就是為了這個。

### 建議與影響

- **`LOW_SHARE_MIN`／`LOW_SHARE_GOOD` 0.75 → 維持。**
  - 它是「底線」，不是目標。底線落在唯一明講的下限 Campos ≥ 70 % 和教練目標眾數 80 % 之間。
  - 依時間的有效介入組 68–80 % 也都在附近。
  - 註解和 `SRC_FLOOR` 的來源改成：「推估；介於 Campos 2022（≥ 70 %，已驗證）和 80/20、UA、CTS 的約 80 %（依時間，已驗證）之間」。
  - 不要再引 Seiler 的 80/20：那是堂數。
- **UI 的「目標 80 %」→ 有來源了**：80/20 Endurance（依時間，已驗證）＋TftNA＋CTS。基礎期 ≥ 90 % 也有 UA 越野文（已驗證）。
- **`LOW_SHARE_WATCH` 0.65 → 可以考慮改成 0.70**（選擇性）。
  - 理由：紅燈就是「低於系統性回顧的下限」（Campos ≥ 70 %），比 0.65 有出處。
  - 影響：只影響狀態燈。65–70 % 的週會從黃變紅；護欄（0.75）不變，所以課表不變。
- 改成 0.80 的選項：教練眾數是 80，但 80 是**目標**，不是底線。拿它當擋 5 區的底線，會讓更多週不排 5 區（只影響實測 AeT 的人），而且沒有研究支持「低於 80 % 就有害」。**不建議。**

---

## 2. 強度預算 `QUALITY_SHARE_MAX = 0.20`

### 程式位置與現值

- `backend/engine/quality_gate.py:166`：一週間歇的區內時間（3 區＋5 區，只算工作段，不算暖身、恢復、緩和）÷ 計畫跑步時間 ≤ 20 %。
- 用在：`overview.py:1039-1040, 960, 1199`（超過就縮組數）、`plan_prefs.py:565-573`（放不下 2 堂就排 1 堂）、`technical.py:190, 238`。
- 現在的註解：「推估 — Seiler 2010's 80/20 counts sessions…；Koop」。
- **單位：時間（區內工作時間）；區間：LT1 以上的間歇（約等於 Seiler Zone 2＋3）**。

### 來源表

| # | 作者 | 出處 | 原文數字與基準 | 驗證 |
|---|---|---|---|---|
| C1 | Fitzgerald／80/20 Endurance | 同 §1 C1 | 「about 20 percent at moderate to high intensity (Zones 3 to 5)」——**每週跑步時間** | 已驗證 |
| C2 | Uphill Athlete（越野） | 同 §1 C2 | 「only 10 percent or less at higher intensities」——**時間** | 已驗證 |
| C3 | Uphill Athlete（加強度） | 同 §1 C3 | 「Start with total high-intensity work time equal to roughly 5 percent of your weekly aerobic volume」；FAQ：「can progress toward 10 percent for well-trained athletes」——**高強度工作時間 ÷ 週有氧量** | 已驗證 |
| C4 | TftNA | 同 §1 C4 | 80 % 在 AeT 以下 → 約 20 % 在 AeT 以上（其中 6–8 % 在 AeT–LT 之間） | 已驗證（劃線；20 % 是我算的） |
| C5 | Friel | 同 §1 C5 | 75/25 到 90/10——**堂數** | 已驗證 |
| C7 | Koop（CTS） | 同 §1 C7 | 「only about 10% of total training hours」；「If you run 10 hours a week (600 minutes), you'd have to average 60 minutes of work above lactate threshold just to hit 10%」——**時間，LT 以上的工作時間** | 已驗證 |
| D1 | Jack Daniels | Kindle 劃線（Goodreads Hilmi）；coachray.nz；active.com（Daniels 本人署名） | 單次課上限：T「not totaling more than 10 percent of your weekly mileage in a single workout」；I「capped at 8 percent of your total weekly mileage」；R「the lesser of 5 miles (8 km) and 5 percent of weekly mileage」；M（coachray）「less than 20% of weekly mileage」——**里程、每堂** | 已驗證（劃線＋Daniels 署名文章；I 的「10 km」只看到截斷的劃線，未驗證） |
| P1 | Pfitzinger《Faster Road Racing》 | 讀書筆記 https://run.wxm.be/books/faster-road-racing.html | VO2max「Total of 5000 to 8000」m、「Once per week tends to be plenty」；tempo「Steady block of 20 to 40 minutes」——**每堂** | 二手（讀書筆記） |
| K2 | Koop（書評轉述） | 搜尋摘要（book review） | VO2 間歇每堂 12–24 分、約每週 2 次、放週期前段 | 二手 |
| RO | David Roche | Canadian Running 2021（pressreader 403） | 每週 1 堂主課「10 to 30 minutes of faster running」＋strides | 未驗證（摘要） |
| R4 | Tønnessen 2014 | 同 §1 | 10 % HIT（LT1 以上）**依時間** | 已驗證 |
| R2 | Seiler & Tønnessen 2009 | 同 §1 | 依心率時間：約 6 % 在 VT1–VT2、2.6 % 在 VT2 以上 → 約 9 %（作者說心率法會低估） | 已驗證 |
| R6 | Esteve-Lanao 2007 | 同 §1 | 效果較好的組 Z2＋Z3 = 20.1 %（時間） | 已驗證 |
| R7 | Muñoz 2014 | 同 §1 | 極化組 Z2＋Z3 = 23 %（時間） | 已驗證 |
| R10 | Campos 2022 | 同 §1 | 閾值＋HIIT ≤ 30 %（量） | 已驗證 |
| R12 | Haugen 2022 | PMC 全文 | 「Training in zone 3 … represents 5–15% of the total running volume」；「Interval training in zone 4–5 also represents 5–15%」——**時間／距離**，菁英，合計 10–30 % | 已驗證 |

### 統計

「上限或建議值、依時間或量」的那幾個：

| 來源 | 數字 |
|---|---|
| UA 越野 | 10 |
| UA 加強度（練到後來） | 10 |
| Koop | 10 |
| Daniels（2 堂 Q 合計 T 10＋I 8，里程） | 18 |
| 80/20 | 20 |
| TftNA（推算） | 20 |
| Friel（上緣，堂數） | 25 |
| Campos（上限） | 30 |

- **中位數 19 %，眾數 10 %（3 個）和 20 %（2 個）**，範圍 10–30 %。
- 共 8 個，全部已驗證（TftNA 是劃線，Daniels 是劃線加本人署名文章）。
- 研究裡實測、依時間的值：9 %（R2）、10 %（R4）、20 %（R6 的好組）、23 %（R7）、32 %（R8）。
- 說穿了是兩派：
  - **UA、Koop：約 10 %**（高強度工作時間）；
  - **80/20、Campos、Daniels 合計：約 20 %**（中強度也算進去）。

### 換算

- app 只算間歇工作段的區內時間，不含暖身、恢復、緩和，也不含輕鬆跑裡飄高的時間。所以它比「心率區內時間」法更接近 UA、Koop 的「工作時間」，也比 80/20「Zone 3–5 的時間」窄。
- Daniels 是里程。T、I 配速比平均配速快約 15–25 %，所以「T 10 %＋I 8 % 的里程」換成時間約是 14–15 %（推估）。
- 實例（推估）：一週 5 h（300 分），上限 60 分。3 區 2×20（40 分）＋5 區 4×4（16 分）＝ 56 分，放得下兩堂。一週 3 h（180 分），上限 36 分：3 區上限 18 分＋5 區 16 分 ＝ 34 分。

### 建議與影響

- **`QUALITY_SHARE_MAX` 0.20 → 維持。**
  - 它是**上限**，不是目標。20 % 是教練、回顧的中位數，也和 80/20（依時間，已驗證）一致。
  - 真正在擋量的是 3 區 10 %（§3）和 5 區每堂 10–16 分。
- **來源說明改成**：「80/20 Endurance：每週跑步時間約 20 % 在 Zone 3–5（已驗證）；Campos 2022 ≤ 30 %；UA、Koop 約 10 %，是比較保守的一端」。拿掉「Koop ≤ 20 %」——Koop 原文是 **10 % 的時數**，不是 20 %。他說的 20 % 是**堂數**。
- 如果要照眾數 10 % 改：一週 5 h 的上限從 60 分變 30 分。這樣 3 區＋5 區兩堂（約 56 分）就排不下，等於每週只剩 1 堂強度課。這和 §5「每週 2 堂」的共識衝突，**不建議**。

---

## 3. 3 區占週量 `Z3_SHARE_START = 0.05`、`Z3_SHARE_MAX = 0.10`

### 程式位置與現值

- `backend/engine/quality_gate.py:168-169`；`quality_gate.py:220-225`。
  - 3 區軌道的第一堂：週計畫時間的 5 %。
  - 之後：每週 3 區區內時間 ≤ 週計畫時間的 10 %。
- 現在的註解：5 % 引 UA；10 % 引 Daniels（並註明 Daniels 是每堂、以里程算）。
- **單位：時間、每週；區間：app 的 3 區（88–95 % CP，LT1–LT2 的上半段）**。

### 來源表

| # | 作者 | 出處 | 原文數字與基準 | 驗證 |
|---|---|---|---|---|
| U1 | Uphill Athlete | 同 §2 C3 | 「Start with total high-intensity work time equal to roughly 5 percent of your weekly aerobic volume」；第一週一趟 10 分的 Zone 3；「Start with one session per week」——**時間、每週** | 已驗證 |
| U2 | Uphill Athlete | 同上 | 「can progress toward 10 percent for well-trained athletes」；到了約週量 10 % 的 Zone 3，就可以把部分 Zone 3 換成 Zone 4 | 已驗證 |
| U3 | TftNA | Kindle 劃線（約全書 45–46 % 處） | 「you can manage around 10 percent of the total weekly time in Zone 3」（之後開始加 Zone 4）——**時間、每週** | 已驗證（劃線） |
| U4 | TftNA | 同上（約 29 % 處） | 「roughly 6 to 8 percent between AeT and LT」——時間 | 已驗證（劃線） |
| U5 | TftNA 肌耐力期 | UA 論壇轉述 | 「one Z3 session that is 10% of weekly volume」；另一位網友說 Z3＋ME 合計 ~5 % 就好 | 二手（UA 論壇） |
| U6 | Scott Johnston（UA） | UA 論壇 | Zone 2 約週量 10–15 %（個案回覆） | 未驗證（摘要） |
| D1 | Daniels | 同 §2 D1 | T「not totaling more than 10 percent of your weekly mileage in a single workout」；cruise「maximum of 30 minutes at T pace」——**里程、每堂** | 已驗證（劃線＋coachray） |
| P1 | Pfitzinger | 同 §2 P1 | tempo 每堂 20–40 分 | 二手 |
| K1 | Koop | `coach-schools-zones-periodization.md` [62][117] | TempoRun 每堂 30–60（或 40–90）分 | 沿用（部分驗證） |
| R12 | Haugen 2022 | 同 §2 | 菁英長距離跑者 zone 3（閾值）占總跑量 5–15 %（時間／距離） | 已驗證 |
| R6 | Esteve-Lanao 2007 | 同 §1 | 效果較好的組 VT1–VT2 11.8 %（時間） | 已驗證 |
| R2 | Seiler & Tønnessen 2009 | 同 §1 | 約 6 % 在 VT1–VT2（心率時間） | 已驗證 |

### 統計

- **起步 5 %**：UA（U1，已驗證）是唯一講「起步量」的來源；UA 論壇另一位說 ~5 %（二手）。→ 1 個已驗證，沒有反例。
- **每週上限**：

  | 來源 | 數字 |
  |---|---|
  | UA（U2） | 10 |
  | TftNA（U3） | 10 |
  | Daniels（每堂） | 10 |
  | Haugen 5–15 % 中點 | 10 |
  | Esteve-Lanao | 11.8 |
  | TftNA（U4，6–8 % 中點） | 7 |
  | Seiler（R2） | 6 |

  **眾數 10 %（4 個），中位數 10 %，範圍 5–15 %**；7 個全部已驗證（2 個是劃線）。

### 換算

- **UA（U2、U3）的基準就是「每週、時間」，和 app 完全一致，不用換算**。這是這次最主要的改進：10 % 不必再借 Daniels（每堂、里程）。
- Daniels 每堂 T ≤ 週里程 10 %，換成時間約 8 %。T 配速約比平均配速快 20 %：10 % 里程 × 0.8 ≈ 8 % 時間（推估）。一週只有 1 堂 T 時，每堂上限就等於每週上限。
- UA 的 Zone 3 是 AeT 到 LT 整段。app 的 3 區只取上半段（88–95 % CP），所以同樣 10 %，app 的課略硬。

### 建議與影響

- **`Z3_SHARE_START` 0.05 → 維持。**
  - 來源標成「UA：total high-intensity work time ≈ 5 % of weekly aerobic volume（已驗證）」。
  - 註：UA 的 5 % 是**全部高強度**的起步量，第一階段只有 Zone 3，所以等於 3 區。
- **`Z3_SHARE_MAX` 0.10 → 維持，來源改成 UA。**
  - 新寫法：「UA〈When and How to Add High-Intensity Training〉：progress toward 10 percent；TftNA：around 10 percent of the total weekly time in Zone 3（都已驗證，每週、時間）；Daniels T ≤ 週里程 10 %（每堂，劃線）作為旁證」。
  - 拿掉「the book not verified (second-hand summaries)」：Daniels 的句子這次讀到 Kindle 劃線的原文了。
  - 影響：數字不動，課表不變。

---

## 4. 硬課間隔 2 天（48 h）

### 程式位置與現值

- `backend/engine/plan_prefs.py:672-684`：
  - `SRC_GAP = "台灣教練：5 區一週最多 2 次、間隔至少 2 天；強度課與長跑隔 ≥ 48 小時"`；
  - `_gap(...) >= 2`（星期幾的距離，跨週也算）。
- `plan_prefs.py:822-826`：強度課／測試和長跑、和本週其他硬課都要 `abs(days) >= 2`。
- `adapt.py:118`：`SRC_SPACING`。
- `overview.py:1248-1252`：週末間歇要離長跑 ≥ 2 天。
- **單位：日曆天（2 天 ≈ 48 h，中間至少 1 個非硬課日）**。

### 來源表

| # | 作者 | 出處 | 原文數字 | 驗證 |
|---|---|---|---|---|
| U1 | Uphill Athlete | 同 §2 C3 | 「Minimum 48 hours between high-intensity sessions. 72 is preferable.」 | 已驗證 |
| U3 | TftNA | Kindle 劃線（約 46 % 處） | 「do the Zone 3 and Zone 4 workouts on separate days and allow at least seventy-two hours between them」 | 已驗證（劃線） |
| F1 | Joe Friel | 〈How To Recover (Follow-Up)〉https://joefrieltraining.com/how-to-recover-follow-up/ | 「Such a session should typically require around 48 to 72 hours in order to recover before doing the next hard one」 | 已驗證 |
| B1 | Bompa & Buzzichelli 2015 | *Periodization Training for Sports* p.167–168 | 高強度日之間隔 48 小時，中間放輕鬆日 | 沿用 `rest-day-placement.md` §2 |
| S1 | Stanley, Peake & Buchheit 2013 | *Sports Med* 43:1259–1277，PMID 23912805 | 心臟副交感恢復：「up to 24 h following low-intensity exercise, 24-48 h following threshold-intensity exercise」「at least 48 h following high-intensity exercise」；建議依個人恢復速度調整 | 已驗證（摘要） |
| S2 | Seiler, Haugen & Kuffel 2007 | *MSSE* 39:1366–1373，PMID 17762370 | VT1 以下 ≤ 120 分「causes minimal disturbance in ANS balance」；VT1 以上 HRV 恢復延到 30 分（高度訓練者）到 ≥ 90 分（一般訓練者）——**分鐘到小時級，不是 48 h** | 已驗證（摘要） |
| S3 | Casado et al. 2022 | 同 §1 R11 | 「traditional periodization pattern with a hard day-easy day basis」 | 已驗證（摘要） |
| S4 | Seiler & Tønnessen 2009 | 同 §1 R2 | 「keeping hard training hard and easy training easy」（引 Gullich 2009）；沒有小時數 | 已驗證 |
| BW | Bill Bowerman | Kenny Moore 傳記、PMC 回顧表格的轉述 | 硬日、輕日交替（1950–60 年代）；沒有小時數 | 二手 |
| L1 | Lydiard | `coach-schools-zones-periodization.md` [96] | 2–3 天 | 沿用 |
| P2 | Pfitzinger《Advanced Marathoning》2 版 p.56（部落格轉述） | 搜尋摘要 | 「space workouts stressing the same system at least four to five days apart」；不同系統則看身體能承受多少 | 二手（未看到原文） |
| P3 | Pfitzinger《Faster Road Racing》5K 課表 | 讀書筆記 | 2 堂課排週二／週五或週三／週六，間隔 3 天 | 二手 |
| H1 | Brad Hudson《Run Faster》 | 部落格轉述 | 週二、週四主課＋週日長跑；「Put your easy days before your hard days」 | 二手 |
| D2 | Daniels 5K–10K 課表 Phase III | runningwithrock.com | 「Phase III includes back to back workouts on Wednesday and Thursday」——**反例** | 二手（部落格；讀到這句） |
| FZ | Fitzgerald《80/20 Running》 | 讀書筆記 | 「three hard days buffered by light ones」 | 二手 |
| HG | Higdon、UA、Koop（長跑隔天休息） | `rest-day-placement.md` §2 | 長跑後休息 | 沿用 |
| — | Magness、Koop（強度課間隔） | — | 沒找到小時數（Koop 有連兩天長跑） | 沒找到 |

### 統計

明講「最少幾小時」的：

| 來源 | 數字 |
|---|---|
| UA | 48 |
| Friel | 48（下限） |
| Bompa | 48 |
| Stanley（高強度） | ≥ 48 |
| Lydiard | 48–72 |
| TftNA（3 區↔4 區） | 72 |
| Pfitzinger（同系統） | 96–120 |

- **眾數 48 h（5 個），中位數 48 h，範圍 24–120 h**。
- 已驗證 4 個：UA、Friel、Stanley、TftNA（劃線）。沿用 2 個；二手 1 個。
- 另有 3 個說「72 更好」：UA、TftNA、Friel 的上緣。
- 反例：Daniels Phase III 連兩天 Q（二手）；Koop 連兩天長跑。

注意：Seiler 2007 **不支持** 48 h，它量到的是分鐘到小時級的 HRV 恢復。48 h 的生理依據應該引 Stanley 2013，不是 Seiler 2007。

### 換算

- `_gap >= 2` 天：週二、週四之間約 48 h（看練跑的時刻），中間隔 1 天。
- UA、TftNA 的「72 h 更好」＝ `_gap >= 3`（週二、週五）。

### 建議與影響

- **`_gap(...) >= 2` → 維持。** 48 h 是眾數，也是 UA、Friel、Bompa、Stanley 的共同下限。
- **`SRC_GAP` 改寫**：把「台灣教練」改成「UA：Minimum 48 hours … 72 is preferable；Friel 48–72 h；Bompa p.167–168；Stanley 2013：高強度後副交感恢復 ≥ 48 h（都已驗證）」。台灣教練可以保留在後面。
- **選擇性**：一週 2 堂強度課、可練日又夠的時候，偏好隔 3 天（例：週二＋週五），只當排序偏好，不擋課。來源是 UA、TftNA「72 preferable」。
  - 影響：只改變放的位置，量不變。
  - 會跟「長跑前後 ≥ 2 天」搶日子：一週 5 跑時，週二、週五、週日長跑會讓週五離長跑只差 2 天。這仍然合規。
- 不建議改成 72 h 硬規則：一週 ≤ 5 跑、長跑占一天時，常常會排不下第二堂。

---

## 5. 每週強度課次數上限

### 程式位置與現值（grep）

- `backend/engine/overview.py:1239-1245` `quality_per_week()`：預設每週 **1** 堂。課表偏好「每週品質課 = 2」時，基礎期、專項期排 **2** 堂；恢復週和基礎期護欄模式只排 1 堂。
- `backend/engine/plan_prefs.py:719-721`（`z5_twice`）、`base_check.py:82`（`SRC_Z5_LIMIT`）、`quality_gate.py:2355, 2359, 2422`：5 區「一週最多 2 次、隔 ≥ 2 天」（台灣教練）。
- `plan_prefs.py:565-573`：兩堂的區內時間 > 週量 20 % → 本週只排 1 堂。
- 長跑另計，不算在強度課裡。

### 來源表

| # | 作者 | 出處 | 原文數字 | 驗證 |
|---|---|---|---|---|
| S4 | Seiler & Tønnessen 2009 | 同 §1 R2 | 每週訓練 10–12 次的菁英「likely to dedicate 1-3 sessions weekly」在 MLSS 以上；「frequent high-intensity bouts (3-4 sessions per week)」會暫時壓低表現（overreaching） | 已驗證 |
| R12 | Haugen et al. 2022 | PMC 全文 | 「2–3 weekly interval sessions, a weekly long run, and fill the rest with as much LIT as you can handle」（文中引 Bowerman 的總結）；5000 m 選手「1–2 weekly interval training sessions in zone 6 or in combination with zone 5」 | 已驗證（WebFetch 讀到；後半頁只有參考文獻） |
| F1 | Friel | 同 §4 F1 | 「Young athletes can often do 3 or 4 such sessions in a week」；「Most over-50 athletes can generally manage two or three such hard workouts in a week」 | 已驗證 |
| U1 | Uphill Athlete | 同 §2 C3 | 「Start with one session per week and add a second only when you can comfortably handle the first」 | 已驗證 |
| U7 | Uphill Athlete（越野） | 同 §1 C2 | 專項期（VK 或較短的賽）「two interval workouts, one Zone 3 and one Zone 4」；長賽：連兩天長跑＋1 堂 Z3 或 Z4 間歇 | 已驗證 |
| U8 | TftNA | Kindle 劃線（約 44 % 處） | 「a single workout per week of these is plenty」（前後文被截斷，看不出是哪一種課） | 已驗證（劃線，脈絡不明） |
| R8 | Stöggl & Sperlich 2014 | 同 §1 R8 | 極化組 9 週 14 堂 HIIT（約 1.5 堂／週；重量週 2 堂、恢復週 1 堂） | 已驗證 |
| FZ | Fitzgerald《80/20 Running》 | 讀書筆記 | 「Two at moderate or high. One as long run」 | 二手 |
| D3 | Daniels | runningwithrock.com；Kindle 劃線 | 5K–10K：「There are three Quality days in any given week」（含長跑）；馬拉松有 2Q 課表 | 二手（部落格；讀到這句） |
| P3 | Pfitzinger《Faster Road Racing》 | 讀書筆記 | 5K 課表每週 2 堂；VO2max「Once per week tends to be plenty」 | 二手 |
| P2 | Pfitzinger《Advanced Marathoning》 | 部落格轉述 | 硬課 2–4 次／週，看選手和週期 | 二手（未看到原文） |
| H1 | Hudson | 部落格轉述 | 週二、週四＋週日長跑（2＋長跑） | 二手 |
| K2 | Koop | 書評轉述 | VO2 間歇約每週 2 次（block 內） | 二手 |
| RO | Roche | Canadian Running 2021 | 每週 1 堂主課＋strides | 未驗證（摘要） |
| X1 | 休閒跑者 HIIT 頻率研究 | `coach-schools-zones-periodization.md` [163]（*Physiol Rep*，DOI 10.14814/phy2.70573） | 4×4 每週 2–3 次都有效，3 次沒有比 2 次好 | 沿用 |
| S3 | Casado 2022 | `unsourced-rules.md` §B2 | 菁英每週 zone 2、zone 3 課各至少 1 次 | 沿用 |

### 統計

每週強度課（不含長跑）：

| 來源 | 數字 |
|---|---|
| Seiler（菁英） | 1–3（取 2） |
| Haugen | 2–3（取 2.5） |
| Friel（> 50 歲） | 2–3（取 2.5） |
| Friel（年輕） | 3–4（取 3.5） |
| UA 專項期 | 2 |
| UA 起步 | 1 |
| Stöggl | 1.5 |
| Fitzgerald | 2 |
| Daniels（3 Q 含長跑） | 2 |
| Pfitzinger FRR | 2 |
| Hudson | 2 |
| Koop | 2 |
| Roche | 1 |

- **眾數 2 堂，中位數 2 堂，範圍 1–3（菁英、年輕到 4）**。
- 已驗證 6 個：Seiler、Haugen、Friel、UA×2、Stöggl。
- 「3–4 堂會 overreaching」（Seiler，已驗證）是上限的依據。
- 5 區（VO2max）單獨看：Pfitzinger「一週一次就夠」（二手）；Koop block 內約 2 次（二手）；UA 專項期 1 堂 Z4（已驗證）。≤ 2 次在範圍內。

### 換算

不用換算。app 的「堂」就是教練的「session／Q day」。教練的「3 Q」常把長跑算進去，app 的長跑另計，所以 Daniels、Fitzgerald 的 3 等於 app 的 2。

### 建議與影響

- **每週上限 2 堂、預設 1 堂 → 維持。**
  - 上限 2 是眾數。
  - 預設 1 和 UA「Start with one session per week and add a second only when you can comfortably handle the first」完全一致。
- **5 區每週 ≤ 2 次 → 維持**；來源加上 UA、Seiler、Friel，不只寫台灣教練。
- **選擇性**：專項期預設改成 2 堂。來源是 UA 專項期「one Zone 3 and one Zone 4」，以及 Haugen、Daniels、Pfitzinger、Hudson 的 2 堂。
  - 影響：沒有手動設 2 的人，專項期會多一堂強度課。仍受 20 % 預算、3 區 10 %、48 h 間隔約束。
  - 這牽涉「給所有人用」的預設強度，**請使用者決定**。
- 不建議開放 3 堂：Seiler 說 3–4 堂是 overreaching 的做法；休閒跑者研究也看不出 3 堂比 2 堂好。

---

## 總表

| 常數 | 現值 | 共識（眾數／範圍） | 來源數（驗證） | 建議 |
|---|---|---|---|---|
| `LOW_SHARE_MIN`／`LOW_SHARE_GOOD`（依時間、< AeT） | 0.75 | 目標眾數 80 %、範圍 70–91 %；唯一明講的下限 Campos ≥ 70 % | 25 列（20 個已驗證；統計用 10 個，全部已驗證） | **維持 0.75**（底線介於 70 和 80 之間）；來源改成 Campos＋80/20、UA、CTS，拿掉「Seiler 80/20」；`LOW_SHARE_WATCH` 0.65 → 0.70 可選（只影響狀態燈） |
| `QUALITY_SHARE_MAX`（間歇區內時間 ÷ 跑步時間） | 0.20 | 中位數 19 %；眾數 10 %（UA、Koop）和 20 %（80/20、TftNA）並列；範圍 10–30 % | 15 列（12 個已驗證） | **維持 0.20**（上限取中位數）；來源改成 80/20 Endurance（時間，已驗證）＋Campos ≤ 30 %；Koop 原文是 10 % 時數、20 % 是堂數，要改正 |
| `Z3_SHARE_START` | 0.05 | 5 %（UA） | 2 個（1 個已驗證） | **維持**；標「UA 已驗證」 |
| `Z3_SHARE_MAX`（每週、時間） | 0.10 | 眾數、中位數 10 %，範圍 5–15 % | 12 列（10 個已驗證，其中 3 個是劃線） | **維持**；主要來源從 Daniels（每堂、里程）改成 UA＋TftNA（每週、時間，已驗證），Daniels 當旁證 |
| 硬課間隔 `_gap >= 2` | 2 天（48 h） | 眾數 48 h；範圍 24–120 h；3 個說 72 h 更好 | 17 列（7 個已驗證） | **維持**；`SRC_GAP` 改成 UA、Friel、Bompa、Stanley 2013；Seiler 2007 量的是分鐘到小時，不支持 48 h，不要引。可選：2 堂時偏好隔 3 天（只當排序偏好） |
| 每週強度課（預設 1、上限 2；5 區 ≤ 2） | 1／2 | 眾數 2 堂（不含長跑），範圍 1–3 | 16 列（6 個已驗證） | **維持**；5 區上限的來源加上 UA、Seiler、Friel。可選：專項期預設 2 堂（請使用者決定） |

## 這次沒查到、或要注意的

- Daniels 原書頁碼沒拿到。T、R 的句子讀到 Kindle 劃線；I 的「lesser of 10K or …」劃線被截斷，「10 km」未驗證。
- Daniels 原書有沒有寫 Q 課之間要隔幾個 E 日，沒找到。部落格說 Phase III 有連兩天的 Q（反例）。
- Pfitzinger「同系統 4–5 天」、Hudson、Koop、Roche 的每週堂數，都只有二手或摘要。
- Magness 沒有找到任何數字。
- 80/20 書和官網的基準寫法不一致：讀書筆記寫「80% of runs」，官網明講「by time, not by number of runs」。以官網（已驗證）為準。
- periodization-phase-metrics.md:97 把 Seiler & Kjerland 的 75／8／17 寫成「依心率」——摘要的原意是「依心率判斷**每堂課**的區間」，所以單位仍然是**堂數**，不是時間。這次重讀摘要確認了。
