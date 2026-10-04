# CTL ramp 門檻校準：我們的 CTL 跟 Friel／TrainingPeaks 的是同一把尺嗎

> 2026-10-04，SP-63。只做分析，不改程式碼。
>
> 標記沿用 `unsourced-rules.md`：**已驗證**（本次讀過原文）、**未驗證**、**未找到來源**、**推估**；外部說法標 **同儕審查** 或 **教練／平台**。已在 `unsourced-rules.md` §B2、`detraining.md`、`docs/wko5-internals/formulas.md`、`uphill-athlete-mountain-metrics.md` 查過的來源直接引用，不重查。
>
> 實測只用一位範例跑者（越野＋路跑＋百岳，約兩年有心率的資料），個人數字不寫進 repo，只寫比例與範圍。

---

## 結論先講

1. **公式一樣，尺不一樣。** 我們的 CTL／ATL 公式、時間常數、ramp 定義都和 TrainingPeaks／WKO5 相同（§1.1、§2.1）。問題出在**每筆活動的 TSS**：同一筆跑步，hrTSS 比功率 TSS 高約 25 %（路跑）到 70 %（越野）；健行、肌力在多數設定下記 0；設定（parity 或山岳預設）一換，同一份訓練的 CTL 水準差 30–50 %（§3）。
2. **ramp 用絕對點數，會跟著 TSS 的尺一起放大縮小。** TSS 全部乘上 k，CTL 和 ramp 也都乘上 k；只有「ramp ÷ CTL」不變。範例跑者同一份訓練，5 注意／8 擋的觸發次數會因為 TSS 算法不同差 4–6 倍（§3.2）。
3. **Friel 的 5–8 原本就不是警戒線**，是「多數人合適的加量速度」，10 以上才是「只能撐一週」（§2.2）。我們拿 5 當注意、8 當擋，比原意保守。對跑步來說保守有理由（機械負荷，§2.4），但數字要換成跟 TSS 尺度無關的寫法。
4. **建議**：ramp 改成相對值：**注意 ΔCTL₇ ≥ max(3, 10 % × CTL₋₇)、擋 ≥ max(5, 15 % × CTL₋₇)**（推估，§4.1）；CTL 不滿 42 天的資料時不看 ramp；週量增幅改成「跑步時間對 max(上週, 前 4 週平均)」（§4.3）。需要改的程式列在 §5。

---

## 1. 我們這邊：CTL 怎麼算

### 1.1 CTL／ATL 公式

| 項目 | 實作 | 錨點 |
|---|---|---|
| 遞迴式 | `v = v + (x_d − v) / const`，線性的 1/42，不是 1 − e^(−1/42)；跟 WKO5 反組譯結果一致 | `backend/engine/wko5expr/evaluator.py:2453`、`docs/wko5-internals/formulas.md:116` |
| 時間常數 | CTL 42、ATL 7（athlete 設定；FIT 來源寫死 42／7） | `backend/engine/wko5expr/fitdataset.py:474` |
| 每日彙總 | 同一天所有活動的 TSS 相加；單筆 < 0 或 > 5000 丟掉 | `backend/engine/wko5expr/evaluator.py:2426` |
| 起算 | 從資料集第一個活動日、v = 0 開始（沒有 seed） | `backend/engine/wko5expr/evaluator.py:514` |
| TSB | shift(CTL − ATL, 1)：昨天的 CTL − ATL | `backend/engine/wko5expr/evaluator.py:757` |
| 運動範圍 | 全部活動，不分運動（Evaluator 沒有 sport 篩選） | `backend/engine/wko5expr/evaluator.py:516`、`backend/engine/status.py:132` |

線性寫法和指數寫法只差在係數 1/42 = 0.02381 對 1 − e^(−1/42) = 0.02353（差 1.2 %）。範例跑者兩年的 CTL 最大相對差 0.9–1.6 %，**可忽略**。GoldenCheetah 用的是指數寫法（§2.1），兩者的 ramp 可以直接比。

### 1.2 每筆活動的 TSS

優先順序（`backend/engine/wko5expr/dataset.py:648-669`）：

