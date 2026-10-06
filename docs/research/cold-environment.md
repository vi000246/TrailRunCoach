# 冷環境與冬季百岳：低溫、風寒、雪地與失溫（SP-194）

> 調查日期：2026-10-06。只做調查，沒有改程式。
> 標記：**已驗證（全文）**＝讀到原文網頁或全文；**已驗證（摘要）**＝讀到 Europe PMC 或 PubMed 的摘要；**摘要**＝只讀到擷取工具的摘述或搜尋摘要；**教練級**＝嚮導、媒體、社群說法，沒有研究；**推估**＝我的延伸；**未找到來源**＝找過沒找到。
> 本文編號用 [C1]、[C2]…。引用舊文件時寫原文件名與段落。

## 摘要

1. **app 現在完全沒有「冷」。** 熱有懲罰、熱適應、熱適應課；冷的一側，熱懲罰在 Hadley 總和 ≤ 100 時直接是 0（`backend/engine/racepower/env.py:52`）。天氣只抓溫度、濕度、露點，**沒有抓風**（`weather.py:529`、`route_weather.py:44`）。雪地的地形係數刻意不提供（`hike.py:21`）。
2. **「冷會讓人變慢」只在很冷、穿得少、或走得慢時成立。** 跑步產熱多，馬拉松最快的氣溫落在 3.8–9.9 °C [C3]；穿滑雪服、風速 5 m/s 時，最好的是 −4 到 1 °C，−9 °C 以下才變差 [C4]。走路產熱少：0 °C、穿短袖短褲走坡，能量消耗比模型多約 13 % [C1]。
3. **冷對百岳真正的影響是「時間」和「安全」，不是心率。**
   - 雪地：日本嚮導與登山平台的經驗值是無雪期路線時間的 1.5 倍，沒有踏跡時 2–3 倍 [C11][C12]（教練級）。Pandolf 的軟雪係數 2.5–4.1 只有單一來源（`racepower-v2.md` §3C.2 已查）。
   - 失溫：致命事件幾乎都是「風＋濕＋累＋穿得不夠」，不是氣溫本身。甘肅 2021 年 21 人死亡時，體感溫度 −5 °C、陣風 8–9 級、防風外套非強制 [C8]。台灣 2025 年 3 月一波冷氣團 5 人死亡，3 人失溫 [C10]。
4. **門檻有官方數字可以用。** 風寒溫度 −27 °C 以下 ACSM 建議加強監看 [C5]；加拿大環境部：−28 到 −39 °C 外露皮膚 10–30 分鐘凍傷 [C6]。玉山雪季措施：排雲或白木林積雪 ≥ 5 cm 啟動，約 12 月中到 3 月，單攻暫停，需冰爪、冰斧、頭盔與雪地經驗 [C9]。
5. **建議**：先做「提醒」不做「修正」。賽事計算機加一行冷風提示（風寒、失溫風險、雪季），百岳加雪地時間倍率（使用者自選、標經驗法則）。走路的冷能量修正、跑步的冷懲罰曲線先不做，證據不夠撐一條公式。

## 1. 現況：app 怎麼處理溫度

| 地方 | 現在的做法 | 程式 |
|---|---|---|
| 環境係數 M | `M = 1 − (A_from − A_to) − (H_to − H_from)/100`。H 是 Hadley 熱懲罰，Hadley 總和（氣溫 °F＋露點 °F）≤ 100 時 H = 0 | `backend/engine/racepower/env.py:52–57`、`:112` |
| 逐段溫度 | 跑步：預報逐時對到分段 ETA。百岳：起點溫度用 0.0065 °C/m 遞減到每段高度 | `env.py:133`（`segment_temp`）、`planner.py:938` |
| 天氣來源 | 氣象署登山三天／一週預報、Open-Meteo 預報、近 5 年同期氣候。只取溫度、相對濕度、露點（Open-Meteo 另取氣壓） | `weather.py:529`、`:557`；活動天氣 `route_weather.py:44` |
| 沒有天氣時 | 預設 200 m、12 °C、70 % | `env.py:19–21` |
| 補給的水 | 10 °C 以下用水量帶的低端，30 °C 以上用高端 | `fuel.py:61–66`、`:289` |
| 熱適應 | 熱暴露模型 S、熱適應課、熱的安全提示 | `heat.py`、`heat_plan.py:43`（`SAFETY`） |
| 百岳地形 | 一般 1.0、碎石 1.3、箭竹 1.35、無路 1.67；「Snow η … not offered」 | `backend/engine/racepower/hike.py:21–22` |
| 賽事「熱不熱」 | `Event.heat` 只有 auto／hot／cool，cool 只是「不熱」 | `planning.py:131`、`:166` |

