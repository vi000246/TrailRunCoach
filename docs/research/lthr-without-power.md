# 沒有功率計的人能不能估出 LTHR（SP-294）

> 調查日期：2026-10-13。只做調查，沒有改正式程式。分支 `research/sp294-lthr-no-power`（從整合分支 6d0bbcec 開出，已含 SP-289、SP-449、SP-468、SP-471）。
> 接續 `cold-start.md` §3 G2、§5 T8、§6。那裡寫「LTHR 估算要先有 CP，沒有 Stryd 的人可能永遠沒有閾值」，但沒有讀 `cp_as_of`，也沒有實際跑過。這份補上。
> 標記：**已驗證（跑過）**＝用合成資料實際執行過，數字來自 `backend/tests/test_lthr_without_power_sp294.py`；**已驗證（讀程式）**＝讀程式確認，附 file:line；**已驗證（搜尋摘要）**＝這次只讀到搜尋引擎回的摘要，沒讀到原文；**推估**＝我的推論或延伸。
> 只用合成資料。沒有讀擁有者的資料、app DB 或 `~/.wko5coach`（`pd_model` 會去讀 `cptest.curves(WX.home())`，測試裡把它換掉了）。

## 摘要

1. **只有心率、沒有功率的人，從一般跑步估不出 LTHR，AeT 也估不出來。** 原因是 `thresholds.estimate` 碰到沒有 `power` 的跑步就整筆跳過，而且沒有 CP 時連估算函式都不呼叫，所以**連一句原因都沒有**。（已驗證，跑過）
2. **但這種人現在不會「一直沒有閾值」。** SP-289 之後的順序是：測試 → 從自己資料估 → COROS 帳號的 LTHR → **0.90 × 最大心率**。所以 G2 說的「永遠沒有閾值」已經不成立，變成「永遠只有推估值」。（已驗證，讀程式＋跑過）
3. **新的問題在最大心率。** 「0.90 × 最大心率」的最大心率，優先用「近 365 天跑步裡撐得住 5 秒的最高心率」。只跑輕鬆跑的人，這個值就是輕鬆跑的心率頂端，而且它排在年齡公式前面。合成資料上：輕鬆跑型的人最大心率估成 147，LTHR 先驗 132；同樣的生理條件，如果改用年齡公式（40 歲）是 180 和 162。（已驗證，跑過）
4. **有 COROS 手錶功率、沒有 Stryd 的人：** 課表和圖表沒有 CP、也沒有功率目標（不管有沒有打開「接受手錶功率」）；LTHR 反而**估得出來**，因為 `cp_as_of` 在沒有 Stryd 時會改用手錶功率擬合 CP，量尺一致。合成資料上 CP 擬合出 332 W（Stryd 尺度 256 W 的 1.33 倍），LTHR 估出 170，跟設定的真值一樣。（已驗證，跑過）
5. **手錶功率和 Stryd 混在同一個 90 天窗口時，LTHR 會被拉低。** 窗口有 Stryd 時 CP 只用 Stryd 擬合，但手錶功率的跑步還是拿來跟這個 CP 比。手錶功率讀數偏高，連輕鬆跑都算成「夠硬」，它們的心率就混進中位數。合成資料：真值 170，估成 **146**。換錶到 Stryd 的過渡期，或 Stryd 使用者偶爾忘了戴 pod，都會遇到。（已驗證，跑過；實際影響多大是推估）
6. **不靠 CP、只用一般跑步的估法，在沒有強度門檻時只能當下限。** 有做過閾值課的人，估得出接近的值（+5 或 −1 bpm）；只跑輕鬆跑的人，估出來的就是輕鬆跑的心率。真正可靠、而且不需要功率的路，是 30 分鐘 LTHR 測試，app 已經支援（`tt30_result` 沒有功率時改用配速選段）。建議不要從一般跑步自動寫 LTHR，修好上面第 3、5 點，並把「做測試」的提示講清楚。

## 1. 只有心率時，現在的程式怎麼走

### 1.1 合成資料

`backend/tests/test_lthr_without_power_sp294.py` 產生一位跑者 120 天的資料，每 2 天一跑，共 61 次：

| 課 | 比例 | 內容 | 心率 |
|---|---|---|---|
| 輕鬆跑 | 6／8 | 60 分鐘，10.5 km/h | 約 146 |
| 閾值課 | 1／8 | 15 分暖身 ＋ **30 分鐘 13 km/h** ＋ 10 分緩和 | 30 分鐘段 **170**（＝這位跑者的 LTHR 真值） |
| 間歇課 | 1／8 | 10 秒、1、3、5、12 分鐘的反覆，中間慢跑 | 最高約 186 |

