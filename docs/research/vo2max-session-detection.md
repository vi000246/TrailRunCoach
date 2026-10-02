# 一趟硬跑算不算間歇：自動判斷有沒有「刺激到 VO2max、而且夠久」

- 日期：2026-10-02
- 分支：本 worktree（只寫文件，不改程式）
- 相關文件：`interval-prescription.md`、`vo2max-gate-and-trail-metric.md`、`zones-and-thresholds.md`、
  `xu-guofeng-reply.md`、`interval-adaptation.md`、`drift-algorithm.md`；
  程式：`backend/engine/workout_review.py`（`session_type`、`_measure`）、`backend/engine/overview.py`（`HARD_EXPRS`、`HARD_SESSION_S`、`Z3_EXPR`）、
  `backend/engine/quality_gate.py`（Z3／Z5 階梯、`dose_history`）、`backend/engine/interval_eval.py`、`backend/engine/interval_reps.py`
- 標記：**同儕審查**、**教練經驗**、**廠商**、**推估**（沒有直接來源的數字，或我們從來源換算出來的數字）、**未驗證**（查不到原文或沒能核對）。
  徐國峰算來源。

---

## 摘要

1. **你的直覺有文獻支持。** 「刺激到 VO2max」在文獻裡就是 **T@VO2max**：攝氧量 ≥ 90 % VO2max 的累積時間。
   Buchheit & Laursen 2013 說每堂要有「at least several minutes」，耐力選手的目標約 **10 分**（同儕審查回顧）。
2. **現在的規則量的是「閾值以上」，不是「VO2max」。**
   - LTHR 155 只有你觀測最大心率（約 185）的 84 %。≥ LTHR 10 分鐘，任何一條認真爬的坡都做得到。
   - ≥ 95 % CP 10 分鐘也一樣：那是閾值強度，不會把攝氧量推到最大（Poole 1988、Jones 2019：CP 以下攝氧量會穩定）。
3. **要到 VO2max，強度要在 CP 以上（嚴重強度區），而且要撐夠久。**
   - 強度越高，到 VO2max 越快（Hill 2002）。實際的門檻：每段 ≥ 106 % CP 撐 ≥ 2 分，或 ≥ 103 % CP 撐 ≥ 5 分。
   - 每段前面的 1～3 分鐘還在爬升，不算（Buchheit：τ 20–35 秒，1:20–2:20 才到）。
4. **手腕心率只能當備案，而且要打折。**
   - 心率有延遲；組休時心率還很高，會高估負荷（Buchheit 2013 §2.3）。
   - 胸帶實測：≥ 90 % HRmax 的時間是 ≥ 90 % VO2max 時間的 1.7 倍（Fleckenstein 2025）。
   - 手腕光學在高心率會低估，也會鎖到步頻（Bent 2020）。
   - 所以心率門檻用 **≥ 93 % 最高心率**，累積時間除以 1.6 才算進來。
5. **一堂算 Z5：等效 T@VO2max ≥ 4 分（推估）。** 「達到目標」是 ≥ 10 分（Buchheit）。
6. **越野：功率只用在 −3…8 % 的坡，> 8 % 改用心率，下坡兩個都不算。**
   百岳不自動判成 Z5，只分「高強度長天」和「長天」。
7. **建議的標籤：Z5 間歇／Z3 閾值／高強度長跑（長天）／中強度跑／長跑／輕鬆跑。**
   Z5 和 Z3 都算間歇（品質課）；週表的 Z5 那一格只能被 Z5 標籤填掉。
8. **回測（2025-10-01～2026-10-01，188 趟）：**
   - 現在有 **100 趟**（53 %）被判成「品質課（間歇）」，51 週裡有 30 週一週 ≥ 2 次。
   - 新分類：Z5 **3**、Z3 53、高強度長跑 5、中強度 89、輕鬆 24、長跑 13、CP 測試 1。
   - 那 3 趟 Z5 都是在 CP 估計偏低（169–182 W）的時候判到的。**用現在的 CP 204 W 重算，一年裡沒有一堂 Z5**（CP 測試除外）。
   - 意思是：你過去一年幾乎沒有做過 VO2max 刺激，現在的規則把閾值跑和中強度跑都算成了間歇。

---

## 1. 現在的規則與它的問題

### 1.1 規則

`workout_review.session_type`（`backend/engine/workout_review.py:1354–1389`）：

- 依序：類別 → AeT 測試 → CP 測試 → **品質課** → 長跑（≥ 75 分，`LONG_MIN_S`）→ 輕鬆跑。
- 品質課（`quality`，標籤「品質課（間歇）」）：
  - 有功率：30 秒功率 ≥ 95 % CP 的時間 ≥ 600 秒（`hard_power_s`），或
  - 心率 ≥ LTHR 的時間 ≥ 600 秒，而且（有功率時）至少偵測到 1 段用力（`detect_efforts`）。
  - 平均心率 ≤ AeT + 3 → 一律不算。
  - 路跑、越野、健行都適用（`QUALITY_CATEGORIES`，使用者 2026-09-30 決定把健行加進來）。
- 週表也用同一組運算式（`overview.HARD_EXPRS`：`heartrate >= lthr` 或 `runpower >= 0.95*cp`）判斷「這週的間歇做了沒」
  （`overview.py:851–860`）；Z3 變體另用 `Z3_EXPR`（≥ 85 % CP）。

### 1.2 誰在用這個結果

| 用途 | 位置 | 判錯的後果 |
|---|---|---|
| 單趟頁的類型標籤、「間歇」卡、Z5 圖示 | `workout_review.py:2343, 2380, 2501` | 一般越野長跑顯示成「品質課（間歇）」 |
| 間歇判讀 | `interval_eval.py:224, 448–462` | 「高強度時間夠算品質課，但找不到一趟一趟的用力段（例如一路爬坡）」：程式自己也發現爬坡被當成間歇 |
| 週表：計畫裡的間歇算做完了沒 | `overview.py:851–860`（`hard >= QG.hard_need`） | **一趟爬坡就把這週的 Z5 間歇勾掉**，那一週其實沒有 VO2max 刺激 |
| 劑量歷史 | `quality_gate.dose_history`（`c["type"] == "quality"` 這條路） | 沒在計畫裡的已經標 `unplanned`、不推進階梯（`quality_gate.py:435, 562`）；但停訓回來的「間歇堂數」用的是日期（`base_check.z5_status` 的 `quality_dates`），會算進去 |

