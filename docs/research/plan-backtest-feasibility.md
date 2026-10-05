# 能不能回測「照 app 排的課練有沒有效」（SP-71）

> 調查日期：2026-10-05。只做調查，沒有改程式；對資料庫與資料檔只做唯讀存取。
> 標記沿用 `unsourced-rules.md`：**已驗證**＝這次讀到原文或摘要；**未驗證**＝只看到轉述；**推估**＝我的延伸。
> 個人數字只寫彙總，不寫單筆活動。

## 摘要

1. **現在不能回測。** 「照 app 排的課練」的紀錄只有 1 堂完成的課（2026-10-04），課表從 2026-09-28 那一週才開始存。過去兩年的訓練不是照 app 排的。
2. **過去的資料也回答不了因果。** 用現有資料做了一次初步的觀察性分析（9 組相鄰的 8 週區塊）：訓練量、次數、連續性和「區塊最佳 12／30 分功率的變化」之間沒有關係（Spearman −0.27 到 +0.27）。唯一看起來很強的關係（前一區塊的週時數 → 下一區塊的有氧效率，+0.83）是季節造成的，不是訓練效果（§3）。
3. **結果指標本身不夠乾淨。** 沒有比賽紀錄、沒有實測的 AeT／LTHR、只偵測到 1 次 CP 測試而且被判為非全力。現有的「CP」是從平常跑步的最佳功率推的，不是測出來的。
4. **文獻也不支持用模型補這個洞。** 訓練負荷 → 表現的模型（Banister 類）參數不穩、對未來的預測力有限；單一受試者要下結論，需要重複測量和預先定好的設計（§4）。
5. **能做的是從現在開始記錄**，6–12 個月後才看得出東西。最小設計：固定的測試、不被覆寫的課表快照、每週遵從度（§5）。即使做了，一個人的資料只能回答「這個人照排的課練之後有沒有進步」，不能回答「app 的排法比別的排法好」。

## 1. 本機有什麼資料（都是實際查詢的結果）

查詢方式：SQLite 以 `mode=ro` 開啟 `~/.wko5coach/wko5coach.db`；JSON 快取用 Python 讀取。查詢日 2026-10-05。腳本在本次工作階段的暫存目錄，沒有進 repo。

### 1.1 活動

| 項目 | 數量 | 範圍 | 查詢 |
|---|---|---|---|
| `workout_files` 全部 | 1,465 | 2020-11-21 – 2026-10-04 | `count(*)` |
| 其中 COROS FIT | 453 | 2024-07-16 – 2026-10-04 | `source='coros'` |
| 其中 WKO5 的 wko4 | 1,012 | 2020-11-21 – 2026-05-14 | `source='local'` |
| COROS 跑步（路跑 240＋7、越野 78＋2） | 327 | 2024-07-16 – 2026-10-04 | `coros_sport_type in (100,101,102)` |
| COROS 登山／健行 | 17 | 2024-09-17 – 2026-08-14 | `coros_sport_type in (104,105)` |
| 有功率曲線的跑步 | 284 | 2024 年 7 筆、2025 年 138 筆、2026 年 139 筆 | `racepower_cptests.json` 有 `curve` 的筆數 |
| 功率來源是 Stryd 的 | 267 | 2025-03-22 – 2026-10-04 | `racepower_power_source.json` |
| 有 hrTSS | 327 | 2024-07-16 – 2026-10-04 | `workout_metrics.metric_key='hr_tss'` |
| 有 RPE／感受 | 1／1 | — | `count(rpe), count(feel)` |

- 單上寫「本機 416 筆」，現在是 453 筆（之後又同步了）。
- App 設定的資料來源是 COROS（`charts.data_source = "source"`、`sync.primary_source = "coros"`）。wko4 的 1,012 筆是舊資料，運動類型多半是 unknown／walking，沒有進這次的分析。
- **有 Stryd 功率的期間只有約 19 個月**（2025-03 起）。2024 年下半年有心率、幾乎沒有功率。
- 主觀感受只有 1 筆，等於沒有。

### 1.2 體能指標的紀錄

| 指標 | 現有的點 | 來源 |
|---|---|---|
| CP 測試（3／12 分） | 偵測到 1 次（2026-09-30），判定為非全力：3 分功率不高於 12 分功率 | `racepower_cptests.json` 的 `test` 欄 |
| 閾值歷史（`plan.json` 的 `thresholds`） | 1 列（2026-10-04），只有最大心率；CP、LTHR、AeT 都是空的 | `~/.wko5coach/plan.json` |
| `athlete_settings` | 4 列（2026-05-15 – 2026-10-04）；LTHR 從 182 改成 152，FTP 一直是 200 | `athlete_settings` |
| 比賽 | 0（`plan.json` 的 `events` 是空的；`activity_tags` 0 列；`race_calc` 0 列） | 同左 |
| 傷病紀錄 | 0 列 | `injury_events` |