心率以 30 秒的時間常數追上目標值，加 ±1 bpm 雜訊。功率另外設定：Stryd 尺度的 CP 是 250 W；手錶功率設成 Stryd 的 1.3 倍。程式註解記錄過擁有者 COROS 手錶功率擬合出來約是測試 CP 的 1.8 倍（`fitdataset.py:1082–1085`），這裡用 1.3 只是讓數字好讀。

另外做一位「只跑輕鬆跑」的跑者（61 次都是輕鬆跑），用來看沒有任何硬跑時的結果。

### 1.2 `thresholds.estimate` 的結果

| 情況 | CP | LTHR | AeT |
|---|---|---|---|
| 只有心率（預設路徑，`cp_of=None`） | None | **None**（沒有原因文字） | None，「只有 0 次 ≥ 45 分鐘的穩定輕鬆跑」 |
| 只有心率（回測路徑，`cp_of=cp_as_of`） | None | **None** | 同上 |

（已驗證，跑過：`test_hr_only_runs_give_no_lthr_and_no_aet_estimate`）

這位跑者 120 天裡有 8 次 30 分鐘、心率正好在 LTHR 的閾值段，還是估不出來。程式上有三道關：

- 每次跑步：`_per_run` 讀不到 `power` 就回 None，整筆不進估算（`backend/engine/thresholds.py:37–39`）。
- 就算有功率：`run_threshold` 沒有 CP 就回空值（`backend/engine/algorithms/threshold_estimate.py:78`），一次跑步只有在「最佳 30 分鐘功率 ≥ 95 % CP」時才算數（`threshold_estimate.py:34`、`:48–49`）。
- 整體：沒有 CP 時直接設成 None，不呼叫 `estimate_lthr`（`thresholds.py:102`），所以 API 只回 `"lthr": null`，沒有「為什麼」。（已驗證，讀程式）

AeT 也一樣：`steady_drift` 沒有功率就回 None（`threshold_estimate.py:145`）。不過另一套 AeT（drift v2，`drift_agg.aet_points`）沒有功率時會改用配速和心率的飄移（`backend/engine/drift_agg.py:335`），閘門和測試建議用的是那一套。課表的輕鬆跑上限用哪一套，這次沒有追到底（見 §7）。（已驗證，讀程式）

### 1.3 `cp_as_of` 在沒有 Stryd、也沒有手錶功率時回什麼

**回 None。**（已驗證，讀程式＋跑過：`A.cp_as_of(ds, TODAY) is None`）

`backend/engine/racepower/athlete.py`：

- `:445` `cp_as_of(ds, day)`：先找當天以前的課表 CP 測試（`:455`），沒有就往回最多 30 天，每天呼叫 `_pd_mftp`（`:458–460`）。
- `:414` `_pd_mftp`：取 90 天內的跑步。有「可用功率」的跑步（Stryd，或打開「接受手錶功率」時的手錶功率）就只用它們擬合（`:435–436` 的 `pw`）；**一筆都沒有時，改用全部跑步、`any_power=True`**，也就是手錶推估功率也算。
- `_curve` 在 `any_power=True` 時不檢查 `power_ok`（`:111–114`），但跑步本身沒有功率資料時拿不到 mean-max 曲線。`pd_model` 收集不到任何曲線就回 None（`:983` 起）。

所以註解寫的「會退回手錶功率」是對的，但前提是要有手錶功率。只有心率的人，`cp_as_of` 一定是 None（除非他手動輸入一筆 CP 測試）。

### 1.4 那這種人的 LTHR 現在從哪來

照 `fitdataset.py` 的順序（COROS／TrainingPeaks 來源）：

1. 課表上的 LTHR 測試（`Dataset.setting` 先看課表）。
2. 從自己的跑步估：`_estimate_settings` 每 30 天估一次，用 `cp_as_of`（`backend/engine/wko5expr/fitdataset.py:1121`）。只有心率的人這一層一定是空的。
3. COROS 帳號的 LTHR：`_coros_lthr_prior`（`fitdataset.py:1009`，在 `:1137` 呼叫）。標「來自手錶」，`threshold_confidence` 判低信心（手錶 LTHR 平均誤差 9–11 bpm，Lu 2025，見 `cold-start.md`）。
4. **SP-289 的先驗：0.90 × 最大心率**：`_apply_lthr_prior`（`fitdataset.py:1033`，在 `:839` 呼叫；值來自 `hr_profile.lthr_prior`，`backend/engine/hr_profile.py:248`）。標「推估（最大心率的 90 %）」，信心低（`threshold_confidence.py:120` 的 `prior`）。

