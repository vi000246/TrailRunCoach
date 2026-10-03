# 百岳時間：從你自己的跑步資料推算步行能力

Date: 2026-09-30. 範圍：研究與設計，**不改程式碼**。

- 現況見 `docs/spec/racepower.spec.md`、`docs/research/racepower-v2.md`（§2.5、§3C.2、§7）。
- 本文件只寫「百岳時間要反映你自己的能力」這件事：
  - 新增或改變的模型；
  - 資料管線；
  - `planner.plan_hike` 怎麼改；
  - UI；
  - 驗證。
- 熱適應由另一份文件 `docs/research/heat-acclimation.md` 負責（撰寫中）。本文件只定義接口（§8.1）。

## 0. 摘要與建議

**問題**：

- 百岳多半跟團，跟團的速度不是你的能力。所以跟團的百岳目前完全不進目標時間校正。
- 沒有標記「自己走」的日子時，百岳 ETA 會退回 Tobler，而 Tobler 是**通用**模型。
- 使用者不接受這樣：預測必須是**他自己的能力**。

**建議模型**（協調者提出、使用者認可的基線。本文逐項對過來源，有三處依證據修正，見 §0.1）：

1. **主要資料**：越野跑裡走路的陡坡窗。
   - 條件：步頻 < 130 spm，坡度 ≥ 10 %，連續窗。
   - 有心率，也沒有團體拖慢。
   - 目前快取裡有 **962 窗，來自 58 次活動**（§2.2）。
   - 這是所有戶外有功率的跑步：快取沒有越野旗標。實作時要用 `grade_samples` 的 `trail` 欄位再篩。
2. **第二來源**：百岳的心率過濾陡坡窗。
   - 條件：HR ≥ AeT，連續 ≥ 300 m，坡度 ≥ 10 %；共 157 窗。
   - 主要用途是海拔項，以及高海拔、重背負下的校正。
3. **生理先驗**（取代 Tobler，是**你個人的**先驗）：
   - 先從平地跑步在 AeT 心率的速度，算出你在 AeT 的代謝功率。
   - 再用 Pandolf 反解：在坡度 g、背負 L 下，同一個代謝功率走得多快（§3.1）。
   - 個人窗的資料往這個先驗收縮，而不是往 Tobler 收縮。
4. **強度用心率**：
   - 擬合「坡度 × 心率帶 → VAM」。多日百岳預設 AeT；使用者可以選較高的心率帶，看能快多少。
   - 注意：原始窗的 VAM 幾乎不隨心率帶改變（§2.2）。心率落後與窗太短是主因，擬合前要處理。
5. **平地與下坡**：
   - 取越野跑裡走路或慢跑的部分，套技術度係數。
   - 下坡用 Tobler 形狀的上限封頂（§3.4）。
6. **百岳修正**：
   - 海拔：個人 −9.9 %/1000 m 往文獻收縮。先驗中心放在 −6.3 %（Wehrlin），寬度要大，理由見 §2.4。
   - 背負：Pandolf，只用在上坡與平地。
   - 地形：每段一個 η。
   - 多日疲勞：資料不夠時就用 1.0，並標出來。
   - 休息：用過去登山的移動比，換成時鐘時間。
   - 熱：接口。
7. **驗證**：
   - 對兩種窗做 leave-one-**activity**-out。高海拔窗單獨看。
   - 整天的百岳紀錄只當合理範圍（sanity band）。
   - 通過之前，一律標**推估**。
8. **限制（必須寫在頁面上）**：
   - 沒有任何越野跑資料涵蓋「背 10–15 kg、每天 6–10 h、連走好幾天」。
   - 背負與多日效應只能靠公式，而這些公式的誤差約 ±15 %（Looney 2022、Weyand 2021）。
   - 所以百岳的不確定帶一定比越野預測寬。

### 0.1 相對於基線的三處修正

1. **海拔先驗的性質不同**：
   - 個人的 −9.9 % 是「**同一心率下的速度**」隨海拔下降的斜率。
   - Wehrlin 的 −6.3 % 是 **VO2max** 的斜率。Wehrlin 也寫到：同速度的次最大 VO2 不變，但心率上升。
   - 所以兩者不是同一個量，不能直接平均。
   - 另一個可比的文獻值是 Coffman 2020：自訂配速、心率各條件相同，換算約 −3.8 %/1000 m（§2.4）。
   - −9.9 % 比兩者都陡，很可能混有干擾：
     - 高營地多在第 2 天；
     - 寒冷；
     - 殘餘的團體效應；
     - 地形。
   - 所以先做 §2.4 的診斷，再用精度加權收縮，先驗寬度 τ 取大（3 個百分點，推估）。
2. **背負不只 Pandolf**：
   - Ludlow & Weyand 2017 的摘要（一手）寫明：步行的代謝量（扣掉靜息）**與總重量（體重 + 背負）成正比**。量測範圍是 −6 到 +9°，負重 +18 % 與 +31 % 體重。
   - 百岳背負 8–15 kg，對 70 kg 是 11–21 % 體重，落在這個範圍內。
   - 在陡坡，Pandolf 的比值趨近線性係數 (W+L₀)/(W+L)：G 30 % 時 0.880 對 0.875（§3.2 算例）。
   - 所以：
     - 上坡與平地用 Pandolf 反解（已驗證，二手核對）；
     - Ludlow & Weyand 的比例關係當第二個獨立依據；
     - 線性係數從「假設」升級為「陡坡時與兩個來源一致」；
     - 下坡仍用線性係數，標推估。
3. **Tobler 不再是先驗**：
   - Tobler 在 20 % 坡給 2.50 km/h，VAM 約 500 m/h。
   - 你走路陡坡窗的中位數是 744 m/h（20–30 %）。
   - Pandolf 生理先驗（算例，§3.1）給 787 m/h（20 %）。
   - Tobler 只剩兩個角色：下坡上限的「形狀」，以及交叉檢查。

## 1. 標記

沿用 `racepower-v2.md` §0.1。

**依據標記**：

- **[確立]** 有同儕審查的研究直接支持，而且用在它量測過的範圍內。
- **[經驗法則]** 社群或指南的慣例。
- **[外插]** 公式本身已確立，但用在量測範圍之外。
- **[推估]** 我們自己把幾個來源組合起來，沒有任何單一來源這樣寫。
- **[未找到來源]** 本次研究沒找到文獻。

**驗證狀態**：

- **已驗證**：原式對過，而且有算例或獨立重算寫成測試。
- **已驗證（二手核對）**：原文讀不到，但有 ≥ 2 個獨立開放來源逐字寫出同一式。
- **待驗證**：還沒對過原文，或推估公式還沒通過回測。
  - 待驗證的公式**不能產生頁面上的主要目標**，只能當交叉檢查。
  - 例外：沿用 v2 的降級路徑，標「推估」顯示。

**取用限制（本次）**：

- WebSearch 額度已用完。文獻改用下列管道查：
  - Europe PMC REST（題名、作者、摘要、DOI）；
  - Crossref API（期刊、卷頁）；
  - PMC 網頁（全文）。
- 下面寫「摘要」的，表示只讀過摘要，沒讀全文。

---

## 2. 研究結果

### 2.1 Q1：跑步能力能不能推算上坡步行速度

**連結的邏輯** **[確立 + 推估]**：

- 走路和跑步共用同一套心肺系統。在某個心率（例如 AeT）能持續的**代謝功率**與步態無關；不同的是每公尺的代謝成本。
- 所以可以這樣推：
  1. 從平地跑步算出在 AeT 的代謝功率 Ė_AeT；
  2. 除以步行在坡度 g、背負 L 下的成本，得到步行速度。
- 這個「串接」是**推估**。其中各段的來源如下。

