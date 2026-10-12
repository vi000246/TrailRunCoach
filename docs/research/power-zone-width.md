# 功率目標要多寬：Palladino 子區間是幹嘛的、輕鬆跑和間歇能不能橫跨區間（SP-378）

> 調查日期：2026-10-12。只做調查，沒有改程式。程式位置以 HEAD `4da8212` 為準。
> 標記：**已驗證**＝這次讀到原始碼、本機資料或全文；**轉述**＝只讀到搜尋摘要（這次的雲端環境擋住了 Stryd、Palladino、COROS 的網頁，回 403，引號內的句子以搜尋摘要回報的原文為準）；**推估**＝我的延伸；**未找到來源**＝查過但沒有。
> 不重做的部分：區間系統的對照（`coach-schools-zones-periodization.md` §Finding 2）、閾值怎麼來（`zones-and-thresholds.md`）、間歇處方（`interval-prescription.md`）、穩定跑的功率變異（`drift-algorithm.md` §4.2）。這裡只引用結論。
> 範例數字：使用者單上的例子 CP 200 W（150–160 W），既有研究文件的範例跑者 CP 220 W。
> 2026-10-12 決定（使用者，SP-378 問答 Q1）：§7 五題都照建議。已實作：路跑輕鬆跑 65–80% CP、長跑 65–88%，間歇處方不動、推到手錶的上限改成判定用的 × 1.05（Zone 5 到 116%），編輯器的輕鬆段功率從 65–80% 起；編輯器「從–到」區間另開單；SP-363 降優先。

## 摘要

1. **10 W 是 app 自己選的，不是 Palladino 或 Stryd 的處方。** 路跑輕鬆跑的功率目標被定在 Palladino 的**子區間 1C**（75–80% CP），5% CP 在 CP 200 就是 150–160 W（`backend/engine/zones.py:394`、`backend/engine/workout_steps.py:693`、`backend/sync/coros_workouts.py:217`）。長跑是 Z2（80–88%，16 W）。已驗證。
2. **1A／1B／1C 是用途標籤，不是三條要分別跑進去的帶。** Palladino 2017 的表把 Zone 1「Easy Running」（50–80% CP）分成 1A 間歇間恢復、1B 暖身、1C 輕鬆有氧跑，整張表自述是「descriptive, not prescriptive」（描述，不是處方）；他另一處說「大多數跑者的輕鬆跑落在 75–80% FTP 附近」，這是觀察到的中心，不是帶寬。Stryd 自己的 Zone 1 是 **65–80% CP**（寬 15%），例子：CP 252 W 的 Zone 1 是 164–202 W，「輕鬆跑約 190 ± 5 W、恢復跑約 170 ± 5 W，找你覺得輕鬆的那個數字」。轉述。
3. **生理上 Zone 1 整段是同一區。** 這位跑者的 LT1／AeT 大約在 1C 和 Z2 的交界（約 80% CP，`coach-schools-zones-periodization.md` 表 2b；推估），80% 以下都是「中等強度域」，Seiler 三區的 Zone 1、Daniels 的 E（59–74% VO2max 一整段）、Uphill Athlete 的「AeT 以下」都是一整段，沒有哪一派要求輕鬆跑壓在 5% 內。
4. **用你自己的資料算：±5 W 本來就跑不進。** 沒有走路段的輕鬆跑，30 秒功率的 CV 中位數 9.2%（p90 12.1%；`drift-algorithm.md` §4.2，28 次通過的跑步 7.8%），在 155 W 就是 SD ≈ 14 W。就算整趟平均**剛好**落在 155 W，30 秒樣本也只有 **27%** 在 150–160 W 內；改成 65–80%（130–160 W）是 74%，65–88%（130–176 W）是 90%（§3，推估：常態近似）。手錶的提醒看的是連續幾秒在帶外（COROS 跑 Stryd 課表的說明，轉述），所以窄帶等於一路被提醒。
5. **課後的判定其實沒有下限。** 輕鬆跑「偏強」只看平均功率 > 80% CP（`backend/engine/adapt.py:91`），沒有任何規則說「太慢」。所以「跑不進目標」只發生在手錶上和課表頁的文字上，不影響課表評分、也不影響自動調整。已驗證。
6. **間歇的帶寬也是 6–12 W，但 app 判定用的帶比推到手錶的寬。** 課表庫每個變體的帶是 5–7% CP（例如 3×7 分近閾值 97–100%＝194–200 W，只有 6 W）；判定卻是「每趟平均 ≥ 0.98 × 下限」和「10 秒功率在 [下限, 上限 × 1.05] 的時間 ≥ 計畫的 85%」，Zone 5 連上限都不看（`backend/engine/interval_eval.py:15`、`:95`）。也就是說手錶在罵你的時候，app 事後可能判你達標。建議手錶推送改用判定用的帶，處方本身不動（§4）。
7. **建議（§5）**：路跑輕鬆跑改成 **Zone 1 整段 65–80% CP**（＝ Stryd Zone 1 ＝ Palladino 1B＋1C），長跑 **65–88%**（Z1B–Z2，說明寫「建議 75–88%」），恢復跑維持只有上限；心率上限照舊。間歇不改處方，手錶上限改成判定用的 `上限 × 1.05`、Zone 5 用區間上緣 116%。編輯器的「區間」模式加「從–到」（1B–2）。這些要你拍板，問題在 §7。研究單不實作。