### 1.3 為什麼它抓不到 VO2max

- **LTHR 太低。** 現行 LTHR 155 是熱天約 83 % CP 的心率，真值推估 157–163（`zones-and-thresholds.md` §1.6）。
  你觀測到的最高心率約 185（同檔 §1，COROS 那列的比較）。155 ÷ 185 = 84 %。
  Daniels／徐國峰的 T 強度是 88–92 % HRmax，I 強度（VO2max）是 98–100 % HRmax
  （你的筆記 `300 Sport/60 🏃 有氧訓練/各心率區間的目的.md:36–52`，「引用自徐國峰」）。**≥ LTHR 連 T 強度都不一定到。**
- **95 % CP 是閾值。** 在 CP 或以下，攝氧量、乳酸最後會穩定下來；CP 以上才會一路上升到 VO2max
  （Poole et al. 1988，*Ergonomics* 31:1265–1279，同儕審查；Jones et al. 2019，*Physiol Rep* 7:e14098，同儕審查；
  跑步：Nixon et al. 2021，*EJAP* 121:3133–3144，CS 以上約 0.4 km/h 攝氧量就升到最大，同儕審查）。
- **10 分鐘可以分散在整趟。** 一條 25 分鐘、心率在 LTHR 上下的坡，就夠了。它是很好的 Z3 刺激，但不是 VO2max。

---

## 2. 文獻：「刺激到 VO2max」怎麼定義、要多少、怎麼估

### 2.1 T@VO2max 的定義與劑量

| 來源 | 類別 | 內容 |
|---|---|---|
| Buchheit & Laursen 2013 Part I，*Sports Med* 43:313–338（作者公開版全文，這次重新核對） | 同儕審查回顧 | 「athletes should spend at least several minutes per HIT session in their 'red zone,' which generally means attaining an intensity greater than 90 % of VO2max」（§1）。<br>T@VO2max 的定義：「time spent ≥ 90 % VO2max」（§3）。<br>「a goal T@VO2max of ≈ 10 min per session is appropriate to elicit important cardiopulmonary adaptations」（§3.1.2.4）。<br>訓練有素的選手常見課表（6×2、5×3、4×4 分）累積「from 10 min [> 90 %] to 4–10 min [> 95 %] at VO2max」（§3.1.1.5） |
| Midgley & McNaughton 2006，*J Sports Med Phys Fitness* 46:1–14 | 同儕審查回顧 | T@VO2max 這個概念的回顧；間歇比持續跑能累積更多 T@VO2max（`interval-prescription.md` §A1.1） |
| Seiler & Sjursen 2004，*SJMSS* 14:318–325 | 同儕審查 | 1 分的趟峰值 VO2 只到 82 %；2–6 分的趟到 92 % |
| Billat et al. 2000，*EJAP* 81:188–196 | 同儕審查 | 30-30 在 VO2max 7′51″；vΔ50 的**持續跑**只有 2′42″ |
| Fleckenstein et al. 2025，*Front Sports Act Living* 6:1507957（全文） | 同儕審查 | 高度訓練的中距離跑者，**胸帶**心率：4×3 分 @ 95 % vVO2max：> 90 % VO2max 328 ± 147 秒、> 90 % HRmax 545 ± 131 秒。24×30 秒：201 ± 268 秒 vs 820 ± 249 秒。作者：「utilization of HRmax does not automatically translate to utilization of VO2max」 |
| 台灣教練 | 教練經驗 | 5 區間歇「每趟的時間（最短兩分鐘）也要夠長才能練到最大攝氧量」 |

**解讀：**
- 「有刺激」的下限是「幾分鐘」，好的一堂是約 10 分。
- 這個領域沒有 dose–response 研究告訴你「4 分不算、5 分算」。Buchheit 自己也說劑量關係「limited understanding」。
  **所以「算 Z5」的門檻我們取 4 分**：「several minutes」的具體化，低於約 10 分的目標。這是**推估**。
- 為什麼是 4 不是 5：app 的第一階 Z5（z5a 5×2 分 @ 106–112 % CP）照 §3.3 的算法是 4.5 分。
  它本來就是「入門」劑量（`interval-prescription.md` §A5.3），用 5 分的話，照課表做完的 z5a 不會被判成 Z5。
  回測裡 4 分和 5 分的差別是 2 趟（§5.3），3 分和 4 分沒有差別。

### 2.2 多強才會到 VO2max：CP 以上、而且要夠久

- **CP 是分界。** CP 以下（重強度區）攝氧量會穩定；CP 以上（嚴重強度區）攝氧量會因為慢成分一路升到 VO2max，
  前提是撐得夠久（Poole 1988；Jones 2019；Nixon 2021）。
- **強度和到達時間成反比。** Hill, Poole & Smith 2002（*MSSE* 34:709–714，同儕審查）：
  「功率 vs 到達 VO2max 的時間」是一條雙曲線，強度越高越快到。強度太高（嚴重區上緣以上）會先力竭、到不了。
- **單趟持續跑的門檻比間歇高。** Buchheit Part I §3.1.1.1：
  - 「middle-distance runners did not manage to reach VO2max while running at 92 % of vVO2max」。
  - vΔ50（MLSS 和 vVO2max 中間，約 92–93 % vVO2max）的持續跑到不到 VO2max 看體能，「highly trained runners unlikely to reach VO2max」。
  - 建議單趟用 **≥ 95 % v/pVO2max**；重複多趟時 **≥ 90 %** 也可以，因為慢成分會一趟趟累積。
- **你的筆記（`60 🏃 有氧訓練/如何進入VO2max.md:69–76`，作者沒有標，內容是功率教練的說法，**未驗證**出處）：**

  | 強度 | 持續時間 | 說明 |
  |---|---|---|
  | 106–120 % FTP/CP | 2–5 分 | 越高越快 |
  | 100–105 % FTP | 5–8 分 | 接近 FTP，要比較久才會引發 VO2max |
  | < 100 % FTP | 幾乎無法 | — |

  同一份筆記 :33：「跑者能夠達到 VO2max 的功率-時間組合區域，比自行車選手小得多」（跑者的慢成分比較小）。
