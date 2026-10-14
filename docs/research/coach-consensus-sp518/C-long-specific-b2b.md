# SP-518 組 C：長跑占週量、長跑單次上限、專項期長跑比例、B2B

- 調查日期：2026-10-14。只查，沒有改 repo。
- repo（唯讀）：`/Users/logan/Projects/TrailRunCoach-wt-integrate-1012a/backend/engine/`
- 驗證標記：**已驗證**＝這次 WebFetch 讀到原文那句；**未驗證（摘要）**＝只看到搜尋摘要；**二手**＝別人轉述（寫明誰轉述）；**沿用 <文件>§**＝repo 既有研究文件已驗證、這次沒重讀。
- 「賽前第 n 週」照程式 `specific_phase.weeks_out`（週一起算、無條件進位）：週日比賽時，第 3 週＝賽前 14–20 天、第 4 週＝21–27 天、第 5 週＝28–34 天、第 6 週＝35–41 天。

---

## 1. 長跑占週量

### 1.1 程式位置與現值

| 位置 | 現值 |
|---|---|
| `overview.py:1876` | 基礎期長跑 = `max(60, min(0.30 × 週分鐘, max(近 30 天最長, 60) × LONG_CAP))` |
| `cold_start.py:49-50, 135-138` `long_share_cap` | 長跑 ≤ 週分鐘 50 %；週 < 120 分時 ≤ 40 %（`LONG_SHARE`，推估），且不套 60 分下限 |

### 1.2 來源

| # | 作者 | 出處 | 原文數字與基準 | 驗證 |
|---|---|---|---|---|
| 1 | Jack Daniels | Paul Baswick〈Is your long run too long?〉，*Canadian Running* 2023-06-28 | 週 ≥ 64 km：「shouldn't exceed 25 per cent of the week's total distance—or 150 minutes, whichever comes first」；週 < 64 km：「limit their long run to 30 per cent」。**基準：週里程** | 二手（Baswick 轉述；文章已讀到） |
| 2 | Jack Daniels | Luke Humphrey（Hansons）〈Mastering the Long Run〉，Outside 2020-10-20（節錄 *Hansons Marathon Method* 2nd ed.） | 「never to exceed 25–30 percent of their weekly mileage in a long run」，「a 2:30–3:00-hour time limit」。週里程 | 二手（Humphrey 轉述；文章已讀到） |
| 3 | Hansons（Humphrey） | Athletics Weekly〈Marathon training: The long run – how far?〉（節錄書）2016／2025 更新 | 「a long run that does not exceed 25-30% of your weekly mileage」；「2:00–3:00 hours is the optimal window」。週里程 | 已驗證 |
| 4 | Luke Humphrey | lukehumphreyrunning.com〈The 16 Miler〉 | 熱門教練書「a long run will constitute 25-30%」；有些計畫「50%, or more」是反例 | 已驗證 |
| 5 | Ryne Anderson（CTS 超馬教練） | trainright.com〈How to Determine Weekly Run Training Volume〉2025-09-09 | 「keep the long run between 25 to 40% of weekly volume」；「times in the build for a run that accounts for more than 40%」；「consistently … more than 50% … that's too much」。**基準：週時數**（文章以小時計量） | 已驗證 |
| 6 | David Roche | *Trail Runner*〈What Your Weekly Training Plan Should Look Like〉2017-04-26 | 「Long run: 25-35% of miles」；超馬可以較高但「never exceed 40 percent of weekly mileage」；週日 aerobic run 15 % | 已驗證 |
| 7 | Coach Laura（RunnersConnect） | 〈Scheduling Long Runs〉（Extra Kick podcast 逐字稿） | 「around 20 – 30%」，「we're doing this by time, not by mileage」；「over 50% of their weekly total」是錯的 | 已驗證 |
| 8 | Kai Ng（紐約教練） | Emilia Benton〈Long Runs Are Key to Endurance Gains〉，*Runner's World* SA 2025-09-23 | 「shouldn't make up more than 25 to 30 per cent of your weekly mileage」 | 已驗證 |
| 9 | Jason Fitzgerald（Strength Running；不是 Matt） | 〈How long should my long run be relative to my weekly mileage?〉2016-02-25 | 整理網路說法 20 %～50 %，結論「around 20-30% of weekly volume」 | 已驗證 |
| 10 | Fleet Feet | 〈How Long Should My Long Run Be?〉 | 「25 to 30 percent of your total weekly mileage」 | 未驗證（摘要） |
| 11 | Uphill Athlete（House & Johnston） | 《Training for the Uphill Athlete》p.294（論壇使用者引用） | 「aim for 30-40 percent of your overall weekly distance/vertical」，週量變大時比例要降 | 二手（UA 論壇使用者引書；論壇頁 404，只看到摘要） |
| 12 | Hal Higdon | halhigdon.com Novice 1 Marathon | 頁面：「you run roughly the same mileage during the week as you do during long runs on the weekends」→ 長跑約占週量一半 | 已驗證（比例是我換算） |
| 13 | Jeff Galloway | Slowtwitch 論壇整理他的初馬計畫 | 平日約 1:45、長跑到 26.2 mi（跑走）→ 長跑約占 70 %（論壇使用者換算） | 二手 |
| 14 | McMillan、Pfitzinger、Matt Fitzgerald、Brad Hudson、Koop | — | 沒找到「長跑占週量 X %」的說法（Pfitzinger 只有論壇使用者自己從 18/70 課表算出 25–28 %；Koop 只講累積量比單次重要） | 查不到 |

**研究**

