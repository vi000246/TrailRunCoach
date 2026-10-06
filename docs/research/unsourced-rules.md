# app 裡還沒有研究佐證的規則：研究結果

> 2026-10-01。回覆 `unsourced-rules-brief.md`。只做研究，不改程式碼。
>
> 標記：**已驗證**（本次讀過原文或摘要，附 DOI／URL）、**未驗證**（有引用但沒讀到原文）、**未找到來源**、**推估**；每條外部說法標 **同儕審查** 或 **教練／平台／廠商**。已在 `racepower-v2.md`（下稱 RP2）、`aerobic-base-readiness.md`（ABR）、`interval-adaptation.md`（IA）、`heat-acclimation.md`（HEAT）、`baiyue-from-running.md`（BY）、`drift-algorithm.md`（DRIFT）驗證過的來源直接引用，不重查。
>
> **資料**：
> - app DB（`~/.wko5coach/wko5coach.db`，唯讀開啟）：讀取時是**一年份**的資料，233 筆（17 筆跨來源重複）。COROS 跑步 178（路跑 144、越野 34、未分類 37）、登山 5、其他 32；TP 跑步 17。≥ 90 分鐘的跑步 18 次。**另一個 session 正在補同步完整歷史**：寫完本文時已到 467 筆、約兩年份。本文的回測（表 2）是在補同步前跑的。
> - 回測用的「7 場日記比賽」（訓練日記裡記錄的比賽）有 4–5 場在更早的歷史裡，補同步後才會進 app DB；§0.3 說明怎麼用。
> - `FitFolderDataset`（TP／COROS 來源）目前仍從 **WKO5 athlete 檔讀閾值和體重**（`fitdataset.py:22`、`:169`，`settings_dir=wko5_dir`）。要讓校正不依賴 WKO5，閾值要改從賽季計畫的 dated 閾值列和 TP `athlete_settings` 來（§0.3 第 0 步）。TP 的 `athlete_settings` 目前只有一列，裡面的 LTHR 比 app 用的值高出 25 bpm 以上，對不上，要先確認那是哪個運動的值。
> - 本次對 COROS／TP 來源各跑了一次 `racepower_backtest --no-save --source coros|tp`（結果見 §0.1 表 2；WKO5 來源的數字引自 `racepower.spec.md`）。

---

## 總表：A、B 各最該先改的 3 項

| 順位 | 項目 | 為什麼先改 | 要做什麼 |
|---|---|---|---|
| A-1 | **A2 能力樣本／「全力」判定** | 它決定哪些跑步能校正能力模型，也是 §0 誤差最大的來源（歷史前段的 LTHR 是 WKO5 預設 160，讓其中一場比賽誤差 −27%）。文獻（Fornasiero 2018、Kerhervé 2015）顯示越野比賽的心率水準隨時長下降，單一門檻 0.90 × LTHR 對 2 小時和 12 小時不能一樣 | 補同步完整歷史、閾值 as-of 從訓練資料重估；「全力」改成 x*(T)：心率／LTHR 對比賽時長的個人曲線（§A2） |
| A-2 | **A3 耐疲勞** | δ 卡在 0.15/h 上限，加了反而讓 7 場比賽的誤差從 5.5% 變 7.6%。文獻給得出先驗（Clark 2019：2 小時後 CP −9 ～ −11%、W′ −20%） | δ 用先驗 0.05/h 收縮，只在 DRIFT 清過的視窗上量；補給有無當共變量（§A3） |
| A-3 | **A8 熱適應指數** | 回測資料不支持熱適應效果（最佳 a = 0），但計算機讓使用者選「已適應 S 0.9」就少算熱懲罰。這是沒有證據的樂觀 | 預設 a = 0（顯示 S，但不折抵熱懲罰），直到 HRC 斜率檢定為負（§A8） |
| B-1 | **B3 AeT 的有效期與測試週期** | 16 週、4–6 週都沒有來源；品質門檻（UA 差距法）整個掛在「AeT 新不新」上。DRIFT §5.4 已經給出替代：多次聚合的 AeT 估計帶標準誤 | 「有效」改成「聚合估計的 SE ≤ 3 bpm 且最近 6 次沒有系統性偏移」；測試週期改成「SE 超標才排測試」（§B3） |
| B-2 | **B4 間歇進階狀態機** | IA 已指出「每做一次就推進」和「掉 5% 就退」都和它引用的講者相反；60 秒心率規則只能當煞車。這是每週都在用的規則 | 照 IA §4.3：達標才進階；順序「加組 → 拉長／縮休 → 加功率」有描述性支持（Casado 2022、Seiler 2013），沒有直接比較；上限 8 組／16 分標推估（§B4） |
| B-3 | **B2 護欄數字** | 週增量 10／20% 和 CTL ramp 5／7 有可用的來源，但現在引的不對：Nielsen 2014、Damsted 2019 支持的危險線是 20–30%，不是 10%；Friel 的 ramp 是 5–8、上限 10 | 週增量：> 20% 擋、10–20% 維持（維持現狀，改標來源）；CTL ramp：改 5（注意）／8（擋）；TSB −20／−30 標 Friel（教練）（§B2） |

---

## 0. 最優先：怎麼提高賽事預測的準度

### 0.1 現況（表 1、表 2）

表 1：2026-10-01 的回測（WKO5 來源，引自 `racepower.spec.md`）：

| 模型 | 樣本 | 中位 \|誤差\| | 偏差 | 備註 |
|---|---|---|---|---|
| 越野心率模型，7 場日記比賽，race level | 7 | 7.6% | −0.9% | 不含耐疲勞 5.5%；驗證標準 6% |
| 同上，given HR | 7 | 9.9% | | 不含耐疲勞 8.8% |
| 越野心率模型，全部越野跑，given HR | 41 | 7.3% | | |
| 越野功率包絡 | 2–4 | 35–41% | −35% | 已降為對照 |
| 地形模型（mode B，實際功率） | 路跑 139／越野 36 | 3.7%／10.3% | −3.6%／−9.2% | 越野下坡偏快 +13%；≤ −15% 坡 +20–23% |
| 路跑能力樣本 | 1–2 | 6.5–13% | | 無法驗證 |

表 2：同一支腳本對 app DB 的 COROS／TP 來源（本次執行，`--no-save`，執行時 DB 還是一年份、沒有努力度標記）：

| 來源 | 案例 | 地形模型 路跑 | 下坡偏差 | 能力模型 路跑 | 越野（地形、能力、心率模型） | CP 測試 |
|---|---|---|---|---|---|---|
| COROS | 175（輕鬆 18／穩定 135／比賽強度 21） | n 174，3.4%，偏差 −3.4%；輕鬆 9.0%、穩定 3.4%、比賽強度 3.4% | +10.8% | n 6（自動規則 5 ＋ 計畫賽 1），**14.5%、偏差 −14.5%、f 中位 0.87**：沒有使用者標記時，自動路跑規則又放進 5 次非全力跑 | **n = 0** | 1 次，−3.6% |
| TP | 18（穩定 7／比賽強度 10 ＋ 1 CP bout） | n 17，2.7% | +1.8% | n 1，7.8% | n = 0 | 同一次 |

- **越野 n = 0 的原因**：`FitFolderDataset.sport_of()`（`fitdataset.py:61`）只在 FIT 的 sub_sport 是 trail／treadmill／track 時才分類；COROS 的檔案沒有這個欄位，DB 裡 `workout_files.trail_classification` 的 34 筆越野沒有被 dataset 讀。**在修好之前，A3、A4、A7、§0 的越野部分都無法用 app 資料校正**（§0.10 第 0 步）。
- 努力度標記在 app DB 裡沒有表（`seed_activity_tags` 寫的是另一個 DB），所以 COROS 的能力樣本回到了「自動規則」的 14.5%——這正是 A2 排第一的理由。
- HR capacity 在 COROS 上同樣無效（斜率 ≤ 0、R² 0.004，26 次）。

### 0.2 已知的誤差來源，用證據排序

| 來源 | 證據 | 影響 |
|---|---|---|
| **閾值錯**：歷史前段很多天 LTHR 是 WKO5 預設 160 | 其中一場比賽 x = 1.11、誤差 −27%；`thresholds_as_of` 在沒有 plan 列、估算也不夠時退回 WKO5 設定 | 單場可到 20–30 個百分點；7 場中 3–4 場受影響 |
| **樣本太少** | 7 場比賽、路跑 1–2 筆 | 中位數本身的 80% 信賴區間約 ±2–3 個百分點（bootstrap，7 筆）；6% 門檻在這個 n 上分不出 5.5 和 7.6 |
| **耐疲勞 δ 卡上限** | δ 的中位數 ≥ 0.15/h，加了反而變差 | 1–2 個百分點（5.5 vs 7.6） |
| **「憑感覺跑」** | 比賽紀錄上的自述；given-HR 誤差 9.9% vs race-level 7.6% 說明 HR 水準本身有 2 個百分點的雜訊 | 2–3 個百分點 |
| **非移動時間** | 7 場比賽 22–48% 的時間「不移動」（速度規則），但 ≥ 5 分鐘的停頓只佔 0–5% | 看預測的是移動還是總時間；目前兩者定義沒分清楚（§0.7） |
| **地形：下坡技術性** | 越野 ≤ −15% 坡模型快 20–23% | 越野地形模型的主要偏差；對心率模型間接影響（effort km 沒有下坡項） |
| **熱** | 回測資料（一位跑者）β = 0.224 ± 0.036 bpm／Hadley 單位（HEAT 回測） | 比賽日 Hadley 150 vs 訓練 120 → 心率 +7 bpm ≈ x +0.045，如果沒修正，時間誤差約 5%（用 §A2 的斜率換算，推估） |

