# 圖表單位與座標軸稽核（chart units audit）

產生時間：2026-09-30 12:14，由 `backend/scripts/audit_chart_units.py` 產生（可重跑）。

## 範圍

- 引擎設定：parity = False（使用者目前的模式）
- 賽季圖表：2025-09-30 – 2026-09-30（最近 365 天），全部運動
- 單次活動圖表：#1097 2026-09-24 running, #1094 2026-09-20 trail running, #1073 2026-08-14 mountaineering
- 檢查 160 張圖、886 條 series（workout 圖在每筆活動各算一次），1581 次 series 渲染，耗時 216 秒

## 摘要

| 檢查 | 說明 | 發現數（未修正） | 套用 wko5_fixes 後 |
|---|---|---:|---:|
| imperial | 英制單位 id 或 english() | 4 | 1 |
| percent-range | PERCENT 不是分數 | 1 | 1 |
| duration-scale | 時間軸數值像分鐘／小時 | 0 | 0 |
| magnitude | 數值量級與單位不符 | 0 | 0 |
| axis-mismatch | series 的量不屬於這個軸 | 10 | 0 |
| clip | 固定軸範圍裁掉 >5% 資料點 | 13 | 0 |
| cadence | 單腳步頻放在 spm 軸 | 11 | 0 |
| pace-base | 配速存成 s/km 或 km/h | 8 | 7 |
| error | series 執行失敗 | 0 | 0 |

「套用後」一欄：imperial / pace-base 由 render_chart 在非 parity 模式自動轉換，仍會被偵測到（偵測看的是原始定義），所以數字不會歸零；其他欄是 `views/wko5_fixes.json` 修掉後剩下的。

## 單位政策

- 單位定義集中在 `backend/engine/wko5expr/units.py`：每個 WKO5 id 有顯示標籤、種類（number / duration / pace / percent / date）、依量級決定的小數位數，以及顯示倍率（PERCENT 存分數 ×100；CM、MILLISECONDS 不需倍率——式子裡 stancetime 已是 ms、verticaloscillation 已是 cm，與 WKO5 相同）。
- 小數：W 0、W/kg 2、kJ 0、TSS 0、TSS/天 ≥10 取 0 否則 1、m 0、km ≥100 取 0 / ≥10 取 1 / 其餘 2、km/h 1、m/s 2、m/h 0、bpm 0、spm 0、% ≥10% 取 0 否則 1、L/min 2、mL/min/kg 1；NONE 與 CUSTOM<label>：|v| ≥ 100 → 0、≥ 10 → 1、其餘 2。
- 時間（HHMMSS/HMSLONG/HMSSHORT/SECONDS，單位秒）顯示 h:mm:ss 或 m:ss；配速一律 m:ss /km。
- 非 parity 模式：`english(x)` 改成 `metric(x)` 計算（evaluator 內部全是公制），FT/MI/MPH/PACEMI/FAHRENHEIT 改成 m/km/km/h/min/km/°C；配速 series 若是 s/km 或 km/h（ngp）會換成 min/km。parity 模式保留 WKO5 原本的 id 與數值，只套用合理的小數。
- WKO5 圖表本身的設計錯誤（單位 id 選錯、軸範圍裁掉資料、單腳步頻）寫在 `views/wko5_fixes.json`，只在非 parity 模式套用，套用過的圖表在檢視器顯示「已修正單位」標籤。

## 發現（每列一項）

