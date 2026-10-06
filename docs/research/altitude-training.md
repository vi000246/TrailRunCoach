# 高地訓練與低氧帳：對百岳和高海拔越野有沒有用（SP-207）

> 調查日期：2026-10-06。只做調查，沒有改程式。
> 標記：**已驗證（全文）**＝讀到全文；**已驗證（摘要）**＝讀到 Europe PMC 摘要；**摘要**＝只讀到擷取工具摘述或搜尋摘要；**教練級**＝教練、醫師科普、社群說法；**推估**＝我的延伸；**未找到來源**＝找過沒找到。
> 本文編號用 [H1]、[H2]…。高山症的預防條件（CDC、Schneider、Shen）在 `mountaineering-physiology-scholars.md` §3 與 `periodization-cross-sport.md` §3.3 已查，這裡只引用。

## 摘要

1. **「高地訓練讓平地變強」對週末跑者做不到。** 有效的低住高練要住在 2,000–2,500 m、每天 ≥ 12–22 小時、≥ 3–4 週 [H2][H3]。統合分析中，次菁英的效果約 1–4 %，而且有安慰劑和集訓效應 [H1]；2016 年的回顧說證據「weak」[H4]。
2. **低氧帳、低氧面罩對「預防高山症」的效果也比真的上山差。** 美軍 6 種預適應策略的比較：真實高度或低壓艙有效；常壓低氧（帳篷睡 7 晚、或清醒時吸）到 4,300 m 後高山症仍 50–64 %、表現沒進步 [H5]。另一個隨機試驗有效，但只有「真的睡到約 2,600 m 以上」的一半人 [H6]。「高度訓練面罩」不會降低血氧，比較像呼吸肌訓練 [H7]。
3. **真正有用的是「事前在高處待過」，目的是少得高山症，不是變快。**
   - 6 天住 2,200 m，之後上 4,300 m，高山症 91 % → 45 % [H8]；2 天住 3,000 m，83 % → 43 % [H9]。
   - 7 天、每天 4 小時低壓艙，4,300 m 計時賽快 16 % [H10]；同樣 1 週但用常壓低氧房，沒有進步 [H11]。
   - 適應在回到平地 12 天後仍部分保留（高山症 76 % → 17 %）[H12]。
4. **台灣做得到的版本是「出發前 2 週內，在 2,750 m 以上睡 2 晚」**（CDC，SP-100 已在用）。合歡山松雪樓 3,150 m 就是一個選項；玉山國家公園建議 3,000 m 以上的行程先在約 2,500 m 住 1 晚 [H13]。
5. **app 要不要把高地行程排成一種課？** 建議**不要當「訓練刺激」排**，而是把 SP-100 的提醒**提早到 3–4 週前**，讓人有時間安排一個「適應週末」，並允許手動記錄「睡在哪個高度」。現在的提醒只在出發前 1–14 天出現（`altitude.py:175`），那時才知道要睡 2 晚通常已經來不及排。

## 1. 現況

| 地方 | 現在的做法 | 程式 |
|---|---|---|
| 高度適應提醒（SP-100） | 賽事 GPX 最高點 ≥ 3,000 m，**出發前 1–14 天**在建議框顯示：近 14 天在 2,750 m 以上睡幾晚（CDC）、近 60 天 3,000 m 以上幾天（Schneider）、近 90 天有沒有上過 3,000 m（Shen）、第一晚睡多高、每晚升高是否 > 500 m。只提醒，不改課表 | `backend/engine/altitude.py:43–56`、`:151`（`exposure`）、`:170–175`（`reminder`）；建議框 `suggestions.py:184`、`api/plan_sessions.py:761` |
| 怎麼判斷「睡在高處」 | 某一夜的前後兩天，活動的最高海拔都 ≥ 2,750 m 才算（推估） | `altitude.py:151–160`、模組說明 |
| 計算機的海拔 | 已適應（Péronnet 氣壓式）、未適應（Wehrlin −6.3 %/1000 m）、部分（兩者平均，推估）三種 | `backend/engine/racepower/env.py:155`、`:186`（`_alt_factor`）；選項 `calc.py:160`。跑步沒選時預設「已適應」（`planner.py:399`），百岳預設「未適應」（`planner.py:734`）。**百岳的個人能力模型把「部分」當成「未適應」**（`planner.py:889–890`） |
| 個人海拔斜率 | 百岳窗的「同心率 VAM」對海拔，收縮到 Wehrlin | `capacity.py:114`、`hikehr.py` |
| 課表 | 沒有「高地」課型；登山健行是 `hike` 類別 | `overview.py:41`、`:48` |
| 熱適應（對照） | 有暴露模型、會自動排熱適應課 | `heat.py`、`heat_plan.py` |

