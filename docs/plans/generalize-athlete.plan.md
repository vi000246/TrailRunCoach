# Plan：把「為作者一人調的」改成每位跑者都能用

- 日期：2026-10-02
- 狀態：**稽核＋設計，尚未實作**（這份只有文件，沒有改程式）
- 分支基準：`feat/overview-racepower-ui`（行號以 merge 後的 `4cd60fd` 為準）
- 類型：refactor（行為對作者不變）＋ feature（新設定、自動校正）；Size：L，分 8 批（§3）
- 相關：`docs/research/unsourced-rules.md` §0.5（可移植性、冷啟動、每人回測、§0.5.7 要改成每人參數的位置）、
  `docs/plans/todo-multi-user-sharing.plan.md` §3（`athlete_id = 1`、`~/.wko5coach` 單人假設；**本計畫不重做**）

## 0. 規則與分類

擁有者的規則：**「設定不要太複雜：能自動判斷就自動，不然放進階設定」**。每一項分成：

| 類 | 意思 | 什麼時候用 |
|---|---|---|
| **A 自動估算** | 文獻預設 ＋ 本人資料擬合，用 `w = n/(n+k)` 收縮，n 不夠就用預設（照 `drift_agg.py` 的 AeT 熱 β：`BETA_DEFAULT` 1.0 bpm/°C、`BETA_MIN_N` 10、`BETA_K` 20、`BETA_BOUNDS`，`drift_agg.py:113-201`） | 首選 |
| **B 一般設定** | 跑者自己知道、資料推不出來的：體重、性別、有沒有功率計、主要資料來源 | 只放少數幾項，第一次開啟時問 |
| **C 進階設定** | 很少人要改、預設對多數人都對的門檻 | 收合在「進階」裡 |
| **D 已通用** | 不用改。標 **D\*** 的是只需要改名、改文字，不用加設定 | — |

**數量**：A **24**、B **7**、C **7**、D **25**（其中 D\* 3 項）＝ 63 項。

### 0.1 共同決策（先定，後面每批都會用到）

| 問題 | 建議 | 理由 |
|---|---|---|
| A 類參數存在哪 | `user_settings` 新增 `athlete.calib.<name>`，存 `{value, se, n, fitted_at, source: default/fitted/user}`；`source=user` 永遠不會被自動覆蓋 | 跟 unsourced-rules §0.5.7 的設計一樣；已經有 `UnknownSetting` 守門（`settings/repository.py:28`） |
| 什麼時候重新擬合 | 新增 `engine/calibrate.py`，在同步匯入活動之後跑（和 `plan_auto` 同一條流程）。每項各自檢查 n 夠不夠，不夠就不寫入 | 不另開排程；作者的資料一跑就會得到跟現在常數接近的值 |
| 畫面怎麼呈現 | 每個 A 值旁邊放一個小 chip：「本人 n=…」或「預設（文獻／推估）」，hover 才顯示說明（照 `drift_agg.beta_text` 的作法） | 符合「UI 要簡潔」 |
| 作者的回歸保證 | 每個 A 項都要加一個**自我一致測試**：用合成 fixture 模擬作者的分佈，自動值要落在現在常數的 ±1 SE 以內；不能讀 WKO5 資料夾 | 測試不能依賴 WKO5 資料夾；行為對作者不能變 |
| 收縮常數 k、最少 n | 下面每項各自給，都標**推估** | 沒有文獻值 |

---

## 1. 稽核清單

格式：`檔案:行`（路徑相對於 `backend/`，除非另有寫）。「本人」= 註解寫明是用作者資料調出來的。

### 1.1 熱與溫度（T）

