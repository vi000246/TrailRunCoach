# SP-518 A-volume-load：週量增幅、週量上限、CTL 增幅、TSB 門檻

> 2026-10-14，子 agent `A-volume-load`。只查、不改 repo。repo：`/Users/logan/Projects/TrailRunCoach-wt-integrate-1012a`。
> 驗證標記照 BRIEF：**已驗證**＝這次 WebFetch 讀到原句（Europe PMC 只讀到摘要的標「已驗證（摘要）」）；**未驗證（摘要）**＝只看到搜尋摘要；**二手**＝別人轉述；**沿用 <文件>§**＝repo 既有文件已驗證，這次沒重讀。
> 恢復週的量不在本組範圍（另一組查）。

## 總表

| # | 常數 | 現值 | 共識（眾數／中位數／範圍） | 來源數（驗證） | 建議 |
|---|---|---|---|---|---|
| 1 | `STEP_HOLD, STEP_BLOCK`（load_guard.py:96） | 跑步時間比 max(上週, 前 4 週平均) 增 10–20 % 維持、> 20 % 擋 | 教練「每週最多加」：眾數 10 %、中位數 10 %、範圍 5–25 %（低基礎才到 20–25 %）；研究的危險線：20 %（Damsted 2019）、30 %（Nielsen 2013、2014），但 2 篇系統性回顧＋Frandsen 2025 都說週對週比例的證據很弱或沒有關係 | 教練 15（有數字 13，驗證 9）＋研究 10（驗證 10，多為摘要） | **維持 10 %／20 %**（10 % 是教練眾數，20 % 是研究裡最低的危險線） |
| 2 | 週量上限 `max(1.10×cap_ref, cap_ref+0.5)`（overview.py:1574） | 計畫時數最多 +10 %，至少 +0.5 h | 同上：+10 % 是眾數；低週量的人教練允許 15–25 % | 同 #1 | **+10 % 維持**；**+0.5 h 的下限建議加上限：最多 +20 %**（週量 < 2.5 h 時，+0.5 h 會超過 20 %，下週會被 app 自己的 20 % 擋線擋掉間歇） |
| 3 | `GOAL`（load_guard.py:104）基礎期 max(2, 5 %)、專項期 max(2.5, 7 %) | CTL 50 時每週 +2.5／+3.5 | 「可長期維持的加量速度」：中位數約 4.5 點／週、範圍 1–8；跑步背景的三個來源（Palladino 1–3、Suunto 2–5、Simmons 馬拉松 4–6）中位數約 3.5；TP 低風險表 ≤ 3／4／5（依 CTL） | 9（驗證 7、沿用 1、二手 1） | **維持**：基礎期落在 Palladino／Couzens／TP 低風險之間，專項期約等於跑步來源的中位數。週量 +10 % 上限幾乎都會先卡住，改了影響很小 |
| 4 | `WATCH_PCT/MIN` 0.10／3、`BLOCK_PCT/MIN/MAX` 0.15／5／10（load_guard.py:87-88） | CTL 50：注意 5、擋 7.5 | 「開始要小心」（注意）：CTL 50 時約 6 點（TP 中風險起點、Simmons、Coggan 5–7）＝12 %；「太多」（擋）：約 8 點（TP 高風險、Friel 崩潰區起點、Couzens）＝16 %；上限：Friel 10、TP 高風險在 CTL ≥ 70 是 ≥ 9 | 7（驗證 6、二手 1）＋ACWR 換算 3 | **維持**。app 在低 CTL 比 TP 表嚴、在高 CTL 略鬆（擋線上限 10 對 TP 的 9）；可選：`BLOCK_MAX` 10 → 9（只影響 CTL > 60 的人） |
| 5a | `TSB_HOLD` −20（quality_gate.py:121） | < −20 間歇不加量 | −20 到 −25：TP（Simone）「到 −20 要注意」、Couzens「−20 是安全的底」、Stryd 有效訓練下緣 −25 | 5（驗證 5） | **維持** |
| 5b | `tsb_today < -30` → 恢復週（overview.py:1598） | | 眾數 −30：Friel、Simmons、Simone 都寫 −30；Stryd 警戒 −25～−40、過度 < −40 | 6（驗證 5） | **維持** |
| 5c | `TSB_PRODUCTIVE` (−30, −10)（status.py:52） | | −30～−10：Friel、Simmons（驗證）；Stryd −25～−10 | 4（驗證 3） | **維持** |
| 5d | `TSB_A` (+10, +20)（status.py:51） | A 賽賽前 3 天內 | 各家中點的中位數約 +14；下緣中位數 +10、上緣中位數 +22.5；範圍 0～+25 | 10（驗證 7、沿用 2、摘要 1） | **維持**（+10～+20 正好在中間） |

**橫向發現（推估，數學推導，§6）**：CTL 穩定以每週 g % 成長時，TSB／CTL 和 ATL／CTL 是固定的：g = 5 % → TSB ≈ −23 % CTL（ATL／CTL 1.23）；7 % → −32 %（1.32）；10 % → −44 %（1.44）；15 % → −62 %（1.62）。所以**在 CTL 50 時，app 的四條線剛好互相對齊**：注意線 10 % ↔ TSB ≈ −22（≈ `TSB_HOLD` −20）、擋線 15 % ↔ TSB ≈ −31（≈ −30 恢復週）。但 TSB 是絕對值、ramp 是相對值：CTL 30 的人 ramp 線一定先觸發，CTL 70 的人 TSB −30 會比 ramp 注意線先觸發。TSB 跟 SP-63 的 ramp 一樣有「尺度跟著 TSS 算法縮放」的問題，詳見 §5.4。

---

## 1. 週跑量增幅護欄 `STEP_HOLD, STEP_BLOCK = 0.10, 0.20`

### 1.1 程式位置與現值

- `backend/engine/load_guard.py:96`：`STEP_HOLD, STEP_BLOCK = 0.10, 0.20`；註解「> 20 % block: Nielsen 2014, Damsted 2019 (peer-reviewed); 10–20 % hold 推估」。
- 量：**跑步（路跑＋越野）移動時間**，基準 `step_base` = max(上週, 前 4 週平均)，跳過減量期／比賽週／恢復期／轉換期（`load_guard.py:300-306`、SP-63／SP-73）。
- 用途：`quality_gate.py:723-727`：> 20 % 本週不排間歇；10–20 % 間歇不加量。