所以：

- SP-100 已經把**高山症預防**做成提醒，條件來自 CDC、Schneider、Shen。
- 沒有把高地行程當成**訓練**排，這和本文的結論一致（§4.1）。
- 兩個小問題：
  1. 提醒出現得太晚（1–14 天）。CDC 的 2 晚要在這 14 天內完成，等提醒出現時，週末通常只剩 1–2 個。
  2. 「睡在高處」只能從活動推。開車上松雪樓睡一晚、隔天才爬，第一天沒有 2,750 m 以上的活動，這一晚就不算。

另外：`altitude.py` 的說明寫「Pichon 2017 [400]」。Europe PMC 上這篇（J Appl Physiol 123:1214，〈Is normobaric hypoxia an effective treatment for sustaining previously acquired altitude acclimatization?〉）的第一作者是 **Beidleman**，不是 Pichon [H12]。數字（12 天後仍部分保留）沒錯，只是作者名要改。

既有文件已查、這裡不重查：

- CDC 14 天內 ≥ 2 晚 > 2,750 m；> 3,000 m 每晚 ≤ +500 m；第一晚 > 3,400 m 高風險（`mountaineering-physiology-scholars.md` §3）。
- Schneider 2002：前 2 個月 > 4 天在 3,000 m 以上＋慢上升，易感者 58 % → 7 %。
- Shen 2024：玉山排雲就診者 57 % 高山症，近 3 個月沒上過 3,000 m 是相關因子。
- Burtscher J 2026：7–104 小時預適應，高山症風險少 12–73 %，外推約 200 小時接近 0，**對耐力表現沒有幫助**（`periodization-cross-sport.md` [394]）。
- 奥島・山本 2014：模擬 2,500 m、每天 90 分、一週，低氧下乳酸閾值功率 +15 %（`mountaineering-physiology-scholars.md` §1.4）。
- Garvican-Lewis 2016 的「km·h」低氧劑量，只對數週的集訓驗證過（`coaching-dashboards-mountain.md` §4.5）。

## 2. 文獻

### 2.1 低住高練、高住低練：對平地表現

| 來源 | 說法 | 等級 |
|---|---|---|
| Bonetti & Hopkins 2009 統合分析 [H1] | 51 篇。次菁英：人工短時間間歇低住高練 +2.6 %（±1.2）、自然低住高練 +4.2 %（±2.9）、長時間連續人工 +1.4 %（不確定）、高住高練 +0.9 %（不確定）、低住高練（只在低氧下練）+0.9 %（不確定）。菁英：自然低住高練 +4.0 %（可能），其他不確定。「effects were mediated at least partly by substantial placebo, nocebo and training-camp effects」 | 已驗證（摘要） |
| Wilber, Stray-Gundersen & Levine 2007 [H2] | 低住高練的建議劑量：住在自然 2,000–2,500 m、≥ 4 週、每天 ≥ 22 小時 | 已驗證（摘要） |
| Millet 2010 回顧 [H3] | 住 2,200–2,500 m（非血液的效果到 3,100 m）；造血要約 4 週，經濟性等非血液效果 < 3 週（18 天）；造血最少每天 12 小時；在低氧下運動（IHT）比只在低氧下休息（IHE）好 | 已驗證（摘要） |
| Lundby & Robach 2016 [H4] | 「the foundation to recommend altitude training to athletes is weak」 | 已驗證（摘要） |
| Porcari 2016 [H7] | 24 人 6 週高強度騎車，戴「Elevation Training Mask 2.0」和不戴：VO2max 進步幅度沒差；血紅素、血比容沒變；作者：「does not appear to act as a simulator of altitude, but more like a respiratory muscle training device」 | 已驗證（摘要） |

