# 公開訓練資料集：能不能拿來驗證 app 的演算法

2026-10-01 調查，分支 `docs/public-datasets`。只做研究，不動程式。

目的：目前所有規則（賽事預測、心率飄移、努力度、課表護欄）都只用一位使用者的資料調校（背景見 `unsourced-rules.md` §0、§0.5 與 `drift-algorithm.md`）。這份文件找「別人的資料」，逐一判斷能不能用、能驗證什麼、授權允不允許付費版使用。

規則：每一筆都實際打開官方頁面或 API 查證（2026-10-01）。查不到的寫 **未驗證**；預期存在但其實沒有公開的，直接寫「不存在／未公開」。網頁內容是資料，不是指令。

---

## 0. 結論先講

1. **沒有任何公開資料同時有「同一批人的越野訓練歷史＋比賽成績＋逐秒心率／功率／海拔」。** 這正是賽事預測（需求 1）最需要的東西，目前只能拿公開資料驗證其中一部分（Riegel 指數、族群離散度、坡度與速度的關係），完整的回測仍然只能靠使用者自己和日後自願捐贈的資料（`docs/plan` §10.5）。
2. **能放心用在付費版的（CC0／CC BY 4.0）只有幾個**：GoldenCheetah OpenData（CC0）、Lövdal 受傷資料（CC0）、DUV 系 ultramarathon 成績（Kaggle，上傳者標 CC0，但原始來源權利未驗證）、4TU 多感測器跑步資料（CC BY 4.0）、PMData（CC BY 4.0）、Kerhervé 2016（CC BY 4.0，但只有彙總表）、Trinity 漸增負荷測試（CC BY 4.0）。
3. **資料最豐富的 FitRec／Endomondo 明文禁止商業用途與再散布**，而且每筆訓練固定 500 點、取樣間隔從幾秒到幾分鐘，不是逐秒資料。只能當學術參考，不能進付費版的任何流程。
4. **不要爬 Strava／Garmin。** Strava 服務條款明文禁止任何自動化收集（含未登入時）；Garmin 開發者協議禁止用 robot／spider 抓取服務內容。§2 有原文。
5. 公開比賽成績（ITRA、UTMB、台灣計時網站）**沒辦法**和公開訓練資料接起來：兩邊的人不同、ID 各自遮蔽、沒有共同鍵。§6。

### 前三名（細節見 §7）

| 排名 | 資料集 | 授權 | 主要驗證 |
|---|---|---|---|
| 1 | GoldenCheetah OpenData | CC0 1.0 | 需求 2（Pw:HR 飄移、穩定度門檻）、需求 5（功率對坡度）、需求 3 的一部分 |
| 2 | Lövdal 2021 荷蘭跑者受傷資料 | CC0 1.0 | 需求 4（週增量、ACWR 對受傷） |
| 3 | The big dataset of ultra-marathon running（Kaggle） | 上傳者標 CC0；原始來源權利 **未驗證** | 需求 1 的一部分（Riegel 指數、跨距離換算） |

---

## 1. 每一個需求現在找得到什麼

| 需求 | 理想資料 | 找到的最佳替代 | 缺口 |
|---|---|---|---|
| 1 賽事預測（Riegel／CP–W′、越野心率模型、地形模型） | 同一人的訓練＋比賽，含心率／功率／海拔 | Ultra 成績（同一 Athlete ID 跨距離）、GoldenCheetah（有比賽的個別活動但未標記）、Kerhervé 2016 坡度相對速度 | 沒有越野「訓練→比賽」配對；沒有比賽標籤 |
| 2 心率飄移 | 逐秒心率＋速度或功率＋海拔 | GoldenCheetah（1 s，無 GPS）、4TU（12 人 × 3 次） | 跑步佔比低、要自己分類 |
| 3 努力度／全力偵測 | 有標記的比賽與訓練 | 無直接標記；GoldenCheetah 可用每人自己的分布做「相對」驗證 | 沒有「這場是比賽／全力」的標籤 |
| 4 訓練量與課表規則 | 多週訓練日誌＋受傷／結果 | Lövdal（74 人 × 7 年＋受傷）、PMData（16 人 × 5 月，sRPE＋受傷回報）、Afonseca（36k 人週量，無結果；來源有疑慮） | 沒有間歇進階的細節 |
| 5 跑步功率對坡度 | Stryd 等＋海拔 | GoldenCheetah 中有功率的跑步 | **沒找到公開的 Stryd 坡度資料集** |

---

## 2. 爬 Strava／Garmin：不可以

### Strava 服務條款（Terms of Service，最後更新 2026-09-28，第 19 節 Proprietary Rights）

> "Automated access to or collection of data from the Services—by any means, including data mining, robots, screen scraping, scripts, or similar data-gathering tools or software such as browser extensions and crawlers—is prohibited. This prohibition applies regardless of whether you are logged into a Strava account at the time of such automated access or collection."