所以 `cold-start.md` §3 G2 擔心的「一直沒有閾值」，在 SP-289 之後只剩「連最大心率都沒有」的人：沒有設定、沒有手錶帳號值、跑步不到 3 次有心率、也沒填出生年。（已驗證，讀程式）

### 1.5 先驗準不準：問題在最大心率那一層

`hr_profile.max_hr` 的順序是：你的設定 → COROS 帳號 → **近 365 天跑步裡撐得住 5 秒的最高心率**（至少 3 次跑步）→ 年齡公式 208 − 0.7 × 年齡（`hr_profile.py:201–228`；跑步那層 `thresholds.py:286` `MHR_HOLD_S = 5`、`:313`）。

| 跑者（40 歲） | 最大心率 | 來源 | LTHR 先驗（× 0.90） | 跟真值 170 差 |
|---|---|---|---|---|
| 有閾值課＋間歇課 | 187 | 跑步資料 | **168** | −2 |
| 只跑輕鬆跑 | 147 | 跑步資料 | **132** | −38 |
| 參考：只用年齡公式 | 180 | 年齡 | 162 | −8 |

（已驗證，跑過：`test_hr_only_prior_comes_from_the_runs_max_hr_and_beats_the_age_formula`）

- 跑步裡看到的最高心率，只能說明「真正的最大心率至少這麼高」。對沒有硬跑的人，它是很低的下限，卻排在年齡公式前面，所以年齡公式永遠用不到。（推估：原理上，清掉尖刺之後，觀察到的心率不會高於真正的最大心率）
- 「只跑輕鬆跑」的人跟有練的人用同一個真值 170，是為了比較；現實中這種人真正的 LTHR 不知道。但不管真值是多少，先驗 132 低於他自己每次輕鬆跑的心率 146，課表的輕鬆跑上限（Friel 0.89 × LTHR ≈ 117）就會比他平常的輕鬆跑低將近 30 bpm。（推估：用算的）
- 現有的信心檢查會發現一部分：TrainingPeaks 規則（訊號 4）看到「20 分鐘心率 × 0.95 ＝ 139 > 132」，報「LTHR 可能設低了」；氣溫未知時只是提示，涼天才是強訊號。兩種情況都建議做最大心率測試和 LTHR 測試，但**不會改值**。（已驗證，跑過：`test_a_prior_below_the_easy_runs_hr_asks_for_the_tests`）

## 2. 有 COROS 手錶功率、沒有 Stryd 的人

設定預設不接受手錶功率（`backend/settings/repository.py:118` `"power.accept_watch_power": False`）。

| 項目 | 現在的行為 | 依據 |
|---|---|---|
| 圖表和課表的 CP | **沒有**。CP 擬合只用 Stryd 跑步，**打開「接受手錶功率」也一樣** | `fitdataset.py:1359`、`:1374` 篩 `power_source == STRYD`；跑過：`test_watch_power_only_no_cp_for_the_charts_or_the_plan_even_with_watch_power_accepted` |
| 課表的功率目標 | 沒有（CP 是 None 時功率欄位是 None） | `backend/engine/zones.py:502`、`:565` |
| 功率 TSS | 沒有，改用 rTSS／hrTSS | `fitdataset.py:1438` `tss_ftp` |
| 「使用功率」自動判斷 | 關（手錶功率要打開接受設定才算） | `backend/engine/athlete_profile.py:127` |
| LTHR（載入時的估算，寫進 `runthr`） | **估得出來**：`cp_as_of` 改用手錶功率擬合 CP（332 W），每次跑步也用手錶功率比，量尺一致 → **170**（n = 11 次，90 天窗口） | 跑過：`test_watch_power_only_cp_as_of_fits_the_watch_power_and_the_lthr_estimate_works` |
| LTHR（「閾值建議」API、總覽的估算） | **估不出來**：這條路用 `Dataset.cp`，也就是 Stryd 擬合 → None | `backend/api/plan.py:897–901`、`thresholds.py:89`；跑過，同上 |
| SP-471（輕鬆跑功率對不起來時建議測試） | 不會出現：沒有 CP 就不判斷 | `backend/engine/base_check.py:497` |

重點：

