# 登山杖：對爬升速度、心率、下坡負荷的影響，計算機要不要修正（SP-196）

> 調查日期：2026-10-06。只做調查，沒有改程式。基準 commit `47c5a6e`。
> 先讀過、只引用不重查的：`run-walk-threshold.md` §3.5（Giovanelli 2019／2022／2023／2026、Henninger／Freetrail，編號 [W16]–[W21]、[W23]）、`baiyue-mountaineering-training.md` §2.4（Howatson 2011）、`baiyue-technical-terrain.md` §1（Faulhaber 2020 的用杖比例）、`downhill-recovery.md`（下坡損傷與恢復）、`strength-session-design.md`（用杖要練的上肢肌群）。
> 標記：**已驗證（全文）**＝讀到原文；**已驗證（摘要）**＝讀到摘要；**二手**＝只看到回顧或搜尋結果的轉述；**教練級**＝教練說法，沒有研究；**推估**＝我的延伸；**未找到來源**。本文新編號用 [T1]、[T2]…；引用舊文件時寫原編號。

## 摘要

1. **上坡：杖讓人「覺得比較輕鬆」，但很少讓人「真的比較省」。** 陡坡（約 25–35°，也就是 47–70 %）用杖，垂直能量成本只少 2–3 % [W16]；自覺強度少 14–19 % [W16]。坡比較緩或平路時，用杖的攝氧量反而多約 10 % [T3]，心率常常也較高 [T2][T3]。
2. **速度：全力時快約 2.5 %，不是全力時沒有差別。** 433 m 爬升的山徑，全力用杖 18:51、不用 19:19；80 % 強度時生理和力學指標都一樣 [W17]。長距離比賽的後段，不用杖的走路成本高 2.5 % [W19]。
3. **下坡：杖明顯減輕膝蓋的負擔，也減少肌肉損傷。** 25° 下坡，地面反作用力、膝關節力矩、脛股關節受力少 12–25 % [T5]；背 15–30 % 體重時，踝和膝的力矩與吸收功率也都下降 [T6]。爬斯諾登山的隨機試驗：用杖那組隔天、後天的肌力下降比較少，痠痛和 CK 比較低 [T7]。但下坡用杖的耗氧量多 19 %，成本多 23 % [T4]。
4. **計算機不建議加「有沒有帶杖」的時間修正。** 計算機的爬坡和健行速度來自使用者自己的活動；有用杖的人，杖的效果已經在資料裡。剩下「訓練和比賽一個有杖、一個沒杖」的情況，差距約 2–3 %，比越野回測的合格門檻 8 % 小很多。
5. **下坡恢復（SP-111 的下坡升級）也不建議依杖修正。** 杖減少負荷有證據，但「少幾天恢復」沒有任何研究；而且升級本來就是「比賽 ÷ 自己最近最大的一次」，有沒有杖兩邊會一起抵消。
6. **建議只做文字層面的事**：賽事可以勾「會用登山杖」，計算機在陡坡段和下坡段加一句提示；有勾的比賽，專項期提醒要帶杖練。詳見 §5。

## 1. 現況：app 怎麼處理爬坡、心率和下坡

程式裡沒有任何「登山杖」的變數（全 repo grep `pole`、`登山杖`、`trekking` 只找到 UA 的 trekking 背包進度）。跟杖有關係的地方有三個：

