# 跑者進步速度：一年能進步多少，跨一個賽事等級要多久（SP-203）

> 調查日期：2026-10-06。只做調查，沒有改程式。基準 commit `47c5a6e`。
> 接續 `race-feasibility.md` §2.1–§2.4、§3（跨級檢查的來源：UTMB 報名規則、Corrion 2018、Maleka 2026）與 `competitor-charts.md`、`effort-distance-formulas.md` §1（ITRA 分級），已有的不重查。
> 標記：**已驗證（全文）**／**已驗證（摘要）**／**搜尋摘要**（沒讀到原頁）／**教練級**／**推估**／**未找到來源**。本文編號 [P1]、[P2]…。

## 摘要

1. **沒有找到「ITRA 指數一年進步多少」或「從 S 級到 M、L、XL 平均要多久」的資料。** ITRA 和 UTMB 都沒有公布，也沒有研究用它們的資料算過。坊間「新手一年進步 10–15 %、中階 5–8 %、老手 2–4 %」的說法，唯一附的出處編號查下去是不相關的文章，**查無出處、不採用**。
2. **找得到的是「經驗」的資料，不是「速度」的。**
   - 超馬跑者第一次跑超馬前，規律跑步的年數中位數是 **7 年**（四分位 3–15），但有 25 % 的人只跑了 3 年以下 [P1]。
   - 跑過 4 場以上馬拉松的人，比 3 場以下的快約 36 分鐘，跑齡 9.7 年對 4.1 年 [P2]（橫斷面，不是同一群人的進步）。
   - 在 100 km、24 小時、多日超馬，「跑了幾年」和成績**沒有關係**；完賽過幾場的關係只在少數研究出現 [P3]。
3. **「24 個月」和「高兩級」有業界規則可以對上，不再只是推估。**
   - UTMB 指數要「近 24 個月內完成過該組別的比賽」才有效 [P5]；Hardrock 的資格賽也要在 2 年內 [P7]。
   - UTMB 總決賽的報名資格：OCC（50K）要 20K 以上的有效指數、CCC（100K）要 50K 以上、UTMB（100M）要 100K 以上 [P6]。**也就是最多往上跳一級**。
4. **發現一個問題：ITRA 分級的寬度不平均，「高兩級」代表的實際倍數差很多。**
   - 最大單日 EP 44（XS）、目標 75（M）：只差 1.7 倍，判「超出」。
   - 最大單日 EP 45（S）、目標 114（M）：差 2.5 倍，判「可以」。
   - 最大單日 EP 115（L）、目標 210（XXL）：差 1.8 倍，判「超出」；EP 155（XL）到 400：2.6 倍，判「可以」。
5. **建議（§5）**：保留「24 個月」和「高兩級」，把來源換成 UTMB／Hardrock 規則（P3）；加一個「EP 倍數」的輔助說明，讓分級邊界附近的人看得懂（P3）；「多久後可以挑戰」不給年數，改給**里程碑**：先完成哪一級、週量要到多少（P3）。不建議用年進步率做任何判斷。

## 1. app 現在怎麼做

| 項目 | 現況 | 位置 |
|---|---|---|
| 跨級檢查 | 過去 24 個月**所有用腳的活動**裡，單日 EP 最大的那天 → ITRA 分級；對照比賽最難那天的分級。高 ≥ 2 級判「太難了」，否則「可以」 | `backend/engine/race_feasibility.py:89–92`、`:488–506` |
| 單日最大 EP | 同一天的活動加總；訓練的長天也算，不限比賽 | `race_feasibility.py:245–255`、`:747` |
| 分級表 | XXS 0、XS 25、S 45、M 75、L 115、XL 155、XXL 210 | `race_feasibility.py:92`、`:240–242` |
| EP 除數 | 跨級檢查用 `divisor()`，有個人化除數時不是 100（ITRA 是 100） | `race_feasibility.py:491`；`planning.py:87` |
| 超出時的建議 | 「先跑一場低一級的比賽，或把這場改成 B／C 賽」 | `race_feasibility.py:504–506` |
| 來源文字 | 「高兩級」和「24 個月」是推估 | `race_feasibility.py:102–103` |
| 賽事大小（另一套） | 依預估時間（2／4／6／20 小時）或 EP（21／42／60／160）分五級 | `backend/engine/planning.py:81–86`、`:303–316` |
| 「多久後可以挑戰」 | **沒有**。可行性只對已經建立的比賽給判斷 | — |

另外有兩點要注意：

- 個人化除數（例如 153）讓 EP 變小，比賽和自己的最大單日都用同一個除數，所以**相對比較不受影響**；但分級邊界是照 ITRA 的除數 100 訂的，個人化除數會讓大家都往下掉級。
- 賽事大小（`planning.SIZES`）和 ITRA 分級是兩套不同的切法，前者用在恢復天數和減量長度，後者只用在跨級。