- 手錶功率的人，LTHR 是靠「手錶功率自己跟自己比」估出來的，這反而沒問題。CP 不給課表用是刻意的設計（`fitdataset.py:1082–1085`：手錶功率擬合出的 CP 約是測試 CP 的 1.8 倍，會讓功率 TSS 小 4 倍）。
- 兩條估算路徑結果不一致：載入時的估算有值，「閾值建議」那個 API 沒有。使用者在設定頁看到的建議可能是空的，但課表其實在用估算值。（已驗證，跑過；畫面上實際長怎樣沒有看）
- 設定「接受手錶功率」的說明寫「功率模型（賽事功率、CP／PD、功率 TSS…）也讀手錶功率」（`repository.py:115–117`），但 COROS／TP 來源的 CP 擬合不管這個設定。說明和行為對不上。（已驗證，讀程式）

### 2.1 手錶功率和 Stryd 混在一起時，LTHR 被拉低

合成資料：前 80 天戴手錶功率（1.3 倍），最後 40 天換成 Stryd。

| | CP（`cp_as_of`） | LTHR | 算進去的跑步 |
|---|---|---|---|
| 混合的歷史 | 255.5 W（Stryd） | **146** | 29 次，四分位 146–170；CP 附近心率的交叉檢查是 170 |
| 只留 Stryd 那 40 天 | 同一個 CP | **170** | — |

（已驗證，跑過：`test_wrist_runs_in_a_stryd_window_are_judged_against_the_stryd_cp`）

原因：`cp_as_of` 在窗口裡只要有 Stryd，就只用 Stryd 擬合（`athlete.py:435`），這是對的。但 `_per_run` 讀 `power` 時不看這筆是不是可用的功率（`thresholds.py:37`），手錶功率的跑步照樣跟 Stryd 的 CP 比。手錶的輕鬆跑 200 W × 1.3 ＝ 260 W，超過 95 % CP（243 W），所以輕鬆跑的心率被當成閾值心率。輕鬆跑比閾值課多很多，中位數就掉到輕鬆跑的心率。

會遇到的人（推估，沒有看真實資料）：

- 從手錶功率換到 Stryd 的人，換之後的 90 天（不夠時放寬到 180 天）。擁有者就是這樣換過來的（`fitdataset.py:1082–1085` 的註解）。
- Stryd 使用者偶爾沒戴 pod，COROS 就自己記手錶功率。只要窗口裡這種跑步比真正的閾值課多，LTHR 就會被拉下來。

交叉檢查（CP 附近的心率 170）其實看到了 24 bpm 的落差，但程式設計上它只當參考，不會擋（`thresholds.py:24–27`，`HR_AT_CP_NOTE`）。

## 3. 不靠 CP 的估法

### 3.1 可能的做法

| 做法 | 需要的資料 | 合成資料結果（真值 170） | 來源與準度 |
|---|---|---|---|
| A. 30 分鐘 LTHR 測試（Friel） | 一次 30 分鐘獨跑全力；心率，有配速更好 | 這次沒跑（是測試，不是從一般跑步推）。程式在沒有功率時改用最快 30 分鐘配速選段，再沒有就用心率（`threshold_confidence.py:584–598`） | Friel 原文（教練）；McGehee 2005：跟實驗室比平均沒偏，SEE 8 bpm（摘要，`zones-and-thresholds.md:165`） |
| B. TrainingPeaks 規則：60 分鐘最高平均心率，或 20 分鐘最高平均 × 0.95 | 一般跑步的心率 | 有練的人 **169**（−1，來自間歇課）；只跑輕鬆跑 **144** | TrainingPeaks 只拿它**建議調高**，不會調低（已驗證，搜尋摘要）。程式已經用它當信心訊號 4（`threshold_confidence.py:299`） |
| C. 從一般跑步做 Friel：每次跑步取最快 30 分鐘，看第 10–30 分鐘的平均心率，取最高 3 次的中位數 | 一般跑步的心率和配速 | 有練的人 **175**（+5，最快的 30 分鐘落在間歇課）；只跑輕鬆跑 **146** | 推估。沒有 CP 時沒有「這次夠不夠硬」的門檻 |

（B、C 的數字：已驗證，跑過：`test_cp_free_routes_are_right_only_when_there_were_hard_runs`）

補充（推估，用算的）：如果有練的人只有 30 分鐘閾值課、沒有間歇課，B 會是 0.95 × 170 ≈ 162（−8）。也就是說 B 的結果看課表怎麼排，在 −8 到 0 之間。

### 3.2 評估