1. **功率 TSS** = NP² × 秒數 ÷ (FTP² × 36)（`backend/engine/wko5expr/dataset.py:654`）。條件：有 NP、有 FTP、不是手錶估算功率（手錶功率預設不算功率 TSS，`backend/engine/wko5expr/dataset.py:648`）。FTP：parity 模式用設定裡的 FTP；COROS／TP 來源非 parity 時用 CP（計畫測試 → DB → Stryd PD 模型推估，`backend/engine/wko5expr/fitdataset.py:996`）。
2. **rTSS**（只限跑步，要有門檻配速）= (分鐘)^1.025 × IF² ÷ 60 × 100（`backend/engine/wko5expr/dataset.py:661`）。這是 WKO5 的寫法：1 小時在門檻 = 111，不是 100；2 小時 = 225，不是 200。COROS 來源目前多半沒有門檻配速，實際上很少走到這支。
3. **TrainingPeaks 同步的 TSS**（只有 WKO5 資料夾有），否則 **hrTSS**（`backend/engine/wko5expr/dataset.py:669`）。

hrTSS 是 WKO5 的分段表：每個心率樣本依 LTHR 比例給每小時 20–140 TSS（`backend/engine/algorithms/wko5_hr.py:22-24`）。最低兩段沒有下限：靜止心率也有每小時 20–30。

非 parity 時有兩個調整（`backend/engine/wko5expr/config.py:136` 的 MOUNTAIN_PRESET）：

- **只算移動時間的 hrTSS**（`backend/engine/wko5expr/dataset.py:558`）：多日行程不會把睡覺時間算進去。
- **Uphill Athlete 爬升加成**：每 1,000 ft 加 10 TSS，只加在 hrTSS 來源的越野、健行（`backend/engine/wko5expr/dataset.py:545`；UA 原文見 `docs/research/uphill-athlete-mountain-metrics.md:32`）。

### 1.3 哪些運動進得了 CTL

公式上全部都算，但 **hrTSS 要有那個運動的 LTHR**：設定名稱是 `<sport>thr`，跑步 `runthr`、單車 `bikethr`，健行和肌力落在 `otherthr`（`backend/engine/wko5expr/dataset.py:620`）。

- 賽季計畫裡有 dated LTHR 時，所有運動的 `*thr` 都用它（`backend/engine/wko5expr/dataset.py:584`），健行和肌力就有 hrTSS。
- 只有 FIT 推估時，推估只寫 `runthr`（`backend/engine/wko5expr/fitdataset.py:817`）。這時**健行、肌力、單車都是 0 TSS**。範例跑者兩年的百岳和健行完全不進 CTL；多日百岳就算只算移動時間，一趟也有好幾百 TSS。

另外，`hr_tss_zone1_floor`（低於 0.70 × LTHR 記 0）有設定、有寫進 MOUNTAIN_PRESET，但**程式沒有讀它**（`backend/engine/wko5expr/config.py:82`）。

**哪個模式是預設？** 沒有 engine.json 時，`parity = wko5_available()`（`backend/engine/wko5expr/config.py:125`）。有 WKO5 資料夾的人預設是 parity（沒有移動 hrTSS、沒有爬升加成）；只有 COROS／TP 的人預設 `parity = False`，但用的是 dataclass 預設值，**不是** MOUNTAIN_PRESET（移動 hrTSS 和爬升加成預設都關）。

### 1.4 「ramp」怎麼算、誰在用

| 用途 | 定義 | 錨點 |
|---|---|---|
| ramp | `CTL(今天) − CTL(今天 − 7)`，滾動 7 天，含今天已做的活動；不是週一對週一 | `backend/engine/status.py:289-293` |
| 狀態卡 | ≥ 8 紅、≥ 5 黃、1–5 綠（`RAMP`） | `backend/engine/status.py:46` |
| 間歇護欄 | ≥ 5 只排閾值下、≥ 8 不排間歇 | `backend/engine/quality_gate.py:102`、`:621`、`:1034` |
| 自動調整 E | ≥ 8 拿掉強度課、輕鬆分鐘 × 0.8（re-entry 除外） | `backend/engine/adapt.py:76`、`:400`；ramp 來自 `backend/api/plan_sessions.py:162` |
| 排課目標 | 基礎期每週 +3、專項期 +4（點數），換成時數，上限上週／4 週平均 +10 % | `backend/engine/overview.py:355`、`:789`；`backend/engine/projection.py:88-89` |