### 0.3 問題 1：越野完賽時間預測的文獻方法

| 方法 | 原理 | 準度（文獻） | 個人化、小樣本下 | 類別 |
|---|---|---|---|---|
| **ITRA performance index** | 每場成績對「該賽道理論最佳」給 0–1000 分（距離、爬升、下降、平均海拔），指數 = 36 個月內最佳 5 場的加權平均 | 官方沒公布預測誤差；第三方（vert.run、trailmath）用「同指數的人在同賽道的時間」查表 | 需要 ≥ 3–5 場有 ITRA 紀錄的比賽；回測的跑者只有 7 場、多半不是 ITRA 認證賽 | 組織／平台，已驗證描述（https://itra.run/Runners/Performance ）；誤差**未找到來源** |
| **UTMB index** | 同類 | 同上 | 同上 | 平台（`effort-distance-formulas.md` §1） |
| **effort km／等效距離** | km + D+/100（ITRA）；app 用 km + D+/153（個人擬合） | 就是 app 的心率模型基礎：7.3–7.6% | 已個人化 | 慣例＋推估 |
| **Minetti 坡度成本 → 代謝功率 → 時間** | 每段用 Cr(i) 換算 | app 地形模型：路跑 3.7%、越野 10.3% | 需要個人 RE(g)；下坡技術性是主要漏洞 | 同儕審查（Minetti 2002）＋推估 |
| **個人化 GAP** | 個人坡度–速度表 | 同上（grade_model） | 同上 | 推估 |
| **實驗室指標回歸** | VO2max、最大功率預測完賽時間 | Fornasiero 2018：65 km／D+ 4000 m，23 名業餘越野跑者，實驗室指標只解釋 **59%** 的完賽時間變異（DOI 10.1080/02640414.2017.1374707） | 橫斷面、跨人；不是個人預測 | 同儕審查，已驗證 |
| **機器學習（族群）** | TRAP：ITRA 歷史 ＋ UTMB 檢查點，預測下一檢查點時間與區間（Fogliato, Oliveira & Yurko 2020, arXiv 2002.01328，預印本；摘要沒給誤差）；XGBoost 加天氣：36,700 筆，MAPE 10.05% → 8.50%（IJADIS 期刊，**未驗證**，非主流期刊） | **8–10% MAPE** 是族群模型的典型水準 | 需要大量他人資料；對單人沒有幫助 | 預印本／未驗證 |
| **賽中即時預測** | 跑完前 1/3 後用檢查點名次與配速變異回歸 | Gutiérrez et al. 2025, *Sports* 13:385, DOI 10.3390/sports13110385：947 人，adjusted R² > 0.95 | 只能賽中用，賽前不行 | 同儕審查，已驗證 |
| **CP／CS 從訓練資料** | 從訓練 GPS 的 mean-max 估 CS | Smyth & Muniz-Pumares 2020（RP2 §12）：訓練資料的 CS 和計時賽相當；全馬在 84.8 ± 13.6% CS | 路跑可行（app 已做）；越野功率包絡失敗（+46%）是因為越野的「全力」不在功率上 | 同儕審查 |

**結論**：文獻的族群方法在 8–10%，實驗室指標只解釋六成；app 的個人心率模型 7.3–7.6% **已經在文獻水準**。要到 6% 以下，不是換模型，是修輸入：閾值、努力度標籤、非移動時間、耐疲勞的先驗。

### 0.4 問題 2：樣本極少時怎麼個人化

- **已經在做的**：RE(g) 的 n/(n+30) 收縮到 Minetti 先驗；海拔 α 的精度加權收縮到 Wehrlin；γ 收縮到 0。這就是經驗貝氏／階層模型在 n = 1 位運動員時的做法：**先驗來自文獻，資料只調整它**。
- **還沒做的**：
  1. 耐疲勞 δ 沒有先驗，直接用中位數再夾在 0–0.15：先驗見 §A3。
  2. 越野心率模型 v₀(x) = a + b·x 是 ≥ 6 次 OLS，沒有先驗；b 可以收縮到地形模型的隱含斜率（同一條路線上心率每升 1% LTHR，effort km 速度升多少），推估。
  3. 比賽 HR 水準 x* 是中位數，沒有對時長的結構（§A2）。
- **避免過度擬合**：參數數 ≤ n/5（7 場 → 1–2 個自由參數）；每個參數都要有 LOO 的證明（加了參數 LOO 誤差要降才留；目前 δ 就是沒過這關）；訓練跑當弱標籤（41 次 given-HR）只拿來校正 b 和 δ，不拿來校正 x*。
- 文獻：沒有找到「單一運動員、< 10 場比賽」的越野預測研究；上面是統計常識，不引 DOI。

### 0.5 問題 3：過去閾值不準時怎麼重建

| 閾值 | 方法 | 可信度 | 類別 |
|---|---|---|---|
| **CP／CS** | 從訓練資料的 mean-max 曲線擬合（app 的 PD refit as-of；Smyth & Muniz-Pumares 2020 證明訓練資料的 CS 和計時賽相當） | 好，只要那段時間有 3–20 分鐘的硬跑 | 同儕審查 |
| **LTHR** | Friel 定義：30 分鐘獨跑計時的最後 20 分平均心率（教練）；app 的 `thresholds.estimate`：≥ 6 次 ≥ MIN_EFFORT_OF_CP 強度的 30 分鐘段，取最後 20 分心率的中位數（推估，照 Friel 定義做） | 中等：取決於那段時間有沒有 ≥ 30 分的硬跑；夏天的心率會偏高（HEAT β） | 教練＋推估 |
| **LTHR（次大）** | Lamberts LSCT（Lamberts 2011, *Br J Sports Med* 45:797–804，程式已引，本次未重讀）：固定 %HRmax 三階段的功率 | 需要做測試，不能回溯 | 同儕審查（未驗證） |
| **AeT** | DRIFT §5.4：多次飄移聚合的回歸 | 回溯可行（每次跑步都有飄移點） | 推估 |
| **HRmax** | 觀測：365 天內每次 ≥ 120 秒的峰值取前 5 的中位數（`maximal.hrmax_observed`） | 好 | 推估 |

做法：補同步 TP 和 COROS 的完整歷史（正在進行；COROS 能補的歷史有限，更早的要靠 TP），對每一天重算 as-of 的 CP／LTHR／AeT，只用當天以前的資料；對照 WKO5 當時的設定。早期的硬跑足夠時，LTHR 估計會把 160 換掉。**可信度要用最近一次 3′/12′ 測試驗證**：測試日 as-of 的估計 LTHR 應該落在 12′ 的峰值心率減去合理差距附近；如果估計比這個值低 15 bpm 以上，方法本身偏低（spec 已懷疑這點）。

### 0.6 問題 4：越野長距離的耐疲勞

文獻（都是同儕審查，已驗證）：

| 來源 | 發現 |
|---|---|
| Maunder, Seiler, Mildenhall, Kilding & Plews 2021, *Sports Med* 51:1619–1628, DOI 10.1007/s40279-021-01459-0 | 定義 durability = 生理指標在長時間運動中「開始惡化的時間點和幅度」；呼籲用能描述這種漂移的模型，沒有給測量標準 |
| Clark et al. 2019, *J Appl Physiol* 127:726–736, DOI 10.1152/japplphysiol.00207.2019 | 2 小時重度強度騎車後 end-test power **−9%**，W′ 大幅下降；補碳水化合物能**抵銷 EP 的下降**，對 W′ 無效 |
| Clark et al. 2019, *Am J Physiol Regul* 317:R59–R67, DOI 10.1152/ajpregu.00031.2019 | 同設計：CP **−11%**、W′ **−20%**；和肌肝醣消耗關係不大 |
| Smyth 2022（ABR §2.7） | 82,303 名全馬跑者 decoupling 中位 1.16，出現在 25.2 km |
| Teso 2025（DRIFT §2.3） | 中等強度的心率慢成分約 0.55 bpm/min（腳踏車） |
| Kerhervé, Millet & Solomon 2015, *PLoS ONE* 10:e0145482, DOI 10.1371/journal.pone.0145482 | 106 km／D± 5871 m，15 人，18.3 ± 3.0 h：跑得快的人**停得少、減速反而多**（速度損失和成績正相關 r = 0.54）；平均速度和總停留時間 r = −0.77 |

