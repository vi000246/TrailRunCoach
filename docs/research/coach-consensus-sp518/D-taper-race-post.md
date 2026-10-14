# SP-518 D 組：減量、B 賽、賽後（D-taper-race-post）

查證日：2026-10-14。repo 唯讀：`/Users/logan/Projects/TrailRunCoach-wt-integrate-1012a`。

驗證標記：
- **已驗證**：這次 WebFetch 或 Europe PMC 讀到原文那句。
- **沿用**：repo 既有文件已驗證，標文件與段落（`PCS` 指 `docs/research/periodization-cross-sport.md`，方括號是它的參考文獻編號）。
- **二手**：別人轉述，會寫是誰轉述。
- **未驗證（摘要）**：只看到搜尋摘要。

「平常」的意思：教練多半用**峰值週**或**減量前的週量**當 100 %。app 的減量期用的是「近 6 週平均」（`base6`），恢復期、轉換期、回量期用「賽前 4 週平均」（`pre_race_mondays`）。近 6 週平均通常是峰值的 90–95 %，所以 app 的 50 % 約等於峰值的 45–48 %（**推估**）。

週次的算法照 Pfitzinger：第 1 週是賽前 1–6 天（比賽那週），第 2 週是 7–13 天，第 3 週是 14–20 天。app 的切點是 ≤ 7 天和 8–14 天，差 1 天，下面不另外區分。

---

## 1. 減量期的量

### 程式位置與現值

- `overview.py:1625`：`share = 0.4 if days_to <= 7 else 0.5`，乘上 `base6`（近 6 週平均）。`projection.py:141` 用同樣的規則。
- `overview.py:1630`、`projection.py:143`：週一落在 `event` 期的那週 `0.3 * base4`。`base4` 已經包含減量週，所以實際大約是平常的 20–25 %（**推估**）。
- 減量 21 天時（課表偏好），賽前 15–21 天也套 0.5，因為程式只分「≤ 7 天」和「其他」。
- 計算細節：`base6` 每週重算，所以減量第 2 週的 `base6` 已經含第 1 週的 50 %。結果第 2 週大約是平常的 0.4 × 0.92 ≈ 37 %（**推估**）。

### 來源表（剩下平常的 X %）

| 作者 | 出處 | 第 3 週（14–20 天） | 第 2 週（7–13 天） | 比賽週（1–6 天，不含比賽） | 基準 | 驗證 |
|---|---|---|---|---|---|---|
| Bosquet、Mujika 等 2007 | 統合分析，MSSE 39:1358（PMID 17762369） | — | 整段量減 41–60 %，指數式遞減，2 週 | ← | 減量前 | **已驗證**（Europe PMC 摘要：「training volume is exponentially decreased by 41-60%」） |
| Wang 2023 | 統合分析，PLOS ONE（PCS [207]） | — | 減 41–60 % 最好；減 ≤ 40 % 不夠 | ← | 減量前 | 沿用 PCS §4.5.1 |
| Mujika & Padilla 2003 | MSSE 35:1182（PMID 12840640） | — | 「reducing the training volume (up to 60-90%)」→ 剩 10–40 % | ← | 減量前 | **已驗證**（Europe PMC 摘要） |
| Pfitzinger | 網路講座投影片〈Marathon training webinar by Pete Pfitzinger〉第 29 張（slideshare 11191347） | 減 20–25 % → **75–80** | 減 40 % → **60** | 減 60 % → **40** | 未說明（「平常」） | **已驗證** |
| Pfitzinger & Douglas | 《進階馬拉松全書》中譯本書摘（PCS [459]） | 減 20–30 % → 70–80 | 減 40 % → 60 | 減 60 % → 40 | 同上 | 沿用 PCS §4.10.5 |
| Higdon | Intermediate 1 馬拉松課表第 15–18 週（halhigdon.com） | 32/43 mi → **74** | 24/43 → **56** | 9/43 → **21** | 峰值週 43 mi（第 15 週）；百分比是我算的 | **已驗證**（課表）；換算**推估** |
| Gaudette（RunnersConnect） | 〈Marathon Taper: Week-by-Week…〉（PCS [420]） | 85–90 | 70–75 | 40–50 | 峰值 | **已驗證**（這次重讀：「drops to 70 to 75 percent of peak」「roughly 40 to 50 percent of peak」） |
| Jason Karp | Active.com〈How to Taper to Boost Performance〉 | 減 30 % → **70** | 減 50 % → **50** | 減 65 % → **35** | 峰值；「not counting the marathon itself」 | **已驗證** |
| Koop（CTS，超馬） | trainright.com〈Tapering for Ultrarunning〉（PCS [419]） | （3 週版）40–50 | 2 週版：40–50；3 週版：20–30 | 2 週版：20–30；3 週版：< 20 | 平常 | 沿用 PCS §4.5.1 |
| Friel | 220 Triathlon 引用 Friel | 3 週減量：約減 30 % | 2 週減量：約減 50 % | — | 建構期的量 | **二手**（220triathlon 原頁 403，只看到搜尋摘要） |
| McMillan | 論壇轉貼 McMillan 網站：「reducing your volume 20 to 30 percent in each of the last two weeks」 | — | 70–80 | 累積 49–64 | 平常 | **二手**（論壇轉貼，原頁沒找到） |
| Roche（教 Skurka 跑 Boston） | andrewskurka.com 訪談 | ~100（80 mpw） | ~75（60） | ~50（40） | 峰值 80 mpw | **已驗證**（里程是 Skurka 自己報的；Roche：「The science of big cuts in mileage is really, really bad」） |
| Uphill Athlete | 〈How Should You Taper…〉（PCS [162]） | — | 約 75 | 約 56 | 每週約減 25 %（換算**推估**） | 沿用 PCS §4.5.1 |
| Knopp 等 2024 | 92 份次菁英馬拉松課表（PCS [418]） | 每週減 22–31 % | ← | 最後一週再減 46–54 % | 峰值在賽前 3–4 週 | 沿用 PCS §4.5.1；換成週次是**推估**：第 2 週約 50–60、比賽週約 25–35 |
| 江晏慶 | Garmin 部落格（PCS [454]） | 每週減 10–15 % | ← | ← | 前一週 | 沿用 PCS §4.10.4（**不採用**，比統合分析淺很多） |