- 沒有任何一個「實測」的 CP、AeT 或 LTHR 時間序列。現在 app 用的 CP（約 191 W）是模型從平常跑步估的。
- 沒有標記過的比賽，所以「比賽成績相對預測」目前算不出來。

### 1.3 課表紀錄

| 項目 | 數量 | 範圍 |
|---|---|---|
| `plan_sessions` | 44 列 | 週起始 2026-09-28 – 2026-11-23 |
| 其中已完成 | 1（長跑） | 2026-10-04 |
| 其中未來的課 | 43（輕鬆 29、長跑 6、強度 8） | — |
| `plan_change_log` | 10 列 | 全部在 2026-10-04 |
| `coros_plan_push` | 5 列 | 全部在 2026-10-04 |

- **照 app 排的課練的樣本數是 1。** 沒有辦法做任何遵從度對成效的分析。
- 課表列會被重新產生覆寫（還沒編輯過的自動課，見 `docs/spec/plan-auto.spec.md` 的 reconcile 規則 2），所以 `plan_sessions` 不是「當時排了什麼」的歷史紀錄。`plan_change_log` 有前後狀態，但只記有變動的課。

## 2. 「回測」可以指三件不同的事

| | 做法 | 現在可行嗎 | 能下的結論 |
|---|---|---|---|
| (a) 觀察性分析 | 過去的訓練「符合 app 規則的程度」對之後體能變化 | 可以跑，但樣本只有 9–10 個區塊 | 很弱。季節、熱、路線、有沒有全力跑都混在一起 |
| (b) 反事實重播 | 讓 app 對過去每一週排課，和實際練的比 | 部分可行（見下） | 只能說「app 會排得和你實際練的差多少」，**不能**說哪個比較好 |
| (c) 前瞻追蹤 | 從現在起記錄遵從度與體能指標 | 可行，需要補記錄 | 中等：能回答「照著練之後有沒有進步」，仍然沒有對照組 |

(b) 的細節：

- 引擎已經有重播的零件：`quality_gate.z5_history`（`backend/engine/quality_gate.py:1530`）會用每一天當時的輸入重算 5 區的解鎖狀態；`projection.project_weeks`（`backend/engine/projection.py:376`）會往後排。
- 但重播需要「當時的閾值」。過去沒有 AeT、LTHR、CP 的歷史紀錄（§1.2），只能用現在的值回填，等於用未來的資訊排過去的課。
- 重播也沒有結果可以比：反事實的課沒有人練過。
- 這次**沒有跑** (b)。原因：引擎載入資料時會寫入快取檔（`backend/engine/wko5expr/fitdataset.py:461–466`、`:910–912`），違反這次「只做唯讀」的限制。要跑的話需要把資料複製到沙盒再跑。

## 3. 初步的觀察性分析（a）

### 3.1 做法

- 資料：2025-01-06 – 2026-10-04 的 COROS 跑步；功率只用 Stryd 的 267 筆。
- 切成 11 個不重疊的 8 週區塊。第一個區塊沒有功率，所以有 10 個區塊有結果、9 組相鄰區塊的變化。
- 每個區塊的訓練特徵：平均每週跑步時數、每週跑步次數、「連續性」（符合 3 區解鎖條件的週數比例：該週 ≥ 3 跑、沒有 ≥ 7 天沒跑）、週時數比 max(上週, 前 4 週平均) 多 20 % 以上的週數。
- 每個區塊的結果：區塊內最佳 12 分功率、最佳 30 分功率、路跑 30–90 分的「平均功率 ÷ 平均心率」中位數（有氧效率 EF）。
- 統計：Spearman 等級相關。n = 9 時，雙尾 5 % 的臨界值約 0.70（教科書查表值，沒有另外查證）。一共算了 18 個相關，沒有做多重比較校正。
- 沒有算低強度佔比：那需要逐秒心率，要跑引擎（同 §2 的限制）。

### 3.2 結果

各區塊的訓練量：每週 1.0–6.6 小時、2.2–4.4 跑；連續性 0.38–1.00。

