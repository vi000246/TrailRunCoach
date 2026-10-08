# 訓練期的碳水週期化與減量期的碳水負荷（SP-206）

> 調查日期：2026-10-06。只做調查，沒有改程式。基準 commit `47c5a6e`。
> 範圍是**訓練期和賽前幾天**的碳水，不是比賽中補給。比賽中補給、賽前超補的基本數字已在 `fueling-and-energy.md` §3（Jeukendrup 2014、Burke 2011、Vitale 2019、Bussau 2002、ISSN 2019），這裡不重查。能量不足（REDs）在 `female-athletes.md` §4，這裡只引用。
> 標記：**已驗證（全文）**／**已驗證（摘要）**／**搜尋摘要**（沒讀到原頁）／**教練級**／**推估**／**未找到來源**。本文編號 [C1]、[C2]…（和 `cold-start.md` 的 [C*] 無關，引用那份時寫檔名）。

## 摘要

1. **app 現在只在賽事計算機給賽前超補**：> 90 分鐘的比賽「前一天 10–12 g/kg」、百岳「前一晚正常吃」（`fuel.py:316–335`）。週課表、減量期、課程說明都沒有飲食提示。
2. **「低碳訓練（train low）」對訓練有素的人沒有明確好處。** 9 篇試驗的統合分析：表現的效果量 0.17（−0.15～0.49），不顯著；只有 sleep-low 的兩篇有進步 [C1]。菁英選手 4 週也沒有比較好 [C2]。
3. **風險是真的，但短期做、有專業監督時不大。** 強度會掉（肝醣太低時）[C4]；短期睡低對免疫、睡眠影響很小 [C6]；鐵調節素在低碳那次最高 [C7]；碳水少時骨吸收指標較高 [C8]。長期生酮（低碳高脂）讓菁英競走選手的經濟性和成績變差，而且重複驗證過 [C9]。越野跑者有四成以上篩檢出能量不足的風險 [C13]，再刻意少吃碳水只會更糟。
4. **強度日和輕鬆日的碳水「依課調整」有共識。** 「fuel for the work required」：硬課和長課吃夠，輕鬆課可以不特別補 [C4][C5]。ISSN 對超馬訓練建議每天 5–8 g/kg、量大時 7–10 g/kg [C3]。
5. **賽前碳水負荷**：超過 90 分鐘的比賽才有用，約多 2–3 %（搜尋摘要）[C10]；做法是賽前 24–48 小時、約 10 g/kg/天 [C3][C5]，一天就夠（Bussau，既有）。**越野超馬適用**（ISSN 建議 48 小時）；**百岳不必**（低強度、多日，重點是每天的總熱量，既有推估）。女性要同時多吃總熱量才補得上 [C12]。
6. **建議（§5）**：減量期最後幾天在週課表上加一則飲食提示，沿用賽事計算機的數字（P3）；強度課和長課的說明加「課前要吃」一句（P3）。**不做** train low 排課、不做生酮、不建議減量期少吃。

## 1. app 現在怎麼做

| 項目 | 現況 | 位置 |
|---|---|---|
| 賽前超補 | > 90 分鐘：前一天 10–12 g/kg；≤ 90 分鐘：約 6 g/kg；百岳：前一晚正常吃；早餐 1–4 g/kg、咖啡因 3–6 mg/kg | `backend/engine/racepower/fuel.py:316–335` |
| 顯示在哪裡 | 只有賽事計算機頁和分享頁的補給卡 | `backend/static/racepower.html:1414`、`backend/static/share.html:125`、CSV `backend/engine/racepower/csvplan.py:56` |
| 比賽中碳水 | 依賽事類型 g/h | `fuel.py:47–60` |
| 專項期的比賽模擬 | 「補給照比賽」，用 `fuel_text` | `backend/engine/specific_phase.py:724–769` |
| B2B 週末 | 「練比賽補給：每小時 30–60 g 醣…當晚要吃回來」 | `backend/engine/b2b.py:592` |
| 減量期週課表 | 量、強度、長跑、爬升的規則與提示；**沒有飲食** | `backend/engine/overview.py:2337`（`taper_rules`）、`:2326`（`taper_climb_note`，現成的週提示寫法） |
| 一般課程說明 | 間歇、長跑的說明沒有課前飲食 | — |
| 能量、體重 | 不算日常攝取（`female-athletes.md` §1：app 算不出能量可用性） | — |