- **Palladino**：MAP 間歇 103–109 % CP（`vo2max-gate-and-trail-metric.md` §1.1，教練經驗）。
- **換算到你**：90 % pVO2max ≈ 105–110 % CP，95 % ≈ 111–116 % CP（`interval-prescription.md` §A1.4，Billat 換算，**推估**）。

**轉成規則（§3.3）：**
- 一段 **≥ 106 % CP、≥ 2 分** → 算「VO2 段」（筆記的第一列；徐國峰的 2 分；106 % 是 app Z5 帶的下限）。
- 一段 **103–106 % CP、≥ 5 分** → 也算（筆記的第二列；103 % 是 Palladino MAP 下限）。
- **< 103 % CP 不管多久都不算 VO2**，歸 Z3（筆記第三列；Buchheit 的 92 % vVO2max 持續跑到不了）。
  103 % 而不是 100 %：CP 本身有誤差（Stryd 的 CP ≠ 實驗室 CS，`interval-prescription.md` §A1.4），留 3 % 的緩衝，**推估**。

### 2.3 每段前面多久不算：攝氧量動力學

- Buchheit Part I §3.1.1.2：嚴重強度區的攝氧量主要時間常數 τ「generally in the range of 20 s to 35 s」，
  「VO2max should then be reached from within 1 min 20 s to 2 min 20 s (at least when intervals are repeated)」。
  第一趟慢一點，後面的趟有「priming」（暖身和前幾趟加快攝氧動力學）。
- Seiler & Sjursen 2004：1 分的趟只到 82 %。
- 所以每段要扣掉爬升時間才是「在 VO2max 附近的時間」：
  - ≥ 106 % CP 的段：扣 **60 秒**；當天第一段多扣 30 秒（Buchheit 的 1:20 下緣；**推估**）。
  - 103–106 % CP 的段：扣 **180 秒**（強度低、到達慢，Hill 2002 的雙曲線方向；數字**推估**）。
  - 組休不算（保守。Buchheit 的 T@VO2max 包含組休中仍 ≥ 90 % 的時間，我們量不到）。

### 2.4 心率當替代指標：可以，但會高估

| 來源 | 類別 | 內容 |
|---|---|---|
| Buchheit & Laursen 2013 Part I §2.3（全文） | 同儕審查回顧 | 「HR is expected to reach maximal values (> 90–95 % HRmax) for exercise at or below the speed/power associated with VO2max, this is not always the case, especially for very short (< 30 s) and medium-long (i.e. 1–2 min) intervals」。<br>「HR lag at exercise onset … much slower to respond compared with the VO2 response」。<br>「HR inertia at exercise cessation … can create an overestimation of the actual work/physiological load that occurs during recovery periods」 |
| Fleckenstein 2025 | 同儕審查 | 長間歇：> 90 % HRmax 的時間 545 秒，> 90 % VO2max 只有 328 秒，**約 1.7 倍**；短間歇的方向還相反 |
| Swain et al. 1994，*MSSE* 26:112–116（摘要） | 同儕審查 | %HRmax ≈ 0.64 × %VO2max + 37。85 % VO2max ≈ 92 % HRmax；換算 90 % VO2max ≈ **95 % HRmax**（**推估**，用回歸式代入） |
| Daniels／徐國峰（你的筆記 `各心率區間的目的.md:36–52`） | 教練經驗 | T：88–92 % HRmax；I：98–100 % HRmax |
| Helgerud 2007 | 同儕審查 | 4×4 分 @ 90–95 % HRmax |
| Hunt 2015／2019、Wang & Hunt 2021 | 同儕審查 | 心率一階時間常數 τ ≈ 55–70 秒（`drift-algorithm.md` §2），比攝氧量的 20–35 秒慢 |
| Coyle & González-Alonso 2001 | 同儕審查 | 心血管飄移：同樣負荷，心率在 10–20 分後慢慢上升（`aerobic-base-readiness.md`） |
| Wingo et al. 2005，*MSSE* 37:248–255（摘要）；Wingo et al. 2020，*MSSE* 52（跑步與騎車，摘要） | 同儕審查 | 熱天的心血管飄移伴隨 VO2max 下降，大致成比例。也就是熱天心率變高時，**相對強度（% VO2max）也真的變高了**，不完全是假訊號 |

**解讀：**
- 心率門檻要比 90 % HRmax 高，才不會把閾值跑算成 VO2max：
  - Swain 換算：90 % VO2max ≈ 95 % HRmax。
  - Daniels 的 T 上緣是 92 % HRmax。
  - 手腕光學在高心率會低估（§2.5）。
  - 取中間 **93 % 最高心率**（**推估**）。用你的 185 算是 172 bpm。
- 心率比攝氧量慢，所以「心率剛跨過 93 %」時攝氧量已經在高點。**心率這條路不再扣爬升時間**（τ 的差抵掉了，**推估**）。
- 但用力結束後心率還高著（慣性）。**用力一停就不算**：有功率時 30 秒功率掉到 CP 以下，沒功率時看坡度和速度（§3.4）。
- 心率時間要**除以 1.6**（Fleckenstein 1.7 倍，取整數；我們的門檻比 90 % 高，所以用略小的係數，**推估**），
  換成「等效 T@VO2max」。等於只靠心率要 ≥ 6.4 分（4 × 1.6）。
- 熱天：心率高的確代表相對強度高（Wingo）。所以不另外打折，但有功率的路段**功率優先**：
  熱天 95 % CP、心率 93 % 的閾值跑，還是 Z3。

### 2.5 手腕光學心率

- 高強度時低估：Fitbit 和 Samsung 在 HR ≥ 150 誤差明顯變大，Apple Watch、TomTom 尚可
  （Bioengineering 2023，10:254，DOI 10.3390/bioengineering10020254，同儕審查，跑步機／腳踏車漸增測驗，ECG 對照）。
  COROS 沒有在這篇裡，**未驗證**。
- 鎖步頻（signal crossover）：「optical HR sensors … tend to lock on to the periodic signal stemming from repetitive motion … and mistake that signal as the cardiovascular cycle」
  （Bent et al. 2020，*npj Digit Med* 3:18，同儕審查）。你的跑步步頻約 160–180 spm，正好落在 93 % HRpeak（約 172）附近。
  **所以要排除「心率 ≈ 步頻（±3）」持續 ≥ 30 秒的時段**（±3 和 30 秒是**推估**）。
