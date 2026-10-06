# 光學心率在越野的資料品質：怎麼找出並濾掉壞資料（SP-200）

> 調查日期：2026-10-06。只做調查，沒有改程式。
> 標記：**已驗證**＝這次讀過原文（全文或官方頁面）；**摘要**＝只看到論文摘要、搜尋摘要或二手轉述；**推估**＝我的延伸或自訂的門檻；**未找到來源**＝找過，沒有找到。
> 既有文件已經寫過的不重複：感測器誤差的整體數字在 `zones-and-thresholds.md` §2.3，鎖步頻規則和 HRpeak「第 3 高」的由來在 `vo2max-session-detection.md` §2.5、§3。這份只補「每趟資料怎麼檢查」。

## 摘要

1. **擁有者現在這支 COROS 錶的資料，壞得不多。** 2025–2026 的 290 次跑步，尖峰、心率突然跳一階、起跑讀太高，合計只出現在約 6% 的跑步。比較常見的是短暫鎖步頻（約 3 成的跑步有 1–5 分鐘）和十幾秒的心率斷訊（約 3 成），但兩者加起來不到移動時間的 1.5%。
2. **舊的 Garmin 錶（2023–2024）差很多。** 越野跑有一半以上出現尖峰（57%）或移動中突然跳一階（47%）。所以「資料品質」主要看**錶**，不是看人。app 給所有人用，不能因為擁有者現在的錶乾淨就不檢查。
3. **app 現有的檢查只用在最大心率**，而且有四套各自的規則（§1.1）。漂移、hrTSS、LTHR 推估、區間時間都直接用原始心率。
4. **對漂移的影響，用擁有者的資料量是很小的**：把尖峰和鎖步頻拿掉，76 次夠長的跑步裡只有 1 次的前後半心率比變動 ≥ 1 個百分點。真正的風險是**偵測不到**的錯誤：長時間穩定的鎖步頻（步頻不變時現有規則測不出來）、持續 1 分鐘以上的緩慢高估（一年 1 次，不是尖峰也不是鎖步頻）。
5. **低溫找不到跑步的研究**。唯一的實驗（10 °C、只測走路）說沒影響；廠商和跑者經驗說會變差。擁有者的資料沒有 8 °C 以下的跑步，沒辦法自己驗。
6. 建議開 4 張單（§3.2）：統一心率清理、漂移加心率品質檢查、活動頁的心率品質標記和「忽略這段心率」、胸帶標記。低溫先不做。

## 1. 現況

### 1.1 app 現在怎麼處理壞心率

| 位置 | 規則 | 用在哪裡 |
|---|---|---|
| `thresholds.estimate_mhr`（`backend/engine/thresholds.py:268–380`，`peak_sustained_hr` 在 :298） | 丟掉 < 30 或 > 220；1 秒內跳 > 15 bpm 算尖峰，丟到回到原本水準；每趟取「撐 5 秒」的最高值；365 天最高那一趟比第二高多 > 5 bpm 就丟掉 | 最大心率的自動推估（`hr_profile.max_hr` 第 3 順位，沒有手錶值時才用） |
| `threshold_confidence.clean_hr`（`backend/engine/threshold_confidence.py:159–212`） | 斷訊 > 5 秒不補；丟掉 < 30 或 > 225；3 秒內升 ≥ 15 bpm 且 30 秒內回來算尖峰；去掉鎖步頻；每趟取「撐 120 秒」的最高值；比設定的最大心率低 > 8 bpm 就提醒 | 閾值可信度（SP-64）的最大心率合理性 |
| `session_stimulus.cadence_lock`、`hr_peak`（`backend/engine/session_stimulus.py:126–143`、:245–250） | 鎖步頻＝60 秒內心率和步頻差 ≤ 3、相關 ≥ 0.8、步頻變動 ≥ 1.5 spm；HRpeak＝365 天每趟 60 秒最高心率的**第 3 高** | 5 區／3 區判讀的心率路線（`workout_review.py:2118`） |
| `racepower.maximal.hrmax_observed`（`backend/engine/racepower/maximal.py:108`） | 每趟撐 120 秒的最高值，取前 5 名的中位數 | 比賽功率的強度換算 |
| 漂移 `drift_of`（`workout_review.py:839`） | 只用有心率的樣本（`workout_review.py:494`），**不濾尖峰、不濾鎖步頻** | 漂移、AeT 測試、間歇解鎖 |
| hrTSS `hr_tss`（`algorithms/wko5_hr.py:28`） | 只跳過沒有值的樣本（和 WKO5 一致） | 沒有功率時的訓練負荷 |
| LTHR 推估 `run_threshold`（`algorithms/threshold_estimate.py:73`） | 不清理 | 閾值推估 |
| 資料修正 `corrections.py`（設定頁） | 可以把任一通道的一段時間清空，但偵測和介面只做功率（`wko5views.py:1184` 預設 `channel="power"`） | 功率尖峰 |
| 文字提醒 | `zone_events.WRIST_NOTE`、`injury_exposure.WRIST`、測試範本「戴胸帶」 | 只提醒，不改計算 |