沒找到數字的：Daniels（4 版書摘只寫「within a week of a race, switch to the prerace week」，**已驗證**；2Q 課表最後一週的比例沒讀到，二手書評說「not very aggressive」）；Hudson（*Run Faster* 的減量沒讀到）；Hansons（論壇說減得很少，**二手**）；Magness、Fitzgerald（沒有百分比）。

### 統計（只算有數字的教練與課表）

| 時段 | 數值（剩下 %） | 中位數 | 眾數 | 範圍 | app |
|---|---|---|---|---|---|
| 第 3 週（只有減量 21 天時才有） | Pfitz 77.5、Higdon 74、Gaudette 87.5、Karp 70、Knopp ~73、Friel 70、Roche 100、Koop 45 | **約 74** | 70–80 | 45–100 | **50** |
| 第 2 週 | Pfitz 60、Higdon 56、Gaudette 72.5、Karp 50、Koop 45、McMillan 75、Roche 75、UA 75、Knopp ~55、Friel 50 | **約 58** | 75（3 個）／50（2 個） | 45–75 | **50**（約峰值的 46 %） |
| 比賽週 | Pfitz 40、Higdon 21、Gaudette 45、Karp 35、Koop 25、Roche 50、UA 56、Knopp ~30、McMillan ~56 | **40** | — | 21–56 | **40**（約平常的 37 %） |

- 有數字的來源：12 個。已驗證：Bosquet、Mujika & Padilla、Pfitzinger 投影片、Higdon 課表、Gaudette、Karp、Roche，共 7 個；沿用 5 個；二手 2 個（Friel、McMillan）。
- 2 週減量的平均：app (50 + 40) / 2 = 剩 45 %，等於減 55 %，落在 Bosquet／Wang 的 41–60 % 之內。

### 換算

- 教練用「峰值」，app 用「近 6 週平均」：0.5 × base6 ≈ 峰值的 46 %（**推估**），比教練第 2 週的中位數 58 % 低一點。
- 比賽週的 40 % 剛好是中位數，也和 Pfitzinger 一樣。

### 建議與影響

- **比賽週 0.4：維持。** 剛好是中位數，也等於 Pfitzinger 的數字。
- **第 2 週 0.5：可以改成 0.6**（Pfitzinger 60 %、中位數 58 %）。改了之後 2 週平均剩 50 %，等於減 50 %，仍然在統合分析的 41–60 % 中間。影響：減量第 1 週（賽前 8–14 天）多 20 % 的量；比賽週不變。不改也有依據：0.5 在教練的範圍裡，也在 Bosquet 的範圍裡。
- **減量 21 天時，賽前 15–21 天應該另外給 0.75**（中位數約 74 %；Pfitzinger 75–80、Higdon 74、Karp 70）。現在是 0.5，比除了 Koop 以外的所有教練都深。影響：選 21 天減量的人，第 1 週多 50 % 的量（0.5 → 0.75）。三週平均 (0.75 + 0.6 + 0.4) / 3 ≈ 58 %，等於減 42 %，仍在 41–60 % 內。
- **比賽週 0.3 × base4：維持（推估）。** 只在週一落在比賽期時才用到。Higdon 比賽週不含比賽約 21 %，Koop 3 週版 < 20 %，同一個量級。

---

## 2. 減量期長度

### 程式位置與現值

- `planning.py:55`：`TAPER_DAYS = 14`
- `planning.py:66`：`TAPER_DAYS_RANGE = (14, 21)`，可以用課表偏好拉長到 21 天，只限路跑全馬以上和超馬級。

### 來源表