- 你沒有胸帶（使用者決定），所以心率路線永遠是「替代指標」，卡片上要寫出來。

### 2.6 坡上：功率在哪裡能用

（詳見 `vo2max-gate-and-trail-metric.md` §2。）
- Stryd 在 0–8 % 坡、跑步機上，固定功率 ≈ 固定代謝負荷（van Rassel 2026，同儕審查）。+7 % 時功率與 VO2 的相關只剩 0.69（Gravina-Cognetti 2025）。
- 下坡低估代謝，而且心率會延續上一段坡的高點（慣性）。
- 上坡間歇的 T@VO2max 比例比平路低（Gajer：27 % vs 44 %，Buchheit §3.1.1.4 轉引），但上坡 2 分後慢成分比較大（同段）。
  > 10 % 坡「More research is required」（同段）。
- 所以：
  - 越野 −3…8 % 的路段：用功率。
  - > 8 % 的路段：用心率（加 VAM 當顯示）。
  - < −3 % 的下坡：兩個都不算。

### 2.7 Z3 和 Z5 怎麼分

- **徐國峰**：3 區先、跑順再加 5 區；5 區每趟 ≥ 2 分、一週最多兩次、間隔兩天（私訊 2026-10-01）。
- **app 的 Z3** = 90–95 % CP（`quality_gate.Z3`：3×6、3×8、2×12；Haugen 2022、Palladino、Daniels），
  找趟的下限是 88 % CP（`interval_reps.Z3_FLOOR`）。
- **Uphill Athlete 的 Zone 3** = AeT–LTHR，總量 30–60 分，坡度像比賽（`vo2max-gate-and-trail-metric.md` §2.1）。
- **長爬坡閾值**（同檔 §2.4(c)）：可以跑的坡 90–100 % CP；要走的坡心率 0.95–1.00 LTHR，頭 3 分不看心率。
- **Seiler 2013 的提醒**：4×8 分「可忍受的最大強度」約 90 % HRpeak、血乳酸 9.6 mmol/L，是嚴重強度。
  也就是**長趟只要接近全力，也會進到 VO2max 區**。所以分界看強度（%CP、%HRpeak），不看每趟多長。

**結論：**
- 一條長坡在 CP 附近（88–103 % CP，或心率 ≥ 0.95 LTHR 但 < 93 % HRpeak）：**Z3 刺激**，不是 Z5。
- 同一條坡如果跑在 ≥ 103 % CP 超過 5 分（例如比賽），就會進 VO2，算 Z5。這符合生理，也符合你說的「強度有到就算」。

---

## 3. 分類器設計

### 3.1 輸入

| 輸入 | 來源 | 用在哪 |
|---|---|---|
| 1 秒網格的功率、心率、速度、步頻、坡度（`rgrade`，30 秒平滑） | `workout_review._samples`、`_rgrade` | 全部 |
| CP、LTHR、AeT（當天生效值） | `workout_review._thresholds` | 功率門檻、Z3 心率門檻、輕鬆判斷 |
| **HRpeak**（新） | 365 天內每趟路跑／越野的最高 60 秒平均心率，取**第 3 高**的那一趟（丟掉 2 個可能的光學尖峰），排除鎖步頻的時段；計畫有 `mhr` 時用計畫值 | 心率 VO2 門檻 |
| 類別 | `overview.category`：road／trail／hike | 規則分支 |
| 計畫、標題、使用者標記 | 既有：CP／AeT 測試、`activity_tags`（「當作間歇判讀」） | 優先序（不變） |

**HRpeak 為什麼用 365 天內 60 秒最高值的第 3 高：**
- app 沒有 HRmax（`zones-and-thresholds.md` §1.3）。公式 HRmax 個人誤差 −18.5～+9.1 bpm（Ausland 2026），不能用。
- 60 秒平均可以濾掉光學的單點尖峰；365 天保證涵蓋一次比賽或測試。
- **回測發現 60 秒平均也擋不住整段的光學錯誤**：2026-03-25 越野 216 bpm、2025 年還有 211、201、198。
  只取最高值的話 93 % 門檻變成 196–201 bpm，心率路線永遠不會觸發。
  第 3 高在 2026-08 以後是 188，和你觀測的約 185 接近。
- 天數、60 秒、第 3 高都是**推估**。最好的做法是你在計畫裡填一個 HRmax（決定 4）。
- 一年都沒有全力跑時，HRpeak 會偏低、門檻會偏鬆。卡片上要顯示「HRpeak 來自哪一天」。

### 3.2 前置

1. CP／AeT 測試、肌力、腳踏車等照現行順序先判（不變）。
2. **平均心率 ≤ AeT + 3 → 不是品質課**（現行 `easy_hr`），**但功率路線的 T_p ≥ 4 分時例外**。
   理由：暖身、緩和、組休很長的 Z5 課，整堂的平均心率可能很低。回測裡 9/30 的 CP 測試平均心率只有 136（AeT 138），
   但 12′ 段是 110 % CP。功率是直接證據，平均心率不是。
3. 使用者標記「當作間歇判讀」 → 照標記（現行 `FLAG_TAG`，不變）。

### 3.3 功率路線：VO2 段

只在「功率可信」的樣本上做：
- 路跑：全部。
- 越野：30 秒坡度在 −3…8 %。
- 健行：不用功率（走路功率不可比，UA；`vo2max-gate-and-trail-metric.md` §2.1）。

步驟：
1. 30 秒功率 ≥ 1.03 × CP 的連續段（中間 < 5 秒的缺口接起來，同 `count_reps`）。
2. 段平均 ≥ 1.06 CP 且 ≥ 120 秒 → VO2 段；扣 60 秒（當天第一段扣 90 秒）。
3. 段平均 1.03–1.06 CP 且 ≥ 300 秒 → VO2 段；扣 180 秒。
4. 其他段不算 VO2（會在 §3.5 算進 Z3）。
5. **T_p** = 所有 VO2 段扣完後的秒數加總。

範例（CP 204 W）：