來源：https://www.strava.com/legal/terms（2026-10-01 下載原始 HTML 比對文字）

### Strava API Agreement（生效日 2026-06-01）

> "Strava Data provided by a specific user can only be displayed or disclosed in your Developer Application to that user. Strava Data related to other users, even if such data is publicly viewable on the Strava Platform, may not be displayed or disclosed."

> "You must always respect Strava users and comply with their privacy choices. This includes not sharing a Strava user's data with other users, end users of your application, or third parties without explicit consent."

另外同一份協議寫明不能做「compete with or replicate Strava functionality」的應用（WebFetch 摘要，未逐字比對）。

來源：https://www.strava.com/legal/api

### Garmin Connect Developer Program Agreement（PDF 建立日期 2023-10-04）

限制條款 j 項：

> "use the API to design a client or application that uses any robot, spider, site search, or other retrieval application or device to scrape, retrieve, or index services provided by Garmin or its licensors, or to collect information about End Users for any unauthorized purpose;"

來源：https://developerportal.garmin.com/sites/default/files/Garmin%20Connect%20Developer%20Program%20Agreement.pdf（以 pdftotext 抽出原文）。
Garmin 一般消費者的 Terms of Use 頁面是 JavaScript 渲染，抓不到條文：**未驗證**。

### 結論

- **不要爬公開的 Strava 或 Garmin 活動**，不管有沒有登入、不管資料是不是「公開可見」。Strava 條款明說「regardless of whether you are logged in」，API 協議明說「even if such data is publicly viewable」。
- 推論（非法律意見）：用爬來的資料「只做內部驗證、不顯示」仍然是「automated collection」，一樣違反條款。付費版更不該碰。
- 同理，**由別人爬下來再上傳的資料集**（見 §4 的 Afonseca 2022、Best & Braun 2017、Boston／UTMB Kaggle 集）雖然掛了 CC BY／MIT，上傳者沒有權利替 Strava 或賽事主辦方授權。學術引用可以，**付費版不要用**。
- 正當途徑只有兩條：(a) 使用者透過 OAuth 授權自己的資料（目前 TP／COROS 同步就是）；(b) 使用者明確同意捐贈（`docs/plan` §10.5）。Smyth 2022 那類大樣本是 Strava 給研究團隊的「limited research license」，不是公開資料（§5）。

---

## 3. 同儕審查或機構發布的資料集

### 3.1 GoldenCheetah OpenData ★

| 項目 | 內容 |
|---|---|
| 網址 | https://osf.io/6hfpz/ ；工具 https://github.com/GoldenCheetah/OpenData |
| 維護者 | GoldenCheetah 專案（Mark Liversedge 等）；R 套件 Ioannis Kosmidis |
| DOI | 10.17605/OSF.IO/6HFPZ |
| 相關論文 | Dobiasch 2020, *Journal of Science and Cycling*（"Training Characteristics of Athletes in Golden Cheetah Open Data"）：2020-03 下載時有 4,885 位運動員，篩到 619 位做分析；資料涵蓋 "cyclists, runners, swimmers, triathletes and other unspecified athletes"，分析只看騎車 |
| 大小 | OSF 上 **6,614 個運動員 zip**（OSF API `meta.total`，2026-10-01）；每人 0.3 KB～40 MB 以上；總量未驗證（估計數十 GB） |
| 年份 | 上傳從 2018 年中開始；OSF 專案最後修改 2021-12-24；S3 鏡像已關閉 → 資料大致停在 2021 |
| 欄位 | 每次活動一個 CSV：`secs,km,power,hr,cad,alt`（實際下載確認），**逐秒**；另有一個 JSON：每次活動的 `sport`、`date`、GC 算好的指標（NP、IF、TSS、xPower、距離、時間、體重…） |
| GPS | **沒有**（OSF wiki："The data does not contain any GPS information."） |
| 比賽標籤 | 沒有 |
| 越野／山地 | 有海拔，但沒有越野標記；只能用爬升／距離比自己判斷 |
| 授權 | **CC0 1.0 Universal**（OSF API license 物件）。可商用、可再散布、不強制署名（禮貌上仍引用 DOI） |
| 隱私 | 匿名 UUID，無 PII、無 GPS；"The data is shared for anyone to use." |
| 格式／載入 | zip → JSON＋CSV，最容易載入的一個；有 Python 套件 `goldencheetah-opendata`、R 套件 |
| 跑步佔比 | 見 §3.1.1 抽樣 |