對 app 的意思：
- **先驗**：以 Clark 的「2 小時重度強度後 CP −10%」當起點，換成「第 2 小時起每小時 −5 ～ −10%」；在比賽強度低於重度（越野多在 VT1 以下，Fornasiero）時應該更小。建議 δ 先驗 **0.05/h**，收縮 n/(n+3)（推估，n = 可用的長跑數）。目前的 0.15 上限其實是「資料給的 δ 太大」的警訊：量 δ 的長跑裡有停等、走路、回程，這些都會讓「同心率的 effort-km 速度」掉下來。
- **量測視窗**：用 DRIFT §8.1 的視窗規則（去掉停等群、尾段、走路段）再量 δ；只在移動時間上量（trailhr 已經是）。
- **補給共變量**：Clark 說碳水化合物抵銷 CP 的下降；如果使用者在活動上標「有補給／沒補給」，δ 可以分兩組。沒標就不分。
- **只有幾場比賽時**：δ 不從比賽估，從 ≥ 2 小時的訓練長跑估（弱標籤），比賽只用來驗證。

### 0.7 問題 5：「憑感覺跑」的比賽怎麼用

- 心率模型本來就能用「這場的心率」預測（given HR：41 次 7.3%）。問題是把它換成「全力時應該跑多快」需要 x*，而 x* 隨時長變：
  - Fornasiero 2018：65 km／11.8 h 的平均強度 **77.1 ± 4.4% HRmax**，85.7% 的時間在 VT1 以下。
  - Kerhervé 2015：106 km，各坡度的心率從 10% 進度到 70–90% 進度顯著下降。
  - 路跑：Smyth 2020 全馬 84.8% CS；Jones et al. 2021, *J Appl Physiol* 130:369–379, DOI 10.1152/japplphysiol.00647.2020：2 小時全馬配速 = 94 ± 3% VO2peak，高於乳酸轉折點（菁英）。
- 提案（推估）：**x*(T) = 心率／LTHR 對比賽移動時長的曲線**，形狀取對數線性 x* = x₀ − s·ln(T/1 h)，用使用者標記「全力」的比賽擬合 x₀、s；文獻錨點當先驗：T = 0.5 h → ≈ 1.00（5K 的最後 1/4 ≥ LTHR，程式現有規則）、T = 3 h → ≈ 0.90（Friel Z3 下緣）、T = 12 h → ≈ 0.85（77% HRmax ÷ 約 90% HRmax 的 LTHR；換算推估）。
- 「憑感覺」的比賽：給 x（實際）和 x*(T)，差值就是「保留了多少」；預測全力時間 = 在 x*(T) 上算。這些比賽**不當能力樣本**（不是全力），但可以校正 b（心率→速度斜率）。
- 標籤來源：使用者的努力度標記（`feat/activity-tags`）；沒標時用 x ≥ x*(T) − 0.03（推估）當自動規則，取代固定的 0.90。

### 0.8 問題 6：非移動時間

- 文獻：Kerhervé 2015 只說「停得少的人快」，沒報比例；UTMB／西部 100 的檢查點研究看的是分段速度，不是停留。補給站停留時間和賽事規模的關係：**未找到來源**。
- 回測資料（spec）：7 場比賽 22–48% 的時間「不移動」（速度 ≤ 門檻），但 ≥ 5 分鐘的停頓只有 0–5%。差額是陡坡慢行、GPS 掉速、短暫排隊——**這些不是補給站，是「移動定義」的問題**。
- 提案：
  1. 預測目標分成兩塊：**移動時間**（模型）＋ **停留時間**（另一個小模型）。移動的定義在訓練和比賽要一樣：陡坡用步頻判斷「在動」，不用速度（`hikehr` 已用 1.5 km/h；DRIFT 用 1.6 km/h；建議統一成「速度 > 1.5 km/h 或步頻 > 0」，推估）。
  2. 停留時間 = 每個補給站的中位停留（本人歷史，依賽事類型）× 站數 ＋ 每小時的零碎停留；給 p25／p75 當區間。資料：每場比賽 FIT 裡 ≥ 60 秒的停頓，對應補給站位置（GPX 的 wpt）。
  3. 準度：這部分的誤差直接加在總時間上；先量 7 場的停留時間變異，再決定要不要讓使用者填「預計每站停幾分鐘」（RP2 §11 第 10 題）。

### 0.9 問題 7：驗證方法

- **LOO vs 時間序列切分**：目前是 LOO ＋ as-of 時間旅行（每場用它前一天的資料、排除自己）。這已經避免了未來資料外洩；時間序列切分（只用更早的比賽）在 7 場上只剩 2–3 個測試點，太少。建議：**主要用 LOO＋as-of；加一個滾動檢查**（第 k 場只用前 k−1 場校正的 x*、δ），當警訊不當門檻。
- **6% 合不合理**：
  - 文獻族群模型 8–10%（§0.3）；實驗室指標解釋 59%。
  - 單場的「努力度雜訊」：全馬跑者在 84.8 ± 13.6% CS（Smyth 2020），即使路跑，個人每場的相對強度也有十幾個百分點的散佈；越野更大。
  - 7 筆的中位數信賴區間 ±2–3 個百分點（bootstrap）。
  - 建議：**越野的通過門檻改 8%，6% 當目標**；另外要求 n ≥ 5 **且** 中位數的 80% bootstrap 上界 ≤ 門檻（推估）。路跑 3% 在 n = 1–2 時沒有意義，先不設通過，顯示誤差就好。
- **回測在 COROS／TP 來源的結果**：表 2。路跑地形模型在 COROS 上 3.4%、TP 上 2.7%，和 WKO5 來源一致（3.7%），表示地形模型不依賴資料來源；越野和能力模型在 app 資料上還沒有東西可驗（§0.1 的兩個缺口）。

### 0.10 問題 8：建議的改進順序

每一步的「預估降幅」是推估（沒有做之前不會知道），用現有數字外推：

| 步 | 做什麼 | 需要的資料 | 預估對 7 場比賽中位誤差的影響 | 工作量 |
|---|---|---|---|---|
| 0 | 三件前置：(a) `FitFolderDataset` 讀 DB 的 `trail_classification`，讓 COROS 越野跑進得了越野模型（現在 n = 0）；(b) 努力度標記存進 app DB，回測讀得到；(c) TP／COROS 來源的閾值不再讀 WKO5 athlete 檔（改讀賽季計畫 dated 列 ＋ TP athlete_settings），並確認 TP 那一列的 LTHR 是哪個運動的 | 無 | 無（前置，但沒有它們 §0 其他步驟都不能在 app 資料上驗證） | 小–中 |
| 1 | **補同步 TP 的早期歷史**；as-of 閾值全部從訓練資料重估；用最近一次 CP 測試驗證 LTHR 估計法 | 補同步 | 7.6% → 約 6–6.5%（誤差 −27% 那一場會大幅縮小；其他場不變） | 小 |
| 2 | **耐疲勞 δ 加先驗 0.05/h、在清過的視窗上量**（§0.6） | 現有長跑 | 回到 ≤ 5.5%（不含 δ 的水準）或更好 | 小 |
| 3 | **x*(T) 曲線**取代固定 0.90／中位數（§0.7）；努力度標記補齊 7 場 | 使用者標記 | given-HR 和 race-level 的差（2 個百分點）縮小一半 | 中 |
| 4 | **非移動時間分開預測**，移動定義統一（§0.8） | 7 場的停頓對應補給站 | 總時間誤差減 1–2 個百分點；移動時間誤差不變 | 中 |
| 5 | **熱**：心率模型用個人 β（回測資料 0.224 bpm／Hadley）把比賽日的 x 修到訓練條件（§A5） | 已有 | 夏季比賽 2–4 個百分點；冬季 0 | 小 |
| 6 | **下坡技術性**：≤ −15% 的個人速度上限用 p50 不用 p90、分坡度箱（§A4） | 現有越野跑 | 越野地形 10.3% → 約 8%；對心率模型間接 | 中 |
| 7 | 新資料：2 場標記全力的比賽（n 7 → 9）、1 次 30 分 TT（LTHR）、1 次 50 分 AeT 測試 | 2–3 個月 | 門檻不確定性減半；n ≥ 9 讓中位數區間縮到 ±2 | 使用者 |
| 8 | 門檻改 8%／目標 6%，加 bootstrap 上界（§0.9） | 無 | 判定更誠實 | 小 |

做完 1–3 的合理預期是 **5–6%**（推估）；4–6 再各減 1 個百分點；族群模型的 8–10% 已經在下面。再往下要靠 7。

---

## 0.5 可移植性：規則能不能套用到別人、或換了環境

分類：**通用** = 有文獻或教練來源、和個人無關；**方法通用** = 偵測方法對誰都成立、數字要依個人校正；**只適用此人** = 用他的路線或比賽調出來的。「最少資料」是推估的經驗估計（能讓校正的估計誤差小於門檻一半），不是文獻值。

### 0.5.1 飄移（DRIFT 的建議）