所以：

- 冷不會讓預估時間變長，也不會出現任何提示。
- 沒有風速，就算不出風寒，也看不出「稜線強風」這種失溫的主因。
- 冬季百岳的雪、冰，計算機會照無雪期算，時間會偏短很多（§2.4）。

既有文件提過的冷：

- `baiyue-from-running.md` §2.4：個人海拔斜率 −9.9 %/1000 m 比文獻陡，干擾之一是「高處較冷」。只列為假說，沒有量。
- `fueling-and-energy.md` §5.1：Tharion 2005，野外軍事訓練「天冷或高海拔時消耗偏高」（已驗證摘要）。
- `racepower-v2.md` §3C.2：Pandolf 軟雪 η 2.5／3.3／4.1 是單一來源；`η = 1.30 + 0.082·D` 找不到出處。
- `racepower-v2.md` 參考文獻有 Looney 2025，但只拿它的坡道式，沒有用冷的結果。本文補讀（§2.2）。

## 2. 文獻

### 2.1 跑步：冷到什麼程度才會變慢

| 來源 | 說法 | 等級 |
|---|---|---|
| Galloway & Maughan 1997 [C2] | 8 人在 3.6、10.5、20.6、30.5 °C 騎車到力竭（約 70 % VO2max）。最久是 10.5 °C（93.5 分），最短是 30.5 °C（51.6 分）；作者說是「倒 U 形」 | 已驗證（摘要） |
| El Helou 2012 [C3] | 6 個大型馬拉松、10 年、約 179 萬人。速度和氣溫是二次曲線，最快的氣溫依程度落在 **3.8–9.9 °C**；「large decreases in air temperatures under the optimum also reduce performances」 | 已驗證（全文） |
| Ely 2007 [C3b] | 7 個馬拉松，WBGT 5–25 °C：越熱越慢。最冷的那一組（5–10 °C）就是最快的 | 已驗證（摘要） |
| Nimmo 2004 回顧 [C14] | 長時間中等強度時，約 11 °C 有利；強度更低、產熱不夠時，11 °C 就可能不利 | 已驗證（摘要） |
| Sandsund 2012 [C4] | 9 名耐力選手穿越野滑雪服，風速 5 m/s，−14 到 20 °C 六種溫度。−4 與 1 °C 時力竭時間最長；−14 °C 比 −4／1 °C 短；**VO2max 各溫度沒有差別** | 已驗證（摘要） |

讀完的判斷：

- 跑步在 0–10 °C 是「最好」的溫度，不是要懲罰的溫度。現在的 H = 0 對路跑和多數越野賽是對的。
- 變慢要到「穿得不夠＋很冷」才出現，劑量曲線沒有（Sandsund 只有 6 個點、9 個人）。
- **沒有找到**「冷 X °C 心率變化幾下」可以直接用的數字。Sandsund 的 VO2max 沒變。

### 2.2 走路：產熱少，冷才有影響

| 來源 | 說法 | 等級 |
|---|---|---|
| Looney 2025 [C1] | 14 人，20、10、0 °C，穿短袖、短褲、薄手套走坡。LCDA 能量式在 20、10 °C 準；**0 °C 低估約 13 %（90 % CI −16.2 到 −9.5 %）**。0 °C 時平均皮膚溫度最低 20.1 °C（20 °C 時 28.8 °C），核心溫度沒變；作者：「users should expect underestimated M˙ in 0°C or colder」 | 已驗證（全文，經擷取工具） |
| Haman 2006 回顧 [C15] | 長時間顫抖靠肝醣和脂肪；肌肝醣耗盡會縮短能撐住顫抖的時間 | 已驗證（摘要） |
| Tharion 2005（`fueling-and-energy.md` §5.1） | 野外訓練每日消耗，冷或高海拔時偏高 | 已驗證（摘要），既有 |