| 研究 | 數字 | 驗證 |
|---|---|---|
| Knopp, Appelhans, Schönfelder, Seiler, Wackerhage 2024, *Sports Med Open*（92 份 12 週次菁英馬拉松課表），DOI 10.1186/s40798-024-00717-5 | 峰值週：高量組長跑 32.2 km／週 132.5 km（**24 %**）、中量組 31.2／75.5（**41 %**）、低量組 29.7／58.6（**51 %**）。12 週平均：25 %、39 %、46 %。比例是我用 Table 3、4 換算 | 已驗證（PMC 全文表格） |
| Haugen 等 2022, *Sports Med Open*（世界級長跑） | 馬拉松選手長跑 75–165 分；Kipchoge 30–40 km／週 200–220 km ≈ 15–20 %（我換算） | 已驗證（全文） |

沒找到比較「長跑占週量多少」和受傷或成績關係的研究。Frandsen 2025 看的是單次對 30 天最長，不是占週量（§2）。

### 1.3 統計

- 上限值（每個來源取它的上限）：Daniels 25（週量低時 30）、Hansons 30、Humphrey 30、Laura 30、Kai Ng 30、J. Fitzgerald 30、Fleet Feet 30、Roche 35（超馬 40）、CTS 40、UA 40。
  - **眾數 30 %，中位數 30 %，範圍 25–40 %**；驗證過 8 個（#3–#9、#12）。
- 「太多」的紅線：CTS「長期 > 50 %」、Laura「> 50 %」、Humphrey「不該到一半」→ **50 %**（3 個，都已驗證）。
- 實際課表：週量越低，長跑占比越高（Knopp：低量組約 50 %；Higdon 初馬約 50 %；Galloway 更高）。

### 1.4 換算

- 教練大多用**里程**，程式用**分鐘**。長跑是輕鬆配速，比全週平均配速慢約 3–8 %，所以同一個里程占比換成時間占比會高一點（25 % 里程 ≈ 26–27 % 時間，推估）。差距小於來源之間的差距，不需要另外修正。
- CTS（#5）與 RunnersConnect（#7）本來就用時間，可以直接比。

### 1.5 建議與影響

- **`0.30`（基礎期長跑目標）：維持。** 它就是眾數和中位數。
- **`0.5`（`long_share_cap` 上限）：維持。** 它對應教練說的「超過一半就太多」的紅線，而不是目標；平常 0.30 目標加上 60 分下限，只在週 120–200 分時才會碰到。
  - 可以考慮：超馬教練（Roche、UA）給的硬上限是 40 %，CTS 允許偶爾超過 40 %。如果想更保守，可以把基礎期的上限改 0.40、專項期留 0.50（專項期的長天占比本來就高）。這是選擇，不是共識。
- **`LONG_SHARE = 0.40`（週 < 120 分）：查不到來源，維持推估。** 注意方向：Daniels 對低量者反而**放寬**到 30 %（里程），Knopp 低量課表實際約 50 %。教練沒有「量越少、比例越嚴」的說法。0.40 是新手安全考量，可以留，但不要寫成有來源。

---

## 2. 長跑單次上限

### 2.1 程式位置與現值

| 常數 | 位置 | 現值 |
|---|---|---|
| `ROAD_LONG_MAX_KM` | `specific_phase.py:74` | 35 km |
| `ROAD_LONG_MAX_MIN` | `specific_phase.py:75` | 180 分 |
| `TRAIL_LONG_MAX_MIN` | `specific_phase.py:76` | 360 分（6 h） |
| `LONG_CAP` | `load_guard.py:107` | 1.10（≤ 近 30 天最長 +10 %） |

### 2.2 來源：路跑（距離）

| # | 作者 | 出處 | 原文 | 驗證 |
|---|---|---|---|---|
| 1 | Pfitzinger | runningwithrock.com〈Pfitz Marathon Training Plans Explained〉 | 「the distance can get up to 20+ miles」，「not afraid to prescribe 21 or 22 mile runs」→ 32–35 km | 二手（Running with Rock 整理書中課表） |
| 2 | Hal Higdon | TrainingPeaks〈Q&A with Hal Higdon: 20 Miles Max?〉 | 「I don't see a lot to gain from going much past 20 miles in training」→ 32 km | 已驗證 |
| 3 | Hal Higdon | halhigdon.com Novice 1 Marathon | 「Longest Workout: 20 miles」（第 15 週／18 週） | 已驗證 |
| 4 | Hansons | Athletics Weekly 節錄 | 標準長跑 16 mi（26 km）；20 mi 只給週跑約 65 mi、配速快於 9:00/mi 的人 | 已驗證 |
| 5 | Matt Fitzgerald | 搜尋摘要（Competitor 文章） | 「at least a few 16-milers … 20, 22, or 24 miles」 | 未驗證（摘要） |
| 6 | Jason Fitzgerald | Competitor 文章（Ryan Hall 部落格轉貼） | 18 mi 要跑約 3.5 h 時「the injury risk is outweighing the benefit」；慢的人「a couple of 20s」 | 已驗證 |
| 7 | Arcelli & Canova | John Davis 書評（沿用 `specific-phase-progression.md` §2.1） | 專項期連續 30–35 km @ 97–100 % MP | 沿用 |
| 8 | Knopp 2024（研究，92 份課表） | PMC11065819 Table 3 | 課表最長一次：高量 35.2 ± 3.3、中量 32.5 ± 3.8、低量 30.9 ± 4.1 km | 已驗證 |
| 9 | Galloway | 論壇整理 | 初馬 26.2 mi 跑走（例外） | 二手 |
| 10 | Brad Hudson | Slowtwitch 論壇使用者 | 課表裡有「22 Miles Easy」（一例） | 二手 |