| 結果變化 | 對同一區塊的訓練 | 對前一區塊的訓練 |
|---|---|---|
| 最佳 12 分功率 | 時數 +0.10、次數 +0.27、連續性 +0.03 | 時數 −0.03、次數 −0.27、連續性 +0.05 |
| 最佳 30 分功率 | 時數 +0.08、次數 +0.20、連續性 −0.13 | 時數 −0.05、次數 −0.25、連續性 +0.08 |
| EF | 時數 +0.42、次數 +0.10、連續性 +0.22 | 時數 **+0.83**、次數 **+0.72**、連續性 +0.55 |

（每格 n = 9。）

### 3.3 怎麼讀

- **功率的結果是「看不出關係」**，不是「沒有關係」。n = 9 時，相關係數的信賴區間寬到涵蓋 −0.6 到 +0.8。
- **EF 的 +0.83 不能當成訓練效果。** EF 有很強的季節性：按月份看，中位數從 2 月的 1.18 降到 8 月的 1.03，再升回 12 月的 1.15（每月 n = 8–36）。訓練量最高的區塊在夏天到秋天，下一個區塊天氣變涼，EF 自然上升。`heat-acclimation.md` 的回測已經量過這個人的熱效應（心率隨熱指數上升）。
- **「區塊最佳功率」不是測試。** 它取決於那 8 週有沒有全力跑。各區塊的最佳 12 分功率是全期最佳的 0.87–1.00 倍，這個範圍裡有多少是體能、多少是「那段時間沒有跑到全力」，分不出來。
- 週時數超過 20 % 的週每個區塊只有 1–3 週，變化太小，沒有分析價值。

結論：現有資料**不足以**回答 app 的規則有沒有效。這是資料的限制，不是分析方法選錯。

## 4. 文獻怎麼說

| 來源 | 等級 | 重點 | 驗證 |
|---|---|---|---|
| Hellard et al. 2006, *J Sports Sci* 24:509–520, DOI 10.1080/02640410500244697 | 同儕審查 | 9 位菁英泳者一季的資料套 Banister 模型：擬合 R² 0.79 ± 0.13，但最有用的參數的 95 % 信賴區間很寬（例如 tₐ 38 天，17–59），參數之間高度相關，「making their interpretation worthless」 | 已驗證（摘要） |
| Marchal et al. 2025, *Sci Rep* 15:3706, DOI 10.1038/s41598-025-88153-7 | 同儕審查 | 用交叉驗證區分「擬合」和「預測」：模型病態、參數難以辨識；加入疲勞項沒有顯著改善預測（p > 0.40），兩組獨立資料結果相同 | 已驗證（摘要） |
| Vermeire et al. 2022, *IJSPP* 17:810–813, DOI 10.1123/ijspp.2021-0494 | 同儕審查（評論） | 模型參數受起始值、擬合方法、輸入的負荷算法影響；不要用通用常數；建議「data-informed rather than data-driven」 | 已驗證（摘要） |
| Imbach et al. 2022, *Sports Med Open* 8:29, DOI 10.1186/s40798-022-00426-x | 同儕審查（回顧） | 模型把所有訓練壓成一個負荷數字，分不出不同類型的課；沒有納入環境、營養、心理；描述與預測表現的效果「remains mitigated」 | 已驗證（全文摘要與章節） |
| Kinugasa, Cerin & Hooper 2004, *Sports Med* 34:1035–1050, DOI 10.2165/00007256-200434150-00003 | 同儕審查（回顧） | 單一受試者研究的四種設計：AB、撤回（ABA）、多重基線、交替處理；資料有序列相關，分析要處理 | 已驗證（摘要） |
| Hecksteden et al. 2015, *J Appl Physiol* 118:1450–1459, DOI 10.1152/japplphysiol.00714.2014 | 同儕審查 | 個人訓練反應的評估、分類、預測在統計上和群體主效應不同，需要專門的方法 | 已驗證（摘要） |
| Hecksteden et al. 2018, *J Appl Physiol* 124:1567–1579, DOI 10.1152/japplphysiol.00896.2017 | 同儕審查 | 一年訓練、每 3 個月重複測 VO2max（n = 36）：不同分析方法對「誰有反應」的分類只有 20 人中 11 人一致。重複測試是個人層級分析的必要條件 | 已驗證（摘要） |
| Hopkins, Schabort & Hawley 2001, *Sports Med* 31:211–234, DOI 10.2165/00007256-200131030-00005 | 同儕審查（統合分析，101 篇） | 定功率測試 1 分到 3 小時的典型誤差 CV 0.9–2.0 %；乳酸閾值功率約 1.5 %；非運動員 × 1.3；第一次測試有學習效應（前兩次之間 CV × 1.3、成績 +1.2 %） | 已驗證（摘要） |
| Muñoz et al. 2014, *IJSPP* 9:265–272, DOI 10.1123/ijspp.2012-0350 | 同儕審查（隨機分組） | 30 位業餘跑者 10 週：極化組 10 km 進步 5.0 %、閾值間組 3.6 %。搜尋摘要提到嚴格遵從的子群差距更大（7.0 % 對 1.6 %） | 主要結果已驗證（摘要）；遵從子群的數字**未驗證** |
| Smyth & Lawlor 2021, *Front Sports Act Living* 3:735220, DOI 10.3389/fspor.2021.735220 | 同儕審查（觀察性） | 15.8 萬位業餘馬拉松跑者：嚴格的 3 週減量比最小減量快 2.6 %（中位 5 分 32 秒） | 已驗證（摘要） |