讀完的判斷：

- 13 % 是「穿短袖在 0 °C」的結果。登山者會穿保暖層，實際多多少**沒有研究**。
- 所以「冷 → 熱量多 X %」可以當補給的提示，不適合直接乘進能量或速度模型（推估）。

### 2.3 失溫、凍傷與風寒

| 來源 | 說法 | 等級 |
|---|---|---|
| ACSM 2006 立場聲明 [C5] | 大多數冷環境都能安全運動；用風寒溫度估凍傷風險，**風寒 −27 °C（−18 °F）以下加強監看**；衣著依個人選，不要整隊一套 | 已驗證（摘要） |
| 美國國家氣象局 [C7] | 風寒（°F、mph）＝ 35.74 + 0.6215T − 35.75V^0.16 + 0.4275T·V^0.16；只適用於氣溫 ≤ 50 °F（10 °C）、風速 > 3 mph（約 4.8 km/h） | 已驗證（原頁） |
| 加拿大環境部 [C6] | 風寒 0 到 −9：稍不舒服；−10 到 −27：長時間在外有失溫和凍傷風險；**−28 到 −39：外露皮膚 10–30 分鐘凍傷**；−40 到 −47：5–10 分鐘；持續風速 > 50 km/h 會更快 | 已驗證（原頁） |
| WMS 意外失溫指引 2019 [C16] | 院前評估與處置；分級細節在全文 | 摘要只讀到目的。**全文沒有讀到**（出版商轉址），分級數字不引用 |
| 甘肅黃河石林 100 km，2021-05-22 [C8] | 第三檢查點約 2,230 m。5 小時內降溫 5–7 °C，平均風 6–7 級、陣風 8–9 級，冰霰和雨 3–5 mm；體感溫度從 10:00 的 1 °C 降到 11:20–13:50 的 −5 °C。**21 人失溫死亡**；防風外套「recommended but not compulsory」；檢查點之間沒有人員、是手機訊號死角 | 摘要（Wikipedia 經擷取工具；中文媒體報導一致） |
| 台灣 2025 年 3 月冷氣團 [C10] | 奇萊東稜、合歡西峰、能高越嶺各 1 人失溫，玉山主北 2 人墜谷，共 5 死；「高山積雪稍融，導致結冰易滑」；國家公園署提醒雪季到 3 月 31 日、帶冰爪冰斧岩盔、體力不足果斷撤退 | 摘要（客新聞報導，經擷取工具） |
| 台灣 2016 年 1 月寒流 [C17] | 全台至少 60 人受凍猝死（多為平地）；陽明山鞍部 −3.7 °C | 摘要（維基百科） |

讀完的判斷：

- **風比氣溫重要。** 甘肅那天的氣溫並不極端，是風、濕、單薄衣著、沒有支援一起造成的。現在 app 抓不到風，就看不到這件事。
- 凍傷有明確的官方門檻（風寒 ≤ −28 °C）。但台灣高山冬天的風寒多半落在 −10 到 −27 的「長時間在外有風險」那一級（推估：3,500 m 夜間 −5 °C、風 30 km/h 時風寒約 −13 °C，用 [C7] 的式子算）。對百岳，**失溫比凍傷更常見**。
- 失溫的觸發是「濕＋風＋累＋糧食不夠」，模型算不出機率。app 能做的是提醒，不是預測。

### 2.4 雪地與冰：行進速度

| 來源 | 說法 | 等級 |
|---|---|---|
| YAMAP STORE〈冬の登山の基本装備〉[C11] | 「積雪期はコースタイムの1.5倍の時間を見積もって行動することをお勧めします」 | 教練級（平台），已驗證（原頁） |
| 沖本浩一（嚮導，20 年）[C12] | 「トレース（踏み跡）がない場合…登りなら無積雪期の2〜3倍の所要時間をみておくことが必要」 | 教練級，已驗證（原頁） |
| Pandolf 1976（`racepower-v2.md` §3C.2） | 軟雪 15／25／35 cm 的 η：2.5／3.3／4.1 | 單一來源（Wikipedia）；原文摘要沒有 |
| Richmond 2019 [C13] | 用積雪深度與密度算下陷量 z，η = 0.0005z³ + 0.0001z² + 0.1072z + 1.2604 | 已驗證（摘要）。要雪的密度，使用者拿不到 |
| 玉山國家公園雪季措施 [C9] | 約 12 月中到隔年 3 月；主峰線白木林或排雲山莊積雪 ≥ 5 cm 就實施；暫停單攻；需冰爪、冰斧、頭盔等；雪地訓練要 14–30 天前申請 | 已驗證（官方頁與健行筆記轉載、多家媒體一致） |
| 百岳分級 D/E（`effort-distance-formulas.md`） | 雪季路線另列一級 | 既有 |