## 1. app 現在怎麼做

### 1.1 區間表

Palladino 跑步功率 10 區（`backend/engine/zones.py:17-29`），全 app 只用這一套（擁有者 2026-10-02 決定，`zones.py:33-35`）。已驗證。

| 區 | 名稱（原文） | % CP | CP 200 | CP 220 | 寬（CP 200） |
|---|---|---|---|---|---|
| 1A | Post-interval recovery | 50–65 | 100–130 | 110–143 | 30 W |
| 1B | EZ warm-up | 65–75 | 130–150 | 143–165 | 20 W |
| **1C** | **EZ aerobic** | **75–80** | **150–160** | **165–176** | **10 W** |
| 2 | Endurance / long run | 80–88 | 160–176 | 176–194 | 16 W |
| 3A | Threshold stimulus – extensive | 88–95 | 176–190 | 194–209 | 14 W |
| 3B | Threshold stimulus – intensive | 95–101 | 190–202 | 209–222 | 12 W |
| 4 | Supra-threshold | 101–106 | 202–212 | 222–233 | 10 W |
| 5 | Maximal aerobic power | 106–116 | 212–232 | 233–255 | 20 W |
| 6 | Anaerobic power | 116–150 | 232–300 | 255–330 | — |
| 7 | Sprint | > 150 | > 300 | > 330 | — |

### 1.2 課表拿哪一格當目標

`zones.WORKOUT_TARGETS`（`backend/engine/zones.py:391-412`）是課表頁、COROS 推送、步驟結構共用的一張表。已驗證。

| 課 | 功率 % CP | CP 200 | 來源欄寫的 | 心率 |
|---|---|---|---|---|
| 恢復跑 | 只有上限 75 | < 150 W | Palladino 1A–1B；Friel Z1 | ≤ 85% LTHR |
| **輕鬆跑（z2）** | **75–80** | **150–160 W** | Palladino 1C；Uphill Athlete AeT | ≤ 輕鬆跑上限 |
| 長跑（路跑） | 80–88 | 160–176 W | Palladino Z2 | ≤ 輕鬆跑上限 |
| 山路長天／越野輕鬆 | 75–88（只當參考） | 150–176 W | — | ≤ 輕鬆跑上限（主） |
| 閾值（near） | 95–101 | 190–202 W | Palladino 3B | 95–100% LTHR |
| Supra | 101–106 | 202–212 W | Palladino Z4 | — |
| VO2max | 106–116 | 212–232 W | Palladino Z5 | — |

