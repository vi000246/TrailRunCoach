# 「推估」常數盤點與個人化校正規劃（SP-69）

> 調查日期：2026-10-05。只做調查，沒有改程式。
> 標記沿用 `unsourced-rules.md`：**已驗證**＝這次讀到原文或摘要；**未驗證**＝只看到轉述；**推估**＝程式自己定的數字，或我的延伸。
> 來源等級：同儕審查／教練／平台／社群。
> 分類沿用 `docs/plans/generalize-athlete.plan.md` §0：**A** 自動估算、**B** 一般設定、**C** 進階設定、**D** 已通用（不用改）。

## 摘要

1. 掃描 `backend/`（不含測試）的模組層大寫常數，鄰近註解有「推估」的共 **545 行**，其中數字常數 **495 個**；本行直接寫「推估」的 **269 個**。另有約 **60 個**放在字典、tuple 或行內，掃描抓不到，用人工補（§1.3）。合計約 **550 個**。
2. 其中 **17 個已經有自動校正**（`engine/calibrate.py` 的註冊項目，§2），**TL 換算和賽後 TSS 校正也已經是「先驗＋本人資料收縮」**。這些不用再規劃。
3. `generalize-athlete.plan.md` 分過類的是 63 項，`unsourced-rules.md` §0.5 分過的是飄移、賽事功率、課表三張表。**之後新增、還沒分類的主要在課表產生這一塊**（雙軌解鎖、強度預算、CTL 目標、轉換期、專項期、B2B、技術地形、恢復期），約 175 個。
4. 大多數不該個人化。原因有兩種：改了也不影響課表（顯示用、資料清理用），或是沒有可以量的結果（例如「幾週才解鎖 3 區」沒有對錯可驗）。真正值得做的是 **7 項**（§4.1）。
5. 查來源時發現 **三處程式引用和原始出處對不上**（§5）：強度預算 20 % 引 Seiler 80/20，但 Seiler 的 80/20 是算「堂數」不是「時間」；恢復週 65 % 比找到的教練說法輕；Daniels「T ≤ 10 %」只找到二手整理。
6. 有 **2 個常數已經沒有被引用**，可以刪（§4.4）。

## 1. 盤點方法與範圍

### 1.1 怎麼掃的

- 條件：`backend/**.py`（排除 `tests/`），行首是大寫名稱的指派，值含數字，且前後 2 行內出現「推估」。
- 結果存成清單逐行讀過，每一行的來源以**程式註解**為準，再對照 spec 和研究文件。
- 限制：
  - 「前後 2 行」會把緊鄰推估常數的有來源常數也算進來（例如 `adapt.py` 的 `EASY_POWER_CAP`，來源是 Palladino）。所以 495 是上限，269 是下限。
  - 字典、tuple 解包、函式內的數字掃不到，§1.3 補了我找到的部分，**不保證完整**。
  - 前端（`static/*.js`、`*.html`）和 `views/*.json` 裡的數字沒有掃。

### 1.2 各領域筆數

| 領域 | 主要模組 | 行數 | 數字常數 | 之前有沒有分類過 |
|---|---|---|---|---|
| 課表產生與護欄 | `quality_gate`、`base_check`、`load_guard`、`adapt`、`reentry`、`overview`、`plan_prefs`、`plan_auto`、`planning`、`specific_phase`、`b2b`、`technical`、`steep_hill`、`primary_sport`、`compliance`、`aet_test`、`template_recs`、`injuries`、`injury_exposure` | 175 | 159 | 部分（`unsourced-rules.md` §0.5.3 只有 6 列） |
| 飄移與單次活動判讀 | `workout_review`、`drift_agg`、`climb_vam`、`activity_tags`、`bad_activity`、`routes`、`equivalence` 等 | 103 | 92 | 有（plan P10、G1–G4；`unsourced-rules.md` §0.5.1） |
| 賽事計算機 | `racepower/*` | 101 | 93 | 有（plan P、G；`unsourced-rules.md` §0.5.2） |
| 間歇判定與課表步驟 | `interval_library`、`interval_eval`、`interval_reps`、`workout_steps`、`workout_templates`、`cp_protocols` | 69 | 65 | 部分 |
| 閾值與區間 | `threshold_confidence`、`threshold_estimate`、`zones`、`zone_events`、`hr_profile`、`status` | 61 | 54 | 部分（B3） |
| 熱 | `heat`、`heat_calib`、`heat_data`、`racepower/heatacc`、`racepower/env` | 22 | 18 | 有（plan T1–T8） |
| TL／TSS 換算 | `coros_tl`、`racepower/tss_calib` | 11 | 11 | 沒有（SP-37、SP-38 之後才加） |
| 腳本 | `scripts/*` | 3 | 3 | — |
| **合計** | | **545** | **495** | |

### 1.3 掃描抓不到、人工補的