## 2. 文獻與規則

### 2.1 ITRA 與 UTMB 的指數

| 來源 | 說法 | 標記 |
|---|---|---|
| ITRA 指數說明（run-motion 轉載）[P4] | 每場成績對「理論最佳」給 0–1000 分；指數 = 近 36 個月最好 5 場的加權平均，越近、越好的權重越大；不跑比賽指數會往下掉。各分級的最佳成績：XS 男 908、S 913、M 943 | 已驗證（全文） |
| ITRA 等級（搜尋摘要） | 菁英門檻：男 > 825、女 > 700；另有 900+ 世界級、800–899 菁英、700–799、600–699、500–599、400–499、300–399、< 300 初學 | 搜尋摘要（itra.run 是動態頁，抓不到） |
| ITRA 指數的年進步幅度 | **未找到來源**。ITRA 說指數「依成績上升」，沒有給典型進步速度 | 未找到來源 |
| UTMB 指數 FAQ [P5] | 近 36 個月最好 5 場加權；超過 12 個月的成績打折；「A valid UTMB Index is achieved by completing at least one race … in the relevant category within the previous 24 months」 | 已驗證（全文） |
| UTMB 總決賽規則 [P6] | OCC 要 20K／50K／100K／100M 任一組的有效指數；CCC 要 50K 以上；UTMB 要 100K 或 100M。Running Stones 不過期 | 已驗證（全文） |
| Hardrock 100 [P7] | 申請者要在申請前 2 年內完成指定資格賽 | 搜尋摘要 |
| Western States 100 [P8] | 2027 年的資格賽要在 2025-11-03 到 2026-11-08 之間完成（約 13 個月），只接受名單上的 100 km 以上賽事 | 已驗證（全文） |

讀完的判斷：

- **「24 個月」= UTMB 指數的有效期、Hardrock 的資格期**。Western States 更短（約 1 年）。所以 24 個月是業界常用的上限，不是我們發明的。
- **「最多往上跳一級」= UTMB 總決賽的規則**。UTMB 的四級（20K／50K／100K／100M）比 ITRA 的七級粗，UTMB 一級大約等於 ITRA 1–2 級。所以「ITRA 高兩級才判超出」和 UTMB 的規則大致相容，嚴格程度略寬。

### 2.2 跑者要累積多久

| 來源 | 對象 | 結果 | 標記 |
|---|---|---|---|
| Hoffman & Krishnan 2013（ULTRA 研究）[P1] | 1,345 名現役與退休超馬跑者 | 第一次超馬的年齡中位數 36 歲；之前規律跑步的年數中位數 7 年（3–15）；25 % 只跑 3 年以下 | 已驗證（摘要） |
| Nikolaidis 等 2021 [P2] | 雅典馬拉松 135 名業餘男性 | 新手（≤ 3 場）跑齡 4.1 ± 2.2 年、最佳 4:20；有經驗（≥ 4 場）9.7 ± 7.0 年、最佳 3:44；週跑量 47 對 59 km | 已驗證（全文摘錄） |
| Knechtle 2014 回顧 [P3] | 多個超耐力項目 | 「Years as active athlete were not related to performance」：多日超馬、24 小時、100 km、長泳、登山車；完賽過幾場 100 km 和成績有關（一篇），24 小時沒有 | 已驗證（全文摘錄） |
| Vickers & Vertosick 2016 [P9] | 2,303 名業餘跑者問卷 | 每週 50 對 30 英里，馬拉松差 25–32 分；模型沒有放跑齡 | 已驗證（全文摘錄） |
| Tanaka & Seals 2008 | — | 表現約 35 歲前維持，之後慢慢下降（`masters-athletes.md` [M1]） | 已驗證（摘要） |
| 50 km 巔峰年齡（Nikolaidis & Knechtle）[P10] | 1975–2016 年 494,414 名 50 km 完賽者 | 5 歲分組巔峰在 35–39 歲；比賽越長，巔峰年齡越大 | 搜尋摘要 |
| Muñoz 2014 | 30 名業餘跑者 10 週 | 10 km 進步 3.6–5.0 %（`plan-backtest-feasibility.md`） | 既有文件 |

讀完的判斷：

- 「累積多久才跑第一場超馬」差很大（3–15 年），沒有一個可以當門檻的數字。
- 和完賽有關的是**有沒有跑過比較接近的距離**（Corrion 2018、Maleka 2026，`race-feasibility.md`），不是年數。這支持 app 用「最大單日 EP」而不是「跑齡」。
- 進步速度的研究都是 8–12 週的短期試驗（3–5 %），沒有越野或用 ITRA 的長期追蹤。