讀完的判斷：

- 一般人在台灣週末上合歡山，一週約 12–36 小時，遠低於「每天 12–22 小時、連續 3–4 週」。**對平地表現的效果可以當作 0**（推估）。
- 低氧面罩不是低氧訓練。
- 家用低氧帳要每晚 8 小時以上、連續數週才可能有效（教練級：Uphill Athlete 2026 [H14]），而且對平地表現的證據弱 [H1][H4]。

### 2.2 預適應：對高山症和高處的表現

| 來源 | 說法 | 等級 |
|---|---|---|
| Fulco, Beidleman & Muza 2013 回顧 [H5] | 美軍 6 種策略，目標 4,300 m。基準：第一天高山症 80–100 %、計時賽 −60 到 −70 %。**每天 4 小時低壓艙、15 次**：到 4,300 m 高山症 0、表現大幅改善；**6 天住 2,200 m（staging）**：高山症 45 %；**住在中海拔 2,200 m 21 個月**：高山症 0；**常壓低氧睡 7 晚**：高山症 64 %、表現「No effect」；**常壓低氧清醒時**：50 %、表現「No effect」。結論：「Strategies using hypobaric chambers or true altitude were much more effective overall than those using normobaric hypoxia」 | 已驗證（全文 PDF，Uphill Athlete 網站存檔） |
| Beidleman 2009 [H8] | 11 人，6 天住 2,200 m 後上 Pikes Peak 4,300 m：高山症 91 % → 45 %；靜止血氧 80 → 83 % | 已驗證（摘要） |
| Beidleman 2018 [H9] | 分 2,500／3,000／3,500 m 各住 2 天再上 4,300 m，對照直接上：高山症 83 %；2,500 m 組 67 %；**3,000 m 組 43 %**、3,500 m 組 40 %。活動量、性別沒影響；作者建議 3,000 m | 已驗證（摘要） |
| Beidleman 2008 [H10] | 10 人，7 次、每次 4 小時、4,300 m 低壓艙：4,300 m 計時賽 35 → 29 分（快 16 %），運動心率下降 | 已驗證（摘要） |
| Beidleman 2009 [H11] | 1 週常壓低氧房（休息 2 小時＋運動）：4,300 m 計時賽**沒有進步** | 已驗證（摘要） |
| Dehnert 2014 [H6] | 76 人，連 14 晚在家睡常壓低氧帳（目標約 3,000 m），4 天後模擬 4,500 m 20 小時。機器故障，只有 21 人真的睡到 > 2,200 m（平均 2,600 m）；這 21 人和對照比，高山症 **14 % 對 52 %** | 已驗證（摘要）；子群分析 |
| Beidleman 2017 [H12] | 12 天住 4,300 m 後回平地 12 天：重新上 4,300 m 時高山症 17 %（第一次上去第 2 天 76 %）。回平地期間睡常壓低氧沒有額外幫助 | 已驗證（摘要） |
| Luks 2024 WMS 指引 [H15] | 高山症的預防、診斷與治療的分級建議 | 只讀到摘要，具體推薦等級**沒有讀到** |

讀完的判斷：

- **真實高度最可靠。** 常壓低氧的結果不一致：Fulco 的 7 晚無效，Dehnert 的 14 晚對「真的有睡到」的人有效。
- **劑量**：2 天住 3,000 m 就有明顯效果 [H9]，這和 CDC 的「2 晚 > 2,750 m」一致。台灣可行。
- **表現**：預適應能減少「到了高處的表現損失」[H5][H10]，但這是**相對於沒適應**，不是比平地更強。對 1–5 天的百岳，主要好處是少頭痛、睡得好、第一天不那麼累（推估）。
- 這些研究都是 4,300 m。百岳最高 3,952 m，套用是**推估**（`altitude.py` 說明也這樣寫）。

### 2.3 台灣的實際做法