| 位置 | 內容 | 筆數 |
|---|---|---|
| `backend/engine/load_guard.py:61-62, 72` | `WATCH_PCT/MIN`、`BLOCK_PCT/MIN/MAX`、`STEP_HOLD/BLOCK`（tuple 解包） | 7 |
| `backend/engine/racepower/trailhr.py:91-134` | `TRAILHR`、`XSTAR`、`DUR` 三個字典 | 約 30 |
| `backend/engine/coros_tl.py:68-95` | 預設換算係數（`POWER_A/K`、`HR_C0/C1/C2`、`LINEAR_A`）、重新擬合的參數（`HALF_LIFE_DAYS`、`THRESHOLD_SHIFT`、`HOLDOUT_DAYS`、`BACKTEST_MIN_N` 等） | 約 14 |
| `backend/engine/overview.py:51, 363-370, 988, 1001-1020, 1186` | 每小時 TSS 預設、輕鬆跑 50 分一堂、週量上限 +10 %、恢復週 60 %／65 %、比賽週 30 %、恢復期 50 %、長跑 ≤ 週量一半 | 約 12 |
| `backend/engine/quality_gate.py:104-119` | `LTHR_FRESH_DAYS`、`LOOKBACK_DAYS`、`FRIEL_HR_BAND`、`REP_PCT/REP_MIN_S/DOSE_MIN_REPS`（標「自訂」，不是「推估」） | 約 8 |

「自訂」和「推估」在程式裡是同一個意思（都是自己定的），掃描只認「推估」。標「自訂」的模組層常數另有 10 個。

## 2. 已經在自動校正的（不用再規劃）

`engine/calibrate.py` 的機制：文獻或推估的預設值，加上本人資料擬合，用 `w = n/(n+k)` 收縮；資料不夠就用預設。同步後自動跑，設定頁可以手動指定。

| 註冊名稱 | 預設 | 位置 |
|---|---|---|
| `drift_early_s`、`drift_tail_s`、`drift_max_vi`、`drift_tau_s`、`walk_max_s` | 1200 s、720 s、1.04、60 s、180 s | `backend/engine/drift_calib.py:117-134` |
| `climb_divisor_run` | 每 153 m 爬升 = 1 km | `backend/engine/terrain_calib.py:74` |
| AeT 熱 β | 1.0 bpm／°C | `backend/engine/drift_agg.py:228` |
| `hadley_hr_beta`、`humidity_default`、住家條件 3 項 | 0.3 bpm／Hadley、83 %… | `backend/engine/heat_calib.py:207-219` |
| `trail_max_min_km`、`trail_max_min_min`、`effort_rest_max` | 10 km、90 分、10 % | `backend/engine/effort_calib.py:88-97` |
| `heat_partial_hadley`、`pack_daily_drop_kg`（只能手動） | 130、0.7 kg | `backend/engine/advanced_params.py:22-27` |

另外兩個不走 `calibrate.py`、但同樣是「先驗＋收縮」：

| 項目 | 做法 | 位置 |
|---|---|---|
| TSS ↔ COROS TL 換算 | 預設係數是一位跑者的擬合；本人 COROS 活動重新擬合，`w = n/(n+30)`，留出最近 30 天做回測，沒有比舊的好就不換 | `backend/engine/coros_tl.py:22-95` |
| 推送後的負荷步驟校正 | 實際 TSS ÷ 送出的 TL 對應的 TSS，`w = n/(n+5)` | `backend/engine/coros_tl.py:42-50, 93-95` |
| 賽後 TSS 校正 | 實際 ÷ 預估的比值，`w = n/(n+3)`，比值限制在 0.5–2.0 | `backend/engine/racepower/tss_calib.py:26-28` |
| 越野耐疲勞 δ、全力心率曲線 x\*(T)、技術係數 | 先驗＋收縮 | `backend/engine/racepower/trailhr.py:91-117`、`grade_model.py:149-157` |

這些的收縮常數 k（30、5、3、20…）本身都是推估。它們是「校正方法的參數」，不建議再個人化（沒有東西可以拿來校正校正器）；回測機制已經在把關。

## 3. 還沒分類的常數：盤點表

同一類的合併成一列。「敏感」＝改 ±20 % 會不會改變排出來的課表。

### 3.1 週量與負荷（`load_guard.py`、`overview.py`）