| 課 | 計算 | T_p |
|---|---|---|
| z5a 5×2 分 @ 110 % | 30 + 60×4 | 4.5 分（≥ 4，算 Z5） |
| z5b 4×3 分 @ 108 % | 90 + 120×3 | 7.5 分 |
| z5d 4×4 分 @ 106 % | 150 + 180×3 | 11.5 分 |
| 5 km 比賽 22 分 @ 108 % | 22×60 − 90 | 20.5 分 |
| 長坡 25 分 @ 98 % | < 1.03 | 0（→ Z3） |
| 20 分節奏跑 @ 104 % | 20×60 − 180 | 17 分 |

最後一列要注意：104 % CP 持續 20 分，對 CP 準確的人來說接近力竭（CP 只撐 20–30 分，Jones 2019），攝氧量會升到最大。
如果你常常跑出這種結果、而且覺得「沒那麼喘」，代表 **CP 偏低**，該重測。這比把 103 % 往上調更好。

### 3.4 心率路線：功率不可信的路段

只在「沒有可信功率」的樣本上做（沒有功率的路跑、越野 > 8 % 的坡）：
1. 30 秒心率 ≥ **0.93 × HRpeak**。
2. 排除：鎖步頻（心率與步頻差 ≤ 3、持續 ≥ 30 秒；回測發現這樣太鬆，實作改成「心率跟著步頻變化」，§5.4）、下坡（< −3 %）、停下來（速度 < 0.5 m/s）。
   後兩條就是「用力一停就不算」，處理心率慣性（Buchheit §2.3）。
3. 連續 ≥ 60 秒才算（濾掉尖峰，**推估**）。
4. **T_h** = 加總秒數。不扣爬升時間（§2.4）。
5. 有可信功率的樣本**不看心率**：功率說 < 1.03 CP 就不是 VO2，心率再高也不算（處理熱天飄移與閾值長坡）。

### 3.5 合併與標籤

**等效 T@VO2max = T_p + T_h ÷ 1.6**

依序判斷（第一個符合的就是答案）：

| 順序 | 條件 | 標籤 | 類型（`session_type`） | 算不算間歇 |
|---|---|---|---|---|
| 1 | 等效 T@VO2max ≥ **4 分**，類別是 road／trail | **Z5 間歇** | `quality`，`stimulus = "z5"` | 算（Z5） |
| 2 | Z3 時間 ≥ **10 分**，移動時間 ≥ 75 分 | **高強度長跑**（健行：**高強度長天**） | `hard_long`（新） | 不算間歇；算硬課（間隔 48 小時） |
| 3 | Z3 時間 ≥ **10 分** | **Z3 閾值** | `quality`，`stimulus = "z3"` | 算（Z3） |
| 4 | 移動時間 ≥ 75 分 | 長跑／長天 | `long` | 不算 |
| 5 | 平均心率 > AeT + 3 | **中強度跑**（灰色地帶） | `easy`，`moderate: true`（新旗標） | 不算 |
| 6 | 其他 | 輕鬆跑 | `easy` | 不算 |

順序 5 是回測後加的：現在被判成品質課、新分類又不是 Z3 的 43 趟，平均心率都在 AeT + 3 以上。
叫它「輕鬆跑」不對，app 的強度卡已經把 AeT–LTHR 叫「中強度（灰色地帶）」（`workout_review.py:2326`），沿用這個詞。
類型還是 `easy`，飄移判讀等既有邏輯不受影響。

**Z3 時間的算法：**
- 功率可信的樣本：30 秒功率 ≥ 0.88 CP 的連續段，每段 ≥ 150 秒（`interval_reps.Z3_FLOOR`、`Z3_MIN_S`）。
- 功率不可信的樣本：30 秒心率 ≥ 0.95 LTHR，扣掉每段的前 3 分鐘（延遲；`vo2max-gate-and-trail-metric.md` §2.4(c)），剩下 ≥ 150 秒才算。
- 10 分是現行的 `HARD_SESSION_S`（沿用）。

**順序 2 在 3 前面的原因：**
- 一趟 2–3 小時的越野，裡面有 20 分鐘的閾值坡，主要目的是長天，不是閾值課。
- 把它算進「本週間歇次數」，會讓週表以為這週已經有品質課，然後少排一堂。
- 但它真的有強度，所以不能當一般長跑：要算硬課，下一堂強度課要隔 48 小時。
- 長跑本身就達到 Z5 時（例如越野賽），還是 Z5（順序 1 先判）。

### 3.6 各運動的規則

| | 路跑 | 越野 | 百岳／健行 |
|---|---|---|---|
| VO2 判斷 | 功率為主；沒有功率才用心率 | −3…8 % 用功率；> 8 % 用心率；下坡不算 | **不自動判 Z5** |
| Z3 判斷 | 功率 ≥ 0.88 CP；沒有功率用心率 | 同左，分坡度 | 心率 ≥ 0.95 LTHR，扣前 3 分 |
| 可能的標籤 | Z5／Z3／高強度長跑／長跑／中強度／輕鬆 | 同左 | 高強度長天／Z3（< 75 分）／長天／中強度／輕鬆 |
| 功率可信的根據 | Stryd 平路（Ruiz-Alias 2024） | van Rassel 2026（0–8 %） | UA：走路功率不可比 |

**百岳不判 Z5 的理由：**
- 只有手腕心率。高海拔時同樣的負荷心率會變高，VO2max 本身也降，%HRpeak 和平地不可比
  （這條我們沒有找海拔的直接研究，**推估**）。
- 負重、補給停頓、長時間的心血管飄移都會把心率推高。
- 百岳的目的是長天和爬升量（`baiyue-from-running.md`）。就算心率在 93 % 以上待了很久，它也不該占掉這週的 Z5 間歇。
- 真的想算，可以用「當作間歇判讀」的標記手動算。

### 3.7 門檻一覽