| 作者 | 出處 | 長度 | 驗證 |
|---|---|---|---|
| Bosquet 2007 | 統合分析 | 「2-wk duration」效果最好 | **已驗證**（摘要） |
| Wang 2023 | 統合分析 | 8–14 天最好；15–21 天也有效；≥ 22 天沒有效果 | 沿用 PCS §4.5.1 [207] |
| Mujika & Padilla 2003 | 綜論 | 「between 4 and more than 28 d」 | **已驗證** |
| Pfitzinger | 網路講座投影片 | 「Begin taper 3 weeks before your marathon」 | **已驗證** |
| Pfitzinger & Douglas | 中譯本書摘 | 最少 2 週，最好 3 週 | 沿用 PCS §4.10.5 |
| Higdon | TrainingPeaks〈A Short Taper for the Boston Marathon〉 | 18 週課表「a three-week taper after the final long run in Week 15」；12 週的 Boston Bound 用 2 週 | **已驗證** |
| Gaudette | RunnersConnect | 3 週 | **已驗證** |
| Karp | Active.com | 3 週 | **已驗證** |
| Roche | Skurka 訪談（Houston Marathon） | 「Taper: ~2 weeks」 | **已驗證** |
| Koop | trainright.com；UltraSignup | 2 週是經驗法則；短於 10–14 天或長於 21 天沒有好處 | 沿用 PCS [419]；UltraSignup 原頁 403，**未驗證（摘要）** |
| Uphill Athlete | 〈How Should You Taper…〉 | 100 英里賽 10–14 天 | 沿用 PCS [162] |
| Heward（Ultra-X） | 部落格 | 2–3 週 | 沿用 PCS [423] |
| McMillan | HMRRC 教練專欄轉述 | 2 週 | **二手**（原頁 403） |
| Friel | 220 Triathlon | 2–3 週，比賽越長減越久 | **二手** |
| Hansons | RunningAhead 論壇 | 10 天 | **二手** |
| Daniels | 《Daniels' Running Formula》4 版書摘（Human Kinetics） | 一般課表「within a week of a race, switch to the prerace week」→ 7 天 | **已驗證** |
| TrainerRoad | 部落格（Specialty 第 7–8 週） | 1–2 週 | **二手** |
| Strava 15.8 萬名跑者 | Smyth 2021（PCS [205]） | 3 週比 2 週好，4 週沒有更好 | 沿用 |
| Haugen 2022 | 世界級長跑選手（PCS [416]） | 最後 7–10 天才明顯減量 | 沿用 |
| Knopp 2024 | 92 份課表（PCS [418]） | 峰值在賽前 3–4 週 → 減量約 3 週 | 沿用 |
| Matos 2020 | 越野跑者觀察研究（PCS [417]） | 22–69 km 減 2 週；70 km 以上減 1 週 | 沿用 |
| 徐國峰 | 部落格（PCS [453]） | 8–14 天（平均 11 天） | 沿用 |
| 江晏慶、余文彥 | PCS [454][458] | 2–3 週 | 沿用 |

### 統計

- 21 個來源（不含 Mujika 的 4–> 28 天）。已驗證 7 個，沿用 10 個，二手 4 個。
- 天數（取中點）：7 天 2 個（Daniels、Haugen）；10–14 天 10 個；21 天 7 個（Pfitzinger、Higdon、Gaudette、Karp、Strava、Knopp、江／余）；14–21 天 2 個。
- **中位數 14 天，範圍 7–21 天。** 兩派：路跑馬拉松教練用 3 週；統合分析、超馬教練、菁英選手用 2 週以下。

### 建議與影響

- **維持 14 天，範圍 (14, 21) 也維持。** 預設 14 天就是中位數，也是統合分析效果最好的區間；21 天上限和 Wang 的「≥ 22 天沒有效果」、Strava 的「4 週沒有更好」一致。
- 不建議把路跑全馬的預設改成 21 天。Wang 的 8–14 天效果（SMD −1.47）大於 15–21 天（−0.78）；21 天留給想照 Pfitzinger 或 Higdon 練的人自己選。

---

## 3. 減量期課表細節

### 程式位置與現值

`overview.py:2970–2976`（註解 2945–2969）：

- `TAPER_FULL_Q_DAYS = 10`：最後一次完整強度課在賽前 ≥ 10 天。
- `TAPER_SHORT_Q_DAYS = (3, 5)`：最後一次短強度在賽前 3–5 天，量減半。
- `TAPER_LONG_DAYS = 7`、`TAPER_LONG_MAX_MIN = 90`：最後一次長跑在賽前 ≥ 7 天，≤ 90 分。
- `TAPER_LONG_SORE_DAYS = 14`：長跑後明顯痠痛（以 A 賽每公里下降 ≥ 40 m 判斷）時，最後一次長跑在賽前 ≥ 14 天。
- `TAPER_RUN_DROP = 1`：跑步次數最多少 1 次。

### 3a. 最後一次完整強度課（`TAPER_FULL_Q_DAYS = 10`）

| 作者 | 原文數字 | 驗證 |
|---|---|---|
| Gaudette | 「Your final hard workout of the cycle should fall 12 to 13 days before race day」（同一篇的比賽週表又排了 6–8 × 2 分馬拉松配速，前後不一致） | **已驗證** |
| Torrence（iRunFar） | 賽前 10–14 天，中等長度、熟悉的課 | 沿用 PCS §4.5.2 [421] |
| Hansons | 最後一次 SOS（馬拉松配速節奏跑）約賽前 10 天；理由是「速度課要約一週半才看得到效果」 | **二手**（RunningAhead 論壇） |
| Higdon Int 1 | 最後一段配速（4 mi pace）在第 16 週週六，賽前 8 天 | **已驗證**（課表） |
| Pfitzinger | 投影片只寫「Maintain training intensity」，沒有天數 | **已驗證**（沒有天數） |
| Roche | 關鍵課（24 mi 含 16 mi 馬拉松配速）在賽前 4 週；之後改成閾值節奏跑 | **已驗證** |

統計：有天數的 4 個，**中位數 10–12 天，範圍 8–14 天**。已驗證 2 個（Gaudette、Higdon）。
建議：**維持 10。** 位在範圍中間、偏晚的一側，和 Hansons、Torrence 的下限一致。

### 3b. 最後一次短強度（`TAPER_SHORT_Q_DAYS = (3, 5)`）

