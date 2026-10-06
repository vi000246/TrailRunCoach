# 各教練學派的區間與周期化：以 LT2 對齊，並映射到 TrailRunCoach 自動排課

> 研究日期：2026-10-04 · 模式：deep · 對應單：SP-29（研究）→ SP-30／SP-31／SP-32／SP-39（實作）
> 數字範例一律用既有研究文件的「範例跑者」（CP 220 W）；心率範例用明確標示為「假設」的整數，不是任何真實使用者的數據。
> 引用格式：`[n]` 是外部來源（文末 Bibliography）；`docs/research/…` 是本 repo 已查證過的研究文件，直接沿用、不重做；`backend/…:行號` 是程式碼位置。

## Executive Summary（摘要）

**對齊**：把 13 套區間系統換算成「% LT2」之後，它們其實只在三條線上吵架：LT1（AeT／VT1）、LT2（LTHR／MLSS／CP／閾值配速）、VO2max。以心率表示時，LT2 家族（LTHR、MLSS 心率、CP 心率）彼此平均只差約 1 bpm，但個人差可達 ±16 bpm；以功率／速度表示時，CP 平均比 MLSS 高約 7%（4–16%）[1][2]。所以 **LT2 是最好的單一錨點，但不夠**：LT1 在不同人身上落在 LT2 心率的約 80–95%，不能由 LT2 推出，低強度端一定要有第二個錨點（實測 AeT）[3][4][5]。用 %HRmax／%HRR 的系統換算到 %LTHR，誤差約 ±3%（1 SD），極端個案 ±10% 以上 [3][4]。

**命名**：Daniels T ≈ LT2（Friel Z4–5a、80/20 Z3、Seiler 中區上緣、挪威 I-3／I-4 交界）；5 區 %HRmax 系統的「zone 3」是 LT1 附近的有氧／馬拉松帶，「zone 4」才包含閾值。台灣常用的「三區＝T、五區＝I」是徐國峰 E/M/T/A/I 的編號，和手錶 5 區的 zone 3 不是同一件事。Daniels I（3–5 分）在每個系統都是最高的有氧區（Friel 5b、Palladino 5、Coggan L5、Seiler Z3）；R（≤ 2 分）在 VO2max 之上，心率不適用 [6][7][8]。

**App 最大的三個缺口**：①「Zone 5 開了，threshold 就消失」——Daniels、Pfitzinger、Koop、UA、挪威派都是閾值課與 VO2max 課並存 [9][10][11][12][13]；②強度課只按 %CP 分三／四／五區，沒有「閾值家族／VO2max 家族／速度家族」之分，也沒有訓練目的；③心率錨點隨模型漂移（HRR／HRmax 的輕鬆跑上限可以高於或遠低於 LT1），編輯器下拉又固定用 Friel。

**建議**：SP-31 用雙軌階梯，三區改為 15–30 分的「長節奏」並保留 6–12 分的「巡航」為同家族；週總量從週有氧量 5% 起步、上限 10% [14][6]。SP-32 分三類（有氧／閾值、VO2max 2–5 分、速度 ≤ 2 分），把「無氧間歇」改名「VO2max 間歇」。SP-39：三區看「基礎期週數＋護欄」或 90 分鐘飄移測試；五區要實測 AeT（＋LTHR）且 UA 差距 ≤ 10%，或 Friel 飄移 < 5%；90 分鐘測試不是 AeT 測試。SP-30：預設保持 %LTHR（COROS 6 區），HRR 只在通過一致性檢查時當選項，輕鬆跑上限永遠由 AeT／LTHR 決定。

**信心**：錨點與命名高；各學派原典數字中（多為二手摘要）；週排程與進階（Q25）低，主要沿用 repo 既有文件。

## Introduction（研究問題、範圍與覆蓋率）

### 研究問題

使用者想把 Daniels、Friel、Seiler／Olympiatoppen、Fitzgerald 80/20、Lydiard、Canova、Pfitzinger、Uphill Athlete、Koop、Coggan／Palladino／Stryd、挪威乳酸區間、通用 %HRmax／%HRR 五區這十幾套系統放到同一把尺上，並且回答：LT2 是不是最正確的共同錨點；各區命名怎麼對應；每個學派在轉換期、基礎期、專項／建構期、巔峰、減量期分別排什麼課；「有氧間歇（三區）」和「無氧／VO2max 間歇（五區）」的生理、工休比、排程與前提；最後把這些落到 TrailRunCoach 的自動排課規則（SP-30／31／32／39）。

### 範圍與方法

檢索階段把問題拆成 28 個子題（Q1–Q28），以平行 subagent 搜尋，去重後得到 **342 個來源、1,253 條附定位的引文證據**。來源類型：同儕審查論文與統合分析（PMC／PubMed／Frontiers／Springer，約 60 篇）、教練本人網站與書摘（joefrieltraining、uphillathlete、trainright／CTS、8020endurance、vdoto2、mariusbakken 等）、平台官方文件（Garmin、Polar、COROS、Stryd、Olympiatoppen）與二手整理（書評、論壇）。App 端（Q29–Q32）來自對本 repo 程式碼、spec 與既有 `docs/research/` 文件的唯讀分析。

**覆蓋不足的子題**：Q25（教練如何在一週內安排與進階閾值課 vs VO2max 課）檢索到 0 個來源；Q26（高強度前的有氧基礎條件）與 Q27（上坡間歇與越野專項強度）各只有 2 個。這三題主要沿用 repo 內已查證的 `docs/research/interval-prescription.md`、`docs/research/aerobic-base-readiness.md`、`docs/research/vo2max-gate-and-trail-metric.md`，再補上本次檢索中散落在其他子題的證據（例如 CTS 的 block 長度、UA 的 Zone 3 加法、Pfitzinger 的 LT→VO2max 順序）。整合階段另做了 1 次補查：Friel 2025 年 9 月修訂版的區間表——頁面確認「不再分開跑步與自行車的區間」，但表格是圖片，**數字無法以文字驗證**，本報告因此仍以 Friel 經典跑步版（2012 起 TrainingPeaks 版本）為準 [15][16]。

### 主要假設

第一，「LT2」在本報告指一個**家族**：LTHR（Friel 30 分 TT 後 20 分平均心率）、MLSS、CP／CS、OBLA 4 mmol、VT2／RCP。它們在概念上都是 heavy／severe 強度區的交界附近，但操作定義不同，數值可差 5–20% [1][2][17]。第二，%HRmax→%LTHR 換算採 LT2 ≈ 90% HRmax（休閒跑者實測男 89.9 ± 2.4%、女 91.7 ± 2.2%）[4]，%HRR 採 LT2 ≈ 87% HRR [4]；這是族群平均，個人可在 80–98% HRmax 之間 [3]。第三，速度採 LT2 ≈ 88% vVO2max、LT1 ≈ 74% vVO2max（VT2／VT1 的實測平均）[18]，也就是 LT1 速度 ≈ 0.84 × LT2 速度。第四，平路功率與速度近似成正比（推估；越野與上坡不適用，見 `docs/research/interval-prescription.md` §A1.4）。這些假設在每一欄換算的信心等級裡都有反映。

## Main Analysis（主要發現）

### Finding 1：LT2 是最好的單一錨點，但要「雙錨點」才夠用

使用者的直覺——用 LT2 當所有系統的共同尺——在證據上大致成立，理由有三。其一，以生理閾值開強度比以最大值開強度的個人反應一致：同一個 %HRmax 在不同人身上的乳酸反應差很多，Zone 2 研究裡固定 %HRmax 的錨點變異係數 6–29% [19]；Iannetta 2020 與 Mann 2013 也指出 %HRmax、%HRR 對強度區間的對應都很差（已整理於 `docs/research/zones-and-thresholds.md` §2.1）。其二，幾乎所有現代系統本來就以 LT2 為錨：Friel、80/20、COROS、CTS 用 %LTHR [16][20][21][22]，Coggan、Palladino、Stryd 用 %FTP／%CP [8][23][24]，Daniels T、Pfitzinger LT、Canova 的 threshold 也都定義成「大約能撐 1 小時的強度」[7][25][26]。其三，LT2 的實測誤差比 LT1 小：以固定錨點估 LT2 的心率平均絕對誤差 2.8–5.2 bpm，估 LT1 是 4.9–7.4 bpm [4]；HRV 法對 LT2／VT2 的一致性也明顯優於 LT1／VT1 [27]。

但「LT2」本身不是一個點，而是一組操作定義。以功率或速度表示時，CP 平均比 MLSS 高約 7%（文獻範圍 4–16%）[1]；系統性回顧顯示 MLSS 低估 CP 11%、VT2 高估 21%、RCP 高估 6% [2]；跑步的 critical speed 是 16.4 km/h、MLSS 是 15.2 km/h [28]。OBLA 4 mmol 只是 MLSS 的族群估計，個人的 MLSS 乳酸可以在 2–8 mmol 之間 [17][29]。所以 **「100% CP」和「100% MLSS 速度」差 5–10%**，跨系統換算時這是第一個要扣掉的系統誤差。以心率表示時情況反而好：CP 與 MLSS 的心率平均只差 0.6 bpm，但個人 95% 一致性界限是 −16 到 +17 bpm（Micheli 2025，見 `docs/research/zones-and-thresholds.md` §2.2）。換句話說，**心率把 LT2 家族壓成同一個數字，功率／速度則把它們攤開**。

更大的問題在低強度端。LT1 在 LT2 心率上的相對位置因人而異：休閒跑者 LT1 平均在 79–84% HRmax、LT2 在 90–92% HRmax [4]，換算成 LT1 ≈ 0.88–0.92 × LTHR；VT1／VT2 心率比約 0.91 [5]。但個人範圍很寬——LT1 可落在 69–94% HRmax、LT2 在 80–98% HRmax，「一個人的 LT1 心率可以高過另一個人的 LT2」[3]。Uphill Athlete 把這個比例本身當成診斷：AeT 和 AnT 差距大於 10% 就是有氧缺乏（ADS），世界盃滑雪選手可低到 6% [14][30]。也就是說，**LT1／LT2 的比值正是訓練要改變的東西**，不能用一個固定比例從 LT2 推出 LT1。所有只靠 LT2 切出來的「Z2 上緣」（Friel 89%、COROS 90%、80/20 90%）都只是族群平均的 LT1 [16][21][20]。

**判斷**：LT2 適合當「上半部」（tempo 以上）的共同尺；「下半部」（輕鬆跑上限、Zone 2）要以實測 LT1／AeT 為錨。這正是 Seiler 三區、Olympiatoppen 與 UA 採取的雙錨點設計 [31][32][33]。App 現行做法（輕鬆跑上限優先用實測 AeT，否則 0.89 × LTHR）方向正確，問題在於選了 HRR／HRmax 模型時 Z2 上緣會覆蓋這個邏輯（Finding 8）。

### Finding 2：對齊總表——各系統換算成 % LT2

**換算規則**（每欄的誤差來源不同，請搭配「信心」欄看）：

- 原生 %LTHR／%CP／%閾值配速的系統：直接抄，誤差只來自錨點本身的測量（手錶自動 LTHR 的平均絕對誤差可達 11 bpm [34]；Friel 30 分 TT 對實驗室 LT2 的驗證研究未找到）。
- %HRmax 系統：%LTHR ≈ %HRmax ÷ 0.90 [4]；1 SD 約 ±3% LTHR，極端 ±10% [3]。
- %HRR 系統：先換成心率再除以 LTHR；以「假設」靜息／最大心率比 0.30、LT2 = 87% HRR 計算 [4]。若某人的 LT2 落在 73–95% HRR 的兩端 [3]，同一條 HRR 邊界會在 ±9% LTHR 範圍內移動（下例）。
- 速度／配速：LT1 ≈ 0.84、vVO2max ≈ 1.14 × LT2 速度 [18]。功率用 CP 時，MLSS ≈ 0.90–0.96 × CP [1][2]。
- 比賽配速（Canova）：受過訓練者 MP ≈ LT2 速度 × 0.97–0.98（Pfitzinger：「馬拉松比乳酸閾值慢 2–3%」）[35]；休閒跑者差很大，雅典馬拉松中段跑者以 105% vLTh、後段跑者以 94% vLTh 完賽 [36]。
- RPE／effort（Lydiard、Koop）：只能定性對位，±10% 以上。

**表 2a：心率系統（% LTHR）**。▲ = LT1／AeT 族群平均所在位置（約 88–92% LTHR），★ = LT2。

| 學派／系統 | 原生定義 | 各區邊界換算成 % LTHR | LT1／LT2 落點 | 信心（誤差） |
|---|---|---|---|---|
| Friel 跑步 7 區 [16][37] | % LTHR | Z1 < 85、Z2 85–89、Z3 90–94、Z4 95–99、Z5a 100–102、Z5b 103–106、Z5c > 106 | ▲ ≈ Z2 上緣（Friel：基礎期 AeT 約在 Zone 2 [38]）；★ = Z5a 下緣 | 高（原生）；2025 修訂版數字未驗證 [15] |
| Fitzgerald 80/20 [20][39] | % LTHR | Z1 72–81、Z2 81–90、X 90–95、Z3 95–100、Y 100–102、Z4 102–105、Z5 > 105 | ▲ 在 Zone X（作者：「VT 在 Zone X」[40]）；★ = Z3 上緣 | 高；初版書的區界不同（Threshold 96–100、VO2max 102–105）[41] |
| COROS LTHR 6 區（app 預設）[21] | % LTHR | Z1 < 80、Z2 80–90、Z3 90–95、Z4 95–102、Z5 102–106、Z6 > 106 | ▲ ≈ Z2 上緣；★ 在 Z4 內 | 高 |
| CTS（Koop）5 區 [22] | % LTHR | Tempo Z3 84–94、Threshold Z4 95–105 | ▲ ≈ Z3 下緣（CTS：Z3–Z4 介於 LT1 與 LT2）；★ 在 Z4 中 | 中 |
| Seiler 3 區 [42][43] | LT1／LT2（或 ≤ 2／2–4／≥ 4 mmol） | Z1 < LT1（≈ < 88–92）、Z2 LT1–100、Z3 > 100 | 兩個閾值就是邊界 | 高（定義）；邊界 bpm 需實測 |
| Olympiatoppen I-1～I-5 [44][45] | % HRmax＋乳酸 | I-1 61–80、I-2 80–91、I-3 91–97、I-4 97–102、I-5 > 102（I-1 < 1.5、I-2 1–2、I-3 1.5–3.5 mmol） | ▲ ≈ I-2／I-3 交界；★ ≈ I-3／I-4 交界（I-3 = 3 區模型的 Zone 2 [31]） | 中（%HRmax 換算 ±3%；Olt 明說要個人化 [45]） |
| Pfitzinger [46][47][25] | % HRmax／% HRR | Recovery < 84、GA 78–90、Long／MLR 82–93、MP 88–98、LT 89–101、VO2max 104–109（≈ HRmax） | ▲ ≈ GA／Long 上緣；★ = LT 上緣 | 中（來源間 MP、LT 區界有 2–3% 出入） |
| Daniels（%HRmax）[6][7][48] | % HRmax | E 72–88、M 89–100、T 98–102（版本差異：91–98 或 98–100）、I 108–111（＝接近 HRmax）、R 不以心率定義 | ▲ ≈ E 上緣；★ ≈ T | 中（各版本 %HRmax 不同，見下文） |
| Uphill Athlete 5 區 [33][49] | AeT／AnT（實測） | Z1 AeT×0.8–0.9（≈ 70–83）、Z2 AeT×0.9–1.0（≈ 79–92）、Z3 AeT–AnT（≈ 90–100）、Z4 AnT–HRmax（> 100）、Z5 < 30–45 s 全力 | ▲ = Z2 上緣（定義）；★ = Z3 上緣（AnT ＝ 30–60 分全力平均 [50]） | 高（定義）；換算欄假設無 ADS |
| 通用 %HRmax 5 區（Garmin／Polar）[51][52][53] | % HRmax | Z1 56–67、Z2 67–78、Z3 78–89、Z4 89–100、Z5 > 100 | ▲ ≈ Z3 上緣；★ ≈ Z4 上緣 | 低（±3% 1 SD，個人可差 10%）[3] |
| COROS／徐國峰 RQ %HRR 6 區（app 選項） | % HRR 59／74／84／88／95 | 假設例：Z1 < 78、Z2 78–90、Z3（M）90–98、Z4（T）98–101、Z5（A）101–106、Z6（I）> 106 | ▲ ≈ Z2 上緣；★ ≈ Z4 | 低：同一個「T 84–88% HRR」對不同人是 92–110% LTHR [3][4] |
| 挪威派（Bakken）乳酸 [13][54] | mmol/L | 輕鬆 < 1 mmol、HR < 70% HRmax（≈ < 78）；閾值 2.0–3.0（菁英實務 2–4.5）mmol ≈ 92–100（推估） | 閾值課刻意在 LT2 之下 [55] | 中低（乳酸→HR 個人差大） |