| 參數 | 值 | 來源 |
|---|---|---|
| T@VO2max 的定義 | ≥ 90 % VO2max | Buchheit & Laursen 2013 §1、§3（同儕審查） |
| 一堂「有刺激」 | 等效 ≥ 4 分 | **推估**：Buchheit「at least several minutes」；讓照課表做完的 z5a（4.5 分）算得到 |
| 一堂「達到目標」（顯示用） | ≥ 10 分 | Buchheit §3.1.1.6 |
| VO2 段（高） | ≥ 1.06 CP、≥ 2 分 | 你的筆記 `如何進入VO2max.md:73`（106–120 %，2–5 分；**未驗證**作者）；徐國峰 ≥ 2 分；app Z5 帶下限 |
| VO2 段（低） | 1.03–1.06 CP、≥ 5 分 | 同筆記 :74（100–105 %，5–8 分）；Palladino MAP 下限 103 %；3 % 緩衝是**推估** |
| 扣爬升時間 | 60 秒（第一段 90）／180 秒 | Buchheit §3.1.1.2（τ 20–35 秒、1:20–2:20 到達）；數字**推估** |
| 心率 VO2 門檻 | ≥ 0.93 HRpeak | **推估**：Swain 1994 換算 95 %、Daniels T 上緣 92 %，再考慮手腕低估 |
| 心率時間係數 | ÷ 1.6 | **推估**：Fleckenstein 2025 的 1.7 倍 |
| 心率最短段 | ≥ 60 秒 | **推估** |
| 鎖步頻排除 | 心率與步頻差 ≤ 3，≥ 30 秒 | Bent 2020 有這個現象；數字**推估** |
| 功率可信坡度 | −3…8 % | van Rassel 2026；下限 −3 % 是**推估** |
| Z3 下限 | 0.88 CP；心率 0.95 LTHR | `interval_reps.Z3_FLOOR`（Palladino／interval-prescription §C2）；Friel Z4 下緣 |
| Z3 最短段 | 150 秒；心率扣前 3 分 | `interval_reps.Z3_MIN_S`（推估，既有）；心率延遲 3τ（推估，既有） |
| 品質課最少時間 | Z3 ≥ 10 分 | 現行 `HARD_SESSION_S`（沿用） |
| 長跑 | ≥ 75 分 | 現行 `LONG_MIN_S`（沿用） |
| HRpeak | 365 天內每趟 60 秒最高值的第 3 高 | **推估**（回測：最高值被光學錯誤汙染） |

### 3.8 虛擬碼

```
def vo2_stimulus(samples, cp, lthr, aet, hrpeak, category):
    p30, h30, g30 = 30 s rolling power / HR / grade on a 1-s grid
    lock   = |HR − cadence_spm| ≤ 3 for ≥ 30 s
    pvalid = category == road, or (trail and −0.03 ≤ g30 ≤ 0.08); never for hike

    T_p, first = 0, True
    for seg in runs(pvalid & p30 ≥ 1.03·cp, bridge=5 s):
        pm = mean(power[seg]) / cp
        if pm ≥ 1.06 and len ≥ 120:   T_p += len − (90 if first else 60); first = False
        elif pm ≥ 1.03 and len ≥ 300: T_p += len − 180;                 first = False

    T_h = Σ len(seg) for seg in runs(~pvalid & ~lock & moving & g30 ≥ −0.03
                                    & h30 ≥ 0.93·hrpeak) if len ≥ 60
    eq = T_p + T_h / 1.6

    Z3 = Σ len for runs(pvalid & p30 ≥ 0.88·cp) if len ≥ 150
       + Σ (len − 180) for runs(~pvalid & ~lock & g30 ≥ −0.03 & h30 ≥ 0.95·lthr) if len − 180 ≥ 150
    return {"t_vo2_eq": eq, "t_vo2_power": T_p, "t_vo2_hr": T_h, "z3_s": Z3, "hrpeak": hrpeak}
```

`session_type` 的新順序：測試 → （road／trail 且 `T_p ≥ 240`）→ `quality/z5` → `easy_hr`
→ （road／trail 且 `t_vo2_eq ≥ 240`）→ `quality/z5` → （`z3_s ≥ 600` 且 ≥ 75 分）→ `hard_long`
→ （`z3_s ≥ 600`）→ `quality/z3` → long → easy（平均心率 > AeT + 3 時 `moderate`）。

### 3.9 卡片要顯示什麼

- 「Z5 間歇」卡：等效 T@VO2max X 分（功率 a 分＋心率 b 分 ÷ 1.6），目標 ≥ 10 分；每段 VO2 段的時間和 %CP。
- 心率路線有用到時加一行：「心率是手腕光學，只當替代指標；HRpeak 172（2026-xx-xx）」。
- 「Z3 閾值」或「高強度長跑」卡：Z3 時間；「最高強度沒有到 VO2（最高的段 98 % CP）」，讓你知道為什麼不是 Z5。
- 說明放 ? icon（UI 要簡潔）。

---

## 4. 接到品質門檻與每週次數

| 地方 | 現在 | 建議 |
|---|---|---|
| `session_type` 的 `quality` | 一種 | 保留 `quality`，加 `stimulus: "z5" | "z3"`；新增 `hard_long`。舊程式碼看 `quality` 的地方不用改 |
| `TYPE_LABEL` | 「品質課（間歇）」 | `quality/z5`「Z5 間歇」、`quality/z3`「Z3 閾值」、`hard_long`「高強度長跑」／（健行）「高強度長天」 |
| 週表：計畫的 **Z5** 間歇算做完 | `HARD_EXPRS`（≥ LTHR 或 ≥ 95 % CP）≥ `hard_need` | 要 `stimulus == "z5"`，而且等效 T@VO2max ≥ 計畫變體的 60 %（沿用 `hard_need` 的 60 %）。計畫變體的 T@VO2max 用 §3.3 對它的標準課表算 |
| 週表：計畫的 **Z3** 間歇算做完 | `Z3_EXPR`（≥ 85 % CP） | `stimulus` 是 z3 或 z5 都可以，Z3 時間 ≥ 60 % 計畫（Z5 的那堂強度更高，也滿足 Z3 那一格；**推估**） |
| `hard_long` | — | 不填間歇那一格；在 `plan_prefs.place`／`b2b` 的「硬課間隔 48 小時」裡算硬課（徐國峰：5 區之間隔兩天；硬課 48 小時是現行規則） |
| `dose_history` | `c["type"] == "quality"` 就收 | 沒計畫的：只收 `stimulus == "z5"` 或 Z3 趟數 ≥ 2（現行）。`hard_long` 不收。**停訓回來的堂數**（`quality_dates`）同樣只收這兩種 |
| Z5 每週最多兩次、間隔兩天（徐國峰） | 只在計畫端 | 用 `stimulus == "z5"` 數，沒計畫的 Z5（比賽、團練）也要算進去 |
| 強度分配卡（Seiler 三區）、TSS | 不受影響 | 不變 |