### 2.3 來源：路跑（時間）

| # | 作者 | 出處 | 原文 | 驗證 |
|---|---|---|---|---|
| 1 | Daniels | Baswick 2023（同 §1 #1） | 150 分 | 二手 |
| 2 | Daniels | Humphrey 2020（同 §1 #2） | 「2:30–3:00-hour time limit」 | 二手 |
| 3 | Daniels | Anne Francis〈How long should your longest run be?〉*Canadian Running* 2018-08-14 | 「long runs should not be longer than three hours」 | 二手 |
| 4 | Hansons | Athletics Weekly | 「2:00–3:00 hours is the optimal window」 | 已驗證 |
| 5 | Humphrey | 〈The 16 Miler〉 | 「after 3 hours of running, you have crossed the point of diminishing returns」 | 已驗證 |
| 6 | Coach Laura（RunnersConnect） | 〈Scheduling Long Runs〉 | 「After three hours of running, your risk of injury is greatly increased」 | 已驗證 |
| 7 | Dave Martin（Georgia State） | Francis 2018，Humphrey 轉述 | 「limiting long runs to two hours (except for high-level marathoners)」 | 二手 |
| 8 | Laura Norris | lauranorrisrunning.com（留言回覆） | 馬拉松「at least 3 hours of running in a long run」 | 已驗證 |
| 9 | Greg McMillan | mcmillanrunning.com〈Longest long run for the marathon?〉 | 依目標成績：破 3 者「marathon time up to marathon time + 30-45 minutes」；4:00 者「marathon time minus 15-30 minutes up to … plus 15-30」；5–6 h 者「Maximum long run = < Marathon time (4:00-4:30)」 | 已驗證 |

### 2.4 來源：越野／超馬（時間）

| # | 作者 | 出處 | 原文 | 驗證 |
|---|---|---|---|---|
| 1 | Ian Torrence | iRunFar〈Ultramarathon Training Volume〉2023-04-04 | 50k：3–4 h；50 mi、100k：4–5 h；100 mi：「5–6 hours (on the trails)」 | 已驗證（也沿用 `race-feasibility.md` §1） |
| 2 | Jason Koop | trainright.com〈How Long Should Your Longest Run Be Before An Ultramarathon?〉2026-03-27 更新 | 最短「4 hours」練補給；16 週計畫的人到後段「could reasonably handle about an 8-hour long run」；最長跑占比賽「20% … 80%」 | 已驗證 |
| 3 | Matt Fitzgerald | Runner's Tribe〈Why to keep your long run in check〉2020-02-05 | 冠軍超馬選手「cap their training distance at or near 35 miles」；慢的人「avoid doing pure runs lasting longer than 4.5 hours or so」 | 已驗證 |
| 4 | vert.run | 〈100 Mile Training Plan〉2025-05-28 | 「A 4 to 6 hour long run or a back-to-back weekend」；B2B 第 1 天「3 to 5 hours」 | 已驗證 |
| 5 | Marathon Handbook（網站課表，不是具名教練） | Ultra Training Plans PDF | 50 mi 峰值「30-35 mi or 6+ hrs」；100k 峰值「35-40 miles or 7+ hours」 | 已驗證（媒體級） |
| 6 | Karl Meltzer | 搜尋摘要 | 備 100 mi 不排超過 20 mi 的單次 | 未驗證（摘要） |
| 7 | Steve House（UA） | uphillathlete.com 滑雪登山頁 | 多日行程「consecutive eight-hour training days in the final block」（滑雪，不是跑步） | 已驗證（只當參考） |

### 2.5 `LONG_CAP` 1.10

| 來源 | 原文 | 驗證 |
|---|---|---|
| Frandsen, Hulme, Parner 等 2025, *BJSM* 59:1203, DOI 10.1136/bjsports-2024-109380（5,205 人、588,071 次跑步） | 參考組是「regression, or up to 10% increase」；單次 > 10–30 % HRR 1.64、> 30–100 % HRR 1.52、> 100 % HRR 2.28；結論「when the distance of a single running session exceeded 10% of the longest run undertaken in the last 30 days」；週對週比例沒有關係 | 已驗證（Europe PMC 摘要；也沿用 `ctl-ramp-calibration.md`、`cold-start.md`） |
| McMillan（Outside〈Want to Run Longer?〉） | 「never add more than 20-30 minutes to any long run」 | 已驗證（教練） |

### 2.6 統計

- 路跑距離上限（km）：Pfitz 32–35、Higdon 32、Hansons 26、M. Fitzgerald 32–39、Canova 30–35、Knopp 平均 31–35、Hudson 35。**眾數 32 km（20 mi），中位數約 33 km，範圍 26–39 km**；驗證過 4 個（Higdon ×2、Hansons、Knopp）。
- 路跑時間上限：120、150、150–180、180、180、180、180、≥ 180、McMillan 依成績（慢的人可到 4–4.5 h）。**眾數 180 分，中位數 180 分，範圍 120–270 分**；驗證過 5 個（Hansons、Humphrey、Laura、Norris、McMillan）。
- 越野單次上限（時間）：4.5、5（50 mi／100k）、6、6、6+、7+、8。**中位數 6 h，範圍 4.5–8 h**；驗證過 5 個。
- `LONG_CAP`：只有一個研究，已驗證；是參考組的上緣。

### 2.7 換算

- Frandsen 用**距離**，程式用**分鐘**。同一條路線配速差不多時兩者相同；越野長天配速較慢（爬升多），+10 % 分鐘換成距離會少於 +10 %，所以分鐘版比較保守，方向沒錯。
- McMillan 的「依完賽時間」是時間制，和 180 分的單一上限不同：5–6 h 完賽者，McMillan 允許到 4–4.5 h，比 180 分長。這是少數意見。