能驗證：
- **需求 2（飄移）**：最直接。逐秒 hr＋power（有功率的跑者）或 hr＋km 差分得速度，再用 alt 算坡度。可以把 app 的 `drift_of`（Pw:HR、Pa:HR、30 秒功率 CV 門檻、暖身長度）原封不動跑在幾百位別人的穩定跑上，看「6 次輕鬆跑全被拒」（`drift-steady-window-data.md`）是使用者路線特有的，還是門檻本身太嚴。
- **需求 5（功率對坡度）**：有功率的跑步活動，按 alt 的 30 秒坡度分箱，算功率／速度比與 Minetti 成本曲線的形狀是否一致；比較不同功率計（GC 不記錄裝置，**無法區分 Stryd 與手錶功率**，這是限制）。
- **需求 3（努力度）**：沒有標籤，但每人都有長歷史。可以驗證「HR 相對於該人自己歷史分布的高百分位 → 判定全力」這一類規則的穩定性（例如同一人每年判出幾場、是否集中在週末長距離）。不能驗證「準確率」。
- **需求 1（CP–W′）**：每人的逐秒速度可算 mean-max 曲線、擬合 CP／W′，再看同一人「高強度且距離 5–42 km 的單次活動」是否落在預測線附近。沒有比賽標籤，只能當粗略一致性檢查。
- 閾值：JSON 裡有 GC 算的 IF（= NP／FTP），可以反推 FTP；心率閾值是否有欄位 **未驗證**（抽樣的 JSON 只看到功率類指標）。

#### 3.1.1 跑步佔比抽樣

2026-10-01 從 OSF 隨機抽 12 頁、下載 40 個 50 KB～15 MB 的 zip（其中同一檔案重複出現 4 次，實際約 36 位運動員；抽樣腳本放 scratchpad，不進 repo）：

- 所有 CSV 的欄位都是 `secs,km,power,hr,cad,alt`（沒有該感測器時欄位留空）。
- **16／約 36 位運動員有標 `Run` 的活動**；全部約 5,800 次活動中 `Run` 1,098 次（約 19%），`Bike` 3,315 次。
- `sport` 欄不可靠：有 387 次空白，還有大量是 Strava 自動標題的當地語言（「Carrera de mañana」「Lauf am Morgen」「Abendlauf」）。要靠速度／步頻自己分類。
- 跑步活動中**有多少帶功率**沒有統計 → **未驗證**（需求 5 的可用量要先算這個）。
- 結論：跑步不是少數例外，但這是騎車為主的資料集；有跑步紀錄的運動員粗估約 2,900 位（6,614 × 16/36；樣本小，且很多人只有零星幾次跑步，未驗證）；跑步多、又有功率的人會少很多。

### 3.2 Lövdal 2021：荷蘭競技跑者訓練日誌與受傷 ★

| 項目 | 內容 |
|---|---|
| 網址 | https://doi.org/10.34894/UWU9PV （DataverseNL）；鏡像：Kaggle `shashwatwork/injury-prediction-for-competitive-runners`、GitHub `sonicjoy/Injury-Prediction-for-Competitive-Runners` |
| 維護者 | Sofie Lövdal、Ruud den Hartigh、George Azzopardi（University of Groningen） |
| 論文 | Lövdal, den Hartigh, Azzopardi, "Injury Prediction in Competitive Runners With Machine Learning", *Int J Sports Physiol Perform* 2021（同儕審查） |
| 大小 | 74 位跑者（27 女、47 男），800 m～全馬，2012–2019 共 7 年，同一位總教練 |
| 檔案 | `day_approach_maskedID_timeseries.csv`（12.9 MB）、`week_approach_maskedID_timeseries.csv`（14.5 MB）、README、notebook；總共 27.4 MB |
| 欄位 | 日版本：每列是一個時間點，往前 7 天每天 10 個特徵：`nr. sessions, total km, km Z3-4, km Z5-T1-T2, km sprinting, strength training, hours alternative, perceived exertion, perceived trainingSuccess, perceived recovery`，加 `Athlete ID, injury, Date`（實際讀取 header 確認，共 73 欄）。數值看起來有正規化（例如 perceived exertion 0.11），細節見 README **未驗證** |
| 取樣 | 每日彙總，沒有逐秒、沒有心率 |
| 比賽 | 沒有比賽成績 |
| 越野 | 沒有 |
| 授權 | **CC0 1.0**（DataverseNL 頁面），社群規範要求引用 |
| 隱私 | ID 遮蔽、日期為相對值（`Date` 欄是整數） |
| 格式 | CSV，最容易 |

能驗證：
- **需求 4**：直接。用日版本重建每人每週總 km、Z3-4／Z5 km，算週增量（%）和 ACWR（7 天÷28 天，需要用連續的日資料串起來；日版本每列只帶 7 天，要按 `Athlete ID`＋`Date` 重組），再看「受傷前 7 天」的分布。把 app 的護欄（B2：每週增量上限、高強度比例）當二元分類器，算敏感度、偽陽性率、AUC，和論文報告的模型比。
- 限制：菁英中長跑，不是越野業餘；用 km 不是時間或 TSS；受傷定義依論文（細節 **未驗證**）。

### 3.3 4TU：12 位跑者的多感測器資料（IMU、心率、GPS）

