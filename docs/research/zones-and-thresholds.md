# 心率區間、功率區間與閾值：app 現在怎麼算、科學怎麼說、這位選手該怎麼設

> 日期：2026-10-01。資料來源：COROS 資料集（`backend.api.wko5views._dataset(source="coros")`），app DB 以 `file:...?mode=ro` 唯讀開啟，沒有寫入任何東西。
> 標示：**推估**＝我們從資料或文獻推出來、沒有直接來源的數字；**未驗證**＝找不到或讀不到原文；「教練」＝教練／平台的做法，不是同儕審查。
> 文獻查證：用 Europe PMC REST API 與 PubMed efetch 讀摘要（部分讀 PMC 全文）。WebFetch 會先經過小模型摘錄，引號內的句子以它回報的原文為準。

## 0. 先回答三個問題

1. **「輕鬆跑心率是用最大心率算的吧？」——不是。** app 的輕鬆跑上限是 **AeT**。你沒有測過 AeT，所以用 **0.89 × LTHR**（Friel Z2 上緣）。LTHR 155 → 上限 **138 bpm**。最大心率完全沒有參與區間計算（§1.3）。
2. **「閾值心率變高，輕鬆跑配速就能變高？」——只對一半。**
   - 把 LTHR 從 155 改成 160，上限會從 138 變成 142。同一天同樣的體能，142 的配速當然比 138 快。但這只是**換了尺**，不是你變強。
   - 換尺只有在原本的尺量錯（LTHR 被低估）時才是對的。這次的資料顯示 155 **很可能偏低**（§1.6）。
   - 真正的進步，是**同一個心率跑得更快**。LTHR 這個心率本身通常變化不大（§2.4）。
3. **「CP 測試可以推 LTHR 嗎？」——可以當參考，不能當測量。**
   - 族群平均來說，CP 時的心率和 MLSS 時的心率幾乎一樣（差 0.6 bpm）。但個人的 95% 一致性界限是 **−16 到 +17 bpm**（Micheli 2025）。
   - 你 9/30 的 12′ 段是 109% CP，又在 27.6 °C 的悶熱夜裡跑，後半段的心率（163）只能當 LTHR 的**上界**。
   - 要得到可信的 LTHR，還是要在涼爽天、戴胸帶，跑一次 30 分鐘獨跑 TT（§2.2、§3）。

---

## 1. app 現在怎麼做（COROS／TP 資料路徑）

### 1.1 閾值從哪裡來（優先順序）

FIT 資料集（COROS／TP）的規則寫在 `backend/engine/wko5expr/fitdataset.py:27-35`。順序如下：

| 順序 | 來源 | 程式 | 管哪些值 |
|---|---|---|---|
| 1 | 季計畫 `~/.wko5coach/plan.json` 有日期的 thresholds 列 | `planning.Plan.threshold_on`（`backend/engine/planning.py:205-218`）。從 2026-10-01 起，測試只往後生效，不再往前套 | lthr、aethr、mhr、cp |
| 2 | app DB `athlete_settings` | `fitdataset._load_db_settings`（`fitdataset.py:675-701`） | **只用** weight_kg、run_ftp_w、threshold_pace_s_per_km |
| 3 | 從 FIT 推估、每 30 天一格的 as-of 值 | `fitdataset._estimate_settings`（`fitdataset.py:703-748`），`ESTIMATE_STEP_DAYS = 30`（`fitdataset.py:68-72`） | runthr（LTHR）。CP 另有一條 Stryd-only PD 擬合：`_estimate_cp`（`fitdataset.py:844-878`） |
| 4 | 未設定 | — | — |

- WKO5 athlete 檔只在明確開啟 `charts.fit_settings_from_wko5` 時才用（`fitdataset.py:33-35`、`:481-495`）。
- `Dataset.setting` 對 `*thr`／`*mhr` 會先查計畫（`backend/engine/wko5expr/dataset.py:474-485`）。
- CP 的順序是：計畫 → `athlete_settings.run_ftp_w` → Stryd PD 擬合（`fitdataset.py:887-916`）。
- AeT 的順序是：計畫 aethr → 0.89 × LTHR（`dataset.py:504-512`）。

**COROS 帳號那一列（LTHR 182／FTP 200）被忽略**（`fitdataset.py:676-682`、`:698-701`，原因字串 `IGNORED_WHY` 在 `:90-91`）：

- 這一列是 `coros_client.login` 寫進去的 COROS zoneData，沒記錄是哪個運動。
- DB 目前有兩列：2026-09-30 和 2026-10-01，都是 `ftp_w 200, weight 66.3, lthr 182`。
- 182 比 9/30 那次全力 12′ 測試的峰值 171 還高，所以不採用。這個判斷是對的：182 / 185（觀測 HRmax）= 98%，不可能是跑步的 LTHR。

### 1.2 今天（2026-10-01）實際生效的值

計畫只有一列：`2026-09-30 lthr 155, cp 204, aethr null, mhr null`。這一列的 note 寫著「LTHR 自動估算（7 次跑步…）；CP 204 W：9/30 測試的 12 分段 221.9 W − W′ 先驗 13.1 kJ ÷ 720」。

| 值 | 今天的數字 | 來源（app 顯示） | 實際是什麼 |
|---|---|---|---|
| LTHR | **155 bpm** | 「你的測試 2026-09-30」（`zones.threshold_info`，`backend/engine/zones.py:155-159`；`training_targets` 的 `lthr_src`，`zones.py:277-278`） | **不是測試**。是 `apply-estimate` 套用的自動估算（`backend/api/plan.py:286-312`），標籤誤導，見 §3.4 改動 1 |
| AeT | **137.95 ≈ 138 bpm** | 「0.89 × LTHR（Friel Z2 上限）」（`zones.py:281-287`；`racepower/athlete.py:445-449`） | 沒有實測。自動估算 `estimate_aet` 和 B3 聚合 `aet_aggregate` 都**算不出來**，見下 |
| CP | **204 W** | 「你的測試 2026-09-30」 | 3′/12′ 測試，3′ 段沒有全力，兩點法無效。用 W′ 先驗 13.1 kJ 單點推：221.9 − 13100/720 = 203.7 W |
| 閾值配速 | **6.48 min/km（6:29 /km）** | 「推估：CP 204 W × 近 90 天 41 次 Stryd 路跑的速度／功率比（中位數 12.6 mm/s/W）」（`backend/engine/thresholds.py:194-224`） | 推估（`thresholds.py:112-133`） |
| 體重 | 66.3 kg | athlete_settings | — |
| 最大心率 | **不在任何區間計算裡**。計畫 mhr 是空的，FIT 資料集沒有 runmhr | — | 觀測值見 §1.5 |