| # | 位置 | 現值 | 本人? | 類 | 怎麼改（A：資料／最少 n → 不足時） |
|---|---|---|---|---|---|
| T1 | `engine/heat.py:70-73` `HR_BETA/SE/REF/SRC`；使用者：`heat.py:76`、`racepower/trailhr.py:156-166`（`TRAILHR["heat_beta"]` :106）、`engine/zone_events.py:395,428,442,524,705`、`engine/panels/climb_vam.py:245-246`；文字：`racepower/backtest.py:1058`、`engine/workout_review.py:124` | β 0.224 ± 0.036 bpm/Hadley，參考 Hadley 120 | 是（271 段路線 effort，`scripts/heat_backtest.py`） | **A** | 把 `scripts/heat_backtest.py` 的 OLS（路線 FE＋功率＋移動分鐘＋時段＋β·(Hadley−120)）改成 `calibrate.fit_hadley_beta()`。資料：有功率（沒有就用配速）和路線天氣的跑步 effort。**最少 30 段、Hadley 跨度 ≥ 40**；k = 60（推估）。預設 β₀：用 `drift_agg` 的 Jenkins 2023 1 bpm/°C 換算成 Hadley 單位，**約 0.3**（露點固定時是 0.56，同時升時約 0.29；換算推估，**待決**）。要和 `drift_agg.heat_beta`（°C 單位）共用 calib 存放和 chip 文字，但兩個 β 分開存 |
| T2 | `engine/zone_events.py:124-126` `WATCH_BIAS_C/SD/PAIR_MIN`；文字：`engine/workout_review.py:123,126` | 手錶溫度 − 氣溫 +3.7 ± 2.7 °C | 是（72 對） | **A**（已經有一半） | 已經是「≥ 10 對就用本人的」。只要改兩件事：預設改標「推估（單一使用者）」；`workout_review.py:123` 的說明改成顯示實際用的值 |
| T3 | `engine/zone_events.py:126` `RH_DEFAULT` | 濕度 83 % | 是（608 筆活動中位數） | **A** | 用本人有天氣快取的活動算 RH 中位數。**≥ 20 筆**；不足時用 60 %（推估，溫帶中位） |
| T4 | `engine/heat.py:29-32`、`engine/route_weather.py:46-49` `PARTIAL_HADLEY` | 130（理由是「台北夏天傍晚」） | 是 | **C** | 150 是 Hadley 表（教練），保留。130 只在熱適應累積用，收進進階 |
| T5 | `engine/workout_review.py:109-113`、`zone_events.py:105` 溫度分區 | < 25 / 25–28 / > 28 °C | 否（徐國峰、Beiter 2025） | D | 分區是生理門檻，跟台灣無關（註解提到台灣只是為什麼要分區） |
| T6 | `racepower/heatacc.py:21` `A_DEFAULT` | 0 | 是 | D | 已經是「本人 HRC 趨勢顯著才給分」（`MIN_ROWS` 8），對別人一樣成立 |
| T7 | `engine/drift_agg.py:113-201` AeT 熱 β | 文獻 ＋ 收縮 | — | D | 範本 |
| T8 | `engine/heat_data.py:35-76` 住家氣候、賽季熱不熱 | 自動 | — | D | 住家 = 快取最多天的天氣格點，誰都適用 |

### 1.2 速度、地形、爬坡（G）

