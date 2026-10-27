# 沒有功率計時的盤點（SP-950）

2026-10-27，三個唯讀盤點（排課、活動判讀、計算機與頁面）合併。路徑相對 `backend/`。
目標：設定 › 基本資料「功率來源」= 無 時，系統用心率或配速維持同樣的排課邏輯與準確度，功率不顯示。

## 拍板的規則（使用者 2026-10-27，SP-950 問答 Q1、Q2）

> **後來的修正（同日）**：① 手錶功率拿掉——裝備只有「Stryd」和「沒有功率計」；存的 `power_source = watch`（和舊欄位 coros／garmin／other）一律讀成「沒有」，手錶手腕功率在任何地方都不算，`accept_watch_power` 不再有作用（只在讀取時，不改存的資料）。② 不要 30 分鐘測試：③ 不建議、不排、沒有選項，只是不再建議 CP 測試、不再擋 AeT 測試；LTHR／閾值配速照舊從跑步、比賽估或手動輸入。下面表格裡「Stryd 或手錶功率」「30 分鐘測試」以這段為準。

**一條規則，全站共用**：功率生效 ＝ 有功率計（裝備 = Stryd 或手錶功率）**而且** CP 新鮮（`base_check.cp_stale` 的定義：42 天內有 CP 測試或比賽）。其他情況一律心率／配速。三種狀態：① 功率生效、② 有功率計但 CP 過期、③ 沒有功率計。

> **CP 新鮮也認全力段（SP-963，使用者 2026-10-27 選 (a)）**：42 天內有正式 CP 測試或比賽，**或** Stryd 的 PD 擬合有新的全力段撐著——42 天內短（3–5 分）與長（12–30 分）各至少一段 mean-max ≥ 當天模型的 95 %（推估；`power_state.efforts_on`），強度課、比賽、越野都算，只算 Stryd；今天的狀態、某天的狀態（單次活動）、`cp_stale`、計算機同一個定義；② 的提醒補「或最近跑一次強度課／比賽」。

| 課的類型 | ① | ②、③ |
|---|---|---|
| 路跑輕鬆跑、長跑 | 功率區間，心率 ≤ 輕鬆跑上限當保險 | 心率 ≤ 輕鬆跑上限 |
| 平路 3／5 區間歇、節奏 | 功率 | 配速（% 閾值配速），心率上限寫在文字；沒有閾值配速 → 心率＋RPE |
| 緩坡爬坡重複 3–8 % | 功率 | 心率上限＋RPE |
| 長爬坡 > 8 %、下坡 | 不設目標（同現在） | 同左 |
| 越野輕鬆、越野長天、恢復跑、登山健走 | 心率（同現在） | 同左 |
| 馬拉松配速段 | 目標配速（同現在） | 同左 |
| CP 測試 | 每 42 天建議 | ② 建議（做完回 ①）；③ 不建議，改建議 30 分鐘測試（LTHR＋閾值配速），不再擋 AeT 測試 |
| AeT 測試 UA／Evoke | 固定功率（SP-949：心率–功率關係） | 固定配速（SP-949：心率–配速關係） |