| 常數 | 值 | 位置 | 用途 | 程式註解寫的來源 | 敏感 | 類 |
|---|---|---|---|---|---|---|
| `GOAL` | 基礎期每週 +max(2, CTL×5 %)；專項期 +max(2.5, CTL×7 %) | `load_guard.py:79` | 每週目標時數 | 推估 | **高** | A |
| `WATCH_PCT/MIN` | max(3, CTL×10 %) | `load_guard.py:61` | CTL 增幅「注意」線 | Friel 5–8 換算成比例，推估 | 高 | A |
| `BLOCK_PCT/MIN/MAX` | min(10, max(5, CTL×15 %)) | `load_guard.py:62` | 「擋」線 | 同上；上限 10 是 Friel | 高 | A |
| `STEP_HOLD/BLOCK` | 10 %／20 % | `load_guard.py:72` | 週跑量增幅 | 20 %：Nielsen 2014、Damsted 2019；10 % 推估 | 高 | D |
| 週量上限 | max(×1.10, +0.5 h) | `overview.py:988` | 一週最多加多少 | 推估：系統性回顧找不到「10 % 法則」的證據 [169]。spec 原本寫「UA 10 %」，SP-104 已改 | 高 | D |
| `SEED_DAYS`、`STARTUP_DAYS` | 28、28 天 | `load_guard.py:69-70` | 新使用者的 CTL 起算 | 推估（Coggan：給起始值） | 低（只影響前 4 週） | D |
| `SHORT_BREAK_MIN` | 3 天 | `load_guard.py:76` | 短中斷豁免 | 推估 | 低 | D |
| 恢復週 | TSB < −30 → 4 週平均的 60 %；連 3 週加量 → 3 週平均的 65 % | `overview.py:1001-1008` | 恢復週的量 | 3:1 是 Friel／UA 的慣例，其他教練也這樣排 [199][114]，但沒有試驗比較過 3:1 和 2:1 [198]。65 %（減 35 %）在挪威教練減 25–35 % [30]、跑步教練減 20–35 % [194] 的範圍內（教練級，SP-104）。60 % 沒有來源 | **高** | A |
| 加量週的判斷 | 每週 ≥ 前一週的 0.95 倍，且 > 0.5 h | `overview.py` `build3`、`projection.py` `build` | 判斷「連 3 週加量」→ 恢復週 | 推估，沒有來源（掉 5 % 以內仍算加量）。SP-104 補列 | 中 | D |
| 減量期 | 近 6 週平均的 50 %，最後 7 天 40 % | `overview.py` `week_plan`（taper）、`projection.py` `week_hours` | 減量期每週的量 | Bosquet 2007、Wang 2023 統合分析：減 41–60 %、減 ≤ 40 % 不夠、保留強度 [206][105]；Pfitzinger 中譯本比賽週減 60 % [459]；徐國峰 8–14 天 [453]（SP-104 補列，最後 7 天 40 % 之前沒列） | 中 | D |
| 比賽週、恢復期、轉換期 | 30 %、50 %、50 %（`TRANSITION_SHARE`） | `overview.py:347, 1017-1020` | 這幾種週的量 | 轉換期「減量、不全停」有出處：輕艇選手全停 VO2max −10.1 %、減量 −4.8 % [412]；50 % 這個數字仍是推估 | 中 | D |
| `TSS_PER_HOUR_DEFAULT` | 路跑 55、越野 60、登山 45… | `overview.py:51` | 沒有歷史時的每小時 TSS | 沒寫 | 低（有 6 週資料就用本人的） | D（已自動） |
| `MP_TSS_PER_HOUR` | 70 | `overview.py:408` | 馬拉松配速段的 TSS | 推估 | 低 | D |
| `easy_count` | 每 50 分一堂、1–5 堂 | `overview.py:363-370` | 輕鬆跑堂數 | 沒寫 | 中 | C（已有「每週跑步次數」偏好） |

### 3.2 間歇解鎖與強度預算（`quality_gate.py`、`base_check.py`）

| 常數 | 值 | 位置 | 用途 | 程式註解寫的來源 | 敏感 | 類 |
|---|---|---|---|---|---|---|
| `Z3_WEEKS_NEED`、`Z3_RUNS_PER_WEEK`、`Z3_MAX_GAP_DAYS` | 4 週、每週 3 跑、不停跑 ≥ 7 天 | `quality_gate.py:153-155` | 3 區解鎖 | 推估（使用者 2026-10-04 的規則） | **高** | C |
| `Z3_RELOCK_DAYS` | 21 天 | `quality_gate.py:156` | 3 區重新上鎖 | 推估，引 Coyle 1984 | 高 | C |
| `Z5_Z3_NEED`、`Z5_Z3_DAYS` | 2 堂、42 天 | `quality_gate.py:149-151` | 5 區的「3 區先」 | 推估（UA、Pfitzinger；Daniels／CTS 相反） | 高 | C |
| `QUALITY_SHARE_MAX` | 20 % | `quality_gate.py:159` | 間歇時間佔週跑步時間上限 | 推估（Seiler 80/20、Koop） | 中 | D，**來源要改**（§5.1） |
| `Z3_SHARE_START`、`Z3_SHARE_MAX` | 5 %、10 % | `quality_gate.py:161-162` | 3 區佔週量 | UA；Daniels | 中 | D |
| `LTHR_FRESH_DAYS`、`AET_FRESH_DAYS` | 84、112 天 | `quality_gate.py:100, 104` | 閾值多久算舊 | 推估／自訂；16 週**未找到來源**（`unsourced-rules.md:371`） | 高 | A |
| `UA_GAP_MAX`、`FRIEL_GOOD`、`XU_GOOD` | 10 %、5 %、10 % | `quality_gate.py:99, 109, 111` | 有氧基礎測試的通過線 | UA、Friel、徐國峰（教練） | 高 | D |
| `FRIEL_HR_BAND` | AeT −5…+3 | `quality_gate.py:106` | 「在 AeT 附近」 | 自訂 | 中 | A（併入 §4.1 第 5 項） |
| `LOW_SHARE_MIN` | 75 % | `quality_gate.py:114` | 低強度佔比護欄 | Seiler（時間） | 中 | D |
| `TSB_HOLD` | −20 | `quality_gate.py:116` | 維持量 | Friel／TrainingPeaks（教練） | 中 | D |
| `IN_BAND_TOL`、`TARGET_DOWN`、`AET60_MIN_SHARE`、`LAST_FADE`、`TIZ_GOAL` | 0.98、0.95、0.5、0.05、0.85 | `quality_gate.py:725-729` | 間歇達標判定 | 推估（`interval-adaptation.md` §4.2–4.3） | **高**（決定階梯進不進階） | A |
| `Z1_KEEP`、`Z1_LOW_WEEKS` | 2/3、3 週 | `base_check.py:80-82` | 5 區暫停 | Hickson 1982；3 週推估 | 中 | D |
| `IF_E` | 0.70 | `base_check.py:70` | E 跑換算 TSS | 推估（未驗證） | 低 | D |
| `LONG_HR_RISE`、`LONG_PACE_DROP`、`LONG_LAST_N`、`LONG_DAYS` | 5 %、5 %、3、28 | `base_check.py:74-77` | 停跑後的長跑檢查 | 推估 | 低（只在 14–28 天停跑後用） | D |
| `EF_RUNS`、`EF_DAYS` | 6、56 | `base_check.py:359-360` | 輕鬆跑目標的 EF | 推估 | 低 | D |