**表 2b：功率／配速系統（% CP 或 % 閾值速度）**

| 學派／系統 | 原生定義 | 各區邊界 | LT1／LT2 落點 | 信心 |
|---|---|---|---|---|
| Palladino 跑步功率 10 區 [23] | % CP／FTP | 1A 50–65、1B 65–75、1C 75–80、2 80–88、3A 88–95、3B 95–101、4 101–106、5 106–116、6 116–150、7 > 150 | ▲ ≈ 1C／2 交界（約 80% CP，推估）；★ = 3B 上緣（CP） | 高（原生）；「descriptive, not prescriptive」[23] |
| Stryd 5 區 [24] | % CP | 65–80、80–90、90–100、100–115、115–130 | ▲ ≈ Z1 上緣；★ = Z3 上緣 | 高；Stryd CP 演算法改版會讓同一個瓦數意義改變 [56] |
| Coggan（自行車）[8][57] | % FTP | L1 < 55、L2 56–75、L3 76–90、L4 91–105、L5 106–120、L6 > 121、L7 | ▲ ≈ L2 上緣（自行車 LT1 約 70–80% FTP [42]）；★ ≈ L4 中段 | 中：FTP 約比 CP 低 3–8% [58]；跑步不建議直接套 |
| 80/20 功率／配速 [20] | % rFTP／% 閾值速度 | 功率 Z1 50–76、Z2 76–88、X 88–94、Z3 94–100、Y 100–103、Z4 103–120、Z5 > 120；速度 Z1 60–76、Z2 76–87、X 87–93、Z3 93–100、Y 100–102、Z4 102–115、Z5 > 115 | ▲ 在 X；★ = Z3 上緣 | 高（原生）；rFTP = 95% × 20 分 TT [20] |
| Friel 配速 7 區（app `backend/engine/zones.py:79-87`） | × 閾值配速 | 換成速度：Z1 < 77.5、Z2 77.5–87.7、Z3 87.7–94.3、Z4 94.3–100、Z5a 100–103、Z5b 103–111、Z5c > 111 | ▲ ≈ Z2 上緣；★ = Z5a 下緣 | 高（原生） |
| Daniels（速度）[6][59] | VDOT 配速 | E ≈ 78–85、M ≈ 94、T = 100、I ≈ 105–109、R ≈ 112–118（% T 速度；app 的換算 `docs/research/workout-templates.md` §2） | ▲ ≈ E 上緣；★ = T；I ≈ vVO2max | 中（倍數為推估） |
| Canova（% 比賽配速，以馬拉松為例）[60][26][61] | % MP | Regeneration < 80% MP（≈ < 78）、Fundamental 80–90（≈ 78–88）、Special 90–95（≈ 87–93）、Specific 95–105（≈ 92–103）、Supramaximal > 105（≈ > 102）；threshold 97–105% AnT [26] | ▲ ≈ Fundamental 中段；★ ≈ Specific 上緣 | 低中：MP／LT2 比因人差 ±5–10% [36] |
| Koop／CTS（RPE）[62][63] | RPE 1–10 | Recovery 4–5、Endurance 5–6、SteadyState 7（在 LT 之下）、Tempo 8–9（在 LT 或稍下）、RunningIntervals 10（VO2max） | ▲ ≈ Endurance 上緣；★ ≈ Tempo 上緣 | 低（RPE；Koop 明確不用心率 [64]） |
| Lydiard（effort）[65][66] | ¼、½、¾、⅞ effort；best aerobic／steady state | 定性：steady state 上限 ≈ 乳酸轉折點 [67]；長跑「pleasantly tired」 | 不建議數值化：「Mapping these directly onto modern zones implies more precision than the historical descriptions support」[66] | 低 |

**範例跑者（CP 220 W）的對照**：LT1 約 176 W（80% CP，推估）；Palladino 3A 194–209 W、3B 209–222 W；MLSS 估 198–211 W（0.90–0.96 × CP）；VO2max 間歇 233–255 W（106–116%）；pVO2max 約 257–268 W（117–122% CP，推估，`docs/research/interval-prescription.md` §A1.4）。注意 MLSS 估計值和 3A 幾乎重疊——這就是「Palladino 3A 其實是 MLSS 附近」的原因。

**Daniels 的版本差異要明講**。T 強度在不同二手來源寫成 83–88% VO2max／88–92% HRmax [6][68]、86–88% VO2max／88–90% HRmax [48]、82–88% HRmax [7]；E 寫成 65–79% 或 60–79% 或 65–78% HRmax；I 寫成 98–100% 或 97–100% HRmax。I 的休息有「約等於趟時間」[7] 與「趟時間的一半」[48] 兩種寫法。這些差異來自《Daniels' Running Formula》不同版次與摘要者的轉述，本次沒有取得原書頁面，所以表中用區間並標「中」信心；Daniels 本人以 VDOT 配速為主，心率只是輔助 [7]。

**HRR 系統的不確定性**（為何列「低」）：以假設靜息／最大心率比 0.30 計算，「T 84–88% HRR」對 LT2 恰在 87% HRR 的人是 98–101% LTHR，剛好對上 Daniels T；但若某人的 LT2 落在 73% HRR，同一段變成 109–114% LTHR，若落在 95% HRR 則是 92–95% LTHR [3]。換句話說，**HRR 模型「對平均人很準、對個人可能錯一整區」**。這不是 HRR 的錯，而是任何以最大值為錨的系統的共同限制 [19][3]。

### Finding 3：命名糾纏——「三區」「Zone 3」「T」與 LT1／LT2 的同義詞

**LT1 家族與 LT2 家族**。文獻裡 LT1 又叫 lactate threshold（LT）、gas exchange threshold、VT1、aerobic threshold（AeT），最容易混淆的是「anaerobic threshold」也曾被用在 LT1 [69]；LT2 又叫 lactate turnpoint、OBLA、VT2／RCP、MLSS、AnT，CP／CS 在概念上是同一條 heavy／severe 交界 [69][70][1]。實測上 VT1 與 LT1、VT2 與 LT2 平均接近，但 LT1／VT1 的個人一致性較差（CV > 12%，ICC < 0.75）[27]；HRV 推估的 HRVT1／HRVT2 與 LT1-VT1／LT2-VT2 的平均差都很小 [70]。結論是：**LT1 ≈ AeT ≈ VT1，LT2 ≈ AnT ≈ VT2 ≈ MLSS ≈ CP 只在族群平均成立**；功率或速度上 CP 比 MLSS 高 5–10%，VT2 偏高更多 [2]。

**「Zone 3」至少有五種意思**：

| 說法 | 實際強度（% LTHR） | 對應 |
|---|---|---|
| 手錶 5 區（%HRmax）的 zone 3 | ≈ 78–89 | LT1 附近，有氧上緣／一般人的「舒服但不輕鬆」；Polar 稱 Moderate [71] |
| COROS／RQ 6 區（%HRR）的 Z3 | ≈ 90–98（假設例） | M（馬拉松配速）；常被叫成「灰區」 |
| Friel Z3 | 90–94 | Tempo [16] |
| Seiler zone 3 | > 100 | 高強度（LT2 以上）[43] |
| UA Zone 3 | AeT–AnT（≈ 90–100） | 介於兩個閾值之間；越野 tempo、ME [33] |
| 台灣 5 區編號（徐國峰 E/M/T/A/I）的「三區」／app「三區」 | T ≈ 98–102／Palladino 3 = 88–101% CP | 乳酸閾值（T） |

因此使用者說的「三區（有氧間歇、tempo run）」對應 Daniels T 與 Friel Z4–5a、80/20 Z3、UA Z3 上半部；**不是**手錶 %HRmax 5 區的 zone 3（那是 LT1 附近）。而「五區 zone 3 是馬拉松配速、灰區」這個說法，指的是 RQ／HRR 6 區的 M 或 %HRmax 5 區的 zone 3——兩者都在 LT1 與 LT2 之間的下半部。80/20 系統刻意把這一段命名為 Zone X，提醒「通常應避免」，但作者也說它不是禁區：長距離賽事本身就在這裡，長課中的 Zone X 是「allowance, not a requirement」[72][73]。所謂「灰區」並不是生理上無效的強度，而是「沒有目的地跑在中強度」[74][75]。

**Daniels T 是 Zone 3 還是 Zone 4？** 用 %LTHR 的系統（Friel、80/20、COROS、CTS）裡 T 落在「閾值區」：Friel Z4 上緣到 5a、80/20 Z3、COROS Z4、CTS Z4 [16][20][21][22]。用 %HRmax 5 區時 T（88–92% HRmax）落在 zone 4（80–90%）上緣到 zone 5 下緣 [51]；marathonireland、running-calculator 等通用表也把 zone 4 叫 Threshold [76][77]。Daniels 的 M（80–90% HRmax）則落在 %HRmax 5 區的 zone 4 下半部、COROS HRR 的 Z3 [6]。所以 **T＝「4 區」（%HRmax 5 區系統）＝「三區」（台灣 E/M/T/A/I 編號與 app）**，兩個名字指同一件事。

**Daniels I 和 R 落在哪**。I 是 3–5 分、約 95–100% VO2max 的強度，目的是最大有氧能力 [6][7]；在 Friel 是 5b（103–106% LTHR，其自行車表直接寫「VO2 max intervals 3–5 min」[78]），80/20 Z4–Z5，Palladino 5（106–116% CP），Coggan L5（「3–8 min intervals intended to increase VO2max」[8]），Seiler Z3，Olympiatoppen I-5，Pfitzinger VO2max（2–6 分、94–98% HRmax [46]），Koop RunningIntervals（2–4 分、RPE 10 [62]）。R 每趟 ≤ 2 分、休息 2–3 倍時間、量 ≤ 週量 5%，目的是無氧能力、速度與經濟性 [6][48]；在功率上是 Palladino 6（116–150%）或 Stryd Z5（115–130%）、Coggan L6——Coggan 明說這種強度「Heart rate is generally not useful as a guide」[8]。心率在 R 不會到 HRmax 以上；R 是以配速定義的（I 配速每 400 m 再快約 6 秒）[79]。

**「有氧」與「無氧」間歇的用詞**。Billat 的經典回顧把「速度 ≥ MLSS 的間歇」整類稱為 aerobic interval training，只有在 vVO2max 之上、10–15 秒、130–160% VO2max 的才叫 anaerobic interval training [80][81]。Daniels 也把 I 的目的寫成「maximise aerobic power」，R 才是「anaerobic power, speed and economy」[6]。所以把 2–5 分的 VO2max 間歇叫「無氧間歇」不符合多數教練與文獻的用法；建議改名「VO2max 間歇」（見 Recommendations）。

### Finding 4：各學派在各周期排什麼課、上限是多少

各學派的周期名稱不同，但可以對到五個共同階段：轉換、基礎（一般準備）、建構／專項、巔峰、減量。下表只列有來源的內容；「—」表示本次沒有找到該學派在該期的明確規定。

| 學派 | 轉換 | 基礎 | 建構／專項 | 巔峰／比賽 | 減量 |
|---|---|---|---|---|---|
| Daniels [82][83][9][84][85] | — | Phase I（FI）：輕鬆跑＋偶爾 strides，「必須先完成才進 Phase II」 | Phase II（EQ）：主課 R（200／400 m、約一英里配速、充分休息），次課 T 巡航 1–2 英里；Phase III（TQ）：主課 I／H，次課 T（巡航延長到 2×3 英里、休 3 分），「最辛苦的一期」 | Phase IV（FQ）：「a fair amount of threshold running」，R／I 很少；有比賽時主課縮短、次課取消 | — |
| Friel [86][87][88][89][90][91] | Prep 2–4 週：低強度、交叉訓練、解剖適應肌力；Transition 1–8 週（一般 3–4 週，「for fun rather than fitness」） | Base 12 週：有氧耐力（Zone 2，「get as much 2 zone as you can」）、速度技巧、肌力；Base 2：2×20 分 Zone 3 肌耐力；Base 3：6–12 分 Zone 4 長間歇、最長的課 | Build 8–9 週：課表像比賽；肌耐力（6–12 分、略低於 AnT、休 ¼）、無氧耐力（2–4 分、遠高於 AnT）、爆發力 | Peak 1–2 週、Race 1 週：「mini-races」 | — |
| Seiler／Olympiatoppen／挪威 [92][93][94][95] | — | HVLIT 為主；挪威派基礎期每週 2 個雙閾值日（2–4 堂閾值課） | 賽前期轉金字塔：Zone 3 課減少、Zone 4（3000–800 m 比賽配速）增加 | 比賽期轉極化；雙閾值改為單堂 | — |
| Fitzgerald 80/20 [72] | — | 全年 ~80% 時間在 Z1–Z2，其餘在 Z3–Z5 | 同（分期細節本次未找到來源） | — | — |
| Lydiard [96][97][98][99] | — | 有氧期 8–12 週（理想 3–6 個月）：每週 3 堂穩定長跑＋1 堂 fartlek＋其餘輕鬆 | 坡道期 4–6 週（坡跑、彈跳）；無氧期最多 4 週，兩堂硬課間隔 2–3 天 | 協調期約 6 週，每週比賽、無氧量與總量遞減 | 1–3 週，依賽距 |
| Canova（馬拉松）[100][101][26][102][61] | 4 週，輕鬆跑 ≤ 1 小時 | General 4 週＋Fundamental 6 週（各素質分開練、長跑 87–93% MP） | Special／Specific 約 10 週：「extend the intensity」，漸進拉長比賽配速段；每 3–4 週一次 special block；「funnel」逐步收斂到 ±3% MP | — | — |
| Pfitzinger [10][103][104][25][105][46] | 賽後 5 週恢復，HR < 76% HRmax [106] | 第 1–5 週 Endurance（建量） | 第 6–11 週 LT＋Endurance（LT 每次最長 35–45 分）；第 12–15 週 Race Preparation（轉 VO2max，2–6 分、每週 1 次「plenty」） | 調整賽 | 3 週，量減 20–25% → 40% → 60%，強度保留 |
| Hudson [107][108] | — | Introductory：加量、坡衝刺 | Fundamental（約 6 週後加 400 m 坡重複，3K 配速）；Sharpening；多個閾值配速（MP／HMP／10K／5K） | — | — |
| Uphill Athlete [109][110][14][111][12][112] | 約 8 週：一般肌力＋有氧容量（ADS 者延長） | 有氧為主（≥ 90% 在 Z1–2）；ADS 差距 ≤ 10% 才加 Zone 3（起步週有氧量 5%、每週 1 次）；登山計畫第 13 週才出現第一堂 Z3；基礎後期加 ME（6–8 週、每 7–10 天 1 次） | 賽前 2–3 個月轉專項：取消 ME，「two interval workouts, one Zone 3 and one Zone 4」 | — | 每週量減約 25%，10–14 天；保留 2–4 分間歇 |
| Koop／CTS（反向周期）[113][114][115][116][117] | — | 最不專項的放最前：RunningIntervals（VO2max）3–5 週 block | TempoRun 3–6 週 → SteadyStateRun 4–6 週＋長跑，越接近比賽越專項；峰值 block 在賽前 6–9 週 | — | （UESCA／Koop 系：量減 40–60%，保留短的比賽配速段 [118]） |
| Roche（越野）[119][120] | — | 全年一致：每週 3–4 次 strides、幾乎每週 ≤ 3 分速度課、約 80/20 | 最後一期才強調專項；上坡跑步機閾值課 | 最後幾週集中下坡量 | — |

**每堂與每週上限**（可直接當 app 規則的上限值）：