### 1.2 來源表

**教練與平台**

| # | 作者 | 出處 | 原文數字與基準 | 驗證 |
|---|---|---|---|---|
| C1 | COROS Coaches（無具名） | https://coros.com/stories/coros-coaches/c/safely-build-running-volume | 「increase your weekly mileage by no more than 10% compared to the previous week」；「A 5 to 10% weekly increase is usually appropriate」；接近之前的最高量時「move closer to the 10% Rule」。基準：**上週里程** | 已驗證 |
| C2 | Andrew Simmons（COROS Coaches，2025-07-15） | https://coros.com/stories/coros-coaches/c/what-is-a-productive-training-load | 「The old "10 percent rule" … still has value, but must be flexible」；「Load increases of 15 to 20 percent may be productive for athletes starting from a low baseline」。基準：週訓練負荷 | 已驗證 |
| C3 | Runna（help center） | https://support.runna.com/en/articles/11598695 | 「increase your weekly mileage by no more than 10%–15% above your current average」；3 週加量＋1 週減量。基準：**目前平均**里程 | 已驗證 |
| C4 | Ryne Anderson（CTS／Koop 體系，2025-09-09） | https://trainright.com/how-to-determine-weekly-run-training-volume/ | 「you can increase the average level of weekly volume by 10%」，之後「Eight to twelve weeks at this new level of volume is a safe period before making the subsequent 10% increase」；回到上個週期的最高量先停 4–8 週。基準：**平均週量**，而且是一階一階、每階停 8–12 週 | 已驗證 |
| C5 | Cliff Pittman（CTS，2026） | https://trainright.com/10-running-rule-reframed/ | 週量「Stop thinking about your progression in terms of a set percentage of running hours or miles」；只給長跑：「your longest long run should not increase by more than 10%」（對 30 天內最長，**用時數**） | 已驗證（不算進週量統計） |
| C6 | Steve House（Uphill Athlete） | https://uphillathlete.com/skiing/how-to-train-for-skiing/ | 轉換期「weekly volume rising seven to ten percent at a time」。基準：**週時數** | 已驗證（沿用 `periodization-cross-sport.md` [441]，這次重讀） |
| C7 | Uphill Athlete 書（*Training for the Uphill Athlete* p.304） | UA 論壇員工回覆轉述 | 距離、爬升、時間「10% or less week to week」，而且「10% is the maximum increase not the recommended increase」；每第 4 週減 50 % 以上 | 二手（UA 論壇員工轉述；搜尋摘要，原帖 404） |
| C8 | Uphill Athlete 論壇員工 | UA 論壇（搜尋摘要） | 每週 +10 % 第 3 週特別累 →「5% is a saner bet」 | 未驗證（摘要） |
| C9 | Jack Daniels（*Daniels' Running Formula*） | RunnersConnect／teamrunrun 等轉述 | 每次加「每週跑幾次就加幾英里」（每次跑 +1 英里），一次最多 +10 英里；**每 3–4 週才加一次**；6 週內跑過的量不算加量 | 二手（未讀原書） |
| C10 | Jason Fitzgerald（Strength Running，2010-06-23） | http://strengthrunning.com/2010/06/how-to-increase-mileage/ | 新手同一量停「3-4 weeks at a time」；有經驗約 10 %，加一天時「may increase by 15 or 20%」；接近高峰「only 5-10% and hold it at that level for several weeks」；每週 50–60 英里以上「at least 3-4 weeks of adjustment at each level」 | 已驗證 |
| C11 | Luke Humphrey（Hansons） | https://lukehumphreyrunning.com/increasing-mileage-the-10-rule/ | 從約 10 英里／週起步「the sweet spot … is 20-25%」；4–6 週「at a rate of 15-25% per week」；停跑後回來 25 %→20 %→10 % | 已驗證 |
| C12 | Jeff Gaudette（RunnersConnect，2011） | https://runnersconnect.net/?p=6471 | 10 % 是迷思；範例 50→55→60→50→60→65→70→60（加量週 +8–10 %），「3 week up, 1 week down」 | 已驗證 |
| C13 | Greg McMillan | Outside 文章（付費牆） | 「not increase your weekly mileage by more than 10% per week」，且只連加 2–3 週 | 未驗證（摘要） |
| C14 | David Roche（SWAP） | Trail Runner 文章（付費牆） | 「by no more than 10 percent per week」 | 未驗證（摘要） |
| C15 | USA Triathlon Magazine | https://www.active.com/running/articles/2-rules-for-building-your-running-base?page=2 | 基礎期「implement the 10 percent increase concept」；進階者「may be able to increase their weekly volume by 15 to 20 percent」 | 已驗證 |
| — | Pfitzinger | 只有來路不明的 PDF 鏡像寫「no more than 10%」 | 不採用（來源不可靠） | — |
| — | Higdon、Hudson、Matt Fitzgerald、Magness、Friel | 搜尋過，找不到週增幅數字（Hudson 只說 adaptive；Magness 反對只看量；Friel 用年時數表） | 查不到 | — |

**研究**