**AeT 自動估算的實際輸出**（`thresholds.estimate`，180 天窗）：

- 10 個穩定跑的飄移點，心率 133–162 bpm。
- 斜率是**負的**：每 +10 bpm 飄移 −2.1 pp。→「心率越高飄移沒有跟著變大」，value = None（`algorithms/threshold_estimate.py:181-184`）。
- 飄移 < 5% 的跑步裡，前半段心率最高到 **162**（`below`）。

**B3 聚合**（`drift_agg.aet_validity`，`backend/engine/drift_agg.py:146-158`）：

- 16 個點，斜率 −2.7 pp／10 bpm，`valid = False`，結論是「需要測試」。

**LTHR 自動估算現在也算不出來**：

- 拿 CP 204 去算，90／180 天內沒有一次 30 分鐘窗 ≥ 95% CP（194 W）。
- 「HR at CP」交叉檢查：180 天內只有 1 次跑步在 97–103% CP 待了 ≥ 10 分鐘，心率 **159**。

### 1.3 輕鬆跑上限到底是什麼

- **不是最大心率，也不是 %HRR。** 是 AeT。沒有 AeT 時用 `0.89 × LTHR`。這條規則分散在下面這些地方：
  - `Dataset.aethr`（`dataset.py:504-512`）
  - `zones.training_targets`（`zones.py:281-287`）
  - `racepower.athlete.thresholds_as_of`（`racepower/athlete.py:419-451`）
  - `racepower/intensity.py:17-18,57-58`（`aet_frac_lthr 0.89`）
  - `api/plan.py:70-71`（`_effective` 顯示 `round(0.89 × LTHR, 1)`）
  - COROS 課表推送 `backend/sync/coros_workouts.py:124-130`（上限 = AeT 或 0.89 × LTHR，下限 0.75 × LTHR）
- 0.89 是 WKO5 Friel HR 表的 Z2（Aerobic）上緣。Friel 原文 Z2 = 85–89% LTHR（§2.1）。
- **今天的數字**：
  - 0.89 × 155 = **137.95 → 138 bpm**。
  - 課表推送是 116–138。
  - 判定「這次是不是輕鬆跑」時多給 3 bpm：平均 ≤ AeT + 3 = **141**（`workout_review.AET_MARGIN`，`backend/engine/workout_review.py:138`；`equivalence.EASY_HR_TOL`，`backend/engine/equivalence.py:81`）。
- 同一張表的**功率上限**是 Palladino 1C：75–80% CP = **153–163 W**；長跑 80–88% CP = 163–180 W（`zones.py:248-252`）。

### 1.4 有哪些區間模型

| 模型 | 錨點 | 程式 | 用在哪 |
|---|---|---|---|
| Friel 心率 7 區 | LTHR（Z1 < 85%、Z2 85–89%、Z3 90–94%、Z4 95–99%、5a 100–102%、5b 103–106%、5c > 106%） | `zones.FRIEL_HR`（`zones.py:53-61`）。邊界改成接續，沒有 1% 空隙（`:39-44`） | 區間表、課表心率、load focus（`panels/loadfocus.py:12,39`） |
| Classic（Coggan）心率 5 區 | LTHR（69／84／95／106%） | `zones.CLASSIC_HR`（`zones.py:46-52`） | WKO5 對照 |
| Friel 配速 7 區 | 閾值配速 | `zones.FRIEL_PACE`（`zones.py:63-71`） | 區間表 |
| Palladino 功率 10 區＋三區 | CP（1A 50% … 7 > 150%；三區 < 80%／80–95%／≥ 95%） | `zones.PALLADINO_POWER_ZONES`、`PALLADINO_3ZONE`（`zones.py:15-29`） | 功率區間、課表功率 |
| Seiler 三區 | 低 < AeT、中 AeT–LTHR、高 ≥ LTHR | `racepower/intensity.py:11-23`、`status.SRC_SEILER`（`backend/engine/status.py:37`） | 強度分配卡、回測分類 |

- 沒有 %HRmax 或 %HRR（Karvonen）模型。HRmax 只用在 racepower 的「這場是不是全力」檢查（`racepower/maximal.py:69-75,106-112`）。

### 1.5 今天算出來的數字

| 項目 | 數字 | 怎麼算的 |
|---|---|---|
| LTHR | 155（計畫，2026-09-30）。as-of 推估歷史：2026-02-15 151、03-17 158、04-16 161、05-16 154、06-15 157、07-15 154、08-14 155、09-13 156 | `ds.athlete.settings["runthr"]` |
| 套用 155 時用的 CP | **cp_as_of = 175 W**（9/13、9/29），不是 204。≥ 95% 的門檻是 166 W | `racepower.athlete.cp_as_of`（`racepower/athlete.py:382-399`）。9/29 的 estimate：7 次、IQR 153–156 |
| AeT | 138（0.89 × 155）。自動與聚合估計都失敗（§1.2） | — |
| 觀測最大心率 | **185 bpm**：365 天內每次「撐 ≥ 120 秒」的峰值，取前 5 的中位數，同 `maximal.hrmax_observed` | 前幾名：2025-07-26 越野 199、2024-11-05 越野 191、2025-03-22 越野 190、2025-09-27 路跑 189、2025-07-02 路跑 187、2026-03-25 越野 187、2026-04-11 越野 187 |
| 原始峰值（多半是光學雜訊） | 2026-03-25 **223**、2025-09-27 **220**、2025-08-09 208 | 單一取樣最大值。撐 120 秒後只剩 187／189／182 |
| CP 測試 9/30（20:30，27.6 °C、RH 85%、Hadley 158） | 3′ 段（8.7–11.8′）216 W、心率均 140、峰值 149（沒有全力）。**12′ 段（28.4–40.3′）222 W（109% CP）、心率均 155、後半 163、最後 2′ 164、峰值 171** | 30 秒功率 > 190 W 且 ≥ 90 秒的段落 |
| 閾值配速 | 6:29 /km（推估，CP 法） | `thresholds.estimate_tpace` |
| 近期穩定跑的「HR at CP」 | 180 天內只有 1 次 ≥ 10 分鐘在 97–103% × 204 W，心率 159。近 120 天的路跑沒有一次 | `threshold_estimate.run_threshold`（`threshold_estimate.py:70-86`） |

**穩定平路段的心率–功率**：

- 方法：`heat_data.steady_segments`，窗口放寬到 800 天，共 150 段。每段 ≥ 10 分鐘、坡度 < 3%、功率 CV < 10%，Hadley 取自 activity_weather。
- 結果：