| 課型 | 上限 | 來源 |
|---|---|---|
| Daniels T | 單堂 ≤ 週跑量 10% | [6][48][79] |
| Daniels I | 單堂 ≤ min(10 km, 週跑量 8%)；每趟 ≤ 5 分 | [48][6][59] |
| Daniels R | 單堂 ≤ min(8 km, 週跑量 5%)；每趟 ≤ 2 分 | [48][6] |
| Daniels M | ≤ 週跑量 15–20% | [6][59] |
| Daniels 長跑 | 不建議 > 2.5 小時 | [6] |
| UA Zone 3 起步量 | 約週有氧量 5%（原文例：第一週 1×10 分 Z3）；Z3 到週量約 10% 後部分換成 Z4；硬課間隔 48–72 小時。~~每週 1 次~~：2026-10-06 核對原頁沒有這句，已刪（`aerobic-base-readiness.md` §8） | [14] |
| UA ME | 每 7–10 天 1 次 | [121] |
| Koop／CTS | 高強度約 20% 的課、約 10% 的年時數；TempoRun 每堂 30–60（或 40–90）分；RunningIntervals 12–24 分 | [117][62][11] |
| Pfitzinger VO2max | 每堂 5–8 km 的趟（800–1600 m）；每週 1 次 | [46][25] |
| Lydiard 無氧期 | 最多 4 週 | [97] |
| Coggan L5 | 總量 30–40 分已「difficult at best」 | [8] |
| 硬課間隔 | UA ≥ 48 小時（72 更好）；Lydiard 2–3 天；Pfitzinger 品質日不連排 | [14][96][10] |

**減量期**是各學派共識最高的部分：統合分析建議 2 週、量呈指數下降 41–60%、維持強度與頻率 [122]；Mujika 的回顧給 4 天到 > 28 天、量可減 60–90%、頻率減不超過 20%、表現約進步 3% [123]。Pfitzinger 3 週 [25]、UA 10–14 天 [112]、Lydiard 1–3 週 [96]、馬拉松一般 2–3 週 [124]，都落在這個範圍內。

### Finding 5：強度分配——極化、金字塔、閾值的證據

**定義先統一**（以 Seiler 三區、依時間）：極化約 80／5／15 或 75／5／20；金字塔約 70／20／10；閾值型約 40／50／10 [125]。也有作者把極化寫成 80／0／20 [126]。要注意「依課數」與「依時間」是兩件事：Seiler 的 80／20 是依課數，同一批菁英選手依時間算是 91／6／2.6（`docs/research/aerobic-base-readiness.md` §2.4）；一堂間歇課的暖身與恢復都是低強度時間 [127][128]。

**觀察型研究**：菁英耐力選手的分配是金字塔形，Zone 1 占 84–95%、Zone 2 2–11%、Zone 3 2–9%；準備期以 HVLIT 為主，賽前期轉金字塔，比賽期轉極化 [92]。菁英跑者裡馬拉松選手偏金字塔、1500 m 選手偏極化，兩種課（vLT2 附近的中長間歇與 Zone 3 短間歇）每週都至少各 1 次 [129]。11.9 萬名跑者、15.2 萬場馬拉松前 16 週的資料中，最快的那一群 > 80% 是金字塔分配，主要靠增加 Zone 1 量 [130]。休閒長跑者平均 69／17／14，馬拉松選手的成績與 Zone 2 量相關（r = −0.6）[131]。92 份次菁英 12 週馬拉松課表（Seiler 共同作者）也是金字塔形 [132]。

**介入研究與統合分析**：Stöggl & Sperlich 2014 的 9 週研究中極化組 VO2peak 與力竭時間進步最多 [133]；Rosenblat 2019 的統合分析（3 篇）極化優於閾值型，ES = −0.66 [134]。但較新、較大的分析結論變溫和：Silva Oliveira 2024（17 篇）極化對 VO2peak 只有小優勢（SMD 0.24），且只在 < 12 週、高水準選手成立，對計時賽沒有差別 [135]；Rosenblat 2025（13 篇、348 人）極化與金字塔在 VO2max、計時賽都沒有差別，「recreational athletes may improve more with a PYR TID」[136]；另一篇網絡統合分析顯示所有模型的可信區間都跨 0 [137]；多項模型都能提升無氧閾值的速度，只有極化與金字塔能提升有氧閾值 [138]。Filipas 2022 的 16 週跑者研究中「先金字塔、後極化」進步最多 [139]；Muñoz 2014 的休閒跑者 10K 極化 5.0% vs 閾值間 3.6%，差異不顯著 [140]；Esteve-Lanao 2007 Zone 1 多的組進步較多，前提是高強度量足夠 [141]。中長跑系統性回顧的結論是：≥ 70% 低強度、≤ 30% 閾值＋高強度的組合最好 [142]。

**越野／超馬**：本次沒有找到越野或超馬的 TID 隨機對照試驗（缺口）。教練實務：Koop 的高強度約占 20% 的課、10% 的時數 [117]；Roche 約 80／20、週跑量 65–75 英里 [119]；一份 UA 24 週登山計畫實際是 Z1–2 78%、Z3–4 22%（依時數）[143]。一位世界冠軍的功率分析顯示，賽事越長，> 4 W/kg 的時間比例越低（垂直賽 44.9%、超馬 2.1%）[144]——也就是超馬的「專項強度」本來就在 LT1 附近，這支持 Koop「長時間低強度就是最專項的訓練」的看法 [145]。

**對使用者的意義**：休閒跑者沒有證據支持「一定要極化」，金字塔（有相當量的閾值附近課）至少一樣好，而且實務上最常見。這直接支持 SP-31——在 VO2max 課開始之後保留閾值課。

### Finding 6：有氧／閾值間歇 vs VO2max 間歇——生理、工休比、進階與前提

**生理目標不同**。閾值附近的間歇（Daniels 巡航、挪威 LGTIT、Friel 肌耐力、Koop TempoRun、UA Zone 3）目標是在乳酸大致穩定的狀態下累積時間：Daniels 說短休息「keep blood-lactate levels fairly constant」[68]；挪威派把乳酸控制在 2–4.5 mmol、每 1–3 趟量一次、休 20 秒到 1.5 分 [54][146]，Bakken 把目標從 4 mmol 降到 2.3–3.0 後效果更好 [13]。VO2max 間歇的目標是累積在 ≥ 90% VO2max 的時間 [147][148]：3 分鐘的趟比 30 秒的趟多約 60% 的 > 90% VO2max 時間 [149]，但各種設計的統合比較並沒有明顯贏家，「應依可行性與選手特性選擇」[150][151]。區塊訓練研究直接對照了兩者：中強度（5–7 × 10–14 分）與高強度區塊對 15 分鐘功率的進步相同，但中強度對乳酸閾值功率較好、高強度對衝刺功率較好；而且只有在高強度區塊，> 90% VO2max 的時間才與 VO2max 進步相關 [152][153]。Seiler 2013 顯示累積 32 分 @ 90% HRmax（4×8）勝過 16 分 @ 95%（4×4），但那個 4×8 是「最大可忍受強度」、9.6 mmol，屬於 severe，不是閾值課 [154]。

**W′ 是分界的力學解釋**。高於 CP 時可做的額外功是固定的（W′），只有回到 CP 以下 W′ 才開始恢復 [155][58]。所以 ≤ CP 的閾值課可以用很短的休息累積很長的時間；> CP 的 VO2max 課每趟都在消耗 W′，休息要足以部分恢復（工休比約 1:1）。這也是**分類應該先看強度相對 CP、再看趟長**的理由：同樣 8 分鐘，98% CP 是巡航，Seiler 的最大努力 4×8 就是 VO2max 類。

**工休比與每趟時間**（依來源）：

| 類型 | 每趟 | 工休比 | 每堂總量 | 來源 |
|---|---|---|---|---|
| UA Zone 3 | 15–60 分 | 4:1–5:1 | 起步 ~週有氧量 5% | [14] |
| Koop SteadyStateRun | 20–60 分 | 5–8:1 | 30 分–2 小時（可放在長跑裡，如 2 小時 ER 內 2×30 分） | [62][156] |
| Koop TempoRun | 8–20 分 | 2:1 | 30–60（或 40–90）分 | [62][11] |
| Daniels 巡航間歇 | 3–15 分（常見 1–2 英里） | 約 5:1（休 20–25%） | ≤ 週量 10%；連續節奏跑約 20 分 | [7][59][48] |
| Friel 肌耐力 | 6–12 分 | 4:1 | — | [90][89] |
| 挪威閾值 | 3–6 分或 400–2000 m | 休 30–60 秒 | 菁英 5×6 分、10–12×1000 m、20–25×400 m | [146][157][158] |
| Pfitzinger LT | 連續 20–45 分或拆成間歇 | — | 4 版起以時間開立 | [25][159][105] |
| Daniels I | 3–5 分 | 1:1（另一版本 2:1） | ≤ min(10 km, 8%) | [7][48] |
| Koop RunningIntervals | 2–4 分（另一頁寫 1–3 分） | 1:1 | 12–24 分 | [62][63] |
| Pfitzinger VO2max | 2–6 分 | 休 50–90% 趟時間；HR 降到 70% HRmax | 5–8 km | [46][160] |
| Helgerud 4×4 | 4 分 @ 90–95% HRmax | 恢復 3 分 @ 70% | 16 分 | [161] |
| UA Zone 4 | ≤ 4 分 | 1:1 | — | [14] |
| Daniels R | ≤ 2 分 | 1:2–1:3 | ≤ min(8 km, 5%) | [48][6] |
| UA 30/30 | 30 秒 @ 92–95% HRmax | 1:1，休息時 HR 只降約 5 bpm | — | [162] |

**排程與進階**（Q25 證據薄，主要依 repo 文件）。`docs/research/interval-prescription.md` §A4 已整理出：同儕審查沒有「先加組數還是先加強度」的 RCT，教練共識的順序是「總量 → 每趟長度 → 縮短休息 → 功率」；VO2max 每堂 10–16 分、閾值每堂 18–25 分（平日 45–50 分上限內）；頻率上，休閒者 4×4 每週 2–3 次都有效，3 次沒有比 2 次好 [163]，Pfitzinger 說 VO2max「每週 1 次通常就夠」[46]。本次新增的教練證據：Daniels 的巡航間歇從 1 英里逐步延長到 2×3 英里 [84]；Pfitzinger 的 LT 從約 20 分增加到 35–45 分 [164][105]；Koop 在 block 開頭（最有精神時）做最大量，之後隨疲勞累積遞減 [165]；CTS 的 TempoRun block 3–6 週、RunningIntervals block 3–5 週 [113]。**週內配置**：Pfitzinger 是 1 堂 LT＋中長跑＋長跑，品質日不連排 [10]；Daniels Phase II／III 是 1 堂主課（R 或 I）＋1 堂 T 次課＋長跑 [9]；UA 專項期是 1 堂 Z3＋1 堂 Z4 [12]；挪威菁英是 2 個雙閾值日＋1 堂不同刺激的「X element」[13]，或閾值為主加每週 1 堂 VO2max（4–6 × 3–5 分）[146]。**沒有任何學派的常態是「兩堂同一種強度課」**。

**前提（什麼時候可以開始）**。UA：AeT／AnT 差距 ≤ 10% 才加 Zone 3 與 Zone 4，先 Zone 3 [14][166]；改善所需時間依週時數，8 小時以上約 2–3 個月、5–6 小時 3–4 個月、< 3 小時 6 個月以上 [166]。Friel：在 AeT 強度跑完目標時間且 decoupling < 5%，代表可以進入 build [167]。Seiler 與 Koop 沒有數字門檻 [117]（`docs/research/aerobic-base-readiness.md` §2.4–2.5）。Lydiard 的原則是依序發展，因為「同時建有氧又做硬的無氧練習會互相干擾」[168][98]。Daniels 的 Phase I 只有輕鬆跑與 strides [83]。研究方面，沒有找到在跑者身上直接比較「先純基礎」與「一開始就混合」的 RCT（`docs/research/aerobic-base-readiness.md` §2.8）。

**上坡版**（Q27 證據薄）。Koop 約 80% 的間歇在上坡做，理由是上坡的 VO2 反應較高、地面衝擊較小 [169][170]；但 Buchheit 回顧中 5% 坡的 T@VO2max 比例低於平路（`docs/research/interval-prescription.md` §A3）。UA 的肌耐力課在 30–100% 陡坡、負重 5–10% 體重起步，心率會比平常 Zone 3 低，「Heart rate will not be a reliable guide」[111]；坡衝刺要 ≥ 20% 坡、8–10 秒 [12]。CTS 建議用接近賽道的坡度（約 ≤ 20%），而不是追求極端爬升 [171]，並且越野課以時間＋RPE 開立 [172]。這些都支持 app 現行「上坡課前 3 分鐘不看心率、用功率或 RPE」的做法（`docs/research/vo2max-gate-and-trail-metric.md` §2.3–2.5）。

### Finding 7：一句話訓練目的（可直接貼進範本的 `purpose` 欄位）

| 課型 | 一句話目的 | 來源 |
|---|---|---|
| 恢復跑 | 低於 AeT 的短跑，促進恢復、同時累積有氧量 | [173][106][63] |
| 輕鬆跑（E） | 在 LT1 以下建立微血管、粒線體與脂肪代謝，是一切強度的地基 | [7][174][175] |
| 長跑（LSD） | 延長在有氧強度下的持續時間，提升耐久性與脂肪利用 | [174][6][176] |
| 中長跑（Pfitzinger MLR） | 週中第二個長刺激，增加耐力而不必再加一次長跑 | [10][103] |
| 背靠背長跑 | 在疲勞的腿上跑，模擬超馬後段；兩天都應保持輕鬆 | [177][178] |
| 穩態跑（SteadyState／Lydiard steady） | 在 LT2 以下的「有挑戰的有氧強度」累積長時間，提升高端有氧耐久 | [179][67] |
| 馬拉松配速（M） | 熟悉比賽配速與補給，代謝效益接近輕鬆跑，量以週量 15–20% 為限 | [7][59] |
| 節奏跑（T，連續 20 分以上） | 在乳酸閾值附近跑，提升清除乳酸的能力、推高 LT2 | [6][68][7] |
| 巡航間歇（T，3–15 分短休） | 用短休息讓乳酸維持穩定，在閾值強度累積比連續跑更多的時間 | [68][7] |
| 長節奏／有氧間歇（15–30 分、LT2 下） | 在 AeT–AnT 之間累積時間，縮小有氧缺乏差距、建立越野爬坡所需的持續力 | [14][62] |
| VO2max 間歇（I，2–5 分） | 累積在接近最大攝氧量的時間，提高最大有氧能力 | [6][7][147] |
| 30/30、30/15 短間歇 | 用極短休息讓心肺維持在 VO2max 附近，同時限制乳酸累積 | [162][180] |
| 重複跑（R，≤ 2 分長休） | 在充分休息下以快於 VO2max 的速度跑，改善速度、跑姿與跑步經濟性 | [6][181][182] |
| 加速跑（strides） | 20–30 秒快而放鬆，維持神經肌肉速度與跑姿，幾乎不增加疲勞 | [183][119] |
| 坡衝刺 | ≥ 20% 坡 8–10 秒全力，訓練爆發力與肌肉徵召，不是有氧課 | [12][184] |
| 坡重複（3–5 分） | 在較低衝擊下達到高心肺負荷，訓練有氧能力與爬坡力量 | [183][169] |
| 肌耐力（UA ME、負重爬坡） | 讓腿部在高比例最大力量下重複上千次，限制來自腿而不是呼吸 | [111][12] |
| 下坡跑 | 透過重複回合效應減少賽後肌肉損傷，放在賽前最後幾週 | [120] |
| 測試（30 分 TT、AeT 飄移） | 重新校正錨點，讓所有區間跟著真實體能移動 | [16][185] |

### Finding 8：App 現行規則對照各學派——缺口在哪