從這些來源得到的判斷：

- **不要用 Banister／CTL 模型去「證明」排課有效。** 模型可以把過去擬合得很好，但參數不穩，對未來的預測沒有比簡單模型好（Hellard、Marchal）。app 的 CTL 是負荷的記帳工具，不是成效的證據。
- **要看得出進步，進步幅度要大於測試誤差。** 實驗室定功率測試的典型誤差約 1–2 %，非運動員再大一些（Hopkins）。場地測試、熱、地形會更大。沒有找到 Stryd 功率的跑步 CP 場地測試的再測信度研究（搜尋結果只有跑步機臨界速度和足球員的研究），所以 app 的 CP 測試雜訊多大是**推估**；repo 既有文件用 ±3 %（`docs/research/racepower-v2.md:852`、`:872`）。
- **單一受試者要下結論，需要設計，不是事後找相關。** 最低限度是 AB 設計（先有一段基線，再開始介入），而且兩段都要重複測量（Kinugasa）。這個人的「A 段」（沒照 app 練）有訓練資料但沒有測試；「B 段」才剛開始。
- **遵從度和成效的關係有證據，但都是群體層級**（Smyth 是觀察性，Muñoz 是 30 人的試驗）。

來源之間不一致的地方：

- Hellard 報告擬合 R² 0.79，Marchal 說模型有重大統計缺陷。兩者不矛盾：前者是擬合度，後者是預測力和參數可辨識性。Hellard 自己也指出參數區間太寬。
- Muñoz 的遵從子群數字只在搜尋摘要看到，Europe PMC 的摘要沒有，引用前要讀原文。

## 5. 建議

### 5.1 最小可行的驗證設計（前瞻，AB 設計）

| 項目 | 做法 | 為什麼 |
|---|---|---|
| 結果指標（主要） | CP：固定協定的 3／12 分測試，每 6–8 週一次，都要全力 | 現在只有 1 次而且非全力；沒有測試就沒有結果可以比 |
| 結果指標（次要） | AeT 心率漂移測試（app 已有協定）；固定路線、氣溫 < 25 °C 的輕鬆跑 EF | AeT 是基礎期的目標；EF 要控制季節 |
| 比賽 | 把比賽標成賽事，存預測和實際成績 | 現在 0 筆 |
| 基線 | 現在馬上做第一次全力 CP 測試和 AeT 測試 | 沒有基線，之後的變化沒有起點 |
| 介入的紀錄 | 每週存一份「當週排了什麼」的快照，不被覆寫 | `plan_sessions` 會被重新產生覆寫（§1.3） |
| 遵從度 | 每週完成率、TSS 達成率、強度課達標率 | `backend/engine/compliance.py` 已經會算，要確認有沒有逐週存下來 |
| 混淆因素 | 每次測試記氣溫、路線、前 48 小時的負荷 | 季節效應比訓練效應大（§3.3） |

### 5.2 多久看得出效果

- CP 測試誤差用 ±3 % 算，兩次測試的差要超過約 4 %（√2 × 3 %）才不像雜訊（推估）。
- 業餘跑者 10 週的進步約 3.6–5.0 %（Muñoz，10 km 成績，不是 CP）。
- 所以**一次前後比較分不出來**。至少要 3–4 次測試（6–8 個月）看趨勢，而且要跨過一個夏天才能排除季節。
- 合理的期待：**6 個月有初步趨勢，12 個月才能說話**（推估）。

### 5.3 能回答和不能回答的問題