- 路跑的輕鬆跑和長跑**看功率、心率當上限**（`backend/engine/target_policy.py:98-104`，擁有者 2026-10-02：「功率沒有延遲、只要 CP」）。越野的輕鬆跑、山路長天看心率。
- 課表頁每堂課的目標文字由 `overview._targets`（`backend/engine/overview.py:826-842`）拼出：「功率 150–160 W · 心率 < 142 bpm」。
- 步驟結構：`workout_steps._easy_items`（`backend/engine/workout_steps.py:669-694`）把輕鬆跑寫成一個 `intent: easy, plo 0.75, phi 0.80` 的步驟；長跑 0.80–0.88；山路長天、健行 0.75–0.88；馬拉松配速長跑的輕鬆段 0.80–0.88、收操 0.75–0.80。
- 編輯器裡一個步驟只能選**一個**區（`backend/static/workout_editor.js:537` 的單選 `<select>`；新步驟預設 work＝3A、其餘＝1C，`:413`），沒有「1B–2」這種跨區選法。要跨區只能改用 pct 模式手打 65–80。

**心率那一邊寬多了**：同一堂輕鬆跑的心率帶是課表心率區間模型的 Z2（COROS 預設 80–90% LTHR，LTHR 160 就是 128–144 bpm，16 bpm），而且 `plan_hr_zones` 保證至少 10 bpm 寬（`backend/engine/hr_profile.py:330`）。功率帶 5% CP、心率帶 10% LTHR，兩邊的寬度標準不一致。已驗證。

### 1.3 推到手錶時長什麼樣

- `coros_workouts.easy_target`（`backend/sync/coros_workouts.py:217-227`）預設 `(0.75, 0.80)`，長跑 `(0.80, 0.88)`，山路長天、健行 `(0.75, 0.88)`（`:497`）。
- COROS 的步驟強度是整數的 `intensityValue`／`intensityValueExtend`（`:559-566`），**一定要有下限和上限**；app 沒有「只有上限」的功率寫法（心率的「只有上限」是用 0.75 × LTHR 當下限湊的，`:173`）。已驗證。
- 手錶上的行為（轉述，Stryd 2025-03 關於 COROS 跑 Stryd 課表的說明、COROS 支援中心「structured workouts 的 activity alerts」）：功率顯示藍／綠／紅＝低於／在／高於目標；「在目標範圍外持續一段時間」才響。用哪一種平均（3 秒、10 秒）**未找到來源**。
- 使用者自己的提議（SP-61，2026-10-04，已取消）：越野課只設上限、不設下限，理由是「設了下限手錶會一直提醒太低」。同一個問題，這次是路跑的輕鬆跑。

### 1.4 課後怎麼判

| 課 | 判什麼 | 程式 |
|---|---|---|
| 輕鬆跑「偏強」 | 平均功率 > 80% CP，或 TSS > 計畫 +20%；沒有功率才看平均心率 > 94% LTHR。**沒有下限** | `backend/engine/adapt.py:89-151`（SP-301） |
| 輕鬆跑的穩定度（飄移測試用） | VI ≤ 1.04、後半功率和前半差 ≤ 5%。這是「算不算穩定跑」，不是「有沒有跑進目標」 | `backend/engine/workout_review.py:1086-1110` |
| 間歇每趟 | 平均功率 ≥ 0.98 × 計畫下限（個人化 0.95–0.99） | `backend/engine/interval_eval.py:15`、`:283`；`interval_calib.py` |
| 間歇目標區時間 | 10 秒功率在 [下限 × CP, 上限 × CP × 1.05] 的秒數 ≥ 計畫的 85%；**Zone 5 不看上限** | `interval_eval.py:64-67`、`:95` |
| 達成度顏色 | 時間和 TSS 各 ±20% 綠 | `backend/engine/compliance.py:31` |

結論：**app 從頭到尾沒有一條規則要求輕鬆跑的功率在 150–160 內**。窄帶只活在課表文字和手錶上。已驗證。

## 2. 這些區間是幹嘛的

### 2.1 Palladino 的設計意圖