### 3.3 自動調整與輕鬆跑偏強（`adapt.py`、`plan_auto.py`）

| 常數 | 值 | 位置 | 用途 | 來源 | 敏感 | 類 |
|---|---|---|---|---|---|---|
| `OVER_HR_BPM`、`OVER_SHARE` | AeT +3、10 % | `adapt.py:71-72` | 輕鬆跑偏強 | 推估（`unsourced-rules.md` §B5） | 高（會改後面幾天的課） | A |
| `EASY_POWER_CAP` | 80 % CP | `adapt.py:73` | 同上 | Palladino（教練） | 中 | D |
| `OVER_TSS`、`BIG_TSS_UP` | +20 %、+20 % | `adapt.py:74`、`plan_auto.py:60` | 偏強、大變更 | TrainingPeaks 達成度綠燈帶；大變更是推估 | 中 | D |
| `MIN_EASY_MIN`、`FATIGUE_CUT`、`RED_STREAK` | 20 分、×0.8、2 堂 | `adapt.py:75-80` | 疲勞時減量 | 推估 | 中 | C |
| `RACE_GUARD_DAYS`、`MAX_CHANGED` | 14 天、3 堂 | `plan_auto.py:61-62` | 大變更的定義 | 推估，**未找到來源**（`unsourced-rules.md:409`） | 低（只決定要不要你確認） | C |
| `STREAK_RATE` | 80 % | `compliance.py:105` | 「達標週」 | 推估 | 低（顯示用） | D |

### 3.4 恢復期、轉換期、專項期、B2B、技術地形、負重