| View / 圖表 / series | 問題 | 證據 | 處理 |
|---|---|---|---|
| WKO5 Workout View / Palladino Run Summary Report / CVI（3 筆活動） | `imperial` imperial unit (NONE, english()) | shown as NONE () outside parity | fixed: CVI 重複兩次（英制 ft/mi）→ 保留一個並改公制 |
| WKO5 Workout View / Palladino Run Summary Report / CVI (feet/mile)（3 筆活動） | `imperial` imperial unit (CUSTOMhill, english()) | shown as CUSTOMhill (hill) outside parity | fixed: CVI 重複兩次（英制 ft/mi）→ 保留一個並改公制 |
| WKO5 Workout View / Palladino Run Summary Report / Distance (mi)（3 筆活動） | `imperial` imperial unit (NONE, english()) | shown as NONE () outside parity | fixed: 英制距離（mi）與「Distance (km)」重複 → 移除 |
| WKO5 Workout View / Palladino Run Summary Report / Pace min/mi（3 筆活動） | `imperial` imperial unit (PACEMI, english()) | shown as PACEKM (/km) outside parity | fixed: 英制配速（/mi）與「Pace min/km」重複 → 移除 |
| WKO5 Season View / Daily % of CTL (run TSS/CTL) / TSS/CTL>= 300% | `percent-range` PERCENT series isn't a fraction (already ×100?) | min 3.074 / median 4.133 / max 14.78 | left as is — intended: the series only shows days with TSS ≥ 300% of CTL, so fractions ≥ 3 are right |
| WKO5 Season View / Daily % of CTL (run TSS/CTL) / High IF | `axis-mismatch` `if` plotted on a TSS axis | median 2.368 | fixed: 「High IF」是強度係數（無單位），原本畫在 TSS 軸 → 改無單位軸 |
| WKO5 Season View / Month Time & Distance (only run) / Monthly Distance | `axis-mismatch` `distance` plotted on a CUSTOMEP axis | median 81.37 | fixed: 月距離原本畫在 EP 軸 → 改 km 軸 |
| WKO5 Season View / Weekly Time & Distance (only run) / Weekly Distance | `axis-mismatch` `distance` plotted on a CUSTOMEP axis | median 20.32 | fixed: 週距離原本畫在 EP 軸 → 改 km 軸 |
| WKO5 Season View / 指標性越野跑 / IF | `axis-mismatch` `if` plotted on a METERS axis | median 0.5036 | fixed: IF（無單位）原本畫在公尺軸 → 改無單位軸 |
| WKO5 Season View / 指標性越野跑 / description | `axis-mismatch` `description` plotted on a METERS axis | static check (no data in range) | fixed: 文字欄位 description 原本掛在公尺軸 → 改無單位 |
| WKO5 Workout View / Palladino Run Summary Report / Avg Power（3 筆活動） | `axis-mismatch` `power` plotted on a NONE axis | median 150.1 | fixed: 功率欄位原本無單位或掛在自訂軸 → W |
| WKO5 Workout View / Palladino Run Summary Report / Climbing (ft)（3 筆活動） | `axis-mismatch` `climbing` plotted on a CUSTOMhill axis | median 24.4 | fixed: 爬升／下降／淨高度差是公尺數值卻標成 ft → m |
| WKO5 Workout View / Palladino Run Summary Report / Descending (ft)（3 筆活動） | `axis-mismatch` `descending` plotted on a CUSTOMhill axis | median 29.4 | fixed: 爬升／下降／淨高度差是公尺數值卻標成 ft → m |
| WKO5 Workout View / Palladino Run Summary Report / Pt (W)（3 筆活動） | `axis-mismatch` `runpower` plotted on a CUSTOMair axis | median 150.1 | fixed: 功率欄位原本無單位或掛在自訂軸 → W |
| WKO5 Workout View / Time in HR Zones / Avg Heart Rate（3 筆活動） | `axis-mismatch` `heartrate` plotted on a NONE axis | median 136.7 | fixed: 平均心率原本無單位 → bpm |
| WKO5 Season View / ATL CTL Ratio 訓練負荷比 (only run) / (axis) | `clip` fixed axis range 0.5–2 clips 8% of points | 31/368 points, data 0.2457–2.883 | fixed: ATL/CTL 軸原本固定 0.5–2，裁掉 8% 的點（實際 0.25–2.9）→ 改自動範圍 |
| WKO5 Season View / CTL Ramp Rate - Run (CTL接近80再看這張圖) / (axis) | `clip` fixed axis range ≥ 0 clips 54% of points | 199/366 points, data -0.1573–0.3383 | fixed: CTL 週增率百分比軸原本最小 0%，負成長（54% 的點）被裁掉 → 允許負值 |
| WKO5 Season View / Daily % of CTL (run TSS/CTL) / (axis) | `clip` fixed axis range 0.5–3 clips 7% of points | 13/183 points, data 0.02069–14.78 | fixed: TSS/CTL 軸原本固定 50–300%，裁掉 7% 的點（最高 1478%）→ 改自動範圍 |
| WKO5 Workout View / Time in iLevel Heat Map Run (Pwoer) / (axis)（1 筆活動） | `clip` fixed axis range ≥ 50 clips 13% of points | 594/4659 points, data 0–486 | fixed: 功率軸原本從 50 W 起，停下來的 0 W（14% 的點）被裁掉 → 從 0 起 |
| WKO5 Workout View / VAM Analysis / (axis)（1 筆活動） | `clip` fixed axis range ≥ 0 clips 62% of points | 1507/2434 points, data -291.1–720 | fixed: VAM 原本無單位且軸最小 0（下坡 37–62% 的點被裁掉）→ 改 m/h 自動範圍；坡度軸原本最小 0%，下坡（22–41% 的點）被裁掉 → 允許負值 |
| WKO5 Workout View / VAM Analysis / (axis)（1 筆活動） | `clip` fixed axis range ≥ 0 clips 27% of points | 1162/4366 points, data -0.2023–0.3 | fixed: 坡度軸原本最小 0%，下坡（22–41% 的點）被裁掉 → 允許負值 |
| WKO5 Workout View / VAM Analysis / (axis)（1 筆活動） | `clip` fixed axis range ≥ 0 clips 37% of points | 985/2635 points, data -1104–3240 | fixed: VAM 原本無單位且軸最小 0（下坡 37–62% 的點被裁掉）→ 改 m/h 自動範圍；坡度軸原本最小 0%，下坡（22–41% 的點）被裁掉 → 允許負值 |
| WKO5 Workout View / VAM Analysis / (axis)（1 筆活動） | `clip` fixed axis range ≥ 0 clips 43% of points | 1956/4508 points, data -1.245–1.598 | fixed: 坡度軸原本最小 0%，下坡（22–41% 的點）被裁掉 → 允許負值 |
| WKO5 Workout View / VAM Analysis / (axis)（1 筆活動） | `clip` fixed axis range ≥ 0 clips 39% of points | 1314/3401 points, data -1088–1800 | fixed: VAM 原本無單位且軸最小 0（下坡 37–62% 的點被裁掉）→ 改 m/h 自動範圍；坡度軸原本最小 0%，下坡（22–41% 的點）被裁掉 → 允許負值 |
| WKO5 Workout View / VAM Analysis / (axis)（1 筆活動） | `clip` fixed axis range ≥ 0 clips 40% of points | 1970/4884 points, data -1.053–0.7609 | fixed: 坡度軸原本最小 0%，下坡（22–41% 的點）被裁掉 → 允許負值 |
| WKO5 Workout View / dFRC Run / (axis)（1 筆活動） | `clip` fixed axis range ≥ 0 clips 27% of points | 581/2183 points, data -0.2023–0.3 | fixed: 坡度軸原本最小 0%，下坡（22–41% 的點）被裁掉 → 允許負值 |
| WKO5 Workout View / dFRC Run / (axis)（1 筆活動） | `clip` fixed axis range ≥ 0 clips 43% of points | 978/2254 points, data -1.245–1.598 | fixed: 坡度軸原本最小 0%，下坡（22–41% 的點）被裁掉 → 允許負值 |
| WKO5 Workout View / dFRC Run / (axis)（1 筆活動） | `clip` fixed axis range ≥ 0 clips 40% of points | 985/2442 points, data -1.053–0.7609 | fixed: 坡度軸原本最小 0%，下坡（22–41% 的點）被裁掉 → 允許負值 |
| WKO5 Workout View / Cadence / Cadence（2 筆活動） | `cadence` single-leg running cadence on a steps/min axis (×2 for spm) | median 79 | fixed: cadence 是單腳步頻（約 79），標成 spm 應 ×2（WKO5 自己的 Workout Graph 也是 cadence*2） |
| WKO5 Workout View / Cadence / Fill（2 筆活動） | `cadence` single-leg running cadence on a steps/min axis (×2 for spm) | median 79 | fixed: cadence 是單腳步頻（約 79），標成 spm 應 ×2（WKO5 自己的 Workout Graph 也是 cadence*2） |
| WKO5 Workout View / Cadence Summary / Avg Cadence vs. Max Cadence（2 筆活動） | `cadence` single-leg running cadence on a steps/min axis (×2 for spm) | median 79.36 | fixed: 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / Cadence Variation and Trend / Cadence（2 筆活動） | `cadence` single-leg running cadence on a steps/min axis (×2 for spm) | median 79 | fixed: 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / Cadence Variation and Trend / Cadence Trend（2 筆活動） | `cadence` single-leg running cadence on a steps/min axis (×2 for spm) | median 71.69 | fixed: 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / Cadence Variation and Trend / High（2 筆活動） | `cadence` single-leg running cadence on a steps/min axis (×2 for spm) | median 83.88 | fixed: 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / Cadence Variation and Trend / Low（1 筆活動） | `cadence` single-leg running cadence on a steps/min axis (×2 for spm) | median 59.55 | fixed: 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / 步頻 vs 垂直比 / grade < 0%（2 筆活動） | `cadence` single-leg running cadence on a steps/min axis (×2 for spm) | median 79 | fixed: 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / 步頻 vs 垂直比 / grade <-20%（2 筆活動） | `cadence` single-leg running cadence on a steps/min axis (×2 for spm) | median 79 | fixed: 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / 步頻 vs 垂直比 / grade <-40%（2 筆活動） | `cadence` single-leg running cadence on a steps/min axis (×2 for spm) | median 79 | fixed: 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / 步頻與垂直比分析 / Stride Rate(兩腳步頻)（2 筆活動） | `cadence` single-leg running cadence on a steps/min axis (×2 for spm) | median 79 | fixed: 「兩腳步頻」其實是 cadence 單腳值 → ×2 |
| WKO5 Season View / Daily Training Log / 坡度調整配速 | `pace-base` pace series holds km/h; normalised to min/km | expr `rev(if(sport="run",ngp))` | fixed automatically: pace normalised to min/km, shown m:ss /km |
| WKO5 Workout View / Hilly Run Summary / NGP（3 筆活動） | `pace-base` pace series holds km/h; normalised to min/km | expr `ngp` | fixed automatically: pace normalised to min/km, shown m:ss /km |
| WKO5 Workout View / Hilly Run Summary / Pace（3 筆活動） | `pace-base` pace series holds s/km; normalised to min/km | expr `avg(duration/distance)` | fixed automatically: pace normalised to min/km, shown m:ss /km |
| WKO5 Workout View / Pace / pace（3 筆活動） | `pace-base` pace series holds s/km; normalised to min/km | expr `avg(duration/distance)` | fixed automatically: pace normalised to min/km, shown m:ss /km |
| WKO5 Workout View / Palladino Run Summary Report / NGP（3 筆活動） | `pace-base` pace series holds km/h; normalised to min/km | expr `ngp` | fixed automatically: pace normalised to min/km, shown m:ss /km |
| WKO5 Workout View / Palladino Run Summary Report / Pace min/km（3 筆活動） | `pace-base` pace series holds s/km; normalised to min/km | expr `avg(duration/metric(distance))` | fixed: 配速原本是 s/km 放在 min/km 軸 → 換算並顯示 m:ss /km |
| WKO5 Workout View / Palladino Run Summary Report / Pace min/mi（3 筆活動） | `pace-base` pace series holds s/km; normalised to min/km | expr `avg(duration/english(distance))` | fixed: 英制配速（/mi）與「Pace min/km」重複 → 移除 |
| WKO5 Workout View / ngp / TSS（3 筆活動） | `pace-base` pace series holds km/h; normalised to min/km | expr `ngp` | fixed: series 名稱寫成 TSS，實際是坡度調整配速 NGP → 改名 |