## 2. 文獻

### 2.1 低碳訓練（train low）有沒有用

| 來源 | 對象與做法 | 結果 | 標記 |
|---|---|---|---|
| Gejl & Nybo 2021 統合分析 [C1] | 9 篇；訓練有素（男 VO2max ≥ 60、女 ≥ 55）；1–4 週 | 表現 SMD 0.17（−0.15～0.49），p = 0.29；只有 2 篇有進步（Marquet 的 sleep-low，約 3 %）；一天兩練、空腹的都沒有；結論「the evidence … is weak and 'train-low' is not per se associated with enhanced endurance」 | 已驗證（全文） |
| Gejl 等 2017 [C2] | 26 名菁英男性，4 週，每週 3 天限制碳水 | 兩組進步一樣（最大攝氧量、30 分計時都約 +5 %） | 已驗證（摘要） |
| Marquet 等 2016 [C2a] | 21 名鐵人，3 週 sleep-low（傍晚強度課→晚上不吃碳水→隔天空腹輕鬆課），每天總碳水一樣 6 g/kg | 10 km −2.9 % 對 −0.1 %；體脂下降 | 已驗證（摘要） |
| Burke 等 2011 [C11a] | 回顧 | 「Whether implementing additional 'train-low' strategies … leads to enhanced performance in well-trained individuals is unclear」 | 已驗證（摘要） |
| Aird 等 2018 統合分析 [C14] | 空腹 vs 吃過再練 | 課前吃讓**長時間**有氧表現較好（p = 0.012），短時間沒差；空腹讓訊號路徑較強 | 已驗證（摘要） |

### 2.2 怎麼做才不傷：強度日、輕鬆日

| 來源 | 說法 | 標記 |
|---|---|---|
| Impey 等 2018〈Fuel for the Work Required〉[C4] | 碳水依當天的課調整；肌肝醣低於約 300 mmol/kg 乾重時適應訊號較強，但低於 200 會影響強度；每天反覆低碳會增加生病風險；跑步的門檻可能比騎車高 | 已驗證（全文摘錄） |
| Mata 等 2019 [C5] | 低碳只用在**第一通氣閾值以下**、強度不會受影響的課；連續 > 3 週會影響免疫、睡眠和蛋白質平衡；賽前 24–36 小時約 10 g/kg；課前 1–4 小時 1–4 g/kg | 已驗證（全文摘錄） |
| ISSN 超馬立場 Tiller 等 2019 [C3] | 訓練期約 60 % 熱量、5–8 g/kg/天，量大或速度快的 7–10 g/kg；「strategically moderating CHO intake」可以，但不要長期把肝醣壓低，尤其高強度課和賽前；生酮「insufficient evidence」 | 已驗證（全文摘錄） |
| Stellingwerff 等 2019 [C5a] | 田徑的營養週期化框架：依月、週、日對應訓練安排；證據層級不一 | 已驗證（摘要） |
| Burke 等 2018 [C5b] | train low、sleep low、低碳高脂等名詞定義不一致，提出統一定義 | 已驗證（摘要） |

### 2.3 風險

| 來源 | 結果 | 標記 |
|---|---|---|
| Louis 等 2016 [C6] | 3 週 sleep-low：白血球、皮質醇、IL-6 不變；唾液 IgA 只在低碳組下降；睡眠效率 −1.1 %；上呼吸道感染沒差 | 已驗證（摘要） |
| McKay 等 2020 [C7] | 11 名菁英鐵人，4 天 sleep-low：鐵調節素在低碳那次升最多（2.5 倍）；「minimal impact … when … undertaken with expert nutrition and coaching input」 | 已驗證（摘要） |
| Hammond 等 2019 [C8] | 課前、中、後吃碳水會降低骨吸收指標，和熱量無關；短期（< 24 小時）限制碳水**沒有**增強適應訊號 | 已驗證（摘要） |
| Burke 等 2017、2020 [C9] | 3.5 週生酮低碳高脂：脂肪氧化大增，但經濟性變差、成績變差；重複實驗結果一樣 | 已驗證（摘要） |
| 越野跑者能量不足 [C13] | 1,899 名越野跑者，43 % 篩檢有低能量可用性風險 | 搜尋摘要；`female-athletes.md` 另有 49.7 %、55.1 % |