- **B、C 都沒有強度門檻。** 有 CP 時，門檻是「最佳 30 分鐘功率 ≥ 95 % CP」。沒有 CP，就分不出「這 30 分鐘是閾值跑」和「這 30 分鐘是輕鬆跑」。結果是：有硬跑時接近真值，沒有硬跑時就是輕鬆跑的心率。這種值只能當**下限**（「LTHR 至少這麼高」），不能當 LTHR。（推估，合成資料支持）
- 用心率自己當門檻（例如窗口平均 ≥ 0.85 × 最大心率）是循環論證：最大心率本身也是從同一批跑步來的，輕鬆跑型的人兩個一起偏低（§1.5）。（推估）
- 用配速當門檻要先有閾值配速，而沒有 CP 時閾值配速又是從 LTHR 推出來的（`thresholds.py:121–140` 的說明）。也是循環。（已驗證，讀程式）
- **A 是唯一不靠功率、又有驗證研究的做法。** app 已經能排、能讀結果、能套用（`threshold_confidence.py:568`、`:584`、`:629`）。

### 3.3 結論

不建議加一條「從一般跑步、不靠 CP 自動估 LTHR 並寫進課表」的路。建議：

1. 修兩個會讓推估值明顯偏掉的地方：最大心率那層（§1.5）、手錶功率混進 Stryd 窗口（§2.1）。
2. 沒有 CP 時，估算要說原因，並指向 30 分鐘測試。
3. B（TrainingPeaks 規則）維持現在的用法：只當「LTHR 可能設低了」的訊號、只往上提醒，不自動改值。要不要讓它在**只有推估值**（先驗或手錶）的時候往上調，是要使用者決定的事（§6 問題 2）。

## 4. 跟 SP-471 怎麼接

- SP-471 的觸發條件是「輕鬆跑在心率上限時的功率，遠高於 CP 算出來的輕鬆功率帶」，所以需要 CP 和功率。沒有功率、只有手錶功率（課表沒有 CP）的人都不會觸發（`base_check.py:497`）。（已驗證，讀程式）
- 沒有功率的人，同樣角色的觸發已經有了：`threshold_confidence` 對先驗或手錶來源的 LTHR 一律判低信心，訊號 4 看到長時間心率高過 LTHR 會說「可能設低了」，然後在同一個「測試」區塊建議最大心率測試和 LTHR 測試，附「安排課表」連結（`threshold_confidence.py:516–556` 的 `assess`）。新使用者要等第 5 週（或有比賽成績且週量 ≥ 3 小時時第 2 週）才出現 LTHR 測試建議（`threshold_confidence.py:530`、`backend/engine/cold_start.py:203`）。（已驗證，跑過＋讀程式）
- 所以不用為沒有功率的人另做一套 SP-471。要補的只有兩點（推估）：
  - 沒有 CP 時的原因文字（§5 單 3）要跟這個測試建議連在一起，不要各說各的。
  - SP-471 的 AeT 測試那一側，對沒有功率的人可以由 drift v2 的配速飄移來觸發。這次沒有查 `quality_gate` 對沒有功率的人是否已經這樣做，列在 §7。

## 5. 建議的實作單