| 環節 | 來源 | 狀態 |
|---|---|---|
| 平地跑步成本 Cr(0) | Minetti et al. 2002：平地 Cr 3.40 ± 0.24 J/kg/m，**與速度無關**（摘要）；多項式常數項 3.6 | 已驗證（`test_minetti`） |
| 步行成本 Cw(g)（最低成本） | Minetti 2002：Cw(0) 1.64 J/kg/m（在 1.0 m/s）；+0.45 時 17.33；+0.15 以上效率接近向心肌肉收縮效率；山徑最佳坡度 0.20–0.30（摘要） | 已驗證 |
| 步行代謝率（含速度、坡度、背負） | Pandolf, Givoni & Goldman 1977：`M = 1.5W + 2.0(W+L)(L/W)² + η(W+L)(1.5V² + 0.35VG)` | 已驗證（二手核對，G ≥ 0；`racepower-v2.md` §3C.2） |
| 背負的比例關係 | Ludlow & Weyand 2017（摘要）：步行代謝量（毛量 − 仰臥靜息）隨總重「直接成正比」；以單位（體重 + 背負）表示的經濟性，對所有負重條件都能用同一式預測，r² 0.99，SEE 1.06 mL/kg/min | 比例關係已驗證（一手摘要） |
| Ludlow & Weyand 的係數式 | Weyand et al. 2021 表 1 轉錄為 `3.05+(w+l)wt(0.32g+3.28+(1+0.19g)2.66s2)`。分數線在網頁轉錄中遺失，「wt」可能是「/w · t（地形）」；坡度單位（% 或度）也不確定 | **待驗證**（單一二手來源、轉錄不清），不採用係數 |
| LCDA 步行式 | Looney et al. 2019：坡度 −40 % 到 +45 %，偏差 0.09 ± 0.40 W/kg，SEE 0.42 W/kg（摘要）；坡道項已驗證（二手核對，`racepower-v2.md` §3C.2） | 平地項因版本而異，不採用 |
| 跑步能力 → 負重步行表現 | Robinson et al. 2018：警察 25 kg 背負 5 km，有氧能力與成績的相關最高（r ≈ −0.71，摘要） | 確立（方向）；不是公式 |
| VO2max 的重要性隨距離下降 | Davies & Thompson 1979：距離愈長，%VO2max（持續能力）對成績的解釋力愈大（摘要） | 確立（方向） |

**Pandolf 的已知誤差**（用來設定不確定帶）：

- Looney 2022（背包 0–66 % 體重）：Pandolf 偏低 −0.44 ± 0.74 W/kg（CCC 0.908）。
- Weyand 2021（野外）：無負重高估 +17 %，負重 30 % 體重時高估 +13 %。
- Ludlow & Weyand 2016：ACSM 與 Pandolf 對平地步行幾乎都**低估**，SEE 4.4–4.5 mL/kg/min，約為 HWS 式的 4 倍。
- 結論：方向依條件而變，幅度約 ±15 %。
- 本設計**只用 Pandolf 的比值**（同一代謝率下，不同坡度與背負的速度比），再用個人資料校正，大部分偏差會抵銷。剩下的部分放進不確定帶（§3.8）。

**走跑轉換坡度（Q1-2）**：

| 研究 | 發現 | 狀態 |
|---|---|---|
| Giovanelli et al. 2016 | 固定垂直速度 0.35 m/s（1260 m/h）。9.4°（≈ 16.6 %）時走路不比跑步便宜；15.8°（≈ 28 %）以上走路平均便宜 8.45 ± 1.05 %。兩種步態在 20.4–35°（≈ 37–70 %）都有寬的最低點（摘要） | 確立 |
| Ortiz, Giovanelli & Kram 2017 | 30° 時，0.3–0.7 m/s 走路較便宜，0.8 m/s 兩者相同，更快則跑步可能較便宜（摘要） | 確立 |
| Minetti, Ardigò & Saibene 1994 | 各坡度下，自發轉換速度都比「代謝成本相等速度」低 0.5–0.9 km/h（摘要） | 確立 |

- 結論：「~15–20 %」**沒有單一來源**。
- 能引用的區間是：
  - 16.6 % 左右時兩者相當；
  - 28 % 以上走路明確較便宜；
  - 而且和速度有關：人會比能量最佳點**更早**改用走的。
- 程式裡已有個人版：`GaitRE.walked()` 以每 2 % 坡度箱的多數步態判定（推估）。百岳模型沿用它的個人轉換點，不另設固定門檻。

### 2.2 Q2：你自己的資料

用現有快取做描述統計（唯讀，2026-09-30）：

- 快取檔：
  - `~/.wko5coach/series_racepower_v2_grade_hr_94790dbe.json`（跑步窗 `[g, v, p, z, hr, run]`）；
  - `…_hike_hr_267bcba1.json`（登山窗）。
- 條件：
  - 跑步：run share < 0.5、g ≥ 0.10。
  - **沒有套連續窗規則**：快取裡沒有窗序號 `k`（§5.1）。
  - AeT 142 bpm、LTHR 160 bpm 取自快取的設定簽章，是**固定值**，不是依日期的門檻。

| 來源 | 坡度帶 | n（活動數） | VAM 中位數 m/h | 海拔中位數 |
|---|---|---|---|---|
| 越野跑·走路窗 | 10–20 % | 393（—） | 545 | — |
| 越野跑·走路窗 | 20–30 % | 268 | 744 | — |
| 越野跑·走路窗 | ≥ 30 % | 300 | 990 | — |
| 越野跑·走路窗（合計） | ≥ 10 % | **962（58 次活動）** | 710 | 503 m（最高 2555 m） |
| 登山窗 HR ≥ 138、> 1.5 km/h | 10–20 % | 94 | 593 | 448 m |
| 登山窗 HR ≥ 138、> 1.5 km/h | 20–30 % | 70 | 781 | 1099 m |
| 登山窗 HR ≥ 138、> 1.5 km/h | ≥ 30 % | 35 | 1386 | 1149 m |

**越野跑走路窗，依心率帶**：

| 心率帶 | 10–20 % | 20–30 % | ≥ 30 % |
|---|---|---|---|
| < AeT | 547（171） | 744（90） | 1041（105） |
| AeT – 0.95·LTHR | 552（32） | 755（25） | 997（29） |
| ≥ 0.95·LTHR | 544（190） | 744（153） | 962（166） |

**發現**：

1. **VAM 幾乎不隨心率帶改變。** 100 m 窗在 20 % 坡約 2 分鐘，而心率的反應有 1–2 分鐘的落後：前一段跑步留下的高心率會落在走路窗裡，走路剛開始的窗心率又偏低。
   - 對策（推估）：
     - 只用 ≥ 3 個連續窗（≥ 300 m）的走路段；
     - 丟掉每段的第一個窗（心率還在過渡）；
     - 心率取窗後移 60 s 的平均；
     - 心率帶只在段的層級決定。
   - 這需要在快取裡加 `k`（§5.1）。處理後斜率仍接近 0 的話，心率帶選項就只顯示「AeT」與「能力上限」兩檔，不假裝有細分。
2. **≥ 30 % 的 VAM（990 / 1386 m/h）偏高。**
   - 1386 m/h 已接近頂尖 VK 選手的水準。它只有 35 窗，而且不是 `hikehr` 的正式過濾（沒有連續窗規則，也沒有 VAM 上限以外的清理）。
   - 很可能是氣壓計或 DEM 高度在短陡段的雜訊。
   - 對策：陡坡窗的坡度改用 300 m 連續段的平均坡度，不用單窗；超過個人 p99 的剔除（推估）。
3. **越野跑走路窗的海拔低**（中位數 503 m，最高 2555 m），所以海拔項只能靠登山窗。
   - 但登山窗的高海拔覆蓋可能也很少：
     - 全部坡度 ≥ 10 % 的登山窗（不分心率，441 窗、20 個檔）海拔中位數 2322 m；
     - 心率 ≥ 138 的子集，各坡度帶的海拔中位數只有 448 / 1099 / 1149 m。
   - 也就是說，心率多半在低海拔的登山才過 AeT，高海拔的百岳很少。
     - 這本身就支持 §2.4 的干擾 4：跟團在高處把心率壓低。
     - 它也表示 −9.9 %（n 153）背後的高海拔槓桿可能很小。
   - `hikehr` 正式的 157 窗（依日期門檻、連續規則）的海拔分布還沒看。§2.4 的診斷第 0 步就是列出它：z ≥ 2500 m 的窗數，以及每趟的窗數。
4. **正式的 `hikehr` 過濾結果**（spec）：157 窗，個人海拔係數 −9.9 %/1000 m（n 153）；第 2 天在同 VAM 下 −1.2 bpm（2 趟）。

**兩種資料怎麼合併**（Q2-3，推估）：

- 兩種窗的狀態不同：
  - 越野跑是輕背負（背心 1–3 kg）、低海拔、單日、跑走混合；
  - 百岳窗是 8–15 kg 背負、可達 3900 m、多日（實際海拔分布待列，見上）。