| 作者 | 原文數字 | 驗證 |
|---|---|---|
| Haugen 2022（世界級） | 最後一堂高強度在賽前 3–5 天 | 沿用 PCS §4.5.2 [416] |
| Gaudette | 比賽週週二 6–8 × 2 分馬拉松配速（週日比賽 → 賽前 5 天） | **已驗證** |
| Karp | 比賽週「one interval workout early in the week at either lactate threshold pace or slightly faster」（4 × 1200 m）→ 賽前約 5–6 天 | **已驗證** |
| Uphill Athlete | 賽前一天 5 分鐘 30 秒快／30 秒慢 | 沿用 PCS [162] |
| 越野滑雪金牌選手 | 11 人裡有 10 人在賽前 48 小時內做過一堂高強度 | 沿用 PCS [116] |
| Hansons | 最後一週不做速度課 | **二手** |
| Higdon | 比賽週沒有配速段（最後一次在賽前 8 天） | **已驗證** |

統計：天數 1–8，**眾數 5，中位數 5**。
建議：**維持 (3, 5)。**

### 3c. 最後一次長跑（`TAPER_LONG_DAYS = 7`、`TAPER_LONG_MAX_MIN = 90`）

| 作者 | 原文數字 | 驗證 |
|---|---|---|
| Torrence | 超馬：賽前 1 週，不管賽事多長都 ≤ 90 分輕鬆跑 | 沿用 PCS §4.5.2 [421] |
| Higdon Int 1 | 第 17 週週日 8 mi（賽前 7 天）；第 16 週 12 mi（14 天）；第 15 週 20 mi（21 天） | **已驗證**（課表）；8 mi ≈ 80–90 分是**推估**（10–11 分／mi） |
| Gaudette | 最後一次長跑在賽前 2 週，是峰值長跑的 50–60 %；比賽週不跑長跑 | **已驗證** |
| Karp | 減量第 1 週（3 週版）「one moderately-long run (20 to 24 kilometres)」 | **已驗證** |

統計：最後一次長跑 7 天的有 2 個（Torrence、Higdon），14 天的有 1 個（Gaudette），14–20 天的有 1 個（Karp）。**眾數 7 天。** 上限 90 分：Torrence 明講；Higdon 的 8 mi 也落在 90 分內（推估）。
建議：**維持 7 天和 90 分。** Gaudette 比較保守（14 天），可以當作「容易痠痛的人」的版本，和 `TAPER_LONG_SORE_DAYS` 同一個方向。

### 3d. 痠痛時提早（`TAPER_LONG_SORE_DAYS = 14`），以及「最長那次」離比賽多遠（參考）

| 作者 | 原文數字 | 驗證 |
|---|---|---|
| Fellrnr | 跑完明顯痠痛：≥ 21 天；只是累：14–21 天；幾乎沒影響：7–14 天 | 沿用 PCS §4.5.2 [422] |
| Bennett Cohen、Gail Gould（Canadian Running 轉述） | 最後一次長跑要在賽前 **4 週**；賽前 3 週跑長跑是「#1 training error committed by marathoners」 | **已驗證**（轉述，報導讀到） |
| Higdon | 最長的 20 mi 在賽前 3 週 | **已驗證** |
| Roche | 關鍵長跑在賽前 4 週 | **已驗證** |
| Knopp 2024 | 峰值長跑在賽前 3–4 週 | 沿用 |
| Lindsey Parry（Comrades） | 50–55 km 最後一次長跑：新手賽前 6 週、有經驗 5 週、菁英 3–4 週 | **已驗證** |
| Marathon Handbook | 50 英里／100 km 賽前 4–6 週跑一次 50 km | **未驗證（摘要）** |

- 這些多半是講「**最長**的那一次」，不是講減量期裡的長跑。中位數：馬拉松 3–4 週，超馬 4–6 週。
- `TAPER_LONG_SORE_DAYS = 14` 的意思是「減量期內不排長跑」（減量期本來就是 14 天）。來源只有 Fellrnr 一個，但方向和上表一致（越傷腿越要提早）。
- 建議：**維持 14。** 另外提出一個發現：「最長那次長跑離比賽 3–6 週」屬於專項期的規則（`specific_phase`），不在這組常數內。如果專項期的最後一次長跑排在賽前 15–20 天，就比教練共識晚。這要給負責專項期的人確認。

### 3e. 減量期跑步次數（`TAPER_RUN_DROP = 1`）

| 作者 | 原文數字 | 驗證 |
|---|---|---|
| Bosquet 2007 | 「without any modification of either training intensity … or frequency」 | **已驗證** |
| Wang 2023 | 維持頻率才有顯著進步 | 沿用 |
| Mujika & Padilla 2003 | 「slightly reducing training frequency (no more than 20%)」 | **已驗證** |
| Pfitzinger | 「Make recovery days very easy or take days off」 | **已驗證** |
| McMillan | 維持頻率和強度，比賽前可以多休 1 天 | **二手**（論壇轉貼） |
| Higdon Int 1 | 平常一週跑 5 次；比賽週 3 次跑步 + 比賽 = 4 次 | **已驗證**（課表） |
| Gaudette | 天氣熱或睡不好就多插 1 天休息 | **已驗證** |
| Koop、Heward | 次數維持或略減，每堂縮短 | 沿用 PCS §4.5.1 |

統計：減少 0–1 次；「≤ 20 %」換算到一週 4–6 跑，就是最多少 1 次（**推估**）。
建議：**維持 1。**

---

## 4. B 賽

### 程式位置與現值

- `planning.py:71`：`MINI_TAPER_DAYS = 5`，同 `post_race.B_INTERVAL_DAYS`。賽前 5 天不做間歇。
- `post_race.py:190`：`B_TEMPO_LONG_DAYS = 4`。賽前 4 天不做節奏跑、不跑長跑。
- `post_race.py:188`：`B_WEEK_SHARE = 0.75`，由 `b_week_factor`（`post_race.py:223`）套用在基礎期和專項期的週。
- `planning.py:93`：`B_RECOVERY_DAYS = 3`。`planning.py:385`：`B_REC_DAYS = {短: 3, 中: 5}`，馬拉松級以上照 A 賽的恢復天數。