| # | 標題 | 驗收條件（草稿） | 優先度 |
|---|---|---|---|
| 1 | LTHR 估算只拿跟 CP 同一種功率的跑步 | (a) `test_wrist_runs_in_a_stryd_window_are_judged_against_the_stryd_cp` 改成斷言 170 ± 2（現在是 146）；(b) 只有手錶功率的歷史仍估出 170 ± 2（`cp_as_of` 用手錶功率擬合時，手錶功率的跑步照樣算）；(c) 只有 Stryd 的結果不變；(d) 估算的快取跟著失效（`ESTIMATE_CODE_V` 或 codehash 有涵蓋）；(e) 在 NAS 上對擁有者的資料比較換 Stryd 前後幾個月的 LTHR，列出變化（擁有者驗收） | **P2**（bug，影響換錶過渡期和忘戴 pod 的人） |
| 2 | 從跑步推的最大心率，在沒有硬跑時不壓過年齡公式 | 依 §6 問題 1 的決定。建議版：(a) 跑步推出的最大心率低於年齡公式超過 10 bpm、而且 365 天內沒有比賽／測試／LTHR 以上的長時間努力時，改用年齡公式，來源文字寫兩個值；(b) 有填出生年的「只跑輕鬆跑」合成跑者，LTHR 先驗變成 162（現在 132）；(c) 有硬跑的跑者結果不變（187 → 168）；(d) 沒填出生年時維持現狀，信心仍是低 | **P2** |
| 3 | 沒有 CP 時，LTHR 估算說明原因，並且兩條路徑結果一致 | (a) 沒有 CP 時 `thresholds.estimate` 回 `{"value": null, "reason": "沒有 CP（沒有 Stryd 功率或 CP 測試），不能從一般跑步估 LTHR；可以做 30 分鐘 LTHR 測試"}`，不再是 `null`；(b) 閾值建議 API 對只有手錶功率的人，回的值跟載入時估出、寫進 `runthr` 的一樣；(c) 畫面上的原因附「安排 LTHR 測試」連結，跟 `threshold_confidence` 的測試建議是同一個 | P3 |
| 4 | 「接受手錶功率」的說明跟 COROS／TP 來源的 CP 行為對齊 | 依 §6 問題 3。建議版：說明文字寫清楚「課表和圖表的 CP 只用 Stryd 擬合；打開後影響賽事功率模型和功率 TSS」；設定頁和 `repository.py` 註解一致 | P3 |
| 5 | （不建議現在做）只有推估 LTHR 時，用 TrainingPeaks 規則往上調 | 依 §6 問題 2。如果要做：(a) 只在 LTHR 來源是先驗或手錶時；(b) 只用涼天、心率乾淨的跑步；(c) 只往上調，不往下；(d) 標「推估（下限）」、信心仍低；(e) 有測試或自己資料的估算時不用 | P3 |

### 5.1 單 2 已做（SP-294，2026-10-14，使用者照建議）

`hr_profile.max_hr` 的順序改成：你的設定 → 手錶帳號 → 跑步推出的值 → 年齡公式；但跑步推出的值**比年齡公式低超過 10 bpm、而且 365 天內沒有硬跑**時，直接用年齡公式（來源文字寫兩個值：「推估（年齡公式 180…；近 365 天跑步最高 147，但沒有比賽、測試或閾值以上的跑步，只能算下限…）」）。

「硬跑」（`thresholds.hard_efforts`，全部推估，只當「有沒有用力跑過」的標記）：
- 比賽：當天行事曆有比賽，或標題有比賽字樣（`threshold_confidence._race`）；
- 測試：標題是 LTHR／最大心率測試（`threshold_confidence.test_kind`）；
- 功率（路跑、可用的功率）：最佳 30 分鐘 ≥ 95 % CP（LTHR 估算自己的「夠硬」門檻）或最佳 5 分鐘 ≥ CP（CP 以上，心率會往最大爬）；
- 心率：同樣兩個門檻，對照計畫裡的 LTHR（測試／自己填／套用的估算）；沒有的話對照 **0.90 × 年齡公式**（年齡那條路會給的 LTHR）。不拿跑步自己的最大心率或資料集的 0.90 × 最大心率先驗當門檻——那是拿被判斷的值判斷自己（§3.2 說的循環論證）。

結果（`backend/tests/test_lthr_without_power_sp294.py` §2、§7）：只跑輕鬆跑、有出生年（40 歲）→ 最大心率 180（年齡）、LTHR 先驗 **162**（原本 ≤ 135）；有硬跑的人不變（187／168）；沒填出生年維持跑步推出的值；差距 ≤ 10 bpm（例如 75 歲公式 156 對 147）維持跑步的值；一次硬跑（比賽標題、行事曆比賽、測試標題、5 分鐘 ≥ CP、5 分鐘 ≥ 0.90 × 年齡公式、30 分鐘 ≥ 95 % 計畫 LTHR）就維持跑步的值；超過 365 天的比賽不算。

可信度（`threshold_confidence`）：年齡公式、跑步推出的值都是低可信度來源，照舊建議最大心率測試；年齡公式的值不再跑「撐 120 秒的最高心率差 8 bpm 以上＝尖峰」那條檢查（輕鬆跑遠低於年齡公式正是改用它的原因，不是尖峰的證據），也就不會出現「套用候選值」。手錶帳號的值另見 `lthr-low-confidence-testing.md` §9.9（SP-493 R1：近 90 天有跑到接近就算可信）。

代價（照 §6 問題 1）：真正最大心率天生偏低、又只用心率跑間歇（沒功率、沒標題、心率到不了 0.90 × 年齡公式）的人，會被當成「沒有硬跑」，最大心率偏高；低可信度＋測試建議兜底。

`cold-start.md` §5 的 T8 可以結案：(a) 已跑；(b) 評估結果是不加自動估算，改開上面的單 1–3。

