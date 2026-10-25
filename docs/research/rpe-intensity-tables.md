# RPE 換強度的兩張預設表：哪張對、怎麼統一（SP-808）

> 調查日期：2026-10-21。只做調查，沒有改程式。分支 `docs/sp808-844-research`（基於 `integrate/2026-10-20b`）。
> 背景：SP-511 Q1 第 4 題（使用者 2026-10-20 決定另開研究單、P3）。相關：SP-57（「負荷」用 RPE 填）、SP-62（RPE 當目標的步驟）、SP-69／`estimated-constants-inventory.md` §4.1 第 6 項。
> 標記：**程式**＝讀 repo 確認（file:line 以這個分支為準）；**出處**＝讀過網頁或摘要頁，網址在 §6；**推估**＝我的推論。沒有讀擁有者的資料，提到他的數字一律引自 SP-511 單上的筆記。

## 0. 結論

1. **兩張表量的是同一件事，沒有理由不同。** 兩邊都標「Borg CR-10、照 Foster session RPE 的用法」（`rpe_load.py:9-16`、`workout_steps.py:161-164`），而且兩邊的本人校正都用**同一批自評活動**（整趟的 RPE）去擬合（`rpe_load.py:24-30`、`intensity_calib.py:17-26`「the same ratings rpe_load fits」）。所以不是「一張評整段、一張評當下」的差別，是**兩次各自推估**。（程式）
2. **`RPE_FRAC` 沒有逐點出處**：2026-10-04 SP-62 一次寫成 0.55→1.10 的斜坡，整張標推估（`cba9c141`，`workout_steps.py:168`）。**`DEFAULT_IF` 有逐級理由**：2026-10-08 SP-57 寫的，每一級寫了出處範圍與取點（`rpe_load.py:84-111`），而且「很累 0.95」「輕鬆 ≈ 55 TSS/h」是使用者拍板過的（SP-57 Q1 2026-10-07、筆記 2026-10-08）。（程式、單）
3. **文獻只給範圍，兩張都落在範圍內**（§3）。高端（RPE 7–10）兩張幾乎一樣；差在低端（RPE 2–5，差 0.06–0.12）。文獻在低端偏向哪邊不一致：Coggan 的「當下 RPE」表與 Dantas 2015 的跑者乳酸對照偏低（支持 `RPE_FRAC`），跑步功率輕鬆區（65–80 % CP）與 app 自己的輕鬆跑錨點 `EASY_F` 0.78 偏高（支持 `DEFAULT_IF`）。
4. **`RPE_FRAC` 的低端跟 app 自己的定義衝突**（程式）：
   - `RPE_EASY_MAX = 4` 的意思是「第一區上緣（約 LT1）」（`workout_steps.py:169`），Palladino 第一區上緣是 80 % CP（`zones.py:32`、`EASY_POWER_PCT` 0.80 `workout_steps.py:823`）；`RPE_FRAC[4]` 卻是 0.76，比 app 自己的「平均輕鬆跑」`EASY_F` 0.78（`workout_steps.py:1592`）還低。
   - 爬坡反覆的上坡段寫「心率約輕鬆跑上限」、目標 RPE 4（`workout_steps.py:678`），用表算是 0.76，低於輕鬆跑本身。
   - RPE 2「輕鬆」0.62，低於跑步功率輕鬆區的下緣 65 % CP。
5. **建議：以 `DEFAULT_IF` 的五檔為準，`RPE_FRAC` 改成由五檔內插**（選項 A，§4）。這樣 `rpe_load` 的預設與本人擬合都不動、用 RPE 填的負荷段不重推；只有 RPE 當目標的步驟換算變高。實際影響比表上看起來小：存下來的課裡 RPE ≤ 4 的時間段早就改用本人輕鬆負荷率計價（SP-437），表的 1–4 主要只剩圖表高度、沒有本人負荷率時的預估，以及「負荷＋RPE 目標」的步驟。

## 1. 兩張表