| # | 研究 | 出處 | 數字與基準 | 驗證 |
|---|---|---|---|---|
| R1 | Buist 2008（GRONORUN，RCT，532 新手） | *Am J Sports Med* 36(1):33–39，DOI 10.1177/0363546507307505 | 13 週「based on the 10% training rule」組受傷 20.8 %，8 週標準組 20.3 %：「no effect of a graded training program」 | 已驗證（摘要） |
| R2 | Nielsen 2014（874 新手） | *JOSPT* 44(10):739–747，DOI 10.2519/jospt.2014.5164 | 2 週內週距離增 > 30 % 對 < 10 %：HR 1.59（0.96–2.66）；「progress their weekly distances by less than 30% per week over a 2-week period」 | 已驗證（摘要）；沿用 `unsourced-rules.md` §B2 |
| R3 | Damsted 2019（261 人備半馬） | *JOSPT* 49(4):230–238，DOI 10.2519/jospt.2019.8541 | 「significantly more runners were injured when they increased their weekly running distance by 20% to 60%」（21 天時） | 已驗證（摘要）；沿用 |
| R4 | Nielsen 2013（GPS，60 新手） | *J Strength Cond Res* 27(6):1471–1478，DOI 10.1519/jsc.0b013e3182711e3c | 受傷者週量增 31.6 %、未受傷者 22.1 %；「may become deleterious at a weekly increase above 30%」 | 已驗證（摘要） |
| R5 | Damsted 2018（系統性回顧） | *Int J Sports Phys Ther* 13(6):931–942，DOI 10.26603/ijspt20180931 | 「Very limited evidence exists supporting that a sudden change in training load is associated」；10 % 與 24 % 平均增幅沒有差別 | 已驗證（摘要） |
| R6 | Fredette 2022（系統性回顧） | *J Athl Train* 57(7):650–671，DOI 10.4085/1062-6050-0195.21 | 「current evidence does not consistently link RRIs with specific training parameters」 | 已驗證（摘要） |
| R7 | Frandsen 2025（Garmin-RUNSAFE，5,205 人） | *BJSM* 59(17):1203–1210，DOI 10.1136/bjsports-2024-109380 | 「No relationship was identified for the week-to-week ratio」；ACWR「negative dose-response」；單次 > 30 天最長的 10–30 % HRR 1.64 | 已驗證（摘要）；全文沿用 `ctl-ramp-calibration.md` §4.4 |
| R8 | Gabbett 2016 | *BJSM*，DOI 10.1136/bjsports-2015-095788（PMC4789704） | 「limit weekly training load increases to <10%」；≥ 15 % 時受傷風險 21–49 %——資料是**職業聯盟式橄欖球、未發表** | 已驗證（全文） |
| R9 | Cloosterman 2026（461 休閒跑者，GPS） | *J Athl Train*，DOI 10.4085/1062-6050-0030.25 | 依時間算的 ACWR HR 1.33（1.02–1.74）；作者說「clinical relevance … is questionable」 | 已驗證（摘要） |
| R10 | Maupin 2020（系統性回顧，各運動） | *Open Access J Sports Med*，DOI 10.2147/oajsm.s231405 | ACWR「0.80-1.30 demonstrating the lowest risk」 | 已驗證（摘要） |

### 1.3 統計

- **教練的「每週最多加多少」**（取每個來源的主數字；有範圍的取上緣，低基礎例外另列）：C1 10、C2 10、C3 10–15（取 12.5）、C4 10、C6 10（7–10）、C7 10、C10 10、C12 10、C13 10、C14 10、C15 10。
  → **眾數 10 %、中位數 10 %**；11 個來源，已驗證 8 個（C1、C2、C3、C4、C6、C10、C12、C15）。
- **低基礎的例外**：C2 15–20、C10 15–20（加一天）、C11 20–25、C15 15–20（進階）、C9（每次跑 +1 英里：每週 20 英里、跑 5 天時是 +25 %，但 3–4 週才加一次）。→ 範圍 15–25 %。
- **很多教練加完要停**：C4 停 8–12 週、C9 停 3–4 週、C10 停 3–4 週（高量）、C13 只連加 2–3 週、C12／C3 3 加 1 減。→ 「每週 +10 %」是**單週上限**，不是每週都要加。
- **研究的危險線**：R3 20 %、R2 30 %、R4 30 %、R8 15 %（橄欖球，未發表）→ 中位數約 25 %，最低的跑步研究是 20 %。R1、R5、R6、R7 都說週增幅（或 10 % 規則）跟受傷的關係很弱或沒有。
- **驗證數**：教練 15 個（有週增幅數字 13 個，其中已驗證 9：C1、C2、C3、C4、C6、C10、C11、C12、C15；C5 已驗證但沒有週增幅數字）；研究 10 個全部已驗證（R8 全文，其餘摘要）。

### 1.4 換算

- 基準：教練幾乎都是**週里程**，app 用**跑步移動時間**。越野的距離受坡度影響太大，時間比較公平（推估，SP-63 已決定）；同一週的時間比例和距離比例在平路幾乎一樣。C6 本來就用時數。
- 分母：教練多半是「上週」（C1）或「目前平均」（C3、C4）；app 用 max(上週, 前 4 週平均)，比「上週」寬（減量週後回升不算加量）。這跟 Daniels「6 週內跑過的量不算加量」（C9）方向一致。

### 1.5 建議與影響

- **維持 10 %（維持）／20 %（擋）**。10 % 是教練眾數；20 % 是跑步研究裡最低的危險線（Damsted 2019）。研究整體說週增幅的預測力很弱（R1、R5、R6、R7），所以不建議再嚴。
- 註解可以補：10 % 改標「教練眾數（COROS、Runna、Koop/CTS、UA、Fitzgerald、Gaudette 等 8 個驗證過）」，不再寫「推估」。
- 沒有改值 → 課表沒有變化。

---

## 2. 週量上限 `cap = max(1.10 × cap_ref, cap_ref + 0.5)`

### 2.1 程式位置與現值

- `backend/engine/overview.py:1574`；`cap_ref` = `LG.step_base(norm_wk)`：最近正常週（跳過減量期／比賽週／恢復期／轉換期）的 max(上週, 4 週平均)，單位是**全部運動的移動時數**（`_week_hours`，`overview.py:700-703`）。
- 每週排的時數 = min(max(CTL 目標需要的時數, 4 週平均), cap)。新手（資料還在累積）直接用 cap（`overview.py:1577-1582`）。

### 2.2 來源

同 §1.2（C1–C15）。跟上限直接相關的：

- **+10 %**：C1、C2、C3（10–15）、C4、C6（7–10）、C7（上限）、C10、C13、C14、C15。
- **低週量時允許更多**：C2「15 to 20 percent … from a low baseline」、C11「20-25%」（約 10 英里／週）、C10「15 or 20%」（加一天）、C9（每次跑 +1 英里）。Runna 的平台說明也寫「maximum increase allowed each week is calculated based on your current weekly mileage, so lower-mileage runners can still progress」（未驗證（摘要））。

### 2.3 統計

- 上限 +10 %：眾數、中位數都是 10 %（同 §1.3）。
- +0.5 h 下限換算成比例：週量 5 h → +10 %；4 h → +12.5 %；3 h → +16.7 %；2.5 h → +20 %；2 h → +25 %；1.5 h → +33 %；1 h → +50 %。
- 教練給低基礎的上限：15–25 %（C2、C10、C11、C15），沒有來源給到 +33 % 以上。

### 2.4 換算