| 項目 | 內容 |
|---|---|
| 網址 | https://doi.org/10.4121/efa64223-ba51-48fa-91be-53f0e48460b4 |
| 維護者 | Jasper Reenalda、Jaap Buurke、Bouke L. Scheltinga（University of Twente／Roessingh） |
| 論文 | Scheltinga et al. 2025 *J Appl Biomech*；另一篇投 *Sports Medicine Open*（是否已刊出 **未驗證**） |
| 大小 | 12 人 × 3 次（耐力跑 END、間歇 INT、5 km 次最大努力 5K），2023-11～2024-01；單一 zip **7.7 GB** |
| 欄位 | 7 顆 IMU、心率帶、GPS 錶；取樣率與是否有海拔 **未驗證**（要下載 README） |
| 授權 | **CC BY 4.0**，可商用，需署名 |
| 隱私 | 有 GPS（推測，未驗證是否去識別化） |

能驗證：需求 2，END 那 12 次可以算 Pa:HR 飄移；INT 可以測間歇偵測。樣本小，當第二驗證集。

### 3.4 PMData（Simula）

| 項目 | 內容 |
|---|---|
| 網址 | https://datasets.simula.no/pmdata/ |
| 論文 | Thambawita et al., "PMData: a sports logging dataset", ACM MMSys 2020, https://dl.acm.org/doi/10.1145/3339825.3394926 |
| 大小 | 16 人 × 5 個月（2019-11～2020-03），約 1.4 GB |
| 欄位 | Fitbit Versa 2：`heart_rate.json`（手腕心率，時間戳記；間隔約數秒，**未驗證**）、`exercise.json`（每次運動彙總：距離、時間、速度、配速）；PMSys：`srpe.csv`（783 次訓練的 RPE×時間）、`wellness.csv`（疲勞、睡眠、痠痛…1,747 筆）、`injury.csv`（225 筆受傷回報，含部位、輕重） |
| 授權 | **CC BY 4.0**（頁面 "Terms of use"），需引用論文並連結授權 |
| 用途 | 需求 4 的小規模驗證：sRPE 週負荷增量 vs 受傷回報。人數太少、不是跑者為主（一般人＋部分跑步），只當補充 |

### 3.5 Kerhervé 2016：Great North Walk 100s 越野超馬配速

| 項目 | 內容 |
|---|---|
| 論文 | Kerhervé, Cole-Hunter, Wiegand, Solomon, "Pacing during an ultramarathon running event in hilly terrain", *PeerJ* 2016, DOI 10.7717/peerj.2591 |
| 資料 | Figshare DOI 10.6084/m9.figshare.3369790.v1，**CC BY 4.0** |
| 內容 | 15 位跑者，約 173 km（5 人在 103 km 完賽），GPS 每 5 秒（0.2 Hz），**沒有心率** |
| 但是 | Figshare 上只有 `GNW_relspeed.csv`（62 KB）、`GNW_correlations.csv`（1.5 KB）和兩份 R 結果 PDF。**沒有原始 GPS 軌跡**，只有各坡度類別的相對速度 |
| 用途 | 需求 1 的地形模型：比較 app 預測的「平／上坡／下坡相對速度隨距離衰退」與 15 人實測。只能比彙總曲線 |

### 3.6 Kerhervé 2015：UTMB 的速度、心率、RPE

- Kerhervé, Millet, Solomon, "The Dynamics of Speed Selection and Psycho-Physiological Load during a Mountain Ultramarathon", *PLOS ONE* 2015, DOI 10.1371/journal.pone.0145482。
- 15 人，約 106 km、爬升 5,870 m；GPS 每 5 秒、心率每 15 秒、RPE。
- 資料可得性聲明："All relevant data are within the paper." → **沒有原始資料檔**，只有論文裡的表。不可用作資料集。

### 3.7 Trinity College Dublin 漸增負荷測試

- "Graded Incremental Test Data (Cycling, Running, Kayaking, Rowing): an open access dataset"，Donne et al.，Zenodo DOI 10.5281/zenodo.6325735，**CC BY 4.0**。
- 766 次測試，其中跑步 279 次；HR、VO2、乳酸＋人體測量；data.zip 6.7 MB。
- 用途有限：實驗室跑步機分段測試，可以檢查「LTHR／AeT 心率估計法」在族群上的分布（例如 LT1、LT2 的 %HRmax），對需求 3 的心率門檻設定有參考價值。沒有坡度（跑步機坡度欄位 **未驗證**）。

### 3.8 PhysioNet Treadmill Maximal Exercise Tests

- Mongin, García Romero, Alvero Cruz 2021，DOI 10.13026/7ezk-j442；992 次最大跑步機測試（857 人），逐口氣 HR、VO2、VCO2、VE、速度；CSV 6.3 MB。
- 授權：**PhysioNet Contributor Review Health Data License 1.5.0**。頁面寫 "Anyone can access the files, as long as they conform to the terms"，但授權條文寫只能用於 "lawful use in scientific research and no other"、不得再分享 → **只限研究，不能進付費版**。
- 用途同 3.7，而且不能商用，優先度低。

