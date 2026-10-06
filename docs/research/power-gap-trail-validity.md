# 功率與坡度調整配速在越野的可信度：陡上、陡下、技術地形（SP-199）

> 調查日期：2026-10-06。只做調查，沒有改程式。基準 commit `47c5a6e`。
> 這個題目已經有三份文件寫過大部分，這裡只引用、不重查：
> - `vo2max-gate-and-trail-metric.md` §2（van Rassel 2026、Gravina-Cognetti 2025、心率延遲、各坡度的時間佔比、「哪種課看哪個指標」的原則）。
> - `racepower-v2.md` §2.4（Minetti 2002、WKO5 的 ACSM GAP、Strava GAP、Taboga 2022、Breiner 2019、技術地形）。
> - `coaching-dashboards-mountain.md` §1.1–§1.2、§6.4（Uphill Athlete、Koop、Stryd 只適合路跑）。
> 這份補的是：新找到的功率計與坡度成本研究、程式裡和證據對不上的地方、以及建議的修改。
> 標記：**已驗證（全文）**／**已驗證（摘要）**／**二手**／**廠商**／**教練級**／**推估**／**未找到來源**。本文新編號 [G1]、[G2]…。

## 摘要

1. **Stryd 功率可信的範圍只有「平路到 +8 % 上坡」。** 這一段固定功率≈固定代謝負荷 [van Rassel 2026]。下坡只驗證到 −7 %，而且相關已經變弱（ρ 0.73），從 −7 % 到 +7 % 功率增加 90.5 %，耗氧只增加 74 % [Gravina-Cognetti 2025]。8 % 以上和下坡都沒有效度研究。
2. **同一個 Stryd 在山徑上量起來很穩定，但穩定不等於準。** 山徑重測 CV 2–3 %、ICC 0.98 [G1]；可是同時戴的 Garmin 功率在上坡差 109 W [G1]。不同品牌、不同人之間不能比 [Aubry 2018，見 `fueling-and-energy.md`]。
3. **坡度調整配速（GAP）的通用公式在陡坡誤差最大。** 674 筆合併資料：上坡的誤差 Minetti 2.18、ACSM 2.17、新公式 1.41 W/kg；下坡 Minetti 1.57、新公式 1.45 [G2]。成本最低的坡度在 −10 % 到 −20 % 之間 [G2]；Strava 的心率模型在約 −10 % 最快、下坡加成最多約 10 % [G3]。所有公式都是跑步機資料，**技術地形沒有資料** [G2]。
4. **app 現在的做法大致對。** 越野整場時間用心率模型（功率模型在越野差 +46 % 功率／−35 % 時間，`trailhr.py:4–9`）；課表只在 3–8 % 坡用功率；陡坡看心率＋VAM；下坡不設目標（`target_policy.py:14–31`、`seg_targets.py:1–17`）。GAP 都用 Minetti 加 0.9 的下坡上限，和 Strava 的 10 % 一致（`minetti.py:47–60`）。
5. **找到三個要修的地方**：
   - 計算機把 −8 % 到 0 的下坡段也當成「Stryd 驗證過」（`grade_model.py:115`、`:248` 用的是絕對值），但驗證只做了上坡。
   - 程式引用的「Kipp 2023」這次找不到原始論文，研究文件裡也沒有。下坡的說法應該改引 Gravina-Cognetti 2025。
   - GAP 只能當「方向參考」，畫面上沒有這樣寫。

## 1. 現況：app 什麼時候用功率、心率、GAP

| 用途 | 指標 | 位置 | 依據（程式註解） |
|---|---|---|---|
| 越野整場時間 | 心率配速模型（effort km ÷ 移動時間 對 心率／LTHR） | `backend/engine/racepower/trailhr.py:1–31` | 功率模型回測差 +46 %／−35 % |
| 越野分段目標 | 3–8 % 可跑坡：功率；> 8 % 或走：心率上限＋VAM；≤ −3 %：不設目標；平路：功率（沒有 CP 用心率） | `backend/engine/racepower/seg_targets.py:20–48` | van Rassel 2026；Kipp 2023；Gravina-Cognetti |
| 分段可信標記 | \|坡度\| ≤ 8 % 或該坡度箱有 ≥ 30 個個人視窗才算「可信」 | `backend/engine/racepower/grade_model.py:27`、`:112–115`、`:246–248`；用在 `planner.py:609` | van Rassel 0–8 % |
| 課表目標 | 路跑輕鬆／長跑：功率＋心率上限；越野輕鬆、山路長天：心率；3–8 % 爬坡重複、間歇：功率；長爬坡：不設；下坡練習：不設；健走：心率 ≤ 75 % HRmax 或 RPE ≤ 13 | `backend/engine/target_policy.py:14–31`、`:150–154` | 同上 |
| GAP（分段表、爬坡段、資料集） | Minetti 2002，下坡成本比下限 0.9 | `backend/engine/algorithms/minetti.py:47–60`；`planner.py:607`、`:616`；`workout_review.py:1643`；`wko5expr/fitdataset.py:297` | Minetti；Strava 下坡約 10 % |
| 計算機爬坡速度 | 個人 RE(g)，往 Minetti 收縮 | `grade_model.py:34–53`、`:59–144` | Breiner 2019 |
| 跑後 RPE | 有欄位，用來判斷「全力」 | `backend/engine/activity_tags.py:417–478` | — |