| 規則 | 分類 | 自動校正 | 最少資料 | 預設值（出處） |
|---|---|---|---|---|
| 排除暖身、緩和 | 通用 | — | — | UA、Friel（教練） |
| 自適應暖身：前 20 分內最後一次停等 + 60 s | 方法通用 | 20 分 = 本人「早段停等」時間的 p95（上限 25 分）；60 s 固定 | 20 次路跑 | 20／60（推估） |
| 回程緩和：最後一群停等、相隔 ≤ 6 分、距結束 ≤ 12 分 | 方法通用 | 12 分 = 本人最後一群停等起點距結束的 p95（上限 15）；6 分 = 群內間隔的 p95 | 20 次有晚段停等的路跑 | 12／6（推估，本人校正） |
| 結尾靜止 ≥ 2 分裁掉 | 通用 | — | — | 2 分（推估，和人無關） |
| 心率延遲 τ | 方法通用 | 加總對數概似挑 τ（DRIFT §2.5） | 20 次 ≥ 30 分、有功率 | **60 s**（Hunt 2015／2019、Wang & Hunt 2021：55–70 s，同儕審查） |
| VI ≤ 1.04 | **只適用此人** | 參考 = 本人跑步機／田徑場跑的 VI p90 × 1.03；再對照跑走課的 p10，取中點 | 3 次穩定參考跑 ＋ 20 次輕鬆跑 | 1.04（推估）；沒有參考跑時 1.03（通過組 p90 × 1.01，推估） |
| 走路段：< 75% 中位速度、≥ 60 s、最長 ≥ 180 s | 只適用此人（相對門檻已經部分可移植） | 180 s = 本人跑走課最長慢段的 p10 和穩定跑 p90 的中點 | 10 次跑走 ＋ 10 次穩定跑；沒有跑走課時用預設 | 75%／60 s／180 s（推估） |
| 前後半功率差、快速結尾、停頓 ≤ 5% | 通用（數字推估、和人無關） | — | — | 5%（RP2 §3B、徐國峰 ≤ 30 秒／次） |
| 30／40 分分級；≥ 6 次聚合 | 通用 | — | — | UA 40 分；30 分推估；6 次（Ikari 2026 預印本、app AET_MIN_RUNS） |
| 溫度：Hadley 當共變量 | 方法通用 | β 從本人穩定段回歸（HEAT 回測） | 100 個穩定段、Hadley 跨度 ≥ 40 | 沒有 β → 只分區顯示，不修正 |

### 0.5.2 賽事功率

| 規則 | 分類 | 自動校正 | 最少資料 | 預設值（出處） |
|---|---|---|---|---|
| CP／W′／TTE | 方法通用 | 3′/12′ 測試；或訓練 mean-max 擬合 | 1 次測試，或 3 次 3–20 分硬跑 | 沒有 → 不給功率預測（Smyth 2020 的方法要有硬跑） |
| LTHR | 方法通用 | ≥ 6 次 30 分硬段的最後 20 分中位（Friel 定義） | 6 次 | 沒有 → 0.90 × 觀測 HRmax（推估，標推估） |
| AeT | 方法通用 | DRIFT 聚合回歸 | 6 個飄移點（約 6–10 週穩定跑） | 0.89 × LTHR（Friel Z2 上限，教練） |
| RE(g) 個人坡度曲線 | 方法通用 | n/(n+30) 收縮到 Minetti（現有） | 每箱 30 個 100 m 窗（約 10 次有坡的跑） | Minetti 2002（同儕審查） |
| 下坡速度上限 | 只適用此人 | 本人下坡箱的 p50 | 每箱 30 窗 | Townshend 2010：自由配速下坡比平路快 13.8%（同儕審查） |
| 技術性係數（依坡度箱） | **只適用此人**（路線性） | 實際 ÷ 模型，每箱 n/(n+30) 收縮到 1 | 每箱 30 窗 | 1.0 |
| 努力度切點 80／90／97／100 | 通用（UX 尺度） | 97 = 1 − CP spread 半寬（自動）；90 = 全力最低 f 與非全力最高 f 的中點 | 5 個能力樣本 | 80／90／97／100（推估） |
| x*(T) 全力心率曲線 | 方法通用 | 標記全力的跑步擬合 x₀、s | 6 次跨時長的全力 | 錨點 1.00 @ 0.5 h、0.90 @ 3 h、0.85 @ 12 h（程式現有規則、Friel Z3、Fornasiero 2018；換算推估） |
| 長休息 ≥ 5 分、≤ 10% | 只適用此人（比賽風格） | 標記全力的比賽的長休息比例 p90 | 3 場 | 10%（推估） |
| 耐疲勞 δ | 方法通用 | 長跑 ≥ 2 h 的清過視窗，收縮 n/(n+3) | 5 次 ≥ 2 h | 0.05/h（Clark 2019 換算，推估） |
| 熱：Hadley 表 | 通用（教練） | — | — | Hadley（教練）；慢跑者可能偏樂觀（Ely 2007、Vihma 2010） |
| 熱：個人 β | 方法通用 | HEAT 回測 | 100 段 | 無 → 用 Hadley 表 |
| 熱適應 a | 方法通用 | HRC 同夏斜率 | 兩個夏天 | **0**（回測資料不支持；Racinais 2015 的效應是族群平均） |
| 海拔 | 通用（Wehrlin 先驗） | 精度加權（現有） | 3 趟 ≥ 2500 m | Wehrlin −6.3%/1000 m |
| 百岳：背負、跟團規則、上限帶 | 只適用此人 | hike-meta 實填；手動標記 ≥ 5 天 | 5 趟 | 9 kg／−0.7 kg（使用者）；跟團 60／15%（推估） |
| CP 測試偵測 1.3×、10 bpm | 方法通用 | 用本人正式測試和假陽性算 ROC | 1 次正式測試 | 1.3／10（推估） |

### 0.5.3 課表

| 規則 | 分類 | 自動校正 | 最少資料 | 預設值（出處） |
|---|---|---|---|---|
| 低強度 ≥ 75%、CTL ramp 5／8、週增量 20%、TSB −20／−30、48 h | 通用 | 不校正（沒有受傷紀錄） | — | Seiler 2006；Friel；Nielsen 2014、Damsted 2019；TP；Casado 2022 |
| AeT 有效（SE ≤ 3 bpm） | 方法通用 | SE 來自聚合回歸 | 6 點 | 沒有 → 「需要測試」 |
| 間歇階梯與狀態機（98%、50%、5%、8 組、16 分） | 通用（教練）；數字推估、和人無關 | 達標率 ≥ 20 堂後可調 | 20 堂 | IA §4.3 |
| 4×4 的 %CP | 方法通用 | 3 堂後取後半趟平均功率／CP | 3 堂 | 心率 90–95% HRmax（Helgerud 2007） |
| 自動重排 A–E（§B5） | 通用原則；門檻推估、和人無關 | 記錄重排後兩週的 fade、TSB | 10 次重排 | spec 的數字 |
| 輕鬆跑偏強（80% CP、+20% TSS；沒有功率時 94% LTHR；SP-301 起只標示；課別分類閾值以上才調課表） | 通用（來源都有）；兩級切法推估 | — | — | 同左 |

### 0.5.4 偵測環境變化

- **路線指紋**（推估）：每次路跑存 (自適應起點、回程起點、坡道數 前／後半、爬升 前／後半、是否室內)。保留最近 20 次的中位數和四分位。
- **觸發**：最近 5 次裡 ≥ 4 次落在舊分佈的四分位外（例：以前 80% 有回程停等群，現在 5 次都沒有；或穩定段長度中位移動 > 10 分；或室內／戶外切換），標「跑步環境改變」。
- **之後**：方法通用的參數（12／6／20 分、VI、走路段）從改變後的跑步重新校正，**在達到最少資料前用預設值**，判讀卡標「參數：預設（環境改變後第 n 次）」；通用規則照常。τ、δ、β 不受路線影響，不重校。
- **搬到更熱／更高**：熱和海拔本來就逐次用當天條件，不需要偵測；熱適應 S 會自己動。
- **跑步機**：GPS 缺或高度變異 0 → 室內；VI、走路段用室內參考。

### 0.5.5 新使用者冷啟動

| 項目 | 沒有歷史時用什麼 | 準度（推估） | 什麼時候換個人模型 |
|---|---|---|---|
| 路跑預測 | 使用者填一場近期成績 → Riegel k = −0.07（RP2 §2.1，Stryd 表隱含 k −0.069） | 一場推另一距離 ±5–8%（Vandewalle 2018 比較過各模型，數字**未驗證**） | 有 CP 測試 ＋ ≥ 5 能力樣本通過回測 |
| 越野預測 | ITRA 指數查表（有指數的人）；否則 effort km（km + D+/100，ITRA）÷ 使用者填的「平路輕鬆配速」× 0.85（推估） | 族群 8–10%（§0.3）；推估查表 10–15%（推估） | ≥ 6 次越野跑有心率 → 心率模型（given HR）；≥ 5 場標記全力 → race level |
| 百岳 | Tobler／Naismith（經驗法則） | 回測 Tobler 51%（spec C）——很差，要說明 | ≥ 30 段個人窗 |
| 閾值 | 第 1–4 週排 3′/12′ 測試 ＋ 30 分 TT；在那之前 LTHR = 0.90 × HRmax（推估）、AeT = 0.89 × LTHR | — | 測試後 |
| 課表護欄 | 通用規則即日生效；CTL／TSB 要 6 週資料才有意義（CTL 時間常數 42 天） | — | 6 週 |
| 飄移 | 通用規則（固定 10 分暖身、無回程偵測）；VI 1.03 | — | 20 次路跑後校正參數 |

### 0.5.6 每人回測