App 的現行行為（以程式碼為準，spec 有幾處已過時）：周期只有轉換、基礎、專項、減量、賽事、恢復六種，沒有 build／peak；沒手動設定時從 A 賽事往回推「專項 8 週＋減量 14 天」（`backend/engine/planning.py:36-51`）。基礎期與專項期每週 1 堂 LSD＋1 堂強度課，強度課來自單一階梯：三區 T1 3×6′ → T2 3×8′ → T3 2×12′（90–95% CP），之後五區 V1 5×2′ → V2 4×3′ → V3 5×3′ → V4 4×4′，再來 V3／V4／T+（3×7′ @ 97–100%）輪替（`backend/engine/quality_gate.py:96-147`）。偏好每週 2 堂時，第 2 堂是同一階的複本（`backend/engine/overview.py:893-895`）。三區只看護欄；五區要三選一的有氧基礎確認（徐國峰 90 分、UA 差距、Friel 飄移）**且**三區達標 3 堂（`backend/engine/quality_gate.py:1519-1523`、`backend/engine/base_check.py:456-460`）。專項期路跑是 2×15′ 閾值節奏、越野是 5×4′ 上坡 supra（101–106% CP）、減量期 4×3′ @ 98–102%（`backend/engine/overview.py:873-899`）。

對照前面的發現，缺口依嚴重程度排列：

**缺口 1：五區開了，閾值課就幾乎消失。** 五區解鎖後階梯進入 V1–V4，閾值課只剩每 3 週一次的 T+。這和每一個有來源的學派相反：Daniels Phase II、III 的次課一直是 T，Phase IV 以 T 為主 [9][84][82]；Pfitzinger 以 LT 為主幹，VO2max 是後段的補充 [103][10]；Koop 的 TempoRun 與 SteadyState 是越接近比賽越重要的課 [113]；UA 專項期是 1 堂 Z3＋1 堂 Z4 [12]；挪威派以閾值密度為核心 [186][13]；菁英跑者兩種課每週都至少各 1 次 [129]。金字塔分配對休閒跑者至少與極化一樣好 [136][130]。現行設計等於「有氧基礎達標 → 閾值量反而減少」。

**缺口 2：強度課只有「強度」一個維度，沒有「家族」與「目的」。** 分類只按主課 %CP 中點分三區（88–101%）、四區（101–106%）、五區（≥ 106%）（`backend/engine/workout_templates.py:27-37`），Daniels R 8×300 與 Daniels I 5×3′、Billat 30-30 都在五區；Palladino「最大有氧功率 4×2:40」被放在四區（`backend/engine/workout_templates.py:201-205`）。`Template` 沒有目的欄位（`backend/engine/workout_templates.py:99-111`）。各學派都是用「目的」組織課表的：Daniels E／M／T／I／R、Friel 的能力（有氧耐力、肌耐力、無氧耐力、爆發力）[88][90]、Koop 的五種課 [62]。三區內同時有 LT2 下的 tempo 與 LT2 上的 cruise，也沒有分開。

**缺口 3：心率錨點隨模型漂移，而且編輯器不跟模型。** 輕鬆跑上限＝「課表心率區間」模型的 Z2 上緣，只有實測 AeT 才覆蓋（`backend/engine/hr_profile.py:270-278`）。所以選 HRR 模型時上限是 74% HRR、選 HRmax 模型時是 60% HRmax——前者對 LT2 落在 HRR 低端的人可能高於 LTHR，後者對大多數人遠低於 LT1（LT1 平均 79–84% HRmax [4]）。編輯器的「區間」下拉固定用 Friel %LTHR（`backend/engine/workout_steps.py:69-70`、`backend/engine/workout_steps.py:132-134`），自動目標卻用課表模型，同一堂課兩種尺混用（SP-30 的根因）。此外三區階梯的心率文字寫「AeT–LTHR」，涵蓋整個 Seiler 中區，對不上 90–95% CP 的功率帶。

**缺口 4：周期與專項期課的來源。** 專項 8 週落在各學派的範圍內（Friel build 8–9 週、UA 2–3 個月、Canova specific 6–10 週、Koop 峰值 block 賽前 6–9 週）[86][12][100][116]，這點沒問題。但專項期內部沒有進階（從 TempoRun 到 SteadyState、從一般到 ±3% MP 的 funnel）；越野專項期的 5×4′ supra 每堂 20 分、坡度 6–10%，比 repo 文件建議的爬坡重複（4–6×3–4′、3–8% 坡）重（`docs/research/vo2max-gate-and-trail-metric.md` §2.3–2.5）；減量期 4×3′ 與證據相容——減量要保留強度、UA 減量期也保留 2–4 分間歇 [122][112]——但它應該是「比賽專項強度的縮量版」，對越野長賽可能是 tempo 而不是 98–102% CP。轉換期只能手動設；Friel 與 Canova 都建議賽後 3–4 週 [91][101]，Pfitzinger 給 5 週恢復表 [106]。

**缺口 5：護欄的低強度占比。** App 用 < AeT 的時間 ≥ 75%（`backend/engine/quality_gate.py:499-533`）。各學派：80/20 約 80%（依時間）[72]、UA 基礎期 ≥ 90% [12]、Seiler 依時間約 91%、中長跑回顧建議 ≥ 70% [142]。75% 作為「本週能不能排」的下限是合理的，但基礎期目標顯示 ≥ 90% 而護欄 75%，兩個數字的語意要在 UI 上分清楚（目標 vs 底線）。

**現行做法裡有證據支持、應保留的部分**：五區每趟 ≥ 2 分、每週 ≤ 2 次、間隔 ≥ 2 天（Daniels I 3–5 分、Koop 2–4 分、Pfitzinger 2–6 分、UA ≥ 48 小時 [6][62][46][14]）；五區 10–16 分的總量（`docs/research/interval-prescription.md` §A4.2）；三區優先於五區的順序（UA「Start with Zone 3」[14]、Pfitzinger 先 LT 後 VO2max [103]）；減量期保留強度 [122]；強度課與長跑隔 ≥ 48 小時（`backend/engine/plan_prefs.py:600-625`）[14]。

### Finding 9：SP-30——app 的預設心率模型應該是哪一個

證據給的排序很清楚：實驗室閾值 > 個人實測 LTHR／AeT > %HRR ≈ %HRmax（實測最大心率）> 公式最大心率（`docs/research/zones-and-thresholds.md` §2.1）。本次新增的證據強化了這個排序：固定錨點估 LT2 的心率誤差（2.8–5.2 bpm）小於估 LT1（4.9–7.4 bpm），而「用估計的最大值」會讓誤差升到 6.7–8.4 bpm [4]；LT2 在 73–95% HRR 之間都可能 [3]；公式最大心率誤差 7–12 bpm [187][188]。HRR 確實比 %HRmax 更貼近攝氧量儲備 [187]，徐國峰的 RQ 6 區（59／74／84／88／95% HRR）也和 Daniels 的 %HRR 表一致——**對一個 LT2 剛好在 87% HRR 的人，RQ 的 T 區正好等於 98–101% LTHR**（Finding 2）。HRR 模型的問題不是表本身，而是它需要兩個錨點（HRmax、HRrest）都準，而且把 LT1、LT2 的個人位置當成固定。

**建議預設：%LTHR（COROS 6 區），不是 HRR，也不是 Friel。** 理由：(1) 以 LT2 為錨，符合 Finding 1；(2) 使用者的手錶是 COROS，COROS EvoLab 本身就用 LTHR 模型分析 [21]，課表推到錶上時區間一致；(3) Friel 與 COROS %LTHR 的差別很小（Z1／Z2 邊界 85 vs 80、Z4 上緣 100 vs 102），兩套並存只會增加混淆；Friel 保留在圖表選單，標成「Friel（經典跑步版）」，因為 2025 年 9 月修訂版的數字未能驗證 [15]。HRR 與 HRmax 保留為選項，但加三條保護：

1. **一致性檢查**：選 HRR 時，計算 LTHR 對應的 %HRR；若不在約 80–95% HRR（休閒跑者平均 86–88%，個人範圍 73–95% [4][3]），顯示「LTHR 與最大／靜息心率互相矛盾，請先重測其中一個」，並暫停用 HRR 產生課表目標。
2. **輕鬆跑上限與模型脫鉤**：永遠是「實測 AeT，否則 0.89 × LTHR」，再取 min(模型 Z2 上緣, 這個值)。這保證任何模型都不會讓輕鬆跑超過 LT1 的族群平均位置。
3. **編輯器下拉跟隨課表模型**：列出課表模型的 Z1–Z6（標模型名），Friel 放在第二組；儲存 `{zone, model}`；HRR 模型下的百分比模式顯示 % HRR。

### Finding 10：使用者筆記中與證據不一致的地方

以下是筆記裡幾個值得回頭修訂的說法。這些大多來自轉述或不同版本的混用，原始教練的意思通常是對的；列出來是為了讓 app 的規則不要繼承這些誤差。

1. **「進入 zone 4 身體才開始用肝醣」**。有氧與無氧能量系統是同時運作的，沒有一個配速會切換開關 [66]；VT1 之後只是碳水比例上升 [19]。比較準確的說法是「強度越高，肝醣占比越高」。
2. **「FTP 強度有氧、無氧各占一半」**。本次沒有找到量化這個比例的來源；但 LT2／CP 的定義就是「還能維持代謝穩態的最高強度」[1]，這代表能量仍以有氧為主。這一條建議刪除或改寫。
3. **「恢復到 65% HRmax（zone 3 的上限）」**。在所有 %HRmax 系統裡 65% 都是 zone 1–2（Garmin／Polar zone 3 是 70–80%）[51][52]，作為休息目標合理，但不是 zone 3 上限。
4. **「I 配速能撐 30 分到 1 小時」**。能撐約 1 小時的是 T [7][189]；I 是 3–5 分的趟，比賽中約 10–15 分 [6]。筆記其他頁的寫法是對的。
5. **「R 在最大心跳率之上還有一級」**。心率不會超過最大心率；R 是用配速定義的，比 I 配速每 400 m 快約 6 秒，心率不適合當目標 [79][8]。
6. **「LTHR 就是 4 mmol 標準」**。Friel 的 LTHR 是 30 分鐘獨跑測驗後 20 分的平均心率 [16][190]；4 mmol 是實驗室慣例，個人 MLSS 的乳酸可在 2–8 mmol [17]。
7. **「T ≤ 週量 10%」接上「10% 規則被 Palladino 否定」**。前者是 Daniels 的單堂 T 量上限 [6]，後者是「每週增量 10%」的經驗法則，兩件事無關。
8. **「巡航間歇加總要 > 30 分」**。Daniels 的規則是 3–10 趟、每趟 3–15 分、T ≤ 週量 10% [7][6]，沒有最少 30 分的要求；Daniels 也說 20 分 T 對大部分跑者練一次就夠（`docs/research/interval-prescription.md` §A1.2）。
9. **4×4 的恢復寫成 4 分、心率「接近 90% 以上」**。Helgerud 的 4×4 是 90–95% HRmax、恢復 3 分 @ 70% [161]；Pfitzinger 的 VO2max 目標是 93–95% HRmax [160]。
10. **「3×6′ @ 105%」被當成 VO2max 能力課**。Palladino 把 101–106% 叫 supra-threshold，VO2max 是 106–116% [23]；Coggan L5 也是 106–120% [8]。105% 的 6 分鐘趟更接近 Friel 的長間歇。
11. **%HRR 與 %HRmax 混用**（例如 A 區同時寫成 89–94% HRmax 與 88–95% HRR）。同一個百分比在兩種系統是不同的心率；對靜息心率低的跑者，差距可達 5–10 bpm（Finding 2 的 HRR 換算）。
12. **「90 分鐘測試 < 5% 是國家級」vs app 的「Friel < 5%」**。兩者不是同一個指標：徐國峰是 E 配速第 90 分與第 10 分的心率差，Friel 是在 AeT 強度前後半段的 Pa:HR decoupling [167]，不能直接比較。

## Synthesis & Insights（綜合與洞見）

### 模式一：生理只有三條線，爭議幾乎都是命名

把 13 套系統攤在同一張表上之後，真正的生理分界只有 LT1、LT2、VO2max 三條（Seiler 三區就是這三條線本身 [43]），其餘的「區」都是教練為了開課表方便而在兩條線之間切的刻度。Olympiatoppen 的 I-3 對齊 Seiler 的 Zone 2 [31]，80/20 的 X、Y 是刻意標出的「縫」[72]，Friel 的 5a／5b／5c 是把 LT2 以上切成三段 [191]。使用者遇到的混亂（「三區」是 T 還是灰區、Zone 3 是 tempo 還是高強度）幾乎全來自不同系統用同一個數字編號。**App 應該在內部只用三條線＋少數「家族」來推理，所有「第幾區」都只是顯示層的翻譯**。

### 模式二：心率與功率對「LT2」的表現不對稱

這是本次整合裡最實用、也最少被明說的一點：在心率上，LTHR、MLSS 心率、CP 心率平均幾乎重疊（差 < 1 bpm）；在功率／速度上，CP 與 MLSS 差 5–10%、VT2 偏更多 [1][2][28]。所以「把功率區和心率區用同一個百分比互推」在 LT2 附近會差半區到一區。App 已經在 `docs/research/zones-and-thresholds.md` §3.3 禁止 %CP↔%LTHR 互推，但 `WORKOUT_TARGETS` 的 threshold 列（95–101% CP 配 0.95–1.00 LTHR）與三區階梯的「AeT–LTHR」心率文字仍隱含互推。建議的修正方向是：功率目標照 Palladino，心率目標另外依 Friel／COROS 的同名區給，兩者各自標來源，不從對方換算。

### 模式三：引入強度的順序各派不同，但「越接近比賽越像比賽」是共識

Daniels 的通用順序是輕鬆 → R → I → T [82]；Pfitzinger 是 LT → VO2max [103]；UA 是 Zone 3 → Zone 4 [14]；Lydiard 是有氧 → 坡 → 無氧 → 協調 [97]；Koop／CTS 則是 VO2max → Tempo → SteadyState（反向）[113]。順序彼此矛盾，沒有一個有 RCT 支持；唯一有實驗的是 Filipas 2022「先金字塔後極化」稍優 [139]。但所有人都同意「越接近目標賽，越練目標賽的強度」：Friel 的 build 要像比賽 [192]、Canova 的 funnel 收斂到 ±3% MP [61]、Koop 把最專項的放最後 [117]。對 app 的含意：**階梯的「終點」應該由目標賽決定，而不是固定在五區**。路跑 5K–10K 的專項強度是 VO2max 附近；半馬到馬拉松是 LT2 附近；越野長賽與超馬的專項強度在 LT1–LT2 之間（世界冠軍的超馬 > 4 W/kg 時間只有 2.1% [144]）。現行設計把 V3／V4 輪替當成維持期常態，對越野超馬目標是反過來的。

### 洞見：使用者的「三區／五區」直覺本身是對的，問題是 app 把它做成一條線

使用者把強度課分成「有氧間歇（三區）」與「無氧／VO2max 間歇（五區）」，並希望兩者各有關卡，這和 UA（Zone 3 與 Zone 4 兩種課）、Koop（TempoRun 與 RunningIntervals 不同 block）、Daniels（主課與次課）的結構一致。App 的單一階梯把兩個家族串成一條線（三區是五區的前置關卡），結果是「升級＝換掉三區」。拆成雙軌、各自進階、每週依目標賽與周期決定比例，是證據與使用者需求的交集。

### 二階影響

雙軌之後，每週兩堂強度課會比現在常見（現在第 2 堂只是複本），總高強度時間可能上升。必須同時保留現有護欄（低強度占比、CTL ramp、週增量、48 小時間隔），並且讓三區的總量受「≤ 週量 10%」約束 [6]，否則金字塔會滑向閾值型分配——Rosenblat 2019 的統合分析與中長跑系統性回顧都顯示閾值型不如低強度量大的分配 [134][142]（但也有反例，見 Counterevidence Register）。

## Limitations & Caveats（限制與注意事項）

### Counterevidence Register

**反證 1：極化可能優於金字塔。** Stöggl & Sperlich 2014 與 Rosenblat 2019 都顯示極化優於閾值型或其他分配 [133][134]，高水準選手在短期介入中極化對 VO2peak 有小優勢 [135]。本報告建議的「保留閾值課」較接近金字塔。解讀：較新、較大的統合分析在休閒者身上找不到差異，且觀察資料中最快的馬拉松跑者多為金字塔 [136][130]；影響：中等——對想專攻 5K–10K 的高水準跑者，可提高五區比例。

**反證 2：閾值型對 VO2max 可能最好。** 一篇貝氏網絡統合分析的排名中，閾值型最可能是提升 VO2max 的最佳模型、HIT 最可能是提升計時賽的最佳模型，但所有可信區間都跨 0 [137]。解讀：排名不確定性高，與其他統合分析不衝突於「沒有明確贏家」；影響：低。