| 模組 | 常數（筆數） | 來源概況 | 敏感 | 類 |
|---|---|---|---|---|
| `reentry.py:43-85` | `MIN_BREAK` 6、`CROSS_MIN` 45、`CROSS_SHARE` 0.5、`LONG_CAP_MIN` 90、`TARGETS_AFTER_DAYS` 14、`CAT4_STEPS`、`CAT4_Z3_WEEKS` 12、`STEP_UP_MIN`（9） | 架構是 Daniels 表 9.2（教練）；其餘推估 | 中（只在停跑後） | D；`CROSS_*` 是 C |
| `planning.py:45-49` | `TRANSITION_WEEKS` 3、`TRANSITION_MIN_DAYS` 7、`LONG_EVENT_HOURS` 6、`B_RECOVERY_DAYS` 3（5） | Friel、Canova（教練）；低端取值推估。`B_RECOVERY_DAYS` 見下面 B 賽事那一列 | 中 | C（已有偏好設定） |
| `planning.py` `SPECIFIC_WEEKS` | 專項期 8 週（1） | 教練級：Friel Build 8–9 週 [18]、Canova 6–8 週 [130]、vert.run 最後 8–10 週 [131]、江晏慶強化 2–3 週＋巔峰約 6 週 [454]（換算成 8 週是推估）。原本只寫 Koop、UA（文字）（SP-104） | 高 | D |
| `planning.py` B 賽事 | `MINI_TAPER_DAYS` 5、`B_RECOVERY_DAYS` 3（2） | 教練級、部分二手：TrainerRoad 比賽當週減量 [362]、Friel 賽前休 2–3 天 [431]、Pfitzinger 賽前 5 天不做間歇、4 天不做節奏跑和長跑、賽後約 5 天恢復 [437]（二手轉述）。3 天恢復是推估；SP-95 要改成依距離。之前清單沒列（SP-104 補列） | 中 | D |
| `overview.py` `strength_n` | 基礎期、轉換期、恢復期每週 2 次肌力，其他 1 次（1） | UA；賽季每週 1 次可維持 13 週（Rønnestad 2010，自行車 [407]）（SP-104） | 低 | D |
| `specific_phase.py:41-59` | `FRAC`（每週佔賽事的比例表）、`ROAD_FRAC`、`STEP` 1.15、`SIM_WEEKS`、爬坡課的 7 個門檻（18） | コース定數（山本正嘉）、江晏慶、Koop、Pfitzinger；比例表本身推估 | **高**（決定專項期長跑多長） | C；見 SP-75 |
| `b2b.py:84-107` | `DAY2_RATIO` 0.67、`PAIR_SHARE` 0.70、`SPACING_DAYS` 14、`LAST_BEFORE_DAYS` 21、`POST_EASY_DAYS` 4 等（21） | CTS、Koop、UA（教練）；多數數字推估。`LAST_BEFORE_DAYS` 21 原本是「未驗證的搜尋摘要」，換成 vert.run 百英里指南：最大的 B2B 在賽前 4–5 週、最後 3 週不做 [190]（教練級，SP-104） | 中（B2B 是建議，要你接受） | C |
| `technical.py:31-36` | RPE 帶、`SPEC_WORK_MIN/MAX`（6） | 範本；推估 | 低 | D |
| `steep_hill.py:36-46` | `STAGE_PCT`、`STAGE_WEEKS`、`BASE_GRADE`、`MINUTES`、`DEFAULT_PCT`（11） | UA trekking、Pandolf；階段週數推估 | 低 | D（`BASE_KMH` 已列在 plan G7） |
| `primary_sport.py:33-35` | 84 天、25 %、5 小時（3） | 推估 | 低（可手動選） | D |
| `injuries.py:42-48`、`injury_exposure.py:46-62` | 28、42 天、P75、P85 等（23） | 推估；部分來自 `validation-lovdal.md` | 低（提示用） | D |
| `aet_test.py:66-76`、`template_recs.py:34-40` | 測試間隔 28 天、起始功率 75 % CP 等（14） | UA（40 分）；其餘自訂 | 低 | D |

### 3.5 間歇、課表步驟、範本

| 模組 | 常數（筆數） | 來源概況 | 敏感 | 類 |
|---|---|---|---|---|
| `interval_library.py:337-444` | `TIZ_TOL` 0.15、`WPRIME_RATIO`、`Z3_RATIO`、`ROTATE_N`、`STD_LEN`（7） | Seiler 2013／Wen 2019 支持「累積時間」；數字推估 | 中 | D |
| `interval_eval.py:60-73`、`interval_reps.py:29-36, 110-111` | 區內時間、趟的偵測容差（17） | 推估（`interval-prescription.md`） | 中 | D（偵測方法；和裝置有關，不是和人有關） |
| `workout_steps.py:110` `RPE_FRAC` | RPE 1–10 → 55–110 % CP | 推估 | 中（RPE 課的 TSS 和手錶目標） | A |
| `workout_steps.py:984, 1106` `EASY_F`、`NONE_IF` | 輕鬆 78 % CP；沒目標的步驟的 IF | 推估（Palladino EZ ≤ 80 %） | 中（計畫 TSS） | A |
| `workout_steps.py` 其餘 | `HR_CLASS_BAND`、`MP_PACE`、`MP_GOAL_BAND`、`DIST_PACE_DEFAULT` 等（約 20） | Pfitzinger、Daniels、Friel 換算；推估 | 低–中 | D |
| `workout_templates.py:473-481` | 範本分類門檻（12） | Palladino、Daniels | 低（分類用） | D |
| `cp_protocols.py:79-93` | `TT20_FACTOR` 0.95、`HR_GAP_BPM` 8 等（8） | Ñancupil-Andrade 2024；推估 | 中 | D；偵測門檻在 plan P11（C） |

### 3.6 閾值可信度與區間事件

| 模組 | 常數（筆數） | 來源概況 | 敏感 | 類 |
|---|---|---|---|---|
| `threshold_confidence.py:74-107` | LTHR 對 HRmax 的合理範圍、`CP_CHANGE` 5 %、`EFFORT_DAYS` 120、比賽支持帶、CP 帶、`TEST_AGE_DAYS` 56、HRmax 尖峰過濾（26） | Nuuttila 2025、Friel、Micheli 2025、Gillinov 2017；帶寬多數推估 | 中（影響「要不要測試」的提示，不直接改課表） | D；`TEST_AGE_DAYS` 併入 §4.1 第 3 項 |
| `zone_events.py:105-135, 773` | AeT 偏移偵測：5 bpm、2 SE、6–8 次、365／90／30 天等（22） | 推估（`unsourced-rules.md` B3） | 中 | D（統計方法的參數） |
| `algorithms/threshold_estimate.py:221-224` | `AET_MAX_SE_BPM` 3、`AET_SHIFT_BPM` 5（4） | 推估（B3） | 中 | D |
| `zones.py:419-420` | `AET_PM_BORROWED` 16、`AET_PM_FLOOR` 3 | Micheli 2025（借用）、Lamberts & Lambert 2009 | 低 | D |