### 2.8 建議與影響

- **`ROAD_LONG_MAX_KM = 35`：維持。** 在 Pfitzinger 22 mi、Knopp 高量組平均的位置，是範圍上緣但合理。眾數是 32 km；如果想跟最多教練一致可以改 32，影響只有能跑到 35 km 的人（高量者），課表最長一次少 3 km。
- **`ROAD_LONG_MAX_MIN = 180`：維持。** 正是眾數與中位數。慢的跑者（5–6 h 完賽）會被 180 分截在 25 km 左右，這和 Hansons／Laura／Humphrey 的看法一致，只有 McMillan 不同。
- **`TRAIL_LONG_MAX_MIN = 360`：維持。** 中位數 6 h，上限再往上的是 Koop（8 h）和網站課表（7+ h）。
- **`LONG_CAP = 1.10`：維持（已確認）。** 研究原文就是 10 %；而且 > 10 % 的每一級風險都上升，沒有「再多一點也沒事」的區間。

---

## 3. 專項期每週長跑占比賽的比例、專項期長度、賽事模擬

### 3.1 程式位置與現值

| 常數 | 位置 | 現值 |
|---|---|---|
| `FRAC`（越野，占比賽日**時間**） | `specific_phase.py:53` | {10: 0.50, 9: 0.55, 8: 0.60, 7: 0.70, 6: 0.85, 5: 0.70, 4: 0.90, 3: 0.70}；5、3 是恢復週 |
| `ROAD_FRAC`（路跑，占比賽**距離**） | `specific_phase.py:73` | {10: 0.55, 9: 0.60, 8: 0.65, 7: 0.70, 6: 0.75, 5: 0.70, 4: 0.80, 3: 0.65} |
| `SPECIFIC_WEEKS` | `planning.py:68` | 8 |
| `SIM_WEEKS` | `specific_phase.py:58` | (4, 3) |

補充：`long_minutes`（`specific_phase.py:364-381`）直接用 FRAC／ROAD_FRAC 算長天，再被 LONG_CAP 截；路跑的 90 分下限是 `min(90, want)`，所以 `want` 小時不會被墊高。

### 3.2 來源：最長一次放在賽前幾週

| # | 作者 | 出處 | 原文 | 換成程式的週 | 驗證 |
|---|---|---|---|---|---|
| 1 | Greg McMillan | 〈Longest long run for the marathon?〉 | 「running their longest long run 3-5 weeks before the marathon is best」 | 第 4–6 週 | 已驗證 |
| 2 | Hal Higdon | Novice 1 Marathon | 20 mi 在第 15 週（18 週計畫）的週六，距賽 22 天；之後 12、8 mi | 第 4 週 | 已驗證 |
| 3 | Knopp 2024（研究） | PMC11065819 | 峰值週在賽週前 4.4 ± 1.4（高量）、4.0 ± 1.5（中）、3.6 ± 1.4（低）週；峰值週長跑「~ 30–32 km」 | 約第 4–5 週 | 已驗證 |
| 4 | Laura Norris | 留言回覆 | 「generally you want the longest run 2-3 weeks prior to the race」 | 第 3–4 週 | 已驗證 |
| 5 | Andy Jones-Wilkins（CTS） | 〈The Best Training Tool for a 100-Mile Ultramarathon〉2025-03-13 | 3 天訓練營「four to six weeks in advance of your event」 | 第 5–7 週 | 已驗證 |
| 6 | Jason Koop | 〈How to Train for Mountains When You Live in a Flat Area〉 | 山區訓練營「4-6 weeks out」 | 第 5–7 週 | 沿用 `specific-phase-progression.md` §2.1 |
| 7 | vert.run | 〈100 Mile Training Plan〉2025-05-28 | 「Your biggest back-to-back should usually happen 4 to 5 weeks before race day」；最後 3 週不要硬塞大的 | 第 5–6 週 | 已驗證 |
| 8 | Marathon Handbook 課表 | Ultra Training Plans PDF | 50K（16 週）峰值在第 11–13 週；50 mi（20 週）第 15–17 週；100K 第 14–16 週 | 第 4–7 週 | 已驗證（媒體級） |
| 9 | Ann Trason | UltraRunning〈Ask Ann: Taper Crazies〉 | 「I do my last long run 21 days before a 100-miler」 | 第 4 週末 | 未驗證（摘要；原頁 403） |
| 10 | 江晏慶 | 筆記（沿用 `back-to-back-and-long-day.md` §2.2） | 專項巔峰期（賽前約 1.5 個月）「抓比賽距離爬升的七成」 | 第 6–7 週 | 沿用 |
| 11 | Jeff Gaudette | RunnersConnect 馬拉松減量（沿用 `periodization-cross-sport.md` §4.5.2 [420]） | 最後一次長跑約賽前 14 天、峰值長跑的 50–60 % | 第 3 週 | 沿用 |

### 3.3 來源：最長一次占比賽多少