| 天氣 | 段數 | 功率範圍 | 心率 = a + b·P | 外插到 204 W |
|---|---|---|---|---|
| 涼（Hadley < 120，2025-11…2026-03） | 19 | 161–188 W | 120.5 + 0.177·P，殘差 SD 4.8 | **157 bpm**（推估：外插超出資料 16 W，斜率可能被日間雜訊壓平） |
| 中（120–150） | 50 | 150–192 W | 134.0 + 0.117·P | 158 |
| 熱（> 150） | 81 | 128–191 W | 斜率 ≈ 0（−0.055） | 熱天時，心率決定功率，而不是功率決定心率 |

- 逐月看，穩定段的心率中位數**一整年都在 151–156**，變的是功率：
  - 2026-01 中位數 179 W、Hadley 113。
  - 2026-07 中位數 155 W、Hadley 161。
  - 每 100 W 的心率從 83（2 月）升到 99（6 月）。
  - → 你平常是**照心率在跑**。熱天同樣 154 bpm 只推得出 76% CP，冬天推得出 88% CP。

### 1.6 為什麼 LTHR 155 很可能偏低

1. **估算的定義是「30 分鐘窗 ≥ 95% CP」**（`threshold_estimate.py:11-14,45-46`）。程式自己的 docstring 也寫了：「otherwise the run wasn't a threshold effort and the HR would underestimate LTHR」。
2. 套用 155 時的 CP 是 **175 W**（Stryd PD 擬合，夏天）。所以入選的 30 分鐘窗只要 ≥ 166 W。以真實的 204 W 來看，那只是 **81–85% CP**，是 Palladino Z2「Endurance」。這 7 次是夏天的一般跑，不是閾值跑。
3. 這些窗後 20 分鐘的平均心率約 155。意思是「熱天 ~83% CP 的心率」，不是乳酸閾值的心率。
4. 交叉證據都指向 > 155（都是推估）：
   - 涼天外插 HR@204 W ≈ 157。
   - 唯一一次 ≥ 10 分鐘在 CP 的心率 159。
   - 2025-12-21 半馬（19.1 °C）最好的 60 分鐘均 156、40 分鐘均 158，而且是在 2 小時多的比賽裡撐住的。
   - 12′ @ 109% CP 後半 163（熱天，而且高於 CP，所以是上界）。
5. 範圍推估：**真實 LTHR 落在約 157–163**。這只是推估，不能直接套用，要測（§3.3）。
6. 熱天會讓同功率的心率變高（§2.3）。所以「熱天 83% CP 時 155」和「涼天 100% CP 時約 160」可以同時成立。

### 1.7 什麼時候會更新

| 項目 | 現在的觸發方式 | 程式 |
|---|---|---|
| CP | 套用 CP 測試結果：`POST /plan/thresholds/apply-cp`，寫成測試日那一列（`api/plan.py:324-359`；`cp_protocols.apply_payload`，`backend/engine/cp_protocols.py:377`）。測試日以前沒有計畫 CP 時，用每 30 天的 Stryd PD 擬合 | `fitdataset._estimate_cp` |
| CP 提醒 | 測試超過 42 天 → WATCH，超過 90 天 → BAD（`status.py:60`，`TEST_DAYS_WATCH, TEST_DAYS_BAD = 42, 90`，「CP test every 4–6 weeks (notes)」）。停跑後回來要重測（`status.py:713-726`） | `status.i_testing`（`status.py:686`） |
| LTHR | (a) 每 30 天的 as-of 推估，只在沒有計畫列時用；(b) 使用者在季計畫頁按「套用估計」（`apply-estimate`，`api/plan.py:286-312`）。沒有「LTHR 測試」的流程 | `fitdataset._estimate_settings`、`thresholds.estimate`（`thresholds.py:50-109`，90 → 180 天窗） |
| AeT | B3：不用天數判斷過期，有理由才排測試（`aet_test.py:70-72`）。理由包括：`no_data`（約 6 週沒有可判讀的跑步）、`se`（聚合 SE > 3 bpm）、`shift`（最近 6 點單向偏 > 5 bpm）、`moved`（和計畫 AeT 差 > max(SE, 3)）、`break`（停跑 ≥ 4 週）（`quality_gate.aet_test_reason`，`backend/engine/quality_gate.py:750-790`）。門檻常數在 `threshold_estimate.py:207-210` | `drift_agg.aet_validity`、`aet_test.apply_body`（`aet_test.py:421`） |
| 區間 | 區間跟著「當天生效的閾值」即時重算（`zones.zone_table`、`training_targets`）。改計畫閾值會重建資料集（`wko5views.plan_changed`） | — |

---

## 2. 科學與教練來源

### 2.1 心率區間的錨點準不準

| 錨點 | 證據 | 典型誤差 | 誰在用 |
|---|---|---|---|
| **%HRmax** | Iannetta 2020（*MSSE* 52:466–473，DOI 10.1249/MSS.0000000000002147，n = 100，摘要）：LT 落在 **60–90% HRmax**、MLSS 落在 **75–97% HRmax**。固定百分比 "conform poorly to exercise intensity domains"。<br>Kanniainen 2025（*Physiol Rep* 13:e70241，DOI 10.14814/phy2.70241，n = 58 跑步機，PMC 全文）：用**實測** HRmax × 70% 當 T1，比 LT1 平均低 **21 bpm**；× 85% 當 T2，比 LT2 低 **11 bpm**。用公式 HRmax 分別低 25／17 bpm | 系統性偏低，個人差異大。上面的範圍等於 ±15% HRmax | 入門、多數手錶的預設；Helgerud 4×4 用 90–95% HRmax（`docs/research/interval-prescription.md`） |
| HRmax 本身 | Ausland, Kelemen & Seiler 2026（*Front Sports Act Living*，DOI 10.3389/fspor.2026.1806303，n = 4,375，摘要）：Tanaka 公式低估 4.8 bpm，一致性界限 **−18.5～+9.1 bpm** | 公式 ±約 14 bpm；實測要有真的全力 | — |
| **%HRR（Karvonen）** | Mann, Lamberts & Lambert 2013（*Sports Med* 43:613–625，DOI 10.1007/s40279-013-0045-x，摘要）："a similar effect has been shown when relating exercise intensity to VO2R or HRR"。也就是說，同一個 %HRR 的乳酸反應個人差異一樣大。建議改用 AerT／AnT 這類閾值錨點 | 跟 %HRmax 差不多 | 徐國峰／RQ：閾值 84–88% HRR（`300 Sport/60 🏃 有氧訓練/不同的心率區間模型比較.md:10-13`），教練 |
| **%LTHR（Friel）** | Friel 原文（TrainingPeaks "Quick Guide to Setting Zones"，教練）：30 分鐘獨跑 TT，"look to see what your average heart rate was for the last 20 minutes. That number is an approximation of your LTHR"。跑步 Z1 < 85%、Z2 85–89%、Z3 90–94%、Z4 95–99%、5a 100–102%、5b 103–106%、5c > 106%。<br>30 分 TT 後 20 分心率對實驗室 LT／MLSS 的**驗證研究沒有找到**（Europe PMC 檢索，**未驗證**） | 錨點本身準度未驗證。百分比切出來的 Z2 上緣（89%）只是慣例，不等於 LT1（`aerobic-base-readiness.md:254`） | Friel、TrainingPeaks、WKO5、Stryd／COROS 的 LTHR 模式 |
| **通氣／乳酸閾值（Seiler 三區）** | Seiler & Kjerland 2006（*Scand J Med Sci Sports* 16:49–56，見 `aerobic-base-readiness.md:214`）：區間邊界直接用 VT1／VT2（或 LT1／LT2）當下的心率 | 實驗室測量本身就是標準。誤差來自「測完之後會變」與「場地和實驗室不同」 | Seiler、Uphill Athlete（AeT／AnT）、挪威派 |
| HRV DFA-α1 | Kanniainen 2025：DDFAT1 對 LT1 平均差 −2 bpm，一致性界限 **+23～−27 bpm**；DDFAT2 +5 bpm，界限較窄 | 平均沒偏，個人散 | 需要 RR 間期（胸帶） |