### 4a. 賽前少練幾天（`MINI_TAPER_DAYS = 5`、4 天）

| 作者 | 原文數字 | 驗證 |
|---|---|---|
| Pfitzinger（Gone For a Run 部落格轉述） | 8–25 km 的熱身賽「about a 6 day taper」；8–12 km 若要小減量：「no interval runs 5 days before」「no tempo runs 4 days before」「no long runs 4 days before」 | **二手**（這次重讀，原文確認是轉述，沒寫書名，PCS [437]） |
| Pfitzinger（論壇轉述《Advanced Marathoning》第 1 章） | > 15 km 的熱身賽要小減量；> 15 km 賽後隔天不跑長跑 | **二手** |
| Friel | B 賽前休息 2–3 天 | 沿用 PCS §4.8.1 [431] |
| Friel（TrainingPeaks 範本說明） | 把 C 賽改成 B 賽時，「reduce the training load for the 3 days prior」 | **未驗證（摘要）** |
| Dan Way（Canadian Running，2017） | 週日比賽：週一、二照常；週三最後一堂課（賽前 4 天），量照常；週四 50–75 %、週五 25–50 %、週六休或 20 分 | **已驗證**（沒有引用教練） |
| Daniels | 一般課表：賽前一週換成「prerace week」 | **已驗證**（4 版書摘） |
| TrainerRoad | B 賽用 Specialty 第 7 週的減量格式；客服另說「our plans don't taper for B races」 | **二手**（部落格與論壇，兩者矛盾） |
| Jason Fitzgerald（Strength Running） | 熱身半馬前不減量，只降強度：賽前 5 天 51 mi 裡只有 4 mi 是硬的 | **已驗證** |

統計：「不做硬課」的天數有 2–3（Friel）、3（Friel 範本）、4（Way）、5（Pfitzinger、Fitzgerald）、7（Daniels、TrainerRoad）。**中位數 5，範圍 2–7。**
建議：**維持 5 和 4。**

### 4b. B 賽那週的量（`B_WEEK_SHARE = 0.75`）

- Way 的小減量換算（**推估**：週一到週六每天量相同、不含比賽）：(3 + 0.625 + 0.375 + 0.1) / 6 ≈ **68 %**。把比賽算進去約 75–85 %。
- Friel 休 2–3 天：如果一週跑 6 天，不含比賽約剩 55–70 %（**推估**）。
- Fitzgerald：不減量（100 %）。
- Pfitzinger、Daniels、TrainerRoad：沒有給百分比。

統計：只有 3 個能換算，範圍 55–100 %，中間約 70–75 %。全部是換算。
建議：**維持 0.75，仍標推估。** 找不到直接給這個比例的教練。

### 4c. B 賽後恢復幾天（`B_RECOVERY_DAYS = 3`、`B_REC_DAYS 短 3／中 5`）

| 作者 | 原文數字 | 驗證 |
|---|---|---|
| Daniels | 「following any race, take 1 E day of running for every 3K of race distance」；例：15K → 5 E 天，半馬 → 7 天 | **已驗證**（Human Kinetics，4 版書摘） |
| Pfitzinger（轉述） | 8–25 km 熱身賽「about 5 days of recovery post-race」 | **二手**（[437]） |
| Pfitzinger & Latter，《Faster Road Racing》 | 「hold back during the first three days after your race」；15K 以上「you may need an extra several days or even another week」 | **已驗證**（Human Kinetics 書摘） |
| Jack Foster（1974） | 「a day off racing for every mile I raced」：這是隔多久才能**再比賽**，不是多久才能練硬課 | **已驗證**（Marathon & Beyond 2004 編者文，引原書） |
| Roche（引 Pam Smith、Joe Uhan） | 超馬：每比 10 英里完全休 1 天；山地賽每 10 km 休 1 天 | **已驗證**（Skurka 訪談） |

換算成天數（Daniels 的每 3 km 一天）：5K 2 天、10K 3 天、15K 5 天、半馬 7 天、30K 10 天、全馬 14 天。

- app 的「短」（< 2 小時）是 3 天：10K 剛好一致；但 < 2 小時的半馬，Daniels 是 7 天、Pfitzinger 約 5 天以上。
- app 的「中」（2–4 小時）是 5 天：2–4 小時的比賽多半是 25–42 km（或越野 EP 21–42），Daniels 是 8–14 天。

統計：10K 級 3 天（Daniels）、3–5 天（Pfitzinger）；半馬 5–7 天；30 km 級 Daniels 10 天。來源 5 個，已驗證 4 個。
建議：**短維持 3 天；中可以考慮改成「每 3 km 一天」**（Daniels，唯一有明確公式又已驗證的），上限是同大小 A 賽的恢復天數。距離用 EP 還是平路 km 要再定（**推估**）。影響：半馬到 30 km 級的 B 賽之後，只排輕鬆跑的天數變長（5 → 7–10 天），那段時間少 1–2 堂強度課。

---

## 5. 賽後：恢復期、轉換期、回量期

### 程式位置與現值