三個地方（status、quality_gate、adapt）各寫一份 5／8 的常數。沒有「資料不滿 42 天不看 ramp」的保護（`backend/engine/status.py:288-320`）。

### 1.5 週量增幅

`step = (上週 − 上上週) ÷ 上上週`。單位是**全部運動的移動時數**（沒有移動時間就用總時間），週一到週日（`backend/engine/status.py:180`、`:364`）。> 20 % 擋間歇、10–20 % 維持劑量（`backend/engine/quality_gate.py:103`）。沒有分運動，也沒有排除計畫內的恢復週。

---

## 2. 他們那邊：教練和平台的定義

### 2.1 CTL／ATL／ramp 的定義（平台）

- **TrainingPeaks**：CTL 是每日 TSS 的指數加權平均，42 天；ATL 7 天（**已驗證**，平台：https://www.trainingpeaks.com/learn/articles/what-is-the-performance-management-chart/ ）。
- **Coggan（TrainingPeaks／WKO 的作者）**：CTL／ATL 是「exponentially-weighted moving average of daily TSS」，預設 42／7；沒有歷史資料時，用一般人 50–75 TSS/h 的量級給初始值，CTL = ATL（**已驗證**，平台：https://www.trainingpeaks.com/learn/articles/the-science-of-the-performance-manager/ ）。
- **WKO5**：`tl()` 是線性的 `v += (x − v)/c`、從 0 起算（反組譯，`docs/wko5-internals/formulas.md:116-128`）。我們照抄這個。
- **GoldenCheetah**：`lts = stress × (1 − e^(−1/42)) + lts₋₁ × e^(−1/42)`；ramp rate = 最近 7 天每天 CTL 增量的和，也就是 CTL(d) − CTL(d−7)（**已驗證**，開源：https://github.com/GoldenCheetah/GoldenCheetah/blob/master/src/Metrics/PMCData.cpp ）。
- **intervals.icu**：ramp = 今天的 CTL 減 7 天前的 CTL（滾動），也可以週一對週一（**已驗證**，平台論壇：https://forum.intervals.icu/t/ramp-rate-calculation/2107 ）。

→ **定義層面我們和他們一致**：時間常數、加權方式（差 1 %）、滾動 7 天的 ramp 都一樣。唯一的差別是起算：WKO5／我們從 0 起算，Coggan 建議給初始值。

### 2.2 門檻數字的原意

| 來源 | 原文數字 | 原意 | 標記 |
|---|---|---|---|
| Friel | 「an increase in CTL of about 5 to 8 points per week is about right for most」；> 10／週只給菁英、最多一週 | 5–8 是**建議的加量速度**，10 是上限 | **已驗證**，教練：https://joefrieltraining.com/the-ctl-ramp-rate/ |
| Coggan | 「>5–7 TSS/d/wk for four or more weeks, is often a recipe for disaster」 | 是**連續 4 週以上**，不是單週 | **已驗證**，平台：https://www.trainingpeaks.com/learn/articles/the-science-of-the-performance-manager/ |
| TrainingPeaks（Simmons） | 「Very fit athletes can increase their CTL up to five-to-seven points a week」 | 很 fit 的人的上限 | **已驗證**，平台：https://www.trainingpeaks.com/learn/articles/a-coachs-guide-to-atl-ctl-tsb/ |
| Palladino（跑步功率） | 基礎期 CTL 每週 +1–3，**約 2–5 %** | 可以長期維持的速度；Palladino 自己就附了百分比 | 教練，課程筆記（`docs/research/detraining.md:192`） |

這些數字背後的 TSS：

- Friel 和 Coggan 的文章都沒有指定運動或 TSS 種類（**已驗證**：兩篇都只寫「TSS」）。Coggan 寫的 TSS 系統來自自行車功率，他的 PMC 文章說概念「apply regardless of how the training load is quantified」，但這只是說公式可以通用，**不代表不同 TSS 的數字可以互換**。
- 這些教練的典型對象是 CTL 50–100 的自行車／三鐵選手（**推估**：Friel 文中的例子是自行車與耐力運動）。5–8 點對 CTL 60–80 來說約是每週 6–13 %。
- **專門給跑步或越野的 ramp 數字：未找到來源。** Palladino 的 +1–3 是唯一跑步功率背景的數字，而且他自己用 % 表示。