| 說法 | 出處 | 標記 |
|---|---|---|
| Zone 1「Easy Running」50–80% FTP/CP，分 1A「Post Interval Recovery：間歇之間的恢復和緩和」50–65、1B「warm-up：間歇或比賽前的暖身」65–75、1C「easy aerobic runs」75–80 | Palladino Power Project, Running Power Zones 2017（Google Doc） | 轉述（搜尋摘要逐字） |
| Zone 2「Endurance / long run」> 80 到 < 88% | 同上 | 轉述 |
| Zone 3 分 3A／3B 是「為了讓跑者留在 95% CP 以下」 | 第三方評論（搜尋摘要） | 轉述 |
| 整張表「descriptive, not prescriptive」 | `coach-schools-zones-periodization.md` 表 2b [23] | 已驗證（本 repo 文件） |
| 「大多數跑者的輕鬆跑落在約 75–80% FTP」 | Palladino 訪談筆記（搜尋摘要） | 轉述 |

讀法：1A／1B／1C 的分法在講**一堂課裡不同片段的用途**（恢復、暖身、主段），不是三個要分別「跑進去」的目標。Zone 1 之所以還要分，是因為時間分布圖（time in zone）要能看出「這週的 Zone 1 時間有多少是真的在跑、有多少是間歇之間的恢復」。app 的區間表、單次活動的時間分布圖（`palladino_rows`，`zones.py:41-47`）正是這個用途。推估。

「75–80%」作為輕鬆跑的**中心**是合理的：這位跑者的 LT1 大約在 80% CP（§2.3）。問題是 app 把中心當成了帶。

### 2.2 Stryd、80/20 等其他系統怎麼開輕鬆跑

| 系統 | 輕鬆跑的帶 | 寬 | 標記 |
|---|---|---|---|
| Stryd 5 區 | Zone 1「Easy」65–80% CP：「easy runs, long runs, and other base mileage」；例子 CP 252 → 164–202 W，「輕鬆跑約 190 ± 5 W、恢復跑約 170 ± 5 W」、「找你覺得輕鬆的那個數字」；Zone 1「在跑步強度上是可變的」 | 15% | 轉述（Stryd 說明中心、2021 部落格） |
| 80/20（Fitzgerald）功率 | Z1 50–76、Z2 76–88；有使用者反映「Zone 1 很難待在裡面」（Garmin 論壇） | Z2 12% | `coach-schools` 表 2b [20]；論壇轉述 |
| Daniels | E 配速 59–74% VO2max，一整段；「大多數週跑量落在這段」 | 15% VO2max | 轉述（二手） |
| Seiler 三區 | Zone 1 ＝ LT1 以下全部 | 開放 | `coach-schools` Finding 1 |
| Uphill Athlete | AeT 以下全部，只有上限 | 開放 | `zones-and-thresholds.md` §1.3 |
| Coggan（自行車） | L2 56–75% FTP | 19% | `coach-schools` 表 2b [8] |

沒有一套系統把輕鬆跑開在 5% 內。Stryd 例子裡的「± 5 W」是在說「你大概會落在這附近」，而且是放在一個 38 W 寬的區裡。

### 2.3 生理上為什麼不用分

- LT1（AeT）以下是「中等強度域」：乳酸不堆積、心率和攝氧穩定，從 60% 到 LT1 的適應方向一樣，差的是每分鐘的量（`coach-schools` Finding 1；`aerobic-base-readiness.md`）。
- 這位跑者的 LT1 位置：心率上 AeT ≈ 0.89 × LTHR（沒有實測；`zones-and-thresholds.md` §1.2），功率上 `coach-schools` 表 2b 把 ▲ 放在 1C／2 交界（約 80% CP，推估）。所以 **80% 是有意義的上限**，75% 不是有意義的下限。
- 下限有意義的只有一種情況：你想確保「有跑到一定的量」（例如長跑想維持 Z2 的刺激）。那是**說明**該講的事，不是手錶該提醒的事。推估。

### 2.4 為什麼窄帶量不出來

| 誤差來源 | 大小 | 出處 |
|---|---|---|
| 30 秒功率在穩定輕鬆跑裡的變異 | CV 中位 9.2%、p90 12.1%（沒走路段的 11 次）；通過飄移測試的 28 次 7.8%／11.0% | `drift-algorithm.md` §4.2 表（本機資料） |
| 2 分鐘平均的變異 | CV 中位 5.3%、p90 8.6% | 同上 |
| 5 分鐘區塊的變異 | 2.5%／6.4% | 同上 |
| CP 本身的誤差 | 20 分測試 SEE 約 3%（CP 200 → ± 6 W）；12 分段少跑 10 W → CP 低估 13 W | `cp-test-protocols.md` §0 #5、:64 |
| Stryd 在越野、坡道的功率可靠度 | 平地可靠；上下坡 CV 0.13 | Navalta 2019、Ziegler 2022（轉述） |