- 沿用 `racepower_backtest`（capacity／terrain／trail_hr）和 `validated[]` 機制；每位使用者各自跑、各自存 `racepower_backtest.json`。
- **通過標準依 n 調整**（推估）：n < 5 → 只顯示誤差，不通過；n ≥ 5 → 中位 |誤差| ≤ 門檻 **且** 80% bootstrap 上界 ≤ 門檻 + 2 個百分點；n ≥ 10 → 上界 ≤ 門檻。門檻：路跑 3%、越野 8%（目標 6%）、百岳 10%。
- 飄移：DRIFT §5 的 A（重測）、D（新舊比較）流程每人各做一次；跨次 SD > 5 個百分點時只用聚合估計。
- 參數校正的「已校正」狀態也要回測：校正後 LOO 誤差沒有比預設低，就回到預設並記錄。

### 0.5.7 要改成「每人參數」的程式位置

| 現在的常數 | 函式 | 改成 |
|---|---|---|
| `workout_review.DRIFT_EARLY_S`、`DRIFT_SETTLE_S`、新的 tail 12／6、VI 1.04、走路段、τ 60 | `steady_start`、`steady_end`、`drift_of`、`drift_regression` | 讀 `user_settings` `athlete.calib.drift.*` |
| `threshold_estimate.AET_*` | `estimate_aet` | `athlete.calib.aet.*`（SE 一起存） |
| `trailhr.TRAILHR`（delta、x_default） | `fit`、`race_level` | `athlete.calib.race.delta_h`、`race.x_star = {x0, s}` |
| `maximal.MAXIMAL`、`activity_tags.AUTO_EFFORT`（rest_max、above_aet） | `trail_maximal`、`effort_hr` | `athlete.calib.race.long_rest_max`；above_aet 改讀 x*(T) |
| `grade_model.TECH_BOUNDS`、technicality、`v_max` | `GaitRE` | `athlete.calib.terrain.tech_by_bin`、`down_cap_q` |
| `heat.py` 的 a、`env.A_RECOVER` | `multiplier`、`segment_factors` | `athlete.calib.heat.a`（預設 0）、`heat.beta` |
| `cptest.BOUT_MIN_RATIO`、`HR_GAP_BPM` | `detect` | `athlete.calib.cptest.*` |
| `adapt.py`、`quality_gate.py` 的門檻 | — | 維持常數（和人無關），只讓 `plan.prefs` 可覆寫 |

存放：`user_settings`（`backend/settings/repository.py` 的 `DEFAULTS`），每個參數存 `{value, n, fitted_at, source: "default" | "fitted" | "user"}`，`user` 永不被自動校正覆蓋。校正由一個新的 `engine/calibrate.py` 在同步後跑（和 `adapt` 同一條流程），只在 n 達到最少資料時寫入。多使用者時每個 `athlete_id` 一組（`todo-multi-user-sharing.plan.md` §3 的單人假設清單要加上這些常數）。

---

## A. 賽事功率計算機

### A1 努力度條的五級切點（< 80 / 80–90 / 90–97 / 97–100 / > 100 %；race_min_f 0.90）

- **來源**：
  - 切點是「佔該時長可持續功率 P_sus(T) 的比例」，不是 %CP，所以 %CS 的文獻只能當旁證：Smyth 2020 全馬 84.8 ± 13.6% CS（快的 93%、慢的 79%）；Jones 2021 菁英 2 小時全馬 94% VO2peak（同儕審查，已驗證；「96% CS」的說法在全文，本次**未驗證**）；教練慣例 5K ≈ 100%、10K 95–97%、半馬 90–94%、全馬 ≈ 85% CS（Running Writings，教練）。
  - 97% 這條線對應 CP 估計 ±3%（RP2 §5.2，推估）。
- **建議數值**：維持五級；**校正 97 和 90 兩條線**：97 = 1 − 模型精度（CP spread 的半寬，現有）；90 = 使用者標記「全力」的最低 f 和「非全力硬跑」的最高 f 之間（目前：全力半馬 f 0.99–1.00，13 次硬 5 km 中位 0.88 → 0.90 落在中間，維持）。
- **校正**：能力樣本 ≥ 5 時看 f 的分佈（spec 已要求中位 0.97–1.03）。資料：app DB 裡一場半程路跑賽（TP 來源）＋ 之後的比賽。
- **程式**：`difficulty.py:85`（levels）、`intensity.py` `INTENSITY["race_min_f"]`。

### A2 能力樣本的判定（路跑五條、越野四條）

- **來源**：
  - 路跑：HRmax 準則 Howley, Bassett & Welch 1995（程式引，未重讀）；平均或負分段 Abbiss & Laursen 2008（RP2）；LTHR 定義 Friel（教練）；功率–時長單調性是定義。0.95／0.90、±10%、10 bpm、0.98、1.5 倍：推估。
  - 越野：**Fornasiero 2018**（同儕審查，已驗證）：65 km 全力賽平均 77% HRmax、86% 時間在 VT1 以下 → 「≥ 2/3 時間高於 AeT」對 ≥ 8 小時的比賽**一定不成立**；對 2–4 小時可能成立。**Kerhervé 2015**：心率隨進度下降。→ 「全力」的心率門檻必須依時長（§0.7 的 x*(T)）。
  - 長休息 ≤ 10%：推估，回測資料的 7 場 0–5%、硬山日 11–17%，分得開（spec）。
- **建議**：
  - 越野：`avg HR ≥ 0.90 × LTHR` 和 `above_aet ≥ 2/3` 改成 `x ≥ x*(T) − 0.03`（推估，T = 移動時長）；長休息規則不變；≥ 10 km／≥ 90 分不變（使用者描述）。
  - 路跑：維持；0.95／0.90 改成同一條 x*(T) 讀出來（半馬 1.6–2.4 h → ≈ 0.93；全馬 → ≈ 0.90），少一組推估數字。
- **校正**：x*(T) 用使用者標記的全力跑（路跑＋越野一起）擬合，≥ 6 筆；少於 6 筆用文獻錨點（§0.7）。資料：app DB 目前有 3 場；其餘要補同步。
- **程式**：`maximal.py` `MAXIMAL`、`trail_maximal`；`activity_tags.py` `AUTO_EFFORT`、`effort_hr`；`trailhr.py` `race_level` → `race_level(T)`。

### A3 越野的耐疲勞

見 §0.6。**建議數值**：δ 先驗 0.05/h（推估，從 Clark 2019 的 −10%／2 h 換算），收縮 n/(n+3)，上限 0.15 保留但要警告「資料給的 δ 超過先驗 3 倍」。**程式**：`trailhr.py` `TRAILHR["delta_max"]`、`durability_delta`、`fit`；視窗改用 `workout_review` 的 steady window（DRIFT §9）。

### A4 越野技術性係數

- **來源**：Voloshina & Ferris 2015（不平路面跑步 +5%）、Voloshina 2013（走路 +28%）、Pandolf η（`effort-distance-formulas.md` §7，同儕審查）；Minetti 2002 的下坡成本是在平滑跑步機上量的，一般人下坡「貴 40%」（RP2 §2.4）。「實際 ÷ 模型」中位數：推估。
- **資料說什麼**：越野 ≤ −15% 坡模型快 +20–23%、−15…−8% 快 +8%，平路 +2–6%（spec）。係數只在 g ≤ +2% 上算一個數，**沒有抓到陡下坡**。
- **建議**：係數改成**依坡度箱**（≤ −15、−15…−8、−8…−2、±2），每箱 n/(n+30) 收縮到 1；比賽預測的下坡速度上限用個人 **p50** 不用 p90（p90 是「最好的一次」，不是比賽能維持的；推估）。路線層級的 `route_tech` 有資料時優先。
- **校正**：現有越野回測的 class × grade 格子就是校正資料；LOO 看 ≤ −15% 箱的偏差從 +20% 降到 ±5% 以內。
- **程式**：`grade_model.py` `TECH_BOUNDS`、technicality 計算、`v_max`（p90 → p50）；`backtest.py` grid。

### A5 每段時間的熱修正（Hadley）

- **Hadley 的出處**：Mark Hadley（教練，Maximum Performance Running，2013 部落格）：氣溫 °F ＋ 露點 °F，≤ 100 不調整，101–110 調 0–0.5%，131–140 調 2–3%，151–160 調 4.5–6%，> 180 不建議硬跑。**社群經驗法則，沒有同儕審查**；適用範圍是「訓練配速調整」，不是比賽時間預測（已驗證描述：http://maximumperformancerunning.blogspot.com/2013/07/temperature-dew-point.html ）。
- **同儕審查的替代**（都已驗證）：
  - Ely et al. 2007（RP2）：WBGT 四分位，頂尖男子慢 1.7 → 4.5%，慢的族群受影響更大。
  - El Helou et al. 2012, *PLoS One* 7:e37407, DOI 10.1371/journal.pone.0037407：179 萬人、6 大馬：氣溫和成績是**二次曲線**，最佳溫度依水準不同；超過最佳點後變慢、棄賽率升。
  - Vihma 2010, *Int J Biometeorol* 54:297–306, DOI 10.1007/s00484-009-0280-x：斯德哥爾摩馬 1980–2008：氣溫是相關最高的單一變數（r 0.66–0.73，慢的人更高）。
  - 這些都是**路跑族群**，沒有越野、沒有逐時。逐段逐時套用是推估。