讀完的判斷：

- **對休閒越野跑者，train low 的好處沒被證實，風險（強度掉、鐵、骨、能量不足）有證據。** 研究裡「沒什麼影響」的條件是「有專業營養和教練監督」[C7]，app 做不到。
- **課前吃、硬課吃夠**這一端有共識，風險低。

### 2.4 賽前碳水負荷

| 來源 | 說法 | 標記 |
|---|---|---|
| Hawley 等 1997 [C10] | 超過 90 分鐘的運動，碳水負荷進步約 2–3 %；60–90 分鐘的跑步或騎車沒有好處 | 搜尋摘要（原文沒讀到） |
| Bussau 等 2002 | 10 g/kg 一天就到頂 | 既有文件已驗證（`fueling-and-energy.md` §3.1） |
| ISSN 2019 [C3] | 超馬：賽前 48 小時，約 10 g/kg/天；選熟悉、好消化、低纖低脂的食物 | 已驗證（全文摘錄） |
| Mata 2019 [C5] | 賽前 24–36 小時約 10 g/kg | 已驗證（全文摘錄） |
| Tarnopolsky 等 2001 [C12] | 女性只提高碳水比例時肝醣沒增加；總熱量多約 34 % 時才和男性一樣 | 搜尋摘要 |
| 每克肝醣帶約 3 克水，負荷期體重多 1–2 kg | 運動飲料公司網頁 | 搜尋摘要；**未找到研究出處**，只當說明用 |
| 減量期要不要少吃 | 只找到部落格和營養師網站（「熱量少 5–10 %、碳水照常」） | **未找到研究出處** |

套到 app 的賽事類型（推估，依上表）：

| 賽事 | 碳水負荷 | 理由 |
|---|---|---|
| 路跑 < 90 分鐘（10 km、半馬快的人） | 不用，前一天正常高碳水（約 6 g/kg） | Hawley 1997；app 現行 |
| 路跑全馬、越野 2–6 小時 | 前 1 天 10 g/kg（Bussau）；可以拉到 2 天 | app 現行；Bussau、Mata |
| 越野超馬 > 6 小時 | 前 1–2 天 10 g/kg | ISSN 48 小時；app 現行是 1 天，可補一句「也可以分兩天」 |
| 百岳多日 | 不必超補，前一晚正常吃；重點是每天的總熱量 | `fueling-and-energy.md` 推估；強度低、每天都會補 |

## 3. 落差

| # | 項目 | 來源怎麼說 | app 現在 | 判斷 |
|---|---|---|---|---|
| 1 | 賽前碳水負荷 | > 90 分鐘才有用；1–2 天、10 g/kg | 有，但只在賽事計算機 | **小缺口**：減量期的週課表看不到 |
| 2 | 超馬負荷天數 | ISSN 48 小時 | 前一天 | 相容（Bussau 一天就夠）；可補「也可分兩天」 |
| 3 | 女性負荷 | 要多吃總熱量 [C12] | 只寫 g/kg | **小缺口**：說明文字可以補一句（不分性別寫成「總量也要跟著多」） |
| 4 | 硬課、長課的課前飲食 | 課前吃長時間表現較好 [C14]；硬課要吃夠 [C4][C5] | 沒有 | **小缺口** |
| 5 | train low 排課 | 好處沒被證實 [C1]；有風險 | 沒有 | 一致，不要加 |
| 6 | 生酮／低碳高脂 | 傷經濟性和成績 [C9] | 沒有 | 一致 |
| 7 | 減量期少吃 | 沒有研究；能量不足的風險已經很高 [C13] | 沒有 | 不要建議少吃 |

## 4. 結論

- **app 要不要在減量期和賽前週給飲食提示？要，但只給「碳水負荷」這一則，而且沿用賽事計算機已有的數字。** 現在使用者要自己打開賽事計算機才看得到，週課表上沒有。
- **強度日和輕鬆日的碳水**：只在課程說明加一句「課前要吃」，不排「空腹課」「低碳課」。
- **不碰熱量**：不建議減量期少吃、不顯示每日熱量目標。理由是越野跑者能量不足的比例已經很高 [C13]，而 app 算不出能量可用性（`female-athletes.md` 的結論）。