5% CP 的帶（± 2.5% CP）比 CP 的測量誤差（± 3%）還窄，比 30 秒功率的一個標準差（≈ 9%）窄得多。這條帶在物理上就不是「跑得進去」的東西。

## 3. ±5 W 跑不跑得進：用你的數字算

常態近似：30 秒功率 ~ N(帶中點, CV × 中點)。平均**剛好**在帶中間（最好的情況），在帶內的 30 秒樣本比例：

| 帶 | CP 200 | CV30 7.8%（通過組中位） | 9.2%（輕鬆跑中位） | 12.1%（輕鬆跑 p90） |
|---|---|---|---|---|
| 1C 75–80 | 150–160 W（± 5） | 32% | **27%** | 21% |
| Z2 80–88 | 160–176 W（± 8） | 46% | 40% | 31% |
| 1C＋2 75–88 | 150–176 W（± 13） | 69% | 61% | 49% |
| **Z1 65–80（Stryd Zone 1）** | 130–160 W（± 15） | 82% | **74%** | 61% |
| 1B–2 65–88 | 130–176 W（± 23） | 95% | **90%** | 79% |

CP 220 的結果一樣（比例只看 CV）。推估（常態近似、平均剛好置中；實際平均偏一邊時更差）。

所以：

- 150–160 這條帶，就算你跑得完美，手錶上也有七成時間是藍或紅。
- 65–80% 是「大多數時間綠、偶爾藍」；65–88% 是「幾乎都綠」。
- 這和 SP-61 你自己的觀察一致：「設了下限，手錶會一直提醒太低」。

## 4. 間歇：能不能橫跨區間

### 4.1 現在的帶

課表庫（`backend/engine/interval_library.py:123-234`）每個變體有自己的帶，比 Palladino 的區窄：

| 變體 | 帶 % CP | CP 200 | 寬 | 判定時實際的帶（`interval_eval`） |
|---|---|---|---|---|
| t1a 3×6 分閾值 | 90–95 | 180–190 W | 10 W | 每趟平均 ≥ 176（0.98 × 180）；TIZ 180–200（× 1.05） |
| tpa 3×7 分近閾值 | 97–100 | 194–200 W | **6 W** | ≥ 190；TIZ 194–210 |
| a1a 2×15 分 | 88–95 | 176–190 W | 14 W | ≥ 172；TIZ 176–200 |
| v1a 5×2 分 Z5 | 106–112 | 212–224 W | 12 W | ≥ 208；**沒有上限** |
| v2a 4×3 分 Z5 | 105–110 | 210–220 W | 10 W | ≥ 206；沒有上限 |
| v4a 4×4 分 Z5 | 104–108 | 208–216 W | 8 W | ≥ 204；沒有上限 |

已驗證。

### 4.2 為什麼間歇不能像輕鬆跑那樣整段開

- 3A／3B／4／5 不是同一種刺激：3A（88–95）是「可以連續 20–40 分的閾值下」、3B（95–101）是閾值本身、Z4（101–106）是超閾值、Z5（106–116）是最大有氧功率，每趟能撐的時間差一個數量級（`interval-prescription.md` Part A；`coach-schools` Finding 3）。把 3×7 分開成「88–116%」等於沒開。
- 但**趟平均**比 30 秒功率穩得多：2 分鐘平均的 CV 中位 5.3%（§2.4），一趟 3–7 分的平均更穩。6–10 W 的帶對趟平均是合理的目標，對手錶上跳動的 3 秒功率不是。
- 判定已經把這點考慮進去了：下限打 0.98 折、上限放 5%、Z5 不看上限（因為 Z5 課的目的是「撐在 MAP 以上」，衝太高是配速問題不是沒練到，`interval-prescription.md` §B3）。