| # | 位置 | 現值 | 本人? | 類 | 怎麼改 |
|---|---|---|---|---|---|
| G1 | `engine/equivalence.py:90,201` `DEFAULT_V_FLAT` | 8.0 km/h | 否（最後備援） | **A** | 現在已經依序用：≥ 3 次輕鬆路跑中位 → ≥ 8 次心率回歸 → 平路中位 → 8.0。最後一級改成「閾值配速 × 0.75」（`thresholds.py` 有 tpace 時，推估）；都沒有才用 8.0 並標推估 |
| G2 | `engine/equivalence.py:37,43-44` 換算公式選 EP 不選 Naismith | EP | 是（9 次越野，5.6 vs 11.4 % MAPE） | **A** | 每人用 LOO MAPE 選：**≥ 8 次輕鬆越野**才比；不足時用 EP |
| G3 | `engine/algorithms/effort.py:36-40` `fitted_run 153`、`fitted_hike 111`；使用者：`racepower/trailhr.py:14,92`、`racepower/athlete.py:1165,1179`、`racepower/backtest.py:472` | 每 153 m 爬升 = 1 km | 是（430 筆最小平方） | **A** | 每人最小平方擬合除數，收縮到 ITRA 100：k = 10 次（推估）。**≥ 8 次有爬升的跑步**；不足時用 100（ITRA） |
| G4 | `engine/climb_vam.py:41-45` `GRADE_MIN`、`MIN_SEG_S`、`JOIN_GAP_S` | +8 % / 8 分 / 30 s | 是（73 筆 → 93 段） | **A** | 自適應：預設 8 %／8 分；最近 180 天 **< 20 段**時，依序放寬到 6 %／5 分（推估），chip 寫出用的是哪一級 |
| G5 | `engine/climb_vam.py:3-5,33` 「大多用走的」前提 | 註解 | 是 | D | 每段的「跑／走」已經用步頻量（≥ 130 spm），不是假設；只是說明文字 |
| G6 | `racepower/seg_targets.py:22-26` `RUN_CLIMB (0.03, 0.08)`：> 8 % 當成走；`racepower/grade_model.py:149` `WALK_MAJORITY` | 8 % | 一半（8 % 是 Stryd 驗證範圍，van Rassel 2026） | **A** | 功率上限 8 % 保留（D）。「從幾 % 開始走」改成每人的值：越野跑每個坡度箱的跑步步頻比例 < 50 % 的最低坡度。**每箱 ≥ 30 個 100 m 窗**（同 `grade_model.SHRINK_N`）；不足時用 8 % |
| G7 | `engine/steep_hill.py:39-40` `BASE_GRADE 12`、`BASE_KMH 3.5` | 12 % 時速 3.5 | 否（研究文件的例子） | **A** | 用 `grade_model.HikeSpeed` 本人 +12 % 箱的速度；箱裡 **≥ 30 窗**才用，不足時用 3.5 |
| G8 | `racepower/course.py:33-36` | 平 2 %、陡 15 %、走 28 % | 否（Giovanelli 2016） | D | — |
| G9 | `racepower/grade_model.py:24,152-157` 技術係數箱 | n/(n+30) | — | D | 已經收縮 |
| G10 | `racepower/hike.py:24-25,66-77,147-153` | 移動比例 0.8、多日疲勞 | — | D | 已經有「≥ 3 趟用本人的」 |
| G11 | `engine/workout_steps.py:63,720,727` | 6:00/km、走 5 km/h | — | D | 只有沒有個人速度時才用 |
| G12 | 越野判定不一致：`engine/overview.py:73`、`racepower/athlete.py:485`、`engine/status.py:153`、`engine/panels/climb_vam.py:28`（只看 `runningtrail` tag 或 sub_sport）vs `engine/workout_review.py:820`（加看爬升率） | tag | 是（WKO5／TP tag） | **A** | 統一成一個 `is_trail(w)`：tag **或** sub_sport **或** 爬升率 ≥ `classify.TRAIL_CLIMB_RATE_M_PER_KM`（20 m/km）。純 COROS／Garmin 使用者沒有 TP tag 也能判斷 |
| G13 | `engine/algorithms/climbs.py:47` | 80 m / 3 % | 否 | D | — |

### 1.3 生理、身體、比賽判定（P）