**反證 3：引入強度的順序。** Daniels（R → I → T）與 CTS（VO2max 最先）都與 UA／Pfitzinger 的「先閾值」相反 [82][113]。本報告仍建議三區先於五區，但降為軟條件；影響：中等，已反映在 R3。

**反證 4：不需要門檻。** Seiler 與 Koop 都沒有高強度前的數字門檻 [117]，研究也沒有支持「先純基礎」的 RCT。本報告的三區時間路徑與五區測試關卡因此屬於教練規則與安全考量，而非實證必要；影響：中等。

### Known Gaps 與不確定性

**原典取得有限**。Daniels、Friel、Pfitzinger、Koop、UA 的書本內容多透過教練網站、書評與讀書筆記取得，數字因版次而異（Daniels T 的 %HRmax 至少三種寫法 [6][7][48]；80/20 初版與現行區界不同 [41][20]；Pfitzinger 4 版改用時間開 LT 課 [164]）。Friel 2025 年 9 月修訂的區間表是圖片，無法驗證 [15]。報告採區間呈現並標信心等級，但仍可能有個別數字與最新原書不同。

**換算建立在族群平均上**。%HRmax→%LTHR 用 0.90、%HRR 用 87%、速度用 VT1／VT2 的平均比例，個人可能偏離一整區 [3]。這正是建議 app 要求實測錨點的理由，但也意味著表 2a 的 %HRmax／%HRR 欄只能當「大概在哪」，不能當處方。

**LT2 的操作定義本身有爭議**。CP 是否等於最大代謝穩態、MLSS 是否低估，文獻仍在辯論 [193][1][194]；Stryd 的 CP 定義與 80/20 的 rFTP 不同，80/20 教練直言「there is no industry standard on critical power」[195]。Friel 30 分 TT 的 LTHR 也沒有找到對實驗室 LT2 的驗證研究。

**越野／超馬證據薄**。沒有找到越野或超馬的強度分配 RCT；Q27（上坡間歇）與 Q25（週內排程）證據少，建議中的進階順序與比例多為教練共識或推估（已標示）。休閒跑者的研究多為 6–16 週，長期（季到年）效果未知 [135]。

**乳酸區間無法精確換成心率**。挪威派以 mmol 控制強度，乳酸與心率的關係個人差大，Olympiatoppen 也明說要個人化 [45]；手持乳酸計之間的差異更大 [45]。表中挪威派的 %LTHR 欄是推估。

**App 端分析的限制**。程式碼分析是唯讀、未執行；行號以撰寫當天為準。部分 spec 與 code 不一致（例如 `docs/spec/overview.spec.md` 的舊階梯描述），本報告以 code 為準。

## Recommendations（建議）

### 立即可做（對應現有的單）

**R1｜SP-32：強度課分三個家族，先看強度、再看趟長。** 分類函式 `family_of(main)` 的判斷順序：

1. 主課強度 ≤ 101% CP（或心率目標 ≤ 1.02 LTHR、或配速不快於 T）→ **有氧間歇（閾值家族，「三區」）**。其下兩個子類：**長節奏**（每趟 ≥ 15 分或連續 ≥ 20 分，88–95% CP／0.90–0.96 LTHR，工休 4:1–8:1）[14][62]；**巡航**（每趟 6–15 分，95–101% CP／0.96–1.00 LTHR，工休約 4:1–5:1）[7][90]。
2. 主課強度 > 101% CP、每趟 2–5 分、工休約 1:1 → **VO2max 間歇（「五區」）**。30/30、30/15 這類短趟短休的課也屬此類，因為目的同樣是累積 VO2max 時間 [162][147]。
3. 每趟 ≤ 2 分且休息 ≥ 2 倍趟時間，或強度 > 116% CP、或以 R 配速開立 → **速度（R／strides／坡衝刺）**，不設心率目標 [6][8]。
4. 101–106% CP、每趟 ≥ 6 分 → 歸「巡航」上緣（標「超閾值」）；每趟 2–5 分 → 歸 VO2max（例如 Palladino 4×2:40 改到 VO2max 家族，名稱也一致）。

**2–5 分 vs 3–5 分的統一**：用 **2–5 分**。Daniels 的「最佳 3–5 分」是核心，但 Koop 2–4 分、Pfitzinger 2–6 分、Buchheit「≥ 2–3 分」都包含 2 分 [6][62][46]；app 的 V1 5×2′ 配短休息（走路）符合。**6–12 分**屬於有氧間歇的「巡航」子類（Friel 肌耐力 6–12 分、Daniels 巡航 3–15 分、挪威 5×6 分）[90][7][157]；**< 2 分**依休息長短分：短休（≤ 趟時間）歸 VO2max、長休歸速度。把「無氧間歇」改名為「**VO2max 間歇**」，因為文獻與 Daniels 都把這類稱為有氧能力訓練 [80][6]。範本加 `purpose` 欄位，內容直接用 Finding 7 的表。

**R2｜SP-31：雙軌階梯，三區在五區開放後不消失。**

- **三區軌（有氧間歇）**：依使用者定義改成長節奏為主，巡航為平日替代。建議進階（推估，順序依 `docs/research/interval-prescription.md` §A4.1「總量 → 每趟長度 → 縮短休息 → 強度」）：A1 2×15′（休 3′）→ A2 3×12′ 或 2×18′ → A3 2×20′ → A4 1×30′ 或 3×15′ → 維持期在 A3／A4 與「長跑內含 2×15–20′ 穩態」之間輪替（Koop 的「2 小時 ER 內 2×30 分 SSR」[62]、Pfitzinger 的中長跑 [10]）。強度 88–95% CP 或 0.90–0.96 LTHR。
- **量的約束**：單堂三區時間 ≤ 週跑步時間 10%（Daniels T 上限）[6]；第一次排入時從約 5% 起步（UA）[14]。例：每週 5 小時的跑者，5% = 15 分、10% = 30 分，所以 A1 的 2×15′ 已經是上限——週量較低時，三區軌自動改用相同總量的巡航版（例如 3×8′）。
- **平日時間上限**：2×15′ 加暖身緩和約 55–60 分，超過平日 45 分上限，因此長節奏預設排在週末或併入中長跑；平日用巡航版（現行 T1–T3 保留為這個子類）。
- **每週組合**（推估，依 Finding 6 的週內配置）：偏好 2 堂時固定「1 堂三區＋1 堂五區」（五區未開時兩堂都是三區軌的不同子類），取代現行的同階複本；偏好 1 堂時，基礎期每週三區，五區開放後依目標賽輪替——路跑 10K 以下「五區：三區 ≈ 1:1」、半馬以上與越野「三區：五區 ≈ 2:1」，越野超馬在專項期改為三區（上坡長節奏）為主。
- **專項期與減量期也走雙軌結果**：越野專項期的三區用上坡長節奏 2×12–20′ @ 90–100% CP（`docs/research/vo2max-gate-and-trail-metric.md` §2.3–2.5），五區用 4–6×3–4′ 坡重複（3–8% 坡），取代現行 5×4′ supra；減量期保留一堂「目標賽強度的縮量版」，總量減 40–60%、維持強度 [122]。
- 課表與總覽要顯示「這週為什麼沒排三區」（哪條護欄、或三區量已達 10% 上限）。

**R3｜SP-39：兩道獨立關卡。**

- **三區關卡（有基礎了就能做）**，三選一，皆需護欄通過：(a) 時間路徑——連續 ≥ 4 週基礎期、低強度占比 ≥ 80%、沒有護欄擋課（推估；UA 登山計畫在 8 週轉換＋4 週基礎後才出現第一堂 Z3 [110]，Pfitzinger 第一個 mesocycle 是 5 週耐力 [10]）；(b) 徐國峰 90 分鐘飄移 < 10%；(c) 已有實測 UA 差距 ≤ 10%。時間路徑保證沒做測試的人也會自動前進，這符合 Seiler／Koop「不設數字門檻」的立場（`docs/research/aerobic-base-readiness.md` §2.4–2.5）。
- **90 分鐘測試算不算 AeT 測試？不算。** 它在固定 E 配速下比較第 90 分與第 10 分的心率，回答的是「這個配速的有氧耐久夠不夠」，不會產生 AeT 的數字。產生 AeT 的是 UA 心率飄移測試：從某個心率開始跑 40–60 分，飄移 3.5–5% 時起始心率就是 AeT [185]；Friel 的 decoupling 則要先知道 AeT [167]。所以 90 分鐘測試放在三區關卡，五區關卡要求一個產生 AeT 數字的測試。
- **五區關卡**：(1) 實測 AeT（UA 40–60′ 飄移測試或 app 的 AeT 測試）**＋** 近期實測 LTHR（30 分 TT），且 LTHR／AeT − 1 ≤ 10% [14][166]；或 (2) 在實測 AeT 強度跑 ≥ 60–90 分、decoupling < 5% [167]。
- **五區要不要先做三區？保留，但改成軟條件**：過去 6 週內完成 ≥ 2 堂三區課（推估）。依據是 UA「Start with Zone 3」與 Pfitzinger 先 LT 後 VO2max [14][103]；但 Daniels 與 CTS 的順序相反 [82][113]，沒有 RCT 支持任何一種，所以不應該是「三區達標 3 堂」這種與階梯 step 綁死的硬條件。兩軌的 step 分開計數。
- 流程表與 `week_decision` 讀同一份 flag；未完成的項目帶「安排課表」動作（測試走現有排入測試流程，間歇帶入該階 variant）。

**R4｜SP-30：預設模型與編輯器。** 見 Finding 9：預設 COROS %LTHR；HRR／HRmax 為選項並加一致性檢查；輕鬆跑上限與模型脫鉤（實測 AeT 優先，否則 0.89 × LTHR，再取與模型 Z2 上緣的較小值）；編輯器下拉跟隨課表模型並標模型名，Friel 放第二組。

### 下一步（1–3 個月）

**R5｜轉換期自動化**：A 賽事的恢復期之後自動接 3–4 週轉換期（無強度課、可交叉訓練）[91][101]，使用者可關閉。

**R6｜專項期內部進階**：依目標賽把專項期分成前段（支持強度，例如越野的 TempoRun／路跑的 ±10% 比賽配速）與後段（專項強度，例如越野的 SteadyState 與長跑內含穩態、路跑的 ±5%）[113][196]。

**R7｜統一 spec 與研究文件**：更新 `docs/spec/plan-auto.spec.md` 的「The ladder」「Two gates」，以及 `docs/research/workout-templates.md` §1 的分類；`docs/research/interval-prescription.md` 標頭的「尚未實作」改掉。

**R8｜錨點測試**：在涼爽天做一次 30 分 TT（LTHR＋CP 交叉檢查）與一次 UA AeT 測試，作為五區關卡的前提，也解決 HRR 模型的一致性問題（`docs/research/zones-and-thresholds.md` §3.3）。

### 待研究

取得 Daniels 第 4 版與 Friel 2025 修訂版的原表，替換表 2a 的二手數字；找越野／超馬的強度分配介入研究（目前只有教練實務）；驗證 Friel 30 分 TT LTHR 對實驗室 LT2；用 app 自己的資料比較「雙軌」上線前後的低強度占比與 CTL ramp，確認沒有滑向閾值型分配。

## Bibliography（參考文獻）

[1] pmc.ncbi.nlm.nih.gov (n.d.). "The maximal metabolic steady state: redefining the gold standard". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC6533178/ (Retrieved: 2026-10-04)

[2] zenodo.org (n.d.). "Relative Proximity of Critical Power and Metabolic/Ventilatory Thresholds: Systematic Review and Meta-Analysis". zenodo.org. https://zenodo.org/records/17816842 (Retrieved: 2026-10-04)

[3] runningwritings.com (n.d.). "Individual variation in heart rates at LT1 and LT2 in runners, and the implications for zone training". runningwritings.com. https://runningwritings.com/2025/02/lt1-lt2-heart-rate-individual-variation.html (Retrieved: 2026-10-04)

[4] pmc.ncbi.nlm.nih.gov (n.d.). "The accuracy of fixed intensity anchors to estimate lactate thresholds in recreational runners". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC12354492/ (Retrieved: 2026-10-04)

[5] pmc.ncbi.nlm.nih.gov (n.d.). "Towards Accurate Reference Values for Heart Rate and Speed Zones by Aerobic Fitness and Sex in Long-Distance Runners". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC12845794/ (Retrieved: 2026-10-04)

[6] coachray.nz (n.d.). "Jack Daniels Running Intensity". coachray.nz. https://www.coachray.nz/2023/05/03/jack-daniels-running-intensity/ (Retrieved: 2026-10-04)

[7] en.wikipedia.org (n.d.). "Jack Daniels (coach) - Wikipedia". en.wikipedia.org. https://en.wikipedia.org/wiki/Jack_Daniels_(coach) (Retrieved: 2026-10-04)

[8] trainingpeaks.com (n.d.). "Power Training Levels (Andrew Coggan, TrainingPeaks)". trainingpeaks.com. https://www.trainingpeaks.com/blog/power-training-levels/ (Retrieved: 2026-10-04)

[9] runningwithrock.com (n.d.). "A Review of the 5k to 10k Training Plan in Jack Daniels Running Formula". runningwithrock.com. https://runningwithrock.com/review-jack-daniels-5k-10k-training-plan/ (Retrieved: 2026-10-04)

[10] patricedouge.com (n.d.). "Pete Pfitzinger — Training Philosophies". patricedouge.com. https://patricedouge.com/training/philosophies/pfitzinger (Retrieved: 2026-10-04)

[11] trainright.com (n.d.). "Stop Wasting Miles: Key Workouts Every Ultrarunner Should Do - Jason Koop". trainright.com. https://trainright.com/12-running-workouts-for-ultramarathon-success (Retrieved: 2026-10-04)

[12] uphillathlete.com (n.d.). "How to Train for Trail Running: Endurance, Strength, and Programming for Mountain Races". uphillathlete.com. https://uphillathlete.com/trail-running/training-for-trail-running/ (Retrieved: 2026-10-04)

[13] mariusbakken.com (n.d.). "The Norwegian model of lactate threshold training and lactate controlled approach to training". mariusbakken.com. https://www.mariusbakken.com/the-norwegian-model.html (Retrieved: 2026-10-04)

[14] uphillathlete.com (n.d.). "When and How to Add High-Intensity Training: The 10 Percent Test". uphillathlete.com. https://uphillathlete.com/aerobic-training/when-to-add-intensity-training/ (Retrieved: 2026-10-04)

[15] joefrieltraining.com (n.d.). "A Quick Guide to Setting Zones - Joe Friel". joefrieltraining.com. https://joefrieltraining.com/a-quick-guide-to-setting-zones/ (Retrieved: 2026-10-04)

[16] trainingpeaks.com (n.d.). "Joe Friel's Quick Guide to Setting Training Zones". trainingpeaks.com. https://www.trainingpeaks.com/learn/articles/joe-friel-s-quick-guide-to-setting-zones/ (Retrieved: 2026-10-04)

[17] inscyd.com (n.d.). "Does the anaerobic threshold really occur at 4 mmol/l blood lactate?". inscyd.com. https://inscyd.com/article/anaerobic-threshold-4mmol-lactate/ (Retrieved: 2026-10-04)

[18] pubmed.ncbi.nlm.nih.gov (n.d.). "Towards Accurate Reference Values for Heart Rate and Speed Zones by Aerobic Fitness and Sex in Long-Distance Runners (Sports 2026)". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/41590971/ (Retrieved: 2026-10-04)

[19] pmc.ncbi.nlm.nih.gov (n.d.). "Zone 2 Intensity: A Critical Comparison of Individual Variability in Different Submaximal Exercise Intensity Boundaries (Meixner et al.)". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC11986187/ (Retrieved: 2026-10-04)

[20] 8020endurance.com (n.d.). "Intensity Guidelines for 80/20 Running". 8020endurance.com. https://www.8020endurance.com/intensity-guidelines-for-80-20-running/ (Retrieved: 2026-10-04)

[21] coros.com (n.d.). "COROS Heart Rate Zones: The Ultimate Guide". coros.com. https://coros.com/stories/coros-metrics/c/coros-heart-rate-zones-the-ultimate-guide (Retrieved: 2026-10-04)