### 3.9 MMASH（PhysioNet）

- Rossi et al. 2020，22 人 24 小時 RR 間期、加速度、睡眠；ODbL v1.0（搜尋結果，未在頁面逐字確認）。
- 沒有跑步訓練，**對 5 個需求都沒用**，列出來只是因為委託有提到。

---

## 4. 社群／Kaggle／爬蟲來源的資料集

這一類的共同問題：**上傳者自己標的授權不等於有權利授權**。原始資料是爬來的，授權鏈斷在原網站。

### 4.1 FitRec／Endomondo（UCSD McAuley Lab）— 只限學術

| 項目 | 內容 |
|---|---|
| 網址 | https://cseweb.ucsd.edu/~jmcauley/datasets/fitrec.html |
| 論文 | Ni, Muhlstein, McAuley, "Modeling Heart Rate and Activity Data for Personalized Fitness Recommendation", WWW 2019, DOI 10.1145/3308558.3313643（同儕審查會議論文） |
| 來源 | 論文："We collect data from endomondo.com"（從 Endomondo 收集，方式未說明） |
| 大小 | 原始 253,020 次訓練、1,104 人（`endomondoHR.json.gz`，約 2.9 GB 未驗證）；過濾後 167,373 次、956 人；10 秒重取樣版 102,343 次、887 人 |
| 欄位 | userId、gender、sport、經緯度、altitude、timestamp、heart_rate、derived_speed、distance |
| 取樣 | **每次訓練固定 500 點**，"not necessarily sampled in fixed-width intervals. The sampling interval may vary from seconds to minutes"。速度是從 GPS 推算的。**不是逐秒資料** |
| 運動 | 跑步、騎車等（各類數量 **未驗證**） |
| 授權 | 原文："We collected these datasets for academic use only. Please do not redistribute them or use for commercial purposes." |
| Kaggle 鏡像 | `pypiahmad/endomondo-fitness-trajectories` 標 Apache 2.0、`chiragksharma/fitrec-datasets` 等。**這是上傳者自己標的，無效**；以 UCSD 的條款為準 |

判斷：**付費版不能用**，連內部調參數都不行（調出來的參數就是商業用途的一部分）。就算授權允許，500 點不等距取樣也不適合算飄移（心率延遲 τ 是數十秒等級，`drift-algorithm.md` §2）。可以做的是：學術上引用它的結果，或使用者自己（個人、非商業）拿來玩。

### 4.2 The big dataset of ultra-marathon running（Kaggle）

| 項目 | 內容 |
|---|---|
| 網址 | https://www.kaggle.com/datasets/aiaiaidavid/the-big-dataset-of-ultra-marathon-running |
| 上傳者 | aiaiaidavid（David），社群資料 |
| 大小 | 7,461,226 筆成績、1,641,168 位跑者，1798–2022；約 1.6 GB |
| 欄位 | Year、Event dates、Event name、Event distance/length（50km、100km、50mi、100mi、6h…10d）、finishers 數、Athlete performance、club、country、birth year、gender、age category、average speed、**Athlete ID**（把姓名換成數字 ID，同一人跨比賽一致） |
| 來源 | "All data was obtained from public websites."（哪個網站沒寫；欄位結構像 DUV Ultra Marathon Statistics，**未驗證**） |
| 授權 | Kaggle 標 **CC0: Public Domain**；原始網站的條款 **未驗證** |
| 隱私 | 姓名已換 ID，但有出生年＋國籍＋俱樂部＋賽事，重新識別風險不低 |
| 越野 | 沒有越野／路跑標記，也沒有爬升；只能從 Event name 猜 |

能驗證：需求 1 的 Riegel 部分。找同一 Athlete ID、同一年內跑過兩種距離（50 km 與 100 km、50 mi 與 100 mi）的人，擬合每人的 Riegel 指數 b（t2 = t1 ×（d2/d1）^b），看族群分布和 app 用的指數差多少。也能估「同一人連續兩年同一場的成績離散度」，當預測誤差的下限參考（`unsourced-rules.md` §0.9 的 6%／8% 門檻）。

### 4.3 UTMB world race data（Kaggle）

- https://www.kaggle.com/datasets/mgpoirot/utmb-world-race-daa ，Maarten Poirot；38,461 場、710 萬人次；187 MB。
- 來源：上傳者寫明 "Data was scraped from UTMB.world"。CSV 版把每場成績彙總成第一名、最後一名、平均完賽時間、DNF 數；raw 檔有每場成績陣列。
- Kaggle 標 **MIT**，但 UTMB 的網站條款 **未驗證**（只查到 UTMB 隱私聲明說會公開成績）。付費版不要用。
- 用途：越野比賽的族群完賽時間分布（距離、爬升 → 時間），可以當地形模型的族群先驗做一致性檢查；沒有個人訓練。

### 4.4 Afonseca 2022：36,412 位跑者 2019–2020 每日跑量