| # | 位置 | 現值 | 本人? | 類 | 怎麼改 |
|---|---|---|---|---|---|
| P1 | `racepower/fuel.py:80` `BODY_DEFAULTS`（175 cm／40 歲／男，警告文字 :677）；性別預設男：`racepower/athlete.py:991`、`racepower/backtest.py:883`、`racepower/cptest.py:91,103`、`racepower/cp.py:82`、`engine/cp_protocols.py:254`；**沒讀性別、直接用男性 W′ 13100**：`engine/workout_review.py:1050`、`engine/interval_eval.py:63`、`engine/cp_protocols.py:43` | 男 | 是 | **B** | 性別、身高、**出生年**（新增）放一般設定；`planning.py:196-212` 的 profile 已經有 sex、height。三個沒讀性別的地方要改成讀 profile。沒填時才用預設，並保留警告 |
| P2 | `engine/bad_activity.py:76` 70 kg；`static/settings.html:388` 新增體重預設 65 | 體重 | 否 | **B** | 體重是一般設定（已有 dated weights）；第一次開啟時必填。COROS 登入時寫進來的體重（`sync/coros_client.py:206-229`）當預填 |
| P3 | `racepower/capacity.py:45-49` 背包 9 kg、`TRIP_DAYS_DEFAULT 3`、`TRIP_KIND_DEFAULT "group"`；`engine/planning.py:77`；`static/plan.html:131`；`api/plan.py:125`；`static/racepower.html:493`；`engine/steep_hill.py:46` `DEFAULT_PCT 0.13`（9／68 kg） | 9 kg、跟團、3 天 | 是（使用者決定） | **B** | 這些本來就是每場活動的欄位（`pack_kg`、天數、跟團／自走）。預設改成「體重 × 13 %」（UA trekking 例子，推估），不再寫 9 kg；跟團／自走沒有預設、要選 |
| P4 | `racepower/capacity.py:41,47`、`racepower/fuel.py:44,79` | 越野背心 2 kg、每天少 0.7 kg | 部分 | **C** | 進階 |
| P5 | `racepower/athlete.py:37` `FALLBACK_TRAINING` | 海拔 100 m、25 °C、75 % | 是（住家條件） | **A** | 用本人活動的海拔中位數和住家格點的氣候（`heat_data` 已經有）。**≥ 10 筆有天氣的活動**；不足時用 `racepower/env.py:19-21` 的參考條件（200 m／12 °C／70 %） |
| P6 | `racepower/env.py:19-21` | 200 m／12 °C／70 % | 否 | D | 參考條件 |
| P7 | `engine/wko5expr/fitdataset.py:90-91,677-682` 不採用 COROS 的 LTHR／FTP | 一律忽略 | 是（作者 LTHR 182 > 測試峰值 171） | **A** | 改成「COROS 值當先驗，跟本人觀測一致才用」：LTHR ≤ 最近 90 天 30 分硬段峰值心率 + 3 bpm（推估）才採用，否則忽略並註明。沒有硬段時直接採用並標「來自手錶」 |
| P8 | `racepower/maximal.py:68-83` 越野全力：≥ 10 km 且 ≥ 90 分 | 10 km／90 分 | 是（「他的比賽都 > 10 km」） | **A** | 用本人標記全力的比賽的距離／時間 p10。**≥ 3 場**；不足時用 10 km／90 分 |
| P9 | `engine/activity_tags.py:47-50,82-84` 長休息 ≥ 5 分、`rest_max 0.10` | 10 % | 是（7 場日記比賽 0–5 %） | **A** | 照 unsourced-rules §0.5.2：本人全力比賽的長休息比例 p90 × 1.5（推估）。**≥ 3 場**；不足時用 10 % |
| P10 | `engine/workout_review.py:129-158` 飄移視窗：`DRIFT_EARLY_S 1200`（為「市區段」設計）、`DRIFT_TAIL_S 720`／`GAP 360`、`DRIFT_MAX_VI 1.04`、`WALK_*` 75 %／60／180 s、`DRIFT_TAU_S 60` | — | 是（「calibrated on the user's runs」） | **A** | 照 unsourced-rules §0.5.1 的表，那裡已經寫好每一項的方法和最少資料：暖身／回程 = 本人停等時間 p95（**20 次路跑**）；τ 用加總對數概似（**20 次 ≥ 30 分、有功率**）；VI 用跑步機／田徑場參考跑（**3 次參考 ＋ 20 次輕鬆跑**）；走路段（**10 次跑走 ＋ 10 次穩定跑**）。不足時用現值 |
| P11 | `engine/workout_review.py:1100-1102`（3′ ≥ 98 % CP）、`racepower/cptest.py:34-35`（1.3×、10 bpm）、`workout_review.py:1781-1782` | — | 是（2026-09-30 測試） | **C** | 用 ROC 校正要有多次正式測試，不值得做；收進進階 |
| P12 | `engine/target_policy.py:90-93` AUTO：路跑輕鬆／長跑用功率 | 功率 | 是（作者決定） | **A** | auto = 最近 8 週路跑 **≥ 70 % 有 Stryd 功率**（`power_source`）而且有 CP 才用功率，否則用心率（推估）。`plan.prefs.target_basis` 照舊可以手動覆寫 |
| P13 | `engine/workout_review.py:1420-1427` 登山算品質課 | 開 | 是（為了百岳） | **C** | 進階開關，預設開（只有 hike 會受影響） |
| P14 | `engine/ai/knowledge.py:1-4,17-18`：「偏無氧型（RWC 充足）…爬升配速約 30 min/km @ HR 160」寫死在每次 LLM 的 system prompt；:19-20 TSB 分段跟 `status.py:47-50` 不一致 | 作者個人特性 | 是 | **A** | 拿掉寫死的個人描述，改由 `ai/context.py` 在執行時從本人資料產生（CP、W′/CP、越野爬升 VAM、HR）；TSB 分段改成引用 `status.py`。沒有資料就不寫這一段 |
| P15 | `engine/wko5expr/fitdataset.py:73-77` `CP_FIT_MIN_RUNS 5` | 5 | 是 | D | 最少 n 規則，對誰都合理 |
| P16 | `racepower/trailhr.py:91-131`（δ、x\*、收縮）、`racepower/riegel.py`、`racepower/athlete.py:30,34`（TTE 3000、k −0.07）、`racepower/backtest.py:75-92`（通過門檻、`MIN_N` 5） | — | — | D | 已經是先驗＋收縮，或是族群值、產品決定 |
| P17 | `engine/adapt.py:26-29`、`engine/bad_activity.py:62-67`（世界紀錄 × 1.15）、`engine/zone_events.py:107-133`（光學心率異常值） | — | 部分 | D | 規則偏保守，換別人也成立；胸帶心率一樣能用 |
| P18 | `racepower/maximal.py:109-111` HRmax 忽略突波 | — | 是（例子） | D | 規則本身通用 |