### 2.3 不同 TSS 的尺度

- **rTSS**：用 NGP 對門檻配速，「adjusts for hills」，而且長時間會加重（**已驗證**，平台：https://www.trainingpeaks.com/learn/articles/running-training-stress-score-rtss-explained/ ）。平台沒有給 rTSS 和 hrTSS、功率 TSS 之間的換算。下坡讓 rTSS 偏高或偏低：**未找到來源**。
- **hrTSS**：TrainingPeaks 說心率和 RPE 的估法「far from perfect」，但會接近（**已驗證**，https://www.trainingpeaks.com/learn/articles/estimating-training-stress-score-tss/ ）。intervals.icu 的作者承認從自行車校出來的心率負荷模型「isn't generalising very well to running」，有跑者的心率負荷是其他工具的 2 倍以上（**已驗證**，平台論壇：https://forum.intervals.icu/t/how-training-load-estimation-from-hr-works/280 ）。
- **Stryd RSS**：1 小時在 CP = 100，但強度係數比自行車 TSS 大，理由是「run training is often constrained by mechanical stress」（**已驗證**，廠商：https://blog.stryd.com/2017/01/28/running-stress-score/ ）。
- **Uphill Athlete**：hrTSS 看不到爬升的肌肉成本，每 1,000 ft 加 10（`docs/research/uphill-athlete-mountain-metrics.md:32`）。

→ 平台和廠商都沒說不同 TSS 的尺度相同；有提到的地方都說會有落差。

### 2.4 跑步要不要比自行車保守

Stryd 和 intervals.icu 都指出跑步的負荷不只是代謝（機械、衝擊）。週量的受傷證據（Nielsen 2014、Damsted 2019）是用**跑步距離**量的（`docs/research/unsourced-rules.md:363`）。所以 CTL ramp 在跑步上取 Friel 範圍的下緣是合理的**推估**；文獻沒有直接比較過。

---

## 3. 範例跑者實測（只列比例與範圍）

做法：用 app 的 `FitFolderDataset` 載入範例跑者約兩年有心率的 FIT 資料（唯讀，快取寫在暫存的 home），比較幾種 TSS 設定下的 CTL 和每週 ramp（週日取 CTL(d) − CTL(d−7)，和 `backend/engine/status.py:293` 相同；前 12 週當起算期不計）。

### 3.1 同一筆跑步，不同 TSS

| | 功率 TSS ÷ hrTSS（WKO5、全時間） |
|---|---|
| 路跑 | 中位約 0.8（四分位 0.67–0.92） |
| 越野 | 中位約 0.6（四分位 0.37–0.69） |

- 越野差最多：爬坡時心率高、功率不高（或心率漂移），hrTSS 偏高。只算移動時間時，差距縮到約 0.75–0.8。
- 範例跑者的路跑 hrTSS 中位接近每小時 100（等於每次都在 LTHR 附近）。**推估的 LTHR 可能偏低**，這會把 hrTSS、CTL、ramp 一起放大。LTHR 準不準是另一題，但它直接改變 ramp 的尺度。
- 爬升加成在 hrTSS 越野上中位約占 TSS 的 12 %。

### 3.2 同一份訓練，5／8 的觸發次數

| TSS 設定 | CTL 水準（相對 A） | 週 ramp ≥ 5 | 週 ramp ≥ 8 |
|---|---|---|---|
| A：目前預設（跑步 hrTSS，健行／肌力 0） | 1.0 | 約 1/9 週 | 約 3 % 的週 |
| B：山岳預設（Stryd 功率 TSS＋移動 hrTSS＋爬升加成，健行 0） | 約 0.6–0.7 | 約 2 % 的週 | 0 |
| C：B＋健行／肌力用移動 hrTSS | 約 1.0 | 約 3 % 的週 | 約 1 % 的週 |
| 全部 hrTSS、全時間、全運動 | 約 1.3 | 約 1/9 週 | 約 5 % 的週 |