- 四套最大心率規則的門檻都標**推估**，彼此不一致（1 秒對 3 秒、220 對 225、5／60／120 秒、最高／第 3 高／前 5 中位數）。
- app **不知道哪一趟有戴胸帶**。這次確認：394 個跑步檔的 `device_info` 沒有任何一筆是心率帶（只有手錶本身、`env_sensor`、一次 `stride_speed_distance`）。不確定 COROS 配胸帶時會不會寫進 FIT（**未驗證**，沒有配胸帶的檔可以比）。

### 1.2 研究和廠商怎麼說

| 來源 | 等級 | 說法 | 標記 |
|---|---|---|---|
| Navalta et al. 2020，*PLoS One* 15:e0238569（PMC7458324） | 同儕審查 | 21 人自選配速跑 3.22 km 越野（一半上坡一半下坡）。胸帶接手錶 MAPE 1.9%、CCC 0.96；Garmin Fenix 5 手腕 **MAPE 13.5%、CCC 0.32，偏差 +15.9（偏高）**；前臂帶 5.6%。前臂帶下坡（3.8%）比上坡（6.2%）準。結論：「All photoplethysmography-based (PPG) devices displayed poor heart rate agreement during variable intensity trail running」，戶外要準就戴胸帶 | 已驗證（全文） |
| Pasadyn et al. 2019，*Cardiovasc Diagn Ther* | 同儕審查 | 50 位運動員跑步機 4–9 mph。整體 rc 0.89–0.96，但 **8–9 mph 時沒有一支手腕錶 rc ≥ 0.70** | 已驗證（全文頁） |
| Sartor et al. 2018，*BMC Sports Sci Med Rehabil*（PMC5984393） | 同儕審查 | 199 人、371 小時。跑步時 95.2% 的時間在胸帶 ±10 bpm 內，整體 MAE ≤ 3 bpm。沒有分析延遲 | 已驗證（全文） |
| Gillinov et al. 2017，*MSSE* 49:1697 | 同儕審查 | 跑步機上多數手腕錶可接受（rc 0.88–0.93），胸帶最好 | 摘要（`zones-and-thresholds.md` 已引） |
| Zhang et al. 2020，*J Sports Sci*（統合分析） | 同儕審查 | 手腕光學 MAPE：走路 3.8%、騎車 6.9%、跑步 8.5% | 摘要 |
| Bent et al. 2020，*npj Digit Med* 3:18 | 同儕審查 | 誤差來源是運動、不是膚色；鎖步頻（signal crossover）；運動時各裝置多半**高估** | 鎖步頻引文既有文件已驗證；「高估」是摘要 |
| Gielen et al. 2026，*JMIR Form Res*，DOI 10.2196/85186（PMC12912460） | 同儕審查 | 45 人、10 款裝置（沒有 COROS），23／36／**10 °C** 三種氣候，**氣候沒有顯著影響**；間歇走路讓誤差變大。只測休息和走路、每段 4–6 分鐘、沒測跑步，也沒測 10 °C 以下 | 已驗證（全文） |
| Vermunicht et al. 2026，*Digital Health*，DOI 10.1177/20552076261426622 | 同儕審查 | 只用手環輸出的心率數列（沒有原始 PPG）訓練模型找壞資料：運動時抓到 76.6%，誤判約 25%。壞資料定義＝和胸帶差 > 10 bpm | 已驗證（全文） |
| Polar 官方說明〈The what and how of Polar's wrist-based heart rate measurement〉 | 廠商 | 「In cold conditions, blood circulation on the skin may become too weak for the sensor to get a proper reading」；胸帶對快速升降反應較快、適合間歇；戴在腕骨上方至少一指、綁緊 | 已驗證（官方頁） |
| COROS 官方說明〈Why Heart Rate Data May Be Inaccurate〉 | 廠商 | 手腕溫度、錶帶鬆緊、汗水、配戴位置、刺青、膚色和毛髮、血管粗細 | 摘要（頁面擋爬蟲，只看到搜尋摘要） |
| Fellrnr wiki〈Optical Heart Rate Monitoring〉 | 個人經驗 | 約 4 °C 時明顯變差；太緊會把血擠走、太鬆會接觸不良；有人遇到鎖步頻 | 已驗證（原頁）；個人觀察，不是研究 |
| intervals.icu 公告（2020-01-06） | 平台做法 | 「Spikes above max HR are replaced with a line interpolated between the HR values before and after the spike」；也能手動忽略整趟或一段心率 | 已驗證（原文） |
| TrainingPeaks 說明〈How to Fix Your Workout Data〉 | 平台做法 | 手動選一段，換成直線，TSS 重算 | 摘要 |
| 運動開始時的心率第一期（迷走神經撤除） | 同儕審查 | 開始運動時心率先急升，大部分變化在前 10 秒 | 摘要（PMC8505324 的搜尋摘要）。意思是：停下再起跑時，真實心率也可能幾秒內升十幾下 |