| # | 作者 | 賽事 | 最長一次 | 占比賽 | 驗證 |
|---|---|---|---|---|---|
| 1 | Koop | 超馬 | 20–80 % 距離都有人成功 | 20–80 % | 已驗證 |
| 2 | 江晏慶 | 越野 | 距離和爬升的七成 | 70 % | 沿用 |
| 3 | Torrence | 50k 越野 | 3–4 h | 50k 完賽約 5–7 h → 約 55–65 %（時間，推估） | 已驗證 |
| 4 | Torrence | 50 mi／100k 越野 | 4–5 h | 約 35–45 %（時間，推估） | 已驗證 |
| 5 | Torrence | 100 mi | 5–6 h | 約 20–25 %（時間，推估） | 已驗證 |
| 6 | Marathon Handbook | 50K | 26–28 mi | 84–90 %（距離） | 已驗證（媒體級） |
| 7 | Marathon Handbook | 50 mi／100k | 30–35 mi、35–40 mi | 56–70 %（距離） | 已驗證（媒體級） |
| 8 | Pfitzinger | 馬拉松 | 20–22 mi | 76–84 % | 二手 |
| 9 | Higdon | 馬拉松 | 20 mi | 76 % | 已驗證 |
| 10 | Hansons | 馬拉松 | 16 mi | 61 % | 已驗證 |
| 11 | Knopp 2024 | 馬拉松 | 峰值週 30–32 km；整份最長 31–35 km | 71–83 % | 已驗證 |
| 12 | Fokkema 2020（研究） | 馬拉松 | 最長 < 25 km（約 59 %）者完賽慢 13.4 分 | 下限參考 | 沿用 `race-feasibility.md` §1 |
| 13 | Laura Norris | 半馬 | 新手 10–11 mi、有基礎 12–13、進階 14–16 mi | 76–122 % | 已驗證 |
| 14 | Matt Rudisill | *Runner's World* AU/NZ 2026-03-26，半馬 | 新手 12–16 km；有成績目標 ≥ 17 km；高量者 22 km | 57–104 % | 已驗證 |
| 15 | Higdon | 10K Intermediate | 最長 8 mi（第 7 週，賽前一週） | **129 %** | 已驗證 |

### 3.4 `SPECIFIC_WEEKS`

| 來源 | 專項期長度 | 驗證 |
|---|---|---|
| Friel Build 8–9 週 | 8–9 | 沿用 `estimated-constants-inventory.md`（[18]） |
| Arcelli & Canova：最後 6–8 週 | 6–8 | 沿用 `specific-phase-progression.md` §2.1 |
| Canova 2017 演講：專項期約 10 週 | 10 | 沿用同上 |
| Koop：最後 6–8 週以 EnduranceRun／SteadyState 為主 | 6–8 | 沿用同上 |
| UA：A 賽前 2–3 個月轉專項 | 8–13 | 沿用同上 |
| vert.run 100 mi：「12 to 16 weeks of focused 100-mile training」 | 12–16 | **已驗證**（這次；注意 inventory 寫的「最後 8–10 週」和這次讀到的不同） |
| 江晏慶：強化 2–3 週＋巔峰約 6 週 | 8–9 | 沿用 |
| Pfitzinger：Race Preparation mesocycle 第 12–15 週（18 週計畫）+ 3 週減量 | 4（＋前面 LT 期） | 沿用（二手） |

中位數 8 週，範圍 4–16 週。

### 3.5 `SIM_WEEKS`

| 來源 | 模擬／最大課的時間 | 驗證 |
|---|---|---|
| CTS Jones-Wilkins：3 天營 4–6 週前 | 第 5–7 週 | 已驗證 |
| Koop 山區營 4–6 週前 | 第 5–7 週 | 沿用 |
| vert.run：最大 B2B 4–5 週前、最後 3 週不做大的 | 第 5–6 週；第 3 週不行 | 已驗證 |
| Marathon Handbook：50K「Peak & Race Simulation」第 11–13／16 週 | 第 4–6 週 | 已驗證（媒體級） |
| Higdon 20 mi：距賽 22 天 | 第 4 週 | 已驗證 |
| Laura Norris：最長一次 2–3 週前 | 第 3–4 週 | 已驗證 |
| Koop：最後 2–3 週不要硬塞 | 第 3 週以前 | 沿用 `back-to-back-and-long-day.md` §2.2 |

### 3.6 統計

- 最長一次的時機：路跑來源落在賽前 2–5 週（中位數約 3.5 週 → **程式第 4 週**），越野／超馬落在 4–6 週（中位數 4.5–5 週 → **程式第 5–6 週**）。驗證過 8 個。
- 最長一次占比賽：
  - 馬拉松：61–84 %，**中位數約 76 %**（驗證 4 個）。
  - 半馬：76–122 %，中位數約 95–100 %（驗證 2 個）。
  - 10K：Higdon 129 %（驗證 1 個）；搜尋摘要「6–8 mi」＝97–129 %。
  - 越野 ≤ 50k（完賽 4–7 h）：55–90 %，**中位數約 70 %**（Koop 上限 80、江晏慶 70、Torrence 55–65、網站 84–90）。
  - 越野 50 mi 以上：20–70 %，比賽越長占比越低；這段已經被 `TRAIL_LONG_MAX_MIN` 6 h 截住。
- `SPECIFIC_WEEKS`：中位數 8 週。

### 3.7 換算

- 越野 `FRAC` 是占比賽日**時間**（コース定數線性，所以也等於距離、爬升的同一比例）。Torrence 的小時數換成占比要先估完賽時間（我用 50k 5–7 h、50 mi 10–14 h、100 mi 24–30 h，推估）。
- 程式的週：週日比賽時第 4 週＝賽前 21–27 天，第 6 週＝35–41 天。「賽前 3–5 週」約等於程式第 4–6 週。

### 3.8 建議與影響