- 同一份訓練，「注意」的次數差 4–6 倍。CTL 水準差多少，ramp 就差多少。
- 改用相對值（§4.1 的規則）後，各設定的「注意」次數落在約 5–11 % 的週，比絕對值（2–12 %）一致。
- **起算效應**：資料開始後的前 6 週，ramp 可以到 8–15 點／週，一定會被擋，但那只是 CTL 從 0 爬升。新使用者、或只同步一年資料的人都會遇到。
- 指數和線性寫法的差別：< 2 %，不影響。

### 3.3 週量增幅

- 全運動時數、跑步時數、跑步距離三種定義，> 20 % 的週數相近（約 3 成的週），**但不是同一批週**：時數和距離只有一部分的週同時超過 20 %，其他都只有其中一種超過。
- 超過 20 % 的週裡，約一半到七成是**前一週剛好是減量週（< 前 4 週平均的 80 %）之後的回升**。app 自己的 3:1 恢復週是平常量的 65 %（`backend/engine/projection.py:85-87`），下一週回到正常量就是 +35–50 %，再下一週的間歇就會被這條規則擋掉。

---

## 4. 建議

### 4.1 CTL ramp：改成相對值（推估）

```
Δ = CTL(d) − CTL(d−7)，c = CTL(d−7)
注意（只排閾值下）：Δ ≥ max(3, 0.10 × c)
擋（不排間歇）：    Δ ≥ max(5, 0.15 × c)
```

- **為什麼用 %**：TSS 尺度一換（hrTSS↔功率 TSS、加不加健行、LTHR 推估偏差），CTL 和 ramp 會一起乘上同一個倍數，只有 ramp ÷ CTL 不變。這是唯一不用先校準 TSS 就能跨設定、跨使用者通用的寫法。Palladino 自己就用 2–5 %。
- **10 %／15 % 怎麼來的**：Friel 5–8（建議）／10（上限）放在典型 CTL 60–80 上，約是 6–13 %／13–17 %。跑步取下緣（§2.4），注意設 10 %、擋設 15 %。CTL ≈ 50–55 時等於現在的 5／8，CTL 越高越寬、越低越嚴。
- **下限 3／5**：CTL 低（< 30–35）時，% 的分母太小、雜訊大，退回點數。CTL 20 時擋線是 +5（25 %），避免輕易誤擋。
- **不按 TSS 來源分門檻**：% 已經消掉尺度差。真正要處理的是**來源在中途切換**（例如開始用 Stryd，CTL 尺度跳 20–40 %），會在 6 週內造成假的負 ramp 或正 ramp。這要靠 §4.2 的一致性處理，不是調門檻。
- **Coggan 的「連 4 週」**：目前是單週觸發。建議「注意」維持單週（只是降級成閾值下），「擋」也維持單週（Friel 的 > 10 本來就只容許一週），不加連續週數的條件（推估，保守）。
- 排課目標 `RAMP_GOAL`（基礎 +3、專項 +4 點）也是絕對點數。CTL 30 的人 +4 已經是 13 %，接近擋線。建議一併改成 %（推估：基礎 5 %、專項 7 %，都低於注意線 10 %）。

### 4.2 讓 CTL 的尺度穩定

1. **資料不滿 42 天（從第一個有 TSS 的日子算）不看 ramp**，或依 Coggan 的方法給初始值。否則新使用者第 1–6 週一定被擋。
2. **健行／肌力要有 TSS**：`otherthr`／`bikethr` 沒設定時，退回跑步 LTHR（目前只有計畫裡有 LTHR 時才會這樣）。多日百岳用移動 hrTSS（＋爬升加成）。現在這些活動算 0，最重的週反而 ramp 最低。
3. **COROS／TP 來源預設用 MOUNTAIN_PRESET 的移動 hrTSS 和爬升加成**，或至少在 ramp 的說明裡標出現在用哪一種 TSS。
4. `hr_tss_zone1_floor`：要嘛實作，要嘛從 MOUNTAIN_PRESET 拿掉，不要留一個沒作用的設定。

### 4.3 週量增幅：定義要改