## 已套用的 WKO5 圖表修正（`views/wko5_fixes.json`，51 項，只在非 parity 模式）

| View / 圖表 | 目標 | 修正 |
|---|---|---|
| WKO5 Season View / ATL CTL Ratio 訓練負荷比 (only run) | 軸 NONE | min=null, max=null — ATL/CTL 軸原本固定 0.5–2，裁掉 8% 的點（實際 0.25–2.9）→ 改自動範圍 |
| WKO5 Season View / Daily % of CTL (run TSS/CTL) | 軸 PERCENT | min=null, max=null — TSS/CTL 軸原本固定 50–300%，裁掉 7% 的點（最高 1478%）→ 改自動範圍 |
| WKO5 Season View / Daily % of CTL (run TSS/CTL) | series「High IF」 | y_axis="NONE" — 「High IF」是強度係數（無單位），原本畫在 TSS 軸 → 改無單位軸 |
| WKO5 Season View / CTL Ramp Rate - Run (CTL接近80再看這張圖) | 軸 PERCENT | min=null — CTL 週增率百分比軸原本最小 0%，負成長（54% 的點）被裁掉 → 允許負值 |
| WKO5 Season View / Aerobic Power Review (only平地>4km跑步) | 軸 PERCENT | min=null — 百分比軸原本最小 0%，負值（17% 的點）被裁掉 → 允許負值 |
| WKO5 Season View / Weekly Time & Distance (only run) | series「Weekly Distance」 | y_axis="KM" — 週距離原本畫在 EP 軸 → 改 km 軸 |
| WKO5 Season View / Month Time & Distance (only run) | series「Monthly Distance」 | y_axis="KM" — 月距離原本畫在 EP 軸 → 改 km 軸 |
| WKO5 Season View / 指標性越野跑 | series「IF」 | y_axis="NONE" — IF（無單位）原本畫在公尺軸 → 改無單位軸 |
| WKO5 Season View / 指標性越野跑 | series「description」 | y_axis="NONE" — 文字欄位 description 原本掛在公尺軸 → 改無單位 |
| WKO5 Workout View / Cadence | series「Cadence」 | ×2 — cadence 是單腳步頻（約 79），標成 spm 應 ×2（WKO5 自己的 Workout Graph 也是 cadence*2） |
| WKO5 Workout View / Cadence | series「Fill」 | ×2 — cadence 是單腳步頻（約 79），標成 spm 應 ×2（WKO5 自己的 Workout Graph 也是 cadence*2） |
| WKO5 Workout View / Cadence Summary | series「Avg Cadence vs. Max Cadence」 | ×2 — 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / Cadence Variation and Trend | series「High」 | ×2 — 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / Cadence Variation and Trend | series「Standard Deviation」 | ×2 — 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / Cadence Variation and Trend | series「Low」 | ×2 — 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / Cadence Variation and Trend | series「Cadence」 | ×2 — 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / Cadence Variation and Trend | series「Cadence Trend」 | ×2 — 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / 步頻與垂直比分析 | series「Stride Rate(兩腳步頻)」 | ×2 — 「兩腳步頻」其實是 cadence 單腳值 → ×2 |
| WKO5 Workout View / 步頻 vs 垂直比 | series「grade <-40%」 | ×2 — 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / 步頻 vs 垂直比 | series「grade <-20%」 | ×2 — 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / 步頻 vs 垂直比 | series「grade < 0%」 | ×2 — 單腳步頻 ×2 → 每分鐘步數（spm） |
| WKO5 Workout View / ngp | series「TSS」 | name="NGP" — series 名稱寫成 TSS，實際是坡度調整配速 NGP → 改名 |
| WKO5 Workout View / Time in HR Zones | series「Avg Heart Rate」 | y_axis="BPM" — 平均心率原本無單位 → bpm |
| WKO5 Workout View / Time in iLevel Heat Map Run (Pwoer) | 軸 WATTS | min=0 — 功率軸原本從 50 W 起，停下來的 0 W（14% 的點）被裁掉 → 從 0 起 |
| WKO5 Workout View / VAM Analysis | series「VAM」 | y_axis="METERSPERHOUR" — VAM 原本無單位且軸最小 0（下坡 37–62% 的點被裁掉）→ 改 m/h 自動範圍 |
| WKO5 Workout View / VAM Analysis | series「MM VAM」 | y_axis="METERSPERHOUR" — VAM 原本無單位且軸最小 0（下坡 37–62% 的點被裁掉）→ 改 m/h 自動範圍 |
| WKO5 Workout View / VAM Analysis | series「MM VAM (without descending)」 | y_axis="METERSPERHOUR" — VAM 原本無單位且軸最小 0（下坡 37–62% 的點被裁掉）→ 改 m/h 自動範圍 |
| WKO5 Workout View / VAM Analysis | 軸 PERCENT | min=null — 坡度軸原本最小 0%，下坡（22–41% 的點）被裁掉 → 允許負值 |
| WKO5 Workout View / dFRC Run | 軸 PERCENT | min=null — 坡度軸原本最小 0%，下坡（22–41% 的點）被裁掉 → 允許負值 |
| WKO5 Workout View / Palladino Run Summary Report | series「Distance (mi)」 | 移除 — 英制距離（mi）與「Distance (km)」重複 → 移除 |
| WKO5 Workout View / Palladino Run Summary Report | series「Distance (km)」 | y_axis="KM", name="Distance" — 距離原本無單位 → km |
| WKO5 Workout View / Palladino Run Summary Report | series「Pace min/mi」 | 移除 — 英制配速（/mi）與「Pace min/km」重複 → 移除 |
| WKO5 Workout View / Palladino Run Summary Report | series「Pace min/km」 | name="Pace" — 配速原本是 s/km 放在 min/km 軸 → 換算並顯示 m:ss /km |
| WKO5 Workout View / Palladino Run Summary Report | series「CVI」 | 移除 — CVI 重複兩次（英制 ft/mi）→ 保留一個並改公制 |
| WKO5 Workout View / Palladino Run Summary Report | series「CVI (feet/mile)」 | y_axis="CUSTOMm/km", name="CVI 每公里爬升" — CVI 重複兩次（英制 ft/mi）→ 保留一個並改公制 |
| WKO5 Workout View / Palladino Run Summary Report | series「Net Elevation Change (ft)」 | y_axis="METERS", name="Net Elevation Change" — 爬升／下降／淨高度差是公尺數值卻標成 ft → m |
| WKO5 Workout View / Palladino Run Summary Report | series「Climbing (ft)」 | y_axis="METERS", name="Climbing" — 爬升／下降／淨高度差是公尺數值卻標成 ft → m |
| WKO5 Workout View / Palladino Run Summary Report | series「Descending (ft)」 | y_axis="METERS", name="Descending" — 爬升／下降／淨高度差是公尺數值卻標成 ft → m |
| WKO5 Workout View / Palladino Run Summary Report | series「Avg Grade (%)」 | y_axis="PERCENT", name="Avg Grade", ×0.01 — 已 ×100 的百分比改回分數，用 % 格式顯示 |
| WKO5 Workout View / Palladino Run Summary Report | series「% of sFTP」 | y_axis="PERCENT", ×0.01 — 已 ×100 的百分比改回分數，用 % 格式顯示 |
| WKO5 Workout View / Palladino Run Summary Report | series「Horiz Pwr Ratio」 | y_axis="PERCENT", ×0.01 — 已 ×100 的百分比改回分數，用 % 格式顯示 |
| WKO5 Workout View / Palladino Run Summary Report | series「Avg Power」 | y_axis="WATTS" — 功率欄位原本無單位或掛在自訂軸 → W |
| WKO5 Workout View / Palladino Run Summary Report | series「Pt (W)」 | y_axis="WATTS", name="Pt" — 功率欄位原本無單位或掛在自訂軸 → W |
| WKO5 Workout View / Palladino Run Summary Report | series「Pa (W)」 | y_axis="WATTS", name="Pa" — 功率欄位原本無單位或掛在自訂軸 → W |
| WKO5 Workout View / Palladino Run Summary Report | series「Pr (W)」 | y_axis="WATTS", name="Pr" — 功率欄位原本無單位或掛在自訂軸 → W |
| WKO5 Workout View / Palladino Run Summary Report | series「Pa %」 | y_axis="PERCENT" — 比例（分數）原本掛在自訂軸 → % |
| WKO5 Workout View / Palladino Run Summary Report | series「Pr %」 | y_axis="PERCENT" — 比例（分數）原本掛在自訂軸 → % |
| WKO5 Workout View / Palladino Run Summary Report | series「Pwr:Wt」 | y_axis="WATTSKG" — 功率體重比原本無單位 → W/kg |
| WKO5 Workout View / Palladino Run Summary Report | series「GCT」 | y_axis="MILLISECONDS" — 觸地時間（式子裡是 ms，與 WKO5 相同）原本無單位 → ms |
| WKO5 Workout View / Palladino Run Summary Report | series「Pwr-GCT」 | y_axis="CUSTOMW/ms" — 功率 ÷ 觸地時間（ms）原本無單位 → W/ms（同 Hilly Run Summary） |
| WKO5 Workout View / Palladino Run Summary Report | series「Stride Rate」 | y_axis="RPM" — 步頻（cadence*2）原本無單位 → spm |