| RPE（CR-10） | Foster 用語 | `rpe_load.DEFAULT_IF`（五檔） | `workout_steps.RPE_FRAC` | 差 |
|---|---|---|---|---|
| 1 | very very easy | （FIT 1 → 輕鬆 0.74） | 0.55 | |
| 2 | easy | **輕鬆 0.74** | 0.62 | −0.12 |
| 3 | moderate | （FIT 3 → 稍累 0.82） | 0.70 | |
| 4 | somewhat hard | **稍累 0.82** | 0.76 | −0.06 |
| 5 | hard | **累 0.88** | 0.82 | −0.06 |
| 6 | | （FIT 6 → 累 0.88） | 0.88 | |
| 7 | very hard | **很累 0.95** | 0.94 | −0.01 |
| 8 | | （FIT 8 → 很累 0.95） | 1.00 | |
| 9 | | （FIT 9 → 極限 1.10） | 1.05 | |
| 10 | maximal | **極限 1.10** | 1.10 | 0 |

- `DEFAULT_IF`：`rpe_load.py:96-104`；粗體是五檔的 CR-10 錨點（`rpe_load.py:77-78`）。FIT／COROS 的 1–10 用 `FIT_LEVEL` 兩分一檔（`rpe_load.py:121-122`）。預設每小時 TSS ＝ IF² × 100（`rpe_load.py:112`）。
- `RPE_FRAC`：`workout_steps.py:168`；前端圖表另有一份副本 `static/workout_editor.js:243`。SP-511 起再乘本人 `rpe_if_scale`（`intensity_calib.py:17-26`、`workout_steps.py:1383-1387`、`:1425`）。

### 1.1 各自用在哪（程式）

| 表 | 用在 | 會不會到手錶 |
|---|---|---|
| `DEFAULT_IF` | 「負荷」步驟用 RPE＋分鐘填時的 TSS（SP-57）；本人逐檔擬合往它收縮；設定頁五檔表（`rpe_load.py:372-394`） | 會：步驟的 TSS → COROS TL；換算值變了會重推一次（`rpe_load.py:46-57`） |
| `RPE_FRAC` | RPE 當目標的步驟（技術地形、下坡、爬坡反覆的上下坡段、沒心率沒 CP 時的輕鬆段）：圖表高度、計畫 TSS（`workout_steps.py:1420-1426`、`:1768-1778`）；「負荷」步驟帶 RPE 目標時的時間與 TL（`load_if`，`workout_steps.py:1658-1672`）；`rpe_if_scale` 擬合的分母（`intensity_calib.py:150-166`） | 一般的時間段不會（推的是時間、不帶強度）；「負荷＋RPE 目標」的步驟會（時間與 TL 由這個 IF 算，`sent_key` 帶 IF，`workout_steps.py:1680-1681`） |

**RPE ≤ 4 的時間段早就不吃表的 TSS**：SP-437 起，暖身、緩和、休息和「輕鬆目標」（含 RPE 上限 ≤ 4，`workout_steps.py:826-831`）的段用本人的輕鬆負荷率計價（`rate_easy`，`workout_steps.py:1739-1747`、`:1772-1775`）；課表編輯器與排課計價都會給這個值（`api/plan_sessions.py:2056-2059`、`session_structure.figures` `session_structure.py:89-105`）。表的 1–4 只在沒給本人負荷率時才算 TSS。

### 1.2 RPE 目標在哪裡出現（程式）

| 哪裡 | RPE | 時間 |
|---|---|---|
| 範本 技術地形 60′（`workout_templates.py:414-417`） | 3–4 | 60 分 |
| 範本 技術地形 90′（`workout_templates.py:418-422`） | 6–7 | 90 分 |
| 爬坡反覆 上坡／下坡（`workout_steps.py:678-679`、`:715-718`） | 4–4／3–4 | 依課 |
| 肌耐力爬升 下山段（`workout_steps.py:736`、`:764`） | 3–4 | 依課 |
| 路線排課 陡坡沒心率（`route_session.py:44`、`:242`） | 3–4 | 依路線 |
| 路線排課 沒 CP 沒心率的輕鬆段（`route_session.py:251`） | 3–4 | 依路線 |

只有「技術地形 90′」落在 RPE ≥ 5、會照表算 TSS。

## 2. 兩張表從哪來