`HARD_EXPRS` 本身（「這週高強度時間」的圖表和 ramp 檢查）可以保留：它量的是「閾值以上的時間」，那個問題沒有錯，只是不該拿來判斷「VO2max 間歇做了沒」。

---

## 5. 回測（唯讀）

### 5.1 方法

- 資料：`dataset_for_source("coros")`，在 `~/.wko5coach` 的**複本**上跑（HOME 指到 scratch 資料夾，跑完刪除）；真的 DB 和 port 8000 沒有碰。
- 範圍：2025-10-01～2026-10-01，路跑、越野、健行共 **188 趟**（路跑 147、越野 36、健行 5）。HRpeak 用再往前 365 天的資料。
- 現行標籤：`workout_review.classify`。新標籤：§3 的規則，Z5 門檻 4 分。CP、LTHR、AeT 都用**當天生效**的值（CP 169–204 W，LTHR 145–161）。
- 腳本在 scratch，沒有進 repo。

### 5.2 結果

| 現行 → 新 | 趟數 |
|---|---|
| 品質課 → **Z5 間歇** | 3 |
| 品質課 → **Z3 閾值** | 48 |
| 品質課 → **高強度長跑** | 5（越野 4、路跑 1） |
| 品質課 → **中強度跑** | 43（路跑 36、越野 7） |
| 品質課 → 長跑 | 1 |
| 輕鬆跑 → Z3 閾值 | 5 |
| 輕鬆跑 → 中強度跑 | 46 |
| 輕鬆跑 → 輕鬆跑 | 24 |
| 長跑 → 長跑 | 12 |
| CP 測試 | 1 |

| | 現行 | 新 |
|---|---|---|
| 「間歇」次數（一年） | 100 | 56（Z5 3 + Z3 53） |
| 有間歇的週（共 51 週） | 43 | 31 |
| 一週 ≥ 2 次間歇的週 | 30 | — |
| 有 Z5 的週 | — | 3 |

**例子：**

| 日期 | 類別 | 時間 | 現行 | 新 | 為什麼 |
|---|---|---|---|---|---|
| 2025-11-02 | 越野 | 223 分 | 品質課 | 高強度長跑 | 心率 ≥ LTHR（149）208 分；Z3 時間 33.5 分；沒有 VO2 段 |
| 2025-11-16 | 越野 | 154 分 | 品質課 | 高強度長跑 | ≥ LTHR 151 分；Z3 28.6 分 |
| 2025-12-21 | 路跑 | 141 分 | 品質課 | 高強度長跑 | Z3 91.5 分（長時間閾值附近），沒有 ≥ 103 % CP 的段 |
| 2026-08-29 | 越野 | 71 分 | 品質課 | Z3 閾值 | 功率可信的時間只有 8 %；心率 ≥ 93 % HRpeak 6 分，但在功率可信的路段或下坡，不算 |
| 2025-10-01 | 路跑 | 36 分 | 品質課 | 中強度 | ≥ LTHR（145）29.7 分，但 ≥ 95 % CP 只有 1.3 分：LTHR 估得太低 |
| 2026-08-22 | 越野 | 61 分 | 品質課 | 中強度 | 平均心率 160 > LTHR 155，但功率可信的路段功率 < 88 % CP（熱天）；見決定 7 |
| 2025-10-21 | 路跑 | 38 分 | 品質課 | **Z5** | 3.4 分 @ 111 %、3.5 分 @ 122 %（CP 182）→ T_p 4.4 分 |
| 2026-09-04 | 路跑 | 35 分 | 品質課 | **Z5** | 4 段 2.4–4 分 @ 111–118 %（CP 169）→ T_p 7.2 分 |
| 2026-09-22 | 路跑 | 42 分 | 品質課 | **Z5** | 6.4 分 @ 113 %（CP 175）→ T_p 4.9 分 |
| 2026-09-30 | 路跑 | 48 分 | CP 測試 | CP 測試 | 12′ 段 110 %（CP 204）→ T_p 6.7 分；平均心率只有 136（§3.2 的例外） |

### 5.3 敏感度

| Z5 門檻（T_p） | 符合的趟（不含 CP 測試） |
|---|---|
| ≥ 3 分 | 3（10/21、9/4、9/22） |
| ≥ 4 分 | 3 |
| ≥ 5 分 | 1（9/4） |
| ≥ 8 分 | 0 |
| ≥ 10 分（Buchheit 的目標） | 0 |

**CP 對結果的影響最大：**
- 3 趟 Z5 都在 CP 估計偏低的時候（169–182 W，夏天的 Stryd PD 擬合）。
- 用 9/30 測出的 **CP 204 W** 重算：
  - 9/4 的 4 段是 188–199 W，只有 92–98 % CP → Z3。
  - 9/22 的 197 W 是 97 % CP → Z3。
  - 10/21 只剩 1 段 3.5 分 @ 108 % → T_p 約 2 分 → Z3。
- 也就是：**以你現在的體能，過去一年除了 CP 測試，沒有一堂真正的 VO2max 刺激。** 跟 `quality_gate` 的階梯一致：你還在 Z3 段。

### 5.4 心率路線

- 心率路線在這一年**沒有讓任何一趟變成 Z5**。最多是 2026-08-22 越野的 3.9 分（等效 2.4 分）。
- 原因：
  - 越野時大部分時間 ≥ 93 % HRpeak 的樣本落在功率可信的坡（功率說不是 VO2）或下坡（慣性）。
  - 百岳（5 趟）照規則不判 Z5。
- **鎖步頻規則觸發太多**：188 趟裡 144 趟有 > 1 分鐘「心率和步頻差 ≤ 3」。
  你的輕鬆跑心率約 150、步頻約 150–160，很多只是剛好接近，不是鎖定。
  這條規則只影響心率路線，而心率路線本來就少用，所以結果沒變；但實作時要改成「心率跟著步頻變化」（例如 60 秒內兩者的相關 > 0.8，**推估**），不能只看數值接近。

---

## 6. 限制

1. **沒有一個門檻是用你的攝氧量驗證過的。** 全部建立在 CP 和 HRpeak 準不準上。CP 偏低 → Z5 判太多（§5.3 就是例子）；HRpeak 偏低 → 心率路線判太多。
   過去的課要用**當天生效的 CP** 判（不然夏天的課全部會變 Z3），但這表示 CP 估錯時歷史標籤也跟著錯。