讀完的判斷：

- 「1.5 倍、有踏跡；2–3 倍、沒踏跡」是唯一能直接用的數字，教練級。
- 台灣的積雪通常薄而且會結冰。冰面主要是「慢＋危險」，Pandolf 的軟雪 η 不適用。結冰的速度係數**未找到來源**。
- 雪季玉山不能單攻，計算機若對「單攻玉山、1 月」算出時間，本身就該加一句「雪季管制」。

### 2.5 冷與補給

- 天冷口渴感降低、但呼吸失水增加（ACSM 2006 全文有，**這次只讀到摘要**，不引數字）。
- 走路的能量消耗在 0 °C、穿得少時多約 13 % [C1]；登山穿著下多多少，**沒有研究**。
- 顫抖靠肝醣，糧食吃不夠會縮短能撐的時間 [C15]。
- app 現在 10 °C 以下用水量帶的低端（`fuel.py:66`）。方向沒錯，但可以加一句「冷天不渴也要喝、熱食熱飲」（推估，教練常識）。

## 3. 落差

| # | 落差 | 影響 | 證據強度 |
|---|---|---|---|
| G1 | 天氣不抓風速、陣風 | 算不出風寒，看不到稜線強風 | 風寒式子與門檻都是官方：強 |
| G2 | 沒有任何冷的提示 | 冬季百岳、夜間高山段，沒有失溫提醒 | 事故報告：中（觀察性） |
| G3 | 雪地、結冰沒有時間修正 | 冬季百岳的 ETA、撤退時間偏樂觀 | 1.5–3 倍：教練級 |
| G4 | 不知道雪季管制 | 對 1–3 月玉山單攻算出時間，沒說不能去 | 官方：強 |
| G5 | 冷的速度懲罰 | 現在 H = 0；跑步在 0–10 °C 本來就快，加懲罰反而錯 | 文獻支持「不加」 |
| G6 | 走路的冷能量修正 | 補給可能偏少 | 只有穿短袖的實驗：弱 |

## 4. 結論與建議

### 4.1 結論

- **不要加「冷懲罰」到 M。** 跑步 0–10 °C 是最快的區間 [C3][C4]，更冷時的劑量曲線沒有。
- **要加「冷的提醒」。** 失溫是越野和百岳真正的冷風險，主因是風和濕。
- **要加風速。** Open-Meteo 有 `wind_speed_10m`、`wind_gusts_10m`、`apparent_temperature`、`snowfall`、`snow_depth`（官方文件，已驗證）；氣象署登山預報頁面有「體感溫度」「風向風力」（已驗證，網頁）。開放資料 F-B0053-035 的欄位**沒有核對到**，要實作時看一次 JSON。
- **雪地用使用者自選的倍率**，標「經驗法則」。

### 4.2 建議開的單

**單 1：計算機抓風速，算風寒，加冷風提示**
- 優先度：**P2**
- 內容：Open-Meteo 預報與逐時資料多抓 `wind_speed_10m`、`wind_gusts_10m`、`apparent_temperature`；氣象署有風就用。逐段算風寒（只在 ≤ 10 °C、風 > 4.8 km/h 時，[C7]）。
- 驗收條件草案：
  1. 任何一段風寒 ≤ −10 °C，或陣風 ≥ 50 km/h，計算機頁出現「冷風」提示，寫出哪幾 km、幾點、風寒多少。
  2. 風寒 ≤ −28 °C 時提示改成「外露皮膚 10–30 分鐘可能凍傷」（加拿大環境部 [C6]）。
  3. 沒有風的資料時不顯示，也不報錯；測試涵蓋三種情況。
  4. 預估時間不變。