- **`RPE_FRAC`**：`cba9c141`（2026-10-04，SP-62「split 越野跑 into 結構化爬升 / 技術地形 / 下坡 with RPE targets」）一次加進來，commit 只說「≈ % CP only for the chart / TSS estimate」，表本身標推估，沒有逐點出處。形狀是 0.55→1.10 每分約 +0.06 的斜坡（推估：看起來是線性鋪出來的）。
- **`DEFAULT_IF`**：SP-57 第一版（`b4321071`，2026-10-05）是單一係數 0.30 × CR-10 × 分鐘，輕鬆一小時約 36 TSS、很累約 126 TSS；使用者 2026-10-07 答 SP-57 Q1「兩端不準」→ 改成每檔 IF² × 100，舉例「輕鬆 ≈ 55、很累 ≈ 90」（`996c41f6`，2026-10-08）。同日使用者拍板「很累用 session-RPE 對照取 0.95；預設是通用規則，不為任何人調」。每檔的範圍有出處，檔內取點標推估（`rpe_load.py:84-111`、`IF_SRC`）。
- 寫 `DEFAULT_IF` 時已經知道 `RPE_FRAC`：極限 1.10 的註解寫「= workout_steps.RPE_FRAC[10]」（`rpe_load.py:103`），但低端沒有對齊，也沒寫為什麼不同。SP-511 做 `rpe_if_scale` 時才被發現（SP-511 Q1 第 4 題）。

## 3. 文獻怎麼對

| 來源 | 量的是 | 說什麼 | 對 RPE 2／4／5／7 的意思 |
|---|---|---|---|
| Foster et al. 2001（出處） | session RPE，課後 30 分鐘評整堂 | 修改版 CR-10 用語：2 easy、3 moderate、4 somewhat hard、5 hard、7 very hard、10 maximal | 只給用語，不給 % CP |
| Seiler & Kjerland 2006（出處：摘要） | session RPE vs 心率區（VT1、VT2 分三區） | 用 session RPE 與用心率算的強度分布幾乎一樣（第一區 76 % vs 75 %）。程式引用的切點「≤ 4 第一區、5–6 第二區、≥ 7 第三區」（`rpe_load.py:87-88`）**在摘要裡看不到**，全文沒讀到（未找到來源；之前寫進程式時的引用照舊） | 4 ≈ VT1 以下的上緣；5–6 在 VT1–VT2；7 起在 VT2 以上 |
| Coggan power levels，TrainingPeaks（出處，已讀頁面） | **當下**的 RPE，CR-10（頁面明寫 Borg 10 分制） | L1 < 55 % RPE < 2；L2 56–75 % RPE 2–3；L3 76–90 % RPE 3–4；L4 91–105 % RPE 4–5；L5 106–120 % RPE 6–7；L6 > 121 % RPE > 7 | 2 ≈ 0.56–0.65；4 跨 0.76–1.05；5 ≈ 1.0；7 ≈ 1.1–1.2。自行車、當下的評分；整段 20–60 分的主課撐不住 L5，所以 session 評分不能直接套（`rpe_load.py:91-92` 已寫） |
| Dantas et al. 2015（出處：摘要） | 跑者 95 人、跑步機漸增測驗**每一級當下**的 CR-10 | RPE = 1.092 × 乳酸 + 2.143；2 mmol 時 RPE 4.3（3.9–4.7），4 mmol 時 6.5（6.0–7.1） | 4.3 ≈ 第一區上緣（≈ 80 % CP，推估：取 Palladino）；6.5 ≈ 第二乳酸閾（≈ 90–95 % CP，推估）；照迴歸，RPE 2–3 在 1 mmol 上下，是很輕鬆的強度 |
| 跑步功率區（Stryd；Palladino）（出處：摘要／程式） | % CP | Stryd 輕鬆 65–80 % CP（從 Stryd 說明文的例子反推，未讀到官方表）；Palladino 三區 < 80 % ／ 80–95 % ／ ≥ 95 %（`zones.py:32`） | 輕鬆跑 ≈ 0.65–0.80；app 的錨點 0.78（`EASY_F`） |

把兩張表放進來看（推估）：