- 能回答：這個人照 app 的課練了 N 個月，遵從度多少，CP／AeT／比賽成績變化多少。
- 不能回答：app 的排法比其他排法好。沒有對照組，一個人也不能同時練兩種。
- 要回答「規則本身對不對」，還是要靠群體資料。`validation-lovdal.md`（護欄）和 `validation-goldencheetah.md`（心率漂移）已經做了能做的部分；`public-datasets.md` 的結論是沒有公開資料同時有越野訓練歷史和比賽成績。**沒有公開資料可以驗證「雙軌階梯」或周期設計的成效。**

### 5.4 不建議做的事

- 不要把 §3 的相關性當成證據寫進產品或文件。
- 不要用 CTL／Banister 模型的擬合度當成「排課有效」的證明。
- 不要現在做反事實重播然後下結論：它只能描述差異，而且會用到未來的閾值。

## 6. 限制

- §3 的分析只用了已經存在的快取和資料庫欄位，沒有跑引擎。低強度佔比、心率漂移、模型 CP 的歷史都沒有算。
- 沒有確認 `compliance.py` 的結果有沒有逐週存進資料庫；只確認了資料表裡沒有對應的歷史列。
- 沒有讀 `reconcile.py` 的原始碼確認「覆寫」的範圍，依據是 spec 的描述。
- wko4 的 1,012 筆舊資料沒有用。它的運動分類不完整，要先清理才能延長時間軸；而且那段期間沒有 Stryd 功率。
- 文獻只讀到摘要（Imbach 讀到章節）。Hellard 的參數區間、Hopkins 的 CV 都是摘要裡的數字。
- 沒有找到跑步功率計場地 CP 測試的再測信度研究。

## 參考

- Hellard P, Avalos M, Lacoste L, Barale F, Chatard JC, Millet GP. Assessing the limitations of the Banister model in monitoring training. *J Sports Sci* 2006;24:509–520. DOI 10.1080/02640410500244697. PMID 16608765.
- Marchal A, Benazieb O, Weldegebriel Y, Méline T, Imbach F. Statistical flaws of the fitness-fatigue sports performance prediction model. *Sci Rep* 2025;15:3706. DOI 10.1038/s41598-025-88153-7. PMID 39881202.
- Vermeire K, Ghijs M, Bourgois JG, Boone J. The fitness-fatigue model: what's in the numbers? *Int J Sports Physiol Perform* 2022;17:810–813. DOI 10.1123/ijspp.2021-0494. PMID 35320776.
- Imbach F, Sutton-Charani N, Montmain J, Candau R, Perrey S. The use of fitness-fatigue models for sport performance modelling: conceptual issues and contributions from machine-learning. *Sports Med Open* 2022;8:29. DOI 10.1186/s40798-022-00426-x.
- Kinugasa T, Cerin E, Hooper S. Single-subject research designs and data analyses for assessing elite athletes' conditioning. *Sports Med* 2004;34:1035–1050. DOI 10.2165/00007256-200434150-00003. PMID 15575794.
- Hecksteden A, Kraushaar J, Scharhag-Rosenberger F, Theisen D, Senn S, Meyer T. Individual response to exercise training – a statistical perspective. *J Appl Physiol* 2015;118:1450–1459. DOI 10.1152/japplphysiol.00714.2014.（作者名單是我記得的，引用前請再確認）
- Hecksteden A, Pitsch W, Rosenberger F, Meyer T. Repeated testing for the assessment of individual response to exercise training. *J Appl Physiol* 2018;124:1567–1579. DOI 10.1152/japplphysiol.00896.2017. PMID 29357481.
- Hopkins WG, Schabort EJ, Hawley JA. Reliability of power in physical performance tests. *Sports Med* 2001;31:211–234. DOI 10.2165/00007256-200131030-00005. PMID 11286357.
- Muñoz I, Seiler S, Bautista J, España J, Larumbe E, Esteve-Lanao J. Does polarized training improve performance in recreational runners? *Int J Sports Physiol Perform* 2014;9:265–272. DOI 10.1123/ijspp.2012-0350. PMID 23752040.
- Smyth B, Lawlor A. Longer disciplined tapers improve marathon performance for recreational runners. *Front Sports Act Living* 2021;3:735220. DOI 10.3389/fspor.2021.735220. PMID 34651125.
- 既有文件：`validation-lovdal.md`、`validation-goldencheetah.md`、`public-datasets.md`、`ctl-ramp-calibration.md`、`heat-acclimation.md`、`racepower-v2.md`、`docs/spec/plan-auto.spec.md`。