### 1.4 裝置與資料來源（S）

| # | 位置 | 現值 | 類 | 怎麼改 |
|---|---|---|---|---|
| S1 | `settings/repository.py:57` `power.accept_watch_power = False`；`engine/power_source.py:18-20,87`；只用 Stryd：`engine/thresholds.py:213-214,226`、`engine/wko5expr/fitdataset.py:845-915` | 只用 Stryd | **A** | 預設改成 `None = auto`：最近 90 天 **≥ 5 次 Stryd 跑步**就只用 Stryd（現在的行為）；完全沒有 Stryd、但有手錶功率時，就用手錶功率（同一個量尺，不會混）。手動開關移到進階 |
| S2 | `engine/planning.py:196-199` profile `power_meter`（stryd/coros/garmin/other）**沒有地方讀**；`settings/repository.py:61` `charts.power.enabled` 是同一件事 | — | **B** | 合併成一項一般設定「功率來源：Stryd／手錶／沒有」，預設值從資料偵測（`power_source`）預填；`charts.power.enabled` 由它推出 |
| S3 | 寫死 Stryd 的文字：`static/settings.html:116`、`static/racepower.html:396,958,1082,1145,1398,1522`、`static/activity.html:355`、`engine/interval_eval.py:421`、`engine/workout_review.py:3072,3117,3232`（`NO_STRYD_NOTE`）、`views/training.json:191,296`、`views/workout.json:81` | 「沒戴 Stryd」 | **B** | 跟著 S2 顯示：沒有功率計 → 「沒有功率」；ILR／LSS 這種只有 Stryd 有的欄位才寫 Stryd |
| S4 | 只能推到 COROS：`engine/plan_auto.py:5,30,230,260,605`、`sync/coros_workouts.py`、`engine/workout_steps.py:60`、`settings/repository.py:118-122`（`plan.auto.push` True、`notify` "watch"）、`static/shell.js:17` | COROS | **B** | 「推課表到手錶」只在 COROS 已登入時才顯示和預設開；沒有 COROS → push 關、notify = overview。Garmin 推送不在這份計畫 |
| S5 | `settings/repository.py:33` `sync.primary_source = None`（先匯入的優先） | — | **B** | 一般設定「主要資料來源」，預設 = 最近 90 天活動最多的來源（自動預填） |
| S6 | WKO5 是預設：`settings/paths.py:22,52`（只找 `~/WKO5`）、`api/athletes.py:14,26-45`（只從 WKO5 資料夾建運動員）、`files/file_service.py:19`、`api/wko5views.py:50-51`；**沒有 WKO5 會壞**：`api/plan.py:38,209`、`engine/wko5expr/dataset.py:239` 用沒有預設值的 `next(glob(...))`（讀程式推斷，未實測）；`settings/repository.py:50` `charts.data_source "wko5"`；`engine/wko5expr/config.py:29` `parity True`；`static/settings.html:134,417` | WKO5 | **A** | `charts.data_source` 預設 `None = auto`：有 WKO5 資料夾 → wko5，否則用 S5 的主要來源；`parity` 沒有 WKO5 時預設關；`next(..., None)` 加防護；`athletes.bootstrap` 在沒有 WKO5 時建一位空的運動員 |
| S7 | `WKO5 Season View/`、`WKO5 Workout View/`（repo 根目錄，當成 app 的視圖）、`views/wko5_fixes.json:4-149`、`engine/wko5expr/chartfixes.py:14`、`views/workout.json:6,28,49,59,81`、`engine/algorithms/validator.py:4`、`engine/panels/activity_charts.py:22,255`、`files/wko5_athlete.py:70`、`files/wko5chart_reader.py:174-175` | 作者名字 | **D\*** | 改名成「預設季節視圖」「預設單次視圖」，`wko5_fixes.json` 的 key 一起改（加別名，舊 key 照樣能讀） |
| S8 | `athlete_id == 1` 寫死：`api/plan_sessions.py:156,1418`、`engine/plan_auto.py:378`、`api/dashboard.py:69`、`api/auth.py:195`、`frontend/src/pages/SyncPage.tsx:26`；預設值 `athlete_id: int = 1` 約 60 處 | 1 | D | 一台電腦一位使用者時沒問題（multi-user plan §3 (a)）。上面 6 處**寫死的**在 B0 順手改成用目前的運動員，其他交給 multi-user plan |
| S9 | `sync/tp_client.py:61,189,1047,1055`、`settings/tp_client.enc`、`settings/repository.py:39` | WKO5 client | D | 散布問題在 multi-user plan §2.1 |
| S10 | `sync/coros_client.py:28,34-37,110`、`sync/tp_client.py:642,737` | 起始日 2020／2010、區域預設 US | D | 通用 |
| S11 | `files/fit_reader.py:2-7,154`（COROS 韌體 bug）、`engine/interval_reps.py:6,56`（COROS lap 優先，有退路）、`engine/panels/fatigue.py:2-16`（COROS 負荷比分段）、`engine/status.py:865-876`／`engine/quality_gate.py:195`（偵測 WKO5 預設 LTHR 160）、`engine/thresholds.py:59`（WKO5 mFTP 備援）、`engine/activity_tags.py:347-353`（RPE 從任何 FIT 讀） | — | D | 有資料才用，沒有就跳過 |
| S12 | `engine/power_source.py:7-13` 註解寫作者的裝置史（2025-03-19、2023-04…2024-09） | — | **D\*** | 只改註解；判斷邏輯（Stryd developer fields、manufacturer 95）本來就通用 |