- 需要使用者決定：提示門檻用 −10 °C（加拿大「長時間在外有風險」）還是更寬鬆；要不要也在百岳卡片顯示。

**單 2：冬季百岳的雪地時間倍率與雪季提醒**
- 優先度：**P2**
- 內容：百岳計算機加「積雪」選項：無、有踏跡（× 1.5）、無踏跡（× 2–3，給區間）。玉山主峰線在 12/15–3/31 之間，顯示「可能實施雪季措施：暫停單攻、需冰爪冰斧頭盔與雪地經驗」，附國家公園公告連結。
- 驗收條件草案：
  1. 選「有踏跡」時，移動時間 × 1.5，撤退時間跟著變；標「經驗法則（日本嚮導）」。
  2. 選「無踏跡」時顯示 × 2 與 × 3 兩個時間。
  3. 單攻玉山、日期落在雪季，頁面出現雪季提醒。
- 需要使用者決定：倍率要不要只乘爬升段（沖本說的是「登り」）；雪季日期用固定區間，還是只在使用者勾「有積雪」時才提醒；其他國家公園（雪霸）要不要一起做。

**單 3：失溫風險提醒（行程層級，不是預測）**
- 優先度：**P3**
- 內容：百岳或越野賽事卡片，在「最低溫 ≤ 5 °C 且（降雨機率高或風寒 ≤ −5 °C）」時，列出檢查項：防水防風外套、保暖層、手套帽子、熱食、撤退點。依據甘肅 2021、台灣 2025 事故與 ACSM 2006 的風險管理原則。
- 驗收條件草案：條件成立時卡片出現檢查清單；條件不成立時不出現；文字不寫機率。
- 需要使用者決定：門檻 5 °C 與 −5 °C 是推估，要不要先用；賽事若有強制裝備清單，要不要讓使用者勾。

**單 4（先不開）：走路的冷能量修正**
- 優先度：P3，**建議不開**。
- 理由：只有「0 °C 穿短袖多 13 %」一個研究 [C1]，登山穿著下沒有數字。等有資料再說。
- 需要使用者決定：同意先不開。

### 4.3 對擁有者的提醒

- 擁有者只在跑步時戴錶，沒有夜間或全天資料。以上建議都不靠全天資料。
- 規則不能照擁有者自己的狀況寫死。風寒、失溫門檻都是通用官方數字，對所有人一樣。

## 5. 沒查到、付費牆、查無出處

**沒查到**

- 冷到幾度跑步心率會變多少的可用數字。
- 登山穿著下，冷天走路的能量多多少。
- 結冰路面的速度係數。
- 台灣高山的失溫事故統計（只有個案報導；`mountaineering-physiology-scholars.md` 的救援統計沒有分出失溫）。

**付費牆或讀不到**

- ACSM 2006 立場聲明全文（MSSE）：只讀摘要；補水、衣著的細節數字沒有讀到。
- WMS 意外失溫指引 2019 全文：出版商轉址，沒有讀到分級表。
- Pandolf 1976（Ergonomics）雪地原文：PubMed 沒有摘要。
- Richmond 2019（Appl Ergon）全文：只讀摘要。
- Sandsund 2012、Galloway 1997、Nimmo 2004 全文：只讀摘要。
- 氣象署開放資料 F-B0053-035 的欄位頁：抓取只拿到網站標題。

**查無出處、不採用**

- 軟雪 `η = 1.30 + 0.082·D`（沿用 `racepower-v2.md` 的結論）。

## 參考文獻