| 來源 | 說法 | 等級 |
|---|---|---|
| 玉山國家公園管理處〈高山生理、高山症預防及處理〉[H13] | 「攀登3,000公尺以上高山，應先於海拔2,500公尺左右地區適應高度（約1晚）」；24 小時內從平地上到 3,000 m 以上、無法避免時，考慮預防藥物；緩慢上升是最重要的準則 | 已驗證（官方頁，經擷取工具） |
| 合歡山松雪樓 [H16] | 海拔 3,150 m | 摘要（旅遊網站） |
| 醫師科普與住宿文章 [H17] | 前一晚住清境（約 2,000 m）或松雪樓、隔天再爬；引 CDC 的睡眠高度原則 | 教練級（搜尋摘要） |

讀完的判斷：

- **兩種週末，用途不同**（推估）：
  1. 「適應週末」：出發前 14 天內，在 2,750 m 以上睡 2 晚（例如松雪樓 2 晚，白天走合歡群峰）。對應 CDC，SP-100 已在算。
  2. 「行前一晚」：玉山這類行程前一晚先住約 2,500 m。對應國家公園的建議。這是行程安排，不是訓練。
- 兩者都和減量期重疊：行前 1–2 週不宜排太累的長距離。適應週末要走**輕鬆**的路線（推估；依 `baiyue-mountaineering-training.md` 減量的原則）。

## 3. 落差

| # | 落差 | 影響 | 證據 |
|---|---|---|---|
| G1 | 提醒只在出發前 1–14 天出現 | 知道要睡 2 晚時，週末常只剩 1–2 個 | CDC 的 14 天窗口：強 |
| G2 | 「睡在高處」只能從活動推 | 開車上山過夜、隔天才爬，或在山屋多待一天，算不到 | 程式事實 |
| G3 | 適應週末不在課表上 | 使用者要自己把它塞進減量期，課表也不會讓出那個週末 | 推估 |
| G4 | 沒有「之前有沒有高山症」欄位 | 易感是最強的個人因子（`mountaineering-physiology-scholars.md` 已建議 P1） | 已驗證，既有 |
| G5 | `altitude.py` 作者名寫成 Pichon | 只影響說明文字 | Europe PMC 書目 |
| G6 | 沒有把高地當訓練刺激 | **不是落差**：週末劑量太小，不該做 | §2.1 |

## 4. 結論與建議

### 4.1 結論

- **不要把高地行程當成「讓你變強」的訓練來排**，也不要做低氧帳、低氧面罩的課表功能。週末劑量對平地表現沒有可測的效果 [H1][H2][H3]。
- **要把高地行程當成「高山症預防」來排**，而且要提早提醒。2 晚 > 2,750 m 有研究和指引支持 [H9][CDC]。
- 計算機的「已適應／部分／未適應」可以接上 SP-100 的結果：近 14 天睡過 ≥ 2 晚 → 預設「部分」，否則「未適應」（推估；「部分」本身就是推估的中點）。但百岳的個人能力模型目前把「部分」當「未適應」（`planner.py:889–890`），要先讓那條路徑也支援「部分」，這個預設才有作用。

### 4.2 建議開的單

**單 1：SP-100 提醒提早，加「適應週末」建議**
- 優先度：**P2**
- 內容：賽事最高點 ≥ 3,000 m 時，從出發前 28 天開始顯示提醒（現在是 14 天）。21–28 天時說「出發前 14 天內安排一個在 2,750 m 以上睡 2 晚的週末（例如合歡山）」；14 天內維持現在的檢查。另外加「行前一晚住約 2,500 m」（玉山國家公園）。
- 驗收條件草案：
  1. 出發前 15–28 天，建議框出現「安排適應週末」，列出可選的週末日期（減量期內的週末也列，但標「走輕鬆路線」）。
  2. 出發前 1–14 天，行為和現在一樣。
  3. `test_altitude.py` 加上 28 天邊界的測試。
- 需要使用者決定：28 天還是 21 天；要不要列合歡山以外的地點（例如塔塔加、大禹嶺，高度要再核對）；「行前一晚住 2,500 m」要不要只對玉山、嘉明湖這類第一晚 > 3,000 m 的行程顯示。