| 模組 | 位置 | 現在怎麼算 | 和杖的關係 |
|---|---|---|---|
| 越野爬坡速度 | `backend/engine/racepower/grade_model.py:147–275`（`GaitRE`） | 每 2 % 坡度箱用使用者自己的 100 m 視窗；走路視窗（步頻 < 130 spm 超過一半）另外一條曲線，往 Minetti 走路成本收縮 n/(n+30) | 有用杖的人，杖的效果已經在走路曲線裡 |
| 越野整場時間 | `backend/engine/racepower/trailhr.py:11–31` | 個人的「effort km 速度 ↔ 心率」回歸 | 同上；用杖時心率可能較高，會被當成「同心率比較慢」 |
| 百岳健行速度 | `grade_model.py:314–359`（`HikeSpeed`，往 Tobler 收縮）、`backend/engine/racepower/hike.py:1–24`（地形係數、背包） | 個人坡度速度 × 背包 × 海拔 × 地形 η | 同上 |
| 百岳陡坡心率視窗 | `backend/engine/racepower/hikehr.py:1–35` | 只取心率 ≥ AeT、坡 ≥ 10 % 的 100 m 視窗算 VAM | 用杖時同 VAM 心率可能高一點 |
| 健走的心率上限 | `backend/engine/hr_profile.py:369–386`（75 % HRmax）、`backend/engine/target_policy.py:22–24` | 陡坡健走、登山上坡看心率 ≤ 75 % HRmax 或 RPE ≤ 13 | 用杖時同速度心率可能較高 |
| 下坡升級 | `backend/engine/downhill_recovery.py:1–17`、`backend/engine/algorithms/chart_metrics.py:155–178`（下坡衝擊等效 km）、`backend/engine/planning.py:391–445`（門檻 1.5） | 比賽的下坡衝擊 ÷ 自己近 6 週最大一次；≥ 1.5 時恢復升一級 | 杖不在公式裡；但兩邊都用同一個人、同一種習慣 |

## 2. 文獻

### 2.1 上坡：攝氧量、心率、自覺強度

| 研究 | 對象與條件 | 結果 | 等級 |
|---|---|---|---|
| Giovanelli 2019 [W16] | 14 名山地跑者，跑步機 10.1°–38.9°，固定垂直速度（最大的 80 %） | 垂直成本在 25.4°、29.8°、35.5° 低 2.6 %、2.8 %、2.0 %；其他坡度沒差。自覺強度在 15.5° 以上多數坡度低 14–19 %。步頻 −6.7 %、步幅 +8.6 %。作者：「only slightly more economical … poles may delay fatigue effects during a prolonged effort」 | 已驗證（全文，這次重讀） |
| Knight & Caldwell 2000 [T1] | 跑步機上坡、背 22.4 kg（30 % 體重） | 攝氧量沒有差別；心率 113.5 對 107 bpm（用杖較高）；自覺強度 10.8 對 11.6（用杖較低）；步幅 1.27 對 1.19 m；下肢多條肌肉的肌電較低 | 二手（搜尋結果引述摘要；PubMed 頁面要 cookie） |
| Perrey & Fabre 2008 [T4] | 12 人，+15 %、0、−15 %，背 15 % 體重，自選速度 | 上坡、平路的攝氧量沒有差別；心率「was not influenced by the use of poles」；自覺強度 10.2 對 9.9（沒差）；用杖步頻較低 | 已驗證（全文） |
| Saunders 2008 [T3] | 14 名休閒健行者，不同坡度，速度相同 | 攝氧量 1,502.9 對 1,362.4 ml/min（用杖多約 10 %，我的換算）；「higher … heart rate in the pole-condition at all grades」；自覺強度沒變 | 已驗證（摘要） |
| Foissac 2008 [T8] | 11 人，20 % 坡，3 km/h | 下肢肌電少約 15 %，上肢多約 95 %；杖重 240–360 g 不影響攝氧量 | 已驗證（摘要） |
| Henninger／Freetrail 2022 [W21] | 教練整理（作者是 [W5] 的共同作者） | 6° 以下沒有代謝好處；20° 以上自覺強度和垂直成本較低 | 既有文件，已驗證 |
| Saller 2023 回顧 [T9] | 各運動的用杖研究 | 健行用杖：攝氧量都上升；「no single investigation reported an increase in RPE」；心率傾向較高；足底壓力和地面反作用力都下降 | 已驗證（全文） |
| Koop 2025 [T10] | 教練文章 | 用杖大多增加耗氧；「metabolic crossover」大約在 26 % 坡；「if you are going to use poles in a race, you better train with them」 | 教練級。擷取結果寫「26 % grade」，和 Giovanelli 的 25°（約 47 %）不一致，可能是單位寫錯，原文沒有核對 |

重點：

- **能量：只有很陡的坡省一點點**（2–3 %，25–35°）[W16]；緩坡和平路多耗 10–20 % [T3]（Giovanelli 2019 引言也寫平路用杖「~20 % greater」）。
- **心率：不一致。** 兩篇較高 [T1][T3]，兩篇沒差 [T4][T7]。方向上「用杖不會讓心率變低」，有時高幾 bpm（推估）。
- **自覺強度：一致地較低或不變**，沒有一篇變高 [T9]。
- **腿：下肢肌肉用得少，上肢用得多** [T1][T8]。這是「腿比較不累」的機制，也是 Koop 說要先練的原因 [T10]。