| RPE | 文獻範圍（推估合併） | `DEFAULT_IF` | `RPE_FRAC` |
|---|---|---|---|
| 2 | 0.60–0.78（Coggan 下半偏低、跑步輕鬆區偏高） | 0.74 | 0.62 |
| 4 | 0.76–0.82（Dantas 4.3 ≈ LT1、Palladino 0.80、Coggan L3 上緣） | 0.82 | 0.76 |
| 5 | 0.82–0.90（Seiler 第二區、Palladino 中區） | 0.88 | 0.82 |
| 7 | 0.92–1.00（Seiler 第三區起點 ≈ CP；整段撐不住 > CP） | 0.95 | 0.94 |
| 10 | 1.05–1.15 | 1.10 | 1.10 |

**文獻定不了 ±0.05 以內的點**：兩張都在範圍裡。能定的是「該是同一張」（§0 第 1 點）和「低端要跟 app 自己的錨點對齊」（§0 第 4 點）。

**一個沒解的考量（推估）**：技術地形的手錶 TSS 本來就偏低——腳步限制速度、心率上不去（範本註解 `workout_templates.py:411-413`）。計畫 TSS 是要預測手錶記錄的負荷（負荷以手錶為準，SP-57 2026-10-04），所以「技術地形的 RPE 換算偏低一點」可能反而比較準。但 `RPE_FRAC` 寫的時候沒有這個理由，`rpe_if_scale` 也是用所有自評跑步擬合、不分地形，所以現在沒有資料能驗。

**擁有者的資料（引自 SP-511 筆記，未重讀）**：心率挑出的輕鬆平路跑 IF 中位數 0.82（8 次）；自評 RPE 的跑步一年內沒有一級滿 3 次，全期的話 RPE 1 有 5 次、RPE 2 有 3 次，`rpe_if_scale` 會是 1.07。如果 1.07 是收縮後的值（w = 8 / 18，個人值在擬合前先夾到上限 1.15，`calibrate.py:135-146`、`intensity_calib.py:41-42`），那他評 RPE 1–2 的跑步 IF ≥ 約 0.63／0.71，較接近 `DEFAULT_IF`；但大多是 2023–24 年、樣本 8 筆，**不能當證據**，只說明兩邊都不離譜。

## 4. 統一的選項

| | 怎麼做 | 優點 | 缺點 |
|---|---|---|---|
| **A（建議）** | 以 `DEFAULT_IF` 五檔為準，`RPE_FRAC` 改成五檔內插：1 0.70、2 0.74、3 0.78、4 0.82、5 0.88、6 0.92、7 0.95、8 1.00、9 1.05、10 1.10 | 有出處與拍板的那張留著；`rpe_load` 預設、本人逐檔擬合、用 RPE 填的負荷段**全部不動、不重推**；RPE 3 剛好 = `EASY_F` 0.78、RPE 4 ≈ 第一區上緣 | Coggan／Dantas 的當下 RPE 在 2–3 偏低一點；技術地形的計畫 TSS 變高（上面那個沒解的考量） |
| B | 折衷：2 0.70、3 0.76、4 0.80、5 0.86、6 0.90、7 0.95，兩張一起改（`DEFAULT_IF` 輕鬆 0.70、稍累 0.80、累 0.86） | 更貼 Dantas（4.3 ≈ LT1）與 Palladino 0.80 | 改到使用者拍板過的五檔（輕鬆 55 → 49 TSS/h）；所有用 RPE 填的負荷段重推一次；本人逐檔擬合要重新收縮 |
| C | 以 `RPE_FRAC` 為準，`DEFAULT_IF` 跟著降（輕鬆 0.62 → 38 TSS/h） | 不用改 RPE 目標的換算 | 回到 SP-57 Q1 使用者說「偏低」的那一端（單一係數時輕鬆約 36 TSS/h）；沒有出處支持 |
| D | 不統一，註明兩張用途不同 | 不用改 | 兩張用同一批自評擬合，「用途不同」說不通（§0 第 1 點） |

RPE 1 用線性外插 0.70；也可以取跑步功率輕鬆區下緣 0.65。差別只在 RPE 1 的段（現在沒有範本用到），實作時再定。

### 4.1 選 A 的影響（推估：用 IF² × 100 × 小時算，只算那一段）