### 3.7 之前已經分類過的（只列筆數）

| 領域 | 筆數 | 分類在哪裡 |
|---|---|---|
| 飄移視窗（`workout_review.py:96-163`） | 約 28 | plan P10；`unsourced-rules.md` §0.5.1。主要 5 項已註冊校正 |
| 單次活動判讀其他（步頻、爬坡比較、路線合併） | 約 60 | 顯示與資料處理用，D |
| 賽事計算機（`racepower/*`） | 約 93＋30 | plan P、G、T；`unsourced-rules.md` §0.5.2 |
| 熱 | 18 | plan T1–T8，已註冊校正 |

## 4. 優先順序

### 4.1 最值得先個人化的 7 項

排序依據：對課表的影響大，而且這位使用者的資料量得出來。

| 順位 | 項目 | 為什麼 | 怎麼校正 | 最少資料 | 沒資料時 |
|---|---|---|---|---|---|
| 1 | **間歇達標判定**：`IN_BAND_TOL` 0.98、`LAST_FADE` 0.05、`TIZ_GOAL` 0.85 | 每一堂間歇都用，直接決定階梯進不進階 | 0.98 換成「1 − 本人 CP 估計的誤差」（CP 測試之間的變動，或 PD 擬合的殘差）；`LAST_FADE` 用本人達標堂的最後一趟落差分布的 P90 | 20 堂間歇（`unsourced-rules.md` §0.5.3 已寫「≥ 20 堂後可調」） | 現值 |
| 2 | **每週 CTL 目標與增幅線**：`GOAL`、`WATCH_PCT`、`BLOCK_PCT` | 決定每週排幾小時 | 用本人歷史：過去每週的 CTL 增幅，分成「之後 2 週沒有停跑 ≥ 6 天、沒有傷病紀錄」和「有」兩組；注意線取前一組的 P75、擋線取 P90，收縮到現值 | 26 週資料，其中至少 8 週有加量 | 現值（Friel 換算） |
| 3 | **閾值多久算舊**：`LTHR_FRESH_DAYS` 84、`AET_FRESH_DAYS` 112、`TEST_AGE_DAYS` 56 | 決定 5 區會不會被重新鎖住、多久叫你測一次 | 看本人相鄰兩次測試之間閾值動了多少（bpm／月）。動得慢就拉長，動得快就縮短；上下限 8–24 週 | 3 次同類測試 | 現值 |
| 4 | **恢復週的量**：60 %、65 % | 每 4 週用一次 | 看本人每次恢復週之後 TSB 有沒有回到 −10 以上、下一週的間歇有沒有達標。沒有 → 往下調 5 個百分點；範圍 50–70 % | 4 次恢復週 | 現值 |
| 5 | **輕鬆跑偏強的心率餘裕**：AeT +3（`OVER_HR_BPM`、`FRIEL_HR_BAND`） | 觸發後會改後面幾天的課 | 3 bpm 換成本人 AeT 聚合估計的標準誤（已經在算，`threshold_estimate.py`），下限 3（Lamberts & Lambert 2009 的日間變異） | 6 個飄移點 | 3 bpm |
| 6 | **RPE 與沒有目標的步驟的強度**：`RPE_FRAC`、`EASY_F`、`NONE_IF` | 決定計畫 TSS，再影響週量和 TL 換算 | 用本人做完的課：輕鬆跑實際的 % CP 中位數取代 0.78；有 RPE 標記的活動擬合 RPE → % CP | 輕鬆跑 10 次；RPE 每一級 3 次 | 現值 |
| 7 | **TSS ↔ TL 的預設係數**（給新使用者） | 現在的預設是一位跑者的擬合 | 已經會自動重新擬合；要做的只是把預設標成「單一使用者」並確認新使用者的誤差提示 | — | — |

- 第 1、2、4 項需要「結果」才能校正（達標、沒受傷、有恢復）。資料少的時候校正值很吵，所以都要收縮到現值，並設上下限。
- 第 2 項要小心：只有一位使用者、傷病紀錄很少時，P75／P90 分不出「安全」和「剛好沒事」。`validation-lovdal.md` 用公開資料驗過同一組護欄，個人化的結果偏離那份驗證太多時不該採用。

### 4.2 維持固定、只補來源或改標示