### 5.2 單 1 已做（SP-572，2026-10-14）

`thresholds.estimate` 判斷「這次跑步夠不夠硬」（最佳 30 分鐘 ≥ 95 % CP）時，只拿跟 CP 同一種功率的跑步（`thresholds._judged_against_cp`）。規則跟 `cp_as_of` 選擬合資料的規則（`athlete._pd_mftp`）一樣：CP 那天往前 90 天裡有可用的功率（Stryd；或打開「接受手錶功率」時的手錶功率），CP 就是用它擬合的，只有這些跑步算；一筆都沒有時 CP 是手錶功率擬合的（或在手錶上做的 CP 測試），所有有功率的跑步都算。

- 混合的歷史（§2.1 那組合成資料）：**170**（原本 146），5 次跑步，跟只留 Stryd 那 40 天一樣；預設路徑（`Dataset.cp`，閾值建議 API）也是 170。
- 只有手錶功率：170（不變）；只有 Stryd：170（不變）；Stryd 只在 CP 窗口之前、窗口裡只有手錶功率：手錶的跑步照樣算，170。
- 只改 LTHR 的那一半；AeT 的漂移點每筆跑步本來就跟它自己那天的 CP 比，沒動。
- 打開「接受手錶功率」時，手錶功率算可用的功率，`cp_as_of` 也拿兩種一起擬合，所以兩種照舊一起算（這個情況沒改）。
- 快取：每筆跑步的數值（`thresholds_v1`／`thresholds_asof_v1`）跟這條規則無關，不用重算；估算結果的快取（`fitdataset.estimate_code`）的 codehash 走得到 `_judged_against_cp`、`athlete.power_ok`／`power_source`、`CP_WINDOW_DAYS`，部署後自動失效重算。

測試：`backend/tests/test_lthr_without_power_sp294.py` §5。

### 5.3 單 3 已做（SP-573，2026-10-15）

- 沒有 CP 時 `thresholds.estimate` 的 `lthr` 不再是 `null`：`{"value": null, "no_cp": true, "reason": "沒有 CP（沒有 Stryd 功率，也沒有 CP 測試），所以不能從一般跑步估 LTHR；可以做一次 30 分鐘 LTHR 測試", "links": [threshold_confidence.schedule_link("tt30")]}`（`thresholds.no_cp_lthr`），連結跟測試建議的「安排課表：LTHR 測試（30 分鐘獨跑）」是同一個。設定 › 閾值的「從活動估計」卡片在沒有值時顯示原因和連結。中英都有。
- 預設路徑（`Dataset.cp`：閾值建議 API、總覽、圖表的訓練目標）在 `Dataset.cp` 沒有值（沒有 CP 測試、沒有 Stryd 擬合）、但窗口裡有帶功率的跑步時，改走 as-of 路徑（`cp_of=cp_as_of`），也就是載入時寫進 `runthr` 的那條路。只有手錶功率的人兩邊同一個值（合成資料 170）。有 Stryd CP 的人照舊；只有心率的人不呼叫 `cp_as_of`（沒有東西可擬合），直接回原因。
- 估算結果的快取（`fitdataset.estimate_code`）的 codehash 涵蓋 `thresholds.estimate`，部署後格點估算會重算一次。

測試：`backend/tests/test_lthr_without_power_sp294.py` §1、§4、§8。

### 5.4 單 4 已做（SP-574，2026-10-15）

讀程式確認打開「接受手錶功率」時兩種 CP 用的功率：

| | 用哪種功率 | 依據 |
|---|---|---|
| 課表和圖表的 CP（`Dataset.cp`，也是功率 TSS 的 FTP） | 只有 Stryd 擬合，或使用者輸入的 CP 測試；**不管這個設定** | `fitdataset.stryd_cp_fit` 篩 `power_source == STRYD`、`cp`、`tss_ftp` |
| LTHR 估算的 CP（`cp_as_of`） | 打開時 Stryd 和手錶功率**一起擬合**；關著時窗口有 Stryd 就只用 Stryd，沒有才用手錶功率 | `athlete._pd_mftp`（`power_ok` 打開時對手錶功率也是 True） |
| 賽事功率模型（功率曲線、PD 模型） | 打開時兩種都用 | `athlete.pd_model`（`accept_watch_power`） |
| 功率 TSS | 打開時手錶功率的跑步也算功率 TSS，但除以上面那個 CP；沒有 CP 就照舊 rTSS／hrTSS | `Dataset._metrics`、`tss_ftp` |