- **每窗先正規化到同一個參考狀態**（L_ref = 2 kg，z = 300 m，當天第 0 h），再合併擬合。現行 `HikeSpeed` 用單一的 `ref_alt_m` 中位數，這在混合兩種來源時不成立，要換掉。
- 參數怎麼估：
  - 海拔斜率 α 主要由百岳窗估（越野窗海拔範圍窄）；
  - 坡度形狀的個人修正 δ(g) 主要由越野窗估（n 大、無背負干擾）；
  - 兩者在同一個迴歸裡估（§3.3）。
- 背負（Q2-4）：
  - 每趟百岳需要背負 kg。沒填的話用預設值（多日 12 kg、單日 6 kg），並標「預設背負」。
  - 越野跑用 2 kg（可設定）。

### 2.3 Q3：時長與多日

| 研究 | 發現 | 用途 | 狀態 |
|---|---|---|---|
| Savoldelli et al. 2017（Tor des Géants 個案，n = 1，330 km / +24 000 m / 6 天） | 六段上坡平均強度 57.3 ± 6.0 % VO2peak、68.0 ± 8.7 % HRpeak；C、C_vert、效率**沒有**隨天數一致上升，沒有代謝疲勞（摘要） | 多日山行的強度落在「AeT 附近」；多日衰減可能很小 | 確立（n = 1，只能當方向） |
| Nuuttila et al. 2025 | 休閒跑者以 90 % LT1 速度跑 90 分鐘後：LT1 速度降 5.3–5.8 %，心率升 5.5–5.9 %（摘要） | **同一心率下的速度會隨時間下降**；8 h 的量沒有研究 | 確立（90 min），外插到 6–10 h |
| Maunder et al. 2021 | 「durability」：生理指標在長時間運動中會漂移，靜息狀態測的門檻會高估長時間能力（摘要） | 概念依據：日內衰減要個人化 | 確立（概念） |
| Coyle & González-Alonso 2001 | 心血管漂移：10–20 分鐘後每搏量下降、心率上升（摘要） | 同上 | 確立（機制） |
| Davies & Thompson 1979 | 距離愈長，%VO2max 的解釋力愈大（摘要） | 長時間的關鍵是「能持續的百分比」 | 確立（方向） |
| Bink 1962（*Ergonomics* 5:25–28） | 工作時間與可持續的體能百分比（只讀到題名，經 Crossref） | 常被引用的「8 h ≈ 1/3 VO2max」**公式沒有核對** | **待驗證**，不使用 |
| Levine et al. 1982（*Ergonomics* 25:393） | 長時間自訂配速重體力工作，受訓與未受訓比較（只讀到題名） | — | 未讀內容，不使用 |
| Wu & Wang 2001 | 最大可接受工作時間是 6.5–18.8 分鐘的高強度工作（摘要） | 範圍不適用 | 不採用 |

**結論**：

- **一天百岳的可持續強度 ≈ AeT**：
  - 依據是 Savoldelli（68 % HRpeak，多日不衰減）加上 Seiler 的三區定義（app 已用）。
  - 證據是「方向一致」，沒有直接研究百岳，所以標**經驗法則／外插**。
- **日內衰減**：固定 AeT 心率時速度會下降（Nuuttila 的 −5.5 % / 90 min 是唯一定量值）。
  - 本設計用**個人資料**估日內衰減：百岳窗與長越野跑，同坡度、同心率下，VAM 對「當天第幾小時」的斜率（§3.5）。
  - 先驗 0，不確定度用 Nuuttila 的量級；**推估**。
- **Riegel 型的步行時間衰減**：**未找到來源**。不用 Riegel 推百岳。
- **ITRA / UTMB 的時長效應**：ITRA performance index、UTMB index 的換算式沒有公開。**未找到來源**，不用。
- **多日（第 n 天）**：
  - 除了 Savoldelli 的個案，**未找到定量研究**。
  - 維持 F17：只用個人資料，少於 3 趟就用 1.0 並警告。
  - 個人資料可以改用 `hikehr.fatigue` 的「同 VAM 心率差」換算成速度差（§3.5），不用 EP/h 比，因為 EP/h 是團體配速。

### 2.4 Q4：海拔

| 研究 | 量 | 數值 | 範圍 | 狀態 |
|---|---|---|---|---|
| Wehrlin & Hallén 2006 | VO2max | **−6.3 %/1000 m**（個體 4.6–7.5 %），300 m 起線性；107 % VO2max 定速的 TTE −14.5 %/1000 m；力竭時 SpO₂ 從 89 % 降到 76.5 %（摘要）。全文：次最大 VO2 不變，心率上升（`racepower-v2.md` §2.5） | 300–2800 m，急性艙內，n = 8 | 已驗證（`env.altitude_factor_linear`） |
| Bassett et al. 1999 | 有氧功率 % | 已適應 `−1.12x² − 1.90x + 99.9`；未適應 `0.178x³ − 1.43x² − 4.07x + 100` | 0–4000 m；3000 m 以上推估 | 已驗證（二手核對，`env.bassett_pct`） |
| Fulco, Rock & Cymerman 1998（回顧） | VO2max、次最大表現 | 580 m 起 VO2max 下降，長期停留也不回升；次最大表現的損失與海拔、時長成正比，**持續停留後會改善**（VO2max 不變）；個體差異最大的來源是暴露前的體能（摘要） | 文獻回顧 | 確立（質性） |
| Coffman et al. 2020 | 背 30 % 體重、5 km 自訂配速 | 250 → 3000 m 時間 +11 %（43 → 48 分）；**心率各條件相同**（170 bpm）（摘要）。換算：速度 −10.4 %，約 **−3.8 %/1000 m**（我們的換算） | 急性、n = 9、約 45 分鐘 | 確立（數值是我們的換算） |
| Wang et al. 2010；Shen et al. 2024 | 玉山高山症 | 排雲山莊（3402 m）問卷 AMS 盛行率 36 %；排雲診所就診者 57 % 診斷 AMS（摘要） | 玉山 | 確立（不是速度模型） |

**個人 −9.9 %/1000 m 與文獻比較**：

- 兩者**不是同一個量**：
  - Wehrlin 量的是最大攝氧；
  - 你的斜率量的是「同心率下的 VAM」。
- 海拔讓同一速度的心率上升（Wehrlin 全文），所以固定心率時，速度的下降應該**大於** VO2max 的下降。
- 但 Coffman（負重、固定心率的自訂配速）只有 −3.8 %/1000 m。所以「固定心率」這個理由無法單獨解釋 −9.9 %。
- **可能的干擾**（推估，待診斷）：
  1. 高海拔的窗多在第 2 天，混入了多日疲勞。
  2. 高海拔多在當天較晚，混入了日內衰減。
  3. 高處較冷、地形較碎（碎石、箭竹）。
  4. 殘餘的團體效應：HR ≥ AeT 的過濾只能排除「心率低」的團體拖慢，不能排除「心率高但被隊伍卡住」。
  5. `hikehr.altitude_factor` 的固定效果是「坡度帶 × 5 bpm 心率格」，**沒有**控制趟次：趟次之間的背負與隊伍差異會混進海拔斜率。
- **診斷**（實作前先跑，結果寫回 spec）：
  1. 加入趟次固定效果（只用趟內的海拔變化）。
  2. 只用第 1 天的窗。
  3. 只用當天前 2 h 的窗。
  4. 用「趟」做群集 bootstrap 估 SE。153 窗彼此不獨立，有效樣本數接近「趟數」。
  - 如果斜率降到 −4 到 −7 %，就證實是干擾，用修正後的值。

**收縮方式** **[推估]**：常態-常態精度加權（Efron & Morris 1975 的 empirical Bayes 概念）：

```
b_post = (b_personal / SE² + b_prior / τ²) / (1/SE² + 1/τ²)
b_prior = −6.3 %/1000 m（Wehrlin；未適應）
τ       = 3.0 個百分點
```

- τ = 3.0 的理由：
  - Wehrlin 的個體範圍 4.6–7.5 約等於 SD 1 點；
  - 再加上「VO2max 斜率 ≠ 固定心率速度斜率」的不確定；
  - Coffman −3.8 和個人值之間的距離。
  - 這個值是推估。
- SE 用群集 bootstrap。算例（b_personal = −9.9）：

| SE | 個人權重 | b_post |
|---|---|---|
| 1.0 | 0.90 | −9.54 |
| 2.0 | 0.69 | −8.79 |
| 3.0 | 0.50 | −8.10 |
| 5.0 | 0.26 | −7.25 |