- 時數 vs 里程：教練是里程，app 是全運動時數（推估同 §1.4）。
- **跟 #1 的衝突**：cap 用全運動時數，擋線用跑步時間；跑步占比固定時兩者比例一樣。週量 < 2.5 h 的人，cap 的 +0.5 h 下限會排出 > +20 % 的週，**下週就被 app 自己的 20 % 擋線擋掉間歇**（`quality_gate.py:723`）。

### 2.5 建議與影響

- **+10 % 維持**（教練眾數）。
- **+0.5 h 下限改成「最多 +20 %」**：`cap = max(1.10 × ref, min(ref + 0.5, 1.20 × ref))`（推估；20 % = 低基礎教練範圍 15–25 % 的中間，也等於 app 自己的擋線）。
  - 影響：只有週量 < 2.5 h（全運動）的人。例：2 h → 上限 2.4 h（原本 2.5 h）；1.5 h → 1.8 h（原本 2.0 h）；1 h → 1.2 h（原本 1.5 h）。量變少一點，但下週不會因為自己排的量被擋間歇。
  - 週量 ≥ 2.5 h 的人完全不變。
- 「加完要停」（C4、C9、C10）：app 的 3:1 是連 3 週 +10 % 再減量，比 Koop／Daniels 快。這牽涉恢復週（另一組），這裡不建議改。

---

## 3. 每週 CTL 目標增幅 `GOAL = {"base": (0.05, 2.0), "specific": (0.07, 2.5)}`

### 3.1 程式位置與現值

- `backend/engine/load_guard.py:104`、`ramp_goal`（`:363-368`）：基礎期每週 +max(2, 5 % × CTL)，專項期 +max(2.5, 7 % × CTL)。
- `overview.py:1572-1575`：需要的 TSS = 7 × (CTL + ramp_goal ÷ f7)，換成時數，再被 §2 的 cap 卡住。
- SP-63 的來歷（沿用 `ctl-ramp-calibration.md` §4.1）：原本基礎 +3、專項 +4 點，CTL 55–60 時等於 5 %／7 %，「維持現狀再換成比例，不是依來源重訂」。

### 3.2 來源表（「建議／可長期維持的加量速度」）

| # | 作者 | 出處 | 原文數字與基準 | 驗證 |
|---|---|---|---|---|
| G1 | Joe Friel | https://joefrieltraining.com/the-ctl-ramp-rate/ （同文也在 TrainingPeaks「Why Ramp Rate is an Important Training Metric」，2015-05-08） | 「an increase in CTL of about 5 to 8 points per week is about right for most」；> 10 只給少數菁英、「a week or less」 | 已驗證 |
| G2 | Andrew Coggan | https://www.trainingpeaks.com/learn/articles/the-science-of-the-performance-manager/ | 「>5-7 TSS/d/wk for four or more weeks, is often a recipe for disaster」→ 長期上限 5–7 | 已驗證 |
| G3 | Andrew Simmons（TP，2020） | https://www.trainingpeaks.com/learn/articles/a-coachs-guide-to-atl-ctl-tsb/ | 「Very fit athletes can increase their CTL up to five-to-seven points a week」 | 已驗證 |
| G4 | Andrew Simmons（TP，2018，**馬拉松**） | https://trainingpeaks.com/blog/planning-for-a-marathon-pr-with-fitness-ctl-and-the-atp-tool/ | 「Most athletes can handle a ramp rate of 4 to 6 points per week without too much discomfort」；常常 > 6「your goal may be too aggressive」（例子 CTL 45–75） | 已驗證 |
| G5 | Maria Simone（TP，2022，三鐵） | https://www.trainingpeaks.com/blog/track-triathlon-training-trainingpeaks/ | 「A safe 7-day ramp rate will reflect an increase in CTL of about 4 to 8 points」；表：CTL ≤ 45 低風險 ≤ 3、中 4–6、高 ≥ 7；45–70 低 ≤ 4、中 6–7、高 ≥ 8；≥ 70 低 ≤ 5、中 6–8、高 ≥ 9 | 已驗證 |
| G6 | Alan Couzens | https://www.alancouzens.com/blog/CTLramp.html | 「a more conservative long term ramp of 3-5 CTL per week (or ~10-20 per 4 week block)」；「Target CTL ramp rate = ~10 per month」；能長期 5／週的人「are the exception」 | 已驗證（沿用 `ctl-ramp-calibration.md` §4.4，這次重讀） |
| G7 | Steve Palladino（跑步功率） | Obsidian 課程筆記 `palladino基礎期訓練.md:48-54` | 基礎期 CTL 每週 +1–3（約 2–5 %），「避免用過時的『10% 升量法則』」 | 沿用 `detraining.md:249` |
| G8 | Suunto（2023-10-13，SuuntoRun） | https://www.suunto.com/sports/News-Articles-container-page/how-to-follow-your-progress-with-suunto/ | 新手／回歸「a weekly increase in CTL between 2–5 units」；中等經驗「A weekly increase of 5 units in CTL or 15–20 in a 6-week period」 | 已驗證 |
| G9 | TrainerRoad | intervals.icu 論壇轉述 | 「6 TSS neighborhood (+/- 2)」——是**每週 TSS 的增量**，不是 CTL | 二手；不算進統計 |
| — | Hunter Allen | https://bikeraceinfo.com/training-fitness/training-stress-balance.html | 只講 TSB 正負，沒有 ramp 數字 | 已驗證（無數字） |
| — | Stryd | https://blog.stryd.com/2020/02/19/obeying-the-immutable-law-of-stress-with-running-stress-balance/ | 沒有 ramp 建議（只看 RSB） | 已驗證（無數字） |
| — | McMillan、Runna、COROS | 都用里程／自家負荷，不用 CTL ramp。COROS 的 Intensity Trend「between 100 and 150 percent is considered "Optimized"」是短期／長期負荷的比值（Simmons 2025，已驗證），換算見 §4.4 | — |

### 3.3 統計

- 取每個來源「一般人可以長期用的加量速度」中點（點數／週）：G7 2、G8 新手 3.5、G6 4、G5 低風險（CTL 50）4、G4 5、G8 中等 5、G3 6（5–7，上限）、G1 6.5。G2 是「連 4 週以上不要超過 5–7」，當上限不算中點。
  → **中位數 4.5、眾數 4–5、範圍 1–8**。8 個有數字的來源，已驗證 7（G7 沿用筆記）。