### 2.2 上坡：速度與比賽

| 研究 | 條件 | 結果 | 等級 |
|---|---|---|---|
| Giovanelli 2022 [W17] | 1.3 km、爬 433 m 的山徑（約 19°） | 全力用杖 18:51、不用 19:19（快 2.5 %，80 % 的人變快）；80 % 強度時沒有差別 | 既有文件，摘要 |
| Giovanelli 2023 [W18]、2026 [W19] | 跑步機陡坡；31 km 模擬賽前後 | 用杖時腳的受力較小；31 km 後，18.6° 坡不用杖的走路成本高 2.5 % | 既有文件，摘要 |

- 快 2.5 % 只在**全力短爬坡**看得到 [W17]。超馬多數時間不是全力，80 % 強度時沒有差別。
- 長賽後段的差距也是 2.5 % [W19]。

### 2.3 下坡：膝蓋負荷、肌肉損傷、耗能

| 研究 | 條件 | 結果 | 等級 |
|---|---|---|---|
| Schwameder 1999 [T5] | 8 名男性，25° 斜坡下走 | 地面反作用力、膝關節力矩、脛股關節的壓力和剪力的峰值與平均值都少 12–25 %；原因是杖分擔了力、上身前傾讓膝的力臂變短 | 已驗證（摘要） |
| Bohne & Abendroth-Smith 2007 [T6] | 15 名有經驗的男性健行者，背 0／15／30 % 體重 | 各關節矢狀面力矩「significant reduction」，踝和膝的峰值吸收功率下降，三種背包重量都一樣 | 已驗證（摘要） |
| Daviaux 2013（轉引自 [T9]） | 下坡 | 腳跟受力少 14.2 % | 二手 |
| Howatson 2011 [T7] | 37 人隨機分兩組，爬斯諾登山上下，背 5.6 ± 1.5 kg | 用杖組：下山後立刻、24、48 小時的最大肌力下降較少；24、48 小時痠痛較低；24 小時 CK 較低；上坡自覺強度較低；心率沒有差別；垂直跳沒有差別 | 已驗證（摘要） |
| Perrey & Fabre 2008 [T4] | −15 %，背 15 % 體重 | 用杖時攝氧量多 19 %、能量成本多 23 %，呼吸頻率和通氣量也較高 | 已驗證（全文） |

- **負荷：減少 12–25 %，有三篇一致** [T5][T6]、Daviaux。
- **損傷：只有一篇隨機試驗** [T7]，對象是一般活動量的人、輕背包、一天的登山，不是越野跑者、不是超馬。
- **代價：下坡用杖比較耗能** [T4]。下坡本來就不是代謝限制（Minetti 2002，見 `racepower-v2.md` §2.4），所以這個代價對速度的影響小（推估）。
- **未找到來源**：用杖之後「恢復要少幾天」的研究；越野「跑」下坡（不是走）用杖的研究。

### 2.4 跌倒

- Faulhaber 2020：跌倒受傷的健行者裡 58 % 有用杖（`baiyue-technical-terrain.md` §1）。這是描述性資料，沒有「沒受傷的人有多少用杖」，不能說杖增加或減少跌倒。
- **未找到來源**：用杖對山上跌倒率的對照研究。

### 2.5 哪些坡度和距離值得用

依上面的證據整理（**推估**，每一條附依據）：

| 情境 | 建議 | 依據 |
|---|---|---|
| 平路、緩坡（< 6°，約 10 %） | 不用，收起來 | 沒有代謝好處 [W21]；平路多耗 10–20 % [T3] |
| 可跑的坡 | 不用 | 用杖要走路；可跑的坡跑比較快 |
| 陡坡走路（≥ 15°，約 27 %） | 用 | 自覺強度低 14–19 % [W16]；≥ 25° 省 2–3 % [W16]；全力快 2.5 % [W17] |
| 長下坡、背包、累了之後 | 用 | 膝負荷少 12–25 % [T5][T6]；損傷較少 [T7] |
| 長賽後段 | 用 | 不用杖的成本高 2.5 % [W19]；下肢用得少 [T1][T8] |
| 比賽第一次用 | 不建議 | 上肢要先練過 [T10]（教練級） |