- `planning.py:380`：`REC_SHARE = 0.40`，乘上「賽前 4 週平均」。
- `planning.py:379`：`REC_BY_SIZE`：短／中 7 天；馬拉松級 7 天 + 1 週回量；超馬級 14 天；100 英里級 14 天 + 2 週回量。`LONG_EVENT_HOURS = 6.0`（`planning.py:73`）是 7 天和 14 天的分界。
- `planning.py:382`：`REC_NO_RUN_DAYS = 2`，前 2 天不跑。
- `overview.py:517`：`TRANSITION_SHARE = 0.5`，乘上賽前 4 週平均。
- `planning.py:95`：`TRANSITION_WEEKS = 3`，可設 0–4，超馬級以上到 6。
- `planning.py:381`：`REBUILD_SHARES = (0.50, 0.75)`。

### 5a. 恢復期的量（`REC_SHARE = 0.40`）

| 作者 | 原文數字 | 換算成賽後第 1 週剩平常的 % | 驗證 |
|---|---|---|---|
| Higdon，Intermediate 賽後計畫 | 「The first three days after the marathon, I want you to do nothing」；第 4 天 2 mi、第 6 天 3 mi、第 7 天 60 分 | 約 11 mi ÷ 賽前 39–43 mi ≈ **26–28 %** | **已驗證**（課表，PCS [446]）；60 分 ≈ 6 mi 是**推估** |
| Pfitzinger（Brian Rock 轉述） | 週一、二全休；週三、五、日輕鬆跑；之後一週跑 5 天 | 無法換算（約 20–35 %，**推估**） | **二手** |
| Hansons（Brian Rock 轉述） | 「a full two weeks off from running」，之後 2 週慢慢加 | **0 %**（前 2 週） | **二手** |
| Koop（100 英里） | 第 1 週每次 ≤ 20 分；第 2–3 週只跑恢復配速、每次 < 1 小時 | 約 10–20 %（**推估**） | 沿用 PCS §4.6.1 [434] |
| Roche | 超馬每 10 mi 完全休 1 天（100 mi → 10 天；山地 100 mi 約 16 天）；馬拉松突破之後「Normally, I would prefer a month of easy running」 | 100 英里第 1 週 0 % | **已驗證** |
| Daniels | 全馬 ≈ 14 個 E 天（每 3 km 一天） | 沒給量 | **已驗證** |

統計：能換算的 3 個（Higdon 27 %、Koop 約 15 %、Hansons 0 %），**中位數約 15–27 %**。都比 app 的 40 % 淺很多；不過 3 個都是換算，而且 Hansons 是二手。
建議：**可以考慮把 0.40 降到 0.30**（最接近唯一已驗證、可換算的 Higdon 27 %）。證據弱：只有 1 個已驗證的換算，維持 0.40 也說得過去（推估）。影響：A 賽後第 1 週少 25 % 的量。前 2 天不跑（`REC_NO_RUN_DAYS`）不在這組，但順帶記：Higdon 3 天、《Faster Road Racing》「前 3 天 hold back」、Pfitzinger（轉述）2 天，眾數 2–3 天。

### 5b. 恢復期長度（7／14 天，`LONG_EVENT_HOURS = 6`）

| 作者 | 比賽 | 多久才回到正常訓練 | 驗證 |
|---|---|---|---|
| Daniels | 全馬 | 14 個 E 天（每 3 km 一天） | **已驗證** |
| Higdon | 全馬 | 5 週的回跑計畫（「a 5-week ramp」，TrainingPeaks） | **已驗證** |
| Pfitzinger | 全馬 | 5 週回跑計畫；第 5 週約峰值的 50–60 % | **二手**（Rock） |
| Hansons | 全馬 | 2 週不跑 + 2 週慢慢加 = 4 週 | **二手** |
| Roche | 馬拉松突破 | 1 個月輕鬆跑 | **已驗證** |
| Jack Foster | 全馬 | ≥ 26 天才再比賽 | **已驗證** |
| Johnston | 一般超馬／100 英里 | 約 2 週／「easily a month」 | 沿用 [442] |
| Torrence | 全馬或超馬 | 至少 1–2 週專心恢復 | 沿用 [445] |
| Koop | 100 英里 | 3 週只輕鬆跑 | 沿用 [434] |
| Roche | UTMB 級 | 完全休 2.5 週到 1 個月，「6 to 8 weeks to be back at full steam」 | **已驗證** |
| Roche 的課表（Bighorn 100 → UTMB） | 100 英里 | 「2-3 weeks of easy running, after nearly a week off」 | **已驗證** |

統計：全馬回到正常訓練：14（Daniels）、28（Hansons、Roche）、26（Foster，是比賽間隔）、35（Higdon、Pfitzinger），**中位數約 4 週**。100 英里：3–4 週到 6–8 週。
app 怎麼對應：馬拉松級 = 7 天恢復 + 1 週回量 = 14 天，再加上轉換期（預設 3 週）就是 5 週。關掉轉換期就只剩 14 天，等於 Daniels 的下限。100 英里級 = 14 + 14 = 28 天，加轉換期約 7 週，和 Roche 的 6–8 週一致。
建議：**7／14 天和 6 小時分界維持。** 第一段（不練）的長度和 Daniels、Torrence、Johnston 一致。另外提出：關掉轉換期時，馬拉松級只有 14 天就回到正常週，比中位數 4 週短。可以考慮馬拉松級的回量期改成 2 週（**推估**）。影響：關掉轉換期的人，A 賽後多 1 週回量。

### 5c. 轉換期的量（`TRANSITION_SHARE = 0.5`）