[22] trainright.com (n.d.). "Zone 3: Why, When, and How Leverage Zone 3 Training". trainright.com. https://trainright.com/zone-3-why-when-and-how-leverage-zone-3-training/ (Retrieved: 2026-10-04)

[23] docs.google.com (n.d.). "Palladino Power Project - Running Power Zones 2017". docs.google.com. https://docs.google.com/document/u/1/d/e/2PACX-1vTHqzlWwp2Dp6f1cMlS45PycEf-hCAjy61KXG7fRoR2e4mxDyWH6gXo5ZnIvj5b9cTBWBcj9kcfJHel/pub (Retrieved: 2026-10-04)

[24] eduardbarcelo.com (n.d.). "Stryd user manual. Train and compete for power with Stryd". eduardbarcelo.com. https://www.eduardbarcelo.com/en/stryd-user-manual/ (Retrieved: 2026-10-04)

[25] slideshare.net (n.d.). "Marathon training webinar by Pete Pfitzinger". slideshare.net. https://www.slideshare.net/slideshow/marathon-training-webinar/11191347 (Retrieved: 2026-10-04)

[26] runningwritings.com (n.d.). "Review and summary of Marathon Training - A Scientific Approach by Renato Canova". runningwritings.com. https://runningwritings.com/2023/06/canova-marathon-book.html (Retrieved: 2026-10-04)

[27] pmc.ncbi.nlm.nih.gov (n.d.). "Is There Agreement and Precision between Heart Rate Variability, Ventilatory, and Lactate Thresholds in Healthy Adults?". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC9690603/ (Retrieved: 2026-10-04)

[28] pmc.ncbi.nlm.nih.gov (n.d.). "Steady-state VO2 above MLSS: evidence that critical speed better represents maximal metabolic steady state in well-trained runners". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC8505327/ (Retrieved: 2026-10-04)

[29] pmc.ncbi.nlm.nih.gov (n.d.). "Lactate Thresholds and the Simulation of Human Energy Metabolism: Contributions by the Cologne Sports Medicine Group in the 1970s and 1980s". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC9353623/ (Retrieved: 2026-10-04)

[30] scientifictriathlon.com (n.d.). "Aerobic training and testing with Scott Johnston". scientifictriathlon.com. https://scientifictriathlon.com/tts326/ (Retrieved: 2026-10-04)

[31] pmc.ncbi.nlm.nih.gov (n.d.). "Contextualizing the Norwegian standardized intensity zone framework in an international sample of endurance practitioners". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC12491423/ (Retrieved: 2026-10-04)

[32] azum.com (n.d.). "Part Two: presenting the three zone model". azum.com. https://www.azum.com/en/part-two-presenting-the-three-zone-model/ (Retrieved: 2026-10-04)

[33] uphillathlete.com (n.d.). "Zone 2 Heart Rate Training: Find Your Real Zone". uphillathlete.com. https://uphillathlete.com/aerobic-training/uphill-athlete-training-zones-heart-rate-calculator/ (Retrieved: 2026-10-04)

[34] pmc.ncbi.nlm.nih.gov (n.d.). "Validity of smartwatch-derived estimates of lactate threshold heart rate and pace compared to graded exercise testing". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC12309276/ (Retrieved: 2026-10-04)

[35] themorningshakeout.com (n.d.). "Going Long: An Interview with Pete Pfitzinger". themorningshakeout.com. https://themorningshakeout.com/going-long-an-interview-with-pete-pfitzinger/ (Retrieved: 2026-10-04)

[36] pmc.ncbi.nlm.nih.gov (n.d.). "Physiological and Race Pace Characteristics of Medium and Low-Level Athens Marathon Runners". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC7552741/ (Retrieved: 2026-10-04)

[37] trainingbible.com (n.d.). "Joe Friel's Blog: A Quick Guide to Setting Zones". trainingbible.com. http://www.trainingbible.com/joesblog/2009/11/quick-guide-to-setting-zones.html (Retrieved: 2026-10-04)

[38] joefrieltraining.com (n.d.). "Base 1 Training, Part 4 - Joe Friel". joefrieltraining.com. https://joefrieltraining.com/base-1-training-part-4/ (Retrieved: 2026-10-04)

[39] 8020endurance.com (n.d.). "80/20 Zone Calculator". 8020endurance.com. https://www.8020endurance.com/80-20-zone-calculator/ (Retrieved: 2026-10-04)

[40] 8020endurance.com (n.d.). "If I Only Had Room in My Suitcase, I Would Pack the Ventilatory Threshold". 8020endurance.com. https://www.8020endurance.com/i-would-pack-the-ventilatory-threshold-heres-why/ (Retrieved: 2026-10-04)

[41] run.wxm.be (n.d.). "80/20 Running by Matt Fitzgerald (book notes)". run.wxm.be. https://run.wxm.be/books/80-20-running.html (Retrieved: 2026-10-04)

[42] polarized.cc (n.d.). "Polarized Training: 20 Years of Seiler Science Explained". polarized.cc. https://polarized.cc/the-science-behind-polarized-training-what-20-years-of-seiler-research-actually-shows/ (Retrieved: 2026-10-04)

[43] frontiersin.org (n.d.). "The training intensity distribution among well-trained and elite endurance athletes". frontiersin.org. https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2015.00295/full (Retrieved: 2026-10-04)

[44] olt-skala.nif.no (n.d.). "olt i-scale (Olympiatoppen intensity scale)". olt-skala.nif.no. https://olt-skala.nif.no/en (Retrieved: 2026-10-04)

[45] olt-skala.nif.no (n.d.). "Version 2 (2024) Olympiatoppen's Intensity Scale". olt-skala.nif.no. https://olt-skala.nif.no/olt_2024_en.pdf (Retrieved: 2026-10-04)

[46] run.wxm.be (n.d.). "Faster Road Racing by Pete Pfitzinger (reading notes)". run.wxm.be. https://run.wxm.be/books/faster-road-racing.html (Retrieved: 2026-10-04)

[47] runningwithrock.com (n.d.). "Pfitz Marathon Training Plans Explained: Everything You Need to Know". runningwithrock.com. https://runningwithrock.com/pfitz-marathon-training-explained/ (Retrieved: 2026-10-04)

[48] run.wxm.be (n.d.). "Daniels Running Formula by Jack Daniels - Ward x Muylaert book notes". run.wxm.be. https://run.wxm.be/books/jack-daniels-running-formula.html (Retrieved: 2026-10-04)

[49] bymatthart.com (n.d.). "Book Notes: Training for the Uphill Athlete by Steve House, Scott Johnston, and Kilian Jornet". bymatthart.com. https://bymatthart.com/uphill (Retrieved: 2026-10-04)

[50] uphillathlete.com (n.d.). "DIY Anaerobic Threshold Test". uphillathlete.com. https://uphillathlete.com/aerobic-training/diy-anaerobic-test/ (Retrieved: 2026-10-04)

[51] www8.garmin.com (n.d.). "Forerunner 945 Manual - About Heart Rate Zones". www8.garmin.com. https://www8.garmin.com/manuals-apac/webhelp/forerunner945/EN-SG/GUID-6CC225C9-01C6-4B58-A40D-6B1A80091092-1111.html (Retrieved: 2026-10-04)

[52] support.polar.com (n.d.). "Polar Loop User Manual - Heart Rate Zones". support.polar.com. https://support.polar.com/e_manuals/polar-loop/polar-loop-user-manual-english/heart-rate-zones.htm (Retrieved: 2026-10-04)

[53] garmin.com (n.d.). "How you can train by heart rate zones using Garmin". garmin.com. https://www.garmin.com/en-US/blog/fitness/how-you-can-train-by-heart-rate-zones-using-garmin/ (Retrieved: 2026-10-04)

[54] pmc.ncbi.nlm.nih.gov (n.d.). "Does Lactate-Guided Threshold Interval Training within a High-Volume Low-Intensity Approach Represent the Next Step in the Evolution of Distance Running Training?". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC10000870/ (Retrieved: 2026-10-04)

[55] coachsaltmarsh.com (n.d.). "Norwegian Double-Threshold Method: The Science of LT1 & LT2". coachsaltmarsh.com. https://coachsaltmarsh.com/norwegian-double-threshold-method-lactate/ (Retrieved: 2026-10-04)

[56] the5krunner.com (n.d.). "Stryd 5.0: Everything You Need to Know". the5krunner.com. https://the5krunner.com/2025/10/08/stryd-5-0-everything-you-need-to-know/ (Retrieved: 2026-10-04)

[57] paincave.io (n.d.). "Coggan Power Zones: The 7-Zone Model Explained". paincave.io. https://www.paincave.io/blog/cycling-power-zones (Retrieved: 2026-10-04)

[58] roadmancycling.com (n.d.). "Critical Power and W Explained - Cycling Guide". roadmancycling.com. https://roadmancycling.com/blog/cycling-critical-power-w-prime-guide (Retrieved: 2026-10-04)

[59] teesche.com (n.d.). "Daniels Running Formula by Jack Daniels Review". teesche.com. https://www.teesche.com/bookshelf/jack_daniels_daniels_running_formula (Retrieved: 2026-10-04)

[60] coachsaltmarsh.com (n.d.). "Renato Canova Training Calculator". coachsaltmarsh.com. https://coachsaltmarsh.com/canova-race-pace-calculator/ (Retrieved: 2026-10-04)

[61] runningwritings.com (n.d.). "Modern marathoning with Renato Canova: Analysis of Emile Cairess training before the London Marathon". runningwritings.com. https://runningwritings.com/2024/05/renato-canova-marathon-training-emile-cairess.html (Retrieved: 2026-10-04)

[62] trainright.com (n.d.). "Decoding Interval Workouts for Ultramarathon Training - Jason Koop". trainright.com. https://trainright.com/decoding-interval-workouts-for-ultramarathon-training/ (Retrieved: 2026-10-04)

[63] trainright.com (n.d.). "CTS Key Running Workouts - CTS". trainright.com. https://trainright.com/running-workouts/ (Retrieved: 2026-10-04)

[64] trainright.com (n.d.). "Why Heart Rate Is Not a Good Training Tool for Ultrarunning - CTS". trainright.com. https://trainright.com/heart-rate-not-good-training-tool-ultrarunning/ (Retrieved: 2026-10-04)

[65] scienceofrunning.com (n.d.). "Arthur Lydiard: The Father of Modern Training". scienceofrunning.com. https://www.scienceofrunning.com/2016/11/arthur-lydiard-the-father-of-modern-training.html (Retrieved: 2026-10-04)

[66] runculture.com (n.d.). "Lydiard training method: phases and example weeks". runculture.com. https://runculture.com/learn/running-methods/lydiard/ (Retrieved: 2026-10-04)

[67] championseverywhere.com (n.d.). "Steady state - what it means". championseverywhere.com. https://www.championseverywhere.com/steady-state-what-it-means/ (Retrieved: 2026-10-04)

[68] news.vdoto2.com (n.d.). "What's Your Threshold Pace? - VDOT O2". news.vdoto2.com. https://news.vdoto2.com/2017/12/whats-threshold-pace/ (Retrieved: 2026-10-04)

[69] runningwritings.com (n.d.). "LT1, LT2, and the scientific basis of heart rate zones for runners". runningwritings.com. https://runningwritings.com/2025/02/lt1-lt2-heart-rate-zone-science.html (Retrieved: 2026-10-04)

[70] pmc.ncbi.nlm.nih.gov (n.d.). "Agreement Between Heart Rate Variability-Derived vs. Ventilatory and Lactate Thresholds: A Systematic Review with Meta-Analyses". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC11461412/ (Retrieved: 2026-10-04)

[71] support.polar.com (n.d.). "Polar Heart Rate Zones (Polar Team Pro user manual)". support.polar.com. https://support.polar.com/e_manuals/Team_Pro/Polar_Team_Pro_user_manual_English/polar_heart_rate_zones.htm (Retrieved: 2026-10-04)

[72] 8020endurance.com (n.d.). "Understanding Your 80/20 Run Plan". 8020endurance.com. https://www.8020endurance.com/understanding-your-8020-run-plan/ (Retrieved: 2026-10-04)

[73] forum.8020endurance.com (n.d.). "Zone X Confusion - 80/20 Endurance Forum". forum.8020endurance.com. https://www.forum.8020endurance.com/topic/zone-x-question/ (Retrieved: 2026-10-04)

[74] drwilloconnor.com (n.d.). "Dispelling the Grey Zone Myth". drwilloconnor.com. https://drwilloconnor.com/dispelling-the-grey-zone-myth (Retrieved: 2026-10-04)

[75] marathonhandbook.com (n.d.). "Zone 3 Running: Are You Stuck In The Grey Zone?". marathonhandbook.com. https://marathonhandbook.com/zone-3-training/ (Retrieved: 2026-10-04)

[76] marathonireland.com (n.d.). "Heart Rate Zones: Your Personalized Guide to Marathon Training". marathonireland.com. https://marathonireland.com/blog/heart-rate-zones-marathon/ (Retrieved: 2026-10-04)

[77] running-calculator.com (n.d.). "Running Training Zone Calculator". running-calculator.com. https://running-calculator.com/training-zone-calculator/ (Retrieved: 2026-10-04)

[78] cyclingarchives.com (n.d.). "Heart Rate Zones for Cycling 2026: Complete LTHR Training Guide". cyclingarchives.com. https://cyclingarchives.com/heart-rate-zones-cycling-complete-lthr-training-guide-coggan-friel-2026/ (Retrieved: 2026-10-04)

[79] hillrunner.com (n.d.). "The Basics of Speed Training". hillrunner.com. https://www.hillrunner.com/jim2/id24.html (Retrieved: 2026-10-04)

[80] pubmed.ncbi.nlm.nih.gov (n.d.). "Interval training for performance... Part I: aerobic interval training (Billat)". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/11219499/ (Retrieved: 2026-10-04)

[81] pubmed.ncbi.nlm.nih.gov (n.d.). "Interval training for performance... Part II: anaerobic interval training (Billat)". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/11227980/ (Retrieved: 2026-10-04)

[82] coacheseducation.com (n.d.). "Coaches Education - Setting Up A Season of Training (Jack Daniels)". coacheseducation.com. https://www.coacheseducation.com/endur/jack-daniels-dec-00.php (Retrieved: 2026-10-04)

[83] coachray.nz (n.d.). "Jack Daniels PhD, Formulaic Approach To Periodisation". coachray.nz. https://www.coachray.nz/2021/10/11/jack-daniels-phd-formulaic-approach-to-periodisation/ (Retrieved: 2026-10-04)

[84] runningwithrock.com (n.d.). "My Experience with Phase III of Jack Daniels 5k-10k Plan". runningwithrock.com. https://runningwithrock.com/my-experience-with-phase-iii-of-jack-daniels-5k-10k-plan/ (Retrieved: 2026-10-04)

[85] runningwithrock.com (n.d.). "Training Explained: Jack Daniels Running Formula". runningwithrock.com. https://runningwithrock.com/training-explained-jack-daniels-running-formula/ (Retrieved: 2026-10-04)

[86] joefrieltraining.com (n.d.). "K.I.S.S. Periodization - Joe Friel". joefrieltraining.com. https://joefrieltraining.com/kiss-periodization/ (Retrieved: 2026-10-04)

[87] joefrieltraining.com (n.d.). "Aging: Customizing the Prep Period - Joe Friel". joefrieltraining.com. https://joefrieltraining.com/aging-customizing-the-prep-period/ (Retrieved: 2026-10-04)

[88] trainingbible.com (n.d.). "Joe Friel's Blog: Thoughts on the Base Period". trainingbible.com. http://www.trainingbible.com/joesblog/2009/12/thoughts-on-base-period.html (Retrieved: 2026-10-04)

[89] joefrieltraining.com (n.d.). "Training in Base 2 and 3 - Joe Friel". joefrieltraining.com. https://joefrieltraining.com/training-in-base-2-and-3/ (Retrieved: 2026-10-04)

[90] joefrieltraining.com (n.d.). "Intervals, Part 5 - Joe Friel". joefrieltraining.com. https://joefrieltraining.com/intervals-part-5/ (Retrieved: 2026-10-04)

[91] trainingpeaks.com (n.d.). "The Transition Period | TrainingPeaks (Joe Friel)". trainingpeaks.com. https://www.trainingpeaks.com/blog/the-transition-period/ (Retrieved: 2026-10-04)