### 1.5 地區與環境（L）

| # | 位置 | 現值 | 類 | 怎麼改 |
|---|---|---|---|---|
| L1 | `racepower/weather.py:44` `TZ = UTC+8`、:263-270,312,318；:513,539,582 Open-Meteo `"timezone": "Asia/Taipei"`；`api/racepower.py:665` | 台灣時區 | **A** | 跟 `route_weather.py:45` 一樣用 `"timezone": "auto"`（依座標），回傳的 `utc_offset_seconds` 取代 `TZ` |
| L2 | `settings/repository.py:30,287-298` `athlete.timezone` 有欄位但**設定頁沒有**；Docker 沒設 `TZ`（`Dockerfile:10`）→ UTC；很多端點用 `date.today()`（`api/plan.py:224`、`api/achievements.py`、`api/analytics.py`、`api/athletes.py:121`、`api/pmc.py`、`api/racepower.py`、`api/wko5views.py`） | 系統時區 | **A** | `athlete.timezone = None` 時依序：最近 FIT 的 `local_timestamp − timestamp` 位移 → 瀏覽器 `Intl` 時區（第一次開啟時寫入）→ 系統。`date.today()` 改成一個 `today_local()` |
| L3 | 百岳：`data/baiyue.json`、`racepower/weather.py:33,96-116`（山名查座標）、`engine/achievements.py:34-49`（百岳 ≥ 3000、中級山 ≥ 1500）、`engine/planning.py:44` kind `baiyue`、`db/models.py:233` 百岳跟團、`racepower/athlete.py:229-232`／`engine/equivalence.py:92`（百岳多跟團）、`racepower/planner.py:854-857`；預填：`static/racepower.html:211,310,311,346,480`（玉山 2 日、台北馬、23.47,120.957）、`static/plan.html:114,123`、`static/achievements.html:56,89,178-188` | 台灣 | **A** | 新增 `athlete.region`（自動）：住家格點（`heat_data`）在台灣 bbox（`scripts/build_baiyue.py:38`：21.5–26.5 N、118–122.5 E）→ `tw`，否則 `intl`。`tw` 照現在；`intl` 隱藏百岳成就、山名查詢，`baiyue` 活動顯示成「多日登山」，預填換成中性範例。資料模型不改名（舊資料照讀） |
| L4 | `settings/repository.py:68,132-133` 預設底圖 `rudy`；`static/basemaps.js:12-31,41`、`static/wko5_viewer.html:528,1891-1926`（魯地圖、NLSC） | 魯地圖 | **A** | 跟 L3 同一個 region：`intl` 預設 `osm`，NLSC／魯地圖選項只在 `tw` 顯示 |
| L5 | `racepower/weather.py:6-8,35-38`、`api/racepower.py:6-7,109-134`、`static/racepower.html:387-390,776,785,1616-1620` CWA | 台灣氣象署優先 | **C** | 本來就是「5 km 內有 CWA 點才用」（`CWA_MATCH_KM`），國外自動退回 Open-Meteo。API key 欄位收進進階、只在 `tw` 顯示 |
| L6 | `racepower/planner.py:857` `AMS_TOP_M 3500` | 依台灣高山 | **D\*** | 改成 2500 m（WMS 高山症指引的門檻），只影響提示 |
| L7 | `settings/repository.py:101-104` `warmup_commute_min 10`（「市區跑到河濱」）；`engine/interval_library.py:237-239`、`engine/workout_steps.py:10,78`、`engine/workout_review.py:104,497`、`static/schedule.html:588-589,1734,1763` | 10 分、河濱 | **C** | 設定已經有（課表偏好），預設改 0、文字改「暖身跑到間歇地點」；作者自己設 10 |
| L8 | `settings/repository.py:96-98` `aet_test_days "weekday"`（「週末跑越野」） | 平日 | **A** | auto = 避開本人慣用的長跑日（`overview.py:368-378` 已經從 12 週推出）；選項照舊 |
| L9 | `settings/repository.py:88-89` `cp_test_protocol "quick"` | 20 分全力 | **C** | 設定已存在，預設保留 |
| L10 | 中文關鍵字：`racepower/maximal.py:84` `RACE_WORDS`、`racepower/trailhr.py:135-136`、`racepower/course.py:39`、`racepower/fuel.py:95`、`engine/aet_test.py:111`、`engine/activity_tags.py:74,90`；只有公制 | — | D | i18n 另案 |
| L11 | `engine/algorithms/routes.py:18` 格點 0.001°（以 25 N 估）、`racepower/hike.py:22-23` 箭竹地形 | — | D | 緯度 60 N 時格點仍約 55 m，夠用；多一個地形選項無害 |
| L12 | `start.sh:6`（homebrew python）、`docker-compose.yml:10-12`（掛 `~/WKO5`） | — | D | 部署，multi-user plan 處理 |