1. **`FRAC` 的時機（峰值在第 6、4 週，第 5、3 週退一步）：維持。** 第 4 週＝21–27 天前，對上 Higdon、Knopp、McMillan、Trason；第 6 週對上 CTS、Koop、vert.run、江晏慶。
2. **`FRAC` 的峰值高度：建議下修。** 第 4 週 0.90、第 6 週 0.85 高於越野來源的中位數（約 70 %，Koop 上限 80 %）。
   - 只影響完賽時間 < 6.7 h 的比賽（更長的已被 6 h 截住）。例：5 h 的越野賽，第 4 週長天從 4.5 h 變成 4 h。
   - 建議（推估，保留形狀）：`{10: 0.50, 9: 0.55, 8: 0.60, 7: 0.65, 6: 0.75, 5: 0.65, 4: 0.80, 3: 0.65}`。第 6 週 0.75、第 4 週 0.80＝江晏慶的七成到 Koop 的上限之間。
   - 影響：短中距離越野的第 4–7 週長天短 5–12 %，週量不變時多出來的時間回到輕鬆跑。
   - 例外：完賽 ≤ 3 h 的短越野，教練常跑到比賽全程（路跑半馬、10K 也是），0.80 可能偏少。要不要依比賽長度分兩段，要使用者決定。
3. **`ROAD_FRAC`：馬拉松維持，短距離有問題。**
   - 馬拉松：第 4 週 0.80 × 42.2 = 33.8 km，落在 Pfitzinger／Higdon／Knopp 的 71–84 %。維持。
   - **半馬和 10K：太短。** 程式第 4 週給半馬 16.9 km、10K 只有 8 km（約 45–50 分），而教練給半馬 16–26 km（76–122 %）、10K 約 13 km（Higdon 8 mi＝129 %）。10K 跑者進專項期，長跑反而比基礎期短。
   - 建議：比賽 ≤ 21.1 km 時，ROAD_FRAC 不要直接乘距離。可以 ① 讓專項期長跑不低於基礎期長跑；或 ② 改用「占比賽距離 ≥ 100 %」（10K 約 1.2–1.3×、半馬約 1.0×）。數字是推估，要使用者決定。
   - 影響：短距離路跑者專項期長跑變長（10K 約 +30–40 分）。
4. **`SPECIFIC_WEEKS = 8`：維持。** 中位數 8 週。另外修正：`estimated-constants-inventory.md` 寫 vert.run「最後 8–10 週」，這次讀到的 vert.run 100 mi 指南寫的是「12 to 16 weeks」。
5. **`SIM_WEEKS = (4, 3)`：越野建議改成 (6, 4)；路跑維持。**
   - 越野：第 3 週＝賽前 14–20 天，落在 vert.run「最後 3 週不做大的」，也和 app 自己的 B2B `LAST_BEFORE_DAYS = 21` 不一致。CTS／Koop 的模擬營在 4–6 週前。第 5 週是恢復週，不能排模擬，所以建議第 6、4 週（正好是 FRAC 的兩個峰值）。
   - 路跑：Higdon 的 20 mi 在 22 天前，Norris 說 2–3 週前，(4, 3) 合理。
   - 影響：越野的模擬提前 1–2 週，比賽前多出一個完整的消化期；第 3 週不再有最長的一天。

---

## 4. B2B（連兩天長跑）

### 4.1 程式位置與現值（`b2b.py:91-102`）

| 常數 | 現值 | 程式註解 |
|---|---|---|
| `LAST_BEFORE_DAYS` | 21 | vert.run 最後 3 週不做；Koop 2–3 週 |
| `SPACING_DAYS` | 14 | 推估 |
| `DAY2_RATIO` | 0.67 | CTS 30:20 |
| `PAIR_SHARE` | 0.70 | 推估：兩天 ≤ 週分鐘 70 % |
| `POST_EASY_DAYS` | 4 | UA「three or four light days」；4 推估 |
| `SINGLE_DAY2` | (90, 150) | 單日 ≥ 6 h 賽事：第 2 天 1.5–2.5 h（推估） |

### 4.2 來源