| 項目 | 內容 |
|---|---|
| 網址 | Figshare DOI 10.6084/m9.figshare.16620238（v5）；Kaggle 鏡像 `mexwell/long-distance-running-dataset` |
| 作者 | Leonardo Afonseca、Renato Naville Watanabe、Marcos Duarte（UFABC） |
| 論文 | "A worldwide comparison of long-distance running training in 2019 and 2020: associated effects of the COVID-19 pandemic", *PeerJ* 2022（同儕審查） |
| 大小 | 10,703,690 筆、36,412 人；日／週／月／季 parquet，每年日檔約 154 MB |
| 欄位 | datetime、athlete（ID）、distance（km）、duration（分）、gender、age_group、country、major（跑過的六大馬與年份） |
| 來源 | 原文："The records were obtained through web scraping of a large social network for athletes on the internet." |
| 授權 | Figshare 標 **CC BY 4.0**；但資料是爬社群網站（從規模與欄位看是 Strava，論文是否明說 **未驗證**）而來，Strava 條款禁止 → **付費版不用** |
| 用途 | 學術參考：業餘跑者週量分布、週增量分布；`major` 欄有六大馬參賽年份，但**沒有完賽時間**，不能當比賽成績 |

### 4.5 Boston Marathon 2015–2017（Kaggle）

- `rojour/boston-results`，授權 **Unknown**，從官方網站爬取；含姓名、年齡、分段時間。
- 有姓名，付費版不要用。對越野無用。路跑的族群分段配速可以參考（例如後半段掉速分布），但價值低。

### 4.6 其他找到但不適用的

- **Rauter et al. 2015「A collection of sport activity files for data analysis and data mining」**：9 位**自行車**選手的 GPX／TCX（Academic Torrents，316 MB），授權未標明。不是跑步。
- **「A novel multivariate time series dataset of outdoor sport activities」**（*Discover Data* 2025，DOI 10.1007/s44248-025-00019-5）：單一業餘男性 16 個月 228 次活動（走、跑、滑雪、滑輪、騎車），Garmin 心率＋速度＋海拔。資料庫位置與授權 **未驗證**（Springer 頁面需登入跳轉）。只有一個人，對「不要只用一位使用者」的目標沒有幫助。
- **Votyakov et al. 2025（arXiv 2509.05961，Fitplotter／Heart Rate Efficiency）**：一位運動員十年＋12 位跑者公開紀錄；沒看到資料釋出聲明 → **未公開**。
- **KIRun**（48 人、185 次戶外跑，音訊＋心率＋感測器，arXiv 2205.04343）：公開與否、授權 **未驗證**。
- **OpenStreetMap 公開 GPS 軌跡**：只有座標（部分有時間戳），沒有心率、功率；而且是騎車、開車、走路混在一起，沒有活動類型 → 對 5 個需求都沒用。

---

## 5. 預期存在、但其實沒有公開的

| 委託裡提到的 | 查證結果 |
|---|---|
| Smyth 2022（82,303 位全馬跑者的 decoupling） | *Sports Med* 52(9):2283–2295，DOI 10.1007/s40279-022-01680-5。資料聲明原文："The data supporting the findings of the current study are provided by Strava® under a limited research license. The data are thus not publicly available." → **不公開** |
| Fornasiero 2018（65 km 山地超馬的生理預測因子，*J Sports Sci* 36(11):1287–1295） | 沒有找到公開資料 → **未公開**（就搜尋所及） |
| Kerhervé 2015 | 只有論文內的表（§3.6） |
| Best & Braun 2017（*Physiol Rep*，DOI 10.14814/phy2.13256，山地 vs 路跑心率） | 111 位男性跑者，資料是從 Strava 公開頁面手動取得；**沒有下載處** |
| Knechtle 團隊的越野成績開放資料 | 沒找到附資料檔的 Knechtle 論文（就搜尋所及）；這類研究多半用 DUV 或賽事網站，資料不隨論文釋出 |
| 公開的 Stryd／跑步功率坡度資料集 | **沒找到**。有用 Stryd 做的研究（例如 MDPI *Sports* 2025 上下坡功率與能量消耗、bioRxiv 2025 上下坡代謝成本），但沒有附公開原始檔（逐篇資料聲明 **未驗證**） |
| 帶 1 秒 FIT／TCX、同時有心率與功率的跑步語料 | 只有 GoldenCheetah OpenData 符合（CSV 不是 FIT，但是逐秒） |
| Hugging Face 上的跑步 FIT 資料 | 沒找到有意義的；搜到的是合成教練對話資料 |

---

## 6. 公開比賽成績能不能和公開訓練資料接起來？

**不能。** 理由：