- **任務說明裡提到的「Nuuttila 2025：LT1 ≈ 79–84% HRmax、±16 bpm」**：`docs/research` 裡找不到這句。Europe PMC 檢索 Nuuttila 2022–2026 的論文，也沒有找到對應的數字 → **未驗證**。
  - `docs/research` 裡唯一引用過的 Nuuttila 2025 是 *Eur J Appl Physiol* 125:697–705（DOI 10.1007/s00421-024-05631-y），內容是 90 分鐘 LT1 跑的耐久性，不是心率錨點的驗證。
  - 最接近的是同一研究群（Ihalainen、Laatikainen-Raussi）的 Kanniainen 2025（上表）。
  - 「±16 bpm」這個量級，在 Micheli 2025 的 CP vs MLSS 心率一致性界限裡出現（§2.2）。
- **結論**：照準確度排序是 **實驗室閾值 > 個人實測的 LTHR／AeT > %HRR ≈ %HRmax（實測 HRmax）> 公式 HRmax**。app 用 LTHR／AeT 當錨點、不用 HRmax，方向是對的。問題在於 LTHR 本身是推估的（§1.6）。

### 2.2 CP 測試能不能推出 LTHR？

**CP 和 MLSS／LT2 的關係**

- Jones et al. 2019（*Physiol Rep* 7:e14098，DOI 10.14814/phy2.14098，摘要）：MLSS 通常落在比 CP 低一點的功率。CP 功率下 "sustainable for no more than approximately 20-30 min"。作者主張 CP 才是真正的最大代謝穩定態邊界，"CP is not MLSS"。
- Galán-Rioja et al. 2020（*Sports Med* 50:1771–1783，DOI 10.1007/s40279-020-01314-8，摘要）：CP 和 MLSS 的 r = 0.77，但 MLSS **低估 CP 11%**；VT2 高估 21%、RCP 高估 6%、VT1 低估 30%。結論是沒有一個閾值 "should be considered synonymous" with CP。
- Nixon et al. 2021（*Eur J Appl Physiol* 121:3133–3144，DOI 10.1007/s00421-021-04780-8，跑步，摘要）：CS 16.4 vs MLSS 15.2 km/h。CS 之上約 0.4 km/h，VO2 一路升到最大。
- **Micheli et al. 2025**（*PeerJ* 13:e19060，DOI 10.7717/peerj.19060，10 篇統合分析，摘要）是這題最直接的證據：
  - 以功率表示，CP 比 MLSS 高 12.4 W（95% LoA −19.2～+44.1 W）。
  - 以**心率**表示，**平均差 0.61 bpm（95% LoA −15.84～+17.05 bpm）**，沒有顯著差異。

→ **「CP 當下的心率 ≈ LTHR」在族群平均上成立，但個人可能差到 ±16 bpm。** 這還是實驗室量的誤差，不含場地的熱和雜訊。

**CP 測試段的心率能不能用**

- 3′、12′ 都高於 CP（嚴重強度區）。這個區間裡 VO2 和心率會一直升（Nixon 2021：CS 之上 VO2 升到最大），不會穩定在「閾值心率」。所以 12′ 段後半的心率是 **LTHR 的上界**，不是 LTHR。
- 心率對功率變化的反應有約 1 分鐘延遲（τ 55–70 s，Hunt 2015／2019，引自 `zones.py:233-235`）。3′ 段的心率幾乎還在爬升，沒有參考價值。
- **Friel 30 分鐘 TT** 跑出來的平均功率，大約落在 CP 附近（CP 只撐得了 20–30 分，Jones 2019）。它的後 20 分鐘平均心率，就是 Friel 定義的 LTHR（教練，對實驗室的驗證**未驗證**）。也就是說，**30 分 TT 同一次就能得到 LTHR，又能交叉驗證 CP**，比 3′/12′ 的心率更適合拿來推 LTHR。

**同一個功率的心率能不能跨天比**

- 日間變異：在 85–90% HRmax 的次大強度，同一個人不同天的心率變異約 **3 ± 1 bpm**，是各強度裡最小的（Lamberts & Lambert 2009，*J Strength Cond Res* 23:1005–1010，DOI 10.1519/JSC.0b013e3181a2dcdc，摘要）。
- 熱：
  - 35 °C 下 15–45 分鐘心率升 11%，22 °C 只升 2%（Lafrenz 2008）。
  - 28.7 °C 對 19.2 °C，最高心率 +16 bpm（Beiter 2025）。
  - 以上見 `aerobic-base-readiness.md:524`、`drift-algorithm-brief.md:55`。
  - 熱天的閾值功率也會被高估：1 小時 TT 的預測，熱天（38 °C）比涼天（13 °C）多高估約 13 W（37.7 vs 24.1 W，Lorenzo 2011，*J Appl Physiol* 111:221–227，DOI 10.1152/japplphysiol.00334.2011）。
  - **熱天時「LTHR 這個心率」本身會不會變：未驗證**（沒找到直接研究）。
- 心血管飄移：10–20 分鐘後每搏量下降、心率上升（Coyle & González-Alonso 2001，引自 `aerobic-base-readiness.md:512`）。
- 這位選手的資料：同樣約 154 bpm，冬天 179 W、夏天 155 W（§1.5）。**季節造成的差異（約 24 W ≈ 12% CP）比任何轉換公式的誤差都大。**

**「用功率推心率區間」成不成立**

- **不是測量，是推估。**
  - 功率區間（Palladino % CP）和心率區間（Friel % LTHR）各有各的錨點。
  - 兩者之間的關係隨天氣、疲勞、飄移、感測器改變。
  - 用 CP 推 LTHR 的個人誤差，至少有實驗室的 ±16 bpm（Micheli 2025）。