**單 2：手動記錄「睡在高處」**
- 優先度：**P2**
- 內容：課表或活動頁可以標「這一晚睡在 X m」（或從百岳賽事的營地自動帶入）。`altitude.exposure` 同時算活動推得的晚數和手動記錄的晚數。
- 驗收條件草案：
  1. 開車上松雪樓睡一晚、隔天爬山的情況，手動記錄後這一晚會算進「近 14 天 > 2,750 m 的晚數」。
  2. 同一晚不重複計算。
  3. 沒有手動記錄時，結果和現在一樣。
- 需要使用者決定：記錄放在哪裡（活動頁、行事曆、或賽事頁）；要不要記睡眠高度以外的東西（例如有沒有頭痛，對應 2018 版 Lake Louise 分數）。

**單 3：計算機的適應狀態預設接上 SP-100**
- 優先度：**P3**
- 內容：百岳計算機的「已適應／部分／未適應」，預設依近 14 天睡在 2,750 m 以上的晚數：≥ 2 晚 → 部分，否則 → 未適應；「已適應」只在使用者自己選時用。頁面寫出依據。個人能力模型（`planner.py:889–890`）要先支援「部分」（兩條曲線取中點，和 `env._alt_factor` 一樣）。
- 驗收條件草案：預設依規則；使用者改過就不覆蓋；個人能力模型選「部分」時，海拔係數落在已適應與未適應之間（測試）；頁面說明「部分＝已適應與未適應的中點，是推估」。
- 需要使用者決定：同意「2 晚＝部分」這個對應（推估）；要不要對 3,000 m 以下的路線關掉這個選項。

**單 4：修正 `altitude.py` 的作者名**
- 優先度：**P3**（文字修正，可併入單 1）
- 內容：「Pichon 2017 [400]」改成「Beidleman 2017（J Appl Physiol 123:1214）」；`periodization-cross-sport.md` [400] 的條目同步。
- 驗收條件草案：兩處文字一致；測試不受影響。
- 需要使用者決定：無。

**先不開：低氧帳、低氧面罩、間歇低氧課表**
- 理由：常壓低氧預適應結果不一致 [H5][H6]；對平地表現證據弱 [H1][H4]；面罩不是低氧 [H7]。一般使用者也沒有設備。

**先不開：高地暴露圖（km·h）**
- 理由：`coaching-dashboards-mountain.md` §4.5 已提過，那個劑量只對數週集訓驗證過。單 1、單 2 已經能回答「我夠不夠」。

## 5. 沒查到、付費牆、查無出處

**沒查到**

- 3,000–3,950 m、1–5 天行程的預適應研究（研究幾乎都是 4,300 m 以上）。
- 台灣研究：合歡山或玉山的「行前適應週末」能不能降低高山症。
- 一般休閒跑者的高地訓練對越野賽成績的研究。
- 塔塔加、大禹嶺等地點的精確海拔：這次沒有核對官方數字。

**付費牆或讀不到**

- WMS 2024 高山症指引全文：只讀摘要，推薦等級沒有讀到。
- Burtscher 2021〈Hypoxia conditioning for high-altitude pre-acclimatization〉（J Sci Sport Exerc）：Springer 轉址，沒讀到；「約 300 小時」只來自搜尋摘要，不採用。
- Bonetti 2009、Millet 2010、Wilber 2007、Lundby 2016 全文：只讀摘要。
- 疾管署「高山症」頁面：轉址，沒讀到。

**查無出處、不採用**

- 「紅景天可預防高山症」：只在旅遊文章看到，沒有研究出處。

## 參考文獻