讀完的判斷：

- **越野比路跑難量，原因是動作，不是地形本身**。唯一的越野研究裡，手腕錶的一致性很差（CCC 0.32），而且是高估。跑步機上速度越快越差。（已驗證）
- **低溫**：研究只測到 10 °C 的走路，沒影響；廠商和經驗說冷會變差。**跑步、10 °C 以下沒有找到研究**（未找到來源）。
- **延遲**：光學心率比胸帶慢，只有廠商（Polar）和中文教練（徐國峰，既有文件引過）的說法，**沒有找到量化幾秒的研究**（未找到來源）。app 已經用其他理由避開心率變化最快的時段（漂移扣前 10 分、3 區扣每段前 3 分、下坡不算），這些剛好也擋掉光學延遲。
- **自動偵測**：研究上的做法大多要原始 PPG 加加速度計，app 拿不到。只用心率數列的方法（Vermunicht 2026）約四分之三準。平台實務是「超過最大心率就內插」加「讓使用者手動忽略」。

### 1.3 擁有者資料的掃描

方法（全部唯讀；只列彙總數字）：

- 資料：NAS `fit/coros/` 的 828 個 FIT 複製到本機。取跑步（路跑、越野、跑步機）、有心率、移動 ≥ 10 分鐘的 **394 次、277 小時**，2023–2026。
- 裝置看 FIT 的 `file_id`：**COROS APEX 2 Pro 290 次（2025–2026）**、**Garmin fenix 7 98 次（2023–2025）**、Apple Watch 6 次（不列）。
- 尖峰和鎖步頻直接呼叫 app 的 `clean_hr`、`cadence_lock`。其他型態的定義是這次自訂的，門檻都是**推估**：
  - 斷訊：手錶繼續記錄，心率連續空白 ≥ 10 秒。
  - 平線：移動中心率同一個數字 ≥ 60 秒。
  - 移動中跳一階：3 秒內升或降 ≥ 15 bpm，而且 30 秒內沒有回來；前 60 秒沒有停下（速度 < 0.5 m/s 累計 ≥ 5 秒）、前 10 秒沒有斷訊、在第 10 分鐘之後。停下再起跑的跳升不算（可能是真的，見 §1.2 第一期心率）。
  - 起跑讀太高：前 10 分鐘有 ≥ 30 秒比「第 10 分鐘之後的 95 百分位」高 10 bpm 以上（只看 ≥ 30 分鐘的跑步）。
  - 起跑讀太低（晚抓到訊號）：第 2–10 分鐘有 ≥ 60 秒比之後的中位數低 30 bpm 以上，速度卻 ≥ 之後中位數的 90%。