- **③「沒有功率計」＝ 不用任何功率**：舊 Stryd fit、課表 CP 列、WKO5 mFTP、手錶功率都不進排課、模型、門檻；資料不刪，改回來全部回來。之前明確選的功率設定（目標依據 = 功率、使用功率、接受手錶功率、個人化間歇、可跑坡目標 = 功率）在 ③ 一律蓋掉不用、選項隱藏，不刪。選「手錶功率」當裝備 ＝ 手錶功率就是功率計，CP 只用手錶功率擬合（量尺一致）。
- **計算機**：同一條規則。① 功率模型；②③ 越野用心率模型（`trail_hr_estimate`，分段用坡度換算配速），路跑用比賽成績推估（`/estimate`）當主路徑，頁面寫用了哪一種。
- **活動判讀與單次活動顯示照「那次活動當天」**：那筆有可信的功率（Stryd；裝備是手錶時手錶功率；越野只算 −3…+8 %）**而且活動當天 CP 有效** → 完整功率（功率區間、% CP、每趟功率），強度用功率判；有功率但當天 CP 過期 → 功率線、平均功率、NP 照顯示，% CP／功率區間不顯示、標「當時 CP 已 N 天沒測」，強度用心率／配速；沒有可信功率 → 只有心率／配速。跨活動的功率圖（功率曲線、CP 歷史）在 ③ 收起。
- **間歇判讀、階梯、Zone 3 完成**：平路有分圈 → 圈配速對目標帶；其他 → 心率找趟，照課表已知的趟數縮短心率延遲扣的秒數。
- **負荷**：維持現在（Stryd 功率負荷，其他心率負荷）。
- **生效時機**：使用者改裝備 → 立刻重排、重推 COROS（`plan_auto._start_run`，不分方案，比照 `_after_hr_model`）；CP 隨時間過期 → 下一次自動調整（付費即時、免費夜間）；課表頁文字開頁即時。
- **② 一定要提醒做 CP 測試**（使用者 2026-10-27）：有功率計但 CP 過期時，課表排 CP 測試建議，總覽待辦、結果卡、課表目標的說明都寫「CP 已 N 天沒測，目前用心率／配速；做一次 CP 測試就切回功率」。使用者在「不再建議的測試」關掉 CP 測試時照舊尊重，但結果卡的狀態列仍寫 CP 過期。
- 賽事可行性 W/kg 只在 ①（順便修 `race_feasibility.py:1006` 讀錯欄位的 bug）。

## 根本原因

- **引擎幾乎不看功率來源。** `athlete_profile.effective_power_source()`／`use_power()`（`engine/athlete_profile.py:117`、`:127`）只有 `api/sync.py:369-388`（設定、圖表顯示）、`api/racepower.py:899`（目標依據）、`engine/target_policy.py:144-156` `_auto_power_ok`（只在 basis = auto 時）在讀。
- **CP 永遠不過期**：`wko5expr/fitdataset.py:1538-1553` `_cp_fit_on` 一直回最後一次 Stryd PD fit；`planning.py:993` `Plan.threshold_on("cp")` 一直回最後一次 CP 測試；計算機的 WKO5 mFTP 快照（`racepower/athlete.py:1400`）、課表 CP 列（`:1391`）也一樣。所以各處「有沒有 CP」的檢查一直通過，「沒有 CP 就退回心率」的路徑不會走到。
- **手錶手腕功率還在**：COROS 不接 Stryd 也會記功率，約是 Stryd 的 1.3–1.8 倍。`Dataset.channel(idx, "power")`（`wko5expr/dataset.py:1097`）不過濾；只有 TSS（`_power_blocked`）、`power_ok` 的呼叫端、Stryd PD fit 有過濾；`Dataset.power_ok`（`dataset.py:582`、`racepower/athlete.py:1076`）只看「Stryd 或手錶＋接受手錶功率」，不看功率來源。
- 結果：手腕功率對上過期的 Stryd CP → 輕鬆跑被判成閾值／VO2、Zone 5 關卡被擋（`power_low_share`）、LTHR 被拉低、計算機一直用舊功率模型預測、推到手錶的還是瓦數。

## A. 排課、課表目標、推送