**適應狀態**：

- 資料窗大多是「上山第 1–2 天」，本來就是未適應狀態。所以個人斜率對應「未適應」。
- 選「已適應」時，用 Bassett 已適應 ÷ 未適應的比，對個人斜率做相對修正（推估）。
  - 3000 m：84.1 / 79.7；3952 m：74.9 / 72.6。
- Fulco 的依據：次最大表現會隨停留改善，VO2max 不會。

**3000–3952 m（玉山）**：

- 文獻值：
  - Wehrlin 的線性外插到 3952 m 是 77.0 %；
  - Bassett 未適應 72.6 %、已適應 74.9 %。
- 個人資料：百岳窗有覆蓋這個高度，所以個人斜率在這個範圍內**不是外插**。但文獻先驗在 2800 m 以上是外插，頁面照 `planner.altitude_note` 標示。
- 高山症：
  - 玉山 AMS 盛行率 28–36 %（Wang 2010，排雲問卷），不是速度模型；
  - 頁面只放**非數值**提示：「高山症會讓速度與行程失準，出現症狀以下撤為先」。

### 2.5 Q5：熱

- 海拔愈高愈冷。熱項在百岳主要影響低海拔的登山口路段，例如林道或 1000–2000 m 的闊葉林段。
- 做法：
  - 每段溫度用標準大氣遞減率 0.0065 K/m，從登山口或測站溫度推算。`env.pressure_torr` 已用同一個遞減率（ICAO 標準大氣）。
  - 熱懲罰沿用 Hadley（`env.heat_penalty_pct`，經驗法則）。
  - 乘上熱適應狀態給的比例 s ∈ [0, 1]。s 的定義與依據**由另一份文件決定**。
- 接口定義在 §8.1。

### 2.6 Q6：跟團與自己走、過去的百岳紀錄

跟團的整天紀錄**還能校正**的東西（推估）：

| 用途 | 怎麼用 | 為什麼可以 |
|---|---|---|
| 時鐘時間比（移動 ÷ 總時間） | 現有 `hike.moving_ratio`；分「跟團」與「自己走」兩組 | 休息型態本來就屬於行程類型；計畫跟團就該用跟團的比 |
| 跟團時間（另一個預估） | 過去跟團日的 EP/h 分位數 → 「跟團大概要多久」 | 跟團時間是真實的使用情境，只是不代表能力 |
| 能力時間的**上界檢查** | 能力模型對每一天的預測移動時間，應該 ≤ 實際跟團移動時間 | 團體只會讓你變慢；若能力預測比跟團還慢，模型偏慢 |
| 停留型態 | 停留次數、長度的分布 → 時鐘 ETA 的休息分配 | 同上 |

**偵測被團體限制的路段**（推估；每個 100 m 窗或 300 m 段）：

- **心率偏低**：坡度 ≥ 10 %、移動中，心率 < AeT − 5 bpm，而且個人模型在這個坡度與心率下預測的速度 > 實際速度 × 1.15。意思是「你在走、坡很陡、心率卻很低」＝被卡住。
- **速度平台**：連續 ≥ 5 窗，速度變異係數 < 5 %，而坡度變化 > 10 個百分點。個人步行的速度會隨坡度改變，團體步調常是固定的。
- **頻繁短停**：每公里 ≥ 3 次 30–120 s 的停止（等人）。
- **自己走的段**：心率 ≥ AeT，而且速度隨坡度的反應符合個人模型（殘差 < 15 %）。

**自動判定「自己走的日子」**（推估；判定結果只當**建議**，由使用者確認後寫進 `racepower_solo_hikes.json`）：

- 條件：
  - 當天坡度 ≥ 10 % 的移動時間中，自己走的段 ≥ 60 %；
  - 被限制的段 ≤ 15 %；
  - 停留型態不像團體（短停 < 1 次/km）。
- 頁面在登山紀錄表每一列顯示「看起來是自己走（62 %）」，旁邊一個確認鈕。
- 閾值是推估。要用使用者手動標記的日子驗證：每類 ≥ 5 天，符合率 ≥ 80 % 才自動套用。在那之前只是建議。

### 2.7 Q7：驗證方式（細節見 §7）

- **切法**：
  - leave-one-**activity**-out，用現有 `grade_models(exclude=)` 的機制。
  - **不用** leave-one-window-out：相鄰的 100 m 窗高度相關，逐窗留一會太樂觀。
- **兩種窗分開看，高海拔窗單獨看**。
- **整天百岳只當合理範圍**，不當準確度指標。

---

## 3. 模型

### 3.1 生理先驗：你在 AeT 的代謝功率 → 步行速度

```
Ė_AeT  = W · (1.5 + Cr₀ · v_run,AeT)                                   (B1)
v₀(g, L, η) = Pandolf⁻¹(M = Ė_AeT; W, L, G = 100·g, η)      for g ≥ 0   (B2)
```

| 部分 | 內容 | 來源 | 狀態 |
|---|---|---|---|
| v_run,AeT | 平地（\|g\| ≤ 2 %）跑步窗，心率在 AeT ± 3 bpm 的速度中位數；近 90 天、戶外 | 個人資料；AeT 取 `thresholds_as_of` | 定義 |
| Cr₀ | 3.6 J/kg/m（Minetti 多項式常數，與 `minetti.FLAT_RUN` 一致；實測 3.40） | Minetti 2002 | 已驗證 |
| 1.5·W | Pandolf 的站立項，讓 Ė 與 Pandolf 的毛代謝率相容 | Pandolf 1977 | 已驗證（二手核對） |
| Pandolf⁻¹ | 二分法反解（`hike.pandolf_speed` 的做法） | 同上 | 已驗證（G ≥ 0） |
| 整條 B1–B2 的串接 | 跑步的淨成本 + Pandolf 的站立項，當成 AeT 的毛代謝率 | **推估** | 待驗證（§7） |

- 個人的跑步經濟性不同於 Minetti 平均值。但 Breiner et al. 2019 顯示平地、上坡、下坡的經濟性彼此高度相關，所以個人偏差大多會被 §3.3 的個人修正 δ 吸收。

**算例**（W 70 kg、假設 v_run,AeT = 2.7 m/s；Ė_AeT ≈ 785 W = 11.22 W/kg；獨立重算，下表為約略值）：

| G | L 2 kg：km/h（VAM） | L 12 kg：km/h（VAM） | 個人走路窗中位數（輕背負） | Tobler |
|---|---|---|---|---|
| 10 % | 5.76（576） | 5.21（521） | VAM 545 | 3.55 km/h（355） |
| 20 % | 3.93（787） | 3.49（698） | VAM 744 | 2.50 km/h（500） |
| 30 % | 2.90（871） | 2.55（766） | VAM 990（≥ 30 %，可能含雜訊） | 1.76 km/h（528） |

- 在這個假設的 v_run,AeT 下：
  - 10–20 % 坡，先驗與個人資料相差 6 % 以內；
  - ≥ 30 % 坡，先驗低 12 %（個人值可能含高度雜訊，§2.2）；
  - Tobler 偏慢 33–47 %。
- 實作時要用你實際的 v_run,AeT 重算這張表，寫進測試。
- Minetti 的步行成本比法（v = Cr₀·v_run·W / ((W+L)·Cw(g))）在 g ≥ 15 % 給出 VAM 804–912（L 2 kg），當交叉檢查。
  - g < 15 % 不能用它：Cw 是最佳速度下的最低成本，而淺坡時步行成本隨速度上升，會高估速度。
- **平地 g < 5 %**：Pandolf 在 0 % 給 9.03 km/h。這是跑步的速度，走路做不到。平地速度改由 §3.4 決定。

### 3.2 背負

```
p(g, L) = Pandolf⁻¹(M; L) / Pandolf⁻¹(M; L₀)     g ≥ 0   （上坡、平地）   (B3)
p(g, L) = (W + L₀)/(W + L)                         g < 0   （下坡）         (B3')
```

| G | Pandolf 比（L₀ 2 → L 12 kg，W 68） | 線性 (W+L₀)/(W+L) |
|---|---|---|
| 5 % | 0.917 | 0.875 |
| 20 % | 0.888 | 0.875 |
| 30 % | 0.880 | 0.875 |