- **可以做的**：拿「涼天、穩定、暖身後、在 CP 附近的心率」當 LTHR 的**交叉檢查**。兩者差 > 5 bpm 就提示「該測一次」（5 bpm 是推估）。
- **不建議做的**：直接把 CP 推出的 HR 寫進計畫當 LTHR；或拿區間百分比互推（例如「Palladino Z2 上緣 88% CP ≈ Friel Z2 上緣 89% LTHR」）。這種對應沒有來源（**未驗證**）。這位選手的資料就反過來：冬天 88% CP 時心率已經約 155，等於 100% 的現行 LTHR。

**app 現在就能套用在既有資料上的方法（推估）**

- **A. 涼天 HR-at-CP**：
  - 條件：Stryd 路跑、Hadley < 120（或 < 25 °C）、暖身 10 分鐘後、30 秒功率在 97–103% CP 的時間 ≥ 10 分鐘、平路。
  - 取符合條件各次心率的中位數，需要 ≥ 3 次（3 次是推估）。
  - 預期誤差：方法本身 ±16 bpm（Micheli LoA）＋日間 ±3 bpm（Lamberts）。光學感測器的誤差另計（§2.3）。
  - 目前資料：180 天內只有 1 次，**用不了**。要等今年冬天，或主動排 CP 附近的節奏跑。
- **B. 涼天心率–功率回歸外插到 CP**：
  - 目前 19 段，得到 157 bpm（殘差 SD 4.8）。
  - 外插 16 W，而且斜率可能被壓平 → **只當方向參考**。
- **C. 12′ 段後半心率當上界**：163（熱天）。

### 2.3 為什麼這位選手的 LTHR／AeT 測不準

| 原因 | 證據 | 這位選手 | 能不能修、怎麼修 |
|---|---|---|---|
| **熱（台灣）** | Lafrenz 2008、Wingo 2020、Beiter 2025（見 §2.2）。台灣教練："臺灣一年有超過半年白天都在 25 度以上…天氣熱的時候心率本來就會偏高，測出來的飄移會失真"（`docs/research/xu-guofeng-reply.md:22`） | 9/30 CP 測試 Hadley 158。LTHR 155 的 7 次跑全在夏天。穩定段每 100 W 的心率 6 月 99、2 月 83 | **能修**：只在 < 25 °C／Hadley < 120 的日子測。10 月以後的清晨，或冷氣房跑步機加電扇（`aerobic-base-readiness.md:579`） |
| **心血管飄移** | Coyle & González-Alonso 2001 | 30 分 TT 後 20 分本來就含飄移，Friel 的定義已經把它算進去 | 照 Friel 的流程做就好；不要用 40 分鐘以上的一般跑代替 |
| **錶的心率感測器** | Gillinov 2017（*MSSE* 49:1697–1703，DOI 10.1249/MSS.0000000000001284，摘要）：胸帶 Polar H7 對 ECG 的 rc = 0.996，腕式 0.67–0.92，Garmin FR235 0.81。Gielen 2026（*JMIR Form Res*，DOI 10.2196/85186）：10 款光學裝置的 MAE 4.5–14 bpm。徐國峰：「因為手腕的血流量會有延遲，所以腕式心率比胸式心率沒那麼即時」（`300 Sport/60 🏃 有氧訓練/跑者都該懂的跑步數據，讀書心得.md:41`） | 原始峰值 223、220、208 bpm，撐 120 秒後只剩 187／189／182 → 有尖峰雜訊。FIT 的 device_info 只記錄 COROS APEX 2 Pro，**無法從檔案判斷那幾次有沒有戴胸帶（未驗證）**，要問使用者 | **能修**：測試一律戴胸帶（Polar H10／COROS HRM）；app 標記沒有胸帶的測試 |
| **很少全力跑** | Friel：「the more times you do this test the more accurate your LTHR is likely to become」 | 路跑 20 分鐘最高心率前幾名幾乎都在夏天，而且功率只有 145–185 W | 一年 2–3 次 30 分 TT 或 10K 比賽（頻率是推估） |
| **閾值是推估的，不是測的** | §1.6 | LTHR 155 = 熱天 83% CP 的心率；AeT 138 = 0.89 × 這個推估值，推估疊推估 | **能修**：LTHR 用 30 分 TT 測；AeT 用徐國峰 90 分鐘或 UA 飄移測試測 |
| AeT 飄移回歸失敗 | 單次飄移誤差 ±4–6 pp（`drift_agg.py:5-8`） | 10–16 點的心率範圍 133–162，飄移沒有跟著心率上升，大多是熱天 40–47 分鐘的短跑 | 一次正式的 AeT 測試，比一百次被動資料有用 |
| 實驗室 | Iannetta 2020、Seiler 2006 | — | 最準。一次乳酸／氣體分析的跑步機測試同時拿到 LT1、LT2、HRmax（可選） |

### 2.4 「閾值心率變高 → 輕鬆跑配速變快」這個想法

- **錨點是尺，不是體能。** 輕鬆跑的上限 = AeT（或 0.89 × LTHR）。把 LTHR 調高，上限就跟著調高，你在同一天就能跑快一點。但同樣的跑法生理上變硬了。
  - 如果原本 LTHR 是低估的（這位選手很可能是），調高是**修正錯誤**。
  - 如果原本是對的，調高就讓「輕鬆跑」超過 AeT，等於把 1 區跑成 2–3 區。後果是灰色地帶的疲勞，有氧基礎反而練不起來（Seiler 的極化理由，見 `aerobic-base-readiness.md:212-223`）。
- **進步的樣子是同一個心率跑得更快。**
  - 徐國峰：「進步的指標是在同心率區間底下，能跑出更快的配速」（`跑者都該懂的跑步數據，讀書心得.md:47`）。
  - 徐國峰（RQ 文）：「最佳的進步結果是 T-pace 提升了，但 LTHR 不變」（`不同的心率區間模型比較.md:24`）。他採用儲備心率法，正是因為「不變的標準」比較好衡量進步（`:11-16`）。
  - Friel 的 EF（同心率的速度或功率）"will rise over the course of a few weeks"（`aerobic-base-readiness.md:258`）。
  - 有氧體能變好時，閾值的速度／功率會往上移。**閾值心率通常只有小幅變動**：這是教練共識，系統性證據**未驗證**。
- **這位選手的情況**：今年冬天同樣約 155 bpm 跑出 179 W，夏天只有 155 W。到了 11–12 月，同一個心率配速自然會變快，那是天氣，不是 LTHR 變了。要看真的進步，比較的是「涼天、同心率的功率或配速」的逐年趨勢，app 已有 EF／HRC（`heat.hr_cost`）。