### 4.3 建議

1. **處方不改**：變體的帶就是課的目的，維持。
2. **手錶推送改用判定用的帶**：下限不動（手錶上低於下限時該提醒），上限改成 `上限 × 1.05`，Zone 5 改成區間上緣 116%（或乾脆用 Palladino Z5 上緣，COROS 一定要有數字）。這樣手錶的紅燈和 app 的判定一致。推估。
3. 課表文字加一句「看每趟的**平均**功率，3 秒功率上下跳是正常的」。
4. 「3 區 → 5 區之間的過渡（Z4 101–106）」要不要獨立開課，是 `interval-prescription.md` 的題目，這裡不碰。

## 5. 輕鬆跑、長跑的設計建議

原則：**上限是生理的（AeT／80% CP），下限只是「不要慢到走」**；帶要寬到大多數 30 秒樣本在裡面，中心仍是 75–80%。

| 課 | 建議的功率目標 | 文字 | 理由 |
|---|---|---|---|
| 路跑輕鬆跑 | **65–80% CP**（CP 200：130–160 W） | 「功率 130–160 W（Zone 1；多數人落在 150–160）· 心率 < 142」 | ＝ Stryd Zone 1 ＝ Palladino 1B＋1C；70–80% 的 30 秒樣本在帶內（§3）；上限和「偏強」的判定線 80% 一致 |
| 路跑長跑 | **65–88%**（130–176 W），文字寫「建議 75–88%」 | 「功率 130–176 W（Z1–Z2；長跑建議 150–176）」 | 長跑中段走到 Z2 是 Palladino 的用法；後段掉到 1B 不該被提醒；90% 樣本在帶內 |
| 馬拉松配速長跑的輕鬆段、收操 | 同長跑、同輕鬆跑 | — | `workout_steps.py:673-675` |
| 恢復跑 | 維持只有上限 75%（COROS 要下限：用 1A 下緣 50%） | — | `zones.py:393` |
| 山路長天、健行、越野輕鬆 | 不動（心率為主，功率只參考 75–88） | — | `target_policy.py` |
| 熱適應跑 | 不動（心率） | — | — |

替代方案（如果你覺得 65% 太低）：**70–80%**（140–160 W，62% 樣本在帶內，推估）。不建議「保留 75–80 只拿掉下限」：COROS 一定要兩個數字，而且 1C 的下限本來就沒有生理意義，與其湊一個數字不如用 Zone 1 的 65。

### 5.1 心率上限的角色不變

功率帶變寬不代表可以跑快：上限 80% 沒動，心率 ≤ 輕鬆跑上限照推（`target_policy.py` 的 `hr_cap`），天熱、疲勞時心率先到就放慢的規則不變。

### 5.2 編輯器

「區間」模式現在一個步驟一格（`workout_editor.js:537`）。建議加「從–到」（例如 1B–2），存成 `{"mode": "zone", "zone": "1B", "zone_to": "2"}`，解析時取第一格的下限和第二格的上限（`workout_steps._zone_of`，`:970`）。這是獨立的功能單。推估。

## 6. 真要改的話會動到哪裡（給之後的功能單，這次不做）

| 檔案 | 行 | 改什麼 |
|---|---|---|
| `backend/engine/zones.py` | 394–395 | `z2` 的 plo 0.75 → 0.65；`long` 0.80 → 0.65（或 0.75）；來源欄改寫「Palladino Z1（1B–1C）；Stryd Zone 1」 |
| `backend/engine/workout_steps.py` | 673–675、677、690、693 | `_easy(0.75, 0.80)` → `(0.65, 0.80)`；長跑 `(0.80, 0.88)` → `(0.65, 0.88)` |
| `backend/sync/coros_workouts.py` | 217、493、497 | `easy_target` 預設 frac 與長跑的 frac 同上 |
| `backend/sync/coros_workouts.py`、`workout_steps.py` | 253、1060 附近 | 間歇的推送上限 × 1.05、Z5 用 1.16（§4.3） |
| `backend/engine/target_policy.py` | `SRC["power_easy"]`、`TIP` | 文字：「Palladino Z1–Z2」改成寫清楚 65–80 / 65–88 |
| `backend/static/workout_editor.js` | 413 | 新步驟預設區 1C → 1（或新的 1B–1C） |
| `backend/engine/adapt.py` | 91 | `EASY_POWER_CAP` 0.80 不動 |
| `backend/engine/plan_auto.py` | 251 | `rescale_watts` 照樣能改寫「130–160 W」，不用動 |
| 測試 | `backend/tests/test_workout_steps.py`、`test_coros_workouts.py` | golden payload 的數字 |
| 規格 | `docs/spec/workouts.spec.md`、`plan-auto.spec.md` | 目標表 |