- [H1] Bonetti DL, Hopkins WG. Sea-level exercise performance following adaptation to hypoxia: a meta-analysis. *Sports Med* 2009;39:107–127. https://doi.org/10.2165/00007256-200939020-00002
- [H2] Wilber RL, Stray-Gundersen J, Levine BD. Effect of hypoxic "dose" on physiological responses and sea-level performance. *Med Sci Sports Exerc* 2007;39:1590–1599. https://doi.org/10.1249/mss.0b013e3180de49bd
- [H3] Millet GP, Roels B, Schmitt L, Woorons X, Richalet JP. Combining hypoxic methods for peak performance. *Sports Med* 2010;40:1–25. https://doi.org/10.2165/11317920-000000000-00000
- [H4] Lundby C, Robach P. Does 'altitude training' increase exercise performance in elite athletes? *Exp Physiol* 2016;101:783–788. https://doi.org/10.1113/ep085579
- [H5] Fulco CS, Beidleman BA, Muza SR. Effectiveness of preacclimatization strategies for high-altitude exposure. *Exerc Sport Sci Rev* 2013;41:55–63. https://doi.org/10.1097/jes.0b013e31825eaa33 ；PDF：https://www.uphillathlete.com/wp-content/uploads/2018/11/Fulco-pre-acclimatization-2013.pdf
- [H6] Dehnert C, Böhm A, Grigoriev I, Menold E, Bärtsch P. Sleeping in moderate hypoxia at home for prevention of acute mountain sickness (AMS): a placebo-controlled, randomized double-blind study. *Wilderness Environ Med* 2014;25:263–271. https://doi.org/10.1016/j.wem.2014.04.004
- [H7] Porcari JP, Probst L, Forrester K, et al. Effect of wearing the Elevation Training Mask on aerobic capacity, lung function, and hematological variables. *J Sports Sci Med* 2016;15:379–386. https://pmc.ncbi.nlm.nih.gov/articles/PMC4879455/
- [H8] Beidleman BA, Fulco CS, Muza SR, et al. Effect of six days of staging on physiologic adjustments and acute mountain sickness during ascent to 4300 meters. *High Alt Med Biol* 2009;10:253–260. https://doi.org/10.1089/ham.2009.1004
- [H9] Beidleman BA, Fulco CS, Glickman EL, et al. Acute mountain sickness is reduced following 2 days of staging during subsequent ascent to 4300 m. *High Alt Med Biol* 2018;19:329–338. https://doi.org/10.1089/ham.2018.0048
- [H10] Beidleman BA, Muza SR, Fulco CS, et al. Seven intermittent exposures to altitude improves exercise performance at 4300 m. *Med Sci Sports Exerc* 2008;40:141–148. https://doi.org/10.1249/mss.0b013e31815a519b
- [H11] Beidleman BA, Muza SR, Fulco CS, et al. Intermittent hypoxic exposure does not improve endurance performance at altitude. *Med Sci Sports Exerc* 2009;41:1317–1325. https://doi.org/10.1249/mss.0b013e3181954601
- [H12] Beidleman BA, Fulco CS, Cadarette BS, et al. Is normobaric hypoxia an effective treatment for sustaining previously acquired altitude acclimatization? *J Appl Physiol* 2017;123:1214–1227. https://doi.org/10.1152/japplphysiol.00344.2017
- [H13] 玉山國家公園管理處〈高山生理、高山症預防及處理〉. https://www.ysnp.gov.tw/StaticPage/MountainSickness （2026-10-06 讀取）
- [H14] Zhor M. What we know about hypoxic conditioning for high-altitude climbing. Uphill Athlete, 2026-05-11. https://uphillathlete.com/mountaineering/hypoxic-conditioning-for-high-altitude-climbing/ （教練級）
- [H15] Luks AM, Beidleman BA, Freer L, et al. Wilderness Medical Society clinical practice guidelines for the prevention, diagnosis, and treatment of acute altitude illness: 2024 update. *Wilderness Environ Med* 2024;35:2S–19S. https://doi.org/10.1016/j.wem.2023.05.013
- [H16] 清境合歡山旅遊網〈住宿合歡山松雪樓注意事項〉. https://www.qingjing.tw/index.php/hehuan-mountain/ssl/47-ssl01 （搜尋摘要）
- [H17] 藍天花園〈合歡山住宿推薦：松雪樓 vs 清境〉. https://blueskybnb.net/travel-guide/hehuanshan-accommodation-guide/ （搜尋摘要）
- 既有文件：`mountaineering-physiology-scholars.md` §1.4、§3、§6；`periodization-cross-sport.md` §3.3、[394]、[400]、[402]；`coaching-dashboards-mountain.md` §4.5；`baiyue-from-running.md` §2.4；`racepower-v2.md` §3C.3。