- **只算跑步背景**（G4 馬拉松、G7 跑步功率、G8 SuuntoRun）：中點 5、2、3.5 → 中位數 3.5。
- 所有來源都是點數；只有 Palladino 自己附了百分比（2–5 %）。點數背後的 TSS 都沒有指定種類（`ctl-ramp-calibration.md` §2.2）。

### 3.4 換算：點數 vs 程式的「CTL 的百分比」

| 來源 | 點數／週 | CTL 30 | CTL 50 | CTL 70 |
|---|---|---|---|---|
| **app 基礎期** max(2, 5 %) | — | **2.0**（6.7 %，下限生效） | **2.5** | **3.5** |
| **app 專項期** max(2.5, 7 %) | — | **2.5**（8.3 %，下限生效） | **3.5** | **4.9** |
| Palladino 1–3 | 1–3 | 3–10 % | 2–6 % | 1.4–4.3 % |
| Suunto 新手 2–5 | 2–5 | 7–17 % | 4–10 % | 3–7 % |
| Couzens 3–5 | 3–5 | 10–17 % | 6–10 % | 4–7 % |
| TP 低風險（依 CTL 分級） | ≤ 3／≤ 4／≤ 5 | ≤ 10 % | ≤ 8 % | ≤ 7 % |
| Simmons 馬拉松 4–6 | 4–6 | 13–20 % | 8–12 % | 6–9 % |
| Friel 5–8 | 5–8 | 17–27 % | 10–16 % | 7–11 % |

- app 基礎期在 CTL 50 是 2.5 點：比 Palladino 上緣（3）低一點、在 Couzens 下緣以下、在 TP 低風險以內。專項期 3.5 點：約等於跑步來源的中位數。
- 教練的點數隨 CTL 增加得比線性慢（TP 表 3→4→5），換成百分比是「CTL 越高、% 越低」。app 的固定 % 在 CTL 70 時（3.5／4.9）仍在 TP 低風險 5 以內，CTL 100 以上（5／7）才會到 TP 中風險。

**為什麼改了影響很小（推估，數學）**：CTL 一週的變化 ≈ 0.155 × (這週平均每日負荷 − CTL)。從穩定狀態（週量 = CTL 水準）開始，週量 +10 %（cap）只讓 CTL 一週 +1.5 %；要 +5 % 需要週量 +32 %。所以 cap 幾乎每週都先卡住（`ctl-ramp-calibration.md` §4.1 也寫了），GOAL 只在**恢復週或低量週之後**（週量低於 CTL 水準）才會是決定因素。

### 3.5 建議與影響

- **維持**。理由：基礎期 5 % 在 Palladino（跑步功率）與 Couzens／TP 低風險之間；專項期 7 % 約等於跑步背景來源的中位數；所有教練數字都來自自行車／三鐵為主的 CTL 50–100、而且是點數，跑步本來就該取下緣（Stryd：機械負荷，`ctl-ramp-calibration.md` §2.4）。
- 如果一定要往共識中位數（4.5 點）靠：專項期改 max(3, 8 %)（CTL 50 → 4 點，等於 TP 低風險上緣）——但因為 cap 先卡住，課表時數幾乎不會變；只有恢復週後那週會多排一點。**不建議為此改**。
- 註解可補：「基礎 5 %：Palladino 2–5 %／Couzens 3–5 點；專項 7 %：Simmons 馬拉松 4–6、Suunto 2–5、Palladino 1–3 的中位數 3.5 點在 CTL 50 的換算」。

---

## 4. CTL 增幅注意線／擋線 `WATCH_PCT, WATCH_MIN = 0.10, 3.0`、`BLOCK_PCT, BLOCK_MIN, BLOCK_MAX = 0.15, 5.0, 10.0`

### 4.1 程式位置與現值

- `backend/engine/load_guard.py:87-88`：注意 ≥ max(3, 10 % × CTL₋₇)、擋 ≥ min(10, max(5, 15 % × CTL₋₇))；Δ = CTL(d) − CTL(d−7)。
- 用在狀態卡（黃／紅）、間歇護欄（只排閾值下／不排間歇）、自動調整 E 規則與 B2B（擋線）。

### 4.2 來源表（「要小心」與「太多」）

| # | 作者 | 出處 | 「要小心」 | 「太多」／上限 | 驗證 |
|---|---|---|---|---|---|
| W1 | Friel | 同 G1 | 5–8 是「about right」，「More than that and you're starting to get into the crash training load range」→ > 8 | 10 以上只給菁英、≤ 1 週 | 已驗證 |
| W2 | Coggan | 同 G2 | — | > 5–7 連 4 週以上 | 已驗證 |
| W3 | Simone（TP） | 同 G5 | 中風險起點：4（CTL ≤ 45）、6（45–70）、6（≥ 70） | 高風險起點：7、8、9 | 已驗證 |
| W4 | Simmons（TP 馬拉松） | 同 G4 | 常常 > 6 → 目標太激進 | — | 已驗證 |
| W5 | Simmons（TP 2020） | 同 G3 | — | 很 fit 的人上限 5–7 | 已驗證 |
| W6 | Couzens | 論壇轉述他的經驗法則 | CTL < 45：低 4、中 6 | 高 8（加量週用，不是長期平均） | 二手（論壇） |
| W7 | Couzens | 同 G6 | — | 「2,4,6 rule, i.e. ramp rate of 20」（每月 20 ≈ 每週 5）只給「proven」的運動員 | 已驗證 |

另：**ACWR**（短期／長期負荷比）是同一件事的另一種寫法，§4.4 換算：Gabbett 2016「0.8–1.3 sweet spot」「≥ 1.5 danger zone」（R8，已驗證全文；資料是板球、澳式足球、橄欖球）；Maupin 2020 系統性回顧 0.8–1.3 最低風險（R10）；COROS Intensity Trend 100–150 % 是「Optimized」（Simmons 2025，已驗證）。跑步上：Frandsen 2025 ACWR 跳升反而受傷率較低（R7）、Cloosterman 2026 HR 1.33 但臨床意義存疑（R9）。

### 4.3 統計（換成 CTL 50 的點數與 %）

- 「要小心」：W3 6、W4 6、W2 5–7（中 6）、W1 8（崩潰區起點）、W6 6（CTL < 45 的數字，參考）→ **中位數 6 點 ＝ 12 %**；範圍 5–8。
- 「太多」：W3 8、W1 8–10（取 8 為崩潰區、10 為上限）、W6 8 → **眾數 8 點 ＝ 16 %**；上限 Friel 10、TP（CTL ≥ 70）≥ 9。
- 7 個來源，已驗證 6。