- [C1] Looney DP, Schafer EA, Chapman CL, et al. Graded walking energetics under cold strain. *Med Sci Sports Exerc* 2025;57:1472–1480. PMID 39898599. https://pmc.ncbi.nlm.nih.gov/articles/PMC12129380/
- [C2] Galloway SD, Maughan RJ. Effects of ambient temperature on the capacity to perform prolonged cycle exercise in man. *Med Sci Sports Exerc* 1997;29:1240–1249. https://doi.org/10.1097/00005768-199709000-00018
- [C3] El Helou N, Tafflet M, Berthelot G, et al. Impact of environmental parameters on marathon running performance. *PLoS One* 2012;7:e37407. https://doi.org/10.1371/journal.pone.0037407 ；https://pmc.ncbi.nlm.nih.gov/articles/PMC3359364/
- [C3b] Ely MR, Cheuvront SN, Roberts WO, Montain SJ. Impact of weather on marathon-running performance. *Med Sci Sports Exerc* 2007;39:487–493. https://doi.org/10.1249/mss.0b013e31802d3aba
- [C4] Sandsund M, Saursaunet V, Wiggen Ø, et al. Effect of ambient temperature on endurance performance while wearing cross-country skiing clothing. *Eur J Appl Physiol* 2012;112:3939–3947. https://doi.org/10.1007/s00421-012-2373-1
- [C5] Castellani JW, Young AJ, Ducharme MB, et al. ACSM position stand: prevention of cold injuries during exercise. *Med Sci Sports Exerc* 2006;38:2012–2029. https://doi.org/10.1249/01.mss.0000241641.75101.64
- [C6] Environment and Climate Change Canada. Wind chill and the Wind Chill Index. https://www.canada.ca/en/services/environment/weather/severeweather/wind-chill-index.html （2026-10-06 讀取）
- [C7] US National Weather Service. Wind Chill Chart. https://www.weather.gov/safety/cold-wind-chill-chart （2026-10-06 讀取）
- [C8] Gansu ultramarathon disaster. Wikipedia. https://en.wikipedia.org/wiki/Gansu_ultramarathon_disaster ；天下雜誌 https://www.cw.com.tw/article/5114943 ；光明網一審判決 https://m.gmw.cn/toutiao/2023-12/15/content_1303602285.htm
- [C9] 玉山國家公園管理處，雪季措施公告（115 年 1 月 1 日起）. https://www.ysnp.gov.tw/Announcement/C001000?ID=3cbca887-0ab0-4382-8d12-5eb7250c7936&PageIndex=1&PageType=1 ；健行筆記轉載入園申請執行要點 https://hiking.biji.co/index.php?act=info&id=6176&q=news ；中央社 https://www.cna.com.tw/news/ahel/202412190137.aspx
- [C10] 客新聞〈這波下雪山難致5死 勿用生命挑戰覆雪高山〉2025-03-24. https://hakkanews.tw/2025/03/24/188649/
- [C11] YAMAP STORE〈今年こそ雪山デビュー！冬の登山の基本装備&オススメルート〉. https://store.yamap.com/articles/snowy-mountains_2022
- [C12] 沖本浩一〈プロガイドが教える 雪山の登山計画の立て方と準備〉. https://yama-guide.com/2019/09/24/winter-mountain-planning/
- [C13] Richmond PW, Potter AW, Looney DP, Santee WR. Terrain coefficients for predicting energy costs of walking over snow. *Appl Ergon* 2019;74:48–54. https://doi.org/10.1016/j.apergo.2018.08.017
- [C14] Nimmo M. Exercise in the cold. *J Sports Sci* 2004;22:898–915. https://doi.org/10.1080/0264041400005883
- [C15] Haman F. Shivering in the cold: from mechanisms of fuel selection to survival. *J Appl Physiol* 2006;100:1702–1708. https://doi.org/10.1152/japplphysiol.01088.2005
- [C16] Dow J, Giesbrecht GG, Danzl DF, et al. Wilderness Medical Society clinical practice guidelines for the out-of-hospital evaluation and treatment of accidental hypothermia: 2019 update. *Wilderness Environ Med* 2019;30:S47–S69. https://doi.org/10.1016/j.wem.2019.10.002
- [C17] 2016年1月北半球寒流. 維基百科. https://zh.wikipedia.org/zh-tw/2016年1月北半球寒流
- Open-Meteo Forecast API 文件. https://open-meteo.com/en/docs （2026-10-06 讀取）；中央氣象署登山預報頁 https://www.cwa.gov.tw/V8/C/L/Mountain/Mountain.html
- 既有文件：`racepower-v2.md` §3C.2、`baiyue-from-running.md` §2.4–2.5、`fueling-and-energy.md` §5.1、`effort-distance-formulas.md`、`heat-acclimation.md`。