- 陡坡時兩者一致。原因是垂直功在總重下是線性的。這同時符合：
  - Ludlow & Weyand 2017 的「代謝量與總重成正比」（一手摘要，負重 +18 / +31 % 體重，−6 到 +9°）；
  - Minetti 2002 的「+15 % 以上效率接近肌肉向心效率」。
- **狀態**：
  - B3：已驗證（二手核對），限 G ≥ 0。陡於 +9°（≈ 16 %）是 Ludlow & Weyand 量測範圍外，但 Pandolf 的比與線性一致，標「外插（兩來源一致）」。
  - B3'：推估。Santee 下坡修正仍是單一來源，未驗證。
- **每天的背負**：
  - 多日行程每天可以填不同的 kg（糧食會吃掉）。預設「第 1 天 L，之後每天 −0.7 kg」（推估，可改）。
- **誤差**：
  - Looney 2022：重負重時 Pandolf 偏低 −0.44 ± 0.74 W/kg；
  - Weyand 2021：野外高估 +13–17 %。
  - 背負項的不確定度設 σ_pack = 0.08（對數尺度 1σ ≈ ±8 %；90 % 區間約 ±13 %，對應 ±15 % 的量級），推估。
  - 只在「L 超出個人資料的背負範圍」時套用。百岳窗本身就有 8–15 kg，所以落在資料內的部分用資料的殘差。

### 3.3 個人化：兩種窗、同一個迴歸

每個窗 w（坡度 g_w ≥ 0.10 的走路段，或 HR ≥ AeT 的百岳段）：

```
ln v_w = ln v₀(g_w, L_w, η=1) + δ(g_w) + β·(HR_w − AeT_d) + α·(z_w − 300)/1000
         + γ·h_w + u_trip + ε_w                                              (B4)
```

| 項 | 意義 | 先驗與收縮 | 狀態 |
|---|---|---|---|
| ln v₀ | §3.1 的生理先驗（已含該窗背負 L_w） | — | 見 §3.1 |
| δ(g) | 每個 2 % 坡度箱的個人修正 | 往 0 收縮，權重 n/(n + 30)（沿用 `SHRINK_N`） | 推估 |
| β | 心率帶斜率（每 bpm 的對數速度） | 先驗 0，寬；資料不足或 \|β\| 不顯著時固定 0（§2.2 發現 1） | 推估 |
| α | 海拔斜率（每 1000 m） | 用 §2.4 的精度加權收縮 | 推估，比較 Wehrlin |
| γ | 當天第幾小時的衰減 | 先驗 0，τ_γ ≈ 0.03/h（Nuuttila 的量級，推估） | 推估 |
| u_trip | 趟次（活動）隨機效果 | 平均 0 | 推估 |
| L_w | 越野跑 2 kg；百岳窗用該趟填的背負或預設值 | — | 定義 |
| HR_w | 段層級的心率，後移 60 s（§2.2） | — | 推估 |

- **α 與 γ 幾乎共線**：只看上坡窗時，同一趟裡海拔與當天第幾小時一起上升，趟次隨機效果分不開它們。識別方式（推估）：
  1. 先用越野走路窗擬合 γ。這些窗海拔低、時間長，γ 不受海拔干擾。
  2. 固定 γ，再用百岳窗擬合 α。α 的訊號來自第 2 天從山屋出發的早上（z 高、h ≈ 0），以及跨趟的對比。
  - 不這樣做的話，只有 §2.4 診斷版本 3（前 2 h）是乾淨的估計。
- **擬合**：加權最小平方，趟次當隨機效果，或先減掉趟內平均再擬合。每個活動的窗數上限 100，避免單一長活動主導。
- **預測**時取 HR = 所選心率帶的中點。預設 AeT，也就是 β 項 = 0。
- 可選心率帶：
  - 「AeT（多日預設）」；
  - 「AeT – 0.95·LTHR」；
  - 「≥ 0.95·LTHR（單日衝刺，≤ 3 h）」。

### 3.4 平地與下坡

- **平地（|g| < 5 %）**：
  ```
  v_flat = min( 越野跑走路或慢跑窗（run share < 0.5，或速度 ≤ 個人 p50 慢跑）在 |g| < 5 % 的
                速度中位數 × 技術度 tech × p(g, L),
                Pandolf⁻¹ 在 g 的值 )
  ```
  - 狀態：個人資料是定義；取 min 是推估。技術度係數是 `GaitRE.tech_factor`（推估，已實作）。
  - 沒有資料時用 Tobler × 個人比。個人比 = 個人走路窗 ÷ Tobler 在 5–10 % 的中位數比（推估）。
- **下坡**（g < 0）：
  ```
  v_down = min( 個人下坡走路窗的速度（同上來源，往 Tobler×c 收縮）× p'(L) × tech,
                c_cap · Tobler(g) )
  c_cap = 個人下坡窗「速度 ÷ Tobler(g)」的 p75（g ≤ −10 %）；沒有資料時為 1.0
  ```
  - 為什麼要上限：Minetti 2002 指出 −0.15 以下是離心收縮的效率，代謝不是限制，限制的是煞車、腳下與背負，所以代謝模型無法限制下坡速度。
  - 回測顯示現行模型在 ≤ −15 % 太快 +20 %（spec 的回測表）。
  - Tobler 在下坡的形狀是經驗法則（1993，經 Wikipedia，已驗證二手，V-F13）；c_cap 是推估。
  - 背負在下坡用 B3'。

### 3.5 時長與多日

```
f_time(h)  = exp(γ · h)                    h = 當天已走的移動小時       (B5)
f_day(n)   = exp(β · ΔHR_n)（n ≥ 2）                                      (B6)
ΔHR_n      = hikehr.fatigue 的第 n 天、同 VAM 心率差（bpm），且 ≥ 3 趟
```

- B5：個人 γ，先驗 0（§3.3）。
  - 資料不足時 γ = 0，不確定帶加上 σ_time = 0.05 × h/1.5（Nuuttila 的量級，外插，推估）。上限 0.10。
- B6：F17 的新版。
  - 用心率差經 β 換算成速度（推估），不用 EP/h 比。原因：EP/h 是團體配速；心率差是個人生理。
  - β 不可靠（§2.2），或趟數 < 3 時，f_day = 1.0，並警告「多日疲勞沒有研究與足夠個人資料，每天都用 1.0」。
  - 不確定帶加 σ_day = 0.03 × (n − 1)（推估）。

### 3.6 海拔

```
A(z) = exp( b_post/100 · (z − z_ref)/1000 ),   b_post 見 §2.4            (B7)
```

- z_ref = 300 m：個人窗已先正規化到這裡（§2.2）。
- 選「已適應」時，乘上 Bassett 已適應 ÷ 未適應在 z 的比（推估）。

### 3.7 單段時間與整體

```
v_i = [ v₀(g_i, L_day, η_i) · e^{δ(g_i)} ]（g ≥ 5 %）或 v_flat / v_down（其餘）
      × A(z_i) × f_time(h_i) × f_day(n_i) × H_i
t_i = d_i / v_i；    移動時間 T = Σ t_i；    時鐘時間 = T ÷ 移動比 + 停留         (B8)
```

- η_i 是地形係數。在 Pandolf 裡 η 乘在步行項上，不另外除（這和 v1 的 `hike_rows` 不同）。
  - 已驗證（二手核對）的 η：1.0 / 1.2 / 1.5 / 2.1。
  - 碎石 1.3、箭竹 1.35 是經驗法則。
- H_i 是熱項接口（§8.1）。沒有熱適應資料時 H_i = 1 − Hadley%/100（v1 行為）。
- **能力時間** = B8，心率帶 AeT。
- **跟團時間** = 同一條路線，EP ÷ 過去跟團日 EP/h 的中位數（p25–p75 當範圍）。
  - 用 `used.eph` 現有的「跟團日」資料；要 ≥ 3 天。
  - 標「跟團預估（不是能力）」。

### 3.8 不確定帶

對數尺度相加（彼此獨立的假設是推估）：

```
σ² = σ²_LOO（整天層級，§7.1 的殘差）+ σ²_pack + σ²_alt + σ²_time + σ²_day
帶 = T · exp(±1.28 σ)（p10–p90）
```

| 分量 | 來源 | 預設 |
|---|---|---|
| σ_LOO | 活動層級 LOO 的對數誤差 SD | 回測前取 0.10 |
| σ_pack | Looney 2022、Weyand 2021 | 0.08（L 超出資料範圍時） |
| σ_alt | α 的 SE × 路線平均海拔差 | 由 bootstrap 算 |
| σ_time | Nuuttila 的量級 | 0.05 × h/1.5，上限 0.10 |
| σ_day | 無來源 | 0.03 × (n − 1) |