### 4.4 換算

**各 CTL 下 app 與 TP 表對照（點數）**

| CTL | app 注意 max(3, 10 %) | TP 中風險起點 | app 擋 min(10, max(5, 15 %)) | TP 高風險起點 | Friel |
|---|---|---|---|---|---|
| 30 | 3（10 %） | 4（13 %） | 5（17 %，下限） | 7（23 %） | 5–8 正常、> 8 崩潰 |
| 50 | 5（10 %） | 6（12 %） | 7.5（15 %） | 8（16 %） | 同上 |
| 70 | 7（10 %） | 6（8.6 %） | 10（14 %，上限） | 9（13 %） | 10 是上限 |

→ app 在 CTL 30 比 TP 嚴（注意早 1 點、擋早 2 點）；CTL 50 幾乎一樣（各早 0.5–1 點）；CTL 70 注意線晚 1 點、擋線晚 1 點。TP 表的形狀是「點數隨 CTL 慢慢長」，app 是「下限＋固定 %＋上限」，兩者在 CTL 45–60 交會。

**換成 ACWR（推估，數學；用 app 的 42／7 天線性遞迴模擬，CTL 穩定以每週 g % 成長）**

| 每週 CTL 增幅 g | ATL／CTL（≈ EWMA 的 ACWR） | TSB／CTL |
|---|---|---|
| 3 % | 1.14 | −14 % |
| 5 %（app 基礎期目標） | 1.23 | −23 % |
| 7 %（app 專項期目標） | 1.32 | −32 % |
| 10 %（app 注意線） | 1.44 | −44 % |
| 15 %（app 擋線） | 1.62 | −62 % |

→ 排課目標落在 Gabbett 的 0.8–1.3 甜蜜區（專項期在上緣）；注意線 1.44 剛好在 1.5 危險區下面；擋線 1.62 在危險區裡；COROS「Optimized」上限 150 % ≈ 每週 +11–12 %。Gabbett 的 ACWR 是滾動平均（7 天／28 天），不是 EWMA 7／42，所以只是量級對照（推估）。

### 4.5 建議與影響

- **維持** 10 %／3、15 %／5／10。理由：CTL 45–60 時兩條線各比 TP 表早 0.5–1 點觸發，比 Friel 保守；跑步取下緣有理由（Stryd 機械負荷；app 的 hrTSS 偏高，`ctl-ramp-calibration.md` §3.1）。ACWR 換算也一致（注意線在危險區下緣、擋線在危險區內）。
- **可選**：`BLOCK_MAX` 10 → 9（TP 高風險在 CTL ≥ 70 是 ≥ 9；Friel 10 是「菁英最多一週」）。影響：只有 CTL > 60 的人，擋線早 1 點觸發，間歇被擋的週變多一點。證據不強，看使用者要不要。
- **不建議**放寬低 CTL 的下限（`BLOCK_MIN` 5 → 7 去配 TP）：TP／Suunto 都說新手、低 CTL 要更保守，而 hrTSS 在低 CTL 的人身上相對誤差更大。
- 註解可補 TP（Simone 2022）的表當第二個來源。

---

## 5. TSB 門檻

### 5.1 程式位置與現值

| 常數 | 位置 | 用途 |
|---|---|---|
| `TSB_HOLD = -20` | `quality_gate.py:121`、`:728` | −30 ≤ TSB < −20：間歇不加量 |
| `tsb_today < -30` | `overview.py:1598` | 改恢復週（近 4 週的 60 %；60 % 由另一組查） |
| `tsb_today < -20` | `overview.py:1605` | 週量 ≤ 4 週平均（不加量） |
| `TSB_PRODUCTIVE = (-30, -10)`、`TSB_OVERREACH = -30` | `status.py:52-53` | 狀態卡：有效訓練區、< −30 紅 |
| `TSB_A = (10, 20)` | `status.py:51` | A 賽前 3 天內的目標 |
| `TSB_STALE = 25` | `status.py:54` | > +25「體能在流失」（不在本題，但順便對照） |

app 的 TSB = 昨天的 CTL − ATL（`ctl-ramp-calibration.md` §1.1），跟 TrainingPeaks 的「Form 是明天的」定義一致。

### 5.2 來源表

| # | 作者 | 出處 | 原文數字 | 驗證 |
|---|---|---|---|---|
| T1 | Joe Friel（TP，2015，2026 更新） | https://www.trainingpeaks.com/learn/articles/applying-the-numbers-part-3-training-stress-balance/ | A 賽「Form at around plus 15 to plus 25 on race day」；有些人「just barely positive, around plus 5 to plus 10」；B 賽「between negative 10 and 0」；訓練「negative 10 to negative 30 range」；「below negative 30 greatly increases your risk of injury or illness」；−10～+10 是過渡區、待久＝停滯；「north of plus 25 your training is much too easy」 | 已驗證 |
| T2 | Andrew Simmons（TP 2020） | 同 G3 | 「ideal training zone for TSB falls between -10 and -30」；「beyond -30 they are headed for extreme strain」、要連續休息；比賽轉述 Friel +15～+25 | 已驗證 |
| T3 | Maria Simone（TP 2022） | 同 G5 | 「I don't recommend exceeding a -30 Training Stress Balance」；「pay attention to how you feel once you hit -20」 | 已驗證 |
| T4 | Andrew Coggan | 同 G2 | 「TSB of less than -10 would usually not be accompanied by the feeling of very "fresh" legs」；> +10 通常會；−10～+10 中性；**沒有給賽前目標** | 已驗證 |
| T5 | Alan Couzens | 同 G6 | 3 週加量後「Target TSB floor … ~= -20 TSB」、「a TSB of -20 is a safe 'bottom'」；proven 運動員「TSB floor of -40」；恢復週回到 ~0 | 已驗證 |
| T6 | Stryd（RSB，跑步） | https://intercom.help/stryd/en/articles/6879346 ；https://blog.stryd.com/2020/02/19/obeying-the-immutable-law-of-stress-with-running-stress-balance/ | 「Overreaching -45 to -40」「Cautionary -40 to -25」「Productive -25 to -10」「Maintenance -10 to 5」「Performance 5 to 25」；賽前「exceed a value of +5」；「Anything beyond +25 means that you have not been training recently」 | 已驗證 |
| T7 | Hunter Allen | https://bikeraceinfo.com/training-fitness/training-stress-balance.html | 「A positive TSB will most likely predict a peak performance」；沒有數字 | 已驗證（無數字） |
| T8 | Phil Crick（TP ATP，2016） | https://www.trainingpeaks.com/learn/articles/a-look-at-planning-by-tss-with-the-new-atp/ | 賽日 TSB 約 +10 | 沿用 `periodization-cross-sport.md` [472] |
| T9 | Palladino | Obsidian `300 Sport/65 ⚡ 功率訓練/圖表筆記/PMC規劃比賽周期.md` | A：TSB +10 到 +20，CTL 下降不超過一週；B：−5 到 +5 | 沿用 `late-race-entry.md:98`（筆記） |
| T10 | Roadman Cycling | https://roadmancycling.com/blog/cycling-taper-pmc-performance-management-chart | 「Most riders target a race-day TSB of around +5 to +15」；masters「+10 to +15」；「much beyond +25, you've usually gone too far」 | 已驗證 |
| T11 | Coach Jess（MyProCoach，2026） | https://support.myprocoach.net/hc/en-us/articles/360041024691 | 「Form 0 to +25: Fresh, sharp, and primed for peak performance」；「-5 to 0: Slightly tired but strong and ready」 | 已驗證 |
| T12 | Zwift Insider | 搜尋摘要 | 15–25 | 未驗證（摘要） |
| T13 | intervals.icu | 論壇（`/t/zones-of-form-in-fitness-chart/3623`） | 區名 Transition／Fresh／Grey／Optimal／High Risk，「The zones come from Joe Friel and are for absolute TSB」；Form % 模式沿用同一組數字；有使用者讀成 High Risk 從 −30 % 開始 | 已驗證（論壇使用者，不是官方文件）；「新帳號預設用 %」只看到摘要 |