- **個人資料**（一位跑者）：HEAT 回測 β = 0.224 ± 0.036 bpm／Hadley 單位（271 段路線 effort）。這是**可以直接用的個人熱係數**。
- **建議**：
  - 心率模型（越野）：不用 Hadley 的時間懲罰表；把比賽日每段的 Hadley 換成心率位移 Δ = β·(Hadley_race − Hadley_train)，再把目標 x 往下修 Δ/LTHR（推估，但 β 是個人實測）。
  - 功率模型（路跑）：Hadley 表保留，但說明它是教練經驗法則；逐時套用保留（推估）。
  - 慢跑者受影響較大（Ely、Vihma）：對業餘跑者，Hadley 表可能偏樂觀；用 β 校正方向一致。
- **校正**：每季重跑 HEAT 回測更新 β；比賽後比較「用 β 修正 vs 用 Hadley 表」哪個誤差小。
- **程式**：`env.py` `heat_term`／`segment_factors`；`planner.py:464`；`trailhr` 加 heat 位移；`heat.py` 的 β 存成運動員參數。

### A6 高海拔修正

- **來源**：Wehrlin & Hallén 2006（同儕審查，已驗證，RP2）：300–2800 m 急性暴露每 1000 m VO2max −6.3%（4.6–7.5）；Bassett 1999 兩條曲線；Péronnet 1991 多項式（單一來源）。
- **個人化規則**（≥ 30 個窗、≥ 800 m）：推估。回測資料沒有 within-trip 的槓桿（α −11.0 %/1000 m、SE 8.9；trip 固定效應 SE 124–147），所以 α_post = Wehrlin。
- **建議**：保留 Wehrlin 當先驗、精度加權（capacity.py B7 已做）；`hikehr` 的「≥ 30 個窗 ≥ 800 m 才用個人值」可以**刪掉**，統一走 B7 的精度加權（SE 大自然不起作用）。要有個人值，需要 ≥ 3 趟 ≥ 2500 m 的獨走（回測資料只有 2 趟）。
- **校正**：補同步 COROS／TP 早期歷史的百岳（app DB 只有 5 筆登山）。
- **程式**：`hikehr.py` altitude（刪門檻）；`capacity.py` B7 不變。

### A7 百岳能力模型

| 數字 | 來源 | 建議 | 校正 | 程式 |
|---|---|---|---|---|
| 背包 9 kg、每天 −0.7 kg | 使用者決定 | 維持；每趟用 `hike-meta` 填實際重量，模型用實際值 | 填了 ≥ 5 趟後看 σ_pack 能不能縮 | `capacity.py` `PACK_DAILY_DROP` |
| 跟團判讀 ≥ 60%／≤ 15% | 推估 | 維持；設計已要求 ≥ 5 天手動標記、≥ 80% 一致才啟用 | 照設計 | `capacity.py` `SOLO_SHARE_MIN`、`classify_day` |
| Wehrlin–Coffman 差距 1.25 | 推估寬度（Coffman 2020 是負重 × 海拔的表現，不是 VO2max） | 維持；它只影響先驗寬度 | 無 | `capacity.py` τ |
| 能力上限帶 p75、≤ 3 h | 推估 | 維持，標推估 | 回測 B 通過後再看 | `planner.py:790` |
| 回測 B 16.4%（16 段） | — | 需要 ≥ 30 段 ≥ 10 趟；只有補同步早期的百岳才可能 | 補同步 | `baiyue_capacity_backtest` |

百岳沒有新文獻可加（BY 已查過；多日疲勞**未找到來源**維持）。

### A8 熱適應指數

- **來源**：Pandolf 1998、Racinais 2015 共識、Daanen 2018（HEAT，已驗證）；150／130 權重、0.75／0.35 切點、a 的用法：推估。
- **資料說什麼**：一位跑者 271 段的 β 在夏末比初夏**大**（0.260 vs 0.149，差 +0.111 ± 0.070），a_hr 最佳 = 0。沒有看到熱適應。
- **建議**：
  - 計算機預設 **a = 0**：S 照算、照顯示，但**不折抵熱懲罰**，直到 HRC（HEAT §2.3）在同一個夏天內的斜率檢定為負（p < 0.1，推估）。「已適應 S 0.9」「部分 0.5」的選項保留，但標「個人資料不支持」。
  - 這一條影響預測：選「已適應」時熱懲罰 × (1 − 0.9·0.75) ≈ 少算 1/3；Hadley 160 的 6% 懲罰會變 4%。
- **程式**：`planner.py:238–259`（heat_s 預設）、`env.py` `A_RECOVER`／`multiplier`、`status.py:205`。

---

## B. 課表安排

### B1 沒有 AeT 時的間歇劑量階梯

- **來源**：Helgerud 2007（4×4 @ 90–95% HRmax，同儕審查，ABR）；Seiler 2013（4×8 @ ~90% HRmax 最好，ABR）；Palladino（教練，筆記）；Koop／CTS（教練）。第 2 步 100–105% CP 是兩個 Palladino 數字的中間；4×4 換算 105% CP：**未找到來源**。
- **建議**：4×4 的目標改成**心率 90–95% HRmax**（一手來源），功率只顯示「上次這種課的平均功率」；100–105% 那一步標「Palladino 105–110% 的下緣，推估」維持。
- **校正**：做過 ≥ 3 堂 4×4 後，取每趟後半的平均功率／CP 當個人換算（推估），取代 105%。資料：FIT 的 lap。
- **程式**：`quality_gate.py` 劑量表；`interval_lines`。

### B2 護欄數字

| 數字 | 現在引的 | 實際來源 | 建議 | 類別 |
|---|---|---|---|---|
| 低強度時間 ≥ 75%／65% | Seiler | Seiler & Kjerland 2006：課表數 75／8／17%，時間約 91% 在 VT1 以下（ABR §2.4）。75% 以時間計已經寬 | 維持 | 同儕審查 |
| CTL ramp 5／7 | Palladino | Friel（教練，https://joefrieltraining.com/the-ctl-ramp-rate/ ）：**5–8** 對多數人合適、**10** 是上限；TrainingPeaks（Simmons 2020）：很 fit 的人 5–7；Couzens：長期 3–5 | 改成 **5 注意／8 擋**；Palladino 的 1–3 當「可長期維持」顯示。**2026-10-04（SP-63）再改成相對門檻**：注意 ≥ max(3, CTL 的 10%)、擋 ≥ min(10, max(5, CTL 的 15%))，週增量改成跑步時間對 max(上週, 前 4 週平均)（`ctl-ramp-calibration.md` §4） | 教練 |
| 週增量 > 20% 擋、10–20% 維持 | UA 10% | **Nielsen et al. 2014**, *JOSPT* 44:739–747, DOI 10.2519/jospt.2014.5164：874 名新手，2 週內增 > 30% 的距離相關傷害 HR 1.59（95% CI 0.96–2.66，p = 0.07）；**Damsted et al. 2019**, *JOSPT* 49:230–238, DOI 10.2519/jospt.2019.8541：261 人備半馬，增 20–60% 的 3 週內受傷比 < 20% 多 22.6 個百分點（p = 0.041），8、14 週後沒差。「10% 法則」本身沒有證據（Buist 2008 的漸進組沒有比較少受傷，**未驗證**） | **維持 20% 擋**（有來源）；10–20% 維持劑量（保守，無害，標推估） | 同儕審查 |
| TSB −20／−30 | Friel | TrainingPeaks（Simmons 2020，教練）：−10 到 −30 是有效訓練區、< −30 過度；+15 到 +25 比賽 | 維持，標 Friel／TP（教練） | 教練 |
| 硬課間隔 48 h | Seiler | Seiler「hard days hard, easy days easy」；Casado et al. 2022, *IJSPP* 17:820–833, DOI 10.1123/ijspp.2021-0435（系統性回顧，菁英跑者）：「hard day–easy day」是常態，每週各至少 1 次 zone 2 和 zone 3 課 | 維持；2 天標「hard–easy 的最小實作，推估」 | 同儕審查（描述性） |

校正：受傷、生病沒有記錄，**不能用個人資料校正**；維持文獻值。

### B3 AeT 測試相關（16 週過期、起始心率、4–6 週）

- **來源**：16 週：**未找到來源**；起始心率 0.89 × LTHR − 5：UA 說從估計的 AeT 開始、Evoke 說鼻呼吸配速（教練），−5 是保守餘裕（推估）；4–6 週再測：UA／Evoke 的重測週期**未驗證**（ABR 沒有查到明文）。
- **建議**（和 DRIFT §5.4 一致）：
  - 「有效」不用天數：`estimate_aet` 的聚合估計 **SE ≤ 3 bpm** 且最近 6 個點的殘差沒有單向偏移 > 5 bpm（推估）→ 有效；否則標「需要測試」。
  - 測試週期：SE 超標、或 CTL 比上次測試時變 ≥ 15%（推估）才排。
  - 起始心率：聚合估計值（有 SE）；沒有時維持 0.89 × LTHR − 5。