## 5. 建議開的單

| # | 標題 | 優先度 | 驗收條件（草案） | 要你決定的事 |
|---|---|---|---|---|
| C-1 | 減量期最後幾天加碳水負荷的週提示 | P3 | (a) A 賽的減量期，賽前 1–2 天所在的那一週加一則 info 週提示（照 `taper_climb_note` 的寫法）；(b) 數字取 `fuel.loading`：> 90 分鐘「前 1 天（可分 2 天）約 10–12 g/kg，有體重時換算成克數」、≤ 90 分鐘「前一天正常高碳水」、百岳「前一晚正常吃」；(c) 加一句「選熟悉、低纖、低脂的食物；總熱量也要跟著多，不只換比例」；(d) 加一句「體重會多 1–2 kg，是肝醣帶的水」（標「說明，推估」）；(e) 來源：Bussau 2002、ISSN 2019、Hawley 1997；(f) 有測試：路跑 10 km 不出現負荷建議、百岳不出現超補 | 有體重時要不要顯示克數（要用設定裡的體重） |
| C-2 | 強度課和長課的說明加「課前要吃」 | P3 | (a) 間歇課、≥ 2 小時長課的說明加一句「課前 1–4 小時吃含碳水的一餐或點心，不要空腹做」；(b) 輕鬆跑不加；(c) 來源：Aird 2018、Mata 2019、Impey 2018；(d) 不推到手錶（只在 app 的課程說明） | 文字要不要附克數（1–4 g/kg）。建議不附，寫「一餐或點心」就好 |
| C-3 | 賽事計算機的超補說明補上「女性要多吃總熱量」與超馬「可分兩天」 | P3 | (a) `fuel.loading` 的說明文字加這兩點，不分性別寫成「總熱量也要跟著多」；(b) 超馬類別寫「前 1–2 天」；(c) 數字不變 | 無 |

> 2026-10-08 更新：C-2 做過之後又拿掉了——使用者決定「課前要吃」整個不要（SP-286），所有課的說明都不再出現這句。

**不建議開的單**

- 排「低碳課」「空腹課」「sleep low」：對休閒跑者沒有被證實的好處，風險要專業監督（§2.1、§2.3）。
- 減量期熱量目標、每日碳水目標（g/kg/天）：app 不知道使用者吃多少，也算不出能量可用性；顯示目標可能讓能量不足的人更少吃。

## 6. 限制、付費牆、查無出處

**限制**

- train low 的研究幾乎都是菁英或訓練有素的騎車、鐵人、競走，**沒有越野跑或超馬的試驗**。
- 碳水負荷的研究多是路跑和騎車，越野超馬只有 ISSN 的建議（證據等級 C）。
- 減量期的飲食沒有任何研究。

**付費牆、沒讀到原文**

- Hawley 1997（Sports Med）：只有搜尋摘要。
- ACSM／AND／DC 2016 聯合立場的表 2（`fueling-and-energy.md` 已列）。
- McKay 2020、Hammond 2019、Burke 2017 全文：只讀到摘要。
- Tarnopolsky 2001（J Appl Physiol）：只有搜尋摘要。
- 越野跑者能量不足的研究（PMC10824294）：只有搜尋摘要。

**查無出處、不採用**

- 「減量期熱量少 5–10 %、碳水照常」：只在部落格和營養師網站。
- 「減量期肌肝醣不負荷也會升 15–25 %」：搜尋摘要，沒有研究出處。

## 參考