## 2. 文獻

### 2.1 功率計在坡上的準確度

既有文件已有（不重查）：van Rassel 2026（0–8 % 固定 Stryd 功率≈固定代謝，陡坡低估 2–4 %）、Gravina-Cognetti 2025（−7～+7 % 的相關與不成比例）、Taboga 2022（各家功率都和速度線性相關）、Aubry 2018（人與人之間 r = 0.29）。這次補的：

| 研究 | 條件 | 結果 | 等級 |
|---|---|---|---|
| Gravina-Cognetti 2025（補讀數字） | 15 名男性越野跑者，跑步機 −7、−5、0、+5、+7 %，70 % vVO2max | 功率 175.6 W（−7 %）→ 335.0 W（+7 %），+90.5 %；耗氧 2,521 → 4,390 ml/min，+74 %。作者：「power alone cannot fully account for the amount of energy expended」，要合併代謝和力學資料 | 已驗證（全文） |
| Berzosa 等 2024 [G1] | 5 人，2.5 km、爬 195 m 的山徑，一週後重測，同時戴兩個 Stryd 和兩支 Garmin | Stryd 自己重測：上坡 ICC 0.981、CV 2.1 %；下坡 ICC 0.983、CV 2.6 %。Stryd 對 Garmin 的平均差：上坡 109 W、下坡 37 W。作者：要用同一個裝置量，真實山徑的研究很少 | 已驗證（全文） |
| Imbach 2020（經 [G1] 與搜尋結果轉述） | 平地次最大速度 | Stryd 量步頻等參數有效；功率和外部功、耗氧強相關，但絕對值偏低 | 二手 |
| Stryd 官方 | — | 「in technical terrain and face steep terrain, you can no longer use a single power number」 | 廠商（`racepower-v2.md` §2.1.4） |

### 2.2 坡度調整配速（GAP）的誤差

| 來源 | 內容 | 等級 |
|---|---|---|
| Looney、Hoogkamer、Kram 2025 [G2] | 合併 674 筆資料（原始個人資料 63、26 篇個人資料 424、12 篇平均值 187），坡度 −45 % 到 +82 %，速度到約 3.5 m/s。均方根誤差（W/kg）：平路 RE3 1.27／Minetti 1.44／ACSM 1.82；上坡 1.41／2.18／2.17；下坡 1.45／1.57。成本最低在 −10 % 到 −20 %。限制：只有跑步機、沒有技術地形、−15 % 以下的下坡力學還不清楚 | 已驗證（預印本全文；正式版 *Eur J Appl Physiol* 2025 沒讀） |
| Strava 2017 [G3] | 舊版 GAP 用 Minetti；新版用數百萬筆跑步的心率擬合。下坡最多約 10 % 的速度好處；約 −10 % 最快，−20 % 以下不再增加 | 二手（工程部落格 403，經搜尋結果轉述；`racepower-v2.md` 也標「本次未重新驗證」） |
| Minetti 2002 | 平滑跑步機；下坡成本比久坐的人低約 40 %，表示下坡很吃技術 | 既有文件（`effort-distance-formulas.md` §7） |
| WKO5 的 GAP（ACSM 線性式） | +20 % 少算約 22 %，−21 % 以下變負 | 既有文件（`minetti.py:12–17` 註解） |
| TrainingPeaks／CTS（Koop） | GAP 和 NGP「neither accounts for differences in surface type」、都低估下坡的壓力，只能當「directional signal」 | 教練級（`coaching-dashboards-mountain.md` §1.2） |

重點：