## 3. 落差

| 問題 | app 現況 | 證據 | 落差大不大 |
|---|---|---|---|
| 爬坡速度要不要依杖修正 | 用個人資料 | 差 0–2.5 %，看強度 [W17][W19] | 小。個人資料已吸收；只有「訓練和比賽習慣不同」時差 2–3 %（推估），遠小於回測門檻 8 %（`backtest.py:75`） |
| 心率上限要不要依杖修正 | 健走 75 % HRmax、越野 AeT | 心率有時高幾 bpm [T1][T3]，有時沒差 [T4][T7] | 小，而且方向不一致；不改 |
| 心率模型 | 同心率速度 | 用杖心率可能較高 → 模型會以為「同心率較慢」 | 小；用杖若是固定習慣，訓練與比賽一致就會抵消（推估） |
| 下坡恢復要不要依杖減少 | 比值門檻 1.5，升一級 | 負荷少 12–25 %、損傷指標較低 [T5][T6][T7]；沒有恢復天數的研究 | 不能定量；而且比值是「比賽 ÷ 自己」，有杖的習慣兩邊一起抵消 |
| 訓練要不要提醒帶杖練 | 沒有 | 教練說比賽要用就要先練 [T10]；上肢肌群（`strength-session-design.md`） | 有落差，但只是文字 |

## 4. 結論

1. **計算機不加時間修正。** 理由：個人資料已經包含杖的效果；剩下的差距（2–3 %）在模型誤差之內；杖的速度好處只在全力時出現 [W17]，比賽多半不是全力。
2. **下坡恢復不依杖修正。** 理由：有負荷的證據，沒有恢復天數的證據；比值設計已經讓同一種習慣抵消；保守一點對恢復比較安全。
3. **心率上限不改。** 證據方向不一致。
4. **可以做的是提示**：陡坡段和長下坡段建議用杖；比賽要用杖，專項期就要帶杖練。

## 5. 建議開的實作單

| # | 標題 | 優先度 | 驗收條件草案 |
|---|---|---|---|
| 1 | 賽事「會用登山杖」選項＋計算機分段提示 | P3 | ① 賽事編輯多一個勾選「會用登山杖」，預設不勾。② 有勾時，計算機分段表在「陡坡（走）」段（`seg_targets.kind_of` = steep_climb）和坡度 ≤ −15 % 的下坡段，多一句提示：上坡「用杖：自覺比較輕鬆，速度差不多」、下坡「用杖：膝蓋負擔少 12–25 %」，附來源。③ 預估時間、配速、心率目標**完全不變**（測試：勾與不勾的 `time_s` 相同）。④ 平路與可跑段不顯示。⑤ 英文翻譯同步 |
| 2 | 專項期提醒帶杖練 | P3 | ① 依賴 #1。目標賽事勾了「會用登山杖」時，專項期的陡坡健走、長爬坡反覆、攻頂日模擬的說明多一句「帶杖練，比賽才用得順」。② 沒勾時不出現。③ 只改說明文字，不新增課表、不改時數 |
| 3 | 活動標籤「有帶杖」（為之後的個人比較留資料） | P3（可不做） | ① 活動標籤可選「有帶杖」，和現有的補給標籤同一套自由標籤（`trailhr.fuel_of` 的做法）。② 這張只存標籤，不進任何模型。③ 之後如果有 ≥ 5 次有杖、≥ 5 次沒杖的陡坡活動，再評估要不要像補給一樣分組 |

**不建議開**：計算機的「帶杖時間修正」、下坡升級依杖減少恢復天數、用杖時改心率上限。理由見 §4。

## 6. 需要使用者決定的事

1. **#1、#2 要不要做？** 只是文字，好處是提醒；壞處是多一個賽事欄位。我建議做 #1，#2 看 #1 上線後再決定。
2. **#3 要不要做？** 目前沒有用途，只是留資料。我建議先不做，等真的想比較時再開。
3. **計算機真的不要修正嗎？** 我建議不要。如果你常常「訓練沒帶杖、比賽帶杖」，可以重新考慮做一個 0–3 % 的手動選項，但證據只有一篇全力短爬坡 [W17]。