| 位置 | 功率在做什麼 | 功率來源 = 無（還留著舊 CP）時現在 | 應該 |
|---|---|---|---|
| `engine/target_policy.py:166-175`、`:182` | 課表偏好或單堂 `target_basis=power` | 不看來源，照推瓦數 | 來源 = 無 時蓋掉，選項隱藏 |
| `target_policy.py:43`、`:187-188` | basis 只有 hr／power／none | 沒有配速 | 加 `pace`（`tpace`；SP-951） |
| `plan_store.py:1052-1056`、`sync/coros_workouts.py:198-205`、`plan_prefs.py:603` | 呼叫 target_policy 不帶 `th` | 只靠 `_auto_power_ok` | 帶來源／`th` |
| `workout_steps.py:1510-1517` | 心率 basis 沒有 LTHR → 退回功率 | 推舊瓦數 | 退回配速或不設 |
| `workout_steps.py:1525-1533`、`workout_templates.py:199-351` | 使用者的功率步驟、Palladino／Stryd 範本 | 舊 CP 推瓦數；沒 CP 報錯 | 隱藏或換成配速／心率 |
| `workout_steps.py:605-610`、`coros_workouts.py:381-387`、`aet_test.py:544`、`:624-627`、`overview.py:2823-2827` | UA／Evoke AeT 測試固定功率 0.75×CP | 推瓦數 | 固定配速（SP-949） |
| `aet_test.py:711-718`（`cp_tested` `:682-685`） | 徐國峰 90：E 配速 → 75–80 % CP → 說話測試 | 舊 CP 列永遠算「實測」→ 給功率 | E 配速 → AeT 附近的配速 → 說話測試 |
| `coros_workouts.py:269-273`、`:313-319` | 間歇主段 = 功率，只有存的 basis = hr 才心率 | 沒 CP 時沒目標 | 跟 `resolve()` 同一套 |
| `zones.py:568`、`:627`、`overview.py:1113-1129`、`:2769-2770`、`:2891`、`projection.py:64-80`、`:3612-3632`（減量期間歇） | 目標文字的瓦數 | 顯示舊瓦數 | 不用功率時 `cp` 給空 |
| `interval_library.py:842-856`、`quality_gate.py:2989-2999` | 間歇文字「功率 W」或「RPE 8（x% CP）」；3 分以下 Z5 沒心率文字 | 只剩 RPE＋% CP | 平路配速（`tpace`）、坡 RPE＋心率上限 |
| `interval_library.py:902-927`、`power_zone_plan.py:203-226` | 個人化間歇用功率–時間模型 | 用舊模型排 | 退回預設 |
| `status.py:988`、`:1003-1007`、`overview.py:2143-2150`、`:2816-2822` | CP 測試每 42 天建議一次 | 永遠建議 CP 測試 | 不建議；改建議 30 分鐘測試（LTHR＋閾值配速） |
| `overview.py:2159` | `aet_due` 要那週沒有 CP 測試 | CP 測試一直擋住 AeT 測試 | 來源 = 無 時拿掉這個條件 |
| `plan_auto.py:996-997`、`:1425-1428`、`:999-1001` | CP 變了才重算瓦數、重推 | 改功率來源不會重推，手錶上留著瓦數 | 來源改變比照 `hr_changed` 重排重推 |
| `adapt.py:168-180` | 輕鬆跑太硬 = 功率 > 80 % CP | 沒均功率時看心率 > 上限+3（OK） | — |

## B. 活動判讀、關卡、完成度

| 位置 | 功率在做什麼 | 現在（手腕功率＋舊 CP） | 應該 |
|---|---|---|---|
| `engine/workout_review.py:1797`、`:1959` `_thresholds` | `cp = ds.cp(w)` 給下面全部用 | 舊 Stryd CP | 不用功率時 cp = None |
| `workout_review.py:1979-1991` `hard_s` | 30 秒功率 ≥ 0.95 CP | 手腕功率灌水 | 心率 ≥ LTHR 或 GAP ≥ 閾值配速 |
| `workout_review.py:1118`、`:1123` | 飄移測試的 VI／> 90 % CP 關卡 | 沒功率時**沒有強度關卡**：60 分節奏跑也算 | 改 % LTHR 或 % 閾值配速 |
| `workout_review.py:1126-1129` `halves_basis` | 功率涵蓋 ≥ 95 % 用功率前後半 | 手腕 Pw:HR | Pa:HR（起伏路用 GAP） |
| `workout_review.py:2454`、`:1745` `aet_steady` → `test_aet` | 靠飄移 `ok` | 低飄移的節奏跑會被標成 AeT 測試 | 加心率 ≤ AeT+x 關卡 |
| `workout_review.py:1225`、`:2003` `detect_efforts` | 只用功率找努力段 | 空的：沒有恢復卡 | 心率或配速／圈 |
| `engine/session_stimulus.py:151-156` `pvalid` | 有 `p30` 且有 cp 就走功率路徑 | 路跑全程走功率 → 心率路徑被蓋掉 → 假 Z3／Z5 | 看 `use_power`／`power_ok` |
| `session_stimulus.py:200-206`、`:212-216` | 心率路徑：VO2 = 30 秒心率 ≥ 0.93 HRpeak、≥ 60 秒、÷1.6；Z3 = 心率 ≥ 0.95 LTHR、每段扣 180 秒 | 可用但偏弱（見下） | 加 GAP 配速 ≥ 閾值配速 |
| `interval_eval.py:342` `evaluate` | 沒檢查 `power_ok` | 用手腕功率判每趟 | 同 `card`（`:607-620`）的檢查 |
| `interval_reps.py:282-314` `find_reps` | 圈數路徑不用功率；功率／短間歇要功率 | 只剩圈數 | 圈數＋心率／配速 |
| `quality_gate.py:589-686` `dose_history`、`:1061-1064` `dose_step`、`:1092-1097`、`:898-924` | 間歇階梯照每趟功率判 | 舊 CP：圈的 power = None → 第 1 趟算沒達到 → 目標 −5 %、階梯不動；沒 CP：永遠「同一階再做一次」 | 平路照圈配速對 `tpace` 帶；其他用心率趟（`find_reps(hr_z3=True)`） |
| `quality_gate.py:791`、`status.py:590-592` `power_low_share` | 原始功率 < 0.8 CP 的比例 | 手腕功率讓比例 < 75 % → 擋 Zone 5 | 不用功率時不算 |
| `overview.py:68-75` `Z3_EXPR`、`:2414-2419` | Zone 3 課算完成 = 功率 ≥ 0.85 CP | 沒功率時永遠不算完成 | 用 `session_stimulus` 的 z3_s |
| `plan_match.py:105` `threshold_rule`（恢復期 fartlek 4×1′ 98–101 % CP） | 功率 ≥ 95 % CP | 心率 1 分鐘到不了 LTHR → 「跑輕鬆」 | 配速或圈 |
| `plan_match.py:121-160`、`:371` | 強度 `max(心率, 功率)` | 手腕灌水 | 不用功率時只看心率／配速 |