每種型態出現在幾成的跑步裡：

| 型態 | COROS 路跑（215 次） | COROS 越野（75） | Garmin 路跑（31） | Garmin 越野（47） | Garmin 跑步機（20） |
|---|---|---|---|---|---|
| 尖峰（app 定義） | 0% | 13%（每小時 0.18 次） | 19%（0.42） | **57%（1.12）** | 10% |
| 鎖步頻 ≥ 60 秒（app 定義） | **34%**（移動時間 1.1%） | 24%（0.7%） | 3% | 2% | 0% |
| 鎖步頻 ≥ 5 分鐘 | 0% | 0% | 0% | 0% | 0% |
| 斷訊 ≥ 10 秒 | 33%（時間 0.2%，每次中位數 15 秒） | 24%（0.15%） | 0% | 0% | 0% |
| 平線 ≥ 60 秒 | 8% | 0% | 10% | 9% | 0% |
| 移動中跳一階 | 0% | 1% | 29% | **47%** | 5% |
| 起跑讀太高 | 1% | 8% | 0% | 0% | 0% |
| 起跑讀太低 | 0% | 8% | 11% | 27% | 44% |

- 「起跑」兩列只算 ≥ 30 分鐘的跑步（COROS 路跑 206、越野 75；Garmin 路跑 27、越野 45、跑步機 9）。
- COROS 有任一型態的跑步約 6 成，**扣掉斷訊和短暫鎖步頻，只剩約 6%**（尖峰、跳一階、起跑讀太高）。同樣算法 Garmin 是 53%。
- 坡度（全部裝置合計，30 秒坡度）：尖峰秒數佔下坡 0.02%、上坡 0.05%、平路 0.02%；鎖步頻佔 0.13%、0.48%、0.88%。**沒有看到下坡特別差**，和 Navalta 前臂帶「上坡比下坡差」的方向一致（推估：樣本混了兩支錶）。
- 溫度：只有 Garmin 檔有手錶溫度（48 次）。扣 3.7 °C 手腕偏差後最低約 8 °C、中位數約 24 °C，**沒有低溫樣本**，低溫的影響驗不了。
- 延遲：沒有胸帶對照，**量不到**。

對計算的影響：

| 用途 | 結果 | 標記 |
|---|---|---|
| 漂移 | 76 次夠長的跑步（第 10 分鐘後移動 ≥ 40 分），把尖峰和鎖步頻拿掉後，前後半心率比的變化：中位數 0、90 百分位 0.15 個百分點、最大 1.04；≥ 1 個百分點的只有 1 次 | 已驗證（這次量的）。沒算「跳一階」，Garmin 越野那 47% 會更大 |
| 平均心率（hrTSS 的輸入） | 所有跑步 < 1 bpm | 已驗證（這次量的） |
| 最大心率 | 最近 365 天（196 次，全部 COROS）：單點最高比「第 3 高的 60 秒值」高 **35 bpm**；四套規則清理後彼此差在 **−4～+3 bpm**。有 1 次越野在第 5 分鐘左右出現約 50 秒的**緩慢**爬升（不是尖峰、步頻 131 也不是鎖步頻），60 秒值比第 3 高多 28 bpm；5 秒規則靠「最高一趟比第二高多 > 5 就丟」、120 秒規則靠撐不到 120 秒、第 3 高靠排名，三套都擋掉了 | 已驗證（這次量的） |
| 步頻相近但測不出是不是鎖住 | COROS 路跑每趟有中位數 20% 的移動時間心率和步頻差 ≤ 3；46 次（21%）超過 30%。現有規則要步頻有變動才判得出來，穩定配速時無法分辨是巧合還是鎖住 | 推估（只是重疊比例，不代表真的鎖住） |