---

## 2. 一般設定與進階設定長什麼樣

**一般設定**（第一次開啟時問一次，之後在設定頁最上面）只有 B 類 7 項，合併後是 5 個欄位：

1. 體重（P2）
2. 性別、身高、出生年（P1）
3. 功率來源：Stryd／手錶／沒有，自動預填（S2、S3）
4. 主要資料來源，自動預填（S5）；有登入 COROS 時多一個「推課表到手錶」（S4）
5. 每場活動的背包重量和跟團／自走（P3）：這兩項在活動表單裡，不放設定頁

**進階**（收合）：C 類 7 項（T4、P4、P11、P13、L5、L7、L9），加上 A 類每項的「手動指定」（存成 `source=user`）。A 類不出現在一般設定。

---

## 3. 實作批次

| 批 | 內容 | 項目 | Size | 依賴 |
|---|---|---|---|---|
| **B0** 沒有 WKO5 也能開 | `next()` 防護、資料來源與 parity 自動、空運動員 bootstrap、6 處寫死的 `athlete_id == 1` | S6、S8 | M | — |
| **B1** 校正基礎 | `athlete.calib.*` 存放、`engine/calibrate.py`（同步後跑）、chip 元件、自我一致測試的 fixture 樣板 | （基礎） | M | B0 |
| **B2** 一般設定＋首次精靈 | 5 個欄位；三個沒讀性別的地方改成讀 profile；Stryd 文字跟著功率來源；推送只在 COROS 登入時開 | P1、P2、P3、S2、S3、S4、S5 | M | B0 |
| **B3** 地區與時區 | `athlete.region`、`timezone` 自動、`today_local()`、weather 改 `"auto"`、底圖預設、AMS 文字 | L1、L2、L3、L4、L6 | M | B1 |
| **B4** 熱與溫度 | Hadley β 擬合（T1，含預設 β₀ 換算的決定）、RH、住家條件、錶溫偏差文字 | T1、T2、T3、P5 | M | B1、B3（住家格點） |
| **B5** 功率與比賽判定 | 手錶功率 auto、COROS LTHR 先驗、全力門檻、長休息、target basis auto、AeT 測試日、AI prompt | S1、P7、P8、P9、P12、L8、P14 | M | B1、B2 |
| **B6** 速度與地形 | v_flat 備援、EP／Naismith 選擇、爬升除數、爬坡段自適應、走路坡度、陡坡速度、越野判定統一 | G1、G2、G3、G4、G6、G7、G12 | L | B1 |
| **B7** 飄移視窗校正 | unsourced-rules §0.5.1 的整組（暖身、回程、VI、走路段、τ） | P10 | L | B1 |
| **B8** 進階收合＋清理 | C 類收進進階；D\* 改名與註解 | T4、P4、P11、P13、L5、L7、L9、S7、S12 | S | B2 |