研究：**沒有找到用 TSB 當賽前目標或訓練門檻的研究**；減量的研究（Bosquet 2007）用量的減幅，不用 TSB。ctyeh.com 說的「2020 統合分析 +15～+25」找不到原研究，不採用。

### 5.3 統計

**(a) `TSB_HOLD` −20（訓練中開始要小心）**：T3 −20、T5 −20、T6 −25（有效區下緣）、T1／T2 有效區 −10～−30 的中點 −20、T4 −10（不新鮮，不是警告）。→ **眾數 −20、範圍 −10～−25**；5 個，驗證 5。

**(b) −30 改恢復週（太多）**：T1 −30、T2 −30、T3 −30、T6 −40（過度起點；警戒從 −25）、T5 −40（只給 proven）、T13 −30（Friel 數字）。→ **眾數 −30、範圍 −25～−40**；6 個，驗證 5（T13 論壇）。

**(c) `TSB_PRODUCTIVE` (−30, −10)**：T1、T2 −30～−10；T6 −25～−10；T13 Optimal（Friel）。→ **−30～−10 一致**；驗證 3。

**(d) `TSB_A` (+10, +20)**：

| 來源 | 範圍 | 中點 |
|---|---|---|
| T1 Friel | +15～+25（部分人 +5～+10） | 20 |
| T8 Crick（TP ATP） | ≈ +10 | 10 |
| T9 Palladino | +10～+20 | 15 |
| T6 Stryd（跑步） | +5～+25 | 15 |
| T10 Roadman | +5～+15（masters +10～+15） | 10 |
| T11 MyProCoach | 0～+25 | 12.5 |
| T12 Zwift Insider | +15～+25 | 20 |
| T4 Coggan、T7 Allen | > +10 新鮮／正值 | — |

→ 中點中位數 **15**（7 個；只算驗證過的 5 個是 12.5）；下緣中位數 +10、上緣中位數 +22.5；範圍 0～+25。app 的 +10～+20（中點 15）正好落在中間。

### 5.4 換算：TSB 的尺度

TSB 跟 CTL 一樣會跟著 TSS 算法縮放（SP-63 的論點）。同一個絕對值，在不同 CTL 代表的疲勞差很多：

| TSB | CTL 30 | CTL 50 | CTL 70 |
|---|---|---|---|
| −10 | −33 %（ATL／CTL 1.33） | −20 %（1.20） | −14 %（1.14） |
| −20 | −67 %（1.67） | −40 %（1.40） | −29 %（1.29） |
| −30 | −100 %（2.00） | −60 %（1.60） | −43 %（1.43） |
| +10 | +33 %（0.67） | +20 %（0.80） | +14 %（0.86） |
| +20 | +67 %（0.33） | +40 %（0.60） | +29 %（0.71） |

- **TSB 與 ramp 的關係**（推估，數學）：CTL 以每週 R 點**穩定直線**成長時，TSB ≈ −5R（ATL 落後 7 天、CTL 落後 42 天，差 35 天 × 每天斜率）。所以 Friel 的 ramp 5–8 ↔ TSB −25～−40；有效訓練區 −10～−30 ↔ ramp 2–6；`TSB_HOLD` −20 ↔ ramp 4；−30 ↔ ramp 6。教練的 ramp 數字和 TSB 數字是互相吻合的同一套經驗。
- **用 %（§4.4 表）**：CTL 50 時注意線 10 % ↔ TSB ≈ −22、擋線 15 % ↔ TSB ≈ −31，四條線剛好對齊。CTL 30 時 TSB −20 要 ACWR 1.67，ramp 線一定先觸發（TSB 門檻幾乎沒作用）；CTL 70 時 −30 只要 ACWR 1.43，比 ramp 注意線（1.44）還早觸發。
- 賽前：CTL 70 的人用 Bosquet 的減量（量 −41～−60 %）兩週，TSB 容易超過 +20，會被判「太新鮮」；CTL 30 的人同樣減量約 +12～+15。教練的 +15～+25 主要來自 CTL 60–100 的自行車／三鐵。

### 5.5 建議與影響