### 2.5 什麼時候該更新區間

**教練建議**

- Friel：在 Base、Build 期初各測一次 LTHR（原文 "early in the Base and Build periods"）。
- 筆記：CP 每 4–6 週（`status.py:60`）。
- UA：AeT 4–6 週重測（措辭**未驗證**，見 `quality_gate.py:754`）。
- 徐國峰：每次目標賽後、跑力提升後，用新的 E 配速重新打底，再做 90 分鐘檢測（`跑者都該懂的跑步數據，讀書心得.md:77`）。

**證據**

- 停訓 2–4 週：次大心率 +11 bpm、血量 −9%。14 天內同速心率 +11 bpm（Coyle 1986、Houmard 1992，見 `docs/research/detraining.md:47-49`）。→ **長休後區間一定要重新確認。**
- 固定週期重測沒有直接證據。

**事件觸發**（建議用這些，不要只看日期）

1. 新的 CP 測試套用後（apply-cp）：功率區間馬上換。如果這次測試是 30 分 TT，同時寫入 LTHR。
2. 新的 AeT 測試套用後：輕鬆跑上限馬上換。
3. **熱校正後**的 HR-at-power 持續偏移（`engine/zone_events.py` `hr_shift`，2026-10-02 改版）→ 提示「該測 LTHR／AeT」，不自動改。
   - 原本只用 < 25 °C 的路跑。但台灣一年大半時間 ≥ 25 °C，最近 60 天常常湊不到 6 次，偵測器多數月份根本不會動；手錶溫度又會被體溫墊高。
   - 改成**每一次穩定路跑都用**，把熱的影響算掉：每次跑步的穩定段心率中位數，用本人 β = 0.224 ± 0.036 bpm／Hadley（HEAT 回測，`heat.HR_BETA`）換算到 Hadley 120：HR′ = HR − β·(Hadley − 120)。
   - 溫度來源依序：Open-Meteo 路線歷史天氣（跑步當時、當地，含露點 → Hadley）→ 手錶溫度扣掉手腕偏差（本人 72 段路線 effort：手錶比空氣高 3.7 ± 2.7 °C；RH 用同季節的歷史值；標「較不準」）→ 同季節的歷史 Hadley 中位數（最不準）→ 都沒有就不用。
   - 基準線：最近幾次之前 365 天的 HR′ = a + b·P（≥ 8 次）；最近 = 90 天內最後 6–8 次、落在基準線功率範圍內。偏移 = 殘差中位數。
   - 門檻 = max(5 bpm, 2·SE)，SE 合併基準線在該功率的誤差、最近幾次的散布（加上溫度不準的那幾次的 β·σ_H）、β 的誤差 × 兩段時間的平均 Hadley 差；且 ≥ 5/6 次同方向。5 bpm／6 次沿用 B3 的 shift 規則，2·SE、8 次／90 天是推估。
   - **去年同季對照**：最近這幾次的日期往前 365 ± 30 天，有 ≥ 4 次就算它們對同一條基準線的殘差；扣掉這個季節殘差後仍要超過門檻才觸發。理由：回測的 β 在初夏（0.149）和夏末（0.260）不一樣，單一 β 可能留下幾 bpm 的季節性；去年同季直接量出這個量，偏移若只是季節，就不是體能變化（推估）。
   - 訊息會寫「已依熱指數校正（β 0.22 ± 0.04 bpm/Hadley …；天氣：…；校正信心 高／中／低）」，保留手腕心率的提醒。
   - 本人資料（2026-10-02，唯讀）：穩定路跑點 62 → 135（全部有 Open-Meteo 天氣）；最近一段 60 天內涼天 8 次 → 90 天內 34 次（取最後 8 次）；基準線 56 → 100 次。偏移 −1.9 ± 2.6 bpm，門檻 5.3 bpm，5/8 同方向 → 不觸發；去年同季 −1.7 bpm（17 次），扣掉後 −0.1。舊規則是 +2.8 bpm（6 次、4/6 同方向）。
   - 交叉檢查：在這 100 次基準線上聯合擬合 β 得 0.115 ± 0.045（全部 135 次 0.078 ± 0.046），比回測的 0.224 小。一年內熱和體能一起變（夏天也是練量期），這個 β 有混淆，所以偵測器仍用回測（同路線比較）的 β；聯合擬合值只放在 evidence（`heat.beta_check`）。β 的誤差已經算進 SE，去年同季對照也會擋掉 β 不準造成的季節性偏移。
4. 停跑 ≥ 4 週（已有 `reentry`）：恢復期後重測 AeT、CP。
5. **季節轉涼**（台灣約 9–10 月）：夏天之後，連續 3 個路跑日的**清晨**（05–07 時，Open-Meteo 歷史天氣，家附近那一格的最低點；`heat_data.morning_weather`）< 25 °C 且 Hadley < 150 → 建議一次 30 分 TT＋一次 AeT 測試。
   - 用清晨而不是跑步當時：測試會排在清晨；傍晚下過雨的 24 °C 不代表換季。快取沒有那天清晨時，退回跑步當時的路線天氣（比清晨熱，只會延後、不會提早）。手錶溫度不用在這裡。
   - 原本寫「Hadley < 120 的清晨」：本人資料裡這要到 11 月中～12 月（2025-11-19、2024-12-09）才出現，太晚。25 °C 是徐國峰「等天氣轉涼再做」的線，Hadley 150 是 Hadley 的熱帶（`route_weather.HOT_HADLEY`）；夏天 = 前 60 天 ≥ 10 個路跑日清晨 Hadley ≥ 150 且過半。3 天／60 天是推估。

- app 現有的 B3／aggregate 邏輯（`quality_gate.aet_test_reason`）已經是事件觸發。只需要把 LTHR 也接上同一套（§3.4 改動 4）。

---

## 3. 這位選手「最正確」的設定

### 3.1 錨點

| 用途 | 錨點 | 為什麼 |
|---|---|---|
| 輕鬆跑／長跑上限 | **實測 AeT**（徐國峰 90 分鐘或 UA 40–60 分鐘飄移測試，< 25 °C） | Seiler／UA 用的是 LT1／AeT 本身。0.89 × LTHR 是兩層推估 |
| 中、高強度心率 | **實測 LTHR**（30 分獨跑 TT 的後 20 分平均，涼天、胸帶） | Friel 定義；同一次 TT 也驗證 CP |
| 功率（主） | **CP 204 W**（9/30 測試，W′ 用先驗）→ Palladino % CP | 爬坡、間歇、熱天都看功率（`zones.py:229-247`）。下一次在涼天重測，最好用兩點法量到 W′ |
| 最大心率 | **不拿來分區**。只當資料清理用（尖峰）和 4×4 的參考（90–95% HRmax，Helgerud） | %HRmax 個人誤差大（Iannetta 2020） |