| 段 | 舊 IF → 新 IF | 舊 TSS → 新 TSS | 實際會變的地方 |
|---|---|---|---|
| 技術地形 90′ 主課 RPE 6–7 | 0.910 → 0.935 | 124 → 131（+6，+5 %） | 計畫 TSS、週量 |
| RPE 5 每小時 | 0.82 → 0.88 | 67 → 77（+10） | 使用者自己寫 RPE 5–6 的段 |
| 技術地形 60′ 主課 RPE 3–4 | 0.73 → 0.80 | 53 → 64（+11，+20 %） | 只有沒給本人輕鬆負荷率時；存下來的課照本人負荷率，**不變**（§1.1） |
| 爬坡反覆上坡 RPE 4 每小時 | 0.76 → 0.82 | 58 → 67 | 同上，存下來的課不變 |
| 編輯器圖表高度 | 低 RPE 的條變高 | — | 只是畫面 |

- **手錶**：一般的 RPE 時間段推的是時間、不帶強度，不變。「負荷」步驟帶 RPE 目標（不是用 RPE＋分鐘填的那種）會用新 IF 重算時間與 TL，`sent_key` 帶 IF，所以**重推一次**；有幾張這種課要上 NAS 查（這張不讀）。
- **`rpe_if_scale`**：擬合是「實際 IF ÷ 表上的值」，表變高、本人倍率會往下移，下一次同步後重算（`calibrate` 每次同步後跑，`calibrate.py:115`）。擁有者現在是預設 1.00（SP-511），不受影響。上下限 0.85–1.15 是相對於表的，不用改。
- **`rpe_load`**：不動。`FIT_LEVEL` 兩分一檔與十分制表在 1、3、6、8、9 會差一點（例：RPE 3 在負荷擬合歸「稍累 0.82」、在 RPE 目標是 0.78），那是五檔制本身的粒度，選 A 之後差距 ≤ 0.04（原本最多 0.12）。
- **要一起改的**：`workout_editor.js:243` 的副本；`intensity_calib.py:211` 的說明「RPE 1–10 對 55–110% CP」；`estimated-constants-inventory.md` 第 157 行；加一條測試釘住「五個錨點 `RPE_FRAC[CR10[k]] == DEFAULT_IF[k]`」，之後改一張另一張會跟著提醒。最乾淨的是 `RPE_FRAC` 直接從 `rpe_load.DEFAULT_IF` 算出來（只有一份數字）。

## 5. 要使用者決定的

1. 統一方向：A／B／C／D？建議 A。
2. 要不要開實作單（維護，P3，related SP-808）：照 §4 選的方向改表與前端副本、說明文字、加錨點一致的測試，查 NAS 上「負荷＋RPE 目標」的課會重推幾張。建議開。
3. 技術地形手錶 TSS 偏低的考量（§3）要不要另開研究單，用技術地形課的實際 IF 驗證？建議先不開：現在沒有資料，`rpe_if_scale` 已經會用本人資料校正倍率；等技術地形課累積 10 堂以上再看。

## 6. 出處

- Foster C, et al. A new approach to monitoring exercise training. *J Strength Cond Res* 2001;15(1):109–115.（程式引用 `rpe_load.py:9-10`；CR-10 用語照程式）
- Seiler KS, Kjerland GØ. Quantifying training intensity distribution in elite endurance athletes: is there evidence for an "optimal" distribution? *Scand J Med Sci Sports* 2006;16(1):49–56. doi:10.1111/j.1600-0838.2004.00418.x — 摘要：https://lida.sport-iat.de/ta/Record/4009027?lng=en
- Dantas JL, et al. Determination of blood lactate training zone boundaries with rating of perceived exertion in runners. *J Strength Cond Res* 2015;29(2):315–320. doi:10.1519/JSC.0000000000000639 — 摘要：https://www.bisp-surf.de/Record/PU201503002448
- Coggan, Power Training Levels（TrainingPeaks）：https://www.trainingpeaks.com/learn/articles/power-training-levels/
- Stryd 功率區（輕鬆區的例子）：https://blog.stryd.com/2021/08/04/train-in-your-zones/ 、https://help.stryd.com/en/articles/8118219-understanding-stryd-data-and-features（摘要，官方百分比表未讀到）
- Palladino 三區：`backend/engine/zones.py:32`（repo 既有引用）