- [C1] Gejl KD, Nybo L. Performance effects of periodized carbohydrate restriction in endurance trained athletes — a systematic review and meta-analysis. *J Int Soc Sports Nutr* 2021;18:37. https://doi.org/10.1186/s12970-021-00435-3 ；https://pmc.ncbi.nlm.nih.gov/articles/PMC8127206/
- [C2] Gejl KD, Thams LB, Hansen M, et al. No superior adaptations to carbohydrate periodization in elite endurance athletes. *Med Sci Sports Exerc* 2017. https://doi.org/10.1249/MSS.0000000000001377
- [C2a] Marquet LA, Brisswalter J, Louis J, et al. Enhanced endurance performance by periodization of carbohydrate intake: "sleep low" strategy. *Med Sci Sports Exerc* 2016. https://doi.org/10.1249/MSS.0000000000000823
- [C3] Tiller NB, Roberts JD, Beasley L, et al. International Society of Sports Nutrition Position Stand: nutritional considerations for single-stage ultra-marathon training and racing. *J Int Soc Sports Nutr* 2019;16:50. https://pmc.ncbi.nlm.nih.gov/articles/PMC6839090/
- [C4] Impey SG, Hearris MA, Hammond KM, et al. Fuel for the work required: a theoretical framework for carbohydrate periodization and the glycogen threshold hypothesis. *Sports Med* 2018;48:1031–1048. https://pmc.ncbi.nlm.nih.gov/articles/PMC5889771/
- [C5] Mata F, Valenzuela PL, Gimenez J, et al. Carbohydrate availability and physical performance: physiological overview and practical recommendations. *Nutrients* 2019;11(5):1084. https://doi.org/10.3390/nu11051084
- [C5a] Stellingwerff T, Morton JP, Burke LM. A framework for periodized nutrition for athletics. *Int J Sport Nutr Exerc Metab* 2019. https://doi.org/10.1123/ijsnem.2018-0305
- [C5b] Burke LM, Hawley JA, Jeukendrup AE, et al. Toward a common understanding of diet–exercise strategies to manipulate fuel availability for training and competition preparation in endurance sport. *Int J Sport Nutr Exerc Metab* 2018;28:451–463. https://doi.org/10.1123/ijsnem.2018-0289
- [C6] Louis J, Marquet LA, Tiollier E, et al. The impact of sleeping with reduced glycogen stores on immunity and sleep in triathletes. *Eur J Appl Physiol* 2016. https://pmc.ncbi.nlm.nih.gov/articles/PMC5020129/
- [C7] McKay AKA, Heikura IA, Burke LM, et al. Influence of periodizing dietary carbohydrate on iron regulation and immune function in elite triathletes. *Int J Sport Nutr Exerc Metab* 2020. https://doi.org/10.1123/ijsnem.2019-0131
- [C8] Hammond KM, Sale C, Fraser W, et al. Post-exercise carbohydrate and energy availability induce independent effects on skeletal muscle cell signalling and bone turnover. *J Physiol* 2019;597:4779–4796. https://doi.org/10.1113/JP278209
- [C9] Burke LM, Ross ML, Garvican-Lewis LA, et al. Low carbohydrate, high fat diet impairs exercise economy and negates the performance benefit from intensified training in elite race walkers. *J Physiol* 2017. https://pubmed.ncbi.nlm.nih.gov/28012184/ ；重複實驗：Burke LM 等 2020, *PLoS One*. https://doi.org/10.1371/journal.pone.0234027
- [C10] Hawley JA, Schabort EJ, Noakes TD, Dennis SC. Carbohydrate-loading and exercise performance. An update. *Sports Med* 1997. https://pubmed.ncbi.nlm.nih.gov/9291549/ （搜尋摘要）
- [C11a] Burke LM, Hawley JA, Wong SH, Jeukendrup AE. Carbohydrates for training and competition. *J Sports Sci* 2011;29 Suppl 1:S17–27. https://doi.org/10.1080/02640414.2011.585473
- [C12] Tarnopolsky MA, Zawada C, Richmond LB, et al. Gender differences in carbohydrate loading are related to energy intake. *J Appl Physiol* 2001;91:225–230. https://journals.physiology.org/doi/full/10.1152/jappl.2001.91.1.225 （搜尋摘要）
- [C13] Low energy availability, disordered eating, exercise dependence, and fueling strategies in trail runners. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC10824294/ （搜尋摘要）
- [C14] Aird TP, Davies RW, Carson BP. Effects of fasted vs fed-state exercise on performance and post-exercise metabolism: a systematic review and meta-analysis. *Scand J Med Sci Sports* 2018. https://doi.org/10.1111/sms.13054
- 既有文件：`fueling-and-energy.md` §3（Bussau 2002、Jeukendrup 2014、Vitale 2019）、`female-athletes.md` §4（REDs）。