## 2. 落差

1. **只有最大心率有清理，而且四套規則各做各的**。改一個地方，另外三個不會跟著改。規則本身在擁有者資料上結果接近（−4～+3 bpm），問題是維護和一致性，不是數字。
2. **漂移、hrTSS、LTHR 推估、區間時間都吃原始心率**。用擁有者現在的錶影響很小；換成 Garmin 時期那種資料（越野 47% 有跳一階、57% 有尖峰），漂移就可能被一兩次跳階帶偏，而 app 不會知道。這是「給所有人用」的問題：別人的錶可能比較像舊 Garmin。
3. **穩定配速時的鎖步頻偵測不到**。`cadence_lock` 要步頻有 ≥ 1.5 spm 的變動才能看相關（`session_stimulus.py:29`），而漂移測試要的正是穩定配速的跑步。
4. **緩慢的高估偵測不到**。尖峰規則只抓 3 秒內跳 ≥ 15 bpm；一分鐘內慢慢爬 20 bpm 的錯誤現在只靠「排名」擋掉，只用在最大心率。
5. **使用者沒辦法說「這段心率是錯的」**。修正機制（`corrections.py`）已經能清空任一通道的一段時間，但介面只做功率。intervals.icu 和 TrainingPeaks 都讓使用者手動忽略。
6. **不知道哪一趟有戴胸帶**。FIT 裡看不到（§1.1），所以所有心率都被當成手腕光學，胸帶跑的高品質資料也打折。
7. **低溫沒有依據也沒有資料**。台灣平地影響有限；百岳冬季可能有，但擁有者的資料沒有、研究也沒有。

## 3. 結論與後續

### 3.1 回答單上的問題

- **光學心率在越野和低溫的誤差**：越野有一篇（Navalta 2020）：手腕錶很差、偏高。低溫只有 10 °C 的走路研究（沒影響），跑步沒有找到。（已驗證／未找到來源）
- **常見的異常型態**：在擁有者的資料裡，依頻率是：短暫鎖步頻、短斷訊、平線、起跑讀太低或太高、尖峰、移動中跳一階。後三種幾乎都是舊 Garmin。延遲量不到。
- **自動偵測和排除的方法**：只拿得到心率數列時，實務上是「門檻＋形狀規則」：超過最大心率、短時間跳太多、和步頻黏在一起、平線、斷訊，再加上讓使用者手動忽略。機器學習只多一點準度（約 75%），不值得現在做（推估）。
- **app 現有的檢查（最大心率取第 3 高等）夠不夠**：**對最大心率是夠的**，四套規則在擁有者資料上都擋掉了唯一那次一分鐘長的錯誤。**對漂移不夠**：沒有任何檢查，只是擁有者現在的錶剛好乾淨。

### 3.2 建議開的實作單

**單 1：統一心率清理（P3）**

- 內容：新增一個 `hr_quality` 模組，負責每趟的清理和標記：範圍、尖峰、鎖步頻、斷訊、平線、移動中跳一階、起跑讀太高。四套最大心率規則改成先用它清理，再各自取峰值（5 秒／60 秒／120 秒和「第 3 高」「前 5 中位數」照舊，各自的用途不同）。
- 驗收條件草稿：
  - 只有一個地方定義尖峰和鎖步頻的門檻；`thresholds`、`threshold_confidence`、`session_stimulus`、`racepower.maximal` 都呼叫它。
  - 每個門檻有來源或「推估」註解。
  - 在擁有者資料上跑新舊對照：四個最大心率值的變化各 ≤ 2 bpm，差異寫進 spec 的 Change History。
  - 每種型態各有單元測試（合成資料）：尖峰會被拿掉、真實的 3 分鐘爬坡升 30 bpm 不會被拿掉、停下再起跑的急升不會被拿掉。