| 項目 | 處理 |
|---|---|
| 週跑量增幅 20 %、低強度 75 %、TSB −20／−30、硬課間隔 2 天 | 維持。`unsourced-rules.md` §0.5.3 已經判定「通用、不校正」 |
| 減量 50 %／40 %／30 % | 維持。Bosquet 2007 的最佳範圍是減 41–60 %（已驗證，見 §5.4），Wang 2023 統合分析結果相同 [206][105]；一個人一年只有幾場 A 賽事，量不出個人值 |
| 3 區解鎖（4 週／3 跑／7 天）、重新上鎖 21 天、5 區的「3 區先」 | 維持，收進進階設定（C）。沒有可以量的結果 |
| 轉換期 50 % | 可以補來源：Bompa & Buzzichelli 2015 p.186（教練；見 `bompa-periodization-strength.md` §4.4）。「減量、不全停」的方向另有輕艇的比較 [412]（SP-104 已寫進 `overview.py` 註解） |
| 強度預算 20 % | 數字維持，**來源說明要改**（§5.1） |
| 專項期每週比例表、B2B 的比例 | 維持（C）。專項期的設計另見 SP-75 |
| 偵測類（趟的容差、尖峰過濾、路線合併、資料異常） | 維持。這些和裝置、資料品質有關，不是和人有關 |
| 所有收縮常數 k、最少 n | 維持。它們是校正方法的參數 |

### 4.3 需要先有資料才談得上的

| 項目 | 缺什麼 |
|---|---|
| 恢復期的 FVDOT 折減 | 需要 ≥ 2 次停跑 ≥ 6 天之後的實際表現（EF、CP）對照 |
| B2B 第二天的比例 | 需要 ≥ 3 次 B2B 的第二天 VAM 比值（程式已經在記） |
| 「大變更」的門檻 | 需要你同意／拒絕的紀錄 ≥ 10 筆 |

### 4.4 可以刪的

| 常數 | 位置 | 依據 |
|---|---|---|
| `ZONE3_SESSIONS` | `backend/engine/quality_gate.py:117` | 整個 `backend/` 只有定義這一行。spec 寫 SP-39 已經用 `Z5_Z3_NEED` 取代舊的「3 堂 3 區」 |
| `HR_BETA_SE` | `backend/engine/heat.py:71` | 只有定義這一行；實際用的是 `heat_calib` 的個人 β |

我只檢查了 22 個看起來可疑的常數，不是全部 550 個。要完整找出沒人用的常數，需要跑一次靜態分析。

## 5. 來源核對：對不上或互相矛盾的地方

### 5.1 強度預算 20 % 和 Seiler 的 80/20

- 程式：`QUALITY_SHARE_MAX = 0.20`，註解寫「推估（Seiler 80/20, Koop）」，算法是**間歇時間 ÷ 計畫跑步時間**（`quality_gate.py:159`）。
- 原文：Seiler 2010, *Int J Sports Physiol Perform* 5:276–291（同儕審查，**已驗證**摘要，PMID 20861519）：「about 80% of training sessions are performed at low intensity … with about 20% dominated by periods of high-intensity work」。**單位是堂數，不是時間。**
- 影響：以時間算，菁英選手的高強度比例遠低於 20 %。所以 20 % 當「時間上限」很寬，實際在擋的是 `Z3_SHARE_MAX` 10 % 和每週堂數。
- 建議：數字不動，把註解和 spec 的來源改成「推估；Seiler 的 80/20 是堂數」。護欄 `LOW_SHARE_MIN` 75 %（依時間）也引 Seiler，同樣要註明。
- `periodization-phase-metrics.md:12` 把 Seiler & Kjerland 2006 的 75–80／0–5／15–20 寫成強度分配，沒有寫單位；那篇我這次沒有讀到原文（未驗證）。

### 5.2 恢復週的量

- 程式：連 3 週加量後，恢復週是前 3 週平均的 **65 %**（減 35 %）；TSB < −30 時是 4 週平均的 60 %。註解沒有寫百分比的來源。
- 找到的說法：Uphill Athlete 論壇〈How to schedule the recovery week〉：重的加量期之後減 60 %，負荷輕時減 40 %（教練；**未驗證**，只看到搜尋摘要，https://www.uphillathlete.com/forums/topic/how-to-schedule-the-recovery-week/ ）。
- 如果這個轉述正確，app 的恢復週比 UA 的範圍輕（減得少）。
- Bompa & Buzzichelli 2015 沒有給恢復週的百分比（`bompa-periodization-strength.md` 對照表 #2，已驗證）。
- 結論：這個數字目前沒有一手來源。列在 §4.1 第 4 項用本人資料校正；同時值得去讀 UA 原文確認。
- 2026-10-05 補（SP-104，`periodization-cross-sport.md` §4.3）：挪威教練恢復週減 25–35 % [30]、跑步教練減 20–35 % [194]，65 %（減 35 %）落在這個範圍的上緣；登山減 50 % [114]、自行車減 40–60 % [199] 比較深。都是教練級，沒有試驗。程式註解和 `SRC_31` 已補上。

### 5.3 Daniels「T 跑 ≤ 週量 10 %」