### 3.2 每一套區間怎麼算

- **心率（Friel，% LTHR）**：照 `zones.FRIEL_HR`。輕鬆跑上限**用 AeT 取代 0.89 × LTHR**（app 已經這樣設計，只是缺實測值）。
- **功率（Palladino，% CP）**：照 `zones.PALLADINO_POWER_ZONES`，CP = 204 → 1C 153–163 W、Z2 163–180 W、3A 180–194 W、3B 194–206 W、Z4 206–216 W。
- **三區（Seiler）**：低 < AeT、中 AeT–LTHR、高 ≥ LTHR；功率版 < 80%／80–95%／≥ 95% CP。
- **配速（Friel，% 閾值配速）**：只在平路、涼天參考。閾值配速 6:29 /km 是推估（CP × 速度／功率）。
- **在測試之前，暫時的設定（推估）**：
  - LTHR 維持 155，不要改成推估的 157–163。理由是：低估 LTHR 只會讓輕鬆跑偏保守，風險低；高估會把輕鬆跑變硬。
  - 熱天的輕鬆跑以**功率 ≤ 80% CP（163 W）且心率 ≤ 138–141** 為準，哪一條先到就停在那裡。熱天心率先到是正常的，**走路或放慢都可以**（`TERRAIN_NOTE`、Seiler「easy days easy」）。
  - 測出 LTHR 之後，才跟著重算 AeT 的暫用值。

### 3.3 可以用與不能用的轉換

| 轉換 | 判斷 |
|---|---|
| 30 分 TT → LTHR（後 20 分心率）＋ CP 檢查（全程功率） | **可以**（Friel，教練；30 分 TT 的強度和 CP 一致，Jones 2019） |
| CP 測試 12′ 段後半心率 → LTHR 的上界 | 可以，只當上界 |
| 涼天 CP 附近穩定段的心率 → LTHR 交叉檢查 | 可以，誤差 ±16 bpm（Micheli 2025） |
| CP 推出的心率直接寫成 LTHR | **不可以**（個人誤差 ±16 bpm；熱天會偏） |
| 0.89 × LTHR → AeT | 暫用可以，但要標「推估」。不是 LT1 的測量（`aerobic-base-readiness.md:254`） |
| % CP 區間 ↔ % LTHR 區間互推 | **不可以**（沒有來源；這位選手冬天 88% CP 時心率就已經約 100% 的現行 LTHR） |
| HRmax × % → 區間 | **不建議**（LT 60–90%、MLSS 75–97% HRmax，Iannetta 2020） |
| COROS 帳號 zoneData（LTHR 182） | **不可以**（app 已忽略，正確） |

**接下來要測什麼、在什麼條件下測**

1. **30 分鐘獨跑 TT（LTHR＋CP 檢查）**：
   - 條件：平路環線或田徑場、**< 25 °C**（最好 Hadley < 120，台灣 11–2 月的清晨）、**戴胸帶**、前 48 小時沒有硬課。
   - 暖身 15 分鐘後，全力均勻跑 30 分鐘，在第 10 分鐘按 lap。
   - 結果：LTHR = 後 20 分平均心率；30 分平均功率和 CP 204 比對。差 > 5% 時（推估）就重做 CP 測試。
2. **AeT 測試**：
   - 徐國峰 90 分鐘版：平地、< 25 °C、E 配速固定，(HR90 − HR10)/HR10（`跑者都該懂的跑步數據，讀書心得.md:58-67`）。
   - 或 UA 版：10 分暖身＋40–60 分固定功率，Pw:HR < 5%（`aet_test.py`）。
   - 從暫用的 AeT（138）或聚合估計的心率開始。現有資料裡飄移 < 5% 的前半段心率最高到 162（自動估算的 `below`），所以真實 AeT 很可能 > 138（推估）。
3. **CP 測試重做**：
   - 涼天、3′ 也要全力（這次 3′ 沒有全力，兩點法失效，W′ 只能用先驗）。
   - 或改用 30 分 TT＋3′ 的組合（`cp_protocols` 的方法表）。
4. **實驗室（選做）**：跑步機乳酸或氣體分析測試，一次拿到 LT1、LT2 和 HRmax。這是最準的錨點，可以拿來校正上面所有的推估。

### 3.4 app 該做的改動

1. **LTHR 的來源標籤**（最重要）：
   - 現在 `apply-estimate` 寫進去的 LTHR，在 `zones.threshold_info`（`zones.py:155-159`）、`zones.training_targets`（`zones.py:277-278`）、`racepower.athlete.thresholds_as_of`（「測試 …」）都顯示成「你的測試」。
   - 改法：`planning.Threshold` 加 `lthr_method`／`aethr_method`（比照 `cp_method`）。值可以是 `estimate`、`friel30`、`race`、`lab`、`manual`。
   - `api/plan.apply_estimate` 寫 `estimate`。顯示時改成「自動估算（已套用 2026-09-30）」。
2. **LTHR 估算的門檻不該只看 as-of CP**（`threshold_estimate.run_threshold`／`RunThreshold.qualifies`，`threshold_estimate.py:45-46`；`thresholds.estimate`）：
   - (a) 只收 Hadley < 150（或 < 25 °C）的跑步。天氣從 `heat_data.exposures` 讀；FIT 資料集要用開始時間對應 WKO5 的天氣檔名，現在只對到 440／803 筆。
   - (b) 30 分鐘窗還要 ≥ 該跑者 90 天最佳 30 分鐘功率的 97%（推估）。
   - (c) 結果標「推估」，旁邊附上 `hr_at_cp` 的交叉檢查與 ±16 bpm 的說明（Micheli 2025）。
   - 理由：§1.6。as-of CP 被低估時，入選的是 Z2 跑，LTHR 會被低估。
3. **新增 30 分 TT 的分析與套用**：
   - 在 `cp_protocols`（已有 `tt20` 方法）加 `tt30`。偵測一段 ≥ 28 分鐘的連續全力段，產出 LTHR（後 20 分心率）、30 分平均功率，加上天氣和胸帶的提醒。
   - 套用時走 `apply-estimate`（`lthr_method="friel30"`）和 `apply-cp`。
   - `workout_review` 加一個「套用這次的 LTHR」動作，比照 `_aet_test_lines`（`workout_review.py:2064`）。