- 越野預測只有 σ_LOO。百岳必然更寬，符合基線第 6 點。

### 3.9 公式總表

| 代號 | 公式 | 來源 | 狀態 |
|---|---|---|---|
| B1 | Ė_AeT = W(1.5 + Cr₀·v_run,AeT) | Minetti 2002 + Pandolf 站立項 | 推估（組件已驗證） |
| B2 | Pandolf⁻¹ | Pandolf 1977 | 已驗證（二手核對，G ≥ 0） |
| B3 | 背負比（上坡、平地） | Pandolf；Ludlow & Weyand 2017 比例 | 已驗證（二手核對）；陡坡外插但兩來源一致 |
| B3' | 背負線性（下坡） | 線性近似 | 推估 |
| B4 | 個人迴歸 | — | 推估，待驗證（§7） |
| B5 | 日內衰減 | Nuuttila 2025 量級 | 推估 |
| B6 | 多日心率差 → 速度 | 個人資料 | 未找到來源（個人資料） |
| B7 | 海拔，精度加權收縮 | Wehrlin 2006；Efron & Morris 1975 | 推估 |
| B8 | 段時間 | — | 定義 |
| 下坡上限 | c_cap · Tobler(g) | Tobler 1993 形狀 | 經驗法則 + 推估 |
| 走跑轉換 | 個人多數步態 | Giovanelli 2016、Ortiz 2017、Minetti 1994（區間） | 推估（已實作） |
| 跟團時間 | EP ÷ 跟團 EP/h | 健行筆記 / ITRA EP | 經驗法則 |
| 時鐘 | ÷ 移動比 | 個人資料 | 定義 |

---

## 4. 資料管線

### 4.1 哪些活動、哪些窗、餵給哪個參數

| 資料 | 過濾 | 餵給 |
|---|---|---|
| 平地跑步窗（近 90 天，戶外） | \|g\| ≤ 2 %、run share ≥ 0.5、心率 AeT ± 3（當天門檻） | v_run,AeT（B1） |
| 越野跑走路窗 | run share < 0.5、g ≥ 0.10、≥ 3 個連續窗、去掉第一窗、心率後移 60 s | δ(g)、β、γ（B4） |
| 越野跑平地與下坡走路／慢跑窗 | run share < 0.5 或慢跑、g < 0.05 | v_flat、v_down、c_cap（§3.4） |
| 百岳 HR 過濾窗（`hikehr`） | 現行過濾 + 趟次、當天第幾小時、背負 | α（主要）、δ、γ、β（B4）；ΔHR_n（B6） |
| 百岳下坡窗（全部移動窗） | g ≤ −10 %、移動 | c_cap（跟團拖慢只會讓上限偏保守，方向安全） |
| 跟團百岳整天 | 全部 | 跟團時間、跟團移動比、停留型態、上界檢查（§2.6） |
| 自己走的百岳整天（確認後） | 標記 | 自己走的移動比、整天回測案例 |

### 4.2 流程

```
activities ──► grade_samples（加 k、t、run、hr）──► walk_windows_trail ──┐
          └──► hike_samples（加 t、pack）──► hikehr.filter_windows ──────┤
                                                                         ▼
 平地跑步 AeT ──► v_run,AeT ──► Ė_AeT ──► v₀(g, L)（先驗）──► fit_walk_capacity（B4）
                                                                         │
                  hikehr.altitude_factor（趟次固定效果 + bootstrap SE）──► shrink（B7）
                                                                         ▼
                                                          WalkCapacity（取代 HikeSpeed）
                                                                         ▼
 course segments + 每天背負 + 地形 η + 熱接口 ──► planner.plan_hike ──► 能力時間 / 跟團時間 / 帶
```

---

## 5. 怎麼在 `planner.plan_hike` 取代 Tobler

### 5.1 要先解決的資料問題

1. **跑步窗快取沒有窗序號 `k` 和時間**：`_grade_windows` 的列是 `[g, v, p, z, hr, run]`，所以連續窗規則與「第幾小時」都做不出來。
   - 要加 `k` 與累計移動秒數 `t`，並更新 `GRADE_KEY` 的版本字串。這會讓快取重算一次。
2. **登山窗快取沒有累計時間**：`HIKE_KEY` 的列有 `k` 和 day，沒有 t。同樣要加 t 並換 key。
3. **背負**：每趟百岳要一個 pack kg 欄位，存在 `racepower_hike_meta.json`，與 solo 清單並列。
4. **solo 百岳的 HR 窗**：現在 `grade_models` 把 solo 趟的 HR 窗排除在 steep 之外（因為 solo 的全部窗已經另外進來）。
   - 新模型只用 HR 過濾窗與越野窗，solo 整天改當回測案例與移動比來源，所以這個排除要拿掉。

### 5.2 改法

- `hike_speed.v(g)` 換成 `capacity.v(g, L_day, η, z, h, n, band)`（§3.7）。
- `pack`（v1 線性）只留給下坡（B3'）。
- `alt`（`ENV.segment_factors`）換成 `capacity.A(z, accl)`（B7）。
- `fat = HK.day_fatigue(hdays)`（EP/h 比，團體配速）換成 B6。
- **`used["eph"]`**：
  - 沒有 solo 時，現在填的是 `hike.tobler_eph`；
  - 改成 EP ÷ 能力模型在同一路線的移動時間，也就是「你的能力 EP/h」。
  - `tobler_eph` 只留在交叉檢查。
- **驗證閘門**：
  - 現在 `v2_primary = validated["hike"]`，而它要 solo 整天案例 ≥ MIN_N。沒有 solo 日就永遠不會通過。
  - 改成新旗標 `validated["hike_capacity"]`：由 §7.1 的窗層級 LOO 決定（沿用 `THRESHOLDS["hike"]` = 0.10）。
  - 通過後，能力時間拿掉「推估」；沒通過就標推估。
- **標籤文字改變**（避免使用者以為還是通用模型）：
  - 從「步行速度用 Tobler 先驗……（推估）」
  - 改為「以你的越野走路窗與百岳心率窗為主（n 窗、m 次活動），待回測（推估）」。
- **回傳**新增：
  - `summary.capacity_time_s`、`summary.group_time_s`（p25 / p50 / p75）；
  - `summary.band`（p10 / p90）；
  - `summary.hr_band`；
  - `capacity`：n、α（個人、先驗、收縮後、SE）、β、γ、診斷結果；
  - `segments[].sigma`。

---

## 6. UI（`racepower.html`，百岳）

### 6.1 輸入

- **行程類型**：`跟團 / 自己走`。
  - 決定時鐘時間用哪一組移動比與停留型態。
  - 也決定主數字是「跟團時間」還是「能力時間」；另一個顯示在旁邊。
- **強度**：`AeT（多日建議）/ AeT–0.95 LTHR / ≥ 0.95 LTHR（≤ 3 h）`。
  - 顯示「比 AeT 快 x %」。
  - β 不可靠時，只提供 AeT 與「能力上限」，並說明原因。
- **每天背負 kg**：預設第 1 天 12 kg，之後每天 −0.7 kg。
- **海拔**：
  - 適應狀態：`未適應（預設）/ 已適應（推估）`，拿掉「部分」。部分適應沒有定量研究，而個人斜率本身已是未適應。
  - 顯示個人斜率 −9.9 %、收縮後的值、Wehrlin −6.3 %、Coffman −3.8 %，以及 n、SE、趟數。
- **熱**：
  - 登山口溫度與濕度（預設帶入天氣資料）；
  - 熱適應狀態（唯讀，來自熱適應文件的狀態物件）；
  - 每段溫度由遞減率推算，顯示在分段表。
- **地形 η**：沿用。

### 6.2 結果

- **兩個主數字並排**：
  - 能力時間（你的能力，AeT），附 p10–p90 帶；
  - 跟團時間（過去跟團日），附 p25–p75。
  - 兩者都標依據的 n。
- **不確定帶拆解**：一條橫條，顯示 σ 各分量的占比（背負、海拔、日內、多日、模型）。
- **資料來源卡**：越野走路窗 n / 活動數，百岳 HR 窗 n / 趟數，先驗 v_run,AeT。
- **剖面圖**：速度階梯線加上帶（淡色）。
- 分段表新增欄位：
  - 溫度；
  - A；
  - f_time；
  - σ；
  - 「資料內 / 外插」（背負超出、坡度箱 n < 30、海拔超出資料）。