[92] pmc.ncbi.nlm.nih.gov (n.d.). "The training intensity distribution among well-trained and elite endurance athletes". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC4621419/ (Retrieved: 2026-10-04)

[93] doi.org (n.d.). "Best-Practice Training Characteristics Within Olympic Endurance Sports as Described by Norwegian World-Class Coaches". doi.org. https://doi.org/10.1186/s40798-025-00848-3 (Retrieved: 2026-10-04)

[94] mariusbakken.com (n.d.). "The Norwegian Model Revisited". mariusbakken.com. https://www.mariusbakken.com/norwegian-model-revisited.html (Retrieved: 2026-10-04)

[95] sjsp.aearedo.es (n.d.). "The Norwegian double-threshold method in distance running: Systematic literature review (Kelemen, Benczenleitner, Toth)". sjsp.aearedo.es. https://sjsp.aearedo.es/index.php/sjsp/article/download/norwegian-double-threshold-method-distance-running/92/1771 (Retrieved: 2026-10-04)

[96] athleticsillustrated.com (n.d.). "The basics of training the Arthur Lydiard way: phases of the pyramid". athleticsillustrated.com. https://athleticsillustrated.com/the-basics-of-training-the-arthur-lydiard-way-phases-of-the-pyramid/ (Retrieved: 2026-10-04)

[97] en.wikipedia.org (n.d.). "Arthur Lydiard - Wikipedia". en.wikipedia.org. https://en.wikipedia.org/wiki/Arthur_Lydiard (Retrieved: 2026-10-04)

[98] coachtherun.com (n.d.). "What is the Lydiard Method? The complete guide to Arthur Lydiard’s revolutionary training system". coachtherun.com. https://coachtherun.com/running-training/technique/what-is-the-lydiard-method/ (Retrieved: 2026-10-04)

[99] articles.sweatelite.co (n.d.). "Arthur Lydiard Method Summarised - Fundamentals (Part 1)". articles.sweatelite.co. https://articles.sweatelite.co/fundamentals-lydiard-method-part-1-5/ (Retrieved: 2026-10-04)

[100] runningwritings.com (n.d.). "The Keys to Marathon Training: Modern changes to Renato Canova elite marathon training methods". runningwritings.com. https://runningwritings.com/2023/07/renato-canova-marathon-training-lecture.html (Retrieved: 2026-10-04)

[101] articles.sweatelite.co (n.d.). "Renato Canova - Training Philosophy Summarised (Part 1)". articles.sweatelite.co. https://articles.sweatelite.co/renato-canova-philosophy-part-1/ (Retrieved: 2026-10-04)

[102] runnersconnect.net (n.d.). "Canova Special Block Training: Marathon-Specific Workouts That Cut 2-3 Minutes". runnersconnect.net. https://runnersconnect.net/special-block-training/ (Retrieved: 2026-10-04)

[103] runningwithrock.com (n.d.). "Pfitzinger Marathon Plan: Pros and Cons of Pete's Approach". runningwithrock.com. https://runningwithrock.com/pfitzinger-marathon-plan/ (Retrieved: 2026-10-04)

[104] runningwithrock.com (n.d.). "Pfitzinger Half Marathon Plan: Pros and Cons of Pete's Training". runningwithrock.com. https://runningwithrock.com/pete-pfitzinger-half-marathon-plans/ (Retrieved: 2026-10-04)

[105] buenavida.run (n.d.). "Advanced Marathoning 18-Week, 70 to 85 mi/wk Review". buenavida.run. https://buenavida.run/plans/pfitz-am-18wk-70-85/ (Retrieved: 2026-10-04)

[106] rulit.me (n.d.). "Advanced Marathoning - Pete Pfitzinger (online text, p.47)". rulit.me. https://www.rulit.me/books/advanced-marathoning-read-299207-47.html (Retrieved: 2026-10-04)

[107] runningahead.com (n.d.). "RunningAHEAD - BOOK: Brad Hudson -- Run Faster From the 5K to the Marathon". runningahead.com. https://www.runningahead.com/groups/mtp/forum/c710a5382e7e4ba78e72d945ea5eb4ae (Retrieved: 2026-10-04)

[108] runningahead.com (n.d.). "RunningAHEAD - Brad Hudson -- Run Faster From the 5K to the Marathon (Thread)". runningahead.com. https://www.runningahead.com/groups/mtp/forum/620a8bc279864a3098612b9956251e14 (Retrieved: 2026-10-04)

[109] uphillathlete.com (n.d.). "How to Build a Transition Period for Tactical Athletes". uphillathlete.com. https://uphillathlete.com/tactical-training/transition-period-training-tactical/ (Retrieved: 2026-10-04)

[110] uphillathlete.com (n.d.). "Training for Mountaineering: Base Training". uphillathlete.com. https://uphillathlete.com/podcast/training-for-mountaineering-base-training/ (Retrieved: 2026-10-04)

[111] uphillathlete.com (n.d.). "Muscular Endurance Training for Mountain Athletes: What It Is, Why It Matters, and How to Do It". uphillathlete.com. https://uphillathlete.com/aerobic-training/vertical-beast-mode-what-is-muscular-endurance-why-it-is-important-for-any-alpinist-or-mountaineer-and-how-do-you-train-it/ (Retrieved: 2026-10-04)

[112] uphillathlete.com (n.d.). "How Should You Taper Before a Race or Big Objective? | Uphill Athlete". uphillathlete.com. https://www.uphillathlete.com/aerobic-training/tapering/ (Retrieved: 2026-10-04)

[113] trainright.com (n.d.). "How Speedwork Improves Ultrarunning Performance - CTS". trainright.com. https://trainright.com/how-speedwork-improves-ultrarunning-performance/ (Retrieved: 2026-10-04)

[114] trainright.com (n.d.). "3 Steps for Creating Your Ultrarunning Long-Range Plan - CTS". trainright.com. https://trainright.com/ultrarunning-long-range-plan-3-steps/ (Retrieved: 2026-10-04)

[115] trainright.com (n.d.). "Which is Better: Block or Mixed-Intensity Periodization for Ultrarunners? - Jason Koop". trainright.com. https://trainright.com/block-mixed-intensity-periodization-ultrarunning/ (Retrieved: 2026-10-04)

[116] trainright.com (n.d.). "How Much Do You Need To Train For A 100-Mile Ultramarathon? - CTS". trainright.com. https://trainright.com/ultramarathon-training-time-required/ (Retrieved: 2026-10-04)

[117] trainright.com (n.d.). "The Hierarchy of Ultramarathon Training Needs - Jason Koop, CTS". trainright.com. https://trainright.com/hierarchy-ultramarathon-training-needs-jason-koop/ (Retrieved: 2026-10-04)

[118] elizabethmyers.substack.com (n.d.). "Periodization Training in Ultrarunning: A Smarter Path to Peak Performance". elizabethmyers.substack.com. https://elizabethmyers.substack.com/p/periodization-training-in-ultrarunning (Retrieved: 2026-10-04)

[119] scientifictriathlon.com (n.d.). "David Roche - The training and racing strategy behind his epic Leadville 100 course record". scientifictriathlon.com. https://scientifictriathlon.com/tts444/ (Retrieved: 2026-10-04)

[120] stories.strava.com (n.d.). "David Roche: The Journey to the Leadville 100 Course Record". stories.strava.com. https://stories.strava.com/articles/david-roche-the-journey-to-the-leadville-100-course-record (Retrieved: 2026-10-04)

[121] uphillathlete.com (n.d.). "Exercises for Muscular Endurance". uphillathlete.com. https://uphillathlete.com/aerobic-training/exercises-for-muscular-endurance/ (Retrieved: 2026-10-04)

[122] pubmed.ncbi.nlm.nih.gov (n.d.). "Effects of tapering on performance: a meta-analysis (Bosquet et al., MSSE 2007)". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/17762369/ (Retrieved: 2026-10-04)

[123] pubmed.ncbi.nlm.nih.gov (n.d.). "Scientific bases for precompetition tapering strategies (Mujika & Padilla, MSSE 2003)". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/12840640/ (Retrieved: 2026-10-04)

[124] en.wikipedia.org (n.d.). "Tapering (sports) - Wikipedia". en.wikipedia.org. https://en.wikipedia.org/wiki/Tapering_(sports) (Retrieved: 2026-10-04)

[125] frontiersin.org (n.d.). "Training Intensity Distribution: definitions of polarized, pyramidal, threshold TID (Frontiers in Physiology 2019)". frontiersin.org. https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2019.00707/full (Retrieved: 2026-10-04)

[126] pheidi.training (n.d.). "Polarized vs Pyramidal Training: Which Intensity Distribution Wins". pheidi.training. https://pheidi.training/articles/polarized-vs-pyramidal-training/ (Retrieved: 2026-10-04)

[127] roadmancycling.com (n.d.). "Polarised Training for Cyclists: The Seiler Guide (2026)". roadmancycling.com. https://roadmancycling.com/blog/polarised-training-cycling-guide (Retrieved: 2026-10-04)

[128] highnorth.co.uk (n.d.). "Polarised Cycling Training: A Detailed Guide". highnorth.co.uk. https://www.highnorth.co.uk/articles/polarised-training-cycling (Retrieved: 2026-10-04)

[129] pubmed.ncbi.nlm.nih.gov (n.d.). "Training Periodization, Methods, Intensity Distribution, and Volume in Highly Trained and Elite Distance Runners: A Systematic Review (Casado et al.)". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/35418513/ (Retrieved: 2026-10-04)

[130] doi.org (n.d.). "The Training Intensity Distribution of Marathon Runners Across Performance Levels (Muniz-Pumares et al., Sports Med)". doi.org. https://doi.org/10.1007/s40279-024-02137-7 (Retrieved: 2026-10-04)

[131] doi.org (n.d.). "Training Intensity Distribution, Load Management, and Performance in Recreational Long-Distance Runners (Bonato ... Filipas, IJSPP)". doi.org. https://doi.org/10.1123/ijspp.2025-0348 (Retrieved: 2026-10-04)

[132] doi.org (n.d.). "Quantitative Analysis of 92 12-Week Sub-elite Marathon Training Plans (Knopp ... Seiler, Wackerhage, Sports Med Open)". doi.org. https://doi.org/10.1186/s40798-024-00717-5 (Retrieved: 2026-10-04)

[133] frontiersin.org (n.d.). "Polarized training has greater impact on key endurance variables than threshold, high intensity, or high volume training (Stöggl & Sperlich 2014)". frontiersin.org. https://www.frontiersin.org/articles/10.3389/fphys.2014.00033/full (Retrieved: 2026-10-04)

[134] pubmed.ncbi.nlm.nih.gov (n.d.). "Polarized vs. Threshold Training Intensity Distribution on Endurance Sport Performance: Systematic Review and Meta-Analysis of RCTs (Rosenblat et al., JSCR 2019)". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/29863593/ (Retrieved: 2026-10-04)

[135] doi.org (n.d.). "Comparison of Polarized Versus Other Types of Endurance Training Intensity Distribution on Athletes Endurance Performance: A Systematic Review with Meta-analysis (Silva Oliveira et al., Sports Med)". doi.org. https://doi.org/10.1007/s40279-024-02034-z (Retrieved: 2026-10-04)

[136] doi.org (n.d.). "Which Training Intensity Distribution Intervention will Produce the Greatest Improvements in VO2max and Time-Trial Performance in Endurance Athletes? A Systematic Review and Network Meta-analysis of Individual Participant Data (Rosenblat et al., Sports Med)". doi.org. https://doi.org/10.1007/s40279-024-02149-3 (Retrieved: 2026-10-04)

[137] doi.org (n.d.). "Effects of Different Training-Intensity Distribution Models on VO2max and Time-Trial Performance in Endurance Athletes: A Bayesian Network Meta-Analysis (Li, Yang, Wang, JSCR)". doi.org. https://doi.org/10.1519/jsc.0000000000005415 (Retrieved: 2026-10-04)

[138] doi.org (n.d.). "Effects of Polarized Training vs. Other TID Models on Physiological Variables and Endurance Performance in Different-Level Endurance Athletes: A Scoping Review (Rivera-Köfler et al., JSCR)". doi.org. https://doi.org/10.1519/jsc.0000000000005033 (Retrieved: 2026-10-04)

[139] pmc.ncbi.nlm.nih.gov (n.d.). "Effects of 16 weeks of pyramidal and polarized training intensity distributions in well-trained endurance runners (Filipas et al., SJMSS)". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC9299127/ (Retrieved: 2026-10-04)

[140] pubmed.ncbi.nlm.nih.gov (n.d.). "Does polarized training improve performance in recreational runners? (Muñoz, Seiler, ... Esteve-Lanao, IJSPP 2014)". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/23752040/ (Retrieved: 2026-10-04)

[141] pubmed.ncbi.nlm.nih.gov (n.d.). "Impact of training intensity distribution on performance in endurance athletes (Esteve-Lanao, Foster, Seiler, Lucia, JSCR 2007)". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/17685689/ (Retrieved: 2026-10-04)

[142] pubmed.ncbi.nlm.nih.gov (n.d.). "Training-intensity Distribution on Middle- and Long-distance Runners: A Systematic Review (Campos, Casado et al., Int J Sports Med 2022)". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/34749417/ (Retrieved: 2026-10-04)

[143] shashishanbhag.com (n.d.). "Uphill Athlete 24-Week Mountaineering Training Plan". shashishanbhag.com. https://shashishanbhag.com/train/uphill-athlete-24-week-mountaineering-training-plan/ (Retrieved: 2026-10-04)

[144] doi.org (n.d.). "Running Power Analysis of a World Champion: Spanish Vertical, European Ultra, and World Trail Championships (Rodríguez-Medina & Rodríguez-Marroyo, IJSPP)". doi.org. https://doi.org/10.1123/ijspp.2025-0561 (Retrieved: 2026-10-04)

[145] bornonthetrail.substack.com (n.d.). "The Holidays are Over. Time to Get Back to Training". bornonthetrail.substack.com. https://bornonthetrail.substack.com/p/the-holidays-are-over-time-to-get-training (Retrieved: 2026-10-04)

[146] frontiersin.org (n.d.). "Training variability and threshold density: a conceptual comparison of East African and Norwegian endurance training systems". frontiersin.org. https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2026.1878492/full (Retrieved: 2026-10-04)

[147] pubmed.ncbi.nlm.nih.gov (n.d.). "High-intensity interval training, solutions to the programming puzzle: Part I: cardiopulmonary emphasis (Buchheit & Laursen)". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/23539308/ (Retrieved: 2026-10-04)

[148] doi.org (n.d.). "Comparison of high-intensity interval training protocol designs on accumulated time ≥ 90% VO2max: a network meta-analysis". doi.org. https://doi.org/10.1186/s13102-026-01891-7 (Retrieved: 2026-10-04)

[149] pmc.ncbi.nlm.nih.gov (n.d.). "Faster intervals, faster recoveries - intensified short VO2max running intervals are inferior to traditional long intervals in terms of time spent above 90% VO2max". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC11743937/ (Retrieved: 2026-10-04)

[150] pmc.ncbi.nlm.nih.gov (n.d.). "Comparison of high-intensity interval training protocol designs on accumulated time >= 90% VO2max: a network meta-analysis". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC13390209/ (Retrieved: 2026-10-04)

[151] aixsurge.com (n.d.). "Short vs Long VO2max intervals for triathletes: duration matters". aixsurge.com. https://www.aixsurge.com/blog/long-vs-short-vo2max-intervals (Retrieved: 2026-10-04)

[152] doi.org (n.d.). "Block Training With Moderate- or High-Intensity Intervals Both Improve Endurance Performance in Well-Trained Cyclists". doi.org. https://doi.org/10.1002/ejsc.70067 (Retrieved: 2026-10-04)

[153] pmc.ncbi.nlm.nih.gov (n.d.). "Block Training With Moderate- or High-Intensity Intervals Both Improve Endurance Performance in Well-Trained Cyclists". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC12575440/ (Retrieved: 2026-10-04)

[154] pubmed.ncbi.nlm.nih.gov (n.d.). "Adaptations to aerobic interval training: interactive effects of exercise intensity and total work duration (Seiler et al., Scand J Med Sci Sports)". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/21812820/ (Retrieved: 2026-10-04)