4. **LTHR 也接上 B3 的事件觸發**：在 `quality_gate`（比照 `aet_test_reason`，`quality_gate.py:750-790`）加 `lthr_test_reason`。理由包括：
   - LTHR 的 method 是 `estimate`。
   - 涼天 HR-at-CP 中位數和 LTHR 差 > 5 bpm（≥ 3 次，推估）。
   - 停跑 ≥ 4 週。
   - 第一次轉涼（連續 3 個路跑日清晨 < 25 °C 且 Hadley < 150，推估；見 §2.5 第 5 點，`zone_events.cool_season`）。
   - `status.i_testing`（`status.py:686-735`）的 LTHR 不要只看日期。
5. **熱天的輕鬆跑判定**：
   - `workout_review` 的 easy 規則與 `adapt` 的規則 D，目前只要心率 > AeT + 3 就算「偏強」。熱天（Hadley > 150）時改成：**功率 ≤ 80% CP 且 RPE 輕鬆**也算達成，心率超標只提醒「熱天心率偏高是正常的，放慢或走」（推估；方向和 `unsourced-rules.md` B5 對規則 D 的建議一致）。
   - 課表推送的心率上限（`coros_workouts.easy_hr`）維持原樣。
6. **最大心率清理**：
   - `maximal.hrmax_observed` 已經用「撐 120 秒的前 5 名中位數」。前 5 名大多是越野賽，可能混入光學尖峰。
   - 建議再排除「心率 > 前 30 秒中位數 + 15 bpm 的跳變」（推估），並在 UI 標「觀測值，非實測」。
7. **文件／程式用字**：`fitdataset.py:68`、`racepower/intensity.py:9,27,38`、`racepower/maximal.py:69,74-75` 的「推估」依本專案用語改成「推估」。這只是用字，不影響行為。

### 3.5 一句話總結

- 現在：輕鬆跑上限 138 = 0.89 × LTHR 155。LTHR 155 是用夏天、被低估的 CP 175 W 推估出來的，很可能偏低（推估真值約 157–163）。
- 先不要動數字。等天氣轉涼，戴胸帶各測一次 30 分 TT（LTHR）和 90 分鐘或 40–60 分鐘的 AeT。測完後，心率區間用 Friel % LTHR、輕鬆跑上限用實測 AeT，功率區間用 Palladino % CP。
- CP 推心率只當交叉檢查（±16 bpm）。
- 區間依事件更新（新測試、熱校正後的 HR-at-power 偏移、長休、季節轉涼），不要只看日期。

---

## 附錄 A：重現本文數字

- 資料集：`backend.api.wko5views._dataset(source="coros")`，today = 2026-10-01，803 筆活動。
- 呼叫：
  - `thresholds.estimate(ds, today)`
  - `thresholds.estimate(ds, d, cp_of=lambda x: racepower.athlete.cp_as_of(ds, x))`（d = 8/14、9/13、9/29）
  - `drift_agg.aet_validity(ds, today, lthr=155)`
  - `zones.training_targets(ds, tday)`
  - `zones.threshold_info(...)`
  - `thresholds.estimate_tpace(ds, today)`
- 觀測 HRmax：每次活動心率排序後的第 120 名（= 撐 120 秒的值），365 天內取前 5 的中位數。
- CP 測試段：30 秒功率 > 190 W 且連續 ≥ 90 秒。
- 穩定段：`heat_data.steady_segments`（HRC_DAYS 暫改 800）。activity_weather 用開始時間（±2 分鐘）對應到 COROS 檔。
- DB 只用 `sqlite3.connect("file:...wko5coach.db?mode=ro", uri=True)` 讀 `athlete_settings`。

## 附錄 B：文獻（本次查證）

- Ausland Å, Kelemen B, Seiler S (2026). *Front Sports Act Living* 8:1806303. DOI 10.3389/fspor.2026.1806303（摘要）
- Galán-Rioja MÁ et al. (2020). Relative proximity of critical power and metabolic/ventilatory thresholds. *Sports Med* 50:1771–1783. DOI 10.1007/s40279-020-01314-8（摘要）
- Gielen J et al. (2026). *JMIR Form Res* 10:e85186. DOI 10.2196/85186（摘要）
- Gillinov S et al. (2017). Variable accuracy of wearable heart rate monitors during aerobic exercise. *Med Sci Sports Exerc* 49:1697–1703. DOI 10.1249/MSS.0000000000001284（摘要）
- Iannetta D et al. (2020). A critical evaluation of current methods for exercise prescription in women and men. *Med Sci Sports Exerc* 52:466–473. DOI 10.1249/MSS.0000000000002147（摘要）
- Jones AM et al. (2019). The maximal metabolic steady state: redefining the 'gold standard'. *Physiol Rep* 7:e14098. DOI 10.14814/phy2.14098（摘要）
- Kanniainen M et al. (2025). Threshold estimation in running using dynamical correlations of RR intervals. *Physiol Rep* 13:e70241. DOI 10.14814/phy2.70241（PMC12064344 全文）
- Lamberts RP, Lambert MI (2009). Day-to-day variation in heart rate at different levels of submaximal exertion. *J Strength Cond Res* 23:1005–1010. DOI 10.1519/JSC.0b013e3181a2dcdc（摘要）
- Lorenzo S et al. (2011). Lactate threshold predicting time-trial performance: impact of heat and acclimation. *J Appl Physiol* 111:221–227. DOI 10.1152/japplphysiol.00334.2011（摘要）
- Mann T, Lamberts RP, Lambert MI (2013). Methods of prescribing relative exercise intensity. *Sports Med* 43:613–625. DOI 10.1007/s40279-013-0045-x（摘要）
- Micheli L et al. (2025). Analysis of the factors influencing the proximity and agreement between critical power and maximal lactate steady state. *PeerJ* 13:e19060. DOI 10.7717/peerj.19060（摘要）
- Nixon RJ et al. (2021). Steady-state VO2 above MLSS: evidence that critical speed better represents maximal metabolic steady state in well-trained runners. *Eur J Appl Physiol* 121:3133–3144. DOI 10.1007/s00421-021-04780-8（摘要）
- Nuuttila OP et al. (2025). *Eur J Appl Physiol* 125:697–705. DOI 10.1007/s00421-024-05631-y（摘要；不是心率錨點的研究）
- 教練：Friel, "Joe Friel's Quick Guide to Setting Zones"，TrainingPeaks（本次核對原文）。
- 徐國峰：使用者 notes `C:\Users\<user>\Projects\notes\notes\300 Sport\60 🏃 有氧訓練\跑者都該懂的跑步數據，讀書心得.md`、`不同的心率區間模型比較.md`；私訊見 `docs/research/xu-guofeng-reply.md`。
- 沿用既有文件、本次沒有重讀的：Seiler & Kjerland 2006、Coyle & González-Alonso 2001、Lafrenz 2008、Wingo 2020、Beiter 2025、Hunt 2015／2019、Coyle 1986、Houmard 1992（出處見文中引用的 `docs/research/*.md` 行號）。