- **警告**：
  1. 「沒有背 10–15 kg、每天 6–10 h、連走多天的跑步資料；背負與多日效應靠公式（誤差約 ±15 %），所以帶比越野寬」；
  2. 高山症提示（玉山、雪山這類 ≥ 3500 m 的路線）；
  3. 多日疲勞用 1.0（F17 資料不足時）。
- **登山紀錄表**：每列加「看起來是自己走（x %）」建議與確認鈕（§2.6）。

---

## 7. 驗證與測試計畫

### 7.1 回測（`backtest.py`）

| 測項 | 切法 | 指標 | 通過門檻 |
|---|---|---|---|
| A 越野走路窗 | leave-one-activity-out；段層級（≥ 300 m） | 段時間對數誤差：中位數 \|err\|、偏差、p10–p90 | 中位數 \|err\| ≤ 10 %（= `THRESHOLDS["hike"]`）、\|偏差\| ≤ 5 %、n ≥ 30 段且 ≥ 10 次活動 |
| B 百岳 HR 窗 | leave-one-trip-out | 同上 | 同上，n ≥ 30 段且 ≥ 5 趟 |
| B-高 百岳 HR 窗 z ≥ 2500 m | leave-one-trip-out；模型用其餘趟擬合 α | 同上 | \|偏差\| ≤ 5 %；另外比較「個人 α」、「收縮 α」、「Wehrlin」三者的誤差，收縮版要不比最差者差 |
| C 先驗對照 | 同 A、B | 生理先驗（無個人修正）vs Tobler vs 完整模型 | 完整模型 ≤ 先驗 ≤ Tobler，否則檢討 B1 |
| D 整天百岳（跟團） | 每天 | 能力移動時間 ÷ 實際移動時間 | **合理範圍**：≥ 80 % 的天數在 0.60–1.00 之間（能力不應比跟團慢，也不應快到不合理）。不算準確度 |
| E 整天百岳（自己走，確認後） | leave-one-trip-out | 整天時間誤差 | 中位數 \|err\| ≤ 10 %；n ≥ 5 天才判定 |
| F 自動判定自己走 | 使用者手動標記 | 符合率 | ≥ 80 %，每類 ≥ 5 天 |
| G 海拔診斷 | §2.4 的 4 個版本 | 斜率與 SE | 記錄，不設門檻 |

**通過後的狀態**：

- A 與 B 都通過 → `validated["hike_capacity"]`，能力時間拿掉推估。
- 即使通過，下列部分**仍標推估**：
  - 背負超出資料範圍的段；
  - 多日 f_day（除非 ≥ 3 趟）；
  - 已適應修正；
  - 熱項（依熱適應文件的狀態）；
  - 跟團時間（本來就不是能力）；
  - 2800 m 以上的文獻先驗部分。

### 7.2 單元測試（`backend/tests/test_racepower_v2.py`，新增）

- B1 / B2 算例：
  - W 68、v_run 2.7 → Ė 763.0 W；
  - L 2 kg、G 20 % → 1.093 m/s；
  - L 12 kg、G 30 % → 0.709 m/s。
- B3：Pandolf 比在 G 30 % = 0.880，與線性 0.875 相差 < 1 %。G < 0 時走線性。
- B7：精度加權。SE 2、τ 3、個人 −9.9、先驗 −6.3 → −8.79。SE → ∞ 時等於先驗；SE → 0 時等於個人。
- 趟次固定效果：合成資料中，趟間有背負差但趟內真值 −6 %，要能回收 −6 ± 0.5 %（沒有趟次效果的版本會失敗）。
- 心率後移與第一窗剔除：合成的跑轉走序列，窗心率要回到穩態值。
- 下坡上限：v_down ≤ c_cap·Tobler(g)。
- 平地：Pandolf 0 % 的 9 km/h 不能出現（取 min）。
- `used.eph` 在 0 solo 時不再等於 `tobler_eph`。
- 不確定帶：百岳的 σ > 同路線越野的 σ_LOO。
- 自動判定：合成的「心率低、速度平台、頻繁短停」日判為跟團；「心率 ≥ AeT、速度隨坡度變」判為自己走。
- 熱接口：`heat_status=None` 時等於 v1 的 Hadley 值。

---

## 8. 檔案與函式

| 檔案 | 函式或物件 | 變更 |
|---|---|---|
| `backend/engine/racepower/athlete.py` | `_grade_windows`、`GRADE_KEY` | 列加 `k`、`t`；換 key |
| 同上 | `_hike_windows`、`HIKE_KEY` | 列加 `t`；換 key |
| 同上 | 新 `walk_windows_trail(ds, runs, exclude)` | 走路陡坡段（連續、去第一窗、心率後移） |
| 同上 | 新 `run_speed_at_aet(ds, today)` | v_run,AeT |
| 同上 | 新 `hike_meta()` / `set_hike_meta()` | 每趟背負 |
| 同上 | `grade_models` | 回傳 `walk_capacity`；拿掉 solo 趟 HR 窗排除 |
| 同上 | `derive` | 沒有 solo 時 `eph` 改由能力模型；另回傳跟團 EP/h |
| `backend/engine/racepower/grade_model.py` | 新 `WalkCapacity`、`fit_walk_capacity` | B4；取代 `HikeSpeed` 在百岳的角色（`HikeSpeed` 留給交叉檢查） |
| `backend/engine/racepower/hike.py` | 新 `aet_power`（B1）、`prior_speed`（B2）、`pack_ratio`（B3 / B3'）、`descent_cap`、`group_time`、`band` | |
| 同上 | `hike_rows` | 改為 B8 |
| 同上 | `day_fatigue` | 改為 B6；EP/h 版保留當跟團描述 |
| 同上 | `tobler_eph` | 只留交叉檢查 |
| `backend/engine/racepower/hikehr.py` | `altitude_factor` | 加趟次固定效果、群集 bootstrap SE、第 1 天與前 2 h 版本 |
| 同上 | 新 `shrink_altitude(b, se, prior, tau)` | B7 |
| 同上 | 新 `classify_day(windows, stops, model)` | 自己走或跟團建議 |
| `backend/engine/racepower/env.py` | 新 `segment_temp(t0, z0, z)` | 遞減率 0.0065 K/m |
| 同上 | 新 `heat_term(temp_c, rh_pct, status)` | §8.1 接口 |
| `backend/engine/racepower/planner.py` | `plan_hike` | §5.2 |
| `backend/engine/racepower/backtest.py` | 新 `evaluate_walk_segments`、`evaluate_hike_hr_segments`、`hike_day_band`；`validated_flags` 加 `hike_capacity` | §7.1 |
| `backend/api/racepower.py` | `PlanIn` 加 `trip_kind`、`hr_band`、`pack_kg_by_day`、`heat_status`；新 `GET/POST /hike-meta` | |
| `backend/static/racepower.html` | `renderBaiyue`、輸入面板、登山紀錄表 | §6 |
| `docs/spec/racepower.spec.md` | 百岳段落 | 實作後用 `/prp-spec` 同步 |

### 8.1 熱項接口

放在 `env.py`。熱適應狀態由 `docs/research/heat-acclimation.md` 定義並產生，本文件只規定形狀：

```python
def heat_term(temp_c: float, rh_pct: float, status: Optional[dict]) -> dict:
    """Per-segment heat multiplier H_i for walking/running speed.
    status (owned by heat-acclimation.md; None = no data):
        {"state": str,            # e.g. "none" | "partial" | "acclimatised"
         "scale": float,          # s ∈ [0, 1]: fraction of the unacclimatised penalty that remains
         "as_of": "YYYY-MM-DD", "source": str, "evidence": str}
    Returns {"H": 1 − s·Hadley%(temp_c, rh_pct)/100, "penalty_pct", "s", "badge"}.
    With status None, s = 1 (v1 behaviour)."""
```

- 每段溫度：`segment_temp(t0, z0, z_i) = t0 − 0.0065·(z_i − z0)`。t0 與 z0 是登山口或測站。
- 濕度沿用同一個值。高山的露點遞減沒有做（未找到來源，保守）。
- `plan_hike` 在每段乘上 `H_i`。個人窗的正規化**也要除掉當時的 H**，前提是窗有溫度資料；沒有就不除，並記錄。