**單 2：漂移和 AeT 判讀加心率品質檢查（P2）**

- 內容：漂移的量測視窗裡，用單 1 的標記計算「可疑心率秒數」。超過門檻時這次不當測試級，只當參考，並寫出原因。
- 建議門檻（全部推估）：視窗內尖峰＋跳一階＋平線＋鎖步頻合計 > 視窗的 3%，或有任何一次移動中跳一階。這次沒有用完整的漂移視窗量會降級幾次；從 §1.3 看，COROS 的跳一階和尖峰都很少，主要會被鎖步頻觸發，實作時先量。
- 驗收條件草稿：
  - `drift_of` 輸出 `hr_quality`（各型態秒數）和降級原因；降級的跑步不進間歇解鎖、AeT 測試、閾值寫入。
  - 卡片文字白話，例如「這次心率有 2 段突然跳動，飄移只當參考」（zh-TW／en）。
  - 用擁有者資料列出被降級的跑步數；如果 > 10%，先回報不要上線。
  - 只標記，不改原始資料，也不內插。

**單 3：活動頁的心率品質標記和「忽略這段心率」（P3）**

- 內容：活動頁心率圖上標出可疑的時段（單 1）。讓使用者選一段或整趟「忽略心率」，存進既有的 `corrections.py` 覆蓋層（`channel="heartrate"`），漂移、hrTSS、最大心率、區間時間都跟著重算。
- 驗收條件草稿：
  - 設定頁的資料修正可以選心率；活動頁可以框選一段忽略、也能復原。
  - 忽略後相關的快取失效，數字重算（測試覆蓋漂移和 hrTSS）。
  - 自動偵測只提示、不自動套用（和現在功率修正的原則一樣）。

**單 4：胸帶標記（P3，先回答問題 1）**

- 內容：FIT 讀不到胸帶，所以讓使用者在活動上勾「這次戴胸帶」，或在設定裡填「平常戴胸帶」。勾了的跑步不顯示手腕提醒，閾值推估和最大心率可以優先用這些跑步。
- 驗收條件草稿：
  - 活動標籤多一個「心率來源：手腕／胸帶」，預設手腕；設定可改預設。
  - 測試範本（LTHR、AeT、最大心率測試）找結果時，標「胸帶」的跑步有提示「這次是胸帶，結果比較可信」。
  - 沒有勾的時候，行為和現在完全一樣。

**先不開**：

- 低溫處理：沒有研究、沒有資料。等使用者說冬天山上的心率怪怪的，再用那幾趟看。
- 穩定配速的鎖步頻（落差 3）：只看重疊比例會誤判太多（COROS 路跑 21% 的跑步超過 30%）。可行的方向是拿心率和功率／配速的個人關係比對，偏離太多才算，但要先有單 1 的乾淨資料當基準。先記著，不開單。
- 機器學習偵測：準度約 75%（Vermunicht 2026），規則版先做完再評估。

### 3.3 要問使用者的問題

1. **Garmin fenix 7 那段時間（2023–2024）有沒有戴胸帶？** 那段資料的尖峰和起跑讀太低很多。如果是胸帶，那可能是胸帶剛開始太乾的問題，不是光學；結論和單 4 的必要性會不一樣。
2. **現在跑步有沒有戴過胸帶？** 如果完全沒有，單 4 可以延後。
3. **冬天上山（< 10 °C）的時候有沒有覺得錶的心率不對？** 有的話，低溫那一項值得排一次對照（同一趟手腕＋胸帶）。
4. **心率有問題時，漂移要「降級成參考」還是「完全不算」？** 文件建議降級（保守，數字仍看得到）。
5. **要不要「忽略這段心率」這個手動功能？** 現在只有功率能修。

## 4. 限制