### 2.3 教練怎麼說

| 來源 | 說法 | 標記 |
|---|---|---|
| Bryon Powell（iRunFar）[P11] | 給跑過馬拉松的人；不建議第一場超馬就跑 100 英里，先用 50 km 和 50 英里或 100 km 鋪路 | 已驗證（全文） |
| CTS／Koop〈How Much Do You Need To Train For A 100-Mile〉 | 至少一場 50 英里或 100 km，最好兩場以上，再跑 100 英里；100 km、100 英里賽前 9 週起每週 9 小時、連 6 週 | 搜尋摘要；週時數已在 `race_feasibility.py:98`（KOOP） |
| 「新手一年進步 10–15 %、中階 5–8 %、老手 2–4 %」（RunnersConnect）[P12] | 教練部落格 | **查無出處、不採用**：文中附的 PubMed 編號 23246873 是一篇實驗動物期刊的社論 |

## 3. 落差

| # | 項目 | 來源 | app 現在 | 判斷 |
|---|---|---|---|---|
| 1 | 24 個月 | UTMB 指數有效期、Hardrock 資格期 [P5][P7] | 24 個月，標推估 | 一致；**來源可以換掉「推估」** |
| 2 | 高兩級 | UTMB 總決賽只接受低一級的指數 [P6]；教練要先跑一場中間距離 [P11] | 高兩級才超出 | 大致相容；比 UTMB 寬一點 |
| 3 | 分級邊界 | ITRA 的級距不平均（20、30、40、40、55、開放） | 只看級數 | **問題**：1.35 倍可能判超出、2.5 倍可能判可以（§4） |
| 4 | 個人化除數 | ITRA 分級用 /100 | 跨級用個人化除數 | **小問題**：除數 > 100 時大家往下掉級；相對比較不受影響 |
| 5 | 年進步率 | 未找到來源 | 沒有用 | 一致，不要加 |
| 6 | 多久後可以挑戰 | 沒有年數；教練給的是「先完成哪一級」 | 沒有 | **缺口**：可以給里程碑，不給年數 |

## 4. 分級邊界的例子

「高兩級」對應的實際倍數（目標 EP ÷ 自己最大單日 EP）：

| 自己最大單日 | 目標比賽 | 級數差 | 倍數 | 現在的判斷 |
|---|---|---|---|---|
| 44（XS 上緣） | 75（M 下緣） | 2 | 1.7 | 超出 |
| 25（XS 下緣） | 74（S 上緣） | 1 | 3.0 | 可以 |
| 45（S 下緣） | 114（M 上緣） | 1 | 2.5 | 可以 |
| 114（M 上緣） | 155（XL 下緣） | 2 | 1.36 | 超出 |
| 115（L 下緣） | 209（XL 上緣） | 1 | 1.8 | 可以 |
| 154（L 上緣） | 210（XXL 下緣） | 2 | 1.36 | 超出 |
| 155（XL 下緣） | 400（XXL） | 1 | 2.6 | 可以 |

- 同樣「超出」，倍數可以是 1.36 也可以是 1.7；同樣「可以」，倍數可以到 3.0。
- 這和 UTMB 的做法一樣是「看級，不看倍數」，所以不是錯。但使用者在邊界附近會看不懂為什麼差一點點就判超出。
- 沒有找到研究說多少倍數是安全的。最接近的是單次長跑的 Frandsen 2025（超過 30 天最長 10 % 風險就上升），但那是訓練、不是比賽，不能直接搬（推估）。

## 5. 建議開的單

| # | 標題 | 優先度 | 驗收條件（草案） | 要你決定的事 |
|---|---|---|---|---|
| P-1 | 跨級檢查的來源文字換成業界規則 | P3 | (a) `SRC_STEP` 改寫：24 個月 = UTMB 指數有效期、Hardrock 資格期；高兩級 = 比 UTMB 總決賽「最多跳一級」略寬；(b) 「推估」只留給「ITRA 兩級 ≈ UTMB 一級」這個對應；(c) 測試不變 | 無 |
| P-2 | 跨級結果顯示 EP 倍數 | P3 | (a) 跨級檢查的文字多一句「比賽最難那天是你 24 個月內最大單日的 N 倍」；(b) 判斷規則不變（仍看級數）；(c) 倍數 ≥ 2 但級數只高一級時，加一句「級數只高一級，但距離和爬升是兩倍以上，建議先有一次 ≥ 比賽 70 % 的長天」（70 % 是推估） | 要不要讓倍數也能判「有點趕」（例如 ≥ 2 倍且只高一級）。建議先只顯示，不判 |
| P-3 | 「多久後可以挑戰」改成里程碑 | P3 | (a) 跨級判超出時，建議列出：低一級的目標 EP 範圍、用現有週量推算（`race_feasibility` 已有每週 +10 % 的推算）要幾週才到 Koop／UA 的週量；(b) 不顯示「幾年後」；(c) 說明寫「經驗的研究看的是跑過哪些距離，不是年數（Hoffman 2013：第一場超馬前跑了 3–15 年都有）」 | 推算的週數要不要顯示成日期（「最快 2027 年 3 月」），還是只寫週數 |
| P-4 | 跨級的分級改用 ITRA 除數 100 | P3 | (a) `step` 檢查的 EP 一律用 100 算（和 ITRA 分級一致）；(b) 其他用個人化除數的地方不變；(c) 測試：個人化除數 153 時，跨級結果和除數 100 一樣 | 要不要做。好處是和 ITRA 官方分級對得上；壞處是同一頁兩種 EP（顯示時要說明） |