沒有資料庫遷移：相對目標存的是 % CP，已存的課在下一次 push 時用新的帶重算（`plan_auto` 的 fingerprint 會標「已過期」）。

## 7. 要你決定的

1. 路跑輕鬆跑的帶：**65–80%**（建議）／70–80%／保留 75–80 只改文字。
2. 路跑長跑的帶：**65–88%**（建議，文字寫「建議 75–88」）／75–88／保持 80–88。
3. 間歇：**處方不動、手錶上限改成判定用的 × 1.05、Z5 用 116%**（建議）／全部不動。
4. 編輯器要不要加「從–到」的區間選法（獨立功能單）。
5. SP-363（WKO5 iLevel、Stryd 5 區可切換）要不要併進來：如果輕鬆跑改成 65–80，等於 Stryd Zone 1，做 Stryd 5 區的誘因變小。

## 8. 來源

本 repo：

- `docs/research/coach-schools-zones-periodization.md` 表 2b（Palladino [23]「descriptive, not prescriptive」、Stryd [24]、80/20 [20]）、Finding 1（LT1 以下同一區）。
- `docs/research/drift-algorithm.md` §4.2 表（30 秒／2 分／5 分功率 CV，本機 189 次跑步）。
- `docs/research/zones-and-thresholds.md` §1.3（心率上限）、§1.2（CP 220 的由來）。
- `docs/research/cp-test-protocols.md`（CP 誤差）。
- `docs/research/interval-prescription.md` Part A、§B3（間歇帶、TIZ）。
- Obsidian 單 SP-61（越野只設上限，2026-10-04 取消）、SP-301（偏強只看功率上限）、SP-363（iLevel／Stryd 區間）。

外部（只讀到搜尋摘要，轉述）：

- Palladino Power Project, *Running Power Zones 2017*（Google Doc）：Zone 1 50–80 分 1A／1B／1C；Zone 2 > 80–< 88。https://docs.google.com/document/d/e/2PACX-1vTHqzlWwp2Dp6f1cMlS45PycEf-hCAjy61KXG7fRoR2e4mxDyWH6gXo5ZnIvj5b9cTBWBcj9kcfJHel/pub
- Stryd 說明中心〈Power Zones〉：Zone 1 65–80%，CP 252 例子 164–202 W、輕鬆跑 190 ± 5、恢復 170 ± 5。https://help.stryd.com/en/articles/6879348
- Stryd 部落格〈Train in Your Zones〉2021-08-04：Easy zone「variable in running intensity」。https://blog.stryd.com/2021/08/04/train-in-your-zones/
- Stryd 部落格 2025-03-07〈Execute Your Stryd Power-Based Workouts Natively on COROS〉：藍／綠／紅、在帶外持續一段時間才響。https://blog.stryd.com/2025/03/07/new-update-for-coros-watches-execute-your-stryd-power-based-workouts-natively/
- COROS 支援中心〈Activity alerts in structured workouts〉。https://support.coros.com/hc/en-us/articles/11219392861076
- Stryd 部落格〈Stryd Power Averaging〉2017：3／10／30 秒、圈平均各自的用途。https://blog.stryd.com/2017/02/27/stryd-power-averaging/
- Navalta et al. 2019, *Int J Sports Med*（越野走跑 Stryd 可靠度，CV < 10%）；Ziegler & Cowley 2022（上下坡功率 CV 0.13）。
- Daniels E 配速 59–74% VO2max：二手整理（athletedata.health、coachray.nz）。