- **單位**：Nielsen 2014、Damsted 2019 量的是**跑步距離**（`docs/research/unsourced-rules.md:363`）。app 用全運動時數，健行和肌力也算進去：一週有百岳，時數就會暴增，被擋的卻是下週的跑步間歇。建議改成**跑步（路跑＋越野）時間**。越野的距離受坡度影響太大，用時間比較公平（推估）；路跑為主的人用距離也可以。
- **基準**：改成 `上週 ÷ max(上上週, 前 4 週平均) − 1`。這樣從減量週回到正常量不會被當成「暴增」，但真正超過最近常態量 20 % 的週照樣會擋。也可以直接排除計畫內的恢復週、re-entry 週。
- 20 %／10–20 % 的數字維持（§B2 已查過來源）。

---

## 5. 需要改的程式（本文不實作）

| # | 檔案 | 改什麼 |
|---|---|---|
| 1 | `backend/engine/status.py:46`、`:288-320` | ramp 改成相對值（Δ 和 Δ/c 都放進 `extra`），等級判斷用 §4.1；資料不滿 42 天就回 INFO |
| 2 | `backend/engine/quality_gate.py:102`、`:621-624`、`:1034` | `guard()` 接 ramp 和 CTL₋₇，用同一組相對門檻；最好跟 status 共用同一個函式，不要三處各寫常數 |
| 3 | `backend/engine/adapt.py:76`、`:400`；`backend/api/plan_sessions.py:162` | E 規則改用同一個判斷（擋線）；ctx 傳 CTL₋₇ 或直接傳判斷結果 |
| 4 | `backend/engine/overview.py:355`；`backend/engine/projection.py:88-89` | `RAMP_GOAL` 改成 % 或加 % 上限 |
| 5 | `backend/engine/wko5expr/dataset.py:620`（`sport_setting`） | `walk`／`strength` 沒有自己的 LTHR 時退回 `runthr` |
| 6 | `backend/engine/wko5expr/config.py:82`、`:136` | `hr_tss_zone1_floor` 實作或刪除；評估 COROS／TP 預設是否改用 MOUNTAIN_PRESET |
| 7 | `backend/engine/status.py:180`、`:364`；`backend/engine/quality_gate.py:103` | 週量增幅改成跑步時間、基準改成 max(上上週, 前 4 週平均) |

文案也要改：「Friel 5–8」改成「每週 CTL +10 %／+15 %（Friel 5–8、上限 10 換算，推估）」。

---

## 來源

- Friel, *The CTL ramp rate* — https://joefrieltraining.com/the-ctl-ramp-rate/ （教練，已驗證）
- Coggan, *The Science of the Performance Manager* — https://www.trainingpeaks.com/learn/articles/the-science-of-the-performance-manager/ （平台，已驗證）
- TrainingPeaks, *What is the Performance Management Chart* — https://www.trainingpeaks.com/learn/articles/what-is-the-performance-management-chart/ （平台，已驗證）
- Simmons, *A coach's guide to ATL, CTL & TSB* — https://www.trainingpeaks.com/learn/articles/a-coachs-guide-to-atl-ctl-tsb/ （平台，已驗證）
- TrainingPeaks, *rTSS explained* — https://www.trainingpeaks.com/learn/articles/running-training-stress-score-rtss-explained/ （平台，已驗證）
- TrainingPeaks, *Estimating TSS* — https://www.trainingpeaks.com/learn/articles/estimating-training-stress-score-tss/ （平台，已驗證）
- GoldenCheetah `PMCData.cpp` — https://github.com/GoldenCheetah/GoldenCheetah/blob/master/src/Metrics/PMCData.cpp （開源，已驗證）
- intervals.icu, *Ramp rate calculation* — https://forum.intervals.icu/t/ramp-rate-calculation/2107 ；*How training load estimation from HR works* — https://forum.intervals.icu/t/how-training-load-estimation-from-hr-works/280 （平台論壇，已驗證）
- Stryd, *Running Stress Score* — https://blog.stryd.com/2017/01/28/running-stress-score/ （廠商，已驗證）
- Palladino 基礎期 ramp：`docs/research/detraining.md:192`（教練，課程筆記）
- Uphill Athlete 爬升加成：`docs/research/uphill-athlete-mountain-metrics.md:32`（教練）
- Nielsen 2014、Damsted 2019（週量）：`docs/research/unsourced-rules.md:363`（同儕審查）