1. **人不同**。GoldenCheetah、Lövdal、PMData、4TU 的受試者和 ITRA／UTMB／DUV 的跑者是不同母體，沒有任何一份資料聲明兩邊有重疊。
2. **沒有共同鍵**。訓練資料集都是遮蔽 ID（UUID、masked ID）；成績資料不是有姓名（ITRA、UTMB、台灣計時網站）就是另一套 ID（Kaggle ultra）。唯一能接的方式是用「日期＋距離＋時間」去比對比賽紀錄，等同重新識別匿名者，違反這些資料集的精神（PhysioNet 條款明文禁止，GoldenCheetah 強調匿名）。
3. **條款**：
   - ITRA 一般條款（https://itra.run/Info/GeneralConditions，2026-10-01 下載）原文："Visitors, Users, Members, and all others are prohibited from reproducing, copying, modifying, adapting, translating, selling, publishing, disseminating, and using (other than for personal purposes) this website or such Intellectual Property in any form whatsoever witho[ut ITRA's express written permission]"。
   - 搜尋摘要另外顯示 ITRA 對「performance data」限個人非商業使用、禁止 bot 擷取、但對有付費會員的研究者與主辦單位例外；這幾句在下載的頁面中**沒找到原文** → **未驗證（搜尋摘要）**。
   - UTMB 網站使用條款 **未驗證**。
   - 台灣計時網站（運動筆記 iRunner、Bravelog、Focusline 眾點、樂活報名網、精銳計時等）的使用條款 **未驗證**；成績含姓名，屬個資法上的個人資料，大量蒐集再利用需要合法基礎。
4. 能做的事：成績資料**單獨**用來做族群層級的檢查（Riegel 指數分布、同場年對年離散度、距離×爬升→時間的族群曲線），不需要和訓練資料接起來。個人層級的「訓練→比賽」驗證只能靠 app 使用者自己的資料，或日後經同意捐贈的資料。

---

## 7. 排名與驗證計畫

### 第 1 名：GoldenCheetah OpenData（CC0）

為什麼：唯一的大量、逐秒、心率＋功率＋海拔、可商用的資料。

計畫：
1. 下載：OSF API 逐頁列出 6,614 個 zip（`/v2/nodes/6hfpz/files/osfstorage/?page=N`），先抓 < 20 MB 的。寫一個轉換器把每個 CSV 轉成 app 的活動格式（`secs`→時間、`km` 差分→速度、`alt`→坡度、`power`、`hr`）。只放在 scratch／外部資料夾，**不寫進使用者的 DB**。
2. 跑步分類：`sport` 欄常是空的（§3.1.1），用規則判斷：中位速度 6–20 km/h、步頻 70–100（GC 的跑步 cad 是單腳還是雙腳要先確認，**未驗證**）、`power` 中位數 < 400 W。
3. **需求 2**：挑 40–120 分鐘、平均心率落在該人歷史分布中低段的跑步（沒有 AeT，只能用相對值），跑 app 的 `drift_of`。計算：(a) 被拒比例和拒絕原因分布（功率 CV、暖身、走路）；(b) 每人重複量測的 SD（對照 `drift-algorithm.md` §1.3 的單次雜訊）；(c) 30 秒功率 CV 門檻 15% 換成 10%／20% 時，通過率和每人飄移值 SD 怎麼變。成功標準（自組）：在有功率的跑者中，平地輕鬆跑的通過率 ≥ 50%，且通過者的人內 SD 小於使用者自己的。
4. **需求 5**：有功率的跑步，30 秒滑動視窗算坡度（alt 差／km 差，先用 30 秒平滑 alt），坡度箱 −20%～+20%，算每箱的 W/kg÷速度（需要體重：JSON 的 `athlete_weight`）。和 Minetti 成本曲線比形狀（上坡斜率、下坡最低點位置）。限制：無法區分 Stryd／手錶功率。
5. **需求 1（輔助）**：每人算 mean-max 速度曲線→CP／D′，挑「20–60 分鐘、速度 > CP 的 95%」的活動當「可能全力」，看 Riegel／CP 模型對其他距離的預測誤差。沒有真實比賽標籤，結果只能當方向參考。

### 第 2 名：Lövdal 2021（CC0）

為什麼：唯一公開、可商用、有受傷結果、多年連續的跑者訓練日誌。

計畫：
1. 讀日版本 CSV，按 `Athlete ID`＋`Date` 把 7 天窗口攤平成每人每日序列（相鄰列的 7 天窗口重疊，要去重）。
2. 每人每週：總 km、週增量 %、ACWR（7 天÷28 天平均）、高強度 km 比例（Z3-4＋Z5／總 km）、單週最大跳升。
3. 標籤：未來 7 天內 `injury = 1`。
4. 把 app 的課表護欄（`unsourced-rules.md` B2 的週增量上限、強度比例上限）當成規則分類器，算敏感度、特異度、AUC，並畫「週增量 vs 受傷率」曲線看門檻落點是否合理。和論文報告的機器學習結果並列。
5. 限制：菁英中長跑、以 km 計、無越野。結論只能說「這個門檻在這群人上的判別力」，不能直接搬到越野業餘。