設定頁說明（`settings.power.watch_help`，中英）和 `settings/repository.py` 註解照這張表寫，並加上「想用功率練可以手動輸入一筆用手錶功率做的 CP 測試」。

兩條 CP 不一致（打開設定時 LTHR 估算的 CP 混了手錶功率，課表的 CP 沒有）沒有改：這次只對齊說明。合成資料上打開設定時 `cp_as_of` 比關著高（手錶功率讀數偏高，mean-max 外框被拉上去）。要不要改成兩邊一致，是使用者決定的事（SP-572 問答 Q1 第 1 題）。

測試：`backend/tests/test_lthr_without_power_sp294.py` §9。

## 6. 要使用者決定的問題

1. **只跑輕鬆跑的人，最大心率要用跑步資料還是年齡公式？**
   現在：跑步資料優先，所以輕鬆跑型的人最大心率和 LTHR 先驗都偏低很多（147／132，對照年齡公式 180／162）。
   **建議**：跑步推出的值比年齡公式低超過 10 bpm、又沒有任何硬跑時，改用年齡公式（10 bpm 是 Tanaka 公式常被引用的個人誤差量級，`cold-start.md` §4.3 標未驗證）。理由：跑步裡看到的最高心率只是下限；年齡公式雖然個人誤差大，但不會系統性地偏低 30 bpm。代價：真正最大心率天生偏低的人，先驗會偏高幾 bpm。兩種情況都有「低信心＋建議測試」兜底。
2. **只有推估 LTHR（先驗或手錶）的時候，TrainingPeaks 規則看到長時間心率比 LTHR 高，要不要自動往上調？**
   現在：只提示，不改值（`threshold_confidence` 只報告，從不寫入）。
   **建議：先不要**。問題 1 修好之後，主要的偏低情況就沒了；剩下的交給測試建議。TrainingPeaks 自己雖然有「自動套用」選項，但它的前提是使用者會上傳硬的課（已驗證，搜尋摘要）。
3. **有 COROS 手錶功率、打開「接受手錶功率」的人，課表要不要有 CP 和功率目標？**
   現在：沒有，CP 擬合只用 Stryd。
   **建議：維持不給**，改說明文字（單 4）。手錶功率擬合出來的 CP 跟 Stryd 量尺不同（擁有者的資料約 1.8 倍），課表的功率目標和功率 TSS 都會跟著錯。想用功率練的人可以手動輸入一筆用手錶功率做的 CP 測試，這樣測試和課表用的是同一種功率。

## 7. 限制

- 全部是合成資料。心率的反應、手錶功率的倍率（1.3）、課表比例都是我設的。真實的光學心率、熱天飄移、手錶功率跟 Stryd 的關係（可能不是固定倍數）都沒有模擬。
- §2.1 對真實使用者的影響（換錶過渡期、忘戴 pod）是推估。依規則沒有看擁有者在 NAS 上的資料；單 1 的驗收 (e) 留給有權限的人做。
- WKO5 來源（`Dataset`，不是 `FitFolderDataset`）沒有測：它的 LTHR 主要來自 WKO5 自己的設定。
- 課表輕鬆跑上限在沒有功率時，用的是 `thresholds.estimate` 的 AeT（需要功率，所以退回 0.89 × LTHR），還是 drift v2 的配速 AeT，這次沒有追到底。
- TrainingPeaks 的規則只讀到搜尋引擎的摘要；說明頁直接抓回 403。
- 沒有看畫面，只看函式回傳值。

## 參考

- 程式：`backend/engine/thresholds.py`、`backend/engine/algorithms/threshold_estimate.py`、`backend/engine/racepower/athlete.py`、`backend/engine/wko5expr/fitdataset.py`、`backend/engine/hr_profile.py`、`backend/engine/threshold_confidence.py`、`backend/engine/base_check.py`、`backend/engine/drift_agg.py`、`backend/settings/repository.py`。
- 重現測試：`backend/tests/test_lthr_without_power_sp294.py`（9 項，合成資料）。
- 前一份研究：`docs/research/cold-start.md` §1.2、§3 G2、§4.3、§5 T8、§6；`docs/research/zones-and-thresholds.md` §2.6、:165（Friel、McGehee 2005）。
- TrainingPeaks Help Center, "How do I change my Threshold Notification Settings"：心率門檻在「60 分鐘最高心率，或 20 分鐘最高心率的 95 %」高於現值時建議調高，只建議調高（已驗證，搜尋摘要；原文頁回 403）。https://help.trainingpeaks.com/hc/en-us/articles/204072304