2. 心率路線的 ÷ 1.6 來自胸帶、跑步機、中距離選手的研究。手腕光學、越野、熱天的係數**未驗證**。
3. 間歇組休中的 T@VO2max 沒有算（保守）。30-30 這類短間歇幾乎都會被判成「不是 Z5」；這和徐國峰「每趟 ≥ 2 分」一致，但和 Billat 的 30-30（7′51″ 在 VO2max）不一致。
4. > 8 % 坡只有心率。Buchheit 自己說 > 10 % 坡的心肺反應「more research is required」。
5. 百岳完全不判 Z5 是保守的選擇，不是生理結論。

---

## 7. 待使用者決定

1. **Z5 的門檻：等效 T@VO2max ≥ 4 分？** 選項：4 分（建議，z5a 做完算得到）／5 分（回測少 2 趟）／10 分（Buchheit 的目標，現在的階梯沒有一階算得到）。
2. **「高強度長跑」要不要獨立成一類、不算間歇次數？** 建議要。不要的話，長天裡的閾值坡會算成 Z3 閾值課，週表會少排一堂。回測有 5 趟。
3. **百岳要不要完全不自動判 Z5？** 建議不判，要算時手動標「當作間歇判讀」。
4. **HRmax 要不要自己填？** 建議在計畫裡填一個 HRmax（`mhr`，例如 185）。沒填時用「365 天內第 3 高的 60 秒心率」（回測是 188；最高值被 216、211 這種光學錯誤汙染）。
5. **週表的 Z5 那一格，要不要改成只有 Z5 標籤才能勾掉？** 建議要。這會讓過去一些週變成「這週沒做到間歇」。
6. **「中強度跑」要不要成為看得到的標籤？** 回測有 89 趟（47 %）平均心率在 AeT + 3 以上、又沒有 Z3 的量。這是 Seiler 的灰色地帶，比「被算成間歇」更值得提醒。
7. **熱天心率高、功率低的路段，算不算 Z3？** 規則是有可信功率時功率優先（2026-08-22 越野，平均心率 160 > LTHR，判成中強度）。
   Wingo 的研究說熱天心率變高時，相對強度真的變高。要不要在 Hadley／氣溫高的時候改成心率優先？建議先不要（熱天的閾值課不算 Z3，最多是少記一堂），但這是你的感受問題。
8. **回測結果你的感受對不對？** 特別是 10/21、9/4、9/22 這 3 趟：用當時的 CP 算是 Z5，用現在的 CP 204 算是 Z3。如果你記得那幾次很喘、接近全力，代表當時的 CP 估得對；如果只是「節奏跑」的感覺，代表夏天的 CP 估低了。

---

## 8. 參考資料

**同儕審查**
- Buchheit M, Laursen PB (2013). High-intensity interval training, solutions to the programming puzzle. Part I: cardiopulmonary emphasis. *Sports Med* 43:313–338. DOI 10.1007/s40279-013-0029-x（作者公開版全文：https://paulogentil.com/pdf/HIIT%201%202013.pdf，本次核對 §1、§2.3、§3.1.1.1–3.1.1.6）
- Bent B, Goldstein BA, Kibbe WA, Dunn JP (2020). Investigating sources of inaccuracy in wearable optical heart rate sensors. *npj Digit Med* 3:18. https://www.nature.com/articles/s41746-020-0226-6
- Fleckenstein D, Braunstein B, Walter M (2025). Faster intervals, faster recoveries – intensified short VO2max running intervals are inferior to traditional long intervals in terms of time spent above 90 % VO2max. *Front Sports Act Living* 6:1507957. DOI 10.3389/fspor.2024.1507957（全文 PMC11743937）
- Hill DW, Poole DC, Smith JC (2002). The relationship between power and the time to achieve VO2max. *Med Sci Sports Exerc* 34:709–714（摘要）
- Poole DC, Ward SA, Gardner GW, Whipp BJ (1988). Metabolic and respiratory profile of the upper limit for prolonged exercise in man. *Ergonomics* 31:1265–1279（摘要）
- Jones AM et al. (2019). *Physiol Rep* 7:e14098；Nixon RJ et al. (2021). *Eur J Appl Physiol* 121:3133–3144：見 `zones-and-thresholds.md` §2.2
- Swain DP, Abernathy KS, Smith CS, Lee SJ, Bunn SA (1994). Target heart rates for the development of cardiorespiratory fitness. *Med Sci Sports Exerc* 26:112–116（摘要，PubMed 8133731）
- Wingo JE, Lafrenz AJ, Ganio MS, Edwards GL, Cureton KJ (2005). Cardiovascular drift is related to reduced maximal oxygen uptake during heat stress. *Med Sci Sports Exerc* 37:248–255（摘要）
- Wingo JE et al. (2020). Cardiovascular drift and maximal oxygen uptake during running and cycling in the heat. *Med Sci Sports Exerc* 52(9)（摘要）
- Are activity wrist-worn devices accurate for determining heart rate during intense exercise? (2023). *Bioengineering* 10:254. DOI 10.3390/bioengineering10020254（PMC9952291，摘要）
- Seiler & Sjursen 2004；Seiler 2013；Billat 2000／2001；Helgerud 2007；Midgley & McNaughton 2006；Haugen 2022：見 `interval-prescription.md` 參考資料
- van Rassel 2026；Gravina-Cognetti 2025：見 `vo2max-gate-and-trail-metric.md`
- Hunt 2015／2019、Wang & Hunt 2021：見 `drift-algorithm.md`；Coyle & González-Alonso 2001：見 `aerobic-base-readiness.md`；Ausland 2026：見 `zones-and-thresholds.md`

**教練經驗**
- 台灣教練：`xu-guofeng-reply.md`
- 徐國峰引 Daniels 的 E／M／T／I 強度表：你的筆記 `C:\Users\<user>\Projects\notes\notes\300 Sport\60 🏃 有氧訓練\各心率區間的目的.md:1–52`
- 「power@VO2max 是功率-時間組合」與強度-時間表：同資料夾 `如何進入VO2max.md:1–76`（作者沒有標，**未驗證**）
- Palladino MAP 103–109 % CP：`vo2max-gate-and-trail-metric.md` §1.1
- Uphill Athlete Zone 3：`vo2max-gate-and-trail-metric.md` §2.1