[155] sparksinto.life (n.d.). "Understanding W-Prime: Anaerobic Work Capacity Explained". sparksinto.life. https://www.sparksinto.life/post/what-is-w-w-prime-fka-anaerobic-work-capacity (Retrieved: 2026-10-04)

[156] trainright.com (n.d.). "Decoding Interval Workouts for Ultramarathon Training - Jason Koop". trainright.com. https://trainright.com/decoding-ultramarathon-interval-workouts/ (Retrieved: 2026-10-04)

[157] shuichi-running.com (n.d.). "Jakob Ingebrigtsen Training: Norwegian Double Threshold". shuichi-running.com. https://shuichi-running.com/en/ingebrigtsen-training/ (Retrieved: 2026-10-04)

[158] o2trening.substack.com (n.d.). "The Norwegian Method for recreational athletes?". o2trening.substack.com. https://o2trening.substack.com/p/the-norwegian-method-for-recreational (Retrieved: 2026-10-04)

[159] itwriting.com (n.d.). "Book review: Advanced Marathoning (4th edition)". itwriting.com. https://www.itwriting.com/blog/12332-book-review-advanced-marathoning-by-pete-pfitzinger-and-scott-douglas-4th-edition.html (Retrieved: 2026-10-04)

[160] rulit.me (n.d.). "Advanced Marathoning - Pete Pfitzinger - RuLit page 10". rulit.me. https://www.rulit.me/books/advanced-marathoning-read-299207-10.html (Retrieved: 2026-10-04)

[161] pubmed.ncbi.nlm.nih.gov (n.d.). "Aerobic high-intensity intervals improve VO2max more than moderate training (Helgerud et al., MSSE)". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/17414804/ (Retrieved: 2026-10-04)

[162] uphillathlete.com (n.d.). "What Is 30/30 Interval Training and How Does It Build Speed?". uphillathlete.com. https://uphillathlete.com/trail-running/30-30s-interval-training-running/ (Retrieved: 2026-10-04)

[163] doi.org (n.d.). "Impact of weekly frequency of high-intensity interval training on cardiorespiratory, metabolic, and performance measures in recreational runners - An exploratory study". doi.org. https://doi.org/10.14814/phy2.70573 (Retrieved: 2026-10-04)

[164] runningwithrock.com (n.d.). "What's New In the 4th Edition of Pete Pfitzinger's Advanced Marathoning?". runningwithrock.com. https://runningwithrock.com/pfitz-4th-differences/ (Retrieved: 2026-10-04)

[165] trainright.com (n.d.). "Which Comes First: Hardest or Easiest Workouts? - Jason Koop". trainright.com. https://trainright.com/do-hardest-workouts-first/ (Retrieved: 2026-10-04)

[166] uphillathlete.com (n.d.). "Why is my Zone 2 so slow? (Aerobic Deficiency)". uphillathlete.com. https://uphillathlete.com/aerobic-training/aerobic-deficiency-syndrome/ (Retrieved: 2026-10-04)

[167] trainingpeaks.com (n.d.). "Aerobic Endurance and Decoupling - TrainingPeaks (Joe Friel)". trainingpeaks.com. https://www.trainingpeaks.com/blog/aerobic-endurance-and-decoupling/ (Retrieved: 2026-10-04)

[168] lydiardfoundation.org (n.d.). "Lydiard Foundation". lydiardfoundation.org. https://www.lydiardfoundation.org/ (Retrieved: 2026-10-04)

[169] trainright.com (n.d.). "Top 4 Reasons Ultrarunners Should Do More Intervals Uphill - CTS". trainright.com. https://trainright.com/benefits-of-uphill-intervals-ultrarunners/ (Retrieved: 2026-10-04)

[170] trainright.com (n.d.). "Top 4 Reasons Ultrarunners Should Do More Uphill Running Intervals - CTS". trainright.com. https://trainright.com/benefits-of-uphill-running-intervals-ultrarunners/ (Retrieved: 2026-10-04)

[171] trainright.com (n.d.). "How CTS Coaches Prepare Athletes for UTMB - CTS". trainright.com. https://trainright.com/how-cts-coaches-prepare-athletes-for-utmb/ (Retrieved: 2026-10-04)

[172] trainright.com (n.d.). "Tracking Training for Trail and Ultramarathon Runners - CTS". trainright.com. https://trainright.com/tracking-training-trail-ultramarathon-runners/ (Retrieved: 2026-10-04)

[173] alpfitness.com (n.d.). "Guide to Using Training Zones - Alp Fitness". alpfitness.com. https://alpfitness.com/knowledge-base/guide-to-using-training-zones/ (Retrieved: 2026-10-04)

[174] en.wikipedia.org (n.d.). "Long slow distance - Wikipedia". en.wikipedia.org. https://en.wikipedia.org/wiki/Long_slow_distance (Retrieved: 2026-10-04)

[175] lydiardfoundation.org (n.d.). "Metabolic Drivers of the Lydiard Phase: Session 2 - The Aerobic Base". lydiardfoundation.org. https://www.lydiardfoundation.org/lydiard-iii-store/p/metabolic-drivers-of-the-lydiard-phases-10-credit-hours-rs22j (Retrieved: 2026-10-04)

[176] pmc.ncbi.nlm.nih.gov (n.d.). "Durability of Parameters Associated With Endurance Running in Marathoners". pmc.ncbi.nlm.nih.gov. https://pmc.ncbi.nlm.nih.gov/articles/PMC12547624/ (Retrieved: 2026-10-04)

[177] vert.run (n.d.). "Back-to-Back Long Runs for Ultra Training: A Practical Guide". vert.run. https://vert.run/whats-back-to-back-training/ (Retrieved: 2026-10-04)

[178] trainingpeaks.com (n.d.). "How to Train for an Ultramarathon - TrainingPeaks". trainingpeaks.com. https://www.trainingpeaks.com/guides/ultra-marathon-training/ (Retrieved: 2026-10-04)

[179] trainright.com (n.d.). "Stop Wasting Miles: Key Workouts Every Ultrarunner Should Do - Jason Koop". trainright.com. https://trainright.com/key-workouts-every-ultrarunner-should-do/ (Retrieved: 2026-10-04)

[180] pezcyclingnews.com (n.d.). "High Intensity Interval Training and Time at VO2max". pezcyclingnews.com. https://pezcyclingnews.com/toolbox/high-intensity-interval-training-and-time-at-vo2max/ (Retrieved: 2026-10-04)

[181] marktosques.com (n.d.). "Jack Daniels Phase II - Mark Tosques". marktosques.com. https://marktosques.com/running/jack-daniels-phase-ii/ (Retrieved: 2026-10-04)

[182] denstarfitness.com (n.d.). "Jack Daniels Running Formula: VDOT and the 5 Training Paces Explained". denstarfitness.com. https://denstarfitness.com/jack-daniels-running-formula/ (Retrieved: 2026-10-04)

[183] runningmagazine.ca (n.d.). "3 workouts from record-breaking trail runner (and coach) David Roche - Canadian Running Magazine". runningmagazine.ca. https://runningmagazine.ca/trail-running/3-workouts-from-record-breaking-trail-runner-and-coach-david-roche/ (Retrieved: 2026-10-04)

[184] marktosques.com (n.d.). "Lydiard Hills - Mark Tosques". marktosques.com. https://marktosques.com/running/lydiard-hills/ (Retrieved: 2026-10-04)

[185] uphillathlete.com (n.d.). "Understanding the Heart Rate Drift Test: A Practical Guide for Endurance Athletes". uphillathlete.com. https://uphillathlete.com/aerobic-training/heart-rate-drift/ (Retrieved: 2026-10-04)

[186] pubmed.ncbi.nlm.nih.gov (n.d.). "Training variability and threshold density: a conceptual comparison of East African and Norwegian endurance training systems". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/42389749/ (Retrieved: 2026-10-04)

[187] the5krunner.com (n.d.). "Karvonen versus %Max HR: which HR zone method should you use?". the5krunner.com. https://the5krunner.com/2026/07/31/karvonen-versus-max-hr-which-hr-zone-method-should-you-use/ (Retrieved: 2026-10-04)

[188] the5krunner.com (n.d.). "Heart Rate Training Zones Explained: LTHR, Thresholds and What Breaks". the5krunner.com. https://the5krunner.com/sports-science/training-zones/ (Retrieved: 2026-10-04)

[189] zarkus.app (n.d.). "Jack Daniels Running Formula Explained: The Science Behind Training Paces". zarkus.app. https://zarkus.app/en/blog/jack-daniels-running-formula-explained (Retrieved: 2026-10-04)

[190] joefrieltraining.com (n.d.). "Determining your LTHR - Joe Friel". joefrieltraining.com. https://joefrieltraining.com/determining-your-lthr/ (Retrieved: 2026-10-04)

[191] trainingzones.app (n.d.). "Friel Heart-Rate Zones: 5, 7 and Platform Numbering". trainingzones.app. https://trainingzones.app/blog/friel-heart-rate-zones (Retrieved: 2026-10-04)

[192] trainingbible.com (n.d.). "Joe Friel's Blog: Build Period Specificity". trainingbible.com. http://www.trainingbible.com/joesblog/2009/06/build-period-specificity.html (Retrieved: 2026-10-04)

[193] frontiersin.org (n.d.). "Relationship Between the Critical Power Test and a 20-min Functional Threshold Power Test in Cycling". frontiersin.org. https://www.frontiersin.org/journals/physiology/articles/10.3389/fphys.2020.613151/full (Retrieved: 2026-10-04)

[194] pubmed.ncbi.nlm.nih.gov (n.d.). "Analysis of the factors influencing the proximity and agreement between critical power and maximal lactate steady state: a systematic review and meta-analyses". pubmed.ncbi.nlm.nih.gov. https://pubmed.ncbi.nlm.nih.gov/40124604/ (Retrieved: 2026-10-04)

[195] forum.8020endurance.com (n.d.). "How to apply Stryd Critical Power to 80/20 Zones? (80/20 Endurance forum)". forum.8020endurance.com. https://www.forum.8020endurance.com/topic/how-to-apply-stryds-critical-power-to-80-20-zones/ (Retrieved: 2026-10-04)

[196] marathonexcellence.com (n.d.). "Marathon Excellence for Everyone - The Modern Approach to Training and Racing". marathonexcellence.com. https://marathonexcellence.com/ (Retrieved: 2026-10-04)


## Methodology Appendix（方法附錄）

### 研究流程

本研究依 deep-research 技能的 deep 模式執行。Phase 1–2 將使用者的六個問題拆成 28 個檢索子題（Q1–Q28，每題 1–2 個角度）與 4 個 app 端子題（Q29–Q32）；Phase 2.5 經使用者確認範圍。Phase 3 以平行 subagent 檢索，所有來源與引文即時寫入來源登錄檔與證據檔（含檢索查詢與頁面定位），去重後得 342 個來源、1,253 條證據。App 端另做唯讀程式碼與文件分析，內容只摘要進本報告，使用者個人數值一律不寫入。Phase 4 將每個核心主張對到 ≥ 2 個獨立來源（教練原站＋第三方，或兩篇論文），並把來源間的數字衝突（Daniels 版次、Friel 修訂版、80/20 初版、CP vs MLSS）明列；Phase 4.5 依證據調整大綱——原計畫的「各學派逐一介紹」改為以對齊總表與 app 缺口為主軸，並新增「心率與功率對 LT2 不對稱」與「階梯終點應由目標賽決定」兩個由證據浮現的主題。Phase 5–7 撰寫、自我審查（以「懷疑的實務教練」「同儕審查者」「實作工程師」三個角度各看一遍），補查 1 次（Friel 2025 修訂版，表格為圖片，無法驗證）。Phase 8 產出本文件並執行結構與引用驗證。

### 來源組成

342 個來源中，同儕審查論文與統合分析約 60 篇（PMC、PubMed、Frontiers、Springer、Human Kinetics、doi.org），教練本人網站約 70 篇（Friel、Uphill Athlete、CTS／Koop、80/20 Endurance、VDOT O2、Marius Bakken、Pfitzinger Coaching、Lydiard Foundation），平台官方文件約 15 篇（Garmin、Polar、COROS、Stryd、Olympiatoppen），其餘為二手整理（書評、讀書筆記、論壇、新聞）。年代從 Lydiard 與 Daniels 的經典到 2026 年的統合分析；地理上涵蓋美國、挪威、義大利（Canova）、紐西蘭（Lydiard）、西班牙與台灣（筆記與 app 規則來源）。本報告實際引用的來源列於 Bibliography；未引用者多為重複或只提供背景的二手整理。可信度沒有用自動評分腳本計分；取捨原則是：生理數字優先引同儕審查，教練規則優先引教練本人網站，二手整理只在原站無法取得時使用，並在文中標示。

### 驗證方式

核心主張要求至少兩個獨立來源；只有單一來源的主張（例如 Olympiatoppen 的乳酸範圍、Roche 的訓練方式、世界冠軍的功率分析）在文中以「該來源指出」的方式陳述。所有換算（%HRmax→%LTHR、%HRR→%LTHR、比賽配速→LT2）都標明假設與誤差範圍。App 端的每條規則都以程式碼行號為準，並在 cite-lint 檢查錨點存在與行號範圍。

### Claims-Evidence Table

| Claim ID | 主要主張 | 證據類型 | 支持來源 | 信心 |
|---|---|---|---|---|
| C1 | 以閾值錨點開強度比 %HRmax／%HRR 一致；LT1 與 LT2 的 %HRmax 個人差很大 | 同儕審查（觀察＋實驗） | [3][4][19][5] | 高 |
| C2 | CP 平均比 MLSS 高約 7%（4–16%），各閾值不可互換 | 同儕審查、系統性回顧 | [1][2][28][194] | 高 |
| C3 | 估 LT2 的誤差小於估 LT1；估計最大值會放大誤差 | 同儕審查 | [4][27] | 高 |
| C4 | Friel、80/20、COROS 的 %LTHR 區界如表 2a | 教練原站＋平台文件 | [16][37][20][39][21] | 高 |
| C5 | Daniels T／I／R 的定義、每趟長度與週量上限 | 教練（二手摘要，多方一致） | [6][7][48][59][79] | 中 |
| C6 | Daniels Phase I–IV 的課表重點 | 教練（原文摘錄＋二手） | [82][83][9][84] | 中 |
| C7 | UA 的 ADS 10% 規則、Zone 3 加法與工休比 | 教練原站 | [14][166][30] | 中（無同儕審查） |
| C8 | 菁英耐力選手多為金字塔分配，賽前轉極化 | 同儕審查回顧 | [92][129][130] | 高 |
| C9 | 休閒者極化不優於金字塔；極化對 VO2peak 的優勢小且有條件 | 統合分析 | [136][135][137] | 高 |
| C10 | 先金字塔後極化效果最好 | 單一 RCT | [139] | 中 |
| C11 | 中強度區塊較能提升乳酸閾值功率、高強度區塊較能提升衝刺 | 同儕審查 | [152][153] | 中 |
| C12 | 3 分鐘 VO2max 間歇的 > 90% VO2max 時間多於 30 秒；但整體設計間無明顯贏家 | 同儕審查、統合分析 | [149][150][148] | 中 |
| C13 | 減量：2 週、量減 41–60%、維持強度 | 統合分析 | [122][123] | 高 |
| C14 | 各學派都在專項期保留閾值課（與 app 的缺口 1 相反） | 教練原站＋二手 | [9][103][113][12][13] | 中 |
| C15 | 把 VO2max 間歇稱為「有氧間歇訓練」 | 同儕審查回顧＋教練 | [80][81][6] | 高 |
| C16 | 90 分鐘飄移測試不產生 AeT；UA 飄移測試以起始心率定 AeT | 教練原站 | [185][167] | 中 |
| C17 | 預設用 %LTHR，HRR 需一致性檢查 | 綜合推論 | [3][4][21][187] | 中 |

**信心等級**：高＝≥ 3 個獨立來源且一致或有統合分析；中＝2 個來源，或單一高品質來源，或來源間有版次差異；低＝單一來源或推估。

### Report Metadata

**Research Mode:** Deep · **Sources in registry:** 342 · **Evidence quotes:** 1,253 · **Generated:** 2026-10-04 · **Validation:** 見 run 紀錄（validate_report.py、verify_citations.py、cite-lint）