- 只有一位跑者、兩支錶；COROS 和 Garmin 也是不同年份、不同路線，錶的差別和路線的差別分不開。
- 沒有胸帶對照，所以「異常」是用形狀判斷的，不知道真正的誤差有多大。偵測不到的錯誤（穩定鎖步頻、整趟偏高或偏低）這次完全量不到。
- 自訂型態的門檻（10 秒、60 秒、15 bpm、30 bpm 等）是推估，換門檻比例會變。
- 漂移的影響是用「前後半心率比」近似，沒有呼叫完整的 `drift_of`（坡道、停等、尾段的切法不同）。
- Bent 2020 和 COROS 官方頁這次沒有讀到全文。

## 參考

- Navalta JW, Montes J, Bodell NG, et al. Concurrent heart rate validity of wearable technology devices during trail running. *PLoS One* 2020;15(8):e0238569. https://pmc.ncbi.nlm.nih.gov/articles/PMC7458324/ （2026-10-06 讀取）
- Pasadyn SR, Soudan M, Gillinov M, et al. Accuracy of commercially available heart rate monitors in athletes: a prospective study. *Cardiovasc Diagn Ther* 2019. https://cdt.amegroups.org/article/view/26754/html （2026-10-06 讀取）
- Sartor F, Gelissen J, van Dinther R, et al. Wrist-worn optical and chest strap heart rate comparison in a heterogeneous sample of healthy individuals and in coronary artery disease patients. *BMC Sports Sci Med Rehabil* 2018. https://pmc.ncbi.nlm.nih.gov/articles/PMC5984393/ （2026-10-06 讀取）
- Gielen J, Van Oost CN, Debard G, et al. Accuracy of optical heart rate measurements for 10 commercial wearables in different climate conditions and activities. *JMIR Form Res* 2026. DOI 10.2196/85186. https://pmc.ncbi.nlm.nih.gov/articles/PMC12912460/ （2026-10-06 讀取）
- Vermunicht P, et al. A novel machine learning procedure to detect and remove artefacts in heart rate data obtained from photoplethysmography wearables. *Digital Health* 2026. DOI 10.1177/20552076261426622. https://pmc.ncbi.nlm.nih.gov/articles/PMC13039649/ （2026-10-06 讀取）
- Gillinov S, et al. Variable accuracy of wearable heart rate monitors during aerobic exercise. *Med Sci Sports Exerc* 2017;49:1697–1703.（摘要）
- Zhang Y, Weaver RG, Armstrong B, et al. Validity of wrist-worn photoplethysmography devices to measure heart rate: a systematic review and meta-analysis. *J Sports Sci* 2020.（摘要）
- Bent B, Goldstein BA, Kibbe WA, Dunn JP. Investigating sources of inaccuracy in wearable optical heart rate sensors. *npj Digit Med* 2020;3:18.（見 `vo2max-session-detection.md`）
- Vagal blockade suppresses the phase I heart rate response but not the phase I cardiac output response at exercise onset in humans. https://pmc.ncbi.nlm.nih.gov/articles/PMC8505324/ （摘要）
- Polar. The what and how of Polar's wrist-based heart rate measurement. https://support.polar.com/us-en/the_what_and_how_of_polars_wrist_based_heart_rate_measurement （2026-10-06 讀取）
- COROS. Why Heart Rate Data May Be Inaccurate. https://support.coros.com/hc/en-us/articles/360040257191-Why-Heart-Rate-Data-May-Be-Inaccurate （摘要，頁面無法讀取）
- Fellrnr. Optical Heart Rate Monitoring. https://fellrnr.com/wiki/Optical_Heart_Rate_Monitoring （2026-10-06 讀取）
- intervals.icu. Heartrate spikes now automatically fixed. https://forum.intervals.icu/t/heartrate-spikes-now-automatically-fixed/174 （2026-10-06 讀取）
- TrainingPeaks. How to Fix Your Workout Data. https://help.trainingpeaks.com/hc/en-us/articles/204072284-How-to-Fix-Your-Workout-Data （摘要）
- 既有文件：`zones-and-thresholds.md` §2.3、`vo2max-session-detection.md` §2.5、§3、`drift-algorithm.md` §1.3、`docs/spec/workout-review.spec.md`、`docs/spec/wko5-engine.spec.md`（Data corrections）。