**不建議開的單**

- 用「年進步率」或「跑齡」做任何判斷或預測：沒有可靠來源（§2.2、§2.3）。
- 把「24 個月」縮短成 12 個月（照 Western States）：Western States 是報名名額的限制，不是能力的判斷。

## 6. 限制、付費牆、查無出處

**限制**

- 沒有任何 ITRA／UTMB 指數的縱向資料。ITRA 條款禁止擷取（`public-datasets.md` §6），所以也沒辦法自己算。
- 經驗的研究都是橫斷面（不同的人比較），不是同一個人隨時間的進步。
- 沒有台灣越野賽的資料。

**付費牆、沒讀到原文**

- itra.run 的 FAQ（動態頁，抓不到）；ITRA 的等級表只有搜尋摘要。
- Hardrock 官網的資格頁 404，只有搜尋摘要。
- Knechtle 的 OAJSM 回顧（403），改讀 2014 年 Asian J Sports Med 版本。
- 50 km 巔峰年齡研究只看到搜尋摘要。

**查無出處、不採用**

- 「新手一年進步 10–15 %、中階 5–8 %、老手 2–4 %」（RunnersConnect，附的出處不對）。
- 「有 2–3 年訓練的人每年進步 3–5 %」：只出現在搜尋摘要，沒有原始研究。

## 參考

- [P1] Hoffman MD, Krishnan E. Exercise behavior of ultramarathon runners: baseline findings from the ULTRA study. *J Strength Cond Res* 2013. https://doi.org/10.1519/jsc.0b013e3182a1f261 （PMID 23838972）
- [P2] Nikolaidis PT, Clemente-Suárez VJ, Chlíbková D, Knechtle B. Training, anthropometric, and physiological characteristics in men recreational marathon runners: the role of sport experience. *Front Physiol* 2021. https://doi.org/10.3389/fphys.2021.666201
- [P3] Knechtle B. Relationship of anthropometric and training characteristics with race performance in endurance and ultra-endurance athletes. *Asian J Sports Med* 2014;5:73–90. https://pmc.ncbi.nlm.nih.gov/articles/PMC4374609/
- [P4] The Ranking in Trail Running with the ITRA Performance Index. run-motion. https://en.run-motion.com/the-ranking-in-trail-running-with-the-itra-performance-index/ （2026-10-06 讀取）
- [P5] UTMB Index FAQ. https://utmb.world/utmb-index/info （2026-10-06 讀取）
- [P6] About the UTMB World Series. https://utmb.world/sports-system （2026-10-06 讀取）
- [P7] Hardrock 100 資格（Trailhead Media 轉述）. https://trailhead.ultrasignup.com/races/how-to-get-into-the-hardrock-hundred-endurance-run/ （搜尋摘要）
- [P8] Western States Qualifying Races. https://www.wser.org/qualifying-races/ （2026-10-06 讀取）
- [P9] Vickers AJ, Vertosick EA. An empirical study of race times in recreational endurance runners. *BMC Sports Sci Med Rehabil* 2016;8:26. https://pmc.ncbi.nlm.nih.gov/articles/PMC5000509/
- [P10] Nikolaidis PT, Knechtle B. Age of peak performance in 50-km ultramarathoners – is it older than in marathoners? *Open Access J Sports Med* 2018. https://doi.org/10.2147/OAJSM.S154816 （搜尋摘要）
- [P11] Bryon Powell. Training for an Ultramarathon. iRunFar. https://www.irunfar.com/training-for-your-first-ultra （2026-10-06 讀取）
- [P12] How Much Faster Can You Get? RunnersConnect. https://runnersconnect.net/how-much-faster-can-you-get-in-a-year/ （查無出處，不採用）
- 既有文件：`race-feasibility.md`（Corrion 2018、Maleka 2026、UTMB Running Stones）、`competitor-charts.md`、`effort-distance-formulas.md`、`public-datasets.md` §6、`masters-athletes.md`。