- **校正**：DRIFT 的 W1 視窗 ＋ 1/SE² 權重；app DB 一年的路跑足夠。
- **程式**：`quality_gate.py` `AET_FRESH_DAYS`（改成讀估計的 SE）；`aet_test.py` `STALE_DAYS`、`EVERY_WEEKS`、`START_BELOW`；`threshold_estimate.estimate_aet` 回傳 SE。
- **週量穩定前提（2026-10-02 加，2026-10-03 擁有者決定拿掉）**：原本規定測試前 3 週週量要在平均 ±15% 內，測試才算、AeT 測試建議才排。沒有原文出處，已經整條移除（`base_check.volume_stable` 刪除）；測試的建議和確認不再看週量。
- **AeT 的「± N bpm」標示（2026-10-02）**：研究裡的 ±16 bpm **不是**飄移／回歸法的誤差，是 Micheli 2025 的 CP 心率 vs MLSS 心率 95% 一致性界限（−15.84～+17.05 bpm，`zones-and-thresholds.md` §2.2）。AeT 估計（0.89 × LTHR、沒有 SE 的自動估算）沒有驗證過的誤差，借用 ±16 當量級（推估）；回歸估計有 SE 時顯示 ±2·SE，下限 ±3（日間變異，Lamberts 2009）。程式：`zones.aet_uncertainty`。

### B4 間歇進階的狀態機

- **8 組／16 分**：Golich、Cusick（WKO5 研討會，教練，IA §2.2）；Seiler 2013 的 4×8 = 32 分 @ 90% HRmax 比 4×4 @ 95% 好（同儕審查）→ 16 分上限只適用 ≥ 95% HRmax 的趟。
- **60 秒心率規則**：IA §3.1 已證明只能當煞車（Bellenger 2016、Aubry 2015：f-OR 也會讓 HRR 變快）。
- **進階順序（組數 → 時長／組休 → 功率）**：**沒有找到直接比較不同進階順序的研究**。支持的間接證據：Casado 2022（傳統週期化先量後強度；菁英每週各一次 zone 2／zone 3 課）、Seiler 2013（拉長趟數比加強度有效，休閒選手）；反對的：無。維持推估，但把 Seiler 2013 列為「先拉長再加強度」的依據。
- **校正**：每堂間歇的 `first_miss`、fade、RPE 都有記錄後，看哪個維度的進階後下一堂「達標」比例高；≥ 20 堂才有意義。
- **程式**：IA §4.3 的清單；`quality_gate.dose_step`、`next_quality`。

### B5 自動重排課表的規則（已合併：`docs/spec/plan-auto.spec.md`、`backend/engine/adapt.py`）

- **現在的規則與它們的來源**（spec 表）：

| 規則 | 門檻 | 來源 | 評估 |
|---|---|---|---|
| A 沒跑的輕鬆跑不補 | — | Seiler「easy days easy」（教練）；不補是推估 | 合理：補課只會增加週量；Nielsen／Damsted 的週增量風險支持不補 |
| B 沒跑的強度／測試課：保留或移到離硬課 ≥ 2 天的空日，否則取消；下週重複同一步 | 48 h | `plan_prefs.place()`；Casado 2022 hard–easy（描述性） | 合理 |
| C 沒跑的長跑：同週移、不跨週 | 48 h | 不跨週（推估） | 合理（長跑跨週會疊上下週的長跑） |
| D 輕鬆跑偏強：AeT+3 **或** > 10% 時間 **或** > 80% CP **或** TSS > 計畫 +20% → 之後的硬課延後／降一步／改輕鬆；其餘輕鬆跑扣掉多出的 TSS（每堂 ≥ 20 分） | 四擇一、20 分、降級順序推估 | AeT+3／10%：`workout_review`；80% CP：Palladino 1C（教練）；+20%：TrainingPeaks 合規帶（平台） | 四擇一太敏感：台灣夏天的輕鬆跑心率本來就偏高（HEAT β），AeT+3 一條會頻繁觸發。建議「心率條件要同時滿足 AeT+3 和 > 10%」，功率、TSS 任一即可（推估）<br>**2026-10-06 使用者決定（SP-301）**：拿掉 AeT+3 心率條件；偏強（> 80% CP 或 TSS > 計畫 +20%；沒有功率時平均心率 > LTHR 的 94%，Friel 跑步 Zone 3 上緣，推估）只標示、不動課表；課別分類為閾值以上（「太強」）才當強度課，延後／降階 48 小時內的強度課；扣輕鬆跑 TSS 拿掉。兩級切法與「太強＝課別分類」推估 |
| E 疲勞保險：TSB < −30、CTL ramp ≥ 7、或連兩堂紅 → 拿掉強度課、輕鬆分鐘 × 0.8 | 連兩堂紅、×0.8 推估 | TSB：Friel／TP（教練）；ramp 7：Palladino（教練，本文 B2 建議改 8；2026-10-01 改 8；SP-63 起用相對擋線 min(10, max(5, CTL 的 15%))） | ×0.8 的量級和 Vesterinen 2016「硬課少 25% 仍進步」一致；連兩堂紅沒有來源，維持 |
| 大改動要確認：週 TSS +20%、A 賽前 14 天拿掉長跑／強度／測試、周期改變、推送視窗 > 3 堂且不全是減量 | 全部推估 | — | 這些是 UX 安全閥，不是訓練規則；維持，不需要來源 |
| 狀態機：98% in-band、60 s 回 AeT < 50%、最後一趟掉 > 5% | 推估 | IA §4.3 | 見 B4 |

- **文獻**（同儕審查，已驗證）：
  - Kiviniemi et al. 2007, *Eur J Appl Physiol* 101:743–751, DOI 10.1007/s00421-007-0552-2：26 人 4 週，HRV 決定當天高或低強度，HRV 組最大跑速與 VO2peak 進步較多。
  - Vesterinen et al. 2016, *MSSE* 48:1347–1354, DOI 10.1249/MSS.0000000000000910：40 名休閒跑者 8 週，HRV 在個人 SWC 內才排 MOD/HIT；HRV 組高強度課少（13.2 vs 17.7）但 3000 m 進步（2.1% vs 1.1%，只有 HRV 組顯著）。
  - Javaloyes et al. 2019, *IJSPP* 14:23–32, DOI 10.1123/ijspp.2018-0122；2020, *JSCR* 34:1511–1518, DOI 10.1519/JSC.0000000000003337：自行車 8 週，HRV 組 PPO、VT 功率、40 分 TT 進步。
  - Manresa-Rocamora et al. 2021, *IJERPH* 18:10299, DOI 10.3390/ijerph181910299（統合分析）：HRV 導向對迷走 HRV 的效果 SMD 0.50；對有氧能力與表現只有**不顯著的小優勢**。
  - → 原則「準備度低就把硬課換成輕鬆」有證據；效果大小不大；而且**全部用 HRV**，COROS 活動檔沒有 RR（ABR §2.7）。
- **具體門檻**（週 TSS ±20%、A 賽前 14 天、> 3 堂、輕鬆跑偏強、減量 20%、不補課）：**未找到來源**。Friel「不要補做錯過的課」是教練說法（未驗證）。
- **建議**：
  - 把規則寫成「準備度」驅動：準備度的代理 = TSB、前一堂的 fade、輕鬆跑的心率飄移（DRIFT）、COROS 若有同步靜息心率就用（目前沒抓）。
  - ±20%、14 天、20% 減量維持推估；「不補課、同週能挪就挪」維持教練慣例。
  - Vesterinen 的「硬課少 25% 還進步」支持減量 20% 的量級。
- **校正**：記錄每次重排和之後兩週的 TSB、fade；≥ 10 次後看減量幅度和下一堂達標率的關係。
- **程式**：`feat/auto-replan`（另一個 agent）；`plan_prefs`、`week_plan`。

### B6 溫度分區（< 25 / 25–28 / > 28 °C）