- **上坡是 Minetti 誤差最大的地方**（2.18 對新公式 1.41 W/kg）[G2]。app 的計算機不直接用 Minetti 算時間，而是用個人資料往它收縮，所以影響主要在沒有個人資料的坡度箱（推估）。
- **下坡：所有公式的最低點都在 −10 % 到 −20 %**，Strava 說約 −10 % 最快、加成不超過 10 %。app 的 0.9 下限等於「最多快 11 %」，和 Strava 一致。
- **技術地形：沒有任何公式有資料** [G2]。app 用個人的「技術係數」按坡度箱修正（`grade_model.py:147–157`；`unsourced-rules.md` §A4），這是目前唯一可行的做法。

### 2.3 越野課表該用哪個指標

`vo2max-gate-and-trail-metric.md` §2.3 已經定了原則，這次沒有新證據推翻：

1. 目標是「很長時間待在 AeT 以下」：心率。
2. 目標是「幾分鐘的特定強度」：3–8 % 可跑坡用功率（心率延遲 55–70 秒，3 分鐘以內的坡心率來不及）。
3. 陡坡（> 12–15 %）：VAM 加心率。
4. 下坡：劑量（下降公尺、時間）加 RPE，功率和心率都不當目標。
5. RPE 每次都記。

補充：

- 一位跑者的越野資料裡，只有約 20 % 的時間落在 Stryd 驗證過的坡度（`vo2max-gate-and-trail-metric.md` §2.2）。新的山徑研究 [G1] 只證明「同一個裝置穩定」，沒有證明「準」，不改變這個結論。
- Gravina-Cognetti 作者明說功率要搭配其他資料 [Gravina-Cognetti 2025]，和「越野不單用功率」一致。

## 3. 落差

| # | 程式 | 問題 | 證據 | 影響 |
|---|---|---|---|---|
| 1 | `grade_model.py:115`、`:248`：`abs(g) <= STRYD_VALID_GRADE` | 下坡 −8 % 到 0 也算「驗證過」 | van Rassel 只做 0–8 % 上坡；−7 % 的相關較弱、功率和耗氧不成比例（Gravina-Cognetti） | 沒有個人資料時，−3～−8 % 的下坡段不會標「推估」。`re_prior` 的說明文字（`grade_model.py:39–41`）寫「0–8 %」，和程式不一致 |
| 2 | `target_policy.py:27`、`:47`；`seg_targets.py:11`、`:33`、`:102` | 引用「Kipp 2023」，研究文件裡沒有這篇；這次搜尋也找不到 | 下坡功率低估代謝的證據其實是 Gravina-Cognetti 2025（`vo2max-gate-and-trail-metric.md` §2.1） | 來源無法追溯；說法本身有其他證據支持 |
| 3 | 分段表的 `gap_pace_s_per_km`（`planner.py:616`） | 越野分段顯示 GAP 配速，沒有說明它在陡坡和技術地形不準 | [G2] 的限制；Koop「directional signal」 | 使用者可能把 GAP 當成目標 |
| 4 | 越野平路段（`seg_targets.py:24`、`:30`）用功率 | 越野的「平路」常是技術路段 | 只有 ±3 % 的平滑路面有驗證 | 小。平路只佔約 11 % 的時間（§2.3） |
| 5 | Minetti 當 RE(g) 的先驗（`grade_model.py:34–53`） | 上坡誤差比新公式大 | [G2] | 小。個人資料收縮 n/(n+30) 後先驗權重下降；要用 RE3 得先取得係數 |

## 4. 結論與建議

1. **主要指標不改。** 越野的時間用心率模型、課表照 `target_policy` 的分法，證據沒有變。
2. **把「可信」改成只認上坡 0–8 %**（落差 1）。下坡沒有個人資料時標「推估」。
3. **把 Kipp 2023 換成 Gravina-Cognetti 2025**（落差 2）。如果使用者知道 Kipp 2023 是哪一篇，補進研究文件也可以。
4. **GAP 加一句說明**（落差 3）：「坡度調整配速只供參考，陡坡、下坡、技術路段不準」。
5. **平路段先不改**（落差 4）。佔時間少，個人技術係數已經在修。
6. **RE3 先不換**（落差 5）。要先讀正式版拿到係數，而且個人資料已經壓過先驗。

## 5. 建議開的實作單