| 作者 | 原文數字 | 剩平常的 % | 驗證 |
|---|---|---|---|
| Bompa & Buzzichelli 2015 p.186 | 轉換期活動量降 50–60 % | **40–50 %** | 沿用 `bompa-periodization-strength.md` 第 73、166、235 行（注意：是「降」50–60 %，剩 40–50 %，app 的 50 % 在上限） |
| Wells 2026（95 名業餘鐵人） | 休賽季 340 分／週，專項期 556 分 | **61 %**（時間） | 沿用 PCS §4.7.1 [450] |
| Friel | 「About 3 or 4 weeks of reduced activity」；「staying active at a minimal level」；賽季最後一場之後先完全休幾天，之後每週 1–2 天完全休息 | 沒給 % | **已驗證**（這次讀 TrainingPeaks 原文） |
| Koop | 2–4 週不跑，其他運動都可以 | 0 %（跑步） | 沿用 [439] |
| Jay Johnson | 第 1 週只跑 1 次 20 分；第 2 週 3 次 20–30 分 | 約 10–25 %（**推估**） | 沿用 [447] |
| Canova | 4 週輕鬆跑，每次 ≤ 1 小時 | 沒給 % | 沿用（`overview.SRC_TRANSITION`） |

統計：有數字的 4 個：0、約 17、40–50、61。**中位數約 30–45 %，範圍 0–61 %。**
建議：**維持 0.5**（在 Bompa 的上限，低於鐵人實際做的 61 %）。不建議往上調。若要往下調，Bompa 的 40–50 % 是唯一出自教科書的數字。

### 5d. 轉換期長度（`TRANSITION_WEEKS = 3`）

| 作者 | 長度 | 驗證 |
|---|---|---|
| Friel | 3–4 週 | **已驗證** |
| Bompa | ≤ 4 週（另一處 ≤ 6 週） | 沿用 |
| Pfitzinger（2005 訪談） | 田徑季後休 3–6 週 | 沿用 `detraining.md` §4.8 |
| Canova | 4 週 | 沿用 |
| Jay Johnson | 2 週 | 沿用 [447] |
| Koop | 不跑 2–4 週 + 每週 2 跑 2–4 週 | 沿用 [439] |
| Hart | 休 2 週到 2 個月 + 回來 ≥ 4 週 | 沿用 [443] |
| Torrence | 1–2 個月不跑 | 沿用 [444] |
| Roche | 超馬完全休 2.5 週到 1 個月，全部 6–8 週 | **已驗證** |
| Haugen（世界級） | 1–2 週，有人約 4 週 | 沿用 [416] |
| 中文來源 | 過渡期 4–5 週；10 天到 1.5 個月 | 沿用 PCS §4.10.3 |

統計：路跑／田徑 2–4 週，**眾數 3–4 週，中位數約 3.5 週**；超馬 4–8 週。
建議：**維持 3**（預設）、上限 4、超馬級 6。Roche 的 6–8 週支持超馬級的 6 週上限。

### 5e. 回量期（`REBUILD_SHARES = (0.50, 0.75)`）

| 作者 | 原文數字 | 驗證 |
|---|---|---|
| Daniels 表 9.2 | 停 6–28 天：前半 50 %、後半 75 % | 沿用 `detraining.md` §4.1（使用者筆記的中文版書圖） |
| Higdon Intermediate 賽後計畫 | 第 2–4 週約 21、27、30 mi（tempo 和 60–90 分長跑換成 mi 是**推估**）→ 賽前的 **54 %、69 %、78 %** | **已驗證**（課表）；換算**推估** |
| Pfitzinger | 第 5 週約峰值的 50–60 % | **二手**（Rock） |
| Uphill Athlete | 每週 +7–10 %，不加強度 | 沿用 [441] |
| Koop | 先加次數再加每次的長度；不跑之後每週 2 跑，2–4 週 | 沿用 [439]；trainright〈return to training〉**未驗證（摘要）** |

統計：起點 50–54 %，終點 60–78 %。Daniels 和 Higdon 兩個都落在 50 → 75。
建議：**維持 (0.50, 0.75)。**

---

## 總表

| 常數 | 現值 | 共識（中位數／眾數；範圍） | 來源數（已驗證） | 建議 |
|---|---|---|---|---|
| 減量第 2 週（8–14 天） | 0.5 × base6 | 中位數 58 %，眾數 75／50；45–75 % | 12（7） | 維持，或改 **0.6**（Pfitzinger 60 %；2 週合計減 50 %） |
| 減量比賽週（≤ 7 天） | 0.4 × base6 | 中位數 40 %；21–56 % | 12（7） | **維持** |
| 減量第 3 週（只在 21 天減量時） | 0.5（和第 2 週共用） | 中位數約 74 %；45–100 % | 8（5） | **新增 0.75** |
| 比賽期週 | 0.3 × base4 | Higdon 約 21 %、Koop < 20 % | 2（1） | 維持（推估） |
| `TAPER_DAYS` | 14 | 中位數 14；7–21 天 | 21（7） | **維持** |
| `TAPER_DAYS_RANGE` | (14, 21) | ≥ 22 天沒有效果；Strava 4 週沒有更好 | 同上 | **維持** |
| `TAPER_FULL_Q_DAYS` | 10 | 中位數 10–12；8–14 天 | 4（2）+ 2 個沒給天數 | **維持** |
| `TAPER_SHORT_Q_DAYS` | (3, 5) | 眾數 5；1–8 天 | 7（3） | **維持** |
| `TAPER_LONG_DAYS` | 7 | 眾數 7；7–20 天 | 4（3） | **維持** |
| `TAPER_LONG_MAX_MIN` | 90 | Torrence 90；Higdon 8 mi ≈ 80–90 分 | 2（1） | **維持** |
| `TAPER_LONG_SORE_DAYS` | 14 | Fellrnr 14–21；「最長那次」3–6 週 | 7（4） | 維持；**專項期最長長跑的時間點要另外查** |
| `TAPER_RUN_DROP` | 1 | 0–1 次；Mujika ≤ 20 % | 8（5） | **維持** |
| `MINI_TAPER_DAYS`／`B_TEMPO_LONG_DAYS` | 5／4 | 中位數 5；2–7 天 | 8（3） | **維持** |
| `B_WEEK_SHARE` | 0.75 | 換算約 70–75 %；55–100 % | 3（2，都是換算） | 維持（推估） |
| `B_RECOVERY_DAYS`／`B_REC_DAYS` | 短 3／中 5 | Daniels 每 3 km 一天（10K 3、半馬 7、30K 10） | 5（4） | 短維持；**中改成「每 3 km 一天」**，上限照 A 賽 |
| `REC_SHARE` | 0.40 | 換算約 15–27 %；0–28 % | 3 可換算（1） | 可考慮 **0.30**（證據弱） |
| 恢復期長度（`LONG_EVENT_HOURS` 6 h → 7／14 天） | 7／14 | 全馬約 4 週回到正常；100 英里 6–8 週 | 11（6） | 維持；關掉轉換期時馬拉松級回量期可考慮 2 週 |
| `TRANSITION_SHARE` | 0.5 | 中位數約 30–45 %；0–61 % | 6（1） | **維持**（Bompa 40–50 % 的上限） |
| `TRANSITION_WEEKS` | 3 | 路跑眾數 3–4 週；超馬 4–8 週 | 11（2） | **維持** |
| `REBUILD_SHARES` | (0.50, 0.75) | 50 → 75（Daniels、Higdon） | 5（1） | **維持** |