| # | 作者 | 出處 | 原文（比例、時機、間隔、恢復、週量） | 驗證 |
|---|---|---|---|---|
| 1 | Andy Jones-Wilkins（CTS） | 〈The Best Training Tool for a 100-Mile Ultramarathon〉2025-03-13 | 「The first day should be the longest. Consider something like 30 miles on the first day and 20 miles each on days two and three」→ 第 2 天 = 0.67；「four to six weeks in advance」 | 已驗證 |
| 2 | Ian Torrence | iRunFar〈Ultramarathon Training Volume〉2023-04-04 | 「cut 25 to 50% off the first day's mileage or time」→ 0.50–0.75；「schedule them every two to three weeks」；50k 不需要，50 mi 以上要 | 已驗證 |
| 3 | Ian Torrence | iRunFar〈Endurance-Based Workouts for Ultramarathon Training〉2022-11-01 | 「Adequate spacing of back-to-backs, approximately four to five weeks」（同一作者，和 #2 不同） | 已驗證 |
| 4 | Jeff Gaudette | RunnersConnect〈Back to Back Long Runs〉2025-09-26 | 起步：週六＝平常長跑 75 %、週日「50% of Saturday's distance」；發展期「80/70 ratios」（週日／週六 ≈ 0.875）；40 歲以上「2:1」；起步期隔週一次，發展期「2 out of every 3 weeks」；新手賽前「only 2-3 times」；之後週一二不做硬課（48–72 h）、「2-3 weeks of reduced training after each B2B block」 | 已驗證 |
| 5 | Jeff Gaudette | ACTIVE〈How to Prepare to Run an Ultra This Year〉2013-09-09 | 長跑前一天「60 percent of your normal long-run distance」，之後每週加 2 mi 到 20 mi（長跑 22–24 mi）→ 短的那天在**前** | 已驗證 |
| 6 | vert.run | 〈What's Back-to-Back Training〉2026-08-27 | 週六「2.5 to 4 hours」、週日「90 minutes to 2.5 hours at an easy, conversational effort」→ 約 0.6；「A few purposeful weekends … are often enough」；之後減強度或量 | 已驗證 |
| 7 | vert.run | 〈100 Mile Training Plan〉2025-05-28 | 「Your biggest back-to-back should usually happen 4 to 5 weeks before race day」；最後 3 週不要硬塞 | 已驗證 |
| 8 | Rachel Spaulding（COROS 教練） | 〈Double the Fun〉2026-09-11 | 第 1 天 14–20 mi、第 2 天「10-20+ miles」；兩天共「30-40 miles」；新手「2-3 times before their event」 | 已驗證 |
| 9 | Andrew Simmons | TrainingPeaks〈Structure Training for Back-to-Back Marathons〉2023-02-06 | 最低峰值週：週六 22、週日 16 mi（0.73），週總量 55–60 mi → 週末占 **63–69 %**；「Keep Saturday TSB to no more than -20」；不可連續兩週 TSB < −30 | 已驗證（目標是連兩天比賽，不是一般超馬） |
| 10 | David Roche | *Trail Runner* 2017-04-26 | 週六長跑 25–35 % 週量、週日 aerobic run 15 % → 週末 40–50 %，第 2 天約 0.43–0.6 | 已驗證（一般週課表，不是專門講 B2B） |
| 11 | Marathon Handbook 課表 | Ultra Training Plans PDF | 100k 峰值：「Saturday 25-35 mi + Sunday 10-15 mi」，週 60–80 mi → 週末約 50–67 %、第 2 天約 0.4；50K 峰值 22–28 + 5–8 mi | 已驗證（媒體級） |
| 12 | Henry Howard（教練） | runspirited.com〈How to Properly Taper〉 | 最後一次 B2B 共 45 mi，下一個週末 36 mi，比賽前一個週末 14 mi；減量 2–3 週從最後一次長跑後開始 | 已驗證（時機是我推的：最大的約 3 週前） |
| 13 | Scott Johnston（UA） | TrainingPeaks 部落格 | 「two massive back to back weekend workouts followed by three of four light days」；前一週「slightly below-average」 | 沿用 `back-to-back-and-long-day.md` §2.2 |
| 14 | Jason Koop | 〈Block Training〉 | 「the first workout should be the harder of the two」；「Do not increase the total amount of time or miles you would normally run」 | 沿用同上 |
| 15 | Koop | 〈Do hardest workouts first〉 | 兩天的區塊放在 2–3 天休息之後 | 沿用同上 |
| 16 | Ann Trason | UltraRunning | 最後一次長跑在 100 mi 前 21 天 | 未驗證（摘要；403） |
| 17 | 搜尋摘要（出處不明） | — | 50 mi／100K：25 + 20 mi（0.8），賽前 3 週 | 未驗證（沿用 `back-to-back-and-long-day.md` §2.2） |
| 18 | Krissy Moehl、Hal Koerner、Roche（B2B 專文） | 書／付費牆 | 沒讀到比例 | 查不到 |

沒有找到比較 B2B 比例、間隔或恢復天數的研究（沿用 `back-to-back-and-long-day.md` §2.1 的結論）。

### 4.3 統計

| 常數 | 各來源的值 | 眾數 | 中位數 | 範圍 | 驗證 |
|---|---|---|---|---|---|
| 最後一次距賽（天） | vert.run ≥ 21（最大的 28–35）、CTS 28–42、Koop ≥ 14–21、Howard 約 21、Trason 21（摘要）、摘要 21 | 21 | 21 | 14–42 | 3 個（vert.run、CTS、Howard） |
| 間隔（天） | Gaudette 7（發展期）、14（起步期）；Torrence 14–21、28–35；app 3:1 週期實際 28 | 14 | 14–21 | 7–35 | 3 個 |
| 第 2 天／第 1 天 | CTS 0.67、Torrence 0.50–0.75（中 0.625）、Gaudette 0.5、0.875、0.5（40+）、vert.run 約 0.6、Roche 約 0.43–0.6、Simmons 0.73、MH 約 0.4、COROS 約 0.7–1.0、摘要 0.8 | 0.5／0.6 | **約 0.62** | 0.4–1.0 | 9 個 |
| 兩天占週量 | Roche 40–50 %、MH 50–67 %、Simmons 63–69 %、UA 論壇 50–60 %（未驗證） | — | 約 60 % | 40–69 % | 3 個 |
| 之後輕鬆天數 | Gaudette 2（48–72 h）、摘要 ≥ 2、Johnston 3–4 | — | 3 | 2–4 | 2 個 |
| 單日長賽的第 2 天 | vert.run 90–150 分 | — | — | 90–150 | 1 個 |

### 4.4 換算

- 第 2 天比例：教練用距離或時間都有（Torrence 明說兩者都可以；vert.run 用時間），程式用分鐘，不用換算。
- Gaudette 2013 把短的放前面（第 1 天＝第 2 天的 0.6），和 Koop／CTS／COROS「第 1 天較硬」相反；比例本身仍算 0.6。
- 兩天占週量：Simmons 和 MH 用里程。B2B 兩天都是輕鬆配速，時間占比會比里程占比再高幾個百分點（推估）。

### 4.5 建議與影響