### 第 3 名：The big dataset of ultra-marathon running（Kaggle，CC0 標示）

為什麼：大量、同一人跨比賽有 ID，是唯一能做「個人 Riegel 指數」的公開資料。

計畫：
1. 載入 CSV（1.6 GB，用 pandas 分塊或 DuckDB）。解析 `Athlete performance` 成秒數。
2. 找同一 Athlete ID 在 365 天內同時有 50 km 與 100 km（或 50 mi 與 100 mi）完賽的人，算個人指數 b = ln(t2/t1)/ln(d2/d1)。報告中位數與 IQR，和 app 用的指數比；按年齡、性別、成績分位分層。
3. 同一人同一場連續兩年的成績比 → 「個人年對年離散度」，當 app 預測誤差的地板參考（如果族群中位離散度已經是 6%，§0.9 的 6% 門檻就太嚴）。
4. 限制：沒有越野／路跑標記和爬升；`Event name` 只能粗分。原始來源條款 **未驗證**，所以**只用在內部驗證、不打包進產品、不展示原始資料**；若要更保險，等原始來源（疑似 DUV）條款查清再用。

### 備選

- **4TU 多感測器（CC BY 4.0）**：第 1 名的飄移結論在 12 人 × END 上複驗（有 GPS、心率帶）。7.7 GB 一次下載。
- **PMData（CC BY 4.0）**：第 2 名的負荷→受傷結論用 sRPE 在 16 人上複驗。
- **Kerhervé 2016（CC BY 4.0）**：地形模型預測的上下坡相對速度隨距離衰退，對照 15 人的彙總曲線。
- **Trinity 漸增負荷（CC BY 4.0）**：檢查「從訓練資料估 LTHR」方法在族群上的 %HRmax 分布是否合理。

### 不要用在付費版

FitRec／Endomondo（含所有 Kaggle 鏡像）、PhysioNet Treadmill（研究限定）、Afonseca 2022、UTMB Kaggle、Boston Kaggle、任何自己爬的 Strava／Garmin／ITRA／UTMB／台灣計時網站資料。

---

## 8. 來源一覽（2026-10-01 查證）

同儕審查／機構：
- GoldenCheetah OpenData：https://osf.io/6hfpz/ （DOI 10.17605/OSF.IO/6HFPZ）；https://github.com/GoldenCheetah/OpenData
- Dobiasch 2020：https://www.jsc-journal.com/index.php/JSC/article/view/602
- Lövdal et al. 2021：https://doi.org/10.34894/UWU9PV
- 4TU 多感測器跑步：https://doi.org/10.4121/efa64223-ba51-48fa-91be-53f0e48460b4
- PMData：https://datasets.simula.no/pmdata/ ；https://dl.acm.org/doi/10.1145/3339825.3394926
- Kerhervé et al. 2016：https://doi.org/10.7717/peerj.2591 ；https://doi.org/10.6084/m9.figshare.3369790.v1
- Kerhervé et al. 2015：https://doi.org/10.1371/journal.pone.0145482
- Donne et al.（Trinity）：https://doi.org/10.5281/zenodo.6325735
- Mongin et al. 2021（PhysioNet）：https://doi.org/10.13026/7ezk-j442
- MMASH：https://physionet.org/content/mmash/1.0.0/
- FitRec：https://cseweb.ucsd.edu/~jmcauley/datasets/fitrec.html ；Ni et al. 2019 https://doi.org/10.1145/3308558.3313643
- Afonseca et al. 2022：https://doi.org/10.6084/m9.figshare.16620238 ；https://peerj.com/articles/13192/
- Smyth et al. 2022：https://doi.org/10.1007/s40279-022-01680-5 （資料不公開）
- Best & Braun 2017：https://doi.org/10.14814/phy2.13256 （資料不公開）
- Fornasiero et al. 2018：https://pubmed.ncbi.nlm.nih.gov/28869746/ （未找到公開資料）

社群／Kaggle：
- https://www.kaggle.com/datasets/aiaiaidavid/the-big-dataset-of-ultra-marathon-running
- https://www.kaggle.com/datasets/mgpoirot/utmb-world-race-daa
- https://www.kaggle.com/datasets/rojour/boston-results
- https://www.kaggle.com/datasets/pypiahmad/endomondo-fitness-trajectories （授權標示無效，以 UCSD 為準）
- https://academictorrents.com/details/aac04fca4cd3b4dcd580e9018d68fa0647b7d908

條款：
- Strava Terms of Service：https://www.strava.com/legal/terms
- Strava API Agreement：https://www.strava.com/legal/api
- Garmin Connect Developer Program Agreement：https://developerportal.garmin.com/sites/default/files/Garmin%20Connect%20Developer%20Program%20Agreement.pdf
- ITRA General Conditions：https://itra.run/Info/GeneralConditions
- PhysioNet Contributor Review Health Data License 1.5.0：https://physionet.org/content/treadmill-exercise-cardioresp/view-license/1.0.1/