心率路徑會改變結果的地方：Zone 3 每段扣 180 秒、要剩 ≥ 150 秒 → 4×4′、5×3′ 算出 0 秒 Z3 → 標「排間歇，實際跑輕鬆」；VO2 用心率時 5×2′ 很少到 4 分鐘等效，30 秒的趟永遠到不了；越野起伏時心率在爬坡偏高、下坡不算。

## C. 負荷與門檻

| 位置 | 功率在做什麼 | 現在 | 應該 |
|---|---|---|---|
| `wko5expr/dataset.py:826-884` `_metrics` | TSS 順序：功率 → rTSS（`runtpace`）→ TP → 心率 TSS；手錶功率擋掉（`power_tss_blocked`）除非接受手錶功率 | 使用者目前就是心率 TSS | 不用功率時同現在（心率 TSS），不接受手錶功率 |
| `engine/thresholds.py:34-44` `_per_run`、`:100-130` `estimate` | LTHR／AeT 估算要功率 | 沒功率就停；手腕＋舊 CP 時 SP-573 的手錶功率分支走不到 | 心率／配速 |
| `thresholds.py:55-69` `_judged_against_cp` | 用 CP 判每次跑步 | 手腕功率對舊 CP 把 LTHR 拉低 | 不用功率時不判 |
| `engine/threshold_confidence.py:898`、`:1097`、`:278`、`:429` | cp_band、cp_change 證據 | 假證據，可能讓實測 LTHR 失效 | 不用功率時跳過 |
| `algorithms/threshold_estimate.py:142` `steady_drift` | AeT 只用 Pw:HR | 退回 0.89 × LTHR | 用 Pa:HR（`drift_agg` 已經會） |
| `drift_agg.py:336-339`、`:167-191` | 有功率就用 Pw:HR | 手腕 Pw:HR 跟 Pa:HR 混在一起 | 只用配速 |
| `thresholds.py:255` `_tpace_at_cp`、`:289` `estimate_tpace` | 閾值配速：CP × Stryd 速度／W，否則 LTHR 附近的配速 | — | 不用功率時走 LTHR 那條 |
| `race_readiness.py:985` | `workout_curve("power")` | 手腕「新紀錄」 | 不用功率時不看 |

## D. 計算機、賽事可行性