- **`LAST_BEFORE_DAYS = 21`：維持。** 眾數、中位數都是 21；最大的那一次教練放在 28–35 天前，可以留給 `FRAC` 的第 6、4 週。
- **`SPACING_DAYS = 14`：維持。** 是眾數，Torrence「每 2–3 週」的下緣。實際上 app 每個 3:1 週期最多一次，間隔多半是 28 天，落在 Torrence 4–5 週的那一說。
- **`DAY2_RATIO = 0.67`：維持或微調到 0.62。** 中位數 0.62，0.67 在中間一半（0.6–0.73）裡面，而且有直接出處（CTS）。改成 0.62 時第 2 天少約 7 %（例：第 1 天 4 h，第 2 天 160 → 150 分），週量不變時多的時間回到平日輕鬆跑。差異小，優先度低。
- **`PAIR_SHARE = 0.70`：維持當上限，但標「來源少」。** 只查到 3 個有週量的課表（40–69 %），0.70 在上緣。如果要對齊中位數可以改 0.60–0.65，影響是 B2B 週要更多平日量才排得出 B2B（週量小的人會更常「這週不排 B2B」）。
- **`POST_EASY_DAYS = 4`：可以改 3。** 中位數 3（Gaudette 2、Johnston 3–4）。改 3 的影響：B2B 後的週四可以排質量課；app 本來的 48 小時硬課間隔仍然保護。若要保守維持 4（UA 的上緣），也有出處。
- **`SINGLE_DAY2 = (90, 150)`：維持，補出處。** vert.run 的週日「90 minutes to 2.5 hours at an easy, conversational effort」正好是這個範圍（已驗證），可以把「推估」改成 vert.run。

---

## 5. 限制

- Daniels、Pfitzinger、UA 書、Koop 書、Hansons 書、Hudson 書都沒讀到原書；Daniels 只有二手轉述（三個轉述版本：25 %／150 分、25–30 %／2:30–3:00、3 h）。
- Marathon Handbook 課表是網站課表，不是具名教練；列進來是因為它有完整的週量，可以算週末占比。
- 越野「最長一次占比賽時間」的換算需要假設完賽時間，標推估。
- 這次搜尋 UA 論壇的頁面大多 404，UA 書的 30–40 % 只有論壇使用者引用。
- §3.8 第 3 點「10K 專項期長跑比基礎期短」是讀 `specific_phase.long_minutes` 和 `overview.py:1872-1876` 推出來的（例：10K 配速 5:00/km × 1.15 × 8 km ≈ 46 分，低於基礎期 60 分下限），沒有實際跑 `week_plan` 確認。

## 6. 參考網址（這次讀過）

- https://runningmagazine.ca/?p=99607（Baswick 2023，Daniels）
- https://www.outsideonline.com/health/running/training-advice/running-101/mastering-the-long-run/（Humphrey 2020）
- https://athleticsweekly.com/?p=45900（Hansons 節錄）
- https://lukehumphreyrunning.com/hansons-marathon-method-the-16-miler/
- https://trainright.com/how-to-determine-weekly-run-training-volume/（Ryne Anderson，CTS）
- https://www.trailrunnermag.com/training/training-plans-training/what-your-weekly-training-plan-should-look-like/（Roche 2017）
- https://runnersconnect.net/?p=30938（Coach Laura）
- https://www.runnersworld.co.za/training/long-runs-are-key-to-endurance-gains/（Kai Ng）
- https://strengthrunning.com/2016/02/how-long-should-my-long-run-be-relative-to-my-weekly-mileage/
- https://pmc.ncbi.nlm.nih.gov/articles/PMC11065819/（Knopp 2024）
- https://pmc.ncbi.nlm.nih.gov/articles/PMC8975965/（Haugen 2022）
- https://www.trainingpeaks.com/blog/qa-with-hal-higdon-20-miles-max
- https://www.halhigdon.com/training-programs/marathon-training/novice-1-marathon/
- https://www.halhigdon.com/training-programs/10k-training/intermediate-10k/
- https://www.mcmillanrunning.com/?p=141438
- https://run.outsideonline.com/training/how-to-run-longer/（McMillan）
- https://runningwithrock.com/pfitz-marathon-training-explained/
- https://runningmagazine.ca/?p=53689（Francis 2018）
- https://www.goodreads.com/author_blog_posts/14051633（Competitor，J. Fitzgerald 等）
- https://runnerstribe.com/a-column-by-matt-fitzgerald/why-to-keep-your-long-run-in-check/
- https://trainright.com/longest-run-ultramarathon-training/（Koop）
- https://www.irunfar.com/ultramarathon-training-volume（Torrence 2023）
- https://www.irunfar.com/endurance-based-workouts-for-ultramarathon-training（Torrence 2022）
- https://vert.run/100-miles-training-guide
- https://vert.run/whats-back-to-back-training/
- https://runnersconnect.net/long-runs-training/（Gaudette 2025）
- https://active.com/running/articles/how-to-prepare-to-run-an-ultra-this-year?page=2（Gaudette 2013）
- https://coros.com/stories/coros-coaches/c/double-the-fun-back-to-back-long-runs
- https://www.trainingpeaks.com/coach-blog/structure-training-back-to-back-marathons/（Simmons）
- https://trainright.com/the-best-training-tool-for-a-100-mile-ultramarathon/（Jones-Wilkins）
- https://www.runspirited.com/single-post/how-to-properly-taper-for-your-race（Howard）
- https://marathonhandbook.com/wp-content/uploads/Ultra-Training-Plans.pdf
- https://lauranorrisrunning.com/how-far-should-you-run-before-a-half-marathon-or-marathon/
- https://runnersworldonline.com.au/whats-the-minimum-long-run-distance-you-can-get-away-with-when-training-for-a-half-marathon/
- Frandsen 2025 摘要：Europe PMC PMC12421110，DOI 10.1136/bjsports-2024-109380