---

## 9. 待決問題（請使用者決定）

1. 預設背負：多日 12 kg、單日 6 kg、每天 −0.7 kg，這樣可以嗎？
2. 行程類型預設「跟團」（顯示跟團時間為主）還是「自己走」？
3. 海拔先驗寬度 τ = 3 個百分點（推估），可以接受嗎？或先等 §2.4 的診斷結果再定？
4. 自動判定自己走的閾值（60 % / 15 % / 1 次/km）先當建議，要等 ≥ 5 天手動標記才驗證，可以嗎？

## 10. 參考文獻

同儕審查（「摘要」＝只讀過摘要）：

- Breiner TJ, Ortiz ALR, Kram R (2019). Level, uphill and downhill running economy values are strongly inter-correlated. *Eur J Appl Physiol*. https://doi.org/10.1007/s00421-018-4021-x （題名，沿用 racepower-v2）
- Bassett DR et al. (1999). *Med Sci Sports Exerc* 31:1665–1676. https://doi.org/10.1097/00005768-199911000-00025 （二手核對，racepower-v2 §3C.3）
- Bink B (1962). The physical working capacity in relation to working time and age. *Ergonomics* 5:25–28. https://doi.org/10.1080/00140136208930548 （只有題名，經 Crossref；公式未核對）
- Coffman KE et al. (2020). Aerobic exercise performance during load carriage and acute altitude exposure. *J Strength Cond Res* 34:946–951. https://doi.org/10.1519/jsc.0000000000003557 （摘要）
- Coyle EF, González-Alonso J (2001). Cardiovascular drift during prolonged exercise: new perspectives. *Exerc Sport Sci Rev* 29:88–92. https://doi.org/10.1097/00003677-200104000-00009 （摘要）
- Davies CT, Thompson MW (1979). Aerobic performance of female marathon and male ultramarathon athletes. *Eur J Appl Physiol* 41:233–245. https://doi.org/10.1007/bf00429740 （摘要）
- Efron B, Morris C (1975). Data analysis using Stein's estimator and its generalizations. *J Am Stat Assoc* 70:311–319. https://doi.org/10.1080/01621459.1975.10479864 （依記憶引用，本次未抓取；只引用概念）
- Fulco CS, Rock PB, Cymerman A (1998). Maximal and submaximal exercise performance at altitude. *Aviat Space Environ Med* 69:793–801. PMID 9715971. https://europepmc.org/article/MED/9715971 （摘要）
- Giovanelli N, Ortiz AL, Henninger K, Kram R (2016). Energetics of vertical kilometer foot races; is steeper cheaper? *J Appl Physiol* 120:370–375. https://doi.org/10.1152/japplphysiol.00546.2015 （摘要）
- Levine L, Evans WJ, Winsmann FR, Pandolf KB (1982). Prolonged self-paced hard physical exercise comparing trained and untrained men. *Ergonomics* 25:393–400. https://doi.org/10.1080/00140138208925006 （只有題名）
- Looney DP et al. (2019). Estimating energy expenditure during level, uphill, and downhill walking. *Med Sci Sports Exerc* 51:1954–1960. https://doi.org/10.1249/MSS.0000000000002002 （摘要）
- Looney DP et al. (2022). Modeling the metabolic costs of heavy military backpacking. *Med Sci Sports Exerc* 54. https://pmc.ncbi.nlm.nih.gov/articles/PMC8919998/ （全文，沿用 racepower-v2 §3C.2）
- Ludlow LW, Weyand PG (2016). Energy expenditure during level human walking: seeking a simple and accurate predictive solution. *J Appl Physiol* 120:481–494. https://doi.org/10.1152/japplphysiol.00864.2015 （摘要）
- Ludlow LW, Weyand PG (2017). Walking economy is predictably determined by speed, grade, and gravitational load. *J Appl Physiol* 123:1288–1302. https://doi.org/10.1152/japplphysiol.00504.2017 （摘要；係數式經 Weyand 2021 表 1，轉錄不清）
- Maunder E, Seiler S, Mildenhall MJ, Kilding AE, Plews DJ (2021). The importance of 'durability' in the physiological profiling of endurance athletes. *Sports Med* 51:1619–1628. https://doi.org/10.1007/s40279-021-01459-0 （摘要）
- Minetti AE, Ardigò LP, Saibene F (1994). The transition between walking and running in humans: metabolic and mechanical aspects at different gradients. *Acta Physiol Scand* 150:315–323. https://doi.org/10.1111/j.1748-1716.1994.tb09692.x （摘要）
- Minetti AE, Moia C, Roi GS, Susta D, Ferretti G (2002). Energy cost of walking and running at extreme uphill and downhill slopes. *J Appl Physiol* 93:1039–1046. https://doi.org/10.1152/japplphysiol.01177.2001 （摘要；係數已驗證，`test_minetti`）
- Nuuttila OP et al. (2025). Durability in recreational runners: effects of 90-min low-intensity exercise on the running speed at the lactate threshold. *Eur J Appl Physiol* 125:697–705. https://doi.org/10.1007/s00421-024-05631-y （摘要，PMC11889008）
- Ortiz ALR, Giovanelli N, Kram R (2017). The metabolic costs of walking and running up a 30-degree incline. *Eur J Appl Physiol* 117:1869–1876. https://doi.org/10.1007/s00421-017-3677-y （摘要）
- Pandolf KB, Givoni B, Goldman RF (1977). *J Appl Physiol* 43:577–581. https://doi.org/10.1152/jappl.1977.43.4.577 （二手核對，racepower-v2 §3C.2）
- Robinson J, Roberts A, Irving S, Orr R (2018). Aerobic fitness is of greater importance than strength and power in the load carriage performance of specialist police. *Int J Exerc Sci* 11:987–998. https://doi.org/10.70252/iwxe4027 （摘要）
- Savoldelli A et al. (2017). The energetics during the world's most challenging mountain ultra-marathon — a case study at the Tor des Geants. *Front Physiol* 8:1003. https://doi.org/10.3389/fphys.2017.01003 （摘要）
- Shen TC, Lin MC, Lin CL, Lin WH, Chuang BK (2024). Acute mountain sickness on Jade Mountain: results from the real-world practice (2018–2019). *J Formos Med Assoc* 123:1161–1166. https://doi.org/10.1016/j.jfma.2024.01.030 （摘要）
- Wang SH et al. (2010). Epidemiology of acute mountain sickness on Jade Mountain, Taiwan: an annual prospective observational study. *High Alt Med Biol* 11:43–49. https://doi.org/10.1089/ham.2009.1063 （摘要）
- Wehrlin JP, Hallén J (2006). Linear decrease in VO2max and performance with increasing altitude in endurance athletes. *Eur J Appl Physiol* 96:404–412. https://doi.org/10.1007/s00421-005-0081-9 （摘要；全文數字見 effort-distance-formulas.md §6）
- Weyand PG, Ludlow LW, Nollkamper JJ, Buller MJ (2021). Real-world walking economy: can laboratory equations predict field energy expenditure? *J Appl Physiol* 131:1272. https://pmc.ncbi.nlm.nih.gov/articles/PMC8560389/ （全文網頁；表 1 與野外誤差）
- Wu HC, Wang MJ (2001). Determining the maximum acceptable work duration for high-intensity work. *Eur J Appl Physiol* 85:339–344. https://doi.org/10.1007/s004210100453 （摘要；範圍不適用）

經驗法則與其他：

- Tobler W (1993) 步行函數；Naismith；ITRA / 健行筆記 EP：見 `docs/research/effort-distance-formulas.md`（例：https://en.wikipedia.org/wiki/Tobler's_hiking_function）。
- Hadley 熱規則：跑者社群計算器，原始出處未取得（沿用 v1）。
- 標準大氣遞減率 0.0065 K/m：ICAO 標準大氣。`env.pressure_torr` 已使用。本次未另外抓取文件。
- 找過但未找到來源：
  - Campbell et al. 2019 用群眾外包健身資料建的坡度-速度模型（*Applied Geography* 106:93–107，https://doi.org/10.1016/j.apgeog.2019.03.008）。經 Crossref 確認存在，內容未讀，不引用數值；日後可當 Tobler 之外的通用形狀對照。
  - ITRA / UTMB index 的時長換算。
  - Riegel 型步行衰減。
  - 多日山行第 n 天的衰減研究。