| 位置 | 功率在做什麼 | 現在 | 應該 |
|---|---|---|---|
| `racepower/calc.py:343-351`、`:383-391` | v1 `predict` 要 CP、RE | 沒 CP 回 400 | 不用功率時直接走心率／配速 |
| `calc.py:659-744`、`racepower/planner.py:668-800` | 全程時間 = CP＋Riegel＋RE；分段照 `GradeRE`（功率擬合） | 沒 CP 跑不了 | 越野：心率模型（`trailhr.py`、`planner.trail_hr_estimate` `:556`，現在要 v1 先成功才走到 `:756-767`），分段用 Minetti／GAP；路跑：`/estimate`（`api/racepower.py:337-351`、`race_estimate.py:63-119`，Riegel／VDOT）當主路徑 |
| `calc.py:732-739`、`goal.py:26-30` | 目標「功率」模式 | `basis(None)` 回 power | 強制心率 |
| `seg_targets.py:175-272`、`watch_export.py:205-235` | 分段目標、手錶匯出：有 CP 就功率 ±3 % | 舊 CP 推瓦數到手錶 | 心率、Effort Pace 或配速 |
| `zonebar.py:79-92` | 心率區間條；路跑沒比賽心率時退回 % CP 條 | 顯示 % CP | 隱藏或心率／配速 |
| `race_feasibility.py:1006` | 百岳 W/kg 爬坡檢查讀舊欄位 `power_meter` | `api/plan.py:863` 存 `power_source` 時清掉 `power_meter` → **連 Stryd 使用者都看不到**（bug） | 看 `profile_power_source(...) == "stryd"` |
| `race_feasibility.py:1085-1120`、`:1149-1159`、`:558-561` | 越野完賽時間走 `make_plan` | 沒 CP 退回課表時數，文字說「沒有越野心率配速模型」 | 直接用心率模型、改文字 |
| `panels/race_refs.py:170-191` | `RP.predict` | 沒 CP 回 None | 同上 |
| `user_templates.py:598-631` 坡段表 | × CP 的功率欄 | 「–」 | 隱藏功率欄 |
| 百岳（`calc.py:465+` `predict_baiyue`、`plan_hike`） | EP/h、Tobler | 不用功率 | 不變 |

## E. 只是顯示（功率來源 = 無 時隱藏）

已經照 `use_power` 藏的：`activity.html` 功率欄、`charts.html` 功率圖（`wko5expr/power_use.py`、`api/wko5views.py:859`）、`overview.html:976` CP。

還沒藏的：
- `racepower.html`：`:368-381`、`:803`、`:818`（目標功率模式）、`:1290+`、`:1345-47`（CP、W′、RE、功率計）、`:1822`、`:1828`、`:1843-50`、`:1856`、`:1948-64`（平均功率／% CP／W/kg、功率↔時間表）、`:2178-84`（補給「功率法」→ 心率法）、`:2275`、`:535`、`:2568-92`（分段的功率）、`:622`、`:630-634`、`:643`、`:647`、`:2723-2852`、`:3014`（RE、CP、準確度的功率偏差）、`:1567-90` 文字「還沒有 CP…有 CP 之後」要改
- `seg_profile.js:36-52`、`:43`、`:111`、`:230`、`:242`；`routelib.html:231-33`
- `share.html:141`、`:247-48`、`:265`
- `schedule.html:849-860`、`:907`、`:714`、`:3724`（目標依據、可跑坡目標、CP 測試）；`workout_editor.js:241`、`:543-45`、`:663-65`、`:674`、`:1249`（預設 `"power"` → hr）；`templates.html:172`、`:328`
- `settings.html:439-449`、`:2073-80`（使用功率、接受手錶功率：選「無」時要清掉並隱藏）、`:493`、`:503-05`、`:508-530`、`:1607-18`（CP 欄、CP 怎麼測、功率區間）
- `charts.html:2239-42`（drawTargets 的 CP 線、功率欄：`targets` 不在 `chart_needs_power`）、`:2442-46`
- `overview.html:361-369`、`:716` 名詞說明「固定功率跑」、CP 測試
- `intro.html:360`、`:389`、`:427-29`、`:472-73`、`demo.html:66`、`:72`、`:78`、`sourcechip.js:31`「功率計算機」：文字

只有資料有功率才顯示的（不用改）：`routes.html`、`actcompare.html`、`sesspop.js`、`eptest.js`。

## F. 測試缺口

沒有任何測試蓋到「來源 = 無」的：target_policy 遇到明確的功率偏好、COROS AeT／間歇步驟、`xu_target`、圈數沒功率的階梯、CP 測試建議擋住 AeT、Zone 3 完成判定、來源改變後重推。`tests/test_auto_zones_sp493.py:265` 把 `_auto_power_ok` 固定成 True。