## 7. 限制

- 多數研究是跑步機、短時間、走路；越野「跑步」用杖幾乎沒有研究。
- 下坡損傷只有一篇隨機試驗 [T7]，對象是一般人、輕背包、一天。
- 沒有用任何人的活動資料驗證（交接規定不讀個人資料）。
- 研究多半是年輕男性或山地跑者。

## 8. 讀不到的來源

- Knight & Caldwell 2000 [T1]：PubMed 頁面只回 cookie 提示；數字來自搜尋結果的摘要引述，沒有讀到原摘要。
- Hawke & Jensen 2020〈Are trekking poles helping or hindering your hiking experience? A review〉*Wilderness Environ Med*：只看到題名，沒有讀。https://journals.sagepub.com/doi/10.1016/j.wem.2020.06.009
- Giovanelli 等 2026（用杖對 31 km 模擬賽前後垂直成本與足部受力，*Eur J Appl Physiol*，https://link.springer.com/article/10.1007/s00421-025-05881-4）：Springer 要登入，沿用 `run-walk-threshold.md` [W19] 的摘要。
- 〈The Effect of Mountaineering Pole Use on Respiratory Muscle Fatigue During Hiking〉*Appl Sci* 2026（https://doi.org/10.3390/app16031593）：MDPI 回 403，沒有讀。
- Koop 2025 [T10] 引的 Church 2002、Duncan 2008、Hansen 2009、Pellegrini 2015／2018：只看到 Koop 的轉述。

## 參考文獻

- [T1] Knight CA, Caldwell GE. Muscular and metabolic costs of uphill backpacking: are hiking poles beneficial? *Med Sci Sports Exerc* 2000. PubMed 11128857。https://pubmed.ncbi.nlm.nih.gov/11128857/
- [T2] 同 [T1]（心率數字）。
- [T3] Saunders MJ, et al. Trekking poles increase physiological responses to hiking without increased perceived exertion. *J Strength Cond Res* 2008. https://doi.org/10.1519/jsc.0b013e31817bd4e8 （PubMed 18714242）
- [T4] Perrey S, Fabre N. Exertion during uphill, level and downhill walking with and without hiking poles. *J Sports Sci Med* 2008;7:32–38. https://pmc.ncbi.nlm.nih.gov/articles/PMC3763349/
- [T5] Schwameder H, Roithner R, Müller E, Niessen W, Raschner C. Knee joint forces during downhill walking with hiking poles. *J Sports Sci* 1999;17:969–978. https://doi.org/10.1080/026404199365362 （PubMed 10622357）
- [T6] Bohne M, Abendroth-Smith J. Effects of hiking downhill using trekking poles while carrying external loads. *Med Sci Sports Exerc* 2007. https://doi.org/10.1249/01.mss.0000240328.31276.fc （PubMed 17218900）
- [T7] Howatson G, et al. Trekking poles reduce exercise-induced muscle injury during mountain walking. *Med Sci Sports Exerc* 2011. https://doi.org/10.1249/mss.0b013e3181e4b649 （PubMed 20473229）
- [T8] Foissac MJ, et al. Effects of hiking pole inertia on energy and muscular costs during uphill walking. *Med Sci Sports Exerc* 2008. https://doi.org/10.1249/mss.0b013e318167228a （PubMed 18460993）
- [T9] Saller M, et al. A review of biomechanical and physiological effects of using poles in sports. *Bioengineering (Basel)* 2023;10:497. https://doi.org/10.3390/bioengineering10040497 ；https://pmc.ncbi.nlm.nih.gov/articles/PMC10135831/
- [T10] Koop J. The science behind using trekking poles in trail and ultrarunning. TrainRight，2025-03-07。https://trainright.com/science-trekking-poles-trail-running-ultrarunning/
- 既有文件：[W16] Giovanelli 2019（https://doi.org/10.1007/s00421-019-04145-2，這次重讀全文）、[W17]、[W18]、[W19]、[W21]，見 `run-walk-threshold.md`；Faulhaber 2020，見 `baiyue-technical-terrain.md`。