| # | 標題 | 優先度 | 驗收條件草案 |
|---|---|---|---|
| 1 | 計算機的「Stryd 可信」只認 0–8 % 上坡 | P2 | ① `GradeRE.trusted`、`GaitRE.trusted` 改成 `−0.02 ≤ g ≤ 0.08` 或該箱 ≥ 30 個個人視窗（±2 % 視為平路，沿用 `course` 的平路定義）。② 單元測試：g = −0.05、沒有個人資料 → 不可信；g = +0.05 → 可信；g = −0.05、40 個視窗 → 可信。③ `re_prior` 的說明文字和程式一致。④ 預估時間不變（這張只改標記） |
| 2 | 下坡功率說法的來源改為 Gravina-Cognetti 2025 | P3 | ① `target_policy.SRC["down"]`、`seg_targets.SRC["descent"]` 和兩個模組的註解，「Kipp 2023」改成「Gravina-Cognetti 2025（−7～+7 %：功率 +90 %、耗氧 +74 %）」。② 英文翻譯同步。③ 如果使用者提供 Kipp 2023 的出處，改為補進研究文件並保留 |
| 3 | 越野分段的 GAP 配速加說明 | P3 | ① 越野計算機分段表的 GAP 欄位加提示：「Minetti 坡度調整，只供參考；陡坡、下坡、技術路段不準」，附 [G2] 與 Koop。② 不改數值。③ 路跑不顯示 |

## 6. 需要使用者決定的事

1. **「Kipp 2023」你知道是哪一篇嗎？** 知道的話給我標題或連結，我補進研究文件；不知道就照 #2 換掉。
2. **#1 會讓更多下坡段顯示「推估」。** 我建議接受，因為比較誠實；如果覺得太吵，可以只在「沒有個人資料」時標。
3. **越野平路段要不要也改看心率？** 我建議先不要（落差 4），等有技術路段的分類再說。

### 已決定（2026-10-06）

1. 使用者不知道 Kipp 2023 的出處，交給我決定：**換成 Gravina-Cognetti 2025**（#2）。
2. 接受更多下坡段顯示「推估」（#1 照原案：沒有個人資料的下坡段都標）。
3. 越野平路段**維持功率**，不改心率。

已開單（2026-10-06）：#1 → **SP-246**（P2）、#2 → **SP-247**（P3）、#3 → **SP-248**（P3）。

## 7. 限制

- 功率計研究都是跑步機或短山徑、少數受試者；8 % 以上的上坡和 −7 % 以下的下坡完全沒有效度研究。
- [G2] 讀的是預印本；正式版的係數和數字可能有小修改。
- Strava 的 GAP 部落格讀不到，數字是二手。
- 沒有用任何人的活動資料驗證（交接規定不讀個人資料）。§2.3 的時間佔比來自既有文件。

## 8. 讀不到的來源

- Strava Engineering〈An Improved GAP Model〉（2017）：medium.com 回 403。https://medium.com/strava-engineering/an-improved-gap-model-8b07ae8886c3
- Looney 等 2025 正式版（*Eur J Appl Physiol*，https://link.springer.com/article/10.1007/s00421-025-05999-5）：Springer 要登入，讀的是 bioRxiv 預印本。
- 「Kipp 2023」：程式引用，這次搜尋找不到符合的論文（**未找到來源**）。
- Imbach 2020〈Validity of the Stryd power meter in measuring running parameters at submaximal speeds〉全文：這次沒有讀，只有轉述。https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7404478/

## 參考文獻

- [G1] Berzosa C, Comeras-Chueca C, Bascuas PJ, Gutiérrez H, Bataller-Cervero AV. Assessing trail running biomechanics: a comparative analysis of the reliability of Stryd and GARMIN RP wearable devices. *Sensors (Basel)* 2024;24(11):3570. https://doi.org/10.3390/s24113570 ；PMC11175203
- [G2] Looney DP, Hoogkamer W, Kram R. Metabolic energy expenditure during level, uphill, and downhill running. *Eur J Appl Physiol* 2025. https://doi.org/10.1007/s00421-025-05999-5 ；預印本 https://www.biorxiv.org/content/10.1101/2025.06.05.658094v1.full
- [G3] Strava Engineering. An improved GAP model. 2017. https://medium.com/strava-engineering/an-improved-gap-model-8b07ae8886c3 （二手）
- Gravina-Cognetti 等 2025, *Sports* 13:294, https://doi.org/10.3390/sports13090294 ；PMC12473670（這次補讀數字）。
- 既有文件：van Rassel 2026、Hunt 2015，見 `vo2max-gate-and-trail-metric.md`；Minetti 2002、Taboga 2022、Breiner 2019，見 `racepower-v2.md`；Aubry 2018，見 `fueling-and-energy.md`；Koop、UA，見 `coaching-dashboards-mountain.md`。