- 程式：`Z3_SHARE_MAX = 0.10`，註解寫 Daniels（`quality_gate.py:162`）。
- 這次只找到二手整理（coachray.nz、hillrunner.com 等，社群；**未驗證**）：T 配速單次不超過週里程的 10 %，I 8 %，R 5 %。
- 兩點要注意：Daniels 的單位是**里程**，app 用的是**時間**；Daniels 講的是**單次課**的上限，app 把它當**每週** 3 區的上限。`interval-adaptation.md:482` 也寫 Daniels 的書「未驗證，只用中文筆記整理」。
- 建議：標成「教練（未驗證原書）」，並在 spec 註明單位不同。

### 5.4 這次核對過、沒有問題的

| 常數 | 來源 | 等級 | 驗證 |
|---|---|---|---|
| 減量 50 %／40 % | Bosquet et al. 2007, *Med Sci Sports Exerc*（PMID 17762369）：2 週、量指數式減 41–60 %、強度和頻率不變；Wang et al. 2023, *PLOS ONE*：減 41–60 % 效果最大、減 ≤ 40 % 不夠、保留強度 [206][105] | 同儕審查（統合分析） | **已驗證**（Europe PMC 摘要；Wang 2023 見 `periodization-cross-sport.md` §4.5） |
| `Z3_RELOCK_DAYS` 21 天 | Coyle et al. 1984, *J Appl Physiol*（PMID 6511559）：7 位耐力訓練者，停練 21 天 VO2max −7 %，56 天後穩定在 −16 % | 同儕審查 | **已驗證**（Europe PMC 摘要）。但研究對象是多年訓練者完全停練；拿 21 天當「重新上鎖」是程式自己的延伸（推估） |
| CTL 增幅 5–8、上限 10 | Friel, The CTL Ramp Rate | 教練 | 已驗證（`ctl-ramp-calibration.md:95`，這次沒有重讀） |
| 週跑量 > 20–30 % | Nielsen 2014、Damsted 2019, *JOSPT* | 同儕審查 | 已驗證（`unsourced-rules.md:363`，這次沒有重讀） |
| `OVER_TSS` ±20 % | TrainingPeaks 達成度：綠燈 80–120 % | 平台 | **未驗證**：官方說明頁回 403，只看到第三方整理（https://www.joinbasecamp.com/support/compliancecolors ） |

### 5.5 找不到來源的（維持原狀，直接寫明）

- `AET_FRESH_DAYS` 16 週：`unsourced-rules.md:371` 已經寫「未找到來源」，這次也沒有找到。
- 大變更的門檻（+20 % TSS、14 天、3 堂）、疲勞減量 ×0.8、連 2 堂紅燈：`unsourced-rules.md:409` 寫「未找到來源」，這次沒有再查。
- 3 區解鎖的 4 週／3 跑／7 天：使用者 2026-10-04 自己定的規則，沒有外部來源。

## 6. 需要你決定的事

1. §4.1 的 7 項要不要做、先做哪幾項。我建議先做第 1、3、5 項：資料已經在算、校正方法簡單、不需要「有沒有受傷」這種稀少的結果。
2. 第 2 項（CTL 增幅線）要不要個人化。風險是只有一個人的資料時，結果可能只是反映「過去剛好沒事」。
3. 3 區解鎖那組數字要不要開放到進階設定讓你自己調。
4. §5.1、§5.3 的來源說明要不要改（只改文字）。
5. §4.4 的兩個常數要不要刪。

## 7. 限制

- 盤點靠關鍵字掃描加人工讀清單。字典、函式內、前端和 views 的數字不完整。
- 「敏感」欄是我讀程式的判斷，沒有實際把每個常數改 ±20 % 跑一次課表。要確定的話，可以寫一支腳本對固定的輸入逐一擾動、比較產出的週課表。
- 來源只對 §4.1 的候選和 §5 的幾項做了外部核對；其餘沿用程式註解和既有研究文件的標記。
- PubMed 頁面抓不到（只回 cookie 提示），三篇論文是從 Europe PMC 的 API 讀摘要，沒有讀全文。
- TrainingPeaks 官方說明、UA 論壇、Daniels 原書都沒有讀到一手內容。

## 參考

- Bosquet L, Montpetit J, Arvisais D, Mujika I. Effects of tapering on performance: a meta-analysis. *Med Sci Sports Exerc* 2007. PMID 17762369.
- Coyle EF, Martin WH, Sinacore DR, Joyner MJ, Hagberg JM, Holloszy JO. Time course of loss of adaptations after stopping prolonged intense endurance training. *J Appl Physiol* 1984. PMID 6511559.
- Seiler S. What is best practice for training intensity and duration distribution in endurance athletes? *Int J Sports Physiol Perform* 2010. PMID 20861519.
- 方括號編號 [n]（§3、§4.2、§5 裡 SP-104 補上的出處）是 `periodization-cross-sport.md` 參考文獻的編號；對照表在那份文件 §6.1 的「SP-104 補來源」。
- 既有文件：`docs/plans/generalize-athlete.plan.md`、`docs/research/unsourced-rules.md`、`ctl-ramp-calibration.md`、`validation-lovdal.md`、`interval-adaptation.md`、`detraining.md`、`bompa-periodization-strength.md`。