## 限制與沒讀到的

- 原書沒讀到：《Advanced Marathoning》（減量表、賽後 5 週計畫）、《Daniels' Running Formula》（2Q 最後幾週的比例）、Hudson《Run Faster》、《Hansons Marathon Method》、Fitzgerald《80/20 Running》。
- 403 或抓不到：220triathlon（Friel 減量）、HMRRC（McMillan）、UltraSignup（Koop）、NSCA〈Tapering and Peaking〉、fetcheveryone（Pfitzinger 賽後計畫）、Marie Curie 減量頁（404）。
- 不採用：NSCA「量減 60–90 %、頻率 ≥ 80 %」（只有搜尋摘要）；Marie Curie「最後一次硬課賽前 10 天」（慈善機構，不是教練）；rundida.com 的「Pfitzinger 每英里一天」（第三方工具，沿用 PCS §4.6.1 的查無出處判定）；「Higdon 每英里一天」（只有搜尋摘要）。
- 「每英里休 1 天」的出處確定是 Jack Foster 1974 年，原意是「隔多久再比賽」，不是「多久不練硬課」。
- 換算成「平常的 %」的地方（Higdon 課表、Koop、Jay Johnson、Way）都標了**推估**，用的配速假設是 10–11 分／mi。

## 新增的參考網址

- Pfitzinger webinar：https://www.slideshare.net/slideshow/marathon-training-webinar/11191347
- Bosquet 2007：PMID 17762369（Europe PMC 摘要）
- Mujika & Padilla 2003：PMID 12840640（Europe PMC 摘要）
- Higdon Intermediate 1：https://www.halhigdon.com/training-programs/marathon-training/intermediate-1-marathon/
- Higdon，Boston 減量：https://trainingpeaks.com/blog/a-short-taper-for-the-boston-marathon
- Higdon，拉長減量：https://trainingpeaks.com/blog/extending-your-taper-before-a-marathon
- Higdon，賽後疲勞：https://www.trainingpeaks.com/blog/fatigue-following-marathon/
- Karp：https://www.active.com/running/Articles/How-to-Taper-to-Boost-Performance?page=3
- Gaudette：https://runnersconnect.net/how-to-taper-for-a-marathon/
- Roche（Boston）：https://andrewskurka.com/taper-training-boston-marathon-david-roche/
- Roche（Houston／UTMB 恢復）：https://andrewskurka.com/houston-marathon-david-roche-interview/
- Roche（100 英里）：https://www.goodreads.com/author_blog_posts/15267691
- Friel，轉換期：https://www.trainingpeaks.com/blog/the-transition-period-by-joe-friel
- Pfitzinger 熱身賽（轉述）：https://www.chalktalksports.com/goneforarun-blog/how-to-run-a-tune-up-race-during-marathon-training.html
- 《Faster Road Racing》書摘：https://us.humankinetics.com/blogs/excerpt/after-the-race-hellip
- 《Daniels' Running Formula》4 版書摘：https://us.humankinetics.com/blogs/excerpt/sample-running-program-from-daniels-running-formula
- Way，小減量：https://runningmagazine.ca/?p=19826
- Fitzgerald，熱身賽：https://strengthrunning.com/2016/05/should-you-taper-before-a-tune-up-race-during-marathon-training/
- Cohen & Gould，最後一次長跑：https://runningmagazine.ca/uncategorized/skip-long-runs-3-weeks-before-race-experts/
- Parry，Comrades 最後一次長跑：https://blog.coachparry.com/when-to-do-your-last-long-comrades-training-run/
- Jack Foster 規則：https://marathonhandbook.com/mab/editorial-november-december-2004-p-8/
- Rock（Pfitzinger／Hansons 賽後，二手）：https://runningwithrock.com/run-after-marathon/
- Rock（Daniels 2Q，二手）：https://runningwithrock.com/marathon-training-plan-jd-2q/