- **四個 TSB 數字都維持**：−20、−30、(−30, −10)、(+10, +20) 都是各自的眾數或中位數；`TSB_STALE` +25 也有 Friel、Stryd、Roadman 三家同數。
- 註解可補：`TSB_HOLD` 的來源改寫成「TP（Simone 2022）到 −20 要注意；Couzens：−20 是安全的底」；`TSB_A` 加「Stryd +5～+25、TP ATP ≈ +10、Friel +15～+25 的中間」。
- **尺度問題留給使用者決定（不在本單的「改數字」範圍）**：要像 SP-63 的 ramp 一樣改成相對值，可以照 intervals.icu 的 Form % 做法（數字沿用、單位換成 % CTL；先例只在論壇看到，未驗證官方文件）。若要換，建議的對應（推估，用 CTL 50–70 把教練數字換成 %，取中間）：維持線 −35 % CTL、恢復週 −50 % CTL、有效區 −15～−50 %、A 賽 +15～+35 %——但這會讓 CTL 30 的人 TSB 門檻變嚴、CTL 70 的人變鬆，要先回測。**不建議在沒有回測前改**。

---

## 6. 換算方法備註（推估）

- 模擬：用 app 的線性遞迴（CTL += (L − CTL)/42、ATL += (L − ATL)/7），讓每日負荷以 e^(kt) 成長、k = ln(1+g)/7，跑 2,000 天到穩定，量一週的 CTL 增幅與 ATL／CTL。結果（§4.4 表）跟連續近似 ATL／CTL = (1+42k)/(1+7k) 一致。
- 直線成長：L = a + st → CTL ≈ L − 42s、ATL ≈ L − 7s → TSB ≈ −35s，週 ramp R = 7s → **TSB ≈ −5R**。
- 一週 CTL 變化 ≈ (1 − (41/42)^7) × (每日平均負荷 − CTL) ≈ 0.155 ×（差距），用來說明 §3.4 的 cap 先卡住。

## 7. 來源清單

教練／平台：
- Friel, *The CTL Ramp Rate* — https://joefrieltraining.com/the-ctl-ramp-rate/ ；同文 TP *Why Ramp Rate is an Important Training Metric* — https://www.trainingpeaks.com/learn/articles/why-ramp-rate-is-an-important-training-metric/
- Friel, *Applying the Numbers Part 3: Training Stress Balance*（TP）— https://www.trainingpeaks.com/learn/articles/applying-the-numbers-part-3-training-stress-balance/
- Coggan, *The Science of the Performance Manager*（TP）— https://www.trainingpeaks.com/learn/articles/the-science-of-the-performance-manager/
- Simmons, *A Coach's Guide to ATL, CTL & TSB*（TP 2020）— https://www.trainingpeaks.com/learn/articles/a-coachs-guide-to-atl-ctl-tsb/
- Simmons, *Marathon Planning Using Fitness (CTL) and the ATP Tool*（TP 2018）— https://trainingpeaks.com/blog/planning-for-a-marathon-pr-with-fitness-ctl-and-the-atp-tool/
- Simone, *Tracking the Build Phase of Triathlon Training with TrainingPeaks*（TP 2022）— https://www.trainingpeaks.com/blog/track-triathlon-training-trainingpeaks/
- Couzens, *CTL ramp rates, TSB floors & loading patterns* — https://www.alancouzens.com/blog/CTLramp.html
- Hunter Allen, *What is Training Stress Balance* — https://bikeraceinfo.com/training-fitness/training-stress-balance.html
- Stryd RSB help — https://intercom.help/stryd/en/articles/6879346 ；Stryd blog 2020 — https://blog.stryd.com/2020/02/19/obeying-the-immutable-law-of-stress-with-running-stress-balance/
- Suunto, *How to follow your progress with Suunto*（2023）— https://www.suunto.com/sports/News-Articles-container-page/how-to-follow-your-progress-with-suunto/
- COROS Coaches, *How to Safely Increase Your Running Volume* — https://coros.com/stories/coros-coaches/c/safely-build-running-volume
- Simmons, *What Is a Productive Training Load*（COROS 2025）— https://coros.com/stories/coros-coaches/c/what-is-a-productive-training-load
- Runna help — https://support.runna.com/en/articles/11598695
- Anderson（CTS）, *How to Determine Weekly Run Training Volume* — https://trainright.com/how-to-determine-weekly-run-training-volume/
- Pittman（CTS）, *10% running rule reframed* — https://trainright.com/10-running-rule-reframed/
- House（Uphill Athlete）, *How to Train for Skiing* — https://uphillathlete.com/skiing/how-to-train-for-skiing/
- J. Fitzgerald, *Forget the 10% Rule* — http://strengthrunning.com/2010/06/how-to-increase-mileage/
- Humphrey, *Increasing mileage: the 10% rule?* — https://lukehumphreyrunning.com/increasing-mileage-the-10-rule/
- Gaudette（RunnersConnect）— https://runnersconnect.net/?p=6471
- USA Triathlon / Active, *2 Rules for Building Your Running Base* — https://www.active.com/running/articles/2-rules-for-building-your-running-base?page=2
- Roadman Cycling, taper／PMC — https://roadmancycling.com/blog/cycling-taper-pmc-performance-management-chart
- MyProCoach, *Important Metrics for Race Day* — https://support.myprocoach.net/hc/en-us/articles/360041024691
- intervals.icu 論壇 — https://forum.intervals.icu/t/zones-of-form-in-fitness-chart/3623

研究（Europe PMC 摘要；Gabbett 讀全文）：
- Buist 2008, AJSM 36:33–39, DOI 10.1177/0363546507307505
- Nielsen 2014, JOSPT 44:739–747, DOI 10.2519/jospt.2014.5164
- Damsted 2019, JOSPT 49:230–238, DOI 10.2519/jospt.2019.8541
- Nielsen 2013, JSCR 27:1471–1478, DOI 10.1519/jsc.0b013e3182711e3c
- Damsted 2018, IJSPT 13:931–942, DOI 10.26603/ijspt20180931
- Fredette 2022, J Athl Train 57:650–671, DOI 10.4085/1062-6050-0195.21
- Frandsen 2025, BJSM 59:1203–1210, DOI 10.1136/bjsports-2024-109380
- Gabbett 2016, BJSM, DOI 10.1136/bjsports-2015-095788（PMC4789704）
- Cloosterman 2026, J Athl Train, DOI 10.4085/1062-6050-0030.25
- Maupin 2020, Open Access J Sports Med, DOI 10.2147/oajsm.s231405

沿用的 repo 文件：`docs/research/ctl-ramp-calibration.md` §2.2、§4；`unsourced-rules.md` §B2；`periodization-cross-sport.md` [441][472]；`detraining.md:249`；`late-race-entry.md:98`。