**順序**：B0 → B1 → B2 → B3 → B4 → B5 → B6 → B8 → B7。

- B0 先做：沒有 WKO5 的人現在可能連課表頁都開不了。
- B2 早做：性別和體重是很多 A 項的輸入。
- B3 在 B4 前：熱需要住家格點和正確時區。
- B7 最後：最大、最容易動到作者現在的飄移結果；而且 §0.5.4 的「環境改變偵測」要一起設計。

**每批的驗收**：

- 作者的資料：自動值落在現在常數的 ±1 SE 內，或寫明差多少、為什麼。
- 一個合成的「新使用者」fixture：只有 COROS、沒有 Stryd、不在台灣、20 筆活動。每頁都能開，chip 都顯示「預設」。
- 測試不讀 WKO5 資料夾。

## 4. 待決（需要擁有者決定）

**擁有者決定（2026-10-02）**：
1. T1：沒有個人 β 時用換算的預設值（約 0.3 bpm/Hadley，標推估），有資料再收縮到個人值。
2. L3：region 先只分 `tw`／`intl`。
3. S1：沒有 Stryd 的人**預設用心率**（不用手錶功率）；手錶功率只在進階設定手動打開。

以下為原始提問，保留作紀錄：

1. **T1 的預設 β₀**：用 Jenkins 換算約 0.3 bpm/Hadley（推估），還是沒有個人 β 時就不做 Hadley 修正（unsourced-rules §0.5.2 的「無 → 用 Hadley 表」）？建議用換算值，標推估，因為不修正會讓非作者的越野心率預測整季偏差。
2. **L3 的 region**：只分 `tw`／`intl` 兩種就好？還是之後要給日本、香港各自的山岳清單？建議先兩種。
3. **S1**：沒有 Stryd 的人用手錶功率，功率模型（賽事功率、CP）會開始對他們出數字，但準度沒驗證過。建議開，回測沒通過就照現在的規則標「推估」。