- **來源**：台灣教練 < 25 °C；Lafrenz 2008、Beiter 2025（同儕審查，ABR／brief）；逐度劑量反應**未找到來源**（上一輪）。路跑族群的連續關係存在（El Helou 2012 二次曲線、Vihma 2010），但那是成績不是心率飄移。
- **個人資料**（一位跑者）：β = 0.224 bpm／Hadley 單位是**連續**的，而且是心率的位移。
- **建議**：分區留著當顯示；**AeT 聚合回歸加 Hadley 當共變量**（推估，係數先驗 β），這樣所有溫度的跑步都能進回歸，不用三區各自比。
- **程式**：`workout_review.heat_gate`（改標示不拒絕）；`threshold_estimate.estimate_aet` 加共變量。
- **2026-10-02 已做**（feat/heat-bands）：`heat_band` 只標示不拒絕；總覽、季圖、判讀卡基準都只在同區比；Friel／徐國峰／AeT 測試照常判，熱天通過仍算數、沒通過標「可能是熱造成的」。**還沒做**：AeT 聚合的 Hadley 共變量——在那之前 `drift_agg.aet_points` 只收 < 25 °C 和溫度不明的跑步（實際資料 180 天內 16 → 4 點，AeT 聚合估計會比較常顯示「需要測試」）。熱校正後的飄移值不做：β 是跑步之間的心率位移，不是單次跑步內的飄移速度。
- **2026-10-02 已做**（feat/aet-heat-covariate）：AeT 聚合收 25–28 °C 的跑步，前半心率先移到 25 °C：hr1′ = hr1 − β·(T − 25)；< 25 °C／溫度不明不動（AeT 測試在 < 25 °C 做，「moved」規則拿估計值和測試值比——用 Hadley 120 當基準會讓估計值整體比涼天測試低 ~7 bpm），> 28 °C 不收，飄移不校正。β 單位 bpm／°C：**預設 1.0**（Jenkins 2023 *Exp Physiol* 108:207–220，DOI 10.1113/EP090969：騎車 70% VO2peak、18／27／36 °C 同水氣壓，「1 bpm/°C」；濕度對 %HRmax 沒有可靠影響；套到跑步是推估），往個人擬合收縮：365 天有溫度、有飄移等級的路跑 OLS hr1 = a + b·P1（沒功率用速度）+ β·T，≥ 10 次且溫度 SD ≥ 2 °C 才算，權重 n/(n+20)，夾在 0–2（推估）。實際資料（一位跑者）：個人 β 0.07 ± 0.39（19 次，不顯著）→ 用 0.55；180 天點數 4 → 14（10 次 25–28 °C，各扣 0.5–1.5 bpm），但回歸斜率 ≤ 0（前半心率幾乎都落在約 10 bpm 寬的窄帶、飄移 ±5% 內），**還是「需要測試」**：過去 53 週每週都是，改前改後一樣，365 天＋時間加權也一樣。瓶頸不在點數，在心率跨度。
- **暫時的 AeT 下限**（使用者同意，2026-10-02，全部推估）：回歸找不到交點時，≤ X bpm 的參考級以上、SE ≤ 5 pp 的跑步（SE 加倍：GC 驗證低估 2–3 倍）≥ 6 次、X 附近 5 bpm 內 ≥ 3 次、最高 6 次平均 + 2·SE < 5% → 「AeT ≥ X（下限，推估）」算有效；≤ X 有一次扣掉雜訊仍 ≥ 5% 就取消。只是下限時每 8 週在建議框提醒一次 AeT 測試（可關掉，不影響指標）。實際資料 53 週：下限一次都沒成立（SE ≤ 5 pp、≤ LTHR − 3 的跑步最多 5 次），仍 53/53 需要測試。

---

## C. 其他

| 項目 | 來源 | 建議 | 校正 | 程式 |
|---|---|---|---|---|
| CP 測試偵測：1.3 × 中位、峰值心率差 ≥ 10 bpm | 推估（cptest.py 自己說明） | 維持，但只當「建議」（spec 已這樣做）；真正的 CP 測試靠課表／標題 | 回測資料只有 1 次正式 3′/12′ 測試和 ~35 個假陽性：算 ROC，看 1.3 要不要提到 1.4 | `cptest.py` `BOUT_MIN_RATIO`、`HR_GAP_BPM`；`cp_protocols.py` 8 bpm |
| 心率推能力 hrcap（≥ 8 次、跨度 ≥ 15 bpm、R² ≥ 0.5） | Åstrand & Ryhming 1954、Lamberts 2011（程式引，未重讀） | **方法本身在被動資料上不成立**：回測資料的輕鬆跑功率幾乎固定，心率差來自熱和飄移（R² 0.01）。Lamberts 的 LSCT 是**主動**的三階段次大測試（固定 %HRmax），不是被動回歸。建議：hrcap 保留程式但不顯示；要用心率推能力就排一個 LSCT 式的次大測試（6 分 60%、6 分 80%、3 分 90% HRmax，Lamberts 的設計，本次未重讀原文），用測試資料擬合 | 做 2–3 次 LSCT 後看 P@90% HRmax 的重測 CV | `hrcap.py`、`athlete.hr_capacity` |
| mFTP 不一致 | 工程問題 | 略過（brief） | — | — |

---

## 來源一覽（本文新增）

**同儕審查（已驗證）**
- Maunder E, Seiler S, Mildenhall MJ, Kilding AE, Plews DJ. The importance of 'durability' in the physiological profiling of endurance athletes. *Sports Med* 2021;51:1619–1628. DOI 10.1007/s40279-021-01459-0
- Clark IE et al. Dynamics of the power-duration relationship during prolonged endurance exercise and influence of carbohydrate ingestion. *J Appl Physiol* 2019;127:726–736. DOI 10.1152/japplphysiol.00207.2019
- Clark IE et al. Changes in the power-duration relationship following prolonged exercise: estimation using conventional and all-out protocols and relationship with muscle glycogen. *Am J Physiol Regul Integr Comp Physiol* 2019;317:R59–R67. DOI 10.1152/ajpregu.00031.2019
- Fornasiero A, Savoldelli A, Fruet D, Boccia G, Pellegrini B, Schena F. Physiological intensity profile, exercise load and performance predictors of a 65-km mountain ultra-marathon. *J Sports Sci* 2018;36:1287–1295. DOI 10.1080/02640414.2017.1374707
- Kerhervé HA, Millet GY, Solomon C. The dynamics of speed selection and psycho-physiological load during a mountain ultramarathon. *PLoS ONE* 2015;10:e0145482. DOI 10.1371/journal.pone.0145482
- Jones AM et al. Physiological demands of running at 2-hour marathon race pace. *J Appl Physiol* 2021;130:369–379. DOI 10.1152/japplphysiol.00647.2020
- Gutiérrez H et al. Real-time performance prediction in long-distance trail running: a practical model based on terrain difficulty and pacing variability. *Sports* 2025;13:385. DOI 10.3390/sports13110385
- El Helou N et al. Impact of environmental parameters on marathon running performance. *PLoS One* 2012;7:e37407. DOI 10.1371/journal.pone.0037407
- Vihma T. Effects of weather on the performance of marathon runners. *Int J Biometeorol* 2010;54:297–306. DOI 10.1007/s00484-009-0280-x
- Nielsen RØ et al. Excessive progression in weekly running distance and risk of running-related injuries: an association which varies according to type of injury. *J Orthop Sports Phys Ther* 2014;44:739–747. DOI 10.2519/jospt.2014.5164
- Damsted C et al. The association between changes in weekly running distance and running-related injury: preparing for a half marathon. *J Orthop Sports Phys Ther* 2019;49:230–238. DOI 10.2519/jospt.2019.8541
- Kiviniemi AM, Hautala AJ, Kinnunen H, Tulppo MP. Endurance training guided individually by daily heart rate variability measurements. *Eur J Appl Physiol* 2007;101:743–751. DOI 10.1007/s00421-007-0552-2
- Vesterinen V et al. Individual endurance training prescription with heart rate variability. *Med Sci Sports Exerc* 2016;48:1347–1354. DOI 10.1249/MSS.0000000000000910
- Javaloyes A, Sarabia JM, Lamberts RP, Moya-Ramon M. Training prescription guided by heart-rate variability in cycling. *Int J Sports Physiol Perform* 2019;14:23–32. DOI 10.1123/ijspp.2018-0122
- Javaloyes A, Sarabia JM, Lamberts RP, Plews D, Moya-Ramon M. Training prescription guided by heart rate variability vs. block periodization in well-trained cyclists. *J Strength Cond Res* 2020;34:1511–1518. DOI 10.1519/JSC.0000000000003337
- Manresa-Rocamora A, Sarabia JM, Javaloyes A, Flatt AA, Moya-Ramón M. Heart rate variability-guided training for enhancing cardiac-vagal modulation, aerobic fitness, and endurance performance: a methodological systematic review with meta-analysis. *Int J Environ Res Public Health* 2021;18:10299. DOI 10.3390/ijerph181910299
- Casado A, González-Mohíno F, González-Ravé JM, Foster C. Training periodization, methods, intensity distribution, and volume in highly trained and elite distance runners: a systematic review. *Int J Sports Physiol Perform* 2022;17:820–833. DOI 10.1123/ijspp.2021-0435
- 已在其他文件驗證：Smyth & Muniz-Pumares 2020、Smyth 2022、Ely 2007、Wehrlin 2006、Minetti 2002、Seiler & Kjerland 2006、Seiler 2013、Helgerud 2007、Lafrenz 2008、Beiter 2025、Bellenger 2016、Aubry 2015、Teso 2025、Voloshina 2013／2015、Pandolf 1998、Racinais 2015、Daanen 2018。

**預印本／未驗證**
- Fogliato R, Oliveira NL, Yurko R. TRAP: a predictive framework for trail running assessment of performance. arXiv 2002.01328 (2020)。預印本。
- 「Weather-aware prediction of trail running finish times using machine learning」（IJADIS）：MAPE 10.05 → 8.50%。未驗證。
- Howley, Bassett & Welch 1995；Lamberts et al. 2011；Buist 2008；Jones 2021 的「96% CS」。

**教練／平台／組織（已驗證描述）**
- ITRA performance index：https://itra.run/Runners/Performance
- Hadley 規則：Mark Hadley，Maximum Performance Running（2013）
- Friel：The CTL ramp rate（joefrieltraining.com）；TrainingPeaks：A coach's guide to ATL, CTL & TSB（Simmons 2020）
- Running Writings：critical speed guide（%CS 依距離）
- UA／Evoke／徐國峰／Palladino／Koop／WKO5 研討會：ABR、IA、RP2

**未找到來源**
- ITRA／UTMB 指數的預測誤差；補給站停留時間與賽事規模的關係；單一運動員小樣本的越野預測研究；AeT 16 週有效期；UA／Evoke 的重測週期；間歇進階順序的直接比較；自動重排的具體門檻；逐度溫度對心率飄移的劑量反應；多日山行疲勞。
